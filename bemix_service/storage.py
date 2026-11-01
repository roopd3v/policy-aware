"""Transactional single-host storage with append-only, authenticated records.

SQL triggers protect history through ordinary database access. HMACs detect changes without the external audit key. Detecting rollback of an entire valid database requires a previously exported checkpoint kept outside this host.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import sqlite3
import uuid


SCHEMA_VERSION = 3


class IntegrityFailure(RuntimeError):
    pass


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


SCHEMA = """
CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE principals(id TEXT PRIMARY KEY, name TEXT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0);
CREATE TABLE memberships(subject TEXT NOT NULL REFERENCES principals(id), program TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('researcher','triager','security','publisher')),
 PRIMARY KEY(subject,program,role));
CREATE TABLE tokens(id TEXT PRIMARY KEY, subject TEXT NOT NULL REFERENCES principals(id),
 digest TEXT UNIQUE NOT NULL, expires TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE events(seq INTEGER PRIMARY KEY, at TEXT NOT NULL, actor TEXT NOT NULL,
 program TEXT NOT NULL, action TEXT NOT NULL, object_id TEXT NOT NULL,
 payload TEXT NOT NULL, previous_hash TEXT NOT NULL, hash TEXT NOT NULL, mac TEXT NOT NULL);
CREATE TABLE records(id TEXT PRIMARY KEY, kind TEXT NOT NULL, program TEXT NOT NULL,
 parent TEXT NOT NULL, revision INTEGER NOT NULL, actor TEXT NOT NULL, at TEXT NOT NULL,
 body TEXT NOT NULL, hash TEXT NOT NULL, mac TEXT NOT NULL);
CREATE INDEX records_parent ON records(program,kind,parent,revision);
CREATE UNIQUE INDEX one_decision_version ON records(program,parent,revision) WHERE kind='decision';
CREATE UNIQUE INDEX one_award_version ON records(program,parent,revision) WHERE kind='award';
CREATE UNIQUE INDEX one_policy_version ON records(program,json_extract(body,'$.policy.asset'),json_extract(body,'$.policy.version')) WHERE kind='policy';
CREATE UNIQUE INDEX one_publication ON records(program,parent) WHERE kind='publication';
CREATE UNIQUE INDEX one_handoff ON records(program,parent) WHERE kind='handoff';
CREATE UNIQUE INDEX one_appeal_resolution ON records(program,parent) WHERE kind='appeal_resolution';
CREATE UNIQUE INDEX one_submission_case ON records(program,json_extract(body,'$.submission_id')) WHERE kind='case';
CREATE UNIQUE INDEX one_claim_version ON records(program,parent,revision) WHERE kind='claim';
CREATE UNIQUE INDEX one_external_report ON records(program,json_extract(body,'$.reference')) WHERE kind='case';
CREATE TABLE requests(program TEXT NOT NULL, subject TEXT NOT NULL, key TEXT NOT NULL,
 operation TEXT NOT NULL, digest TEXT NOT NULL, response TEXT NOT NULL, mac TEXT NOT NULL,
 PRIMARY KEY(program,subject,key));
CREATE TABLE rate_limits(subject TEXT PRIMARY KEY, window INTEGER NOT NULL, count INTEGER NOT NULL);
CREATE TRIGGER records_no_update BEFORE UPDATE ON records BEGIN SELECT RAISE(ABORT,'history is append-only'); END;
CREATE TRIGGER records_no_delete BEFORE DELETE ON records BEGIN SELECT RAISE(ABORT,'history is append-only'); END;
CREATE TRIGGER events_no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT,'audit is append-only'); END;
CREATE TRIGGER events_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT,'audit is append-only'); END;
CREATE TRIGGER requests_no_update BEFORE UPDATE ON requests BEGIN SELECT RAISE(ABORT,'idempotency records are append-only'); END;
CREATE TRIGGER requests_no_delete BEFORE DELETE ON requests BEGIN SELECT RAISE(ABORT,'idempotency records are append-only'); END;
PRAGMA user_version=3;
"""


class Store:
    def __init__(self, path, key):
        self.path = Path(path).resolve()
        self.key = key
        if not isinstance(key, bytes) or len(key) < 32:
            raise ValueError("Audit key must contain at least 32 random bytes.")
        if not self.path.is_file():
            raise ValueError("Initialize the database explicitly before starting the service.")
        if os.name == "posix" and self.path.stat().st_mode & 0o077:
            raise ValueError("Database must not be readable or writable by group/other users.")
        with self.connection() as conn:
            if conn.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                raise ValueError("Unsupported database schema version.")
            expected = conn.execute("SELECT value FROM metadata WHERE key='audit_key_hash'").fetchone()
            if expected is None or not hmac.compare_digest(expected[0], sha256(key).hexdigest()):
                raise IntegrityFailure("The audit key does not match this database.")
            self.store_id = conn.execute("SELECT value FROM metadata WHERE key='store_id'").fetchone()[0]

    @classmethod
    def initialize(cls, path, key):
        path = Path(path).resolve()
        if not isinstance(key, bytes) or len(key) < 32:
            raise ValueError("Audit key must contain at least 32 random bytes.")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        conn = sqlite3.connect(path)
        try:
            conn.executescript(SCHEMA)
            conn.execute("INSERT INTO metadata VALUES('audit_key_hash',?)", (sha256(key).hexdigest(),))
            conn.execute("INSERT INTO metadata VALUES('store_id',?)", (str(uuid.uuid4()),))
            conn.commit()
        finally:
            conn.close()
        return cls(path, key)

    @contextmanager
    def connection(self, write=False):
        # DELETE journal supports the standard Python/OS SQLite without relying
        # on WAL-specific version fixes. No shared/network filesystem deployment.
        conn = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _mac(self, domain, digest):
        return hmac.new(self.key, (domain + ":" + self.store_id + ":" + digest).encode(), "sha256").hexdigest()

    def event(self, conn, actor, program, action, object_id, payload):
        previous = conn.execute("SELECT seq,hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        data = {"seq": previous["seq"] + 1 if previous else 1, "at": utcnow(),
                "actor": actor, "program": program, "action": action, "object_id": object_id,
                "payload": encode(payload), "previous_hash": previous["hash"] if previous else "0" * 64}
        digest = sha256(encode(data).encode()).hexdigest()
        conn.execute("INSERT INTO events VALUES(:seq,:at,:actor,:program,:action,:object_id,:payload,:previous_hash,:hash,:mac)",
                     {**data, "hash": digest, "mac": self._mac("event", digest)})

    def insert(self, conn, kind, program, actor, body, parent="", revision=0, record_id=None):
        data = {"id": record_id or str(uuid.uuid4()), "kind": kind, "program": program,
                "parent": parent, "revision": revision, "actor": actor, "at": utcnow(), "body": encode(body)}
        digest = sha256(encode(data).encode()).hexdigest()
        conn.execute("INSERT INTO records VALUES(:id,:kind,:program,:parent,:revision,:actor,:at,:body,:hash,:mac)",
                     {**data, "hash": digest, "mac": self._mac("record", digest)})
        self.event(conn, actor, program, kind + ".created", data["id"], {"record_hash": digest})
        return {**data, "body": body, "hash": digest}

    def unpack(self, row):
        if row is None:
            return None
        data = dict(row)
        supplied_hash, supplied_mac = data.pop("hash"), data.pop("mac")
        digest = sha256(encode(data).encode()).hexdigest()
        if not hmac.compare_digest(digest, supplied_hash) or not hmac.compare_digest(self._mac("record", digest), supplied_mac):
            raise IntegrityFailure("Stored record integrity verification failed.")
        data["body"] = json.loads(data["body"])
        data["hash"] = digest
        return data

    def get(self, conn, kind, program, record_id):
        return self.unpack(conn.execute("SELECT * FROM records WHERE kind=? AND program=? AND id=?",
                                        (kind, program, record_id)).fetchone())

    def save_request(self, conn, program, subject, key, operation, digest, result):
        data = {"program": program, "subject": subject, "key": key, "operation": operation,
                "digest": digest, "response": encode(result)}
        mac = self._mac("idempotency", sha256(encode(data).encode()).hexdigest())
        conn.execute("INSERT INTO requests VALUES(:program,:subject,:key,:operation,:digest,:response,:mac)", {**data, "mac": mac})

    def read_request(self, row):
        data = dict(row)
        mac = data.pop("mac")
        expected = self._mac("idempotency", sha256(encode(data).encode()).hexdigest())
        if not hmac.compare_digest(expected, mac):
            raise IntegrityFailure("Idempotency response integrity verification failed.")
        return json.loads(data["response"])

    def children(self, conn, kind, program, parent):
        return [self.unpack(row) for row in conn.execute(
            "SELECT * FROM records WHERE kind=? AND program=? AND parent=? ORDER BY revision,at,id",
            (kind, program, parent))]

    def verify(self, checkpoint=None):
        with self.connection() as conn:
            previous, seq, record_events = "0" * 64, 0, {}
            for row in conn.execute("SELECT * FROM events ORDER BY seq"):
                data = dict(row)
                digest, mac = data.pop("hash"), data.pop("mac")
                if data["seq"] != seq + 1 or data["previous_hash"] != previous:
                    raise IntegrityFailure("Audit sequence is discontinuous.")
                actual = sha256(encode(data).encode()).hexdigest()
                if not hmac.compare_digest(actual, digest) or not hmac.compare_digest(self._mac("event", digest), mac):
                    raise IntegrityFailure("Audit event authentication failed.")
                payload = json.loads(data["payload"])
                if "record_hash" in payload:
                    record_events[data["object_id"]] = payload["record_hash"]
                previous, seq = digest, data["seq"]
            for row in conn.execute("SELECT * FROM records"):
                record = self.unpack(row)
                if record_events.pop(record["id"], None) != record["hash"]:
                    raise IntegrityFailure("Record does not match its audit event.")
            if record_events:
                raise IntegrityFailure("An audited record is missing.")
            for request in conn.execute("SELECT * FROM requests"):
                self.read_request(request)
            if checkpoint:
                claimed = {key: checkpoint[key] for key in ("store_id", "seq", "hash")}
                digest = sha256(encode(claimed).encode()).hexdigest()
                if claimed["store_id"] != self.store_id or not hmac.compare_digest(self._mac("checkpoint", digest), checkpoint["mac"]):
                    raise IntegrityFailure("Checkpoint authentication failed.")
                row = conn.execute("SELECT hash FROM events WHERE seq=?", (claimed["seq"],)).fetchone()
                if claimed["seq"] and (row is None or row[0] != claimed["hash"]):
                    raise IntegrityFailure("History predates or contradicts the externally retained checkpoint.")
            result = {"store_id": self.store_id, "seq": seq, "hash": previous}
            result["mac"] = self._mac("checkpoint", sha256(encode(result).encode()).hexdigest())
            return result

    def backup(self, target):
        target = Path(target).resolve()
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        source = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        return Store(target, self.key).verify()
