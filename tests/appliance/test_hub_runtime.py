"""Bounded, secret-free Hub runtime health projection tests."""

from __future__ import annotations

import json
from typing import Any

import pytest

from appliance.hub.catalog import HubApp, HubCatalog
from appliance.hub.runtime import HubRuntimeInspector, validate_hub_runtime


class _RuntimeEngine:
    def __init__(
        self,
        containers: list[dict[str, Any]],
        inspected: dict[str, dict[str, Any]],
        stats: dict[str, dict[str, Any]],
    ) -> None:
        self.containers = containers
        self.inspected = inspected
        self.stats = stats
        self.inspect_calls: list[str] = []
        self.stats_calls: list[str] = []

    def list_containers(self, include_stopped: bool = True) -> list[dict[str, Any]]:
        assert include_stopped is True
        return self.containers

    def inspect_container(self, container_id: str) -> dict[str, Any] | None:
        self.inspect_calls.append(container_id)
        return self.inspected.get(container_id)

    def container_stats(self, container_id: str) -> dict[str, Any]:
        self.stats_calls.append(container_id)
        return self.stats[container_id]


def _single_container(app: HubApp, container_id: str = "a" * 64) -> dict[str, Any]:
    assert app.package is not None
    return {
        "Id": container_id,
        "Names": [f"/echo-hub-{app.id}"],
        "Labels": {
            "sh.echo.hub.managed": "true",
            "sh.echo.hub.app-id": app.id,
            "sh.echo.hub.catalog-digest": "1" * 64,
            "sh.echo.hub.plan-id": "2" * 64,
            "sh.echo.hub.package-digest": app.package.digest,
            "sh.echo.hub.version": app.version,
        },
    }


def _running_inspect(*, health: str | None = None, restarts: int = 0) -> dict[str, Any]:
    state: dict[str, Any] = {
        "Status": "running",
        "Running": True,
        "OOMKilled": False,
        "ExitCode": 0,
    }
    if health is not None:
        state["Health"] = {
            "Status": health,
            "Log": [{"Output": "password=must-not-cross-the-boundary"}],
        }
    return {
        "State": state,
        "RestartCount": restarts,
        "Config": {"Env": ["ADMIN_PASSWORD=must-not-leak"]},
        "Mounts": [{"Source": "/srv/private", "Destination": "/data"}],
        "NetworkSettings": {"IPAddress": "172.30.0.7"},
    }


def _stats(*, current: int = 150, previous: int = 100) -> dict[str, Any]:
    return {
        "cpu_stats": {
            "cpu_usage": {"total_usage": current, "percpu_usage": [1, 1]},
            "system_cpu_usage": 2_000,
            "online_cpus": 2,
        },
        "precpu_stats": {
            "cpu_usage": {"total_usage": previous},
            "system_cpu_usage": 1_000,
        },
        "memory_stats": {"usage": 256 * 1024**2, "limit": 3 * 1024**3},
        "pids_stats": {"current": 23},
        "networks": {"eth0": {"rx_bytes": 999, "tx_bytes": 888}},
    }


def test_single_app_runtime_is_healthy_and_never_exposes_raw_docker_fields() -> None:
    catalog = HubCatalog.load()
    app = catalog.get("jellyfin")
    assert app is not None
    container = _single_container(app)
    container_id = container["Id"]
    engine = _RuntimeEngine(
        [container],
        {container_id: _running_inspect(restarts=2)},
        {container_id: _stats()},
    )

    result = HubRuntimeInspector(catalog, engine).inspect(app.id)

    assert validate_hub_runtime(result) is result
    assert result["status"] == "healthy"
    assert result["summary"] == {
        "serviceCount": 1,
        "runningServices": 1,
        "healthyServices": 1,
        "restartCount": 2,
        "cpuPercent": 10.0,
        "memoryUsageBytes": 256 * 1024**2,
        "memoryLimitBytes": 3 * 1024**3,
        "pids": 23,
    }
    service = result["services"][0]
    assert service["id"] == "app"
    assert service["public"] is True
    serialized = json.dumps(result)
    for forbidden in (
        "must-not-leak",
        "must-not-cross-the-boundary",
        "/srv/private",
        "172.30.0.7",
        "rx_bytes",
        "Mounts",
        "NetworkSettings",
        "Config",
    ):
        assert forbidden not in serialized


def test_multi_service_runtime_reports_catalog_roles_and_degradation() -> None:
    catalog = HubCatalog.load()
    app = catalog.get("nextcloud")
    assert app is not None and app.bundle is not None
    containers: list[dict[str, Any]] = []
    inspected: dict[str, dict[str, Any]] = {}
    stats: dict[str, dict[str, Any]] = {}
    for index, service in enumerate(app.bundle.services, start=10):
        container_id = format(index, "x") * 64
        public = service.id == app.bundle.public_service
        labels = {
            "sh.echo.hub.managed": "true",
            "sh.echo.hub.bundle-app-id": app.id,
            "sh.echo.hub.bundle-service": service.id,
            "sh.echo.hub.catalog-digest": "3" * 64,
            "sh.echo.hub.plan-id": "4" * 64,
            "sh.echo.hub.package-digest": app.bundle.digest,
            "sh.echo.hub.bundle-digest": app.bundle.digest,
            "sh.echo.hub.bundle-version": app.version,
            "sh.echo.hub.version": app.version,
        }
        if public:
            labels["sh.echo.hub.app-id"] = app.id
        containers.append(
            {
                "Id": container_id,
                "Names": [f"/echo-hub-{app.id}" if public else f"/echo-hub-{app.id}--{service.id}"],
                "Labels": labels,
            }
        )
        inspected[container_id] = _running_inspect(
            health="unhealthy" if service.id == "cache" else "healthy"
        )
        stats[container_id] = _stats(current=100 + index, previous=100)
    engine = _RuntimeEngine(containers, inspected, stats)

    result = HubRuntimeInspector(catalog, engine).inspect(app.id)

    assert result["status"] == "degraded"
    assert result["summary"]["serviceCount"] == len(app.bundle.services)
    assert result["summary"]["runningServices"] == len(app.bundle.services)
    assert [service["id"] for service in result["services"]] == [
        service.id for service in app.bundle.services
    ]
    assert (
        next(service for service in result["services"] if service["id"] == "cache")["health"]
        == "unhealthy"
    )
    assert sum(service["public"] for service in result["services"]) == 1


def test_runtime_fails_closed_before_inspect_for_spoofed_or_duplicate_identity() -> None:
    catalog = HubCatalog.load()
    app = catalog.get("jellyfin")
    assert app is not None
    first = _single_container(app)
    duplicate = {**first, "Id": "b" * 64}
    engine = _RuntimeEngine([first, duplicate], {}, {})

    result = HubRuntimeInspector(catalog, engine).inspect(app.id)

    assert result["status"] == "unavailable"
    assert result["services"] == []
    assert engine.inspect_calls == []
    assert engine.stats_calls == []


def test_runtime_contract_rejects_extra_fields_that_could_carry_secrets() -> None:
    value = {
        "schema": "echo.hub.runtime.v1",
        "status": "unavailable",
        "summary": {
            "serviceCount": 0,
            "runningServices": 0,
            "healthyServices": 0,
            "restartCount": 0,
            "cpuPercent": None,
            "memoryUsageBytes": None,
            "memoryLimitBytes": None,
            "pids": None,
        },
        "services": [],
        "logs": "token=secret",
    }

    with pytest.raises(ValueError, match="fields"):
        validate_hub_runtime(value)
