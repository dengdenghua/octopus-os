"""Strict, read-only catalog model for Echo Hub.

The bundled catalog is presentation metadata until an entry carries a complete
``echo.hub.docker-package.v1`` package.  Mutable image tags, arbitrary bind
paths, privileged flags and caller supplied Compose fragments are deliberately
not part of the schema.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from appliance.hub.bundle import HubBundleError, HubBundlePackage

CATALOG_SCHEMA = "echo.hub.catalog.v1"
DOCKER_PACKAGE_SCHEMA = "echo.hub.docker-package.v1"
IMAGE_STORAGE_SCHEMA = "echo.hub.image-storage.v1"
MAX_CATALOG_BYTES = 1024 * 1024
MAX_APPS = 256

_APP_ID = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_VERSION = re.compile(r"^[0-9][0-9A-Za-z.+-]{0,31}$")
_IMAGE = re.compile(
    r"^[a-z0-9]+(?:[._-][a-z0-9]+)*(?::[0-9]+)?/"
    r"[a-z0-9]+(?:[._/-][a-z0-9]+)*@sha256:[0-9a-f]{64}$"
)
_ENV_KEY = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ARCHITECTURES = frozenset({"amd64", "arm64"})
_PROTOCOLS = frozenset({"tcp", "udp"})
_VOLUME_SOURCES = frozenset({"app-data", "nas-root"})
_CATEGORIES = frozenset(
    {
        "ai",
        "automation",
        "backup",
        "documents",
        "downloads",
        "media",
        "photos",
        "productivity",
        "sync",
        "system",
    }
)


class HubCatalogError(ValueError):
    """The catalog is unavailable, untrusted or violates the bounded schema."""


def _exact_keys(value: dict[str, Any], allowed: set[str], where: str) -> None:
    unexpected = sorted(set(value) - allowed)
    if unexpected:
        raise HubCatalogError(f"{where} has unexpected fields: {', '.join(unexpected)}")


def _text(value: Any, where: str, *, maximum: int, minimum: int = 1) -> str:
    if not isinstance(value, str):
        raise HubCatalogError(f"{where} must be a string")
    text = value.strip()
    if not minimum <= len(text) <= maximum or "\x00" in text:
        raise HubCatalogError(f"{where} length is invalid")
    return text


def _https_url(value: Any, where: str) -> str:
    url = _text(value, where, maximum=512)
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise HubCatalogError(f"{where} must be a public HTTPS URL")
    return url


def _container_path(value: Any, where: str) -> str:
    path = _text(value, where, maximum=256)
    pure = PurePosixPath(path)
    if not pure.is_absolute() or ".." in pure.parts or path == "/":
        raise HubCatalogError(f"{where} must be a bounded absolute container path")
    return str(pure)


@dataclass(frozen=True)
class HubPort:
    container: int
    host: int
    protocol: str

    def to_dict(self) -> dict[str, Any]:
        return {"container": self.container, "host": self.host, "protocol": self.protocol}


@dataclass(frozen=True)
class HubVolume:
    source: str
    name: str
    target: str
    read_only: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "name": self.name,
            "target": self.target,
            "readOnly": self.read_only,
        }


@dataclass(frozen=True)
class HubContainerRuntime:
    memory_mib: int
    pids: int
    shm_size_mib: int
    read_only_rootfs: bool

    @classmethod
    def parse(cls, value: Any, where: str) -> HubContainerRuntime:
        if not isinstance(value, dict):
            raise HubCatalogError(f"{where} must be an object")
        _exact_keys(value, {"memoryMiB", "pids", "shmSizeMiB", "readOnlyRootfs"}, where)
        memory_mib = value.get("memoryMiB")
        pids = value.get("pids")
        shm_size_mib = value.get("shmSizeMiB")
        read_only_rootfs = value.get("readOnlyRootfs")
        if (
            not isinstance(memory_mib, int)
            or isinstance(memory_mib, bool)
            or not 128 <= memory_mib <= 8192
            or not isinstance(pids, int)
            or isinstance(pids, bool)
            or not 64 <= pids <= 2048
            or not isinstance(shm_size_mib, int)
            or isinstance(shm_size_mib, bool)
            or not 64 <= shm_size_mib <= 1024
            or not isinstance(read_only_rootfs, bool)
        ):
            raise HubCatalogError(f"{where} resource bounds are invalid")
        return cls(memory_mib, pids, shm_size_mib, read_only_rootfs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "memoryMiB": self.memory_mib,
            "pids": self.pids,
            "shmSizeMiB": self.shm_size_mib,
            "readOnlyRootfs": self.read_only_rootfs,
        }


@dataclass(frozen=True)
class HubImageStorageArchitecture:
    download_bytes: int
    blob_count: int

    def to_dict(self) -> dict[str, int]:
        return {"downloadBytes": self.download_bytes, "blobCount": self.blob_count}


@dataclass(frozen=True)
class HubImageStorage:
    architectures: tuple[tuple[str, HubImageStorageArchitecture], ...]

    @classmethod
    def parse(
        cls,
        value: Any,
        where: str,
        *,
        expected_architectures: tuple[str, ...],
    ) -> HubImageStorage:
        if not isinstance(value, dict):
            raise HubCatalogError(f"{where} must be an object")
        _exact_keys(value, {"schema", "architectures"}, where)
        if value.get("schema") != IMAGE_STORAGE_SCHEMA:
            raise HubCatalogError(f"{where}.schema is unsupported")
        raw_architectures = value.get("architectures")
        if not isinstance(raw_architectures, dict) or set(raw_architectures) != set(
            expected_architectures
        ):
            raise HubCatalogError(f"{where}.architectures must exactly match the package")
        parsed: list[tuple[str, HubImageStorageArchitecture]] = []
        for architecture in sorted(raw_architectures):
            raw = raw_architectures[architecture]
            item_where = f"{where}.architectures.{architecture}"
            if not isinstance(raw, dict):
                raise HubCatalogError(f"{item_where} must be an object")
            _exact_keys(raw, {"downloadBytes", "blobCount"}, item_where)
            download_bytes = raw.get("downloadBytes")
            blob_count = raw.get("blobCount")
            if (
                not isinstance(download_bytes, int)
                or isinstance(download_bytes, bool)
                or not 1024 <= download_bytes <= 64 * 1024**3
                or not isinstance(blob_count, int)
                or isinstance(blob_count, bool)
                or not 1 <= blob_count <= 4096
            ):
                raise HubCatalogError(f"{item_where} bounds are invalid")
            parsed.append(
                (
                    architecture,
                    HubImageStorageArchitecture(
                        download_bytes=download_bytes,
                        blob_count=blob_count,
                    ),
                )
            )
        return cls(tuple(parsed))

    def for_architecture(self, architecture: str) -> HubImageStorageArchitecture | None:
        return next((value for key, value in self.architectures if key == architecture), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": IMAGE_STORAGE_SCHEMA,
            "architectures": {key: value.to_dict() for key, value in self.architectures},
        }


@dataclass(frozen=True)
class HubDockerPackage:
    image: str
    architectures: tuple[str, ...]
    ports: tuple[HubPort, ...]
    volumes: tuple[HubVolume, ...]
    environment: tuple[tuple[str, str], ...]
    runtime: HubContainerRuntime

    @property
    def digest(self) -> str:
        canonical = json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @classmethod
    def parse(cls, value: Any, where: str) -> HubDockerPackage:
        if not isinstance(value, dict):
            raise HubCatalogError(f"{where} must be an object")
        _exact_keys(
            value,
            {
                "schema",
                "image",
                "architectures",
                "ports",
                "volumes",
                "environment",
                "runtime",
            },
            where,
        )
        if value.get("schema") != DOCKER_PACKAGE_SCHEMA:
            raise HubCatalogError(f"{where}.schema is unsupported")
        image = _text(value.get("image"), f"{where}.image", maximum=512)
        if _IMAGE.fullmatch(image) is None:
            raise HubCatalogError(f"{where}.image must use an immutable sha256 digest")

        raw_architectures = value.get("architectures")
        if not isinstance(raw_architectures, list) or not raw_architectures:
            raise HubCatalogError(f"{where}.architectures must be a non-empty list")
        architectures = tuple(dict.fromkeys(str(item) for item in raw_architectures))
        if len(architectures) != len(raw_architectures) or not set(architectures) <= _ARCHITECTURES:
            raise HubCatalogError(
                f"{where}.architectures contains duplicates or unsupported values"
            )

        raw_ports = value.get("ports", [])
        if not isinstance(raw_ports, list) or len(raw_ports) > 32:
            raise HubCatalogError(f"{where}.ports must be a bounded list")
        ports: list[HubPort] = []
        port_keys: set[tuple[int, str]] = set()
        host_keys: set[tuple[int, str]] = set()
        for index, raw in enumerate(raw_ports):
            port_where = f"{where}.ports[{index}]"
            if not isinstance(raw, dict):
                raise HubCatalogError(f"{port_where} must be an object")
            _exact_keys(raw, {"container", "host", "protocol"}, port_where)
            container = raw.get("container")
            host = raw.get("host")
            protocol = raw.get("protocol")
            if (
                not isinstance(container, int)
                or isinstance(container, bool)
                or not 1 <= container <= 65535
                or not isinstance(host, int)
                or isinstance(host, bool)
                or not 1024 <= host <= 65535
                or protocol not in _PROTOCOLS
            ):
                raise HubCatalogError(f"{port_where} is invalid")
            if (container, protocol) in port_keys or (host, protocol) in host_keys:
                raise HubCatalogError(f"{port_where} duplicates a port mapping")
            port_keys.add((container, protocol))
            host_keys.add((host, protocol))
            ports.append(HubPort(container=container, host=host, protocol=protocol))

        raw_volumes = value.get("volumes", [])
        if not isinstance(raw_volumes, list) or len(raw_volumes) > 32:
            raise HubCatalogError(f"{where}.volumes must be a bounded list")
        volumes: list[HubVolume] = []
        volume_names: set[str] = set()
        volume_targets: set[str] = set()
        for index, raw in enumerate(raw_volumes):
            volume_where = f"{where}.volumes[{index}]"
            if not isinstance(raw, dict):
                raise HubCatalogError(f"{volume_where} must be an object")
            _exact_keys(raw, {"source", "name", "target", "readOnly"}, volume_where)
            source = raw.get("source")
            name = _text(raw.get("name"), f"{volume_where}.name", maximum=64)
            target = _container_path(raw.get("target"), f"{volume_where}.target")
            read_only = raw.get("readOnly")
            if source not in _VOLUME_SOURCES or _APP_ID.fullmatch(name) is None:
                raise HubCatalogError(f"{volume_where} source or name is invalid")
            if not isinstance(read_only, bool):
                raise HubCatalogError(f"{volume_where}.readOnly must be boolean")
            if name in volume_names or target in volume_targets:
                raise HubCatalogError(f"{volume_where} duplicates a volume")
            volume_names.add(name)
            volume_targets.add(target)
            volumes.append(HubVolume(source=source, name=name, target=target, read_only=read_only))

        raw_environment = value.get("environment", {})
        if not isinstance(raw_environment, dict) or len(raw_environment) > 64:
            raise HubCatalogError(f"{where}.environment must be a bounded object")
        environment: list[tuple[str, str]] = []
        for key, raw in sorted(raw_environment.items()):
            if _ENV_KEY.fullmatch(str(key)) is None:
                raise HubCatalogError(f"{where}.environment has an invalid key")
            environment.append((str(key), _text(raw, f"{where}.environment.{key}", maximum=512)))

        return cls(
            image=image,
            architectures=architectures,
            ports=tuple(ports),
            volumes=tuple(volumes),
            environment=tuple(environment),
            runtime=HubContainerRuntime.parse(value.get("runtime"), f"{where}.runtime"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": DOCKER_PACKAGE_SCHEMA,
            "image": self.image,
            "architectures": list(self.architectures),
            "ports": [item.to_dict() for item in self.ports],
            "volumes": [item.to_dict() for item in self.volumes],
            "environment": dict(self.environment),
            "runtime": self.runtime.to_dict(),
        }


@dataclass(frozen=True)
class HubApp:
    id: str
    name: str
    name_zh: str
    version: str
    summary: str
    category: str
    icon: str
    source_url: str
    featured: bool
    image_storage: HubImageStorage | None
    package: HubDockerPackage | None
    bundle: HubBundlePackage | None
    integration_status: str
    integration_note: str

    @classmethod
    def parse(cls, value: Any, index: int) -> HubApp:
        where = f"apps[{index}]"
        if not isinstance(value, dict):
            raise HubCatalogError(f"{where} must be an object")
        _exact_keys(
            value,
            {
                "id",
                "name",
                "nameZh",
                "version",
                "summary",
                "category",
                "icon",
                "sourceUrl",
                "featured",
                "imageStorage",
                "package",
                "bundle",
                "integrationStatus",
                "integrationNote",
            },
            where,
        )
        app_id = _text(value.get("id"), f"{where}.id", maximum=64)
        if _APP_ID.fullmatch(app_id) is None:
            raise HubCatalogError(f"{where}.id is invalid")
        category = value.get("category")
        if category not in _CATEGORIES:
            raise HubCatalogError(f"{where}.category is unsupported")
        featured = value.get("featured")
        if not isinstance(featured, bool):
            raise HubCatalogError(f"{where}.featured must be boolean")
        integration_status = value.get("integrationStatus")
        if integration_status not in {"available", "integration-pending"}:
            raise HubCatalogError(f"{where}.integrationStatus is unsupported")
        package_value = value.get("package")
        package = (
            HubDockerPackage.parse(package_value, f"{where}.package")
            if package_value is not None
            else None
        )
        bundle_value = value.get("bundle")
        try:
            bundle = (
                HubBundlePackage.parse(bundle_value, f"{where}.bundle")
                if bundle_value is not None
                else None
            )
        except HubBundleError as exc:
            raise HubCatalogError(str(exc)) from exc
        if package is not None and bundle is not None:
            raise HubCatalogError(f"{where} cannot publish package and bundle together")
        if package is not None and integration_status != "available":
            raise HubCatalogError(
                f"{where} can publish a single-container package only when available"
            )
        if integration_status == "available" and package is None and bundle is None:
            raise HubCatalogError(f"{where} must publish a package or bundle when available")
        artifact = package or bundle
        image_storage_value = value.get("imageStorage")
        image_storage = (
            HubImageStorage.parse(
                image_storage_value,
                f"{where}.imageStorage",
                expected_architectures=artifact.architectures,
            )
            if artifact is not None and image_storage_value is not None
            else None
        )
        if artifact is not None and image_storage is None:
            raise HubCatalogError(f"{where} must publish release-attested image storage metadata")
        if artifact is None and image_storage_value is not None:
            raise HubCatalogError(
                f"{where} cannot publish image storage metadata without a package"
            )
        version = _text(value.get("version"), f"{where}.version", maximum=32)
        if _VERSION.fullmatch(version) is None:
            raise HubCatalogError(f"{where}.version is invalid")
        if bundle is not None and version != bundle.upgrade_policy.application_version:
            raise HubCatalogError(f"{where}.version must match bundle upgrade policy")
        return cls(
            id=app_id,
            name=_text(value.get("name"), f"{where}.name", maximum=80),
            name_zh=_text(value.get("nameZh"), f"{where}.nameZh", maximum=80),
            version=version,
            summary=_text(value.get("summary"), f"{where}.summary", maximum=240),
            category=str(category),
            icon=_text(value.get("icon"), f"{where}.icon", maximum=64),
            source_url=_https_url(value.get("sourceUrl"), f"{where}.sourceUrl"),
            featured=featured,
            image_storage=image_storage,
            package=package,
            bundle=bundle,
            integration_status=str(integration_status),
            integration_note=_text(
                value.get("integrationNote"), f"{where}.integrationNote", maximum=240
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "nameZh": self.name_zh,
            "version": self.version,
            "summary": self.summary,
            "category": self.category,
            "icon": self.icon,
            "sourceUrl": self.source_url,
            "featured": self.featured,
            "imageStorage": self.image_storage.to_dict() if self.image_storage else None,
            "package": self.package.to_dict() if self.package else None,
            "bundle": self.bundle.to_dict() if self.bundle else None,
            "integrationStatus": self.integration_status,
            "integrationNote": self.integration_note,
        }


@dataclass(frozen=True)
class HubCatalog:
    version: str
    publisher_id: str
    publisher_name: str
    apps: tuple[HubApp, ...]
    digest: str
    source: str

    @classmethod
    def from_mapping(cls, value: Any, *, source: str = "memory") -> HubCatalog:
        if not isinstance(value, dict):
            raise HubCatalogError("catalog must be an object")
        _exact_keys(value, {"schema", "version", "publisher", "apps"}, "catalog")
        if value.get("schema") != CATALOG_SCHEMA:
            raise HubCatalogError("catalog schema is unsupported")
        publisher = value.get("publisher")
        if not isinstance(publisher, dict):
            raise HubCatalogError("catalog.publisher must be an object")
        _exact_keys(publisher, {"id", "name"}, "catalog.publisher")
        raw_apps = value.get("apps")
        if not isinstance(raw_apps, list) or not 1 <= len(raw_apps) <= MAX_APPS:
            raise HubCatalogError("catalog.apps must be a non-empty bounded list")
        apps = tuple(HubApp.parse(item, index) for index, item in enumerate(raw_apps))
        ids = [item.id for item in apps]
        if len(set(ids)) != len(ids):
            raise HubCatalogError("catalog contains duplicate app ids")
        canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return cls(
            version=_text(value.get("version"), "catalog.version", maximum=64),
            publisher_id=_text(publisher.get("id"), "catalog.publisher.id", maximum=64),
            publisher_name=_text(publisher.get("name"), "catalog.publisher.name", maximum=80),
            apps=apps,
            digest=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            source=source,
        )

    @classmethod
    def load(cls, path: str | Path | None = None) -> HubCatalog:
        configured = path or os.environ.get("ECHO_HUB_CATALOG")
        bundled = Path(__file__).with_name("catalog.json")
        catalog_path = Path(configured).expanduser() if configured else bundled
        try:
            payload = catalog_path.read_bytes()
        except OSError as exc:
            raise HubCatalogError(f"hub catalog unavailable: {catalog_path}") from exc
        if not payload or len(payload) > MAX_CATALOG_BYTES:
            raise HubCatalogError("hub catalog size is invalid")
        actual_digest = hashlib.sha256(payload).hexdigest()
        expected_digest = os.environ.get("ECHO_HUB_CATALOG_SHA256", "").strip().lower()
        if expected_digest:
            if _SHA256.fullmatch(expected_digest) is None or actual_digest != expected_digest:
                raise HubCatalogError("hub catalog checksum mismatch")
        elif configured and os.environ.get("ECHO_APPLIANCE") == "1":
            raise HubCatalogError("a custom appliance hub catalog requires ECHO_HUB_CATALOG_SHA256")
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HubCatalogError("hub catalog is not valid UTF-8 JSON") from exc
        return cls.from_mapping(value, source=str(catalog_path))

    def get(self, app_id: str) -> HubApp | None:
        return next((item for item in self.apps if item.id == app_id), None)


__all__ = [
    "CATALOG_SCHEMA",
    "DOCKER_PACKAGE_SCHEMA",
    "IMAGE_STORAGE_SCHEMA",
    "HubApp",
    "HubCatalog",
    "HubCatalogError",
    "HubBundlePackage",
    "HubDockerPackage",
    "HubImageStorage",
]
