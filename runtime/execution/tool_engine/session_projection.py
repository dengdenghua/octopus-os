"""Byte-bounded projection of a session's conversation surface.

Ported from DeepSeek Harness' ``@deepseek-ai/dsh-session-reference``
projection (``projection.ts`` + ``serialization.ts`` + the compaction
checkpoint marker): materialize a read-only snapshot of a session's current
user/assistant conversation — excluding tools, reasoning, and injected
context — into an exact byte-bounded JSON object, with retention stats.

Two-phase retention (dsh ``retainReferencedSession``):
1. Drop whole non-checkpoint messages (oldest non-checkpoint first, keeping
   the newest) until the serialized snapshot fits ``max_bytes``.
2. Binary-search the longest retained message's head/tail truncation with an
   exact ``[… omitted N UTF-8 bytes …]`` notice until it fits.
3. If the fixed fields alone cannot fit even after dropping/truncating
   everything, return ``None`` instead of a partial context (dsh budget
   contract — the caller surfaces a budget-exceeded failure rather than a
   truncated-at-any-cost projection).

The event surface is duck-typed after dsh's ``SessionSurfaceSnapshot``:
``user/message`` (``data.source`` + ``data.content`` text blocks),
``assistant/message`` (``data.message.content`` text blocks), and
``tool/result`` (skipped). Non-text blocks are ignored; empty projections are
dropped. Serialization escapes every ``<`` as ``\\u003c`` so source text can
never spell a framing tag (dsh ``stringifyTagSafeJson``).
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, is_dataclass, replace
from typing import Any

from .tool_output_spill import head_tail_preview_bytes

_COMPACT_PLUGIN_MARKER = "compact"

# `[… omitted N UTF-8 bytes …]` — dsh's truncation notice appended by
# ``truncateWithNotice``.
_OMISSION_NOTICE = "\n[… omitted {omitted} UTF-8 bytes …]"


@dataclass(frozen=True, slots=True)
class ProjectedItem:
    """One projected conversation unit (user or assistant text)."""

    role: str
    text: str
    checkpoint: bool
    original_text: str
    omitted_bytes: int


@dataclass(frozen=True, slots=True)
class ReferencedSessionData:
    """Snapshot data serialized into the model-facing reference."""

    session_id: str
    label: str
    cwd: str | None
    captured_through_seq: int | None
    conversation: list[dict[str, str]]


@dataclass(frozen=True, slots=True)
class ReferenceRetentionStats:
    """Retention facts beside the projected snapshot."""

    compacted: bool
    original_messages: int
    retained_messages: int
    omitted_messages: int
    omitted_bytes: int
    truncated: bool


@dataclass(frozen=True, slots=True)
class TruncatedText:
    """One message shortened to a byte budget with an exact omission count."""

    text: str
    omitted_bytes: int


def is_compact_checkpoint_source(source: Any) -> bool:
    """Whether a persisted message source identifies a compaction checkpoint.

    Mirrors dsh ``isCompactCheckpointSource``: ``{kind: 'plugin',
    plugin: 'compact'}`` is the backend-independent checkpoint marker.
    """
    return (
        isinstance(source, dict)
        and source.get("kind") == "plugin"
        and source.get("plugin") == _COMPACT_PLUGIN_MARKER
    )


def _text_content(blocks: Any) -> str:
    """Flatten text content blocks to one string (non-text blocks skipped)."""
    if not isinstance(blocks, list):
        return ""
    parts: list[str] = []
    for block in blocks:
        if (
            isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        ):
            parts.append(block["text"])
    return "\n".join(parts)


def project_session_conversation(events: Any) -> list[ProjectedItem]:
    """Project the user/assistant surface, excluding tools and injected context.

    Only direct ``user`` (or compaction-checkpoint) and assistant text
    messages are kept; tool results, reasoning, plugin-generated user
    messages (other than marked checkpoints), and empty texts are skipped —
    mirroring dsh ``projectSessionConversation``.
    """
    conversation: list[ProjectedItem] = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        etype = event.get("type")
        data = event.get("data") or {}
        if etype == "user/message":
            source = data.get("source")
            checkpoint = is_compact_checkpoint_source(source)
            if not checkpoint and (not isinstance(source, dict) or source.get("kind") != "user"):
                continue
            text = _text_content(data.get("content"))
            if text != "":
                conversation.append(
                    ProjectedItem(
                        role="user",
                        text=text,
                        checkpoint=checkpoint,
                        original_text=text,
                        omitted_bytes=0,
                    )
                )
        elif etype == "assistant/message":
            message = data.get("message") or {}
            text = _text_content(message.get("content"))
            if text != "":
                conversation.append(
                    ProjectedItem(
                        role="assistant",
                        text=text,
                        checkpoint=False,
                        original_text=text,
                        omitted_bytes=0,
                    )
                )
        # tool/result and unknown event kinds are excluded from the surface.
    return conversation


def stringify_tag_safe_json(value: Any) -> str:
    """Compact JSON with every ``<`` escaped as ``\\u003c``.

    The parse result is unchanged and the data contains no literal ``<``, so
    source text cannot spell an XML-like framing tag (dsh
    ``stringifyTagSafeJson``).
    """
    if is_dataclass(value):
        value = asdict(value)
    serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return serialized.replace("<", "\\u003c")


def truncate_with_notice(text: str, max_output_bytes: int) -> TruncatedText:
    """Binary-search a head/tail truncation of ``text`` that fits the budget.

    Returns the largest head/tail preview (with an exact ``[… omitted N UTF-8
    bytes …]`` notice appended) whose total UTF-8 size is at most
    ``max_output_bytes``. When the source already fits, returns it unchanged.
    """
    total = len(text.encode("utf-8"))
    if total <= max_output_bytes:
        return TruncatedText(text=text, omitted_bytes=0)
    low = 0
    high = max_output_bytes
    best = TruncatedText(text="", omitted_bytes=total)
    while low <= high:
        retained_bytes = (low + high) // 2
        head_bytes = math.ceil(retained_bytes / 2)
        tail_bytes = math.floor(retained_bytes / 2)
        preview, omitted = head_tail_preview_bytes(
            text,
            head_bytes=head_bytes,
            tail_bytes=tail_bytes,
        )
        candidate = f"{preview}{_OMISSION_NOTICE.format(omitted=omitted)}"
        if len(candidate.encode("utf-8")) <= max_output_bytes:
            best = TruncatedText(text=candidate, omitted_bytes=omitted)
            low = retained_bytes + 1
        else:
            high = retained_bytes - 1
    return best


def retain_session_reference(
    events: Any,
    *,
    session_id: str,
    label: str,
    max_bytes: int,
    cwd: str | None = None,
    captured_through_seq: int | None = None,
) -> tuple[ReferencedSessionData, ReferenceRetentionStats] | None:
    """Fit one projected session snapshot into an exact byte cap.

    Returns ``(data, stats)`` or ``None`` when the fixed fields alone cannot
    fit (dsh budget contract: never a partial context). The snapshot keeps
    compaction checkpoints and the newest message before dropping older
    non-checkpoint messages, then head/tail-truncates the longest retained
    message until the whole serialized object fits ``max_bytes``.
    """
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 0:
        raise ValueError(
            f"retain_session_reference: max_bytes ({max_bytes!r}) must be a non-negative integer"
        )
    original = project_session_conversation(events)
    retained = [replace(item) for item in original]
    omitted_messages = 0
    dropped_omitted_bytes = 0

    def data_dict() -> dict[str, Any]:
        return {
            "session_id": session_id,
            "label": label,
            "cwd": cwd,
            "captured_through_seq": captured_through_seq,
            "conversation": [{"role": item.role, "text": item.text} for item in retained],
        }

    def size() -> int:
        return len(stringify_tag_safe_json(data_dict()).encode("utf-8"))

    # Phase 1 · drop whole non-checkpoint messages, oldest first, keep newest.
    while size() > max_bytes:
        newest_index = len(retained) - 1
        drop_index = next(
            (
                index
                for index, item in enumerate(retained)
                if not item.checkpoint and index != newest_index
            ),
            -1,
        )
        if drop_index < 0:
            break
        removed = retained.pop(drop_index)
        omitted_messages += 1
        dropped_omitted_bytes += len(removed.original_text.encode("utf-8"))

    # Phase 2 · binary-search the longest retained message's truncation.
    while size() > max_bytes:
        longest_index = -1
        longest_bytes = 0
        for index, item in enumerate(retained):
            item_bytes = len(item.text.encode("utf-8"))
            if item_bytes > longest_bytes:
                longest_bytes = item_bytes
                longest_index = index
        if longest_index < 0 or longest_bytes == 0:
            return None
        overflow = size() - max_bytes
        target = max(0, longest_bytes - overflow)
        item = retained[longest_index]
        shortened = truncate_with_notice(item.original_text, target)
        if shortened.text == item.text:
            return None
        retained[longest_index] = replace(
            item,
            text=shortened.text,
            omitted_bytes=shortened.omitted_bytes,
        )

    compacted = any(item.checkpoint for item in original)
    retained_omitted_bytes = sum(item.omitted_bytes for item in retained)
    omitted_bytes = retained_omitted_bytes + dropped_omitted_bytes
    stats = ReferenceRetentionStats(
        compacted=compacted,
        original_messages=len(original),
        retained_messages=len(retained),
        omitted_messages=omitted_messages,
        omitted_bytes=omitted_bytes,
        truncated=(omitted_messages > 0 or omitted_bytes > 0),
    )
    return ReferencedSessionData(**data_dict()), stats


__all__ = [
    "ProjectedItem",
    "ReferenceRetentionStats",
    "ReferencedSessionData",
    "TruncatedText",
    "is_compact_checkpoint_source",
    "project_session_conversation",
    "retain_session_reference",
    "stringify_tag_safe_json",
    "truncate_with_notice",
]
