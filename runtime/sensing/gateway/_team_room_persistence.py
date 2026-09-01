"""Cross-process optimistic persistence for Team Room state."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi import HTTPException

from runtime.platform.io import path_transaction

from ._team_rooms_state import _load_state, _room_storage_payload, _save_state
from .team_rooms_models import TeamRoomWire


def _same_exact_room(left: TeamRoomWire, right: TeamRoomWire) -> bool:
    if not str(left.thread_id or "").strip() or left.thread_id != right.thread_id:
        return False
    left_payload = _room_storage_payload(left)
    right_payload = _room_storage_payload(right)
    for payload in (left_payload, right_payload):
        payload.pop("created_at", None)
        payload.pop("updated_at", None)
    return left_payload == right_payload


def merge_team_room_state(
    *,
    path: Path,
    local: dict[str, TeamRoomWire],
    baseline: dict[str, TeamRoomWire],
    legacy_tenant_for_owner: Callable[[str | None], str],
) -> dict[str, TeamRoomWire]:
    """Merge only this router's delta into the latest durable snapshot."""

    added = set(local).difference(baseline)
    deleted = set(baseline).difference(local)
    changed = {
        team_id
        for team_id in set(local).intersection(baseline)
        if local[team_id] != baseline[team_id]
    }
    with path_transaction(path):
        durable = _load_state(
            path,
            legacy_tenant_for_owner=legacy_tenant_for_owner,
            strict=True,
        )
        for team_id in changed | deleted:
            if durable.get(team_id) != baseline.get(team_id):
                raise HTTPException(
                    409,
                    {
                        "code": "TEAM_ROOM_STATE_CONFLICT",
                        "message": "team room changed in another server worker; retry",
                        "team_id": team_id,
                    },
                )
        merged = dict(durable)
        for team_id in deleted:
            merged.pop(team_id, None)
        for team_id in changed:
            merged[team_id] = local[team_id]
        for team_id in added:
            current = durable.get(team_id)
            if current is None:
                merged[team_id] = local[team_id]
            elif not _same_exact_room(current, local[team_id]):
                raise HTTPException(
                    409,
                    {
                        "code": "TEAM_ROOM_STATE_CONFLICT",
                        "message": "team room id was created in another server worker",
                        "team_id": team_id,
                    },
                )
        _save_state(path, merged)
        return merged


def delete_reserved_team_room_state(
    *,
    path: Path,
    team_id: str,
    tenant_id: str,
    owner_id: str,
    legacy_tenant_for_owner: Callable[[str | None], str],
) -> tuple[dict[str, TeamRoomWire], TeamRoomWire | None]:
    """Delete the latest scoped room after GroupStore granted a durable token."""

    with path_transaction(path):
        durable = _load_state(
            path,
            legacy_tenant_for_owner=legacy_tenant_for_owner,
            strict=True,
        )
        current = durable.get(team_id)
        if current is not None:
            if current.tenant_id != tenant_id or current.owner_id != owner_id:
                raise HTTPException(404, f"team not found: {team_id}")
            linked_thread_id = str(current.thread_id or "").strip()
            if linked_thread_id:
                raise HTTPException(
                    409,
                    {
                        "code": "TEAM_ROOM_LINKED",
                        "message": "unlink the collaboration thread before deleting this room",
                        "team_id": team_id,
                        "thread_id": linked_thread_id,
                    },
                )
        merged = dict(durable)
        merged.pop(team_id, None)
        _save_state(path, merged)
        return merged, current


def refresh_team_room_state(
    *,
    path: Path,
    legacy_tenant_for_owner: Callable[[str | None], str],
) -> dict[str, TeamRoomWire]:
    """Read one strict, cross-process-consistent durable room snapshot."""

    with path_transaction(path):
        return _load_state(
            path,
            legacy_tenant_for_owner=legacy_tenant_for_owner,
            strict=True,
        )


__all__ = [
    "delete_reserved_team_room_state",
    "merge_team_room_state",
    "refresh_team_room_state",
]
