"""Retrieval router · ``/api/retrieve/rank``.

A generic "rank these candidates by meaning" endpoint backed by the configurable
embedder (lexical fallback when none). Lets a connected phone — or any client —
pick the most relevant skill / cached action / doc for a goal instead of brittle
keyword matching.
"""

from __future__ import annotations

from typing import Any

try:
    from fastapi import APIRouter, HTTPException, Request

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment, misc]
    HTTPException = None  # type: ignore[assignment, misc]
    Request = None  # type: ignore[assignment, misc]

from pydantic import BaseModel, ConfigDict, Field

from runtime.sensing._fastapi_guard import require_fastapi


class RankRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    query: str
    candidates: list[str] = Field(default_factory=list)
    top_k: int | None = None


def create_retrieve_router(
    *,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> Any:
    """Build the router. ``app.include_router(create_retrieve_router())``."""
    require_fastapi(__name__)

    router = APIRouter(tags=["retrieve"])

    def _auth(request: Request) -> str | None:
        if require_auth and identity_store is None:
            raise HTTPException(401, "auth required")
        from runtime.sensing.gateway.openai_gateway_router import _resolve_actor

        return _resolve_actor(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    @router.post("/api/retrieve/rank")
    def rank(request: Request, body: RankRequest) -> dict[str, Any]:
        _auth(request)
        from runtime.memory.hemolymph.semantic_rank import rank as _rank

        return _rank(body.query, body.candidates, top_k=body.top_k)

    @router.get("/api/retrieve/backend")
    def backend(request: Request) -> dict[str, Any]:
        _auth(request)
        from runtime.memory.hemolymph import embedding_backend

        return embedding_backend.backend_info()

    return router
