"""
Endpoint handlers for the agents router.

Split out of ``agents_router.py`` (pure structural refactor — no logic
changes). ``_build_endpoints`` registers every agents endpoint on an
injected router, reading shared state through an ``_AgentsCtx`` context
bundle so the handlers stay free of module-level globals — the same
"own-your-state" pattern the router factory already followed and the
one used by ``_config_endpoints.py`` / ``_observability_router_factory.py``.

The handler bodies themselves live in ``_agents_endpoints_*`` submodules
(``_agents_endpoints_crud``, ``_tasks``, ``_tools``,
``_system``, ``_conversations``, ``_groups``), each exposing a
``_register_*`` function that attaches its endpoints to the router. This
module keeps ``_AgentsCtx`` and ``_build_endpoints`` and delegates to
those register functions.

The auth / identity helpers (``_auth``, ``_resolve_identity``,
``_require_admin``, ``_require_task_owner``, ``_require_thread_owner``)
were closure-scoped in the original factory; they are still built here,
reading the same fields off the injected context, and are passed to the
register submodules through an ``_AuthActions`` bundle.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any

try:
    from fastapi import HTTPException

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    HTTPException = None  # type: ignore[assignment, misc]

from ._agents_endpoints_conversations import _register_conversations
from ._agents_endpoints_crud import _register_agents_crud
from ._agents_endpoints_groups import _register_groups
from ._agents_endpoints_local_partners import _register_local_partners
from ._agents_endpoints_shared import _AuthActions
from ._agents_endpoints_system import _register_system
from ._agents_endpoints_tasks import _register_tasks
from ._agents_endpoints_tools import _register_agents_tools


def _identity_has_admin_role(identity: Any) -> bool:
    """Return whether the authenticated identity carries the admin role."""
    if identity is None:
        return False
    roles = getattr(identity, "roles", ()) or ()
    return "admin" in {str(role).casefold() for role in roles}


@dataclass
class _AgentsCtx:
    """Shared state + helpers the handlers need, injected by the router.

    ``router`` carries the APIRouter the handlers attach to; the rest
    are the closure dependencies the original factory captured. Keeping
    them on the context lets the handlers stay free of module-level
    globals.
    """

    router: Any
    registry: Any
    identity_store: Any = None
    require_auth: bool = False
    jwt_secret: str | None = None
    jwt_issuer: str | None = None
    jwt_audience: str | None = None
    journal: Any = None
    group_registry: Any = None
    runtime: Any = None
    thread_store: Any = None
    allow_local_workspace_access: bool = False


def _build_endpoints(ctx: _AgentsCtx) -> None:
    router = ctx.router
    identity_store = ctx.identity_store
    require_auth = ctx.require_auth
    jwt_secret = ctx.jwt_secret
    jwt_issuer = ctx.jwt_issuer
    jwt_audience = ctx.jwt_audience
    thread_store = ctx.thread_store

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

    def _resolve_identity(request: Any) -> Any:
        """Resolve full Identity (not just actor_id) for endpoints that
        need to check roles. Returns ``None`` when require_auth is off
        (single-user dev mode) or when no identity_store is wired.
        """
        if not require_auth or identity_store is None:
            return None
        auth = request.headers.get("Authorization") or ""
        if not auth.lower().startswith("bearer "):
            return None
        token = auth[7:].strip()
        if jwt_secret and token.count(".") == 2:
            with contextlib.suppress(Exception):
                identity = identity_store.verify_jwt(
                    token,
                    secret=jwt_secret,
                    required_issuer=jwt_issuer,
                    required_audience=jwt_audience,
                )
                if identity is not None:
                    return identity
        with contextlib.suppress(Exception):
            return identity_store.verify_api_key(token)
        return None

    def _require_admin(request: Any) -> None:
        """Gate for high-risk endpoints (writes to global agent registry,
        triggers subprocess discovery, mutates SOUL.md).

        Single-user dev mode (require_auth=False) bypasses this — that's
        consistent with how the rest of the router treats _auth. When
        require_auth=True, the resolved identity must carry the
        ``admin`` role.
        """
        _auth(request)  # AUTH-OK: actor-agnostic — credential check; role check follows
        if not require_auth:
            return
        identity = _resolve_identity(request)
        if not _identity_has_admin_role(identity):
            raise HTTPException(403, "admin role required")

    def _require_task_owner(request: Any, task_id: str) -> str | None:
        """Enforce that the caller owns the thread containing ``task_id``.

        The chain is task_id → ActiveTask.thread_id → Thread.metadata
        ["owner_id"]. Returns actor_id (None in dev mode). Raises 404
        if the task doesn't exist; 403 if the caller doesn't own its
        thread.

        Backward-compat: tasks whose threads have ``owner_id is None``
        (legacy or dev-mode threads) are visible to everyone — matching
        the parallel_agents_router pattern. Once all threads are
        created with owner_id, this branch rarely fires.
        """
        actor = _auth(request)
        if not require_auth:
            return actor
        if not actor:
            raise HTTPException(401, "authentication required")
        from runtime.core.cerebrum.pause_control import get_pause_controller

        ctrl = get_pause_controller()
        thread_id = ctrl.get_task_thread_id(task_id)
        if thread_id is None:
            raise HTTPException(404, f"task not found: {task_id}")
        identity = _resolve_identity(request)
        is_admin = _identity_has_admin_role(identity)
        if thread_store is None:
            if is_admin:
                return actor
            raise HTTPException(404, f"task not found: {task_id}")
        try:
            thread = thread_store.get_state(thread_id)
        except (AttributeError, TypeError):
            thread = None
        if thread is None:
            raise HTTPException(404, f"task not found: {task_id}")
        metadata = thread.get("metadata") or {}
        owner = metadata.get("owner_actor_id") or metadata.get("owner_id")
        if owner is None:
            if not is_admin:
                raise HTTPException(404, f"task not found: {task_id}")
        elif owner != actor:
            raise HTTPException(403, "not the owner of this task's thread")
        return actor

    def _require_thread_owner(request: Any, thread_id: str) -> str | None:
        """Enforce that the caller owns ``thread_id``.

        Used by /api/conversations/{id}/events and similar endpoints
        that take thread_id directly. Same backward-compat as
        _require_task_owner.
        """
        actor = _auth(request)
        if not require_auth:
            return actor
        if not actor:
            raise HTTPException(401, "authentication required")
        identity = _resolve_identity(request)
        is_admin = _identity_has_admin_role(identity)
        if thread_store is None:
            if is_admin:
                return actor
            raise HTTPException(404, f"thread not found: {thread_id}")
        try:
            thread = thread_store.get_state(thread_id)
        except (AttributeError, TypeError):
            thread = None
        if thread is None:
            raise HTTPException(404, f"thread not found: {thread_id}")
        metadata = thread.get("metadata") or {}
        owner = metadata.get("owner_actor_id") or metadata.get("owner_id")
        if owner is None:
            if not is_admin:
                raise HTTPException(404, f"thread not found: {thread_id}")
        elif owner != actor:
            raise HTTPException(403, "not the owner of this thread")
        return actor

    auth = _AuthActions(
        auth=_auth,
        resolve_identity=_resolve_identity,
        require_admin=_require_admin,
        require_task_owner=_require_task_owner,
        require_thread_owner=_require_thread_owner,
    )

    _register_local_partners(router, ctx, auth)
    _register_agents_crud(router, ctx, auth)
    _register_tasks(router, ctx, auth)
    _register_agents_tools(router, ctx, auth)
    _register_system(router, ctx, auth)
    _register_conversations(router, ctx, auth)
    _register_groups(router, ctx, auth)
