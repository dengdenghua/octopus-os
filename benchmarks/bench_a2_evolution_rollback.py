"""Benchmark: evolution rollback speed and completeness.

Replaces the deprecated test_a2_auto_rollback.py.
Quantifies rollback latency and query performance for canary, ledger,
and coordinator subsystems.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.safety.evolution.canary import CanaryConfig, CanaryManager  # noqa: E402
from runtime.safety.evolution.proposal_ledger import ProposalLedger, ProposalStatus  # noqa: E402
from runtime.safety.evolution.rollback_coordinator import RollbackCoordinator  # noqa: E402


class TestBenchCanaryRollback:
    """Canary rollback latency benchmarks."""

    def test_canary_rollback_latency(self, tmp_path: Path) -> None:
        config = CanaryConfig(state_dir=str(tmp_path / "canary"))
        mgr = CanaryManager(config=config)
        mgr.register("skill_a")

        start = time.monotonic()
        result = mgr.force_rollback("skill_a")
        elapsed = time.monotonic() - start

        print(f"canary force_rollback latency: {elapsed * 1000:.2f} ms")
        assert result is not None
        assert elapsed < 0.5

    def test_canary_auto_rollback_latency(self, tmp_path: Path) -> None:
        config = CanaryConfig(state_dir=str(tmp_path / "canary"), rollback_threshold=0.50)
        mgr = CanaryManager(config=config)
        mgr.register("skill_b")

        for _ in range(4):
            mgr.record_outcome("skill_b", success=False)

        start = time.monotonic()
        state = mgr.record_outcome("skill_b", success=False)
        elapsed = time.monotonic() - start

        print(f"canary auto-rollback latency: {elapsed * 1000:.2f} ms")
        assert state is not None
        assert state.phase.value == "rolled_back"
        assert elapsed < 0.1

    def test_canary_registration_latency(self, tmp_path: Path) -> None:
        config = CanaryConfig(state_dir=str(tmp_path / "canary"))
        mgr = CanaryManager(config=config)

        start = time.monotonic()
        for i in range(100):
            mgr.register(f"bulk_skill_{i}")
        elapsed = time.monotonic() - start

        print(f"canary register 100 skills: {elapsed:.3f} s")
        assert elapsed < 2.0


class TestBenchLedgerRollback:
    """Proposal ledger rollback and query benchmarks."""

    def test_ledger_rollback_latency(self, tmp_path: Path) -> None:
        ledger = ProposalLedger(path=str(tmp_path / "ledger.jsonl"))
        record = ledger.propose(
            kind="test",
            description="bench rollback",
            proposer="bench",
        )
        ledger.accept(record.proposal_id)
        ledger.mark_applied(record.proposal_id)

        start = time.monotonic()
        result = ledger.mark_rolled_back(record.proposal_id)
        elapsed = time.monotonic() - start

        print(f"ledger mark_rolled_back latency: {elapsed * 1000:.2f} ms")
        assert result is not None
        assert result.status == ProposalStatus.ROLLED_BACK
        assert elapsed < 0.2

    def test_ledger_query_with_1000_records(self, tmp_path: Path) -> None:
        ledger = ProposalLedger(path=str(tmp_path / "ledger_large.jsonl"))
        for i in range(1000):
            ledger.propose(
                kind="bulk" if i % 2 == 0 else "test",
                description=f"proposal {i}",
                proposer="bench",
            )

        start = time.monotonic()
        results = ledger.query(kind="bulk", limit=50)
        elapsed = time.monotonic() - start

        print(f"ledger query over 1000 records: {elapsed * 1000:.2f} ms")
        assert len(results) > 0
        assert elapsed < 0.5


class TestBenchRollbackCoordinator:
    """Rollback coordinator integration benchmarks."""

    def test_coordinator_canary_rollback(self, tmp_path: Path) -> None:
        state_dir = str(tmp_path / "coord")
        ledger_path = str(tmp_path / "coord" / "ledger.jsonl")
        coord = RollbackCoordinator(
            canary_config=CanaryConfig(state_dir=str(tmp_path / "coord" / "canary")),
            ledger_path=ledger_path,
            state_dir=state_dir,
        )
        coord._canary.register("coord_skill")

        start = time.monotonic()
        result = coord.execute_rollback("canary:coord_skill", reason="bench")
        elapsed = time.monotonic() - start

        print(f"coordinator canary rollback: {elapsed * 1000:.2f} ms")
        assert result.success
        assert elapsed < 1.0

    def test_coordinator_verify_rollback(self, tmp_path: Path) -> None:
        state_dir = str(tmp_path / "coord_verify")
        ledger_path = str(tmp_path / "coord_verify" / "ledger.jsonl")
        coord = RollbackCoordinator(
            canary_config=CanaryConfig(state_dir=str(tmp_path / "coord_verify" / "canary")),
            ledger_path=ledger_path,
            state_dir=state_dir,
        )
        coord._canary.register("verify_skill")
        rr = coord.execute_rollback("canary:verify_skill", reason="bench verify")

        start = time.monotonic()
        verification = coord.verify_rollback(rr.rollback_id)
        elapsed = time.monotonic() - start

        print(f"coordinator verify_rollback: {elapsed * 1000:.2f} ms")
        assert verification.complete
        assert elapsed < 0.2

    def test_coordinator_history_query(self, tmp_path: Path) -> None:
        state_dir = str(tmp_path / "coord_hist")
        ledger_path = str(tmp_path / "coord_hist" / "ledger.jsonl")
        coord = RollbackCoordinator(
            canary_config=CanaryConfig(state_dir=str(tmp_path / "coord_hist" / "canary")),
            ledger_path=ledger_path,
            state_dir=state_dir,
        )
        for i in range(10):
            skill_name = f"hist_skill_{i}"
            coord._canary.register(skill_name)
            coord.execute_rollback(f"canary:{skill_name}", reason=f"bench history {i}")

        start = time.monotonic()
        history = coord.rollback_history(limit=50)
        elapsed = time.monotonic() - start

        print(f"coordinator rollback_history (10 rollbacks): {elapsed * 1000:.2f} ms")
        assert len(history) == 10
        assert elapsed < 0.2

