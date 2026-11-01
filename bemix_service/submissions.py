"""Researcher declarations and program-owned submission options.

Selections and submitted values can produce a concrete provisional estimate. They never grant validated status. The responsible assessor confirms or corrects.
"""

import re

from bemix_pilot.engine import EvidenceError, SEVERITIES


def require(condition, message):
    if not condition:
        raise EvidenceError(message)


def obj(value, required, optional=()):
    require(isinstance(value, dict), "Expected a submission object.")
    require(not (set(value) - set(required) - set(optional)) and not (set(required) - set(value)),
            "Missing or unknown submission fields.")


def string(value, maximum):
    require(isinstance(value, str) and bool(value.strip()) and len(value) <= maximum,
            "Submission text is missing or too long.")
    return value


def ids(values, allowed, maximum=8):
    require(isinstance(values, list) and len(values) <= maximum
            and all(isinstance(v, str) and v in allowed for v in values),
            "Selections must use identifiers from the pinned program catalog.")
    require(len(values) == len(set(values)), "Duplicate selection identifiers.")
    return values


def validate_catalog(policy, options):
    require(isinstance(options, list) and len(options) <= 64, "At most 64 submission options are supported.")
    rules = {r.id for r in policy.rules}
    seen = set()
    for option in options:
        obj(option, ("id", "label", "rule_ids", "evidence_prompts"))
        identifier = option["id"]
        require(isinstance(identifier, str) and re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", identifier),
                "Invalid submission option identifier.")
        require(identifier not in seen, "Duplicate submission option identifier.")
        seen.add(identifier)
        string(option["label"], 240)
        rule_ids = ids(option["rule_ids"], rules)
        require(bool(rule_ids) or identifier == "other", "Only the 'other' option may have no automatic rule mapping.")
        prompts = option["evidence_prompts"]
        require(isinstance(prompts, list) and len(prompts) <= 8, "At most eight evidence prompts per option.")
        prompt_ids = set()
        for prompt in prompts:
            obj(prompt, ("id", "prompt", "required"))
            require(isinstance(prompt["id"], str) and re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", prompt["id"]),
                    "Invalid evidence prompt identifier.")
            require(prompt["id"] not in prompt_ids, "Duplicate evidence prompt identifier.")
            prompt_ids.add(prompt["id"])
            string(prompt["prompt"], 2000)
            require(type(prompt["required"]) is bool, "Evidence prompt requirement must be boolean.")
    return options


def validate_claims(policy, catalog, claims):
    validate_catalog(policy, catalog)
    options = {o["id"]: o for o in catalog}
    require(isinstance(claims, list) and 1 <= len(claims) <= 8, "Select one to eight behaviours.")
    seen = set()
    for claim in claims:
        obj(claim, ("option_id", "evidence"))
        identifier = claim["option_id"]
        require(isinstance(identifier, str) and identifier in options and identifier not in seen,
                "Claim identifier is unknown or duplicated.")
        seen.add(identifier)
        evidence = claim["evidence"]
        require(isinstance(evidence, dict), "Evidence must map program prompt identifiers to report sections or references.")
        prompts = {p["id"]: p for p in options[identifier]["evidence_prompts"]}
        require(not set(evidence) - set(prompts), "Unknown evidence prompt identifier.")
        for name, prompt in prompts.items():
            if prompt["required"] or name in evidence:
                string(evidence.get(name), 8000)
    return claims


def preview(policy, catalog, claims, estimate=None):
    options = {o["id"]: o for o in catalog}
    rules = {r.id: r for r in policy.rules}
    result = []
    for claim in claims:
        option = options[claim["option_id"]]
        mapped = set(option["rule_ids"])
        result.append({"option_id": option["id"], "label": option["label"], "rule_ids": sorted(mapped),
                       "conditional_severities": sorted({rules[r].severity for r in mapped}, key=SEVERITIES.index),
                       "candidate_profile_ids": sorted(p.id for p in policy.profiles if set(p.required_rules) <= mapped)})
    return {"status": "awaiting_validation", "options": result, "trusted_facts_assigned": False,
            "financial_amount": estimate["amount"] if estimate else None,
            "financial_stage": "estimated_bounty", "range": estimate.get("range") if estimate else None,
            "notice": "The estimate is calculated from declared inputs and will be recalculated when findings are validated or corrected."}


def validate_claim_review(policy, catalog, claims, review, decision):
    obj(review, ("claims",), ("additional_options",))
    options = {o["id"]: o for o in catalog}
    originals = {c["option_id"] for c in claims}
    reviews = review["claims"]
    require(isinstance(reviews, list) and len(reviews) == len(originals), "Address each declared behaviour once.")
    matched = set(decision["classification"].get("matched_rules", []))
    seen, confirmed = set(), set()
    pending_rules = set()
    for item in reviews:
        obj(item, ("option_id", "outcome", "confirmed_option_ids", "reason"))
        identifier = item["option_id"]
        require(isinstance(identifier, str) and identifier in originals and identifier not in seen,
                "Claim review identifier is unknown or duplicated.")
        seen.add(identifier)
        outcome = item["outcome"]
        require(outcome in ("confirmed", "changed", "not_demonstrated", "pending"), "Unknown claim review outcome.")
        chosen = ids(item["confirmed_option_ids"], options)
        string(item["reason"], 8000)
        if outcome == "confirmed":
            require(chosen == [identifier], "Confirmation must retain the declared behaviour.")
        elif outcome == "changed":
            require(bool(chosen) and chosen != [identifier], "A changed classification needs a replacement option.")
        else:
            require(not chosen, "Pending or unsupported claims cannot carry confirmed selections.")
        if outcome == "pending":
            pending_rules.update(options[identifier]["rule_ids"])
        if outcome == "not_demonstrated":
            require(not (matched & set(options[identifier]["rule_ids"])), "Claim rejection contradicts matched technical facts.")
        confirmed.update(chosen)
    extra = ids(review.get("additional_options", []), options)
    confirmed.update(extra)
    for identifier in confirmed:
        mapped = set(options[identifier]["rule_ids"])
        require(not mapped or bool(mapped & matched), "Confirmed selection is not supported by the assessed rule conditions.")
    if pending_rules and decision["reward"]["status"] == "calculated":
        certain_winners = set(decision["reward"].get("winning_profiles", []))
        supported = [p for p in policy.profiles if p.id in certain_winners
                     and not set(p.required_rules) & pending_rules]
        require(bool(supported), "A claim needed to justify the calculated reward still requires validation.")
    return review
