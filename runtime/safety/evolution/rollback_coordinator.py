"""Unified rollback coordinator for canary, ledger, and fitness subsystems."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from runtime.safety.evolution.canary import CanaryConfig, CanaryManager
from runtime.safety.evolution.proposal_ledger import ProposalLedger, ProposalStatus

_LOG = logging.getLogger("echo.evolution.rollback_coordinator")

_FILE_LOCK = threading.Lock()


@dataclass
class RollbackResult:
    rollback_id: str
    target: str
    strategy: str
    reason: str
    success: bool
    fitness_before: float | None
    fitness_after: float | None
    ts: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class RollbackVerification:
    rollback_id: str
    complete: bool
    fitness_recovered: bool
    message: str


@dataclass
class RollbackRecord:
    rollback_id: str
    target: str
    reason: str
    success: bool
    ts: str


class RollbackCoordinator:
    def __init__(
        self,
        *,
        canary_config: CanaryConfig | None = None,
        ledger_path: str | None = None,
        state_dir: str | None = None,
    ) -> None:
        self._canary_config = canary_config or CanaryConfig()
        self._canary = CanaryManager(config=self._canary_config)
        self._ledger = ProposalLedger(path=ledger_path or "data/proposal_ledger.jsonl")
        self._state_dir = Path(state_dir or "data/rollback_states")
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._rollback_log = self._state_dir / "rollback_log.jsonl"

    def execute_rollback(
        self,
        target: str,
        reason: str,
        strategy: str = "full",
    ) -> RollbackResult:
        ts = datetime.now().isoformat(timespec="seconds")
        rollback_id = hashlib.sha256(f"{target}:{reason}:{time.time_ns()}".encode()).hexdigest()[
            :12
        ]

        fitness_before: float | None = None
        fitness_after: float | None = None
        success = False
        details: dict[str, Any] = {}

        if target.startswith("canary:"):
            self._canary = CanaryManager(config=self._canary_config)
            skill_name = target[len("canary:") :]
            state = self._canary.get_state(skill_name)
            if state is not None:
                fitness_before = state.current_rate
                details["phase_before"] = state.phase.value
            rolled = self._canary.force_rollback(
                skill_name,
                reason=reason,
                metadata={"last_rollback_id": rollback_id},
            )
            if rolled is not None:
                success = True
                fitness_after = rolled.current_rate
                details["skill_name"] = skill_name
                source_proposal_id = None
                if isinstance(rolled.metadata, dict):
                    source_proposal_id = (
                        str(rolled.metadata.get("proposal_id") or "").strip() or None
                    )
                ledger_record = self._ledger.propose(
                    kind="canary_rollback",
                    description=f"Rollback canary for {skill_name}: {reason}",
                    proposer="rollback_coordinator",
                    fitness_before=fitness_before,
                    metadata={
                        "rollback_id": rollback_id,
                        "target": target,
                        "strategy": strategy,
                        "reason": reason,
                        "source_proposal_id": source_proposal_id,
                    },
                )
                self._ledger.mark_rolled_back(ledger_record.proposal_id)
                details["ledger_proposal_id"] = ledger_record.proposal_id
                if source_proposal_id:
                    source_record = self._ledger.mark_rolled_back(source_proposal_id)
                    if source_record is not None:
                        source_record.metadata = {
                            **source_record.metadata,
                            "rollback_id": rollback_id,
                            "target": target,
                            "strategy": strategy,
                            "reason": reason,
                            "canary_key": skill_name,
                            "last_rollback_reason": reason,
                        }
                        self._ledger._rewrite_record(source_record)
                        details["source_proposal_id"] = source_proposal_id
                        details["source_proposal_status"] = source_record.status.value
                    else:
                        details["source_proposal_error"] = (
                            f"Proposal not found: {source_proposal_id}"
                        )
            else:
                details["error"] = f"No canary state for skill: {skill_name}"

        elif target.startswith("proposal:"):
            proposal_id = target[len("proposal:") :]
            all_records = self._ledger.query()
            target_record = None
            for r in all_records:
                if r.proposal_id == proposal_id:
                    target_record = r
                    break
            if target_record is not None:
                fitness_before = target_record.fitness_before
            rolled = self._ledger.mark_rolled_back(proposal_id)
            if rolled is not None:
                success = True
                fitness_after = rolled.fitness_after
                rolled.metadata = {
                    **rolled.metadata,
                    "rollback_id": rollback_id,
                    "target": target,
                    "strategy": strategy,
                    "reason": reason,
                }
                self._ledger._rewrite_record(rolled)
                details["proposal_id"] = proposal_id
            else:
                details["error"] = f"Proposal not found: {proposal_id}"

        else:
            details["error"] = f"Unknown target format: {target}"

        result = RollbackResult(
            rollback_id=rollback_id,
            target=target,
            strategy=strategy,
            reason=reason,
            success=success,
            fitness_before=fitness_before,
            fitness_after=fitness_after,
            ts=ts,
            details=details,
        )
        self._append_log(result)
        return result

    def verify_rollback(self, rollback_id: str) -> RollbackVerification:
        entries = self._read_log()
        match: dict[str, Any] | None = None
        for e in entries:
            if e.get("rollback_id") == rollback_id:
                match = e
                break

        if match is None:
            return RollbackVerification(
                rollback_id=rollback_id,
                complete=False,
                fitness_recovered=False,
                message=f"Rollback {rollback_id} not found",
            )

        complete = bool(match.get("success", False))
        target = str(match.get("target", ""))
        details = match.get("details") if isinstance(match.get("details"), dict) else {}

        if target.startswith("canary:"):
            skill_name = target[len("canary:") :]
            state = self._canary.get_state(skill_name)
            complete = complete and state is not None and state.phase.value == "rolled_back"
        elif target.startswith("proposal:"):
            proposal_id = target[len("proposal:") :]
            record = self._find_proposal(proposal_id)
            complete = (
                complete and record is not None and record.status == ProposalStatus.ROLLED_BACK
            )
        elif details.get("ledger_proposal_id"):
            record = self._find_proposal(str(details["ledger_proposal_id"]))
            complete = (
                complete and record is not None and record.status == ProposalStatus.ROLLED_BACK
            )

        fb = match.get("fitness_before")
        fa = match.get("fitness_after")

        fitness_recovered = False
        if fb is not None and fa is not None:
            fitness_recovered = fa >= fb

        parts: list[str] = []
        parts.append("Rollback complete" if complete else "Rollback incomplete")
        if fb is not None and fa is not None:
            parts.append("fitness recovered" if fitness_recovered else "fitness not yet recovered")

        return RollbackVerification(
            rollback_id=rollback_id,
            complete=complete,
            fitness_recovered=fitness_recovered,
            message=", ".join(parts),
        )

    def rollback_history(
        self,
        agent_id: str | None = None,
        limit: int = 50,
    ) -> list[RollbackRecord]:
        ledger_entries: list[RollbackRecord] = []
        for record in self._ledger.query(status=ProposalStatus.ROLLED_BACK, limit=10_000):
            rollback_id = record.metadata.get("rollback_id")
            if not rollback_id:
                continue
            if agent_id is not None and record.metadata.get("agent_id") != agent_id:
                continue
            ledger_entries.append(
                RollbackRecord(
                    rollback_id=str(rollback_id),
                    target=str(record.metadata.get("target") or f"proposal:{record.proposal_id}"),
                    reason=str(record.metadata.get("reason") or record.description),
                    success=True,
                    ts=record.rolled_back_ts or record.ts,
                )
            )
        if ledger_entries:
            return ledger_entries[-limit:]

        entries = self._read_log()
        if agent_id is not None:
            entries = [e for e in entries if e.get("details", {}).get("agent_id") == agent_id]
        entries = entries[-limit:]
        return [
            RollbackRecord(
                rollback_id=e["rollback_id"],
                target=e["target"],
                reason=e["reason"],
                success=e["success"],
                ts=e["ts"],
            )
            for e in entries
        ]

    def _append_log(self, result: RollbackResult) -> None:
        line = json.dumps(asdict(result), ensure_ascii=False, default=str) + "\n"
        with _FILE_LOCK, self._rollback_log.open("a", encoding="utf-8") as fh:
            fh.write(line)

    def _find_proposal(self, proposal_id: str):
        for record in self._ledger.query(limit=10_000):
            if record.proposal_id == proposal_id:
                return record
        return None

    def _read_log(self) -> list[dict[str, Any]]:
        if not self._rollback_log.exists():
            return []
        records: list[dict[str, Any]] = []
        with _FILE_LOCK, self._rollback_log.open("r", encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    records.append(json.loads(stripped))
                except json.JSONDecodeError:
                    continue
        return records


__all__ = [
    "RollbackCoordinator",
    "RollbackRecord",
    "RollbackResult",
    "RollbackVerification",
]
