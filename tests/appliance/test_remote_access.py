"""Private-network gateway status stays truthful and credential free."""

from __future__ import annotations

import asyncio
import json

import pytest

from appliance.remote_access import RemoteAccessService


def test_remote_access_is_off_by_default() -> None:
    status = RemoteAccessService().status()

    assert status == {
        "schema": "echo.remote-access.v1",
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


def test_tailscale_health_enables_only_the_https_web_surface() -> None:
    service = RemoteAccessService(
        provider="tailscale-sidecar",
        endpoint="https://Echo-OS.Example.ts.net/",
        probe=lambda: True,
        clock=lambda: 2_000.9,
    )

    status = asyncio.run(service.refresh())

    assert status["provider"] == "tailscale"
    assert status["available"] is True
    assert status["state"] == "connected"
    assert status["endpoint"] == "https://echo-os.example.ts.net"
    assert status["lastCheckedAt"] == 2_000
    assert status["transport"] == {
        "protocol": "wireguard+https",
        "encrypted": True,
        "tailnetOnly": True,
    }
    assert status["features"] == {
        "desktopWeb": True,
        "deviceLink": False,
        "fileSync": False,
        "photoSync": False,
    }
    serialized = json.dumps(status, sort_keys=True).casefold()
    assert all(marker not in serialized for marker in ("authkey", "token", "secret", "digest"))


def test_tailscale_reports_sync_only_after_the_device_api_mounts() -> None:
    service = RemoteAccessService(
        provider="tailscale-sidecar",
        endpoint="https://echo-os.example.ts.net",
        probe=lambda: True,
    )
    service.set_sync_available(True)

    status = asyncio.run(service.refresh())

    assert status["features"]["desktopWeb"] is True
    assert status["features"]["deviceLink"] is False
    assert status["features"]["fileSync"] is True
    assert status["features"]["photoSync"] is True


def test_failed_or_raising_health_probe_is_a_safe_connecting_state() -> None:
    def unavailable() -> bool:
        raise OSError("internal endpoint details must not escape")

    service = RemoteAccessService(
        provider="tailscale-sidecar",
        endpoint="https://echo-os.example.ts.net",
        probe=unavailable,
        clock=lambda: 5,
    )

    status = asyncio.run(service.refresh())

    assert status["available"] is False
    assert status["state"] == "connecting"
    assert status["lastCheckedAt"] == 5
    assert "internal endpoint" not in status["reason"]


@pytest.mark.parametrize(
    ("provider", "endpoint"),
    [
        ("other-provider", "https://echo.example.ts.net"),
        ("tailscale-sidecar", ""),
        ("", "https://echo.example.ts.net"),
        ("tailscale-sidecar", "http://echo.example.ts.net"),
        ("tailscale-sidecar", "https://echo.example.com"),
        ("tailscale-sidecar", "https://user:password@echo.example.ts.net"),
        ("tailscale-sidecar", "https://echo.example.ts.net/path"),
        ("tailscale-sidecar", "https://echo.example.ts.net:8443"),
    ],
)
def test_remote_access_configuration_fails_closed(provider: str, endpoint: str) -> None:
    with pytest.raises(ValueError):
        RemoteAccessService(provider=provider, endpoint=endpoint)


def test_environment_selects_only_the_supported_provider(monkeypatch) -> None:
    monkeypatch.setenv("ECHO_REMOTE_ACCESS_PROVIDER", "tailscale-sidecar")
    monkeypatch.setenv(
        "ECHO_REMOTE_ACCESS_URL",
        "https://echo-os.tailnet-name.ts.net",
    )

    service = RemoteAccessService.from_environment()

    assert service.configured is True
    assert service.status()["endpoint"] == "https://echo-os.tailnet-name.ts.net"
