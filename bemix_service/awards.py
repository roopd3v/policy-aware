"""Program-authorized awards, kept separate from calculated scores and bounties."""

from bemix_pilot.engine import money
from .submissions import obj, require, string


def validate_adjustments(rules):
    require(isinstance(rules, list) and len(rules) <= 32, "At most 32 published adjustment rules are supported.")
    seen = set()
    for rule in rules:
        obj(rule, ("id", "description", "min_delta_minor_units", "max_delta_minor_units"),
            ("override_calculation_limits", "min_award_minor_units", "max_award_minor_units"))
        string(rule["id"], 100); string(rule["description"], 8000)
        require(rule["id"] not in seen, "Duplicate award adjustment identifier.")
        seen.add(rule["id"])
        low, high = rule["min_delta_minor_units"], rule["max_delta_minor_units"]
        require(type(low) is int and type(high) is int and low <= high, "Adjustment limits must be ordered integer minor units.")
        override = rule.get("override_calculation_limits", False)
        require(type(override) is bool, "Adjustment exception flag must be boolean.")
        if override:
            lower, upper = rule.get("min_award_minor_units"), rule.get("max_award_minor_units")
            require(type(lower) is int and type(upper) is int and 0 <= lower <= upper,
                    "An exception must publish explicit non-negative award limits.")
        else:
            require("min_award_minor_units" not in rule and "max_award_minor_units" not in rule,
                    "Custom award limits require an explicit published exception.")
    return rules


def calculate_award(policy, governance, decision, adjustment):
    result = decision["body"]["result"]
    reward = result["reward"]
    require(reward["status"] in ("calculated", "ineligible"), "The program must resolve the relevant assessment before confirming an award.")
    if reward["status"] == "ineligible":
        require(adjustment is None, "An ineligible finding cannot receive an adjustment through this workflow.")
        return {"calculated_bounty_minor_units": 0, "adjustment_minor_units": 0, "amount_minor_units": 0,
                "amount": "0.00", "currency": policy.currency, "adjustment": None}
    base = reward["minor_units"]
    delta, applied = 0, None
    severity = result["classification"]["severity"]
    lower = policy.bands[severity][0]
    upper = reward["eligible_ceiling_minor_units"]
    if adjustment is not None:
        obj(adjustment, ("rule_id", "delta_minor_units", "reason"))
        rules = {r["id"]: r for r in validate_adjustments(governance.get("award_adjustments", []))}
        identifier = adjustment["rule_id"]
        require(isinstance(identifier, str) and identifier in rules, "The adjustment must refer to a rule in this case's policy snapshot.")
        rule = rules[identifier]
        delta = adjustment["delta_minor_units"]
        require(type(delta) is int and rule["min_delta_minor_units"] <= delta <= rule["max_delta_minor_units"],
                "Adjustment is outside its published range.")
        string(adjustment["reason"], 8000)
        if rule.get("override_calculation_limits", False):
            lower, upper = rule["min_award_minor_units"], rule["max_award_minor_units"]
        applied = {**adjustment, "published_rule": rule}
    final = base + delta
    require(lower <= final <= upper, "Final award is outside the applicable published limits.")
    return {"calculated_bounty_minor_units": base, "adjustment_minor_units": delta,
            "amount_minor_units": final, "amount": money(final), "currency": policy.currency, "adjustment": applied}
