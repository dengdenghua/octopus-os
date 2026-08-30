"""
Prompts router · ``/api/prompts/*``.

Endpoints
---------

    GET    /api/prompts                     list registered prompts
    GET    /api/prompts/{name}              read body (optionally ?variant=…)
    PUT    /api/prompts/{name}              upsert body (atomic write)
    POST   /api/prompts/reload              force re-scan from disk

Mutating endpoints (``PUT``, ``POST /reload``) are gated on the
``ui.prompts_hot_reload`` feature flag — when it's OFF, both return
``403 prompts_hot_reload_disabled``.  Reads always work, regardless of
the flag, because the UI's "view current prompt" panel shouldn't break
just because you haven't enabled experimental editing yet.

The router is constructed via a factory that takes a ``PromptRegistry``
instance, mirroring the project convention (``create_*_router``).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from runtime.platform.prompts.registry import PromptRegistry


def _require_flag() -> None:
    """Block mutations when ``ui.prompts_hot_reload`` is OFF."""
    from runtime.platform import feature_flags as _ff

    if not _ff.is_on("ui.prompts_hot_reload"):
        raise HTTPException(
            403,
            detail={
                "error": "prompts_hot_reload_disabled",
                "hint": ("set feature flag 'ui.prompts_hot_reload' to enable live prompt editing"),
            },
        )


def create_prompts_router(
    registry: PromptRegistry,
    *,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> APIRouter:
    """Factory.  Bind a router to a specific ``PromptRegistry`` instance.

    Tests inject an in-tmp-dir registry; production wires the
    application-wide one.
    """

    def _auth_dep(request: Request) -> None:
        from runtime.adapters.web_auth import _resolve_actor

        _resolve_actor(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    def _admin_dep(request: Request) -> None:
        from runtime.safety.auth.principal import require_roles

        require_roles(
            request,
            identity_store,
            require_auth,
            ("admin",),
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    router = APIRouter(dependencies=[Depends(_auth_dep)])

    @router.get("/api/prompts")
    def list_prompts() -> dict[str, Any]:
        return {"prompts": registry.list()}

    @router.get("/api/prompts/{name}")
    def get_prompt(
        name: str,
        variant: str | None = Query(default=None),
    ) -> dict[str, Any]:
        try:
            content = registry.get(name, variant=variant)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {
            "name": name,
            "variant": variant,
            "content": content,
        }

    @router.put(
        "/api/prompts/{name}",
        dependencies=[Depends(_admin_dep)],
    )
    def put_prompt(
        name: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        _require_flag()
        payload = body or {}
        content = payload.get("content")
        if not isinstance(content, str):
            raise HTTPException(400, "content must be a string")
        variant_raw = payload.get("variant")
        variant: str | None
        if variant_raw is None or variant_raw == "":
            variant = None
        elif isinstance(variant_raw, str):
            variant = variant_raw
        else:
            raise HTTPException(400, "variant must be a string or omitted")
        try:
            registry.set(name, content, variant=variant)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {
            "ok": True,
            "name": name,
            "variant": variant,
        }

    @router.post("/api/prompts/reload", dependencies=[Depends(_admin_dep)])
    def reload_prompts() -> dict[str, Any]:
        _require_flag()
        registry.reload()
        return {"ok": True, "prompts": registry.list()}

    return router


__all__ = ["create_prompts_router"]
