"""Synthetic fixtures, not program adjudications or recommended rewards.

All bands, weights, precedence, scores, caps and factual assumptions below are illustrative fixtures, not an approved program configuration.
"""

from dataclasses import replace
from fractions import Fraction as F

from .engine import CapRule, Fact, Metric, Policy, Profile, Report, Rule, fingerprint


REF = "SYNTHETIC: no real report has been adjudicated"
TOP_REQUIREMENTS = {"fully_reliable": True, "no_user_interaction": True,
                    "all_protocols_covered": True, "no_effective_mitigation": True}
BANDS = {"Low": (10000, 30000), "Medium": (30000, 150000),
         "High": (200000, 450000), "Critical": (450000, 5000000)}
IMPORTANCE_RANGES = {"Low": (1, 2), "Medium": (3, 4), "High": (5, 7), "Critical": (8, 10)}


def profile(name, severity, rules, when=None):
    low, high = BANDS[severity]
    return Profile(name, severity, rules,
                   {"extent": F(1), "reliability": F(1)},
                   ((F(0), low), (F(1), high)), when or {}, IMPORTANCE_RANGES[severity][0])


def sample_policy():
    return Policy(
        version="SYNTHETIC-PUBLIC-1.3", asset="Wallet Extension / synthetic fixture",
        currency="USDC", bands=BANDS,
        importance_ranges=IMPORTANCE_RANGES, importance_influence=F(1, 5),
        favorable_facts={"unauthorized_fund_effect": True, "key_exfiltration": True,
                         "silent_signing": True, "restricted_path": False,
                         "restricted_keys": False, "fully_reliable": True,
                         "no_user_interaction": True, "all_protocols_covered": True,
                         "no_effective_mitigation": True, "combination_validated": True,
                         "global_limit_example": False, "profile_limit_example": False},
        rules=(
            Rule("funds", "authorization", "Critical",
                 {"unauthorized_fund_effect": True, "restricted_path": False}),
            Rule("restricted-funds", "authorization", "High",
                 {"unauthorized_fund_effect": True, "restricted_path": True}),
            Rule("induction", "authorization", "Medium", {"induced_signature": True}),
            Rule("silent", "authorization", "Critical", {"silent_signing": True}),
            Rule("keys", "secrets", "Critical",
                 {"key_exfiltration": True, "restricted_keys": False}),
            Rule("restricted-keys", "secrets", "High",
                 {"key_exfiltration": True, "restricted_keys": True}),
        ),
        precedence=(("funds", "induction"), ("restricted-funds", "induction"),
                    ("silent", "induction")),
        profiles=(
            profile("primary", "Critical", ("funds",)),
            profile("restricted", "High", ("restricted-funds",)),
            profile("induced", "Medium", ("induction",)),
            profile("silent-profile", "Critical", ("silent",)),
            profile("secondary", "Critical", ("keys",)),
            profile("restricted-secret", "High", ("restricted-keys",)),
            profile("combined", "Critical", ("funds", "keys"),
                    {"combination_validated": True}),
        ),
        caps=(
            CapRule("EXPERIMENT-only-top-cap", TOP_REQUIREMENTS,
                    ("Critical",), 4090000, mode="unless_all"),
            CapRule("EXPERIMENT-only-global-34000", {"global_limit_example": True},
                    ("Critical",), 3400000),
            CapRule("EXPERIMENT-only-profile-25000", {"profile_limit_example": True},
                    ("Critical",), 2500000, ("primary",)),
        ),
    )


def metrics(q):
    return {name: Metric(F(q), REF) for name in ("extent", "reliability")}


def estimation_config():
    """Published synthetic form defaults, not assertions about validated evidence."""
    policy = sample_policy()
    names = set(policy.favorable_facts).union(*(set(item.when) for item in (*policy.rules, *policy.profiles, *policy.caps)))
    return {"fact_defaults": {name: False for name in sorted(names)},
            "option_facts": {}, "metric_bindings": {}}


def example_inputs():
    return {"core": [4, 0, 0, 1], "metrics": {"primary": {"extent": "0.6", "reliability": "0.8"}}}


def adjustment_rules():
    return [{"id": "example-quality-adjustment", "description": "Synthetic published quality adjustment; not a program recommendation",
             "min_delta_minor_units": -100000, "max_delta_minor_units": 100000}]


def submission_options():
    """Illustrative program catalog, not an official wallet policy."""
    descriptions = [
        ("withdrawal_manipulation", "DApp initiates or alters withdrawals without authorization", "funds",
         "Compare the effect presented for approval with the actual authorised or signed effect."),
        ("restricted_withdrawal", "Unauthorized fund movement with additional prerequisites", "restricted-funds",
         "Identify the prerequisites, affected population and demonstrated fund effect."),
        ("induced_signature", "DApp induces the user to sign a transaction", "induction",
         "Show what the wallet displays, what the user approves, and the resulting effect."),
        ("silent_signing", "Transactions signed without user approval", "silent",
         "Demonstrate the absence of the required approval and the resulting signed effect."),
        ("key_exfiltration", "Unauthorized private-key disclosure without special restrictions", "keys",
         "Reference evidence of key disclosure and the actual prerequisites without including live secrets."),
        ("restricted_keys", "Key disclosure requiring additional prerequisites", "restricted-keys",
         "Describe the key-access boundary, restrictions and affected accounts."),
    ]
    options = [{"id": identifier, "label": label, "rule_ids": [rule],
                "evidence_prompts": [{"id": "proof", "prompt": prompt, "required": True}]}
               for identifier, label, rule, prompt in descriptions]
    options.append({"id": "other", "label": "Other / not listed", "rule_ids": [],
                    "evidence_prompts": [{"id": "proof", "prompt": "Describe the security boundary and demonstrated impact.", "required": True}]})
    return options


def base_report(policy):
    # All negatives in this fixture are explicit assumptions, not missing facts
    # silently converted to False by the engine.
    flags = {"unauthorized_fund_effect": True, "restricted_path": False,
             "induced_signature": True, "silent_signing": False,
             "key_exfiltration": False, "restricted_keys": False,
             "combination_validated": False, "fully_reliable": True,
             "no_user_interaction": False, "all_protocols_covered": False,
             "no_effective_mitigation": None,
             "global_limit_example": False, "profile_limit_example": False}
    return Report("accepted", REF, (4, 0, 0, 1),
                  {name: Fact(value, REF) for name, value in flags.items()},
                  {"primary": metrics("0.6")}, policy.version, fingerprint(policy),
                  policy.asset, policy.currency, REF,
                  "Researcher's proposed name")


def change_facts(report, **values):
    facts = dict(report.facts)
    facts.update({name: Fact(value, REF) for name, value in values.items()})
    return replace(report, facts=facts)


def bind(report, policy):
    """Explicitly create a separate fixture under another policy snapshot."""
    declared = set(policy.favorable_facts).union(*(set(entry.when) for entry in (*policy.rules, *policy.profiles, *policy.caps)))
    return replace(report, policy_version=policy.version, policy_sha256=fingerprint(policy),
                   asset=policy.asset, currency=policy.currency,
                   facts={k: v for k, v in report.facts.items() if k in declared})


def scenarios():
    policy = sample_policy()
    a = base_report(policy)
    b = replace(change_facts(a, unauthorized_fund_effect=False), eligibility="pending")
    c = change_facts(a, unauthorized_fund_effect=False, induced_signature=False,
                     key_exfiltration=True, restricted_keys=None)
    c = replace(c, core=(4, 4, 0, 2), metrics={"secondary": metrics("0.4")})
    d = change_facts(a, unauthorized_fund_effect=False, induced_signature=False,
                     silent_signing=True, **TOP_REQUIREMENTS)
    d = replace(d, core=(4, 0, 0, 2), metrics={"silent-profile": metrics("1")})
    e = change_facts(a, key_exfiltration=True)
    e = replace(e, core=(4, 4, 0, 1),
                metrics={"primary": metrics("0.6"), "secondary": metrics("0.4")})
    f = replace(a, metrics={"primary": {"extent": Metric(F("0.6"), REF),
                                       "reliability": Metric(F("0.8"), REF)}})
    g = replace(change_facts(a, restricted_path=True), core=(4, 0, 0, 0),
                metrics={"restricted": metrics("0.6")})
    h = replace(a, researcher_label="A different vulnerability name")
    conflict = replace(policy, version="SYNTHETIC-PILOT-UNRESOLVED", precedence=())
    combined = replace(change_facts(e, combination_validated=True),
                       metrics={**e.metrics, "combined": metrics("63/91")})
    unquantified = replace(policy, version="SYNTHETIC-PILOT-UNQUANTIFIED",
                           caps=(replace(policy.caps[0], amount=None), *policy.caps[1:]))
    missing = replace(a, metrics={"primary": {"extent": Metric(None, REF, "unknown"),
                                             "reliability": Metric(F("0.6"), REF)}})
    return [
        ("A", "Withdrawal effect differs from the confirmation; SYNTHETIC precedence accepted", policy, a),
        ("B", "Correctly displayed signature alone does not establish an eligible vulnerability", policy, b),
        ("C", "Key exfiltration: restrictions have not yet been established", policy, c),
        ("D", "Silent signing; all top conditions and maximum metrics assumed demonstrated", policy, d),
        ("E", "A plus a separately evaluated impact", policy, e),
        ("F", "A with higher reliability, all other facts unchanged", policy, f),
        ("G", "A after correcting a previously omitted prerequisite", policy, g),
        ("H", "Only the researcher's label changes", policy, h),
        ("A-conflict", "No published precedence between conflicting severity rules", conflict, bind(a, conflict)),
        ("C-complete", "Key restrictions explicitly excluded in this synthetic case", policy,
         change_facts(c, restricted_keys=False)),
        ("Combined", "An explicitly validated combination yields 36,000", policy, combined),
        ("Global-cap", "The combined fixture under a published SYNTHETIC 34,000 ceiling", policy,
         change_facts(combined, global_limit_example=True)),
        ("Unquantified", "Top restriction exists but its financial consequence is unspecified", unquantified,
         bind(a, unquantified)),
        ("Unknown-metric", "Severity resolved; an essential financial metric is still unknown", policy, missing),
    ]

