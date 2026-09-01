"""Strict multi-container package contract for Echo Hub.

This module validates trusted catalog data only.  It deliberately models a
small, auditable subset instead of accepting Compose YAML or caller-supplied
Docker configuration.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

BUNDLE_PACKAGE_SCHEMA = "echo.hub.bundle-package.v1"
_NAME = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_IMAGE = re.compile(
    r"^[a-z0-9]+(?:[._-][a-z0-9]+)*(?::[0-9]+)?/"
    r"[a-z0-9]+(?:[._/-][a-z0-9]+)*@sha256:[0-9a-f]{64}$"
)
_ENV_KEY = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+){0,3}(?:-[a-z0-9.-]+)?$")
_ARCHITECTURES = frozenset({"amd64", "arm64"})
_ROLES = frozenset({"app", "database", "cache", "worker"})
_RUNTIME_PROFILES = frozenset({"unprivileged", "data-root-dropper", "web-root-dropper"})
_PROVIDERS = frozenset({"lan-discovery"})
_NETWORK_MODES = frozenset({"bridge", "host"})


class HubBundleError(ValueError):
    """The multi-container contract is malformed or exceeds safe bounds."""


def _exact(value: dict[str, Any], allowed: set[str], where: str) -> None:
    unexpected = sorted(set(value) - allowed)
    missing = sorted(allowed - set(value))
    if unexpected or missing:
        details = []
        if unexpected:
            details.append(f"unexpected: {', '.join(unexpected)}")
        if missing:
            details.append(f"missing: {', '.join(missing)}")
        raise HubBundleError(f"{where} fields are invalid ({'; '.join(details)})")


def _text(value: Any, where: str, *, maximum: int = 256) -> str:
    if not isinstance(value, str):
        raise HubBundleError(f"{where} must be a string")
    result = value.strip()
    if not result or len(result) > maximum or "\x00" in result:
        raise HubBundleError(f"{where} is invalid")
    return result


def _name(value: Any, where: str) -> str:
    result = _text(value, where, maximum=64)
    if _NAME.fullmatch(result) is None:
        raise HubBundleError(f"{where} must be a portable name")
    return result


def _path(value: Any, where: str) -> str:
    result = _text(value, where)
    parsed = PurePosixPath(result)
    if not parsed.is_absolute() or ".." in parsed.parts or result == "/":
        raise HubBundleError(f"{where} must be a bounded absolute container path")
    return str(parsed)


def _argv(value: Any, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > 32:
        raise HubBundleError(f"{where} must be a bounded argv list")
    return tuple(_text(item, f"{where}[{index}]", maximum=512) for index, item in enumerate(value))


@dataclass(frozen=True)
class BundleNetwork:
    name: str
    internal: bool

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "internal": self.internal}


@dataclass(frozen=True)
class BundleVolume:
    name: str
    source: str
    relative_path: str | None
    retention: str
    snapshot_on_update: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source": self.source,
            "relativePath": self.relative_path,
            "retention": self.retention,
            "snapshotOnUpdate": self.snapshot_on_update,
        }


@dataclass(frozen=True)
class BundleSecret:
    name: str
    generation: str
    bytes: int
    reveal_once: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "generation": self.generation,
            "bytes": self.bytes,
            "revealOnce": self.reveal_once,
        }


@dataclass(frozen=True)
class BundlePort:
    container: int
    host: int
    protocol: str

    def to_dict(self) -> dict[str, Any]:
        return {"container": self.container, "host": self.host, "protocol": self.protocol}


@dataclass(frozen=True)
class BundleMount:
    volume: str
    target: str
    read_only: bool

    def to_dict(self) -> dict[str, Any]:
        return {"volume": self.volume, "target": self.target, "readOnly": self.read_only}


@dataclass(frozen=True)
class BundleSecretMount:
    secret: str
    target: str

    def to_dict(self) -> dict[str, Any]:
        return {"secret": self.secret, "target": self.target}


@dataclass(frozen=True)
class BundleHealthcheck:
    command: tuple[str, ...]
    interval_seconds: int
    timeout_seconds: int
    retries: int
    start_period_seconds: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": list(self.command),
            "intervalSeconds": self.interval_seconds,
            "timeoutSeconds": self.timeout_seconds,
            "retries": self.retries,
            "startPeriodSeconds": self.start_period_seconds,
        }


@dataclass(frozen=True)
class BundleRuntime:
    profile: str
    memory_mib: int
    pids: int
    shm_size_mib: int
    read_only_rootfs: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "memoryMiB": self.memory_mib,
            "pids": self.pids,
            "shmSizeMiB": self.shm_size_mib,
            "readOnlyRootfs": self.read_only_rootfs,
        }


@dataclass(frozen=True)
class BundleService:
    id: str
    role: str
    version: str
    image: str
    depends_on: tuple[str, ...]
    networks: tuple[str, ...]
    ports: tuple[BundlePort, ...]
    mounts: tuple[BundleMount, ...]
    secrets: tuple[BundleSecretMount, ...]
    secret_environment: tuple[tuple[str, str], ...]
    environment: tuple[tuple[str, str], ...]
    entrypoint: tuple[str, ...]
    command: tuple[str, ...]
    healthcheck: BundleHealthcheck | None
    runtime: BundleRuntime
    network_mode: str = "bridge"

    def to_dict(self) -> dict[str, Any]:
        result = {
            "id": self.id,
            "role": self.role,
            "version": self.version,
            "image": self.image,
            "dependsOn": list(self.depends_on),
            "networks": list(self.networks),
            "ports": [item.to_dict() for item in self.ports],
            "mounts": [item.to_dict() for item in self.mounts],
            "secrets": [item.to_dict() for item in self.secrets],
            "secretEnvironment": dict(self.secret_environment),
            "environment": dict(self.environment),
            "entrypoint": list(self.entrypoint),
            "command": list(self.command),
            "healthcheck": self.healthcheck.to_dict() if self.healthcheck else None,
            "runtime": self.runtime.to_dict(),
        }
        if self.network_mode != "bridge":
            result["networkMode"] = self.network_mode
        return result


@dataclass(frozen=True)
class BundleUpgradePolicy:
    application_version: str
    max_major_step: int
    snapshot_volumes: tuple[str, ...]
    service_order: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "applicationVersion": self.application_version,
            "maxMajorStep": self.max_major_step,
            "snapshotVolumes": list(self.snapshot_volumes),
            "serviceOrder": list(self.service_order),
        }


@dataclass(frozen=True)
class HubBundlePackage:
    architectures: tuple[str, ...]
    public_service: str
    networks: tuple[BundleNetwork, ...]
    volumes: tuple[BundleVolume, ...]
    secrets: tuple[BundleSecret, ...]
    services: tuple[BundleService, ...]
    upgrade_policy: BundleUpgradePolicy
    providers: tuple[str, ...] = ()

    @property
    def digest(self) -> str:
        canonical = json.dumps(
            self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @classmethod
    def parse(cls, value: Any, where: str) -> HubBundlePackage:
        if not isinstance(value, dict):
            raise HubBundleError(f"{where} must be an object")
        fields = {
            "schema",
            "architectures",
            "publicService",
            "networks",
            "volumes",
            "secrets",
            "services",
            "upgradePolicy",
        }
        actual_fields = set(value)
        if actual_fields not in {frozenset(fields), frozenset(fields | {"providers"})}:
            _exact(value, fields, where)
        if value["schema"] != BUNDLE_PACKAGE_SCHEMA:
            raise HubBundleError(f"{where}.schema is unsupported")
        architectures = cls._architectures(value["architectures"], where)
        networks = cls._networks(value["networks"], where)
        volumes = cls._volumes(value["volumes"], where)
        secrets = cls._secrets(value["secrets"], where)
        services = cls._services(value["services"], where)
        public_service = _name(value["publicService"], f"{where}.publicService")
        upgrade_policy = cls._upgrade_policy(value["upgradePolicy"], where)
        providers = cls._providers(value.get("providers", []), where)
        cls._validate_references(
            public_service=public_service,
            networks=networks,
            volumes=volumes,
            secrets=secrets,
            services=services,
            upgrade_policy=upgrade_policy,
            where=where,
        )
        return cls(
            architectures=architectures,
            public_service=public_service,
            networks=networks,
            volumes=volumes,
            secrets=secrets,
            services=services,
            upgrade_policy=upgrade_policy,
            providers=providers,
        )

    @staticmethod
    def _architectures(value: Any, where: str) -> tuple[str, ...]:
        if not isinstance(value, list) or not value or len(value) > 2:
            raise HubBundleError(f"{where}.architectures is invalid")
        result = tuple(dict.fromkeys(str(item) for item in value))
        if len(result) != len(value) or not set(result) <= _ARCHITECTURES:
            raise HubBundleError(f"{where}.architectures is invalid")
        return result

    @staticmethod
    def _providers(value: Any, where: str) -> tuple[str, ...]:
        if not isinstance(value, list) or len(value) > len(_PROVIDERS):
            raise HubBundleError(f"{where}.providers is invalid")
        result = tuple(str(item) for item in value)
        if len(set(result)) != len(result) or not set(result) <= _PROVIDERS:
            raise HubBundleError(f"{where}.providers is invalid")
        return result

    @staticmethod
    def _networks(value: Any, where: str) -> tuple[BundleNetwork, ...]:
        if not isinstance(value, list) or len(value) > 8:
            raise HubBundleError(f"{where}.networks is invalid")
        result: list[BundleNetwork] = []
        for index, raw in enumerate(value):
            item_where = f"{where}.networks[{index}]"
            if not isinstance(raw, dict):
                raise HubBundleError(f"{item_where} must be an object")
            _exact(raw, {"name", "internal"}, item_where)
            if not isinstance(raw["internal"], bool):
                raise HubBundleError(f"{item_where}.internal must be boolean")
            result.append(BundleNetwork(_name(raw["name"], f"{item_where}.name"), raw["internal"]))
        if len({item.name for item in result}) != len(result):
            raise HubBundleError(f"{where}.networks contains duplicate names")
        return tuple(result)

    @staticmethod
    def _volumes(value: Any, where: str) -> tuple[BundleVolume, ...]:
        if not isinstance(value, list) or not value or len(value) > 32:
            raise HubBundleError(f"{where}.volumes is invalid")
        result: list[BundleVolume] = []
        for index, raw in enumerate(value):
            item_where = f"{where}.volumes[{index}]"
            if not isinstance(raw, dict):
                raise HubBundleError(f"{item_where} must be an object")
            _exact(
                raw,
                {"name", "source", "relativePath", "retention", "snapshotOnUpdate"},
                item_where,
            )
            if raw["retention"] != "retain":
                raise HubBundleError(f"{item_where}.retention must be retain")
            source = raw["source"]
            relative_path = raw["relativePath"]
            snapshot_on_update = raw["snapshotOnUpdate"]
            if source not in {"app-data", "nas-data"} or not isinstance(snapshot_on_update, bool):
                raise HubBundleError(f"{item_where} storage policy is invalid")
            if source == "app-data":
                if relative_path is not None or snapshot_on_update is not True:
                    raise HubBundleError(
                        f"{item_where} app-data must use snapshots and no relative path"
                    )
                parsed_relative_path = None
            else:
                parsed_relative_path = HubBundlePackage._relative_path(
                    relative_path, f"{item_where}.relativePath"
                )
                if snapshot_on_update is not False:
                    raise HubBundleError(
                        f"{item_where} nas-data is retained outside update snapshots"
                    )
            result.append(
                BundleVolume(
                    _name(raw["name"], f"{item_where}.name"),
                    str(source),
                    parsed_relative_path,
                    "retain",
                    snapshot_on_update,
                )
            )
        if len({item.name for item in result}) != len(result):
            raise HubBundleError(f"{where}.volumes contains duplicate names")
        return tuple(result)

    @staticmethod
    def _relative_path(value: Any, where: str) -> str:
        result = _text(value, where, maximum=192)
        parsed = PurePosixPath(result)
        if parsed.is_absolute() or not 1 <= len(parsed.parts) <= 4:
            raise HubBundleError(f"{where} must be a bounded NAS-relative path")
        if any(_NAME.fullmatch(part) is None for part in parsed.parts):
            raise HubBundleError(f"{where} contains a non-portable path segment")
        canonical = "/".join(parsed.parts)
        if result != canonical:
            raise HubBundleError(f"{where} must be canonical")
        return canonical

    @staticmethod
    def _secrets(value: Any, where: str) -> tuple[BundleSecret, ...]:
        if not isinstance(value, list) or len(value) > 32:
            raise HubBundleError(f"{where}.secrets is invalid")
        result: list[BundleSecret] = []
        for index, raw in enumerate(value):
            item_where = f"{where}.secrets[{index}]"
            if not isinstance(raw, dict):
                raise HubBundleError(f"{item_where} must be an object")
            _exact(raw, {"name", "generation", "bytes", "revealOnce"}, item_where)
            if raw["generation"] not in {"random-base64url", "random-alphanumeric"}:
                raise HubBundleError(f"{item_where}.generation is unsupported")
            byte_count = raw["bytes"]
            reveal_once = raw["revealOnce"]
            if (
                not isinstance(byte_count, int)
                or isinstance(byte_count, bool)
                or not 16 <= byte_count <= 64
                or not isinstance(reveal_once, bool)
            ):
                raise HubBundleError(f"{item_where} generation bounds are invalid")
            result.append(
                BundleSecret(
                    _name(raw["name"], f"{item_where}.name"),
                    str(raw["generation"]),
                    byte_count,
                    reveal_once,
                )
            )
        if len({item.name for item in result}) != len(result):
            raise HubBundleError(f"{where}.secrets contains duplicate names")
        return tuple(result)

    @classmethod
    def _services(cls, value: Any, where: str) -> tuple[BundleService, ...]:
        if not isinstance(value, list) or not 1 <= len(value) <= 16:
            raise HubBundleError(f"{where}.services must contain 1 to 16 services")
        result = tuple(
            cls._service(raw, f"{where}.services[{index}]") for index, raw in enumerate(value)
        )
        if len({item.id for item in result}) != len(result):
            raise HubBundleError(f"{where}.services contains duplicate ids")
        return result

    @classmethod
    def _service(cls, raw: Any, where: str) -> BundleService:
        if not isinstance(raw, dict):
            raise HubBundleError(f"{where} must be an object")
        fields = {
            "id",
            "role",
            "version",
            "image",
            "dependsOn",
            "networks",
            "ports",
            "mounts",
            "secrets",
            "secretEnvironment",
            "environment",
            "entrypoint",
            "command",
            "healthcheck",
            "runtime",
        }
        if set(raw) not in {frozenset(fields), frozenset(fields | {"networkMode"})}:
            _exact(raw, fields, where)
        role = raw["role"]
        if role not in _ROLES:
            raise HubBundleError(f"{where}.role is unsupported")
        version = _text(raw["version"], f"{where}.version", maximum=64)
        if _VERSION.fullmatch(version) is None:
            raise HubBundleError(f"{where}.version is invalid")
        image = _text(raw["image"], f"{where}.image", maximum=512)
        if _IMAGE.fullmatch(image) is None:
            raise HubBundleError(f"{where}.image must use an immutable sha256 digest")
        depends_on = cls._name_list(raw["dependsOn"], f"{where}.dependsOn", maximum=15)
        network_mode = raw.get("networkMode", "bridge")
        if network_mode not in _NETWORK_MODES:
            raise HubBundleError(f"{where}.networkMode is unsupported")
        networks = cls._name_list(
            raw["networks"],
            f"{where}.networks",
            maximum=8,
            required=network_mode == "bridge",
        )
        ports = cls._ports(raw["ports"], where)
        mounts = cls._mounts(raw["mounts"], where)
        secrets = cls._secret_mounts(raw["secrets"], where)
        secret_environment = cls._secret_environment(raw["secretEnvironment"], where)
        environment = cls._environment(raw["environment"], where)
        if set(dict(secret_environment)) & set(dict(environment)):
            raise HubBundleError(f"{where} cannot define one environment key twice")
        if secret_environment and not (raw["entrypoint"] or raw["command"]):
            raise HubBundleError(
                f"{where}.secretEnvironment requires an explicit original process argv"
            )
        healthcheck = cls._healthcheck(raw["healthcheck"], where)
        return BundleService(
            id=_name(raw["id"], f"{where}.id"),
            role=str(role),
            version=version,
            image=image,
            depends_on=depends_on,
            networks=networks,
            ports=ports,
            mounts=mounts,
            secrets=secrets,
            secret_environment=secret_environment,
            environment=environment,
            entrypoint=_argv(raw["entrypoint"], f"{where}.entrypoint"),
            command=_argv(raw["command"], f"{where}.command"),
            healthcheck=healthcheck,
            runtime=cls._runtime(raw["runtime"], where),
            network_mode=str(network_mode),
        )

    @staticmethod
    def _name_list(
        value: Any, where: str, *, maximum: int, required: bool = False
    ) -> tuple[str, ...]:
        if not isinstance(value, list) or len(value) > maximum or (required and not value):
            raise HubBundleError(f"{where} is invalid")
        result = tuple(_name(item, f"{where}[{index}]") for index, item in enumerate(value))
        if len(set(result)) != len(result):
            raise HubBundleError(f"{where} contains duplicates")
        return result

    @staticmethod
    def _ports(value: Any, where: str) -> tuple[BundlePort, ...]:
        if not isinstance(value, list) or len(value) > 16:
            raise HubBundleError(f"{where}.ports is invalid")
        result: list[BundlePort] = []
        for index, raw in enumerate(value):
            item_where = f"{where}.ports[{index}]"
            if not isinstance(raw, dict):
                raise HubBundleError(f"{item_where} must be an object")
            _exact(raw, {"container", "host", "protocol"}, item_where)
            container = raw["container"]
            host = raw["host"]
            if (
                not isinstance(container, int)
                or isinstance(container, bool)
                or not 1 <= container <= 65535
                or not isinstance(host, int)
                or isinstance(host, bool)
                or not 1024 <= host <= 65535
                or raw["protocol"] not in {"tcp", "udp"}
            ):
                raise HubBundleError(f"{item_where} is invalid")
            result.append(BundlePort(container, host, str(raw["protocol"])))
        if len({(item.host, item.protocol) for item in result}) != len(result):
            raise HubBundleError(f"{where}.ports contains duplicate host ports")
        return tuple(result)

    @staticmethod
    def _mounts(value: Any, where: str) -> tuple[BundleMount, ...]:
        if not isinstance(value, list) or len(value) > 32:
            raise HubBundleError(f"{where}.mounts is invalid")
        result: list[BundleMount] = []
        for index, raw in enumerate(value):
            item_where = f"{where}.mounts[{index}]"
            if not isinstance(raw, dict):
                raise HubBundleError(f"{item_where} must be an object")
            _exact(raw, {"volume", "target", "readOnly"}, item_where)
            if not isinstance(raw["readOnly"], bool):
                raise HubBundleError(f"{item_where}.readOnly must be boolean")
            result.append(
                BundleMount(
                    _name(raw["volume"], f"{item_where}.volume"),
                    _path(raw["target"], f"{item_where}.target"),
                    raw["readOnly"],
                )
            )
        if len({item.target for item in result}) != len(result):
            raise HubBundleError(f"{where}.mounts contains duplicate targets")
        return tuple(result)

    @staticmethod
    def _secret_mounts(value: Any, where: str) -> tuple[BundleSecretMount, ...]:
        if not isinstance(value, list) or len(value) > 32:
            raise HubBundleError(f"{where}.secrets is invalid")
        result: list[BundleSecretMount] = []
        for index, raw in enumerate(value):
            item_where = f"{where}.secrets[{index}]"
            if not isinstance(raw, dict):
                raise HubBundleError(f"{item_where} must be an object")
            _exact(raw, {"secret", "target"}, item_where)
            secret = _name(raw["secret"], f"{item_where}.secret")
            target = _path(raw["target"], f"{item_where}.target")
            if target != f"/run/secrets/{secret}":
                raise HubBundleError(f"{item_where}.target must match its secret name")
            result.append(BundleSecretMount(secret, target))
        if len({item.secret for item in result}) != len(result):
            raise HubBundleError(f"{where}.secrets contains duplicate mounts")
        return tuple(result)

    @staticmethod
    def _environment(value: Any, where: str) -> tuple[tuple[str, str], ...]:
        if not isinstance(value, dict) or len(value) > 64:
            raise HubBundleError(f"{where}.environment is invalid")
        result: list[tuple[str, str]] = []
        for key, raw in sorted(value.items()):
            if _ENV_KEY.fullmatch(str(key)) is None:
                raise HubBundleError(f"{where}.environment has an invalid key")
            text = _text(raw, f"{where}.environment.{key}", maximum=512)
            if "${" in text:
                raise HubBundleError(f"{where}.environment cannot contain interpolation")
            result.append((str(key), text))
        return tuple(result)

    @staticmethod
    def _secret_environment(value: Any, where: str) -> tuple[tuple[str, str], ...]:
        if not isinstance(value, dict) or len(value) > 32:
            raise HubBundleError(f"{where}.secretEnvironment is invalid")
        result: list[tuple[str, str]] = []
        for key, raw in sorted(value.items()):
            if _ENV_KEY.fullmatch(str(key)) is None:
                raise HubBundleError(f"{where}.secretEnvironment has an invalid key")
            result.append(
                (
                    str(key),
                    _name(raw, f"{where}.secretEnvironment.{key}"),
                )
            )
        return tuple(result)

    @staticmethod
    def _healthcheck(value: Any, where: str) -> BundleHealthcheck | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise HubBundleError(f"{where}.healthcheck must be an object or null")
        _exact(
            value,
            {
                "command",
                "intervalSeconds",
                "timeoutSeconds",
                "retries",
                "startPeriodSeconds",
            },
            f"{where}.healthcheck",
        )
        command = _argv(value["command"], f"{where}.healthcheck.command")
        if not command:
            raise HubBundleError(f"{where}.healthcheck.command cannot be empty")
        bounds = {
            "intervalSeconds": (2, 300),
            "timeoutSeconds": (1, 60),
            "retries": (1, 20),
            "startPeriodSeconds": (0, 600),
        }
        parsed: dict[str, int] = {}
        for key, (minimum, maximum) in bounds.items():
            raw = value[key]
            if not isinstance(raw, int) or isinstance(raw, bool) or not minimum <= raw <= maximum:
                raise HubBundleError(f"{where}.healthcheck.{key} is invalid")
            parsed[key] = raw
        return BundleHealthcheck(
            command,
            parsed["intervalSeconds"],
            parsed["timeoutSeconds"],
            parsed["retries"],
            parsed["startPeriodSeconds"],
        )

    @staticmethod
    def _runtime(value: Any, where: str) -> BundleRuntime:
        item_where = f"{where}.runtime"
        if not isinstance(value, dict):
            raise HubBundleError(f"{item_where} must be an object")
        _exact(
            value,
            {"profile", "memoryMiB", "pids", "shmSizeMiB", "readOnlyRootfs"},
            item_where,
        )
        profile = value["profile"]
        memory_mib = value["memoryMiB"]
        pids = value["pids"]
        shm_size_mib = value["shmSizeMiB"]
        read_only_rootfs = value["readOnlyRootfs"]
        if profile not in _RUNTIME_PROFILES:
            raise HubBundleError(f"{item_where}.profile is unsupported")
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
            raise HubBundleError(f"{item_where} resource bounds are invalid")
        return BundleRuntime(str(profile), memory_mib, pids, shm_size_mib, read_only_rootfs)

    @classmethod
    def _upgrade_policy(cls, value: Any, where: str) -> BundleUpgradePolicy:
        item_where = f"{where}.upgradePolicy"
        if not isinstance(value, dict):
            raise HubBundleError(f"{item_where} must be an object")
        _exact(
            value,
            {"applicationVersion", "maxMajorStep", "snapshotVolumes", "serviceOrder"},
            item_where,
        )
        version = _text(value["applicationVersion"], f"{item_where}.applicationVersion", maximum=64)
        if _VERSION.fullmatch(version) is None:
            raise HubBundleError(f"{item_where}.applicationVersion is invalid")
        max_major_step = value["maxMajorStep"]
        if max_major_step != 1:
            raise HubBundleError(f"{item_where}.maxMajorStep must be 1")
        return BundleUpgradePolicy(
            version,
            1,
            cls._name_list(
                value["snapshotVolumes"], f"{item_where}.snapshotVolumes", maximum=32, required=True
            ),
            cls._name_list(
                value["serviceOrder"], f"{item_where}.serviceOrder", maximum=16, required=True
            ),
        )

    @staticmethod
    def _validate_references(
        *,
        public_service: str,
        networks: tuple[BundleNetwork, ...],
        volumes: tuple[BundleVolume, ...],
        secrets: tuple[BundleSecret, ...],
        services: tuple[BundleService, ...],
        upgrade_policy: BundleUpgradePolicy,
        where: str,
    ) -> None:
        network_map = {item.name: item for item in networks}
        volume_names = {item.name for item in volumes}
        volume_map = {item.name: item for item in volumes}
        secret_names = {item.name for item in secrets}
        service_map = {item.id: item for item in services}
        if public_service not in service_map:
            raise HubBundleError(f"{where}.publicService does not exist")
        if not service_map[public_service].ports:
            raise HubBundleError(f"{where}.publicService must publish at least one port")
        if (
            any(port.container < 1024 for port in service_map[public_service].ports)
            and service_map[public_service].runtime.profile != "web-root-dropper"
        ):
            raise HubBundleError(
                f"{where}.publicService needs web-root-dropper for a privileged container port"
            )
        all_host_ports: set[tuple[int, str]] = set()
        for service in services:
            if service.network_mode == "host":
                if (
                    service.id != public_service
                    or service.networks
                    or service.runtime.profile != "unprivileged"
                    or any(
                        port.protocol != "tcp" or port.container != port.host
                        for port in service.ports
                    )
                ):
                    raise HubBundleError(
                        f"{where}.{service.id} host networking exceeds the safe LAN boundary"
                    )
            elif not service.networks:
                raise HubBundleError(f"{where}.{service.id} bridge networking is incomplete")
            if service.id != public_service and service.ports:
                raise HubBundleError(f"{where} only publicService may publish ports")
            for port in service.ports:
                key = (port.host, port.protocol)
                if key in all_host_ports:
                    raise HubBundleError(f"{where} contains duplicate host ports")
                all_host_ports.add(key)
            if not set(service.depends_on) <= set(service_map) - {service.id}:
                raise HubBundleError(f"{where}.{service.id} has invalid dependencies")
            if not set(service.networks) <= set(network_map):
                raise HubBundleError(f"{where}.{service.id} references an unknown network")
            if service.role in {"database", "cache"} and any(
                not network_map[name].internal for name in service.networks
            ):
                raise HubBundleError(f"{where}.{service.id} backend networks must be internal")
            if (
                service.role in {"database", "cache"}
                and service.runtime.profile == "web-root-dropper"
            ):
                raise HubBundleError(f"{where}.{service.id} cannot use the web runtime profile")
            if not {item.volume for item in service.mounts} <= volume_names:
                raise HubBundleError(f"{where}.{service.id} references an unknown volume")
            if not {item.secret for item in service.secrets} <= secret_names:
                raise HubBundleError(f"{where}.{service.id} references an unknown secret")
            mounted_secret_names = {item.secret for item in service.secrets}
            if not set(dict(service.secret_environment).values()) <= mounted_secret_names:
                raise HubBundleError(
                    f"{where}.{service.id}.secretEnvironment must use mounted secrets"
                )
            secret_targets = {item.target for item in service.secrets}
            for key, env_value in service.environment:
                if key.endswith("_FILE") and env_value not in secret_targets:
                    raise HubBundleError(
                        f"{where}.{service.id}.{key} must reference a mounted secret"
                    )

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(service_id: str) -> None:
            if service_id in visiting:
                raise HubBundleError(f"{where}.services contains a dependency cycle")
            if service_id in visited:
                return
            visiting.add(service_id)
            for dependency in service_map[service_id].depends_on:
                visit(dependency)
            visiting.remove(service_id)
            visited.add(service_id)

        for service_id in service_map:
            visit(service_id)

        order = upgrade_policy.service_order
        if set(order) != set(service_map) or len(order) != len(service_map):
            raise HubBundleError(f"{where}.upgradePolicy.serviceOrder must list every service")
        positions = {service_id: index for index, service_id in enumerate(order)}
        for service in services:
            if any(
                positions[dependency] > positions[service.id] for dependency in service.depends_on
            ):
                raise HubBundleError(f"{where}.upgradePolicy.serviceOrder violates dependencies")
        if not set(upgrade_policy.snapshot_volumes) <= volume_names:
            raise HubBundleError(f"{where}.upgradePolicy references an unknown snapshot volume")
        writable = {
            mount.volume for service in services for mount in service.mounts if not mount.read_only
        }
        required_snapshots = {name for name in writable if volume_map[name].snapshot_on_update}
        if set(upgrade_policy.snapshot_volumes) != required_snapshots:
            raise HubBundleError(
                f"{where}.upgradePolicy must exactly snapshot writable app-data volumes"
            )
        if any(
            not volume_map[name].snapshot_on_update and volume_map[name].source != "nas-data"
            for name in writable
        ):
            raise HubBundleError(f"{where} only nas-data may bypass update snapshots")

    def to_dict(self) -> dict[str, Any]:
        result = {
            "schema": BUNDLE_PACKAGE_SCHEMA,
            "architectures": list(self.architectures),
            "publicService": self.public_service,
            "networks": [item.to_dict() for item in self.networks],
            "volumes": [item.to_dict() for item in self.volumes],
            "secrets": [item.to_dict() for item in self.secrets],
            "services": [item.to_dict() for item in self.services],
            "upgradePolicy": self.upgrade_policy.to_dict(),
        }
        if self.providers:
            result["providers"] = list(self.providers)
        return result


__all__ = ["BUNDLE_PACKAGE_SCHEMA", "HubBundleError", "HubBundlePackage"]
