"""Regression test: project documentation must not contain verified-false claims.

This guards against the kind of overclaiming an independent analyst flagged in
2026-05: doc text confidently described features that did not exist in the
codebase. The fix in that pass was either to delete the false strings or to
re-phrase them as inspirations / flag-level behavior. This test pins those
fixes so the offending strings cannot creep back in via copy-paste from old
marketing material.

Scope: project-authored docs only.
- ``docs/`` (project documentation)
- ``protocols/`` (protocol specs)
- top-level project ``*.md`` (README, CHANGELOG, CONTRIBUTING, CODE_WIKI, etc.)

Explicitly excluded (each is a separate concern):
- ``agents/**/*.md`` (per-agent SOUL/MEMORY files — agent-owned content)
- ``runtime/execution/all_skills/*/SKILL.md`` (skill prompts — separate concern)
- ``frontend/`` and ``_external/`` and ``.venv/`` and ``node_modules/``
- ``data/``, ``logs/``, ``test-results/`` (runtime artifacts)
- This file itself (it has to mention the banned strings to enforce them).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from runtime.execution.suckers.ephemeral_agents import BUILTIN_ROLES

# Repo root is two levels up from this file: tests/test_doc_reality_alignment.py
REPO_ROOT = Path(__file__).resolve().parent.parent

# Strings whose presence in project docs we treat as verified-false claims.
# Each entry is (substring, why_it_is_false).
#
# NB: '拓扑排序' / 'topological sort' is intentionally NOT banned here —
# verification against runtime/core/ganglia/runtime.py:_topo_layers (see
# in_degree / topological-layer execution in `_run_layered`) showed that
# the DAG runtime really does perform topological-layer execution, so a
# doc that says '按拓扑排序执行 TaskGraph' is accurate, not overclaiming.
# What was overclaimed was the existence of a separate "Plan-then-Execute"
# path inside the ReAct loop; that string is the one we ban.
BANNED_PHRASES: list[tuple[str, str]] = [
    (
        "Plan-then-Execute",
        "There is no separate Plan-then-Execute path. `planning_mode` is a "
        "boolean flag on the same ReAct loop. The DAG runtime "
        "(runtime/core/ganglia/) is a different runtime entirely.",
    ),
    (
        "plan-then-execute",
        "Same as above (lower-case form).",
    ),
    (
        "DAG 执行引擎",
        "Marketing phrase. Use 'GraphRuntime' or 'DAG runtime' and point "
        "at runtime/core/ganglia/ instead.",
    ),
]


def _iter_project_md_files() -> list[Path]:
    """Yield all .md files we consider 'project docs' for this audit."""
    targets: list[Path] = []

    # docs/
    docs_dir = REPO_ROOT / "docs"
    if docs_dir.is_dir():
        for path in docs_dir.rglob("*.md"):
            targets.append(path)

    # protocols/
    protocols_dir = REPO_ROOT / "protocols"
    if protocols_dir.is_dir():
        for path in protocols_dir.rglob("*.md"):
            targets.append(path)

    # Top-level *.md files only (no recursion)
    for entry in REPO_ROOT.iterdir():
        if entry.is_file() and entry.suffix == ".md":
            targets.append(entry)

    # Exclude this test file itself (it must contain the banned strings)
    self_path = Path(__file__).resolve()
    return [p for p in targets if p.resolve() != self_path]


@pytest.mark.parametrize("banned_phrase, reason", BANNED_PHRASES)
def test_no_verified_false_claims_in_docs(banned_phrase: str, reason: str) -> None:
    """Project docs must not assert features the code does not implement."""
    offenders: list[str] = []
    for md_path in _iter_project_md_files():
        try:
            text = md_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if banned_phrase in text:
            rel = md_path.relative_to(REPO_ROOT).as_posix()
            # Capture line numbers for a useful failure message.
            for i, line in enumerate(text.splitlines(), start=1):
                if banned_phrase in line:
                    offenders.append(f"{rel}:{i}")

    assert not offenders, (
        f"Banned doc phrase {banned_phrase!r} found "
        f"({len(offenders)} occurrence(s)).\n"
        f"Why it is banned: {reason}\n"
        f"Locations:\n  " + "\n  ".join(offenders)
    )


def test_role_count_in_docs_matches_builtin_roles() -> None:
    """Any doc claiming a numeric count of builtin roles must match reality.

    The runtime exposes ``BUILTIN_ROLES``; anything else is stale text. We
    only check docs that attempt a count — docs that do not enumerate a
    count are fine.
    """
    actual_count = len(BUILTIN_ROLES)
    bad_strings = [
        # Stale numbers seen in earlier doc revisions.
        "7 个 builtin role",
        "7 个内置角色",
        "7 个 internal role",
        "7 builtin roles",
    ]
    # Also reject any specific count that is not the real count, but only
    # when it appears in a builtin-role context. We use the explicit list
    # above plus a guard for the same phrasing with the wrong number.
    for n in range(1, 30):
        if n == actual_count:
            continue
        bad_strings.append(f"{n} 个 builtin role")
        bad_strings.append(f"{n} 个内置角色")
        bad_strings.append(f"{n} builtin roles")

    offenders: list[str] = []
    for md_path in _iter_project_md_files():
        try:
            text = md_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for needle in bad_strings:
            if needle in text:
                rel = md_path.relative_to(REPO_ROOT).as_posix()
                for i, line in enumerate(text.splitlines(), start=1):
                    if needle in line:
                        offenders.append(f"{rel}:{i} -> {needle!r}")

    assert not offenders, (
        f"Doc claims a builtin-role count that does not match "
        f"len(BUILTIN_ROLES)={actual_count}.\n"
        f"Update the docs (or this test, if BUILTIN_ROLES legitimately "
        f"changed) to reflect reality.\n"
        f"Locations:\n  " + "\n  ".join(offenders)
    )


def test_audit_actually_finds_some_files() -> None:
    """Sanity check: the audit must scan something, otherwise the other
    tests can pass vacuously by mis-locating the repo root."""
    files = _iter_project_md_files()
    assert len(files) >= 5, (
        f"Expected to find at least a few project doc .md files, got "
        f"{len(files)}. REPO_ROOT={REPO_ROOT}"
    )
    # And verify the well-known anchors are reachable.
    expected_any = ["README.md", "CHANGELOG.md", "CONTRIBUTING.md"]
    rels = {p.relative_to(REPO_ROOT).as_posix() for p in files}
    assert any(name in rels for name in expected_any), (
        f"Expected to scan at least one of {expected_any} from REPO_ROOT="
        f"{REPO_ROOT}, but only found: {sorted(rels)[:10]}..."
    )
