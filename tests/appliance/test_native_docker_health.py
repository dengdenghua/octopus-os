from __future__ import annotations

import pytest

from appliance import native_docker_health
from appliance.app_registry.docker_client import DockerUnavailable


class FakeClient:
    def __init__(self, *, base_url: str, allow_direct_socket: bool) -> None:
        assert base_url == "http://127.0.0.1:2375"
        assert allow_direct_socket is False

    def ping(self) -> bool:
        return True


def test_health_binds_authenticated_control_to_exact_firewall_state(monkeypatch) -> None:
    monkeypatch.setattr(native_docker_health, "DockerClient", FakeClient)
    monkeypatch.setattr(
        native_docker_health.native_firewall,
        "verify",
        lambda: {
            "state": {"hubForwards": [{"appId": "jellyfin"}]},
            "rules": ["one", "two", "three"],
        },
    )

    assert native_docker_health.verify() == {
        "control": "ready",
        "hubForwards": 1,
        "managedRules": 3,
    }


def test_health_rejects_an_unreachable_control_endpoint(monkeypatch) -> None:
    class UnavailableClient(FakeClient):
        def ping(self) -> bool:
            return False

    monkeypatch.setattr(native_docker_health, "DockerClient", UnavailableClient)

    with pytest.raises(DockerUnavailable, match="authenticated ping"):
        native_docker_health.verify()
