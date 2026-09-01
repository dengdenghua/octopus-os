"""Versioned experiment evidence for engine and genome comparisons.

The legacy dual-helix report pairs arbitrary completed turns by a hash of the
goal text.  That is useful telemetry, but it is not an experiment: the two
engines may have run in different workspaces, with different budgets, or at
different times.  This module provides the identity and persistence contract
for deliberate same-task trials.

Experiment identity is deliberately independent from prose normalization.
Two trials are comparable only when they share the same experiment, TaskSpec,
environment digest, and trial index.  Goal fingerprints remain search hints;
they are never an acceptance key.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

TASK_SPEC_SCHEMA = "echo.evolution.task_spec.v1"
TRIAL_SCHEMA = "echo.evolution.trial.v1"
PAIR_EVIDENCE_SCHEMA = "echo.evolution.pair_evidence.v1"

_STORE_LOCK = threading.RLock()
_ENGINES = frozenset({"echo", "codex"})


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _digest(payload: dict[str, Any], *, length: int = 24) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:length]


class TrialStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INFRASTRUCTURE_FAILED = "infrastructure_failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class TaskSpec:
    case_id: str
    goal: str
    domain: str
    environment_digest: str
    workspace_fixture_digest: str = ""
    role_id: str = "general"
    gene_scope: str = "runtime"
    task_spec_version: str = "1"
    budget_policy: dict[str, Any] = field(default_factory=dict)
    grader_version: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    schema: str = TASK_SPEC_SCHEMA

    def __post_init__(self) -> None:
        for label, value in (
            ("case_id", self.case_id),
            ("goal", self.goal),
            ("domain", self.domain),
            ("environment_digest", self.environment_digest),
            ("task_spec_version", self.task_spec_version),
        ):
            if not _clean(value):
                raise ValueError(f"TaskSpec {label} is required")

    @property
    def task_spec_hash(self) -> str:
        # Metadata is intentionally excluded: display annotations must not make
        # otherwise identical trials incomparable.
        return _digest(
            {
                "schema": self.schema,
                "case_id": self.case_id,
                "goal": self.goal,
                "domain": self.domain,
                "environment_digest": self.environment_digest,
                "workspace_fixture_digest": self.workspace_fixture_digest,
                "role_id": self.role_id,
                "gene_scope": self.gene_scope,
                "task_spec_version": self.task_spec_version,
                "budget_policy": self.budget_policy,
                "grader_version": self.grader_version,
            }
        )

    def to_wire(self) -> dict[str, Any]:
        return {**asdict(self), "task_spec_hash": self.task_spec_hash}

    @classmethod
    def from_wire(cls, value: dict[str, Any]) -> TaskSpec:
        return cls(
            case_id=_clean(value.get("case_id")),
            goal=_clean(value.get("goal")),
            domain=_clean(value.get("domain")),
            environment_digest=_clean(value.get("environment_digest")),
            workspace_fixture_digest=_clean(value.get("workspace_fixture_digest")),
            role_id=_clean(value.get("role_id")) or "general",
            gene_scope=_clean(value.get("gene_scope")) or "runtime",
            task_spec_version=_clean(value.get("task_spec_version")) or "1",
            budget_policy=(
                dict(value.get("budget_policy"))
                if isinstance(value.get("budget_policy"), dict)
                else {}
            ),
            grader_version=_clean(value.get("grader_version")),
            metadata=(
                dict(value.get("metadata")) if isinstance(value.get("metadata"), dict) else {}
            ),
            schema=_clean(value.get("schema")) or TASK_SPEC_SCHEMA,
        )


@dataclass
class ExperimentTrial:
    experiment_id: str
    run_id: str
    task_spec: TaskSpec
    engine: str
    trial_index: int
    seed: int
    status: TrialStatus = TrialStatus.QUEUED
    candidate_id: str = "baseline"
    outcome_passed: bool | None = None
    hard_gates: dict[str, bool] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)
    artifacts: dict[str, Any] = field(default_factory=dict)
    started_at: str | None = None
    ended_at: str | None = None
    error: str | None = None
    infrastructure_error: str | None = None
    schema: str = TRIAL_SCHEMA

    def __post_init__(self) -> None:
        self.engine = _clean(self.engine).lower()
        if self.engine not in _ENGINES:
            raise ValueError("trial engine must be echo or codex")
        if not _clean(self.experiment_id) or not _clean(self.run_id):
            raise ValueError("experiment_id and run_id are required")
        if int(self.trial_index) < 0:
            raise ValueError("trial_index must be non-negative")
        self.trial_index = int(self.trial_index)
        self.seed = int(self.seed)

    @property
    def pair_key(self) -> str:
        return ":".join(
            (
                self.experiment_id,
                self.task_spec.case_id,
                self.task_spec.task_spec_hash,
                self.task_spec.environment_digest,
                str(self.trial_index),
            )
        )

    @property
    def hard_gate_passed(self) -> bool:
        return bool(self.hard_gates) and all(bool(value) for value in self.hard_gates.values())

    @property
    def comparable_result(self) -> bool:
        return (
            self.status == TrialStatus.COMPLETED
            and self.outcome_passed is not None
            and self.hard_gate_passed
            and not self.infrastructure_error
        )

    def to_wire(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "experiment_id": self.experiment_id,
            "run_id": self.run_id,
            "task_spec": self.task_spec.to_wire(),
            "task_spec_hash": self.task_spec.task_spec_hash,
            "pair_key": self.pair_key,
            "engine": self.engine,
            "trial_index": self.trial_index,
            "seed": self.seed,
            "status": self.status.value,
            "candidate_id": self.candidate_id,
            "outcome_passed": self.outcome_passed,
            "hard_gates": dict(self.hard_gates),
            "hard_gate_passed": self.hard_gate_passed,
            "metrics": dict(self.metrics),
            "artifacts": dict(self.artifacts),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "error": self.error,
            "infrastructure_error": self.infrastructure_error,
        }

    @classmethod
    def from_wire(cls, value: dict[str, Any]) -> ExperimentTrial:
        raw_spec = value.get("task_spec")
        if not isinstance(raw_spec, dict):
            raise ValueError("trial task_spec is required")
        return cls(
            experiment_id=_clean(value.get("experiment_id")),
            run_id=_clean(value.get("run_id")),
            task_spec=TaskSpec.from_wire(raw_spec),
            engine=_clean(value.get("engine")),
            trial_index=int(value.get("trial_index") or 0),
            seed=int(value.get("seed") or 0),
            status=TrialStatus(_clean(value.get("status")) or TrialStatus.QUEUED.value),
            candidate_id=_clean(value.get("candidate_id")) or "baseline",
            outcome_passed=(
                bool(value.get("outcome_passed"))
                if value.get("outcome_passed") is not None
                else None
            ),
            hard_gates=(
                dict(value.get("hard_gates")) if isinstance(value.get("hard_gates"), dict) else {}
            ),
            metrics={
                str(key): float(metric)
                for key, metric in (value.get("metrics") or {}).items()
                if isinstance(metric, (int, float)) and math.isfinite(float(metric))
            },
            artifacts=(
                dict(value.get("artifacts")) if isinstance(value.get("artifacts"), dict) else {}
            ),
            started_at=_clean(value.get("started_at")) or None,
            ended_at=_clean(value.get("ended_at")) or None,
            error=_clean(value.get("error")) or None,
            infrastructure_error=_clean(value.get("infrastructure_error")) or None,
            schema=_clean(value.get("schema")) or TRIAL_SCHEMA,
        )


def new_experiment_id(*, prefix: str = "exp") -> str:
    return f"{prefix}_{uuid4().hex[:20]}"


def new_run_id(*, prefix: str = "trial") -> str:
    return f"{prefix}_{uuid4().hex[:20]}"


class ExperimentStore:
    """Append-only JSONL store for deliberate experiment trials."""

    def __init__(self, path: str | Path = "data/evolution_experiments.jsonl") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, trial: ExperimentTrial) -> Path:
        line = json.dumps(trial.to_wire(), ensure_ascii=False, sort_keys=True) + "\n"
        with _STORE_LOCK, self.path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
        return self.path

    def list_trials(
        self,
        *,
        experiment_id: str | None = None,
        case_id: str | None = None,
        limit: int = 10_000,
    ) -> list[ExperimentTrial]:
        if not self.path.exists():
            return []
        rows: list[ExperimentTrial] = []
        with _STORE_LOCK, self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    raw = json.loads(line)
                    if not isinstance(raw, dict):
                        continue
                    trial = ExperimentTrial.from_wire(raw)
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue
                if experiment_id and trial.experiment_id != experiment_id:
                    continue
                if case_id and trial.task_spec.case_id != case_id:
                    continue
                rows.append(trial)
        return rows[-max(1, int(limit)) :]


def build_pair_evidence(
    trials: list[ExperimentTrial],
    *,
    primary_metric: str = "quality",
    limit: int = 100,
) -> dict[str, Any]:
    """Build strict pair evidence without treating infrastructure errors as losses."""

    groups: dict[str, dict[str, ExperimentTrial]] = {}
    excluded: dict[str, int] = {
        "infrastructure_failed": 0,
        "incomplete": 0,
        "hard_gate_failed": 0,
        "duplicate_engine_trial": 0,
    }
    for trial in trials:
        if trial.status == TrialStatus.INFRASTRUCTURE_FAILED or trial.infrastructure_error:
            excluded["infrastructure_failed"] += 1
            continue
        if trial.status != TrialStatus.COMPLETED or trial.outcome_passed is None:
            excluded["incomplete"] += 1
            continue
        if not trial.hard_gate_passed:
            excluded["hard_gate_failed"] += 1
        strand = groups.setdefault(trial.pair_key, {})
        if trial.engine in strand:
            excluded["duplicate_engine_trial"] += 1
        strand[trial.engine] = trial

    pairs: list[dict[str, Any]] = []
    wins = {"echo": 0, "codex": 0, "tie": 0}
    for pair_key, strand in groups.items():
        if set(strand) != _ENGINES:
            continue
        echo = strand["echo"]
        codex = strand["codex"]
        winner = _winner(echo, codex, primary_metric=primary_metric)
        wins[winner] += 1
        pairs.append(
            {
                "pair_key": pair_key,
                "experiment_id": echo.experiment_id,
                "case_id": echo.task_spec.case_id,
                "task_spec_hash": echo.task_spec.task_spec_hash,
                "trial_index": echo.trial_index,
                "goal": echo.task_spec.goal,
                "domain": echo.task_spec.domain,
                "winner": winner,
                "echo": echo.to_wire(),
                "codex": codex.to_wire(),
            }
        )
    pairs.sort(key=lambda row: (row["experiment_id"], row["case_id"], row["trial_index"]))
    pairable_keys = sum(1 for strand in groups.values() if set(strand) == _ENGINES)
    return {
        "ok": True,
        "schema": PAIR_EVIDENCE_SCHEMA,
        "generated_at": _now(),
        "trial_count": len(trials),
        "paired_count": len(pairs),
        "pairable_key_count": pairable_keys,
        "unpaired_key_count": max(0, len(groups) - pairable_keys),
        "echo_wins": wins["echo"],
        "codex_wins": wins["codex"],
        "ties": wins["tie"],
        "excluded": excluded,
        "primary_metric": primary_metric,
        "pairs": pairs[: max(1, int(limit))],
    }


def _winner(
    echo: ExperimentTrial,
    codex: ExperimentTrial,
    *,
    primary_metric: str,
) -> str:
    # Hard gates and real outcome dominate every efficiency metric.
    echo_valid = echo.hard_gate_passed and bool(echo.outcome_passed)
    codex_valid = codex.hard_gate_passed and bool(codex.outcome_passed)
    if echo_valid != codex_valid:
        return "echo" if echo_valid else "codex"
    if not echo_valid:
        return "tie"
    echo_metric = echo.metrics.get(primary_metric)
    codex_metric = codex.metrics.get(primary_metric)
    if echo_metric is None or codex_metric is None:
        return "tie"
    if math.isclose(echo_metric, codex_metric, rel_tol=1e-9, abs_tol=1e-9):
        return "tie"
    return "echo" if echo_metric > codex_metric else "codex"


__all__ = [
    "ExperimentStore",
    "ExperimentTrial",
    "PAIR_EVIDENCE_SCHEMA",
    "TASK_SPEC_SCHEMA",
    "TRIAL_SCHEMA",
    "TaskSpec",
    "TrialStatus",
    "build_pair_evidence",
    "new_experiment_id",
    "new_run_id",
]
