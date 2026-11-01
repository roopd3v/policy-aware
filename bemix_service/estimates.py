"""Use the same calculator on declared inputs, without granting validation status."""

from dataclasses import replace
from fractions import Fraction

from bemix_pilot.engine import Fact, Metric, Report, canonical, evaluate, fingerprint
from bemix_pilot.serialization import _fraction
from .submissions import obj, require, validate_claims


DIMENSIONS = {"E": (0, 4), "D": (1, 4), "O": (2, 4), "X": (3, 2)}


def declared_fact_names(policy):
    return set(policy.favorable_facts).union(*(set(item.when) for item in (*policy.rules, *policy.profiles, *policy.caps)))


def validate_estimation(policy, catalog, configuration):
    obj(configuration, (), ("fact_defaults", "option_facts", "metric_bindings"))
    known = declared_fact_names(policy)
    defaults = configuration.get("fact_defaults", {})
    require(isinstance(defaults, dict) and not set(defaults) - known, "Estimation defaults must use published fact identifiers.")
    require(all(type(v) is bool for v in defaults.values()), "Declared form defaults must be boolean.")
    option_facts = configuration.get("option_facts", {})
    require(isinstance(option_facts, dict) and not set(option_facts) - {o["id"] for o in catalog}, "Unknown option in estimation mapping.")
    for facts in option_facts.values():
        require(isinstance(facts, dict) and not set(facts) - known and all(type(v) is bool for v in facts.values()),
                "Option fact mappings must use published boolean conditions.")
    profiles = {p.id: p for p in policy.profiles}
    bindings = configuration.get("metric_bindings", {})
    require(isinstance(bindings, dict) and not set(bindings) - set(profiles), "Unknown bound profile.")
    for identifier, mapping in bindings.items():
        require(isinstance(mapping, dict) and not set(mapping) - set(profiles[identifier].weights), "Unknown bound criterion.")
        require(all(isinstance(v, str) and v in DIMENSIONS for v in mapping.values()), "A metric binding must name E, D, O or X.")
    return configuration


def apply_bindings(policy, governance, report):
    metrics = {key: dict(value) for key, value in report.metrics.items()}
    bindings = governance.get("estimation", {}).get("metric_bindings", {})
    for profile, mapping in bindings.items():
        for metric, dimension in mapping.items():
            index, maximum = DIMENSIONS[dimension]
            metrics.setdefault(profile, {})[metric] = Metric(Fraction(report.core[index], maximum),
                f"Published binding {dimension}/{maximum}; {report.core_reference}")
    return replace(report, metrics=metrics)


def submitted_report(policy, governance, claims, inputs):
    obj(inputs, ("core",), ("facts", "metrics"))
    core = inputs["core"]
    require(isinstance(core, list) and len(core) == 4 and all(type(v) is int for v in core), "Provide the four BEMIX integers E, D, O, X.")
    require(all(0 <= v <= 4 for v in core[:3]) and 0 <= core[3] <= 2, "BEMIX values are outside their published ranges.")
    catalog = governance.get("submission_options", [])
    validate_claims(policy, catalog, claims)
    configuration = validate_estimation(policy, catalog, governance.get("estimation", {}))
    facts = {name: Fact(value, "Published form default, still researcher-declared")
             for name, value in configuration.get("fact_defaults", {}).items()}
    asserted = {}
    rules = {r.id: r for r in policy.rules}
    options = {o["id"]: o for o in catalog}
    for claim in claims:
        option = options[claim["option_id"]]
        # A single mapped rule has an unambiguous declaration meaning. A program
        # can publish a custom mapping for alternatives; no trusted fact is created.
        mapping = configuration.get("option_facts", {}).get(option["id"])
        if mapping is None:
            mapping = rules[option["rule_ids"][0]].when if len(option["rule_ids"]) == 1 else {}
        for name, value in mapping.items():
            require(name not in asserted or asserted[name] == value, "Selected options declare contradictory conditions.")
            asserted[name] = value
            facts[name] = Fact(value, "Researcher-selected behaviour: " + option["id"])
    explicit = inputs.get("facts", {})
    require(isinstance(explicit, dict) and not set(explicit) - declared_fact_names(policy), "Unknown declared fact.")
    for name, value in explicit.items():
        require(value is None or type(value) is bool, "Declared facts must be true, false or null.")
        require(name not in asserted or value is asserted[name],
                f"Declared fact '{name}' conflicts with a selected behaviour. Change the selection or supply the matching value.")
        facts[name] = Fact(value, "Researcher-submitted value")
    provided = inputs.get("metrics", {})
    profiles = {p.id: p for p in policy.profiles}
    require(isinstance(provided, dict) and not set(provided) - set(profiles), "Unknown declared profile.")
    metrics = {}
    for profile, values in provided.items():
        require(isinstance(values, dict) and not set(values) - set(profiles[profile].weights), "Unknown declared criterion.")
        metrics[profile] = {name: Metric(None if value is None else _fraction(value), "Researcher-submitted value",
                                       "unknown" if value is None else "demonstrated") for name, value in values.items()}
        require(all(metric.value is None or 0 <= metric.value <= 1 for metric in metrics[profile].values()),
                "Declared criterion values must lie between zero and one.")
    report = Report("accepted", "Eligible only as an assumption for this estimate", tuple(core), facts, metrics,
                    policy.version, fingerprint(policy), policy.asset, policy.currency,
                    "Researcher-submitted BEMIX values")
    return apply_bindings(policy, governance, report)


def estimated_bounty(policy, governance, claims, inputs):
    report = submitted_report(policy, governance, claims, inputs)
    result = evaluate(policy, report)
    reward = result["reward"]
    amount = reward["amount"]
    severity = result["classification"].get("severity")
    status = "estimated" if reward["status"] == "calculated" else "conditional"
    if result["bemix"]["score"] is None:
        amount, severity, status = "0.00", "Informational", "estimated"
    return {"stage": "estimated_bounty", "status": status, "provisional": True,
            "facts_validated": False, "basis": "researcher_submitted_information",
            "amount": amount, "currency": policy.currency, "severity": severity,
            "range": reward.get("range"), "bemix": result["bemix"],
            "calculation": result,
            "prepared_inputs": {"core": list(report.core), "facts": canonical(report.facts), "metrics": canonical(report.metrics)},
            "policy_sha256": fingerprint(policy),
            "notice": "Exact arithmetic on declared inputs is provisional. The responsible triager and program can correct the inputs and recalculate."}


def bounty_view(decision, stage="validated_bounty"):
    result = decision["body"]["result"]
    reward = result["reward"]
    return {"stage": stage, "status": reward["status"], "amount": reward["amount"],
            "currency": result["policy"]["currency"], "severity": result["classification"].get("severity"),
            "range": reward.get("range"), "bemix": result["bemix"],
            "assessment_stage": decision["body"]["assessment_stage"], "assessment_version": decision["revision"],
            "assessment_hash": decision["hash"]}
