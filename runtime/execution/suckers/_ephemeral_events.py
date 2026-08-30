"""Event emission helpers for ephemeral sub-agent runs.

Split out from ``ephemeral_runner.py`` to keep that module under the
god-file line cap. Pure structural move — no behavior changes.

Contains:
    * ``_safe_ctx_emit`` — fire-and-forget wrapper around a caller-supplied
      event emitter (silently no-ops on ``None`` / exceptions).
    * ``_emit_sub_tool_event`` — best-effort push of a sub-agent tool
      event (start/end) to the active stream queue AND the genome journal.
    * ``_emit_subagent_lifecycle_event`` — best-effort push of a sub-agent
      lifecycle event (spawned/finished) to the genome journal.
    * ``_emit_sub_user_message`` — best-effort journal of a session's
      user prompt as a ``user/message`` row correlated to its session.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

from runtime.protocol.text_limits import MAX_SUBAGENT_ANSWER_CHARS

__all__ = [
    "_emit_sub_text_delta",
    "_emit_sub_tool_event",
    "_emit_subagent_lifecycle_event",
    "_emit_sub_session_summary",
    "_emit_sub_user_message",
    "_safe_ctx_emit",
]


def _safe_ctx_emit(emitter: Any, event: dict) -> None:
    """Fire-and-forget call to a caller-supplied event emitter.

    Silently no-ops when ``emitter`` is ``None`` or raises. The runner
    must never crash because of a buggy emitter callback.
    """
    if emitter is None:
        return
    with contextlib.suppress(Exception):
        emitter(event)


def _publish_to_bus(type: str, payload: dict) -> None:
    """Fire-and-forget mirror of a sub-agent event onto the typed event bus.

    Best-effort: no session / no coordination root → silent no-op. Telemetry
    loss never breaks the runner. The bus is the substrate the Workbench
    subscribes to for an independent, full-fidelity stream of a sub-agent
    thread (see ``runtime.execution.subagents.event_bus``).
    """
    with contextlib.suppress(Exception):
        from runtime.execution.subagents.event_bus import publish_subagent_event

        publish_subagent_event(type, payload)


def _current_subagent_codename() -> str:
    """Read the current child's codename off the bound run Session.

    The bridge stamps ``subagent_codename`` on the child's session metadata
    so the typed event bus (which keys lanes by codename) can attribute tool /
    conclude / fail events to the right sub-agent thread — even when several
    parallel children share the same role. Empty when unset (parent turns,
    one-shot children) — the lane then falls back to the role, which is
    graceful, never incorrect."""
    try:
        from runtime.platform.process.session import current_session

        sess = current_session()
    except (ImportError, TypeError, AttributeError, OSError):  # noqa: BLE001
        return ""
    if sess is None:
        return ""
    meta = getattr(sess, "metadata", None) or {}
    if not isinstance(meta, dict):
        return ""
    codename = meta.get("subagent_codename") or ""
    return str(codename)


def _emit_sub_text_delta(
    role_id: str,
    round: int,
    delta: str,
    *,
    session_id: str = "",
    emitter: Any = None,
) -> None:
    """Best-effort forward of one streamed role-prose chunk.

    Pushes the ``sub_text_delta`` event to the caller-supplied
    ``emitter`` (parent gateway render path) AND mirrors it onto the
    genome journal as a ``SubTextDeltaEvent`` row, so the sub-agent's
    streaming prose is reconstructable from the log alone (dsh
    session-log invariant: model-visible means logged). The parent
    only stitches role text into prompts after the fact; journaling
    each chunk keeps the audit trail at the same fidelity as the SSE
    stream.

    Silently no-ops when no session / journal is bound — unit tests
    calling the runner directly stay green, and telemetry loss never
    breaks the runner.
    """
    _safe_ctx_emit(
        emitter,
        {
            "type": "sub_text_delta",
            "agent_id": role_id,
            "round": round,
            "delta": delta,
        },
    )
    try:
        from runtime.platform.process.session import current_session

        sess = current_session()
    except (ImportError, TypeError, AttributeError, OSError):  # noqa: BLE001
        return
    if sess is None:
        return
    meta = getattr(sess, "metadata", None) or {}
    if not isinstance(meta, dict):
        return
    journal = meta.get("journal")
    if journal is None:
        try:
            stack = meta.get("stack")
            if stack is not None:
                journal = getattr(stack, "journal", None)
        except (AttributeError, TypeError):  # noqa: BLE001
            journal = None
    if journal is None:
        return
    try:
        from runtime.memory.journal import SubTextDeltaEvent

        journal.write(
            SubTextDeltaEvent(
                task_id=meta.get("task_id"),
                session_id=session_id,
                agent_id=str(meta.get("subagent_agent_id") or role_id),
                codename=str(meta.get("subagent_codename") or ""),
                avatar=str(meta.get("subagent_avatar") or ""),
                role_id=role_id,
                round=int(round),
                delta=delta,
                parent_tool_use_id=meta.get("_active_parent_tool_use_id") or None,
            )
        )
    except (OSError, TypeError, ValueError):  # noqa: BLE001
        # Mirroring is best-effort; never break the runner.
        pass


def _emit_sub_user_message(session_id: str, text: str) -> None:
    """Best-effort journal of one sub-agent session's user prompt.

    Records the durable prompt a continuable session was started with as a
    ``user/message`` row correlated to ``session_id``, so a session's
    surface user lane is reconstructable from the log alone — the dsh
    session-log invariant. Silently no-ops when no journal is bound or the
    write fails; telemetry loss never breaks the runner. One-shot / remote
    children pass an empty ``session_id`` and are skipped.
    """
    if not session_id:
        return
    try:
        from runtime.platform.process.session import current_session

        sess = current_session()
    except (ImportError, TypeError, AttributeError, OSError):  # noqa: BLE001
        return
    if sess is None:
        return
    meta = getattr(sess, "metadata", None) or {}
    if not isinstance(meta, dict):
        return
    journal = meta.get("journal")
    if journal is None:
        try:
            stack = meta.get("stack")
            if stack is not None:
                journal = getattr(stack, "journal", None)
        except (AttributeError, TypeError):  # noqa: BLE001
            journal = None
    if journal is None:
        return
    try:
        from runtime.memory.journal._journal_models import UserMessageEvent

        journal.write(
            UserMessageEvent(
                task_id=meta.get("task_id"),
                session_id=session_id,
                text=text,
            )
        )
    except (OSError, TypeError, ValueError):  # noqa: BLE001
        # Mirroring is best-effort; never break the runner.
        pass


def _emit_sub_session_summary(
    session_id: str,
    *,
    agent_id: str = "",
    rounds: int = 0,
    success: bool = True,
    error: str = "",
) -> None:
    """Best-effort journal of one sub-agent session turn's completion.

    Writes a ``SubSessionSummaryEvent`` row (rounds spent, success, error)
    correlated to ``session_id`` so a resume path can report the session's
    effort/outcome without replaying every chunk — the dsh session-log
    invariant extended to the turn's outcome. Silently no-ops when no
    journal is bound or the write fails; one-shot/remote children pass an
    empty ``session_id`` and are skipped.
    """
    if not session_id:
        return
    try:
        from runtime.platform.process.session import current_session

        sess = current_session()
    except (ImportError, TypeError, AttributeError, OSError):  # noqa: BLE001
        return
    if sess is None:
        return
    meta = getattr(sess, "metadata", None) or {}
    if not isinstance(meta, dict):
        return
    journal = meta.get("journal")
    if journal is None:
        try:
            stack = meta.get("stack")
            if stack is not None:
                journal = getattr(stack, "journal", None)
        except (AttributeError, TypeError):  # noqa: BLE001
            journal = None
    if journal is None:
        return
    try:
        from runtime.memory.journal._journal_models import SubSessionSummaryEvent

        # Sum the usage rows attributed to this session (dsh session-log
        # invariant extended to spend): the bridge scoped the child's react
        # loop so its ``token_usage`` rows carry this ``session_id``. Rows
        # with no attribution (parent turns / one-shot children) never match.
        _in_tok = 0
        _out_tok = 0
        _cost = 0.0
        for _event in journal.read_all():
            if (
                getattr(_event, "event_type", "") == "token_usage"
                and (_event.session_id or "") == session_id
            ):
                _in_tok += int(_event.input_tokens or 0)
                _out_tok += int(_event.output_tokens or 0)
                _cost += float(_event.cost_usd or 0.0)

        journal.write(
            SubSessionSummaryEvent(
                task_id=meta.get("task_id"),
                session_id=session_id,
                agent_id=agent_id,
                rounds=int(rounds or 0),
                success=bool(success),
                error=error or "",
                input_tokens=_in_tok,
                output_tokens=_out_tok,
                cost_usd=round(_cost, 6),
            )
        )
    except (OSError, TypeError, ValueError):  # noqa: BLE001
        # Mirroring is best-effort; never break the runner.
        pass


def _emit_sub_tool_event(
    kind: str,
    *,
    role_id: str,
    tool_call: Any,
    iteration: int,
    output: str | None = None,
    is_error: bool = False,
    duration_ms: int | None = None,
) -> None:
    """Best-effort push of a sub-agent tool event to the active stream
    queue so the frontend LiveToolTimeline can nest it under the
    parent's ``call_agent_parallel`` / ``call_agent`` tool_use row.

    Wiring:
        * ``tool_bridge.stream_agentic_fallback`` stashes the stream
          queue on ``session.metadata["sub_tool_event_queue"]``
          AND the currently-running parent tool_use id on
          ``session.metadata["_active_parent_tool_use_id"]`` before
          invoking each handler.
        * Inside the sub-agent handler → ephemeral runner → here:
          we look up both and push a ``(kind, payload, None)`` tuple.
        * The active realtime bridge drains the tuple and emits the
          corresponding tool event.

    Silently no-ops when:
        * No session is bound (unit tests calling the runner directly)
        * No queue was stashed (older bootstrap paths)
        * Queue put fails (full / closed)

    The sub-agent keeps running regardless · losing telemetry beats
    deadlocking the worker thread.
    """
    try:
        from runtime.platform.process.session import current_session

        sess = current_session()
    except (ImportError, TypeError, AttributeError, OSError):  # noqa: BLE001
        return
    if sess is None:
        return
    meta = getattr(sess, "metadata", None) or {}
    if not isinstance(meta, dict):
        return
    q = meta.get("sub_tool_event_queue")
    parent_id = meta.get("_active_parent_tool_use_id") or ""
    payload: dict[str, Any] = {
        "id": getattr(tool_call, "id", "") or "",
        "name": getattr(tool_call, "name", "") or "",
        "input": getattr(tool_call, "input", {}) or {},
        "iteration": iteration,
        "parent_tool_use_id": parent_id,
        "sub_agent_role": role_id,
    }
    if kind == "sub_tool_end":
        # Truncate output preview · same 200-char cap as parent
        # loop's tool_end event so the SSE frame stays small.
        if output is not None:
            payload["output"] = str(output)[:200]
        payload["is_error"] = bool(is_error)
        payload["status"] = "error" if is_error else "success"
        if duration_ms is not None:
            payload["duration_ms"] = int(duration_ms)
    _publish_to_bus(
        kind,
        {
            "role": role_id,
            "codename": _current_subagent_codename(),
            "iteration": iteration,
            "tool": payload.get("name") or "",
            "tool_call_id": payload.get("id") or "",
            "parent_tool_use_id": payload.get("parent_tool_use_id") or "",
            "input": payload.get("input") if kind == "sub_tool_start" else None,
            "status": payload.get("status") or "",
            "duration_ms": payload.get("duration_ms"),
            "output_preview": payload.get("output"),
            "error": payload.get("output") if payload.get("is_error") else "",
        },
    )
    if q is not None:
        with contextlib.suppress(Exception):
            q.put_nowait((kind, payload, None))

    # ── Mirror to journal as well ───────────────────────────
    # The above queue path requires the SSE pump to have stashed a
    # queue in session.metadata — which only the agentic-fallback
    # code path does. Most production turns route through the
    # OpenAI-gateway worker which subscribes to the journal
    # instead. Writing a JournalEvent here surfaces sub-tool
    # progress on BOTH paths uniformly.
    journal = meta.get("journal")
    if journal is None:
        try:
            stack = meta.get("stack")
            if stack is not None:
                journal = getattr(stack, "journal", None)
        except (AttributeError, TypeError):  # noqa: BLE001
            journal = None
    if journal is None:
        return
    try:
        from runtime.memory.journal import (
            SubToolEndEvent,
            SubToolStartEvent,
        )

        task_id_obj = meta.get("task_id")
        if kind == "sub_tool_start":
            try:
                args_preview = json.dumps(
                    payload.get("input") or {},
                    ensure_ascii=False,
                    default=str,
                )[:1000]
            except (TypeError, ValueError):
                args_preview = str(payload.get("input") or "")[:1000]
            ev = SubToolStartEvent(
                task_id=task_id_obj,
                agent_id=str(meta.get("subagent_agent_id") or role_id),
                codename=str(meta.get("subagent_codename") or ""),
                avatar=str(meta.get("subagent_avatar") or ""),
                role_id=role_id,
                tool_call_id=str(getattr(tool_call, "id", "") or ""),
                tool_name=str(getattr(tool_call, "name", "") or ""),
                iteration=int(iteration),
                args_preview=args_preview,
                parent_tool_use_id=parent_id or None,
            )
        else:
            ev = SubToolEndEvent(
                task_id=task_id_obj,
                agent_id=str(meta.get("subagent_agent_id") or role_id),
                codename=str(meta.get("subagent_codename") or ""),
                avatar=str(meta.get("subagent_avatar") or ""),
                role_id=role_id,
                tool_call_id=str(getattr(tool_call, "id", "") or ""),
                tool_name=str(getattr(tool_call, "name", "") or ""),
                iteration=int(iteration),
                is_error=bool(is_error),
                duration_ms=int(duration_ms or 0),
                output_preview=(output or "")[:200] if output else "",
                parent_tool_use_id=parent_id or None,
            )
        journal.write(ev)
    except (OSError, TypeError, ValueError):  # noqa: BLE001
        # Mirroring is best-effort; never break the runner.
        pass


def _emit_subagent_lifecycle_event(
    kind: str,
    payload: dict[str, Any] | None,
    *,
    publish_bus: bool = True,
) -> None:
    """Best-effort push of a sub-agent lifecycle event to the genome
    journal so the realtime gateway / observability panel can render
    a sub-agent tile from the moment the agent spawns instead of
    waiting for its first ``sub_tool_*`` event.

    ``kind`` is ``"subagent_spawned"`` or ``"subagent_finished"``.
    ``payload`` mirrors the dict the bridge fires through its
    ``event_emitter`` (codename, avatar, role, prompt_preview, ok,
    duration_s, iteration_count, files_touched, error, status).

    Convention
    ----------
    Reuses the existing ``SubToolStartEvent`` / ``SubToolEndEvent``
    journal-event shape — same wire used by ``_emit_sub_tool_event``
    above — but stamps the ``tool_name`` with one of the
    ``ItemMarker`` magic strings (``__subagent_spawned__`` /
    ``__subagent_finished__``). Subscribers that don't care about
    lifecycle simply ignore the marker; the realtime gateway uses it
    to synthesise an ``McpToolCallItem`` the frontend's
    ``mcpItemToLiveEvent`` recognises and renders as a lifecycle tile.

    Silently no-ops when no session / journal is bound, so unit tests
    calling the bridge directly stay green. Empty / malformed
    payloads are tolerated — the helper coerces missing fields to
    safe defaults rather than raising.
    """
    import json as _json

    from runtime.protocol.items import ItemMarker

    payload = payload or {}
    try:
        from runtime.platform.process.session import current_session

        sess = current_session()
    except (ImportError, TypeError, AttributeError, OSError):  # noqa: BLE001
        return
    if sess is None:
        return
    meta = getattr(sess, "metadata", None) or {}
    if not isinstance(meta, dict):
        return
    journal = meta.get("journal")
    if journal is None:
        try:
            stack = meta.get("stack")
            if stack is not None:
                journal = getattr(stack, "journal", None)
        except (AttributeError, TypeError):  # noqa: BLE001
            journal = None
    if journal is None:
        return

    if publish_bus:
        # Mirror onto the typed event bus for direct callers of this helper.
        # The higher-level bridge opts out because it publishes with its
        # explicit caller session (and therefore the correct root thread).
        _bus_role = str(payload.get("role") or payload.get("agent_id") or "")
        if kind == "subagent_spawned":
            _publish_to_bus(
                "sub_started",
                {
                    "role": _bus_role,
                    "codename": payload.get("codename") or "",
                    "avatar": payload.get("avatar") or "",
                    "prompt_preview": (payload.get("prompt_preview") or "")[:200],
                    "started_at": payload.get("started_at"),
                },
            )
        elif kind == "subagent_finished":
            _bus_type = (
                "sub_concluded" if payload.get("ok") and not payload.get("error") else "sub_failed"
            )
            _publish_to_bus(
                _bus_type,
                {
                    "role": _bus_role,
                    "codename": (_current_subagent_codename() or payload.get("codename") or ""),
                    "ok": bool(payload.get("ok")),
                    "error": payload.get("error") or "",
                    "duration_s": payload.get("duration_s"),
                    "iteration_count": payload.get("iteration_count"),
                    "files_touched": payload.get("files_touched") or 0,
                    "status": payload.get("status") or "",
                    "output": payload.get("output") or "",
                },
            )
    role_id = str(payload.get("role") or payload.get("agent_id") or "")
    parent_id = payload.get("parent_tool_use_id") or meta.get("_active_parent_tool_use_id") or None
    task_id_obj = meta.get("task_id")
    try:
        args_preview = _json.dumps(
            {
                "codename": payload.get("codename"),
                "avatar": payload.get("avatar"),
                "role": payload.get("role"),
                "agent_id": payload.get("agent_id"),
                "requested_agent_id": payload.get("requested_agent_id"),
                "role_display_name": payload.get("role_display_name"),
                "role_description": payload.get("role_description"),
                "prompt_preview": payload.get("prompt_preview"),
                "use_cheap_model": payload.get("use_cheap_model"),
                "started_at": payload.get("started_at"),
            },
            ensure_ascii=False,
            default=str,
        )[:1000]
    except (TypeError, ValueError):
        args_preview = ""

    try:
        from runtime.memory.journal import (
            SubToolEndEvent,
            SubToolStartEvent,
        )

        if kind == "subagent_spawned":
            ev = SubToolStartEvent(
                task_id=task_id_obj,
                agent_id=str(payload.get("requested_agent_id") or payload.get("agent_id") or ""),
                codename=str(payload.get("codename") or ""),
                avatar=str(payload.get("avatar") or ""),
                role_id=role_id,
                tool_call_id=str(payload.get("agent_id") or ""),
                tool_name=ItemMarker.SUBAGENT_SPAWNED.value,
                iteration=0,
                args_preview=args_preview,
                parent_tool_use_id=parent_id,
            )
        elif kind == "subagent_finished":
            try:
                output_preview = _json.dumps(
                    {
                        "codename": payload.get("codename"),
                        "avatar": payload.get("avatar"),
                        "role": payload.get("role"),
                        "agent_id": payload.get("agent_id"),
                        "requested_agent_id": payload.get("requested_agent_id"),
                        "ok": payload.get("ok"),
                        "duration_s": payload.get("duration_s"),
                        "iteration_count": payload.get("iteration_count"),
                        "files_touched": payload.get("files_touched"),
                        "error": payload.get("error"),
                        "status": payload.get("status"),
                        "output": payload.get("output"),
                    },
                    ensure_ascii=False,
                    default=str,
                )[:MAX_SUBAGENT_ANSWER_CHARS]
            except (TypeError, ValueError):
                output_preview = ""
            ok = bool(payload.get("ok", True))
            duration_s = payload.get("duration_s") or 0
            try:
                duration_ms = int(float(duration_s) * 1000)
            except (TypeError, ValueError):
                duration_ms = 0
            ev = SubToolEndEvent(
                task_id=task_id_obj,
                agent_id=str(payload.get("requested_agent_id") or payload.get("agent_id") or ""),
                codename=str(payload.get("codename") or ""),
                avatar=str(payload.get("avatar") or ""),
                role_id=role_id,
                tool_call_id=str(payload.get("agent_id") or ""),
                tool_name=ItemMarker.SUBAGENT_FINISHED.value,
                iteration=int(payload.get("iteration_count") or 0),
                is_error=not ok,
                duration_ms=duration_ms,
                output_preview=output_preview,
                parent_tool_use_id=parent_id,
            )
        else:
            return
        journal.write(ev)
    except (OSError, TypeError, ValueError):  # noqa: BLE001
        # Lifecycle mirroring is best-effort; never break the run.
        pass
