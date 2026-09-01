from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from runtime.platform.process.paths import app_paths


def _deployments_root() -> Path:
    return app_paths().data_dir / "deployments"


def _manifest_path() -> Path:
    return _deployments_root() / "manifest.json"


def _load_manifest() -> dict[str, Any]:
    path = _manifest_path()
    if not path.is_file():
        return {"deployments": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {"deployments": []}
    return data if isinstance(data, dict) else {"deployments": []}


def _resolve_file(deployment_id: str, file_path: str) -> Path:
    clean_id = Path(deployment_id).name
    if clean_id != deployment_id or not clean_id:
        raise HTTPException(404, "deployment not found")
    root = (_deployments_root() / clean_id).resolve()
    target = (root / (file_path or "index.html")).resolve()
    if root != target and root not in target.parents:
        raise HTTPException(404, "not found")
    if not target.is_file():
        raise HTTPException(404, "file not found")
    return target


def create_deployments_router(
    *,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> APIRouter:
    router = APIRouter(tags=["deployments"])

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

    @router.get("/api/deployments")
    def list_deployments(request: Request) -> dict[str, Any]:
        _auth(request)
        return _load_manifest()

    @router.get("/api/deployments/{deployment_id}")
    def deployment_index(request: Request, deployment_id: str) -> FileResponse:
        _auth(request)
        return FileResponse(str(_resolve_file(deployment_id, "index.html")))

    @router.get("/api/deployments/{deployment_id}/{file_path:path}")
    def deployment_file(
        request: Request,
        deployment_id: str,
        file_path: str,
    ) -> FileResponse:
        _auth(request)
        return FileResponse(str(_resolve_file(deployment_id, file_path)))

    return router


__all__ = ["create_deployments_router"]
