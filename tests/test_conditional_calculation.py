from dataclasses import replace
from fractions import Fraction as F
import unittest

from bemix_pilot.engine import Metric, evaluate
from bemix_pilot.examples import REF, base_report, change_facts, sample_policy


class ConditionalCalculationTests(unittest.TestCase):
    def setUp(self):
        self.policy = sample_policy()
        self.report = base_report(self.policy)

    def test_unknown_losing_profile_does_not_block_a_determinate_amount(self):
        report = change_facts(self.report, key_exfiltration=True, restricted_keys=True)
        result = evaluate(self.policy, report)
        self.assertEqual(result["reward"]["status"], "calculated")
        self.assertEqual(result["reward"]["amount"], "31800.00")
        self.assertEqual(result["reward"]["uncertainty"]["status"], "does_not_change_result")

    def test_unknown_rule_with_only_lower_financial_outcomes_does_not_block(self):
        report = change_facts(self.report, key_exfiltration=None, restricted_keys=True)
        result = evaluate(self.policy, report)
        self.assertEqual(result["classification"]["severity"], "Critical")
        self.assertEqual(result["reward"]["amount"], "31800.00")

    def test_unknown_cap_above_the_current_result_does_not_block(self):
        result = evaluate(self.policy, change_facts(self.report, global_limit_example=None))
        self.assertEqual(result["reward"]["amount"], "31800.00")
        self.assertEqual(result["reward"]["eligible_ceiling_minor_units"], 3400000)

    def test_unknown_cap_that_changes_the_result_gives_a_range(self):
        report = replace(change_facts(self.report, global_limit_example=None),
                         metrics={"primary": {"extent": Metric(F(1), REF), "reliability": Metric(F(1), REF)}})
        result = evaluate(self.policy, report)
        self.assertIsNone(result["reward"]["amount"])
        self.assertEqual(result["reward"]["range"], {"lower": "34000.00", "upper": "40900.00", "currency": "USDC"})

    def test_missing_relevant_metric_gives_bounds_without_fabricating_a_value(self):
        report = replace(self.report, metrics={"primary": {"reliability": Metric(F(3, 5), REF)}})
        result = evaluate(self.policy, report)
        self.assertEqual(result["reward"]["status"], "pending")
        self.assertEqual(result["reward"]["range"]["lower"], "18150.00")
        self.assertEqual(result["reward"]["range"]["upper"], "40900.00")
        self.assertNotIn("extent", report.metrics["primary"])

    def test_unknown_metadata_is_not_an_unknown_calculation_input(self):
        result = evaluate(self.policy, change_facts(self.report, no_effective_mitigation=None))
        self.assertEqual(result["reward"]["status"], "calculated")
        self.assertEqual(result["reward"]["amount"], "31800.00")

    def test_unknown_rules_with_different_severities_stay_conditional(self):
        report = change_facts(self.report, restricted_path=None)
        result = evaluate(self.policy, report)
        self.assertIsNone(result["reward"]["amount"])
        self.assertEqual(result["classification"]["possible_severities"], ["High", "Critical"])


if __name__ == "__main__":
    unittest.main()
