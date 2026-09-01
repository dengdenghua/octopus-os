"""Persistence and wire-serialization helpers for Team Rooms."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any
from uuid import uuid4

from .team_rooms_models import TeamRoomWire
from .team_speaker_policy import _now


def _slug_id(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.strip().lower()).strip("-")
    return f"team-{slug or uuid4().hex[:8]}"


def _unique_team_id(candidate: str, teams: dict[str, TeamRoomWire]) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_-]+", "-", candidate.strip()).strip("-")
    clean = clean or f"team-{uuid4().hex[:8]}"
    if clean not in teams:
        return clean
    return f"{clean}-{uuid4().hex[:6]}"


def _room_payload(
    team: TeamRoomWire,
    *,
    join_policy: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Serialize a room without ever exposing legacy invitation secrets."""

    payload = team.model_dump()
    override = payload.pop("join_policy_override", None)
    payload["join_policy"] = join_policy or override or "direct_join"
    payload["is_project_group"] = project_id is not None
    payload["project_id"] = project_id
    for field in ("invite_token", "invite_role", "invite_created_at"):
        payload.pop(field, None)
    return payload


def _room_storage_payload(team: TeamRoomWire) -> dict[str, Any]:
    """Persist the nullable override, not a computed environment default."""

    payload = team.model_dump()
    for field in ("invite_token", "invite_role", "invite_created_at", "join_policy"):
        payload.pop(field, None)
    return payload


def _load_state(
    path: Path,
    *,
    legacy_tenant_for_owner: Callable[[str | None], str] | None = None,
    strict: bool = False,
) -> dict[str, TeamRoomWire]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        if strict:
            raise RuntimeError(f"invalid Team Room state: {path}") from exc
        return {}
    items = raw.get("teams") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        if strict:
            raise RuntimeError(f"invalid Team Room state payload: {path}")
        return {}
    out: dict[str, TeamRoomWire] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        clean = dict(item)
        # Pre-hardening snapshots embedded the raw bearer token in each room.
        # Ignore it on read so the next save/projection scrubs it permanently.
        for field in ("invite_token", "invite_role", "invite_created_at"):
            clean.pop(field, None)
        legacy_policy = clean.pop("join_policy", None)
        if not clean.get("join_policy_override") and legacy_policy in {
            "direct_join",
            "apply_then_join",
        }:
            clean["join_policy_override"] = legacy_policy
        if not str(clean.get("tenant_id") or "").strip():
            clean["tenant_id"] = (
                legacy_tenant_for_owner(clean.get("owner_id"))
                if legacy_tenant_for_owner is not None
                else "local"
            )
        try:
            team = TeamRoomWire.model_validate(clean)
        except (ValueError, TypeError) as exc:
            if strict:
                raise RuntimeError(f"invalid Team Room entry in {path}") from exc
            continue
        out[team.id] = team
    return out


def _save_state(path: Path, teams: dict[str, TeamRoomWire]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "teams": [_room_storage_payload(team) for team in teams.values()],
        "updated_at": _now(),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


def _refresh_project_room_bindings(
    thread_id: str,
    *,
    lock: Any,
    teams: dict[str, TeamRoomWire],
    public_room_payload: Callable[[TeamRoomWire], dict[str, Any]],
    room_projection: Callable[[dict[str, Any]], None] | None,
    refresh: Callable[[], None],
) -> list[dict[str, Any]]:
    """Refresh computed project fields without mutating canonical room state."""

    canonical_thread_id = str(thread_id or "").strip()
    if not canonical_thread_id:
        raise ValueError("thread_id is required")
    refresh()
    with lock:
        snapshots = [
            public_room_payload(team)
            for team in teams.values()
            if str(team.thread_id or "").strip() == canonical_thread_id
        ]
    if room_projection is not None:
        for snapshot in snapshots:
            room_projection(dict(snapshot))
    return snapshots


def _team_snapshot(
    team_id: str,
    *,
    lock: Any,
    teams: dict[str, TeamRoomWire],
    public_room_payload: Callable[[TeamRoomWire], dict[str, Any]],
    refresh: Callable[[], None],
) -> dict[str, Any] | None:
    """Return one exact current room payload for saga recovery decisions."""

    refresh()
    with lock:
        current = teams.get(team_id)
        return public_room_payload(current) if current is not None else None


def _team_project_helpers(
    *,
    lock: Any,
    teams: dict[str, TeamRoomWire],
    public_room_payload: Callable[[TeamRoomWire], dict[str, Any]],
    room_projection: Callable[[dict[str, Any]], None] | None,
    refresh: Callable[[], None],
) -> tuple[Callable[[str], list[dict[str, Any]]], Callable[[str], dict[str, Any] | None]]:
    """Bind project recovery helpers to one router's in-memory room state."""

    return (
        partial(
            _refresh_project_room_bindings,
            lock=lock,
            teams=teams,
            public_room_payload=public_room_payload,
            room_projection=room_projection,
            refresh=refresh,
        ),
        partial(
            _team_snapshot,
            lock=lock,
            teams=teams,
            public_room_payload=public_room_payload,
            refresh=refresh,
        ),
    )
