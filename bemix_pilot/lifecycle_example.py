"""Synthetic, deterministic examples used in both the Gist and repository."""

from fractions import Fraction as F

from .engine import canonical, evaluate
from .examples import adjustment_rules, base_report, example_inputs, estimation_config, sample_policy, submission_options
from bemix_service.estimates import estimated_bounty
from bemix_service.awards import calculate_award


def demonstration():
    policy = sample_policy()
    governance = {"submission_options": submission_options(), "estimation": estimation_config(),
                  "award_adjustments": adjustment_rules()}
    claims = [{"option_id": "withdrawal_manipulation", "evidence": {"proof": "Synthetic comparison evidence"}}]
    inputs = example_inputs()
    estimated = estimated_bounty(policy, governance, claims, inputs)
    validated = evaluate(policy, base_report(policy))
    final = calculate_award(policy, governance, {"body": {"result": validated}},
                            {"rule_id": "example-quality-adjustment", "delta_minor_units": 50000,
                             "reason": "Synthetic program-permitted quality adjustment"})
    return {"synthetic": True, "official_program_calibration": False, "currency": policy.currency,
            "scope": {"asset": policy.asset, "policy_version": policy.version},
            "weights": {"extent": "1", "reliability": "1"},
            "band": {"minimum": "4500.00", "maximum": "50000.00"},
            "states": [
                {"stage": "Estimated Bounty", "basis": "researcher_declared", "facts_validated": False,
                 "E": 4, "D": 0, "O": 0, "X": 1, "bemix_score": estimated["bemix"]["score"],
                 "policy_category": estimated["severity"], "extent": "0.6", "reliability": "0.8",
                 "Q": str((F(3, 5) + F(4, 5)) / 2), "amount": estimated["amount"]},
                {"stage": "Validated Bounty", "basis": "synthetic_triager_correction", "facts_validated": "assumed_for_example",
                 "bemix_score": validated["bemix"]["score"], "policy_category": validated["classification"]["severity"],
                 "extent": "0.6", "reliability": "0.6", "Q": validated["reward"]["profiles"][0]["Q"],
                 "amount": validated["reward"]["amount"], "reason": "Reliability corrected from 0.8 to 0.6"},
                {"stage": "Final Award", "basis": "synthetic_program_confirmation", "calculated_bounty": validated["reward"]["amount"],
                 "adjustment": "500.00", "adjustment_rule": "example-quality-adjustment", "amount": final["amount"],
                 "bemix_score_unchanged": validated["bemix"]["score"], "payment_executed": False}],
            "reproduction_inputs": {"policy": canonical(policy), "governance": governance, "claims": claims, "inputs": inputs},
            "notice": "These are synthetic computations demonstrating states and recalculation, not a real report's recommended award."}


if __name__ == "__main__":
    import json
    print(json.dumps(demonstration(), ensure_ascii=False, indent=2))
