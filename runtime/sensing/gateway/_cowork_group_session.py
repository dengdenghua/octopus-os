"""Read-side projection helpers for unified cowork sessions."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any


class _SessionMessageSearch:
    def __init__(self, view: CoworkGroupSessionView, thread_id: str) -> None:
        self._view = view
        self._thread_id = thread_id

    def search(self, room_id: str, query: str, *, limit: int = 50) -> list[dict[str, Any]]:
        canonical = self._view.collaboration_store().search_messages(
            self._thread_id,
            query,
            limit=limit,
        )
        if canonical:
            return canonical
        return self._view.room_message_store().search(room_id, query, limit=limit)


class CoworkGroupSessionView:
    """Combine canonical cowork data with legacy Team Room projections."""

    def __init__(
        self,
        *,
        group_store: Any,
        collaboration_store: Callable[[], Any],
        async_store: Callable[[], Any],
        presence_store: Callable[[], Any],
        room_message_store: Callable[[], Any],
        team_rooms_state_path: Any,
        team_tasks_state_path: Any,
    ) -> None:
        self._group_store = group_store
        self.collaboration_store = collaboration_store
        self._async_store = async_store
        self._presence_store = presence_store
        self.room_message_store = room_message_store
        self._team_rooms_state_path = team_rooms_state_path
        self._team_tasks_state_path = team_tasks_state_path

    def room_participants(self, room_id: str) -> list[dict[str, Any]]:
        canonical = self.collaboration_store().room_by_id(room_id)
        if canonical is not None:
            participants = canonical.get("participants")
            return participants if isinstance(participants, list) else []

        from runtime.platform.process.paths import app_paths
        from runtime.sensing.gateway.team_rooms_router import _load_state

        path = self._team_rooms_state_path or (app_paths().data_dir / "team_rooms.json")
        room = _load_state(Path(path)).get(room_id)
        if room is None:
            return []
        return [participant.model_dump() for participant in room.participants]

    def room_tasks(self, room_id: str) -> list[dict[str, Any]]:
        canonical = self.collaboration_store().tasks_for_room(room_id)
        if canonical:
            return canonical

        from runtime.platform.process.paths import app_paths
        from runtime.sensing.gateway.team_tasks_router import _load_state

        path = self._team_tasks_state_path or (app_paths().data_dir / "team_tasks.json")
        tasks = _load_state(Path(path))
        return [task.model_dump() for task in tasks.values() if task.room_id == room_id]

    def room_messages(
        self,
        thread_id: str,
        room_id: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        canonical = self.collaboration_store().messages_for_session(thread_id, limit=limit)
        if canonical:
            return canonical
        try:
            return self.room_message_store().history(room_id, limit=limit)
        except Exception:  # noqa: BLE001 — linked-room transcript is best-effort
            return []

    def message_search(self, thread_id: str) -> _SessionMessageSearch:
        return _SessionMessageSearch(self, thread_id)

    def room_snapshot(self, room_id: str) -> dict[str, Any] | None:
        canonical = self.collaboration_store().room_by_id(room_id)
        if canonical is not None:
            return canonical

        from runtime.platform.process.paths import app_paths
        from runtime.sensing.gateway.team_rooms_router import _load_state

        path = self._team_rooms_state_path or (app_paths().data_dir / "team_rooms.json")
        room = _load_state(Path(path)).get(room_id)
        return room.model_dump() if room is not None else None

    def session_payload(self, thread_id: str) -> dict[str, Any]:
        from runtime.memory.cowork.session import resolve_session

        session = resolve_session(
            self._group_store,
            thread_id,
            async_store=self._async_store(),
            presence_store=self._presence_store(),
            room_message_store=None,
            room_messages_provider=lambda room_id: self.room_messages(thread_id, room_id),
            room_participants_provider=self.room_participants,
            room_tasks_provider=self.room_tasks,
        )
        return session.to_dict()

    def room_members_from_group(
        self,
        thread_id: str,
        *,
        state: Any = None,
    ) -> list[dict[str, Any]]:
        state = state or self._group_store.state(thread_id)
        return [
            {
                "name": member.id,
                "display_name": member.id,
                "description": "",
            }
            for member in state.roster
            if member.kind == "agent" and member.role == "participant" and not member.muted
        ]

    def room_members_for_projection(
        self,
        thread_id: str,
        *,
        existing: list[Any] | None = None,
        preferred: list[Any] | None = None,
        state: Any = None,
    ) -> list[dict[str, Any]]:
        """Project only canonical GroupStore agents while retaining display data."""

        details: dict[str, dict[str, Any]] = {}
        for candidates in (existing or [], preferred or []):
            for source in candidates:
                if hasattr(source, "model_dump"):
                    raw = source.model_dump()
                elif isinstance(source, dict):
                    raw = dict(source)
                else:
                    continue
                name = str(raw.get("name") or "").strip()
                if name:
                    details[name] = raw
        return [
            {
                **base,
                **details.get(str(base["name"]), {}),
                "name": str(base["name"]),
            }
            for base in self.room_members_from_group(thread_id, state=state)
        ]
