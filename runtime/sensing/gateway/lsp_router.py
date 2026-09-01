"""Thin HTTP wrapper around the registered LSP skills.

This exposes definition / references / diagnostics to the frontend without
bypassing the existing skill registry. The heavy lifting still lives in
`runtime.execution.suckers.lsp_skills`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    from fastapi import APIRouter, HTTPException, Request
    from pydantic import BaseModel

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment, misc]
    HTTPException = None  # type: ignore[assignment, misc]
    Request = None  # type: ignore[assignment, misc]
    BaseModel = object  # type: ignore[assignment, misc]

from runtime.sensing._fastapi_guard import require_fastapi

if FASTAPI_AVAILABLE:

    class LspSymbolRequest(BaseModel):
        path: str
        symbol: str
        workspace: str | None = None
        thread_id: str | None = None

    class LspDiagnosticsRequest(BaseModel):
        path: str
        workspace: str | None = None
        thread_id: str | None = None


def create_lsp_router(
    registry: Any,
    *,
    thread_store: Any = None,
    workspace_root: Path | str | None = None,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> Any:
    require_fastapi(__name__)

    router = APIRouter(tags=["lsp"])

    def _call_skill(name: str, **kwargs: Any) -> dict[str, Any]:
        if registry is None or not registry.has(name):
            raise HTTPException(503, f"skill not available: {name}")
        skill = registry.get(name)
        try:
            return skill.handler(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"lsp skill failed: {exc}") from exc

    def _managed_workspace_for(request: Request, body: Any) -> str | None:
        """Return the only LSP sandbox the authenticated caller may use."""
        if not require_auth:
            return body.workspace

        from runtime.safety.auth.principal import resolve_principal
        from runtime.sensing.gateway.thread_workspace import verified_managed_workspace

        principal = resolve_principal(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )
        if principal is None:  # pragma: no cover - require_auth resolves or raises
            raise HTTPException(401, "authentication required")
        if thread_store is None or workspace_root is None:
            raise HTTPException(503, "managed thread workspace unavailable")

        requested_thread_id = str(body.thread_id or "").strip()
        candidates: list[dict[str, Any]] = []
        requested_path: Path | None = None
        if requested_thread_id:
            if not hasattr(thread_store, "get"):
                raise HTTPException(503, "managed thread workspace unavailable")
            thread = thread_store.get(requested_thread_id)
            if isinstance(thread, dict):
                candidates.append(thread)
        else:
            # Compatibility for the existing editor payload, which identifies
            # its thread workspace by path. Only server records already scoped
            # to this exact actor+tenant are considered.
            requested_workspace = str(body.workspace or "").strip()
            if not requested_workspace:
                raise HTTPException(403, "authenticated LSP access requires thread scope")
            try:
                requested_path = Path(requested_workspace).expanduser().resolve(strict=False)
            except (OSError, RuntimeError, ValueError) as exc:
                raise HTTPException(403, "invalid workspace scope") from exc
            if not requested_path.is_absolute() or not hasattr(thread_store, "search"):
                raise HTTPException(403, "invalid workspace scope")
            candidates = list(
                thread_store.search(
                    limit=10_000,
                    metadata={
                        "owner_actor_id": principal.actor_id,
                        "tenant_id": principal.tenant_id,
                    },
                )
            )

        for thread in candidates:
            raw_metadata = thread.get("metadata")
            metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
            if metadata.get("owner_actor_id") != principal.actor_id:
                continue
            if metadata.get("tenant_id") != principal.tenant_id:
                continue
            thread_id = thread.get("thread_id")
            if not isinstance(thread_id, str) or not thread_id:
                continue
            managed = verified_managed_workspace(
                workspace_root,
                thread_id=thread_id,
                metadata=metadata,
            )
            if managed is None:
                continue
            if not requested_thread_id and requested_path != managed:
                continue
            return str(managed)

        if requested_thread_id:
            # Do not reveal whether the requested thread belongs to someone
            # else or merely does not exist.
            raise HTTPException(404, f"thread not found: {requested_thread_id}")
        raise HTTPException(403, "workspace is not an owned managed thread workspace")

    def _authorized_arguments(request: Request, body: Any) -> tuple[str, str | None]:
        sandbox = _managed_workspace_for(request, body)
        if not require_auth:
            return body.path, sandbox
        from runtime.safety.auth.path_guard import check_path

        verdict = check_path(body.path, sandbox_dir=sandbox)
        if not verdict.allow or not verdict.resolved:
            raise HTTPException(403, "LSP path is outside the managed thread workspace")
        return verdict.resolved, sandbox

    @router.post("/api/lsp/definition")
    def api_lsp_definition(request: Request, body: LspSymbolRequest) -> dict[str, Any]:
        path, sandbox = _authorized_arguments(request, body)
        return _call_skill(
            "find_symbol",
            path=path,
            symbol=body.symbol,
            sandbox_dir=sandbox,
        )

    @router.post("/api/lsp/references")
    def api_lsp_references(request: Request, body: LspSymbolRequest) -> dict[str, Any]:
        path, sandbox = _authorized_arguments(request, body)
        return _call_skill(
            "find_refs",
            path=path,
            symbol=body.symbol,
            sandbox_dir=sandbox,
        )

    @router.post("/api/lsp/diagnostics")
    def api_lsp_diagnostics(request: Request, body: LspDiagnosticsRequest) -> dict[str, Any]:
        path, sandbox = _authorized_arguments(request, body)
        return _call_skill(
            "get_diagnostics",
            path=path,
            sandbox_dir=sandbox,
        )

    return router


__all__ = ["create_lsp_router"]
