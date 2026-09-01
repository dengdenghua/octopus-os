"""Shared prompt contract for user messages received during an active turn."""

from __future__ import annotations

from collections.abc import Iterable

from runtime.platform.models.llm import Message

LIVE_STEERING_PROTOCOL = (
    "LIVE USER FOLLOW-UP — HIGH PRIORITY\n"
    "The user sent one or more messages while the current task was running. "
    "Handle them at the next safe model boundary and do not silently consume them.\n"
    "- In your next user-visible response, directly answer or acknowledge the "
    "latest follow-up before doing more tool work.\n"
    "- If it is a question or asks for status, answer from the evidence already "
    "available, then continue the active task unless the user asks you to pause "
    "or stop.\n"
    "- If it corrects or redirects the work, briefly acknowledge the change, "
    "update the approach, and continue from the new direction.\n"
    "- Preserve the active task and its completed work. A follow-up is not a "
    "turn cancellation unless the user explicitly requests cancellation."
)


def append_live_steering_messages(
    messages: list[Message],
    texts: Iterable[str],
) -> int:
    """Append one priority protocol marker followed by exact user messages."""

    normalized = [str(text).strip() for text in texts]
    normalized = [text for text in normalized if text]
    if not normalized:
        return 0
    messages.append(Message(role="system", content=LIVE_STEERING_PROTOCOL))
    messages.extend(Message(role="user", content=text) for text in normalized)
    return len(normalized)


def insert_live_steering_protocol(messages: list[Message]) -> None:
    """Place the protocol immediately before the current user follow-up."""

    if any(
        message.role == "system" and message.content == LIVE_STEERING_PROTOCOL
        for message in messages
    ):
        return
    insertion_index = len(messages)
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].role == "user":
            insertion_index = index
            break
    messages.insert(
        insertion_index,
        Message(role="system", content=LIVE_STEERING_PROTOCOL),
    )
