"""Only this workflow may persist assessments; the core never writes state."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import re
import secrets
import time
import uuid

from dataclasses import replace
from bemix_pilot.engine import canonical, evaluate, fingerprint
from bemix_pilot.revisions import evaluate_revision
from bemix_pilot.serialization import policy_from_dict, report_from_dict
from .storage import IntegrityFailure, Store, encode, utcnow
from .submissions import validate_catalog, validate_claims, preview, validate_claim_review
from .estimates import validate_estimation, apply_bindings, submitted_report, estimated_bounty, bounty_view
from .awards import validate_adjustments, calculate_award
from .guardrails import validate_template, validate_configuration, certify_configuration


ROLES = {"researcher", "triager", "security", "publisher"}
CORE_CONTRACT = "bemix-policy-aware/1.3"


class ServiceError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message
        super().__init__(message)


def fail(status, code, message):
    raise ServiceError(status, code, message)


def text(value, name, limit=1000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        fail(422, "invalid_input", f"{name} must be a non-empty string of at most {limit} characters.")
    return value


def fields(body, required, optional=()):
    if not isinstance(body, dict) or set(body) - set(required) - set(optional) or set(required) - set(body):
        fail(422, "invalid_fields", "Missing or unknown fields.")


def public_record(record):
    return {key: record[key] for key in ("id", "kind", "program", "parent", "revision", "actor", "at", "body", "hash")}


def record_summary(record):
    result = {key: record[key] for key in ("id", "kind", "program", "parent", "revision", "actor", "at", "hash")}
    body = record["body"]
    if record["kind"] == "decision":
        reward = body["result"]["reward"]
        result["summary"] = {"kind": body["kind"], "reason": body["reason"],
                             "reward_status": reward["status"], "amount": reward["amount"],
                             "severity": body["result"]["classification"].get("severity")}
    else:
        result["body"] = body
    return result


class DecisionService:
    def __init__(self, store: Store, requests_per_minute=120):
        self.store = store
        self.requests_per_minute = requests_per_minute

    # These provisioning methods are deliberately absent from HTTP routes.
    # They require local operator access to the database and audit key.
    def add_principal(self, name, subject=None):
        text(name, "name", 200)
        subject = subject or str(uuid.uuid4())
        with self.store.connection(write=True) as conn:
            conn.execute("INSERT INTO principals(id,name) VALUES(?,?)", (subject, name))
            self.store.event(conn, "local-operator", "", "principal.created", subject, {"name": name})
        return subject

    def add_program(self, program):
        if not isinstance(program, str) or re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", program) is None:
            fail(422, "invalid_program", "Program identifiers use lowercase letters, digits and hyphens.")
        with self.store.connection(write=True) as conn:
            self.store.insert(conn, "program", program, "local-operator", {"name": program}, record_id=program)

    def install_guardrails(self, program, template, reason):
        """Trusted platform provisioning, intentionally absent from HTTP writes."""
        policy = validate_template(template)
        governance = template["base_governance"]
        validate_catalog(policy, governance.get("submission_options", []))
        validate_estimation(policy, governance.get("submission_options", []), governance.get("estimation", {}))
        validate_adjustments(governance.get("award_adjustments", []))
        certificate = certify_configuration(template, policy, governance)
        reason = text(reason, "reason", 4000)
        with self.store.connection(write=True) as conn:
            if self.store.get(conn, "program", program, program) is None:
                fail(404, "not_found", "Program not found.")
            row = conn.execute("SELECT * FROM records WHERE kind='platform_guardrails' AND program=? AND json_extract(body,'$.template.version')=?",
                               (program, template["version"])).fetchone()
            previous = self.store.unpack(row)
            if previous:
                if previous["body"]["template"] != template:
                    fail(409, "guardrails_version_exists", "Platform template versions are immutable.")
                return public_record(previous)
            return public_record(self.store.insert(conn, "platform_guardrails", program, "local-operator",
                {"template": template, "certificate": certificate, "reason": reason}, parent=policy.asset))

    def _active_guardrails(self, conn, program, asset):
        row = conn.execute("""SELECT r.* FROM records r JOIN events e ON e.object_id=r.id
            AND e.action='platform_guardrails.created' WHERE r.kind='platform_guardrails'
            AND r.program=? AND r.parent=? ORDER BY e.seq DESC LIMIT 1""", (program, asset)).fetchone()
        return self.store.unpack(row)

    def grant(self, subject, program, role):
        if role not in ROLES:
            fail(422, "invalid_role", "Unknown role.")
        with self.store.connection(write=True) as conn:
            if self.store.get(conn, "program", program, program) is None:
                fail(404, "not_found", "Program not found.")
            conn.execute("INSERT OR IGNORE INTO memberships VALUES(?,?,?)", (subject, program, role))
            self.store.event(conn, "local-operator", program, "membership.granted", subject, {"role": role})

    def issue_token(self, subject, days=30):
        if type(days) is not int or not 1 <= days <= 90:
            fail(422, "invalid_expiry", "Token validity must be between 1 and 90 days.")
        token = "bmx_" + secrets.token_urlsafe(32)
        token_id = str(uuid.uuid4())
        expiry = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(timespec="microseconds")
        with self.store.connection(write=True) as conn:
            conn.execute("INSERT INTO tokens(id,subject,digest,expires) VALUES(?,?,?,?)",
                         (token_id, subject, sha256(token.encode()).hexdigest(), expiry))
            self.store.event(conn, "local-operator", "", "token.issued", token_id, {"subject": subject, "expires": expiry})
        return {"id": token_id, "token": token, "expires": expiry}

    def revoke_membership(self, subject, program, role):
        with self.store.connection(write=True) as conn:
            conn.execute("DELETE FROM memberships WHERE subject=? AND program=? AND role=?", (subject, program, role))
            self.store.event(conn, "local-operator", program, "membership.revoked", subject, {"role": role})

    def disable_principal(self, subject):
        with self.store.connection(write=True) as conn:
            if conn.execute("UPDATE principals SET disabled=1 WHERE id=?", (subject,)).rowcount != 1:
                fail(404, "not_found", "Principal not found.")
            self.store.event(conn, "local-operator", "", "principal.disabled", subject, {})

    def revoke_token(self, token_id):
        with self.store.connection(write=True) as conn:
            if conn.execute("UPDATE tokens SET revoked=1 WHERE id=?", (token_id,)).rowcount != 1:
                fail(404, "not_found", "Token not found.")
            self.store.event(conn, "local-operator", "", "token.revoked", token_id, {})

    def _identity(self, conn, token):
        if not isinstance(token, str) or re.fullmatch(r"bmx_[A-Za-z0-9_-]{43}", token) is None:
            fail(401, "unauthorized", "Valid bearer credentials are required.")
        row = conn.execute("SELECT t.subject,t.expires,t.revoked,p.disabled FROM tokens t JOIN principals p ON p.id=t.subject WHERE t.digest=?",
                           (sha256(token.encode()).hexdigest(),)).fetchone()
        if row is None or row["revoked"] or row["disabled"] or row["expires"] <= utcnow():
            fail(401, "unauthorized", "Valid bearer credentials are required.")
        return row["subject"]

    def _consume_rate(self, token):
        with self.store.connection(write=True) as conn:
            subject = self._identity(conn, token)
            window = int(time.time()) // 60
            row = conn.execute("SELECT window,count FROM rate_limits WHERE subject=?", (subject,)).fetchone()
            count = row["count"] + 1 if row and row["window"] == window else 1
            conn.execute("INSERT INTO rate_limits VALUES(?,?,?) ON CONFLICT(subject) DO UPDATE SET window=excluded.window,count=excluded.count",
                         (subject, window, count))
        if count > self.requests_per_minute:
            fail(429, "rate_limited", "Request rate exceeded. Retry in the next minute.")

    def _roles(self, conn, actor, program):
        roles = {r[0] for r in conn.execute("SELECT role FROM memberships WHERE subject=? AND program=?", (actor, program))}
        if not roles:
            fail(404, "not_found", "Program or resource not found.")
        return roles

    def preflight(self, token, program):
        with self.store.connection() as conn:
            self._roles(conn, self._identity(conn, token), program)

    def _require(self, roles, *allowed):
        if not roles.intersection(allowed):
            fail(403, "forbidden", "This role cannot perform the operation.")

    def _case(self, conn, actor, roles, program, case_id, staff=False):
        case = self.store.get(conn, "case", program, case_id)
        if case is None:
            fail(404, "not_found", "Case not found.")
        owner = case["body"]["researcher"]
        if staff:
            self._require(roles, "triager", "security")
            if actor == owner:
                fail(403, "self_assessment", "An assessor cannot adjudicate their own case.")
        elif not roles.intersection({"triager", "security"}) and actor != owner:
            fail(404, "not_found", "Case not found.")
        return case

    def _current(self, conn, program, case_id):
        row = conn.execute("SELECT * FROM records WHERE kind='decision' AND program=? AND parent=? ORDER BY revision DESC LIMIT 1",
                           (program, case_id)).fetchone()
        return self.store.unpack(row)

    def _active_policy(self, conn, program, asset):
        row = conn.execute("""SELECT pub.* FROM records pub
            JOIN records pol ON pol.id=pub.parent AND pol.kind='policy' AND pol.program=pub.program
            JOIN events ev ON ev.object_id=pub.id AND ev.action='publication.created'
            WHERE pub.kind='publication' AND pub.program=? AND json_extract(pol.body,'$.policy.asset')=?
            ORDER BY ev.seq DESC LIMIT 1""", (program, asset)).fetchone()
        publication = self.store.unpack(row)
        if publication is None:
            return None
        policy = self.store.get(conn, "policy", program, publication["parent"])
        if policy is None or policy["hash"] != publication["body"]["policy_record_hash"]:
            raise IntegrityFailure("Published policy content does not match its approval.")
        approval = self.store.get(conn, "platform_approval", program, publication["body"].get("platform_approval_id", ""))
        if (approval is None or approval["hash"] != publication["body"].get("platform_approval_hash")
                or approval["body"]["policy_record_hash"] != policy["hash"]
                or approval["body"]["platform_validation"] != publication["body"].get("platform_validation")):
            raise IntegrityFailure("A live policy requires the exact platform approval and configuration validation.")
        return {"policy": policy, "publication": publication}

    def _stage(self, conn, program, case_id):
        handoffs = self.store.children(conn, "handoff", program, case_id)
        return ("security", handoffs[0]) if handoffs else ("triage", None)

    def _assessor(self, conn, actor, roles, program, case):
        if actor == case["body"]["researcher"]:
            fail(403, "self_assessment", "An assessor cannot adjudicate their own report.")
        stage, handoff = self._stage(conn, program, case["id"])
        if stage == "triage":
            self._require(roles, "triager")
            if actor != case["body"]["assigned_triager"]:
                fail(403, "not_assigned", "Only the assigned triager can change this triage assessment.")
        else:
            self._require(roles, "security")
        return stage

    def _expected(self, current, expected):
        if type(expected) is not int or expected < 0:
            fail(422, "invalid_version", "expected_version must be a non-negative integer.")
        version = current["revision"] if current else 0
        if expected != version:
            fail(409, "stale_version", "The accepted assessment changed; reload it before revising.")

    def _report(self, conn, program, case, raw):
        policy_record = self.store.get(conn, "policy", program, case["body"]["policy_id"])
        policy = policy_from_dict(policy_record["body"]["policy"])
        report = report_from_dict(raw)
        # evaluate validates hash, asset, currency and inputs; the caller cannot
        # substitute a policy or a client-provided previous assessment.
        return policy, apply_bindings(policy, policy_record["body"]["governance"], report)

    def _reproduce_current(self, policy, current):
        before = report_from_dict(current["body"]["report"])
        if (current["body"].get("engine_version") != CORE_CONTRACT
                or evaluate(policy, before) != current["body"]["result"]):
            fail(409, "engine_migration_required", "The stored assessment is not reproduced by this engine version; an explicit migration review is required.")
        return before

    def command(self, token, program, operation, resource, body, idempotency_key):
        if not isinstance(idempotency_key, str) or re.fullmatch(r"[A-Za-z0-9_.:-]{16,128}", idempotency_key) is None:
            fail(422, "idempotency_required", "A 16–128 character Idempotency-Key is required.")
        self._consume_rate(token)
        digest = sha256(encode({"operation": operation, "resource": resource, "body": body}).encode()).hexdigest()
        with self.store.connection(write=True) as conn:
            actor = self._identity(conn, token)
            roles = self._roles(conn, actor, program)
            allowed = {"draft_policy": ("publisher",), "publish_policy": ("publisher", "security"),
                       "create_case": ("triager",), "initial": ("triager",), "revise": ("triager", "security"), "handoff": ("triager",),
                       "appeal": ("researcher",), "submit": ("researcher",), "change_claims": ("researcher",),
                       "award": ("security",),
                       "resolve_appeal": ("triager", "security")}
            if operation not in allowed:
                fail(404, "not_found", "Operation not found.")
            self._require(roles, *allowed[operation])
            saved = conn.execute("SELECT * FROM requests WHERE program=? AND subject=? AND key=?", (program, actor, idempotency_key)).fetchone()
            if saved:
                if saved["operation"] != operation or saved["digest"] != digest:
                    fail(409, "idempotency_conflict", "This idempotency key already identifies a different request.")
                return self.store.read_request(saved)
            handlers = {"draft_policy": self._draft_policy, "publish_policy": self._publish_policy,
                        "create_case": self._create_case, "initial": self._initial,
                        "revise": self._revise, "handoff": self._handoff,
                        "appeal": self._appeal, "resolve_appeal": self._resolve_appeal,
                        "submit": self._submit, "change_claims": self._change_claims, "award": self._award}
            if operation not in handlers:
                fail(404, "not_found", "Operation not found.")
            result = handlers[operation](conn, actor, roles, program, resource, body)
            self.store.save_request(conn, program, actor, idempotency_key, operation, digest, result)
            return result

    def _draft_policy(self, conn, actor, roles, program, resource, body):
        self._require(roles, "publisher")
        fields(body, ("policy", "governance"))
        policy = policy_from_dict(body["policy"])
        governance = body["governance"]
        fields(governance, ("source_reference", "calibration_reference", "precedence_reference", "rubrics"),
               ("submission_options", "estimation", "award_adjustments"))
        validate_catalog(policy, governance.get("submission_options", []))
        validate_estimation(policy, governance.get("submission_options", []), governance.get("estimation", {}))
        validate_adjustments(governance.get("award_adjustments", []))
        for field in ("source_reference", "calibration_reference", "precedence_reference"):
            text(governance[field], field, 4000)
        if not isinstance(governance["rubrics"], dict):
            fail(422, "invalid_rubrics", "Rubrics must map every profile and metric to its published definition.")
        guardrails = self._active_guardrails(conn, program, policy.asset)
        if guardrails is None:
            fail(422, "platform_configuration_required", "The platform must configure allowed intervals and acceptance cases for this program/asset first.")
        validate_configuration(guardrails["body"]["template"], policy, governance, complete=False)
        record = self.store.insert(conn, "policy", program, actor,
                                   {"policy": canonical(policy), "governance": governance, "policy_sha256": fingerprint(policy),
                                    "platform_guardrails_id": guardrails["id"], "platform_guardrails_hash": guardrails["hash"]})
        return public_record(record)

    def _validate_publication(self, conn, program, record):
        policy = policy_from_dict(record["body"]["policy"])
        if policy.experimental or any(c.amount is None for c in policy.caps):
            fail(422, "policy_not_ready", "Experimental policies and unquantified limits cannot be published.")
        if not policy.profiles:
            fail(422, "policy_not_ready", "A published policy needs financial profiles.")
        rubrics = record["body"]["governance"]["rubrics"]
        if set(rubrics) != {p.id for p in policy.profiles}:
            fail(422, "policy_not_ready", "Rubrics must cover exactly the configured profiles.")
        for profile in policy.profiles:
            if not isinstance(rubrics[profile.id], dict) or set(rubrics[profile.id]) != set(profile.weights):
                fail(422, "policy_not_ready", "Every financial criterion needs a rubric.")
            for value in rubrics[profile.id].values():
                text(value, "rubric", 8000)
        guardrails = self._active_guardrails(conn, program, policy.asset)
        if (guardrails is None or guardrails["id"] != record["body"].get("platform_guardrails_id")
                or guardrails["hash"] != record["body"].get("platform_guardrails_hash")):
            fail(409, "platform_configuration_changed", "Platform limits changed; create a draft against the current template.")
        certificate = certify_configuration(guardrails["body"]["template"], policy, record["body"]["governance"])
        return guardrails, certificate

    def _publish_policy(self, conn, actor, roles, program, policy_id, body):
        self._require(roles, "publisher", "security")
        fields(body, ("expected_policy_record_hash", "reason"))
        record = self.store.get(conn, "policy", program, policy_id)
        if record is None:
            fail(404, "not_found", "Policy not found.")
        if body["expected_policy_record_hash"] != record["hash"]:
            fail(409, "policy_changed", "The reviewed policy content does not match.")
        if self.store.children(conn, "publication", program, policy_id):
            fail(409, "already_published", "Policy already published.")
        if self.store.children(conn, "policy_review_request", program, policy_id):
            fail(409, "already_requested", "This version already awaits platform review.")
        guardrails, certificate = self._validate_publication(conn, program, record)
        reason = text(body["reason"], "reason", 4000)
        result = self.store.insert(conn, "policy_review_request", program, actor,
                                   {"policy_id": policy_id, "policy_record_hash": record["hash"], "reason": reason,
                                    "status": "pending_platform_review",
                                    "platform_guardrails_id": guardrails["id"], "platform_guardrails_hash": guardrails["hash"],
                                    "platform_validation": certificate}, parent=policy_id)
        return public_record(result)

    def approve_policy(self, program, policy_id, expected_record_hash, reviewer, reason):
        """Only the trusted platform operator can approve and activate a version."""
        reviewer = text(reviewer, "reviewer", 200)
        reason = text(reason, "reason", 4000)
        with self.store.connection(write=True) as conn:
            record = self.store.get(conn, "policy", program, policy_id)
            if record is None:
                fail(404, "not_found", "Policy not found.")
            if record["hash"] != expected_record_hash:
                fail(409, "policy_changed", "Platform approval must identify the exact reviewed policy record hash.")
            if self.store.children(conn, "publication", program, policy_id):
                fail(409, "already_published", "Policy already published.")
            requests = self.store.children(conn, "policy_review_request", program, policy_id)
            if not requests:
                fail(409, "review_request_required", "The program must first submit this version for platform review.")
            if self.store.children(conn, "platform_changes_requested", program, policy_id):
                fail(409, "policy_changes_requested", "The platform requested changes; submit a new policy version.")
            guardrails, certificate = self._validate_publication(conn, program, record)
            request = requests[-1]
            if request["body"]["platform_validation"] != certificate:
                fail(409, "validation_changed", "Platform approval requires the current configuration validation results.")
            approval = self.store.insert(conn, "platform_approval", program, "platform:" + reviewer,
                {"policy_id": policy_id, "policy_record_hash": record["hash"], "reason": reason, "reviewer": reviewer,
                 "review_request_id": request["id"], "review_request_hash": request["hash"],
                 "platform_guardrails_id": guardrails["id"], "platform_guardrails_hash": guardrails["hash"],
                 "platform_validation": certificate}, parent=policy_id)
            publication = self.store.insert(conn, "publication", program, "platform:" + reviewer,
                {"policy_id": policy_id, "policy_record_hash": record["hash"], "reason": reason,
                 "platform_approval_id": approval["id"], "platform_approval_hash": approval["hash"],
                 "platform_guardrails_id": guardrails["id"], "platform_guardrails_hash": guardrails["hash"],
                 "platform_validation": certificate}, parent=policy_id)
            return {"approval": public_record(approval), "publication": public_record(publication)}

    def request_policy_changes(self, program, policy_id, expected_record_hash, reviewer, reason):
        reviewer = text(reviewer, "reviewer", 200)
        reason = text(reason, "reason", 4000)
        with self.store.connection(write=True) as conn:
            record = self.store.get(conn, "policy", program, policy_id)
            if record is None:
                fail(404, "not_found", "Policy not found.")
            if record["hash"] != expected_record_hash:
                fail(409, "policy_changed", "Platform review must identify the exact policy record hash.")
            if self.store.children(conn, "publication", program, policy_id):
                fail(409, "already_published", "Published policies need a new version, not a rewrite.")
            requests = self.store.children(conn, "policy_review_request", program, policy_id)
            if not requests:
                fail(409, "review_request_required", "There is no submitted platform review request.")
            return public_record(self.store.insert(conn, "platform_changes_requested", program, "platform:" + reviewer,
                {"policy_id": policy_id, "policy_record_hash": record["hash"], "review_request_hash": requests[-1]["hash"],
                 "status": "changes_requested", "reviewer": reviewer, "reason": reason}, parent=policy_id))

    def _create_case(self, conn, actor, roles, program, resource, body):
        self._require(roles, "triager")
        fields(body, ("researcher", "policy_id", "reference"), ("assigned_triager", "submission_id"))
        researcher = text(body["researcher"], "researcher", 128)
        if researcher == actor:
            fail(403, "self_assessment", "A triager cannot create their own assessed case.")
        if conn.execute("SELECT 1 FROM memberships WHERE subject=? AND program=? AND role='researcher'", (researcher, program)).fetchone() is None:
            fail(422, "unknown_researcher", "Researcher must belong to this program.")
        policy_id = text(body["policy_id"], "policy_id", 128)
        if not self.store.children(conn, "publication", program, policy_id):
            fail(422, "unpublished_policy", "A case must pin a published policy.")
        policy = self.store.get(conn, "policy", program, policy_id)
        if "submission_id" in body:
            submission = self.store.get(conn, "submission", program, text(body["submission_id"], "submission_id", 128))
            if submission is None:
                fail(404, "not_found", "Submission not found.")
            if submission["body"]["researcher"] != researcher or submission["body"]["policy_id"] != policy_id:
                fail(422, "submission_mismatch", "The case must retain the submission's researcher and policy snapshot.")
            if self._linked_case(conn, program, submission["id"]) is not None:
                fail(409, "submission_already_assigned", "This submission already has an assigned case.")
        else:
            active = self._active_policy(conn, program, policy["body"]["policy"]["asset"])
            if active is None or active["policy"]["id"] != policy_id:
                fail(409, "superseded_policy", "New unlinked cases must pin the active policy; submitted reports retain their submission snapshot.")
        reference = text(body["reference"], "reference", 4000).strip()
        if conn.execute("SELECT 1 FROM records WHERE kind='case' AND program=? AND json_extract(body,'$.reference')=?",
                        (program, reference)).fetchone():
            fail(409, "report_already_registered", "This external report already has a case; revise that case instead.")
        assigned = text(body.get("assigned_triager", actor), "assigned_triager", 128)
        if assigned == researcher:
            fail(403, "self_assessment", "The assigned triager cannot be the report's researcher.")
        if conn.execute("SELECT 1 FROM memberships WHERE subject=? AND program=? AND role='triager'", (assigned, program)).fetchone() is None:
            fail(422, "unknown_triager", "The assigned triager must belong to this program.")
        return public_record(self.store.insert(conn, "case", program, actor,
                             {**body, "reference": reference, "assigned_triager": assigned}))

    def _initial(self, conn, actor, roles, program, case_id, body):
        self._require(roles, "triager")
        fields(body, ("expected_version", "reason"), ("report", "use_submission_inputs", "claim_review"))
        case = self._case(conn, actor, roles, program, case_id, staff=True)
        stage = self._assessor(conn, actor, roles, program, case)
        current = self._current(conn, program, case_id)
        self._expected(current, body["expected_version"])
        if current is not None:
            fail(409, "revision_required", "An assessment already exists; use the mandatory revision workflow.")
        if "report" in body and "use_submission_inputs" not in body:
            policy, report = self._report(conn, program, case, body["report"])
        elif body.get("use_submission_inputs") is True and "report" not in body and case["body"].get("submission_id"):
            record = self.store.get(conn, "policy", program, case["body"]["policy_id"])
            policy = policy_from_dict(record["body"]["policy"])
            claim = self._claim_record(conn, program, case["body"]["submission_id"])
            report = submitted_report(policy, record["body"]["governance"], claim["body"]["claims"], claim["body"]["inputs"])
            report = replace(report, eligibility_reference="Assigned triager confirmed the submitted inputs: " + text(body["reason"], "reason", 4000))
        else:
            fail(422, "assessment_input_required", "Provide a report or explicitly confirm the linked submission inputs.")
        decision = evaluate(policy, report)
        claim_review, claim_source = self._assessed_claims(conn, program, case, body.get("claim_review"), decision)
        text(body["reason"], "reason", 4000)
        return public_record(self.store.insert(conn, "decision", program, actor,
            {"report": canonical(report), "result": decision, "kind": "initial", "reason": body["reason"], "previous_decision_hash": None,
             "engine_version": CORE_CONTRACT, "assessment_stage": stage,
             "claim_review": claim_review, "claim_source": claim_source},
            parent=case_id, revision=1))

    def _revise(self, conn, actor, roles, program, case_id, body):
        self._require(roles, "triager", "security")
        fields(body, ("report", "expected_version", "reason"), ("kind", "claim_review"))
        kind = body.get("kind", "correction")
        case = self._case(conn, actor, roles, program, case_id, staff=True)
        stage = self._assessor(conn, actor, roles, program, case)
        current = self._current(conn, program, case_id)
        self._expected(current, body["expected_version"])
        if current is None:
            fail(409, "initial_required", "Create the initial assessment first.")
        text(body["reason"], "reason", 4000)
        policy, after = self._report(conn, program, case, body["report"])
        before = self._reproduce_current(policy, current)
        transition = evaluate_revision(policy, before, after, kind=kind, reason_reference=body["reason"])
        if kind == "improvement" and transition["status"] != "accepted_improvement":
            fail(409, "correction_required", "This change does not satisfy the improvement contract. The responsible assessor can record it as a justified correction; no additional triager is required.")
        result = transition["applicable_decision"] or transition["proposed_decision"]
        claim_review, claim_source = self._assessed_claims(conn, program, case,
            body.get("claim_review", current["body"].get("claim_review")), result)
        return public_record(self.store.insert(conn, "decision", program, actor,
            {"report": canonical(after), "result": result, "kind": kind, "reason": body["reason"],
             "previous_decision_hash": current["hash"], "transition": transition,
             "engine_version": CORE_CONTRACT, "assessment_stage": stage,
             "claim_review": claim_review, "claim_source": claim_source},
            parent=case_id, revision=current["revision"] + 1))

    def _linked_case(self, conn, program, submission_id):
        return self.store.unpack(conn.execute("SELECT * FROM records WHERE program=? AND kind='case' AND json_extract(body,'$.submission_id')=?",
                                              (program, submission_id)).fetchone())

    def _claim_record(self, conn, program, submission_id):
        return self.store.unpack(conn.execute("SELECT * FROM records WHERE program=? AND kind='claim' AND parent=? ORDER BY revision DESC LIMIT 1",
                                              (program, submission_id)).fetchone())

    def _assessed_claims(self, conn, program, case, review, decision):
        submission_id = case["body"].get("submission_id")
        if not submission_id:
            if review is not None:
                fail(422, "no_submission", "This case has no structured researcher declaration to review.")
            return None, None
        if review is None:
            fail(422, "claim_review_required", "Confirm or correct the researcher's declared behaviours before recording an assessment.")
        claim = self._claim_record(conn, program, submission_id)
        policy_record = self.store.get(conn, "policy", program, case["body"]["policy_id"])
        policy = policy_from_dict(policy_record["body"]["policy"])
        catalog = policy_record["body"]["governance"].get("submission_options", [])
        validate_claim_review(policy, catalog, claim["body"]["claims"], review, decision)
        return review, {"submission_id": submission_id, "claim_version": claim["revision"], "claim_hash": claim["hash"]}

    def _submit(self, conn, actor, roles, program, resource, body):
        fields(body, ("asset", "claims", "summary", "inputs"), ("expected_policy_record_hash",))
        text(body["asset"], "asset", 1000); text(body["summary"], "summary", 8000)
        active = self._active_policy(conn, program, body["asset"])
        if active is None:
            fail(404, "not_found", "No active policy for this asset.")
        record = active["policy"]
        if "expected_policy_record_hash" in body and body["expected_policy_record_hash"] != record["hash"]:
            fail(409, "policy_changed", "Reload the current program form before submitting.")
        policy = policy_from_dict(record["body"]["policy"])
        catalog = record["body"]["governance"].get("submission_options", [])
        claims = validate_claims(policy, catalog, body["claims"])
        estimate = estimated_bounty(policy, record["body"]["governance"], claims, body["inputs"])
        submission = self.store.insert(conn, "submission", program, actor,
            {"researcher": actor, "asset": body["asset"], "summary": body["summary"], "policy_id": record["id"], "policy_record_hash": record["hash"]})
        claim = self.store.insert(conn, "claim", program, actor,
            {"claims": claims, "inputs": body["inputs"], "estimate": estimate,
             "preview": preview(policy, catalog, claims, estimate)}, parent=submission["id"], revision=1)
        return {"submission": public_record(submission), "declaration": public_record(claim), "estimated_bounty": estimate}

    def _change_claims(self, conn, actor, roles, program, submission_id, body):
        fields(body, ("expected_version",), ("claims", "inputs"))
        if not {"claims", "inputs"} & set(body):
            fail(422, "input_required", "Provide changed declarations or calculation inputs.")
        submission = self.store.get(conn, "submission", program, submission_id)
        if submission is None or submission["body"]["researcher"] != actor:
            fail(404, "not_found", "Submission not found.")
        case = self._linked_case(conn, program, submission_id)
        if case and self._current(conn, program, case["id"]):
            fail(409, "triage_started", "The original declaration is fixed once assessed; provide further evidence through the case discussion.")
        previous = self._claim_record(conn, program, submission_id)
        self._expected(previous, body["expected_version"])
        record = self.store.get(conn, "policy", program, submission["body"]["policy_id"])
        policy = policy_from_dict(record["body"]["policy"])
        catalog = record["body"]["governance"].get("submission_options", [])
        claims = validate_claims(policy, catalog, body.get("claims", previous["body"]["claims"]))
        inputs = body.get("inputs", previous["body"]["inputs"])
        estimate = estimated_bounty(policy, record["body"]["governance"], claims, inputs)
        return public_record(self.store.insert(conn, "claim", program, actor,
            {"claims": claims, "inputs": inputs, "estimate": estimate,
             "preview": preview(policy, catalog, claims, estimate)}, parent=submission_id, revision=previous["revision"] + 1))

    def read_submission(self, token, program, submission_id):
        self._consume_rate(token)
        with self.store.connection() as conn:
            actor = self._identity(conn, token)
            roles = self._roles(conn, actor, program)
            record = self.store.get(conn, "submission", program, submission_id)
            if record is None or record["body"]["researcher"] != actor and not roles.intersection({"triager", "security"}):
                fail(404, "not_found", "Submission not found.")
            claim = self._claim_record(conn, program, submission_id)
            return {"submission": public_record(record), "declaration": public_record(claim), "estimated_bounty": claim["body"]["estimate"],
                    "declaration_versions": conn.execute("SELECT count(*) FROM records WHERE program=? AND kind='claim' AND parent=?", (program, submission_id)).fetchone()[0],
                    "case_id": (case["id"] if (case := self._linked_case(conn, program, submission_id)) else None)}

    def read_submission_version(self, token, program, submission_id, revision):
        if type(revision) is not int or revision < 1:
            fail(422, "invalid_version", "Declaration versions start at 1.")
        self._consume_rate(token)
        with self.store.connection() as conn:
            actor = self._identity(conn, token)
            roles = self._roles(conn, actor, program)
            submission = self.store.get(conn, "submission", program, submission_id)
            if submission is None or submission["body"]["researcher"] != actor and not roles.intersection({"triager", "security"}):
                fail(404, "not_found", "Submission not found.")
            claim = self.store.unpack(conn.execute("SELECT * FROM records WHERE program=? AND kind='claim' AND parent=? AND revision=?",
                                                   (program, submission_id, revision)).fetchone())
            if claim is None:
                fail(404, "not_found", "Declaration version not found.")
            return public_record(claim)

    def read_options(self, token, program, asset):
        active = self.read_active_policy(token, program, asset)
        record = active["policy"]
        return {"asset": record["body"]["policy"]["asset"], "policy_id": record["id"], "policy_record_hash": record["hash"],
                "options": record["body"]["governance"].get("submission_options", []),
                "scoring_policy": record["body"]["policy"], "estimation": record["body"]["governance"].get("estimation", {}),
                "financial_status": "automatically_estimated_then_validated"}

    def preview_estimate(self, token, program, body):
        fields(body, ("asset", "claims", "inputs"), ("expected_policy_record_hash",))
        active = self.read_active_policy(token, program, body["asset"])
        record = active["policy"]
        if "expected_policy_record_hash" in body and body["expected_policy_record_hash"] != record["hash"]:
            fail(409, "policy_changed", "Reload the current program form.")
        return estimated_bounty(policy_from_dict(record["body"]["policy"]), record["body"]["governance"], body["claims"], body["inputs"])

    def _handoff(self, conn, actor, roles, program, case_id, body):
        self._require(roles, "triager")
        fields(body, ("expected_version", "reason"))
        case = self._case(conn, actor, roles, program, case_id, staff=True)
        stage = self._assessor(conn, actor, roles, program, case)
        if stage != "triage":
            fail(409, "already_handed_off", "This case is already with the program security team.")
        current = self._current(conn, program, case_id)
        self._expected(current, body["expected_version"])
        if current is None:
            fail(409, "initial_required", "Create a triage assessment before forwarding it.")
        reason = text(body["reason"], "reason", 4000)
        return public_record(self.store.insert(conn, "handoff", program, actor,
            {"reason": reason, "triage_version": current["revision"], "triage_decision_hash": current["hash"]},
            parent=case_id))

    def _current_award(self, conn, program, case_id):
        return self.store.unpack(conn.execute("SELECT * FROM records WHERE program=? AND parent=? AND kind='award' ORDER BY revision DESC LIMIT 1",
                                              (program, case_id)).fetchone())

    def _award(self, conn, actor, roles, program, case_id, body):
        self._require(roles, "security")
        fields(body, ("expected_version", "expected_award_version", "reason"), ("adjustment",))
        case = self._case(conn, actor, roles, program, case_id, staff=True)
        if self._assessor(conn, actor, roles, program, case) != "security":
            fail(403, "program_confirmation_required", "Only the program security stage can confirm the final award.")
        current = self._current(conn, program, case_id)
        self._expected(current, body["expected_version"])
        if current is None:
            fail(409, "assessment_required", "An assessment is required before award confirmation.")
        previous = self._current_award(conn, program, case_id)
        self._expected(previous, body["expected_award_version"])
        record = self.store.get(conn, "policy", program, case["body"]["policy_id"])
        policy = policy_from_dict(record["body"]["policy"])
        self._reproduce_current(policy, current)
        calculated = calculate_award(policy, record["body"]["governance"], current, body.get("adjustment"))
        reason = text(body["reason"], "reason", 8000)
        return public_record(self.store.insert(conn, "award", program, actor,
            {**calculated, "stage": "final_award", "status": "program_confirmed",
             "assessment_version": current["revision"], "assessment_hash": current["hash"],
             "policy_id": record["id"], "policy_record_hash": record["hash"], "reason": reason,
             "previous_award_hash": previous["hash"] if previous else None, "payment_executed": False},
            parent=case_id, revision=previous["revision"] + 1 if previous else 1))

    def _appeal(self, conn, actor, roles, program, case_id, body):
        fields(body, ("decision_version", "grounds"))
        case = self._case(conn, actor, roles, program, case_id)
        if case["body"]["researcher"] != actor:
            fail(403, "forbidden", "Only this case's researcher can submit its appeal.")
        current = self._current(conn, program, case_id)
        self._expected(current, body["decision_version"])
        if current is None:
            fail(409, "no_assessment", "There is no assessment to contest.")
        text(body["grounds"], "grounds", 8000)
        for appeal in self.store.children(conn, "appeal", program, case_id):
            if not self.store.children(conn, "appeal_resolution", program, appeal["id"]):
                fail(409, "open_appeal", "This case already has an open appeal.")
        return public_record(self.store.insert(conn, "appeal", program, actor,
            {**body, "decision_id": current["id"], "decision_hash": current["hash"]}, parent=case_id))

    def _resolve_appeal(self, conn, actor, roles, program, appeal_id, body):
        self._require(roles, "triager", "security")
        fields(body, ("outcome", "reason"))
        if body["outcome"] not in ("upheld", "reassessment_required"):
            fail(422, "invalid_outcome", "Choose upheld or reassessment_required.")
        appeal = self.store.get(conn, "appeal", program, appeal_id)
        if appeal is None:
            fail(404, "not_found", "Appeal not found.")
        case = self._case(conn, actor, roles, program, appeal["parent"], staff=True)
        self._assessor(conn, actor, roles, program, case)
        if self.store.children(conn, "appeal_resolution", program, appeal_id):
            fail(409, "already_resolved", "Appeal already resolved.")
        text(body["reason"], "reason", 8000)
        # Resolving an appeal never directly changes money or bypasses revisions.
        return public_record(self.store.insert(conn, "appeal_resolution", program, actor, body, parent=appeal_id))

    def read_case(self, token, program, case_id, offset=0, limit=20):
        if type(offset) is not int or type(limit) is not int or not 0 <= offset <= 1000000 or not 1 <= limit <= 50:
            fail(422, "invalid_pagination", "offset must be non-negative and limit between 1 and 50.")
        self._consume_rate(token)
        with self.store.connection() as conn:
            actor = self._identity(conn, token)
            roles = self._roles(conn, actor, program)
            case = self._case(conn, actor, roles, program, case_id)
            def page(kind):
                return [record_summary(self.store.unpack(r)) for r in conn.execute("SELECT * FROM records WHERE program=? AND parent=? AND kind=? ORDER BY revision,at,id LIMIT ? OFFSET ?",
                                                                 (program, case_id, kind, limit, offset))]
            decisions, appeals, awards = (page(kind) for kind in ("decision", "appeal", "award"))
            stage, handoff = self._stage(conn, program, case_id)
            current = self._current(conn, program, case_id)
            latest_award = self._current_award(conn, program, case_id)
            final_award = latest_award if latest_award and current and latest_award["body"]["assessment_hash"] == current["hash"] else None
            claim = self._claim_record(conn, program, case["body"]["submission_id"]) if case["body"].get("submission_id") else None
            counts = {kind: conn.execute("SELECT count(*) FROM records WHERE program=? AND parent=? AND kind=?", (program, case_id, kind)).fetchone()[0]
                      for kind in ("decision", "appeal", "award")}
            return {"case": public_record(case), "version": current["revision"] if current else 0,
                    "stage": stage, "handoff": public_record(handoff) if handoff else None,
                    "accepted": public_record(current) if current else None,
                    "estimated_bounty": claim["body"]["estimate"] if claim else None,
                    "validated_bounty": bounty_view(current) if current else None,
                    "final_award": public_record(final_award) if final_award else None,
                    "final_award_stale": bool(latest_award and not final_award), "award_history": awards,
                    "pagination": {"offset": offset, "limit": limit, "totals": counts},
                    "history": decisions,
                    "appeals": [{**a, "resolutions": [public_record(r) for r in self.store.children(conn, "appeal_resolution", program, a["id"])]} for a in appeals]}

    def read_case_record(self, token, program, case_id, record_id):
        self._consume_rate(token)
        with self.store.connection() as conn:
            actor = self._identity(conn, token)
            self._case(conn, actor, self._roles(conn, actor, program), program, case_id)
            record = self.store.unpack(conn.execute("SELECT * FROM records WHERE program=? AND id=? AND parent=? AND kind IN ('decision','appeal','handoff','award')",
                                                   (program, record_id, case_id)).fetchone())
            if record is None:
                fail(404, "not_found", "Record not found in this case.")
            return public_record(record)

    def read_policy(self, token, program, policy_id):
        self._consume_rate(token)
        with self.store.connection() as conn:
            actor = self._identity(conn, token)
            roles = self._roles(conn, actor, program)
            policy = self.store.get(conn, "policy", program, policy_id)
            publication = self.store.children(conn, "publication", program, policy_id)
            if policy is None or not publication and not roles.intersection({"publisher", "security", "triager"}):
                fail(404, "not_found", "Policy not found.")
            return {"policy": public_record(policy), "publication": [public_record(r) for r in publication],
                    "review_requests": [public_record(r) for r in self.store.children(conn, "policy_review_request", program, policy_id)],
                    "platform_approvals": [public_record(r) for r in self.store.children(conn, "platform_approval", program, policy_id)],
                    "platform_feedback": [public_record(r) for r in self.store.children(conn, "platform_changes_requested", program, policy_id)]}

    def read_active_policy(self, token, program, asset):
        text(asset, "asset", 1000)
        self._consume_rate(token)
        with self.store.connection() as conn:
            self._roles(conn, self._identity(conn, token), program)
            active = self._active_policy(conn, program, asset)
            if active is None:
                fail(404, "not_found", "No published policy for this asset.")
            return {key: public_record(record) for key, record in active.items()}

    def read_guardrails(self, token, program, asset):
        text(asset, "asset", 1000)
        self._consume_rate(token)
        with self.store.connection() as conn:
            self._roles(conn, self._identity(conn, token), program)
            guardrails = self._active_guardrails(conn, program, asset)
            if guardrails is None:
                fail(404, "not_found", "No platform limits for this program/asset.")
            return public_record(guardrails)
