"""Subagent FastAPI router.

This router exposes Claude-style subagent definitions and a direct dispatch
endpoint. It deliberately does not touch the SkillRegistry.
"""

from __future__ import annotations

import contextlib
from typing import Any

from pydantic import BaseModel, ConfigDict

try:
    from fastapi import APIRouter, HTTPException, Query, Request
    from fastapi.responses import StreamingResponse

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment,misc]
    HTTPException = None  # type: ignore[assignment,misc]
    Query = None  # type: ignore[assignment,misc]
    Request = None  # type: ignore[assignment,misc]
    StreamingResponse = None  # type: ignore[assignment,misc]

from runtime.sensing._fastapi_guard import require_fastapi


class SubagentDispatchRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    subagent_type: str | None = None
    name: str | None = None
    prompt: str
    context: dict[str, Any] | None = None
    timeout_s: int = 300
    # Optional thread anchor for per-role memory continuity. When set,
    # the second call with the same (thread_id, subagent_type) sees the
    # first call's prompt + output rendered as a "Prior turns" prefix.
    # Leave empty for stateless calls (default).
    thread_id: str | None = None
    turn_id: str | None = None
    run_id: str | None = None
    trace_id: str | None = None
    parent_task_id: str | None = None
    source: str | None = None
    # When True (default), inject prior subagent turns for this thread.
    # Set to False to force a fresh single-shot call without history.
    share_history: bool = True
    # Per-call tool whitelist override. Each name is added to the role's
    # default tool_allowlist for THIS call only. Common use:
    #   {"extra_tools": ["read_file", "list_cwd"]} so a researcher can
    #   also read local docs in addition to its default web tools.
    # Use "tool_allowlist_mode": "all" to grant the full sub-agent
    # tool catalog (caller is asserting it trusts the role for this turn).
    extra_tools: list[str] | None = None
    # Capabilities the chosen subagent must declare before any runner
    # work starts; a missing one fails the dispatch loudly (dsh
    # fail-loud rule) instead of being accepted-then-ignored.
    requires_capabilities: list[str] | None = None
    # Durable subagent session to continue (dsh continuable). When set,
    # the prior transcript is injected and the new turn appends to the same
    # session; unknown session ids fail loudly before any runner work.
    continue_session_id: str | None = None


_MIN_DISPATCH_TIMEOUT_S = 1
_MAX_DISPATCH_TIMEOUT_S = 900
_SUBAGENT_BUS_QUEUE_MAX = 256
_SUBAGENT_BUS_REPLAY_MAX = 500


def _bounded_dispatch_timeout(value: Any) -> int:
    try:
        timeout = int(value)
    except (TypeError, ValueError):
        timeout = 300
    return max(_MIN_DISPATCH_TIMEOUT_S, min(_MAX_DISPATCH_TIMEOUT_S, timeout))


def _dispatch_context_from_body(body: SubagentDispatchRequest) -> dict[str, Any]:
    ctx = dict(body.context or {})
    for field_name in (
        "thread_id",
        "turn_id",
        "run_id",
        "trace_id",
        "parent_task_id",
        "source",
    ):
        value = getattr(body, field_name, None)
        if isinstance(value, str) and value.strip():
            ctx.setdefault(field_name, value.strip())
    ctx.setdefault("share_history", body.share_history)
    if body.extra_tools:
        ctx.setdefault("extra_tools", body.extra_tools)
    return ctx


def _put_bounded_bus_event(queue: Any, event: dict[str, Any]) -> None:
    """Keep a slow SSE consumer bounded, retaining the newest lifecycle data."""
    if queue.full():
        with contextlib.suppress(Exception):
            queue.get_nowait()
    with contextlib.suppress(Exception):
        queue.put_nowait(event)


def create_subagents_router(
    *,
    registry: Any = None,
    thread_store: Any = None,
    workspace_root: Any = None,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> Any:
    require_fastapi(__name__)

    router = APIRouter(tags=["subagents"])

    def _auth(request: Any) -> str | None:
        from .openai_gateway_router import _resolve_actor

        return _resolve_actor(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    def _require_root_thread_access(request: Any, root_thread_id: str) -> None:
        actor = _auth(request)
        if not require_auth:
            return
        principal = getattr(getattr(request, "state", None), "principal", None)
        if not actor or principal is None:
            raise HTTPException(401, "authentication required")
        if thread_store is None:
            raise HTTPException(503, "thread ownership store unavailable")
        thread = None
        if hasattr(thread_store, "get"):
            thread = thread_store.get(root_thread_id)
        if thread is None and hasattr(thread_store, "get_state"):
            thread = thread_store.get_state(root_thread_id)
        if not isinstance(thread, dict):
            raise HTTPException(404, f"thread not found: {root_thread_id}")
        raw_metadata = thread.get("metadata")
        metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
        owner = metadata.get("owner_actor_id") or metadata.get("actor_id")
        privileged = bool(principal.roles.intersection({"admin", "operator"}))
        if owner != actor and not privileged:
            raise HTTPException(404, f"thread not found: {root_thread_id}")
        if privileged:
            return
        stored_tenant = str(metadata.get("tenant_id") or "").strip()
        principal_tenant = str(getattr(principal, "tenant_id", "") or "").strip()
        if (
            principal_tenant
            and not principal_tenant.startswith("legacy:")
            and stored_tenant != principal_tenant
        ):
            raise HTTPException(404, f"thread not found: {root_thread_id}")
        if principal_tenant and stored_tenant and stored_tenant != principal_tenant:
            raise HTTPException(404, f"thread not found: {root_thread_id}")

    def _authenticated_dispatch_context(
        request: Any,
        body: SubagentDispatchRequest,
    ) -> tuple[dict[str, Any], str | None, Any]:
        """Resolve direct-dispatch execution authority from the owned thread.

        Anonymous local mode retains the legacy caller-selected context. In
        authenticated mode, the thread id is mandatory and the principal,
        workspace and tool policy are all server-owned.
        """
        actor = _auth(request)
        if not require_auth:
            return _dispatch_context_from_body(body), None, None
        principal = getattr(getattr(request, "state", None), "principal", None)
        if not actor or principal is None:
            raise HTTPException(401, "authentication required")
        thread_id = str(body.thread_id or "").strip()
        if not thread_id:
            raise HTTPException(400, "thread_id is required")
        try:
            from runtime.memory.threads.event_log import validate_thread_id

            thread_id = validate_thread_id(thread_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if thread_store is None or not hasattr(thread_store, "get"):
            raise HTTPException(503, "thread ownership store unavailable")
        thread = thread_store.get(thread_id)
        raw_metadata = thread.get("metadata") if isinstance(thread, dict) else None
        metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
        if metadata.get("owner_actor_id") != actor:
            raise HTTPException(404, f"thread not found: {thread_id}")
        tenant_id = str(getattr(principal, "tenant_id", "") or "").strip()
        if not tenant_id or metadata.get("tenant_id") != tenant_id:
            raise HTTPException(404, f"thread not found: {thread_id}")

        from runtime.sensing.gateway.thread_workspace import (
            PROTECTED_WORKSPACE_METADATA_KEYS,
            verified_managed_workspace,
        )

        workspace = verified_managed_workspace(
            workspace_root,
            thread_id=thread_id,
            metadata=metadata,
        )
        if workspace is None:
            raise HTTPException(409, "verified managed thread workspace required")

        context = _dispatch_context_from_body(body)
        untrusted_keys = {
            *PROTECTED_WORKSPACE_METADATA_KEYS,
            "workspace",
            "sandbox_dir",
            "locked_write_root",
            "_locked_write_root",
            "allowed_paths",
            "tool_allowlist",
            "tool_allowlist_mode",
            "allowed_tools",
            "tools",
            "extra_tool_allowlist",
            "extra_tools",
            "extra_skills",
            "actor",
            "actor_id",
            "owner_actor_id",
            "tenant_id",
            "caller_thread_id",
        }
        for key in untrusted_keys:
            context.pop(key, None)
        raw_runtime_metadata = context.get("runtime_session_metadata")
        runtime_metadata = (
            dict(raw_runtime_metadata) if isinstance(raw_runtime_metadata, dict) else {}
        )
        for key in untrusted_keys:
            runtime_metadata.pop(key, None)
        workspace_str = str(workspace)
        runtime_metadata.update(
            {
                "thread_id": thread_id,
                "workspace_path": workspace_str,
                "_locked_write_root": workspace_str,
                "_artifact_output_root": str(workspace / "output" / "final"),
                "owner_actor_id": actor,
                "tenant_id": tenant_id,
            }
        )
        context.update(
            {
                "thread_id": thread_id,
                "caller_thread_id": thread_id,
                "actor": actor,
                "actor_id": actor,
                "owner_actor_id": actor,
                "tenant_id": tenant_id,
                "workspace_path": workspace_str,
                "_locked_write_root": workspace_str,
                # A client cannot promote a direct HTTP dispatch to the full
                # catalog. The selected role's server-defined allowlist is the
                # only positive tool grant.
                "tool_allowlist_mode": "role",
                "runtime_session_metadata": runtime_metadata,
            }
        )
        return context, workspace_str, principal

    def _registry() -> Any:
        if registry is not None:
            return registry
        from runtime.execution.subagents import get_subagent_registry

        return get_subagent_registry()

    @router.get("/api/subagents")
    def list_subagents(request: Request) -> dict[str, Any]:
        _auth(request)  # AUTH-OK: actor-agnostic — subagent definitions are global
        reg = _registry()
        items: list[dict[str, Any]] = []
        if reg is not None:
            items.extend(d.to_wire() for d in reg.all())
        from runtime.execution.suckers.ephemeral_agents import BUILTIN_ROLES

        for role in BUILTIN_ROLES.values():
            items.append(
                {
                    "name": role.id,
                    "description": role.description,
                    "tools": list(role.tool_allowlist),
                    "model": None,
                    "source_path": "",
                    "scope": "builtin",
                }
            )
        items.sort(key=lambda item: (item["scope"] != "project", item["name"]))
        return {"subagents": items}

    @router.get("/api/subagents/sessions")
    def list_subagent_sessions(
        request: Request,
        query: str = "",
        target: str = "",
        limit: int = 50,
    ) -> dict[str, Any]:
        """List durable sub-agent sessions as mention-autocomplete candidates.

        Backs the host's ``@session:<id>`` / ``@subagent:<id>`` autocomplete
        (dsh host candidate seam): returns the store's ranked candidates
        (``sessionId`` / ``label`` / ``createdAt``) filtered by an optional
        substring ``query``, excluding ``target``. ``limit`` is clamped to
        [1, 200]. Best-effort — an unavailable store returns an empty list.
        """
        actor = _auth(request)
        principal = getattr(getattr(request, "state", None), "principal", None)
        if require_auth:
            if not target.strip():
                raise HTTPException(400, "target thread is required")
            if not actor or principal is None:
                raise HTTPException(401, "authentication required")
            if thread_store is None or not hasattr(thread_store, "get"):
                raise HTTPException(503, "thread ownership store unavailable")
            thread = thread_store.get(target.strip())
            raw_metadata = thread.get("metadata") if isinstance(thread, dict) else None
            metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
            if (
                metadata.get("owner_actor_id") != actor
                or metadata.get("tenant_id") != principal.tenant_id
            ):
                raise HTTPException(404, f"thread not found: {target.strip()}")
        from runtime.execution.subagents.sessions import get_subagent_session_store

        store = get_subagent_session_store()
        if store is None:
            return {"candidates": []}
        try:
            limit_int = max(1, min(int(limit), 200))
        except (TypeError, ValueError):
            limit_int = 50
        candidates = store.list_reference_candidates(
            target_id=target or "",
            query=query or "",
            limit=limit_int,
            owner_actor_id=(actor if require_auth else None),
            tenant_id=(principal.tenant_id if require_auth and principal is not None else None),
        )
        return {"candidates": candidates}

    @router.get("/api/subagents/{name}")
    def get_subagent(request: Request, name: str) -> dict[str, Any]:
        _auth(request)  # AUTH-OK: actor-agnostic — subagent definitions are global
        reg = _registry()
        if reg is not None and reg.has(name):
            return reg.get(name).to_wire(include_prompt=True)
        from runtime.execution.suckers.ephemeral_agents import BUILTIN_ROLES

        role = BUILTIN_ROLES.get(name)
        if role is None:
            raise HTTPException(404, f"subagent not found: {name}")
        return {
            "name": role.id,
            "description": role.description,
            "tools": list(role.tool_allowlist),
            "model": None,
            "source_path": "",
            "scope": "builtin",
            "system_prompt": role.system_prompt,
        }

    @router.post("/api/subagents/dispatch")
    def dispatch_subagent(request: Request, body: SubagentDispatchRequest) -> dict[str, Any]:
        ctx, workspace_path, _principal = _authenticated_dispatch_context(request, body)
        target = (body.subagent_type or body.name or "").strip()
        if not target:
            raise HTTPException(400, "subagent_type is required")
        if not body.prompt.strip():
            raise HTTPException(400, "prompt is required")
        from runtime.execution.subagents import call_subagent

        timeout_s = _bounded_dispatch_timeout(body.timeout_s)
        result = call_subagent(
            target,
            body.prompt,
            context=ctx,
            timeout_s=timeout_s,
            timeout_seconds=float(timeout_s),
            workspace_path=workspace_path or "",
            requires_capabilities=body.requires_capabilities,
            continue_session_id=body.continue_session_id,
        )
        if not result.get("success"):
            raise HTTPException(400, result.get("error") or "subagent failed")
        return result

    @router.post("/api/subagents/dispatch/stream")
    def dispatch_subagent_stream(
        request: Request,
        body: SubagentDispatchRequest,
    ) -> Any:
        """SSE-streaming variant of /api/subagents/dispatch.

        Emits events in real time so the caller (frontend cowork pane,
        CLI, or another agent) can render the subagent's progress
        instead of waiting for the final JSON. Event shape mirrors the
        in-memory event_emitter used by the team-runner path.

        Event types streamed:
          - subagent_spawned   : {agent_id, role, codename, avatar, ...}
          - sub_text_delta     : {agent_id, round, delta}
          - sub_tool_start     : {agent_id, round, skill, tool_call_id, args_preview}
          - sub_tool_end       : {agent_id, round, skill, status, duration_ms}
          - subagent_finished  : {agent_id, ok, duration_s, iteration_count, ...}
          - result             : {success, output, error, ...}  (terminal, with full payload)

        The terminal ``result`` event always fires last so consumers can
        treat it as the canonical end-of-stream marker.
        """
        stream_ctx, workspace_path, _principal = _authenticated_dispatch_context(request, body)
        target = (body.subagent_type or body.name or "").strip()
        if not target:
            raise HTTPException(400, "subagent_type is required")
        if not body.prompt.strip():
            raise HTTPException(400, "prompt is required")

        import json
        import queue
        import threading

        from runtime.execution.subagents import call_subagent

        event_queue: queue.Queue[dict[str, Any] | None] = queue.Queue(
            maxsize=1024,
        )

        def _emitter(event: dict[str, Any]) -> None:
            with contextlib.suppress(queue.Full):
                # Drop on flood — preserves liveness over completeness.
                # Producer (subagent loop) must never block on the queue.
                event_queue.put_nowait(event)

        def _runner() -> None:
            try:
                timeout_s = _bounded_dispatch_timeout(body.timeout_s)
                result = call_subagent(
                    target,
                    body.prompt,
                    context=stream_ctx,
                    timeout_s=timeout_s,
                    timeout_seconds=float(timeout_s),
                    workspace_path=workspace_path or "",
                    event_emitter=_emitter,
                    requires_capabilities=body.requires_capabilities,
                    continue_session_id=body.continue_session_id,
                )
                event_queue.put({"type": "result", **result})
            except Exception as exc:  # noqa: BLE001 — surface as terminal event
                event_queue.put(
                    {
                        "type": "result",
                        "success": False,
                        "error": f"{type(exc).__name__}: {exc}",
                        "agent_id": target,
                    }
                )
            finally:
                event_queue.put(None)  # sentinel: stream done

        worker = threading.Thread(
            target=_runner,
            name=f"subagent-stream-{target}",
            daemon=True,
        )
        worker.start()

        def _sse_iter() -> Any:
            """SSE event generator. Each event is one ``data: <json>\\n\\n`` block."""
            while True:
                # Block on the queue rather than busy-loop. Worker pushes
                # None on completion which terminates this iterator.
                event = event_queue.get()
                if event is None:
                    yield 'data: {"type":"done"}\n\n'
                    return
                try:
                    payload = json.dumps(event, ensure_ascii=False)
                except (TypeError, ValueError):
                    # Non-serializable event → emit a marker rather than crash
                    payload = json.dumps(
                        {
                            "type": "error",
                            "error": "event-not-serializable",
                            "event_type": str(event.get("type", "unknown")),
                        }
                    )
                yield f"data: {payload}\n\n"

        return StreamingResponse(
            _sse_iter(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",  # disable nginx buffering if proxied
            },
        )

    @router.get("/api/subagents/stream/{root_thread_id}")
    async def _subagents_bus_stream(
        request: Request,
        root_thread_id: str,
        after_seq: int = Query(0, ge=0),
        limit: int | None = Query(None, ge=1, le=_SUBAGENT_BUS_REPLAY_MAX),
    ) -> Any:
        """Live SSE stream of the typed sub-agent event bus for a root thread.

        Replays events after ``after_seq`` (0 = whole buffer), then pushes new
        events as they land. The Workbench subscribes here for an independent,
        full-fidelity stream of a sub-agent run. Pass ``limit`` for a bounded
        snapshot instead of a long-lived subscription.
        """
        _require_root_thread_access(request, root_thread_id)
        return _subagent_bus_endpoint(root_thread_id, after_seq, limit)

    return router


async def _subagent_bus_sse(
    root_thread_id: str,
    after_seq: int,
    limit: int | None,
) -> Any:
    """Generator: replay bus history, then live-stream new events.

    ``limit`` (when set) turns this into a snapshot: it yields at most
    ``limit`` replayed events then signals ``done`` and returns — no
    long-lived subscription. Useful for late UIs and for tests.
    """
    import asyncio
    import json

    from runtime.execution.subagents.event_bus import get_bus

    bus = get_bus(root_thread_id)
    if bus is None:
        yield 'data: {"type":"done"}\n\n'
        return

    for emitted, event in enumerate(bus.replay(after_seq=after_seq)):
        if limit is not None and emitted >= limit:
            break
        try:
            payload = json.dumps(event, ensure_ascii=False)
        except (TypeError, ValueError):
            payload = json.dumps({"type": "error", "error": "not-serializable"})
        yield f"data: {payload}\n\n"

    if limit is not None:
        yield 'data: {"type":"done"}\n\n'
        return

    loop = asyncio.get_running_loop()
    # ``None`` is the shutdown sentinel, so the live queue is widened relative to
    # the replay events above. Keep the streamed value in its own name: reusing
    # ``event`` from the replay loop would pin it to the narrower ``dict[str, Any]``.
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=_SUBAGENT_BUS_QUEUE_MAX)

    def _on_event(event: dict[str, Any]) -> None:
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(_put_bounded_bus_event, queue, event)

    unsubscribe = bus.subscribe(_on_event)
    try:
        yield 'data: {"type":"subscribed"}\n\n'
        while True:
            try:
                live_event = await asyncio.wait_for(queue.get(), timeout=15.0)
            except TimeoutError:
                yield ": keepalive\n\n"
                continue
            if live_event is None:
                break
            try:
                payload = json.dumps(live_event, ensure_ascii=False)
            except (TypeError, ValueError):
                payload = json.dumps({"type": "error", "error": "not-serializable"})
            yield f"data: {payload}\n\n"
    finally:
        unsubscribe()


def _subagent_bus_endpoint(
    root_thread_id: str,
    after_seq: int,
    limit: int | None,
) -> Any:
    """SSE endpoint for the typed sub-agent event bus."""
    return StreamingResponse(
        _subagent_bus_sse(root_thread_id, after_seq, limit),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering if proxied
        },
    )


__all__ = ["SubagentDispatchRequest", "create_subagents_router"]
