"""Local memory compatibility API.

The frontend settings UI talks to ``/api/memory/*``. This router keeps that
HTTP contract thin and delegates all normalization/persistence to
``runtime.memory.users.user_store`` so memory state has one owner.
"""

from __future__ import annotations

import asyncio
from typing import Any

try:
    from fastapi import APIRouter, Depends, HTTPException, Query, Request

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment,misc]
    Depends = None  # type: ignore[assignment,misc]
    HTTPException = None  # type: ignore[assignment,misc]
    Query = None  # type: ignore[assignment,misc]
    Request = None  # type: ignore[assignment,misc]

from runtime.sensing._fastapi_guard import require_fastapi


def create_memory_router(
    *,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> Any:
    require_fastapi(__name__)

    from runtime.memory import user_store
    from runtime.memory.assets import asset_trace, can_read_asset, fact_to_asset
    from runtime.safety.auth.scope import scope_from_principal

    def _auth_dep(request: Request) -> None:
        from runtime.safety.auth.principal import resolve_principal

        principal = resolve_principal(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )
        request.state.memory_scope = scope_from_principal(principal)
        request.state.memory_actor = principal.actor_id if principal is not None else "local-user"

    def _scope(request: Request) -> Any:
        return getattr(request.state, "memory_scope", None)

    def _roles(request: Request, requested: str) -> list[str]:
        principal = getattr(request.state, "principal", None)
        if require_auth and principal is not None:
            return sorted(principal.roles)
        return [part.strip() for part in requested.split(",") if part.strip()]

    router = APIRouter(tags=["memory"], dependencies=[Depends(_auth_dep)])

    @router.get("/api/memory")
    def api_memory_get(request: Request) -> dict[str, Any]:
        return user_store.read_memory(_scope(request))

    @router.get("/api/memory/search")
    def api_memory_search(
        request: Request, q: str = "", limit: int = Query(20, ge=1, le=100)
    ) -> list[dict[str, Any]]:
        query = " ".join(q.split()).casefold()
        if not query:
            return []
        terms = [term for term in query.split() if term]
        results: list[dict[str, Any]] = []
        for fact in user_store.read_memory(_scope(request)).get("facts", []):
            if not isinstance(fact, dict):
                continue
            content = str(fact.get("content") or "").casefold()
            category = str(fact.get("category") or "").casefold()
            haystack = f"{content} {category}"
            if query not in haystack and not all(term in haystack for term in terms):
                continue
            relevance = 1.0 if query and content.startswith(query) else 0.75
            results.append({**fact, "relevance": relevance})
        results.sort(key=lambda item: item.get("relevance", 0), reverse=True)
        return results[:limit]

    @router.get("/api/memory/assets")
    def api_memory_assets(
        request: Request,
        q: str = "",
        asset_type: str = "",
        layer: str = "",
        status: str = "active",
        visibility: str = "",
        team_id: str = "",
        agent_id: str = "",
        roles: str = "",
        limit: int = Query(50, ge=1, le=200),
    ) -> dict[str, Any]:
        """List legacy and new memories through one governed asset contract."""
        query = " ".join(q.split()).casefold()
        role_list = _roles(request, roles)
        assets: list[dict[str, Any]] = []
        for fact in user_store.read_memory(_scope(request)).get("facts", []):
            if not isinstance(fact, dict):
                continue
            asset = fact_to_asset(fact)
            if not can_read_asset(
                asset,
                actor=getattr(request.state, "memory_actor", "local-user"),
                roles=role_list,
                agent_id=agent_id,
                team_id=team_id,
            ):
                continue
            if asset_type and asset.asset_type != asset_type:
                continue
            if layer and asset.layer != layer.upper():
                continue
            if status and asset.status != status:
                continue
            if visibility and asset.visibility != visibility:
                continue
            haystack = f"{asset.title} {asset.content} {' '.join(asset.tags)}".casefold()
            if (
                query
                and query not in haystack
                and not all(term in haystack for term in query.split())
            ):
                continue
            assets.append(asset.to_dict())
        assets.sort(
            key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""),
            reverse=True,
        )
        return {"items": assets[:limit], "count": min(len(assets), limit)}

    @router.get("/api/memory/assets/{asset_id}/trace")
    def api_memory_asset_trace(
        asset_id: str,
        request: Request,
        team_id: str = "",
        agent_id: str = "",
        roles: str = "",
    ) -> dict[str, Any]:
        role_list = _roles(request, roles)
        for fact in user_store.read_memory(_scope(request)).get("facts", []):
            if not isinstance(fact, dict) or str(fact.get("id")) != asset_id:
                continue
            asset = fact_to_asset(fact)
            if not can_read_asset(
                asset,
                actor=getattr(request.state, "memory_actor", "local-user"),
                roles=role_list,
                agent_id=agent_id,
                team_id=team_id,
            ):
                raise HTTPException(403, "memory asset is not visible to this caller")
            return asset_trace(asset)
        raise HTTPException(404, "memory asset not found")

    @router.post("/api/memory/reload")
    def api_memory_reload(request: Request) -> dict[str, Any]:
        return user_store.read_memory(_scope(request))

    @router.delete("/api/memory")
    def api_memory_clear(request: Request) -> dict[str, Any]:
        return user_store.write_memory(user_store.empty_memory(), scope=_scope(request))

    @router.post("/api/memory/facts")
    async def api_memory_add_fact(request: Request) -> dict[str, Any]:
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "invalid memory fact")
        content = str(body.get("content") or body.get("fact") or "").strip()
        if not content:
            raise HTTPException(400, "content is required")
        try:
            confidence = float(body.get("confidence", 0.8))
        except Exception:
            confidence = 0.8
        await asyncio.to_thread(
            user_store.add_fact,
            content,
            category=str(body.get("category") or "context"),
            confidence=confidence,
            source=str(body.get("source") or "manual"),
            scope=str(body.get("scope") or "global"),
            agent_id=str(body.get("agent_id") or "") or None,
            project=str(body.get("project") or "") or None,
            owner=str(getattr(request.state, "memory_actor", "local-user")),
            visibility=str(body.get("visibility") or "private"),
            team_id=str(body.get("team_id") or "") or None,
            allowed_users=body.get("allowed_users"),
            allowed_roles=body.get("allowed_roles"),
            allowed_agents=body.get("allowed_agents"),
            provenance=body.get("provenance"),
            title=str(body.get("title") or "") or None,
            tags=body.get("tags"),
            tenant_scope=_scope(request),
        )
        return await asyncio.to_thread(user_store.read_memory, _scope(request))

    @router.patch("/api/memory/facts/{fact_id}")
    async def api_memory_update_fact(fact_id: str, request: Request) -> dict[str, Any]:
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "invalid memory fact")
        memory = await asyncio.to_thread(user_store.read_memory, _scope(request))
        found = False
        for fact in memory.get("facts", []):
            if str(fact.get("id")) != fact_id:
                continue
            asset = fact_to_asset(fact)
            actor = str(getattr(request.state, "memory_actor", "local-user"))
            if asset.owner != actor:
                raise HTTPException(403, "only the memory asset owner may update it")
            if "content" in body:
                content = str(body.get("content") or "").strip()
                if not content:
                    raise HTTPException(400, "content is required")
                fact["content"] = content
            for key in ("category", "source", "scope", "agent_id", "project"):
                if key in body:
                    fact[key] = str(body.get(key) or "")
            for key in (
                "title",
                "asset_type",
                "layer",
                "visibility",
                "status",
                "team_id",
            ):
                if key in body:
                    fact[key] = str(body.get(key) or "")
            for key in ("allowed_users", "allowed_roles", "allowed_agents", "tags"):
                if key in body:
                    fact[key] = body.get(key)
            if "provenance" in body:
                fact["provenance"] = body.get("provenance")
            if "confidence" in body:
                try:
                    confidence = float(body.get("confidence") or 0)
                except Exception:
                    confidence = float(fact.get("confidence", 0.8))
                fact["confidence"] = max(0.0, min(1.0, confidence))
            fact["asset_version"] = int(fact.get("asset_version") or 1) + 1
            fact["updatedAt"] = user_store.now_iso()
            found = True
            break
        if not found:
            raise HTTPException(404, "memory fact not found")
        return await asyncio.to_thread(user_store.write_memory, memory, scope=_scope(request))

    @router.delete("/api/memory/facts/{fact_id}")
    def api_memory_delete_fact(fact_id: str, request: Request) -> dict[str, Any]:
        memory = user_store.read_memory(_scope(request))
        facts = list(memory.get("facts", []))
        actor = str(getattr(request.state, "memory_actor", "local-user"))
        for fact in facts:
            if str(fact.get("id")) != fact_id:
                continue
            if fact_to_asset(fact).owner != actor:
                raise HTTPException(403, "only the memory asset owner may delete it")
            break
        next_facts = [fact for fact in facts if str(fact.get("id")) != fact_id]
        if len(next_facts) == len(facts):
            raise HTTPException(404, "memory fact not found")
        memory["facts"] = next_facts
        return user_store.write_memory(memory, scope=_scope(request))

    @router.get("/api/memory/config")
    def api_memory_config(request: Request) -> dict[str, Any]:
        return user_store.read_config(_scope(request))

    @router.put("/api/memory/config")
    async def api_memory_update_config(request: Request) -> dict[str, Any]:
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "invalid memory config")
        return await asyncio.to_thread(user_store.write_config, body, scope=_scope(request))

    @router.get("/api/memory/export")
    def api_memory_export(request: Request) -> dict[str, Any]:
        return user_store.read_memory(_scope(request))

    @router.post("/api/memory/import")
    async def api_memory_import(request: Request) -> dict[str, Any]:
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "invalid memory payload")
        return await asyncio.to_thread(user_store.write_memory, body, scope=_scope(request))

    return router


__all__ = ["create_memory_router"]
