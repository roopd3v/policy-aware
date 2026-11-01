"""Exercise the built service over localhost HTTP using disposable Docker volumes.

No production account, policy or report is used. Credentials stay in memory and are never included in the saved verification record.
"""

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlencode
import uuid


ROOT = Path(__file__).resolve().parents[1]
IMAGE = "bemix-policy-aware-public:1.3.0"


def command(args, **kwargs):
    return subprocess.run(args, check=True, text=True, capture_output=True, **kwargs).stdout.strip()


BOOTSTRAP = r'''
import json,secrets
from dataclasses import replace
from pathlib import Path
from bemix_service.storage import Store
from bemix_service.service import DecisionService
from bemix_pilot.examples import sample_policy,base_report,bind,change_facts,metrics,submission_options,estimation_config,example_inputs,adjustment_rules
from bemix_pilot.engine import canonical
from bemix_pilot.platform_example import example_guardrails
key=secrets.token_bytes(32)
p=Path('/run/bemix-keys/audit.key');p.write_bytes(key);p.chmod(0o600)
s=DecisionService(Store.initialize('/var/lib/bemix/decisions.sqlite',key))
s.add_program('integration-test')
people={};tokens={}
for name,role in [('publisher','publisher'),('triager','triager'),('security','security'),('researcher','researcher')]:
 subject=s.add_principal(name);people[name]=subject;s.grant(subject,'integration-test',role);tokens[name]=s.issue_token(subject)['token']
policy=replace(sample_policy(),version='PUBLIC-CONTAINER-TEST-ONLY',experimental=False)
report=bind(base_report(sample_policy()),policy)
reduced=replace(change_facts(report,restricted_path=True),core=(4,0,0,0),metrics={'restricted':metrics('0.6')})
governance={'source_reference':'SYNTHETIC','calibration_reference':'SYNTHETIC','precedence_reference':'SYNTHETIC','rubrics':{p.id:{m:'SYNTHETIC rubric for integration test' for m in p.weights} for p in policy.profiles},'submission_options':submission_options(),'estimation':estimation_config(),'award_adjustments':adjustment_rules()}
s.install_guardrails('integration-test',example_guardrails(policy,governance),'Trusted platform setup for synthetic container test')
print(json.dumps({'people':people,'tokens':tokens,'policy':canonical(policy),'report':canonical(report),'reduced':canonical(reduced),'governance':governance,'inputs':example_inputs()}))
'''


def main():
    suffix = uuid.uuid4().hex[:12]
    data, keys, name = f"bemix-test-data-{suffix}", f"bemix-test-keys-{suffix}", f"bemix-test-api-{suffix}"
    checks = []
    try:
        for volume in (data, keys):
            command(["docker", "volume", "create", volume])
        mounts = ["-v", data + ":/var/lib/bemix", "-v", keys + ":/run/bemix-keys"]
        bootstrap = json.loads(command(["docker", "run", "--rm", "-i", *mounts, IMAGE, "python", "-"], input=BOOTSTRAP))
        command(["docker", "run", "-d", "--name", name, "--read-only", "--cap-drop", "ALL",
                 "--security-opt", "no-new-privileges", "--pids-limit", "128", "--memory", "512m",
                 "--tmpfs", "/tmp:rw,noexec,nosuid,size=32m", "-p", "127.0.0.1::8000",
                 "-v", data + ":/var/lib/bemix", "-v", keys + ":/run/bemix-keys:ro",
                 "-e", "BEMIX_DB_PATH=/var/lib/bemix/decisions.sqlite", "-e", "BEMIX_AUDIT_KEY_FILE=/run/bemix-keys/audit.key",
                 "-e", "BEMIX_ALLOWED_HOSTS=127.0.0.1,localhost", IMAGE])
        address = command(["docker", "port", name, "8000/tcp"]).splitlines()[0]
        base = "http://" + address
        def ready():
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                try:
                    with urlopen(base + "/readyz", timeout=2) as response:
                        if response.status == 200:
                            return
                except (URLError, TimeoutError, ConnectionError):
                    time.sleep(.1)
            raise RuntimeError("Container failed readiness check.")
        ready()
        def request(who, path, body=None, key=None):
            headers = {"Authorization": "Bearer " + bootstrap["tokens"][who]}
            if body is not None:
                headers.update({"Content-Type": "application/json", "Idempotency-Key": key or str(uuid.uuid4())})
            req = Request(base + "/v1/programs/integration-test" + path,
                          data=json.dumps(body).encode() if body is not None else None, headers=headers)
            try:
                with urlopen(req, timeout=10) as response:
                    return response.status, json.load(response)
            except HTTPError as error:
                return error.code, json.load(error)
        status, draft = request("publisher", "/policies", {"policy": bootstrap["policy"], "governance": bootstrap["governance"]})
        assert status == 200
        active_path = "/active-policy?" + urlencode({"asset": bootstrap["policy"]["asset"]})
        for field, value in (("importance", 0), ("importance", 11), ("weights", {"extent": "0", "reliability": "1"})):
            invalid = json.loads(json.dumps(bootstrap["policy"]))
            invalid["version"] = "INVALID-" + str(uuid.uuid4())
            invalid["profiles"][0][field] = value
            assert request("publisher", "/policies", {"policy": invalid, "governance": bootstrap["governance"]})[0] == 422
        checks.append("Platform-owned severity and context limits reject invalid program settings")
        status, review_request = request("publisher", f"/policies/{draft['id']}/publish",
            {"expected_policy_record_hash": draft["hash"], "reason": "Program requests platform review"})
        assert status == 200 and review_request["body"]["status"] == "pending_platform_review"
        assert request("researcher", active_path)[0] == 404
        assert request("publisher", f"/policies/{draft['id']}/approve", {})[0] == 404
        approval = json.loads(command(["docker", "exec", name, "python", "-m", "bemix_service",
            "--db", "/var/lib/bemix/decisions.sqlite", "--key-file", "/run/bemix-keys/audit.key",
            "approve-policy", "integration-test", draft["id"], "--expected-record-hash", draft["hash"],
            "--reviewer", "synthetic-platform-reviewer", "--reason", "Platform reviewed priorities and test results"]))
        assert approval["publication"]["body"]["platform_approval_hash"] == approval["approval"]["hash"]
        assert request("researcher", active_path)[1]["policy"]["id"] == draft["id"]
        checks.append("Program publication stays pending until platform-local exact-hash approval activates it")
        claims = [{"option_id": "withdrawal_manipulation", "evidence": {"proof": "Synthetic report evidence"}}]
        contradictory = {**bootstrap["inputs"], "facts": {"unauthorized_fund_effect": False}}
        for endpoint in ("/estimates", "/submissions"):
            body = {"asset": bootstrap["policy"]["asset"], "claims": claims, "inputs": contradictory}
            if endpoint == "/submissions":
                body["summary"] = "Contradictory synthetic declaration"
            status, rejected = request("researcher", endpoint, body)
            assert status == 422 and "selected behaviour" in rejected["message"]
        checks.append("Contradictory selection and explicit fields are rejected before estimation or submission")
        status, estimate = request("researcher", "/estimates", {"asset": bootstrap["policy"]["asset"], "claims": claims, "inputs": bootstrap["inputs"]})
        assert status == 200 and estimate["amount"] == "36350.00" and estimate["facts_validated"] is False
        status, submitted = request("researcher", "/submissions", {"asset": bootstrap["policy"]["asset"], "summary": "Synthetic submission", "claims": claims, "inputs": bootstrap["inputs"]})
        assert status == 200 and submitted["estimated_bounty"]["amount"] == "36350.00"
        checks.append("Complete declared submission calculates an exact provisional Estimated Bounty")
        status, case = request("triager", "/cases", {"researcher": bootstrap["people"]["researcher"], "policy_id": draft["id"],
                                                       "reference": "Disposable synthetic case", "submission_id": submitted["submission"]["id"]})
        assert status == 200
        prefix = f"/cases/{case['id']}"
        key = str(uuid.uuid4())
        review = {"claims": [{"option_id": "withdrawal_manipulation", "outcome": "confirmed", "confirmed_option_ids": ["withdrawal_manipulation"], "reason": "Synthetic behaviour verified"}]}
        body = {"report": bootstrap["report"], "expected_version": 0, "reason": "Synthetic initial evidence", "claim_review": review}
        status, initial = request("triager", prefix + "/assessments", body, key)
        assert status == 200 and initial["body"]["result"]["reward"]["amount"] == "31800.00"
        assert request("triager", prefix + "/assessments", body, key)[1]["id"] == initial["id"]
        state = request("researcher", prefix)[1]
        assert state["estimated_bounty"]["amount"] == "36350.00" and state["validated_bounty"]["amount"] == "31800.00"
        checks.append("Triager correction recalculates the Validated Bounty while retaining the original estimate")
        changed = {"claims": [{"option_id": "withdrawal_manipulation", "outcome": "changed", "confirmed_option_ids": ["restricted_withdrawal"], "reason": "Additional prerequisites verified"}]}
        status, correction = request("triager", prefix + "/revisions", {"report": bootstrap["reduced"], "expected_version": 1,
                                     "kind": "correction", "reason": "Responsible triager corrects prerequisites", "claim_review": changed})
        assert status == 200 and correction["kind"] == "decision"
        assert request("researcher", prefix)[1]["version"] == 2
        checks.append("Same assigned triager corrects and recalculates without a peer approval queue")
        assert request("triager", prefix + "/handoff", {"expected_version": 2, "reason": "Forwarded to program security"})[0] == 200
        assert request("triager", prefix + "/revisions", {"report": bootstrap["report"], "expected_version": 2, "kind": "correction", "reason": "wrong stage", "claim_review": review})[0] == 403
        status, program_assessment = request("security", prefix + "/revisions", {"report": bootstrap["report"], "expected_version": 2,
                                             "kind": "correction", "reason": "Program security reassesses impact", "claim_review": review})
        assert status == 200 and program_assessment["body"]["assessment_stage"] == "security"
        assert program_assessment["body"]["result"]["reward"]["amount"] == "31800.00"
        checks.append("Handoff transfers write authority to the program security team")
        status, award = request("security", prefix + "/award", {"expected_version": 3, "expected_award_version": 0,
            "reason": "Program confirms the synthetic award", "adjustment": {"rule_id": "example-quality-adjustment", "delta_minor_units": 50000,
            "reason": "Published quality adjustment"}})
        assert status == 200 and award["body"]["amount"] == "32300.00" and award["body"]["calculated_bounty_minor_units"] == 3180000
        state = request("researcher", prefix)[1]
        assert state["validated_bounty"]["bemix"]["score"] == 17 and state["validated_bounty"]["amount"] == "31800.00"
        assert state["final_award"]["body"]["amount"] == "32300.00"
        checks.append("Final Award records program confirmation and a separate permitted adjustment without changing the score")
        status, appeal = request("researcher", prefix + "/appeals", {"decision_version": 3, "grounds": "Synthetic question"})
        assert status == 200
        assert request("security", f"/appeals/{appeal['id']}/resolve", {"outcome": "upheld", "reason": "Responsible program team explains its assessment"})[0] == 200
        assert request("researcher", prefix)[1]["stage"] == "security"
        checks.append("Researcher question is answered without reassignment or automatic score changes")
        later = json.loads(json.dumps(bootstrap["policy"]))
        later["version"] = "PUBLIC-CONTAINER-NEXT-ONLY"
        later["profiles"][0]["importance"] = 9
        status, later_draft = request("publisher", "/policies", {"policy": later, "governance": bootstrap["governance"]})
        assert status == 200
        assert request("publisher", f"/policies/{later_draft['id']}/publish", {"expected_policy_record_hash": later_draft["hash"], "reason": "Proposed new priority"})[0] == 200
        assert request("researcher", active_path)[1]["policy"]["id"] == draft["id"]
        feedback = json.loads(command(["docker", "exec", name, "python", "-m", "bemix_service",
            "--db", "/var/lib/bemix/decisions.sqlite", "--key-file", "/run/bemix-keys/audit.key",
            "return-policy", "integration-test", later_draft["id"], "--expected-record-hash", later_draft["hash"],
            "--reviewer", "synthetic-platform-reviewer", "--reason", "Priority is within limits but needs further justification"]))
        assert feedback["body"]["status"] == "changes_requested"
        assert request("researcher", active_path)[1]["policy"]["id"] == draft["id"]
        checks.append("New settings require fresh platform review and can be returned without changing live report snapshots")
        command(["docker", "restart", name])
        # Docker may assign a different ephemeral host port on restart.
        address = command(["docker", "port", name, "8000/tcp"]).splitlines()[0]
        base = "http://" + address
        ready()
        state = request("researcher", prefix)[1]
        assert state["version"] == 3 and len(state["history"]) == 3 and len(state["appeals"]) == 1
        assert state["estimated_bounty"]["amount"] == "36350.00" and state["validated_bounty"]["amount"] == "31800.00" and state["final_award"]["body"]["amount"] == "32300.00"
        checks.append("Container restart preserves Estimated Bounty, Validated Bounty, Final Award and their histories")
        assert command(["docker", "exec", name, "id", "-u"]) == "10001"
        checkpoint = json.loads(command(["docker", "exec", name, "python", "-m", "bemix_service", "--db", "/var/lib/bemix/decisions.sqlite", "--key-file", "/run/bemix-keys/audit.key", "verify"]))
        assert checkpoint["seq"] > 0
        checks.append("Non-root, read-only container and authenticated audit-chain verification")
        result = {"status": "passed", "verified_at_utc": datetime.now(timezone.utc).isoformat(),
                  "image_id": command(["docker", "image", "inspect", IMAGE, "--format", "{{.Id}}"]),
                  "checks": checks, "synthetic_data_only": True, "public_network_service_started": False,
                  "production_accounts_used": False, "tls_termination_tested": False}
        inputs = [ROOT / "deploy/Dockerfile", ROOT / "requirements-service.lock", Path(__file__).resolve()]
        for folder in ("bemix_pilot", "bemix_service"):
            inputs.extend((ROOT / folder).glob("*.py"))
        result["tested_input_sha256"] = {p.relative_to(ROOT).as_posix(): sha256(p.read_bytes()).hexdigest() for p in sorted(inputs)}
        (ROOT / "artifacts/container-smoke.json").write_text(json.dumps(result, indent=2) + "\n")
        print("PASS: " + "; ".join(checks))
    except Exception:
        logs = subprocess.run(["docker", "logs", "--tail", "60", name], capture_output=True, text=True)
        if logs.stdout or logs.stderr:
            print("Container diagnostic output:\n" + logs.stdout + logs.stderr)
        raise
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        for volume in (data, keys):
            subprocess.run(["docker", "volume", "rm", volume], capture_output=True)


if __name__ == "__main__":
    main()
