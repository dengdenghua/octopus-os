from __future__ import annotations

import logging
from typing import Any

from runtime.platform.models import ParsedIntent


def _runtime_soul_for_agent(agent: Any) -> str:
    if agent is None:
        return ""
    try:
        from runtime.execution.agents.loader import compose_runtime_soul

        return compose_runtime_soul(agent)
    except Exception as _e:  # noqa: BLE001
        _logger = logging.getLogger(__name__)
        _logger.debug("compose_runtime_soul failed for %s: %s", agent, _e)
        return str(getattr(agent, "soul", "") or "")


def _build_messages_for_llm(
    intent: ParsedIntent,
    agent: Any,
) -> list[Any]:
    """Build the message list for LLM calls with all system prompts.

    Extracts the duplicated message-building logic from both
    _direct_llm_fallback_impl and _stream_direct_llm_fallback.
    """
    from runtime.sensing.model_router.models import Message

    messages = _conversation_messages_for_model(intent)
    try:
        from runtime.core.cerebrum.llm_planner import _render_team_roster_section

        team_section = _render_team_roster_section(intent.user_context or {})
    except Exception as _e:  # noqa: BLE001
        _logger = logging.getLogger(__name__)
        _logger.debug("team roster render failed: %s", _e)
        team_section = ""
    if team_section:
        messages.insert(0, Message(role="system", content=team_section))
    runtime_soul = _runtime_soul_for_agent(agent)
    if runtime_soul:
        messages.insert(0, Message(role="system", content=runtime_soul))
    from runtime.memory.users.profile import render_profile_memories

    profile_section = render_profile_memories(_profile_memories_payload(intent))
    if profile_section:
        messages.insert(0, Message(role="system", content=profile_section))
    try:
        from runtime.core.cerebrum.thinking_mode import render_thinking_guidance

        thinking_section = render_thinking_guidance(
            (intent.user_context or {}).get("thinking_plan"),
        )
    except Exception as _e:  # noqa: BLE001
        _logger = logging.getLogger(__name__)
        _logger.debug("thinking guidance render failed: %s", _e)
        thinking_section = ""
    if thinking_section:
        messages.insert(0, Message(role="system", content=thinking_section))
    interaction_profile = _interaction_profile_prompt(intent.user_context)
    if interaction_profile:
        messages.insert(0, Message(role="system", content=interaction_profile))
    messages.insert(
        0,
        Message(
            role="system",
            content=(
                "Follow the user's requested language, length, and format exactly. "
                "Keep ordinary chat replies concise unless the user explicitly asks "
                "for detail. Render substantive answers, analyses, project reports, "
                "and summaries as clean GitHub-flavored Markdown: start with a short "
                "descriptive heading, use compact sections, bullet lists, numbered "
                "steps, tables, and fenced code blocks when useful. Do not wrap the "
                "entire answer in a markdown code fence. Put a blank line before "
                "each heading, list, table, horizontal rule, and code block."
            ),
        ),
    )
    if not messages:
        messages.append(Message(role="user", content=intent.normalized_goal))
    return messages


def _extract_goal(messages: list[Any]) -> str:
    for m in reversed(messages):
        if not isinstance(m, dict):
            continue
        if m.get("role") == "user":
            return _message_content_text(m).strip()
    return ""


def _message_content_text(message: dict[str, Any]) -> str:
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                text = part.get("text", "")
                if isinstance(text, str):
                    parts.append(text)
        return " ".join(parts)
    return ""


def _normalize_conversation_messages(messages: list[Any]) -> list[dict[str, str]]:
    """Normalize OpenAI chat messages into roles our model routers accept."""
    normalized: list[dict[str, str]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role in ("system", "developer"):
            out_role = "system"
        elif role == "user":
            out_role = "user"
        elif role in ("assistant", "tool"):
            out_role = "assistant"
        else:
            continue
        content = _message_content_text(message).strip()
        if not content:
            continue
        normalized.append({"role": out_role, "content": content})
    return normalized


def _conversation_messages_payload(
    intent: ParsedIntent,
) -> list[dict[str, str]]:
    payload = intent.user_context.get("conversation_messages", [])
    if not isinstance(payload, list):
        return []
    out: list[dict[str, str]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role in ("system", "user", "assistant") and isinstance(content, str):
            out.append({"role": role, "content": content})
    return out


def _conversation_messages_for_model(intent: ParsedIntent) -> list[Any]:
    from runtime.sensing.model_router.models import Message

    messages = [
        Message(role=item["role"], content=item["content"])
        for item in _conversation_messages_payload(intent)
    ]
    if any(m.role == "user" for m in messages):
        return messages
    return [Message(role="user", content=intent.normalized_goal)]


def _profile_memories_payload(intent: ParsedIntent) -> list[str]:
    payload = intent.user_context.get("profile_memories", [])
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, str) and item.strip()]


def _interaction_profile_prompt(user_context: dict[str, Any] | None) -> str:
    mode = str((user_context or {}).get("interaction_mode") or "").strip().lower()
    code_mode = str((user_context or {}).get("code_mode") or "").strip().lower()
    if mode == "office":
        return (
            "Interaction profile: office conversation mode. Keep the same "
            "capability and rigor, but present it as a polished assistant for "
            "documents, decisions, research, and everyday work. Prefer concise "
            "Markdown reports, summaries, tables, checklists, and clear next "
            "actions. Avoid low-level implementation, file, command, test, or "
            "tool-routing detail unless the user asks for it or it directly "
            "changes the answer. When showing reasoning, provide a public work "
            "log or rationale summary, not hidden chain-of-thought."
        )
    if mode == "coding" or code_mode == "solo":
        return (
            "Interaction profile: coding solo mode. Respond like a senior "
            "developer working directly in the user's workspace. Be explicit "
            "about relevant files, commands, tool actions, assumptions, risks, "
            "and verification. Keep prose concise, but include enough technical "
            "detail for the user to understand control flow, code changes, and "
            "test results. Use Markdown with file paths, commands, short plans, "
            "and concrete outcomes."
        )
    return ""


def _render_conversation_history(
    messages: list[dict[str, str]],
    *,
    max_messages: int = 12,
    max_chars: int = 4_000,
) -> str:
    if not messages:
        return ""
    rendered: list[str] = []
    total = 0
    for message in messages[-max_messages:]:
        role = message["role"]
        content = message["content"].replace("\n", " ").strip()
        if not content:
            continue
        line = f"[{role}] {content}"
        remaining = max_chars - total
        if remaining <= 0:
            break
        if len(line) > remaining:
            line = line[: max(0, remaining - 1)] + "…"
        rendered.append(line)
        total += len(line) + 1
    return "\n".join(rendered)
