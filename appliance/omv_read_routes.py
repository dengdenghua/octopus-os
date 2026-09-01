"""Read-only inventory and health routes for the Echo OMV API."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from appliance.omv_client import (
    GROUP_CONTROL_CAPABILITY,
    NFS_CONTROL_CAPABILITY,
    QUOTA_CONTROL_CAPABILITY,
    SHARE_PRIVILEGE_CONTROL_CAPABILITY,
    SHARED_FOLDER_CONTROL_CAPABILITY,
    SMB_CONTROL_CAPABILITY,
    USER_CONTROL_CAPABILITY,
    USER_PASSWORD_CONTROL_CAPABILITY,
    OmvClient,
    OmvUnavailable,
    validate_devicefile,
    validate_omv_uuid,
)
from appliance.omv_health import OmvHealthMonitor

CONTROL_CAPABILITIES = frozenset(
    {
        GROUP_CONTROL_CAPABILITY,
        NFS_CONTROL_CAPABILITY,
        QUOTA_CONTROL_CAPABILITY,
        SHARE_PRIVILEGE_CONTROL_CAPABILITY,
        SHARED_FOLDER_CONTROL_CAPABILITY,
        SMB_CONTROL_CAPABILITY,
        USER_CONTROL_CAPABILITY,
        USER_PASSWORD_CONTROL_CAPABILITY,
    }
)


def register_omv_read_routes(
    router: APIRouter,
    omv: OmvClient,
    health_monitor: OmvHealthMonitor,
) -> None:
    """Attach read-only OMV routes without owning router policy or authentication."""

    @router.get("/status")
    async def status() -> dict:
        available = await run_in_threadpool(omv.ping) if omv.configured else False
        capabilities: list[str] = []
        capabilities_method = getattr(omv, "capabilities", None)
        if available and callable(capabilities_method):
            try:
                reported = await run_in_threadpool(capabilities_method)
                capabilities = [item for item in reported if item in CONTROL_CAPABILITIES]
            except OmvUnavailable:
                capabilities = []
        elif available:
            supports_control = getattr(omv, "supports_smb_control", None)
            if callable(supports_control) and await run_in_threadpool(supports_control):
                capabilities = [SMB_CONTROL_CAPABILITY]
        return {
            "configured": omv.configured,
            "available": available,
            "readOnly": not capabilities,
            "adminUrl": omv.admin_url,
            "capabilities": capabilities,
        }

    @router.get("/health")
    async def health() -> dict:
        snapshot = health_monitor.snapshot()
        if omv.configured and snapshot.get("checkedAt") is None:
            snapshot = await run_in_threadpool(health_monitor.poll)
        return snapshot

    @router.get("/filesystems")
    async def filesystems() -> dict:
        try:
            entries = await run_in_threadpool(omv.filesystems)
        except OmvUnavailable as exc:
            raise HTTPException(
                status_code=503,
                detail="OMV read-only integration is unavailable",
            ) from exc
        return {"filesystems": entries, "readOnly": True}

    @router.get("/smart")
    async def smart(
        devicefile: str = Query(min_length=5, max_length=256),
    ) -> dict:
        try:
            validated = validate_devicefile(devicefile)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            report = await run_in_threadpool(omv.smart, validated)
        except OmvUnavailable as exc:
            raise HTTPException(
                status_code=503,
                detail="OMV read-only integration is unavailable",
            ) from exc
        return {"smart": report, "readOnly": True}

    @router.get("/smart/devices")
    async def smart_devices() -> dict:
        try:
            devices = await run_in_threadpool(omv.smart_devices)
        except OmvUnavailable as exc:
            raise HTTPException(
                status_code=503,
                detail="OMV read-only integration is unavailable",
            ) from exc
        return {"devices": devices, "readOnly": True}

    @router.get("/topology")
    async def storage_topology() -> dict:
        try:
            topology = await run_in_threadpool(omv.storage_topology)
        except OmvUnavailable as exc:
            raise HTTPException(
                status_code=503,
                detail="OMV read-only integration is unavailable",
            ) from exc
        return {**topology, "readOnly": True}

    @router.get("/sharing")
    async def sharing_overview() -> dict:
        try:
            overview = await run_in_threadpool(omv.sharing_overview)
        except OmvUnavailable as exc:
            raise HTTPException(
                status_code=503,
                detail="OMV read-only integration is unavailable",
            ) from exc
        return {**overview, "readOnly": True}

    @router.get("/sharing/{share_uuid}/privileges")
    async def share_privileges(share_uuid: str) -> dict:
        try:
            validated = validate_omv_uuid(share_uuid)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            privileges = await run_in_threadpool(omv.share_privileges, validated)
        except OmvUnavailable as exc:
            raise HTTPException(
                status_code=503,
                detail="OMV read-only integration is unavailable",
            ) from exc
        return {"privileges": privileges, "readOnly": True}


__all__ = ["CONTROL_CAPABILITIES", "register_omv_read_routes"]
