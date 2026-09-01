"""Account / auth provider routers for ``create_app``.

Extracted from ``app.py`` during the god-file reduction (§2.6 of the
navigation map). Mounts the oct and local-auth account routers and
attaches the account-backed fallback model dispatcher.
"""

from __future__ import annotations

import logging
from typing import Any

from ._app_context import AppContext
from ._app_fallback_routers import _attach_oct_fallback_router


def _restore_oct_identities(identity_store: Any, link_store: Any) -> int:
    """Rehydrate locally linked Oct actors after a process restart.

    The signed session JWT can outlive the in-memory ``IdentityStore``.  The
    link store is durable and is written only after a successful Oct login, so
    it is the local authority for rebuilding the matching user identity.
    """
    if identity_store is None or not hasattr(identity_store, "add"):
        return 0
    restored = 0
    try:
        actor_ids = link_store.all_actor_ids()
    except (AttributeError, OSError, TypeError, ValueError):
        return 0
    from runtime.safety.auth.identity import Identity

    for actor_id in actor_ids:
        if not isinstance(actor_id, str) or not actor_id:
            continue
        try:
            if hasattr(identity_store, "get") and identity_store.get(actor_id) is not None:
                continue
            link = link_store.get(actor_id)
            if link is None or bool(getattr(link, "token_invalid", False)):
                continue
            identity_store.add(
                Identity(
                    actor_id=actor_id,
                    roles=("user", "oct"),
                    metadata={
                        "provider": "oct",
                        "email": getattr(link, "email", None),
                        "oct_user_id": str(getattr(link, "oct_user_id", "") or ""),
                        "restored_from_link": True,
                    },
                )
            )
            restored += 1
        except (AttributeError, TypeError, ValueError):
            continue
    return restored


def mount_auth_routers(
    ctx: AppContext,
    *,
    oct_config: Any,
    oct_link_store: Any,
) -> None:
    """Mount the oct and local-auth account routers."""
    app = ctx.app
    stack = ctx.stack

    if oct_config is not None and getattr(oct_config, "enabled", False):
        # oct 账号网关（echo 自己的，echo.aurest.ai）。
        # agent 内部 LLM 调度接 oct 计费 = actor 感知 fallback(登录走网关计费、guest 走自配,保 P0)。
        from runtime.adapters.integrations.oct import (
            OctLinkStore,
            create_oct_routers,
        )

        effective_oct_link_store = oct_link_store or OctLinkStore()
        restored = _restore_oct_identities(ctx.identity_store, effective_oct_link_store)
        if restored:
            logging.getLogger(__name__).info(
                "restored %d Oct identity record(s) from durable account links",
                restored,
            )
        _attach_oct_fallback_router(
            stack=stack,
            oct_config=oct_config,
            link_store=effective_oct_link_store,
        )
        oct_auth_router, oct_account_router, oct_proxy_router = create_oct_routers(
            config=oct_config,
            link_store=effective_oct_link_store,
            identity_store=ctx.identity_store,
            require_auth=ctx.require_auth,
            jwt_secret=ctx.jwt_secret,
        )
        app.include_router(oct_auth_router)
        app.include_router(oct_account_router)
        app.include_router(oct_proxy_router)

    if ctx.local_auth_runtime_config is not None and getattr(
        ctx.local_auth_runtime_config,
        "enabled",
        False,
    ):
        from runtime.adapters.integrations.local_auth import create_local_auth_router

        app.include_router(
            create_local_auth_router(
                config=ctx.local_auth_runtime_config,
                identity_store=ctx.identity_store,
            )
        )
