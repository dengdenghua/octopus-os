"""Strict multi-container Hub package contract tests."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from appliance.hub.bundle import HubBundleError, HubBundlePackage


def _nextcloud_bundle() -> dict:
    catalog_path = Path(__file__).parents[2] / "appliance" / "hub" / "catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    return copy.deepcopy(next(app for app in catalog["apps"] if app["id"] == "nextcloud")["bundle"])


def _immich_bundle() -> dict:
    catalog_path = Path(__file__).parents[2] / "appliance" / "hub" / "catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    return copy.deepcopy(next(app for app in catalog["apps"] if app["id"] == "immich")["bundle"])


def _open_webui_bundle() -> dict:
    catalog_path = Path(__file__).parents[2] / "appliance" / "hub" / "catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    return copy.deepcopy(
        next(app for app in catalog["apps"] if app["id"] == "open-webui")["bundle"]
    )


def _qbittorrent_bundle() -> dict:
    catalog_path = Path(__file__).parents[2] / "appliance" / "hub" / "catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    return copy.deepcopy(
        next(app for app in catalog["apps"] if app["id"] == "qbittorrent")["bundle"]
    )


def _syncthing_bundle() -> dict:
    catalog_path = Path(__file__).parents[2] / "appliance" / "hub" / "catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    return copy.deepcopy(next(app for app in catalog["apps"] if app["id"] == "syncthing")["bundle"])


def _paperless_bundle() -> dict:
    catalog_path = Path(__file__).parents[2] / "appliance" / "hub" / "catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    return copy.deepcopy(
        next(app for app in catalog["apps"] if app["id"] == "paperless-ngx")["bundle"]
    )


def _home_assistant_bundle() -> dict:
    catalog_path = Path(__file__).parents[2] / "appliance" / "hub" / "catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    return copy.deepcopy(
        next(app for app in catalog["apps"] if app["id"] == "home-assistant")["bundle"]
    )


def _service(bundle: dict, service_id: str) -> dict:
    return next(service for service in bundle["services"] if service["id"] == service_id)


def test_nextcloud_bundle_is_deterministic_and_bounded() -> None:
    first = HubBundlePackage.parse(_nextcloud_bundle(), "bundle")
    second = HubBundlePackage.parse(_nextcloud_bundle(), "bundle")

    assert first.digest == second.digest
    assert len(first.digest) == 64
    assert first.public_service == "app"
    assert [service.id for service in first.services] == ["database", "cache", "app", "cron"]
    assert first.upgrade_policy.max_major_step == 1
    assert first.upgrade_policy.snapshot_volumes == ("database", "nextcloud")
    assert first.providers == ()
    assert "providers" not in first.to_dict()


def test_bundle_provider_requirement_is_bounded_and_digest_stable() -> None:
    original = HubBundlePackage.parse(_nextcloud_bundle(), "bundle")
    required = _nextcloud_bundle()
    required["providers"] = ["lan-discovery"]
    parsed = HubBundlePackage.parse(required, "bundle")

    assert parsed.providers == ("lan-discovery",)
    assert parsed.to_dict()["providers"] == ["lan-discovery"]
    assert parsed.digest != original.digest

    unknown = _nextcloud_bundle()
    unknown["providers"] = ["host-network"]
    with pytest.raises(HubBundleError, match="providers is invalid"):
        HubBundlePackage.parse(unknown, "bundle")

    duplicate = _nextcloud_bundle()
    duplicate["providers"] = ["lan-discovery", "lan-discovery"]
    with pytest.raises(HubBundleError, match="providers is invalid"):
        HubBundlePackage.parse(duplicate, "bundle")


def test_home_assistant_host_network_is_catalog_only_and_unprivileged() -> None:
    bundle = HubBundlePackage.parse(_home_assistant_bundle(), "bundle")
    service = bundle.services[0]

    assert bundle.networks == ()
    assert service.network_mode == "host"
    assert service.networks == ()
    assert service.runtime.profile == "unprivileged"
    assert service.ports[0].container == service.ports[0].host == 8123
    assert service.image.endswith(
        "@sha256:14931c6b13756317849f46da1d01b45937a1150db66c081cfe529d48215943fe"
    )

    privileged_profile = _home_assistant_bundle()
    privileged_profile["services"][0]["runtime"]["profile"] = "data-root-dropper"
    with pytest.raises(HubBundleError, match="host networking exceeds"):
        HubBundlePackage.parse(privileged_profile, "bundle")

    arbitrary_port = _home_assistant_bundle()
    arbitrary_port["services"][0]["ports"][0]["host"] = 18123
    with pytest.raises(HubBundleError, match="host networking exceeds"):
        HubBundlePackage.parse(arbitrary_port, "bundle")

    hidden_host_service = _nextcloud_bundle()
    hidden_host_service["services"][0]["networkMode"] = "host"
    hidden_host_service["services"][0]["networks"] = []
    with pytest.raises(HubBundleError, match="host networking exceeds"):
        HubBundlePackage.parse(hidden_host_service, "bundle")


def test_immich_bundle_locks_official_four_service_contract() -> None:
    bundle = HubBundlePackage.parse(_immich_bundle(), "bundle")

    assert bundle.public_service == "server"
    assert [service.id for service in bundle.services] == [
        "cache",
        "database",
        "machine-learning",
        "server",
    ]
    server = next(service for service in bundle.services if service.id == "server")
    assert server.secret_environment == (("DB_PASSWORD", "database-password"),)
    assert server.image.endswith(
        "@sha256:b434cb9287eea1471c9974845914d4dd328c9c2d652e446ed4930f99944f0ceb"
    )
    machine_learning = next(
        service for service in bundle.services if service.id == "machine-learning"
    )
    assert machine_learning.image.endswith(
        "@sha256:5a0839dc5303cd7215bcd2180a26aed3af41675aefb3e75e5157e9f10ad16e6e"
    )
    library = next(volume for volume in bundle.volumes if volume.name == "library")
    assert library.source == "nas-data"
    assert library.relative_path == "photos/immich"
    assert library.snapshot_on_update is False
    assert bundle.upgrade_policy.snapshot_volumes == (
        "database",
        "model-cache",
    )


def test_open_webui_bundle_is_bounded_and_update_safe() -> None:
    first = HubBundlePackage.parse(_open_webui_bundle(), "bundle")
    second = HubBundlePackage.parse(_open_webui_bundle(), "bundle")

    assert first.digest == second.digest
    assert first.public_service == "app"
    assert [service.id for service in first.services] == ["cache", "app"]
    cache = next(service for service in first.services if service.id == "cache")
    app = next(service for service in first.services if service.id == "app")
    assert cache.ports == ()
    assert cache.networks == ("backend",)
    assert app.image.endswith(
        "@sha256:6bb1fbe8ab0a3e0456067f493044ffb66a30a65a34be47f6a5862176a370dd16"
    )
    assert app.secret_environment == (("WEBUI_SECRET_KEY", "webui-secret"),)
    assert dict(app.environment)["REDIS_URL"] == "redis://cache:6379/0"
    data = next(volume for volume in first.volumes if volume.name == "data")
    assert data.source == "app-data"
    assert data.snapshot_on_update is True
    assert first.upgrade_policy.snapshot_volumes == ("data",)
    assert first.upgrade_policy.service_order == ("cache", "app")


def test_qbittorrent_single_service_bundle_is_credential_and_storage_safe() -> None:
    first = HubBundlePackage.parse(_qbittorrent_bundle(), "bundle")
    second = HubBundlePackage.parse(_qbittorrent_bundle(), "bundle")

    assert first.digest == second.digest
    assert first.public_service == "app"
    assert len(first.services) == 1
    app = first.services[0]
    assert app.image.endswith(
        "@sha256:304b19cf94bf4fda534e0b086cab9c5f1a9e139a8180c05c0ad7d2ba1526fa99"
    )
    assert app.secret_environment == (("QBT_PASSWORD", "admin-password"),)
    assert dict(app.environment)["PUID"] == "system"
    assert dict(app.environment)["PGID"] == "system"
    assert {(port.host, port.protocol) for port in app.ports} == {
        (3006, "tcp"),
        (6881, "tcp"),
        (6881, "udp"),
    }
    downloads = next(volume for volume in first.volumes if volume.name == "downloads")
    assert downloads.source == "nas-data"
    assert downloads.relative_path == "downloads/qbittorrent"
    assert downloads.snapshot_on_update is False
    assert first.upgrade_policy.snapshot_volumes == ("config",)
    assert first.upgrade_policy.service_order == ("app",)


def test_syncthing_bundle_preserves_identity_without_granting_host_network() -> None:
    first = HubBundlePackage.parse(_syncthing_bundle(), "bundle")
    second = HubBundlePackage.parse(_syncthing_bundle(), "bundle")

    assert first.digest == second.digest
    assert first.providers == ("lan-discovery",)
    assert first.public_service == "app"
    assert len(first.services) == 1
    app = first.services[0]
    assert app.image.endswith(
        "@sha256:8c8ff37ab6aa8be23b700648a90fa9412e214852e9fd6ea8477c8334792daec0"
    )
    assert app.secret_environment == (("ST_GUI_PASSWORD", "admin-password"),)
    assert dict(app.environment)["STGUIADDRESS"] == "0.0.0.0:8384"
    assert {(port.host, port.protocol) for port in app.ports} == {
        (3007, "tcp"),
        (22000, "tcp"),
        (22000, "udp"),
    }
    assert all(port.container != 21027 for port in app.ports)
    config = next(volume for volume in first.volumes if volume.name == "config")
    sync = next(volume for volume in first.volumes if volume.name == "sync")
    assert config.source == "app-data"
    assert config.snapshot_on_update is True
    assert sync.source == "nas-data"
    assert sync.relative_path == "sync/syncthing"
    assert sync.snapshot_on_update is False
    assert first.upgrade_policy.snapshot_volumes == ("config",)


def test_paperless_bundle_locks_full_office_ocr_and_nas_boundary() -> None:
    first = HubBundlePackage.parse(_paperless_bundle(), "bundle")
    second = HubBundlePackage.parse(_paperless_bundle(), "bundle")

    assert first.digest == second.digest
    assert first.public_service == "app"
    assert [service.id for service in first.services] == [
        "cache",
        "database",
        "gotenberg",
        "tika",
        "app",
    ]
    app = next(service for service in first.services if service.id == "app")
    assert app.image.endswith(
        "@sha256:49eba766581b9134cfa6b584b9eb718355fb9cfbd44b2a7c9c72a427d4891648"
    )
    assert app.secret_environment == (
        ("PAPERLESS_ADMIN_PASSWORD", "admin-password"),
        ("PAPERLESS_DBPASS", "database-password"),
        ("PAPERLESS_SECRET_KEY", "paperless-secret"),
    )
    assert dict(app.environment)["PAPERLESS_OCR_LANGUAGE"] == "chi_sim+eng"
    assert {(port.host, port.protocol) for port in app.ports} == {(3008, "tcp")}
    database = next(service for service in first.services if service.id == "database")
    assert database.mounts[0].target == "/var/lib/postgresql"
    assert all(
        service.ports == () for service in first.services if service.id != first.public_service
    )
    assert {volume.relative_path for volume in first.volumes if volume.source == "nas-data"} == {
        "documents/paperless/media",
        "documents/paperless/consume",
        "documents/paperless/export",
    }
    assert first.upgrade_policy.snapshot_volumes == ("database", "cache", "data")
    assert first.upgrade_policy.service_order == (
        "cache",
        "database",
        "gotenberg",
        "tika",
        "app",
    )


def test_bundle_rejects_mutable_images_and_non_public_ports() -> None:
    mutable = _nextcloud_bundle()
    _service(mutable, "app")["image"] = "docker.io/library/nextcloud:latest"
    with pytest.raises(HubBundleError, match="immutable sha256 digest"):
        HubBundlePackage.parse(mutable, "bundle")

    extra_port = _nextcloud_bundle()
    _service(extra_port, "cache")["ports"] = [{"container": 6379, "host": 16379, "protocol": "tcp"}]
    with pytest.raises(HubBundleError, match="only publicService may publish ports"):
        HubBundlePackage.parse(extra_port, "bundle")


def test_bundle_keeps_database_and_cache_on_internal_networks() -> None:
    bundle = _nextcloud_bundle()
    next(network for network in bundle["networks"] if network["name"] == "backend")["internal"] = (
        False
    )

    with pytest.raises(HubBundleError, match="backend networks must be internal"):
        HubBundlePackage.parse(bundle, "bundle")


def test_bundle_rejects_dependency_cycles_and_invalid_upgrade_order() -> None:
    cycle = _nextcloud_bundle()
    _service(cycle, "database")["dependsOn"] = ["app"]
    with pytest.raises(HubBundleError, match="dependency cycle"):
        HubBundlePackage.parse(cycle, "bundle")

    invalid_order = _nextcloud_bundle()
    invalid_order["upgradePolicy"]["serviceOrder"] = ["app", "database", "cache", "cron"]
    with pytest.raises(HubBundleError, match="serviceOrder violates dependencies"):
        HubBundlePackage.parse(invalid_order, "bundle")


def test_bundle_requires_every_writable_volume_snapshot() -> None:
    bundle = _nextcloud_bundle()
    bundle["upgradePolicy"]["snapshotVolumes"] = ["database"]

    with pytest.raises(HubBundleError, match="exactly snapshot writable app-data volumes"):
        HubBundlePackage.parse(bundle, "bundle")


def test_bundle_file_environment_must_reference_a_mounted_secret() -> None:
    bundle = _nextcloud_bundle()
    _service(bundle, "app")["environment"]["POSTGRES_PASSWORD_FILE"] = "/run/secrets/not-mounted"

    with pytest.raises(HubBundleError, match="must reference a mounted secret"):
        HubBundlePackage.parse(bundle, "bundle")


def test_bundle_secret_environment_must_use_a_mounted_secret_and_unique_key() -> None:
    unmounted = _nextcloud_bundle()
    app = _service(unmounted, "app")
    app["secretEnvironment"] = {"DB_PASSWORD": "not-mounted"}
    app["entrypoint"] = ["echo"]
    with pytest.raises(HubBundleError, match="secretEnvironment must use mounted secrets"):
        HubBundlePackage.parse(unmounted, "bundle")

    duplicate = _nextcloud_bundle()
    app = _service(duplicate, "app")
    app["secretEnvironment"] = {"POSTGRES_DB": "database-password"}
    app["entrypoint"] = ["echo"]
    with pytest.raises(HubBundleError, match="environment key twice"):
        HubBundlePackage.parse(duplicate, "bundle")


def test_bundle_rejects_multi_major_upgrade_policy() -> None:
    bundle = _nextcloud_bundle()
    bundle["upgradePolicy"]["maxMajorStep"] = 2

    with pytest.raises(HubBundleError, match="maxMajorStep must be 1"):
        HubBundlePackage.parse(bundle, "bundle")
