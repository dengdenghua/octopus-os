"""Authenticated operations API for native multi-platform Reach."""

from __future__ import annotations

import json
import os
from contextlib import suppress
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field


class CollectRequest(BaseModel):
    platform: str = "web"
    queries: list[str] = Field(default_factory=list, max_length=50)
    urls: list[str] = Field(default_factory=list, max_length=100)
    max_results: int = Field(default=10, ge=1, le=50)
    output_format: str = "json"
    use_browser: bool = False


def _collection_dir() -> Path:
    root = Path(os.environ.get("ECHO_HOME") or (Path.home() / ".echo"))
    path = root / "data" / "reach" / "collections"
    if path.is_dir():
        with suppress(OSError):
            path.chmod(0o700)
    return path


def _safe_collection(name: str) -> Path:
    if not name or Path(name).name != name or not name.startswith("collection-"):
        raise HTTPException(400, "invalid collection name")
    path = _collection_dir() / name
    if path.suffix not in {".json", ".md"}:
        raise HTTPException(400, "unsupported collection type")
    return path


def create_reach_router(
    *,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> APIRouter:
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

    router = APIRouter(tags=["reach"], dependencies=[Depends(_auth_dep)])

    @router.get("/api/reach/status")
    def status() -> dict[str, Any]:
        from runtime.platform.reach import diagnose_reach

        result = diagnose_reach()
        result["collection_count"] = len(_list_collections())
        return result

    # Collections are a single global directory with timestamp-only filenames: a
    # collection records the operator's queries plus the fetched page bodies, and
    # nothing in it identifies an owner. ``platform_collect`` is also reachable
    # from the LLM skill surface, where there is no request principal to stamp,
    # so the store cannot be partitioned per actor at write time. Read is
    # therefore held to the same bar as the writes that produce it
    # (``collect``/``delete`` already require operator) instead of being open to
    # every authenticated tenant.
    @router.get("/api/reach/collections", dependencies=[Depends(_operator_dep)])
    def collections() -> dict[str, Any]:
        return {"ok": True, "collections": _list_collections()}

    @router.get("/api/reach/collections/{name}", dependencies=[Depends(_operator_dep)])
    def collection(name: str) -> dict[str, Any]:
        path = _safe_collection(name)
        if not path.is_file():
            raise HTTPException(404, "collection not found")
        if path.suffix == ".json":
            try:
                return {"ok": True, "name": name, "data": json.loads(path.read_text("utf-8"))}
            except (OSError, json.JSONDecodeError) as exc:
                raise HTTPException(500, "collection cannot be read") from exc
        return {"ok": True, "name": name, "content": path.read_text("utf-8")}

    @router.post("/api/reach/collect", dependencies=[Depends(_operator_dep)])
    def collect(body: CollectRequest) -> dict[str, Any]:
        from runtime.platform.reach import platform_collect

        return platform_collect(**body.model_dump())

    @router.delete("/api/reach/cache", dependencies=[Depends(_operator_dep)])
    def clear_cache() -> dict[str, Any]:
        from runtime.platform.reach.cache import reach_cache

        reach_cache.clear()
        return {"ok": True, "cleared": True}

    @router.delete("/api/reach/collections/{name}", dependencies=[Depends(_operator_dep)])
    def delete_collection(name: str) -> dict[str, Any]:
        path = _safe_collection(name)
        if not path.is_file():
            raise HTTPException(404, "collection not found")
        path.unlink()
        return {"ok": True, "deleted": name}

    return router


def _list_collections() -> list[dict[str, Any]]:
    root = _collection_dir()
    if not root.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(
        root.glob("collection-*.*"), key=lambda item: item.stat().st_mtime, reverse=True
    )[:200]:
        if path.suffix not in {".json", ".md"} or not path.is_file():
            continue
        with suppress(OSError):
            path.chmod(0o600)
        stat = path.stat()
        rows.append({"name": path.name, "size": stat.st_size, "modified_at": stat.st_mtime})
    return rows
