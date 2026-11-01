"""Importance is bounded by severity and affects money without changing BEMIX."""

from dataclasses import replace
from fractions import Fraction as F
import unittest

from bemix_pilot.engine import ConfigurationError, canonical, evaluate, reward_position, validate_policy
from bemix_pilot.examples import TOP_REQUIREMENTS, base_report, bind, change_facts, metrics, sample_policy
from bemix_pilot.serialization import InputError, policy_from_dict


class ImportanceTests(unittest.TestCase):
    def setUp(self):
        self.policy = sample_policy()
        self.report = base_report(self.policy)

    def configured(self, importance):
        first = replace(self.policy.profiles[0], importance=importance)
        return replace(self.policy, profiles=(first, *self.policy.profiles[1:]))

    def test_different_importance_changes_money_inside_the_same_critical_band(self):
        amounts = []
        for importance in (8, 9, 10):
            policy = self.configured(importance)
            result = evaluate(policy, bind(self.report, policy))
            self.assertEqual(result["bemix"]["score"], 17)
            self.assertEqual(result["classification"]["severity"], "Critical")
            amounts.append(result["reward"]["amount"])
        self.assertEqual(amounts, ["31800.00", "32892.00", "33984.00"])

    def test_critical_importance_cannot_be_zero_low_or_above_its_interval(self):
        for importance in (0, 2, 7, 11, True, None):
            with self.subTest(importance=importance), self.assertRaises(ConfigurationError):
                validate_policy(self.configured(importance))

    def test_severity_intervals_cannot_overlap_or_reverse(self):
        for values in ((0, 10), (4, 10), (10, 8), (8, 8)):
            with self.subTest(values=values), self.assertRaises(ConfigurationError):
                validate_policy(replace(self.policy, importance_ranges={**self.policy.importance_ranges, "Critical": values}))

    def test_higher_importance_never_changes_the_severity_or_invents_bemix_points(self):
        policy = self.configured(10)
        report = bind(replace(self.report, core=(3, 0, 0, 0)), policy)
        result = evaluate(policy, report)
        self.assertEqual(result["bemix"]["score"], 11)
        self.assertEqual(result["bemix"]["legacy_severity"], "Medium")
        self.assertEqual(result["classification"]["severity"], "Critical")

    def test_maximum_high_importance_stays_high_and_cannot_use_critical_values(self):
        profile = replace(self.policy.profiles[1], importance=7)
        policy = replace(self.policy, profiles=(self.policy.profiles[0], profile, *self.policy.profiles[2:]))
        report = bind(replace(change_facts(self.report, restricted_path=True), metrics={"restricted": metrics("0.6")}), policy)
        result = evaluate(policy, report)
        self.assertEqual(result["classification"]["severity"], "High")
        self.assertEqual(result["bemix"]["score"], 17)
        self.assertEqual(result["reward"]["amount"], "3620.00")
        invalid = replace(policy, profiles=(policy.profiles[0], replace(profile, importance=8), *policy.profiles[2:]))
        with self.assertRaises(ConfigurationError):
            validate_policy(invalid)

    def test_financial_endpoints_remain_reachable_for_every_allowed_importance(self):
        for importance in (8, 9, 10):
            policy = self.configured(importance)
            for quality, expected in (("0", "4500.00"), ("1", "50000.00")):
                report = change_facts(self.report, **TOP_REQUIREMENTS)
                report = bind(replace(report, metrics={"primary": metrics(quality)}), policy)
                self.assertEqual(evaluate(policy, report)["reward"]["amount"], expected)

    def test_complete_context_and_importance_grid_is_monotone_even_with_caps(self):
        previous = {}
        for importance in (8, 9, 10):
            policy = self.configured(importance)
            last = 0
            for step in range(41):
                report = bind(replace(self.report, metrics={"primary": metrics(F(step, 40))}), policy)
                amount = evaluate(policy, report)["reward"]["minor_units"]
                self.assertGreaterEqual(amount, last)
                self.assertGreaterEqual(amount, previous.get(step, 0))
                self.assertLessEqual(amount, 4090000)
                previous[step], last = amount, amount

    def test_uniformly_multiplying_context_weights_does_not_replace_importance(self):
        profile = replace(self.policy.profiles[0], weights={"extent": F(2), "reliability": F(2)})
        policy = replace(self.policy, profiles=(profile, *self.policy.profiles[1:]))
        self.assertEqual(evaluate(policy, bind(self.report, policy))["reward"]["amount"], "31800.00")
        self.assertNotEqual(evaluate(self.configured(10), bind(self.report, self.configured(10)))["reward"]["amount"], "31800.00")

    def test_placement_stays_between_zero_and_one_for_supported_influence(self):
        for influence in (F(0), F(1, 5), F(1)):
            policy = replace(self.policy, importance_influence=influence)
            for importance in (8, 9, 10):
                profile = replace(policy.profiles[0], importance=importance)
                last = F(0)
                for step in range(101):
                    position, _ = reward_position(policy, profile, F(step, 100))
                    self.assertLessEqual(last, position)
                    self.assertLessEqual(position, 1)
                    last = position

    def test_invalid_importance_influence_is_rejected(self):
        for influence in (F(-1, 10), F(11, 10), True, 0.2):
            with self.subTest(influence=influence), self.assertRaises(ConfigurationError):
                validate_policy(replace(self.policy, importance_influence=influence))

    def test_json_transport_preserves_bounded_importance_exactly(self):
        policy = self.configured(9)
        parsed = policy_from_dict(canonical(policy))
        self.assertEqual(parsed, policy)
        self.assertEqual(evaluate(parsed, bind(self.report, parsed))["reward"]["amount"], "32892.00")
        data = canonical(policy)
        data["profiles"][0]["importance"] = "9"
        with self.assertRaises(InputError):
            policy_from_dict(data)


if __name__ == "__main__":
    unittest.main()
