"""Echo Hub catalog, install planning and authenticated API tests."""

from __future__ import annotations

import hashlib
import json
import time

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance.app_registry.docker_client import (
    DockerClient,
    DockerControlDenied,
    DockerUnavailable,
)
from appliance.approval import (
    APPROVAL_HEADER,
    INTENT_HEADER,
    HighRiskApprovalService,
    create_approval_router,
)
from appliance.audit import ApplianceAudit
from appliance.hub import HubCatalog, HubCatalogError, HubService, create_hub_router
from appliance.hub.docker_installer import HubDockerInstaller, HubInstallRejected
from appliance.hub.operations import HubOperationService, HubOperationStore
from runtime.safety.auth.identity import encode_jwt_hs256

JWT_SECRET = "echo-hub-test-secret-that-is-long-enough"


def _catalog_mapping(*, image: str | None = None) -> dict:
    package = None
    image_storage = None
    status = "integration-pending"
    note = "waiting for a trusted package"
    if image is not None:
        status = "available"
        note = "trusted single-container fixture"
        package = {
            "schema": "echo.hub.docker-package.v1",
            "image": image,
            "architectures": ["amd64", "arm64"],
            "ports": [{"container": 8080, "host": 18080, "protocol": "tcp"}],
            "volumes": [
                {
                    "source": "app-data",
                    "name": "config",
                    "target": "/config",
                    "readOnly": False,
                },
                {
                    "source": "nas-root",
                    "name": "media",
                    "target": "/media",
                    "readOnly": True,
                },
            ],
            "environment": {"TZ": "system"},
            "runtime": {
                "memoryMiB": 2048,
                "pids": 384,
                "shmSizeMiB": 128,
                "readOnlyRootfs": False,
            },
        }
        image_storage = {
            "schema": "echo.hub.image-storage.v1",
            "architectures": {
                "amd64": {"downloadBytes": 256 * 1024 * 1024, "blobCount": 8},
                "arm64": {"downloadBytes": 240 * 1024 * 1024, "blobCount": 8},
            },
        }
    return {
        "schema": "echo.hub.catalog.v1",
        "version": "test.1",
        "publisher": {"id": "echo-test", "name": "Echo Test"},
        "apps": [
            {
                "id": "demo-app",
                "name": "Demo App",
                "nameZh": "演示应用",
                "version": "1.2.3",
                "summary": "A bounded test application.",
                "category": "media",
                "icon": "media",
                "sourceUrl": "https://example.com/demo",
                "featured": True,
                "imageStorage": image_storage,
                "package": package,
                "integrationStatus": status,
                "integrationNote": note,
            }
        ],
    }


def _managed_container(
    catalog: HubCatalog,
    *,
    image: str,
    container_id: str = "2" * 64,
    state: str = "running",
    ports: list[dict] | None = None,
    package_digest: str | None = None,
) -> dict:
    labels = {
        "sh.echo.hub.managed": "true",
        "sh.echo.hub.app-id": "demo-app",
        "sh.echo.hub.catalog-digest": catalog.digest,
        "sh.echo.hub.plan-id": "3" * 64,
        "sh.echo.hub.version": "1.2.3",
    }
    if package_digest is not None:
        labels["sh.echo.hub.package-digest"] = package_digest
    return {
        "Id": container_id,
        "Image": image,
        "State": state,
        "Status": "Up" if state == "running" else "Exited",
        "Names": ["/echo-hub-demo-app"],
        "Labels": labels,
        "Ports": ports or [],
    }


def _provider_container(*, state: str = "running", healthy: bool = True) -> dict:
    return {
        "Id": "a" * 64,
        "Image": "echo-provider",
        "State": state,
        "Status": "Up 10 seconds (healthy)" if state == "running" and healthy else "Up 10 seconds",
        "Names": ["/echo-lan-discovery"],
        "Labels": {"sh.echo.hub.lan-discovery-provider": "true"},
        "Ports": [],
    }


def _provider_catalog() -> HubCatalog:
    mapping = _catalog_mapping()
    source = HubCatalog.load().get("nextcloud")
    assert source is not None and source.bundle is not None
    bundle = source.bundle.to_dict()
    bundle["providers"] = ["lan-discovery"]
    app = mapping["apps"][0]
    app["bundle"] = bundle
    app["package"] = None
    app["imageStorage"] = source.image_storage.to_dict()
    app["version"] = source.version
    app["integrationStatus"] = "available"
    app["integrationNote"] = "provider fixture"
    return HubCatalog.from_mapping(mapping)


class _Docker:
    def __init__(
        self,
        containers: list[dict] | None = None,
        *,
        available: bool = True,
        storage_status: str = "observed",
        storage_free_bytes: int = 64 * 1024**3,
    ) -> None:
        self.containers = containers or []
        self.available = available
        self.storage_status = storage_status
        self.storage_free_bytes = storage_free_bytes

    def list_containers(self, include_stopped: bool = True) -> list[dict]:
        assert include_stopped is True
        if not self.available:
            raise DockerUnavailable("bounded Docker control is offline")
        return self.containers

    def hub_storage_capacity(self) -> dict:
        if self.storage_status != "observed":
            return {
                "schema": "echo.hub.docker-storage.v1",
                "status": self.storage_status,
                "totalBytes": None,
                "freeBytes": None,
                "usedPercent": None,
            }
        total = 128 * 1024**3
        return {
            "schema": "echo.hub.docker-storage.v1",
            "status": "observed",
            "totalBytes": total,
            "freeBytes": self.storage_free_bytes,
            "usedPercent": round(((total - self.storage_free_bytes) / total) * 100, 1),
        }


class _InstallExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def install_hub_app(
        self,
        app_id: str,
        *,
        plan_id: str,
        catalog_digest: str,
    ) -> dict:
        self.calls.append((app_id, plan_id, catalog_digest))
        return {
            "schema": "echo.hub.install-result.v1",
            "appId": app_id,
            "planId": plan_id,
            "catalogDigest": catalog_digest,
            "containerId": "f" * 12,
            "state": "running",
            "image": "fixture",
        }

    def uninstall_hub_app(
        self,
        app_id: str,
        *,
        plan_id: str,
        catalog_digest: str,
    ) -> dict:
        self.calls.append((app_id, plan_id, catalog_digest))
        return {
            "schema": "echo.hub.uninstall-result.v1",
            "appId": app_id,
            "planId": plan_id,
            "catalogDigest": catalog_digest,
            "containerId": "f" * 12,
            "state": "not-installed",
            "dataVolumesRetained": True,
            "nasDataRetained": True,
        }

    def update_hub_app(
        self,
        app_id: str,
        *,
        plan_id: str,
        catalog_digest: str,
    ) -> dict:
        self.calls.append((app_id, plan_id, catalog_digest))
        return {
            "schema": "echo.hub.update-result.v1",
            "appId": app_id,
            "planId": plan_id,
            "catalogDigest": catalog_digest,
            "previousContainerId": "6" * 12,
            "containerId": "f" * 12,
            "previousImage": "old-image",
            "image": "new-image",
            "state": "running",
            "dataVolumesRetained": True,
            "nasDataRetained": True,
        }


class _Engine(_Docker):
    def __init__(
        self,
        containers: list[dict] | None = None,
        *,
        fail_start: bool = False,
        fail_start_ids: set[str] | None = None,
        fail_remove: bool = False,
        fail_restore: bool = False,
    ) -> None:
        super().__init__(containers)
        self.calls: list[tuple[str, object]] = []
        self.fail_start = fail_start
        self.fail_start_ids = fail_start_ids or set()
        self.fail_remove = fail_remove
        self.fail_restore = fail_restore
        self.config: dict | None = None

    def pull_image(self, image: str) -> None:
        self.calls.append(("pull", image))

    def create_volume(self, name: str, *, labels: dict[str, str]) -> None:
        self.calls.append(("volume", (name, labels)))

    def create_container(self, name: str, config: dict) -> str:
        self.calls.append(("create", name))
        self.config = config
        return "e" * 64

    def start(self, container_id: str) -> None:
        self.calls.append(("start", container_id))
        if self.fail_start or container_id in self.fail_start_ids:
            raise DockerUnavailable("start failed")

    def inspect_container(self, container_id: str) -> dict:
        self.calls.append(("inspect", container_id))
        return {"State": {"Running": True}}

    def stop(self, container_id: str) -> None:
        self.calls.append(("stop", container_id))

    def remove_container(self, container_id: str, *, force: bool = False) -> None:
        self.calls.append(("remove", (container_id, force)))
        if self.fail_remove:
            raise DockerUnavailable("remove failed")

    def rename_container(self, container_id: str, name: str) -> None:
        self.calls.append(("rename", (container_id, name)))

    def snapshot_volume(
        self,
        source: str,
        backup: str,
        *,
        labels: dict[str, str],
    ) -> None:
        self.calls.append(("snapshot", (source, backup, labels)))

    def restore_volume(self, backup: str, destination: str) -> None:
        self.calls.append(("restore", (backup, destination)))
        if self.fail_restore:
            raise DockerUnavailable("restore failed")

    def remove_volume(self, name: str) -> None:
        self.calls.append(("remove-volume", name))


def _authenticated_client(app: FastAPI, *, actor: str = "local:admin") -> TestClient:
    client = TestClient(app)
    token = encode_jwt_hs256(
        {"sub": actor, "iat": 0, "exp": 9_999_999_999},
        secret=JWT_SECRET,
    )
    client.cookies.set("echo_session", token)
    return client


def test_bundled_catalog_is_truthful_preview() -> None:
    catalog = HubCatalog.load()
    service = HubService(
        catalog,
        docker=_Docker([_provider_container()]),
        architecture="amd64",
    )

    result = service.list_catalog()

    assert result["schema"] == "echo.hub.catalog-response.v1"
    assert result["total"] == 9
    assert result["runtime"] == {"available": True, "error": None}
    jellyfin = next(app for app in result["apps"] if app["id"] == "jellyfin")
    assert jellyfin["installable"] is True
    assert jellyfin["version"] == "10.11.11"
    assert jellyfin["installation"]["version"] is None
    assert jellyfin["package"]["image"].endswith(
        "@sha256:aefb67e6a7ff1debdd154a78a7bbb780fd0c873d8639210a7f6a2016ad2b35db"
    )
    assert jellyfin["package"]["architectures"] == ["amd64", "arm64"]
    assert jellyfin["package"]["runtime"] == {
        "memoryMiB": 3072,
        "pids": 512,
        "shmSizeMiB": 256,
        "readOnlyRootfs": False,
    }
    navidrome = next(app for app in result["apps"] if app["id"] == "navidrome")
    assert navidrome["installable"] is True
    assert navidrome["package"]["image"].endswith(
        "@sha256:9012939114fbb1bb641b81cf96dec5ded15f0aafefe8d47a511d7cb919658e40"
    )
    assert navidrome["package"]["architectures"] == ["amd64", "arm64"]
    assert navidrome["package"]["ports"] == [{"container": 4533, "host": 4533, "protocol": "tcp"}]
    assert navidrome["package"]["runtime"]["memoryMiB"] == 1024
    assert navidrome["package"]["volumes"] == [
        {
            "source": "app-data",
            "name": "data",
            "target": "/data",
            "readOnly": False,
        },
        {
            "source": "nas-root",
            "name": "music",
            "target": "/music",
            "readOnly": True,
        },
    ]
    nextcloud = next(app for app in result["apps"] if app["id"] == "nextcloud")
    assert nextcloud["installable"] is True
    assert nextcloud["package"] is None
    assert nextcloud["bundle"]["schema"] == "echo.hub.bundle-package.v1"
    assert [service["id"] for service in nextcloud["bundle"]["services"]] == [
        "database",
        "cache",
        "app",
        "cron",
    ]
    assert [port for service in nextcloud["bundle"]["services"] for port in service["ports"]] == [
        {"container": 80, "host": 8081, "protocol": "tcp"}
    ]
    assert nextcloud["bundle"]["upgradePolicy"] == {
        "applicationVersion": "34.0.3",
        "maxMajorStep": 1,
        "snapshotVolumes": ["database", "nextcloud"],
        "serviceOrder": ["database", "cache", "app", "cron"],
    }
    nextcloud_images = {
        service["id"]: service["image"] for service in nextcloud["bundle"]["services"]
    }
    nextcloud_database = next(
        service for service in nextcloud["bundle"]["services"] if service["id"] == "database"
    )
    assert nextcloud_database["mounts"][0]["target"] == "/var/lib/postgresql"
    assert nextcloud_images["database"].endswith(
        "@sha256:d3e1620b530c944afa6e887d22eb899824da68e19c52024bf98f5220c88a65b2"
    )
    assert nextcloud_images["cache"].endswith(
        "@sha256:becdda6c7f4b3fb42e42fd7f120bbf5c54c4caaaf16f26da24e4563d2c1f0576"
    )
    assert nextcloud_images["app"].endswith(
        "@sha256:8e5f49801db0cf4659b3089ce1917728023bb8cba7f93731f2abbdfe3a18df0a"
    )
    immich = next(app for app in result["apps"] if app["id"] == "immich")
    assert immich["installable"] is True
    assert immich["package"] is None
    assert immich["bundle"]["publicService"] == "server"
    assert [service["id"] for service in immich["bundle"]["services"]] == [
        "cache",
        "database",
        "machine-learning",
        "server",
    ]
    immich_server = next(
        service for service in immich["bundle"]["services"] if service["id"] == "server"
    )
    assert immich_server["secretEnvironment"] == {"DB_PASSWORD": "database-password"}
    assert immich_server["runtime"]["profile"] == "data-root-dropper"
    assert immich["bundle"]["volumes"][0] == {
        "name": "library",
        "source": "nas-data",
        "relativePath": "photos/immich",
        "retention": "retain",
        "snapshotOnUpdate": False,
    }
    assert immich["bundle"]["upgradePolicy"]["snapshotVolumes"] == [
        "database",
        "model-cache",
    ]
    assert immich["integrationNote"].endswith("保留数据卸载已接入。")
    open_webui = next(app for app in result["apps"] if app["id"] == "open-webui")
    assert open_webui["installable"] is True
    assert open_webui["package"] is None
    assert open_webui["bundle"]["publicService"] == "app"
    assert [service["id"] for service in open_webui["bundle"]["services"]] == [
        "cache",
        "app",
    ]
    open_webui_app = next(
        service for service in open_webui["bundle"]["services"] if service["id"] == "app"
    )
    assert open_webui_app["image"].endswith(
        "@sha256:6bb1fbe8ab0a3e0456067f493044ffb66a30a65a34be47f6a5862176a370dd16"
    )
    assert open_webui_app["ports"] == [{"container": 8080, "host": 3005, "protocol": "tcp"}]
    assert open_webui_app["secretEnvironment"] == {"WEBUI_SECRET_KEY": "webui-secret"}
    assert open_webui_app["environment"]["REDIS_URL"] == "redis://cache:6379/0"
    assert open_webui_app["runtime"]["profile"] == "unprivileged"
    assert open_webui["bundle"]["upgradePolicy"] == {
        "applicationVersion": "0.11.1",
        "maxMajorStep": 1,
        "snapshotVolumes": ["data"],
        "serviceOrder": ["cache", "app"],
    }
    qbittorrent = next(app for app in result["apps"] if app["id"] == "qbittorrent")
    assert qbittorrent["installable"] is True
    assert qbittorrent["package"] is None
    assert qbittorrent["bundle"]["publicService"] == "app"
    assert len(qbittorrent["bundle"]["services"]) == 1
    qbittorrent_app = qbittorrent["bundle"]["services"][0]
    assert qbittorrent_app["image"].endswith(
        "@sha256:304b19cf94bf4fda534e0b086cab9c5f1a9e139a8180c05c0ad7d2ba1526fa99"
    )
    assert qbittorrent_app["secretEnvironment"] == {"QBT_PASSWORD": "admin-password"}
    assert qbittorrent["bundle"]["volumes"][1]["relativePath"] == ("downloads/qbittorrent")
    syncthing = next(app for app in result["apps"] if app["id"] == "syncthing")
    assert syncthing["installable"] is True
    assert syncthing["package"] is None
    assert syncthing["bundle"]["providers"] == ["lan-discovery"]
    assert syncthing["bundle"]["publicService"] == "app"
    syncthing_app = syncthing["bundle"]["services"][0]
    assert syncthing_app["image"].endswith(
        "@sha256:8c8ff37ab6aa8be23b700648a90fa9412e214852e9fd6ea8477c8334792daec0"
    )
    assert syncthing_app["secretEnvironment"] == {"ST_GUI_PASSWORD": "admin-password"}
    assert syncthing["bundle"]["volumes"][1]["relativePath"] == "sync/syncthing"
    assert all(port["container"] != 21027 for port in syncthing_app["ports"])
    paperless = next(app for app in result["apps"] if app["id"] == "paperless-ngx")
    assert paperless["installable"] is True
    assert paperless["package"] is None
    assert paperless["bundle"]["publicService"] == "app"
    assert [service["id"] for service in paperless["bundle"]["services"]] == [
        "cache",
        "database",
        "gotenberg",
        "tika",
        "app",
    ]
    paperless_app = next(
        service for service in paperless["bundle"]["services"] if service["id"] == "app"
    )
    assert paperless_app["image"].endswith(
        "@sha256:49eba766581b9134cfa6b584b9eb718355fb9cfbd44b2a7c9c72a427d4891648"
    )
    assert paperless_app["ports"] == [{"container": 8000, "host": 3008, "protocol": "tcp"}]
    assert paperless_app["environment"]["PAPERLESS_OCR_LANGUAGE"] == "chi_sim+eng"
    home_assistant = next(app for app in result["apps"] if app["id"] == "home-assistant")
    assert home_assistant["installable"] is True
    assert home_assistant["package"] is None
    assert home_assistant["bundle"]["networks"] == []
    home_assistant_app = home_assistant["bundle"]["services"][0]
    assert home_assistant_app["networkMode"] == "host"
    assert home_assistant_app["ports"] == [{"container": 8123, "host": 8123, "protocol": "tcp"}]
    assert home_assistant_app["runtime"]["profile"] == "unprivileged"
    assert home_assistant_app["image"].endswith(
        "@sha256:14931c6b13756317849f46da1d01b45937a1150db66c081cfe529d48215943fe"
    )
    home_assistant_plan = service.plan_install("home-assistant")
    assert next(
        change for change in home_assistant_plan["changes"] if change["field"] == "networkModes"
    )["after"] == [{"id": "app", "mode": "host"}]
    assert all(
        "PACKAGE_NOT_PUBLISHED" in app["installBlockers"]
        for app in result["apps"]
        if app["id"]
        not in {
            "immich",
            "jellyfin",
            "navidrome",
            "nextcloud",
            "open-webui",
            "qbittorrent",
            "syncthing",
            "paperless-ngx",
            "home-assistant",
        }
    )
    assert {app["id"] for app in result["apps"]} >= {
        "immich",
        "jellyfin",
        "navidrome",
        "syncthing",
    }


def test_immutable_package_generates_deterministic_ready_plan() -> None:
    image = "registry.example.com/echo/demo@sha256:" + "a" * 64
    catalog = HubCatalog.from_mapping(_catalog_mapping(image=image))
    service = HubService(catalog, docker=_Docker(), architecture="x86_64")

    first = service.plan_install("demo-app")
    second = service.plan_install("demo-app")

    assert first == second
    assert first["ready"] is True
    assert first["requiresApproval"] is True
    assert first["approvalAction"] == "hub.app.install"
    assert first["approvalTarget"] == first["planId"]
    assert len(first["planId"]) == 64
    assert first["desired"]["architecture"] == "amd64"
    assert first["desired"]["package"]["image"] == image
    assert first["changes"][1]["after"] == [{"container": 8080, "host": 18080, "protocol": "tcp"}]
    assert first["blockers"] == []
    assert first["resourcePreflight"]["runtime"] == {
        "serviceCount": 1,
        "memoryLimitMiB": 2048,
        "pidsLimit": 384,
        "shmLimitMiB": 128,
        "healthcheckedServices": 0,
    }


def test_resource_preflight_reports_real_nas_capacity_without_exposing_path(tmp_path) -> None:
    image = "registry.example.com/echo/demo@sha256:" + "e" * 64
    catalog = HubCatalog.from_mapping(_catalog_mapping(image=image))
    service = HubService(
        catalog,
        docker=_Docker(),
        architecture="amd64",
        nas_root=tmp_path,
    )

    detail = service.app_detail("demo-app")
    preflight = detail["resourcePreflight"]

    assert detail["appRuntime"]["status"] == "not-installed"
    assert preflight["schema"] == "echo.hub.resource-preflight.v1"
    assert preflight["readyForInstall"] is True
    assert preflight["network"]["ports"] == [
        {
            "container": 8080,
            "host": 18080,
            "protocol": "tcp",
            "status": "available",
        }
    ]
    assert preflight["storage"]["nasAccess"] == "read-only"
    assert preflight["storage"]["appDataVolumes"] == 1
    assert preflight["storage"]["snapshotVolumes"] == 1
    assert preflight["storage"]["nasCapacity"]["status"] == "observed"
    assert preflight["storage"]["nasCapacity"]["freeBytes"] > 0
    image_storage = preflight["storage"]["imageStorage"]
    assert image_storage["status"] == "sufficient"
    assert image_storage["downloadBytes"] == 256 * 1024**2
    assert image_storage["blobCount"] == 8
    assert image_storage["requiredFreeBytes"] == 768 * 1024**2
    assert image_storage["capacity"]["status"] == "observed"
    assert image_storage["capacity"]["freeBytes"] == 64 * 1024**3
    assert str(tmp_path) not in json.dumps(preflight)


def test_app_detail_projects_sanitized_runtime_only_for_managed_installation() -> None:
    image = "registry.example.com/echo/demo@sha256:" + "9" * 64
    catalog = HubCatalog.from_mapping(_catalog_mapping(image=image))
    app = catalog.get("demo-app")
    assert app is not None and app.package is not None
    container = _managed_container(
        catalog,
        image=image,
        package_digest=app.package.digest,
    )

    class RuntimeDocker(_Docker):
        def hub_app_runtime(self, app_id: str) -> dict:
            assert app_id == "demo-app"
            return {
                "schema": "echo.hub.runtime.v1",
                "status": "healthy",
                "summary": {
                    "serviceCount": 1,
                    "runningServices": 1,
                    "healthyServices": 1,
                    "restartCount": 0,
                    "cpuPercent": 2.5,
                    "memoryUsageBytes": 1024,
                    "memoryLimitBytes": 4096,
                    "pids": 3,
                },
                "services": [
                    {
                        "id": "app",
                        "role": "app",
                        "public": True,
                        "state": "running",
                        "health": "not-configured",
                        "restartCount": 0,
                        "oomKilled": False,
                        "exitCode": 0,
                        "cpuPercent": 2.5,
                        "memoryUsageBytes": 1024,
                        "memoryLimitBytes": 4096,
                        "pids": 3,
                    }
                ],
            }

    detail = HubService(
        catalog,
        docker=RuntimeDocker([container]),
        architecture="amd64",
    ).app_detail("demo-app")

    assert detail["appRuntime"]["status"] == "healthy"
    assert detail["appRuntime"]["summary"]["cpuPercent"] == 2.5
    assert set(detail["appRuntime"]) == {"schema", "status", "summary", "services"}


def test_image_storage_preflight_fails_closed_and_changes_plan_identity() -> None:
    image = "registry.example.com/echo/demo@sha256:" + "4" * 64
    catalog = HubCatalog.from_mapping(_catalog_mapping(image=image))

    ready = HubService(catalog, docker=_Docker(), architecture="amd64").plan_install("demo-app")
    insufficient = HubService(
        catalog,
        docker=_Docker(storage_free_bytes=700 * 1024**2),
        architecture="amd64",
    ).plan_install("demo-app")
    unavailable = HubService(
        catalog,
        docker=_Docker(storage_status="unavailable"),
        architecture="amd64",
    ).plan_install("demo-app")
    mismatch = HubService(
        catalog,
        docker=_Docker(storage_status="mismatch"),
        architecture="amd64",
    ).plan_install("demo-app")

    assert ready["ready"] is True
    assert ready["resourcePreflight"]["storage"]["imageStorage"]["status"] == "sufficient"
    assert insufficient["ready"] is False
    assert insufficient["blockers"][0]["code"] == "DOCKER_STORAGE_INSUFFICIENT"
    assert insufficient["resourcePreflight"]["storage"]["imageStorage"]["status"] == (
        "insufficient"
    )
    assert unavailable["blockers"][0]["code"] == "DOCKER_STORAGE_UNAVAILABLE"
    assert mismatch["resourcePreflight"]["storage"]["imageStorage"]["status"] == "mismatch"
    assert (
        len(
            {
                ready["planId"],
                insufficient["planId"],
                unavailable["planId"],
                mismatch["planId"],
            }
        )
        == 3
    )
    assert unavailable["planId"] == mismatch["planId"]


def test_required_provider_must_be_running_and_healthy() -> None:
    catalog = _provider_catalog()

    missing = HubService(catalog, docker=_Docker(), architecture="amd64").plan_install("demo-app")
    unhealthy = HubService(
        catalog,
        docker=_Docker([_provider_container(healthy=False)]),
        architecture="amd64",
    ).plan_install("demo-app")
    stopped = HubService(
        catalog,
        docker=_Docker([_provider_container(state="exited")]),
        architecture="amd64",
    ).plan_install("demo-app")
    healthy = HubService(
        catalog,
        docker=_Docker([_provider_container()]),
        architecture="amd64",
    ).plan_install("demo-app")

    expected = {
        "code": "REQUIRED_PROVIDER_UNAVAILABLE",
        "message": "当前设备的局域网发现服务尚未就绪",
    }
    assert expected in missing["blockers"]
    assert expected in unhealthy["blockers"]
    assert expected in stopped["blockers"]
    assert healthy["ready"] is True
    assert healthy["blockers"] == []


def test_uninstall_plan_removes_only_container_and_retains_data() -> None:
    image = "registry.example.com/echo/demo@sha256:" + "a" * 64
    catalog = HubCatalog.from_mapping(_catalog_mapping(image=image))
    container = _managed_container(catalog, image=image)
    service = HubService(catalog, docker=_Docker([container]), architecture="amd64")

    first = service.plan_uninstall("demo-app")
    second = service.plan_uninstall("demo-app")

    assert first == second
    assert first["schema"] == "echo.hub.uninstall-plan.v1"
    assert first["ready"] is True
    assert first["approvalAction"] == "hub.app.uninstall"
    assert first["current"]["version"] == "1.2.3"
    assert first["desired"]["containerRemoved"] is True
    assert first["desired"]["dataVolumesRetained"] is True
    assert first["desired"]["nasDataRetained"] is True
    assert first["changes"][0] == {
        "field": "container",
        "before": "2" * 12,
        "after": None,
    }

    missing = HubService(catalog, docker=_Docker(), architecture="amd64").plan_uninstall("demo-app")
    ambiguous = HubService(
        catalog,
        docker=_Docker([container, {**container, "Id": "3" * 64}]),
        architecture="amd64",
    ).plan_uninstall("demo-app")
    assert missing["blockers"][0]["code"] == "NOT_INSTALLED"
    assert ambiguous["blockers"][0]["code"] == "INSTALLATION_AMBIGUOUS"


def test_update_plan_is_deterministic_preserves_data_and_excludes_own_port() -> None:
    old_image = "registry.example.com/echo/demo@sha256:" + "8" * 64
    new_image = "registry.example.com/echo/demo@sha256:" + "9" * 64
    catalog = HubCatalog.from_mapping(_catalog_mapping(image=new_image))
    current = _managed_container(
        catalog,
        image=old_image,
        ports=[{"PrivatePort": 8080, "PublicPort": 18080, "Type": "tcp"}],
    )
    service = HubService(catalog, docker=_Docker([current]), architecture="amd64")

    first = service.plan_update("demo-app")
    second = service.plan_update("demo-app")

    assert first == second
    assert first["schema"] == "echo.hub.update-plan.v1"
    assert first["ready"] is True
    assert first["approvalAction"] == "hub.app.update"
    assert first["current"]["image"] == old_image
    assert first["desired"]["package"]["image"] == new_image
    assert first["desired"]["appDataVolumesRetained"] is True
    assert first["desired"]["nasDataRetained"] is True
    assert first["desired"]["runningStatePreserved"] is True
    assert first["blockers"] == []

    package = catalog.get("demo-app").package
    assert package is not None
    current_version = _managed_container(
        catalog,
        image=new_image,
        package_digest=package.digest,
    )
    already_current = HubService(
        catalog, docker=_Docker([current_version]), architecture="amd64"
    ).plan_update("demo-app")
    unmanaged = HubService(
        catalog,
        docker=_Docker(
            [
                {
                    **current,
                    "Labels": {"sh.echo.hub.app-id": "demo-app"},
                }
            ]
        ),
        architecture="amd64",
    ).plan_update("demo-app")
    conflict = HubService(
        catalog,
        docker=_Docker(
            [
                current,
                {
                    "Id": "7" * 64,
                    "Image": "other",
                    "State": "running",
                    "Status": "Up",
                    "Names": ["/other"],
                    "Labels": {},
                    "Ports": [{"PrivatePort": 9999, "PublicPort": 18080, "Type": "tcp"}],
                },
            ]
        ),
        architecture="amd64",
    ).plan_update("demo-app")
    assert already_current["blockers"][0]["code"] == "ALREADY_CURRENT"
    assert unmanaged["blockers"][0]["code"] == "INSTALLATION_NOT_MANAGED"
    assert conflict["blockers"][-1]["code"] == "PORT_IN_USE"


def test_mutable_image_and_arbitrary_package_fields_are_rejected() -> None:
    tagged = _catalog_mapping(image="registry.example.com/echo/demo:latest")
    with pytest.raises(HubCatalogError, match="immutable sha256 digest"):
        HubCatalog.from_mapping(tagged)

    arbitrary = _catalog_mapping(image="registry.example.com/echo/demo@sha256:" + "b" * 64)
    arbitrary["apps"][0]["package"]["privileged"] = True
    with pytest.raises(HubCatalogError, match="unexpected fields"):
        HubCatalog.from_mapping(arbitrary)

    invalid_runtime = _catalog_mapping(image="registry.example.com/echo/demo@sha256:" + "d" * 64)
    invalid_runtime["apps"][0]["package"]["runtime"]["memoryMiB"] = 0
    with pytest.raises(HubCatalogError, match="resource bounds are invalid"):
        HubCatalog.from_mapping(invalid_runtime)

    missing_storage = _catalog_mapping(image="registry.example.com/echo/demo@sha256:" + "f" * 64)
    missing_storage["apps"][0]["imageStorage"] = None
    with pytest.raises(HubCatalogError, match="release-attested image storage"):
        HubCatalog.from_mapping(missing_storage)

    mismatched_storage = _catalog_mapping(image="registry.example.com/echo/demo@sha256:" + "1" * 64)
    del mismatched_storage["apps"][0]["imageStorage"]["architectures"]["arm64"]
    with pytest.raises(HubCatalogError, match="exactly match"):
        HubCatalog.from_mapping(mismatched_storage)

    invalid_version = _catalog_mapping()
    invalid_version["apps"][0]["version"] = "latest release"
    with pytest.raises(HubCatalogError, match="version is invalid"):
        HubCatalog.from_mapping(invalid_version)

    mismatched_bundle_version = _catalog_mapping()
    source = HubCatalog.load().get("nextcloud")
    assert source is not None and source.bundle is not None
    mismatched_bundle_version["apps"][0]["package"] = None
    mismatched_bundle_version["apps"][0]["bundle"] = source.bundle.to_dict()
    mismatched_bundle_version["apps"][0]["imageStorage"] = source.image_storage.to_dict()
    mismatched_bundle_version["apps"][0]["integrationStatus"] = "available"
    with pytest.raises(HubCatalogError, match="must match bundle upgrade policy"):
        HubCatalog.from_mapping(mismatched_bundle_version)


def test_installed_or_offline_runtime_blocks_plan_and_changes_identity() -> None:
    image = "registry.example.com/echo/demo@sha256:" + "c" * 64
    catalog = HubCatalog.from_mapping(_catalog_mapping(image=image))
    installed = _Docker(
        [
            {
                "Id": "d" * 64,
                "Image": image,
                "State": "running",
                "Status": "Up 1 minute",
                "Labels": {"sh.echo.hub.app-id": "demo-app"},
            }
        ]
    )
    ready_plan = HubService(catalog, docker=_Docker(), architecture="amd64").plan_install(
        "demo-app"
    )
    installed_plan = HubService(catalog, docker=installed, architecture="amd64").plan_install(
        "demo-app"
    )
    offline_plan = HubService(
        catalog, docker=_Docker(available=False), architecture="amd64"
    ).plan_install("demo-app")

    assert installed_plan["ready"] is False
    assert installed_plan["blockers"][0]["code"] == "ALREADY_INSTALLED"
    assert installed_plan["current"]["containerId"] == "d" * 12
    assert offline_plan["blockers"][0]["code"] == "DOCKER_RUNTIME_UNAVAILABLE"
    assert len({ready_plan["planId"], installed_plan["planId"], offline_plan["planId"]}) == 3


@pytest.mark.parametrize(
    "version",
    ["latest release", "1.2.3\nprivate", "1" + "2" * 64],
)
def test_installed_version_projection_rejects_malformed_labels(version: str) -> None:
    image = "registry.example.com/echo/demo@sha256:" + "c" * 64
    catalog = HubCatalog.from_mapping(_catalog_mapping(image=image))
    container = _managed_container(catalog, image=image)
    container["Labels"]["sh.echo.hub.version"] = version

    result = HubService(
        catalog,
        docker=_Docker([container]),
        architecture="amd64",
    ).list_catalog()

    assert result["apps"][0]["installation"]["version"] is None


def test_host_port_collision_blocks_install_before_docker_create() -> None:
    image = "registry.example.com/echo/demo@sha256:" + "9" * 64
    catalog = HubCatalog.from_mapping(_catalog_mapping(image=image))
    docker = _Docker(
        [
            {
                "Id": "8" * 64,
                "Image": "another-service",
                "State": "running",
                "Status": "Up",
                "Labels": {},
                "Ports": [{"PrivatePort": 8080, "PublicPort": 18080, "Type": "tcp"}],
            }
        ]
    )

    plan = HubService(catalog, docker=docker, architecture="amd64").plan_install("demo-app")

    assert plan["ready"] is False
    assert plan["blockers"] == [{"code": "PORT_IN_USE", "message": "应用所需端口已被其他服务占用"}]
    assert plan["resourcePreflight"]["network"]["ports"][0]["status"] == "conflict"


def test_custom_appliance_catalog_requires_exact_checksum(monkeypatch, tmp_path) -> None:
    path = tmp_path / "catalog.json"
    payload = json.dumps(_catalog_mapping(), ensure_ascii=False).encode()
    path.write_bytes(payload)
    monkeypatch.setenv("ECHO_APPLIANCE", "1")
    monkeypatch.delenv("ECHO_HUB_CATALOG_SHA256", raising=False)

    with pytest.raises(HubCatalogError, match="requires ECHO_HUB_CATALOG_SHA256"):
        HubCatalog.load(path)

    monkeypatch.setenv("ECHO_HUB_CATALOG_SHA256", hashlib.sha256(payload).hexdigest())
    assert HubCatalog.load(path).get("demo-app") is not None


def test_existing_hub_volume_must_match_the_verified_plan(monkeypatch) -> None:
    client = DockerClient()
    existing = httpx.Response(
        200,
        json={
            "Name": "echo-hub-demo-app-config",
            "Labels": {
                "sh.echo.hub.app-id": "another-app",
                "sh.echo.hub.catalog-digest": "0" * 64,
            },
        },
    )
    monkeypatch.setattr(client, "_request", lambda *_args, **_kwargs: existing)

    with pytest.raises(DockerControlDenied, match="does not belong"):
        client.create_volume(
            "echo-hub-demo-app-config",
            labels={
                "sh.echo.hub.app-id": "demo-app",
                "sh.echo.hub.catalog-digest": "a" * 64,
            },
        )


def test_hub_router_requires_login_filters_and_returns_blocked_plan() -> None:
    catalog = HubCatalog.from_mapping(_catalog_mapping())
    service = HubService(catalog, docker=_Docker(), architecture="arm64")
    app = FastAPI()
    app.include_router(create_hub_router(service, jwt_secret=JWT_SECRET))

    anonymous = TestClient(app)
    assert anonymous.get("/api/appliance/hub/catalog").status_code == 401

    client = _authenticated_client(app)
    listed = client.get(
        "/api/appliance/hub/catalog", params={"search": "演示", "category": "media"}
    )
    detail = client.get("/api/appliance/hub/apps/demo-app")
    plan = client.post("/api/appliance/hub/plans/install", json={"appId": "demo-app"})
    invalid = client.post(
        "/api/appliance/hub/plans/install", json={"appId": "../escape", "extra": True}
    )
    missing = client.get("/api/appliance/hub/apps/missing-app")

    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert detail.json()["app"]["id"] == "demo-app"
    assert detail.json()["resourcePreflight"]["schema"] == "echo.hub.resource-preflight.v1"
    assert plan.status_code == 200
    assert plan.json()["ready"] is False
    assert plan.json()["blockers"][0]["code"] == "PACKAGE_NOT_PUBLISHED"
    assert invalid.status_code == 422
    assert missing.status_code == 404


def test_family_member_can_browse_hub_but_cannot_plan_device_lifecycle() -> None:
    catalog = HubCatalog.from_mapping(_catalog_mapping())
    service = HubService(catalog, docker=_Docker(), architecture="arm64")
    app = FastAPI()
    app.include_router(create_hub_router(service, jwt_secret=JWT_SECRET))
    client = _authenticated_client(app, actor="local:alice")

    assert client.get("/api/appliance/hub/catalog").status_code == 200
    assert client.get("/api/appliance/hub/apps/demo-app").status_code == 200
    assert (
        client.post(
            "/api/appliance/hub/plans/install",
            json={"appId": "demo-app"},
        ).status_code
        == 403
    )


def test_hub_router_queues_control_by_plan_identity() -> None:
    plan_id = "8" * 64

    class _ControlService:
        catalog = type("Catalog", (), {"digest": "7" * 64})()

        def plan_start(self, app_id: str) -> dict:
            assert app_id == "demo-app"
            return {
                "schema": "echo.hub.start-plan.v1",
                "planId": plan_id,
                "ready": True,
                "blockers": [],
                "approvalAction": "hub.app.start",
            }

        plan_stop = plan_start
        plan_restart = plan_start
        plan_install = plan_start
        plan_update = plan_start
        plan_uninstall = plan_start

    class _Operations:
        def __init__(self) -> None:
            self.submissions: list[dict] = []

        def submit(self, **values: object) -> dict:
            self.submissions.append(values)
            return {
                "schema": "echo.hub.operation.v1",
                "operationId": "9" * 32,
                "operation": values["action"],
                "appId": values["app_id"],
                "planId": values["plan_id"],
                "catalogDigest": values["catalog_digest"],
                "status": "queued",
            }

    operations = _Operations()
    app = FastAPI()
    app.include_router(
        create_hub_router(
            _ControlService(),  # type: ignore[arg-type]
            operations=operations,  # type: ignore[arg-type]
        )
    )
    client = TestClient(app)

    plan = client.post("/api/appliance/hub/plans/start", json={"appId": "demo-app"})
    assert plan.status_code == 200
    assert plan.json()["approvalAction"] == "hub.app.start"
    queued = client.post(
        "/api/appliance/hub/plans/start/queue",
        json={"appId": "demo-app", "planId": plan.json()["planId"]},
    )

    assert queued.status_code == 202
    assert queued.json()["operation"] == "start"
    assert operations.submissions[0]["action"] == "start"
    assert operations.submissions[0]["plan_id"] == plan.json()["planId"]


def test_apply_recomputes_plan_requires_step_up_and_audits(tmp_path) -> None:
    password = "hub-install-password"
    image = "registry.example.com/echo/demo@sha256:" + "d" * 64
    catalog = HubCatalog.from_mapping(_catalog_mapping(image=image))
    service = HubService(catalog, docker=_Docker(), architecture="amd64")
    executor = _InstallExecutor()
    audit = ApplianceAudit.from_data_dir(tmp_path, jwt_secret=JWT_SECRET)
    approval = HighRiskApprovalService(
        password_hash=hashlib.sha256(password.encode()).hexdigest(),
        jwt_secret=JWT_SECRET,
        audit=audit,
        boot_nonce=b"echo-hub-install-test" * 2,
    )
    app = FastAPI()
    app.include_router(create_approval_router(approval, jwt_secret=JWT_SECRET))
    app.include_router(
        create_hub_router(
            service,
            installer=executor,
            jwt_secret=JWT_SECRET,
            approval=approval,
            audit=audit,
        )
    )
    client = _authenticated_client(app)
    plan = client.post("/api/appliance/hub/plans/install", json={"appId": "demo-app"}).json()

    unapproved = client.post(
        "/api/appliance/hub/plans/install/apply",
        json={"appId": "demo-app", "planId": plan["planId"]},
    )
    assert unapproved.status_code == 403
    assert executor.calls == []

    intent_id = "task.hub.install.demo"
    issued = client.post(
        "/api/appliance/approvals",
        json={
            "action": "hub.app.install",
            "target": plan["planId"],
            "intentId": intent_id,
            "password": password,
        },
    )
    assert issued.status_code == 200
    installed = client.post(
        "/api/appliance/hub/plans/install/apply",
        json={"appId": "demo-app", "planId": plan["planId"]},
        headers={
            APPROVAL_HEADER: issued.json()["approvalToken"],
            INTENT_HEADER: intent_id,
        },
    )

    assert installed.status_code == 200
    assert installed.json()["containerId"] == "f" * 12
    assert executor.calls == [("demo-app", plan["planId"], catalog.digest)]
    events = [
        event["payload"]
        for event in audit.recent(20)
        if event["payload"]["action"] == "hub.app.install"
    ]
    assert [event["outcome"] for event in events] == ["attempted", "succeeded"]
    assert all(event["metadata"]["intentId"] == intent_id for event in events)


@pytest.mark.parametrize(
    ("failure", "status_code", "detail"),
    [
        (
            DockerControlDenied("private policy path: /etc/echo/docker-policy"),
            403,
            "Hub application control is not allowed",
        ),
        (
            DockerUnavailable("private endpoint: http://docker-proxy.internal:2375"),
            503,
            "Hub application control is unavailable",
        ),
    ],
)
def test_hub_apply_does_not_expose_private_docker_errors(
    failure: Exception,
    status_code: int,
    detail: str,
) -> None:
    class _FailingInstaller(_InstallExecutor):
        def install_hub_app(
            self,
            app_id: str,
            *,
            plan_id: str,
            catalog_digest: str,
        ) -> dict:
            raise failure

    image = "registry.example.com/echo/demo@sha256:" + "d" * 64
    catalog = HubCatalog.from_mapping(_catalog_mapping(image=image))
    service = HubService(catalog, docker=_Docker(), architecture="amd64")
    app = FastAPI()
    app.include_router(create_hub_router(service, installer=_FailingInstaller()))
    client = TestClient(app)
    plan = client.post("/api/appliance/hub/plans/install", json={"appId": "demo-app"})
    response = client.post(
        "/api/appliance/hub/plans/install/apply",
        json={"appId": "demo-app", "planId": plan.json()["planId"]},
    )

    assert response.status_code == status_code
    assert response.json() == {"detail": detail}
    assert "private" not in response.text


def test_queue_returns_durable_operation_and_authenticated_status(tmp_path) -> None:
    password = "hub-background-password"
    image = "registry.example.com/echo/demo@sha256:" + "9" * 64
    catalog = HubCatalog.from_mapping(_catalog_mapping(image=image))
    service = HubService(catalog, docker=_Docker(), architecture="amd64")
    executor = _InstallExecutor()
    audit = ApplianceAudit.from_data_dir(tmp_path / "audit", jwt_secret=JWT_SECRET)
    approval = HighRiskApprovalService(
        password_hash=hashlib.sha256(password.encode()).hexdigest(),
        jwt_secret=JWT_SECRET,
        audit=audit,
        boot_nonce=b"echo-hub-background-test" * 2,
    )
    operations = HubOperationService(
        HubOperationStore(tmp_path / "state", encryption_secret=JWT_SECRET),
        executor=executor,
        audit=audit,
        workers=1,
    )
    app = FastAPI()
    app.include_router(create_approval_router(approval, jwt_secret=JWT_SECRET))
    app.include_router(
        create_hub_router(
            service,
            installer=executor,
            jwt_secret=JWT_SECRET,
            approval=approval,
            audit=audit,
            operations=operations,
        )
    )
    anonymous = TestClient(app)
    assert anonymous.get("/api/appliance/hub/operations").status_code == 401
    client = _authenticated_client(app)
    plan = client.post("/api/appliance/hub/plans/install", json={"appId": "demo-app"}).json()
    intent_id = "task.hub.background.demo"
    issued = client.post(
        "/api/appliance/approvals",
        json={
            "action": "hub.app.install",
            "target": plan["planId"],
            "intentId": intent_id,
            "password": password,
        },
    ).json()
    queued = client.post(
        "/api/appliance/hub/plans/install/queue",
        json={"appId": "demo-app", "planId": plan["planId"]},
        headers={
            APPROVAL_HEADER: issued["approvalToken"],
            INTENT_HEADER: intent_id,
        },
    )
    assert queued.status_code == 202
    operation_id = queued.json()["operationId"]

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        detail = client.get(f"/api/appliance/hub/operations/{operation_id}")
        if detail.json()["status"] == "succeeded":
            break
        time.sleep(0.01)
    assert detail.status_code == 200
    assert detail.json()["result"]["containerId"] == "f" * 12
    listed = client.get("/api/appliance/hub/operations", params={"appId": "demo-app"})
    assert listed.json()["operations"][0]["operationId"] == operation_id
    events = [
        event["payload"]
        for event in audit.recent(20)
        if event["payload"]["action"] == "hub.app.install"
    ]
    assert [event["outcome"] for event in events] == ["attempted", "succeeded"]
    assert all(event["metadata"]["intentId"] == intent_id for event in events)
    operations.shutdown()


def test_apply_rejects_plan_drift_before_execution(tmp_path) -> None:
    image = "registry.example.com/echo/demo@sha256:" + "e" * 64
    catalog = HubCatalog.from_mapping(_catalog_mapping(image=image))
    service = HubService(catalog, docker=_Docker(), architecture="amd64")
    executor = _InstallExecutor()
    app = FastAPI()
    app.include_router(
        create_hub_router(
            service,
            installer=executor,
            jwt_secret=JWT_SECRET,
            approval=None,
            audit=ApplianceAudit.from_data_dir(tmp_path, jwt_secret=JWT_SECRET),
        )
    )
    client = _authenticated_client(app)

    response = client.post(
        "/api/appliance/hub/plans/install/apply",
        json={"appId": "demo-app", "planId": "0" * 64},
    )

    assert response.status_code == 409
    assert executor.calls == []


def test_update_requires_bound_approval_audits_and_retains_data(tmp_path) -> None:
    password = "hub-update-password"
    old_image = "registry.example.com/echo/demo@sha256:" + "1" * 64
    new_image = "registry.example.com/echo/demo@sha256:" + "2" * 64
    catalog = HubCatalog.from_mapping(_catalog_mapping(image=new_image))
    service = HubService(
        catalog,
        docker=_Docker([_managed_container(catalog, image=old_image, container_id="6" * 64)]),
        architecture="amd64",
    )
    executor = _InstallExecutor()
    audit = ApplianceAudit.from_data_dir(tmp_path, jwt_secret=JWT_SECRET)
    approval = HighRiskApprovalService(
        password_hash=hashlib.sha256(password.encode()).hexdigest(),
        jwt_secret=JWT_SECRET,
        audit=audit,
        boot_nonce=b"echo-hub-update-test" * 2,
    )
    app = FastAPI()
    app.include_router(create_approval_router(approval, jwt_secret=JWT_SECRET))
    app.include_router(
        create_hub_router(
            service,
            installer=executor,
            jwt_secret=JWT_SECRET,
            approval=approval,
            audit=audit,
        )
    )
    client = _authenticated_client(app)
    plan = client.post("/api/appliance/hub/plans/update", json={"appId": "demo-app"}).json()

    denied = client.post(
        "/api/appliance/hub/plans/update/apply",
        json={"appId": "demo-app", "planId": plan["planId"]},
    )
    assert denied.status_code == 403
    assert executor.calls == []

    intent_id = "task.hub.update.demo"
    issued = client.post(
        "/api/appliance/approvals",
        json={
            "action": "hub.app.update",
            "target": plan["planId"],
            "intentId": intent_id,
            "password": password,
        },
    )
    result = client.post(
        "/api/appliance/hub/plans/update/apply",
        json={"appId": "demo-app", "planId": plan["planId"]},
        headers={
            APPROVAL_HEADER: issued.json()["approvalToken"],
            INTENT_HEADER: intent_id,
        },
    )

    assert result.status_code == 200
    assert result.json()["state"] == "running"
    assert result.json()["dataVolumesRetained"] is True
    assert result.json()["nasDataRetained"] is True
    events = [
        event["payload"]
        for event in audit.recent(20)
        if event["payload"]["action"] == "hub.app.update"
    ]
    assert [event["outcome"] for event in events] == ["attempted", "succeeded"]
    assert all(event["metadata"]["intentId"] == intent_id for event in events)


def test_uninstall_requires_bound_approval_audits_and_retains_data(tmp_path) -> None:
    password = "hub-uninstall-password"
    image = "registry.example.com/echo/demo@sha256:" + "7" * 64
    catalog = HubCatalog.from_mapping(_catalog_mapping(image=image))
    docker = _Docker([_managed_container(catalog, image=image, container_id="6" * 64)])
    service = HubService(catalog, docker=docker, architecture="amd64")
    executor = _InstallExecutor()
    audit = ApplianceAudit.from_data_dir(tmp_path, jwt_secret=JWT_SECRET)
    approval = HighRiskApprovalService(
        password_hash=hashlib.sha256(password.encode()).hexdigest(),
        jwt_secret=JWT_SECRET,
        audit=audit,
        boot_nonce=b"echo-hub-uninstall-test" * 2,
    )
    app = FastAPI()
    app.include_router(create_approval_router(approval, jwt_secret=JWT_SECRET))
    app.include_router(
        create_hub_router(
            service,
            installer=executor,
            jwt_secret=JWT_SECRET,
            approval=approval,
            audit=audit,
        )
    )
    client = _authenticated_client(app)
    plan = client.post("/api/appliance/hub/plans/uninstall", json={"appId": "demo-app"}).json()

    denied = client.post(
        "/api/appliance/hub/plans/uninstall/apply",
        json={"appId": "demo-app", "planId": plan["planId"]},
    )
    assert denied.status_code == 403
    assert executor.calls == []

    intent_id = "task.hub.uninstall.demo"
    issued = client.post(
        "/api/appliance/approvals",
        json={
            "action": "hub.app.uninstall",
            "target": plan["planId"],
            "intentId": intent_id,
            "password": password,
        },
    )
    result = client.post(
        "/api/appliance/hub/plans/uninstall/apply",
        json={"appId": "demo-app", "planId": plan["planId"]},
        headers={
            APPROVAL_HEADER: issued.json()["approvalToken"],
            INTENT_HEADER: intent_id,
        },
    )

    assert result.status_code == 200
    assert result.json()["state"] == "not-installed"
    assert result.json()["dataVolumesRetained"] is True
    assert result.json()["nasDataRetained"] is True
    events = [
        event["payload"]
        for event in audit.recent(20)
        if event["payload"]["action"] == "hub.app.uninstall"
    ]
    assert [event["outcome"] for event in events] == ["attempted", "succeeded"]
    assert all(event["metadata"]["intentId"] == intent_id for event in events)


def test_privileged_installer_builds_only_bounded_docker_config_and_rolls_back() -> None:
    image = "registry.example.com/echo/demo@sha256:" + "f" * 64
    mapping = _catalog_mapping(image=image)
    mapping["apps"][0]["package"]["volumes"] = [
        {
            "source": "app-data",
            "name": "config",
            "target": "/config",
            "readOnly": False,
        }
    ]
    catalog = HubCatalog.from_mapping(mapping)
    engine = _Engine()
    plan = HubService(catalog, docker=engine).plan_install("demo-app")

    result = HubDockerInstaller(catalog, engine).install(
        "demo-app", plan_id=plan["planId"], catalog_digest=catalog.digest
    )

    assert result["state"] == "running"
    assert result["containerId"] == "e" * 12
    assert engine.config is not None
    assert engine.config["Image"] == image
    assert engine.config["HostConfig"]["CapDrop"] == ["ALL"]
    assert engine.config["HostConfig"]["SecurityOpt"] == ["no-new-privileges"]
    assert engine.config["HostConfig"]["Memory"] == 2048 * 1024 * 1024
    assert engine.config["HostConfig"]["PidsLimit"] == 384
    assert engine.config["HostConfig"]["ShmSize"] == 128 * 1024 * 1024
    assert "Privileged" not in engine.config["HostConfig"]
    assert engine.config["HostConfig"]["Mounts"][0] == {
        "Type": "volume",
        "Source": "echo-hub-demo-app-config",
        "Target": "/config",
        "ReadOnly": False,
    }
    volume_call = next(value for action, value in engine.calls if action == "volume")
    assert volume_call[1] == {
        "sh.echo.hub.managed": "true",
        "sh.echo.hub.app-id": "demo-app",
        "sh.echo.hub.volume-name": "config",
    }
    assert "sh.echo.hub.plan-id" not in volume_call[1]
    assert engine.config["Labels"]["sh.echo.hub.plan-id"] == plan["planId"]
    assert engine.config["Labels"]["sh.echo.hub.version"] == "1.2.3"

    failing = _Engine(fail_start=True)
    failing_plan = HubService(catalog, docker=failing).plan_install("demo-app")
    with pytest.raises(DockerUnavailable, match="start failed"):
        HubDockerInstaller(catalog, failing).install(
            "demo-app",
            plan_id=failing_plan["planId"],
            catalog_digest=catalog.digest,
        )
    assert ("remove", ("e" * 64, True)) in failing.calls


def test_privileged_installer_rejects_catalog_or_plan_drift() -> None:
    image = "registry.example.com/echo/demo@sha256:" + "1" * 64
    catalog = HubCatalog.from_mapping(_catalog_mapping(image=image))
    engine = _Engine()
    plan = HubService(catalog, docker=engine).plan_install("demo-app")
    installer = HubDockerInstaller(catalog, engine)

    with pytest.raises(HubInstallRejected, match="catalog changed"):
        installer.install("demo-app", plan_id=plan["planId"], catalog_digest="0" * 64)
    with pytest.raises(HubInstallRejected, match="plan changed"):
        installer.install("demo-app", plan_id="0" * 64, catalog_digest=catalog.digest)

    with pytest.raises(HubInstallRejected, match="plan changed"):
        HubDockerInstaller(
            catalog,
            engine,
            docker_capacity_provider=lambda: {
                "schema": "echo.hub.docker-storage.v1",
                "status": "observed",
                "totalBytes": 128 * 1024**3,
                "freeBytes": 128 * 1024**2,
                "usedPercent": 99.9,
            },
        ).install(
            "demo-app",
            plan_id=plan["planId"],
            catalog_digest=catalog.digest,
        )
    assert not any(action == "pull" for action, _value in engine.calls)


def test_privileged_updater_switches_candidates_and_rolls_back_failure() -> None:
    old_image = "registry.example.com/echo/demo@sha256:" + "2" * 64
    new_image = "registry.example.com/echo/demo@sha256:" + "3" * 64
    mapping = _catalog_mapping(image=new_image)
    mapping["apps"][0]["package"]["volumes"] = [
        {
            "source": "app-data",
            "name": "config",
            "target": "/config",
            "readOnly": False,
        }
    ]
    catalog = HubCatalog.from_mapping(mapping)
    old = _managed_container(catalog, image=old_image, container_id="5" * 64)
    engine = _Engine([old])
    plan = HubService(catalog, docker=engine).plan_update("demo-app")

    result = HubDockerInstaller(catalog, engine).update(
        "demo-app", plan_id=plan["planId"], catalog_digest=catalog.digest
    )

    assert result["state"] == "running"
    assert result["previousContainerId"] == "5" * 12
    assert result["containerId"] == "e" * 12
    assert result["dataVolumesRetained"] is True
    assert result["nasDataRetained"] is True
    assert engine.config is not None
    package = catalog.get("demo-app").package
    assert package is not None
    assert engine.config["Labels"]["sh.echo.hub.package-digest"] == package.digest
    assert ("rename", ("5" * 64, f"echo-hub-demo-app-rollback-{'5' * 12}")) in engine.calls
    assert ("rename", ("e" * 64, "echo-hub-demo-app")) in engine.calls
    assert ("remove", ("5" * 64, False)) in engine.calls
    assert any(action == "snapshot" for action, _value in engine.calls)
    assert engine.calls[-1][0] == "remove-volume"
    stop_old_index = engine.calls.index(("stop", "5" * 64))
    snapshot_index = next(
        index for index, (action, _value) in enumerate(engine.calls) if action == "snapshot"
    )
    start_candidate_index = engine.calls.index(("start", "e" * 64))
    assert stop_old_index < snapshot_index < start_candidate_index

    failing = _Engine([old], fail_start_ids={"e" * 64})
    failing_plan = HubService(catalog, docker=failing).plan_update("demo-app")
    with pytest.raises(DockerUnavailable, match="start failed"):
        HubDockerInstaller(catalog, failing).update(
            "demo-app",
            plan_id=failing_plan["planId"],
            catalog_digest=catalog.digest,
        )
    assert ("remove", ("e" * 64, True)) in failing.calls
    assert any(action == "restore" for action, _value in failing.calls)
    assert ("rename", ("5" * 64, "echo-hub-demo-app")) in failing.calls
    assert ("start", "5" * 64) in failing.calls
    assert failing.calls[-1][0] == "remove-volume"
    restore_index = next(
        index for index, (action, _value) in enumerate(failing.calls) if action == "restore"
    )
    rename_old_index = failing.calls.index(("rename", ("5" * 64, "echo-hub-demo-app")))
    restart_old_index = failing.calls.index(("start", "5" * 64))
    assert restore_index < rename_old_index < restart_old_index

    rollback_blocked = _Engine(
        [old],
        fail_start_ids={"e" * 64},
        fail_restore=True,
    )
    rollback_blocked_plan = HubService(catalog, docker=rollback_blocked).plan_update("demo-app")
    with pytest.raises(HubInstallRejected, match="rollback could not finish"):
        HubDockerInstaller(catalog, rollback_blocked).update(
            "demo-app",
            plan_id=rollback_blocked_plan["planId"],
            catalog_digest=catalog.digest,
        )
    assert ("start", "5" * 64) not in rollback_blocked.calls
    assert all(action != "remove-volume" for action, _value in rollback_blocked.calls)

    stopped_old = _managed_container(
        catalog,
        image=old_image,
        container_id="5" * 64,
        state="exited",
    )
    stopped = _Engine([stopped_old])
    stopped_plan = HubService(catalog, docker=stopped).plan_update("demo-app")
    stopped_result = HubDockerInstaller(catalog, stopped).update(
        "demo-app",
        plan_id=stopped_plan["planId"],
        catalog_digest=catalog.digest,
    )
    assert stopped_result["state"] == "stopped"
    assert ("stop", "5" * 64) not in stopped.calls
    assert ("stop", "e" * 64) in stopped.calls


def test_privileged_uninstaller_stops_only_owned_container_and_keeps_volumes() -> None:
    image = "registry.example.com/echo/demo@sha256:" + "4" * 64
    catalog = HubCatalog.from_mapping(_catalog_mapping(image=image))
    container = _managed_container(catalog, image=image, container_id="5" * 64)
    engine = _Engine([container])
    plan = HubService(catalog, docker=engine).plan_uninstall("demo-app")

    result = HubDockerInstaller(catalog, engine).uninstall(
        "demo-app", plan_id=plan["planId"], catalog_digest=catalog.digest
    )

    assert result["state"] == "not-installed"
    assert result["dataVolumesRetained"] is True
    assert result["nasDataRetained"] is True
    assert engine.calls == [
        ("stop", "5" * 64),
        ("remove", ("5" * 64, False)),
    ]
    assert all(action != "volume" for action, _value in engine.calls)

    spoofed = _Engine([{**container, "Names": ["/not-owned"]}])
    spoofed_plan = HubService(catalog, docker=spoofed).plan_uninstall("demo-app")
    with pytest.raises(HubInstallRejected, match="blocked"):
        HubDockerInstaller(catalog, spoofed).uninstall(
            "demo-app",
            plan_id=spoofed_plan["planId"],
            catalog_digest=catalog.digest,
        )
    assert spoofed.calls == []

    failing = _Engine([container], fail_remove=True)
    failing_plan = HubService(catalog, docker=failing).plan_uninstall("demo-app")
    with pytest.raises(DockerUnavailable, match="remove failed"):
        HubDockerInstaller(catalog, failing).uninstall(
            "demo-app",
            plan_id=failing_plan["planId"],
            catalog_digest=catalog.digest,
        )
    assert failing.calls[-1] == ("start", "5" * 64)

    stopped = _Engine([{**container, "State": "exited", "Status": "Exited"}], fail_remove=True)
    stopped_plan = HubService(catalog, docker=stopped).plan_uninstall("demo-app")
    with pytest.raises(DockerUnavailable, match="remove failed"):
        HubDockerInstaller(catalog, stopped).uninstall(
            "demo-app",
            plan_id=stopped_plan["planId"],
            catalog_digest=catalog.digest,
        )
    assert stopped.calls == [
        ("stop", "5" * 64),
        ("remove", ("5" * 64, False)),
    ]


def test_privileged_hub_lifecycle_rejects_apps_outside_the_catalog() -> None:
    image = "registry.example.com/echo/demo@sha256:" + "6" * 64
    catalog = HubCatalog.from_mapping(_catalog_mapping(image=image))
    installer = HubDockerInstaller(catalog, _Engine())

    with pytest.raises(HubInstallRejected, match="trusted catalog"):
        installer.install(
            "unknown-app",
            plan_id="0" * 64,
            catalog_digest=catalog.digest,
        )
    with pytest.raises(HubInstallRejected, match="trusted catalog"):
        installer.uninstall(
            "unknown-app",
            plan_id="0" * 64,
            catalog_digest=catalog.digest,
        )
    with pytest.raises(HubInstallRejected, match="trusted catalog"):
        installer.update(
            "unknown-app",
            plan_id="0" * 64,
            catalog_digest=catalog.digest,
        )
