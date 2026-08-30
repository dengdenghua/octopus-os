"""Background-job seam types (dsh ``packages/jobs`` port).

Task lifecycle: ``running``, optionally ``stopping``, then exactly one
terminal status. Producer-specific facts belong in ``detail``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

JobStatus = Literal["running", "stopping", "completed", "killed", "failed"]
TerminalJobStatus = Literal["completed", "killed", "failed"]

TASK_WAIT_TIMEOUT = "TASK_WAIT_TIMEOUT"


@dataclass(frozen=True)
class JobOutcome:
    """Terminal result supplied by a producer through ``JobHooks.done``."""

    status: TerminalJobStatus
    detail: str | None = None
    output: str | None = None


@dataclass(frozen=True)
class JobHooks:
    """Hooks through which the runtime controls and observes producer work."""

    cancel: Callable[[str | None], None]
    done: Awaitable[JobOutcome]
    read_output: Callable[[], str] | None = None


@dataclass(frozen=True)
class JobStart:
    """Producer declaration passed to ``LocalJobRegistry.start``.

    The runtime preflights access and cleanup before invoking ``run``; the
    producer owns execution resources while the runtime owns identity and
    lifecycle state.
    """

    kind: str
    label: str
    run: Callable[[], JobHooks]
    output_limit_bytes: int | None = None
    owner: str | None = None
    owner_cleanup: Callable[[], Awaitable[None] | None] | None = None
    notify: Callable[[JobSnapshot], None] | None = None
    on_start: Callable[[JobSnapshot], None] | None = None
    on_settle: Callable[[JobSnapshot], None] | None = None
    # Registry-level backstop deadline in seconds. The producer's own
    # timeout stays authoritative; this only force-fails a job whose
    # producer never settles (stuck worker, leaked thread) so it cannot
    # pin an owner's concurrency slot forever. ``None`` disables it.
    watchdog_timeout_s: int | None = None


@dataclass(frozen=True)
class JobSnapshot:
    """A read-only projection of one job, safe to hand to listeners and
    tools — a fresh object per call, never live registry state."""

    id: str
    kind: str
    label: str
    status: JobStatus
    started_at: int
    finished_at: int | None
    reported: bool
    output_limit_bytes: int | None = None
    owner_session: str | None = None
    detail: str | None = None

    def to_public(self) -> dict[str, Any]:
        """State safe for model-authored programs; ownership/bookkeeping
        fields are omitted (dsh ``PublicJobSnapshot``)."""
        public: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "status": self.status,
            "startedAt": self.started_at,
        }
        if self.detail is not None:
            public["detail"] = self.detail
        if self.finished_at is not None:
            public["finishedAt"] = self.finished_at
        return public


@dataclass(frozen=True)
class JobRead:
    """Output and post-read state returned by ``LocalJobRegistry.read``."""

    text: str
    snapshot: JobSnapshot


def is_terminal(status: JobStatus) -> bool:
    return status in ("completed", "killed", "failed")


__all__ = [
    "JobHooks",
    "JobOutcome",
    "JobRead",
    "JobSnapshot",
    "JobStart",
    "JobStatus",
    "TASK_WAIT_TIMEOUT",
    "TerminalJobStatus",
    "is_terminal",
]
