from dataclasses import replace
import uuid

from fastapi.testclient import TestClient

from bemix_pilot.engine import canonical
from bemix_service.api import create_app
from support import Harness


class WorkflowTests(Harness):
    def handoff(self, case, version=1):
        return self.command("triager", "handoff", {"expected_version": version, "reason": "Triage completed and forwarded"}, case["id"])

    def revise(self, case, who="triager", report=None, version=1, kind="correction"):
        return self.command(who, "revise", {"report": canonical(report or self.reduced_report()),
            "expected_version": version, "kind": kind, "reason": "Evidence-based correction by responsible assessor"}, case["id"])

    def test_program_author_can_request_review_but_only_platform_approval_activates(self):
        draft = self.command("publisher", "draft_policy", {"policy": canonical(self.policy), "governance": self.governance()})
        request = self.command("publisher", "publish_policy", {"expected_policy_record_hash": draft["hash"], "reason": "Program submits its configuration"}, draft["id"])
        self.assertEqual(request["actor"], draft["actor"])
        self.assertEqual(request["body"]["status"], "pending_platform_review")
        self.assert_error(404, lambda: self.service.read_active_policy(self.tokens["researcher"]["token"], "alpha", self.policy.asset))
        publication = self.platform_approve(draft)["publication"]
        self.assertNotEqual(publication["actor"], draft["actor"])

    def test_assigned_triager_corrects_directly_without_peer_approval(self):
        case = self.case(); previous = self.state(case)["accepted"]
        decision = self.revise(case)
        self.assertEqual(decision["kind"], "decision")
        self.assertEqual(decision["actor"], self.people["triager"])
        self.assertEqual(decision["body"]["result"]["reward"]["amount"], "3500.00")
        self.assertEqual(decision["body"]["assessment_stage"], "triage")
        self.assertEqual(self.state(case)["version"], 2)
        self.assertEqual(self.state(case)["history"][0]["hash"], previous["hash"])

    def test_other_triager_cannot_change_the_assigned_triage_assessment(self):
        case = self.case()
        self.assert_error(403, lambda: self.revise(case, "other_triager"))
        self.assertEqual(self.state(case)["version"], 1)

    def test_company_can_designate_a_different_assigned_triager(self):
        draft = self.publish()
        case = self.command("triager", "create_case", {"researcher": self.people["researcher"], "policy_id": draft["id"],
            "reference": "ASSIGNED-REPORT", "assigned_triager": self.people["other_triager"]})
        body = {"report": canonical(self.report), "expected_version": 0, "reason": "Assigned assessor checks the evidence"}
        self.assert_error(403, lambda: self.command("triager", "initial", body, case["id"]))
        decision = self.command("other_triager", "initial", body, case["id"])
        self.assertEqual(decision["actor"], self.people["other_triager"])

    def test_security_team_cannot_reassess_before_handoff(self):
        case = self.case()
        self.assert_error(403, lambda: self.revise(case, "security"))

    def test_handoff_retains_triage_record_and_transfers_edit_authority(self):
        case = self.case(); initial = self.state(case)["accepted"]
        handoff = self.handoff(case)
        state = self.state(case)
        self.assertEqual(state["stage"], "security")
        self.assertEqual(handoff["body"]["triage_decision_hash"], initial["hash"])
        self.assertEqual(state["accepted"], initial)
        self.assert_error(403, lambda: self.revise(case))

    def test_security_team_can_lower_then_raise_with_recorded_reasons(self):
        case = self.case(); self.handoff(case)
        lower = self.revise(case, "security")
        self.assertEqual(lower["body"]["result"]["reward"]["amount"], "3500.00")
        higher = self.revise(case, "security", self.report, version=2)
        self.assertEqual(higher["body"]["result"]["reward"]["amount"], "31800.00")
        self.assertEqual(higher["body"]["assessment_stage"], "security")
        self.assertEqual(self.state(case)["version"], 3)

    def test_disagreement_never_creates_another_triager_or_changes_stage(self):
        case = self.case()
        appeal = self.command("researcher", "appeal", {"decision_version": 1, "grounds": "Please explain the scope assessment"}, case["id"])
        state = self.state(case)
        self.assertEqual(state["stage"], "triage")
        self.assertEqual(state["case"]["body"]["assigned_triager"], self.people["triager"])
        self.assertEqual(state["version"], 1)
        self.command("triager", "resolve_appeal", {"outcome": "upheld", "reason": "Responsible triager explains the evidence"}, appeal["id"])
        self.assertEqual(self.state(case)["version"], 1)

    def test_responsible_security_team_can_answer_its_own_assessment(self):
        case = self.case(); self.handoff(case); self.revise(case, "security")
        appeal = self.command("researcher", "appeal", {"decision_version": 2, "grounds": "Question about the new assessment"}, case["id"])
        response = self.command("security", "resolve_appeal", {"outcome": "upheld", "reason": "Program team explains its correction"}, appeal["id"])
        self.assertEqual(response["actor"], self.people["security"])

    def test_correction_still_requires_a_reason(self):
        case = self.case()
        self.assert_error(422, lambda: self.command("triager", "revise", {"report": canonical(self.reduced_report()),
            "expected_version": 1, "kind": "correction", "reason": " "}, case["id"]))

    def test_a_reduction_cannot_silently_be_labelled_an_improvement(self):
        case = self.case()
        error = self.assert_error(409, lambda: self.revise(case, kind="improvement"))
        self.assertEqual(error.code, "correction_required")
        self.assertEqual(self.state(case)["version"], 1)
        self.revise(case, kind="correction")
        self.assertEqual(self.state(case)["version"], 2)

    def test_pending_evidence_can_be_recorded_by_responsible_assessor(self):
        case = self.case()
        pending = replace(self.report, eligibility="pending")
        decision = self.revise(case, report=pending)
        self.assertEqual(decision["body"]["result"]["reward"]["status"], "pending")
        self.assertIsNone(decision["body"]["result"]["reward"]["amount"])

    def test_handoff_is_versioned_and_idempotent(self):
        case = self.case()
        self.assert_error(409, lambda: self.handoff(case, 0))
        key = str(uuid.uuid4()); body = {"expected_version": 1, "reason": "Forward to the program team"}
        first = self.command("triager", "handoff", body, case["id"], key)
        self.assertEqual(first, self.command("triager", "handoff", body, case["id"], key))
        self.assert_error(403, lambda: self.handoff(case))

    def test_no_forced_distinct_people_when_company_assigns_both_roles(self):
        case = self.case()
        self.service.grant(self.people["triager"], "alpha", "security")
        self.handoff(case)
        decision = self.revise(case)
        self.assertEqual(decision["body"]["assessment_stage"], "security")

    def test_http_workflow_uses_direct_triage_correction_and_security_handoff(self):
        case = self.case(); base = f"/v1/programs/alpha/cases/{case['id']}"
        with TestClient(create_app(self.service, ["testserver"])) as client:
            def post(who, path, body):
                return client.post(path, json=body, headers={"Authorization": "Bearer " + self.tokens[who]["token"], "Idempotency-Key": str(uuid.uuid4())})
            correction = {"report": canonical(self.reduced_report()), "expected_version": 1, "kind": "correction", "reason": "Responsible triager correction"}
            response = post("triager", base + "/revisions", correction)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["kind"], "decision")
            self.assertEqual(post("triager", base + "/handoff", {"expected_version": 2, "reason": "Ready for security team"}).status_code, 200)
            response = post("security", base + "/revisions", {"report": canonical(self.report), "expected_version": 2, "kind": "correction", "reason": "Program reassessment"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["body"]["assessment_stage"], "security")
            self.assertEqual(client.post("/v1/programs/alpha/proposals/unused/resolve").status_code, 404)
