"""Workflow seam vocabulary (dsh ``@deepseek-ai/dsh-workflow/types``).

Model-authored orchestration scripts fan out subagents through a small
hook vocabulary; this module owns the request/run/result/event types the
engine consumes and produces. Types only — execution lives in
``engine.py`` / ``worker.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, NewType

WorkflowRunId = NewType("WorkflowRunId", str)


@dataclass(frozen=True)
class WorkflowPhase:
    """One phase declared in a script's ``meta.phases`` (progress only)."""

    title: str
    detail: str | None = None
    provider: str | None = None
    model: str | None = None


@dataclass(frozen=True)
class WorkflowMeta:
    """The script's identity block, provided as plain JSON data.

    ``name`` / ``description`` are required; the rest is optional
    annotation. The field vocabulary matches dsh / Claude Code
    dynamic-workflows.
    """

    name: str
    description: str
    when_to_use: str | None = None
    phases: list[WorkflowPhase] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
        }
        if self.when_to_use is not None:
            out["whenToUse"] = self.when_to_use
        if self.phases:
            out["phases"] = [
                {
                    "title": p.title,
                    **({"detail": p.detail} if p.detail is not None else {}),
                    **({"provider": p.provider} if p.provider is not None else {}),
                    **({"model": p.model} if p.model is not None else {}),
                }
                for p in self.phases
            ]
        return out


WorkflowStopReason = Literal["completed", "cancelled", "error"]


@dataclass(frozen=True)
class WorkflowResult:
    """The outcome resolved by a live workflow run.

    ``value`` is the script's materialized return value (plain JSON data;
    ``None`` when the script returned nothing) — meaningful only for
    ``completed``. ``agentsStarted`` counts accepted ``agent()`` calls.
    """

    value: Any
    stop_reason: WorkflowStopReason
    error: str | None = None
    agents_started: int = 0


@dataclass(frozen=True)
class WorkflowRunInfo:
    """Identifying detail for a run, carried by every ``workflow/*`` event."""

    id: WorkflowRunId
    meta: WorkflowMeta


@dataclass(frozen=True)
class WorkflowAgentInfo:
    """One ``agent()`` call's identity within a run (``agent-start`` payload)."""

    seq: int
    label: str
    phase: str | None = None
    child_id: str | None = None


WorkflowAgentOutcome = Literal["completed", "failed", "cancelled"]


@dataclass(frozen=True)
class WorkflowAgentEndInfo:
    """How one ``agent()`` call settled (``agent-end`` payload)."""

    seq: int
    label: str
    phase: str | None = None
    child_id: str | None = None
    outcome: WorkflowAgentOutcome = "completed"


@dataclass(frozen=True)
class WorkflowResultInfo:
    """A settled run's outcome as event data (``workflow/end`` payload).

    Deliberately WITHOUT the result ``value`` — observers must not receive
    a mutable alias of the caller's result value.
    """

    stop_reason: WorkflowStopReason
    error: str | None = None
    agents_started: int = 0


WorkflowErrorCode = Literal[
    "SCRIPT_PARSE",
    "META_INVALID",
    "INVALID_ARGUMENT",
    "UNSUPPORTED_OPTION",
    "UNSUPPORTED_SCHEMA",
    "AGENT_CAP",
    "ITEM_CAP",
    "AGENT_START",
    "AGENT_RESULT",
    "RESULT_UNSERIALIZABLE",
    "CANCELLED",
]


class WorkflowError(Exception):
    """Typed error for workflow-seam failures.

    ``fatal`` drives the combinator discipline: ``parallel()`` /
    ``pipeline()`` re-throw a fatal error (a bad option or a tripped cap
    must kill the script loudly), reserving the per-item ``None`` for
    child-run failures and ordinary stage errors. Every shipped code is
    fatal; the flag exists so the distinction is explicit at catch sites.
    """

    def __init__(
        self,
        message: str,
        code: WorkflowErrorCode,
        *,
        fatal: bool = True,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.fatal = fatal
        if cause is not None:
            self.__cause__ = cause


def is_fatal_workflow_error(error: Any) -> bool:
    """Whether combinators must re-throw ``error`` instead of nulling the item."""
    return isinstance(error, WorkflowError) and error.fatal


__all__ = [
    "WorkflowAgentEndInfo",
    "WorkflowAgentInfo",
    "WorkflowAgentOutcome",
    "WorkflowError",
    "WorkflowErrorCode",
    "WorkflowMeta",
    "WorkflowPhase",
    "WorkflowResult",
    "WorkflowResultInfo",
    "WorkflowRunId",
    "WorkflowRunInfo",
    "WorkflowStopReason",
    "is_fatal_workflow_error",
]
