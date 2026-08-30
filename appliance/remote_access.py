"""Safe projection of Echo's optional private-network gateway.

The appliance process never receives a host network-management capability or
an arbitrary command surface.  Production remote access is provided by the
official Tailscale container in ``docker-compose.remote-access.yml``.  This
module only monitors that sidecar's fixed health endpoint and publishes a
credential-free product status to authenticated Echo clients.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

REMOTE_ACCESS_SCHEMA = "echo.remote-access.v1"
REMOTE_ACCESS_PROVIDER_ENV = "ECHO_REMOTE_ACCESS_PROVIDER"
REMOTE_ACCESS_URL_ENV = "ECHO_REMOTE_ACCESS_URL"
TAILSCALE_SIDECAR_PROVIDER = "tailscale-sidecar"
TAILSCALE_HEALTH_URL = "http://tailscale:9002/healthz"
DEFAULT_MONITOR_SECONDS = 5.0
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def _tailscale_endpoint(value: str) -> str:
    parsed = urlsplit(value.strip())
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid Tailscale remote-access URL port") from exc
    hostname = (parsed.hostname or "").rstrip(".").casefold()
    if (
        parsed.scheme.casefold() != "https"
        or not hostname
        or not hostname.endswith(".ts.net")
        or hostname == "ts.net"
        or len(hostname) > 253
        or any(_DNS_LABEL.fullmatch(label) is None for label in hostname.split("."))
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("ECHO_REMOTE_ACCESS_URL must be one HTTPS *.ts.net origin")
    return f"https://{hostname}"


def _probe_tailscale_sidecar() -> bool:
    request = urllib.request.Request(
        TAILSCALE_HEALTH_URL,
        headers={"User-Agent": "Echo-Remote-Access-Monitor/1"},
        method="GET",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=2.0) as response:  # nosec B310 - fixed URL
            response.read(64)
            return response.status == 200
    except (OSError, TimeoutError, urllib.error.HTTPError, urllib.error.URLError):
        return False


def unavailable_remote_access_status() -> dict[str, Any]:
    return {
        "schema": REMOTE_ACCESS_SCHEMA,
        "provider": "none",
        "mode": "not-configured",
        "configured": False,
        "available": False,
        "state": "not-configured",
        "scope": "none",
        "endpoint": None,
        "lastCheckedAt": None,
        "transport": {
            "protocol": "none",
            "encrypted": False,
            "tailnetOnly": False,
        },
        "features": {
            "desktopWeb": False,
            "deviceLink": False,
            "fileSync": False,
            "photoSync": False,
        },
        "reason": "private network or relay is not configured",
    }


class RemoteAccessService:
    """Monitor one fixed Tailscale Serve sidecar without handling its secret."""

    def __init__(
        self,
        *,
        provider: str = "",
        endpoint: str = "",
        probe: Callable[[], bool] = _probe_tailscale_sidecar,
        clock: Callable[[], float] = time.time,
        monitor_seconds: float = DEFAULT_MONITOR_SECONDS,
    ) -> None:
        normalized_provider = provider.strip().casefold()
        if normalized_provider not in {"", TAILSCALE_SIDECAR_PROVIDER}:
            raise ValueError("unsupported Echo remote-access provider")
        if normalized_provider and not endpoint.strip():
            raise ValueError("ECHO_REMOTE_ACCESS_URL is required for Tailscale")
        if not normalized_provider and endpoint.strip():
            raise ValueError("ECHO_REMOTE_ACCESS_PROVIDER is required with a remote-access URL")
        self._provider = normalized_provider
        self._endpoint = _tailscale_endpoint(endpoint) if normalized_provider else None
        self._probe = probe
        self._clock = clock
        self._monitor_seconds = max(1.0, float(monitor_seconds))
        self._lock = threading.RLock()
        self._connected = False
        self._sync_available = False
        self._checked_at: int | None = None
        self._monitor_task: asyncio.Task[None] | None = None

    @classmethod
    def from_environment(cls) -> RemoteAccessService:
        return cls(
            provider=os.environ.get(REMOTE_ACCESS_PROVIDER_ENV, ""),
            endpoint=os.environ.get(REMOTE_ACCESS_URL_ENV, ""),
        )

    @property
    def configured(self) -> bool:
        return self._provider == TAILSCALE_SIDECAR_PROVIDER

    def set_sync_available(self, available: bool) -> None:
        """Publish whether the authenticated device-sync API mounted safely."""

        with self._lock:
            self._sync_available = bool(available)

    def status(self) -> dict[str, Any]:
        if not self.configured:
            return unavailable_remote_access_status()
        with self._lock:
            connected = self._connected
            checked_at = self._checked_at
            sync_available = self._sync_available
        return {
            "schema": REMOTE_ACCESS_SCHEMA,
            "provider": "tailscale",
            "mode": "sidecar",
            "configured": True,
            "available": connected,
            "state": "connected" if connected else "connecting",
            "scope": "private-network",
            "endpoint": self._endpoint,
            "lastCheckedAt": checked_at,
            "transport": {
                "protocol": "wireguard+https",
                "encrypted": True,
                "tailnetOnly": True,
            },
            "features": {
                "desktopWeb": connected,
                # The sidecar deliberately exposes only Echo's HTTPS surface.
                # Tentacle's separate LAN WebSocket remains private.
                "deviceLink": False,
                "fileSync": connected and sync_available,
                "photoSync": connected and sync_available,
            },
            "reason": (
                "Tailscale private-network gateway is connected"
                if connected
                else "Tailscale is waiting for authorization or network connectivity"
            ),
        }

    async def refresh(self) -> dict[str, Any]:
        if not self.configured:
            return self.status()
        connected = False
        with contextlib.suppress(Exception):
            connected = bool(await asyncio.to_thread(self._probe))
        with self._lock:
            self._connected = connected
            self._checked_at = max(0, int(self._clock()))
        return self.status()

    async def _monitor(self) -> None:
        while True:
            await asyncio.sleep(self._monitor_seconds)
            await self.refresh()

    async def start(self) -> None:
        if not self.configured or (
            self._monitor_task is not None and not self._monitor_task.done()
        ):
            return
        await self.refresh()
        self._monitor_task = asyncio.create_task(
            self._monitor(),
            name="echo-remote-access-monitor",
        )

    async def stop(self) -> None:
        task = self._monitor_task
        self._monitor_task = None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


__all__ = [
    "REMOTE_ACCESS_PROVIDER_ENV",
    "REMOTE_ACCESS_SCHEMA",
    "REMOTE_ACCESS_URL_ENV",
    "RemoteAccessService",
    "TAILSCALE_HEALTH_URL",
    "TAILSCALE_SIDECAR_PROVIDER",
    "unavailable_remote_access_status",
]
