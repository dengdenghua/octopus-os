"""C4 regression: journal append + rotation share one stable cross-process lock.

Rotation rewrites the journal via ``tmp.replace`` (a rename that swaps the
inode). The cross-process lock is now taken on a stable ``<path>.lock`` sidecar
and held across both append and rotation, so a rename can't clobber a
concurrent writer. These tests guard the in-process invariants the change must
preserve: the sidecar is created, concurrent writes are not lost, and rotation
never leaves a torn (non-JSON) line behind.
"""

from __future__ import annotations

import json
import threading
from uuid import uuid4

from runtime.memory.journal import FileOpEvent, JSONLJournal


def _event(i: int) -> FileOpEvent:
    return FileOpEvent(
        task_id=uuid4(),
        arm_id="arm-1",
        path=f"f{i}.txt",
        action="write",
        bytes_delta=i,
        diff="x" * 20,
    )


def test_lock_sidecar_is_created(tmp_path) -> None:
    j = JSONLJournal(tmp_path / "j.jsonl")
    j.write(_event(0))
    assert (tmp_path / "j.jsonl.lock").exists()


def test_concurrent_writes_are_not_lost(tmp_path) -> None:
    j = JSONLJournal(tmp_path / "j.jsonl")  # rotation disabled

    def worker() -> None:
        for i in range(50):
            j.write(_event(i))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    lines = (tmp_path / "j.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 8 * 50
    for line in lines:
        json.loads(line)  # every record is complete and valid


def test_rotation_under_concurrent_writes_stays_valid(tmp_path) -> None:
    j = JSONLJournal(tmp_path / "j.jsonl", max_size_bytes=4000, keep_ratio=0.5)

    def worker() -> None:
        for i in range(80):
            j.write(_event(i))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    raw = (tmp_path / "j.jsonl").read_text(encoding="utf-8")
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    assert lines  # file is not empty
    for line in lines:
        json.loads(line)  # rotation racing appends never tore a line
    # rotation actually kept the file bounded (well under the unbounded total)
    assert len(raw.encode("utf-8")) <= 4000 * 2

