"""Background-job capability seam (dsh ``packages/jobs`` port)."""

from __future__ import annotations

from runtime.platform.process.session import current_session

from .registry import (
    DEFAULT_MAX_CONCURRENT_JOBS_PER_OWNER,
    LocalJobRegistry,
)
from .subagent_producer import (
    DEFAULT_SUBAGENT_JOB_TIMEOUT_S,
    build_subagent_job_start,
    completion_notice,
)
from .types import (
    TASK_WAIT_TIMEOUT,
    JobHooks,
    JobOutcome,
    JobRead,
    JobSnapshot,
    JobStart,
    JobStatus,
    TerminalJobStatus,
    is_terminal,
)


def parent_job_key() -> str | None:
    """The opaque owner key for jobs started from the current turn: the
    parent thread id when one is bound, else the agent id, else ``None``
    (an unowned job, open to any caller)."""
    session = current_session()
    if session is None:
        return None
    thread_id = getattr(session, "thread_id", None)
    if isinstance(thread_id, str) and thread_id:
        return thread_id
    agent_id = session.agent_id
    return agent_id or None


__all__ = [
    "DEFAULT_MAX_CONCURRENT_JOBS_PER_OWNER",
    "DEFAULT_SUBAGENT_JOB_TIMEOUT_S",
    "JobHooks",
    "JobOutcome",
    "JobRead",
    "JobSnapshot",
    "JobStart",
    "JobStatus",
    "LocalJobRegistry",
    "TASK_WAIT_TIMEOUT",
    "TerminalJobStatus",
    "build_subagent_job_start",
    "completion_notice",
    "is_terminal",
    "parent_job_key",
]
