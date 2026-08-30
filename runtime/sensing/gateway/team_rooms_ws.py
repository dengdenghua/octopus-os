"""Realtime Team Room WebSocket handler.

Split out of ``team_rooms_router`` to keep that module under the god-file
threshold. Unlike the pure helpers in ``team_speaker_policy``, this handler
is *stateful* — it reads and mutates the shared room store under the
router's lock and uses the router's broadcast/persistence helpers. Rather
than live as a closure inside ``create_team_rooms_router``, it takes those
dependencies explicitly as a :class:`TeamRoomWsContext`, which the router
builds once and passes to a thin ``@router.websocket`` wrapper.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import Lock
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from runtime.platform.process.sliding_window_limiter import SlidingWindowLimiter

from .team_speaker_policy import (
    _TURN_POLICIES,
    _authorized_to_speak_for,
    _is_moderator,
    _next_speaker,
    _normalize_participant_role,
    _normalize_speak_mode,
    _normalize_speaker_policy,
    _now,
    _participant_can_speak,
    advance_round_robin,
    apply_floor_grant,
    apply_floor_request,
    apply_floor_yield,
)

try:
    from fastapi import WebSocket, WebSocketDisconnect

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    WebSocket = None  # type: ignore[assignment,misc]
    WebSocketDisconnect = None  # type: ignore[assignment,misc]

if TYPE_CHECKING:
    from .team_rooms_router import TeamParticipantWire, TeamRoomWire

# How many recent spoken lines a room keeps so a twin responder has
# conversational context. Durable history is projected to CollaborationStore
# (with RoomMessageStore retained as the legacy fallback).
_RING_SIZE = 20

# Anti-flood ceilings on the inbound WS. cursor/ping/message frames are
# relayed to every peer, so an unbounded client could amplify a flood
# across the room. These are lenient — smooth cursor tracking (~10-30/s)
# passes; only a runaway client trips them. Oversized frames and
# over-rate frames are dropped before any broadcast (no error spam).
_TEAM_WS_MAX_MSG_BYTES = 64 * 1024
_TEAM_WS_MSG_PER_SEC = 30

# Single background worker for durable message writes — keeps synchronous
# sqlite I/O off the WS event loop and serializes appends (no write contention).
_PERSIST_POOL: ThreadPoolExecutor | None = None


def _noop_refresh() -> None:
    return


def _persist_pool() -> ThreadPoolExecutor:
    global _PERSIST_POOL
    if _PERSIST_POOL is None:
        _PERSIST_POOL = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="room-persist",
        )
    return _PERSIST_POOL


async def broadcast_authorized_team_sockets(
    *,
    team_id: str,
    payload: dict[str, Any] | Callable[[], dict[str, Any]],
    teams: dict[str, TeamRoomWire],
    lock: Lock,
    live_sockets: dict[str, dict[str, WebSocket]],
    socket_loops: dict[str, dict[str, asyncio.AbstractEventLoop]],
    refresh: Callable[[], None],
    exclude: str | None = None,
    include: str | None = None,
) -> None:
    """Broadcast only to participants authorized by the durable roster.

    A router process may retain a socket after another worker removes its
    participant.  Refresh before every server push, evict and close those
    sockets before sending the payload, and schedule each operation on the
    event loop that accepted the socket.
    """

    refresh()
    with lock:
        team = teams.get(team_id)
        authorized_ids = {
            participant.id
            for participant in (team.participants if team is not None else [])
            if participant.status != "removed"
        }
        room = live_sockets.get(team_id, {})
        loops = socket_loops.get(team_id, {})
        authorized = [
            (participant_id, socket, loops.get(participant_id))
            for participant_id, socket in room.items()
            if participant_id in authorized_ids
        ]
        revoked = [
            (participant_id, socket, loops.get(participant_id))
            for participant_id, socket in room.items()
            if participant_id not in authorized_ids
        ]
        for participant_id, _socket, _owner_loop in revoked:
            room.pop(participant_id, None)
            loops.pop(participant_id, None)
        resolved_payload = payload() if callable(payload) else payload

    current_loop = asyncio.get_running_loop()

    async def _dispatch(
        socket: WebSocket,
        owner_loop: asyncio.AbstractEventLoop | None,
        *,
        close: bool,
    ) -> None:
        operation = socket.close(code=4403) if close else socket.send_json(resolved_payload)
        if owner_loop is None or owner_loop is current_loop:
            await operation
        elif not owner_loop.is_closed():
            dispatched = asyncio.run_coroutine_threadsafe(operation, owner_loop)
            await asyncio.wrap_future(dispatched)
        else:
            operation.close()
            raise RuntimeError("WebSocket owner loop is closed")

    for _participant_id, socket, owner_loop in revoked:
        with contextlib.suppress(ConnectionError, TimeoutError, OSError, RuntimeError):
            await _dispatch(socket, owner_loop, close=True)

    dead: list[str] = []
    for participant_id, socket, owner_loop in authorized:
        if (exclude and participant_id == exclude) or (include and participant_id != include):
            continue
        try:
            await _dispatch(socket, owner_loop, close=False)
        except (ConnectionError, TimeoutError, OSError, RuntimeError):
            dead.append(participant_id)
    if dead:
        with lock:
            current_room = live_sockets.get(team_id)
            current_loops = socket_loops.get(team_id)
            if current_room:
                for participant_id in dead:
                    current_room.pop(participant_id, None)
                    if current_loops:
                        current_loops.pop(participant_id, None)


@dataclass
class TeamRoomWsContext:
    """The room store, lock, live-socket registry, and the router's
    broadcast/persistence/auth helpers — everything the WS handler needs to
    operate on the same shared state as the HTTP routes."""

    teams: dict[str, TeamRoomWire]
    lock: Lock
    live_sockets: dict[str, dict[str, WebSocket]]
    auth: Callable[[Any], str | None]
    save: Callable[[], None]
    broadcast: Callable[..., Awaitable[None]]
    broadcast_presence: Callable[[str], Awaitable[None]]
    broadcast_floor: Callable[[str, TeamRoomWire], Awaitable[None]]
    active_participant: Callable[[str, str], TeamParticipantWire | None]
    refresh: Callable[[], None] = _noop_refresh
    require_auth: bool = False
    # A TestClient connection, embedded server, or multi-loop host may own
    # different sockets from different event loops. Broadcasts must schedule
    # each send on the loop that accepted that socket; directly awaiting a
    # foreign-loop socket intermittently deadlocks both loops.
    socket_loops: dict[str, dict[str, asyncio.AbstractEventLoop]] = field(default_factory=dict)
    # Twin speaking (optional injection — keeps the gateway a leaf): when the
    # floor lands on a participant who bound a digital-twin agent
    # (``speak_mode == "twin"``), the handler asks this callback for a line
    # and emits it on that participant's behalf. Signature is
    # ``(team, twin_participant, recent_transcript) -> utterance | None``;
    # None on the field (the default) disables twin speaking entirely.
    twin_responder: (
        Callable[
            [TeamRoomWire, TeamParticipantWire, list[dict[str, Any]]],
            Awaitable[str | None],
        ]
        | None
    ) = None
    # Per-room ring buffer of the most recent spoken lines, so a twin
    # responder has context. The router hands over one shared empty dict; the
    # handler appends as messages land and trims to ``_RING_SIZE``.
    recent_messages: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # Durable message log (optional injection): when set, every remembered line
    # is also appended here so room transcripts survive reconnect/restart and
    # can be caught up on / searched. None disables persistence (back-compat).
    message_store: Any = None
    message_projection: Callable[[str, dict[str, Any]], None] | None = None


def _remember_line(
    ctx: TeamRoomWsContext,
    team_id: str,
    participant_id: str,
    display_name: str,
    text: str,
) -> None:
    """Append a spoken line to the room's bounded ring buffer. Locked (cheap,
    no await) — the buffer is shared across the room's sockets."""
    with ctx.lock:
        buf = ctx.recent_messages.setdefault(team_id, [])
        buf.append(
            {
                "participant_id": participant_id,
                "display_name": display_name,
                "text": text,
            }
        )
        if len(buf) > _RING_SIZE:
            del buf[: len(buf) - _RING_SIZE]
    # Durable persistence (best-effort): so the transcript survives beyond the
    # in-memory ring. Offloaded to a single background worker — the store does
    # synchronous sqlite I/O, which must never run on the WS event loop (it
    # would block the broadcast). One worker serializes writes (no contention).
    store = ctx.message_store
    if store is not None:
        with contextlib.suppress(Exception):
            _persist_pool().submit(
                store.append,
                team_id,
                text=text,
                participant_id=participant_id,
                display_name=display_name,
            )
    projection = ctx.message_projection
    if projection is not None:
        with contextlib.suppress(Exception):
            projection(
                team_id,
                {
                    "participant_id": participant_id,
                    "display_name": display_name,
                    "text": text,
                },
            )


async def _twin_speak(
    ctx: TeamRoomWsContext, team_id: str, participant: TeamParticipantWire
) -> bool:
    """If ``participant`` delegated to a digital twin and a responder is
    wired, ask it for a line and broadcast it on the participant's behalf —
    reusing the on_behalf_of message shape (attributed to the participant,
    ``spoken_by`` the twin agent, ``via`` ``"twin"``). Returns True iff a
    line was emitted. None / empty / any error → no broadcast: the twin
    simply stays silent, which callers treat like a silent human.

    The model call happens inside ``responder`` and is awaited OUTSIDE the
    room lock — only the snapshot read and the post-broadcast ring append
    take the lock, and neither awaits while holding it."""
    responder = ctx.twin_responder
    if responder is None:
        return False
    if _normalize_speak_mode(getattr(participant, "speak_mode", "manual")) != "twin":
        return False
    twin_agent_id = (getattr(participant, "twin_agent_id", None) or "").strip()
    if not twin_agent_id:
        return False
    with ctx.lock:
        team = ctx.teams.get(team_id)
        transcript = list(ctx.recent_messages.get(team_id, []))
    if team is None:
        return False
    try:
        line = await responder(team, participant, transcript)
    except Exception:  # noqa: BLE001 — twin generation is best-effort; stay silent on any failure
        return False
    text = (str(line).strip() if line else "")[:4000]
    if not text:
        return False
    _remember_line(ctx, team_id, participant.id, participant.display_name, text)
    await ctx.broadcast(
        team_id,
        {
            "type": "message",
            "team_id": team_id,
            "thread_id": None,
            "message_id": f"room-msg-{uuid4().hex}",
            "participant_id": participant.id,
            "display_name": participant.display_name,
            "spoken_by": twin_agent_id,
            "via": "twin",
            "text": text,
            "created_at": _now(),
        },
    )
    return True


async def _drive_round_robin_twins(ctx: TeamRoomWsContext, team_id: str) -> None:
    """Round_robin only: after the floor advances, let a twin floor-holder
    speak, then advance past it — repeating for consecutive twins so the
    rotation never stalls on an agent that can't self-advance. Each twin
    speaks at most once per call (tracked in ``spoken``), and the walk stops
    at the first human or when it wraps, so even an all-twin room makes one
    pass and halts — never an unbounded advance."""
    spoken: set[str] = set()
    while True:
        with ctx.lock:
            room = ctx.teams.get(team_id)
        if room is None:
            return
        holder_id = getattr(room, "current_speaker_id", None)
        if not holder_id or holder_id in spoken:
            return
        holder = next((p for p in room.participants if p.id == holder_id), None)
        if holder is None:
            return
        if not await _twin_speak(ctx, team_id, holder):
            return
        spoken.add(holder_id)
        # The twin "spoke" → round_robin hands the floor to the next seat.
        with ctx.lock:
            room = ctx.teams.get(team_id)
            if room is None:
                return
            advanced = advance_round_robin(room, holder_id)
            ctx.teams[team_id] = advanced
            ctx.save()
        await ctx.broadcast_floor(team_id, advanced)


async def team_room_ws(ctx: TeamRoomWsContext, ws: WebSocket, team_id: str) -> None:
    """Realtime Team Room presence and lightweight event broadcast."""
    # Bind the shared state / helpers to the names the handler body uses, so
    # the logic below reads exactly as it did inside the router closure.
    teams = ctx.teams
    lock = ctx.lock
    live_sockets = ctx.live_sockets
    socket_loops = ctx.socket_loops
    _auth = ctx.auth
    _save = ctx.save
    _broadcast = ctx.broadcast
    _broadcast_presence = ctx.broadcast_presence
    _broadcast_floor = ctx.broadcast_floor
    _active_participant = ctx.active_participant
    from .team_rooms_router import TeamParticipantWire

    async def _emit_to_peers_or_self(payload: dict[str, Any]) -> None:
        """Relay participant-originated events without blocking multi-peer
        rooms on sender self-echo.

        Cursor events already exclude the sender. Chat/message events used
        to branch on a pre-send socket count, which is brittle under
        TestClient's per-WebSocket portals. Broadcasting to peers first keeps
        the normal multi-client path deterministic; a lone socket still gets
        an echo so single-participant governance tests and clients work.
        """
        await _broadcast(team_id, payload, exclude=participant_id)
        with lock:
            has_peer = any(pid != participant_id for pid in live_sockets.get(team_id, {}))
        if not has_peer:
            await _broadcast(team_id, payload, include=participant_id)

    actor: str | None = None
    auth_error: str | None = None
    try:
        actor = _auth(ws)
    except Exception as exc:
        auth_error = str(getattr(exc, "detail", None) or exc or "unauthorized")

    await ws.accept()
    if auth_error is not None:
        await ws.send_json({"type": "error", "message": auth_error})
        await ws.close(code=4401)
        return

    ctx.refresh()
    participant_id = ws.query_params.get("participant_id") or f"guest-{uuid4().hex[:10]}"
    display_name = ws.query_params.get("display_name") or "Guest"
    thread_id = (ws.query_params.get("thread_id") or "").strip() or None
    now = _now()
    owner_loop = asyncio.get_running_loop()
    ready_payload: dict[str, Any] | None = None
    reject_reason: str | None = None
    with lock:
        team = teams.get(team_id)
        if team is not None:
            existing = next(
                (p for p in team.participants if p.id == participant_id),
                None,
            )
            if existing and existing.status == "removed":
                reject_reason = "participant removed"
            elif ctx.require_auth:
                if existing is not None and existing.actor_id != actor:
                    # Shared mode never binds an authenticated actor to a
                    # legacy/unowned participant merely because they guessed
                    # its id.
                    reject_reason = "participant actor mismatch"
                elif existing is None:
                    # Browser clients may carry a stale/random local participant
                    # id. Resolve that to the participant established by the
                    # invite flow, but never create a new membership here.
                    existing = next(
                        (
                            p
                            for p in team.participants
                            if p.actor_id == actor and p.status == "active"
                        ),
                        None,
                    )
                    if existing is None:
                        reject_reason = "team invite required"
                    else:
                        participant_id = existing.id
            elif (
                existing is not None
                and existing.actor_id
                and actor is not None
                and existing.actor_id != actor
            ):
                reject_reason = "participant actor mismatch"

            if reject_reason is None:
                # In shared mode ``existing`` is guaranteed here: membership is
                # established only by room creation or the HTTP invite flow.
                participants = [p for p in team.participants if p.id != participant_id]
                participant = TeamParticipantWire(
                    id=participant_id,
                    display_name=display_name,
                    role=_normalize_participant_role(existing.role if existing else "viewer"),
                    actor_id=existing.actor_id if existing and existing.actor_id else actor,
                    joined_at=existing.joined_at if existing else now,
                    last_seen_at=now,
                    status="active",
                    # Preserve an admin-imposed mute across reconnects —
                    # otherwise a muted member could silence-bust by
                    # simply dropping and re-opening the socket.
                    muted=bool(existing.muted) if existing else False,
                    # Likewise preserve the participant's delegation opt-in
                    # so a reconnect doesn't silently drop their twin/host.
                    speak_mode=existing.speak_mode if existing else "manual",
                    twin_agent_id=existing.twin_agent_id if existing else None,
                    host_id=existing.host_id if existing else None,
                )
                participants.append(participant)
                team = team.model_copy(
                    update={
                        "participants": participants,
                        "updated_at": now,
                    }
                )
                teams[team_id] = team
                live_sockets.setdefault(team_id, {})[participant_id] = ws
                socket_loops.setdefault(team_id, {})[participant_id] = owner_loop
                _save()
                ready_payload = {
                    "type": "ready",
                    "team": team.model_dump(),
                    "participant": participant.model_dump(),
                    "server_time": now,
                }

    if ready_payload is None:
        await ws.send_json({"type": "error", "message": reject_reason or "team not found"})
        await ws.close(code=4403 if reject_reason else 4404)
        return

    await _broadcast(team_id, ready_payload, include=participant_id)
    await _broadcast_presence(team_id)

    # Per-connection anti-flood limiter. Local to the handler, so it's
    # freed when the connection closes — no shared map to leak.
    _msg_limiter = SlidingWindowLimiter(limit=_TEAM_WS_MSG_PER_SEC, window_s=1.0)

    try:
        while True:
            raw = await ws.receive_text()
            ctx.refresh()
            # Drop oversized or over-rate frames before parsing/broadcast so
            # one client can't amplify a flood across the whole room.
            if len(raw) > _TEAM_WS_MAX_MSG_BYTES:
                continue
            if not _msg_limiter.allow(participant_id):
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            msg_type = msg.get("type")
            with lock:
                active_participant = _active_participant(team_id, participant_id)
                current_team = teams.get(team_id)
            if active_participant is None:
                # A cross-worker broadcast may already have sent the 4403
                # close while this handler was blocked in receive_text().
                with contextlib.suppress(Exception):
                    await ws.send_json({"type": "error", "message": "participant removed"})
                    await ws.close(code=4403)
                return
            if msg_type in {"ping", "presence:ping"}:
                with lock:
                    current = teams.get(team_id)
                    if current is not None:
                        seen = _now()
                        participants = [
                            p.model_copy(
                                update={
                                    "last_seen_at": seen,
                                    "status": "active",
                                }
                            )
                            if p.id == participant_id
                            else p
                            for p in current.participants
                        ]
                        teams[team_id] = current.model_copy(
                            update={
                                "participants": participants,
                                "updated_at": seen,
                            }
                        )
                        _save()
                await _broadcast(
                    team_id,
                    {"type": "pong", "server_time": _now()},
                    include=participant_id,
                )
                await _broadcast_presence(team_id)
            elif msg_type == "cursor":
                msg_thread_id = str(msg.get("thread_id") or thread_id or "").strip() or None
                await _broadcast(
                    team_id,
                    {
                        "type": "cursor",
                        "team_id": team_id,
                        "thread_id": msg_thread_id,
                        "participant_id": participant_id,
                        "position": msg.get("position"),
                        "server_time": _now(),
                    },
                    exclude=participant_id,
                )
            elif msg_type == "message":
                text = str(msg.get("text") or "").strip()
                if not text:
                    continue
                # Resolve the effective speaker. A message may be sent on
                # another participant's behalf (twin/hosted delegation),
                # honored only if that participant opted in and authorized
                # this sender. Everything downstream — mute/floor checks,
                # attribution, the round_robin advance — uses the effective
                # speaker, so a delegate stands fully in their shoes.
                on_behalf_of = str(msg.get("on_behalf_of") or "").strip() or None
                speaker = active_participant
                spoken_by: str | None = None
                if on_behalf_of and on_behalf_of != participant_id:
                    target = (
                        next(
                            (p for p in current_team.participants if p.id == on_behalf_of),
                            None,
                        )
                        if current_team is not None
                        else None
                    )
                    if target is None or not _authorized_to_speak_for(active_participant, target):
                        await _broadcast(
                            team_id,
                            {
                                "type": "error",
                                "code": "delegation_denied",
                                "message": f"not authorized to speak for {on_behalf_of}",
                            },
                            include=participant_id,
                        )
                        continue
                    speaker = target
                    spoken_by = participant_id
                # Speech chokepoint: mute + room policy, evaluated against
                # the effective speaker. Reuses the loop-top locked snapshot
                # — no second acquisition (a second lock here deadlocks the
                # two-socket portal in tests).
                if current_team is None:
                    allowed = False
                    reason: str | None = "participant removed"
                else:
                    allowed, reason = _participant_can_speak(current_team, speaker)
                if not allowed:
                    await _broadcast(
                        team_id,
                        {"type": "error", "code": "speech_denied", "message": reason},
                        include=participant_id,
                    )
                    continue
                msg_thread_id = str(msg.get("thread_id") or thread_id or "").strip() or None
                payload = {
                    "type": "message",
                    "team_id": team_id,
                    "thread_id": msg_thread_id,
                    "message_id": f"room-msg-{uuid4().hex}",
                    "participant_id": speaker.id,
                    "display_name": speaker.display_name,
                    "spoken_by": spoken_by,
                    "via": _normalize_speak_mode(speaker.speak_mode) if spoken_by else None,
                    "text": text[:4000],
                    "created_at": _now(),
                }
                # Persist every accepted line before fan-out. The transcript is
                # canonical room state even when no twin responder is wired;
                # the in-memory ring is merely the responder's bounded view.
                _remember_line(ctx, team_id, speaker.id, speaker.display_name, text[:4000])
                await _emit_to_peers_or_self(payload)
                # round_robin hands the floor to the next eligible speaker
                # once a message lands. The other turn modes hold the floor
                # until the speaker yields or the moderator moves it.
                if (
                    current_team is not None
                    and _normalize_speaker_policy(current_team.speaker_policy) == "round_robin"
                ):
                    with lock:
                        advanced = teams.get(team_id)
                        if advanced is not None:
                            advanced = advance_round_robin(advanced, speaker.id)
                            teams[team_id] = advanced
                            _save()
                    if advanced is not None:
                        await _broadcast_floor(team_id, advanced)
                        # If the floor just landed on a bound twin, let it (and
                        # any consecutive twins) speak, advancing the rotation.
                        if ctx.twin_responder is not None:
                            await _drive_round_robin_twins(ctx, team_id)
            elif msg_type == "thread:update":
                msg_thread_id = str(msg.get("thread_id") or thread_id or "").strip() or None
                if not msg_thread_id:
                    continue
                payload = {
                    "type": "thread:update",
                    "team_id": team_id,
                    "thread_id": msg_thread_id,
                    "participant_id": participant_id,
                    "display_name": display_name,
                    "reason": str(msg.get("reason") or "updated")[:80],
                    "created_at": _now(),
                }
                await _emit_to_peers_or_self(payload)
            elif msg_type == "floor:request":
                # Raise hand — only meaningful in moderated mode; queued
                # for the moderator to grant. No-op under other policies.
                with lock:
                    floor_team = apply_floor_request(teams.get(team_id), participant_id)
                    if floor_team is not None:
                        teams[team_id] = floor_team
                        _save()
                if floor_team is not None:
                    await _broadcast_floor(team_id, floor_team)
            elif msg_type == "floor:yield":
                # The floor holder gives it up: round_robin advances to the
                # next seat, the moderated/roll_call modes re-open the floor.
                with lock:
                    floor_team = apply_floor_yield(teams.get(team_id), participant_id)
                    if floor_team is not None:
                        teams[team_id] = floor_team
                        _save()
                if floor_team is not None:
                    await _broadcast_floor(team_id, floor_team)
            elif msg_type == "floor:grant":
                # Moderator hands the floor to ``target`` (or, with no
                # target, re-opens it). roll_call + moderated only.
                target_id = str(msg.get("target") or "").strip() or None
                not_moderator = False
                with lock:
                    room_now = teams.get(team_id)
                    floor_team = apply_floor_grant(room_now, active_participant, target_id)
                    if floor_team is not None:
                        teams[team_id] = floor_team
                        _save()
                    elif (
                        room_now is not None
                        and _normalize_speaker_policy(room_now.speaker_policy)
                        in {"roll_call", "moderated"}
                        and not _is_moderator(room_now, active_participant)
                    ):
                        not_moderator = True
                if not_moderator:
                    await _broadcast(
                        team_id,
                        {
                            "type": "error",
                            "code": "not_moderator",
                            "message": "only the moderator can move the floor",
                        },
                        include=participant_id,
                    )
                elif floor_team is not None:
                    await _broadcast_floor(team_id, floor_team)
                    # A twin granted the floor speaks once and keeps it — in
                    # roll_call/moderated the moderator moves the floor on, so
                    # there's no stall to advance past (unlike round_robin).
                    if ctx.twin_responder is not None:
                        granted_id = getattr(floor_team, "current_speaker_id", None)
                        granted = (
                            next(
                                (p for p in floor_team.participants if p.id == granted_id),
                                None,
                            )
                            if granted_id
                            else None
                        )
                        if granted is not None:
                            await _twin_speak(ctx, team_id, granted)
    except WebSocketDisconnect:  # noqa: BLE001 — WS closed normally during send; cleanup proceeds
        pass
    except (ConnectionError, TimeoutError, OSError):  # noqa: BLE001 — WS send failed; cleanup proceeds
        pass
    finally:
        floor_broadcast: TeamRoomWire | None = None
        with lock:
            room = live_sockets.get(team_id)
            if room and room.get(participant_id) is ws:
                room.pop(participant_id, None)
                socket_loops.get(team_id, {}).pop(participant_id, None)
            current = teams.get(team_id)
            if current is not None:
                left_at = _now()
                participants = [
                    p.model_copy(
                        update={
                            "last_seen_at": left_at,
                            "status": "offline",
                        }
                    )
                    if p.id == participant_id and p.status != "removed"
                    else p
                    for p in current.participants
                ]
                updates: dict[str, Any] = {
                    "participants": participants,
                    "updated_at": left_at,
                }
                # Keep turn-based rooms live if the floor holder drops:
                # round_robin advances past them, roll_call/moderated
                # re-open the floor. Also drop them from the hand queue.
                policy_now = _normalize_speaker_policy(current.speaker_policy)
                if policy_now in _TURN_POLICIES and current.current_speaker_id == participant_id:
                    rebuilt = current.model_copy(update={"participants": participants})
                    updates["current_speaker_id"] = (
                        _next_speaker(rebuilt, participant_id)
                        if policy_now == "round_robin"
                        else None
                    )
                if participant_id in (current.floor_requests or []):
                    updates["floor_requests"] = [
                        q for q in current.floor_requests if q != participant_id
                    ]
                new_team = current.model_copy(update=updates)
                teams[team_id] = new_team
                _save()
                if "current_speaker_id" in updates or "floor_requests" in updates:
                    floor_broadcast = new_team
        await _broadcast_presence(team_id)
        if floor_broadcast is not None:
            await _broadcast_floor(team_id, floor_broadcast)
