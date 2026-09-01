from __future__ import annotations

from typing import Any

_VALID_TARGET_MODES = frozenset({"chat", "team", "code"})


def _exit_plan_mode(
    plan: str,
    *,
    confirm: bool = False,
    new_mode: str = "chat",
    session: Any = None,
    **_kw: Any,
) -> dict[str, Any]:
    """Transition the current thread from plan → {chat, team, code}.

    Mid-turn behaviour (preferred path)
    -----------------------------------

    When a live ``ApprovalProvider`` is wired onto the session
    (``session.metadata["_approval_provider"]``), this handler interrupts
    the planning turn, blocks on an ``item/planMode/exitRequest`` round
    trip, and — on user approval — flips a session metadata flag
    ``_plan_mode_exit_approved`` so the surrounding react_loop can
    re-enable tools and continue execution under the SAME turn id.

    If no provider is reachable (headless tests, batch runs), the
    handler falls back to the legacy "end turn, await new turn" payload
    so the existing front-end can still drive the transition by
    submitting a new turn with ``planning_mode=false``.

    Parameters
    ----------
    plan :
        The executable plan the agent wants to run. Passed through
        to the UI / journal · not interpreted here.
    confirm :
        Must be ``True`` explicitly · guards against the agent
        half-heartedly sending an exit request. Mirrors the
        "are you sure" affordance of plan-mode confirmation.
    new_mode :
        Which tier to land in · one of ``chat`` / ``team`` / ``code``.
        ``plan`` is rejected (would be a no-op). Unknown modes reject
        too · caller must pick an explicit tier.
    session :
        Current Session (injected by executor). Mutates
        ``session.metadata["mode"]`` so the REST of this turn sees
        the new tier. Next-turn persistence is the client's
        responsibility (it reads the return value and sets
        ``body.context.mode`` on the next request).

    Returns
    -------
    dict describing the transition · the shape the UI / router
    react to. Contains ``mode_transitioned: True`` on success.
    """
    if not confirm:
        return {
            "mode_transitioned": False,
            "reason": "confirm=False · pass confirm=True to actually exit",
            "plan": plan,
        }
    if new_mode not in _VALID_TARGET_MODES:
        return {
            "mode_transitioned": False,
            "reason": (
                f"new_mode {new_mode!r} not valid · must be one of {sorted(_VALID_TARGET_MODES)}"
            ),
            "plan": plan,
        }

    # ── Mid-turn approval bridge ──────────────────────────────
    # If a live ApprovalProvider is wired through the session, ask the
    # user RIGHT NOW (same turn). On approval, flip the metadata flag so
    # react_loop's per-iteration check picks it up and re-enables tools.
    active_session = session
    if active_session is None:
        try:
            from runtime.platform.process.session import current_session

            active_session = current_session()
        except ImportError:  # noqa: BLE001 — platform layer optional in tests
            active_session = None

    metadata = (
        active_session.metadata
        if active_session is not None and active_session.metadata is not None
        else None
    )
    provider = metadata.get("_approval_provider") if metadata is not None else None

    if provider is None:
        # Headless / test / batch — no live channel. Caller falls back
        # to the legacy "end turn, await new turn" persist hint below.
        return _legacy_payload(plan, new_mode, active_session, error_type="unsupported")

    decision = _request_plan_exit(provider, plan, active_session)
    if decision is None:
        return {
            "ok": False,
            "approved": False,
            "reason": "approval_provider_error",
            "error_type": "transport",
            "plan": plan,
        }
    if not decision.approved:
        return {
            "ok": False,
            "approved": False,
            "reason": decision.reason or "user_denied",
            "plan": plan,
        }

    # Approved — flip metadata for react_loop AND apply the legacy
    # transition mutation so any same-turn skill that reads
    # ``session.metadata["mode"]`` sees the new tier immediately.
    metadata["_plan_mode_exit_approved"] = True
    current = metadata.get("mode", "plan")
    metadata["mode"] = new_mode

    _notify_hooks(plan, current, new_mode, active_session)

    return {
        "ok": True,
        "approved": True,
        "continue_in_same_turn": True,
        "mode_transitioned": True,
        "from": current,
        "to": new_mode,
        "plan": plan,
        "persist_hint": {"thread_metadata": {"mode": new_mode}},
    }


def _legacy_payload(
    plan: str,
    new_mode: str,
    session: Any,
    *,
    error_type: str | None = None,
) -> dict[str, Any]:
    """Legacy "end turn, await new turn" response shape."""
    current = "plan"
    if session is not None and session.metadata is not None:
        current = session.metadata.get("mode", "plan")
        session.metadata["mode"] = new_mode

    _notify_hooks(plan, current, new_mode, session)

    payload: dict[str, Any] = {
        "ok": False,
        "approved": False,
        "mode_transitioned": True,
        "from": current,
        "to": new_mode,
        "plan": plan,
        "persist_hint": {"thread_metadata": {"mode": new_mode}},
    }
    if error_type is not None:
        payload["error"] = "no_user_channel"
        payload["error_type"] = error_type
    return payload


def _request_plan_exit(provider: Any, plan: str, session: Any) -> Any:
    """Issue the plan-exit approval call and translate the response.

    Reuses the existing ``ApprovalProvider.request`` blocking interface
    — the provider already bridges sync↔async and knows how to talk to
    the realtime gateway.
    """
    try:
        from runtime.protocol.events import ServerMethod
        from runtime.safety.approval.approval_gate import ApprovalRequest
    except ImportError:  # noqa: BLE001 — tolerant in stripped test envs
        return None

    thread_id = getattr(session, "thread_id", "") or ""
    turn_id = getattr(session, "turn_id", "") or ""
    args_preview = (plan or "")[:500]
    detail = (
        ServerMethod.PLAN_MODE_EXIT_REQUEST.value
        if hasattr(ServerMethod, "PLAN_MODE_EXIT_REQUEST")
        else "item/planMode/exitRequest"
    )
    req = ApprovalRequest(
        thread_id=thread_id,
        tool_name="exit_plan_mode",
        tool_call_id=f"plan-exit-{turn_id}",
        args_preview=args_preview,
        detail=detail,
    )
    try:
        return provider.request(req, timeout=600.0)
    except Exception:  # noqa: BLE001 — provider-level errors mean denial
        return None


def _notify_hooks(plan: str, current: str, new_mode: str, session: Any) -> None:
    """Best-effort audit notification."""
    try:
        from runtime.safety.hooks.runner import dispatch_notification

        dispatch_notification(
            kind="plan_mode_exit",
            details={
                "from": current,
                "to": new_mode,
                "thread_id": (getattr(session, "thread_id", "") if session is not None else ""),
                "plan_preview": (plan or "")[:200],
            },
            session=session,
        )
    except (ImportError, AttributeError):  # noqa: BLE001
        pass


def register_plan_mode_skill(registry: Any) -> None:
    """Add ``exit_plan_mode`` to the given SkillRegistry.

    Called from the atomic-base registration path · so every agent
    gets this skill regardless of its tool-registry profile. Plan
    mode wouldn't work if some agents couldn't exit it.
    """
    from runtime.execution.suckers import Skill
    from runtime.execution.suckers.testing import (
        SkillExpect,
        SkillTestCase,
    )

    registry.register(
        Skill(
            name="exit_plan_mode",
            description=(
                "Transition the current thread from plan mode to a "
                "write-capable tier (chat/team/code) after a plan has "
                "been confirmed."
            ),
            affinity=["meta", "mode"],
            cost_profile="low",
            trusted_source="skill://public/exit_plan_mode",
            handler=_exit_plan_mode,
            tests=[
                SkillTestCase(
                    name="confirm_false_no_op",
                    tier="golden",
                    args={"plan": "demo", "confirm": False},
                    expect=SkillExpect(schema_keys=["mode_transitioned"]),
                    custom_predicate=lambda r: (
                        isinstance(r, dict) and r.get("mode_transitioned") is False
                    ),
                ),
                SkillTestCase(
                    name="valid_transition",
                    tier="golden",
                    args={
                        "plan": "write hello.txt",
                        "confirm": True,
                        "new_mode": "chat",
                    },
                    expect=SkillExpect(
                        schema_keys=["mode_transitioned", "from", "to"],
                    ),
                    custom_predicate=lambda r: (
                        isinstance(r, dict)
                        and r.get("mode_transitioned") is True
                        and r.get("to") == "chat"
                    ),
                ),
                SkillTestCase(
                    name="invalid_new_mode_rejected",
                    tier="golden",
                    args={
                        "plan": "x",
                        "confirm": True,
                        "new_mode": "galaxy_brain",
                    },
                    expect=SkillExpect(schema_keys=["mode_transitioned"]),
                    custom_predicate=lambda r: (
                        isinstance(r, dict) and r.get("mode_transitioned") is False
                    ),
                ),
            ],
        )
    )


__all__ = ["register_plan_mode_skill"]
