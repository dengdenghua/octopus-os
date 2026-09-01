"""Realtime turn ↔ legacy conversation history adapters.

Split out of ``realtime_cerebrum.py``: flatten a realtime ``Turn[]``
snapshot into the legacy ``AgentThreadState`` triple for the sidebar's
thread store, and into OpenAI-style chat history for
``stream_react_loop`` follow-up turns.
"""

from __future__ import annotations

import difflib
from typing import Any

from runtime.protocol import Turn, TurnStatus


def _json_safe(value: Any) -> Any:
    """Recursively normalise objects into JSON-serialisable plain data.

    The legacy ``ThreadStateStore`` snapshot is written with
    ``json.dumps`` (no ``default=`` hook), so any pydantic model or
    other non-JSON object nested inside a flattened message (e.g.
    ``FileChange`` under ``tool_calls[].args.changes``) would raise
    ``TypeError`` and silently abort the turn-state write. Converting
    here keeps the snapshot writer robust without changing its wire
    contract.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "model_dump"):
        try:
            return _json_safe(value.model_dump(by_alias=True, mode="json"))
        except Exception:  # noqa: BLE001 - best-effort flatten
            try:
                return _json_safe(value.model_dump())
            except Exception:  # noqa: BLE001
                return repr(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    # datetime / Enum / any remaining non-JSON leaf: fall back to str.
    return str(value)


def _collapse_near_identical_ai(messages: list[dict[str, Any]], incoming: dict[str, Any]) -> bool:
    """Collapse a re-sent, near-identical assistant message (latest wins).

    A guard-rejected draft and its retry can be byte-for-byte the same long
    report except for a single number (observed: an AI4S report re-emitted at
    0.9988 similarity right after a fabricated-fact guard rejection — both
    messages landed in the same turn). Persisting both inflates the sidebar
    transcript and re-sends ~2x the tokens to the model. We only collapse
    pure-text AI messages whose bodies are long and very similar, keeping the
    newest copy in place. If the earlier copy carried tool calls (e.g. a
    ``todo_write`` before the report draft), the caller preserves them on the
    merged message so no real action is dropped from the transcript.
    """
    content = incoming.get("content")
    if not isinstance(content, str) or len(content) < 120:
        return False
    # An incoming message that itself carries tool calls is a distinct
    # action carrier, never a re-sent report — don't collapse it.
    if incoming.get("tool_calls"):
        return False
    prev = messages[-1] if messages else None
    if not isinstance(prev, dict) or prev.get("type") != "ai":
        return False
    prev_content = prev.get("content")
    if not isinstance(prev_content, str) or len(prev_content) < 120:
        return False
    ratio = difflib.SequenceMatcher(None, prev_content, content).ratio()
    return ratio >= 0.9


def _flatten_turns_to_messages(
    turns: list[Turn],
    *,
    include_failed_drafts: bool = True,
) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]] | None]:
    """Translate a realtime ``Turn[]`` snapshot into the legacy
    ``AgentThreadState`` triple (messages, artifacts, todos).

    Mirrors the frontend ``conversationToAgentThreadState`` in
    ``src/core/threads/realtime-adapter.ts`` so a thread looks the same
    whether the sidebar loads it from the legacy store (this snapshot)
    or rehydrates it live from the WebSocket.

    Rules:
      * userMessage     → ``HumanMessage``
      * reasoning summary + plan → folded into ``additional_kwargs`` of the
                          next ``AIMessage`` in the same turn. Summaries stay
                          in the private ``reasoning_content`` lane; raw
                          provider reasoning content is never copied
      * agentMessage    → ``AIMessage`` (Thought/Action/Observation
                          prefixes pass through; the frontend's
                          ``splitReactTrace`` handles cleanup at render
                          time)
      * commandExecution / mcpToolCall / fileChange → tool_calls on the
                          trailing AIMessage of the turn
      * todo-list       → flat list at thread level (last write wins)
      * fileChange      → paths collected into ``artifacts``
      * error           → final synthetic AIMessage with
                          ``additional_kwargs.error``

    ``include_failed_drafts`` controls how a FAILED / INTERRUPTED turn's
    intermediate agentMessage drafts are treated. When ``False`` (used by
    the model-context adapter ``_conversation_messages_for_react``), only
    the turn's user prompt and its final error are kept — the half-built
    commentary / reasoning / tool chain of a failed turn would otherwise
    leak stale task narrative into the next turn and make the model answer
    the *previous* (unfinished) question instead of the user's new one.
    The sidebar keeps ``True`` so the user can still review what happened.
    """
    messages: list[dict[str, Any]] = []
    artifacts: list[str] = []
    todos: list[dict[str, Any]] | None = None

    for turn in turns:
        turn_failed = turn.status in (
            TurnStatus.FAILED,
            TurnStatus.PAUSED,
            TurnStatus.CANCELLED,
            TurnStatus.INTERRUPTED,
        )
        # When not including failed drafts, drop every intermediate AI
        # message (commentary / reasoning / tool chain) of a failed turn
        # up front. The user prompt and any trailing error item are still
        # appended below; the model context then only sees the *failed*
        # fact, not the stale in-progress narrative it was building.
        if turn_failed and not include_failed_drafts:
            # Keep the task objective (user prompt), the last concrete
            # ``answer`` the turn produced (progress anchor) and the error
            # (when present). Commentary checkpoints are still dropped —
            # they are mid-flight narration, not a conclusion — but without
            # the anchor the next turn cannot tell what the previous run was
            # doing or how far it got.
            failed_user = ""
            user_id: Any = None
            last_answer: str | None = None
            error_item: Any = None
            for item in turn.items:
                t = getattr(item, "type", None)
                if t == "userMessage":
                    failed_user = getattr(item, "text", "") or ""
                    user_id = getattr(item, "id", None)
                elif t == "agentMessage" and (getattr(item, "message_kind", "answer") == "answer"):
                    text = (getattr(item, "text", "") or "").strip()
                    if text:
                        last_answer = text
                elif t == "error":
                    error_item = item
            if failed_user:
                messages.append(
                    {
                        "type": "human",
                        "id": user_id,
                        "content": failed_user,
                    }
                )
            if last_answer:
                messages.append(
                    {
                        # This is private continuity data, not prior assistant
                        # speech. A system role prevents providers from
                        # imitating the truncation scaffold in the next public
                        # answer (the source of visible "后文已省略" leaks).
                        "type": "system",
                        "id": None,
                        "content": (
                            "Internal recovery context from an incomplete prior turn; "
                            "use it for continuity but never quote this note.\n"
                            f"{last_answer[:600]}"
                        ),
                        # Recovery-only context for the next model turn. It is
                        # deliberately bounded and must never render as a new
                        # assistant answer in the conversation.
                        "additional_kwargs": {"hide_from_ui": True},
                    }
                )
            if error_item is not None:
                message = getattr(error_item, "message", "") or ""
                messages.append(
                    {
                        "type": "ai",
                        "id": getattr(error_item, "id", None),
                        "content": f"[上一轮任务失败。] {message}"
                        if message
                        else "[上一轮任务失败。]",
                        "additional_kwargs": {
                            "error": {
                                "message": message,
                                "will_retry": False,
                                "info": getattr(error_item, "error_info", None),
                            },
                        },
                    }
                )
            continue
        pending_reasoning: list[str] = []
        pending_plan: str | None = None
        pending_tool_calls: list[dict[str, Any]] = []

        def merge_into_last_ai(
            reasoning: list[str],
            plan: str | None,
            tool_calls: list[dict[str, Any]],
        ) -> bool:
            for message in reversed(messages):
                if message.get("type") == "human":
                    return False
                if message.get("type") != "ai":
                    continue
                content = message.get("content")
                if not isinstance(content, str) or not content.strip():
                    continue

                incoming_kwargs = _build_ai_kwargs(reasoning, plan)
                if incoming_kwargs:
                    existing_kwargs = message.setdefault("additional_kwargs", {})
                    if not isinstance(existing_kwargs, dict):
                        existing_kwargs = {}
                        message["additional_kwargs"] = existing_kwargs
                    incoming_reasoning = incoming_kwargs.get("reasoning_content")
                    if isinstance(incoming_reasoning, str) and incoming_reasoning.strip():
                        existing_reasoning = existing_kwargs.get("reasoning_content")
                        parts = [
                            part
                            for part in (
                                existing_reasoning if isinstance(existing_reasoning, str) else "",
                                incoming_reasoning,
                            )
                            if part.strip()
                        ]
                        existing_kwargs["reasoning_content"] = "\n\n".join(parts)
                    if "thinking_plan" in incoming_kwargs:
                        existing_kwargs["thinking_plan"] = incoming_kwargs["thinking_plan"]
                if tool_calls:
                    calls = message.setdefault("tool_calls", [])
                    if not isinstance(calls, list):
                        calls = []
                        message["tool_calls"] = calls
                    seen = {
                        str(call.get("id") or "")
                        for call in calls
                        if isinstance(call, dict) and call.get("id")
                    }
                    for call in tool_calls:
                        call_id = str(call.get("id") or "")
                        if call_id and call_id in seen:
                            continue
                        calls.append(dict(call))
                        if call_id:
                            seen.add(call_id)
                return True
            return False

        def flush_trailing_ai(current_turn_status: TurnStatus) -> None:
            nonlocal pending_reasoning, pending_plan, pending_tool_calls
            if not pending_reasoning and pending_plan is None and not pending_tool_calls:
                return
            if current_turn_status == TurnStatus.COMPLETED and merge_into_last_ai(
                pending_reasoning, pending_plan, pending_tool_calls
            ):
                pending_reasoning = []
                pending_plan = None
                pending_tool_calls = []
                return
            ai: dict[str, Any] = {
                "type": "ai",
                "content": "",
                "additional_kwargs": _build_ai_kwargs(pending_reasoning, pending_plan),
            }
            if pending_tool_calls:
                ai["tool_calls"] = list(pending_tool_calls)
            messages.append(ai)
            pending_reasoning = []
            pending_plan = None
            pending_tool_calls = []

        for item in turn.items:
            t = getattr(item, "type", None)
            if t == "userMessage":
                flush_trailing_ai(turn.status)
                messages.append(
                    {
                        "type": "human",
                        "id": getattr(item, "id", None),
                        "content": getattr(item, "text", "") or "",
                    }
                )
            elif t == "reasoning":
                # ``content`` contains provider chain-of-thought and is
                # intentionally excluded from every user-facing legacy
                # snapshot. A readable ``summary`` remains reasoning metadata;
                # it must never be promoted to public commentary.
                summary = getattr(item, "summary", None) or []
                if summary:
                    pending_reasoning.append("\n".join(summary))
            elif t == "plan":
                pending_plan = getattr(item, "text", "") or pending_plan
            elif t == "commandExecution":
                command = getattr(item, "command", "") or "command"
                input_preview = getattr(item, "input_preview", None)
                args: dict[str, Any] = {}
                if isinstance(input_preview, dict):
                    args.update(input_preview)
                elif input_preview is not None:
                    args["inputPreview"] = input_preview
                args.setdefault("command", command)
                args.setdefault("tool", command)
                args.update(
                    {
                        "cwd": getattr(item, "cwd", None),
                        "output": getattr(item, "aggregated_output", "") or "",
                        "exit_code": getattr(item, "exit_code", None),
                        "networkAccess": getattr(item, "network_access", None),
                    }
                )
                pending_tool_calls.append(
                    {
                        "id": getattr(item, "id", ""),
                        "name": command,
                        "args": args,
                        "type": "tool_call",
                    }
                )
            elif t == "mcpToolCall":
                tool = getattr(item, "tool", "") or ""
                arguments = _json_safe(getattr(item, "arguments", {}) or {})
                # Lifecycle finish markers carry their durable identity and
                # outcome in ``result``. The legacy transcript only has one
                # tool-call payload lane, so merge that public envelope into
                # args; otherwise replay degrades to ``args: {}`` after a
                # refresh even though the realtime item was complete.
                if tool in {"__subagent_spawned__", "__subagent_finished__"}:
                    result = _json_safe(getattr(item, "result", None))
                    if isinstance(result, dict):
                        arguments = {**result, **arguments}
                    arguments.setdefault(
                        "status", getattr(getattr(item, "status", None), "value", None)
                    )
                    duration_ms = getattr(item, "duration_ms", None)
                    if duration_ms is not None:
                        arguments.setdefault("duration_ms", duration_ms)
                pending_tool_calls.append(
                    {
                        "id": getattr(item, "id", ""),
                        "name": f"{getattr(item, 'server', '')}.{tool}",
                        "args": arguments,
                        "type": "tool_call",
                        "timelineSequence": getattr(item, "timeline_sequence", None),
                        "parentItemId": getattr(item, "parent_item_id", None),
                        "phaseId": getattr(item, "phase_id", None),
                    }
                )
            elif t == "subagent":
                pending_tool_calls.append(
                    {
                        "id": getattr(item, "id", ""),
                        "name": "subagent",
                        "args": {
                            "subagent_id": getattr(item, "subagent_id", ""),
                            "role": getattr(item, "role", None),
                            "name": getattr(item, "name", None),
                            "codename": getattr(item, "codename", None),
                            "avatar": getattr(item, "avatar", None),
                            "status": getattr(getattr(item, "status", None), "value", None),
                            "summary": getattr(item, "summary", None),
                            "error": getattr(item, "error", None),
                            "iteration_count": getattr(item, "iteration_count", None),
                            "files_touched": _json_safe(getattr(item, "files_touched", []) or []),
                        },
                        "type": "tool_call",
                        "timelineSequence": getattr(item, "timeline_sequence", None),
                        "parentItemId": getattr(item, "parent_item_id", None),
                        "phaseId": getattr(item, "phase_id", None),
                    }
                )
            elif t == "agentMessage":
                message_kind = getattr(item, "message_kind", "answer") or "answer"
                additional_kwargs = _build_ai_kwargs(pending_reasoning, pending_plan)
                additional_kwargs["message_kind"] = message_kind
                if message_kind == "commentary":
                    additional_kwargs["public_progress"] = True
                ai = {
                    "type": "ai",
                    "id": getattr(item, "id", None),
                    "content": getattr(item, "text", "") or "",
                    "additional_kwargs": additional_kwargs,
                }
                if pending_tool_calls:
                    ai["tool_calls"] = list(pending_tool_calls)
                pending_reasoning = []
                pending_plan = None
                pending_tool_calls = []
                if _collapse_near_identical_ai(messages, ai):
                    # A guard-rejected draft + its near-identical retry would
                    # otherwise both persist; keep only the newest copy. Real
                    # tool calls attached to the earlier copy (todo_update etc.)
                    # are preserved so no action is lost from the transcript.
                    merged = ai
                    prev_tool_calls = messages[-1].get("tool_calls")
                    if prev_tool_calls:
                        merged = dict(ai)
                        merged["tool_calls"] = prev_tool_calls
                    messages[-1] = merged
                else:
                    messages.append(ai)
            elif t == "fileChange":
                changes = getattr(item, "changes", None) or []
                for ch in changes:
                    p = getattr(ch, "path", None) if not isinstance(ch, dict) else ch.get("path")
                    if p:
                        artifacts.append(p)
                pending_tool_calls.append(
                    {
                        "id": getattr(item, "id", ""),
                        "name": "file_change",
                        "args": {
                            # FileChange is a pydantic model — the legacy
                            # ThreadStateStore json.dumps()s this snapshot, so
                            # raw model objects would raise TypeError and the
                            # whole turn-state write gets silently swallowed
                            # (updated_at frozen at thread creation). Normalise
                            # to plain dicts before handing off.
                            "changes": _json_safe(changes),
                            "grant_root": getattr(item, "grant_root", None),
                        },
                        "type": "tool_call",
                    }
                )
            elif t == "todo-list":
                plan = getattr(item, "plan", None) or []
                snapshot: list[dict[str, Any]] = []
                for entry in plan:
                    title = (
                        entry.get("title")
                        if isinstance(entry, dict)
                        else getattr(entry, "title", "")
                    )
                    status = (
                        entry.get("status")
                        if isinstance(entry, dict)
                        else getattr(entry, "status", "pending")
                    )
                    snapshot.append(
                        {
                            "content": title or "",
                            "status": status or "pending",
                            "objective_id": getattr(item, "objective_id", None)
                            or turn.objective_id,
                            "task_id": getattr(item, "task_id", None) or turn.task_id,
                            "turn_id": turn.id,
                        }
                    )
                todos = snapshot
            elif t == "error":
                flush_trailing_ai(turn.status)
                message = getattr(item, "message", "") or ""
                messages.append(
                    {
                        "type": "ai",
                        "id": getattr(item, "id", None),
                        "content": f"出错了：{message}" if message else "出错了。",
                        "additional_kwargs": {
                            "error": {
                                "message": message,
                                "will_retry": bool(getattr(item, "will_retry", False)),
                                "info": getattr(item, "error_info", None),
                            },
                        },
                    }
                )

        flush_trailing_ai(turn.status)

    return messages, artifacts, todos


# Total base64 bytes of rehydrated history images allowed per request. ~4 MiB
# of data URL is roughly 1.3M characters — already a large slice of any
# context window, and beyond it providers start rejecting the request outright.
_HISTORY_IMAGE_BYTE_BUDGET = 4 * 1024 * 1024


def _user_message_attachments_by_id(turns: list[Turn]) -> dict[str, list[dict[str, Any]]]:
    """Map ``userMessage`` item id → its persisted attachments.

    ``_flatten_turns_to_messages`` deliberately keeps its output shape narrow
    for the UI snapshot, so attachments never reach it. Rather than widen that
    contract, the model-context adapter re-joins on the item id.
    """

    by_id: dict[str, list[dict[str, Any]]] = {}
    for turn in turns:
        for item in getattr(turn, "items", None) or []:
            if getattr(item, "type", None) != "userMessage":
                continue
            item_id = getattr(item, "id", None)
            attachments = getattr(item, "attachments", None)
            if (
                isinstance(item_id, str)
                and item_id
                and isinstance(attachments, list)
                and attachments
            ):
                by_id[item_id] = attachments
    return by_id


def _history_image_blocks(
    attachments: list[dict[str, Any]],
    *,
    budget: int,
    byte_budget: int,
) -> tuple[list[dict[str, Any]], int]:
    """Return inline image blocks for a past user message, within both budgets.

    Reuses the same builder the live turn uses, so a rehydrated image is
    shaped exactly like a freshly uploaded one (and inherits its refusal to
    emit unresolvable server-relative URLs).

    A count cap alone is not enough: a phone screenshot is 200 KiB–3 MiB of
    base64, so four of them would add ~10 MiB to every subsequent request.
    Oversized images are skipped rather than truncated — half a data URL is
    not a picture. Returns the blocks and the bytes they consumed.
    """

    if budget <= 0 or byte_budget <= 0:
        return [], 0
    from runtime.core.cerebrum._react_context_attachments import (
        _image_blocks_from_attachments,
    )

    blocks, _consumed = _image_blocks_from_attachments(attachments)
    kept: list[dict[str, Any]] = []
    spent = 0
    for block in blocks[:budget]:
        url = block.get("image_url", {}).get("url", "")
        cost = len(url) if isinstance(url, str) else 0
        # A hosted https URL is a few dozen bytes; only data URLs are heavy.
        if spent + cost > byte_budget:
            continue
        spent += cost
        kept.append(block)
    return kept, spent


def _tool_summary_note(item: dict[str, Any], anchor: str = "") -> str | None:
    """Return a model-facing note listing the tools an AI turn executed.

    Kept in English on purpose: this is protocol scaffolding, not prose, and
    the previous Chinese marker was injected verbatim into English threads.

    The note is **self-anchoring**: it quotes the opening of the assistant turn
    it describes instead of saying "the previous turn". Position is not portable
    across providers -- ``anthropic_router._split_system`` and
    ``gemini_router._split_system_and_contents`` both hoist every ``system``
    message out of the conversation and concatenate them into the top-level
    system prompt, so a positional phrasing would end up describing whichever
    turn the model guesses, and several notes would pile up unordered. Quoting
    the turn survives hoisting. (``openai_router`` keeps position; it also drops
    all system messages for ``glm-5.1``, where the note is simply lost -- that
    degrades continuity without leaking, which is the safe direction.)
    """

    tool_calls = item.get("tool_calls")
    if not isinstance(tool_calls, list) or not tool_calls:
        return None
    tool_names: list[str] = []
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        name = call.get("name")
        if isinstance(name, str) and name and name not in tool_names:
            tool_names.append(name)
    if not tool_names:
        return None
    summary = ", ".join(tool_names[:6])
    if len(tool_names) > 6:
        summary += f" (+{len(tool_names) - 6} more)"
    quoted = " ".join(anchor.split())[:60].strip()
    subject = f'the assistant reply starting "{quoted}"' if quoted else "an earlier assistant reply"
    return (
        f"Context: {subject} was produced after running these tools: {summary}. "
        "This note is internal context, not part of the conversation -- never quote it."
    )


def _conversation_messages_for_react(
    turns: list[Turn],
    *,
    max_messages: int = 24,
    max_history_images: int = 4,
    max_history_image_bytes: int = _HISTORY_IMAGE_BYTE_BUDGET,
) -> list[dict[str, Any]]:
    """Return recent OpenAI-style chat history for ``stream_react_loop``.

    The realtime UI reconstructs visible history from the EventLog, but
    the react loop only sees previous turns when they are placed in
    ``intent.user_context["conversation_messages"]``. This adapter keeps
    follow-up replies like "yes" or "go check it" anchored to the same
    thread without making the frontend resend the whole transcript.

    For AI turns that executed tools, a compact tool summary follows the
    assistant message as its own ``system`` entry, so the next round's model
    can see what was actually done without inflating the transcript with raw
    tool I/O. It deliberately does **not** ride inside the assistant content:
    doing that made the model read the marker as its own prior speech and
    reproduce it verbatim in fresh replies, leaking scaffolding like
    ``[上轮操作: web_search]`` into user-visible output.

    User messages that carried image uploads are rehydrated as multimodal
    block lists. Without this, an image was visible only on the turn it was
    sent: asking "and what about the top-left corner?" one message later left
    the model blind. ``max_history_images`` caps how many past images are
    re-sent (newest first) because each data URL costs real tokens and the
    frame budget is finite.
    """

    legacy_messages, _, _ = _flatten_turns_to_messages(
        turns,
        include_failed_drafts=False,
    )
    attachments_by_id = _user_message_attachments_by_id(turns) if max_history_images > 0 else {}
    role_by_type = {
        "human": "user",
        "ai": "assistant",
        "system": "system",
    }
    history: list[dict[str, Any]] = []
    image_carrying_indices: list[int] = []
    for item in legacy_messages:
        if not isinstance(item, dict):
            continue
        role = role_by_type.get(str(item.get("type") or ""))
        if role is None:
            continue
        content = item.get("content")
        # An assistant turn that only ran tools has empty prose but still
        # carries ``tool_calls``. Surface a compact "what the last turn did"
        # note so the next model stays anchored -- the old code dropped these
        # messages because empty content failed the ``content.strip()`` gate,
        # which silently discarded the tool-action context and weakened
        # cross-turn grounding.
        if (
            role == "assistant"
            and isinstance(item.get("tool_calls"), list)
            and item["tool_calls"]
            and not (isinstance(content, str) and content.strip())
        ):
            tool_note = _tool_summary_note(item)
            if tool_note is not None:
                history.append({"role": "system", "content": tool_note, "_tool_note": True})
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        content = content.strip()
        # Carry a compact tool-action summary so the next turn's model
        # understands what the previous round actually did, not just the
        # final prose. It is emitted as a separate system entry below rather
        # than folded into the assistant text -- see the docstring.
        tool_note = _tool_summary_note(item, anchor=content) if role == "assistant" else None
        if role == "user":
            item_id = item.get("id")
            if isinstance(item_id, str) and item_id in attachments_by_id:
                image_carrying_indices.append(len(history))
                history.append(
                    {
                        "role": role,
                        "content": content,
                        "_attachment_id": item_id,
                    }
                )
                continue
        history.append({"role": role, "content": content})
        if tool_note is not None:
            history.append({"role": "system", "content": tool_note, "_tool_note": True})
    if max_messages > 0 and len(history) > max_messages:
        dropped = len(history) - max_messages
        history = history[-max_messages:]
        image_carrying_indices = [i - dropped for i in image_carrying_indices if i >= dropped]
        # Truncation can behead a note's assistant message. A note about a turn
        # the model can no longer see is just noise, so drop it.
        while history and history[0].get("_tool_note"):
            history.pop(0)
            image_carrying_indices = [i - 1 for i in image_carrying_indices if i >= 1]
    # Spend the image budget newest-first: a recent picture is far more likely
    # to be what a follow-up question refers to. The final entry is skipped —
    # it is the message being sent right now, and prompt assembly rebuilds it
    # from ``user_context["attachments"]`` after dropping it from history.
    remaining = max_history_images
    remaining_bytes = max_history_image_bytes
    for index in reversed(image_carrying_indices):
        entry = history[index]
        item_id = entry.pop("_attachment_id", None)
        if index == len(history) - 1 or remaining <= 0 or not isinstance(item_id, str):
            continue
        blocks, spent = _history_image_blocks(
            attachments_by_id[item_id],
            budget=remaining,
            byte_budget=remaining_bytes,
        )
        if not blocks:
            continue
        remaining -= len(blocks)
        remaining_bytes -= spent
        text = entry.get("content")
        content_blocks: list[dict[str, Any]] = []
        if isinstance(text, str) and text:
            content_blocks.append({"type": "text", "text": text})
        content_blocks.extend(blocks)
        entry["content"] = content_blocks
    for entry in history:
        entry.pop("_tool_note", None)
    return history


def _build_ai_kwargs(
    reasoning: list[str],
    plan: str | None,
) -> dict[str, Any]:
    kw: dict[str, Any] = {}
    if reasoning:
        # A reasoning summary is readable, but it is still not an assistant
        # message. Keep it in the private reasoning lane so the frontend can
        # render it as a muted disclosure instead of conversational prose.
        kw["reasoning_content"] = "\n\n".join(reasoning)
    if plan is not None:
        kw["thinking_plan"] = plan
    return kw


def _title_from_messages(messages: list[dict[str, Any]]) -> str | None:
    """Pull the first user message text as a 60-char title.

    The sidebar already has its own ``titleOfThread`` fallback, but
    seeding ``values.title`` here means the legacy threads.jsonl is
    self-descriptive and search/sort works without resolving the full
    message list.
    """
    for msg in messages:
        if msg.get("type") != "human":
            continue
        raw = msg.get("content")
        if isinstance(raw, str) and raw.strip():
            text = raw.strip()
            return text if len(text) <= 60 else text[:57] + "…"
        if isinstance(raw, list):
            for part in raw:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    text = part["text"].strip()
                    if text:
                        return text if len(text) <= 60 else text[:57] + "…"
    return None
