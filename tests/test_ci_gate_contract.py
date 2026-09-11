"""Lock the CI gate contract.

Three gates were absent for months while the team kept committing:

* ``ci.yml`` only triggered on ``os-main``, but work happened on
  ``p3-provision``.  ``git rev-list --left-right --count os-main...p3-provision``
  returned ``0 / 199``: 199 commits, 87 days, zero CI runs.
* ``lint-and-test`` carried ``if: github.event_name != 'pull_request'``, so the
  one job that ran pytest skipped every pull request.
* ``tools/lint/untracked_source_check.py`` — the guard written after the
  2026-08-28 audit found 26 never-``git add``-ed runtime modules — was never
  referenced by any workflow.  It recurred on 2026-09-09 with 49 offenders,
  including ``runtime/adapters/integrations/local_auth/passwords.py``, which
  ``appliance/agent_api/contract.py`` probes at startup (CI lost auth).

Each assertion below maps to one of those regressions. Deleting a gate should
fail a test, not fail silently in production.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CI_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"

yaml = pytest.importorskip("yaml", reason="PyYAML is required to inspect CI gates")


def _ci_doc() -> dict:
    return yaml.safe_load(CI_PATH.read_text(encoding="utf-8"))


def _triggers() -> dict:
    doc = _ci_doc()
    # PyYAML parses the bare `on:` key as boolean True (YAML 1.1).
    return doc[True] if True in doc else doc["on"]


def test_working_branch_is_ci_triggered() -> None:
    """``p3-provision`` must be a CI trigger.

    Without this the actively developed branch is never validated.
    """
    triggers = _triggers()
    for event in ("push", "pull_request"):
        branches = triggers[event]["branches"]
        assert "p3-provision" in branches, (
            f"ci.yml {event}.branches = {branches}; p3-provision is missing, so "
            "commits on the working branch never reach CI"
        )


def test_python_tests_run_on_pull_requests() -> None:
    """``lint-and-test`` must not opt out of pull requests."""
    job = _ci_doc()["jobs"]["lint-and-test"]
    condition = (job.get("if") or "").replace('"', "'")
    assert "event_name != 'pull_request'" not in condition, (
        f"lint-and-test still excludes pull requests (if: {condition!r}); "
        "Python tests then only run on direct pushes"
    )


def test_untracked_source_guard_is_wired() -> None:
    """The 2026-08-28 P0-1 prevention guard must stay installed."""
    jobs = _ci_doc()["jobs"]
    assert "untracked-source-guard" in jobs, (
        "ci.yml has no untracked-source-guard job; untracked source files will "
        "again pass locally and vanish on every fresh clone"
    )
    script = " ".join(str(step.get("run", "")) for step in jobs["untracked-source-guard"]["steps"])
    assert "untracked_source_check" in script, (
        "untracked-source-guard does not invoke tools/lint/untracked_source_check"
    )


def test_full_suite_job_is_wired() -> None:
    """The 86% of tests outside ``testpaths`` must be collected somewhere.

    ``pyproject.toml`` limits the default collection to ``tests/appliance``
    (2,404 of 17,050).  Until that can be widened safely, ``full-test-suite``
    is the only job that executes the rest — deleting it silently returns the
    project to validating 14% of its suite.
    """
    jobs = _ci_doc()["jobs"]
    assert "full-test-suite" in jobs, (
        "ci.yml has no full-test-suite job; 14,646 tests (86%) stop running "
        "again, since testpaths only covers tests/appliance"
    )
    script = " ".join(str(step.get("run", "")) for step in jobs["full-test-suite"]["steps"])
    assert "pytest tests/" in script, (
        "full-test-suite does not run the whole suite; it must invoke "
        "`pytest tests/` (not the testpaths default)"
    )
    assert jobs["full-test-suite"].get("timeout-minutes"), (
        "full-test-suite needs an explicit timeout-minutes; the suite takes "
        "~50 min and would otherwise hang on a stalled runner"
    )


# `monkeypatch.setattr(<module>.threading.X, ...)` mutates the *stdlib* class,
# because `mod.threading` is just the global threading module.  Patching
# `threading.Thread.start` broke pytest-timeout's Timer and aborted a full run
# with INTERNALERROR after 46 minutes — silently skipping thousands of tests
# that were never executed.  Patch the module-local name instead.
_GLOBAL_THREADING_PATCH = re.compile(r"monkeypatch\.setattr\(\s*[\w.]*\bthreading\b\s*\.")


def test_tests_do_not_monkeypatch_global_threading() -> None:
    """Test helpers must not mutate process-wide threading classes."""
    offenders: list[str] = []
    for path in sorted((REPO_ROOT / "tests").rglob("*.py")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):  # pragma: no cover - unreadable file
            continue
        for lineno, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):  # prose describing the pattern is fine
                continue
            if _GLOBAL_THREADING_PATCH.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {stripped}")

    assert not offenders, (
        "tests patch global threading objects; this can break pytest-timeout "
        "and abort the whole session. Rebind the name inside the module under "
        "test instead (e.g. monkeypatch.setattr(deploy, 'threading', ...)).\n  "
        + "\n  ".join(offenders)
    )


def test_no_untracked_source_files() -> None:
    """Same invariant the CI job enforces, checked from the test suite too.

    Fails loudly on a developer machine instead of on a clean CI checkout,
    where the symptom is an import error rather than a clear message.
    """
    from tools.lint.untracked_source_check import main

    assert main([]) == 0, (
        "untracked source files exist under a source root; run "
        "`python tools/lint/untracked_source_check.py` for the list and "
        "`git add` them (or move them out of the source roots)"
    )


def test_untracked_source_guard_covers_the_installable_appliance_package() -> None:
    """The primary NAS package must not fall outside the local commit guard."""
    from tools.lint.untracked_source_check import SOURCE_ROOTS

    assert "appliance/" in SOURCE_ROOTS


def test_untracked_source_guard_rejects_an_omitted_appliance_module(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from tools.lint import untracked_source_check

    monkeypatch.setattr(
        untracked_source_check,
        "_untracked_files",
        lambda _root: ["notes/local.md", "appliance/native_future.py"],
    )

    assert untracked_source_check.main([]) == 1
    output = capsys.readouterr()
    assert "appliance/native_future.py" in output.err
    assert "notes/local.md" not in output.err
