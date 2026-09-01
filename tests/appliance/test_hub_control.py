"""Whole-application Hub lifecycle control tests."""

from __future__ import annotations

import json
from typing import Any

import pytest

from appliance.app_registry.docker_client import DockerUnavailable
from appliance.hub.catalog import HubCatalog
from appliance.hub.docker_installer import HubDockerInstaller, HubInstallRejected
from appliance.hub.service import HubService


class _ControlEngine:
    def __init__(self, *, fail_start_service: str | None = None) -> None:
        self.catalog = HubCatalog.load()
        self.app = self.catalog.get("nextcloud")
        assert self.app is not None and self.app.bundle is not None
        self.fail_start_service = fail_start_service
        self.calls: list[tuple[str, str]] = []
        self.oom_services: set[str] = set()
        self.unhealthy_services: set[str] = set()
        self.restart_counts: dict[str, int] = {}
        self.containers: list[dict[str, Any]] = []
        self.service_by_id: dict[str, str] = {}
        hex_ids = ("a", "b", "c", "d")
        for marker, service in zip(hex_ids, self.app.bundle.services, strict=True):
            container_id = marker * 64
            public = service.id == self.app.bundle.public_service
            labels = {
                "sh.echo.hub.managed": "true",
                "sh.echo.hub.bundle-app-id": self.app.id,
                "sh.echo.hub.bundle-service": service.id,
                "sh.echo.hub.catalog-digest": self.catalog.digest,
                "sh.echo.hub.plan-id": "1" * 64,
                "sh.echo.hub.package-digest": self.app.bundle.digest,
                "sh.echo.hub.bundle-digest": self.app.bundle.digest,
                "sh.echo.hub.bundle-version": self.app.version,
                "sh.echo.hub.version": self.app.version,
            }
            if public:
                labels["sh.echo.hub.app-id"] = self.app.id
            self.containers.append(
                {
                    "Id": container_id,
                    "Image": service.image,
                    "State": "exited",
                    "Status": "Exited (0)",
                    "Names": [
                        f"/echo-hub-{self.app.id}"
                        if public
                        else f"/echo-hub-{self.app.id}--{service.id}"
                    ],
                    "Labels": labels,
                    "Ports": [],
                }
            )
            self.service_by_id[container_id] = service.id

    def list_containers(self, include_stopped: bool = True) -> list[dict[str, Any]]:
        assert include_stopped is True
        return self.containers

    def _container(self, container_id: str) -> dict[str, Any]:
        return next(item for item in self.containers if item["Id"] == container_id)

    def inspect_container(self, container_id: str) -> dict[str, Any]:
        container = self._container(container_id)
        service_id = self.service_by_id[container_id]
        running = container["State"] == "running"
        return {
            "State": {
                "Status": "running" if running else "exited",
                "Running": running,
                "OOMKilled": service_id in self.oom_services,
                "ExitCode": 137 if service_id in self.oom_services else 0,
                "Health": {
                    "Status": ("unhealthy" if service_id in self.unhealthy_services else "healthy")
                },
            },
            "RestartCount": self.restart_counts.get(service_id, 0),
        }

    def container_stats(self, container_id: str) -> dict[str, Any]:
        assert self._container(container_id)["State"] == "running"
        return {}

    def start(self, container_id: str) -> None:
        service_id = self.service_by_id[container_id]
        self.calls.append(("start", service_id))
        if service_id == self.fail_start_service:
            raise DockerUnavailable("private engine error")
        container = self._container(container_id)
        container["State"] = "running"
        container["Status"] = "Up (healthy)"

    def stop(self, container_id: str) -> None:
        service_id = self.service_by_id[container_id]
        self.calls.append(("stop", service_id))
        container = self._container(container_id)
        container["State"] = "exited"
        container["Status"] = "Exited (0)"

    def running_services(self) -> set[str]:
        return {
            self.service_by_id[item["Id"]] for item in self.containers if item["State"] == "running"
        }


def _plan(engine: _ControlEngine, operation: str) -> dict[str, Any]:
    service = HubService(engine.catalog, docker=engine, architecture="amd64")
    return getattr(service, f"plan_{operation}")(engine.app.id)


def test_bundle_control_uses_dependency_order_and_reverse_stop_order() -> None:
    engine = _ControlEngine()
    installer = HubDockerInstaller(engine.catalog, engine)
    order = list(engine.app.bundle.upgrade_policy.service_order)

    start_plan = _plan(engine, "start")
    assert start_plan["ready"] is True
    started = installer.start(
        engine.app.id,
        plan_id=start_plan["planId"],
        catalog_digest=engine.catalog.digest,
    )
    assert started["schema"] == "echo.hub.start-result.v1"
    assert started["serviceCount"] == len(order)
    assert engine.calls == [("start", service_id) for service_id in order]
    assert engine.running_services() == set(order)

    engine.calls.clear()
    restart_plan = _plan(engine, "restart")
    restarted = installer.restart(
        engine.app.id,
        plan_id=restart_plan["planId"],
        catalog_digest=engine.catalog.digest,
    )
    assert restarted["schema"] == "echo.hub.restart-result.v1"
    assert engine.calls == [
        *(("stop", service_id) for service_id in reversed(order)),
        *(("start", service_id) for service_id in order),
    ]

    engine.calls.clear()
    stop_plan = _plan(engine, "stop")
    stopped = installer.stop(
        engine.app.id,
        plan_id=stop_plan["planId"],
        catalog_digest=engine.catalog.digest,
    )
    assert stopped["schema"] == "echo.hub.stop-result.v1"
    assert engine.calls == [("stop", service_id) for service_id in reversed(order)]
    assert engine.running_services() == set()


def test_bundle_start_failure_restores_the_previous_stopped_state() -> None:
    engine = _ControlEngine(fail_start_service="app")
    installer = HubDockerInstaller(engine.catalog, engine)
    plan = _plan(engine, "start")

    with pytest.raises(HubInstallRejected, match="restore the previous state") as failure:
        installer.start(
            engine.app.id,
            plan_id=plan["planId"],
            catalog_digest=engine.catalog.digest,
        )

    assert "private engine error" not in str(failure.value)
    assert engine.running_services() == set()
    assert engine.calls == [
        ("start", "database"),
        ("start", "cache"),
        ("start", "app"),
        ("stop", "cache"),
        ("stop", "database"),
    ]


def test_control_plans_block_noop_actions_and_bind_runtime_state() -> None:
    engine = _ControlEngine()
    stopped_start = _plan(engine, "start")
    stopped_stop = _plan(engine, "stop")
    stopped_restart = _plan(engine, "restart")

    assert stopped_start["ready"] is True
    assert stopped_stop["blockers"][0]["code"] == "ALREADY_STOPPED"
    assert stopped_restart["blockers"][0]["code"] == "ALREADY_STOPPED"

    installer = HubDockerInstaller(engine.catalog, engine)
    installer.start(
        engine.app.id,
        plan_id=stopped_start["planId"],
        catalog_digest=engine.catalog.digest,
    )
    running_start = _plan(engine, "start")
    running_stop = _plan(engine, "stop")
    running_restart = _plan(engine, "restart")
    assert running_start["blockers"][0]["code"] == "ALREADY_RUNNING"
    assert running_stop["ready"] is True
    assert running_restart["ready"] is True
    assert stopped_start["planId"] != running_start["planId"]


def test_app_detail_derives_bounded_incidents_without_reading_logs() -> None:
    engine = _ControlEngine()
    start_plan = _plan(engine, "start")
    HubDockerInstaller(engine.catalog, engine).start(
        engine.app.id,
        plan_id=start_plan["planId"],
        catalog_digest=engine.catalog.digest,
    )
    cache_id = next(
        container_id
        for container_id, service_id in engine.service_by_id.items()
        if service_id == "cache"
    )
    engine._container(cache_id)["State"] = "exited"
    engine.oom_services.add("cache")
    engine.restart_counts["cache"] = 4

    detail = HubService(
        engine.catalog,
        docker=engine,
        architecture="amd64",
    ).app_detail(engine.app.id)

    assert detail["diagnostics"]["schema"] == "echo.hub.diagnostics.v1"
    assert detail["diagnostics"]["status"] == "attention"
    assert {
        (incident["code"], incident["serviceId"], incident["recovery"])
        for incident in detail["diagnostics"]["incidents"]
    } >= {
        ("OOM_KILLED", "cache", "restart"),
        ("RESTART_LOOP", "cache", "restart"),
        ("CRASHED", "cache", "restart"),
    }
    serialized = json.dumps(detail["diagnostics"])
    assert "logs" not in serialized.casefold()
    assert "private engine error" not in serialized
