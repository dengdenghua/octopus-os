"""Legacy control-plane auth gate helpers for the Web UI app.

Extracted from ``app.py`` during the god-file reduction. These are
module-level helpers + the middleware that gates older HTTP
control-plane routers behind the cocoloop identity store.
"""

from __future__ import annotations

from typing import Any

try:
    from fastapi import HTTPException
    from fastapi.responses import JSONResponse
except ImportError:  # pragma: no cover
    HTTPException = None  # type: ignore[assignment]
    JSONResponse = None  # type: ignore[assignment]

_LEGACY_CONTROL_PLANE_PREFIXES = (
    "/api/account",
    "/api/agent-world",
    "/api/agent-market",
    "/api/agent-modes",
    "/api/android",
    "/api/ambient-suggestions",
    "/api/apps",
    "/api/computer",
    "/api/config",
    "/api/dag",
    "/api/evolution",
    "/api/feature-flags",
    "/api/gene-locks",
    "/api/intelligence",
    "/api/lsp",
    "/api/mcp",
    "/api/memory",
    "/api/meta-skill",
    "/api/meta-skills",
    "/api/path-denylist",
    "/api/permissions",
    "/api/plugin-hub",
    "/api/plugins",
    "/api/prompts",
    "/api/reflex",
    "/api/remote-backends",
    "/api/safety",
    "/api/skill-market",
    "/api/skills/market",
    "/api/smart-routing",
    "/api/tasks",
    "/api/tentacle",
    "/api/team/role-models",
)


def _path_matches_prefix(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def _is_public_plugin_asset_request(method: str, path: str) -> bool:
    if method.upper() not in {"GET", "HEAD"}:
        return False
    parts = path.split("/")
    return (
        len(parts) >= 6
        and parts[0] == ""
        and parts[1] == "api"
        and parts[2] == "plugins"
        and bool(parts[3])
        and parts[4] == "assets"
        and bool(parts[5])
    )


def _is_public_plugin_landing_request(method: str, path: str) -> bool:
    """Allow only inert auth-host explanation pages from bundled plugins.

    These plugins deliberately mount no stateful API, proxy, or WebSocket
    routes on an authenticated (therefore potentially multi-user) host. A
    browser iframe cannot attach a Bearer header, so the exact static pages
    are public; no wildcard or child path is accepted.
    """
    return method.upper() in {"GET", "HEAD"} and path in {
        "/api/plugins/paper-trading/page",
        "/api/plugins/paper-trading/watch",
        "/api/plugins/mx2025_viewer/page",
    }


def _is_trusted_local_paper_trading_request(method: str, path: str, app: Any) -> bool:
    """Match the explicit paper-trading localhost exception, and nothing else.

    The plugin publishes the app-state bit only after all three gates pass:
    host auth is on, the serve process is local + loopback, and the operator
    explicitly enabled ``trusted_single_user_local_proxy``.  Segment-aware
    matching prevents lookalikes such as ``check-in-evil`` or ``page/child``
    from inheriting the exception.
    """

    state = getattr(app, "state", None)
    if not bool(
        state is not None and getattr(state, "paper_trading_trusted_single_user_local_proxy", False)
    ):
        return False

    normalized_method = method.upper()
    root = "/api/plugins/paper-trading"
    if normalized_method in {"GET", "HEAD"} and path in {
        f"{root}/page",
        f"{root}/watch",
        f"{root}/watch.js",
    }:
        return True
    if normalized_method in {"GET", "HEAD", "POST"} and _path_matches_prefix(
        path,
        f"{root}/check-in",
    ):
        return True
    return normalized_method in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"} and (
        _path_matches_prefix(path, f"{root}/origin")
    )


def _is_oauth_callback_request(method: str, path: str) -> bool:
    # The MCP OAuth callback is reached by the *provider* redirecting the
    # user's browser, which carries no Authorization header — gating it
    # here would deadlock the flow in auth-on deployments. Its credential
    # is the single-use, TTL-bounded ``state`` checked in the handler.
    return method.upper() in {"GET", "HEAD"} and path == "/api/mcp/oauth/callback"


def _install_legacy_control_plane_auth(
    app: Any,
    *,
    identity_store: Any,
    require_auth: bool,
    jwt_secret: str | None,
    jwt_issuer: str | None,
    jwt_audience: str | None,
) -> None:
    """Add one auth gate for older HTTP control-plane routers."""
    if not require_auth:
        return

    @app.middleware("http")
    async def _legacy_control_plane_auth(request: Any, call_next: Any) -> Any:
        if request.method == "OPTIONS":
            return await call_next(request)
        path = str(getattr(getattr(request, "url", None), "path", "") or "")
        if _is_public_plugin_asset_request(request.method, path):
            return await call_next(request)
        if _is_trusted_local_paper_trading_request(request.method, path, request.app):
            return await call_next(request)
        if _is_public_plugin_landing_request(request.method, path):
            return await call_next(request)
        if _is_oauth_callback_request(request.method, path):
            return await call_next(request)
        if not any(_path_matches_prefix(path, prefix) for prefix in _LEGACY_CONTROL_PLANE_PREFIXES):
            return await call_next(request)
        if identity_store is None:
            return JSONResponse(
                {"detail": "identity store required for control-plane auth"},
                status_code=401,
            )
        try:
            from runtime.adapters.web_auth import _resolve_actor

            actor = _resolve_actor(
                request,
                identity_store,
                True,
                jwt_secret=jwt_secret,
                jwt_issuer=jwt_issuer,
                jwt_audience=jwt_audience,
            )
            if actor:
                request.state.actor_id = actor
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        return await call_next(request)
