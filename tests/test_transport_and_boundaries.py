"""Input contracts, independent top requirements, and policy boundaries."""

from dataclasses import replace
from fractions import Fraction as F
from itertools import product
import json
import random
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from bemix_pilot.engine import ConfigurationError, EvidenceError, Metric, canonical, evaluate, round_minor
from bemix_pilot.examples import REF, TOP_REQUIREMENTS, base_report, bind, change_facts, metrics, sample_policy, scenarios
from bemix_pilot.serialization import InputError, document, load_document, policy_from_dict, report_from_dict


class Boundaries(unittest.TestCase):
    def setUp(self):
        self.policy = sample_policy()
        self.report = base_report(self.policy)

    def test_all_81_top_requirement_states(self):
        for states in product((True, False, None), repeat=4):
            report = change_facts(self.report, **dict(zip(TOP_REQUIREMENTS, states)))
            report = replace(report, metrics={"primary": metrics("1")})
            result = evaluate(self.policy, report)["reward"]
            if False in states:
                self.assertEqual(result["amount"], "40900.00")
            elif None in states:
                self.assertEqual(result["status"], "pending")
                self.assertIsNone(result["amount"])
            else:
                self.assertEqual(result["amount"], "50000.00")

    def test_zero_weight_unknown_cannot_block_payment(self):
        first = replace(self.policy.profiles[0], weights={"extent": F(1), "reliability": F(0)})
        policy = replace(self.policy, profiles=(first, *self.policy.profiles[1:]))
        report = bind(replace(self.report, metrics={"primary": {
            "extent": Metric(F(3, 5), REF), "reliability": Metric(None, REF, "unknown")}}), policy)
        self.assertEqual(evaluate(policy, report)["reward"]["amount"], "31800.00")

    def test_resolved_critical_needs_a_critical_financial_profile(self):
        # A High reward at the shared 4,500 boundary cannot stand in for a
        # missing Critical financial evaluation.
        policy = replace(self.policy, profiles=tuple(p for p in self.policy.profiles
                                                     if p.id not in ("secondary", "combined")))
        report = change_facts(self.report, restricted_path=True, key_exfiltration=True)
        report = bind(replace(report, metrics={"restricted": metrics("1")}), policy)
        result = evaluate(policy, report)
        self.assertEqual(result["classification"]["severity"], "Critical")
        self.assertEqual(result["reward"]["status"], "pending")
        self.assertIn("resolved_severity:financial_profile_missing", result["reward"]["pending"])

    def test_asset_and_currency_cannot_be_substituted(self):
        for changes in ({"asset": "Wallet App"}, {"currency": "USDT"}):
            with self.assertRaises(EvidenceError):
                evaluate(self.policy, replace(self.report, **changes))

    def test_all_zero_impact_is_not_paid_as_critical(self):
        result = evaluate(self.policy, replace(self.report, core=(0, 0, 0, 2)))
        self.assertEqual(result["classification"]["status"], "evidence_conflict")
        self.assertIsNone(result["reward"]["amount"])

    def test_core_scores_need_a_reference(self):
        with self.assertRaises(EvidenceError):
            evaluate(self.policy, replace(self.report, core_reference=" "))

    def test_extra_profiles_and_misspelled_facts_are_errors(self):
        for report in (replace(self.report, metrics={"typo": metrics("1")}),
                       change_facts(self.report, no_user_interacton=True)):
            with self.assertRaises(EvidenceError):
                evaluate(self.policy, report)

    def test_extra_metrics_on_ineligible_profiles_are_also_errors(self):
        report = replace(self.report, metrics={**self.report.metrics, "secondary": {
            **metrics("0.5"), "invented": Metric(F(1), REF)}})
        with self.assertRaises(EvidenceError):
            evaluate(self.policy, report)

    def test_empty_identifiers_and_unknown_cap_modes_are_errors(self):
        for policy in (replace(self.policy, caps=(replace(self.policy.caps[0], mode="invalid"),)),
                       replace(self.policy, rules=(replace(self.policy.rules[0], id=""),))):
            with self.assertRaises(ConfigurationError):
                evaluate(policy, bind(self.report, policy))

    def test_piecewise_curve_interpolates_financial_anchors_exactly(self):
        points = ((F(0), 450000), (F(1, 4), 750000), (F(1, 2), 1350000),
                  (F(3, 4), 2750000), (F(1), 5000000))
        first = replace(self.policy.profiles[0], anchors=points)
        policy = replace(self.policy, profiles=(first, *self.policy.profiles[1:]))
        for q, expected in (("0.35", "9900.00"), ("0.5", "13500.00"), ("0.65", "21900.00")):
            report = bind(replace(self.report, metrics={"primary": metrics(q)}), policy)
            self.assertEqual(evaluate(policy, report)["reward"]["amount"], expected)

    def test_publication_bit_is_not_proof_of_authority(self):
        policy = replace(self.policy, experimental=False)
        result = evaluate(policy, bind(self.report, policy))
        self.assertFalse(result["policy"]["experimental"])
        self.assertNotIn("official", result["policy"])

    def test_impossible_profile_conditions_are_rejected(self):
        first = replace(self.policy.profiles[0], when={"unauthorized_fund_effect": False})
        policy = replace(self.policy, profiles=(first, *self.policy.profiles[1:]))
        with self.assertRaises(ConfigurationError):
            evaluate(policy, bind(self.report, policy))
        first = replace(self.policy.profiles[0], required_rules=("funds", "induction"))
        policy = replace(self.policy, profiles=(first, *self.policy.profiles[1:]))
        with self.assertRaises(ConfigurationError):
            evaluate(policy, bind(self.report, policy))

    def test_seeded_weighted_cases_against_independent_linear_reference(self):
        rng = random.Random(20261031)
        for _ in range(120):
            a, b = rng.randint(1, 9), rng.randint(0, 9)
            x, y = F(rng.randint(0, 100), 100), F(rng.randint(0, 100), 100)
            first = replace(self.policy.profiles[0], weights={"extent": F(a), "reliability": F(b)})
            policy = replace(self.policy, profiles=(first, *self.policy.profiles[1:]))
            report = bind(replace(self.report, metrics={"primary": {
                "extent": Metric(x, REF), "reliability": Metric(y, REF)}}), policy)
            expected = min(F(4090000), F(450000) + F(4550000) * (a * x + b * y) / (a + b))
            result = evaluate(policy, report)
            self.assertEqual(result["reward"]["minor_units"], round_minor(expected))
            changed = replace(report, metrics={"primary": {
                "extent": Metric(min(F(1), x + F(1, 100)), REF), "reliability": Metric(y, REF)}})
            self.assertGreaterEqual(evaluate(policy, changed)["reward"]["minor_units"], result["reward"]["minor_units"])


class Transport(unittest.TestCase):
    def setUp(self):
        self.policy = sample_policy()
        self.report = base_report(self.policy)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, content):
        path = self.root / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_all_scenarios_roundtrip_without_changing_decisions_or_hashes(self):
        for _, _, policy, report in scenarios():
            p = policy_from_dict(canonical(policy))
            r = report_from_dict(canonical(report))
            self.assertEqual(evaluate(p, r), evaluate(policy, report))

    def test_unknown_fields_are_not_ignored(self):
        for decode, data in ((policy_from_dict, canonical(self.policy)),
                             (report_from_dict, canonical(self.report))):
            data["ignored_override"] = "Critical"
            with self.assertRaises(InputError):
                decode(data)

    def test_missing_and_malformed_inputs_fail_clearly(self):
        variants = []
        data = canonical(self.report); data.pop("asset"); variants.append(data)
        data = canonical(self.report); data["core"] = [4, 0, 0]; variants.append(data)
        data = canonical(self.report); data["core"] = [True, 0, 0, 2]; variants.append(data)
        data = canonical(self.report); data["facts"]["silent_signing"]["value"] = "false"; variants.append(data)
        for data in variants:
            with self.assertRaises(InputError):
                report_from_dict(data)

    def test_decimal_float_nan_duplicate_keys_and_wrong_schema_rejected(self):
        for content in ('{"x": 0.1}', '{"x": NaN}', '{"x": 1, "x": 2}',
                        '{"schema_version": "unknown", "policy": {}}', 'null'):
            path = self.write("bad.json", content)
            with self.assertRaises(InputError):
                load_document(path, "policy")

    def test_fractions_cannot_use_large_exponents(self):
        data = canonical(self.policy)
        for value in ("1e999999999", "1/0", "NaN", True):
            data["profiles"][0]["weights"]["extent"] = value
            with self.assertRaises(InputError):
                policy_from_dict(data)

    def test_input_size_limit(self):
        path = self.write("oversize.json", " " * 2_000_001)
        with self.assertRaises(InputError):
            load_document(path, "policy")

    def test_cli_evaluates_imported_documents(self):
        p = self.write("policy.json", json.dumps(document("policy", self.policy)))
        r = self.write("report.json", json.dumps(document("report", self.report)))
        run = subprocess.run([sys.executable, "-m", "bemix_pilot", "evaluate",
                              "--policy", str(p), "--report", str(r)], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout)["reward"]["amount"], "31800.00")

    def test_cli_does_not_overwrite_input(self):
        text = json.dumps(document("policy", self.policy))
        p = self.write("policy.json", text)
        run = subprocess.run([sys.executable, "-m", "bemix_pilot", "validate",
                              "--policy", str(p), "--output", str(p)], capture_output=True, text=True)
        self.assertEqual(run.returncode, 2)
        self.assertEqual(p.read_text(), text)
        self.assertEqual(json.loads(run.stderr)["status"], "invalid_input")

    def test_cli_invalid_input_is_structured_and_nonzero(self):
        p = self.write("policy.json", "not JSON")
        run = subprocess.run([sys.executable, "-m", "bemix_pilot", "validate",
                              "--policy", str(p)], capture_output=True, text=True)
        self.assertEqual(run.returncode, 2)
        self.assertEqual(json.loads(run.stderr)["status"], "invalid_input")


if __name__ == "__main__":
    unittest.main()
