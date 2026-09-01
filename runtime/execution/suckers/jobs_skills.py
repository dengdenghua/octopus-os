"""Model-facing background-job skills (dsh ``tool-jobs`` port).

``call_agent_background`` starts a subagent in the background and returns a
job id immediately; ``job_list`` / ``job_output`` / ``job_kill`` are the
generic controls over ``runtime.execution.jobs``. The module owns the
process-wide registry singleton and attaches the controller required by
producers at registration.
"""

from __future__ import annotations

import logging
from typing import Any

from runtime.execution.jobs import (
    LocalJobRegistry,
    build_subagent_job_start,
    parent_job_key,
)

from .registry import Skill, SkillRegistry

_log = logging.getLogger("runtime.execution.suckers.jobs")

_JOB_OUTPUT_DEFAULT_WAIT_MS = 30_000
_JOB_OUTPUT_MAX_WAIT_MS = 600_000

_JOBS_REGISTRY: LocalJobRegistry | None = None


def get_jobs_registry() -> LocalJobRegistry:
    """The process-wide background-job registry (default caps)."""
    global _JOBS_REGISTRY
    if _JOBS_REGISTRY is None:
        _JOBS_REGISTRY = LocalJobRegistry()
    return _JOBS_REGISTRY


def set_jobs_registry(registry: LocalJobRegistry | None) -> None:
    """Inject a custom registry (tests / deployment wiring)."""
    global _JOBS_REGISTRY
    _JOBS_REGISTRY = registry


def _validate_job_id(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"invalid job_id: expected a non-empty string, got {value!r}")
    return value


def _error(message: str) -> dict[str, Any]:
    return {"success": False, "error": message}


# ── handlers ───────────────────────────────────────────────


def _call_agent_background(
    *,
    agent_id: str,
    prompt: str,
    label: str = "",
    timeout_s: int = 600,
    **_: Any,
) -> dict[str, Any]:
    """Start one subagent in the background; returns a job id immediately."""
    if not agent_id or not isinstance(agent_id, str):
        return _error("agent_id is required")
    if not prompt or not isinstance(prompt, str):
        return _error("prompt is required")
    if not isinstance(timeout_s, int) or timeout_s <= 0:
        return _error(f"invalid timeout_s: expected a positive integer, got {timeout_s!r}")
    try:
        job_id = get_jobs_registry().start(
            build_subagent_job_start(
                agent_id=agent_id,
                prompt=prompt,
                label=label or None,
                timeout_s=timeout_s,
                owner=parent_job_key(),
            )
        )
    except (RuntimeError, ValueError) as exc:
        return _error(str(exc))
    snapshot = get_jobs_registry().get(job_id, parent_job_key())
    return {"job_id": job_id, "job": snapshot.to_public()}


def _job_list(**_: Any) -> list[dict[str, Any]]:
    """List the caller's background jobs (running and finished)."""
    return [snapshot.to_public() for snapshot in get_jobs_registry().list(parent_job_key())]


async def _job_output(
    *,
    job_id: str,
    wait: bool = False,
    timeout_ms: int | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Read a background job (optionally waiting for settlement)."""
    job_id = _validate_job_id(job_id)
    registry = get_jobs_registry()
    caller = parent_job_key()
    if wait:
        timeout = _JOB_OUTPUT_DEFAULT_WAIT_MS if timeout_ms is None else timeout_ms
        if not isinstance(timeout, int) or timeout <= 0:
            timeout = _JOB_OUTPUT_DEFAULT_WAIT_MS
        timeout = min(timeout, _JOB_OUTPUT_MAX_WAIT_MS)
        await registry.wait(job_id, timeout, caller)
    read = registry.read(job_id, caller)
    return {"text": read.text, "job": read.snapshot.to_public()}


def _job_kill(
    *,
    job_id: str,
    reason: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Request cancellation of a running background job by job id."""
    job_id = _validate_job_id(job_id)
    registry = get_jobs_registry()
    caller = parent_job_key()
    outcome = registry.kill(job_id, caller, reason)
    snapshot = registry.get(job_id, caller)
    mapped = "cancellation-requested" if outcome == "requested" else "already-finished"
    return {"outcome": mapped, "job": snapshot.to_public()}


# ── registration ──────────────────────────────────────────

_CALL_AGENT_BACKGROUND_DESCRIPTION = (
    "Start a subagent in the background and return a job id immediately. "
    "The child runs asynchronously; when it settles, the parent is notified "
    "in-session with the outcome. Collect the result with job_output "
    "(set wait: true only when you are genuinely blocked on it) and stop it "
    "with job_kill when it stops mattering. Do not busy-poll or sleep on a "
    "background job — keep working on independent steps and do not duplicate "
    "a running job's work. Arguments: agent_id (string, required — the "
    "subagent role/agent id), prompt (string, required — the child's task), "
    "label (string, optional — one-line display label), timeout_s (number, "
    "optional — wall-clock cap in seconds, default 600)."
)

_JOB_LIST_DESCRIPTION = (
    "List your background jobs (running and finished) with their ids, kinds, "
    "labels, and statuses. Takes no arguments. Returns an array of "
    "{id, kind, label, status, detail?, startedAt, finishedAt?}."
)

_JOB_OUTPUT_DESCRIPTION = (
    "Read a background job. Final-output jobs return their result after "
    "settlement; while live they return empty output. Reads are non-blocking "
    "unless wait: true, which blocks up to the timeout (default 30s, capped "
    "at 10min) and returns the job state when the time expires, leaving the "
    "job alive. Arguments: job_id (string, required), wait (boolean, "
    "optional), timeout_ms (number, optional — only meaningful with "
    "wait: true)."
)

_JOB_KILL_DESCRIPTION = (
    "Request cancellation of a running background job by job id. Returns "
    "immediately; the job settles as killed once its work actually stops. "
    "Arguments: job_id (string, required), reason (string, optional — short "
    "reason, recorded and forwarded to the job)."
)


def register_jobs_skills(registry: SkillRegistry) -> int:
    """Register the jobs skill family. Returns the count registered."""
    get_jobs_registry().attach_controller("jobs-skills")
    registry.register(
        Skill(
            name="call_agent_background",
            description=_CALL_AGENT_BACKGROUND_DESCRIPTION,
            summary=(
                "Start a subagent in the background and return a job id; "
                "collect with job_output, stop with job_kill."
            ),
            affinity=["delegation", "background", "subagent", "async", "task"],
            cost_profile="high",
            trusted_source="skill://public/jobs",
            handler=_call_agent_background,
        ),
        replace=True,
    )
    registry.register(
        Skill(
            name="job_list",
            description=_JOB_LIST_DESCRIPTION,
            summary="List your background jobs with ids, kinds, and statuses.",
            affinity=["delegation", "background", "jobs", "async"],
            cost_profile="low",
            trusted_source="skill://public/jobs",
            handler=_job_list,
        ),
        replace=True,
    )
    registry.register(
        Skill(
            name="job_output",
            description=_JOB_OUTPUT_DESCRIPTION,
            summary=("Read a background job's output; optionally wait for it to finish."),
            affinity=["delegation", "background", "jobs", "async"],
            cost_profile="low",
            trusted_source="skill://public/jobs",
            handler=_job_output,
        ),
        replace=True,
    )
    registry.register(
        Skill(
            name="job_kill",
            description=_JOB_KILL_DESCRIPTION,
            summary="Request cancellation of a background job by id.",
            affinity=["delegation", "background", "jobs", "async"],
            cost_profile="low",
            trusted_source="skill://public/jobs",
            handler=_job_kill,
        ),
        replace=True,
    )
    return 4


__all__ = [
    "get_jobs_registry",
    "register_jobs_skills",
    "set_jobs_registry",
]
