"""Run the synthetic examples: python3 -m bemix_pilot.demo [--json]."""

import argparse
import json

from .engine import canonical, evaluate
from .examples import scenarios


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Include exact audit calculations and fixture inputs.")
    args = parser.parse_args()
    results = []
    cases = scenarios()
    for case_id, description, policy, report in cases:
        result = evaluate(policy, report)
        results.append({"case": case_id, "description": description,
                        "synthetic": True, "input": canonical(report),
                        "policy_fixture": canonical(policy), "result": result})
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return
    print("EXPERIMENTAL — synthetic decisions and scores; not a program appraisal.")
    print(f"{'Case':<16} {'BEMIX':<8} {'Policy classification':<22} {'Reward':<17} Status")
    for case in results:
        result = case["result"]
        classification, reward = result["classification"], result["reward"]
        severity = classification.get("severity", classification["status"])
        amount = (reward["amount"] + " " + result["policy"]["currency"]) if reward["amount"] is not None else "—"
        print(f"{case['case']:<16} {str(result['bemix']['score']):<8} {severity:<22} {amount:<17} {reward['status']}")


if __name__ == "__main__":
    main()
