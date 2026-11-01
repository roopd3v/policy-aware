"""Strict, versioned JSON transport. No code execution or implicit coercion."""

from dataclasses import MISSING, fields
from fractions import Fraction
import json
from pathlib import Path
import re

from .engine import CapRule, Fact, Metric, Policy, Profile, Report, Rule, canonical, validate_policy


SCHEMA_VERSION = "bemix-policy-aware/1"
MAX_BYTES = 2_000_000


class InputError(ValueError):
    pass


def _mapping(value):
    if not isinstance(value, dict):
        raise InputError("Expected a JSON object.")
    if any(not isinstance(k, str) or not k.strip() for k in value):
        raise InputError("Object keys must be non-empty strings.")
    return value


def _fields(value, kind):
    data = _mapping(value)
    allowed = {f.name for f in fields(kind)}
    required = {f.name for f in fields(kind)
                if f.default is MISSING and f.default_factory is MISSING}
    if set(data) - allowed or required - set(data):
        raise InputError(f"Invalid fields for {kind.__name__}: missing={sorted(required - set(data))}, extra={sorted(set(data) - allowed)}")
    return dict(data)


def _string(value):
    if not isinstance(value, str):
        raise InputError("Expected a string.")
    return value


def _integer(value):
    if type(value) is not int:
        raise InputError("Expected an integer; booleans and floats are not accepted.")
    return value


def _fraction(value):
    if type(value) is int:
        return Fraction(value)
    if (not isinstance(value, str) or len(value) > 100
            or re.fullmatch(r"[+-]?\d+(?:/\d+|\.\d+)?", value) is None):
        raise InputError("Exact scores/weights must be integers or decimal/fraction strings.")
    try:
        return Fraction(value)
    except (ValueError, ZeroDivisionError) as error:
        raise InputError("Invalid exact fraction.") from error


def _sequence(value):
    if not isinstance(value, list):
        raise InputError("Expected a JSON array.")
    return value


def _conditions(value):
    data = _mapping(value)
    if any(type(v) is not bool for v in data.values()):
        raise InputError("Conditions must be booleans, not strings or numbers.")
    return dict(data)


def policy_from_dict(value):
    data = _fields(value, Policy)
    for key in ("version", "asset", "currency"):
        data[key] = _string(data[key])
    bands = {}
    for name, values in _mapping(data["bands"]).items():
        values = _sequence(values)
        if len(values) != 2:
            raise InputError("Each band needs exactly two endpoints.")
        bands[name] = tuple(_integer(v) for v in values)
    data["bands"] = bands
    rules = []
    for value in _sequence(data["rules"]):
        rule = _fields(value, Rule)
        for key in ("id", "group", "severity"):
            rule[key] = _string(rule[key])
        rule["when"] = _conditions(rule["when"])
        rules.append(Rule(**rule))
    data["rules"] = tuple(rules)
    profiles = []
    for value in _sequence(data["profiles"]):
        profile = _fields(value, Profile)
        for key in ("id", "severity"):
            profile[key] = _string(profile[key])
        profile["required_rules"] = tuple(_string(r) for r in _sequence(profile["required_rules"]))
        profile["weights"] = {k: _fraction(v) for k, v in _mapping(profile["weights"]).items()}
        profile["when"] = _conditions(profile.get("when", {}))
        if profile.get("importance") is not None:
            profile["importance"] = _integer(profile["importance"])
        anchors = []
        for point in _sequence(profile["anchors"]):
            point = _sequence(point)
            if len(point) != 2:
                raise InputError("Each financial anchor needs Q and an amount in minor units.")
            anchors.append((_fraction(point[0]), _integer(point[1])))
        profile["anchors"] = tuple(anchors)
        profiles.append(Profile(**profile))
    data["profiles"] = tuple(profiles)
    caps = []
    for value in _sequence(data.get("caps", [])):
        cap = _fields(value, CapRule)
        cap["id"] = _string(cap["id"])
        cap["mode"] = _string(cap.get("mode", "when_matches"))
        cap["when"] = _conditions(cap["when"])
        cap["severities"] = tuple(_string(v) for v in _sequence(cap["severities"]))
        cap["profiles"] = tuple(_string(v) for v in _sequence(cap.get("profiles", [])))
        cap["amount"] = None if cap["amount"] is None else _integer(cap["amount"])
        caps.append(CapRule(**cap))
    data["caps"] = tuple(caps)
    precedence = []
    for pair in _sequence(data.get("precedence", [])):
        pair = _sequence(pair)
        if len(pair) != 2:
            raise InputError("Each precedence relation needs two rule identifiers.")
        precedence.append(tuple(_string(v) for v in pair))
    data["precedence"] = tuple(precedence)
    data["favorable_facts"] = _conditions(data.get("favorable_facts", {}))
    importance_ranges = {}
    for severity, values in _mapping(data.get("importance_ranges", {})).items():
        values = _sequence(values)
        if len(values) != 2:
            raise InputError("Each importance interval needs two integer endpoints.")
        importance_ranges[severity] = tuple(_integer(v) for v in values)
    data["importance_ranges"] = importance_ranges
    data["importance_influence"] = _fraction(data.get("importance_influence", 0))
    policy = Policy(**data)
    validate_policy(policy)
    return policy


def report_from_dict(value):
    data = _fields(value, Report)
    for key in ("eligibility", "eligibility_reference", "policy_version", "policy_sha256",
                "asset", "currency", "core_reference"):
        data[key] = _string(data[key])
    data["researcher_label"] = _string(data.get("researcher_label", ""))
    core = _sequence(data["core"])
    if len(core) != 4:
        raise InputError("Core must contain exactly E, D, O and X.")
    data["core"] = tuple(_integer(v) for v in core)
    facts = {}
    for name, value in _mapping(data["facts"]).items():
        fact = _fields(value, Fact)
        if fact["value"] is not None and type(fact["value"]) is not bool:
            raise InputError("Fact values must be true, false or null.")
        fact["reference"] = _string(fact["reference"])
        facts[name] = Fact(**fact)
    data["facts"] = facts
    profiles = {}
    for profile, values in _mapping(data["metrics"]).items():
        metrics = {}
        for name, value in _mapping(values).items():
            metric = _fields(value, Metric)
            metric["value"] = None if metric["value"] is None else _fraction(metric["value"])
            metric["reference"] = _string(metric["reference"])
            metric["state"] = _string(metric.get("state", "demonstrated"))
            metrics[name] = Metric(**metric)
        profiles[profile] = metrics
    data["metrics"] = profiles
    return Report(**data)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_number(value):
    raise InputError("Use quoted decimal/fraction strings; floating-point JSON numbers are not accepted.")


def load_document(path, kind):
    with Path(path).open("rb") as handle:
        raw = handle.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise InputError("Input exceeds the 2 MB document limit.")
    try:
        data = json.loads(raw, object_pairs_hook=_pairs, parse_float=_reject_number,
                          parse_constant=_reject_number)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as error:
        raise InputError("Invalid JSON document.") from error
    _mapping(data)
    if set(data) != {"schema_version", kind} or data["schema_version"] != SCHEMA_VERSION:
        raise InputError(f"Expected schema {SCHEMA_VERSION!r} and a {kind!r} object.")
    return {"policy": policy_from_dict, "report": report_from_dict}[kind](data[kind])


def document(kind, value):
    return {"schema_version": SCHEMA_VERSION, kind: canonical(value)}
