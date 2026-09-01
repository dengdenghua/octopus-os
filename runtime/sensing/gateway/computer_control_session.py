"""Control-session bookkeeping and activity/replay logging for the
computer-automation router.

Split out of the former ~1994-line computer_router.py. Wraps every
desktop-automation call in a ``ControlSessionStore`` record (session →
action → evidence), and keeps the router's own bounded in-memory activity
log (``state.activity``, capped at 500 entries) that powers the
status/activity endpoints and replay-case queueing.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from runtime.memory.learning.review_queue import ReviewQueue
from runtime.platform.process.paths import app_paths

from .computer_lease import _lease_from_body, _public_lease
from .computer_router_state import _PENDING_TTL_SECONDS, ComputerRouterState


def _control_session_id(body: dict[str, Any] | None) -> str:
    if not isinstance(body, dict):
        return ""
    return str(body.get("control_session_id") or body.get("controlSessionId") or "").strip()


def _ensure_control_session(
    state: ComputerRouterState,
    body: dict[str, Any] | None,
    owner: dict[str, str] | None = None,
    *,
    status: str = "idle",
    metadata: dict[str, Any] | None = None,
) -> str:
    session_id = _control_session_id(body)
    if not session_id:
        return ""
    owner = owner or _lease_from_body(body)
    try:
        if state.control_sessions.get_session(session_id) is not None:
            return session_id
        state.control_sessions.upsert_session(
            session_id=session_id,
            owner_id=str(owner.get("owner_id") or "computer-agent"),
            owner_label=str(owner.get("owner_label") or "Computer Agent"),
            surface="computer",
            target_id="local-pc",
            status=status,
            metadata={
                "source": "computer_router",
                **(metadata or {}),
            },
        )
    except Exception:  # noqa: BLE001 - control evidence must not block the safe preview chain
        return ""
    return session_id


def _record_control_action(
    state: ComputerRouterState,
    body: dict[str, Any] | None,
    *,
    action_id: str | None = None,
    action_type: str,
    descriptor: dict[str, Any],
    status: str = "queued",
    owner: dict[str, str] | None = None,
) -> str:
    session_id = _ensure_control_session(state, body, owner, status="running")
    if not session_id:
        return ""
    try:
        action = state.control_sessions.append_action(
            session_id,
            action_id=action_id,
            action_type=action_type,
            descriptor=descriptor,
            status=status,
            surface="computer",
            target_id="local-pc",
        )
        action_id = str(action.get("action_id") or "")
    except Exception:  # noqa: BLE001
        return ""
    _sync_escalation(state, action_id, status)
    return action_id


def _update_control_action(
    state: ComputerRouterState,
    body: dict[str, Any] | None,
    action_id: str,
    *,
    status: str,
    result: dict[str, Any] | None = None,
    error: str = "",
) -> None:
    session_id = _control_session_id(body)
    if not session_id or not action_id:
        return
    try:
        state.control_sessions.update_action(
            session_id,
            action_id,
            status=status,
            result=result or {},
            error=error,
        )
        _sync_escalation(state, action_id, status)
    except Exception:  # noqa: BLE001
        return


def _sync_escalation(
    state: ComputerRouterState,
    action_id: str,
    status: str,
) -> None:
    """Side-channel bridge to the waiting-user watchdog.

    Never raises: escalation is best-effort and must not break control-session
    bookkeeping or the approval path.
    """
    watchdog = state.escalation
    if watchdog is None or not action_id:
        return
    try:
        if status == "waiting_user":
            watchdog.on_waiting(action_id)
        else:
            watchdog.on_resolved(action_id)
        watchdog.poll()
    except Exception:  # noqa: BLE001 - escalation is best-effort only
        return


def _record_control_evidence(
    state: ComputerRouterState,
    body: dict[str, Any] | None,
    *,
    action_id: str = "",
    kind: str,
    action: str,
    ok: bool | None = None,
    summary: str = "",
    detail: dict[str, Any] | None = None,
    owner: dict[str, str] | None = None,
) -> None:
    session_id = _ensure_control_session(state, body, owner)
    if not session_id:
        return
    try:
        state.control_sessions.append_evidence(
            session_id,
            action_id=action_id,
            kind=kind,
            action=action,
            ok=ok,
            summary=summary,
            detail=detail or {},
        )
    except Exception:  # noqa: BLE001
        return


def _cleanup_pending(state: ComputerRouterState) -> None:
    now = time.time()
    expired = [
        token
        for token, item in state.pending.items()
        if now - float(item.get("created_at") or 0) > _PENDING_TTL_SECONDS
    ]
    for token in expired:
        state.pending.pop(token, None)


def _record_activity(
    state: ComputerRouterState,
    event: str,
    *,
    ok: bool = True,
    action: dict[str, Any] | None = None,
    token: str = "",
    risk: dict[str, str] | None = None,
    lease_state: dict[str, Any] | None = None,
    error: str = "",
    detail: dict[str, Any] | None = None,
    proof: dict[str, Any] | None = None,
) -> None:
    state.activity.append(
        {
            "id": uuid.uuid4().hex,
            "event": event,
            "ok": ok,
            "action": action or {},
            "token": token,
            "risk": risk or {},
            "lease": lease_state or _public_lease(state),
            "error": error,
            "detail": detail or {},
            "proof": proof or {},
            "created_at": time.time(),
        }
    )
    if len(state.activity) > 500:
        del state.activity[:-500]


def _queue_activity_replay_case(
    replay_case: dict[str, Any],
    *,
    reason: str = "",
    priority: str = "",
) -> dict[str, Any]:
    raw_items = replay_case.get("items")
    items = raw_items if isinstance(raw_items, list) else []
    last_activity = replay_case.get("last_activity")
    last_activity = last_activity if isinstance(last_activity, dict) else {}
    has_failure = any(isinstance(item, dict) and item.get("ok") is False for item in items)
    chosen_priority = priority or ("P0" if has_failure else "P1")
    last_event = str(last_activity.get("event") or "no activity")
    last_action = last_activity.get("action")
    last_action = last_action if isinstance(last_action, dict) else {}
    case_id = str(replay_case.get("case_id") or "")
    fingerprint = str(replay_case.get("fingerprint") or "")
    text = (
        f"Computer automation replay case `{case_id}` captured for operator review.\n"
        f"Last event: {last_event}; action: {last_action.get('action', 'none')}.\n"
        f"Activity count: {replay_case.get('activity_count', 0)}; "
        f"pending previews: {replay_case.get('pending_count', 0)}."
    )
    if reason:
        text += f"\nReason: {reason[:500]}"
    queue = ReviewQueue(app_paths().review_queue_path)
    return queue.upsert_item(
        source="computer_activity_replay",
        source_kind="browser_desktop_replay",
        candidate_kind="computer_activity_replay_case",
        priority=chosen_priority,
        target_bucket="browser_desktop_replay",
        title="Review computer automation replay case",
        text=text,
        metadata={
            "schema": replay_case.get("schema"),
            "case_id": case_id,
            "fingerprint": fingerprint,
            "replay_ready": bool(replay_case.get("replay_ready")),
            "activity_count": replay_case.get("activity_count"),
            "pending_count": replay_case.get("pending_count"),
            "lease": replay_case.get("lease"),
            "last_activity": last_activity,
            "proof": last_activity.get("proof")
            if isinstance(last_activity.get("proof"), dict)
            else {},
        },
        tags=[
            "computer",
            "desktop",
            "replay_case",
            "review_queue",
            "operator_review",
        ],
    )


def _queue_uia_replay_assertion(
    action: dict[str, Any],
    assertion: dict[str, Any],
) -> dict[str, Any]:
    _assertion_match = assertion.get("matched_control")
    _action_match = action.get("matched_control")
    matched = (
        _assertion_match
        if isinstance(_assertion_match, dict)
        else _action_match
        if isinstance(_action_match, dict)
        else {}
    )
    label = str(matched.get("name") or matched.get("automation_id") or "desktop control")
    reason = str(assertion.get("reason") or assertion.get("error") or "UIA replay assertion failed")
    queue = ReviewQueue(app_paths().review_queue_path)
    return queue.upsert_item(
        source="computer_uia_replay_assertion",
        source_kind="browser_desktop_replay",
        candidate_kind="computer_uia_replay_assertion",
        priority="P0",
        target_bucket="browser_desktop_replay",
        title=f"Review desktop UIA replay assertion: {label}",
        text=(f"Desktop UIA replay assertion failed for `{label}`.\nReason: {reason[:500]}."),
        metadata={
            "schema": "echo.computer_uia_replay_assertion_queue.v1",
            "trace_id": assertion.get("trace_id"),
            "action": action,
            "replay_assertion": assertion,
            "source_trace": assertion.get("source_trace")
            if isinstance(assertion.get("source_trace"), dict)
            else {},
            "matched_control": matched,
        },
        tags=[
            "computer",
            "desktop",
            "uia",
            "replay_assertion",
            "review_queue",
        ],
    )


__all__: list[str] = []
