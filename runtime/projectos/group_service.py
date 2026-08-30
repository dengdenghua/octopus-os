"""Single-boundary creation for a Project OS collaboration group.

The service preallocates the project id so a planner that commits and then
raises can still be recovered.  No committed project or collaboration surface
is automatically deleted: failures preserve durable state and record recovery
when a project row may exist.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import suppress
from typing import Any
from uuid import uuid4

from runtime.memory.cowork.session import link_room
from runtime.projectos.cowork_bridge import full_project_state
from runtime.sensing.gateway.thread_workspace import ensure_managed_thread_workspace

_LOG = logging.getLogger(__name__)

_WHITE_GHOST_AGENT_IDS = frozenset(
    {
        "general",
        "coder",
        "desktop_operator",
        "vibe_selling",
        "ecommerce_mind",
        "market_researcher",
        "aoi",
        "admin",
    }
)


def _persona_led_agents(agents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one fixed persona as owner and every other role as a member."""

    normalized = [dict(agent) for agent in agents]
    leader_index = next(
        (
            index
            for index, agent in enumerate(normalized)
            if str(agent.get("id") or "") in _WHITE_GHOST_AGENT_IDS
        ),
        -1,
    )
    if leader_index < 0:
        normalized.insert(0, {"id": "general", "display_name": "通用助手"})
    elif leader_index > 0:
        normalized.insert(0, normalized.pop(leader_index))
    return normalized


class ProjectGroupCreationRecoveryPending(RuntimeError):
    """Creation crossed a public boundary and was preserved for recovery."""

    def __init__(
        self,
        *,
        project_id: str,
        thread_id: str,
        room_id: str,
        creation_id: str,
        surfaces: list[str],
        recovery_event_id: str = "",
        recovery_recorded: bool = False,
    ) -> None:
        super().__init__("project group creation recovery is pending")
        self.project_id = project_id
        self.thread_id = thread_id
        self.room_id = room_id
        self.creation_id = creation_id
        self.surfaces = list(surfaces)
        self.recovery_event_id = recovery_event_id
        self.recovery_recorded = recovery_recorded

    def detail(self) -> dict[str, Any]:
        return {
            "code": "PROJECT_GROUP_CREATION_RECOVERY_PENDING",
            "project_id": self.project_id,
            "thread_id": self.thread_id,
            "room_id": self.room_id,
            "creation_id": self.creation_id,
            "surfaces": self.surfaces,
            "recovery_event_id": self.recovery_event_id,
            "recovery_recorded": self.recovery_recorded,
        }


class ProjectGroupBindingChanged(RuntimeError):
    """Creation lost its binding generation before the response boundary."""

    def __init__(
        self,
        *,
        thread_id: str,
        requested_project_id: str,
        winner_project_id: str,
        binding_generation: int,
    ) -> None:
        super().__init__("project group binding changed during creation")
        self.thread_id = thread_id
        self.requested_project_id = requested_project_id
        self.winner_project_id = winner_project_id
        self.binding_generation = binding_generation

    def detail(self) -> dict[str, Any]:
        return {
            "code": "PROJECT_BINDING_CHANGED",
            "message": "thread project binding changed while the group was being created",
            "thread_id": self.thread_id,
            "requested_project_id": self.requested_project_id,
            "winner_project_id": self.winner_project_id,
            "binding_generation": self.binding_generation,
        }


class _ProjectGroupBindingRepairFailed(RuntimeError):
    def __init__(
        self,
        *,
        requested_project_id: str,
        winner_project_id: str,
        binding_generation: int,
    ) -> None:
        super().__init__("project group winner projection repair failed")
        self.requested_project_id = requested_project_id
        self.winner_project_id = winner_project_id
        self.binding_generation = binding_generation


class ProjectGroupCreationService:
    """Create one group, preserving published surfaces for durable recovery."""

    def __init__(
        self,
        *,
        project_store: Any,
        group_store: Any,
        collaboration_store: Any,
        team_rooms_router: Any,
        thread_store: Any,
        workspace_root: Any = None,
        require_auth: bool = False,
    ) -> None:
        self.project_store = project_store
        self.group_store = group_store
        self.collaboration_store = collaboration_store
        self.team_rooms_router = team_rooms_router
        self.thread_store = thread_store
        self.workspace_root = workspace_root
        self.require_auth = require_auth
        bind_group_store = getattr(self.team_rooms_router, "bind_group_store", None)
        if callable(bind_group_store):
            bind_group_store(self.group_store)

    def _require_wiring(self) -> None:
        required = {
            "thread creation": getattr(self.thread_store, "ensure_thread", None),
            "project binding": getattr(self.project_store, "bind_thread_versioned", None),
            "thread project projection": getattr(
                self.thread_store,
                "set_project_binding_metadata",
                None,
            ),
            "group roster": getattr(self.group_store, "replace_agent_roster", None),
            "room creation": getattr(self.team_rooms_router, "create_team_from_payload", None),
            "room binding": getattr(self.team_rooms_router, "bind_team_thread", None),
            "room lookup": getattr(self.team_rooms_router, "team_snapshot", None),
            "collaboration projection": getattr(self.collaboration_store, "upsert_room", None),
            "versioned collaboration projection": getattr(
                self.collaboration_store,
                "upsert_project_room",
                None,
            ),
            "collaboration project binding": getattr(
                self.collaboration_store,
                "set_room_project_metadata",
                None,
            ),
        }
        missing = [label for label, callback in required.items() if not callable(callback)]
        if missing:
            raise RuntimeError(f"project group creation is not wired: {', '.join(missing)}")

    def _shared_surfaces(self, project_id: str, thread_id: str, room_id: str) -> list[str]:
        """Describe preserved surfaces; uncertainty is explicit and fail-closed."""

        surfaces: list[str] = []
        try:
            if self.project_store.get_project(project_id) is not None:
                surfaces.append("project")
            if thread_id and self.thread_store.get(thread_id) is not None:
                surfaces.append("thread")
            if thread_id and self.project_store.project_for_thread(thread_id) is not None:
                surfaces.append("project_binding")
            if thread_id and self.group_store.events(thread_id):
                surfaces.append("group_events")
            if thread_id and self.group_store.blackboard_snapshot(thread_id):
                surfaces.append("group_blackboard")
            if (thread_id and self.collaboration_store.room_for_session(thread_id) is not None) or (
                room_id and self.collaboration_store.room_by_id(room_id) is not None
            ):
                surfaces.append("collaboration_room")
            if room_id and self.team_rooms_router.team_snapshot(room_id) is not None:
                surfaces.append("team_room")
        except Exception:  # noqa: BLE001 - uncertainty must preserve, never delete
            _LOG.exception("project group shared-surface detection failed")
            surfaces.append("unknown")
        return list(dict.fromkeys(surfaces))

    def _record_recovery_pending(
        self,
        *,
        project_id: str,
        thread_id: str,
        room_id: str,
        creation_id: str,
        surfaces: list[str],
        failure: BaseException,
    ) -> ProjectGroupCreationRecoveryPending:
        recovery_event_id = ""
        recovery_recorded = False
        try:
            binding_context = {
                key: value
                for key in (
                    "requested_project_id",
                    "winner_project_id",
                    "binding_generation",
                )
                if (value := getattr(failure, key, None)) is not None
            }
            event = self.project_store.append_event(
                project_id,
                kind="project.group_creation_recovery_pending",
                payload={
                    "creation_id": creation_id,
                    "thread_id": thread_id,
                    "room_id": room_id,
                    "surfaces": surfaces,
                    "failure_type": type(failure).__name__,
                    "failure": str(failure)[:500],
                    **binding_context,
                },
            )
            recovery_event_id = str(event["id"])
            recovery_recorded = True
        except BaseException:  # noqa: BLE001 - preserve data even when journaling fails
            _LOG.exception("project group recovery event could not be recorded")
        return ProjectGroupCreationRecoveryPending(
            project_id=project_id,
            thread_id=thread_id,
            room_id=room_id,
            creation_id=creation_id,
            surfaces=surfaces,
            recovery_event_id=recovery_event_id,
            recovery_recorded=recovery_recorded,
        )

    def _require_final_binding(
        self,
        *,
        thread_id: str,
        requested_project_id: str,
        requested_generation: int,
    ) -> None:
        """Repair a lost generation to its winner and forbid a stale 200."""

        canonical, generation = self.project_store.binding_snapshot(thread_id)
        winner_id = str(getattr(canonical, "id", "") or "")
        if winner_id == requested_project_id and generation == requested_generation:
            return
        try:
            self.thread_store.set_project_binding_metadata(
                thread_id,
                winner_id or None,
                generation=generation,
            )
            self.collaboration_store.set_room_project_metadata(
                thread_id,
                winner_id or None,
                generation=generation,
            )
            refresh = getattr(self.team_rooms_router, "refresh_project_binding", None)
            if callable(refresh):
                refresh(thread_id)
        except Exception as exc:
            raise _ProjectGroupBindingRepairFailed(
                requested_project_id=requested_project_id,
                winner_project_id=winner_id,
                binding_generation=generation,
            ) from exc
        raise ProjectGroupBindingChanged(
            thread_id=thread_id,
            requested_project_id=requested_project_id,
            winner_project_id=winner_id,
            binding_generation=generation,
        )

    def create(
        self,
        *,
        request: Any,
        name: str,
        goal: str,
        agents: list[dict[str, Any]],
        actor_id: str,
        tenant_id: str,
        plan_project: Callable[[str], Any],
    ) -> dict[str, Any]:
        """Create all surfaces, preserving every committed project for recovery."""

        self._require_wiring()
        agents = _persona_led_agents(agents)
        agent_ids = [str(agent["id"]) for agent in agents]
        mode = "cluster" if len(agent_ids) > 1 else "chat"
        primary_agent_id = agent_ids[0]
        project: Any = None
        project_id = f"P-{uuid4().hex}"
        thread_id = ""
        room_id = ""
        creation_id = uuid4().hex
        try:
            project = plan_project(project_id)
            if str(getattr(project, "id", "") or "") != project_id:
                raise RuntimeError("project planner returned an invalid project id")

            metadata = {
                "mode": "code",
                "agent_name": primary_agent_id,
                "group_creation_id": creation_id,
                "title": project.name,
            }
            if actor_id:
                metadata["owner_actor_id"] = actor_id
                metadata["tenant_id"] = tenant_id
            values = {
                "title": project.name,
                "agent_name": primary_agent_id,
            }
            # Pick the opaque collaboration ids before the first public write.
            # The project id was allocated even earlier so a post-commit planner
            # failure can be recovered without learning or deleting new state.
            thread_id = uuid4().hex
            room_id = f"collab-{thread_id}"
            thread = self.thread_store.ensure_thread(
                thread_id,
                metadata=metadata,
                values=values,
            )
            if str(thread.get("thread_id") or "") != thread_id:
                raise RuntimeError("thread store returned an invalid thread id")
            if self.require_auth:
                ensure_managed_thread_workspace(
                    self.workspace_root,
                    thread_id=thread_id,
                    actor_id=actor_id,
                    tenant_id=tenant_id,
                    store=self.thread_store,
                )

            _bound_project, binding_generation = self.project_store.bind_thread_versioned(
                thread_id,
                project.id,
            )
            thread = self.thread_store.set_project_binding_metadata(
                thread_id,
                project.id,
                generation=binding_generation,
            )
            _events, group_state = self.group_store.replace_agent_roster(
                thread_id,
                actor=actor_id or "system",
                agent_ids=agent_ids,
                mode=mode,
            )

            members = [
                {
                    "name": str(agent["id"]),
                    "display_name": str(agent.get("display_name") or agent["id"]),
                    "description": str(agent.get("description") or ""),
                    **({"avatar_url": agent["avatar_url"]} if agent.get("avatar_url") else {}),
                    **({"icon": agent["icon"]} if agent.get("icon") else {}),
                }
                for agent in agents
            ]
            room = self.team_rooms_router.create_team_from_payload(
                request,
                {
                    "id": room_id,
                    "name": project.name,
                    "members": members,
                    "leaderId": primary_agent_id,
                },
            )
            returned_room_id = str((room or {}).get("id") or "")
            if returned_room_id != room_id:
                raise RuntimeError("team room creator returned an invalid id")
            group_state = link_room(
                self.group_store,
                thread_id,
                room_id,
                actor=actor_id or "system",
            )
            room = self.team_rooms_router.bind_team_thread(request, room_id, thread_id)

            projected_room = self.collaboration_store.upsert_project_room(
                session_id=thread_id,
                room={
                    **dict(room),
                    "metadata": {
                        "group_creation_id": creation_id,
                        "tenant_id": tenant_id,
                        "thread_id": thread_id,
                    },
                },
                project_id=project.id,
                generation=binding_generation,
            )
            projected_room = self.collaboration_store.set_room_project_metadata(
                thread_id,
                project.id,
                generation=binding_generation,
            )
            if projected_room is None:
                raise RuntimeError("collaboration project room is unavailable")
            project_state = full_project_state(self.project_store, project.id)
            if project_state is None:
                raise RuntimeError("created project state is unavailable")
        except Exception as creation_error:
            project_probe_failed = False
            if project is None:
                try:
                    project = self.project_store.get_project(project_id)
                except BaseException:  # noqa: BLE001 - uncertainty must preserve
                    project_probe_failed = True
                    _LOG.exception("project group plan commit probe failed")
                if project is None and not project_probe_failed:
                    raise

            recovery_project_id = str(getattr(project, "id", "") or project_id)
            surfaces = self._shared_surfaces(recovery_project_id, thread_id, room_id)
            if project_probe_failed and "unknown" not in surfaces:
                surfaces.append("unknown")
            recovery = self._record_recovery_pending(
                project_id=recovery_project_id,
                thread_id=thread_id,
                room_id=room_id,
                creation_id=creation_id,
                surfaces=surfaces or ["unknown"],
                failure=creation_error,
            )
            raise recovery from creation_error

        # Keep only local references beyond the commit point.  A response
        # serialization failure must not trigger deletion of a committed group.
        try:
            self._require_final_binding(
                thread_id=thread_id,
                requested_project_id=project.id,
                requested_generation=binding_generation,
            )
        except ProjectGroupBindingChanged:
            raise
        except Exception as reconciliation_error:
            recovery = self._record_recovery_pending(
                project_id=project.id,
                thread_id=thread_id,
                room_id=room_id,
                creation_id=creation_id,
                surfaces=self._shared_surfaces(project.id, thread_id, room_id) or ["unknown"],
                failure=reconciliation_error,
            )
            raise recovery from reconciliation_error
        with suppress(Exception):
            thread = self.thread_store.get(thread_id) or thread
        return {
            "project": project,
            "project_state": project_state,
            "thread": thread,
            "thread_id": thread_id,
            "room": projected_room,
            "group_state": group_state,
            "mode": mode,
        }


__all__ = [
    "ProjectGroupBindingChanged",
    "ProjectGroupCreationRecoveryPending",
    "ProjectGroupCreationService",
]
