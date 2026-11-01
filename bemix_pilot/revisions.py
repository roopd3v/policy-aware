"""Review a proposed revision without preserving an unjustified old payment.

Single-report evaluate() is deliberately stateless. Call evaluate_revision() with the last accepted assessment before accepting an update as an improvement. The guard returns no applicable decision when precedence removes a prior profile or the declared positive direction is not supported by the frozen policy.
"""

from fractions import Fraction

from .engine import EvidenceError, SEVERITIES, cap_state, evaluate


def _profile_ceiling(policy, report, profile_id):
    profile = next(p for p in policy.profiles if p.id == profile_id)
    return min((cap.amount for cap in policy.caps
                if profile_id in cap.profiles and profile.severity in cap.severities
                and cap.amount is not None and cap_state(cap, report.facts) is True),
               default=policy.bands[profile.severity][1])


def evaluate_revision(policy, before, after, *, kind, reason_reference):
    if kind not in ("improvement", "correction"):
        raise EvidenceError("A revision must be explicitly classified as improvement or correction.")
    if not isinstance(reason_reference, str) or not reason_reference.strip():
        raise EvidenceError("A revision requires a recorded explanation/evidence reference.")
    previous = evaluate(policy, before)
    proposed = evaluate(policy, after)
    old_rules = set(previous["classification"].get("accepted_rules", []))
    new_rules = set(proposed["classification"].get("accepted_rules", []))
    old_profiles = {p["profile"] for p in previous["reward"].get("profiles", [])}
    new_profiles = {p["profile"] for p in proposed["reward"].get("profiles", [])}
    fact_changes = []
    violations = []
    for name in sorted(before.facts.keys() | after.facts.keys()):
        old = before.facts[name].value if name in before.facts else None
        new = after.facts[name].value if name in after.facts else None
        if old == new:
            continue
        direction = policy.favorable_facts.get(name)
        fact_changes.append({"fact": name, "before": old, "after": new,
                             "favorable_value_in_policy": direction})
        if name not in policy.favorable_facts or new is None or new != direction:
            violations.append(f"fact:{name}:not_a_declared_improvement")
    for axis, old, new in zip(("E", "D", "O", "X"), before.core, after.core):
        if new < old:
            violations.append(f"core:{axis}:decreased")
    if before.eligibility != after.eligibility:
        violations.append("report_eligibility:changed")
    lost_profiles = sorted(old_profiles - new_profiles)
    for profile in lost_profiles:
        violations.append(f"profile:{profile}:removed")
    for profile_id in sorted(old_profiles):
        profile = next(p for p in policy.profiles if p.id == profile_id)
        for name, weight in profile.weights.items():
            if weight <= 0:
                continue
            old = before.metrics.get(profile_id, {}).get(name)
            new = after.metrics.get(profile_id, {}).get(name)
            if old is not None and old.value is not None:
                if new is None or new.value is None or new.value < old.value:
                    violations.append(f"metric:{profile_id}:{name}:decreased_or_missing")
        if profile_id in new_profiles and _profile_ceiling(policy, after, profile_id) < _profile_ceiling(policy, before, profile_id):
            violations.append(f"cap:{profile_id}:tightened")
    old_severity = previous["classification"].get("severity")
    new_severity = proposed["classification"].get("severity")
    if old_severity in SEVERITIES and new_severity in SEVERITIES:
        if SEVERITIES.index(new_severity) < SEVERITIES.index(old_severity):
            violations.append("severity:decreased")
    if previous["reward"]["status"] == proposed["reward"]["status"] == "calculated":
        if Fraction(proposed["reward"]["global_ceiling"]) < Fraction(previous["reward"]["global_ceiling"]):
            violations.append("global_cap:tightened")
        if Fraction(proposed["reward"]["exact_final_minor_units"]) < Fraction(previous["reward"]["exact_final_minor_units"]):
            violations.append("reward:decreased")
    result = {
        "schema_version": "bemix-revision/1",
        "kind": kind,
        "reason_reference": reason_reference,
        "policy_sha256": previous["policy"]["sha256"],
        "before_input_sha256": previous["input_sha256"],
        "after_input_sha256": proposed["input_sha256"],
        "changes": {"facts": fact_changes,
                    "core_before": list(before.core), "core_after": list(after.core),
                    "rules_removed": sorted(old_rules - new_rules),
                    "rules_added": sorted(new_rules - old_rules),
                    "profiles_removed": lost_profiles,
                    "profiles_added": sorted(new_profiles - old_profiles)},
        "improvement_contract_violations": sorted(set(violations)),
        "previous_decision": previous,
        "proposed_decision": proposed,
        "applicable_decision": None,
        "warning": "This records and checks an assessment transition; it does not authenticate the assessor or validate the factual justification.",
    }
    if proposed["reward"]["status"] not in ("calculated", "ineligible"):
        result["status"] = "pending"
    elif kind == "correction":
        result.update(status="recorded_correction", applicable_decision=proposed)
    elif previous["reward"]["status"] != "calculated":
        result["status"] = "needs_review"
        result["improvement_contract_violations"].append("baseline:not_a_calculated_decision")
    elif violations:
        result["status"] = "needs_review"
    else:
        result.update(status="accepted_improvement", applicable_decision=proposed)
    return result
