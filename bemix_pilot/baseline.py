"""Reproduce the observed BEMIX marginal per-point calculation.

Rates and returned values use the same integer minor units. This is separate from the policy-aware reward engine and never changes the core score.
"""

from .engine import ConfigurationError, EvidenceError, SEVERITIES


def baseline_bounty(score, per_point):
    if score is None:
        score = 0
    if type(score) is not int or not 0 <= score <= 30:
        raise EvidenceError("BEMIX payout requires an integer score from 0 to 30.")
    if set(per_point) != set(SEVERITIES) or any(type(v) is not int or v < 0 for v in per_point.values()):
        raise ConfigurationError("Define four non-negative integer per-point rates in minor units.")
    total = 0
    for severity, start, end in zip(SEVERITIES, (1, 8, 15, 22), (7, 14, 21, 30)):
        total += max(0, min(score, end) - start + 1) * per_point[severity]
    return total


def baseline_bands(per_point):
    return {severity: (baseline_bounty(start, per_point), baseline_bounty(end, per_point))
            for severity, start, end in zip(SEVERITIES, (1, 8, 15, 22), (7, 14, 21, 30))}
