"""Serve-side wiring for team-room *twin speaking*.

The team-rooms gateway (``runtime.sensing.gateway.team_rooms_router``) is an
import leaf — it must not reach into the model/execution layers. So the actual
"ask an LLM for a line in this teammate's voice" logic lives here, in the
composition layer (``platform.ui``), and is injected into the router as a
plain callback (``twin_responder``).

``make_twin_responder(stack)`` returns that callback, or ``None`` when no model
router is available (graceful no-op — twins simply won't speak). The callback
looks up the bound twin agent in ``team.members`` by name, then asks the
planner's :class:`ModelRouter` for a short first-person line, given the room's
recent transcript.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

_LOG = logging.getLogger("echo.team.twin")

# Keep twin lines short — this is a chat room, not an essay. A small token
# budget also caps the cost of an idle room full of twins.
_MAX_TOKENS = 256
_TEMPERATURE = 0.7
# How much of the transcript window to feed the model (the room keeps ~20).
_TRANSCRIPT_TAIL = 12
_DEFAULT_MODEL = "claude-sonnet-4-6"


def make_twin_responder(stack: Any) -> Any:
    """Build the team-room ``twin_responder`` callback, or ``None`` when no
    model router is wired (so the gateway disables twin speaking).

    ``stack.planner.router`` is the shared :class:`ModelRouter` (same instance
    the ReAct loop and account fallback use); we capture it once. The returned
    coroutine resolves the bound twin agent in ``team.members`` and delegates
    to :func:`_generate_twin_line`."""
    planner = getattr(stack, "planner", None) if stack is not None else None
    router = getattr(planner, "router", None)
    if router is None:
        return None
    default_model = getattr(planner, "planner_model", None) or _DEFAULT_MODEL

    async def _twin_responder(
        team: Any, participant: Any, transcript: list[dict[str, Any]]
    ) -> str | None:
        twin_agent_id = (getattr(participant, "twin_agent_id", None) or "").strip()
        if not twin_agent_id:
            return None
        member = next(
            (
                m
                for m in getattr(team, "members", []) or []
                if getattr(m, "name", None) == twin_agent_id
            ),
            None,
        )
        if member is None:
            _LOG.debug("twin agent %r not in team members — staying silent", twin_agent_id)
            return None
        return await _generate_twin_line(router, default_model, member, participant, transcript)

    return _twin_responder


def _format_transcript(transcript: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for entry in transcript[-_TRANSCRIPT_TAIL:]:
        who = entry.get("display_name") or entry.get("participant_id") or "?"
        what = (entry.get("text") or "").strip()
        if what:
            lines.append(f"{who}: {what}")
    return "\n".join(lines) if lines else "(no messages yet)"


async def _generate_twin_line(
    router: Any,
    default_model: str,
    member: Any,
    participant: Any,
    transcript: list[dict[str, Any]],
) -> str | None:
    """Ask the model router for one short line in the bound teammate's voice.
    Returns the trimmed text, or ``None`` on an empty reply or any error
    (best-effort: a twin that can't generate just stays quiet). The blocking
    ``router.call`` runs in a worker thread so the event loop keeps serving."""
    # Lazy import · platform.models is the always-allowed shared base, but we
    # still defer it so importing this module stays cheap for the test path.
    from runtime.platform.models.llm import Message, ModelRequest

    persona = (getattr(member, "description", "") or "").strip() or "a helpful teammate"
    speaking_as = (
        getattr(participant, "display_name", None)
        or getattr(member, "display_name", None)
        or getattr(member, "name", "")
        or "the teammate"
    )
    model = getattr(member, "model", None) or default_model

    system = (
        f"You are the digital twin standing in for {speaking_as} in a live "
        f"team chat room. Persona / brief: {persona}. Speak in the first "
        f"person as {speaking_as}, in one or two short sentences — this is a "
        "chat room, not an essay. Match the language of the conversation. Do "
        "not announce that you are an AI or a twin; just contribute as the "
        "teammate would. If you have nothing useful to add, reply with an "
        "empty message."
    )
    user = f"Recent room conversation:\n{_format_transcript(transcript)}\n\nReply as {speaking_as}:"

    req = ModelRequest(
        model=model,
        messages=[
            Message(role="system", content=system),
            Message(role="user", content=user),
        ],
        max_tokens=_MAX_TOKENS,
        temperature=_TEMPERATURE,
    )
    try:
        resp = await asyncio.to_thread(router.call, req)
    except Exception as exc:  # noqa: BLE001 — twin generation is best-effort
        _LOG.debug("twin line generation failed (%s) — staying silent", exc)
        return None
    text = (getattr(resp, "text", "") or "").strip()
    return text or None


__all__ = ["make_twin_responder"]
