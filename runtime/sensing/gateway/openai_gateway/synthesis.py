"""Final-answer synthesis for completed non-streaming gateway runs."""

from __future__ import annotations

import logging
from typing import Any

from runtime.sensing.gateway.openai_formatting import (
    summarize_step_for_stream as _summarize_step_for_stream,
)

from .context_manager import (
    _interaction_profile_prompt,
    _render_conversation_history,
    _runtime_soul_for_agent,
)
from .response_formatter import (
    _assistant_text_from_trajectory,
    _format_research_report_fallback,
    _is_research_report_context,
    _looks_like_complete_research_report,
)
from .stream_handler import _commit_direct_llm_cost


def synthesize_reply(
    stack: Any,
    *,
    goal: str,
    trajectory: Any,
    model: str | None = None,
    agent: Any = None,
    conversation_messages: list[dict[str, str]] | None = None,
    profile_memories: list[str] | None = None,
    user_context: dict[str, Any] | None = None,
    usage_out: dict[str, int] | None = None,
) -> str:
    wants_research_report = _is_research_report_context(goal, user_context)

    if not getattr(trajectory, "steps", None):
        return _assistant_text_from_trajectory(trajectory)

    router = getattr(stack.planner, "router", None)
    if router is None:
        if wants_research_report:
            return _format_research_report_fallback(
                goal=goal,
                trajectory=trajectory,
                user_context=user_context,
            )
        return _assistant_text_from_trajectory(trajectory)

    tool_summary = "\n".join(
        f"{index}. {_summarize_step_for_stream(step)}"
        for index, step in enumerate(trajectory.steps, 1)
    )
    tool_summary = tool_summary or "(none)"

    from runtime.sensing.model_router.models import Message, ModelRequest

    system_soul = (
        _runtime_soul_for_agent(agent)
        or "You are a helpful assistant. The user asked a question, and "
        "the system already ran the necessary tools to answer it. Read "
        "the tool outputs and respond in natural language, concise and "
        "to the point. Do NOT mention the tools by name. If the tool "
        "outputs are structured data (file lists, JSON, etc.), summarize "
        "them rather than pasting raw content. Use the user's language."
    )
    try:
        from runtime.core.cerebrum.llm_planner import _render_team_roster_section

        team_section = _render_team_roster_section(user_context or {})
    except (ImportError, AttributeError):
        team_section = ""
    if team_section:
        system_soul = f"{system_soul}\n\n{team_section}"
    interaction_profile = _interaction_profile_prompt(user_context)
    if interaction_profile:
        system_soul = f"{system_soul}\n\n{interaction_profile}"

    from runtime.memory.users.profile import render_profile_memories

    profile_section = render_profile_memories(profile_memories or [])
    profile_block = f"{profile_section}\n\n" if profile_section else ""
    history = _render_conversation_history(conversation_messages or [])
    history_block = f"Conversation history:\n{history}\n\n" if history else ""
    research_instruction = ""
    if wants_research_report:
        research_instruction = (
            "\n\nThe user is asking for a deep research style report. "
            "Do not return a tool log, JSON plan, or brief answer. "
            "Write a complete Markdown report in the user's language with these sections: "
            "title, executive summary, scope and method, key findings, evidence/sources, "
            "uncertainties and gaps, conclusion and recommendations. "
            "If evidence is thin, say that explicitly and label claims as preliminary."
        )

    synthesis_user = (
        f"{profile_block}{history_block}"
        f"Latest user question:\n{goal}\n\n"
        f"Tool outputs:\n{tool_summary}\n\n"
        "Write the final reply to the user based on the conversation and "
        f"these outputs.{research_instruction}"
    )
    effective_model = (
        model
        if model and model not in ("echo-agent", "")
        else getattr(stack.planner, "planner_model", None) or "echo-agent"
    )
    request = ModelRequest(
        model=effective_model,
        messages=[
            Message(role="system", content=system_soul),
            Message(role="user", content=synthesis_user),
        ],
        max_tokens=4096 if wants_research_report else 1024,
        temperature=0.3,
    )
    try:
        response = router.call(request)
        usage = {
            "input_tokens": int(getattr(response, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(response, "output_tokens", 0) or 0),
        }
        if usage_out is not None:
            usage_out.update(usage)
        _commit_direct_llm_cost(stack, usage, agent, reason="synthesize_reply")
        text = (response.text or "").strip()
        if text:
            from runtime.platform.process.session import current_session
            from runtime.platform.runtime_policy.identity_filter import filter_text

            filtered = filter_text(
                text,
                session=current_session(),
                user_message=goal,
                agent=agent,
            )
            if wants_research_report and not _looks_like_complete_research_report(filtered):
                return _format_research_report_fallback(
                    goal=goal,
                    trajectory=trajectory,
                    user_context=user_context,
                )
            return filtered
    except (ConnectionError, TimeoutError, OSError, ValueError, TypeError) as exc:
        logging.getLogger(__name__).warning(
            "synthesize_reply failed: %s: %s",
            type(exc).__name__,
            exc,
        )

    if wants_research_report:
        return _format_research_report_fallback(
            goal=goal,
            trajectory=trajectory,
            user_context=user_context,
        )
    return _assistant_text_from_trajectory(trajectory)


__all__ = ["synthesize_reply"]
