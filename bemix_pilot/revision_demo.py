"""A minimal synthetic precedence counterexample and its revision guard."""

from dataclasses import replace
from fractions import Fraction as F
import json

from .engine import Profile, Rule, canonical
from .examples import base_report, bind, change_facts, metrics, sample_policy
from .revisions import evaluate_revision


def counterexample():
    base = sample_policy()
    policy = replace(base, version="SYNTHETIC-PRECEDENCE-COUNTEREXAMPLE-1",
                     favorable_facts={**base.favorable_facts, "wider_coverage": True},
                     rules=(*base.rules, Rule("wider-funds", "authorization", "Critical",
                                             {"unauthorized_fund_effect": True, "wider_coverage": True})),
                     precedence=(*base.precedence, ("wider-funds", "funds")),
                     profiles=(*base.profiles, Profile("wider-profile", "Critical", ("wider-funds",),
                                                       {"extent": F(1), "reliability": F(1)},
                                                       ((F(0), 450000), (F(1), 5000000)), importance=8)))
    before = bind(base_report(base), policy)
    before = replace(change_facts(before, wider_coverage=False),
                     metrics={"primary": metrics("0.8"), "wider-profile": metrics("0.4")})
    after = change_facts(before, wider_coverage=True)
    return policy, before, after


def demonstration():
    policy, before, after = counterexample()
    return {"synthetic": True, "policy": canonical(policy),
            "before": canonical(before), "after": canonical(after),
            "revision": evaluate_revision(policy, before, after, kind="improvement",
                                          reason_reference="SYNTHETIC wider coverage with earlier impact retained")}


if __name__ == "__main__":
    print(json.dumps(demonstration(), ensure_ascii=False, indent=2))
