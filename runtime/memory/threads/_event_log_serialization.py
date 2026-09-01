"""Serialization helpers and log-shrink transform for the event log.

Extracted from ``event_log.py`` to keep the writer/reader class focused on
file I/O. This module holds the cross-process file lock, the coalescing
transform used for full-log fetches, and a small path helper.
"""

from __future__ import annotations

import contextlib
import logging
import os
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .event_log import LoggedEvent

_logger = logging.getLogger(__name__)


@contextlib.contextmanager
def _exclusive_file_lock(stream: Any, path: Path) -> Iterator[None]:
    """Best-effort cross-process exclusive lock for one open file."""
    fd = stream.fileno()
    locked = False
    try:
        try:
            if os.name == "nt":
                import msvcrt

                stream.seek(0)
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX)
            locked = True
        except (ImportError, OSError) as exc:
            _logger.warning(
                "event-log: cross-process lock unavailable for %s (%s)",
                path.name,
                exc,
            )
        yield
    finally:
        if locked:
            with contextlib.suppress(ImportError, OSError):
                if os.name == "nt":
                    import msvcrt

                    stream.seek(0)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_UN)


# Delta kinds whose payload is an append-only text string.
_COALESCE_TEXT_KINDS = frozenset({"agentMessage", "reasoning", "plan", "commandOutput"})


def coalesce_events(
    events: list[tuple[int, LoggedEvent]],
) -> list[tuple[int, LoggedEvent]]:
    """Shrink a raw event slice without changing the state it rebuilds.

    Intended for full-log fetches (cold start / cache backfill), where
    shipping every delta of a long-finished item wastes the wire. The
    transform is replay-equivalent — ``EventLogSnapshot(events=coalesced)``
    replays to the same turns as the raw slice:

    - An item COMPLETED inside this slice carries its full snapshot in the
      ``item_completed`` event, so its earlier ``item_started`` and all
      earlier deltas are dropped. Deltas landing AFTER the completion
      (rare, but legal) are kept verbatim.
    - Surviving text deltas for one (turn, item, kind) merge into a single
      concatenated delta at the position of the first — folding N appends
      equals folding one concatenated append.
    - ``mcpToolProgress`` deltas are absolute patches; only the latest per
      item survives.
    - ``turn_updated`` payloads are per-field absolute patches; they merge
      per turn with later fields winning.
    - Everything else (turn lifecycle, compaction, hunks, completions)
      passes through untouched.

    Sequence semantics: each output event keeps the sequence and eventId
    of its FIRST contributor. Gaps are fine — consumers order by
    sequence and page by the response cursor, never by contiguity.

    IMPORTANT: merged events share an eventId with their first delta, so
    a client that applies live notifications MUST NOT use this mode when
    its dedupe ledger may already hold ids from the slice (a merged event
    re-delivers the text of deltas it already saw). Coalesce is for empty
    states and cache backfill only.
    """
    if not events:
        return []

    # Completion boundary per item: anything before it is redundant.
    completion_seq: dict[str, int] = {}
    for sequence, event in events:
        if event.event != "item_completed":
            continue
        item = event.payload.get("item")
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            completion_seq[item["id"]] = sequence

    out: list[tuple[int, LoggedEvent]] = []
    text_group_index: dict[tuple[str | None, str, str], int] = {}
    progress_index: dict[str, int] = {}
    turn_update_index: dict[str, int] = {}

    for sequence, event in events:
        kind = event.event
        payload = event.payload

        if kind == "item_started":
            item = payload.get("item")
            item_id = item.get("id") if isinstance(item, dict) else None
            boundary = completion_seq.get(item_id) if isinstance(item_id, str) else None
            if boundary is not None and sequence < boundary:
                continue  # the completed snapshot supersedes this start
            out.append((sequence, event))
            continue

        if kind == "item_delta":
            item_id = payload.get("itemId")
            delta_kind = payload.get("kind")
            boundary = completion_seq.get(item_id) if isinstance(item_id, str) else None
            if boundary is not None and sequence < boundary:
                continue  # the completed snapshot carries the full content
            if (
                isinstance(item_id, str)
                and isinstance(delta_kind, str)
                and delta_kind in _COALESCE_TEXT_KINDS
                and isinstance(payload.get("delta"), str)
            ):
                key = (event.turn_id, item_id, delta_kind)
                existing = text_group_index.get(key)
                if existing is None:
                    text_group_index[key] = len(out)
                    out.append((sequence, event))
                else:
                    first_seq, first_event = out[existing]
                    merged_payload = dict(first_event.payload)
                    merged_payload["delta"] = (
                        str(merged_payload.get("delta", "")) + payload["delta"]
                    )
                    merged_payload["coalesced"] = True
                    out[existing] = (
                        first_seq,
                        first_event.model_copy(update={"payload": merged_payload}),
                    )
                continue
            if (
                isinstance(item_id, str)
                and delta_kind == "mcpToolProgress"
                and isinstance(payload.get("delta"), dict)
            ):
                existing = progress_index.get(item_id)
                if existing is None:
                    progress_index[item_id] = len(out)
                    out.append((sequence, event))
                else:
                    first_seq, _first_event = out[existing]
                    out[existing] = (first_seq, event)
                continue
            out.append((sequence, event))
            continue

        if kind == "turn_updated" and event.turn_id:
            existing = turn_update_index.get(event.turn_id)
            if existing is None:
                turn_update_index[event.turn_id] = len(out)
                out.append((sequence, event))
            else:
                first_seq, first_event = out[existing]
                # Per-field absolute patches: later fields win.
                merged_payload = {**first_event.payload, **payload}
                out[existing] = (
                    first_seq,
                    first_event.model_copy(update={"payload": merged_payload}),
                )
            continue

        out.append((sequence, event))

    return out


def _thread_id_from_path(path: Path) -> str:
    return path.stem
