"""Durable, fail-closed Team Room deletion."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import HTTPException, Request


def _recovery_pending(team_id: str, token: str, exc: Exception) -> HTTPException:
    return HTTPException(
        409,
        {
            "code": "TEAM_ROOM_DELETE_RECOVERY_PENDING",
            "message": "team room deletion is reserved and must be retried",
            "team_id": team_id,
            "delete_token": token,
            "error_kind": type(exc).__name__,
            "retry": {"method": "DELETE", "path": f"/api/teams/{team_id}"},
        },
    )


def delete_team_room_fail_safe(
    *,
    request: Request,
    team_id: str,
    teams: dict[str, Any],
    lock: Any,
    group_store: Any,
    principal: Callable[[Any], Any],
    require_owner: Callable[[Any, str], str | None],
    invite_store: Any,
    save: Callable[[], None],
    delete_reserved_state: Callable[[str, str, str], Any],
    delete_projection: Callable[[str], None],
) -> dict[str, Any]:
    """Delete a room only after winning the canonical GroupStore reservation.

    Once the reservation exists the operation is roll-forward: failures retain
    the token so the same request can safely finish after a crash or retry.
    """

    resolved = principal(request)
    tenant_id = str(getattr(resolved, "tenant_id", "") or "local").strip() or "local"
    principal_actor = str(getattr(resolved, "actor_id", "") or "").strip()
    owner_id = principal_actor or "local"

    lease = None
    if group_store is not None:
        try:
            lease = group_store.room_delete_lease(
                team_id,
                tenant_id=tenant_id,
                owner_id=owner_id,
            )
        except PermissionError as exc:
            raise HTTPException(404, f"team not found: {team_id}") from exc

    if lease is None:
        actor = require_owner(request, team_id)
        with lock:
            existing = teams.get(team_id)
            if existing is not None:
                linked_thread_id = str(existing.thread_id or "").strip()
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
                tenant_id = str(existing.tenant_id or tenant_id).strip() or tenant_id
                owner_id = str(existing.owner_id or actor or owner_id).strip() or owner_id
        if group_store is not None:
            from runtime.memory.cowork.group_store import GroupRoomLinkedError

            try:
                lease = group_store.begin_room_delete(
                    team_id,
                    tenant_id=tenant_id,
                    owner_id=owner_id,
                )
            except GroupRoomLinkedError as exc:
                raise HTTPException(
                    409,
                    {
                        "code": "TEAM_ROOM_LINKED",
                        "message": "unlink the collaboration thread before deleting this room",
                        "team_id": team_id,
                        "thread_id": exc.thread_id,
                    },
                ) from exc

    if lease is not None and lease.finalized:
        return {"ok": True, "deleted": False, "team_id": team_id}

    token = str(getattr(lease, "token", "") or "")
    try:
        if lease is not None:
            with lock:
                existing = delete_reserved_state(team_id, tenant_id, owner_id)
        else:
            with lock:
                existing = teams.get(team_id)
                if existing is not None:
                    linked_thread_id = str(existing.thread_id or "").strip()
                    if linked_thread_id:
                        raise RuntimeError("team room became linked after delete reservation")
                    teams.pop(team_id, None)
                    try:
                        save()
                    except Exception:
                        teams[team_id] = existing
                        raise
        delete_projection(team_id)
        invite_store.revoke_room(
            tenant_id=tenant_id,
            room_id=team_id,
            revoked_by=principal_actor or "local",
        )
        if lease is not None:
            group_store.finalize_room_delete(team_id, token)
    except Exception as exc:
        if lease is not None:
            raise _recovery_pending(team_id, token, exc) from exc
        raise
    return {"ok": True, "deleted": existing is not None, "team_id": team_id}


__all__ = ["delete_team_room_fail_safe"]
