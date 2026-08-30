from __future__ import annotations

import logging
import time
from functools import partial
from typing import Any
from uuid import uuid4

from runtime.memory.journal import journal_context

# Split-out submodules. The public API of this module is preserved by
# re-exporting the names below so external call sites
# ``from runtime.sensing.gateway.openai_gateway_router import X`` keep working.
from ._openai_gateway_router_helpers import (
    _deep_requested,
    _evict_idle_rate_buckets,  # noqa: F401
    _reasoning_effort_from_body,
)
from ._openai_gateway_router_ratelimit import _PerActorRateLimiter
from ._openai_gateway_router_run import _run_chat
from ._openai_gateway_router_synthesize import (
    _maybe_reflex_chat,
)
from ._openai_gateway_router_synthesize import (
    synthesize_reply as synthesize_reply,
)
from .openai_gateway.context_manager import (
    _extract_goal,
    _normalize_conversation_messages,
)
from .openai_gateway.mix import (
    is_mix_model,
    mix_model_ids,
    mix_sse_frames,
    run_mix_chat,
)
from .openai_gateway.request_parser import _resolve_actor
from .openai_gateway.stream_handler import (
    _reflex_stream_frames,
    _stream_chat_wrapped,
)

try:
    from fastapi import APIRouter, HTTPException, Request
    from fastapi.responses import StreamingResponse

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment,misc]
    Request = None  # type: ignore[assignment,misc]

from runtime.platform.models import ParsedIntent
from runtime.platform.models.llm import default_reasoning_effort
from runtime.safety.recovery.tenant_scope import (
    AUTHORITATIVE_SCOPE_CONTEXT_KEY,
    authoritative_scope_context,
)
from runtime.sensing._fastapi_guard import require_fastapi

# Re-export formatting helpers from openai_formatting.py so call
# sites inside this file stay unchanged. The public API of that
# module uses the non-underscored names.
from .openai_formatting import (  # noqa: E402,I001
    _pick_output_keys,  # noqa: F401
    _pick_preview_keys,  # noqa: F401
    _short,  # noqa: F401
)


def create_openai_router(
    stack: Any,
    *,
    default_arm: str = "code_arm",
    reflex_router: Any = None,
    prompt_optimizer: Any = None,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
    jwt_leeway_seconds: int = 0,
    agent_registry: Any = None,
    max_concurrent_completions_per_actor: int = 4,
    max_completions_per_minute_per_actor: int = 30,
) -> Any:
    require_fastapi(__name__)

    router = APIRouter()

    # ── Per-actor rate limiting ──────────────────────────────
    #
    # /v1/chat/completions runs a planner + LLM round-trip per call.
    # A small bot can burn quota fast. Limit (a) concurrent in-flight
    # completions per actor (semaphore) and (b) calls/minute per actor
    # (sliding window). Anonymous callers (require_auth=False) are all
    # bucketed under a single shared key — they collectively can't
    # exceed one actor's allotment.
    _rate_limiter = _PerActorRateLimiter(
        concurrent_limit=max_concurrent_completions_per_actor,
        per_min_limit=max_completions_per_minute_per_actor,
    )

    def _agent_owned_by_actor(agent_id: str, actor: str | None) -> bool:
        if not agent_id:
            return True  # no agent scope to police
        if agent_registry is None:
            # No registry -> can't check ownership. Fail closed only
            # if auth is actually required (otherwise authoring with
            # the global memory namespace stays open).
            return not require_auth
        try:
            owner = None
            for attr in ("owner_of", "owner", "get_owner", "actor_of"):
                fn = getattr(agent_registry, attr, None)
                if callable(fn):
                    owner = fn(agent_id) if attr != "owner" else fn
                    break
            if owner is None:
                # Registry doesn't expose ownership at all. Treat
                # "any registered agent" as global-writable when
                # unauthenticated, but if we have an actor, only allow
                # writes against agents prefixed with that actor.
                if actor and not agent_id.startswith(actor):  # noqa: SIM103
                    return False
                return True
            return owner == actor
        except Exception:  # noqa: BLE001 — best-effort; fail-open
            return False

    def _list_openai_models() -> dict[str, Any]:
        now = int(time.time())
        return {
            "object": "list",
            "data": [
                {
                    "id": f"echo-agent/{name}",
                    "object": "model",
                    "created": now,
                    "owned_by": "echo-agent",
                }
                for name in stack.registry.all_names()
            ]
            + [
                {
                    "id": "echo-agent",
                    "object": "model",
                    "created": now,
                    "owned_by": "echo-agent",
                }
            ]
            + [
                {
                    "id": _mix_id,
                    "object": "model",
                    "created": now,
                    "owned_by": "echo-agent",
                }
                for _mix_id in mix_model_ids()
            ],
        }

    @router.get("/v1/models")
    def list_models(request: Request) -> dict[str, Any]:
        _resolve_actor(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
            jwt_leeway_seconds=jwt_leeway_seconds,
        )
        return _list_openai_models()

    @router.get("/api/models")
    def list_models_alias(request: Request) -> dict[str, Any]:
        _resolve_actor(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
            jwt_leeway_seconds=jwt_leeway_seconds,
        )
        return {
            "models": [
                {
                    "id": model["id"],
                    "name": model["id"],
                    "provider": model["owned_by"],
                }
                for model in _list_openai_models()["data"]
            ]
        }

    # ── Mix preset config (proposer pool / aggregator / count) ──
    # Read+write the user's echo-mix mixture so the settings UI can
    # compose its own pool. Resolution at run time is config → env →
    # default (see openai_gateway/mix.py).
    @router.get("/api/mix-config")
    def get_mix_config(request: Request) -> dict[str, Any]:
        _resolve_actor(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
            jwt_leeway_seconds=jwt_leeway_seconds,
        )
        from .openai_gateway.mix import _DEFAULT_N, load_mix_config

        cfg = load_mix_config()
        return {
            "proposers": cfg.get("proposers") or [],
            "aggregator": cfg.get("aggregator") or "",
            "n": cfg.get("n") or _DEFAULT_N,
        }

    @router.put("/api/mix-config")
    def put_mix_config(body: dict[str, Any], request: Request) -> dict[str, Any]:
        _resolve_actor(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
            jwt_leeway_seconds=jwt_leeway_seconds,
        )
        from .openai_gateway.mix import save_mix_config

        return save_mix_config(body if isinstance(body, dict) else {})

    @router.post("/v1/chat/completions")
    def chat_completions(body: dict[str, Any], request: Request) -> Any:
        messages = body.get("messages") or []
        if not isinstance(messages, list) or not messages:
            raise HTTPException(400, "messages must be a non-empty list")

        # Auth + rate-limit at the top so spammers don't get to touch
        # planner / memory / journal at all.
        actor = _resolve_actor(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
            jwt_leeway_seconds=jwt_leeway_seconds,
        )
        _completion_slot = _rate_limiter.acquire(actor, request)
        try:
            return _chat_completions_impl(body, request, messages, actor)
        finally:
            try:  # noqa: SIM105
                _rate_limiter.release(_completion_slot)
            except Exception:  # noqa: BLE001 — completion slot release; lock might already be released
                pass

    def _chat_completions_impl(
        body: dict[str, Any],
        request: Request,
        messages: list[Any],
        actor: str | None,
    ) -> Any:
        principal = getattr(getattr(request, "state", None), "principal", None)
        tenant_id = getattr(principal, "tenant_id", None)
        owner_actor_id = getattr(principal, "actor_id", None) or actor
        from runtime.safety.auth.scope import scope_from_principal

        memory_tenant_scope = scope_from_principal(principal)
        conversation_messages = _normalize_conversation_messages(messages)
        from runtime.memory.users.profile import (
            memories_from_messages,
            merge_profile_memories,
        )

        explicit_memories = memories_from_messages(conversation_messages)
        stored_memories: list[str] = []
        written_memory_count = 0
        raw_context = body.get("context")
        request_context: dict[str, Any] = raw_context if isinstance(raw_context, dict) else {}
        page_memory_mode = str(request_context.get("page_agent_memory_mode") or "").strip()
        agent_name_for_memory = str(
            request_context.get("agent")
            or request_context.get("agent_id")
            or body.get("agent")
            or "",
        ).strip()
        project_for_memory = str(request_context.get("project") or "").strip()
        allow_memory_read = page_memory_mode != "ephemeral"
        allow_memory_write = page_memory_mode in ("", "write_allowed")
        # ACL: never let an authenticated actor write into a different
        # actor's per-agent memory by passing context.agent. When the
        # ownership check fails, demote to global scope (don't fully
        # disable writes — backwards compat for non-authed deployments).
        if (
            allow_memory_write
            and agent_name_for_memory
            and not _agent_owned_by_actor(agent_name_for_memory, actor)
        ):
            agent_name_for_memory = ""
        try:
            from runtime.memory.users.user_store import (
                add_fact,
                read_config,
                relevant_memory_texts,
            )

            memory_config = read_config(memory_tenant_scope)
            if allow_memory_write and memory_config.get("auto_capture_enabled", True):
                memory_scope = (
                    "project"
                    if project_for_memory
                    else ("agent" if agent_name_for_memory else "global")
                )
                source = (
                    f"page-agent:{agent_name_for_memory}"
                    if page_memory_mode == "write_allowed" and agent_name_for_memory
                    else ("page-agent" if page_memory_mode == "write_allowed" else "chat")
                )
                for memory in explicit_memories:
                    if (
                        add_fact(
                            memory,
                            category="profile",
                            source=source,
                            scope=memory_scope,
                            agent_id=agent_name_for_memory or None,
                            project=project_for_memory or None,
                            tenant_scope=memory_tenant_scope,
                        )
                        is not None
                    ):
                        written_memory_count += 1
            if allow_memory_read:
                stored_memories = relevant_memory_texts(
                    _extract_goal(messages),
                    limit=8,
                    agent_id=agent_name_for_memory or None,
                    project=project_for_memory or None,
                    scope=memory_tenant_scope,
                )
        except (OSError, ValueError) as _e:
            _logger = logging.getLogger(__name__)
            _logger.warning("memory read/write failed: %s", _e)
            stored_memories = []
            written_memory_count = 0
        profile_memories = merge_profile_memories(
            stored_memories,
            explicit_memories if page_memory_mode != "ephemeral" else [],
        )

        goal = _extract_goal(messages)
        if not goal:
            raise HTTPException(400, "no user message found in messages")

        # ``actor`` was already resolved by the wrapper above.

        stream = bool(body.get("stream", False))
        stream_mode = str(body.get("stream_mode") or "full").lower()
        if stream_mode not in ("full", "values"):
            stream_mode = "full"
        requested_model = body.get("model", "echo-agent")
        reasoning_effort = _reasoning_effort_from_body(body) or default_reasoning_effort(
            requested_model
        )
        force_deep = _deep_requested(body)

        agent_id = body.get("agent")
        selected_agent: Any = None
        if agent_id is not None:
            if agent_registry is None:
                raise HTTPException(
                    400,
                    "agent parameter given but no agent_registry configured",
                )
            if not isinstance(agent_id, str) or not agent_id:
                raise HTTPException(400, "agent must be a non-empty string")
            if agent_registry.has(agent_id):
                selected_agent = agent_registry.get(agent_id)
            else:
                import logging as _lg

                _lg.info(
                    "unknown agent %r · falling back to no-agent mode",
                    agent_id,
                )
                selected_agent = None

        conversation_id = body.get("conversation_id")
        if conversation_id is not None and (
            not isinstance(conversation_id, str) or not conversation_id
        ):
            raise HTTPException(
                400,
                "conversation_id must be a non-empty string",
            )
        if conversation_id is None:
            conversation_id = uuid4().hex

        intent = ParsedIntent(
            raw=goal,
            intent_type="task",
            normalized_goal=goal,
            user_context={
                "conversation_messages": conversation_messages,
                "profile_memories": profile_memories,
                "memory_written_count": written_memory_count,
                **(
                    {
                        AUTHORITATIVE_SCOPE_CONTEXT_KEY: authoritative_scope_context(
                            memory_tenant_scope
                        )
                    }
                    if memory_tenant_scope is not None
                    else {}
                ),
                **({"reasoning_effort": reasoning_effort} if reasoning_effort else {}),
            },
        )

        # Mix virtual model: explicit request for mixture-of-agents
        # orchestration. Runs BEFORE reflex so it's never short-circuited by
        # the trivial-input fast path.
        if is_mix_model(requested_model):
            from runtime.sensing.model_router.actor_context import (
                current_actor as _model_actor_ctx,
            )

            _mix_agent_ctx = selected_agent.agent_id if selected_agent is not None else None
            _mix_token = _model_actor_ctx.set(actor)
            try:
                with journal_context(
                    agent_id=_mix_agent_ctx,
                    conversation_id=conversation_id,
                    tenant_id=tenant_id,
                    owner_actor_id=owner_actor_id,
                ):
                    mix_result = run_mix_chat(
                        stack,
                        intent,
                        requested_model,
                        default_arm,
                        actor=actor,
                        agent=selected_agent,
                        run_chat=partial(
                            _run_chat,
                            conversation_id=conversation_id,
                            tenant_id=tenant_id,
                            owner_actor_id=owner_actor_id,
                        ),
                        optimizer=prompt_optimizer,
                    )
            finally:
                _model_actor_ctx.reset(_mix_token)
            mix_meta = mix_result.setdefault("echo", {})
            mix_meta["conversation_id"] = conversation_id
            if selected_agent is not None:
                mix_meta["agent"] = selected_agent.agent_id
            if stream:
                return StreamingResponse(
                    mix_sse_frames(mix_result, requested_model),
                    media_type="text/event-stream",
                )
            return mix_result

        # Deep runs must not be short-circuited by the reflex fast-path —
        # that's what makes a trivial prompt come back with no task_id/trace.
        reflex_response = (
            None
            if force_deep
            else _maybe_reflex_chat(
                reflex_router,
                intent,
                stack,
                requested_model,
                actor=actor,
            )
        )
        if reflex_response is not None:
            if reflex_response.get("echo") is not None:
                reflex_response["echo"]["conversation_id"] = conversation_id
                if selected_agent is not None:
                    reflex_response["echo"]["agent"] = selected_agent.agent_id
            if stream:
                return StreamingResponse(
                    _reflex_stream_frames(reflex_response, requested_model),
                    media_type="text/event-stream",
                )
            return reflex_response

        agent_id_for_ctx = selected_agent.agent_id if selected_agent is not None else None

        from runtime.sensing.model_router.actor_context import current_actor as _model_actor_ctx

        if stream:
            return StreamingResponse(
                _stream_chat_wrapped(
                    stack,
                    intent,
                    requested_model,
                    default_arm,
                    actor=actor,
                    agent=selected_agent,
                    agent_id=agent_id_for_ctx,
                    conversation_id=conversation_id,
                    tenant_id=tenant_id,
                    owner_actor_id=owner_actor_id,
                    stream_mode=stream_mode,
                ),
                media_type="text/event-stream",
            )
        _model_actor_token = _model_actor_ctx.set(actor)
        try:
            with journal_context(
                agent_id=agent_id_for_ctx,
                conversation_id=conversation_id,
                tenant_id=tenant_id,
                owner_actor_id=owner_actor_id,
            ):
                response = _run_chat(
                    stack,
                    intent,
                    requested_model,
                    default_arm,
                    optimizer=prompt_optimizer,
                    actor=actor,
                    agent=selected_agent,
                    force_deep=force_deep,
                    conversation_id=conversation_id,
                    tenant_id=tenant_id,
                    owner_actor_id=owner_actor_id,
                )
        finally:
            _model_actor_ctx.reset(_model_actor_token)
        response.setdefault("echo", {})["conversation_id"] = conversation_id
        return response

    return router
