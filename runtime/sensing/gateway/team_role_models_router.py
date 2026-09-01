"""Team role-model settings router · ``/api/team/role-models``.

Lets the work-mode team settings UI read + set the per-role model tier (cheap vs
primary) — making the cost-saving division of labour configurable instead of
hard-coded. GET returns each role with its built-in default + any override; PUT
persists the overrides.
"""

from __future__ import annotations

from typing import Any

try:
    from fastapi import APIRouter, Depends, Request
    from pydantic import BaseModel

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment, misc]
    Depends = None  # type: ignore[assignment, misc]
    Request = None  # type: ignore[assignment, misc]
    BaseModel = object  # type: ignore[assignment, misc]

from runtime.sensing._fastapi_guard import require_fastapi


class RoleModelsBody(BaseModel):
    overrides: dict[str, str] = {}


def create_team_role_models_router(
    *,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> Any:
    """Build + return the router."""
    require_fastapi(__name__)

    def _auth_dep(request: Request) -> None:
        # Role-model overrides steer team execution cost/tier choices.
        # Keep single-user dev mode open, but require an authenticated
        # actor when this control surface is exposed in shared deploys.
        from runtime.adapters.web_auth import _resolve_actor

        _resolve_actor(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    def _operator_dep(request: Request) -> None:
        from runtime.safety.auth.principal import require_roles

        require_roles(
            request,
            identity_store,
            require_auth,
            ("admin", "operator"),
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    router = APIRouter(
        tags=["team-role-models"],
        dependencies=[Depends(_auth_dep)],
    )

    @router.get("/api/team/role-models")
    def api_get_role_models() -> dict[str, Any]:
        from runtime.safety.organization.team_role_models import load_overrides, role_defaults

        defaults = role_defaults()
        overrides = load_overrides()
        return {
            "roles": [
                {
                    "role": role,
                    "default": default,
                    "tier": overrides.get(role, "default"),
                }
                for role, default in sorted(defaults.items())
            ],
            "tiers": ["default", "cheap", "primary"],
        }

    @router.put(
        "/api/team/role-models",
        dependencies=[Depends(_operator_dep)],
    )
    def api_put_role_models(body: RoleModelsBody) -> dict[str, Any]:
        from runtime.safety.organization.team_role_models import save_overrides

        return {"ok": True, "overrides": save_overrides(body.overrides or {})}

    return router
