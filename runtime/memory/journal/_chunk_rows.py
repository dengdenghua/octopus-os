"""Lossless storage packing for delta-chunk runs (dsh ``chunk-rows``).

Streamed reply fragments (``assistant/chunk``) and sub-agent prose
chunks (``sub_text_delta``) are token-sized, so an un-packed journal
stores hundreds of near-identical JSONL lines whose envelopes dwarf
their payloads — the same 56x overhead dsh measured on a real
DeepSeek session. This module packs each run of at least
``MIN_RUN`` consecutive same-shape chunk events into ONE storage row
and expands rows back to the exact original events on read.

Storage rows are a durable-encoding vocabulary, NOT session events:
they carry a ``__chunk_row__`` marker (precedent: dsh's slash-less
row tags) so a reader can never confuse them with the event
taxonomy. The encoder whitelists exact shapes — anything it does not
fully recognize is stored verbatim, so unknown fields or future
chunk variants lose compression, never data. The decoder validates
before expanding and raises on a malformed row instead of silently
dropping part of a run.

Token boundaries are data: members are never joined, only listed, so
expanding a row yields the same number of events, in the same order,
with the same ids and timestamps.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

MIN_RUN = 3

_CHUNK_ROW_MARKER = "__chunk_row__"
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

# Envelope keys that must be identical across a packed run. Values are
# JSON-safe (``model_dump(mode="json")``), so plain equality is exact.
_COMMON_KEYS = (
    "schema_version",
    "task_id",
    "arm_id",
    "actor",
    "tenant_id",
    "owner_actor_id",
    "agent_id",
    "conversation_id",
    "source",
)

# Per-event-type keys that must be identical across a packed run.
_EXTRA_KEYS: dict[str, tuple[str, ...]] = {
    # ``tool-call-delta`` fragments carry optional call identity
    # (index/call_id/name); including them keeps packed rows lossless.
    # Defaults keep text/reasoning runs identical to before.
    "assistant/chunk": ("iteration", "kind", "index", "call_id", "name"),
    "sub_text_delta": ("role_id", "round", "parent_tool_use_id", "session_id"),
}

_PACKABLE_TYPES = frozenset(_EXTRA_KEYS)


def chunk_packing_enabled() -> bool:
    """Whether the JSONL writer may pack chunk runs.

    Reads ``ECHO_JOURNAL_CHUNK_PACKING`` fresh on each call; a
    literal ``"0"`` disables packing (readers always handle both
    encodings, so this is a pure rollback knob for the write side).
    """
    return os.environ.get("ECHO_JOURNAL_CHUNK_PACKING", "1").strip() != "0"


def _iso_to_us(value: str) -> int:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int((parsed - _EPOCH).total_seconds() * 1_000_000)


def _us_to_iso(value: int) -> str:
    # Pydantic serialises UTC datetimes with a ``Z`` suffix; emit the
    # same spelling so packed rows decode to byte-identical JSON.
    return (_EPOCH + timedelta(microseconds=int(value))).isoformat().replace("+00:00", "Z")


def is_chunk_row(data: dict[str, Any]) -> bool:
    """Whether a decoded JSONL line is a packed chunk row (not an event)."""
    return data.get(_CHUNK_ROW_MARKER) == 1


def classify_chunk(event: Any) -> dict[str, Any] | None:
    """Classify a typed event for packing, or ``None`` (store verbatim).

    Structural, not type-trusted: the check runs on the JSON dump so
    parsed fixture files classify identically to live appends. Only
    exact shapes pack — unknown fields or variants degrade to
    verbatim, never data loss.
    """
    data = event.model_dump(mode="json")
    event_type = data.get("event_type")
    if event_type not in _PACKABLE_TYPES:
        return None
    delta = data.get("delta")
    if not isinstance(delta, str) or not delta:
        return None
    ts = data.get("ts")
    if not isinstance(ts, str):
        return None
    extra = {key: data.get(key) for key in _EXTRA_KEYS[event_type]}
    if not all(
        isinstance(value, (str, int, float, bool)) or value is None for value in extra.values()
    ):
        return None
    return {
        "event_type": event_type,
        "common": {key: data.get(key) for key in _COMMON_KEYS},
        "extra": extra,
        "event_id": data.get("event_id"),
        "ts_us": _iso_to_us(ts),
        "delta": delta,
    }


def continues_chunk_run(prev: dict[str, Any], entry: dict[str, Any]) -> bool:
    """Whether ``entry`` extends a run ending in ``prev``.

    Same event type, identical envelope (common + extra), and a
    strictly increasing timestamp — the same-block, same-call
    continuity dsh enforces with its seq/block checks.
    """
    return (
        entry["event_type"] == prev["event_type"]
        and entry["common"] == prev["common"]
        and entry["extra"] == prev["extra"]
        and entry["ts_us"] > prev["ts_us"]
    )


def pack_chunk_row(run: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the storage row for a completed run (``len(run) >= MIN_RUN``)."""
    first = run[0]
    return {
        _CHUNK_ROW_MARKER: 1,
        "event_type": first["event_type"],
        "count": len(run),
        "ts0_us": first["ts_us"],
        "dt_us": [entry["ts_us"] - run[i - 1]["ts_us"] for i, entry in enumerate(run) if i],
        "common": first["common"],
        "extra": first["extra"],
        "members": [{"event_id": entry["event_id"], "delta": entry["delta"]} for entry in run],
    }


def expand_chunk_row(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Expand a chunk row back to the exact original event dicts.

    Fail-loud on a malformed row: silently dropping part of a run
    would reconstruct a wrong session.
    """
    if not is_chunk_row(data):
        raise ValueError("not a chunk row")
    event_type = data.get("event_type")
    if event_type not in _PACKABLE_TYPES:
        raise ValueError(f"unknown packed event type: {event_type!r}")
    common = data.get("common")
    extra = data.get("extra")
    members = data.get("members")
    count = data.get("count")
    ts0 = data.get("ts0_us")
    dt = data.get("dt_us")
    if not isinstance(common, dict) or not isinstance(extra, dict):
        raise ValueError("malformed chunk row: common/extra")
    if not isinstance(members, list) or not isinstance(count, int) or count < MIN_RUN:
        raise ValueError("malformed chunk row: count/members")
    if len(members) != count:
        raise ValueError("malformed chunk row: member count mismatch")
    if not isinstance(ts0, int) or not isinstance(dt, list) or len(dt) != count - 1:
        raise ValueError("malformed chunk row: timestamps")
    if not all(isinstance(gap, int) for gap in dt):
        raise ValueError("malformed chunk row: non-integer gap")

    events: list[dict[str, Any]] = []
    ts = int(ts0)
    for member in members:
        if not isinstance(member, dict):
            raise ValueError("malformed chunk row: member")
        event_id = member.get("event_id")
        delta = member.get("delta")
        if not isinstance(delta, str):
            raise ValueError("malformed chunk row: delta")
        event = dict(common)
        event.update(extra)
        event.update(
            {
                "event_type": event_type,
                "event_id": event_id,
                "ts": _us_to_iso(ts),
                "delta": delta,
            }
        )
        events.append(event)
        if dt:
            ts += dt.pop(0)
    return events
