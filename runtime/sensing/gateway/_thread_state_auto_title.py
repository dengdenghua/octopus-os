"""Auto-title service wiring shared by the thread state router."""

from __future__ import annotations

import logging
from typing import Any

_logger = logging.getLogger("runtime.sensing.gateway.thread_state_router")


def build_auto_title_service(store: Any, *, model_router: Any = None) -> Any:
    """Wire a session-title service for first-turn automatic titles."""
    if store is None:
        return None
    from runtime.memory.threads.session_title import SessionTitleService

    service = SessionTitleService(store)
    if model_router is None:
        return service
    try:
        from runtime.projectos.llm_hooks import DEFAULT_MODEL as _TITLE_MODEL
        from runtime.sensing.model_router import Message, ModelRequest

        def _llm_title_provider(thread: dict[str, Any]) -> str | None:
            values = thread.get("values") or {}
            messages = values.get("messages") or []
            first_human = next(
                (
                    message
                    for message in messages
                    if isinstance(message, dict)
                    and message.get("type") == "human"
                    and isinstance(message.get("content"), str)
                    and message["content"].strip()
                ),
                None,
            )
            if first_human is None:
                return None
            prompt = (
                "You are a session-title assistant. Write a concise "
                "conversation title (under 60 characters, plain text, "
                "no quotes or punctuation at the end) for a thread that "
                "starts with this user message:\n\n"
                f"{first_human['content'].strip()[:400]}"
            )
            response = model_router.call(
                ModelRequest(
                    model=_TITLE_MODEL,
                    messages=[Message(role="user", content=prompt)],
                    max_tokens=60,
                    temperature=0.2,
                )
            )
            return response.text or None

        service.register_provider("llm", _llm_title_provider, model=_TITLE_MODEL)
    except Exception as exc:  # noqa: BLE001 — auto-title is best-effort
        _logger.warning("session title provider unavailable: %s", exc)
    return service


__all__ = ["build_auto_title_service"]
