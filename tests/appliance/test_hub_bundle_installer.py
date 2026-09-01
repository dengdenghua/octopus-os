"""Atomic multi-container Hub installation tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from appliance.app_registry.docker_client import DockerUnavailable
from appliance.hub import HubCatalog, HubService
from appliance.hub.docker_installer import HubDockerInstaller, HubInstallRejected

OPEN_WEBUI_V0_11_0_INDEX_DIGEST = "72c0ba641ba75e7aa52655cb242570906ececd09b1140fb736483038a22b3228"
OPEN_WEBUI_V0_11_1_INDEX_DIGEST = "6bb1fbe8ab0a3e0456067f493044ffb66a30a65a34be47f6a5862176a370dd16"
QBITTORRENT_V5_2_2_INDEX_DIGEST = "dd24a5f3db32bc1425d3f8dc95e8aca8ac5a35905d798171230edf33f516d9a4"
QBITTORRENT_V5_2_3_INDEX_DIGEST = "304b19cf94bf4fda534e0b086cab9c5f1a9e139a8180c05c0ad7d2ba1526fa99"
SYNCTHING_V2_1_2_INDEX_DIGEST = "4464f4161dd0251e20d46bb3aec83363db75d80cef1abdd5d5fd4054b04a004d"
SYNCTHING_V2_1_3_INDEX_DIGEST = "8c8ff37ab6aa8be23b700648a90fa9412e214852e9fd6ea8477c8334792daec0"
PAPERLESS_V3_0_5_INDEX_DIGEST = "65a4cabf0169ea7fbd90ab7bb28ba3f8b5909613635acda1a03ad606f34b456b"
PAPERLESS_V3_1_0_INDEX_DIGEST = "49eba766581b9134cfa6b584b9eb718355fb9cfbd44b2a7c9c72a427d4891648"
HOME_ASSISTANT_2026_8_2_INDEX_DIGEST = (
    "56690a89c79a0de98035e1719f8324a92d5859c1192ff45adb0230ea81cb42a5"
)
HOME_ASSISTANT_2026_8_3_INDEX_DIGEST = (
    "14931c6b13756317849f46da1d01b45937a1150db66c081cfe529d48215943fe"
)


def _catalog(
    *,
    app_digest: str | None = None,
    app_version: str = "34.0.4",
    version: str = "bundle-test.1",
) -> HubCatalog:
    catalog_path = Path(__file__).parents[2] / "appliance" / "hub" / "catalog.json"
    mapping = json.loads(catalog_path.read_text(encoding="utf-8"))
    nextcloud = next(app for app in mapping["apps"] if app["id"] == "nextcloud")
    nextcloud["integrationStatus"] = "available"
    nextcloud["integrationNote"] = "test-only bundle executor fixture"
    if app_digest is not None:
        for service in nextcloud["bundle"]["services"]:
            if service["id"] in {"app", "cron"}:
                service["image"] = f"docker.io/library/nextcloud@sha256:{app_digest}"
                service["version"] = app_version
        nextcloud["bundle"]["upgradePolicy"]["applicationVersion"] = app_version
        nextcloud["version"] = app_version
    mapping["apps"] = [nextcloud]
    mapping["version"] = version
    return HubCatalog.from_mapping(mapping)


def _immich_catalog() -> HubCatalog:
    catalog_path = Path(__file__).parents[2] / "appliance" / "hub" / "catalog.json"
    mapping = json.loads(catalog_path.read_text(encoding="utf-8"))
    immich = next(app for app in mapping["apps"] if app["id"] == "immich")
    immich["integrationStatus"] = "available"
    immich["integrationNote"] = "test-only named-library executor fixture"
    mapping["apps"] = [immich]
    mapping["version"] = "immich-bundle-test.1"
    return HubCatalog.from_mapping(mapping)


def _open_webui_catalog(
    *,
    app_digest: str | None = None,
    app_version: str = "0.11.1",
    version: str = "open-webui-bundle-test.1",
) -> HubCatalog:
    catalog_path = Path(__file__).parents[2] / "appliance" / "hub" / "catalog.json"
    mapping = json.loads(catalog_path.read_text(encoding="utf-8"))
    open_webui = next(app for app in mapping["apps"] if app["id"] == "open-webui")
    if app_digest is not None:
        app = next(
            service for service in open_webui["bundle"]["services"] if service["id"] == "app"
        )
        app["image"] = f"ghcr.io/open-webui/open-webui@sha256:{app_digest}"
        app["version"] = app_version
        open_webui["bundle"]["upgradePolicy"]["applicationVersion"] = app_version
        open_webui["version"] = app_version
    mapping["apps"] = [open_webui]
    mapping["version"] = version
    return HubCatalog.from_mapping(mapping)


def _qbittorrent_catalog(
    *,
    app_digest: str | None = None,
    app_version: str = "5.2.3",
    version: str = "qbittorrent-bundle-test.1",
) -> HubCatalog:
    catalog_path = Path(__file__).parents[2] / "appliance" / "hub" / "catalog.json"
    mapping = json.loads(catalog_path.read_text(encoding="utf-8"))
    qbittorrent = next(app for app in mapping["apps"] if app["id"] == "qbittorrent")
    if app_digest is not None:
        app = qbittorrent["bundle"]["services"][0]
        app["image"] = f"ghcr.io/linuxserver/qbittorrent@sha256:{app_digest}"
        app["version"] = app_version
        qbittorrent["bundle"]["upgradePolicy"]["applicationVersion"] = app_version
        qbittorrent["version"] = app_version
    mapping["apps"] = [qbittorrent]
    mapping["version"] = version
    return HubCatalog.from_mapping(mapping)


def _syncthing_catalog(
    *,
    app_digest: str | None = None,
    app_version: str = "2.1.3",
    version: str = "syncthing-bundle-test.1",
) -> HubCatalog:
    catalog_path = Path(__file__).parents[2] / "appliance" / "hub" / "catalog.json"
    mapping = json.loads(catalog_path.read_text(encoding="utf-8"))
    syncthing = next(app for app in mapping["apps"] if app["id"] == "syncthing")
    if app_digest is not None:
        app = syncthing["bundle"]["services"][0]
        app["image"] = f"docker.io/syncthing/syncthing@sha256:{app_digest}"
        app["version"] = app_version
        syncthing["bundle"]["upgradePolicy"]["applicationVersion"] = app_version
        syncthing["version"] = app_version
    mapping["apps"] = [syncthing]
    mapping["version"] = version
    return HubCatalog.from_mapping(mapping)


def _paperless_catalog(
    *,
    app_digest: str | None = None,
    app_version: str = "3.1.0",
    version: str = "paperless-bundle-test.1",
) -> HubCatalog:
    catalog_path = Path(__file__).parents[2] / "appliance" / "hub" / "catalog.json"
    mapping = json.loads(catalog_path.read_text(encoding="utf-8"))
    paperless = next(app for app in mapping["apps"] if app["id"] == "paperless-ngx")
    if app_digest is not None:
        app = next(service for service in paperless["bundle"]["services"] if service["id"] == "app")
        app["image"] = f"ghcr.io/paperless-ngx/paperless-ngx@sha256:{app_digest}"
        app["version"] = app_version
        paperless["bundle"]["upgradePolicy"]["applicationVersion"] = app_version
        paperless["version"] = app_version
    mapping["apps"] = [paperless]
    mapping["version"] = version
    return HubCatalog.from_mapping(mapping)


def _home_assistant_catalog(
    *,
    app_digest: str | None = None,
    app_version: str = "2026.8.3",
    version: str = "home-assistant-bundle-test.1",
) -> HubCatalog:
    catalog_path = Path(__file__).parents[2] / "appliance" / "hub" / "catalog.json"
    mapping = json.loads(catalog_path.read_text(encoding="utf-8"))
    home_assistant = next(app for app in mapping["apps"] if app["id"] == "home-assistant")
    if app_digest is not None:
        app = home_assistant["bundle"]["services"][0]
        app["image"] = f"ghcr.io/home-assistant/home-assistant@sha256:{app_digest}"
        app["version"] = app_version
        home_assistant["bundle"]["upgradePolicy"]["applicationVersion"] = app_version
        home_assistant["version"] = app_version
    mapping["apps"] = [home_assistant]
    mapping["version"] = version
    return HubCatalog.from_mapping(mapping)


class _BundleEngine:
    def __init__(
        self,
        *,
        fail_start_service: str | None = None,
        provider_available: bool = True,
    ) -> None:
        self.fail_start_service = fail_start_service
        self.provider_available = provider_available
        self.volumes: dict[str, dict[str, str]] = {}
        self.secret_files: dict[str, dict[str, bytes]] = {}
        self.networks: dict[str, dict[str, Any]] = {}
        self.containers: dict[str, dict[str, Any]] = {}
        self.names: dict[str, str] = {}
        self.running: set[str] = set()
        self.calls: list[tuple[str, str]] = []
        self.restores: list[tuple[str, str]] = []
        self._counter = 0

    def hub_storage_capacity(self) -> dict[str, Any]:
        return {
            "schema": "echo.hub.docker-storage.v1",
            "status": "observed",
            "totalBytes": 128 * 1024**3,
            "freeBytes": 64 * 1024**3,
            "usedPercent": 50.0,
        }

    def list_containers(self, include_stopped: bool = True) -> list[dict[str, Any]]:
        assert include_stopped is True
        result = []
        for container_id, config in self.containers.items():
            ports = []
            for key, bindings in config["HostConfig"]["PortBindings"].items():
                private, protocol = key.split("/", 1)
                for binding in bindings:
                    ports.append(
                        {
                            "PrivatePort": int(private),
                            "PublicPort": int(binding["HostPort"]),
                            "Type": protocol,
                        }
                    )
            result.append(
                {
                    "Id": container_id,
                    "Image": config["Image"],
                    "State": "running" if container_id in self.running else "exited",
                    "Status": "Up" if container_id in self.running else "Exited",
                    "Names": [f"/{self.names[container_id]}"],
                    "Labels": config["Labels"],
                    "Ports": ports,
                }
            )
        if self.provider_available:
            result.append(
                {
                    "Id": "e" * 64,
                    "Image": "echo-lan-discovery",
                    "State": "running",
                    "Status": "Up 10 seconds (healthy)",
                    "Names": ["/echo-lan-discovery"],
                    "Labels": {"sh.echo.hub.lan-discovery-provider": "true"},
                    "Ports": [],
                }
            )
        return result

    def pull_image(self, image: str) -> None:
        self.calls.append(("pull", image))

    def inspect_volume(self, name: str) -> dict[str, Any] | None:
        labels = self.volumes.get(name)
        return {"Name": name, "Labels": labels} if labels is not None else None

    def create_volume(self, name: str, *, labels: dict[str, str]) -> bool:
        existing = self.volumes.get(name)
        if existing is not None:
            assert existing == labels
            return False
        self.volumes[name] = dict(labels)
        self.calls.append(("create-volume", name))
        return True

    def ensure_nas_subdirectory(self, relative_path: str) -> str:
        sources = {
            "photos/immich": "/srv/echo-nas/photos/immich",
            "downloads/qbittorrent": "/srv/echo-nas/downloads/qbittorrent",
            "sync/syncthing": "/srv/echo-nas/sync/syncthing",
            "documents/paperless/media": "/srv/echo-nas/documents/paperless/media",
            "documents/paperless/consume": "/srv/echo-nas/documents/paperless/consume",
            "documents/paperless/export": "/srv/echo-nas/documents/paperless/export",
        }
        assert relative_path in sources
        self.calls.append(("ensure-nas", relative_path))
        return sources[relative_path]

    def remove_volume(self, name: str) -> None:
        self.volumes.pop(name, None)
        self.secret_files.pop(name, None)
        self.calls.append(("remove-volume", name))

    def write_secret_volume(self, volume: str, files: dict[str, bytes]) -> None:
        self.secret_files[volume] = dict(files)
        self.calls.append(("write-secrets", volume))

    def verify_secret_volume(self, volume: str, file_names: tuple[str, ...]) -> None:
        assert set(self.secret_files[volume]) == set(file_names)
        self.calls.append(("verify-secrets", volume))

    def create_network(
        self,
        name: str,
        *,
        internal: bool,
        labels: dict[str, str],
    ) -> bool:
        assert name not in self.networks
        self.networks[name] = {"internal": internal, "labels": dict(labels)}
        self.calls.append(("create-network", name))
        return True

    def remove_network(self, name: str) -> None:
        self.networks.pop(name, None)
        self.calls.append(("remove-network", name))

    def snapshot_volume(
        self,
        source: str,
        backup: str,
        *,
        labels: dict[str, str],
    ) -> None:
        assert source in self.volumes
        self.volumes[backup] = dict(labels)
        self.calls.append(("snapshot", source))

    def restore_volume(self, backup: str, destination: str) -> None:
        assert backup in self.volumes
        assert destination in self.volumes
        self.restores.append((backup, destination))
        self.calls.append(("restore", destination))

    def create_container(self, name: str, config: dict[str, Any]) -> str:
        self._counter += 1
        container_id = f"{self._counter:012x}" + "a" * 52
        self.containers[container_id] = config
        self.names[container_id] = name
        self.calls.append(("create-container", config["Labels"]["sh.echo.hub.bundle-service"]))
        return container_id

    def start(self, container_id: str) -> None:
        service_id = self.containers[container_id]["Labels"]["sh.echo.hub.bundle-service"]
        self.calls.append(("start", service_id))
        if service_id == self.fail_start_service:
            self.fail_start_service = None
            raise DockerUnavailable("injected bundle start failure")
        self.running.add(container_id)

    def inspect_container(self, container_id: str) -> dict[str, Any] | None:
        if container_id not in self.containers:
            return None
        state: dict[str, Any] = {"Running": container_id in self.running}
        if "Healthcheck" in self.containers[container_id]:
            state["Health"] = {"Status": "healthy"}
        return {"State": state}

    def stop(self, container_id: str) -> None:
        self.running.discard(container_id)

    def remove_container(self, container_id: str, *, force: bool = False) -> None:
        self.running.discard(container_id)
        self.containers.pop(container_id, None)
        self.names.pop(container_id, None)
        self.calls.append(("remove-container", container_id))

    def rename_container(self, container_id: str, name: str) -> None:
        assert container_id in self.containers
        self.names[container_id] = name
        self.calls.append(("rename", name))


def test_bundle_plan_and_installer_build_only_catalog_owned_resources() -> None:
    catalog = _catalog()
    engine = _BundleEngine()
    plan = HubService(catalog, docker=engine).plan_install("nextcloud")

    assert plan["ready"] is True
    assert plan["desired"]["package"] is None
    assert plan["desired"]["bundle"]["schema"] == "echo.hub.bundle-package.v1"

    result = HubDockerInstaller(catalog, engine).install(
        "nextcloud",
        plan_id=plan["planId"],
        catalog_digest=catalog.digest,
    )

    assert result["state"] == "running"
    assert set(result["serviceContainerIds"]) == {"database", "cache", "app", "cron"}
    assert set(result["revealedSecrets"]) == {"admin-password"}
    assert len(result["revealedSecrets"]["admin-password"]) >= 32
    assert [value for action, value in engine.calls if action == "start"] == [
        "database",
        "cache",
        "app",
        "cron",
    ]

    configs = {
        config["Labels"]["sh.echo.hub.bundle-service"]: config
        for config in engine.containers.values()
    }
    database = configs["database"]
    app = configs["app"]
    assert database["HostConfig"]["PortBindings"] == {}
    assert list(database["NetworkingConfig"]["EndpointsConfig"]) == [
        next(name for name in engine.networks if "-backend-" in name)
    ]
    assert (
        engine.networks[next(name for name in engine.networks if "-backend-" in name)]["internal"]
        is True
    )
    assert app["HostConfig"]["PortBindings"] == {
        "80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8081"}]
    }
    assert app["HostConfig"]["CapDrop"] == ["ALL"]
    assert app["HostConfig"]["CapAdd"] == [
        "CHOWN",
        "DAC_OVERRIDE",
        "FOWNER",
        "NET_BIND_SERVICE",
        "SETGID",
        "SETUID",
    ]
    assert app["HostConfig"]["Memory"] == 1536 * 1024 * 1024
    assert app["Labels"]["sh.echo.hub.app-id"] == "nextcloud"
    assert app["Labels"]["sh.echo.hub.version"] == "34.0.3"
    assert "sh.echo.hub.app-id" not in configs["database"]["Labels"]
    assert configs["database"]["Labels"]["sh.echo.hub.version"] == "34.0.3"
    assert configs["database"]["Labels"]["sh.echo.hide"] == "1"

    database_secret = engine.secret_files["echo-hub-nextcloud-secrets-database"][
        "database-password"
    ]
    assert (
        engine.secret_files["echo-hub-nextcloud-secrets-app"]["database-password"]
        == database_secret
    )
    assert (
        engine.secret_files["echo-hub-nextcloud-secrets-cron"]["database-password"]
        == database_secret
    )
    serialized_config = json.dumps(engine.containers, ensure_ascii=False)
    assert database_secret.decode("ascii") not in serialized_config
    assert result["revealedSecrets"]["admin-password"] not in serialized_config
    assert all(
        mount["ReadOnly"] is True
        for mount in app["HostConfig"]["Mounts"]
        if mount["Target"] == "/run/secrets"
    )


def test_bundle_install_failure_removes_only_resources_created_by_the_plan() -> None:
    catalog = _catalog()
    engine = _BundleEngine(fail_start_service="app")
    plan = HubService(catalog, docker=engine).plan_install("nextcloud")

    with pytest.raises(DockerUnavailable, match="injected bundle start failure"):
        HubDockerInstaller(catalog, engine).install(
            "nextcloud",
            plan_id=plan["planId"],
            catalog_digest=catalog.digest,
        )

    assert engine.containers == {}
    assert engine.networks == {}
    assert engine.volumes == {}
    assert engine.secret_files == {}


def test_immich_secret_is_alphanumeric_and_injected_only_by_file_wrapper() -> None:
    catalog = _immich_catalog()
    engine = _BundleEngine()
    plan = HubService(catalog, docker=engine).plan_install("immich")

    result = HubDockerInstaller(catalog, engine).install(
        "immich",
        plan_id=plan["planId"],
        catalog_digest=catalog.digest,
    )

    assert result["state"] == "running"
    assert result["revealedSecrets"] == {}
    server_secret = engine.secret_files["echo-hub-immich-secrets-server"]["database-password"]
    assert len(server_secret) == 32
    assert server_secret.decode("ascii").isalnum()
    assert (
        engine.secret_files["echo-hub-immich-secrets-database"]["database-password"]
        == server_secret
    )
    server = next(
        config
        for config in engine.containers.values()
        if config["Labels"]["sh.echo.hub.bundle-service"] == "server"
    )
    assert server["Entrypoint"][0:2] == ["/bin/sh", "-c"]
    assert server["Entrypoint"][2] == (
        'export DB_PASSWORD="$(cat /run/secrets/database-password)"\nexec "$@"'
    )
    assert server["Cmd"] == ["tini", "--", "/bin/bash", "-c", "start.sh"]
    assert server["HostConfig"]["ShmSize"] == 256 * 1024 * 1024
    assert server["HostConfig"]["CapAdd"] == [
        "CHOWN",
        "DAC_OVERRIDE",
        "FOWNER",
        "SETGID",
        "SETUID",
    ]
    assert next(
        mount for mount in server["HostConfig"]["Mounts"] if mount["Target"] == "/data"
    ) == {
        "Type": "bind",
        "Source": "/srv/echo-nas/photos/immich",
        "Target": "/data",
        "ReadOnly": False,
        "BindOptions": {"Propagation": "rprivate"},
    }
    assert ("ensure-nas", "photos/immich") in engine.calls
    serialized = json.dumps(server, ensure_ascii=False)
    assert server_secret.decode("ascii") not in serialized
    assert [value for action, value in engine.calls if action == "start"] == [
        "cache",
        "database",
        "machine-learning",
        "server",
    ]


def test_open_webui_install_keeps_secret_out_of_config_and_cache_private() -> None:
    catalog = _open_webui_catalog()
    engine = _BundleEngine()
    plan = HubService(catalog, docker=engine).plan_install("open-webui")

    result = HubDockerInstaller(catalog, engine).install(
        "open-webui",
        plan_id=plan["planId"],
        catalog_digest=catalog.digest,
    )

    assert result["state"] == "running"
    assert set(result["serviceContainerIds"]) == {"cache", "app"}
    assert result["revealedSecrets"] == {}
    secret = engine.secret_files["echo-hub-open-webui-secrets-app"]["webui-secret"]
    assert len(secret) >= 48
    configs = {
        config["Labels"]["sh.echo.hub.bundle-service"]: config
        for config in engine.containers.values()
    }
    cache = configs["cache"]
    app = configs["app"]
    assert cache["HostConfig"]["PortBindings"] == {}
    cache_network = next(iter(cache["NetworkingConfig"]["EndpointsConfig"]))
    assert engine.networks[cache_network]["internal"] is True
    assert app["HostConfig"]["PortBindings"] == {
        "8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "3005"}]
    }
    assert app["Entrypoint"][0:2] == ["/bin/sh", "-c"]
    assert app["Entrypoint"][2] == (
        'export WEBUI_SECRET_KEY="$(cat /run/secrets/webui-secret)"\nexec "$@"'
    )
    assert app["Cmd"] == ["bash", "start.sh"]
    assert all(not value.startswith("WEBUI_SECRET_KEY=") for value in app["Env"])
    assert "REDIS_URL=redis://cache:6379/0" in app["Env"]
    assert next(
        mount for mount in app["HostConfig"]["Mounts"] if mount["Target"] == "/app/backend/data"
    ) == {
        "Type": "volume",
        "Source": "echo-hub-open-webui-data",
        "Target": "/app/backend/data",
        "ReadOnly": False,
    }
    assert all(
        mount["ReadOnly"] is True
        for mount in app["HostConfig"]["Mounts"]
        if mount["Target"] == "/run/secrets"
    )
    assert secret.decode("ascii") not in json.dumps(app, ensure_ascii=False)
    assert [value for action, value in engine.calls if action == "start"] == [
        "cache",
        "app",
    ]


def test_qbittorrent_install_preseeds_one_time_password_and_isolates_downloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECHO_PUID", "1200")
    monkeypatch.setenv("ECHO_PGID", "1300")
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    catalog = _qbittorrent_catalog()
    engine = _BundleEngine()
    installer = HubDockerInstaller(catalog, engine)
    plan = HubService(catalog, docker=engine).plan_install("qbittorrent")

    result = installer.install(
        "qbittorrent",
        plan_id=plan["planId"],
        catalog_digest=catalog.digest,
    )

    assert result["state"] == "running"
    assert set(result["serviceContainerIds"]) == {"app"}
    assert set(result["revealedSecrets"]) == {"admin-password"}
    password = result["revealedSecrets"]["admin-password"]
    assert len(password) == 24
    assert password.isalnum()
    assert engine.secret_files["echo-hub-qbittorrent-secrets-app"][
        "admin-password"
    ] == password.encode("ascii")
    app = next(iter(engine.containers.values()))
    assert app["Env"] == [
        "PGID=1300",
        "PUID=1200",
        "TORRENTING_PORT=6881",
        "TZ=Asia/Shanghai",
        "WEBUI_PORT=8080",
    ]
    assert app["Entrypoint"][2] == (
        'export QBT_PASSWORD="$(cat /run/secrets/admin-password)"\nexec "$@"'
    )
    assert app["Cmd"][0:2] == ["/bin/sh", "-c"]
    bootstrap = app["Cmd"][2]
    assert "h.pbkdf2_hmac('sha512'" in bootstrap
    assert "WebUI\\Password_PBKDF2" in bootstrap
    assert "100000" in bootstrap
    assert "unset QBT_PASSWORD" in bootstrap
    assert "exec /init" in bootstrap
    assert app["HostConfig"]["PortBindings"] == {
        "8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "3006"}],
        "6881/tcp": [{"HostIp": "0.0.0.0", "HostPort": "6881"}],
        "6881/udp": [{"HostIp": "0.0.0.0", "HostPort": "6881"}],
    }
    assert next(
        mount for mount in app["HostConfig"]["Mounts"] if mount["Target"] == "/downloads"
    ) == {
        "Type": "bind",
        "Source": "/srv/echo-nas/downloads/qbittorrent",
        "Target": "/downloads",
        "ReadOnly": False,
        "BindOptions": {"Propagation": "rprivate"},
    }
    assert app["HostConfig"]["CapAdd"] == [
        "CHOWN",
        "DAC_OVERRIDE",
        "FOWNER",
        "SETGID",
        "SETUID",
    ]
    assert app["Healthcheck"]["Test"] == [
        "CMD",
        "curl",
        "--fail",
        "--silent",
        "--show-error",
        "http://127.0.0.1:8080/",
    ]
    assert password not in json.dumps(app, ensure_ascii=False)
    assert ("ensure-nas", "downloads/qbittorrent") in engine.calls

    uninstall_plan = HubService(catalog, docker=engine).plan_uninstall("qbittorrent")
    installer.uninstall(
        "qbittorrent",
        plan_id=uninstall_plan["planId"],
        catalog_digest=catalog.digest,
    )
    reinstall_plan = HubService(catalog, docker=engine).plan_install("qbittorrent")
    reinstalled = installer.install(
        "qbittorrent",
        plan_id=reinstall_plan["planId"],
        catalog_digest=catalog.digest,
    )
    assert reinstalled["revealedSecrets"] == {}
    assert engine.secret_files["echo-hub-qbittorrent-secrets-app"][
        "admin-password"
    ] == password.encode("ascii")


@pytest.mark.parametrize("environment", ["ECHO_PUID", "ECHO_PGID"])
def test_bundle_rejects_invalid_appliance_identity(
    monkeypatch: pytest.MonkeyPatch,
    environment: str,
) -> None:
    monkeypatch.setenv(environment, "root")
    catalog = _qbittorrent_catalog()
    engine = _BundleEngine()
    plan = HubService(catalog, docker=engine).plan_install("qbittorrent")

    with pytest.raises(HubInstallRejected, match=environment):
        HubDockerInstaller(catalog, engine).install(
            "qbittorrent",
            plan_id=plan["planId"],
            catalog_digest=catalog.digest,
        )
    assert engine.containers == {}


def test_qbittorrent_updates_from_the_real_previous_multiarch_release() -> None:
    old_catalog = _qbittorrent_catalog(
        app_digest=QBITTORRENT_V5_2_2_INDEX_DIGEST,
        app_version="5.2.2",
        version="qbittorrent-bundle-test.old",
    )
    engine = _BundleEngine()
    install_plan = HubService(old_catalog, docker=engine).plan_install("qbittorrent")
    installed = HubDockerInstaller(old_catalog, engine).install(
        "qbittorrent",
        plan_id=install_plan["planId"],
        catalog_digest=old_catalog.digest,
    )
    old_ids = set(engine.containers)
    retained_secrets = {volume: dict(files) for volume, files in engine.secret_files.items()}

    current_catalog = _qbittorrent_catalog(version="qbittorrent-bundle-test.current")
    update_plan = HubService(current_catalog, docker=engine).plan_update("qbittorrent")
    result = HubDockerInstaller(current_catalog, engine).update(
        "qbittorrent",
        plan_id=update_plan["planId"],
        catalog_digest=current_catalog.digest,
    )

    assert installed["revealedSecrets"].keys() == {"admin-password"}
    assert update_plan["ready"] is True
    assert result["previousImage"].endswith("@sha256:" + QBITTORRENT_V5_2_2_INDEX_DIGEST)
    assert result["image"].endswith("@sha256:" + QBITTORRENT_V5_2_3_INDEX_DIGEST)
    assert set(result["serviceContainerIds"]) == {"app"}
    assert old_ids.isdisjoint(engine.containers)
    assert engine.secret_files == retained_secrets
    assert [value for action, value in engine.calls if action == "snapshot"][-1:] == [
        "echo-hub-qbittorrent-config"
    ]
    assert not any("-rollback-" in volume for volume in engine.volumes)


def test_qbittorrent_failed_real_release_update_restores_config_and_password() -> None:
    old_catalog = _qbittorrent_catalog(
        app_digest=QBITTORRENT_V5_2_2_INDEX_DIGEST,
        app_version="5.2.2",
        version="qbittorrent-bundle-test.old",
    )
    engine = _BundleEngine()
    install_plan = HubService(old_catalog, docker=engine).plan_install("qbittorrent")
    HubDockerInstaller(old_catalog, engine).install(
        "qbittorrent",
        plan_id=install_plan["planId"],
        catalog_digest=old_catalog.digest,
    )
    old_ids = set(engine.containers)
    old_names = dict(engine.names)
    retained_secrets = {volume: dict(files) for volume, files in engine.secret_files.items()}
    engine.fail_start_service = "app"

    current_catalog = _qbittorrent_catalog(version="qbittorrent-bundle-test.current")
    update_plan = HubService(current_catalog, docker=engine).plan_update("qbittorrent")
    with pytest.raises(DockerUnavailable, match="injected bundle start failure"):
        HubDockerInstaller(current_catalog, engine).update(
            "qbittorrent",
            plan_id=update_plan["planId"],
            catalog_digest=current_catalog.digest,
        )

    assert set(engine.containers) == old_ids
    assert engine.names == old_names
    assert engine.running == old_ids
    assert engine.secret_files == retained_secrets
    assert engine.restores[-1:] == [
        (
            f"echo-hub-qbittorrent-config-rollback-{update_plan['planId'][:12]}",
            "echo-hub-qbittorrent-config",
        )
    ]
    assert not any("-rollback-" in volume for volume in engine.volumes)


def test_syncthing_install_persists_identity_and_never_grants_host_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECHO_PUID", "1200")
    monkeypatch.setenv("ECHO_PGID", "1300")
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    catalog = _syncthing_catalog()
    engine = _BundleEngine()
    installer = HubDockerInstaller(catalog, engine)
    plan = HubService(catalog, docker=engine).plan_install("syncthing")

    result = installer.install(
        "syncthing",
        plan_id=plan["planId"],
        catalog_digest=catalog.digest,
    )

    assert plan["ready"] is True
    assert result["state"] == "running"
    assert set(result["serviceContainerIds"]) == {"app"}
    assert set(result["revealedSecrets"]) == {"admin-password"}
    password = result["revealedSecrets"]["admin-password"]
    assert len(password) == 24
    assert password.isalnum()
    assert engine.secret_files["echo-hub-syncthing-secrets-app"][
        "admin-password"
    ] == password.encode("ascii")

    app = next(iter(engine.containers.values()))
    assert app["Env"] == [
        "PGID=1300",
        "PUID=1200",
        "STGUIADDRESS=0.0.0.0:8384",
        "STNOPORTPROBING=1",
        "STNOUPGRADE=1",
        "TZ=Asia/Shanghai",
    ]
    assert app["Entrypoint"][2] == (
        'export ST_GUI_PASSWORD="$(cat /run/secrets/admin-password)"\nexec "$@"'
    )
    assert app["Cmd"][0:2] == ["/bin/sh", "-c"]
    bootstrap = app["Cmd"][2]
    assert "syncthing generate --gui-user admin --gui-password -" in bootstrap
    assert "printf '%s\\n' \"$ST_GUI_PASSWORD\"" in bootstrap
    assert "unset ST_GUI_PASSWORD" in bootstrap
    assert "syncthing serve --no-browser --no-upgrade --no-port-probing" in bootstrap
    assert app["HostConfig"]["PortBindings"] == {
        "8384/tcp": [{"HostIp": "0.0.0.0", "HostPort": "3007"}],
        "22000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "22000"}],
        "22000/udp": [{"HostIp": "0.0.0.0", "HostPort": "22000"}],
    }
    assert "21027/udp" not in app["HostConfig"]["PortBindings"]
    assert "NetworkMode" not in app["HostConfig"]
    assert next(
        mount for mount in app["HostConfig"]["Mounts"] if mount["Target"] == "/var/syncthing"
    ) == {
        "Type": "volume",
        "Source": "echo-hub-syncthing-config",
        "Target": "/var/syncthing",
        "ReadOnly": False,
    }
    assert next(mount for mount in app["HostConfig"]["Mounts"] if mount["Target"] == "/data") == {
        "Type": "bind",
        "Source": "/srv/echo-nas/sync/syncthing",
        "Target": "/data",
        "ReadOnly": False,
        "BindOptions": {"Propagation": "rprivate"},
    }
    assert app["Healthcheck"]["Test"] == [
        "CMD",
        "curl",
        "--fail",
        "--silent",
        "--show-error",
        "http://127.0.0.1:8384/rest/noauth/health",
    ]
    assert password not in json.dumps(app, ensure_ascii=False)
    assert ("ensure-nas", "sync/syncthing") in engine.calls

    uninstall_plan = HubService(catalog, docker=engine).plan_uninstall("syncthing")
    installer.uninstall(
        "syncthing",
        plan_id=uninstall_plan["planId"],
        catalog_digest=catalog.digest,
    )
    reinstall_plan = HubService(catalog, docker=engine).plan_install("syncthing")
    reinstalled = installer.install(
        "syncthing",
        plan_id=reinstall_plan["planId"],
        catalog_digest=catalog.digest,
    )
    assert reinstalled["revealedSecrets"] == {}
    assert engine.secret_files["echo-hub-syncthing-secrets-app"][
        "admin-password"
    ] == password.encode("ascii")


def test_syncthing_updates_from_previous_release_and_restores_on_failure() -> None:
    old_catalog = _syncthing_catalog(
        app_digest=SYNCTHING_V2_1_2_INDEX_DIGEST,
        app_version="2.1.2",
        version="syncthing-bundle-test.old",
    )
    engine = _BundleEngine()
    install_plan = HubService(old_catalog, docker=engine).plan_install("syncthing")
    installed = HubDockerInstaller(old_catalog, engine).install(
        "syncthing",
        plan_id=install_plan["planId"],
        catalog_digest=old_catalog.digest,
    )
    retained_secrets = {volume: dict(files) for volume, files in engine.secret_files.items()}

    current_catalog = _syncthing_catalog(version="syncthing-bundle-test.current")
    update_plan = HubService(current_catalog, docker=engine).plan_update("syncthing")
    result = HubDockerInstaller(current_catalog, engine).update(
        "syncthing",
        plan_id=update_plan["planId"],
        catalog_digest=current_catalog.digest,
    )

    assert installed["revealedSecrets"].keys() == {"admin-password"}
    assert update_plan["ready"] is True
    assert result["previousImage"].endswith("@sha256:" + SYNCTHING_V2_1_2_INDEX_DIGEST)
    assert result["image"].endswith("@sha256:" + SYNCTHING_V2_1_3_INDEX_DIGEST)
    assert engine.secret_files == retained_secrets
    assert [value for action, value in engine.calls if action == "snapshot"][-1:] == [
        "echo-hub-syncthing-config"
    ]

    rollback_engine = _BundleEngine()
    rollback_plan = HubService(old_catalog, docker=rollback_engine).plan_install("syncthing")
    HubDockerInstaller(old_catalog, rollback_engine).install(
        "syncthing",
        plan_id=rollback_plan["planId"],
        catalog_digest=old_catalog.digest,
    )
    old_ids = set(rollback_engine.containers)
    old_names = dict(rollback_engine.names)
    retained_rollback_secrets = {
        volume: dict(files) for volume, files in rollback_engine.secret_files.items()
    }
    rollback_engine.fail_start_service = "app"
    failed_plan = HubService(current_catalog, docker=rollback_engine).plan_update("syncthing")
    with pytest.raises(DockerUnavailable, match="injected bundle start failure"):
        HubDockerInstaller(current_catalog, rollback_engine).update(
            "syncthing",
            plan_id=failed_plan["planId"],
            catalog_digest=current_catalog.digest,
        )

    assert set(rollback_engine.containers) == old_ids
    assert rollback_engine.names == old_names
    assert rollback_engine.running == old_ids
    assert rollback_engine.secret_files == retained_rollback_secrets
    assert rollback_engine.restores[-1:] == [
        (
            f"echo-hub-syncthing-config-rollback-{failed_plan['planId'][:12]}",
            "echo-hub-syncthing-config",
        )
    ]


def test_home_assistant_install_uses_bounded_host_lan_without_host_privilege(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    catalog = _home_assistant_catalog()
    engine = _BundleEngine()
    installer = HubDockerInstaller(catalog, engine)
    plan = HubService(catalog, docker=engine).plan_install("home-assistant")

    result = installer.install(
        "home-assistant",
        plan_id=plan["planId"],
        catalog_digest=catalog.digest,
    )

    assert plan["ready"] is True
    assert plan["desired"]["bundle"]["services"][0]["networkMode"] == "host"
    assert result["state"] == "running"
    assert result["revealedSecrets"] == {}
    app = next(iter(engine.containers.values()))
    host = app["HostConfig"]
    assert host["NetworkMode"] == "host"
    assert host["PortBindings"] == {}
    assert host["CapDrop"] == ["ALL"]
    assert "CapAdd" not in host
    assert "Privileged" not in host
    assert host["SecurityOpt"] == ["no-new-privileges"]
    assert "Devices" not in host
    assert "Binds" not in host
    assert "NetworkingConfig" not in app
    assert "Hostname" not in app
    assert app["StopTimeout"] == 60
    assert app["Env"] == ["TZ=Asia/Shanghai"]
    assert app["HostConfig"]["Mounts"] == [
        {
            "Type": "volume",
            "Source": "echo-hub-home-assistant-config",
            "Target": "/config",
            "ReadOnly": False,
        }
    ]
    assert app["Healthcheck"]["Test"] == [
        "CMD",
        "python3",
        "-c",
        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8123/', timeout=5)",
    ]
    assert engine.networks == {}

    uninstall_plan = HubService(catalog, docker=engine).plan_uninstall("home-assistant")
    installer.uninstall(
        "home-assistant",
        plan_id=uninstall_plan["planId"],
        catalog_digest=catalog.digest,
    )
    assert "echo-hub-home-assistant-config" in engine.volumes
    reinstall_plan = HubService(catalog, docker=engine).plan_install("home-assistant")
    installer.install(
        "home-assistant",
        plan_id=reinstall_plan["planId"],
        catalog_digest=catalog.digest,
    )
    assert "echo-hub-home-assistant-config" in engine.volumes


def test_home_assistant_updates_real_previous_release_and_rolls_back_config() -> None:
    old_catalog = _home_assistant_catalog(
        app_digest=HOME_ASSISTANT_2026_8_2_INDEX_DIGEST,
        app_version="2026.8.2",
        version="home-assistant-bundle-test.old",
    )
    engine = _BundleEngine()
    install_plan = HubService(old_catalog, docker=engine).plan_install("home-assistant")
    HubDockerInstaller(old_catalog, engine).install(
        "home-assistant",
        plan_id=install_plan["planId"],
        catalog_digest=old_catalog.digest,
    )

    current_catalog = _home_assistant_catalog(version="home-assistant-bundle-test.current")
    update_plan = HubService(current_catalog, docker=engine).plan_update("home-assistant")
    result = HubDockerInstaller(current_catalog, engine).update(
        "home-assistant",
        plan_id=update_plan["planId"],
        catalog_digest=current_catalog.digest,
    )

    assert update_plan["ready"] is True
    assert result["previousImage"].endswith("@sha256:" + HOME_ASSISTANT_2026_8_2_INDEX_DIGEST)
    assert result["image"].endswith("@sha256:" + HOME_ASSISTANT_2026_8_3_INDEX_DIGEST)
    assert [value for action, value in engine.calls if action == "snapshot"][-1:] == [
        "echo-hub-home-assistant-config"
    ]
    app = next(iter(engine.containers.values()))
    assert app["HostConfig"]["NetworkMode"] == "host"
    assert app["HostConfig"]["PortBindings"] == {}

    rollback_engine = _BundleEngine()
    rollback_plan = HubService(old_catalog, docker=rollback_engine).plan_install("home-assistant")
    HubDockerInstaller(old_catalog, rollback_engine).install(
        "home-assistant",
        plan_id=rollback_plan["planId"],
        catalog_digest=old_catalog.digest,
    )
    old_ids = set(rollback_engine.containers)
    old_names = dict(rollback_engine.names)
    rollback_engine.fail_start_service = "app"
    failed_plan = HubService(current_catalog, docker=rollback_engine).plan_update("home-assistant")
    with pytest.raises(DockerUnavailable, match="injected bundle start failure"):
        HubDockerInstaller(current_catalog, rollback_engine).update(
            "home-assistant",
            plan_id=failed_plan["planId"],
            catalog_digest=current_catalog.digest,
        )

    assert set(rollback_engine.containers) == old_ids
    assert rollback_engine.names == old_names
    assert rollback_engine.running == old_ids
    assert rollback_engine.restores[-1:] == [
        (
            f"echo-hub-home-assistant-config-rollback-{failed_plan['planId'][:12]}",
            "echo-hub-home-assistant-config",
        )
    ]


def test_paperless_install_keeps_documents_on_nas_and_secrets_out_of_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECHO_PUID", "1200")
    monkeypatch.setenv("ECHO_PGID", "1300")
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    catalog = _paperless_catalog()
    engine = _BundleEngine()
    installer = HubDockerInstaller(catalog, engine)
    plan = HubService(catalog, docker=engine).plan_install("paperless-ngx")

    result = installer.install(
        "paperless-ngx",
        plan_id=plan["planId"],
        catalog_digest=catalog.digest,
    )

    assert plan["ready"] is True
    assert result["state"] == "running"
    assert set(result["serviceContainerIds"]) == {
        "cache",
        "database",
        "gotenberg",
        "tika",
        "app",
    }
    assert set(result["revealedSecrets"]) == {"admin-password"}
    password = result["revealedSecrets"]["admin-password"]
    assert len(password) == 24
    assert password.isalnum()
    assert [value for action, value in engine.calls if action == "start"] == [
        "cache",
        "database",
        "gotenberg",
        "tika",
        "app",
    ]

    configs = {
        config["Labels"]["sh.echo.hub.bundle-service"]: config
        for config in engine.containers.values()
    }
    app = configs["app"]
    database = configs["database"]
    gotenberg = configs["gotenberg"]
    tika = configs["tika"]
    assert app["HostConfig"]["PortBindings"] == {
        "8000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "3008"}]
    }
    assert all(
        config["HostConfig"]["PortBindings"] == {}
        for service_id, config in configs.items()
        if service_id != "app"
    )
    assert "PAPERLESS_TIME_ZONE=Asia/Shanghai" in app["Env"]
    assert "USERMAP_UID=1200" in app["Env"]
    assert "USERMAP_GID=1300" in app["Env"]
    assert "PAPERLESS_OCR_LANGUAGE=chi_sim+eng" in app["Env"]
    assert "PAPERLESS_OCR_LANGUAGES=chi-sim" in app["Env"]
    assert all(
        not item.startswith(
            ("PAPERLESS_ADMIN_PASSWORD=", "PAPERLESS_DBPASS=", "PAPERLESS_SECRET_KEY=")
        )
        for item in app["Env"]
    )
    assert app["Entrypoint"][2] == (
        'export PAPERLESS_ADMIN_PASSWORD="$(cat /run/secrets/admin-password)"\n'
        'export PAPERLESS_DBPASS="$(cat /run/secrets/database-password)"\n'
        'export PAPERLESS_SECRET_KEY="$(cat /run/secrets/paperless-secret)"\n'
        'exec "$@"'
    )
    assert app["Cmd"] == ["/init"]
    assert database["HostConfig"]["Mounts"][0]["Target"] == "/var/lib/postgresql"
    assert gotenberg["Cmd"] == [
        "gotenberg",
        "--chromium-disable-javascript=true",
        "--chromium-allow-list=file:///tmp/.*",
    ]
    assert tika["Healthcheck"]["Test"][0:3] == ["CMD", "/bin/sh", "-c"]
    for logical, target in (
        ("media", "/usr/src/paperless/media"),
        ("consume", "/usr/src/paperless/consume"),
        ("export", "/usr/src/paperless/export"),
    ):
        mount = next(item for item in app["HostConfig"]["Mounts"] if item["Target"] == target)
        assert mount == {
            "Type": "bind",
            "Source": f"/srv/echo-nas/documents/paperless/{logical}",
            "Target": target,
            "ReadOnly": False,
            "BindOptions": {"Propagation": "rprivate"},
        }
        assert ("ensure-nas", f"documents/paperless/{logical}") in engine.calls

    serialized = json.dumps(engine.containers, ensure_ascii=False)
    assert password not in serialized
    for volume in engine.secret_files.values():
        for secret in volume.values():
            assert secret.decode("ascii") not in serialized

    uninstall_plan = HubService(catalog, docker=engine).plan_uninstall("paperless-ngx")
    installer.uninstall(
        "paperless-ngx",
        plan_id=uninstall_plan["planId"],
        catalog_digest=catalog.digest,
    )
    reinstall_plan = HubService(catalog, docker=engine).plan_install("paperless-ngx")
    reinstalled = installer.install(
        "paperless-ngx",
        plan_id=reinstall_plan["planId"],
        catalog_digest=catalog.digest,
    )
    assert reinstalled["revealedSecrets"] == {}
    assert engine.secret_files["echo-hub-paperless-ngx-secrets-app"][
        "admin-password"
    ] == password.encode("ascii")


def test_paperless_updates_real_previous_release_and_restores_all_state_on_failure() -> None:
    old_catalog = _paperless_catalog(
        app_digest=PAPERLESS_V3_0_5_INDEX_DIGEST,
        app_version="3.0.5",
        version="paperless-bundle-test.old",
    )
    engine = _BundleEngine()
    install_plan = HubService(old_catalog, docker=engine).plan_install("paperless-ngx")
    HubDockerInstaller(old_catalog, engine).install(
        "paperless-ngx",
        plan_id=install_plan["planId"],
        catalog_digest=old_catalog.digest,
    )
    retained_secrets = {volume: dict(files) for volume, files in engine.secret_files.items()}

    current_catalog = _paperless_catalog(version="paperless-bundle-test.current")
    update_plan = HubService(current_catalog, docker=engine).plan_update("paperless-ngx")
    result = HubDockerInstaller(current_catalog, engine).update(
        "paperless-ngx",
        plan_id=update_plan["planId"],
        catalog_digest=current_catalog.digest,
    )

    assert update_plan["ready"] is True
    assert result["previousImage"].endswith("@sha256:" + PAPERLESS_V3_0_5_INDEX_DIGEST)
    assert result["image"].endswith("@sha256:" + PAPERLESS_V3_1_0_INDEX_DIGEST)
    assert engine.secret_files == retained_secrets
    assert [value for action, value in engine.calls if action == "snapshot"][-3:] == [
        "echo-hub-paperless-ngx-database",
        "echo-hub-paperless-ngx-cache",
        "echo-hub-paperless-ngx-data",
    ]

    rollback_engine = _BundleEngine()
    rollback_plan = HubService(old_catalog, docker=rollback_engine).plan_install("paperless-ngx")
    HubDockerInstaller(old_catalog, rollback_engine).install(
        "paperless-ngx",
        plan_id=rollback_plan["planId"],
        catalog_digest=old_catalog.digest,
    )
    old_ids = set(rollback_engine.containers)
    old_names = dict(rollback_engine.names)
    retained_rollback_secrets = {
        volume: dict(files) for volume, files in rollback_engine.secret_files.items()
    }
    rollback_engine.fail_start_service = "app"
    failed_plan = HubService(current_catalog, docker=rollback_engine).plan_update("paperless-ngx")
    with pytest.raises(DockerUnavailable, match="injected bundle start failure"):
        HubDockerInstaller(current_catalog, rollback_engine).update(
            "paperless-ngx",
            plan_id=failed_plan["planId"],
            catalog_digest=current_catalog.digest,
        )

    assert set(rollback_engine.containers) == old_ids
    assert rollback_engine.names == old_names
    assert rollback_engine.running == old_ids
    assert rollback_engine.secret_files == retained_rollback_secrets
    assert rollback_engine.restores[-3:] == [
        (
            f"echo-hub-paperless-ngx-database-rollback-{failed_plan['planId'][:12]}",
            "echo-hub-paperless-ngx-database",
        ),
        (
            f"echo-hub-paperless-ngx-cache-rollback-{failed_plan['planId'][:12]}",
            "echo-hub-paperless-ngx-cache",
        ),
        (
            f"echo-hub-paperless-ngx-data-rollback-{failed_plan['planId'][:12]}",
            "echo-hub-paperless-ngx-data",
        ),
    ]


def test_open_webui_updates_from_the_real_previous_multiarch_release() -> None:
    old_catalog = _open_webui_catalog(
        app_digest=OPEN_WEBUI_V0_11_0_INDEX_DIGEST,
        app_version="0.11.0",
        version="open-webui-bundle-test.old",
    )
    engine = _BundleEngine()
    install_plan = HubService(old_catalog, docker=engine).plan_install("open-webui")
    HubDockerInstaller(old_catalog, engine).install(
        "open-webui",
        plan_id=install_plan["planId"],
        catalog_digest=old_catalog.digest,
    )
    old_ids = set(engine.containers)
    retained_secrets = {volume: dict(files) for volume, files in engine.secret_files.items()}

    current_catalog = _open_webui_catalog(version="open-webui-bundle-test.current")
    update_plan = HubService(current_catalog, docker=engine).plan_update("open-webui")
    result = HubDockerInstaller(current_catalog, engine).update(
        "open-webui",
        plan_id=update_plan["planId"],
        catalog_digest=current_catalog.digest,
    )

    assert update_plan["ready"] is True
    assert update_plan["current"]["image"].endswith("@sha256:" + OPEN_WEBUI_V0_11_0_INDEX_DIGEST)
    assert result["previousImage"].endswith("@sha256:" + OPEN_WEBUI_V0_11_0_INDEX_DIGEST)
    assert result["image"].endswith("@sha256:" + OPEN_WEBUI_V0_11_1_INDEX_DIGEST)
    assert set(result["serviceContainerIds"]) == {"cache", "app"}
    assert old_ids.isdisjoint(engine.containers)
    assert engine.secret_files == retained_secrets
    assert [value for action, value in engine.calls if action == "snapshot"][-1:] == [
        "echo-hub-open-webui-data"
    ]
    assert not any("-rollback-" in volume for volume in engine.volumes)


def test_open_webui_failed_real_release_update_restores_data_and_old_services() -> None:
    old_catalog = _open_webui_catalog(
        app_digest=OPEN_WEBUI_V0_11_0_INDEX_DIGEST,
        app_version="0.11.0",
        version="open-webui-bundle-test.old",
    )
    engine = _BundleEngine()
    install_plan = HubService(old_catalog, docker=engine).plan_install("open-webui")
    HubDockerInstaller(old_catalog, engine).install(
        "open-webui",
        plan_id=install_plan["planId"],
        catalog_digest=old_catalog.digest,
    )
    old_ids = set(engine.containers)
    old_names = dict(engine.names)
    engine.fail_start_service = "app"

    current_catalog = _open_webui_catalog(version="open-webui-bundle-test.current")
    update_plan = HubService(current_catalog, docker=engine).plan_update("open-webui")
    with pytest.raises(DockerUnavailable, match="injected bundle start failure"):
        HubDockerInstaller(current_catalog, engine).update(
            "open-webui",
            plan_id=update_plan["planId"],
            catalog_digest=current_catalog.digest,
        )

    assert set(engine.containers) == old_ids
    assert engine.names == old_names
    assert engine.running == old_ids
    assert engine.restores[-1:] == [
        (
            f"echo-hub-open-webui-data-rollback-{update_plan['planId'][:12]}",
            "echo-hub-open-webui-data",
        )
    ]
    assert not any("-rollback-" in volume for volume in engine.volumes)


def test_bundle_uninstall_retains_data_and_secrets_for_credential_safe_reinstall() -> None:
    catalog = _catalog()
    engine = _BundleEngine()
    installer = HubDockerInstaller(catalog, engine)
    install_plan = HubService(catalog, docker=engine).plan_install("nextcloud")
    first = installer.install(
        "nextcloud",
        plan_id=install_plan["planId"],
        catalog_digest=catalog.digest,
    )
    retained_secrets = {volume: dict(files) for volume, files in engine.secret_files.items()}

    uninstall_plan = HubService(catalog, docker=engine).plan_uninstall("nextcloud")
    assert uninstall_plan["ready"] is True
    removed = installer.uninstall(
        "nextcloud",
        plan_id=uninstall_plan["planId"],
        catalog_digest=catalog.digest,
    )

    assert removed["state"] == "not-installed"
    assert removed["containerId"] == first["containerId"]
    assert removed["dataVolumesRetained"] is True
    assert removed["secretVolumesRetained"] is True
    assert removed["networkCleanupComplete"] is True
    assert engine.containers == {}
    assert engine.networks == {}
    assert engine.secret_files == retained_secrets
    assert {"echo-hub-nextcloud-database", "echo-hub-nextcloud-nextcloud"} <= set(engine.volumes)

    reinstall_plan = HubService(catalog, docker=engine).plan_install("nextcloud")
    second = installer.install(
        "nextcloud",
        plan_id=reinstall_plan["planId"],
        catalog_digest=catalog.digest,
    )
    assert second["revealedSecrets"] == {}
    assert engine.secret_files == retained_secrets


def test_bundle_update_switches_all_services_after_joint_data_snapshots() -> None:
    old_catalog = _catalog(version="bundle-test.old")
    engine = _BundleEngine()
    old_installer = HubDockerInstaller(old_catalog, engine)
    install_plan = HubService(old_catalog, docker=engine).plan_install("nextcloud")
    old_installer.install(
        "nextcloud",
        plan_id=install_plan["planId"],
        catalog_digest=old_catalog.digest,
    )
    old_ids = set(engine.containers)
    retained_secrets = {volume: dict(files) for volume, files in engine.secret_files.items()}

    new_catalog = _catalog(app_digest="9" * 64, version="bundle-test.new")
    update_plan = HubService(new_catalog, docker=engine).plan_update("nextcloud")
    assert update_plan["ready"] is True
    assert update_plan["desired"]["bundle"]["upgradePolicy"]["applicationVersion"] == "34.0.4"

    result = HubDockerInstaller(new_catalog, engine).update(
        "nextcloud",
        plan_id=update_plan["planId"],
        catalog_digest=new_catalog.digest,
    )

    assert result["state"] == "running"
    assert result["previousContainerId"] not in result["serviceContainerIds"].values()
    assert result["image"].endswith("@sha256:" + "9" * 64)
    assert old_ids.isdisjoint(engine.containers)
    assert set(engine.names.values()) == {
        "echo-hub-nextcloud",
        "echo-hub-nextcloud--database",
        "echo-hub-nextcloud--cache",
        "echo-hub-nextcloud--cron",
    }
    assert engine.secret_files == retained_secrets
    assert [value for action, value in engine.calls if action == "snapshot"][-2:] == [
        "echo-hub-nextcloud-database",
        "echo-hub-nextcloud-nextcloud",
    ]
    assert not any("-rollback-" in volume for volume in engine.volumes)
    assert all(update_plan["planId"][:12] in name for name in engine.networks)


def test_bundle_update_failure_restores_both_volumes_and_all_old_services() -> None:
    old_catalog = _catalog(version="bundle-test.old")
    engine = _BundleEngine()
    install_plan = HubService(old_catalog, docker=engine).plan_install("nextcloud")
    HubDockerInstaller(old_catalog, engine).install(
        "nextcloud",
        plan_id=install_plan["planId"],
        catalog_digest=old_catalog.digest,
    )
    old_ids = set(engine.containers)
    old_networks = set(engine.networks)
    old_names = dict(engine.names)
    engine.fail_start_service = "app"

    new_catalog = _catalog(app_digest="8" * 64, version="bundle-test.new")
    update_plan = HubService(new_catalog, docker=engine).plan_update("nextcloud")
    with pytest.raises(DockerUnavailable, match="injected bundle start failure"):
        HubDockerInstaller(new_catalog, engine).update(
            "nextcloud",
            plan_id=update_plan["planId"],
            catalog_digest=new_catalog.digest,
        )

    assert set(engine.containers) == old_ids
    assert engine.names == old_names
    assert engine.running == old_ids
    assert set(engine.networks) == old_networks
    assert [destination for _backup, destination in engine.restores] == [
        "echo-hub-nextcloud-database",
        "echo-hub-nextcloud-nextcloud",
    ]
    assert not any("-rollback-" in volume for volume in engine.volumes)


def test_bundle_plan_rejects_skipping_multiple_application_major_versions() -> None:
    old_catalog = _catalog(version="bundle-test.old")
    engine = _BundleEngine()
    install_plan = HubService(old_catalog, docker=engine).plan_install("nextcloud")
    HubDockerInstaller(old_catalog, engine).install(
        "nextcloud",
        plan_id=install_plan["planId"],
        catalog_digest=old_catalog.digest,
    )

    skipped = _catalog(
        app_digest="7" * 64,
        app_version="36.0.0",
        version="bundle-test.skipped",
    )
    plan = HubService(skipped, docker=engine).plan_update("nextcloud")

    assert plan["ready"] is False
    assert plan["blockers"][-1]["code"] == "UPGRADE_PATH_UNSUPPORTED"


def test_bundle_refuses_partial_retained_state_before_creating_resources() -> None:
    catalog = _catalog()
    engine = _BundleEngine()
    engine.volumes["echo-hub-nextcloud-database"] = {
        "sh.echo.hub.managed": "true",
        "sh.echo.hub.bundle-app-id": "nextcloud",
        "sh.echo.hub.bundle-volume": "database",
        "sh.echo.hub.bundle-volume-role": "data",
    }
    plan = HubService(catalog, docker=engine).plan_install("nextcloud")

    with pytest.raises(HubInstallRejected, match="data volumes are incomplete"):
        HubDockerInstaller(catalog, engine).install(
            "nextcloud",
            plan_id=plan["planId"],
            catalog_digest=catalog.digest,
        )

    assert engine.calls == []
