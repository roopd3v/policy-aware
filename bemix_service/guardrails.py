"""Platform-owned configuration bounds and deterministic publication checks.

Only the local platform operator installs these templates. A program request cannot supply, replace or widen its own limits.
"""

from dataclasses import replace
from fractions import Fraction
import json
from pathlib import Path

from bemix_pilot.engine import Metric, canonical, evaluate, fingerprint, interpolate, reward_position, validate_policy
from bemix_pilot.serialization import MAX_BYTES, _fraction, _pairs, _reject_number, policy_from_dict, report_from_dict
from .estimates import apply_bindings
from .submissions import obj, require, string


SCHEMA = "bemix-platform-guardrails/1"
REFERENCES = {"source_reference", "calibration_reference", "precedence_reference"}


def policy_structure(policy):
    data = canonical(policy)
    data.pop("version")
    data.pop("experimental")
    for profile in data["profiles"]:
        profile.pop("importance")
        profile["weights"] = sorted(profile["weights"])
    return data


def governance_structure(governance):
    return {k: v for k, v in governance.items() if k not in REFERENCES}


def validate_configuration(template, policy, governance, complete=True):
    validate_policy(policy)
    base = policy_from_dict(template["base_policy"])
    require(policy_structure(policy) == policy_structure(base),
            "Program settings cannot change the platform-approved rules, ranges, curves, bands or scope.")
    for profile in policy.profiles:
        total = sum(profile.weights.values())
        for name, weight in profile.weights.items():
            low, high = (_fraction(v) for v in template["context_weight_bounds"][profile.id][name])
            require(low <= weight <= high,
                    f"Context weight '{profile.id}.{name}' is outside the platform's allowed interval.")
            low_share, high_share = (_fraction(v) for v in template["context_share_bounds"][profile.id][name])
            require(low_share <= weight / total <= high_share,
                    f"Context share '{profile.id}.{name}' is outside the platform's allowed interval.")
    if complete:
        require(governance_structure(governance) == governance_structure(template["base_governance"]),
                "Program settings cannot replace platform-approved criterion definitions, mappings or award rules.")


def validate_template(template):
    obj(template, ("schema_version", "version", "base_policy", "base_governance",
                   "context_weight_bounds", "context_share_bounds", "profile_scenarios"))
    require(template["schema_version"] == SCHEMA, "Unsupported platform guardrails schema.")
    string(template["version"], 200)
    policy = policy_from_dict(template["base_policy"])
    require(bool(policy.importance_ranges) and policy.importance_influence > 0,
            "Platform templates must define severity importance intervals and a positive bounded influence.")
    require(all(high - low <= 100 for low, high in policy.importance_ranges.values()),
            "Platform importance intervals support at most 101 integer choices each.")
    require(isinstance(template["base_governance"], dict), "Platform governance must be an object.")
    governance = template["base_governance"]
    obj(governance, ("source_reference", "calibration_reference", "precedence_reference", "rubrics"),
        ("submission_options", "estimation", "award_adjustments"))
    for name in REFERENCES:
        string(governance[name], 4000)
    bounds = template["context_weight_bounds"]
    profiles = {p.id: p for p in policy.profiles}
    rubrics = governance["rubrics"]
    require(isinstance(rubrics, dict) and set(rubrics) == set(profiles), "Platform rubrics must cover every profile.")
    for identifier, mapping in rubrics.items():
        require(isinstance(mapping, dict) and set(mapping) == set(profiles[identifier].weights),
                "Platform rubrics must define every criterion exactly.")
        for definition in mapping.values():
            string(definition, 8000)
    require(isinstance(bounds, dict) and set(bounds) == set(profiles), "Define platform weight bounds for every profile.")
    for identifier, mapping in bounds.items():
        require(isinstance(mapping, dict) and set(mapping) == set(profiles[identifier].weights),
                "Platform weight bounds must cover the fixed criteria exactly.")
        for pair in mapping.values():
            require(isinstance(pair, list) and len(pair) == 2, "A context weight interval needs two endpoints.")
            low, high = (_fraction(v) for v in pair)
            require(0 <= low <= high and (low > 0 or high == 0),
                    "An applicable criterion needs a positive minimum; only platform-fixed zero criteria may be disabled.")
    shares = template["context_share_bounds"]
    require(isinstance(shares, dict) and set(shares) == set(profiles), "Define platform share bounds for every profile.")
    for identifier, mapping in shares.items():
        require(isinstance(mapping, dict) and set(mapping) == set(profiles[identifier].weights),
                "Platform share bounds must cover the fixed criteria exactly.")
        low_sum, high_sum = Fraction(0), Fraction(0)
        for pair in mapping.values():
            require(isinstance(pair, list) and len(pair) == 2, "A context share interval needs two endpoints.")
            low, high = (_fraction(v) for v in pair)
            require(0 <= low <= high <= 1, "Context shares must be fractions between zero and one.")
            low_sum += low
            high_sum += high
        require(low_sum <= 1 <= high_sum, "Context share bounds cannot represent a total influence of 100%.")
    scenarios = template["profile_scenarios"]
    require(isinstance(scenarios, dict) and set(scenarios) == set(profiles),
            "The platform must provide a synthetic acceptance scenario for every profile.")
    for scenario in scenarios.values():
        report_from_dict(scenario)
    validate_configuration(template, policy, template["base_governance"])
    return policy


def certify_configuration(template, policy, governance):
    """Check both financial invariants and complete BEMIX/profile scenarios.

    The grid is a regression check. Continuous monotonicity also follows from
    non-negative fixed context weights, dR/dQ >= 0 and monotone financial anchors.
    """
    validate_configuration(template, policy, governance)
    points, input_checks, examples = 0, 0, []
    for profile in policy.profiles:
        low, high = policy.bands[profile.severity]
        start, end = policy.importance_ranges[profile.severity]
        previous_by_q = {}
        for importance in range(start, end + 1):
            configured = replace(profile, importance=importance)
            previous = None
            for step in range(21):
                q = Fraction(step, 20)
                position, _ = reward_position(policy, configured, q)
                amount = interpolate(configured.anchors, position)
                require(low <= amount <= high, "Platform check: financial placement escapes its severity band.")
                require(previous is None or previous <= amount, "Platform check: improving context reduces the reward.")
                require(step not in previous_by_q or previous_by_q[step] <= amount,
                        "Platform check: increasing impact importance reduces the reward.")
                require(step not in (0, 20) or amount == (low if step == 0 else high),
                        "Platform check: a financial endpoint is unreachable.")
                previous, previous_by_q[step] = amount, amount
                points += 1
        report = report_from_dict(template["profile_scenarios"][profile.id])
        report = replace(report, policy_version=policy.version, policy_sha256=fingerprint(policy),
                         asset=policy.asset, currency=policy.currency)
        result = evaluate(policy, apply_bindings(policy, governance, report))
        require(result["reward"]["status"] == "calculated"
                and result["classification"].get("severity") == profile.severity
                and any(p["profile"] == profile.id for p in result["reward"].get("profiles", [])),
                f"Platform check: acceptance scenario for '{profile.id}' does not reach its declared profile/severity.")
        examples.append({"profile": profile.id, "bemix_score": result["bemix"]["score"],
                         "severity": result["classification"]["severity"], "amount": result["reward"]["amount"]})
        for name in profile.weights:
            previous = None
            for value in (Fraction(0), Fraction(1, 2), Fraction(1)):
                metrics = {k: dict(v) for k, v in report.metrics.items()}
                metrics.setdefault(profile.id, {})[name] = Metric(value, "Platform criterion transition check")
                changed = evaluate(policy, apply_bindings(policy, governance, replace(report, metrics=metrics)))
                require(changed["reward"]["status"] == "calculated", "Platform check: criterion transition becomes unresolved.")
                amount = Fraction(changed["reward"]["exact_final_minor_units"])
                require(previous is None or previous <= amount, "Platform check: improving a criterion reduces the amount.")
                previous = amount
                input_checks += 1
        for dimension, maximum in enumerate((4, 4, 4, 2)):
            previous = None
            for value in range(maximum + 1):
                core = list(report.core)
                core[dimension] = value
                if core[:3] == [0, 0, 0]:
                    continue
                changed = evaluate(policy, apply_bindings(policy, governance, replace(report, core=tuple(core))))
                require(changed["reward"]["status"] == "calculated", "Platform check: BEMIX input transition becomes unresolved.")
                amount = Fraction(changed["reward"]["exact_final_minor_units"])
                require(previous is None or previous <= amount, "Platform check: increasing a BEMIX input reduces the amount.")
                previous = amount
                input_checks += 1
    return {"status": "passed", "guardrails_version": template["version"],
            "guardrails_sha256": fingerprint(template), "policy_sha256": fingerprint(policy),
            "governance_sha256": fingerprint(governance), "financial_grid_points": points,
            "input_transition_checks": input_checks, "profile_scenarios": examples,
            "checks": ["bounded impact importance", "bounded contextual weights", "fixed platform-approved structure",
                       "monotone context and importance", "financial endpoints", "BEMIX and criterion transitions"]}


def load_template(path):
    raw = Path(path).read_bytes()
    require(len(raw) <= MAX_BYTES, "Platform template exceeds the document size limit.")
    template = json.loads(raw, object_pairs_hook=_pairs, parse_float=_reject_number, parse_constant=_reject_number)
    validate_template(template)
    return template
