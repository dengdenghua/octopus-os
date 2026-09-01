"""Thread-id validation and actor sidecar helpers.

Extracted from ``event_log.py`` to keep the module focused on the log
writer/reader. These are pure helpers shared with the router layer.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from runtime.protocol.items import Turn, TurnParams

_THREAD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def validate_thread_id(thread_id: str) -> str:
    if not isinstance(thread_id, str) or not _THREAD_ID_RE.fullmatch(thread_id):
        raise ValueError("threadId must be 1-128 chars of letters, numbers, '_' or '-'")
    return thread_id


def thread_log_path(logs_root: Path | str, thread_id: str) -> Path:
    safe_thread_id = validate_thread_id(thread_id)
    return Path(logs_root) / f"{safe_thread_id}.jsonl"


def actor_id_from_turn_params(params: TurnParams | None) -> str | None:
    if params is None:
        return None
    for block in params.input:
        if not isinstance(block, dict):
            continue
        metadata = block.get("metadata")
        if not isinstance(metadata, dict):
            continue
        actor_id = metadata.get("actor_id") or metadata.get("actorId")
        if isinstance(actor_id, str) and actor_id.strip():
            return actor_id.strip()
    return None


def owner_actor_id_from_turns(turns: list[Turn]) -> str | None:
    for turn in turns:
        actor_id = actor_id_from_turn_params(turn.params)
        if actor_id is not None:
            return actor_id
    return None
