"""
CLI smoke test for ``bugfix-demo-v2``.

What this guards:
    1. ``python -m runtime bugfix-demo-v2`` exits 0.
    2. SOUL.md is restored byte-identical after the demo runs
       (the demo backs up + restores · regressions here would pollute
       the coder agent's actual scaffold on every dev smoke).
    3. `.soul_history/` gains at least one new snapshot from the
       Round 1 ``update_soul`` call · proving the evolution path
       actually ran (not just silently failed).

Kept separate from ``test_cli_smoke.py`` (which only tests ``--help``)
because this one actually executes the demo · ~10-15s wall. Skipped
on CI without git.
"""

from __future__ import annotations

import contextlib
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SOUL_PATH = Path("agents/coder/agent-core/SOUL.md")
HIST_DIR = Path("agents/coder/agent-core/.soul_history")
ROOT = Path(__file__).parent.parent


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


@pytest.mark.skipif(
    shutil.which("git") is None,
    reason="demo requires git on PATH",
)
@pytest.mark.skipif(
    not SOUL_PATH.exists(),
    reason="coder agent SOUL.md missing (fresh clone without agent seed)",
)
def test_bugfix_demo_v2_runs_and_restores_soul():
    """Run the demo end-to-end · assert SOUL bytes unchanged · assert
    snapshot was created during the run even though it's been cleaned up
    in the restore (we detect via `.soul_history/` count delta BEFORE
    the restore completes · but since the demo restores SOUL and we
    check AFTER, we look for the snapshot that the demo itself leaves
    untouched in the history dir).
    """
    # ── Baseline snapshot ─────────────────────────────────
    soul_before = SOUL_PATH.read_bytes()
    soul_hash_before = _md5(soul_before.decode("utf-8", errors="replace"))

    hist_files_before = set(p.name for p in HIST_DIR.iterdir()) if HIST_DIR.exists() else set()

    # ── Run the demo · capture output ─────────────────────
    r = subprocess.run(
        [sys.executable, "-m", "runtime", "bugfix-demo-v2", "--no-color"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(ROOT),
    )

    assert r.returncode == 0, (
        f"bugfix-demo-v2 failed:\nstdout={r.stdout[-2000:]}\nstderr={r.stderr[-2000:]}"
    )

    # ── SOUL.md must be byte-identical after the demo ─────
    soul_after = SOUL_PATH.read_bytes()
    assert soul_after == soul_before, (
        f"SOUL.md changed after demo · should be restored byte-identical.\n"
        f"  before hash: {soul_hash_before}\n"
        f"  after hash:  {_md5(soul_after.decode('utf-8', errors='replace'))}\n"
        f"  len before: {len(soul_before)}, after: {len(soul_after)}"
    )

    # ── .soul_history/ must have gained a snapshot ────────
    # The demo creates an auto-snapshot via `_update_soul` BEFORE
    # appending the lesson. That snapshot is NOT cleaned up by the
    # demo's restore (it only restores SOUL.md, not the history dir).
    # Its presence proves the evolution path was actually exercised.
    hist_files_after = set(p.name for p in HIST_DIR.iterdir()) if HIST_DIR.exists() else set()
    new_snaps = hist_files_after - hist_files_before
    assert len(new_snaps) >= 1, (
        f"No new snapshot in .soul_history/ · evolution path may have "
        f"silently failed. stdout tail: {r.stdout[-500:]}"
    )

    # ── Output should name the key milestones ─────────────
    # These are printed by the demo itself · deterministic text.
    out = r.stdout + r.stderr
    for milestone in [
        "Round 1",
        "Round 2",
        "update_soul",
        "self-evolution closed",
    ]:
        assert milestone in out, (
            f"demo output missing milestone {milestone!r}. Got stdout[-1500:]: {r.stdout[-1500:]}"
        )

    # ── Cleanup the snapshot files we left behind ─────────
    # (we deliberately verified their existence above · now remove
    # so repeated test runs don't accumulate them).
    for name in new_snaps:
        # best-effort · harmless if someone else cleaned it
        with contextlib.suppress(OSError):
            (HIST_DIR / name).unlink()
