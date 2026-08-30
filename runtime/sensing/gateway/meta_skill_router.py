"""FastAPI router for the 能力包 / Meta-Skill catalog.

Exposes the on-disk ``meta_skills/`` YAML catalog over HTTP so the
frontend can list, inspect, and render the workflow diagrams without
importing backend modules directly.

Endpoints
---------
- ``GET  /api/meta-skills`` — list all shipped 能力包 (id + summary).
- ``GET  /api/meta-skills/{name}`` — full MetaSkill payload.
- ``GET  /api/meta-skills/{name}/mermaid`` — pre-rendered Mermaid
  ``flowchart`` string (LR by default; pass ``?direction=TD``).
- ``POST /api/meta-skills/match`` — find the best 能力包 for a user
  query (body: ``{"query": "..."}`). Returns the matched name and
  the full MetaSkill, or 404 if nothing matches.
"""

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


def create_meta_skill_router(
    *,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> Any:
    """Build a FastAPI router over the MetaSkill catalog."""
    require_fastapi(__name__)

    # Imported lazily so this module is importable in tests without
    # pulling the full runtime into memory.
    from runtime.memory.skills_lib.meta_skill import (  # noqa: PLC0415
        list_meta_skills,
        load_meta_skill,
        match_meta_skill,
        meta_skill_to_mermaid,
    )

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

    router = APIRouter(tags=["meta-skill"], dependencies=[Depends(_auth_dep)])

    @router.get("/api/meta-skills")
    def list_packs() -> dict[str, Any]:
        """Light-weight summary of every shipped 能力包."""
        entries = list_meta_skills()
        return {
            "count": len(entries),
            "packs": entries,
        }

    @router.get("/api/meta-skills/{name}")
    def get_pack(name: str) -> dict[str, Any]:
        meta = load_meta_skill(name)
        if meta is None:
            raise HTTPException(status_code=404, detail=f"meta_skill {name!r} not found")
        # Round-trip through the dataclass so we don't leak internals.
        return {
            "name": meta.name,
            "kind": meta.kind,
            "display_name": meta.kind,  # frontend uses display_name_for_kind()
            "description": meta.description,
            "when_to_use": meta.when_to_use,
            "affinity": list(meta.affinity),
            "budget": {
                "tokens": meta.budget_tokens,
                "usd": meta.budget_usd,
                "latency_ms": meta.budget_latency_ms,
            },
            "version": meta.version,
            "author": meta.author,
            "learned_at": meta.learned_at,
            "steps": [
                {
                    "node_id": s.node_id,
                    "skill": s.skill_ref,
                    "args": dict(s.args_template),
                    "depends_on": list(s.depends_on),
                    "timeout_ms": s.timeout_ms,
                }
                for s in meta.steps
            ],
            "edges": [{"from": e.from_node, "to": e.to_node, "kind": e.kind} for e in meta.edges],
        }

    @router.get("/api/meta-skills/{name}/mermaid")
    def get_mermaid(
        name: str,
        direction: str = Query(default="LR", pattern="^(LR|TD|RL|BT)$"),
        include_budget: bool = Query(default=True),
    ) -> dict[str, Any]:
        meta = load_meta_skill(name)
        if meta is None:
            raise HTTPException(status_code=404, detail=f"meta_skill {name!r} not found")
        try:
            mm = meta_skill_to_mermaid(
                meta,
                direction=direction,
                include_budget=include_budget,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"name": name, "direction": direction, "mermaid": mm}

    @router.post("/api/meta-skills/match")
    def match_pack(body: dict[str, Any] | None = None) -> dict[str, Any]:
        if not isinstance(body, dict) or "query" not in body:
            raise HTTPException(
                status_code=400,
                detail="body must be JSON with a 'query' field",
            )
        query = str(body["query"]).strip()
        if not query:
            raise HTTPException(status_code=400, detail="'query' must be non-empty")
        meta = match_meta_skill(query)
        if meta is None:
            raise HTTPException(
                status_code=404,
                detail=f"no 能力包 matches query {query!r}",
            )
        return {
            "query": query,
            "matched": meta.name,
            "description": meta.description,
            "affinity": list(meta.affinity),
        }

    return router
