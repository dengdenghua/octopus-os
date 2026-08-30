"""System-owned mobile device linking built on Agent Tentacle.

The native Echo image deliberately disables Agent's convenience LAN listener.
This module exposes that capability through an authenticated appliance surface:

* listening is opt-in and persisted only after a successful administrator step-up;
* pairing invitations are short lived and bind to the first device id that uses them;
* only keyed token digests are stored, so a copied state file cannot reconnect;
* paired devices can be revoked individually;
* the public status response never contains a token, host filesystem path or UDID.

When a development/NAS Agent already owns a Tentacle coordinator, Echo reuses it
without attempting to replace its listener.  That compatibility mode is clearly
reported as ``agent-shared`` and cannot claim per-device revocation.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import socket
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request

from appliance.approval import HighRiskApprovalService, consume_request_approval
from appliance.audit import ApplianceAudit, AuditIntegrityError
from appliance.auth import write_auth_store
from appliance.remote_access import unavailable_remote_access_status
from appliance.security import ApplianceAuthenticator, resolve_authenticator

DEVICE_LINK_SCHEMA = "echo.device-link.v1"
DEVICE_LINK_STATE_FILENAME = "device-link.json"
DEVICE_LINK_ENABLE_ACTION = "device-link.enable"
DEVICE_LINK_DISABLE_ACTION = "device-link.disable"
DEVICE_LINK_PAIR_ACTION = "device-link.pair"
DEVICE_LINK_REVOKE_ACTION = "device-link.device.revoke"
DEVICE_LINK_LAN_TARGET = "lan"
DEFAULT_TENTACLE_PORT = 8765
DEFAULT_DEVICE_SYNC_PORT = 8000
PAIRING_INVITATION_SECONDS = 5 * 60
MAX_PAIRED_DEVICES = 64
_DEVICE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_LAN_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(?:\.local)?$",
    re.IGNORECASE,
)
_RFC1918_NETWORKS = tuple(
    ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


class DeviceLinkError(RuntimeError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _safe_text(value: Any, maximum: int) -> str:
    text = str(value or "").strip()
    return text[:maximum]


def _lan_ip() -> str:
    """Best-effort LAN address without sending traffic."""

    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        client.connect(("8.8.8.8", 80))
        return str(client.getsockname()[0])
    except OSError:
        return "127.0.0.1"
    finally:
        client.close()


def _safe_lan_host(value: Any) -> str | None:
    """Accept only a reachable RFC1918 IPv4 or local hostname for pairing."""

    host = str(value or "").strip().rstrip(".")
    if not host or host.casefold() in {"localhost", "localhost.localdomain"}:
        return None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host.casefold() if _LAN_HOSTNAME.fullmatch(host) else None
    if isinstance(address, ipaddress.IPv4Address) and any(
        address in network for network in _RFC1918_NETWORKS
    ):
        return str(address)
    return None


def _empty_state() -> dict[str, Any]:
    return {"schema": DEVICE_LINK_SCHEMA, "enabled": False, "devices": []}


class DeviceLinkService:
    """Own or project one Agent Tentacle coordinator for Echo OS."""

    def __init__(
        self,
        *,
        data_dir: str | Path,
        jwt_secret: str,
        coordinator: Any | None = None,
        coordinator_factory: Callable[[], Any] | None = None,
        ws_port: int = DEFAULT_TENTACLE_PORT,
        device_sync_port: int | None = None,
        public_host: str = "",
        allow_host_resolver_fallback: bool = True,
        clock: Callable[[], float] = time.time,
        lan_ip_resolver: Callable[[], str] = _lan_ip,
        remote_access: Any | None = None,
    ) -> None:
        if not jwt_secret:
            raise ValueError("device link requires a JWT secret")
        if not 1 <= int(ws_port) <= 65535:
            raise ValueError("device link port must be between 1 and 65535")
        if device_sync_port is not None and not 1 <= int(device_sync_port) <= 65535:
            raise ValueError("device sync port must be between 1 and 65535")
        normalized_public_host = _safe_lan_host(public_host)
        if public_host.strip() and normalized_public_host is None:
            raise ValueError("device link public host must be an RFC1918 IPv4 or local hostname")
        self._path = Path(data_dir) / DEVICE_LINK_STATE_FILENAME
        self._credential_key = hmac.new(
            jwt_secret.encode("utf-8"),
            b"echo-os/device-link/credential/v1",
            hashlib.sha256,
        ).digest()
        self._coordinator = coordinator
        self._coordinator_factory = coordinator_factory
        self._external = coordinator is not None
        self._ws_port = int(
            getattr(getattr(coordinator, "ws_server", None), "port", ws_port)
            if coordinator is not None
            else ws_port
        )
        self._device_sync_port = int(device_sync_port) if device_sync_port is not None else None
        self._public_host = normalized_public_host
        self._allow_host_resolver_fallback = bool(allow_host_resolver_fallback)
        self._clock = clock
        self._lan_ip_resolver = lan_ip_resolver
        self._remote_access = remote_access
        self._state_lock = threading.RLock()
        self._lifecycle_lock = asyncio.Lock()
        self._invitations: dict[str, float] = {}
        self._startup_error = ""
        self._state = self._read_state()

    @property
    def managed(self) -> bool:
        return not self._external

    def _read_state(self) -> dict[str, Any]:
        if not self._path.exists():
            return _empty_state()
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid device link state") from exc
        if not isinstance(payload, dict) or payload.get("schema") != DEVICE_LINK_SCHEMA:
            raise ValueError("invalid device link state schema")
        if not isinstance(payload.get("enabled"), bool):
            raise ValueError("invalid device link enabled state")
        raw_devices = payload.get("devices")
        if not isinstance(raw_devices, list) or len(raw_devices) > MAX_PAIRED_DEVICES:
            raise ValueError("invalid device link device registry")
        seen: set[str] = set()
        devices: list[dict[str, Any]] = []
        for raw in raw_devices:
            if not isinstance(raw, dict):
                raise ValueError("invalid device link device record")
            device_id = raw.get("id")
            digest = raw.get("credentialDigest")
            if (
                not isinstance(device_id, str)
                or _DEVICE_ID.fullmatch(device_id) is None
                or device_id in seen
                or not isinstance(digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            ):
                raise ValueError("invalid device link device record")
            seen.add(device_id)
            devices.append(
                {
                    "id": device_id,
                    "credentialDigest": digest,
                    "pairedAt": max(0, int(raw.get("pairedAt") or 0)),
                    "lastSeenAt": max(0, int(raw.get("lastSeenAt") or 0)),
                    "platform": _safe_text(raw.get("platform"), 32) or "unknown",
                    "brand": _safe_text(raw.get("brand"), 64),
                    "model": _safe_text(raw.get("model"), 96),
                    "version": _safe_text(raw.get("version"), 32),
                }
            )
        return {
            "schema": DEVICE_LINK_SCHEMA,
            "enabled": bool(payload["enabled"]),
            "devices": devices,
        }

    def _write_state_locked(self) -> None:
        write_auth_store(self._state, self._path)

    def _credential_digest(self, token: str) -> str:
        return hmac.new(
            self._credential_key,
            token.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _prune_invitations_locked(self, now: float) -> None:
        self._invitations = {
            digest: expires_at
            for digest, expires_at in self._invitations.items()
            if expires_at > now
        }

    def _install_managed_auth(self, coordinator: Any) -> None:
        server = getattr(coordinator, "ws_server", None)
        if server is None or not hasattr(server, "_check_auth"):
            raise DeviceLinkError(503, "Agent device transport is incompatible")
        # A non-empty unpredictable fallback keeps the upstream transport in
        # authenticated mode. The actual decision is the keyed, per-device
        # validator below; random fallback still fails closed if hook semantics
        # change in a future Agent release.
        server.auth_token = secrets.token_urlsafe(32)
        server._check_auth = self._check_hello_auth

    def _check_hello_auth(self, message: dict[str, Any]) -> bool:
        params = message.get("params")
        if not isinstance(params, dict):
            return False
        device_id = params.get("tentacle_id")
        token = params.get("auth_token") or params.get("token")
        if (
            not isinstance(device_id, str)
            or _DEVICE_ID.fullmatch(device_id) is None
            or not isinstance(token, str)
            or not 16 <= len(token) <= 512
        ):
            return False

        digest = self._credential_digest(token)
        now = self._clock()
        with self._state_lock:
            for device in self._state["devices"]:
                if device["id"] != device_id:
                    continue
                if not hmac.compare_digest(device["credentialDigest"], digest):
                    return False
                device["lastSeenAt"] = int(now)
                device["platform"] = _safe_text(params.get("platform"), 32) or "unknown"
                device["brand"] = _safe_text(params.get("brand"), 64)
                device["model"] = _safe_text(params.get("model"), 96)
                device["version"] = _safe_text(params.get("version"), 32)
                self._write_state_locked()
                return True

            self._prune_invitations_locked(now)
            expires_at = self._invitations.get(digest)
            if expires_at is None or expires_at <= now:
                return False
            if len(self._state["devices"]) >= MAX_PAIRED_DEVICES:
                return False
            self._invitations.pop(digest, None)
            self._state["devices"].append(
                {
                    "id": device_id,
                    "credentialDigest": digest,
                    "pairedAt": int(now),
                    "lastSeenAt": int(now),
                    "platform": _safe_text(params.get("platform"), 32) or "unknown",
                    "brand": _safe_text(params.get("brand"), 64),
                    "model": _safe_text(params.get("model"), 96),
                    "version": _safe_text(params.get("version"), 32),
                }
            )
            self._write_state_locked()
            return True

    def authenticate_device(self, device_id: str, token: str) -> bool:
        """Validate one managed per-device credential for non-WebSocket APIs.

        Agent compatibility mode intentionally returns ``False``: its shared
        credential cannot identify or revoke one phone safely.  Pairing also
        has to be enabled, so disabling Device Link closes both transports.
        """

        if (
            self._external
            or not self._state["enabled"]
            or _DEVICE_ID.fullmatch(device_id) is None
            or not isinstance(token, str)
            or not 16 <= len(token) <= 512
        ):
            return False
        digest = self._credential_digest(token)
        with self._state_lock:
            for device in self._state["devices"]:
                if device["id"] != device_id:
                    continue
                if not hmac.compare_digest(device["credentialDigest"], digest):
                    return False
                now = int(self._clock())
                if now - int(device.get("lastSeenAt") or 0) >= 15:
                    device["lastSeenAt"] = now
                    self._write_state_locked()
                return True
        return False

    def managed_device(self, device_id: str) -> dict[str, Any] | None:
        """Return a bounded public managed-device record, never its digest."""

        if self._external or _DEVICE_ID.fullmatch(device_id) is None:
            return None
        live = self._live_devices()
        with self._state_lock:
            record = next(
                (dict(item) for item in self._state["devices"] if item["id"] == device_id),
                None,
            )
        if record is None:
            return None
        return self._public_device(record, live.get(device_id))

    def _listener_active(self) -> bool:
        server = getattr(getattr(self._coordinator, "ws_server", None), "_server", None)
        return server is not None

    async def startup(self) -> None:
        if self._external or not self._state["enabled"]:
            return
        try:
            await self._start_managed()
        except Exception as exc:  # noqa: BLE001 - report unavailable, don't break OS boot
            self._startup_error = _safe_text(exc, 256) or "device listener failed to start"

    async def shutdown(self) -> None:
        if self._external or self._coordinator is None or not self._listener_active():
            return
        with contextlib.suppress(Exception):
            await self._coordinator.stop()

    async def _start_managed(self) -> None:
        if self._listener_active():
            return
        if self._coordinator is None:
            if self._coordinator_factory is None:
                raise DeviceLinkError(503, "Agent device transport is unavailable")
            self._coordinator = self._coordinator_factory()
        self._install_managed_auth(self._coordinator)
        await self._coordinator.start()
        self._startup_error = ""
        with contextlib.suppress(Exception):
            from appliance.agent_api.devices import set_active_device_coordinator

            set_active_device_coordinator(self._coordinator)

    async def enable(self) -> dict[str, Any]:
        if self._external:
            return self.status()
        async with self._lifecycle_lock:
            try:
                await self._start_managed()
            except DeviceLinkError:
                raise
            except OSError as exc:
                raise DeviceLinkError(503, "LAN device listener could not start") from exc
            except Exception as exc:  # noqa: BLE001 - dependency boundary
                raise DeviceLinkError(503, "Agent device transport could not start") from exc
            with self._state_lock:
                self._state["enabled"] = True
                self._write_state_locked()
        return self.status()

    async def disable(self) -> dict[str, Any]:
        if self._external:
            raise DeviceLinkError(409, "Agent owns this development device listener")
        async with self._lifecycle_lock:
            if self._coordinator is not None and self._listener_active():
                try:
                    await self._coordinator.stop()
                except Exception as exc:  # noqa: BLE001 - dependency boundary
                    raise DeviceLinkError(503, "LAN device listener could not stop") from exc
            with contextlib.suppress(Exception):
                from appliance.agent_api.devices import set_active_device_coordinator

                set_active_device_coordinator(None)
            with self._state_lock:
                self._invitations.clear()
                self._state["enabled"] = False
                self._write_state_locked()
        return self.status()

    def create_pairing_invitation(self, *, request_host: str = "") -> dict[str, Any]:
        if (not self._external and not self._state["enabled"]) or not self._listener_active():
            raise DeviceLinkError(409, "LAN device connection is not enabled")
        lan_host = (
            self._public_host
            or _safe_lan_host(request_host)
            or (
                _safe_lan_host(self._lan_ip_resolver())
                if self._allow_host_resolver_fallback
                else None
            )
        )
        if lan_host is None:
            raise DeviceLinkError(503, "a reachable LAN device host is not configured")
        ws_url = f"ws://{lan_host}:{self._ws_port}"
        if self._external:
            token = _safe_text(
                getattr(getattr(self._coordinator, "ws_server", None), "auth_token", ""),
                512,
            )
            if not token:
                raise DeviceLinkError(503, "Agent pairing credential is unavailable")
            expires_at: int | None = None
            credential_mode = "shared"
        else:
            token = secrets.token_urlsafe(24)
            expires_at = int(self._clock()) + PAIRING_INVITATION_SECONDS
            with self._state_lock:
                self._prune_invitations_locked(self._clock())
                self._invitations[self._credential_digest(token)] = expires_at
            credential_mode = "per-device"
        sync_base_url: str | None = None
        sync_transport: str | None = None
        if not self._external:
            remote_status = self._remote_access.status() if self._remote_access is not None else {}
            remote_features = remote_status.get("features")
            if (
                isinstance(remote_features, dict)
                and bool(remote_features.get("fileSync"))
                and isinstance(remote_status.get("endpoint"), str)
            ):
                sync_base_url = str(remote_status["endpoint"])
                sync_transport = "tailnet-https"
            elif self._device_sync_port is not None:
                sync_base_url = f"http://{lan_host}:{self._device_sync_port}"
                sync_transport = "lan-http"
        connect_string = f"echo://join?ws={quote(ws_url, safe='')}&token={quote(token, safe='')}"
        if sync_base_url is not None:
            connect_string += f"&sync={quote(sync_base_url, safe='')}"
        invitation: dict[str, Any] = {
            "schema": "echo.device-link.invitation.v1",
            "scope": "lan",
            "wsUrl": ws_url,
            "connectString": connect_string,
            "expiresAt": expires_at,
            "credentialMode": credential_mode,
        }
        if sync_base_url is not None:
            invitation["deviceSync"] = {
                "baseUrl": sync_base_url,
                "protocolVersion": 1,
                "transport": sync_transport,
            }
        return invitation

    def _live_devices(self) -> dict[str, Any]:
        pool = getattr(self._coordinator, "pool", None)
        if pool is None:
            return {}
        try:
            return {str(device.tentacle_id): device for device in pool.all()}
        except Exception:  # noqa: BLE001 - Agent compatibility boundary
            return {}

    @staticmethod
    def _public_device(record: dict[str, Any], live: Any | None) -> dict[str, Any]:
        meta = getattr(live, "meta", {}) if live is not None else {}
        if not isinstance(meta, dict):
            meta = {}
        capabilities = getattr(live, "capabilities", ()) if live is not None else ()
        safe_capabilities = [
            _safe_text(item, 96) for item in list(capabilities)[:8] if _safe_text(item, 96)
        ]
        status = _safe_text(getattr(getattr(live, "status", None), "value", ""), 24)
        return {
            "id": record["id"],
            "type": _safe_text(getattr(getattr(live, "tentacle_type", None), "value", "mobile"), 24)
            or "mobile",
            "platform": _safe_text(getattr(live, "platform", ""), 32)
            or record.get("platform")
            or "unknown",
            "brand": _safe_text(meta.get("brand"), 64) or record.get("brand", ""),
            "model": _safe_text(meta.get("model"), 96) or record.get("model", ""),
            "version": _safe_text(meta.get("version"), 32) or record.get("version", ""),
            "status": status or ("online" if live is not None else "offline"),
            "online": bool(live is not None and getattr(live, "is_online", False)),
            "busy": bool(live is not None and getattr(live, "is_busy", False)),
            "battery": (
                int(meta["battery"])
                if isinstance(meta.get("battery"), (int, float))
                and not isinstance(meta.get("battery"), bool)
                and 0 <= int(meta["battery"]) <= 100
                else None
            ),
            "charging": bool(meta.get("is_charging", False)),
            "currentApp": _safe_text(meta.get("current_app"), 128),
            "pairedAt": record.get("pairedAt"),
            "lastSeenAt": record.get("lastSeenAt"),
            "capabilities": safe_capabilities,
            "totalCapabilities": len(capabilities),
            "individuallyRevocable": "credentialDigest" in record,
        }

    def status(self) -> dict[str, Any]:
        live = self._live_devices()
        if self._external:
            # The upstream compatibility listener retains online devices only;
            # synthesize bounded public records without inventing persistence.
            records = [
                {
                    "id": device_id,
                    "pairedAt": None,
                    "lastSeenAt": None,
                    "platform": "unknown",
                    "brand": "",
                    "model": "",
                    "version": "",
                }
                for device_id in sorted(live)[:MAX_PAIRED_DEVICES]
            ]
        else:
            with self._state_lock:
                records = [dict(item) for item in self._state["devices"]]
        devices = [self._public_device(record, live.get(record["id"])) for record in records]
        enabled = self._external or bool(self._state["enabled"])
        listener_active = self._listener_active()
        return {
            "schema": DEVICE_LINK_SCHEMA,
            "enabled": enabled,
            "listenerActive": listener_active,
            "mode": "agent-shared" if self._external else "echo-managed",
            "scope": "lan",
            "wsPort": self._ws_port,
            "canManageListener": not self._external,
            "canPair": enabled and listener_active,
            "pairedDeviceCount": len(devices),
            "onlineDeviceCount": sum(1 for device in devices if device["online"]),
            "devices": devices,
            "startupError": self._startup_error,
            "transport": {
                "protocol": "websocket",
                "encrypted": False,
                "authenticated": True,
            },
            "remoteAccess": (
                self._remote_access.status()
                if self._remote_access is not None
                else unavailable_remote_access_status()
            ),
        }

    async def revoke_device(self, device_id: str) -> dict[str, Any]:
        if _DEVICE_ID.fullmatch(device_id) is None:
            raise DeviceLinkError(422, "invalid device id")
        if self._external:
            raise DeviceLinkError(409, "shared Agent credentials cannot revoke one device")
        with self._state_lock:
            remaining = [item for item in self._state["devices"] if item["id"] != device_id]
            if len(remaining) == len(self._state["devices"]):
                raise DeviceLinkError(404, "paired device not found")
            self._state["devices"] = remaining
            self._write_state_locked()
        connection = getattr(getattr(self._coordinator, "ws_server", None), "_connections", {}).get(
            device_id
        )
        if connection is not None:
            with contextlib.suppress(Exception):
                await connection.close(code=1008, reason="device link revoked")
        return self.status()


def create_device_link_router(
    service: DeviceLinkService,
    *,
    jwt_secret: str | None = None,
    approval: HighRiskApprovalService,
    audit: ApplianceAudit,
    authenticator: ApplianceAuthenticator | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/appliance/device-link", tags=["appliance", "device-link"])
    require_operator = resolve_authenticator(
        jwt_secret=jwt_secret, authenticator=authenticator
    ).operator_dependency()

    def _record(*, actor: str, action: str, target: str, outcome: str) -> None:
        try:
            audit.record(actor=actor, action=action, target=target, outcome=outcome)
        except (OSError, AuditIntegrityError) as exc:
            raise HTTPException(
                status_code=503, detail="appliance audit integrity check failed"
            ) from exc

    def _authorize(
        request: Request,
        *,
        actor: str,
        action: str,
        target: str,
    ) -> None:
        consume_request_approval(
            request,
            approval,
            actor=actor,
            action=action,
            target=target,
        )
        _record(actor=actor, action=action, target=target, outcome="attempted")

    @router.get("")
    def status(_actor: str = Depends(require_operator)) -> dict[str, Any]:
        return service.status()

    @router.post("/enable")
    async def enable(
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        action = DEVICE_LINK_ENABLE_ACTION
        target = DEVICE_LINK_LAN_TARGET
        _authorize(request, actor=actor, action=action, target=target)
        try:
            result = await service.enable()
        except DeviceLinkError as exc:
            _record(actor=actor, action=action, target=target, outcome="failed")
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        _record(actor=actor, action=action, target=target, outcome="succeeded")
        return result

    @router.post("/disable")
    async def disable(
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        action = DEVICE_LINK_DISABLE_ACTION
        target = DEVICE_LINK_LAN_TARGET
        _authorize(request, actor=actor, action=action, target=target)
        try:
            result = await service.disable()
        except DeviceLinkError as exc:
            _record(actor=actor, action=action, target=target, outcome="failed")
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        _record(actor=actor, action=action, target=target, outcome="succeeded")
        return result

    @router.post("/pairing-invitations")
    def create_invitation(
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        action = DEVICE_LINK_PAIR_ACTION
        target = DEVICE_LINK_LAN_TARGET
        _authorize(request, actor=actor, action=action, target=target)
        try:
            result = service.create_pairing_invitation(request_host=request.url.hostname or "")
        except DeviceLinkError as exc:
            _record(actor=actor, action=action, target=target, outcome="failed")
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        _record(actor=actor, action=action, target=target, outcome="succeeded")
        return result

    @router.delete("/devices/{device_id}")
    async def revoke_device(
        device_id: str,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        if _DEVICE_ID.fullmatch(device_id) is None:
            raise HTTPException(status_code=422, detail="invalid device id")
        action = DEVICE_LINK_REVOKE_ACTION
        _authorize(request, actor=actor, action=action, target=device_id)
        try:
            result = await service.revoke_device(device_id)
        except DeviceLinkError as exc:
            _record(actor=actor, action=action, target=device_id, outcome="failed")
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        _record(actor=actor, action=action, target=device_id, outcome="succeeded")
        return result

    return router


__all__ = [
    "DEFAULT_DEVICE_SYNC_PORT",
    "DEFAULT_TENTACLE_PORT",
    "DEVICE_LINK_DISABLE_ACTION",
    "DEVICE_LINK_ENABLE_ACTION",
    "DEVICE_LINK_LAN_TARGET",
    "DEVICE_LINK_PAIR_ACTION",
    "DEVICE_LINK_REVOKE_ACTION",
    "DEVICE_LINK_SCHEMA",
    "DeviceLinkError",
    "DeviceLinkService",
    "PAIRING_INVITATION_SECONDS",
    "create_device_link_router",
]
