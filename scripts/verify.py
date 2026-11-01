"""Run the publication copy's tests and verify its source/example contracts."""

from datetime import datetime, timezone
from hashlib import sha256
from importlib.metadata import version
import io
import json
from pathlib import Path
import re
import sys
import unittest
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from build_examples import outputs
from public_files import selected_files, source_hashes


def check_public_tree():
    for path in selected_files():
        relative = path.relative_to(ROOT)
        if any(p in {"private", "sources", "observations", "runtime", "output", "tmp"} for p in relative.parts):
            raise ValueError("Private or unrelated material entered the publication tree.")
        value = path.read_text(encoding="utf-8")
        # No real BugEx report/snapshot URLs or literal issued bearer secrets.
        if re.search(r"https://bugex\.ai/(?:reports/|programs/[^\s]+/snapshot)", value):
            raise ValueError("A private report/snapshot reference entered the publication tree.")
        if re.search(r"bmx_[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])", value):
            raise ValueError("A literal bearer credential entered the publication tree.")


def check_links():
    count = 0
    for path in [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]:
        for target in re.findall(r"\]\(([^)]+)\)", path.read_text()):
            if urlsplit(target).scheme or target.startswith("#"):
                continue
            resolved = (path.parent / unquote(target.split("#", 1)[0])).resolve()
            if resolved == ROOT / "artifacts/verification.json":
                count += 1
                continue
            if not resolved.is_relative_to(ROOT) or not resolved.exists():
                raise ValueError(f"Broken local link in {path.name}: {target}")
            count += 1
    return count


def main():
    if sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 14):
        raise SystemExit("The supported verification runtime is CPython 3.14; use the locked environment or container.")
    dependencies = {}
    for line in (ROOT / "requirements-test.lock").read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        name, expected = line.split(" --hash=", 1)[0].split("==")
        actual = version(name)
        if actual != expected:
            raise SystemExit(f"Install the locked runtime: {name} is {actual}, expected {expected}.")
        dependencies[name] = actual
    suite = unittest.TestSuite()
    for folder in ("tests", "tests_service"):
        suite.addTests(unittest.TestLoader().discover(str(ROOT / folder)))
    log = io.StringIO()
    result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    if not result.wasSuccessful():
        sys.stderr.write(log.getvalue())
        raise SystemExit(1)
    for name, expected in outputs().items():
        path = ROOT / name
        if not path.exists() or path.read_text() != expected:
            raise SystemExit("Regenerate synthetic examples: " + name)
    check_public_tree()
    links = check_links()
    smoke_path = ROOT / "artifacts/container-smoke.json"
    smoke = json.loads(smoke_path.read_text()) if smoke_path.exists() else None
    if smoke:
        if smoke["status"] != "passed":
            raise SystemExit("Recorded container workflow did not pass.")
        for name, digest in smoke["tested_input_sha256"].items():
            if sha256((ROOT / name).read_bytes()).hexdigest() != digest:
                raise SystemExit("Rebuild and re-test the container; stale evidence for " + name)
    audit = json.loads((ROOT / "artifacts/dependency-audit.json").read_text())
    if any(p.get("vulns") for p in audit["dependencies"]):
        raise SystemExit("Dependency audit has unresolved findings.")
    review = json.loads((ROOT / "artifacts/dependency-review.json").read_text())
    if review["requirements_lock_sha256"] != sha256((ROOT / "requirements-service.lock").read_bytes()).hexdigest():
        raise SystemExit("Dependency audit refers to a different service lock.")
    for dependency in audit["dependencies"]:
        if dependencies.get(dependency["name"].replace("_", "-")) != dependency["version"]:
            raise SystemExit("Dependency audit and installed versions differ.")
    record = {"status": "passed", "version": "1.3.0", "at_utc": datetime.now(timezone.utc).isoformat(),
              "python": sys.version.split()[0], "tests_run": result.testsRun, "failures": len(result.failures),
              "errors": len(result.errors), "skipped": len(result.skipped), "dependencies": dependencies,
              "synthetic_data_only": True, "live_platform_integration": False, "official_program_calibration": False,
              "public_tree_check": "passed", "local_links_checked": links,
              "container_workflow": "recorded_pass" if smoke else "not_run",
              "source_sha256": source_hashes()}
    (ROOT / "artifacts/verification.json").write_text(json.dumps(record, indent=2) + "\n")
    (ROOT / "artifacts/test-results.txt").write_text(log.getvalue())
    print(f"PASS: {result.testsRun} tests, synthetic examples, publication checks, {links} local links; container={record['container_workflow']}.")


if __name__ == "__main__":
    main()
