"""Bounded file selection for the publication source tree."""

from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUFFIXES = {".py", ".md", ".json", ".txt", ".toml", ".yaml", ".yml", ".lock", ".example"}
ROOT_FILES = {"README.md", "LICENSE", ".gitignore", ".dockerignore", "pyproject.toml",
              "requirements-service.txt", "requirements-service.lock", "requirements-test.txt", "requirements-test.lock"}
DIRECTORIES = {"bemix_pilot", "bemix_service", "tests", "tests_service", "scripts", "docs", "fixtures", "artifacts", "deploy", ".github"}
GENERATED = {"artifacts/verification.json", "artifacts/test-results.txt"}


def selected_files():
    files = []
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if not path.is_file() or any(p in {".git", ".venv", "__pycache__", ".ruff_cache"} for p in relative.parts):
            continue
        if len(relative.parts) == 1 and path.name in ROOT_FILES or relative.parts[0] in DIRECTORIES and (path.suffix in SUFFIXES or path.name == "Dockerfile"):
            if not path.resolve().is_relative_to(ROOT):
                raise ValueError("A publication file resolves outside the source tree.")
            files.append(path)
    return sorted(files)


def source_hashes():
    return {p.relative_to(ROOT).as_posix(): sha256(p.read_bytes()).hexdigest()
            for p in selected_files() if p.relative_to(ROOT).as_posix() not in GENERATED}
