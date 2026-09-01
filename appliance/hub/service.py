"""Read-only Echo Hub catalog projection and deterministic install plans."""

from __future__ import annotations

import hashlib
import json
import platform
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from appliance.app_registry.docker_client import DOCKER_STORAGE_SCHEMA, DockerUnavailable
from appliance.hub.catalog import HubApp, HubCatalog
from appliance.hub.runtime import (
    HubRuntimeInspector,
    empty_hub_runtime,
    validate_hub_runtime,
)

PLAN_SCHEMA = "echo.hub.install-plan.v1"
UPDATE_PLAN_SCHEMA = "echo.hub.update-plan.v1"
UNINSTALL_PLAN_SCHEMA = "echo.hub.uninstall-plan.v1"
START_PLAN_SCHEMA = "echo.hub.start-plan.v1"
STOP_PLAN_SCHEMA = "echo.hub.stop-plan.v1"
RESTART_PLAN_SCHEMA = "echo.hub.restart-plan.v1"
DIAGNOSTICS_SCHEMA = "echo.hub.diagnostics.v1"
CATALOG_RESPONSE_SCHEMA = "echo.hub.catalog-response.v1"
RESOURCE_PREFLIGHT_SCHEMA = "echo.hub.resource-preflight.v1"
IMAGE_STORAGE_MIN_RESERVE_BYTES = 512 * 1024 * 1024
IMAGE_STORAGE_EXPANSION_MULTIPLIER = 3
_LEGACY_LABEL_NAMESPACE = "sh.octo" + "pus"
_HUB_APP_LABELS = (
    "sh.echo.hub.app-id",
    f"{_LEGACY_LABEL_NAMESPACE}.hub.app-id",
)
_VERSION = re.compile(r"^[0-9][0-9A-Za-z.+-]{0,31}$")
_PROVIDER_LABELS = {"sh.echo.hub.lan-discovery-provider": "lan-discovery"}
_ARCHITECTURE_ALIASES = {
    "x86_64": "amd64",
    "amd64": "amd64",
    "aarch64": "arm64",
    "arm64": "arm64",
}


class DockerInventory(Protocol):
    def list_containers(self, include_stopped: bool = True) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class HubRuntimeSnapshot:
    available: bool
    error: str | None
    by_app_id: dict[str, dict[str, Any]]
    occupied_ports: frozenset[tuple[int, str]]
    port_counts: dict[tuple[int, str], int] = field(default_factory=dict)
    providers: frozenset[str] = frozenset()
    docker_capacity: dict[str, Any] = field(default_factory=dict)


class HubService:
    def __init__(
        self,
        catalog: HubCatalog,
        *,
        docker: DockerInventory | None = None,
        architecture: str | None = None,
        nas_root: str | Path | None = None,
        docker_capacity_provider: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self.catalog = catalog
        self._docker = docker
        machine = (architecture or platform.machine()).strip().lower()
        self.architecture = _ARCHITECTURE_ALIASES.get(machine, machine or "unknown")
        self._nas_root = Path(nas_root) if nas_root is not None else None
        self._docker_capacity_provider = docker_capacity_provider

    @staticmethod
    def _unavailable_docker_capacity(status: str = "unavailable") -> dict[str, Any]:
        return {
            "schema": DOCKER_STORAGE_SCHEMA,
            "status": status,
            "totalBytes": None,
            "freeBytes": None,
            "usedPercent": None,
        }

    def _docker_capacity(self) -> dict[str, Any]:
        provider = self._docker_capacity_provider
        if provider is None and self._docker is not None:
            candidate = getattr(self._docker, "hub_storage_capacity", None)
            if callable(candidate):
                provider = candidate
        if provider is None:
            return self._unavailable_docker_capacity()
        try:
            value = provider()
        except (DockerUnavailable, OSError):
            return self._unavailable_docker_capacity()
        if not isinstance(value, dict) or set(value) != {
            "schema",
            "status",
            "totalBytes",
            "freeBytes",
            "usedPercent",
        }:
            return self._unavailable_docker_capacity()
        status = value.get("status")
        total = value.get("totalBytes")
        free = value.get("freeBytes")
        used_percent = value.get("usedPercent")
        if value.get("schema") != DOCKER_STORAGE_SCHEMA or status not in {
            "observed",
            "unavailable",
            "mismatch",
        }:
            return self._unavailable_docker_capacity()
        if status == "observed":
            if (
                not isinstance(total, int)
                or isinstance(total, bool)
                or total <= 0
                or not isinstance(free, int)
                or isinstance(free, bool)
                or not 0 <= free <= total
                or not isinstance(used_percent, (int, float))
                or isinstance(used_percent, bool)
                or not 0 <= used_percent <= 100
            ):
                return self._unavailable_docker_capacity()
        elif total is not None or free is not None or used_percent is not None:
            return self._unavailable_docker_capacity()
        return value

    @staticmethod
    def _app_id(container: dict[str, Any]) -> str | None:
        labels = container.get("Labels") or {}
        if not isinstance(labels, dict):
            return None
        for label in _HUB_APP_LABELS:
            value = str(labels.get(label) or "").strip()
            if value:
                return value
        return None

    def runtime_snapshot(self) -> HubRuntimeSnapshot:
        if self._docker is None:
            return HubRuntimeSnapshot(
                False,
                "docker inventory unavailable",
                {},
                frozenset(),
                docker_capacity=self._unavailable_docker_capacity(),
            )
        try:
            containers = self._docker.list_containers(include_stopped=True)
        except DockerUnavailable as exc:
            return HubRuntimeSnapshot(
                False,
                str(exc),
                {},
                frozenset(),
                docker_capacity=self._unavailable_docker_capacity(),
            )
        by_app_id: dict[str, dict[str, Any]] = {}
        occupied_ports: set[tuple[int, str]] = set()
        port_counts: dict[tuple[int, str], int] = {}
        providers: set[str] = set()
        for container in containers:
            labels = container.get("Labels") or {}
            if not isinstance(labels, dict):
                labels = {}
            state = str(container.get("State") or "").strip().lower()
            status = str(container.get("Status") or "").strip().lower()
            if state == "running" and "(healthy)" in status:
                providers.update(
                    provider
                    for label, provider in _PROVIDER_LABELS.items()
                    if labels.get(label) == "true"
                )
            container_ports: set[tuple[int, str]] = set()
            for port in container.get("Ports") or []:
                if not isinstance(port, dict):
                    continue
                public_port = port.get("PublicPort")
                protocol = str(port.get("Type") or "").lower()
                if (
                    isinstance(public_port, int)
                    and not isinstance(public_port, bool)
                    and 1 <= public_port <= 65535
                    and protocol in {"tcp", "udp"}
                ):
                    key = (public_port, protocol)
                    occupied_ports.add(key)
                    port_counts[key] = port_counts.get(key, 0) + 1
                    container_ports.add(key)
            app_id = self._app_id(container)
            if app_id is None:
                continue
            if app_id in by_app_id:
                by_app_id[app_id]["ambiguous"] = True
                continue
            container_id = str(container.get("Id") or "")
            names = container.get("Names") or []
            if not isinstance(names, list):
                names = []
            by_app_id[app_id] = {
                "installed": True,
                "containerId": container_id[:12] if len(container_id) >= 12 else None,
                "state": str(container.get("State") or "unknown"),
                "status": str(container.get("Status") or ""),
                "image": str(container.get("Image") or ""),
                "ambiguous": False,
                "_managed": labels.get("sh.echo.hub.managed") == "true",
                "_canonicalName": any(str(name) == f"/echo-hub-{app_id}" for name in names),
                "_catalogDigest": str(labels.get("sh.echo.hub.catalog-digest") or ""),
                "_planId": str(labels.get("sh.echo.hub.plan-id") or ""),
                "_packageDigest": str(labels.get("sh.echo.hub.package-digest") or ""),
                "_bundleVersion": str(labels.get("sh.echo.hub.bundle-version") or ""),
                "_version": str(
                    labels.get("sh.echo.hub.version")
                    or labels.get("sh.echo.hub.bundle-version")
                    or ""
                ),
                "_ports": frozenset(container_ports),
            }
        return HubRuntimeSnapshot(
            True,
            None,
            by_app_id,
            frozenset(occupied_ports),
            port_counts,
            frozenset(providers),
            self._docker_capacity(),
        )

    @staticmethod
    def _public_installation(installed: dict[str, Any] | None) -> dict[str, Any]:
        if installed:
            public = {
                key: value
                for key, value in installed.items()
                if key != "ambiguous" and not key.startswith("_")
            }
            version = str(installed.get("_version") or "")
            public["version"] = version if _VERSION.fullmatch(version) else None
            return public
        return {
            "installed": False,
            "containerId": None,
            "state": "not-installed",
            "status": "",
            "image": None,
            "version": None,
        }

    @staticmethod
    def _managed_installation(installed: dict[str, Any] | None) -> bool:
        if not installed:
            return False
        return (
            installed.get("_managed") is True
            and installed.get("_canonicalName") is True
            and re.fullmatch(r"[0-9a-f]{64}", str(installed.get("_catalogDigest") or ""))
            is not None
            and re.fullmatch(r"[0-9a-f]{64}", str(installed.get("_planId") or "")) is not None
        )

    def _app_runtime(
        self,
        app: HubApp,
        installed: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if installed is None:
            return empty_hub_runtime("not-installed")
        if installed.get("ambiguous") or not self._managed_installation(installed):
            return empty_hub_runtime("unavailable")
        provider = getattr(self._docker, "hub_app_runtime", None)
        try:
            if callable(provider):
                return validate_hub_runtime(provider(app.id))
            if self._docker is not None and all(
                callable(getattr(self._docker, method, None))
                for method in ("list_containers", "inspect_container", "container_stats")
            ):
                return validate_hub_runtime(
                    HubRuntimeInspector(self.catalog, self._docker).inspect(app.id)
                )
        except (DockerUnavailable, OSError, RuntimeError, ValueError):
            return empty_hub_runtime("unavailable")
        return empty_hub_runtime("unavailable")

    @staticmethod
    def _diagnostics(runtime: dict[str, Any]) -> dict[str, Any]:
        runtime_status = runtime["status"]
        incidents: list[dict[str, str]] = []
        if runtime_status not in {"not-installed", "unavailable", "stopped"}:
            for service in runtime["services"]:
                service_id = service["id"]
                if service["oomKilled"]:
                    incidents.append(
                        {
                            "code": "OOM_KILLED",
                            "severity": "critical",
                            "serviceId": service_id,
                            "recovery": "restart",
                        }
                    )
                if service["health"] == "unhealthy":
                    incidents.append(
                        {
                            "code": "HEALTHCHECK_FAILED",
                            "severity": "error",
                            "serviceId": service_id,
                            "recovery": "restart",
                        }
                    )
                if service["state"] == "restarting" or service["restartCount"] >= 3:
                    incidents.append(
                        {
                            "code": "RESTART_LOOP",
                            "severity": "warning",
                            "serviceId": service_id,
                            "recovery": "restart",
                        }
                    )
                if service["state"] == "dead" or (
                    service["state"] == "exited" and service["exitCode"] not in {None, 0}
                ):
                    incidents.append(
                        {
                            "code": "CRASHED",
                            "severity": "error",
                            "serviceId": service_id,
                            "recovery": "restart",
                        }
                    )
                elif runtime_status == "degraded" and service["state"] in {
                    "created",
                    "exited",
                }:
                    incidents.append(
                        {
                            "code": "SERVICE_STOPPED",
                            "severity": "warning",
                            "serviceId": service_id,
                            "recovery": "restart",
                        }
                    )
                if service["state"] in {"paused", "removing", "unknown"}:
                    incidents.append(
                        {
                            "code": "STATE_UNAVAILABLE",
                            "severity": "error",
                            "serviceId": service_id,
                            "recovery": "inspect",
                        }
                    )
        status = {
            "healthy": "ok",
            "stopped": "stopped",
            "not-installed": "not-installed",
            "unavailable": "unavailable",
        }.get(runtime_status, "attention" if incidents else "observing")
        return {
            "schema": DIAGNOSTICS_SCHEMA,
            "status": status,
            "incidents": incidents[:64],
        }

    @staticmethod
    def _update_available(app: HubApp, installed: dict[str, Any] | None) -> bool:
        if not installed or app.integration_status != "available":
            return False
        artifact = app.package or app.bundle
        if artifact is None:
            return False
        package_digest = str(installed.get("_packageDigest") or "")
        desired_image = (
            app.package.image
            if app.package is not None
            else next(
                service.image
                for service in app.bundle.services
                if service.id == app.bundle.public_service
            )
        )
        return str(installed.get("image") or "") != desired_image or (
            bool(package_digest) and package_digest != artifact.digest
        )

    @staticmethod
    def _install_artifact(app: HubApp) -> Any | None:
        if app.integration_status != "available":
            return None
        return app.package or app.bundle

    @staticmethod
    def _artifact_ports(app: HubApp) -> tuple[Any, ...]:
        artifact = HubService._install_artifact(app)
        if artifact is None:
            return ()
        if app.package is not None:
            return app.package.ports
        return next(
            service.ports
            for service in app.bundle.services
            if service.id == app.bundle.public_service
        )

    @staticmethod
    def _artifact_providers(app: HubApp) -> tuple[str, ...]:
        artifact = HubService._install_artifact(app)
        if artifact is None or app.bundle is None:
            return ()
        return app.bundle.providers

    def _app_view(self, app: HubApp, snapshot: HubRuntimeSnapshot) -> dict[str, Any]:
        installed = snapshot.by_app_id.get(app.id)
        artifact = self._install_artifact(app)
        blockers: list[str] = []
        if artifact is None:
            blockers.append("PACKAGE_NOT_PUBLISHED")
        elif self.architecture not in artifact.architectures:
            blockers.append("ARCHITECTURE_UNSUPPORTED")
        if not set(self._artifact_providers(app)) <= snapshot.providers:
            blockers.append("REQUIRED_PROVIDER_UNAVAILABLE")
        if artifact is not None and any(
            (port.host, port.protocol) in snapshot.occupied_ports
            for port in self._artifact_ports(app)
        ):
            blockers.append("PORT_IN_USE")
        if not snapshot.available:
            blockers.append("DOCKER_RUNTIME_UNAVAILABLE")
        elif artifact is not None and self.architecture in artifact.architectures:
            image_storage = self._image_storage_preflight(app, snapshot)
            if image_storage["status"] in {"unavailable", "mismatch"}:
                blockers.append("DOCKER_STORAGE_UNAVAILABLE")
            elif image_storage["status"] == "insufficient":
                blockers.append("DOCKER_STORAGE_INSUFFICIENT")
        if installed:
            blockers.append("ALREADY_INSTALLED")
            if installed.get("ambiguous"):
                blockers.append("INSTALLATION_AMBIGUOUS")
        public_installation = self._public_installation(installed)
        update_available = (
            self._update_available(app, installed)
            and self._managed_installation(installed)
            and not bool(installed and installed.get("ambiguous"))
        )
        return {
            **app.to_dict(),
            "installation": public_installation,
            "installable": not blockers,
            "installBlockers": blockers,
            "updateAvailable": update_available,
        }

    def _image_storage_preflight(
        self,
        app: HubApp,
        snapshot: HubRuntimeSnapshot,
    ) -> dict[str, Any]:
        attestation = (
            app.image_storage.for_architecture(self.architecture)
            if app.image_storage is not None
            else None
        )
        capacity = snapshot.docker_capacity or self._unavailable_docker_capacity()
        if attestation is None:
            return {
                "status": "unavailable",
                "downloadBytes": None,
                "blobCount": None,
                "requiredFreeBytes": None,
                "reservePolicy": "compressed-times-three-or-plus-512MiB",
                "capacity": capacity,
            }
        required_free_bytes = max(
            attestation.download_bytes * IMAGE_STORAGE_EXPANSION_MULTIPLIER,
            attestation.download_bytes + IMAGE_STORAGE_MIN_RESERVE_BYTES,
        )
        status = str(capacity.get("status") or "unavailable")
        if status == "observed":
            status = (
                "sufficient"
                if int(capacity["freeBytes"]) >= required_free_bytes
                else "insufficient"
            )
        return {
            "status": status,
            "downloadBytes": attestation.download_bytes,
            "blobCount": attestation.blob_count,
            "requiredFreeBytes": required_free_bytes,
            "reservePolicy": "compressed-times-three-or-plus-512MiB",
            "capacity": capacity,
        }

    def _nas_capacity(self, *, requested: bool) -> dict[str, Any]:
        if not requested:
            return {
                "status": "not-requested",
                "totalBytes": None,
                "freeBytes": None,
                "usedPercent": None,
            }
        if self._nas_root is None:
            return {
                "status": "unavailable",
                "totalBytes": None,
                "freeBytes": None,
                "usedPercent": None,
            }
        try:
            usage = shutil.disk_usage(self._nas_root)
        except OSError:
            return {
                "status": "unavailable",
                "totalBytes": None,
                "freeBytes": None,
                "usedPercent": None,
            }
        return {
            "status": "observed",
            "totalBytes": usage.total,
            "freeBytes": usage.free,
            "usedPercent": round((usage.used / usage.total) * 100, 1) if usage.total else 0.0,
        }

    def _resource_preflight(
        self,
        app: HubApp,
        snapshot: HubRuntimeSnapshot,
        app_view: dict[str, Any],
    ) -> dict[str, Any]:
        artifact = self._install_artifact(app)
        installed = snapshot.by_app_id.get(app.id)
        own_ports = set(installed.get("_ports") or ()) if installed else set()
        ports = []
        for port in self._artifact_ports(app):
            key = (port.host, port.protocol)
            count = snapshot.port_counts.get(key, 0)
            own_count = 1 if key in own_ports else 0
            status = "conflict" if count > own_count else "owned" if own_count else "available"
            ports.append({**port.to_dict(), "status": status})

        service_count = 0
        memory_limit_mib = 0
        pids_limit = 0
        shm_limit_mib = 0
        healthchecked_services = 0
        app_data_volumes = 0
        nas_volumes = 0
        snapshot_volumes = 0
        nas_access = "none"
        host_network = False
        one_time_credentials = 0
        if artifact is not None and app.package is not None:
            service_count = 1
            memory_limit_mib = app.package.runtime.memory_mib
            pids_limit = app.package.runtime.pids
            shm_limit_mib = app.package.runtime.shm_size_mib
            app_data_volumes = sum(volume.source == "app-data" for volume in app.package.volumes)
            nas_mounts = [volume for volume in app.package.volumes if volume.source == "nas-root"]
            nas_volumes = len(nas_mounts)
            snapshot_volumes = sum(
                volume.source == "app-data" and not volume.read_only
                for volume in app.package.volumes
            )
            if nas_mounts:
                nas_access = (
                    "read-write"
                    if any(not volume.read_only for volume in nas_mounts)
                    else "read-only"
                )
        elif artifact is not None and app.bundle is not None:
            service_count = len(app.bundle.services)
            memory_limit_mib = sum(service.runtime.memory_mib for service in app.bundle.services)
            pids_limit = sum(service.runtime.pids for service in app.bundle.services)
            shm_limit_mib = sum(service.runtime.shm_size_mib for service in app.bundle.services)
            healthchecked_services = sum(
                service.healthcheck is not None for service in app.bundle.services
            )
            app_data_volumes = sum(volume.source == "app-data" for volume in app.bundle.volumes)
            nas_names = {
                volume.name for volume in app.bundle.volumes if volume.source == "nas-data"
            }
            nas_volumes = len(nas_names)
            snapshot_volumes = len(app.bundle.upgrade_policy.snapshot_volumes)
            if nas_names:
                nas_access = (
                    "read-write"
                    if any(
                        mount.volume in nas_names and not mount.read_only
                        for service in app.bundle.services
                        for mount in service.mounts
                    )
                    else "read-only"
                )
            host_network = any(service.network_mode == "host" for service in app.bundle.services)
            one_time_credentials = sum(secret.reveal_once for secret in app.bundle.secrets)

        required_providers = list(self._artifact_providers(app))
        providers_ready = set(required_providers) <= snapshot.providers
        architecture_status = (
            "unavailable"
            if artifact is None
            else "pass"
            if self.architecture in artifact.architectures
            else "fail"
        )
        port_status = "pass" if all(port["status"] != "conflict" for port in ports) else "fail"
        notices = []
        if host_network:
            notices.append("HOST_LAN")
        if nas_access == "read-write":
            notices.append("NAS_READ_WRITE")
        elif nas_access == "read-only":
            notices.append("NAS_READ_ONLY")
        if service_count > 1:
            notices.append("MULTI_SERVICE")
        if one_time_credentials:
            notices.append("ONE_TIME_CREDENTIALS")
        nas_capacity = self._nas_capacity(requested=bool(nas_volumes))
        image_storage = self._image_storage_preflight(app, snapshot)

        return {
            "schema": RESOURCE_PREFLIGHT_SCHEMA,
            "readyForInstall": app_view["installable"],
            "blockingIssues": list(app_view["installBlockers"]),
            "checks": [
                {
                    "id": "architecture",
                    "status": architecture_status,
                    "blocking": True,
                },
                {
                    "id": "docker-runtime",
                    "status": "pass" if snapshot.available else "fail",
                    "blocking": True,
                },
                {
                    "id": "docker-storage",
                    "status": {
                        "sufficient": "pass",
                        "insufficient": "fail",
                    }.get(image_storage["status"], image_storage["status"]),
                    "blocking": True,
                },
                {"id": "ports", "status": port_status, "blocking": True},
                {
                    "id": "providers",
                    "status": "pass" if providers_ready else "fail",
                    "blocking": True,
                },
                {
                    "id": "nas-capacity",
                    "status": nas_capacity["status"],
                    "blocking": False,
                },
            ],
            "runtime": {
                "serviceCount": service_count,
                "memoryLimitMiB": memory_limit_mib,
                "pidsLimit": pids_limit,
                "shmLimitMiB": shm_limit_mib,
                "healthcheckedServices": healthchecked_services,
            },
            "network": {
                "mode": "host" if host_network else "bridge",
                "ports": ports,
                "requiredProviders": required_providers,
                "providersReady": providers_ready,
            },
            "storage": {
                "appDataVolumes": app_data_volumes,
                "nasVolumes": nas_volumes,
                "nasAccess": nas_access,
                "snapshotVolumes": snapshot_volumes,
                "nasCapacity": nas_capacity,
                "imageStorage": image_storage,
            },
            "notices": notices,
        }

    def list_catalog(
        self,
        *,
        search: str | None = None,
        category: str | None = None,
    ) -> dict[str, Any]:
        snapshot = self.runtime_snapshot()
        apps = [self._app_view(app, snapshot) for app in self.catalog.apps]
        if category:
            apps = [item for item in apps if item["category"] == category]
        if search:
            needle = search.strip().casefold()
            apps = [
                item
                for item in apps
                if needle
                in " ".join(
                    str(item[key]) for key in ("id", "name", "nameZh", "summary", "category")
                ).casefold()
            ]
        return {
            "schema": CATALOG_RESPONSE_SCHEMA,
            "version": self.catalog.version,
            "digest": self.catalog.digest,
            "publisher": {
                "id": self.catalog.publisher_id,
                "name": self.catalog.publisher_name,
            },
            "architecture": self.architecture,
            "runtime": {"available": snapshot.available, "error": snapshot.error},
            "total": len(apps),
            "apps": apps,
        }

    def app_detail(self, app_id: str) -> dict[str, Any]:
        app = self.catalog.get(app_id)
        if app is None:
            raise KeyError(app_id)
        snapshot = self.runtime_snapshot()
        app_view = self._app_view(app, snapshot)
        installed = snapshot.by_app_id.get(app.id)
        app_runtime = self._app_runtime(app, installed)
        return {
            "schema": "echo.hub.app-detail.v1",
            "catalogDigest": self.catalog.digest,
            "architecture": self.architecture,
            "runtime": {"available": snapshot.available, "error": snapshot.error},
            "appRuntime": app_runtime,
            "diagnostics": self._diagnostics(app_runtime),
            "app": app_view,
            "resourcePreflight": self._resource_preflight(app, snapshot, app_view),
        }

    def plan_install(self, app_id: str) -> dict[str, Any]:
        app = self.catalog.get(app_id)
        if app is None:
            raise KeyError(app_id)
        snapshot = self.runtime_snapshot()
        app_view = self._app_view(app, snapshot)
        blockers = [
            {"code": code, "message": self._blocker_message(code, app)}
            for code in app_view["installBlockers"]
        ]
        artifact = self._install_artifact(app)
        desired = {
            "appId": app.id,
            "architecture": self.architecture,
            "catalogDigest": self.catalog.digest,
            "package": app.package.to_dict() if app.package and artifact else None,
            "bundle": app.bundle.to_dict() if app.bundle and artifact else None,
        }
        current = app_view["installation"]
        identity = {
            "schema": PLAN_SCHEMA,
            "operation": "install",
            "current": current,
            "desired": desired,
            "blockers": [item["code"] for item in blockers],
        }
        canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        plan_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        changes: list[dict[str, Any]] = []
        if app.package is not None and artifact is not None:
            changes = [
                {"field": "image", "before": None, "after": app.package.image},
                {
                    "field": "ports",
                    "before": [],
                    "after": [item.to_dict() for item in app.package.ports],
                },
                {
                    "field": "volumes",
                    "before": [],
                    "after": [item.to_dict() for item in app.package.volumes],
                },
            ]
        elif app.bundle is not None and artifact is not None:
            changes = [
                {
                    "field": "services",
                    "before": [],
                    "after": [
                        {"id": service.id, "role": service.role, "image": service.image}
                        for service in app.bundle.services
                    ],
                },
                {
                    "field": "ports",
                    "before": [],
                    "after": [item.to_dict() for item in self._artifact_ports(app)],
                },
                {
                    "field": "networkModes",
                    "before": [],
                    "after": [
                        {"id": service.id, "mode": service.network_mode}
                        for service in app.bundle.services
                    ],
                },
                {
                    "field": "volumes",
                    "before": [],
                    "after": [item.to_dict() for item in app.bundle.volumes],
                },
                {
                    "field": "generatedSecrets",
                    "before": [],
                    "after": [
                        {"name": secret.name, "revealOnce": secret.reveal_once}
                        for secret in app.bundle.secrets
                    ],
                },
            ]
        ready = not blockers
        return {
            "schema": PLAN_SCHEMA,
            "planId": plan_id,
            "operation": "install",
            "ready": ready,
            "requiresApproval": ready,
            "approvalAction": "hub.app.install" if ready else None,
            "approvalTarget": plan_id if ready else None,
            "current": current,
            "desired": desired,
            "changes": changes,
            "blockers": blockers,
            "resourcePreflight": self._resource_preflight(app, snapshot, app_view),
        }

    def plan_uninstall(self, app_id: str) -> dict[str, Any]:
        app = self.catalog.get(app_id)
        if app is None:
            raise KeyError(app_id)
        snapshot = self.runtime_snapshot()
        installed = snapshot.by_app_id.get(app.id)
        blocker_codes: list[str] = []
        if not snapshot.available:
            blocker_codes.append("DOCKER_RUNTIME_UNAVAILABLE")
        if installed is None:
            blocker_codes.append("NOT_INSTALLED")
        elif installed.get("ambiguous"):
            blocker_codes.append("INSTALLATION_AMBIGUOUS")
        elif not self._managed_installation(installed):
            blocker_codes.append("INSTALLATION_NOT_MANAGED")
        blockers = [
            {"code": code, "message": self._blocker_message(code, app)} for code in blocker_codes
        ]
        current = self._public_installation(installed)
        desired = {
            "appId": app.id,
            "catalogDigest": self.catalog.digest,
            "containerRemoved": True,
            "dataVolumesRetained": True,
            "nasDataRetained": True,
        }
        identity = {
            "schema": UNINSTALL_PLAN_SCHEMA,
            "operation": "uninstall",
            "current": current,
            "desired": desired,
            "blockers": blocker_codes,
        }
        canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        plan_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        ready = not blockers
        return {
            "schema": UNINSTALL_PLAN_SCHEMA,
            "planId": plan_id,
            "operation": "uninstall",
            "ready": ready,
            "requiresApproval": ready,
            "approvalAction": "hub.app.uninstall" if ready else None,
            "approvalTarget": plan_id if ready else None,
            "current": current,
            "desired": desired,
            "changes": [
                {
                    "field": "container",
                    "before": current.get("containerId"),
                    "after": None,
                },
                {
                    "field": "appDataVolumes",
                    "before": "retained",
                    "after": "retained",
                },
                {"field": "nasData", "before": "unchanged", "after": "unchanged"},
            ]
            if installed
            else [],
            "blockers": blockers,
        }

    def plan_update(self, app_id: str) -> dict[str, Any]:
        app = self.catalog.get(app_id)
        if app is None:
            raise KeyError(app_id)
        snapshot = self.runtime_snapshot()
        installed = snapshot.by_app_id.get(app.id)
        artifact = self._install_artifact(app)
        blocker_codes: list[str] = []
        if not snapshot.available:
            blocker_codes.append("DOCKER_RUNTIME_UNAVAILABLE")
        if artifact is None:
            blocker_codes.append("PACKAGE_NOT_PUBLISHED")
        elif self.architecture not in artifact.architectures:
            blocker_codes.append("ARCHITECTURE_UNSUPPORTED")
        if (
            snapshot.available
            and artifact is not None
            and self.architecture in artifact.architectures
        ):
            image_storage = self._image_storage_preflight(app, snapshot)
            if image_storage["status"] in {"unavailable", "mismatch"}:
                blocker_codes.append("DOCKER_STORAGE_UNAVAILABLE")
            elif image_storage["status"] == "insufficient":
                blocker_codes.append("DOCKER_STORAGE_INSUFFICIENT")
        if not set(self._artifact_providers(app)) <= snapshot.providers:
            blocker_codes.append("REQUIRED_PROVIDER_UNAVAILABLE")
        if installed is None:
            blocker_codes.append("NOT_INSTALLED")
        elif installed.get("ambiguous"):
            blocker_codes.append("INSTALLATION_AMBIGUOUS")
        elif not self._managed_installation(installed):
            blocker_codes.append("INSTALLATION_NOT_MANAGED")
        elif not self._update_available(app, installed):
            blocker_codes.append("ALREADY_CURRENT")
        elif app.bundle is not None:
            installed_version = str(installed.get("_bundleVersion") or "")
            if re.fullmatch(r"[0-9]+(?:\.[0-9]+){0,3}", installed_version) is None:
                blocker_codes.append("UPGRADE_PATH_UNSUPPORTED")
            else:
                installed_major = int(installed_version.split(".", 1)[0])
                desired_major = int(app.bundle.upgrade_policy.application_version.split(".", 1)[0])
                if (
                    desired_major < installed_major
                    or desired_major - installed_major > app.bundle.upgrade_policy.max_major_step
                ):
                    blocker_codes.append("UPGRADE_PATH_UNSUPPORTED")
        if artifact is not None:
            current_ports = set(installed.get("_ports") or frozenset()) if installed else set()
            if any(
                snapshot.port_counts.get((port.host, port.protocol), 0)
                > (1 if (port.host, port.protocol) in current_ports else 0)
                for port in self._artifact_ports(app)
            ):
                blocker_codes.append("PORT_IN_USE")
        blockers = [
            {"code": code, "message": self._blocker_message(code, app)} for code in blocker_codes
        ]
        current = self._public_installation(installed)
        desired = {
            "appId": app.id,
            "architecture": self.architecture,
            "catalogDigest": self.catalog.digest,
            "packageDigest": artifact.digest if artifact else None,
            "package": app.package.to_dict() if app.package and artifact else None,
            "bundle": app.bundle.to_dict() if app.bundle and artifact else None,
            "appDataVolumesRetained": True,
            "nasDataRetained": True,
            "runningStatePreserved": True,
        }
        identity = {
            "schema": UPDATE_PLAN_SCHEMA,
            "operation": "update",
            "current": current,
            "desired": desired,
            "blockers": blocker_codes,
        }
        canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        plan_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        changes: list[dict[str, Any]] = []
        if artifact is not None and installed is not None:
            desired_image = (
                app.package.image
                if app.package is not None
                else next(
                    service.image
                    for service in app.bundle.services
                    if service.id == app.bundle.public_service
                )
            )
            changes = [
                {
                    "field": "image",
                    "before": current.get("image"),
                    "after": desired_image,
                },
                {
                    "field": "packageDigest",
                    "before": installed.get("_packageDigest") or None,
                    "after": artifact.digest,
                },
                {
                    "field": "appDataVolumes",
                    "before": "retained",
                    "after": "retained",
                },
                {"field": "nasData", "before": "unchanged", "after": "unchanged"},
            ]
        ready = not blockers
        return {
            "schema": UPDATE_PLAN_SCHEMA,
            "planId": plan_id,
            "operation": "update",
            "ready": ready,
            "requiresApproval": ready,
            "approvalAction": "hub.app.update" if ready else None,
            "approvalTarget": plan_id if ready else None,
            "current": current,
            "desired": desired,
            "changes": changes,
            "blockers": blockers,
        }

    @staticmethod
    def _control_runtime_identity(runtime: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": runtime["status"],
            "services": [
                {
                    "id": service["id"],
                    "state": service["state"],
                    "health": service["health"],
                    "restartCount": service["restartCount"],
                    "oomKilled": service["oomKilled"],
                    "exitCode": service["exitCode"],
                }
                for service in runtime["services"]
            ],
        }

    def _plan_control(self, app_id: str, operation: str) -> dict[str, Any]:
        app = self.catalog.get(app_id)
        if app is None:
            raise KeyError(app_id)
        schemas = {
            "start": START_PLAN_SCHEMA,
            "stop": STOP_PLAN_SCHEMA,
            "restart": RESTART_PLAN_SCHEMA,
        }
        if operation not in schemas:
            raise ValueError("unsupported Hub control operation")
        snapshot = self.runtime_snapshot()
        installed = snapshot.by_app_id.get(app.id)
        runtime = self._app_runtime(app, installed)
        blocker_codes: list[str] = []
        if not snapshot.available:
            blocker_codes.append("DOCKER_RUNTIME_UNAVAILABLE")
        if installed is None:
            blocker_codes.append("NOT_INSTALLED")
        elif installed.get("ambiguous"):
            blocker_codes.append("INSTALLATION_AMBIGUOUS")
        elif not self._managed_installation(installed):
            blocker_codes.append("INSTALLATION_NOT_MANAGED")
        if runtime["status"] in {"not-installed", "unavailable"} or any(
            service["state"] in {"paused", "removing", "unknown"} for service in runtime["services"]
        ):
            blocker_codes.append("RUNTIME_STATE_UNAVAILABLE")
        elif (
            operation == "start"
            and runtime["summary"]["runningServices"] == runtime["summary"]["serviceCount"]
        ):
            blocker_codes.append("ALREADY_RUNNING")
        elif operation in {"stop", "restart"} and runtime["summary"]["runningServices"] == 0:
            blocker_codes.append("ALREADY_STOPPED")
        blocker_codes = list(dict.fromkeys(blocker_codes))
        blockers = [
            {"code": code, "message": self._blocker_message(code, app)} for code in blocker_codes
        ]
        order = list(app.bundle.upgrade_policy.service_order) if app.bundle is not None else ["app"]
        current = {
            "installation": {
                key: self._public_installation(installed).get(key)
                for key in ("installed", "containerId", "state", "image", "version")
            },
            "runtime": self._control_runtime_identity(runtime),
        }
        desired_state = "stopped" if operation == "stop" else "running"
        desired = {
            "appId": app.id,
            "catalogDigest": self.catalog.digest,
            "state": desired_state,
            "serviceOrder": order,
            "dataVolumesRetained": True,
            "nasDataRetained": True,
        }
        identity = {
            "schema": schemas[operation],
            "operation": operation,
            "current": current,
            "desired": desired,
            "blockers": blocker_codes,
        }
        canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        plan_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        ready = not blockers
        return {
            "schema": schemas[operation],
            "planId": plan_id,
            "operation": operation,
            "ready": ready,
            "requiresApproval": ready,
            "approvalAction": f"hub.app.{operation}" if ready else None,
            "approvalTarget": plan_id if ready else None,
            "current": current,
            "desired": desired,
            "changes": [
                {
                    "field": "services",
                    "before": [
                        {"id": service["id"], "state": service["state"]}
                        for service in runtime["services"]
                    ],
                    "after": [{"id": service_id, "state": desired_state} for service_id in order],
                }
            ]
            if runtime["services"]
            else [],
            "blockers": blockers,
        }

    def plan_start(self, app_id: str) -> dict[str, Any]:
        return self._plan_control(app_id, "start")

    def plan_stop(self, app_id: str) -> dict[str, Any]:
        return self._plan_control(app_id, "stop")

    def plan_restart(self, app_id: str) -> dict[str, Any]:
        return self._plan_control(app_id, "restart")

    @staticmethod
    def _blocker_message(code: str, app: HubApp) -> str:
        return {
            "PACKAGE_NOT_PUBLISHED": f"{app.name_zh} 的受信安装包尚未发布",
            "ARCHITECTURE_UNSUPPORTED": "当前设备架构不在此应用的支持范围内",
            "DOCKER_RUNTIME_UNAVAILABLE": "Docker 受限控制服务当前不可用",
            "DOCKER_STORAGE_UNAVAILABLE": "无法核对 Docker 数据盘余量，请先检查受限控制服务的数据根挂载",
            "DOCKER_STORAGE_INSUFFICIENT": "Docker 数据盘余量不足，无法安全拉取和展开应用镜像",
            "PORT_IN_USE": "应用所需端口已被其他服务占用",
            "ALREADY_INSTALLED": "应用已经安装",
            "NOT_INSTALLED": "应用尚未安装",
            "INSTALLATION_AMBIGUOUS": "检测到多个同名受管容器，需要先在 Docker 管理页处理",
            "INSTALLATION_NOT_MANAGED": "当前容器不是由 Echo Hub 完整管理，不能自动变更",
            "ALREADY_CURRENT": "应用已经是目录中的当前版本",
            "UPGRADE_PATH_UNSUPPORTED": "当前版本不能直接升级到目录版本，需要逐个大版本迁移",
            "REQUIRED_PROVIDER_UNAVAILABLE": "当前设备的局域网发现服务尚未就绪",
            "RUNTIME_STATE_UNAVAILABLE": "无法安全确认全部受管服务，已停止自动控制",
            "ALREADY_RUNNING": "全部受管服务已经在运行",
            "ALREADY_STOPPED": "全部受管服务已经停止",
        }.get(code, code)


__all__ = [
    "CATALOG_RESPONSE_SCHEMA",
    "PLAN_SCHEMA",
    "RESOURCE_PREFLIGHT_SCHEMA",
    "RESTART_PLAN_SCHEMA",
    "START_PLAN_SCHEMA",
    "STOP_PLAN_SCHEMA",
    "UNINSTALL_PLAN_SCHEMA",
    "UPDATE_PLAN_SCHEMA",
    "HubService",
]
