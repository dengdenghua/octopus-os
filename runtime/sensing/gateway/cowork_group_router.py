"""Thread-group API: WeChat-style membership + mode + shared blackboard.

A thread *is* the group (1:1 = the N=2 case), so these endpoints hang off the
thread id. In shared/authenticated deployments every read and write is bound to
the server-owned thread principal; local no-auth mode keeps the original
single-user behaviour. Mutations are attributed to the resolved actor.

Path is ``/api/cowork/*`` to avoid colliding with ``/api/groups/*`` (which is the
static AgentGroupRegistry of agent-team *templates*, a different concept).
"""

from __future__ import annotations

import asyncio
import inspect
from contextlib import suppress
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from runtime.memory.cowork.group import (
    LEGACY_PROJECT_MODE,
    ContextGrant,
    MemberEvent,
    MemberKind,
    responders,
)
from runtime.memory.cowork.group_store import GroupStore
from runtime.memory.threads.event_log import validate_thread_id

from ._cowork_group_access import CoworkGroupAccess
from ._cowork_group_models import (
    AssignBody,
    BoardBody,
    BreakoutBody,
    CollabTaskBody,
    CompleteBody,
    EnsureRoomBody,
    HeartbeatBody,
    InviteBody,
    LinkRoomBody,
    MergeBody,
    MessageProjectActionBody,
    ModeBody,
    ReadBody,
    RoomMessageBody,
    RosterBody,
    response_mode,
)
from ._cowork_group_models import GrantBody as GrantBody
from ._cowork_group_session import CoworkGroupSessionView
from .thread_access import ThreadAccessResolver


def create_cowork_group_router(
    *,
    store: GroupStore | None = None,
    async_store: Any = None,
    collaboration_store: Any = None,
    room_message_store: Any = None,
    team_rooms_state_path: Any = None,
    team_tasks_state_path: Any = None,
    team_rooms_router: Any = None,
    team_tasks_router: Any = None,
    runtime: Any = None,
    project_store: Any = None,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> APIRouter:
    """Create the ``/api/cowork/*`` thread-group router."""
    group_store = store or GroupStore()
    bind_team_group_store = getattr(team_rooms_router, "bind_group_store", None)
    if callable(bind_team_group_store):
        bind_team_group_store(group_store)

    def _ensure_project_for_thread(thread_id: str, request: Request) -> str | None:
        """Bind a Project OS project to the thread if none exists yet.

        This compatibility path lets an old client that still submits
        ``mode=project`` attach project state without turning project into a
        response strategy or running any project work. It fails soft when
        planning is unavailable."""
        try:
            from runtime.projectos.cowork_bridge import ensure_project_for_thread

            name = ""
            thread_store = getattr(runtime, "thread_store", None)
            get_state = getattr(thread_store, "get_state", None)
            if callable(get_state):
                try:
                    st = get_state(thread_id)
                    values = st.get("values") if isinstance(st, dict) else None
                    title = values.get("title") if isinstance(values, dict) else None
                    if isinstance(title, str) and title.strip():
                        name = title.strip()
                except Exception:  # noqa: BLE001
                    name = ""
            principal = _principal(request)
            scoped_project_store = _project_store()
            owner_id = ""
            tenant_id = ""
            if principal is not None:
                from runtime.safety.auth.scope import scope_from_principal

                scope = scope_from_principal(
                    principal,
                    allow_cross_tenant=bool(principal.roles.intersection({"admin", "operator"})),
                )
                with_scope = getattr(scoped_project_store, "with_scope", None)
                if not callable(with_scope):
                    raise RuntimeError("scoped project store is unavailable")
                scoped_project_store = with_scope(scope)
                owner_id = principal.actor_id
                tenant_id = principal.tenant_id
            elif require_auth:
                raise RuntimeError("authenticated project principal is unavailable")
            return ensure_project_for_thread(
                scoped_project_store,
                group_store,
                thread_id,
                name=name,
                goal=name,
                owner_id=owner_id,
                tenant_id=tenant_id,
            )
        except Exception as exc:  # noqa: BLE001
            _logger = __import__("logging").getLogger("echo.cowork")
            _logger.warning("legacy project attach failed for %s: %s", thread_id, exc)
            return None

    def _require_thread_path(thread_id: str) -> None:
        try:
            validate_thread_id(thread_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def _collaboration_store():
        if collaboration_store is not None:
            return collaboration_store
        from runtime.memory.cowork.collaboration_store import CollaborationStore

        return CollaborationStore(base_dir=group_store.base_dir)

    def _project_store():
        if project_store is not None:
            return project_store
        from runtime.projectos.store import ProjectStore

        return ProjectStore()

    thread_access = ThreadAccessResolver(
        thread_store=getattr(runtime, "thread_store", None),
        group_store=group_store,
        collaboration_store=collaboration_store,
        team_rooms_router=team_rooms_router,
        identity_store=identity_store,
    )

    def _async_store():
        if async_store is not None:
            return async_store
        from runtime.memory.cowork.async_work import AsyncWorkStore

        return AsyncWorkStore(base_dir=group_store.base_dir, group_store=group_store)

    _presence_holder: dict[str, Any] = {}

    def _presence_store():
        store = _presence_holder.get("v")
        if store is None:
            from runtime.memory.cowork.presence import PresenceStore

            store = PresenceStore(base_dir=group_store.base_dir)
            _presence_holder["v"] = store
        return store

    _room_msg_holder: dict[str, Any] = {}

    def _room_message_store():
        if room_message_store is not None:
            return room_message_store
        store = _room_msg_holder.get("v")
        if store is None:
            from runtime.memory.cowork.room_messages import RoomMessageStore

            # Default teamroom dir — shared with the team_rooms router's store,
            # so a linked room's transcript is the same one it persists.
            store = RoomMessageStore()
            _room_msg_holder["v"] = store
        return store

    session_view = CoworkGroupSessionView(
        group_store=group_store,
        collaboration_store=_collaboration_store,
        async_store=_async_store,
        presence_store=_presence_store,
        room_message_store=_room_message_store,
        team_rooms_state_path=team_rooms_state_path,
        team_tasks_state_path=team_tasks_state_path,
    )
    _room_participants = session_view.room_participants
    _room_tasks = session_view.room_tasks
    _room_messages = session_view.room_messages
    _room_snapshot = session_view.room_snapshot
    _session_payload = session_view.session_payload
    _room_members_from_group = session_view.room_members_from_group
    _room_members_for_projection = session_view.room_members_for_projection

    async def _maybe_await(value: Any) -> Any:
        if inspect.isawaitable(value):
            return await value
        return value

    access = CoworkGroupAccess(
        runtime=runtime,
        identity_store=identity_store,
        require_auth=require_auth,
        jwt_secret=jwt_secret,
        jwt_issuer=jwt_issuer,
        jwt_audience=jwt_audience,
        thread_access=thread_access,
        team_rooms_router=team_rooms_router,
        room_snapshot=_room_snapshot,
    )
    _principal = access.principal
    _require_owned_thread = access.require_owned_thread
    _require_collaborative_thread = access.require_collaborative_thread
    _require_room_member = access.require_room_member

    async def _ensure_room(
        thread_id: str,
        body: EnsureRoomBody,
        request: Request,
    ) -> tuple[dict[str, Any], bool]:
        from runtime.sensing.gateway._cowork_group_room_ensure import (
            ensure_session_room_fail_safe,
        )

        return await ensure_session_room_fail_safe(
            thread_id=thread_id,
            body=body,
            request=request,
            group_store=group_store,
            team_rooms_router=team_rooms_router,
            room_snapshot=_room_snapshot,
            require_room_member=_require_room_member,
            require_owned_thread=_require_owned_thread,
            room_members_for_projection=_room_members_for_projection,
            room_members_from_group=_room_members_from_group,
            collaboration_store=_collaboration_store,
            actor=_actor,
            ensure_project_for_thread=_ensure_project_for_thread,
        )

    _actor = access.actor

    def _project_linked_room_roster(
        thread_id: str,
        request: Request,
        state: Any,
    ) -> dict[str, Any] | None:
        """Keep the optional Team Room read model aligned with GroupStore."""

        room_id = str(getattr(state, "room_id", None) or "").strip()
        if not room_id:
            return None
        roster_projector = getattr(team_rooms_router, "replace_team_agent_members", None)
        if not callable(roster_projector):
            return {"ok": False, "room_id": room_id}
        try:
            projection_state = state
            for _attempt in range(5):
                current_room = _room_snapshot(room_id) or {}
                projected_room = roster_projector(
                    request,
                    room_id,
                    _room_members_for_projection(
                        thread_id,
                        existing=current_room.get("members") or [],
                        state=projection_state,
                    ),
                    current_room.get("leaderId"),
                )
                _collaboration_store().upsert_room(thread_id, dict(projected_room))
                latest_state = group_store.state(thread_id)
                if latest_state.event_count == projection_state.event_count:
                    return {"ok": True, "room_id": room_id}
                # Another membership transaction committed while this
                # projection was writing. Re-project the newer generation so
                # a delayed response cannot overwrite the canonical roster.
                projection_state = latest_state
            return {"ok": False, "room_id": room_id, "stale": True}
        except Exception as exc:  # noqa: BLE001 - canonical GroupStore mutation already committed
            __import__("logging").getLogger("echo.cowork").warning(
                "linked room roster projection failed for %s: %s",
                thread_id,
                exc,
            )
            return {"ok": False, "room_id": room_id}

    def _auth_dep(request: Request) -> None:
        thread_id = str(getattr(request, "path_params", {}).get("thread_id") or "")
        _require_collaborative_thread(thread_id, request, write=True)

    def _owner_dep(request: Request) -> None:
        thread_id = str(getattr(request, "path_params", {}).get("thread_id") or "")
        _require_owned_thread(thread_id, request)

    def _thread_access_dep(thread_id: str, request: Request) -> None:
        _require_thread_path(thread_id)
        _require_collaborative_thread(thread_id, request)

    router = APIRouter(tags=["cowork"], dependencies=[Depends(_thread_access_dep)])

    @router.get("/api/cowork/{thread_id}")
    def get_group(thread_id: str, until_seq: int | None = None) -> dict[str, Any]:
        """Folded group state (roster + mode), the shared blackboard, the raw
        membership timeline, and who would respond this turn under the mode.
        ``until_seq`` replays the group as it was at that event (time-travel)."""
        state = group_store.state(thread_id, until_seq=until_seq)
        return {
            "thread_id": thread_id,
            "state": state.to_dict(),
            "blackboard": group_store.blackboard_snapshot(thread_id),
            "events": [e.to_dict() for e in group_store.events(thread_id)],
            "responders": responders(state),
        }

    @router.get("/api/collab/{thread_id}")
    def get_session(thread_id: str, request: Request) -> dict[str, Any]:
        """Unified collaboration session — one read over roster/mode/room link,
        shared blackboard, async tasks, and presence (instead of stitching the
        per-surface endpoints). The cowork thread is the canonical session; a
        Team Room is its optional linked surface."""
        room_id = getattr(group_store.state(thread_id), "room_id", None)
        if room_id:
            _require_room_member(room_id, request)
        return _session_payload(thread_id)

    @router.post("/api/collab/{thread_id}/room", dependencies=[Depends(_owner_dep)])
    async def ensure_session_room(
        thread_id: str,
        body: EnsureRoomBody,
        request: Request,
    ) -> dict[str, Any]:
        """Create/link the session's persistent room.

        This is the canonical replacement for "go create a Team elsewhere":
        the user stays in one collaboration thread, and persistence/invites/tasks
        become properties of that same session.
        """
        room, created = await _ensure_room(thread_id, body, request)
        return {
            "ok": True,
            "created": created,
            "room": room,
            "session": await asyncio.to_thread(_session_payload, thread_id),
        }

    @router.get("/api/collab/{thread_id}/tasks")
    def list_session_tasks(thread_id: str, request: Request) -> dict[str, Any]:
        """List heavyweight room tasks through the canonical session path."""
        room_id = getattr(group_store.state(thread_id), "room_id", None)
        if room_id:
            _require_room_member(room_id, request)
        tasks = _collaboration_store().tasks_for_session(thread_id)
        if not tasks and room_id:
            tasks = _room_tasks(room_id)
        return {
            "thread_id": thread_id,
            "room_id": room_id,
            "tasks": tasks,
            "count": len(tasks),
        }

    @router.post("/api/collab/{thread_id}/tasks", dependencies=[Depends(_auth_dep)])
    async def create_session_task(
        thread_id: str,
        body: CollabTaskBody,
        request: Request,
    ) -> dict[str, Any]:
        """Create a heavyweight task through the collaboration session.

        The underlying TeamTask store is still reused for compatibility, but the
        caller no longer has to choose a separate Team surface first.
        """
        creator = getattr(team_tasks_router, "create_task_from_payload", None)
        if not callable(creator):
            raise HTTPException(501, "collab task creation is not wired")
        room_body = body.room or EnsureRoomBody()
        room, _created = await _ensure_room(thread_id, room_body, request)
        room_id = str(room.get("id") or getattr(group_store.state(thread_id), "room_id", "") or "")
        if not room_id:
            raise HTTPException(409, "collab session has no linked room")
        metadata = {
            **body.metadata,
            "collab_session_id": thread_id,
            "source": "collab_session",
        }
        task = await _maybe_await(
            creator(
                request,
                {
                    "room_id": room_id,
                    "title": body.title,
                    "description": body.description,
                    "sop_template": body.sop_template,
                    "assignees": body.assignees,
                    "metadata": metadata,
                },
            )
        )
        if body.run:
            runner = getattr(team_tasks_router, "run_task_from_request", None)
            if callable(runner):
                task = await _maybe_await(runner(request, task["id"]))
        task = await asyncio.to_thread(_collaboration_store().upsert_task, thread_id, dict(task))
        return {
            "ok": True,
            "room_id": room_id,
            "task": task,
            "session": await asyncio.to_thread(_session_payload, thread_id),
        }

    @router.post("/api/collab/{thread_id}/link-room", dependencies=[Depends(_owner_dep)])
    async def link_session_room(
        thread_id: str,
        body: LinkRoomBody,
        request: Request,
    ) -> dict[str, Any]:
        """Link a Team Room to this session (event-sourced) so the two surfaces
        stop drifting as separate sources of truth."""
        from runtime.sensing.gateway._cowork_group_room_link import (
            link_session_room_fail_safe,
        )

        _require_room_member(body.room_id, request)
        current_state = group_store.state(thread_id)
        if current_state.room_id and current_state.room_id != body.room_id:
            current_room = _room_snapshot(current_state.room_id)
            current_room_thread = str((current_room or {}).get("thread_id") or "").strip()
            if current_room_thread == thread_id:
                raise HTTPException(409, "collaboration thread is already linked to another room")

        collaboration = _collaboration_store()
        prior_room = _room_snapshot(body.room_id)
        prior_thread_id = str((prior_room or {}).get("thread_id") or "").strip()
        if prior_thread_id and prior_thread_id != thread_id:
            raise HTTPException(409, "team room is already bound to another thread")
        state = await link_session_room_fail_safe(
            thread_id=thread_id,
            room_id=body.room_id,
            request=request,
            actor=_actor(request),
            prior_room=prior_room,
            room_snapshot=_room_snapshot,
            group_store=group_store,
            collaboration=collaboration,
            team_rooms_router=team_rooms_router,
        )
        return {
            "ok": True,
            "state": state.to_dict(),
            "session": await asyncio.to_thread(_session_payload, thread_id),
        }

    @router.post("/api/collab/{thread_id}/room-message", dependencies=[Depends(_auth_dep)])
    def post_room_message(
        thread_id: str,
        body: RoomMessageBody,
        request: Request,
    ) -> dict[str, Any]:
        """Write a line into the session's linked Team Room transcript.

        The write side of the unified session: where ``get_session`` /search read
        the linked room transcript, this lets the cowork thread *post* into it
        through the same session — so an agent or summary in the group lands in
        the room surface instead of a separate write path. 409 if no room is
        linked (link it first via ``/link-room``)."""
        room_id = getattr(group_store.state(thread_id), "room_id", None)
        if not room_id:
            raise HTTPException(409, "no room linked to this session — link one first")
        _require_room_member(room_id, request)
        metadata = dict(body.metadata)
        if body.source_message_id:
            metadata["source_message_id"] = body.source_message_id
        if body.message_type:
            metadata["message_type"] = body.message_type
        if body.entity_refs:
            metadata["entity_refs"] = body.entity_refs
        if body.system_card is not None:
            metadata["system_card"] = body.system_card
        try:
            canonical_store = _collaboration_store()
            source_message_id = str(metadata.get("source_message_id") or "")
            existing_source = (
                canonical_store.message_by_source_id(thread_id, source_message_id)
                if source_message_id
                else None
            )
            seq = canonical_store.append_message(
                thread_id,
                room_id=room_id,
                text=body.text,
                participant_id=body.participant_id,
                display_name=body.display_name,
                metadata=metadata,
            )
            message = canonical_store.message_for_session(thread_id, seq)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if existing_source is None:
            with suppress(Exception):  # legacy transcript projection is best-effort
                _room_message_store().append(
                    room_id,
                    text=body.text,
                    participant_id=body.participant_id,
                    display_name=body.display_name,
                )
        return {"ok": True, "room_id": room_id, "seq": seq, "message": message}

    @router.post(
        "/api/collab/{thread_id}/room-messages/{message_seq}/project-actions",
        dependencies=[Depends(_owner_dep)],
    )
    async def message_project_action(
        thread_id: str,
        message_seq: int,
        body: MessageProjectActionBody,
        request: Request,
    ) -> dict[str, Any]:
        """Promote a room message into Project OS without a second task truth.

        Supported actions are ``link_milestone``, ``create_item``,
        ``record_decision``, and ``publish_artifact``.  The source message is
        enriched with entity references and an idempotent system-card message
        is appended to the room.  ``create_item`` writes Project OS first and
        only then projects the task into collaboration storage.
        """

        room_id = getattr(group_store.state(thread_id), "room_id", None)
        if not room_id:
            raise HTTPException(409, "no room linked to this session — link one first")
        _require_room_member(room_id, request)
        canonical_store = _collaboration_store()
        try:
            message = canonical_store.message_for_session(thread_id, message_seq)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if message is None or str(message.get("room_id") or "") != str(room_id):
            raise HTTPException(404, "room message not found in the linked room")
        from runtime.projectos.message_actions import (
            MessageProjectActionError,
            apply_message_project_action,
        )

        try:
            result = await asyncio.to_thread(
                apply_message_project_action,
                _project_store(),
                canonical_store,
                thread_id=thread_id,
                room_id=str(room_id),
                message=message,
                body=body.model_dump(),
                actor=_actor(request),
            )
        except MessageProjectActionError as exc:
            raise HTTPException(exc.status_code, exc.detail) from exc
        except PermissionError as exc:
            raise HTTPException(404, "project not found") from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        card = result.get("system_card_message")
        broadcaster = getattr(team_rooms_router, "broadcast", None)
        if callable(broadcaster) and isinstance(card, dict) and not result.get("replayed"):
            with suppress(Exception):  # persistence succeeded; live fan-out is best-effort
                await _maybe_await(
                    broadcaster(
                        str(room_id),
                        {
                            "type": "message",
                            "team_id": str(room_id),
                            "thread_id": thread_id,
                            "message_id": str(
                                (card.get("metadata") or {}).get("source_message_id")
                                if isinstance(card.get("metadata"), dict)
                                else f"room-msg-{card.get('seq')}"
                            ),
                            "participant_id": card.get("participant_id") or "project-os",
                            "display_name": card.get("display_name") or "Project OS",
                            "text": card.get("text") or "",
                            "created_at": card.get("ts"),
                            "metadata": card.get("metadata") or {},
                        },
                    )
                )
        return result

    @router.get("/api/cowork/{thread_id}/nominate")
    def nominate_turn(thread_id: str, text: str = "", threshold: float = 0.5) -> dict[str, Any]:
        """Self-nomination gate: of the participant agents, who is relevant enough
        to speak for ``text`` — so a swarm doesn't pile on every turn."""
        from runtime.memory.cowork.nominate import gate

        state = group_store.state(thread_id)
        participants = [
            (m.id, m.id)
            for m in state.roster
            if m.kind == "agent" and m.role == "participant" and not m.muted
        ]
        return {"nominated": gate(participants, text, threshold=threshold)}

    @router.get("/api/cowork/{thread_id}/search")
    def search(
        thread_id: str,
        request: Request,
        q: str = "",
        limit: int = 20,
        kinds: str = "",
        until_seq: int | None = None,
    ) -> dict[str, Any]:
        """Replayable, session-wide search across the shared blackboard, async
        tasks, the membership/mode event log, and (when a room is linked) the
        room transcript + team tasks. ``kinds`` is a comma-separated subset of
        ``blackboard,task,event,room_message,room_task`` (default all);
        ``until_seq`` bounds the event scan to a past point (time-travel)."""
        from runtime.memory.cowork.search import search_group

        room_id = getattr(group_store.state(thread_id), "room_id", None)
        if room_id:
            _require_room_member(room_id, request)
        kind_filter = tuple(k.strip() for k in kinds.split(",") if k.strip()) or None
        hits = search_group(
            group_store,
            thread_id,
            q,
            limit=max(1, min(100, limit)),
            kinds=kind_filter,
            until_seq=until_seq,
            async_store=_async_store(),
            room_message_store=session_view.message_search(thread_id),
            room_task_provider=_room_tasks,
        )
        return {"thread_id": thread_id, "query": q, "hits": [h.to_dict() for h in hits]}

    @router.get("/api/cowork/{thread_id}/presence")
    def presence(thread_id: str, online_window_s: int = 60) -> dict[str, Any]:
        """Per-member presence + unread for the thread's roster. Unread counts
        group events past each member's read marker (floored at their join)."""
        from runtime.memory.cowork.presence import group_presence

        members = group_presence(
            group_store,
            _presence_store(),
            thread_id,
            online_window_s=max(1, online_window_s),
        )
        return {"thread_id": thread_id, "members": [m.to_dict() for m in members]}

    @router.post("/api/cowork/{thread_id}/read", dependencies=[Depends(_auth_dep)])
    def mark_read(thread_id: str, body: ReadBody) -> dict[str, Any]:
        """Mark ``member_id`` caught up to ``seq`` (default: the current event
        head). The marker is monotonic — it never rewinds."""
        seq = body.seq
        if seq is None:
            events = group_store.events(thread_id)
            seq = max((e.seq for e in events), default=0)
        _presence_store().mark_read(thread_id, body.member_id, int(seq))
        return {"ok": True, **_presence_store().get(thread_id, body.member_id)}

    @router.post("/api/cowork/{thread_id}/heartbeat", dependencies=[Depends(_auth_dep)])
    def heartbeat(thread_id: str, body: HeartbeatBody) -> dict[str, Any]:
        """Presence ping — refresh ``member_id``'s online status."""
        _presence_store().heartbeat(thread_id, body.member_id)
        return {"ok": True, **_presence_store().get(thread_id, body.member_id)}

    @router.get("/api/cowork/{thread_id}/catchup/{member_id}")
    def catchup(thread_id: str, member_id: str) -> dict[str, Any]:
        """Catch-up brief for a member (roster + shared board + grant scope). The
        realtime layer fills in recent messages via build_catchup in-process."""
        from runtime.memory.cowork.catchup import build_catchup

        cu = build_catchup(
            group_store.state(thread_id),
            member_id,
            messages=[],
            blackboard=group_store.blackboard_snapshot(thread_id),
        )
        if cu is None:
            raise HTTPException(404, "member not in group")
        return {**cu.to_dict(), "render": cu.render()}

    @router.get("/api/cowork/{thread_id}/tasks")
    def list_tasks(thread_id: str) -> dict[str, Any]:
        """Background tasks in this thread (async coworkers)."""
        return {"tasks": [t.to_dict() for t in _async_store().list(thread_id)]}

    @router.get("/api/cowork/{thread_id}/tasks/summary")
    def tasks_summary(thread_id: str) -> dict[str, Any]:
        """Small operational summary for async cowork task badges/health."""
        store = _async_store()
        if runtime is not None and hasattr(runtime, "status"):
            status = runtime.status(thread_id)
        else:
            status = {
                "runner_enabled": False,
                "runner_reason": "runtime not attached",
                "task_counts": store.counts(thread_id),
            }
        return {"thread_id": thread_id, **status}

    @router.get("/api/cowork/{thread_id}/health")
    def health(thread_id: str) -> dict[str, Any]:
        """Unified operational health for a collaboration thread — one call for
        an ops panel: runner state, task queue + failure reasons, presence,
        mode/roster, and recent events. Read-only (like presence/search)."""
        from runtime.memory.cowork.presence import group_presence

        async_store = _async_store()
        tasks = async_store.list(thread_id)
        failures = [
            {"task_id": t.task_id, "assignee": t.assignee, "error": t.result or ""}
            for t in tasks
            if getattr(t, "status", "") == "failed"
        ][:10]
        if runtime is not None and hasattr(runtime, "status"):
            rstatus = runtime.status(thread_id)
            runner = {
                "enabled": bool(rstatus.get("runner_enabled")),
                "reason": rstatus.get("runner_reason") or "",
                "status": rstatus.get("runner_status"),
            }
        else:
            runner = {"enabled": False, "reason": "runtime not attached", "status": None}

        state = group_store.state(thread_id)
        members = group_presence(group_store, _presence_store(), thread_id)
        events = group_store.events(thread_id)
        return {
            "thread_id": thread_id,
            "mode": state.mode,
            "roster_size": len(state.roster),
            "runner": runner,
            "tasks": {
                "counts": async_store.counts(thread_id),
                "failures": failures,
            },
            "presence": {
                "members": len(members),
                "online": sum(1 for m in members if m.online),
                "unread": sum(m.unread for m in members),
            },
            "recent_events": [e.to_dict() for e in events[-10:]],
        }

    @router.post("/api/cowork/{thread_id}/tasks", dependencies=[Depends(_auth_dep)])
    def assign_task(thread_id: str, body: AssignBody, request: Request) -> dict[str, Any]:
        """Give a member a task to work in the background; result lands on the
        shared blackboard when complete."""
        task = _async_store().assign(thread_id, body.assignee, body.prompt, actor=_actor(request))
        return {"ok": True, "task": task.to_dict()}

    @router.post(
        "/api/cowork/{thread_id}/tasks/{task_id}/complete", dependencies=[Depends(_auth_dep)]
    )
    def complete_task(thread_id: str, task_id: str, body: CompleteBody) -> dict[str, Any]:
        """A runner reports a background task done — posts the result to the board."""
        async_store = _async_store()
        task = async_store.get(task_id)
        if task is None or task.thread_id != thread_id:
            raise HTTPException(404, "task not found")
        if task.status == "pending":
            async_store.claim(task_id)
        ok = async_store.complete(task_id, body.result, blackboard_key=body.blackboard_key)
        if not ok:
            raise HTTPException(409, "task is not claimable")
        return {"ok": True, "blackboard": group_store.blackboard_snapshot(thread_id)}

    @router.post("/api/cowork/{thread_id}/breakout", dependencies=[Depends(_owner_dep)])
    def breakout_fork(thread_id: str, body: BreakoutBody, request: Request) -> dict[str, Any]:
        """Spin off a focused side-thread with a subset of members + a grant."""
        from runtime.memory.cowork.breakout import fork
        from runtime.memory.cowork.group import ContextGrant

        _require_thread_path(body.child_thread)
        # Authenticated child threads must already have been created through
        # the canonical thread/realtime flow, which provisions server-owned
        # workspace metadata. Cowork must not mint an unmanaged parallel id.
        _require_owned_thread(body.child_thread, request)
        res = fork(
            group_store,
            thread_id,
            body.child_thread,
            actor=_actor(request),
            members=body.members,
            grant=ContextGrant.from_dict(body.grant),
            at_message=body.at_message,
        )
        return {"ok": True, **res}

    @router.post(
        "/api/cowork/{thread_id}/breakout/{child_thread}/merge",
        dependencies=[Depends(_owner_dep)],
    )
    def breakout_merge(
        thread_id: str, child_thread: str, body: MergeBody, request: Request
    ) -> dict[str, Any]:
        """Merge a breakout's conclusion back onto the parent's blackboard."""
        from runtime.memory.cowork.breakout import merge_back

        _require_thread_path(child_thread)
        _require_owned_thread(child_thread, request)
        res = merge_back(
            group_store, child_thread, thread_id, actor=_actor(request), summary=body.summary
        )
        return {"ok": True, **res, "blackboard": group_store.blackboard_snapshot(thread_id)}

    @router.get("/api/cowork/{thread_id}/plan")
    def plan(thread_id: str, text: str = "") -> dict[str, Any]:
        """Given a draft message, who would act this turn and how (mode →
        single / cluster / swarm), honouring @agent mentions. The realtime
        driver reads this to dispatch without a manual mode switch."""
        from runtime.memory.cowork.turn_plan import plan_turn_for_thread

        return plan_turn_for_thread(group_store, thread_id, text).to_dict()

    @router.get("/api/cowork/{thread_id}/view/{member_id}")
    def member_view(thread_id: str, member_id: str, max_message: int = 0) -> dict[str, Any]:
        """The history slice ``member_id`` is allowed to see at ``max_message``
        (their context grant resolved). The context assembler uses this to bound
        what reaches the agent's prompt — the enforcement half of the privacy seam."""
        from runtime.memory.cowork.context_view import resolve_view

        view = resolve_view(group_store.state(thread_id), member_id, max_message)
        if view is None:
            raise HTTPException(404, "member not in group")
        return view.to_dict()

    @router.post("/api/cowork/{thread_id}/members", dependencies=[Depends(_owner_dep)])
    def invite_member(thread_id: str, body: InviteBody, request: Request) -> dict[str, Any]:
        """Reference a canonical agent (or human) from this thread.

        Retrying the same add is a successful no-op.  The group stores only the
        canonical id; it does not clone a role, home, memory, or owner lane.
        """
        target_kind: MemberKind = "human" if body.kind == "human" else "agent"
        ev = MemberEvent(
            action="invite",
            actor=_actor(request),
            target_id=body.target_id,
            target_kind=target_kind,
            role="observer" if body.role == "observer" else "participant",
            grant=ContextGrant.from_dict(body.grant.model_dump()),
            at_message=body.at_message,
        )
        try:
            changed, state = group_store.ensure_member(thread_id, ev)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        result: dict[str, Any] = {
            "ok": True,
            "added": changed is not None,
            "state": state.to_dict(),
        }
        projection = _project_linked_room_roster(thread_id, request, state)
        if projection is not None:
            result["room_projection"] = projection
        return result

    @router.delete(
        "/api/cowork/{thread_id}/members/{member_id}", dependencies=[Depends(_owner_dep)]
    )
    def remove_member(thread_id: str, member_id: str, request: Request) -> dict[str, Any]:
        """Remove a session reference idempotently; attributed history stays."""
        try:
            changed, state = group_store.remove_member_if_present(
                thread_id,
                actor=_actor(request),
                member_id=member_id,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        result: dict[str, Any] = {
            "ok": True,
            "removed": changed is not None,
            "state": state.to_dict(),
        }
        projection = _project_linked_room_roster(thread_id, request, state)
        if projection is not None:
            result["room_projection"] = projection
        return result

    @router.post("/api/cowork/{thread_id}/mode", dependencies=[Depends(_owner_dep)])
    def set_mode(thread_id: str, body: ModeBody, request: Request) -> dict[str, Any]:
        """Switch how AI participants respond: chat, cluster, or swarm.

        ``project`` is accepted only as a deprecated client wire value. It is
        projected to ``chat`` and may attach an idle Project for continuity;
        it is never stored as a fourth response mode and never starts work.
        """
        canonical_mode = response_mode(body.mode)
        group_store.append(
            thread_id,
            MemberEvent(action="mode", actor=_actor(request), mode=canonical_mode),
        )
        bound_project_id: str | None = None
        if body.mode == LEGACY_PROJECT_MODE:
            bound_project_id = _ensure_project_for_thread(thread_id, request)
        state = group_store.state(thread_id).to_dict()
        if bound_project_id is not None:
            state["bound_project_id"] = bound_project_id
        return {"ok": True, "state": state}

    @router.put("/api/cowork/{thread_id}/roster", dependencies=[Depends(_owner_dep)])
    def replace_roster(thread_id: str, body: RosterBody, request: Request) -> dict[str, Any]:
        """Replace the desired agent roster and mode as one atomic mutation.

        Human participants are intentionally untouched. The store calculates
        the diff against its own transactional snapshot, appends only the
        necessary leave/invite/mode events, and returns the canonical fold.
        """

        canonical_mode = response_mode(body.mode)
        try:
            changed, state = group_store.replace_agent_roster(
                thread_id,
                actor=_actor(request),
                agent_ids=body.agent_ids,
                mode=canonical_mode,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        payload = state.to_dict()
        room_projection = _project_linked_room_roster(thread_id, request, state)
        if body.mode == LEGACY_PROJECT_MODE:
            bound_project_id = _ensure_project_for_thread(thread_id, request)
            if bound_project_id is not None:
                payload["bound_project_id"] = bound_project_id
        result = {
            "ok": True,
            "state": payload,
            "events": [event.to_dict() for event in changed],
        }
        if room_projection is not None:
            result["room_projection"] = room_projection
        return result

    @router.post("/api/cowork/{thread_id}/blackboard", dependencies=[Depends(_auth_dep)])
    def write_board(thread_id: str, body: BoardBody, request: Request) -> dict[str, Any]:
        """Write a key to the group's shared blackboard, attributed to the actor."""
        board = group_store.blackboard(thread_id)
        board.write(body.key, body.value, writer=_actor(request))
        return {"ok": True, "blackboard": group_store.blackboard_snapshot(thread_id)}

    return router
