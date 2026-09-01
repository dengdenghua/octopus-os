"""Create the FastAPI app, AppState, and auth config for ``create_app``.

Extracted from ``app.py`` during the god-file reduction (§2.1 of the
navigation map). Builds the ``FastAPI`` instance, the shared
``AppState``, resolves the cocoloop/oct/local jwt config, and
installs the legacy control-plane auth middleware.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI
from starlette.datastructures import MutableHeaders

from runtime.platform.ui.compression import GzipStaticMiddleware
from runtime.platform.ui.state import AppState

from ._app_auth import _install_legacy_control_plane_auth
from ._app_context import AppContext


class _SecurityHeadersMiddleware:
    """Small, policy-safe browser hardening applied to every HTTP response.

    The app still has legacy inline UI and same-origin plugin frames, so a
    broad script/style CSP would be a breaking policy change. ``frame-ancestors``
    alone closes cross-origin clickjacking without affecting those resources;
    explicit plugin headers remain authoritative through ``setdefault``.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        async def _send(message: Any) -> None:
            if message.get("type") == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
                headers.setdefault("X-Frame-Options", "SAMEORIGIN")
                headers.setdefault("Content-Security-Policy", "frame-ancestors 'self'")
                if str(scope.get("scheme") or "").lower() == "https":
                    headers.setdefault(
                        "Strict-Transport-Security",
                        "max-age=31536000; includeSubDomains",
                    )
            await send(message)

        await self.app(scope, receive, _send)


def setup_app(
    *,
    journal_path: Any,
    journal: Any,
    registry: Any,
    stack: Any,
    cocoloop_identity_store: Any,
    cocoloop_require_auth: bool,
    allow_local_workspace_access: bool,
    oct_config: Any,
    oct_jwt_secret: str | None,
    local_auth_config: Any,
    kernel: Any = None,
) -> AppContext:
    """Build the app shell + auth config; return the shared context."""
    from runtime.platform.process.paths import app_paths, project_root, resources_root

    _paths = app_paths()
    _project_root_path = project_root()
    _resources_root_path = resources_root()
    trace_store_path = _paths.agent_trace_path.resolve()
    state = AppState(
        journal_path=journal_path,
        journal=journal,
        registry=registry,
        trace_store_path=trace_store_path,
    )
    from runtime import __version__

    app = FastAPI(title="echo-agent", version=__version__)
    app.state.echo_state = state
    # Plugins are process-wide today.  Expose the host auth posture before
    # PluginHub loads so plugins with singleton account/state cannot
    # accidentally mount multi-user APIs or unauthenticated WebSockets.
    app.state.echo_require_auth = bool(cocoloop_require_auth)
    # ``allow_local_workspace_access`` is true only for the explicit local
    # deployment contract on a loopback listener (computed by ``cli_serve``).
    # Publish that already-validated posture before PluginHub loads so a
    # plugin can require both conditions instead of inferring "single user"
    # merely from an auth toggle or a manifest flag.
    app.state.echo_allow_local_workspace_access = bool(allow_local_workspace_access)

    # Gzip the static Vite UI bundle (~18 MB raw) and JSON API responses while
    # leaving SSE / streaming endpoints untouched. See GzipStaticMiddleware.
    app.add_middleware(GzipStaticMiddleware)

    # Audit P-06: HTTP backstops — a bounded request timeout and a bounded
    # concurrency cap so a hung endpoint or a burst of requests cannot hold
    # threads forever or exhaust the worker pool. Streaming/SSE/websocket and
    # the OpenAI-compat /v1 surface are skipped (the model deadlines govern
    # those). Env overrides: ECHO_HTTP_REQUEST_TIMEOUT_S (0 disables),
    # ECHO_HTTP_MAX_CONCURRENCY (0 disables).
    from runtime.platform.ui.request_limits import (
        DEFAULT_MAX_CONCURRENCY,
        DEFAULT_TIMEOUT_S,
        ConcurrencyCapMiddleware,
        RequestTimeoutMiddleware,
        _env_float,
        _env_int,
    )

    app.add_middleware(
        RequestTimeoutMiddleware,
        timeout_s=_env_float("ECHO_HTTP_REQUEST_TIMEOUT_S", DEFAULT_TIMEOUT_S),
    )
    app.add_middleware(
        ConcurrencyCapMiddleware,
        max_concurrency=_env_int("ECHO_HTTP_MAX_CONCURRENCY", DEFAULT_MAX_CONCURRENCY),
    )

    if oct_jwt_secret is None and oct_config is not None:
        oct_jwt_secret = getattr(oct_config, "jwt_secret", None)
    oct_enabled = bool(oct_config is not None and getattr(oct_config, "enabled", False))

    auth_enabled = oct_enabled or bool(
        local_auth_config is not None and getattr(local_auth_config, "enabled", False)
    )
    # oct 网关优先，其次 local_auth。
    cocoloop_jwt_secret = (oct_jwt_secret if oct_enabled else None) or (
        getattr(local_auth_config, "jwt_secret", None) if local_auth_config else None
    )
    cocoloop_jwt_issuer = (
        getattr(oct_config, "jwt_issuer", None)
        if (oct_enabled and oct_jwt_secret)
        else getattr(local_auth_config, "jwt_issuer", None)
        if local_auth_config
        else None
    )
    cocoloop_jwt_audience = (
        getattr(oct_config, "jwt_audience", None)
        if (oct_enabled and oct_jwt_secret)
        else getattr(local_auth_config, "jwt_audience", None)
        if local_auth_config
        else None
    )
    # Audit H1: never silently run auth with a well-known dev/test secret —
    # anyone who knows it can forge tokens. Warn loudly so local configs that
    # shipped with the placeholder value are caught before exposure.
    _known_test_jwt_secrets = ("test-secret-key-for-local-development-only-1234567890",)
    if cocoloop_jwt_secret and cocoloop_jwt_secret in _known_test_jwt_secrets:
        logging.getLogger(__name__).warning(
            "jwt_secret is the well-known development/test secret; anyone who "
            "knows it can forge auth tokens. Set a strong random value before "
            "exposing this instance to a network."
        )

    local_auth_runtime_config = local_auth_config
    if (
        local_auth_config is not None
        and getattr(local_auth_config, "enabled", False)
        and cocoloop_jwt_secret
        and getattr(local_auth_config, "jwt_secret", None) != cocoloop_jwt_secret
    ):
        try:
            local_auth_runtime_config = local_auth_config.model_copy(
                update={
                    "jwt_secret": cocoloop_jwt_secret,
                    "jwt_issuer": cocoloop_jwt_issuer,
                    "jwt_audience": cocoloop_jwt_audience,
                }
            )
        except AttributeError:
            local_auth_runtime_config = local_auth_config
    if cocoloop_identity_store is None and auth_enabled:
        from runtime.safety.auth.identity import IdentityStore

        cocoloop_identity_store = IdentityStore()
    if (
        local_auth_config is not None
        and getattr(local_auth_config, "enabled", False)
        and getattr(local_auth_config, "allow_any_username", False)
    ):
        logging.getLogger(__name__).warning(
            "local auth allow_any_username=true accepts arbitrary usernames; "
            "use only for trusted local development"
        )

    _install_legacy_control_plane_auth(
        app,
        identity_store=cocoloop_identity_store,
        require_auth=cocoloop_require_auth,
        jwt_secret=cocoloop_jwt_secret,
        jwt_issuer=cocoloop_jwt_issuer,
        jwt_audience=cocoloop_jwt_audience,
    )
    # Install last so these headers also wrap early 401/403 responses emitted
    # by the legacy control-plane auth middleware.
    app.add_middleware(_SecurityHeadersMiddleware)

    return AppContext(
        app=app,
        state=state,
        stack=stack,
        kernel=kernel,
        paths=_paths,
        project_root=_project_root_path,
        resources_root=_resources_root_path,
        trace_store_path=trace_store_path,
        identity_store=cocoloop_identity_store,
        require_auth=cocoloop_require_auth,
        allow_local_workspace_access=allow_local_workspace_access,
        jwt_secret=cocoloop_jwt_secret,
        jwt_issuer=cocoloop_jwt_issuer,
        jwt_audience=cocoloop_jwt_audience,
        local_auth_runtime_config=local_auth_runtime_config,
    )
