"""Executable contract for the behaviour described in the public Gist."""

from dataclasses import replace
from fractions import Fraction as F

from bemix_pilot.engine import Fact, evaluate
from bemix_pilot.examples import REF, example_inputs
from bemix_pilot.lifecycle_example import demonstration
from support import Harness


class GistContractTests(Harness):
    def claim(self, identifier="withdrawal_manipulation"):
        return {"option_id": identifier, "evidence": {"proof": "Synthetic report evidence"}}

    def test_the_gist_examples_are_actual_engine_results_at_three_stages(self):
        states = demonstration()["states"]
        self.assertEqual([s["stage"] for s in states], ["Estimated Bounty", "Validated Bounty", "Final Award"])
        self.assertEqual([s["amount"] for s in states], ["36350.00", "31800.00", "32300.00"])
        self.assertFalse(states[0]["facts_validated"])
        self.assertEqual(states[-1]["bemix_score_unchanged"], 17)

    def test_identical_declared_and_assessed_values_produce_identical_arithmetic(self):
        self.publish()
        estimate = self.service.preview_estimate(self.tokens["researcher"]["token"], "alpha", {
            "asset": self.policy.asset, "claims": [self.claim()], "inputs": example_inputs()})
        prepared = estimate["prepared_inputs"]
        from bemix_pilot.serialization import report_from_dict
        from bemix_pilot.engine import canonical
        raw = canonical(self.report)
        raw.update(core=prepared["core"], facts=prepared["facts"], metrics=prepared["metrics"])
        assessment = evaluate(self.policy, report_from_dict(raw))
        self.assertEqual(estimate["amount"], assessment["reward"]["amount"])
        self.assertEqual(estimate["severity"], assessment["classification"]["severity"])

    def test_unverified_extra_claim_can_remain_pending_if_it_cannot_change_the_amount(self):
        policy = self.publish()
        claims = [self.claim(), self.claim("restricted_keys")]
        inputs = example_inputs()
        submitted = self.command("researcher", "submit", {"asset": self.policy.asset, "summary": "Two claims", "claims": claims, "inputs": inputs})
        self.assertEqual(submitted["estimated_bounty"]["amount"], "36350.00")
        case = self.command("triager", "create_case", {"researcher": self.people["researcher"], "policy_id": policy["id"],
            "submission_id": submitted["submission"]["id"], "reference": "LOWER-CLAIM-CASE"})
        report = replace(self.report, facts={**self.report.facts,
            "key_exfiltration": Fact(None, REF), "restricted_keys": Fact(True, REF)})
        from bemix_pilot.engine import canonical
        decision = self.command("triager", "initial", {"report": canonical(report), "expected_version": 0,
            "reason": "Primary impact demonstrated; smaller independent impact still uncertain",
            "claim_review": {"claims": [
                {"option_id": "withdrawal_manipulation", "outcome": "confirmed", "confirmed_option_ids": ["withdrawal_manipulation"], "reason": "Primary effect demonstrated"},
                {"option_id": "restricted_keys", "outcome": "pending", "confirmed_option_ids": [], "reason": "Still under analysis"}]}
            }, case["id"])
        self.assertEqual(decision["body"]["result"]["reward"]["amount"], "31800.00")

    def test_a_program_cannot_receive_another_programs_estimate_or_decision_as_authority(self):
        case = self.case()
        self.assert_error(404, lambda: self.service.read_case(self.tokens["security"]["token"], "beta", case["id"]))

    def test_unnecessary_unknown_does_not_create_a_fake_new_fact(self):
        report = replace(self.report, facts={**self.report.facts, "global_limit_example": Fact(None, REF)})
        result = evaluate(self.policy, report)
        self.assertEqual(result["reward"]["amount"], "31800.00")
        self.assertIsNone(report.facts["global_limit_example"].value)
        self.assertEqual(result["reward"]["uncertainty"]["status"], "does_not_change_result")

    def test_zero_weight_input_is_not_required_for_estimation(self):
        profile = replace(self.policy.profiles[0], weights={"extent": F(1), "reliability": F(0)})
        policy = replace(self.policy, version="ZERO-WEIGHT", profiles=(profile, *self.policy.profiles[1:]))
        self.publish(policy)
        result = self.service.preview_estimate(self.tokens["researcher"]["token"], "alpha", {
            "asset": policy.asset, "claims": [self.claim()], "inputs": {"core": [4, 0, 0, 1], "metrics": {"primary": {"extent": "0.6"}}}})
        self.assertEqual(result["amount"], "31800.00")
