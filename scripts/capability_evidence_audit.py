"""Behavior-grounded capability evidence audit.

The `codex_gap` / `agent_competitor_scorecard` engines score a capability as
"strong" when its declared files merely *exist* (`Path.exists()`). Existence is
not behavior: a file can be present, imported, and still have a failing or
hollow test suite. This audit closes that gap — for each capability it actually
*runs* the declared backend tests and reports the real pass/fail, then contrasts
the behavior score with the existence score the scorecard would award.

It is intentionally a standalone script (not wired into the runtime hot path):
running real test suites is slow and belongs in CI / on-demand governance, not
in a request. Frontend (`.tsx`) tests are skipped here — run them via vitest.

Usage:
    .venv/bin/python -m scripts.capability_evidence_audit            # all caps
    .venv/bin/python -m scripts.capability_evidence_audit cap_id ... # subset
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass

from runtime.safety.evolution.codex_gap import CAPABILITIES

_SUMMARY = re.compile(r"(?:(\d+) failed)?,?\s*(\d+) passed", re.IGNORECASE)


@dataclass(frozen=True)
class CapabilityEvidence:
    capability_id: str
    backend_tests: tuple[str, ...]
    collected: int
    passed: int
    failed: int
    behavior_score: float
    existence_score: float
    honest: bool


def _run_pytest(paths: tuple[str, ...]) -> tuple[int, int]:
    """Run the given test files; return (passed, failed)."""
    if not paths:
        return (0, 0)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *paths,
         "-q", "--no-header", "--tb=no", "-p", "no:cacheprovider"],
        capture_output=True,
        text=True,
    )
    tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    match = _SUMMARY.search(tail)
    if not match:
        # No "N passed" line — treat as zero collected (e.g. collection error).
        return (0, 0)
    failed = int(match.group(1) or 0)
    passed = int(match.group(2) or 0)
    return (passed, failed)


def audit(capability_ids: tuple[str, ...] = ()) -> list[CapabilityEvidence]:
    selected = [
        cap
        for cap in CAPABILITIES
        if not capability_ids or cap.id in capability_ids
    ]
    out: list[CapabilityEvidence] = []
    for cap in selected:
        backend = tuple(p for p in cap.test_paths if p.startswith("tests/"))
        passed, failed = _run_pytest(backend)
        collected = passed + failed
        behavior = round(passed / collected, 3) if collected else 0.0
        # The scorecard's existence basis: every declared path is present.
        existence = 1.0  # codex_gap awards ~1.0 once files exist (verified)
        out.append(CapabilityEvidence(
            capability_id=cap.id,
            backend_tests=backend,
            collected=collected,
            passed=passed,
            failed=failed,
            behavior_score=behavior,
            existence_score=existence,
            honest=failed == 0 and collected > 0,
        ))
    return out


def main() -> int:
    ids = tuple(sys.argv[1:])
    rows = audit(ids)
    print(f"{'capability':32} {'tests':>7} {'pass':>5} {'fail':>5} "
          f"{'behavior':>9} {'existence':>9}  honest")
    print("-" * 86)
    any_gap = False
    for r in rows:
        flag = "yes" if r.honest else "NO  <-- existence overstates"
        if not r.honest:
            any_gap = True
        print(f"{r.capability_id:32} {r.collected:>7} {r.passed:>5} "
              f"{r.failed:>5} {r.behavior_score:>9.3f} "
              f"{r.existence_score:>9.3f}  {flag}")
    print("-" * 86)
    print("behavior = tests that actually pass; existence = what the scorecard "
          "awards for files being present.")
    return 1 if any_gap else 0


if __name__ == "__main__":
    raise SystemExit(main())

