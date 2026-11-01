"""Acceptance and invariant tests for the experimental mechanism."""

from dataclasses import replace
from fractions import Fraction as F
from itertools import product
import unittest

from bemix_pilot.engine import (
    ConfigurationError, EvidenceError, Metric, bemix, evaluate, validate_policy,
)
from bemix_pilot.examples import (
    REF, base_report, bind, change_facts, metrics, sample_policy, scenarios,
)


class CoreTests(unittest.TestCase):
    def test_single_axis_never_reaches_critical(self):
        for score, x in product(range(1, 5), range(3)):
            for vector in ((score, 0, 0), (0, score, 0), (0, 0, score)):
                self.assertLessEqual(bemix(*vector, x)["score"], 18)
        self.assertEqual(bemix(4, 0, 0, 1)["score"], 17)
        self.assertEqual(bemix(4, 0, 0, 2)["score"], 18)

    def test_entire_original_domain_and_only_maximum(self):
        maxima = []
        for e, d, o, x in product(range(5), range(5), range(5), range(3)):
            result = bemix(e, d, o, x)
            if e == d == o == 0:
                self.assertIsNone(result["score"])
                self.assertEqual(result["legacy_severity"], "Informational")
            else:
                self.assertTrue(1 <= result["score"] <= 30)
                if result["score"] == 30:
                    maxima.append((e, d, o, x))
        self.assertEqual(maxima, [(4, 4, 4, 2)])

    def test_invalid_inputs_are_rejected(self):
        for args in ((-1, 0, 0, 1), (5, 0, 0, 1), (4, 0, 0, 3),
                     (True, 0, 0, 1), (4.0, 0, 0, 1)):
            with self.assertRaises(EvidenceError):
                bemix(*args)


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.policy = sample_policy()
        self.report = base_report(self.policy)
        self.cases = {name: (policy, report) for name, _, policy, report in scenarios()}

    def decision(self, case):
        return evaluate(*self.cases[case])

    def test_reference_cases_a_to_h(self):
        a, e, f, g, h = (self.decision(c) for c in ("A", "E", "F", "G", "H"))
        self.assertEqual(a["reward"]["amount"], "31800.00")
        self.assertEqual(e["reward"]["amount"], "31800.00")
        self.assertEqual(f["reward"]["amount"], "36350.00")
        self.assertEqual(g["classification"]["severity"], "High")
        self.assertEqual(g["reward"]["amount"], "3500.00")
        self.assertEqual(a, h)

    def test_case_b_does_not_infer_vulnerability_from_a_signature(self):
        result = self.decision("B")
        self.assertEqual(result["classification"]["status"], "pending")
        self.assertIsNone(result["reward"]["amount"])

    def test_key_exfiltration_alone_does_not_resolve_restrictions(self):
        result = self.decision("C")
        self.assertEqual(result["classification"]["status"], "pending")
        self.assertIsNone(result["reward"]["amount"])
        completed = self.decision("C-complete")
        self.assertEqual(completed["classification"]["severity"], "Critical")
        self.assertEqual(completed["reward"]["amount"], "22700.00")

    def test_maximum_reachable_without_data_or_operational_impact(self):
        result = self.decision("D")
        self.assertEqual(result["bemix"]["score"], 18)
        self.assertEqual(result["bemix"]["D"], 0)
        self.assertEqual(result["bemix"]["O"], 0)
        self.assertEqual(result["classification"]["severity"], "Critical")
        self.assertEqual(result["reward"]["amount"], "50000.00")

    def test_low_payment_does_not_downgrade_critical(self):
        report = replace(self.report, metrics={"primary": metrics("0")})
        result = evaluate(self.policy, report)
        self.assertEqual(result["classification"]["severity"], "Critical")
        self.assertEqual(result["reward"]["amount"], "4500.00")

    def test_bemix_21_does_not_promote_high(self):
        policy, report = self.cases["G"]
        result = evaluate(policy, replace(report, core=(4, 2, 0, 1)))
        self.assertEqual(result["bemix"]["score"], 21)
        self.assertEqual(result["classification"]["severity"], "High")

    def test_conflict_is_not_resolved_by_choosing_highest_or_lowest(self):
        result = self.decision("A-conflict")
        self.assertEqual(result["classification"]["status"], "policy_conflict")
        self.assertNotIn("severity", result["classification"])
        self.assertIsNone(result["reward"]["amount"])

    def test_extra_profile_never_reduces_payment_over_grid(self):
        # The input facts, primary profile, policy, and global cap remain fixed.
        for q1, q2 in product(range(11), repeat=2):
            first = replace(self.report, metrics={"primary": metrics(F(q1, 10))})
            extra = replace(change_facts(first, key_exfiltration=True),
                            metrics={**first.metrics, "secondary": metrics(F(q2, 10))})
            old = evaluate(self.policy, first)["reward"]["minor_units"]
            new = evaluate(self.policy, extra)["reward"]["minor_units"]
            self.assertGreaterEqual(new, old)

    def test_reliability_improvements_never_reduce_payment(self):
        previous = -1
        for value in range(101):
            report = replace(self.report, metrics={"primary": {
                "extent": Metric(F(3, 5), REF),
                "reliability": Metric(F(value, 100), REF),
            }})
            amount = evaluate(self.policy, report)["reward"]["minor_units"]
            self.assertGreaterEqual(amount, previous)
            previous = amount

    def test_global_cap_cannot_be_bypassed_by_another_profile(self):
        report = replace(change_facts(self.report, key_exfiltration=True,
                                     global_limit_example=True),
                         metrics={"primary": metrics("0.6"), "secondary": metrics("1")})
        result = evaluate(self.policy, report)
        self.assertEqual(result["reward"]["amount"], "34000.00")
        self.assertIn("EXPERIMENT-only-global-34000", result["reward"]["global_cap_rules"])

    def test_per_profile_cap_does_not_suppress_other_eligible_impact(self):
        report = replace(change_facts(self.report, key_exfiltration=True,
                                     profile_limit_example=True),
                         metrics={"primary": metrics("0.6"), "secondary": metrics("0.4")})
        result = evaluate(self.policy, report)
        self.assertEqual(result["reward"]["amount"], "25000.00")
        improved = replace(report, metrics={**report.metrics, "secondary": metrics("0.7")})
        self.assertEqual(evaluate(self.policy, improved)["reward"]["amount"], "36350.00")

    def test_combined_impact_and_global_example(self):
        self.assertEqual(self.decision("Combined")["reward"]["amount"], "36000.00")
        self.assertEqual(self.decision("Global-cap")["reward"]["amount"], "34000.00")

    def test_unknown_financial_metric_preserves_resolved_severity(self):
        result = self.decision("Unknown-metric")
        self.assertEqual(result["classification"]["severity"], "Critical")
        self.assertEqual(result["reward"]["status"], "pending")
        self.assertIsNone(result["reward"]["amount"])

    def test_unquantified_restriction_does_not_invent_a_percentage(self):
        result = self.decision("Unquantified")
        self.assertEqual(result["classification"]["severity"], "Critical")
        self.assertEqual(result["reward"]["status"], "pending")
        self.assertIsNone(result["reward"]["amount"])

    def test_missing_fact_is_unknown_not_false(self):
        facts = dict(self.report.facts)
        facts.pop("restricted_path")
        result = evaluate(self.policy, replace(self.report, facts=facts))
        self.assertEqual(result["classification"]["status"], "pending")
        self.assertIsNone(result["reward"]["amount"])

    def test_absent_and_unknown_are_distinct(self):
        absent = replace(self.report, metrics={"primary": {
            "extent": Metric(F(0), REF, "absent"),
            "reliability": Metric(F(1), REF),
        }})
        self.assertEqual(evaluate(self.policy, absent)["reward"]["status"], "calculated")
        unknown = replace(absent, metrics={"primary": {
            **absent.metrics["primary"], "extent": Metric(None, REF, "unknown"),
        }})
        self.assertEqual(evaluate(self.policy, unknown)["reward"]["status"], "pending")

    def test_cannot_drop_bad_metric_as_not_applicable(self):
        report = replace(self.report, metrics={"primary": {
            "extent": Metric(None, REF, "not_applicable"),
            "reliability": Metric(F(1), REF),
        }})
        with self.assertRaises(EvidenceError):
            evaluate(self.policy, report)

    def test_cannot_add_a_metric_to_change_the_denominator(self):
        report = replace(self.report, metrics={"primary": {
            **self.report.metrics["primary"], "new-damage": Metric(F(1), REF),
        }})
        with self.assertRaises(EvidenceError):
            evaluate(self.policy, report)

    def test_rejected_eligibility_has_no_reward(self):
        result = evaluate(self.policy, replace(self.report, eligibility="rejected"))
        self.assertEqual(result["classification"]["status"], "ineligible")
        self.assertIsNone(result["reward"]["amount"])

    def test_same_inputs_have_identical_audit(self):
        self.assertEqual(evaluate(self.policy, self.report), evaluate(self.policy, self.report))

    def test_policy_changes_cannot_retroactively_replace_a_snapshot(self):
        changed = replace(self.policy, version="SYNTHETIC-PILOT-2")
        with self.assertRaises(EvidenceError):
            evaluate(changed, self.report)
        changed_same_name = replace(self.policy, caps=())
        with self.assertRaises(EvidenceError):
            evaluate(changed_same_name, self.report)

    def test_no_cap_is_invented_when_none_is_published(self):
        policy = replace(self.policy, version="SYNTHETIC-NO-CAP", caps=())
        report = bind(replace(self.report, metrics={"primary": metrics("1")}), policy)
        self.assertEqual(evaluate(policy, report)["reward"]["amount"], "50000.00")

    def test_exact_half_cent_rounding(self):
        report = replace(self.report, metrics={"primary": metrics(F(1, 9100000))})
        result = evaluate(self.policy, report)
        self.assertEqual(result["reward"]["exact_final_minor_units"], "900001/2")
        self.assertEqual(result["reward"]["amount"], "4500.01")

    def test_rounding_cannot_cross_a_top_cap(self):
        cap = replace(self.policy.caps[0], amount=4999999)
        policy = replace(self.policy, version="SYNTHETIC-ROUNDING", caps=(cap,))
        report = bind(replace(self.report, metrics={"primary": metrics("1")}), policy)
        self.assertEqual(evaluate(policy, report)["reward"]["amount"], "49999.99")

    def test_negative_and_zero_weights_are_invalid(self):
        for weights in ({"extent": F(-1), "reliability": F(2)},
                        {"extent": F(0), "reliability": F(0)}):
            profile = replace(self.policy.profiles[0], weights=weights)
            policy = replace(self.policy, profiles=(profile, *self.policy.profiles[1:]))
            with self.assertRaises(ConfigurationError):
                validate_policy(policy)

    def test_non_monotone_or_unreachable_curve_is_invalid(self):
        for anchors in (
            ((F(0), 450000), (F(1, 3), 3000000), (F(2, 3), 2000000), (F(1), 5000000)),
            ((F(0), 450000), (F(1), 4000000)),
        ):
            profile = replace(self.policy.profiles[0], anchors=anchors)
            with self.assertRaises(ConfigurationError):
                validate_policy(replace(self.policy, profiles=(profile, *self.policy.profiles[1:])))

    def test_cap_below_band_floor_is_configuration_error(self):
        cap = replace(self.policy.caps[0], amount=400000)
        with self.assertRaises(ConfigurationError):
            validate_policy(replace(self.policy, caps=(cap,)))

    def test_profile_cap_cannot_silently_target_the_wrong_severity(self):
        cap = replace(self.policy.caps[2], severities=("High",), amount=400000)
        with self.assertRaises(ConfigurationError):
            validate_policy(replace(self.policy, caps=(cap,)))

    def test_cyclic_precedence_is_configuration_error(self):
        policy = replace(self.policy, precedence=(
            *self.policy.precedence, ("induction", "funds"),
        ))
        with self.assertRaises(ConfigurationError):
            validate_policy(policy)

    def test_overlapping_bands_require_an_explicit_extension_of_the_pilot(self):
        bands = {**self.policy.bands, "High": (200000, 600000)}
        with self.assertRaises(ConfigurationError):
            validate_policy(replace(self.policy, bands=bands))


if __name__ == "__main__":
    unittest.main()
