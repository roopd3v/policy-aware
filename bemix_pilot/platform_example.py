"""Synthetic platform templates. Never infer trusted limits from a program request."""

from fractions import Fraction

from dataclasses import replace
from .engine import Fact, Metric, Report, canonical, evaluate, fingerprint
from .examples import REF, estimation_config, submission_options, adjustment_rules, sample_policy, base_report, bind


def example_governance(policy):
    return {"source_reference": "Synthetic source", "calibration_reference": "Synthetic uncalibrated test fixture",
            "precedence_reference": "Synthetic published rule ordering",
            "rubrics": {p.id: {m: "Synthetic criterion definition; calibrate before any real adoption" for m in p.weights} for p in policy.profiles},
            "submission_options": submission_options(), "estimation": estimation_config(), "award_adjustments": adjustment_rules()}


def example_guardrails(policy, governance):
    """Called only by synthetic fixture generation and trusted test setup."""
    names = set(policy.favorable_facts).union(*(set(p.when) for p in (*policy.rules, *policy.profiles, *policy.caps)))
    rules = {r.id: r for r in policy.rules}
    scenarios = {}
    for profile in policy.profiles:
        facts = {name: Fact(False, REF) for name in names}
        for identifier in profile.required_rules:
            facts.update({name: Fact(value, REF) for name, value in rules[identifier].when.items()})
        facts.update({name: Fact(value, REF) for name, value in profile.when.items()})
        metrics = {p.id: {name: Metric(Fraction(1, 2), REF) for name in p.weights} for p in policy.profiles}
        scenarios[profile.id] = canonical(Report("accepted", REF, (4, 0, 0, 1), facts, metrics,
            policy.version, fingerprint(policy), policy.asset, policy.currency, REF))
    data = {"schema_version": "bemix-platform-guardrails/1", "base_policy": canonical(policy),
            "base_governance": governance,
            "context_weight_bounds": {p.id: {name: [str(weight / 2), str(weight * 2)]
                for name, weight in p.weights.items()} for p in policy.profiles}, "profile_scenarios": scenarios}
    data["context_share_bounds"] = {p.id: {name: [str(weight / sum(p.weights.values()) / 2),
        str(min(Fraction(1), weight / sum(p.weights.values()) * Fraction(3, 2)))]
        for name, weight in p.weights.items()} for p in policy.profiles}
    return {**data, "version": "SYNTHETIC-LIMITS-" + fingerprint(data)[:16]}


def importance_demonstration():
    """Controlled financial comparison, not four technical adjudications."""
    base = sample_policy()
    rows = []
    for label, importance in (("RCE via Extension", 10), ("Private Key Exfiltration", 9),
                              ("Silent Transaction Signing", 9), ("DApp-Induced Withdrawal Manipulation", 8)):
        profile = replace(base.profiles[0], importance=importance)
        policy = replace(base, version="SYNTHETIC-PRIORITY-" + str(importance), profiles=(profile, *base.profiles[1:]))
        result = evaluate(policy, bind(base_report(base), policy))
        candidate = result["reward"]["profiles"][0]
        rows.append({"illustrative_impact_label": label, "importance": importance,
                     "severity": result["classification"]["severity"], "bemix_score": result["bemix"]["score"],
                     "context_Q": candidate["Q"], "importance_position": candidate["importance_position"],
                     "reward_position": candidate["reward_position"], "amount": result["reward"]["amount"]})
    return {"synthetic": True, "official_program_calibration": False, "currency": base.currency,
            "importance_ranges": canonical(base.importance_ranges), "importance_influence": str(base.importance_influence),
            "notice": "Hypothetical program priorities with equal contextual inputs, not official Bitget rankings or real report assessments.",
            "comparisons": rows}


def gist_examples():
    from .lifecycle_example import demonstration
    data = demonstration()
    data.pop("reproduction_inputs")
    data["importance_comparison"] = importance_demonstration()
    data["reproduce"] = ["python -m bemix_pilot.lifecycle_example", "python -m bemix_pilot.platform_example"]
    return data


if __name__ == "__main__":
    import json
    print(json.dumps(importance_demonstration(), ensure_ascii=False, indent=2))
