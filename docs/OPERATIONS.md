# Operations

Supported profile: CPython 3.14, Linux x86_64, one ASGI worker and SQLite on a local persistent disk. Put TLS and edge rate limiting in front of the API.
Distributed high availability, external identity and payment integration are separate deployment work. No live BugEx integration is included.

## Install and verify

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-test.lock
.venv/bin/python scripts/build_examples.py --check
.venv/bin/python scripts/verify.py
```

Locks are for the stated runtime. Other Python/platform wheels need their own reviewed hashes. Package metadata and the verification script require CPython 3.14.x; successful source execution on another interpreter does not extend the supported runtime. The pure calculation engine has no third-party dependencies.

## Container

```sh
export BEMIX_ALLOWED_HOSTS=decisions.example.org,localhost,127.0.0.1
docker compose -f deploy/compose.yaml build
docker compose -f deploy/compose.yaml --profile operator run --rm operator init
docker compose -f deploy/compose.yaml --profile operator run --rm operator program example-program
docker compose -f deploy/compose.yaml --profile operator run --rm operator principal "Assigned triager"
docker compose -f deploy/compose.yaml --profile operator run --rm operator grant SUBJECT_ID example-program triager
docker compose -f deploy/compose.yaml --profile operator run --rm operator token SUBJECT_ID --days 30
docker compose -f deploy/compose.yaml up -d api
```

Create individual principals for researcher, assigned triager, program security and policy management. The organization chooses who holds each role; there is no forced second-person approval per correction. Tokens are printed once, stored as digests, expire, and can be revoked. CLI administration is local only.

Program policy roles can draft and request publication, but cannot activate their own configuration. The platform operator first installs a reviewed template of allowed ranges, criteria and acceptance cases, then reviews each requested version and approves its exact hash. That approval activates the version.
Keep platform administration under platform host/key access; never give it to program accounts. Ordinary report corrections remain with the assigned triager or program security team.

The local operator commands are `guardrails PROGRAM TEMPLATE --reason ...`, `approve-policy PROGRAM POLICY_ID --expected-record-hash ... --reviewer ... --reason ...`, and `return-policy` with the same review arguments. See [PLATFORM_GOVERNANCE.md](PLATFORM_GOVERNANCE.md) for the full workflow and commands. `POST /policies/{id}/publish` returns a pending review request, not activation.

The API port is bound to localhost by default. Adapt `nginx.conf.example` to the real domain and certificate. The service image is non-root and read-only, with persistent data and a separate read-only audit-key mount. Checkpoints and backups must be retained outside the same administrative failure domain.

## Backup and integrity

The operator supports `verify`, `checkpoint OUTPUT` and `backup OUTPUT` using `--db PATH --key-file PATH`. Backup uses SQLite's backup API and writes its matching checkpoint. Output files are new and not silently overwritten.

Stop the API before restoring a backup. Preserve the old files for investigation, restore permissions and the corresponding key, then run `verify --checkpoint EXTERNAL_CHECKPOINT`. An older valid database is detectable against a checkpoint retained outside that database. Do not disregard a mismatch merely to restart.

Protect and back up the audit key separately. Key replacement is not an in-place rotation procedure: it requires a planned migration or a new store linked to retained checkpoints. Tokens can be rotated by issuing and revoking credentials.

## Updates

Version 1.3.0 retains schema 3 and uses core contract 1.3. New platform templates, mandatory approval and importance-aware placement require an explicit transition from an earlier release. Publications without a matching platform approval are not accepted as live by the new service. Do not fabricate retrospective approvals or rewrite historical decisions. Review and approve future configurations, and preserve earlier report snapshots under their original contract.

The estimate/validated/final-award lifecycle remains in place. It rejects new or edited researcher inputs that contradict their selected option; earlier stored declarations remain in history. Correct historical inconsistent
declarations through the responsible assessor's recorded findings rather than silently rewriting them. There is no automatic migration of an older database. Existing assessments are tied to a core contract and must be reproduced before revision. Semantic or schema changes need a reviewed migration and restore test.

The local HTTP check is `python3 scripts/smoke_container.py`, after building `bemix-policy-aware-public:1.3.0`. It uses synthetic data and disposable Docker volumes, tests estimates through program award and restart, then removes the resources. 
