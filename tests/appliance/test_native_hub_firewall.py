from __future__ import annotations

from typing import Any

import pytest

from appliance import native_hub_firewall
from appliance.hub.catalog import HubApp, HubCatalog

PLAN_ID = "a" * 64
CONTAINER_ID = "b" * 64


class FakeDocker:
    def __init__(self, containers: list[dict[str, Any]], inspected: dict[str, Any]) -> None:
        self.containers = containers
        self.inspected = inspected

    def list_containers(self, include_stopped: bool = True) -> list[dict[str, Any]]:
        assert include_stopped is True
        return self.containers

    def inspect_container(self, container_id: str) -> dict[str, Any]:
        assert container_id == CONTAINER_ID
        return self.inspected


def _labels(catalog: HubCatalog, app: HubApp, *, service: Any = None) -> dict[str, str]:
    artifact = app.package or app.bundle
    assert artifact is not None
    labels = {
        "sh.echo.hub.managed": "true",
        "sh.echo.hub.catalog-digest": catalog.digest,
        "sh.echo.hub.package-digest": artifact.digest,
        "sh.echo.hub.plan-id": PLAN_ID,
        "sh.echo.hub.version": app.version,
    }
    if service is not None:
        labels.update(
            {
                "sh.echo.hub.bundle-app-id": app.id,
                "sh.echo.hub.bundle-service": service.id,
                "sh.echo.hub.bundle-digest": artifact.digest,
                "sh.echo.hub.bundle-version": app.version,
            }
        )
        assert app.bundle is not None
        if service.id == app.bundle.public_service:
            labels["sh.echo.hub.app-id"] = app.id
    else:
        labels["sh.echo.hub.app-id"] = app.id
    return labels


def _runtime(
    catalog: HubCatalog,
    app: HubApp,
    *,
    mode: str,
    address: str | None,
    service: Any = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    labels = _labels(catalog, app, service=service)
    name = f"/echo-hub-{app.id}"
    ports = app.package.ports if app.package is not None else service.ports
    actual_ports = (
        {}
        if mode == "host"
        else {
            f"{port.container}/{port.protocol}": [{"HostIp": "0.0.0.0", "HostPort": str(port.host)}]
            for port in ports
        }
    )
    if service is None:
        networks = {"bridge": {"IPAddress": address}}
    elif mode == "host":
        networks = {}
    else:
        network_name = f"echo-hub-{app.id}-{service.networks[0]}-{PLAN_ID[:12]}"
        networks = {network_name: {"IPAddress": address}}
    listed = {"Id": CONTAINER_ID, "Names": [name], "Labels": labels}
    inspected = {
        "Id": CONTAINER_ID,
        "Name": name,
        "Config": {"Labels": labels},
        "State": {"Running": True},
        "HostConfig": {"NetworkMode": mode},
        "NetworkSettings": {"IPAddress": address, "Networks": networks, "Ports": actual_ports},
    }
    return [listed], inspected


def test_single_container_forward_is_derived_from_exact_runtime_state() -> None:
    catalog = HubCatalog.load()
    app = catalog.get("jellyfin")
    assert app is not None and app.package is not None
    containers, inspected = _runtime(catalog, app, mode="default", address="172.17.0.4")

    forwards = native_hub_firewall.desired_forwards(
        catalog,
        FakeDocker(containers, inspected),  # type: ignore[arg-type]
    )

    assert forwards == [
        {
            "appId": "jellyfin",
            "hostPort": 8096,
            "containerPort": 8096,
            "protocol": "tcp",
            "mode": "bridge",
            "address": "172.17.0.4",
        }
    ]


def test_host_network_forward_uses_private_source_input_rules_without_an_address() -> None:
    catalog = HubCatalog.load()
    app = catalog.get("home-assistant")
    assert app is not None and app.bundle is not None
    service = app.bundle.services[0]
    containers, inspected = _runtime(
        catalog,
        app,
        mode="host",
        address=None,
        service=service,
    )

    forwards = native_hub_firewall.desired_forwards(
        catalog,
        FakeDocker(containers, inspected),  # type: ignore[arg-type]
    )

    assert forwards == [
        {
            "appId": "home-assistant",
            "hostPort": 8123,
            "containerPort": 8123,
            "protocol": "tcp",
            "mode": "host",
            "address": None,
        }
    ]


def test_complete_multi_service_bundle_uses_its_declared_public_network() -> None:
    catalog = HubCatalog.load()
    app = catalog.get("nextcloud")
    assert app is not None and app.bundle is not None
    inspections: dict[str, dict[str, Any]] = {}
    containers: list[dict[str, Any]] = []
    public = next(
        service for service in app.bundle.services if service.id == app.bundle.public_service
    )
    for index, service in enumerate(app.bundle.services, start=1):
        container_id = f"{index:x}" * 64
        labels = _labels(catalog, app, service=service)
        is_public = service.id == public.id
        name = f"/echo-hub-{app.id}" if is_public else f"/echo-hub-{app.id}--{service.id}"
        containers.append({"Id": container_id, "Names": [name], "Labels": labels})
        network_name = f"echo-hub-{app.id}-{service.networks[0]}-{PLAN_ID[:12]}"
        inspections[container_id] = {
            "Id": container_id,
            "Name": name,
            "Config": {"Labels": labels},
            "State": {"Running": True},
            "HostConfig": {"NetworkMode": "default"},
            "NetworkSettings": {
                "Networks": {network_name: {"IPAddress": f"172.21.0.{index + 2}"}},
                "Ports": (
                    {"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8081"}]} if is_public else {}
                ),
            },
        }

    class MultiDocker:
        def list_containers(self, include_stopped: bool = True) -> list[dict[str, Any]]:
            assert include_stopped is True
            return containers

        def inspect_container(self, container_id: str) -> dict[str, Any]:
            return inspections[container_id]

    forwards = native_hub_firewall.desired_forwards(
        catalog,
        MultiDocker(),  # type: ignore[arg-type]
    )

    assert forwards == [
        {
            "appId": "nextcloud",
            "hostPort": 8081,
            "containerPort": 80,
            "protocol": "tcp",
            "mode": "bridge",
            "address": "172.21.0.5",
        }
    ]


def test_runtime_binding_or_catalog_identity_drift_is_rejected() -> None:
    catalog = HubCatalog.load()
    app = catalog.get("jellyfin")
    assert app is not None and app.package is not None
    containers, inspected = _runtime(catalog, app, mode="default", address="172.17.0.4")
    inspected["NetworkSettings"]["Ports"]["8096/tcp"][0]["HostPort"] = "9999"

    with pytest.raises(OSError, match="bindings differ"):
        native_hub_firewall.desired_forwards(
            catalog,
            FakeDocker(containers, inspected),  # type: ignore[arg-type]
        )


def test_sync_closes_all_hub_ports_when_docker_derivation_is_unsafe(monkeypatch) -> None:
    calls: list[list[dict[str, Any]]] = []

    class BrokenDocker:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def list_containers(self, include_stopped: bool = True) -> list[dict[str, Any]]:
            raise OSError("docker state changed")

    monkeypatch.setattr(native_hub_firewall, "DockerClient", BrokenDocker)
    monkeypatch.setattr(
        native_hub_firewall.native_firewall,
        "sync_hub",
        lambda forwards: calls.append(list(forwards)) or {"changed": True},
    )

    with pytest.raises(OSError, match="docker state changed"):
        native_hub_firewall.sync()
    assert calls == [[]]
