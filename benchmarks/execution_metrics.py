"""Measured, backend-neutral outcomes for engine comparisons.

This module deliberately keeps *execution* and *evaluation* separate:

* ``execution_success`` comes only from a real terminal turn returned by the
  realtime gateway;
* ``verification`` comes only from first-class ``VerificationItem`` records;
* ``outcome_grader`` is the isolated fixture grader's independent verdict;
* token and dollar usage is recorded only when the server reported it.

There is no synthetic quality score and no price-table estimate.  Missing
evidence stays ``null``/``not_run`` instead of being converted to a flattering
zero or a guessed pass.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from benchmarks.eval_harness import Trajectory, Verdict

EXECUTION_MEASUREMENT_SCHEMA = "echo.engine_execution_measurement.v2"
EXECUTION_MEASUREMENT_VERSION = 2

BackendId = Literal["native", "codex"]
VerificationStatus = Literal["passed", "failed", "incomplete", "not_run"]

_SUCCESS_TERMINAL_STATUSES = frozenset({"completed"})
_FAILURE_TERMINAL_STATUSES = frozenset({"failed", "cancelled", "canceled", "interrupted", "paused"})
_FAILED_ITEM_STATUSES = frozenset({"failed", "cancelled", "canceled", "interrupted"})
_COMPLETED_ITEM_STATUSES = frozenset({"completed"})


@dataclass(frozen=True)
class UsageMeasurement:
    """Usage values copied from the latest server-reported cumulative total."""

    reported: bool
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "reported": self.reported,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
            "cost_source": "server_reported" if self.cost_usd is not None else "not_reported",
        }


@dataclass(frozen=True)
class ExecutionMeasurement:
    """One factual backend/case/trial measurement with its raw trajectory."""

    task_id: str
    case_id: str
    trial_id: str
    schedule_ordinal: int
    trial_index: int
    backend: BackendId
    agent_id: str
    requested_model: str | None
    observed_control_plane_model: str | None
    observed_backend_model: str | None
    execution_success: bool | None
    terminal_status: str | None
    verification: VerificationStatus
    verification_items: tuple[dict[str, Any], ...]
    grader_passed: bool | None
    grader_reason: str
    valid_for_engine_rate: bool
    failure_category: str | None
    infrastructure_reason: str | None
    duration_ms: float
    usage: UsageMeasurement
    trajectory_sha256: str
    trajectory: dict[str, Any]

    @property
    def infrastructure_valid(self) -> bool:
        """Compatibility alias for callers that predate measurement v2."""

        return self.valid_for_engine_rate

    @property
    def model(self) -> str | None:
        """Compatibility alias; never represents an observed backend model."""

        return self.requested_model

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": EXECUTION_MEASUREMENT_SCHEMA,
            "version": EXECUTION_MEASUREMENT_VERSION,
            "task_id": self.task_id,
            "case_id": self.case_id,
            "trial_id": self.trial_id,
            "schedule_ordinal": self.schedule_ordinal,
            "trial_index": self.trial_index,
            "backend": self.backend,
            "backend_source": "explicit_agent_id",
            "agent_id": self.agent_id,
            "model": {
                "requested": self.requested_model,
                "observed_control_plane": self.observed_control_plane_model,
                "observed_backend": self.observed_backend_model,
                "observed_backend_status": (
                    "observed" if self.observed_backend_model is not None else "unattested"
                ),
            },
            "execution_success": self.execution_success,
            "terminal_status": self.terminal_status,
            "valid_for_engine_rate": self.valid_for_engine_rate,
            "failure_category": self.failure_category,
            "infrastructure": {
                "valid": self.valid_for_engine_rate,
                "failure_category": self.failure_category,
                "reason": self.infrastructure_reason,
            },
            "verification": {
                "status": self.verification,
                "source": (
                    "turn_verification_items" if self.verification_items else "not_reported"
                ),
                "items": list(self.verification_items),
            },
            "outcome_grader": {
                "passed": self.grader_passed,
                "reason": self.grader_reason,
            },
            "duration_ms": self.duration_ms,
            "usage": self.usage.to_dict(),
            "trajectory_sha256": self.trajectory_sha256,
            # Raw events are evidence, not an optional debug afterthought.  A
            # comparison artifact must remain independently auditable.
            "trajectory": self.trajectory,
        }


def measurement_from_trial(
    trajectory: Trajectory,
    verdict: Verdict,
    *,
    backend: BackendId,
    agent_id: str,
    model: str | None,
    schedule_ordinal: int = 0,
    trial_index: int = 0,
) -> ExecutionMeasurement:
    """Build one measurement without inferring facts the trial did not emit."""

    terminal_turn = _terminal_turn(trajectory)
    terminal_status = _terminal_status(terminal_turn)
    execution_success: bool | None = None
    if terminal_status in _SUCCESS_TERMINAL_STATUSES:
        execution_success = True
    elif terminal_status in _FAILURE_TERMINAL_STATUSES:
        execution_success = False

    verification, verification_items = _verification(terminal_turn)
    trajectory_payload = trajectory.to_dict()
    serialized = json.dumps(
        trajectory_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    normalized_model = str(model).strip() if model is not None else ""
    infrastructure_reason = _infrastructure_reason(trajectory)
    valid_for_engine_rate = infrastructure_reason is None
    failure_category = (
        "infrastructure" if not valid_for_engine_rate else trajectory.failure_category
    )
    return ExecutionMeasurement(
        task_id=trajectory.case_id,
        case_id=trajectory.case_id,
        trial_id=trajectory.trial_id,
        schedule_ordinal=schedule_ordinal,
        trial_index=trial_index,
        backend=backend,
        agent_id=agent_id,
        requested_model=normalized_model or None,
        observed_control_plane_model=_observed_control_plane_model(terminal_turn),
        observed_backend_model=_observed_backend_model(terminal_turn),
        execution_success=execution_success,
        terminal_status=terminal_status,
        verification=verification,
        verification_items=verification_items,
        grader_passed=verdict.passed if valid_for_engine_rate else None,
        grader_reason=verdict.reason,
        valid_for_engine_rate=valid_for_engine_rate,
        failure_category=failure_category,
        infrastructure_reason=infrastructure_reason,
        duration_ms=trajectory.runtime_ms(),
        usage=_usage(trajectory),
        trajectory_sha256=hashlib.sha256(serialized).hexdigest(),
        trajectory=trajectory_payload,
    )


def aggregate_measurements(
    measurements: Sequence[ExecutionMeasurement],
    *,
    requested_k: int,
) -> list[dict[str, Any]]:
    """Aggregate engine rates without scoring infrastructure failures."""

    if requested_k < 1:
        raise ValueError("requested_k must be at least 1")
    grouped: dict[tuple[str, str], list[ExecutionMeasurement]] = {}
    for measurement in measurements:
        grouped.setdefault((measurement.backend, measurement.case_id), []).append(measurement)

    rows: list[dict[str, Any]] = []
    for (backend, case_id), group in sorted(grouped.items()):
        valid = [measurement for measurement in group if measurement.valid_for_engine_rate]
        passes = sum(measurement.grader_passed is True for measurement in valid)
        complete = len(group) == requested_k and len(valid) == requested_k
        rows.append(
            {
                "backend": backend,
                "case_id": case_id,
                "requested_k": requested_k,
                "scheduled": len(group),
                "valid": len(valid),
                "invalid": len(group) - len(valid),
                "passes": passes,
                "pass_rate": (passes / len(valid) if valid else None),
                "complete": complete,
                "pass_at_k": (1.0 if passes > 0 else 0.0) if complete else None,
            }
        )
    return rows


def _terminal_turn(trajectory: Trajectory) -> dict[str, Any] | None:
    """Return the last real terminal turn carried by a gateway result event."""

    for step in reversed(trajectory.steps):
        if step.kind != "turn_result":
            continue
        turn = step.payload.get("turn")
        if isinstance(turn, dict):
            return turn
    return None


def _infrastructure_reason(trajectory: Trajectory) -> str | None:
    if trajectory.failure_category == "infrastructure":
        return trajectory.error or "trajectory classified as infrastructure failure"
    for step in trajectory.steps:
        if step.kind == "infrastructure_error":
            detail = step.payload.get("error") or step.payload
            if isinstance(detail, (dict, list)):
                return json.dumps(detail, ensure_ascii=False, sort_keys=True)
            return str(detail)
    return None


def _observed_control_plane_model(turn: dict[str, Any] | None) -> str | None:
    if turn is None:
        return None
    params = turn.get("params")
    if not isinstance(params, dict):
        return None
    return _optional_text(params.get("model"))


def _observed_backend_model(turn: dict[str, Any] | None) -> str | None:
    """Accept only an explicit backend observation, never the outer alias."""

    if turn is None:
        return None
    value = turn.get("backendModel")
    if value is None:
        value = turn.get("backend_model")
    return _optional_text(value)


def _terminal_status(turn: dict[str, Any] | None) -> str | None:
    if turn is None:
        return None
    raw = turn.get("status")
    if not isinstance(raw, str) or not raw.strip():
        return None
    return raw.strip().casefold()


def _verification(
    turn: dict[str, Any] | None,
) -> tuple[VerificationStatus, tuple[dict[str, Any], ...]]:
    if turn is None:
        return "not_run", ()
    raw_items = turn.get("items")
    if not isinstance(raw_items, list):
        return "not_run", ()

    evidence: list[dict[str, Any]] = []
    failed = False
    incomplete = False
    for raw in raw_items:
        if not isinstance(raw, dict) or str(raw.get("type") or "") != "verification":
            continue
        status = str(raw.get("status") or "").strip().casefold()
        exit_code = _integer(raw.get("exitCode"))
        if exit_code is None:
            exit_code = _integer(raw.get("exit_code"))
        if status in _FAILED_ITEM_STATUSES or (exit_code is not None and exit_code != 0):
            failed = True
        elif status not in _COMPLETED_ITEM_STATUSES:
            incomplete = True
        evidence.append(
            {
                "id": _optional_text(raw.get("id")),
                "status": status or None,
                "kind": _optional_text(raw.get("kind")),
                "command": _optional_text(raw.get("command")),
                "exit_code": exit_code,
                "summary": _bounded_text(raw.get("summary"), limit=1000),
            }
        )

    if not evidence:
        return "not_run", ()
    if failed:
        return "failed", tuple(evidence)
    if incomplete:
        return "incomplete", tuple(evidence)
    return "passed", tuple(evidence)


def _usage(trajectory: Trajectory) -> UsageMeasurement:
    reported = False
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None

    for step in trajectory.steps:
        if step.kind != "token_usage":
            continue
        reported = True
        raw = step.payload.get("usage")
        if not isinstance(raw, dict):
            continue
        total = raw.get("total")
        total_obj = total if isinstance(total, dict) else {}
        next_input = _first_int(total_obj, raw, keys=("inputTokens", "input_tokens"))
        next_output = _first_int(total_obj, raw, keys=("outputTokens", "output_tokens"))
        next_total = _first_int(total_obj, raw, keys=("totalTokens", "total_tokens"))
        next_cost = _first_float(total_obj, raw, keys=("costUsd", "cost_usd", "usd"))
        if next_input is not None:
            input_tokens = next_input
        if next_output is not None:
            output_tokens = next_output
        if next_total is not None:
            total_tokens = next_total
        if next_cost is not None:
            cost_usd = next_cost

    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    return UsageMeasurement(
        reported=reported,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cost_usd=cost_usd,
    )


def _first_int(*objects: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for obj in objects:
        for key in keys:
            value = _nonnegative_int(obj.get(key))
            if value is not None:
                return value
    return None


def _first_float(*objects: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for obj in objects:
        for key in keys:
            value = obj.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            parsed = float(value)
            if math.isfinite(parsed) and parsed >= 0:
                return parsed
    return None


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _integer(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _bounded_text(value: Any, *, limit: int) -> str | None:
    text = _optional_text(value)
    if text is None:
        return None
    return text if len(text) <= limit else text[:limit]


__all__ = [
    "BackendId",
    "EXECUTION_MEASUREMENT_SCHEMA",
    "EXECUTION_MEASUREMENT_VERSION",
    "ExecutionMeasurement",
    "UsageMeasurement",
    "VerificationStatus",
    "aggregate_measurements",
    "measurement_from_trial",
]


