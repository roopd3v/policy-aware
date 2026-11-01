"""Local operator commands. Tokens are printed only once when explicitly issued."""

import argparse
import json
import os
from pathlib import Path
import secrets

from .api import read_key
from .service import DecisionService
from .storage import Store
from .guardrails import load_template


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--key-file", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    program = commands.add_parser("program"); program.add_argument("name")
    limits = commands.add_parser("guardrails")
    limits.add_argument("program"); limits.add_argument("template"); limits.add_argument("--reason", required=True)
    approval = commands.add_parser("approve-policy")
    approval.add_argument("program"); approval.add_argument("policy_id")
    approval.add_argument("--expected-record-hash", required=True)
    approval.add_argument("--reviewer", required=True); approval.add_argument("--reason", required=True)
    feedback = commands.add_parser("return-policy")
    feedback.add_argument("program"); feedback.add_argument("policy_id")
    feedback.add_argument("--expected-record-hash", required=True)
    feedback.add_argument("--reviewer", required=True); feedback.add_argument("--reason", required=True)
    subject = commands.add_parser("principal"); subject.add_argument("name")
    grant = commands.add_parser("grant"); grant.add_argument("subject"); grant.add_argument("program"); grant.add_argument("role")
    remove = commands.add_parser("revoke-membership"); remove.add_argument("subject"); remove.add_argument("program"); remove.add_argument("role")
    disable = commands.add_parser("disable-principal"); disable.add_argument("subject")
    token = commands.add_parser("token"); token.add_argument("subject"); token.add_argument("--days", type=int, default=30)
    revoke = commands.add_parser("revoke-token"); revoke.add_argument("token_id")
    verify = commands.add_parser("verify"); verify.add_argument("--checkpoint")
    checkpoint = commands.add_parser("checkpoint"); checkpoint.add_argument("output")
    backup = commands.add_parser("backup"); backup.add_argument("output")
    args = parser.parse_args()
    if args.command == "init":
        key_path = Path(args.key_file)
        if not key_path.exists():
            key_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(key_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(secrets.token_bytes(32))
        store = Store.initialize(args.db, read_key(key_path))
        print(json.dumps({"initialized": True, "store_id": store.store_id}))
        return
    store = Store(args.db, read_key(args.key_file))
    service = DecisionService(store)
    if args.command == "program":
        service.add_program(args.name); result = {"program": args.name}
    elif args.command == "guardrails":
        result = service.install_guardrails(args.program, load_template(args.template), args.reason)
    elif args.command == "approve-policy":
        result = service.approve_policy(args.program, args.policy_id, args.expected_record_hash, args.reviewer, args.reason)
    elif args.command == "return-policy":
        result = service.request_policy_changes(args.program, args.policy_id, args.expected_record_hash, args.reviewer, args.reason)
    elif args.command == "principal":
        result = {"subject": service.add_principal(args.name)}
    elif args.command == "grant":
        service.grant(args.subject, args.program, args.role); result = {"granted": True}
    elif args.command == "revoke-membership":
        service.revoke_membership(args.subject, args.program, args.role); result = {"membership_revoked": True}
    elif args.command == "disable-principal":
        service.disable_principal(args.subject); result = {"principal_disabled": True}
    elif args.command == "token":
        result = service.issue_token(args.subject, args.days)
    elif args.command == "revoke-token":
        service.revoke_token(args.token_id); result = {"revoked": True}
    elif args.command == "verify":
        checkpoint = json.loads(Path(args.checkpoint).read_text()) if args.checkpoint else None
        result = store.verify(checkpoint)
    elif args.command == "checkpoint":
        result = store.verify()
        fd = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(result, handle, indent=2)
        result = {"checkpoint_written": args.output, "seq": result["seq"]}
    elif args.command == "backup":
        checkpoint = store.backup(args.output)
        checkpoint_path = str(args.output) + ".checkpoint.json"
        fd = os.open(checkpoint_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(checkpoint, handle, indent=2)
        result = {"backup_verified": args.output, "checkpoint": checkpoint_path}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
