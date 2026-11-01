"""Regenerate synthetic, publication-safe examples without network access."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bemix_pilot.engine import canonical, evaluate
from bemix_pilot.examples import base_report, sample_policy, scenarios, submission_options, example_inputs
from bemix_pilot.lifecycle_example import demonstration as lifecycle_example
from bemix_pilot.revision_demo import counterexample, demonstration
from bemix_pilot.serialization import document
from bemix_pilot.platform_example import example_governance, example_guardrails, importance_demonstration, gist_examples


def dump(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def outputs():
    policy = sample_policy()
    rows = []
    for name, description, configuration, report in scenarios():
        rows.append({"case": name, "description": description, "synthetic": True,
                     "policy": canonical(configuration), "report": canonical(report),
                     "result": evaluate(configuration, report)})
    revision_policy, before, after = counterexample()
    return {
        "fixtures/demo-policy.json": dump(document("policy", policy)),
        "fixtures/demo-report.json": dump(document("report", base_report(policy))),
        "fixtures/submission-options.json": dump(submission_options()),
        "fixtures/example-governance.json": dump(example_governance(policy)),
        "fixtures/platform-guardrails.json": dump(example_guardrails(policy, example_governance(policy))),
        "fixtures/example-submission.json": dump({"asset": policy.asset,
            "summary": "Synthetic demonstration of a claimed unauthorized fund effect",
            "claims": [{"option_id": "withdrawal_manipulation", "evidence": {"proof": "Synthetic report section 2: displayed effect versus signed effect"}}],
            "inputs": example_inputs()}),
        "fixtures/example-claim-review.json": dump({"claims": [{"option_id": "withdrawal_manipulation", "outcome": "confirmed",
            "confirmed_option_ids": ["withdrawal_manipulation"], "reason": "Synthetic PoC conditions verified by the assigned triager"}]}),
        "fixtures/revision-policy.json": dump(document("policy", revision_policy)),
        "fixtures/revision-before.json": dump(document("report", before)),
        "fixtures/revision-after.json": dump(document("report", after)),
        "artifacts/examples.json": dump(rows),
        "artifacts/precedence-example.json": dump(demonstration()),
        "artifacts/lifecycle-example.json": dump(lifecycle_example()),
        "artifacts/importance-example.json": dump(importance_demonstration()),
        "artifacts/gist-examples.json": dump(gist_examples()),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    stale = []
    for name, content in outputs().items():
        path = ROOT / name
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                stale.append(name)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    if stale:
        raise SystemExit("Regenerate examples: " + ", ".join(stale))
    print("Synthetic examples verified." if args.check else "Synthetic examples generated.")


if __name__ == "__main__":
    main()
