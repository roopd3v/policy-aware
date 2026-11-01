"""Local CLI: python3 -m bemix_pilot --help."""

import argparse
import json
from pathlib import Path
import sys

from .engine import ConfigurationError, EvidenceError, evaluate, fingerprint
from .serialization import InputError, load_document
from .revisions import evaluate_revision


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local BEMIX Policy-Aware pilot; never sends a report or payment.")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("validate", "evaluate", "revise"):
        child = commands.add_parser(command)
        child.add_argument("--policy", required=True)
        child.add_argument("--output")
        if command == "evaluate":
            child.add_argument("--report", required=True)
        elif command == "revise":
            child.add_argument("--before", required=True)
            child.add_argument("--after", required=True)
            child.add_argument("--kind", choices=("improvement", "correction"), required=True)
            child.add_argument("--reason-reference", required=True)
    args = parser.parse_args(argv)
    try:
        policy = load_document(args.policy, "policy")
        if args.command == "validate":
            result = {"configuration": "structurally_valid", "policy_sha256": fingerprint(policy),
                      "experimental": policy.experimental,
                      "notice": "Structural validity does not establish calibration, approval or applicability."}
        elif args.command == "evaluate":
            result = evaluate(policy, load_document(args.report, "report"))
        else:
            result = evaluate_revision(policy, load_document(args.before, "report"),
                                       load_document(args.after, "report"), kind=args.kind,
                                       reason_reference=args.reason_reference)
        content = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            target = Path(args.output).resolve()
            inputs = [Path(args.policy).resolve()]
            if args.command == "evaluate":
                inputs.append(Path(args.report).resolve())
            elif args.command == "revise":
                inputs.extend((Path(args.before).resolve(), Path(args.after).resolve()))
            if target in inputs:
                raise InputError("Output must not overwrite the policy or report input.")
            target.write_text(content, encoding="utf-8")
        else:
            sys.stdout.write(content)
        return 3 if args.command == "revise" and result["applicable_decision"] is None else 0
    except (InputError, ConfigurationError, EvidenceError, OSError, ValueError) as error:
        sys.stderr.write(json.dumps({"status": "invalid_input", "message": str(error)}, ensure_ascii=False) + "\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
