from __future__ import annotations

import json
import multiprocessing
from pathlib import Path
from typing import Any

import pytest

from runtime.safety.evolution import candidate_registry as candidate_registry_module
from runtime.safety.evolution.candidate_registry import (
    CandidateRegistry,
    CandidateRegistryError,
    GeneType,
    candidate_id_for,
)


def _concurrent_propose_worker(
    path: str,
    barrier: Any,
    results: Any,
) -> None:
    try:
        barrier.wait(timeout=10)
        candidate = CandidateRegistry(path).propose(
            gene_type=GeneType.SKILL,
            scope="skill.concurrent",
            patch={"skill": "concurrent"},
            proposer="worker",
        )
        results.put(("ok", candidate.candidate_id))
    except Exception as exc:  # pragma: no cover - asserted in parent process
        results.put(("error", f"{type(exc).__name__}: {exc}"))


def test_candidate_registry_fails_closed_on_truncated_lineage(tmp_path: Path) -> None:
    path = tmp_path / "candidates.jsonl"
    path.write_bytes(b'{"candidate_id":"partial"')

    with pytest.raises(CandidateRegistryError, match="truncated"):
        CandidateRegistry(path).list()


def test_candidate_registry_does_not_acknowledge_failed_fsync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "candidates.jsonl"
    registry = CandidateRegistry(path)
    real_fsync = candidate_registry_module.os.fsync
    failed = False

    def _fail_first_fsync(fd: int) -> None:
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("simulated candidate fsync failure")
        real_fsync(fd)

    monkeypatch.setattr(candidate_registry_module.os, "fsync", _fail_first_fsync)
    with pytest.raises(CandidateRegistryError, match="not durable"):
        registry.propose(
            gene_type=GeneType.SKILL,
            scope="skill.fsync",
            patch={"skill": "fsync"},
            proposer="test",
        )

    # The append may be visible in the page cache. A subsequent strict read
    # repairs durability before acknowledging that state.
    monkeypatch.setattr(candidate_registry_module.os, "fsync", real_fsync)
    expected_id = candidate_id_for(
        gene_type=GeneType.SKILL,
        scope="skill.fsync",
        patch={"skill": "fsync"},
    )
    assert registry.get(expected_id) is not None


def test_candidate_registry_cross_process_propose_is_one_line(tmp_path: Path) -> None:
    path = tmp_path / "candidates.jsonl"
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    results = context.Queue()
    workers = [
        context.Process(
            target=_concurrent_propose_worker,
            args=(str(path), barrier, results),
        )
        for _ in range(2)
    ]

    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=20)
        assert worker.exitcode == 0

    outcomes = [results.get(timeout=5) for _ in workers]
    assert {status for status, _value in outcomes} == {"ok"}
    assert len({value for _status, value in outcomes}) == 1
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert CandidateRegistry(path).list()[0].candidate_id == outcomes[0][1]

