from __future__ import annotations

from typing import Any

try:
    from fastapi import APIRouter, Depends, HTTPException, Query, Request

    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment, misc]
    Depends = None  # type: ignore[assignment, misc]
    HTTPException = None  # type: ignore[assignment, misc]
    Query = None  # type: ignore[assignment, misc]
    Request = None  # type: ignore[assignment, misc]

from runtime.sensing._fastapi_guard import require_fastapi


def create_skill_market_router(
    *,
    skill_market: Any = None,
    skills_dir: str | None = None,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> Any:
    require_fastapi(__name__)

    if skill_market is None:
        from runtime.platform.plugins.skill_market import SkillMarket

        skill_market = SkillMarket(skills_dir=skills_dir)

    def _auth_dep(request: Request) -> None:
        # Marketplace install/uninstall/publish mutates local skill state.
        # In multi-user/auth-on setups, require a valid actor before any
        # read or write on this control surface.
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

    router = APIRouter(tags=["skill-market"], dependencies=[Depends(_auth_dep)])

    @router.get("/api/skills/market/search")
    def search_skills(
        q: str = Query(default="", min_length=0),
        tag: str | None = Query(default=None),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> dict[str, Any]:
        if not q and not tag:
            return {"query": "", "results": [], "total": 0}

        results = skill_market.search(q or tag or "", limit=limit)
        if tag:
            results = [r for r in results if tag in r.tags]

        return {
            "query": q or tag or "",
            "results": [
                {
                    "name": r.name,
                    "description": r.description,
                    "author": r.author,
                    "tags": r.tags,
                    "version": r.version,
                    "installed": r.installed,
                }
                for r in results
            ],
            "total": len(results),
        }

    @router.get("/api/skills/market/info/{name}")
    def skill_info(name: str) -> dict[str, Any]:
        info = skill_market.info(name)
        if info is None:
            raise HTTPException(404, f"skill '{name}' not found")
        return info

    @router.post(
        "/api/skills/market/install/{name}",
        dependencies=[Depends(_admin_dep)],
    )
    def install_skill(name: str, version: str | None = None) -> dict[str, Any]:
        result = skill_market.install(name, version=version)
        if result.status == "failed":
            raise HTTPException(400, result.message)
        return {
            "name": result.name,
            "version": result.version,
            "path": result.path,
            "status": result.status,
            "message": result.message,
        }

    @router.delete(
        "/api/skills/market/install/{name}",
        dependencies=[Depends(_admin_dep)],
    )
    def uninstall_skill(name: str) -> dict[str, Any]:
        ok = skill_market.uninstall(name)
        if not ok:
            raise HTTPException(404, f"skill '{name}' not installed")
        return {"name": name, "status": "uninstalled"}

    @router.get("/api/skills/market/installed")
    def list_installed() -> dict[str, Any]:
        skills = skill_market.list_installed()
        return {
            "count": len(skills),
            "skills": [
                {
                    "name": s.name,
                    "version": s.version,
                    "author": s.author,
                    "description": s.description,
                    "tags": s.tags,
                    "min_echo_version": s.min_echo_version,
                    "requires": s.requires,
                }
                for s in skills
            ],
        }

    @router.post(
        "/api/skills/market/publish",
        dependencies=[Depends(_admin_dep)],
    )
    def prepare_publish(body: dict[str, Any]) -> dict[str, Any]:
        skill_path = body.get("path", "")
        if not skill_path:
            raise HTTPException(400, "path is required")
        result = skill_market.publish(skill_path)
        if result.get("status") == "error":
            raise HTTPException(400, result.get("message", "publish failed"))
        return result

    @router.get("/api/skills/market/categories")
    def list_categories() -> dict[str, Any]:
        installed = skill_market.list_installed()
        tag_counts: dict[str, int] = {}
        for s in installed:
            for tag in s.tags:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1

        registry = skill_market._fetch_registry()
        if registry:
            for entry in registry:
                for tag in entry.get("tags", []):
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1

        categories = [
            {"name": k, "count": v}
            for k, v in sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)
        ]
        return {
            "categories": categories,
            "total": len(categories),
        }

    return router
