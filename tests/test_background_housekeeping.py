"""Regression tests for background-task housekeeping (TTL + cap).

Both cleanup paths — the in-memory registry prune and the on-disk task-dir
sweep — landed without tests, and both shipped the same defect: overflow for
the ``max_keep`` cap was measured against *every* entry, while only *terminal*
entries are actually evictable. A session holding many running tasks (or a
directory holding many unparsable ones) therefore computed a huge overflow and
destroyed terminal data that was seconds old, despite TTLs of 30 minutes and
7 days — and the registry still stayed over its cap, because running entries
can never be evicted.

The invariant these tests pin: the cap may only ever remove the *oldest
surplus of what it governs*, and never anything inside its TTL unless that
governed set itself exceeds the cap.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from runtime.execution.suckers import _write_skills_background as bg_mod


class _FakeProc:
    def __init__(self, running: bool) -> None:
        self._running = running

    def poll(self) -> int | None:
        return None if self._running else 0


class _FakeBackgroundProcess:
    """Minimal stand-in exposing only what housekeeping reads."""

    def __init__(self, ended_at: float | None, *, running: bool = False) -> None:
        self.ended_at = ended_at
        self._lock = threading.Lock()
        self.proc = _FakeProc(running)


@pytest.fixture(autouse=True)
def _clean_registry():
    bg_mod._BACKGROUND_PROCESSES.clear()
    yield
    bg_mod._BACKGROUND_PROCESSES.clear()


def _seed_registry(*, running: int = 0, fresh: int = 0, expired: int = 0) -> None:
    now = time.time()
    for i in range(running):
        bg_mod._BACKGROUND_PROCESSES[f"run{i}"] = _FakeBackgroundProcess(None, running=True)
    for i in range(fresh):
        bg_mod._BACKGROUND_PROCESSES[f"fresh{i}"] = _FakeBackgroundProcess(now - 5)
    for i in range(expired):
        bg_mod._BACKGROUND_PROCESSES[f"old{i}"] = _FakeBackgroundProcess(
            now - bg_mod._BACKGROUND_FINISHED_TTL_S - 60
        )


def _ids(prefix: str, count: int) -> int:
    return sum(f"{prefix}{i}" in bg_mod._BACKGROUND_PROCESSES for i in range(count))


class TestRegistryPrune:
    def test_expired_entries_are_pruned_and_fresh_ones_kept(self) -> None:
        _seed_registry(fresh=2, expired=3)

        pruned = bg_mod._prune_finished_background_processes()

        assert pruned == 3
        assert _ids("old", 3) == 0
        assert _ids("fresh", 2) == 2

    def test_running_entries_never_evict_fresh_finished_ones(self) -> None:
        """The cap governs finished entries only.

        Sizing overflow by the whole registry made 200 running tasks evict
        4 finished entries that were 5 seconds old (TTL: 30 min) — and left
        the registry over its cap regardless, since running entries stay.
        """
        _seed_registry(running=200, fresh=4)

        bg_mod._prune_finished_background_processes()

        assert _ids("fresh", 4) == 4, "fresh finished entries must stay queryable"
        assert _ids("run", 200) == 200, "running entries are never evictable"

    def test_cap_drops_oldest_surplus_of_finished_entries(self) -> None:
        now = time.time()
        for i in range(200):
            # Ascending ended_at: fin0 is oldest, fin199 newest.
            bg_mod._BACKGROUND_PROCESSES[f"fin{i}"] = _FakeBackgroundProcess(now - (200 - i))

        bg_mod._prune_finished_background_processes(max_keep=128)

        assert len(bg_mod._BACKGROUND_PROCESSES) == 128
        # The oldest 72 go; the newest 128 stay.
        assert _ids("fin", 72) == 0
        assert all(f"fin{i}" in bg_mod._BACKGROUND_PROCESSES for i in range(72, 200))

    def test_prune_is_a_noop_on_an_empty_registry(self) -> None:
        assert bg_mod._prune_finished_background_processes() == 0


def _task_dir(root: Path, name: str, metadata: dict[str, Any], *, age_s: float = 0.0) -> Path:
    task_dir = root / name
    task_dir.mkdir()
    meta_path = task_dir / "metadata.json"
    meta_path.write_text(json.dumps(metadata), encoding="utf-8")
    if age_s:
        stamp = time.time() - age_s
        os.utime(meta_path, (stamp, stamp))
        os.utime(task_dir, (stamp, stamp))
    return task_dir


class TestDirSweep:
    """``_sweep_background_dirs`` calls ``shutil.rmtree`` — every assertion here
    is about not deleting data the operator can still legitimately read."""

    def test_expired_terminal_dirs_are_swept_and_fresh_ones_kept(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(bg_mod, "_background_root", lambda: tmp_path)
        fresh = _task_dir(tmp_path, "aaaa0001", {"exit_code": 0})
        expired = _task_dir(
            tmp_path, "aaaa0002", {"exit_code": 0}, age_s=bg_mod._BACKGROUND_DIR_TTL_S + 3600
        )

        removed = bg_mod._sweep_background_dirs()

        assert removed == 1
        assert not expired.exists()
        assert fresh.exists()

    def test_live_dirs_do_not_drive_deletion_of_fresh_terminal_dirs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """300 live dirs used to inflate overflow past the cap and delete
        terminal dirs that had finished seconds ago, despite a 7-day TTL."""
        monkeypatch.setattr(bg_mod, "_background_root", lambda: tmp_path)
        for i in range(300):
            _task_dir(tmp_path, f"{i:08x}", {"exit_code": None})
        fresh = [_task_dir(tmp_path, f"{0xAAAA0000 + i:08x}", {"exit_code": 0}) for i in range(5)]

        removed = bg_mod._sweep_background_dirs()

        assert removed == 0
        assert all(d.exists() for d in fresh)

    def test_unparsable_dirs_do_not_drive_deletion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Dirs skipped for a non-task name were still counted in the overflow
        basis, so they deleted valid fresh results too."""
        monkeypatch.setattr(bg_mod, "_background_root", lambda: tmp_path)
        for i in range(260):
            (tmp_path / f"not-a-task-id-{i}").mkdir()
        fresh = [_task_dir(tmp_path, f"{0xBBBB0000 + i:08x}", {"exit_code": 0}) for i in range(3)]

        removed = bg_mod._sweep_background_dirs()

        assert removed == 0
        assert all(d.exists() for d in fresh)

    def test_cap_drops_only_the_oldest_surplus(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(bg_mod, "_background_root", lambda: tmp_path)
        # Ascending age: dir 0 is newest, dir 19 oldest.
        dirs = [
            _task_dir(tmp_path, f"{0xCCCC0000 + i:08x}", {"exit_code": 0}, age_s=float(i * 60))
            for i in range(20)
        ]

        removed = bg_mod._sweep_background_dirs(max_keep=15)

        assert removed == 5
        assert sum(d.exists() for d in dirs) == 15
        # The 5 oldest are the ones gone.
        assert not any(d.exists() for d in dirs[-5:])

    def test_running_dirs_are_never_swept_even_past_the_ttl(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(bg_mod, "_background_root", lambda: tmp_path)
        live = _task_dir(
            tmp_path,
            "dddd0001",
            {"exit_code": None},
            age_s=bg_mod._BACKGROUND_DIR_TTL_S * 4,
        )

        assert bg_mod._sweep_background_dirs() == 0
        assert live.exists()

    def test_cancelled_and_recovery_states_count_as_terminal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(bg_mod, "_background_root", lambda: tmp_path)
        old = bg_mod._BACKGROUND_DIR_TTL_S + 3600
        cancelled = _task_dir(tmp_path, "eeee0001", {"cancelled": True}, age_s=old)
        recovered = _task_dir(
            tmp_path, "eeee0002", {"recovery_state": "orphaned_process_exited"}, age_s=old
        )

        assert bg_mod._sweep_background_dirs() == 2
        assert not cancelled.exists()
        assert not recovered.exists()

    def test_missing_root_is_tolerated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(bg_mod, "_background_root", lambda: tmp_path / "nope")
        assert bg_mod._sweep_background_dirs() == 0

