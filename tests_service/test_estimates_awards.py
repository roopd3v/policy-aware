from copy import deepcopy
from dataclasses import replace
import uuid

from fastapi.testclient import TestClient

from bemix_pilot.engine import EvidenceError, canonical
from bemix_pilot.examples import example_inputs
from bemix_service.api import create_app
from support import Harness


class EstimateAwardTests(Harness):
    def declaration(self):
        return [{"option_id": "withdrawal_manipulation", "evidence": {"proof": "Synthetic reference"}}]

    def claim_review(self):
        return {"claims": [{"option_id": "withdrawal_manipulation", "outcome": "confirmed",
                            "confirmed_option_ids": ["withdrawal_manipulation"], "reason": "Assessor confirms the declared effect"}]}

    def submitted_case(self):
        policy = self.publish()
        submitted = self.command("researcher", "submit", {"asset": self.policy.asset, "summary": "Synthetic", "claims": self.declaration(), "inputs": example_inputs(),
            "expected_policy_record_hash": policy["hash"]})
        case = self.command("triager", "create_case", {"researcher": self.people["researcher"], "policy_id": policy["id"],
            "submission_id": submitted["submission"]["id"], "reference": "ESTIMATE-CASE"})
        return case, submitted

    def handoff(self, case, version=1):
        return self.command("triager", "handoff", {"expected_version": version, "reason": "Forwarded to program"}, case["id"])

    def award(self, case, version=1, award_version=0, adjustment=None, who="security"):
        body = {"expected_version": version, "expected_award_version": award_version, "reason": "Program confirms its award"}
        if adjustment is not None:
            body["adjustment"] = adjustment
        return self.command(who, "award", body, case["id"])

    def test_complete_submission_immediately_returns_a_concrete_estimate(self):
        _, submitted = self.submitted_case()
        estimate = submitted["estimated_bounty"]
        self.assertEqual(estimate["stage"], "estimated_bounty")
        self.assertEqual(estimate["amount"], "36350.00")
        self.assertFalse(estimate["facts_validated"])
        self.assertTrue(estimate["provisional"])
        self.assertEqual(estimate["bemix"]["score"], 17)
        self.assertEqual(estimate["severity"], "Critical")

    def test_triager_can_confirm_the_submitted_inputs_without_reentering_them(self):
        case, submitted = self.submitted_case()
        self.command("triager", "initial", {"use_submission_inputs": True, "expected_version": 0,
            "reason": "Confirmed submitted values and PoC", "claim_review": self.claim_review()}, case["id"])
        state = self.state(case)
        self.assertEqual(state["estimated_bounty"]["amount"], "36350.00")
        self.assertEqual(state["validated_bounty"]["amount"], "36350.00")
        self.assertIsNone(state["final_award"])

    def test_researcher_estimate_and_corrected_validated_bounty_remain_separate(self):
        case, _ = self.submitted_case()
        self.command("triager", "initial", {"report": canonical(self.report), "expected_version": 0,
            "reason": "Reliability corrected from declared 0.8 to 0.6", "claim_review": self.claim_review()}, case["id"])
        state = self.state(case)
        self.assertEqual(state["estimated_bounty"]["amount"], "36350.00")
        self.assertEqual(state["validated_bounty"]["amount"], "31800.00")
        self.assertIsNone(state["final_award"])

    def test_editing_researcher_values_recalculates_and_retains_original_estimate(self):
        _, submitted = self.submitted_case()
        sid = submitted["submission"]["id"]
        values = example_inputs(); values["metrics"]["primary"]["reliability"] = "0.6"
        changed = self.command("researcher", "change_claims", {"expected_version": 1, "inputs": values}, sid)
        self.assertEqual(changed["body"]["estimate"]["amount"], "31800.00")
        with self.store.connection() as conn:
            history = self.store.children(conn, "claim", "alpha", sid)
        self.assertEqual([c["body"]["estimate"]["amount"] for c in history], ["36350.00", "31800.00"])
        original = self.service.read_submission_version(self.tokens["researcher"]["token"], "alpha", sid, 1)
        self.assertEqual(original["body"]["estimate"]["amount"], "36350.00")
        self.assert_error(404, lambda: self.service.read_submission_version(self.tokens["other"]["token"], "alpha", sid, 1))

    def test_read_only_form_estimate_does_not_create_a_validated_decision(self):
        self.publish()
        result = self.service.preview_estimate(self.tokens["researcher"]["token"], "alpha",
            {"asset": self.policy.asset, "claims": self.declaration(), "inputs": example_inputs()})
        self.assertEqual(result["amount"], "36350.00")
        with self.store.connection() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM records WHERE kind IN ('decision','award','submission')").fetchone()[0], 0)

    def test_missing_metric_returns_a_conditional_range_not_an_invented_score(self):
        self.publish()
        values = example_inputs(); values["metrics"]["primary"].pop("extent")
        result = self.service.preview_estimate(self.tokens["researcher"]["token"], "alpha",
            {"asset": self.policy.asset, "claims": self.declaration(), "inputs": values})
        self.assertIsNone(result["amount"])
        self.assertEqual(result["status"], "conditional")
        self.assertEqual(result["range"]["lower"], "22700.00")

    def test_policy_can_derive_criteria_from_bemix_so_selection_and_four_scores_are_enough(self):
        governance = self.governance()
        governance["estimation"]["metric_bindings"] = {"primary": {"extent": "E", "reliability": "X"}}
        self.trust_guardrails(self.policy, governance)
        draft = self.command("publisher", "draft_policy", {"policy": canonical(self.policy), "governance": governance})
        self.command("publisher", "publish_policy", {"expected_policy_record_hash": draft["hash"], "reason": "Published synthetic bindings"}, draft["id"])
        self.platform_approve(draft)
        result = self.service.preview_estimate(self.tokens["researcher"]["token"], "alpha",
            {"asset": self.policy.asset, "claims": self.declaration(), "inputs": {"core": [4, 0, 0, 1]}})
        self.assertEqual(result["amount"], "38625.00")
        self.assertEqual(result["prepared_inputs"]["metrics"]["primary"]["reliability"]["value"], "1/2")

    def test_estimate_uses_one_asset_and_policy_currency_only(self):
        self.publish()
        values = example_inputs(); values["currency"] = "USDT"
        with self.assertRaises(EvidenceError):
            self.service.preview_estimate(self.tokens["researcher"]["token"], "alpha",
                {"asset": self.policy.asset, "claims": self.declaration(), "inputs": values})

    def test_changed_form_policy_is_not_silently_substituted(self):
        old = self.publish()
        self.publish(replace(self.policy, version="TEST-APPROVED-2"))
        self.assert_error(409, lambda: self.command("researcher", "submit", {"asset": self.policy.asset, "claims": self.declaration(),
            "summary": "Old form", "inputs": example_inputs(), "expected_policy_record_hash": old["hash"]}))

    def test_corrected_values_default_to_normal_recalculation(self):
        case = self.case()
        revised = self.command("triager", "revise", {"report": canonical(self.reduced_report()), "expected_version": 1,
            "reason": "Corrected prerequisites"}, case["id"])
        self.assertEqual(revised["body"]["kind"], "correction")
        self.assertEqual(revised["body"]["result"]["reward"]["amount"], "3500.00")

    def test_program_confirms_base_and_authorized_adjustment_separately(self):
        case = self.case(); self.handoff(case)
        adjustment = {"rule_id": "example-quality-adjustment", "delta_minor_units": 50000, "reason": "Synthetic permitted quality adjustment"}
        awarded = self.award(case, adjustment=adjustment)
        self.assertEqual(awarded["body"]["calculated_bounty_minor_units"], 3180000)
        self.assertEqual(awarded["body"]["adjustment_minor_units"], 50000)
        self.assertEqual(awarded["body"]["amount"], "32300.00")
        state = self.state(case)
        self.assertEqual(state["validated_bounty"]["amount"], "31800.00")
        self.assertEqual(state["validated_bounty"]["bemix"]["score"], 17)
        self.assertEqual(state["final_award"]["body"]["status"], "program_confirmed")
        self.assertFalse(awarded["body"]["payment_executed"])

    def test_researcher_and_triager_cannot_confirm_final_awards(self):
        case = self.case(); self.handoff(case)
        for who in ("researcher", "triager"):
            self.assert_error(403, lambda: self.award(case, who=who))

    def test_award_requires_handoff_and_resolved_required_inputs(self):
        case = self.case()
        self.assert_error(403, lambda: self.award(case))
        self.handoff(case)
        pending = replace(self.report, eligibility="pending")
        self.command("security", "revise", {"report": canonical(pending), "expected_version": 1, "reason": "Need relevant evidence"}, case["id"])
        with self.assertRaises(EvidenceError):
            self.award(case, version=2)

    def test_unpublished_or_out_of_range_adjustments_are_rejected(self):
        case = self.case(); self.handoff(case)
        for identifier, delta in (("not-published", 50000), ("example-quality-adjustment", 100001)):
            with self.assertRaises(EvidenceError):
                self.award(case, adjustment={"rule_id": identifier, "delta_minor_units": delta, "reason": "test"})

    def test_permitted_negative_adjustment_and_award_revision_keep_history(self):
        case = self.case(); self.handoff(case)
        self.award(case)
        changed = self.award(case, award_version=1, adjustment={"rule_id": "example-quality-adjustment", "delta_minor_units": -50000, "reason": "Program permitted deduction"})
        self.assertEqual(changed["body"]["amount"], "31300.00")
        self.assertEqual(len(self.state(case)["award_history"]), 2)

    def test_adjustment_cannot_cross_a_cap_without_an_explicit_exception(self):
        case = self.case(); self.handoff(case)
        maximal = deepcopy(canonical(self.report))
        maximal["metrics"]["primary"]["extent"]["value"] = "1"
        maximal["metrics"]["primary"]["reliability"]["value"] = "1"
        self.command("security", "revise", {"report": maximal, "expected_version": 1, "reason": "Confirmed maximum profile extent"}, case["id"])
        with self.assertRaises(EvidenceError):
            self.award(case, version=2, adjustment={"rule_id": "example-quality-adjustment", "delta_minor_units": 1, "reason": "Attempt to cross a cap"})

    def test_explicit_published_exception_can_authorize_an_award_above_the_band(self):
        governance = self.governance()
        governance["award_adjustments"] = [{"id": "exceptional-impact", "description": "Synthetic explicit exceptional award rule",
            "min_delta_minor_units": 0, "max_delta_minor_units": 10000000,
            "override_calculation_limits": True, "min_award_minor_units": 0, "max_award_minor_units": 15000000}]
        self.trust_guardrails(self.policy, governance)
        draft = self.command("publisher", "draft_policy", {"policy": canonical(self.policy), "governance": governance})
        self.command("publisher", "publish_policy", {"expected_policy_record_hash": draft["hash"], "reason": "Published exceptional award rule"}, draft["id"])
        self.platform_approve(draft)
        case = self.command("triager", "create_case", {"policy_id": draft["id"], "researcher": self.people["researcher"], "reference": "EXCEPTION-CASE"})
        self.command("triager", "initial", {"report": canonical(self.report), "expected_version": 0, "reason": "Evidence assessed"}, case["id"])
        self.handoff(case)
        award = self.award(case, adjustment={"rule_id": "exceptional-impact", "delta_minor_units": 5000000, "reason": "Program applies published exception"})
        self.assertEqual(award["body"]["amount"], "81800.00")
        self.assertEqual(self.state(case)["validated_bounty"]["bemix"]["score"], 17)

    def test_concurrent_awards_accept_only_one_matching_version(self):
        from concurrent.futures import ThreadPoolExecutor
        from bemix_service.service import ServiceError
        case = self.case(); self.handoff(case)
        def confirm(_):
            try:
                return self.award(case)["kind"]
            except ServiceError as error:
                return error.status
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(confirm, range(2)))
        self.assertCountEqual(results, ["award", 409])

    def test_published_bindings_apply_equally_to_estimated_and_validated_calculations(self):
        governance = self.governance()
        governance["estimation"]["metric_bindings"] = {"primary": {"extent": "E", "reliability": "X"}}
        self.trust_guardrails(self.policy, governance)
        draft = self.command("publisher", "draft_policy", {"policy": canonical(self.policy), "governance": governance})
        self.command("publisher", "publish_policy", {"expected_policy_record_hash": draft["hash"], "reason": "Rule defines BEMIX bindings"}, draft["id"])
        self.platform_approve(draft)
        submitted = self.command("researcher", "submit", {"asset": self.policy.asset, "summary": "All required fields", "claims": self.declaration(), "inputs": {"core": [4, 0, 0, 1]}})
        case = self.command("triager", "create_case", {"researcher": self.people["researcher"], "policy_id": draft["id"], "reference": "BINDING-CASE", "submission_id": submitted["submission"]["id"]})
        self.command("triager", "initial", {"use_submission_inputs": True, "expected_version": 0, "reason": "All declared values confirmed", "claim_review": self.claim_review()}, case["id"])
        state = self.state(case)
        self.assertEqual(state["estimated_bounty"]["amount"], state["validated_bounty"]["amount"])
        self.assertEqual(state["validated_bounty"]["amount"], "38625.00")

    def test_assessment_revision_invalidates_current_award_without_erasing_it(self):
        case = self.case(); self.handoff(case); old = self.award(case)
        self.command("security", "revise", {"report": canonical(self.reduced_report()), "expected_version": 1, "reason": "Corrected after confirmation"}, case["id"])
        state = self.state(case)
        self.assertIsNone(state["final_award"])
        self.assertTrue(state["final_award_stale"])
        self.assertEqual(state["award_history"][0]["hash"], old["hash"])
        self.assert_error(409, lambda: self.award(case, version=1, award_version=1))
        self.assertEqual(self.award(case, version=2, award_version=1)["body"]["amount"], "3500.00")

    def test_final_award_is_idempotent_and_version_checked(self):
        case = self.case(); self.handoff(case)
        key = str(uuid.uuid4()); body = {"expected_version": 1, "expected_award_version": 0, "reason": "Confirmed"}
        first = self.command("security", "award", body, case["id"], key)
        self.assertEqual(first, self.command("security", "award", body, case["id"], key))
        self.assert_error(409, lambda: self.award(case))

    def test_http_estimated_validated_final_sequence(self):
        policy = self.publish()
        with TestClient(create_app(self.service, ["testserver"])) as client:
            def post(who, suffix, body):
                return client.post("/v1/programs/alpha" + suffix, json=body,
                    headers={"Authorization": "Bearer " + self.tokens[who]["token"], "Idempotency-Key": str(uuid.uuid4())})
            submitted = post("researcher", "/submissions", {"asset": self.policy.asset, "summary": "Full flow", "claims": self.declaration(), "inputs": example_inputs()})
            self.assertEqual(submitted.status_code, 200, submitted.text)
            self.assertEqual(submitted.json()["estimated_bounty"]["amount"], "36350.00")
            case = post("triager", "/cases", {"researcher": self.people["researcher"], "policy_id": policy["id"], "reference": "FULL-FLOW", "submission_id": submitted.json()["submission"]["id"]}).json()
            prefix = f"/cases/{case['id']}"
            assessed = post("triager", prefix + "/assessments", {"report": canonical(self.report), "expected_version": 0, "reason": "Reliability corrected", "claim_review": self.claim_review()})
            self.assertEqual(assessed.status_code, 200, assessed.text)
            self.assertEqual(post("triager", prefix + "/handoff", {"expected_version": 1, "reason": "Triage complete"}).status_code, 200)
            award = post("security", prefix + "/award", {"expected_version": 1, "expected_award_version": 0, "reason": "Confirmed"})
            self.assertEqual(award.status_code, 200, award.text)
            self.assertEqual(award.json()["body"]["amount"], "31800.00")


if __name__ == "__main__":
    import unittest
    unittest.main()
