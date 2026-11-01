from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from bemix_pilot.engine import EvidenceError
from bemix_pilot.examples import REF, bind, change_facts, metrics, scenarios
from bemix_pilot.revision_demo import counterexample
from bemix_pilot.revisions import evaluate_revision
from bemix_pilot.serialization import document


class RevisionTests(unittest.TestCase):
    def setUp(self):
        self.cases = {name: (policy, report) for name, _, policy, report in scenarios()}
        self.policy, self.before = self.cases["A"]

    def revise(self, after, kind="improvement"):
        return evaluate_revision(self.policy, self.before, after, kind=kind, reason_reference=REF)

    def test_positive_precedence_drop_is_blocked(self):
        policy, before, after = counterexample()
        result = evaluate_revision(policy, before, after, kind="improvement", reason_reference=REF)
        self.assertEqual(result["previous_decision"]["reward"]["amount"], "40900.00")
        self.assertEqual(result["proposed_decision"]["reward"]["amount"], "22700.00")
        self.assertEqual(result["status"], "needs_review")
        self.assertIsNone(result["applicable_decision"])
        self.assertIn("profile:primary:removed", result["improvement_contract_violations"])
        self.assertIn("reward:decreased", result["improvement_contract_violations"])

    def test_profile_removal_remains_visible_even_when_new_payment_is_higher(self):
        policy, before, after = counterexample()
        after = replace(after, metrics={**after.metrics, "wider-profile": metrics("1")})
        result = evaluate_revision(policy, before, after, kind="improvement", reason_reference=REF)
        self.assertEqual(result["status"], "needs_review")
        self.assertIn("primary", result["changes"]["profiles_removed"])

    def test_additional_independent_impact_is_accepted(self):
        result = self.revise(self.cases["E"][1])
        self.assertEqual(result["status"], "accepted_improvement")
        self.assertEqual(result["applicable_decision"]["reward"]["amount"], "31800.00")

    def test_better_reliability_is_accepted(self):
        result = self.revise(self.cases["F"][1])
        self.assertEqual(result["status"], "accepted_improvement")
        self.assertEqual(result["applicable_decision"]["reward"]["amount"], "36350.00")

    def test_label_change_does_not_change_revision_result(self):
        result = self.revise(self.cases["H"][1])
        self.assertEqual(result["status"], "accepted_improvement")
        self.assertEqual(result["before_input_sha256"], result["after_input_sha256"])

    def test_omitted_prerequisite_is_not_an_improvement(self):
        result = self.revise(self.cases["G"][1])
        self.assertEqual(result["status"], "needs_review")
        self.assertIsNone(result["applicable_decision"])
        self.assertIn("severity:decreased", result["improvement_contract_violations"])

    def test_explained_correction_can_reduce_result_without_clamping_to_history(self):
        result = self.revise(self.cases["G"][1], "correction")
        self.assertEqual(result["status"], "recorded_correction")
        self.assertEqual(result["applicable_decision"]["reward"]["amount"], "3500.00")
        self.assertEqual(result["reason_reference"], REF)

    def test_correction_requires_recorded_reason(self):
        with self.assertRaises(EvidenceError):
            evaluate_revision(self.policy, self.before, self.cases["G"][1], kind="correction", reason_reference=" ")

    def test_unknown_new_evidence_does_not_become_final(self):
        result = self.revise(self.cases["Unknown-metric"][1])
        self.assertEqual(result["status"], "pending")
        self.assertIsNone(result["applicable_decision"])

    def test_lower_metric_is_detected_even_if_money_is_flat_due_to_cap(self):
        before = replace(self.before, metrics={"primary": metrics("1")})
        after = replace(before, metrics={"primary": metrics("0.9")})
        result = evaluate_revision(self.policy, before, after, kind="improvement", reason_reference=REF)
        self.assertEqual(result["previous_decision"]["reward"]["amount"], result["proposed_decision"]["reward"]["amount"])
        self.assertEqual(result["status"], "needs_review")
        self.assertIn("metric:primary:extent:decreased_or_missing", result["improvement_contract_violations"])

    def test_new_global_or_profile_limit_cannot_be_mislabelled_improvement(self):
        for name in ("global_limit_example", "profile_limit_example"):
            result = self.revise(change_facts(self.before, **{name: True}))
            self.assertEqual(result["status"], "needs_review")
            self.assertIsNone(result["applicable_decision"])

    def test_favorable_fact_direction_must_come_from_policy(self):
        policy = replace(self.policy, favorable_facts={})
        before = bind(self.before, policy)
        after = bind(self.cases["E"][1], policy)
        result = evaluate_revision(policy, before, after, kind="improvement", reason_reference=REF)
        self.assertEqual(result["status"], "needs_review")
        self.assertIn("fact:key_exfiltration:not_a_declared_improvement", result["improvement_contract_violations"])

    def test_policy_snapshot_cannot_change_during_revision(self):
        policy = replace(self.policy, version="different")
        with self.assertRaises(EvidenceError):
            evaluate_revision(policy, self.before, self.cases["F"][1], kind="improvement", reason_reference=REF)

    def test_cli_returns_review_exit_code_and_no_applicable_decision(self):
        policy, before, after = counterexample()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, kind, value in (("policy", "policy", policy), ("before", "report", before), ("after", "report", after)):
                (root / f"{name}.json").write_text(json.dumps(document(kind, value)))
            result = subprocess.run([sys.executable, "-m", "bemix_pilot", "revise",
                                     "--policy", str(root / "policy.json"), "--before", str(root / "before.json"),
                                     "--after", str(root / "after.json"), "--kind", "improvement",
                                     "--reason-reference", REF], capture_output=True, text=True)
            self.assertEqual(result.returncode, 3, result.stderr)
            decision = json.loads(result.stdout)
            self.assertEqual(decision["status"], "needs_review")
            self.assertIsNone(decision["applicable_decision"])


if __name__ == "__main__":
    unittest.main()
