"""Sanitized runtime health projection for catalog-owned Hub applications.

The privileged Docker sidecar may inspect containers and read one-shot stats,
but the unprivileged appliance receives only this bounded contract.  Raw
inspect payloads, logs, environment variables, mounts, network addresses and
host paths never cross the sidecar boundary.
"""

from __future__ import annotations

import concurrent.futures
import math
import re
from typing import Any, Protocol

from appliance.hub.catalog import HubApp, HubCatalog

HUB_RUNTIME_SCHEMA = "echo.hub.runtime.v1"
_CONTAINER_ID = re.compile(r"^[0-9a-f]{12,64}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_VERSION = re.compile(r"^[0-9][0-9A-Za-z.+-]{0,31}$")
_APP_STATUSES = frozenset(
    {"healthy", "degraded", "starting", "stopped", "not-installed", "unavailable"}
)
_CONTAINER_STATES = frozenset(
    {"created", "running", "paused", "restarting", "removing", "exited", "dead", "unknown"}
)
_HEALTH_STATUSES = frozenset({"healthy", "unhealthy", "starting", "not-configured", "unknown"})
_MAX_SAFE_INTEGER = 2**53 - 1


class HubRuntimeEngine(Protocol):
    def list_containers(self, include_stopped: bool = True) -> list[dict[str, Any]]: ...

    def inspect_container(self, container_id: str) -> dict[str, Any] | None: ...

    def container_stats(self, container_id: str) -> dict[str, Any]: ...


def empty_hub_runtime(status: str) -> dict[str, Any]:
    if status not in {"not-installed", "unavailable"}:
        raise ValueError("empty Hub runtime status is invalid")
    return {
        "schema": HUB_RUNTIME_SCHEMA,
        "status": status,
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
    }


def _bounded_int(value: Any, *, maximum: int = 2**63 - 1) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        return None
    return value


def _resource_stats(value: Any) -> dict[str, int | float | None]:
    if not isinstance(value, dict):
        return {
            "cpuPercent": None,
            "memoryUsageBytes": None,
            "memoryLimitBytes": None,
            "pids": None,
        }
    cpu = value.get("cpu_stats")
    previous_cpu = value.get("precpu_stats")
    cpu_percent: float | None = None
    if isinstance(cpu, dict) and isinstance(previous_cpu, dict):
        current_usage = cpu.get("cpu_usage")
        previous_usage = previous_cpu.get("cpu_usage")
        current_total = (
            _bounded_int(current_usage.get("total_usage"))
            if isinstance(current_usage, dict)
            else None
        )
        previous_total = (
            _bounded_int(previous_usage.get("total_usage"))
            if isinstance(previous_usage, dict)
            else None
        )
        current_system = _bounded_int(cpu.get("system_cpu_usage"))
        previous_system = _bounded_int(previous_cpu.get("system_cpu_usage"))
        online_cpus = _bounded_int(cpu.get("online_cpus"), maximum=4096)
        if not online_cpus and isinstance(current_usage, dict):
            per_cpu = current_usage.get("percpu_usage")
            online_cpus = len(per_cpu) if isinstance(per_cpu, list) else None
        if (
            current_total is not None
            and previous_total is not None
            and current_system is not None
            and previous_system is not None
            and online_cpus
        ):
            cpu_delta = current_total - previous_total
            system_delta = current_system - previous_system
            if cpu_delta >= 0 and system_delta > 0:
                candidate = (cpu_delta / system_delta) * online_cpus * 100
                if math.isfinite(candidate) and 0 <= candidate <= online_cpus * 100:
                    cpu_percent = round(candidate, 1)

    memory = value.get("memory_stats")
    memory_usage: int | None = None
    memory_limit: int | None = None
    if isinstance(memory, dict):
        memory_usage = _bounded_int(memory.get("usage"))
        memory_limit = _bounded_int(memory.get("limit"))
        if memory_limit == 0:
            memory_limit = None
    pids_stats = value.get("pids_stats")
    pids = (
        _bounded_int(pids_stats.get("current"), maximum=1_048_576)
        if isinstance(pids_stats, dict)
        else None
    )
    return {
        "cpuPercent": cpu_percent,
        "memoryUsageBytes": memory_usage,
        "memoryLimitBytes": memory_limit,
        "pids": pids,
    }


def _container_name(app: HubApp, service_id: str) -> str:
    if app.bundle is None or service_id == app.bundle.public_service:
        return f"echo-hub-{app.id}"
    return f"echo-hub-{app.id}--{service_id}"


def _identity_is_valid(
    app: HubApp,
    container: dict[str, Any],
    *,
    service_id: str,
) -> bool:
    labels = container.get("Labels")
    names = container.get("Names")
    container_id = str(container.get("Id") or "")
    if (
        not isinstance(labels, dict)
        or not isinstance(names, list)
        or _CONTAINER_ID.fullmatch(container_id) is None
        or labels.get("sh.echo.hub.managed") != "true"
        or _SHA256.fullmatch(str(labels.get("sh.echo.hub.catalog-digest") or "")) is None
        or _SHA256.fullmatch(str(labels.get("sh.echo.hub.plan-id") or "")) is None
        or _SHA256.fullmatch(str(labels.get("sh.echo.hub.package-digest") or "")) is None
        or _VERSION.fullmatch(str(labels.get("sh.echo.hub.version") or "")) is None
        or f"/{_container_name(app, service_id)}" not in names
    ):
        return False
    if app.bundle is None:
        return labels.get("sh.echo.hub.app-id") == app.id
    return (
        labels.get("sh.echo.hub.bundle-app-id") == app.id
        and labels.get("sh.echo.hub.bundle-service") == service_id
        and _SHA256.fullmatch(str(labels.get("sh.echo.hub.bundle-digest") or "")) is not None
        and _VERSION.fullmatch(str(labels.get("sh.echo.hub.bundle-version") or "")) is not None
        and (service_id != app.bundle.public_service or labels.get("sh.echo.hub.app-id") == app.id)
    )


def owned_hub_services(
    app: HubApp,
    containers: list[dict[str, Any]],
) -> list[tuple[Any, dict[str, Any]]]:
    """Return the complete catalog-owned service set or fail closed.

    Lifecycle control and runtime inspection deliberately share this identity
    boundary.  A duplicate, missing, renamed or relabelled container therefore
    cannot be inspected *or* controlled as part of a Hub application.
    """
    if app.package is not None:
        candidates = [
            item
            for item in containers
            if isinstance(item, dict)
            and isinstance(item.get("Labels"), dict)
            and item["Labels"].get("sh.echo.hub.app-id") == app.id
        ]
        if len(candidates) != 1 or not _identity_is_valid(app, candidates[0], service_id="app"):
            return []
        return [(None, candidates[0])]
    bundle = app.bundle
    if bundle is None:
        return []
    candidates = [
        item
        for item in containers
        if isinstance(item, dict)
        and isinstance(item.get("Labels"), dict)
        and item["Labels"].get("sh.echo.hub.bundle-app-id") == app.id
    ]
    by_service: dict[str, dict[str, Any]] = {}
    plan_ids: set[str] = set()
    bundle_digests: set[str] = set()
    bundle_versions: set[str] = set()
    expected = {service.id for service in bundle.services}
    for container in candidates:
        labels = container.get("Labels")
        if not isinstance(labels, dict):
            return []
        service_id = str(labels.get("sh.echo.hub.bundle-service") or "")
        if (
            service_id not in expected
            or service_id in by_service
            or not _identity_is_valid(app, container, service_id=service_id)
        ):
            return []
        by_service[service_id] = container
        plan_ids.add(str(labels.get("sh.echo.hub.plan-id") or ""))
        bundle_digests.add(str(labels.get("sh.echo.hub.bundle-digest") or ""))
        bundle_versions.add(str(labels.get("sh.echo.hub.bundle-version") or ""))
    if (
        set(by_service) != expected
        or len(plan_ids) != 1
        or len(bundle_digests) != 1
        or len(bundle_versions) != 1
    ):
        return []
    return [(service, by_service[service.id]) for service in bundle.services]


def _service_state(inspected: dict[str, Any]) -> tuple[str, str, int, bool, int | None]:
    state = inspected.get("State")
    if not isinstance(state, dict):
        return "unknown", "unknown", 0, False, None
    raw_status = str(state.get("Status") or "unknown").casefold()
    status = raw_status if raw_status in _CONTAINER_STATES else "unknown"
    health_value = state.get("Health")
    if isinstance(health_value, dict):
        raw_health = str(health_value.get("Status") or "unknown").casefold()
        health = raw_health if raw_health in _HEALTH_STATUSES else "unknown"
    else:
        health = "not-configured"
    restart_count = _bounded_int(inspected.get("RestartCount"), maximum=1_000_000) or 0
    oom_killed = state.get("OOMKilled") is True
    raw_exit = state.get("ExitCode")
    exit_code = raw_exit if isinstance(raw_exit, int) and not isinstance(raw_exit, bool) else None
    if exit_code is not None and not -(2**31) <= exit_code <= 2**31 - 1:
        exit_code = None
    return status, health, restart_count, oom_killed, exit_code


def _overall_status(services: list[dict[str, Any]]) -> str:
    if any(
        service["health"] == "unhealthy"
        or service["state"] in {"dead", "paused", "removing", "unknown"}
        or service["oomKilled"]
        or (service["state"] == "exited" and service["exitCode"] not in {None, 0})
        for service in services
    ):
        return "degraded"
    if any(
        service["state"] in {"created", "restarting"} or service["health"] == "starting"
        for service in services
    ):
        return "starting"
    running = [service for service in services if service["state"] == "running"]
    if not running:
        return "stopped"
    if len(running) != len(services):
        return "degraded"
    if all(service["health"] in {"healthy", "not-configured"} for service in services):
        return "healthy"
    return "degraded"


class HubRuntimeInspector:
    def __init__(self, catalog: HubCatalog, docker: HubRuntimeEngine) -> None:
        self.catalog = catalog
        self.docker = docker

    def inspect(self, app_id: str) -> dict[str, Any]:
        app = self.catalog.get(app_id)
        if app is None:
            raise KeyError(app_id)
        containers = self.docker.list_containers(include_stopped=True)
        owned = owned_hub_services(app, containers)
        if not owned:
            has_candidate = any(
                isinstance(item, dict)
                and isinstance(item.get("Labels"), dict)
                and (
                    item["Labels"].get("sh.echo.hub.app-id") == app.id
                    or item["Labels"].get("sh.echo.hub.bundle-app-id") == app.id
                )
                for item in containers
            )
            return empty_hub_runtime("unavailable" if has_candidate else "not-installed")

        services: list[dict[str, Any]] = []
        running_stats: list[tuple[int, str]] = []
        for definition, container in owned:
            container_id = str(container.get("Id") or "")
            inspected = self.docker.inspect_container(container_id)
            if not isinstance(inspected, dict):
                return empty_hub_runtime("unavailable")
            state, health, restart_count, oom_killed, exit_code = _service_state(inspected)
            resources = {
                "cpuPercent": None,
                "memoryUsageBytes": None,
                "memoryLimitBytes": None,
                "pids": None,
            }
            service_id = definition.id if definition is not None else "app"
            role = definition.role if definition is not None else "app"
            public = app.bundle is None or service_id == app.bundle.public_service
            services.append(
                {
                    "id": service_id,
                    "role": role,
                    "public": public,
                    "state": state,
                    "health": health,
                    "restartCount": restart_count,
                    "oomKilled": oom_killed,
                    "exitCode": exit_code,
                    **resources,
                }
            )
            if state == "running":
                running_stats.append((len(services) - 1, container_id))

        def collect_stats(target: tuple[int, str]) -> tuple[int, dict[str, Any] | None]:
            index, container_id = target
            try:
                return index, _resource_stats(self.docker.container_stats(container_id))
            except (OSError, RuntimeError):
                return index, None

        if running_stats:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=min(8, len(running_stats)),
                thread_name_prefix="hub-stats",
            ) as pool:
                for index, resources in pool.map(collect_stats, running_stats):
                    if resources is not None:
                        services[index].update(resources)

        cpu_values = [
            service["cpuPercent"] for service in services if service["cpuPercent"] is not None
        ]
        memory_values = [
            service["memoryUsageBytes"]
            for service in services
            if service["memoryUsageBytes"] is not None
        ]
        limit_values = [
            service["memoryLimitBytes"]
            for service in services
            if service["memoryLimitBytes"] is not None
        ]
        pids_values = [service["pids"] for service in services if service["pids"] is not None]
        return {
            "schema": HUB_RUNTIME_SCHEMA,
            "status": _overall_status(services),
            "summary": {
                "serviceCount": len(services),
                "runningServices": sum(service["state"] == "running" for service in services),
                "healthyServices": sum(
                    service["state"] == "running"
                    and service["health"] in {"healthy", "not-configured"}
                    for service in services
                ),
                "restartCount": sum(service["restartCount"] for service in services),
                "cpuPercent": round(sum(cpu_values), 1) if cpu_values else None,
                "memoryUsageBytes": sum(memory_values) if memory_values else None,
                "memoryLimitBytes": sum(limit_values) if limit_values else None,
                "pids": sum(pids_values) if pids_values else None,
            },
            "services": services,
        }


def validate_hub_runtime(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"schema", "status", "summary", "services"}:
        raise ValueError("Hub runtime fields are invalid")
    if value.get("schema") != HUB_RUNTIME_SCHEMA or value.get("status") not in _APP_STATUSES:
        raise ValueError("Hub runtime identity is invalid")
    summary = value.get("summary")
    services = value.get("services")
    summary_fields = {
        "serviceCount",
        "runningServices",
        "healthyServices",
        "restartCount",
        "cpuPercent",
        "memoryUsageBytes",
        "memoryLimitBytes",
        "pids",
    }
    if not isinstance(summary, dict) or set(summary) != summary_fields:
        raise ValueError("Hub runtime summary is invalid")
    if not isinstance(services, list) or len(services) > 64:
        raise ValueError("Hub runtime services are invalid")
    for key in ("serviceCount", "runningServices", "healthyServices"):
        if _bounded_int(summary.get(key), maximum=64) is None:
            raise ValueError("Hub runtime service count is invalid")
    if summary["serviceCount"] != len(services):
        raise ValueError("Hub runtime service count does not match")
    for key, maximum in (
        ("restartCount", 64_000_000),
        ("memoryUsageBytes", _MAX_SAFE_INTEGER),
        ("memoryLimitBytes", _MAX_SAFE_INTEGER),
        ("pids", 67_108_864),
    ):
        candidate = summary.get(key)
        if candidate is not None and _bounded_int(candidate, maximum=maximum) is None:
            raise ValueError(f"Hub runtime {key} is invalid")
    cpu = summary.get("cpuPercent")
    if cpu is not None and (
        isinstance(cpu, bool)
        or not isinstance(cpu, (int, float))
        or not math.isfinite(cpu)
        or not 0 <= cpu <= 409_600
    ):
        raise ValueError("Hub runtime CPU is invalid")
    service_fields = {
        "id",
        "role",
        "public",
        "state",
        "health",
        "restartCount",
        "oomKilled",
        "exitCode",
        "cpuPercent",
        "memoryUsageBytes",
        "memoryLimitBytes",
        "pids",
    }
    if (
        (value["status"] in {"not-installed", "unavailable"} and services)
        or (value["status"] not in {"not-installed", "unavailable"} and not services)
        or summary["runningServices"] > summary["serviceCount"]
        or summary["healthyServices"] > summary["runningServices"]
    ):
        raise ValueError("Hub runtime status is inconsistent")
    for service in services:
        if not isinstance(service, dict) or set(service) != service_fields:
            raise ValueError("Hub runtime service fields are invalid")
        if (
            not isinstance(service.get("id"), str)
            or re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", service["id"]) is None
            or service.get("role") not in {"app", "database", "cache", "worker"}
            or not isinstance(service.get("public"), bool)
            or service.get("state") not in _CONTAINER_STATES
            or service.get("health") not in _HEALTH_STATUSES
            or _bounded_int(service.get("restartCount"), maximum=1_000_000) is None
            or not isinstance(service.get("oomKilled"), bool)
        ):
            raise ValueError("Hub runtime service identity is invalid")
        exit_code = service.get("exitCode")
        if exit_code is not None and (
            isinstance(exit_code, bool)
            or not isinstance(exit_code, int)
            or not -(2**31) <= exit_code <= 2**31 - 1
        ):
            raise ValueError("Hub runtime exit code is invalid")
        for key, maximum in (
            ("memoryUsageBytes", _MAX_SAFE_INTEGER),
            ("memoryLimitBytes", _MAX_SAFE_INTEGER),
            ("pids", 1_048_576),
        ):
            candidate = service.get(key)
            if candidate is not None and _bounded_int(candidate, maximum=maximum) is None:
                raise ValueError(f"Hub runtime service {key} is invalid")
        service_cpu = service.get("cpuPercent")
        if service_cpu is not None and (
            isinstance(service_cpu, bool)
            or not isinstance(service_cpu, (int, float))
            or not math.isfinite(service_cpu)
            or not 0 <= service_cpu <= 409_600
        ):
            raise ValueError("Hub runtime service CPU is invalid")
    if summary["restartCount"] != sum(service["restartCount"] for service in services):
        raise ValueError("Hub runtime restart count does not match")
    return value


__all__ = [
    "HUB_RUNTIME_SCHEMA",
    "HubRuntimeInspector",
    "empty_hub_runtime",
    "owned_hub_services",
    "validate_hub_runtime",
]
