"""Project model-visible history from the journal (dsh session-log idea).

dsh's core invariant: **model-visible means logged** — anything that
reaches a model request must be reconstructable from the session log,
and raw assistant/tool events preserve replay and audit fidelity.

This module is the projection layer for Echo: it rebuilds the
assistant ``tool_use`` / user ``tool_result`` message sequence from
``StepEvent`` rows. A caller can therefore resume or audit a turn from
the journal alone, without holding the original in-memory message list,
and a test can prove "the model saw exactly what the journal recorded".

The projection is deliberately lossy where the model contract allows:
tool outputs flatten to strings (matching the anthropic router's
``str(content)`` fallback) and timestamps/costs are dropped. User intent
is not yet a journal event type, so ``user_intent`` is supplied by the
caller until a user-message event type lands.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from runtime.memory.journal._journal_base import Journal
from runtime.memory.journal._journal_models import StepEvent
from runtime.platform.models import TaskId
from runtime.platform.models.llm import Message


def _flatten_output(output: Any) -> str:
    """Render a tool result as the model-facing string.

    Mirrors the anthropic router's flattening: strings pass through,
    structured values serialize deterministically.
    """

    if output is None:
        return ""
    if isinstance(output, str):
        return output
    return json.dumps(output, ensure_ascii=False, sort_keys=True, default=str)


def derive_model_messages(
    journal: Journal,
    *,
    task_id: TaskId | None = None,
    user_intent: str | None = None,
    max_steps: int | None = None,
) -> list[Message]:
    """Rebuild model-visible messages from the journal's ``StepEvent`` rows.

    Each recorded step projects to two messages:

    1. assistant — one ``tool_use`` content block (id = the recorded
       ``ToolCall.call_id``, so providers that correlate tool results
       by id see a consistent pair).
    2. user — one ``tool_result`` content block referencing that id.

    ``user_intent`` becomes the leading user message when supplied.
    ``max_steps`` keeps only the tail of the step stream (context
    window pressure). Order follows journal order — the journal is
    append-only, so that is execution order.
    """

    events = journal.read_all()
    # Parsed step rows are concrete ``StepEvent`` instances. Narrow by the
    # runtime class rather than only the discriminator string so static and
    # runtime consumers agree that ``step`` is available below.
    steps = [e for e in events if isinstance(e, StepEvent)]
    if task_id is not None:
        wanted = str(task_id)
        steps = [e for e in steps if str(e.task_id) == wanted]
    if max_steps is not None:
        steps = steps[-max_steps:]

    messages: list[Message] = []
    if user_intent:
        messages.append(Message(role="user", content=user_intent))

    for event in steps:
        call = event.step.action
        result = event.step.result
        messages.append(
            Message(
                role="assistant",
                content=[
                    {
                        "type": "tool_use",
                        "id": str(call.call_id),
                        "name": str(call.sucker_id),
                        "input": call.args,
                    }
                ],
            )
        )
        messages.append(
            Message(
                role="user",
                content=[
                    {
                        "type": "tool_result",
                        "tool_use_id": str(call.call_id),
                        "content": _flatten_output(result.output),
                    }
                ],
            )
        )
    return messages


@dataclass(frozen=True)
class AssistantChunkStream:
    """One iteration's streamed parent-reply text, rebuilt from the journal.

    ``text`` is the exact concatenation of that iteration's
    ``assistant/chunk`` deltas in journal order; ``chunk_count`` is how
    many chunks the log recorded (proves per-chunk fidelity, not just
    an opaque final string).
    """

    iteration: int
    text: str
    chunk_count: int


def derive_assistant_stream(
    journal: Journal,
    *,
    iteration: int | None = None,
    kind: str | None = "text-delta",
) -> list[AssistantChunkStream]:
    """Reconstruct the parent's streamed reply from ``assistant/chunk`` rows.

    The dsh session-log invariant applied to the main turn: the
    streamed lanes are recoverable from the append-only journal alone,
    in journal order, grouped by iteration. ``kind`` selects the dsh
    ``StreamChunk`` lane — ``"text-delta"`` (the visible reply) by
    default, ``"reasoning-delta"`` for the private reasoning lane,
    ``None`` for everything. Iterations that streamed no matching
    chunks contribute nothing.
    """

    chunks: dict[int, list[str]] = {}
    order: list[int] = []
    for event in journal.read_all():
        if event.event_type != "assistant/chunk":
            continue
        # ``getattr`` guards against an untyped fallback row (unknown
        # event_type decodes to the base JournalEvent).
        event_kind = getattr(event, "kind", "text-delta") or "text-delta"
        if kind is not None and event_kind != kind:
            continue
        event_iter = int(getattr(event, "iteration", 0) or 0)
        if iteration is not None and event_iter != iteration:
            continue
        if event_iter not in chunks:
            chunks[event_iter] = []
            order.append(event_iter)
        chunks[event_iter].append(getattr(event, "delta", "") or "")
    return [
        AssistantChunkStream(
            iteration=key,
            text="".join(chunks[key]),
            chunk_count=len(chunks[key]),
        )
        for key in order
    ]


def assert_logged_assistant_reconstructs(
    journal: Journal,
    expected: list[AssistantChunkStream],
    *,
    iteration: int | None = None,
) -> None:
    """Assert the journal reconstructs the given streamed reply — round-trip.

    Mirrors ``assert_logged_stream_reconstructs`` for the parent reply
    lane: call from tests and audit paths that must prove the text a
    turn streamed is fully recoverable from the log.
    """

    actual = derive_assistant_stream(journal, iteration=iteration)
    assert actual == expected, f"derived {len(actual)} iteration-streams, expected {len(expected)}"


@dataclass(frozen=True)
class SubagentRoundStream:
    """One role's streamed prose for one round, rebuilt from the journal.

    ``text`` is the exact concatenation of that round's
    ``sub_text_delta`` deltas in journal order; ``chunk_count`` is how
    many chunks the log recorded (proves per-chunk fidelity, not just
    an opaque final string). ``session_id`` is the durable sub-agent
    session the chunks belong to (empty for one-shot/remote children).
    """

    role_id: str
    round: int
    text: str
    chunk_count: int
    session_id: str = ""


def derive_subagent_streams(
    journal: Journal,
    *,
    session_id: str | None = None,
    role_id: str | None = None,
) -> list[SubagentRoundStream]:
    """Reconstruct each role's streamed prose from ``SubTextDeltaEvent`` rows.

    The dsh session-log invariant applied to sub-agent turns: the
    prose a role streamed is recoverable from the append-only journal
    alone, in journal order, grouped by ``(session_id, role_id, round)``.
    Rounds that streamed no text contribute nothing; groups are returned
    in first-seen order.

    ``session_id`` narrows to one durable session (``role_id`` alone is
    ambiguous because every session of the same role shares its id);
    ``role_id`` narrows to one role. Either or both may be supplied.
    """

    streams: dict[tuple[str, str, int], list[str]] = {}
    order: list[tuple[str, str, int]] = []
    for event in journal.read_all():
        if event.event_type != "sub_text_delta":
            continue
        # ``getattr`` guards against an untyped fallback row (unknown
        # event_type decodes to the base JournalEvent).
        event_session = getattr(event, "session_id", "") or ""
        event_role = getattr(event, "role_id", "") or ""
        event_round = int(getattr(event, "round", 0) or 0)
        if session_id is not None and event_session != session_id:
            continue
        if role_id is not None and event_role != role_id:
            continue
        key = (event_session, event_role, event_round)
        if key not in streams:
            streams[key] = []
            order.append(key)
        streams[key].append(getattr(event, "delta", "") or "")
    return [
        SubagentRoundStream(
            session_id=key[0],
            role_id=key[1],
            round=key[2],
            text="".join(streams[key]),
            chunk_count=len(streams[key]),
        )
        for key in order
    ]


@dataclass(frozen=True)
class SessionSummary:
    """One completed sub-agent session turn's outcome, rebuilt from the journal."""

    session_id: str
    agent_id: str
    rounds: int
    success: bool
    error: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


def derive_session_summaries(
    journal: Journal,
    *,
    session_id: str | None = None,
) -> list[SessionSummary]:
    """Reconstruct each sub-agent session turn's completion from the journal.

    Reads ``SubSessionSummaryEvent`` rows in journal order, optionally
    narrowed to one ``session_id``. Complements ``derive_subagent_streams``
    and ``surface_events_from_journal``: the prose lanes give the text, this
    gives the structured effort/outcome a resume path needs without replaying
    every chunk (dsh session-log invariant).
    """

    summaries: list[SessionSummary] = []
    for event in journal.read_all():
        if event.event_type != "sub_session_summary":
            continue
        event_session = getattr(event, "session_id", "") or ""
        if session_id is not None and event_session != session_id:
            continue
        summaries.append(
            SessionSummary(
                session_id=event_session,
                agent_id=getattr(event, "agent_id", "") or "",
                rounds=int(getattr(event, "rounds", 0) or 0),
                success=bool(getattr(event, "success", True)),
                error=getattr(event, "error", "") or "",
                input_tokens=int(getattr(event, "input_tokens", 0) or 0),
                output_tokens=int(getattr(event, "output_tokens", 0) or 0),
                cost_usd=float(getattr(event, "cost_usd", 0.0) or 0.0),
            )
        )
    return summaries


@dataclass(frozen=True)
class SessionUsageRecord:
    """One model call's token/cost spend for a sub-agent session.

    The dsh token-meter granularity a resume path needs when it wants to
    report spend at call level rather than the turn-level total carried by
    ``SessionSummary``. Rebuilt from a ``token_usage`` journal row.
    """

    session_id: str
    iteration: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    model: str
    task_id: str = ""


def derive_session_usage(
    journal: Journal,
    *,
    session_id: str | None = None,
) -> list[SessionUsageRecord]:
    """Reconstruct per-call token/cost spend from ``token_usage`` rows.

    One record per model call (dsh token-meter), optionally narrowed to one
    ``session_id``. Unattributed rows (parent turns, one-shot/remote
    children) are skipped when filtering by session; legacy rows without the
    attribution field default to an empty session id. Complements
    ``derive_session_summaries``: the summaries give the turn-level totals,
    this gives the underlying per-call lane from the same log.
    """

    records: list[SessionUsageRecord] = []
    for event in journal.read_all():
        if getattr(event, "event_type", "") != "token_usage":
            continue
        event_session = getattr(event, "session_id", "") or ""
        if session_id is not None and event_session != session_id:
            continue
        records.append(
            SessionUsageRecord(
                session_id=event_session,
                iteration=int(getattr(event, "iteration", 0) or 0),
                input_tokens=int(getattr(event, "input_tokens", 0) or 0),
                output_tokens=int(getattr(event, "output_tokens", 0) or 0),
                cost_usd=float(getattr(event, "cost_usd", 0.0) or 0.0),
                model=getattr(event, "model", "") or "",
                task_id=str(getattr(event, "task_id", "") or ""),
            )
        )
    return records


def assert_logged_stream_reconstructs(
    journal: Journal,
    expected: list[SubagentRoundStream],
    *,
    session_id: str | None = None,
    role_id: str | None = None,
) -> None:
    """Assert the journal reconstructs the given streamed prose — round-trip.

    Mirrors ``assert_logged_history_reconstructs`` for the sub-agent
    prose lane: call from tests and audit paths that must prove the
    stream a role produced is fully recoverable from the log.
    """

    actual = derive_subagent_streams(
        journal,
        session_id=session_id,
        role_id=role_id,
    )
    assert actual == expected, f"derived {len(actual)} round-streams, expected {len(expected)}"


def assert_logged_history_reconstructs(
    journal: Journal,
    expected_steps: list[StepEvent],
    *,
    task_id: TaskId | None = None,
) -> None:
    """Assert the journal reconstructs the given steps — the round-trip.

    The dsh invariant "model-visible means logged" reduces to: a step
    written to the journal derives back to the same tool_use id, tool
    name, and input. Call this from tests and from audit paths that
    must prove a transcript is complete.
    """

    messages = derive_model_messages(journal, task_id=task_id)
    tool_uses = [
        block
        for message in messages
        if isinstance(message.content, list)
        for block in message.content
        if isinstance(block, dict) and block.get("type") == "tool_use"
    ]
    assert len(tool_uses) == len(expected_steps), (
        f"derived {len(tool_uses)} tool_use blocks, expected {len(expected_steps)}"
    )
    for block, expected in zip(tool_uses, expected_steps, strict=True):
        call = expected.step.action
        assert block["id"] == str(call.call_id)
        assert block["name"] == str(call.sucker_id)
        assert block["input"] == call.args


def _user_surface(text: str) -> dict[str, Any]:
    """One dsh ``user/message`` surface event."""
    return {
        "type": "user/message",
        "data": {
            "source": {"kind": "user"},
            "content": [{"type": "text", "text": text}],
        },
    }


def _assistant_surface(text: str) -> dict[str, Any]:
    """One dsh ``assistant/message`` surface event."""
    return {
        "type": "assistant/message",
        "data": {"message": {"content": [{"type": "text", "text": text}]}},
    }


def surface_events_from_journal(
    journal: Journal,
    *,
    session_id: str,
    prompts: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Build a dsh surface for one sub-agent session from real journal events.

    Walks the append-only log in order and projects the session's
    ``user/message`` + ``sub_text_delta`` rows into the dsh surface shape,
    interleaved exactly as they were written (a turn journals its prompt,
    then its streamed prose). When the log has no per-session ``user/message``
    rows yet (older sessions / one-shot children), the user lane falls back
    to ``prompts`` and interleaves with the assistant streams by round.

    Returns events consumable by ``retain_session_reference`` —
    ``user/message`` + ``assistant/message``.
    """
    events: list[dict[str, Any]] = []
    pending: list[str] = []
    journal_user_count = 0
    # Audit P-04: consume only this session's rows (read_by_session) instead
    # of scanning the whole journal and discarding every other session's
    # events — repeated projections stay O(session events), not O(journal).
    for event in journal.read_by_session(session_id):
        etype = getattr(event, "event_type", "")
        if etype == "user/message":
            if pending:
                events.append(_assistant_surface("".join(pending)))
                pending = []
            text = (getattr(event, "text", "") or "").strip()
            if text:
                journal_user_count += 1
                events.append(_user_surface(text))
        elif etype == "sub_text_delta":
            pending.append(getattr(event, "delta", "") or "")
    if pending:
        events.append(_assistant_surface("".join(pending)))

    if journal_user_count:
        # Both lanes came from the log — a pure-journal surface.
        return events

    # Fallback: no per-session user rows — take the user lane from the
    # caller and interleave with the assistant streams by round.
    streams_by_round = {
        stream.round: stream for stream in derive_subagent_streams(journal, session_id=session_id)
    }
    prompts_list = list(prompts or [])
    num_rounds = max(len(prompts_list), max(streams_by_round, default=0))
    fallback: list[dict[str, Any]] = []
    for index in range(num_rounds):
        if index < len(prompts_list) and (prompts_list[index] or "").strip():
            fallback.append(_user_surface(prompts_list[index].strip()))
        stream = streams_by_round.get(index + 1)
        if stream is not None and stream.text:
            fallback.append(_assistant_surface(stream.text))
    return fallback
