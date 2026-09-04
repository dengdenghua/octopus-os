"""Read-only native storage routes (no OpenMediaVault required).

Exposed under ``/api/appliance/storage`` with the same payload shapes as
``/api/appliance/omv`` so the appliance UI can fall back to the native plane
whenever a host has no OMV installed — which is the case for the p3-provision
installer route, which never deployed OMV.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from appliance import native_storage
from appliance.security import ApplianceAuthenticator, resolve_authenticator


def register_native_storage_routes(router: APIRouter) -> None:
    """Attach native read-only routes to an already-authenticated router."""

    @router.get("/status")
    async def status() -> dict[str, Any]:
        return native_storage.status()

    @router.get("/health")
    async def health() -> dict[str, Any]:
        try:
            return native_storage.storage_health()
        except OSError as exc:  # pragma: no cover - defensive
            raise HTTPException(
                status_code=503, detail="native storage read failed"
            ) from exc

    @router.get("/filesystems")
    async def filesystems() -> dict[str, Any]:
        return {"filesystems": native_storage.filesystems(), "readOnly": True}

    @router.get("/topology")
    async def topology() -> dict[str, Any]:
        return native_storage.storage_topology()

    @router.get("/smart/devices")
    async def smart_devices() -> dict[str, Any]:
        return {"devices": native_storage.smart_devices(), "readOnly": True}

    @router.get("/sharing")
    async def sharing() -> dict[str, Any]:
        return {**native_storage.sharing_overview(), "readOnly": True}

    @router.get("/smart")
    async def smart(
        devicefile: str = Query(min_length=5, max_length=256),
    ) -> dict[str, Any]:
        try:
            validated = native_storage.validated_devicefile(devicefile)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"smart": native_storage.smart_report(validated), "readOnly": True}


def create_native_storage_router(
    *,
    jwt_secret: str | None = None,
    authenticator: ApplianceAuthenticator | None = None,
) -> APIRouter:
    """Standalone router with the same operator guard as the OMV router."""
    auth = resolve_authenticator(jwt_secret=jwt_secret, authenticator=authenticator)
    require_operator = auth.operator_dependency()
    router = APIRouter(
        prefix="/api/appliance/storage",
        tags=["appliance", "storage"],
        dependencies=[Depends(require_operator)],
    )
    register_native_storage_routes(router)
    return router


def create_omv_alias_router(
    *,
    jwt_secret: str | None = None,
    authenticator: ApplianceAuthenticator | None = None,
) -> APIRouter:
    """Serve the legacy ``/api/appliance/omv`` read paths from the native plane.

    Panels keep working on hosts without OpenMediaVault. Mutation endpoints
    (plan/apply) respond 501 until the native write plane lands — they used to
    mutate an OMV backend that no longer exists here.
    """
    auth = resolve_authenticator(jwt_secret=jwt_secret, authenticator=authenticator)
    require_operator = auth.operator_dependency()
    router = APIRouter(
        prefix="/api/appliance/omv",
        tags=["appliance", "storage"],
        dependencies=[Depends(require_operator)],
    )
    register_native_storage_routes(router)

    removed = "OMV 存储面已从本机移除；共享/配额/账号变更的原生写路径尚未开通"

    def _removed() -> dict[str, Any]:
        raise HTTPException(status_code=501, detail=removed)

    for path in (
        "/accounts/groups/plan",
        "/accounts/groups/apply",
        "/accounts/users/plan",
        "/accounts/users/apply",
        "/accounts/users/password/plan",
        "/accounts/users/password/apply",
        "/sharing/folders/plan",
        "/sharing/folders/apply",
        "/sharing/privileges/plan",
        "/sharing/privileges/apply",
        "/sharing/smb/plan",
        "/sharing/smb/apply",
        "/sharing/nfs/plan",
        "/sharing/nfs/apply",
        "/quota/plan",
        "/quota/apply",
    ):
        router.add_api_route(
            path,
            _removed,
            methods=["POST"],
            name=f"removed{path.replace('/', '_')}",
        )

    return router


__all__ = ["create_native_storage_router", "create_omv_alias_router", "register_native_storage_routes"]
