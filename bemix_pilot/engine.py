"""Pure calculations over the supplied policy and input values.

This module does not establish whether input claims are true. The caller labels researcher estimates, validated assessments and program awards separately. Researcher labels are excluded from the arithmetic. Monetary values use integer minor units and exact fractional intermediate calculations.
"""

from dataclasses import asdict, dataclass, field, is_dataclass, replace
from fractions import Fraction
from hashlib import sha256
import json
from itertools import product
from typing import Mapping


SEVERITIES = ("Low", "Medium", "High", "Critical")


class ConfigurationError(ValueError):
    pass


class EvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class Fact:
    value: bool | None
    reference: str


@dataclass(frozen=True)
class Metric:
    value: Fraction | None
    reference: str
    state: str = "demonstrated"  # demonstrated, absent, unknown


@dataclass(frozen=True)
class Rule:
    id: str
    group: str
    severity: str
    when: Mapping[str, bool]


@dataclass(frozen=True)
class Profile:
    id: str
    severity: str
    required_rules: tuple[str, ...]
    weights: Mapping[str, Fraction]
    anchors: tuple[tuple[Fraction, int], ...]
    when: Mapping[str, bool] = field(default_factory=dict)
    importance: int | None = None


@dataclass(frozen=True)
class CapRule:
    id: str
    when: Mapping[str, bool]
    severities: tuple[str, ...]
    amount: int | None  # None means the consequence has not been quantified.
    profiles: tuple[str, ...] = ()  # Empty means a report-wide ceiling.
    mode: str = "when_matches"  # or unless_all: cap if any requirement fails


@dataclass(frozen=True)
class Policy:
    version: str
    asset: str
    currency: str
    bands: Mapping[str, tuple[int, int]]
    rules: tuple[Rule, ...]
    profiles: tuple[Profile, ...]
    caps: tuple[CapRule, ...] = ()
    precedence: tuple[tuple[str, str], ...] = ()  # winner, overridden rule
    experimental: bool = True
    favorable_facts: Mapping[str, bool] = field(default_factory=dict)
    importance_ranges: Mapping[str, tuple[int, int]] = field(default_factory=dict)
    importance_influence: Fraction = Fraction(0)


@dataclass(frozen=True)
class Report:
    eligibility: str  # accepted, rejected, pending
    eligibility_reference: str
    core: tuple[int, int, int, int]  # E, D, O, X
    facts: Mapping[str, Fact]
    metrics: Mapping[str, Mapping[str, Metric]]
    policy_version: str
    policy_sha256: str
    asset: str
    currency: str
    core_reference: str
    researcher_label: str = ""


def canonical(value):
    if is_dataclass(value):
        return canonical(asdict(value))
    if isinstance(value, Fraction):
        return str(value)
    if isinstance(value, dict):
        return {key: canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [canonical(item) for item in value]
    return value


def fingerprint(value):
    data = json.dumps(canonical(value), sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))
    return sha256(data.encode()).hexdigest()


def money(minor_units: int) -> str:
    return f"{minor_units // 100}.{minor_units % 100:02d}"


def round_minor(value: Fraction) -> int:
    """Nearest minor unit, with exact half units rounded upward."""
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


def bemix(e: int, d: int, o: int, x: int):
    if any(type(v) is not int or not 0 <= v <= 4 for v in (e, d, o)):
        raise EvidenceError("E, D and O must be integers from 0 to 4.")
    if type(x) is not int or not 0 <= x <= 2:
        raise EvidenceError("X must be an integer from 0 to 2.")
    if e == d == o == 0:
        return {"E": e, "D": d, "O": o, "X": x,
                "score": None, "legacy_severity": "Informational"}
    a, b, c = sorted((e, d, o), reverse=True)
    score = 5 * a + 2 * b + c + x - 4
    severity = next(s for s, upper in zip(SEVERITIES, (7, 14, 21, 30))
                    if score <= upper)
    return {"E": e, "D": d, "O": o, "X": x,
            "score": score, "legacy_severity": severity}


def matches(conditions, facts):
    unknown = False
    for name, expected in conditions.items():
        fact = facts.get(name)
        if fact is None or fact.value is None:
            unknown = True
        elif fact.value != expected:
            return False
    return None if unknown else True


def cap_state(cap, facts):
    state = matches(cap.when, facts)
    return (not state if state is not None else None) if cap.mode == "unless_all" else state


def dominance(policy):
    edges = {r.id: set() for r in policy.rules}
    for winner, loser in policy.precedence:
        edges[winner].add(loser)
    cache = {}
    def descendants(node, path):
        if node in path:
            raise ConfigurationError("The precedence graph contains a cycle.")
        if node in cache:
            return cache[node]
        result = set(edges[node])
        for child in edges[node]:
            result.update(descendants(child, path | {node}))
        cache[node] = result
        return result
    return {node: descendants(node, set()) for node in edges}


def validate_policy(policy):
    if any(not isinstance(v, str) or not v.strip() for v in
           (policy.version, policy.asset, policy.currency)):
        raise ConfigurationError("A version, asset and currency are required.")
    if type(policy.experimental) is not bool:
        raise ConfigurationError("Experimental must be an explicit boolean.")
    if not isinstance(policy.importance_influence, Fraction) or not 0 <= policy.importance_influence <= 1:
        raise ConfigurationError("Importance influence must be an exact fraction between zero and one.")
    if policy.importance_ranges:
        if set(policy.importance_ranges) != set(SEVERITIES):
            raise ConfigurationError("Define an importance interval for all four severities.")
        previous = 0
        for severity in SEVERITIES:
            interval = policy.importance_ranges[severity]
            if (len(interval) != 2 or any(type(v) is not int for v in interval)
                    or not previous < interval[0] < interval[1]):
                raise ConfigurationError("Importance intervals must be positive, strictly ordered and disjoint.")
            previous = interval[1]
    elif policy.importance_influence:
        raise ConfigurationError("Importance influence requires published severity intervals.")
    if any(not isinstance(k, str) or not k.strip() or type(v) is not bool
           for k, v in policy.favorable_facts.items()):
        raise ConfigurationError("Favorable fact directions must map non-empty identifiers to booleans.")
    if any(len(items) > 256 for items in (policy.rules, policy.profiles, policy.caps)):
        raise ConfigurationError("This pilot supports at most 256 rules, profiles or caps per snapshot.")
    if set(policy.bands) != set(SEVERITIES):
        raise ConfigurationError("Define all four financial bands.")
    previous_max = 0
    for severity in SEVERITIES:
        low, high = policy.bands[severity]
        if any(type(n) is not int for n in (low, high)):
            raise ConfigurationError("Money must use integer minor units.")
        if not 0 <= low < high or low < previous_max:
            raise ConfigurationError("The pilot requires ordered, non-overlapping bands.")
        previous_max = high
    rules = {r.id: r for r in policy.rules}
    profiles = {p.id: p for p in policy.profiles}
    ids = [x.id for x in (*policy.rules, *policy.profiles, *policy.caps)]
    if any(not isinstance(name, str) or not name.strip() for name in ids):
        raise ConfigurationError("Identifiers must be non-empty strings.")
    if len(ids) != len(set(ids)):
        raise ConfigurationError("Identifiers must be unique.")
    for rule in policy.rules:
        if rule.severity not in SEVERITIES or not rule.when or not rule.group.strip():
            raise ConfigurationError("Each rule needs conditions and a severity.")
    for winner, loser in policy.precedence:
        if winner not in rules or loser not in rules:
            raise ConfigurationError("Precedence refers to an unknown rule.")
        if rules[winner].group != rules[loser].group:
            raise ConfigurationError("Precedence is local to the same impact group.")
    graph = dominance(policy)
    for profile in policy.profiles:
        if len(profile.weights) > 64 or len(profile.anchors) > 64:
            raise ConfigurationError("This pilot supports at most 64 criteria or financial anchors per profile.")
        if profile.severity not in SEVERITIES or not profile.required_rules:
            raise ConfigurationError("A profile needs a severity and eligibility rules.")
        if policy.importance_ranges:
            low_importance, high_importance = policy.importance_ranges[profile.severity]
            if type(profile.importance) is not int or not low_importance <= profile.importance <= high_importance:
                raise ConfigurationError("Impact importance must be inside its severity's published interval.")
        elif profile.importance is not None:
            raise ConfigurationError("An impact importance requires published severity intervals.")
        if any(r not in rules for r in profile.required_rules):
            raise ConfigurationError("Profile eligibility refers to an unknown rule.")
        if len(profile.required_rules) != len(set(profile.required_rules)):
            raise ConfigurationError("Duplicate eligibility rules in a profile.")
        combined_conditions = dict(profile.when)
        for rule_id in profile.required_rules:
            if set(profile.required_rules) & graph[rule_id]:
                raise ConfigurationError("A profile requires both an overriding and an overridden rule.")
            for name, expected_value in rules[rule_id].when.items():
                if name in combined_conditions and combined_conditions[name] != expected_value:
                    raise ConfigurationError("A profile has mutually contradictory eligibility conditions.")
                combined_conditions[name] = expected_value
        expected = max((rules[r].severity for r in profile.required_rules),
                       key=SEVERITIES.index)
        if profile.severity != expected:
            raise ConfigurationError("Profile and eligibility-rule severities disagree.")
        if (not profile.weights or any(not isinstance(w, Fraction) or w < 0
                                       for w in profile.weights.values())
                or sum(profile.weights.values()) <= 0):
            raise ConfigurationError("Weights must be fixed, non-negative fractions with a positive sum.")
        anchors = profile.anchors
        low, high = policy.bands[profile.severity]
        if len(anchors) < 2 or anchors[0] != (0, low) or anchors[-1] != (1, high):
            raise ConfigurationError("Curves must reach both published band endpoints.")
        if any(not isinstance(q, Fraction) or type(p) is not int for q, p in anchors):
            raise ConfigurationError("Curve points require exact fractions and integer money.")
        for (q0, p0), (q1, p1) in zip(anchors, anchors[1:]):
            if not q0 < q1 or p0 > p1:
                raise ConfigurationError("Curves must be monotone with strictly ordered Q coordinates.")
    for cap in policy.caps:
        if cap.mode not in ("when_matches", "unless_all"):
            raise ConfigurationError("Unknown cap mode.")
        if not cap.when or not cap.severities or any(s not in SEVERITIES for s in cap.severities):
            raise ConfigurationError("A cap requires conditions and applicable severities.")
        if any(p not in profiles for p in cap.profiles):
            raise ConfigurationError("A cap refers to an unknown profile.")
        if cap.profiles and set(cap.severities) != {profiles[p].severity for p in cap.profiles}:
            raise ConfigurationError("A profile cap's severity scope contradicts its target profiles.")
        if cap.amount is not None:
            if type(cap.amount) is not int:
                raise ConfigurationError("Caps must use integer minor units.")
            if any(not policy.bands[s][0] <= cap.amount <= policy.bands[s][1]
                   for s in cap.severities):
                raise ConfigurationError("A cap contradicts a published severity band.")
    for entry in (*policy.rules, *policy.profiles, *policy.caps):
        if any(not isinstance(k, str) or not k.strip() for k in entry.when):
            raise ConfigurationError("Fact identifiers must be non-empty strings.")
        if any(type(v) is not bool for v in entry.when.values()):
            raise ConfigurationError("Rule predicates must be boolean.")


def interpolate(anchors, q):
    for (q0, p0), (q1, p1) in zip(anchors, anchors[1:]):
        if q0 <= q <= q1:
            return Fraction(p0) + (q - q0) * (p1 - p0) / (q1 - q0)
    raise EvidenceError("Q lies outside the published curve.")


def reward_position(policy, profile, q):
    """Bounded importance changes placement, preserving both financial endpoints.

    For 0<=a,i,Q<=1: R=Q+a*i*Q*(1-Q) lies in [0,1], increases
    with i, and dR/dQ=1+a*i*(1-2Q)>=0. Severity is unchanged.
    """
    if not policy.importance_ranges:
        return q, Fraction(0)
    low, high = policy.importance_ranges[profile.severity]
    importance_position = Fraction(profile.importance - low, high - low)
    return q + policy.importance_influence * importance_position * q * (1 - q), importance_position


def _evaluate(policy: Policy, report: Report):
    validate_policy(policy)
    if report.policy_version != policy.version or report.policy_sha256 != fingerprint(policy):
        raise EvidenceError("The supplied policy does not match the report's frozen snapshot.")
    if report.asset != policy.asset or report.currency != policy.currency:
        raise EvidenceError("The report's asset/currency does not match the policy snapshot.")
    if not isinstance(report.core_reference, str) or not report.core_reference.strip():
        raise EvidenceError("BEMIX inputs require an evidence/decision reference.")
    if report.eligibility not in ("accepted", "rejected", "pending"):
        raise EvidenceError("Unknown eligibility status.")
    if not isinstance(report.eligibility_reference, str) or not report.eligibility_reference.strip():
        raise EvidenceError("Eligibility requires an evidence/decision reference.")
    for fact in report.facts.values():
        if fact.value is not None and (type(fact.value) is not bool or not isinstance(fact.reference, str) or not fact.reference.strip()):
            raise EvidenceError("Established facts need a boolean and an evidence reference.")
    known_profiles = {p.id: p for p in policy.profiles}
    if set(report.metrics) - known_profiles.keys():
        raise EvidenceError("Metrics refer to an unknown impact profile.")
    known_facts = set(policy.favorable_facts).union(*(set(entry.when) for entry in (*policy.rules, *policy.profiles, *policy.caps)))
    if set(report.facts) - known_facts:
        raise EvidenceError("Facts refer to identifiers not declared in this policy.")
    for profile_id, values in report.metrics.items():
        if set(values) - known_profiles[profile_id].weights.keys():
            raise EvidenceError("Report metrics cannot add criteria to a fixed profile.")
        for metric in values.values():
            if metric.state not in ("demonstrated", "absent", "unknown"):
                raise EvidenceError("A fixed-profile metric cannot be removed as N/A.")
            if metric.state == "unknown":
                if metric.value is not None:
                    raise EvidenceError("Unknown evidence cannot carry an assigned score.")
            elif (not isinstance(metric.value, Fraction) or not 0 <= metric.value <= 1
                  or not isinstance(metric.reference, str) or not metric.reference.strip()
                  or metric.state == "absent" and metric.value != 0):
                raise EvidenceError("Established metrics need a score in [0, 1] and a reference.")
    used_input = asdict(report)
    used_input.pop("researcher_label")
    result = {
        "policy": {"version": policy.version, "sha256": fingerprint(policy),
                   "asset": policy.asset, "currency": policy.currency,
                   "experimental": policy.experimental},
        "input_sha256": fingerprint(used_input),
        "bemix": bemix(*report.core),
        "classification": {"status": "pending"},
        "reward": {"status": "pending", "amount": None},
    }
    classification, reward = result["classification"], result["reward"]
    if report.eligibility != "accepted":
        classification.update(status="ineligible" if report.eligibility == "rejected" else "pending",
                              reason="Report eligibility has not been accepted.")
        reward["status"] = classification["status"]
        return result
    if result["bemix"]["score"] is None:
        classification.update(status="evidence_conflict", reason="Accepted impact conflicts with E=D=O=0; review the evidence and anchors.")
        return result
    rule_states = {r.id: matches(r.when, report.facts) for r in policy.rules}
    classification["rule_states"] = {key: "matched" if state is True else "unmatched" if state is False else "unknown"
                                     for key, state in sorted(rule_states.items())}
    matched = {r for r, state in rule_states.items() if state is True}
    unknown = sorted(r for r, state in rule_states.items() if state is None)
    classification.update(matched_rules=sorted(matched), pending_rules=unknown)
    if unknown:
        classification["reason"] = "Unknown facts may change the policy decision."
        return result
    graph = dominance(policy)
    dominated = set().union(*(graph[r] for r in matched)) if matched else set()
    accepted = matched - dominated
    classification["accepted_rules"] = sorted(accepted)
    classification["overridden_rules"] = sorted(matched & dominated)
    if not accepted:
        classification.update(status="unmapped", reason="No published rule matches; human review is required.")
        return result
    groups = {}
    for rule in policy.rules:
        if rule.id in accepted:
            groups.setdefault(rule.group, set()).add(rule.severity)
    conflicts = sorted(group for group, severities in groups.items() if len(severities) > 1)
    if conflicts:
        classification.update(status="policy_conflict", conflicting_groups=conflicts,
                              reason="Conflicting rules have no published precedence.")
        return result
    severity = max((r.severity for r in policy.rules if r.id in accepted), key=SEVERITIES.index)
    classification.update(status="resolved", severity=severity)
    low, high = policy.bands[severity]
    candidates, pending = [], []
    eligible_profiles = []
    for profile in policy.profiles:
        if not set(profile.required_rules) <= accepted:
            continue
        state = matches(profile.when, report.facts)
        if state is None:
            pending.append(f"profile:{profile.id}:eligibility")
        elif state:
            eligible_profiles.append(profile)
    global_caps = []
    profile_caps = {p.id: [] for p in eligible_profiles}
    for cap in policy.caps:
        applicable = ([p.id for p in eligible_profiles if p.id in cap.profiles
                       and p.severity in cap.severities] if cap.profiles else
                      ["global"] if severity in cap.severities else [])
        if not applicable:
            continue
        state = cap_state(cap, report.facts)
        if state is None or state and cap.amount is None:
            pending.append(f"cap:{cap.id}:unresolved")
        elif state:
            if cap.profiles:
                for profile_id in applicable:
                    profile_caps[profile_id].append(cap)
            else:
                global_caps.append(cap)
    for profile in eligible_profiles:
        values = report.metrics.get(profile.id, {})
        if set(values) - set(profile.weights):
            raise EvidenceError("Report metrics cannot add criteria to a fixed profile.")
        if any(name not in values or values[name].state == "unknown"
               for name, weight in profile.weights.items() if weight > 0):
            pending.append(f"profile:{profile.id}:metrics")
            continue
        q = sum((weight * values[name].value for name, weight in profile.weights.items() if weight > 0), Fraction(0)) / sum(profile.weights.values())
        position, importance_position = reward_position(policy, profile, q)
        raw = interpolate(profile.anchors, position)
        profile_ceiling = min((c.amount for c in profile_caps[profile.id]),
                              default=policy.bands[profile.severity][1])
        candidate = min(raw, profile_ceiling)
        candidates.append({"profile": profile.id, "severity": profile.severity,
                           "Q": str(q), "importance": profile.importance,
                           "importance_position": str(importance_position),
                           "importance_influence": str(policy.importance_influence),
                           "reward_position": str(position), "raw_minor_units": str(raw),
                           "eligible_minor_units": str(candidate),
                           "ceiling_minor_units": profile_ceiling,
                           "cap_rules": sorted(c.id for c in profile_caps[profile.id]),
                           "metrics": canonical(values), "weights": canonical(dict(profile.weights))})
    reward.update(profiles=candidates, pending=pending,
                  global_cap_rules=sorted(c.id for c in global_caps))
    if not any(p["severity"] == severity for p in candidates):
        pending.append("resolved_severity:financial_profile_missing")
    if pending or not candidates:
        reward["reason"] = "Financial evidence or a policy consequence remains unresolved."
        return result
    best = max(Fraction(p["eligible_minor_units"]) for p in candidates)
    global_ceiling = min((c.amount for c in global_caps), default=high)
    # Validate consistency rather than silently raising a result to its band floor.
    final = min(best, global_ceiling)
    if not low <= final <= high:
        raise ConfigurationError("Financial profiles do not support the resolved severity band.")
    rounded = min(round_minor(final), global_ceiling, high)
    reward.update(status="calculated", amount=money(rounded), minor_units=rounded,
                  uncapped_max_minor_units=str(best), global_ceiling=money(global_ceiling),
                  eligible_ceiling_minor_units=min(global_ceiling, max(p["ceiling_minor_units"] for p in candidates)),
                  exact_final_minor_units=str(final), rounding="nearest minor unit, half upward",
                  winning_profiles=sorted(p["profile"] for p in candidates
                                          if Fraction(p["eligible_minor_units"]) == best))
    return result


def evaluate(policy: Policy, report: Report):
    """Calculate exactly when possible, or give conditional bounds for missing inputs.

    Verification of the evidence is a workflow concern. Missing numerical inputs
    are handled separately: an unknown that cannot change severity or money must
    not suppress an otherwise determinate calculation. Boolean completions are
    explicitly bounded; numeric bounds use monotonicity of the fixed profiles.
    No hypothetical values are promoted to observed facts in the returned record.
    """
    result = _evaluate(policy, report)
    if (result["reward"]["status"] != "pending" or report.eligibility != "accepted"
            or result["bemix"]["score"] is None):
        return result
    unresolved_facts = set()
    states = {r.id: matches(r.when, report.facts) for r in policy.rules}
    possible_profiles = {p.id for p in policy.profiles if all(states[r] is not False for r in p.required_rules)}
    conditions = [r.when for r in policy.rules]
    conditions += [p.when for p in policy.profiles if p.id in possible_profiles]
    conditions += [c.when for c in policy.caps if not c.profiles or set(c.profiles) & possible_profiles]
    for condition in conditions:
        if matches(condition, report.facts) is None:
            unresolved_facts.update(k for k in condition if k not in report.facts or report.facts[k].value is None)
    unknowns = sorted(unresolved_facts)
    if len(unknowns) > 8:
        result["reward"]["uncertainty"] = {"status": "needs_inputs", "facts": unknowns,
            "reason": "More than eight unresolved boolean inputs; provide the relevant declared values to obtain an exact calculation."}
        return result
    endpoints = []
    unresolved_metrics = set()
    unresolved_branches = False
    for values in product((False, True), repeat=len(unknowns)):
        facts = {**report.facts, **{k: Fact(v, "Conditional calculation only") for k, v in zip(unknowns, values)}}
        for extreme in (0, 1):
            metrics = {p: dict(v) for p, v in report.metrics.items()}
            for profile in policy.profiles:
                for name, weight in profile.weights.items():
                    if weight == 0:
                        continue
                    metric = metrics.get(profile.id, {}).get(name)
                    if metric is None or metric.value is None:
                        if profile.id in possible_profiles:
                            unresolved_metrics.add(f"{profile.id}.{name}")
                        metrics.setdefault(profile.id, {})[name] = Metric(Fraction(extreme), "Conditional bound only")
            hypothetical = replace(report, facts=facts, metrics=metrics)
            calculated = _evaluate(policy, hypothetical)
            if calculated["reward"]["status"] != "calculated":
                unresolved_branches = True
            else:
                branch = calculated["reward"]
                endpoints.append({"severity": calculated["classification"]["severity"],
                    "rules": set(calculated["classification"]["accepted_rules"]),
                    "amount": Fraction(branch["exact_final_minor_units"]),
                    "winners": set(branch["winning_profiles"]),
                    "ceiling": branch["eligible_ceiling_minor_units"],
                    "global_ceiling": int(Fraction(branch["global_ceiling"]) * 100)})
    result["reward"]["uncertainty"] = {"facts": unknowns, "metrics": sorted(unresolved_metrics),
                                         "boolean_scenarios": 2 ** len(unknowns)}
    if unresolved_branches or not endpoints:
        result["reward"]["uncertainty"]["status"] = "conditional"
        return result
    low = min(r["amount"] for r in endpoints)
    high = max(r["amount"] for r in endpoints)
    severities = sorted({r["severity"] for r in endpoints}, key=SEVERITIES.index)
    result["classification"]["possible_severities"] = severities
    if len(severities) == 1:
        result["classification"].update(status="resolved", severity=severities[0])
        result["classification"].pop("reason", None)
    result["reward"]["range"] = {"lower": money(round_minor(low)), "upper": money(round_minor(high)),
                                     "currency": policy.currency}
    if low != high or len(severities) != 1:
        result["reward"]["uncertainty"]["status"] = "affects_result"
        return result
    reward = result["reward"]
    common_rules = set.intersection(*(r["rules"] for r in endpoints))
    common_winners = set.intersection(*(r["winners"] for r in endpoints))
    result["classification"]["accepted_rules"] = sorted(common_rules)
    result["classification"]["pending_rules"] = []
    reward.update(status="calculated", amount=money(round_minor(low)), minor_units=round_minor(low),
                  exact_final_minor_units=str(low), winning_profiles=sorted(common_winners),
                  eligible_ceiling_minor_units=min(r["ceiling"] for r in endpoints),
                  global_ceiling=money(min(r["global_ceiling"] for r in endpoints)),
                  rounding="nearest minor unit, half upward")
    reward["uncertainty"]["status"] = "does_not_change_result"
    reward["pending"] = []
    reward.pop("reason", None)
    return result
