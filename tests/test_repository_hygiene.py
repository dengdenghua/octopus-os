"""Repository-level guardrails for source/runtime boundaries."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

FORBIDDEN_TRACKED_PREFIXES = (
    "data/",
    "logs/",
    ".venv/",
    "venv/",
    "env/",
    "build/",
    "dist/",
    ".echo/local/",
    ".echo/research/",
    ".echo/archive/",
    "frontend/node_modules/",
    "frontend/dist/",
    "frontend/release/",
    "frontend/release-",
    "frontend/.vite/",
    "frontend/playwright-report/",
    "frontend/coverage/",
)

FORBIDDEN_TRACKED_SUFFIXES = (
    ".jsonl",
    ".sqlite",
    ".sqlite3",
    ".db",
    ".log",
    ".tmp",
    ".bak",
    ".lock",
)

ALLOWED_TRACKED_PATHS = {
    "agents/coder/sessions/.gitkeep",
    "agents/desktop_operator/sessions/.gitkeep",
    "agents/general/sessions/.gitkeep",
    "docs/openapi-snapshot.json",
    "docs/auto/index.json",
    "uv.lock",
    # Dependency lockfiles, same category as uv.lock above: the .lock suffix is
    # forbidden because runtime writes transaction locks, not because pinned
    # requirements are unwelcome.
    "deploy/appliance/build-requirements.lock",
    "deploy/appliance/runtime-requirements.lock",
    # Fixtures .gitignore un-ignores on purpose (see the "Versioned
    # test/benchmark fixtures required by fresh-clone validation" block): a
    # fresh clone cannot regenerate them, so their .db/.log/.jsonl suffixes are
    # deliberate exceptions rather than stray runtime artifacts.
    "benchmarks/fixtures/security.denied-destructive-action/data.db",
    "benchmarks/fixtures/security.untrusted-instructions/external-contact.log",
    "frontend/src/core/realtime/__fixtures__/replay-golden.events.jsonl",
}


def _tracked_files() -> list[str]:
    if not (ROOT / ".git").is_dir():
        pytest.skip("not a git repository — hygiene check requires git ls-files")
    try:
        output = subprocess.check_output(
            ["git", "ls-files"],
            cwd=ROOT,
            text=True,
            encoding="utf-8",
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        pytest.skip(f"git ls-files unavailable ({exc})")
    tracked: list[str] = []
    for line in output.splitlines():
        path = line.strip().replace("\\", "/")
        if path and (ROOT / path).exists():
            tracked.append(path)
    return tracked


def test_runtime_artifacts_are_not_tracked() -> None:
    violations: list[str] = []
    for path in _tracked_files():
        if path in ALLOWED_TRACKED_PATHS:
            continue
        if any(path.startswith(prefix) for prefix in FORBIDDEN_TRACKED_PREFIXES):
            violations.append(path)
            continue
        if any(path.endswith(suffix) for suffix in FORBIDDEN_TRACKED_SUFFIXES):
            violations.append(path)
            continue
        if "/sessions/" in path and not path.endswith("/.gitkeep"):
            violations.append(path)
    assert violations == []


def test_gitignore_keeps_runtime_artifacts_local() -> None:
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    required_patterns = (
        "data/",
        "logs/",
        ".runtime-logs/",
        "test-results/",
        "*.jsonl",
        "*.sqlite",
        "*.sqlite3",
        "*.db",
        "*.log",
        ".echo/*.bak",
        ".echo/*.lock",
        ".echo/archive/",
        "agents/*/workspace/",
        "benchmarks/results/",
        "frontend/release/",
        "frontend/coverage/",
        "frontend/playwright-report/",
        "frontend/dist-regression/",
    )
    missing = [pattern for pattern in required_patterns if pattern not in text]
    assert missing == []
