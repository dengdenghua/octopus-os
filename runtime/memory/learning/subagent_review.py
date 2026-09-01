"""Turn subagent outputs into trace-linked review queue candidates.

Subagents are most valuable when their useful work can outlive the current
turn. This module converts a completed subagent result into the existing
``ReviewQueue`` shape, but only when there is enough trace context to audit
where the candidate came from.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from runtime.memory.learning.review_queue import ReviewQueue
from runtime.platform.process.paths import app_paths


def queue_subagent_review_candidate(
    *,
    agent_id: str,
    role: str,
    prompt: str,
    result: dict[str, Any],
    context: dict[str, Any] | None = None,
    session: Any = None,
    review_queue: ReviewQueue | None = None,
    review_queue_path: str | Path | None = None,
) -> dict[str, Any]:
    """Persist one successful subagent output as a pending review candidate.

    The write is intentionally conservative:
    - explicit ``queue_subagent_review=False`` disables it;
    - failed/empty outputs are ignored;
    - at least one trace anchor (``task_id`` or ``turn_id``) is required, so
      anonymous direct calls do not fill the operator queue with noise.
    """
    body = dict(context or {})
    if body.get("queue_subagent_review") is False:
        return _skipped("disabled")
    if not isinstance(result, dict):
        return _skipped("invalid_result")
    if not bool(result.get("success", result.get("ok", False))):
        return _skipped("not_successful")

    output = _clean(result.get("output"), limit=1200)
    if not output:
        return _skipped("empty_output")

    trace = _trace_context(body, session)
    if not trace["task_id"] and not trace["turn_id"]:
        return _skipped("missing_trace_anchor")

    review = _review_from_subagent(
        agent_id=agent_id,
        role=role,
        prompt=prompt,
        output=output,
        result=result,
        trace=trace,
    )
    queue = review_queue or ReviewQueue(
        Path(review_queue_path) if review_queue_path is not None else _path_from_context(body)
    )
    return {"queued": True, "queue": queue.add_from_task_run_review(review)}


def _review_from_subagent(
    *,
    agent_id: str,
    role: str,
    prompt: str,
    output: str,
    result: dict[str, Any],
    trace: dict[str, str],
) -> dict[str, Any]:
    clean_role = _clean(role or agent_id or "subagent", limit=80)
    clean_agent = _clean(agent_id or clean_role, limit=80)
    clean_prompt = _clean(prompt, limit=360)
    title = f"Subagent {clean_role} result"
    if clean_prompt:
        title = f"{title}: {clean_prompt[:96]}"
    text = (
        f"When the {clean_role} subagent handled this request, it produced a "
        f"potentially reusable result.\n\nRequest: {clean_prompt}\n\nResult: {output}"
    )
    return {
        "status": "completed" if bool(result.get("success", result.get("ok"))) else "failed",
        "task_id": trace["task_id"],
        "thread_id": trace["thread_id"],
        "turn_id": trace["turn_id"],
        "agent_id": clean_agent,
        "replay": {
            "schema": "echo.subagent_review.v1",
            "case_id": trace["task_id"] or trace["turn_id"],
            "fingerprint": trace["fingerprint"],
            "replayable": bool(trace["task_id"] or trace["turn_id"]),
            "step_count": int(result.get("iteration_count") or 0),
        },
        "learning_candidates": [
            {
                "kind": "subagent_output",
                "priority": _priority(result),
                "memory_bucket": "experience",
                "title": title,
                "text": text,
                "subagent": {
                    "agent_id": clean_agent,
                    "role": clean_role,
                    "codename": _clean(result.get("codename"), limit=80),
                    "iteration_count": int(result.get("iteration_count") or 0),
                    "files_touched": [
                        _clean(path, limit=240)
                        for path in (result.get("files_touched") or [])
                        if _clean(path, limit=240)
                    ],
                },
            }
        ],
    }


def _trace_context(context: dict[str, Any], session: Any) -> dict[str, str]:
    meta = getattr(session, "metadata", None)
    if not isinstance(meta, dict):
        meta = {}
    task_id = (
        context.get("task_id")
        or context.get("task_run_id")
        or meta.get("task_id")
        or meta.get("task_run_id")
        or ""
    )
    turn_id = context.get("turn_id") or getattr(session, "turn_id", None) or ""
    thread_id = (
        context.get("thread_id")
        or context.get("caller_thread_id")
        or getattr(session, "thread_id", None)
        or getattr(session, "conversation_id", None)
        or ""
    )
    parent_agent_id = context.get("parent_agent_id") or getattr(session, "agent_id", None) or ""
    return {
        "task_id": _clean(task_id, limit=120),
        "turn_id": _clean(turn_id, limit=120),
        "thread_id": _clean(thread_id, limit=120),
        "parent_agent_id": _clean(parent_agent_id, limit=120),
        "fingerprint": _clean(
            context.get("trace_fingerprint")
            or "|".join(str(part or "") for part in (thread_id, turn_id, task_id, parent_agent_id)),
            limit=240,
        ),
    }


def _path_from_context(context: dict[str, Any]) -> Path:
    raw = context.get("review_queue_path")
    if raw:
        return Path(str(raw))
    return app_paths().review_queue_path


def _priority(result: dict[str, Any]) -> str:
    if result.get("files_touched"):
        return "P1"
    rounds = int(result.get("iteration_count") or 0)
    return "P1" if rounds >= 3 else "P2"


def _clean(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text[:limit].rstrip()


def _skipped(reason: str) -> dict[str, Any]:
    return {"queued": False, "reason": reason}


__all__ = ["queue_subagent_review_candidate"]
