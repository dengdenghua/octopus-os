#!/usr/bin/env python3
"""Run a destructive, confirmation-bound Echo Hub lifecycle lab on real Docker."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import stat
import subprocess  # nosec B404
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any, Optional
from urllib.parse import SplitResult, urlsplit, urlunsplit

try:
    from deploy.appliance import operations_systemd as systemd
    from deploy.appliance import operations_systemd_lab as operations_lab
except ModuleNotFoundError:
    import operations_systemd as systemd
    import operations_systemd_lab as operations_lab

SCHEMA_VERSION = 9
PLAN_KIND = "echo.hub-physical-lifecycle-plan"
RESULT_KIND = "echo.hub-physical-lifecycle-result"
PAPERLESS_PRIVATE_SECRET_KIND = (  # nosec B105 - schema identifier, not a credential
    "echo.paperless-functional-private-secret"
)
PAPERLESS_PRIVATE_SECRET_NAME = (  # nosec B105 - fixed artifact name, not a credential
    "paperless-functional-private-secret.json"
)
APPS = (
    "jellyfin",
    "navidrome",
    "syncthing",
    "nextcloud",
    "immich",
    "open-webui",
    "qbittorrent",
    "paperless-ngx",
    "home-assistant",
)
PHASES = (
    "install",
    "inspect",
    "stop",
    "inspect-stopped",
    "start",
    "inspect",
    "restart",
    "inspect",
    "uninstall",
    "reinstall",
    "inspect",
    "uninstall",
)
CONTROL_OPERATIONS = frozenset({"start", "stop", "restart"})
APP_ID = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
CONTAINER_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
IMMUTABLE_IMAGE_REFERENCE = re.compile(r"^[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}$")
MAX_JSON_BYTES = 8 * 1024 * 1024
PUBLIC_ENDPOINT_SAMPLE_BYTES = 64 * 1024
PUBLIC_ENDPOINT_MAX_ATTEMPTS = 90
PUBLIC_ENDPOINT_RETRY_SECONDS = 2.0
CONTROL_OPERATION_TIMEOUT_SECONDS = 3600.0
CONTROL_OPERATION_POLL_SECONDS = 1.0
DOCKER_PATH = Path("/usr/bin/docker")
APPROVAL_HEADER = "X-Echo-Approval"
INTENT_HEADER = "X-Echo-Intent"


class HubLifecycleLabError(RuntimeError):
    """The real-device Hub lifecycle lab cannot proceed safely."""


HttpRequest = Callable[
    [
        str,
        str,
        Optional[Mapping[str, Any]],  # noqa: UP045 - imported by macOS Python 3.9 verifier
        Optional[str],  # noqa: UP045 - imported by macOS Python 3.9 verifier
        Optional[Mapping[str, str]],  # noqa: UP045 - imported by macOS Python 3.9 verifier
        float,
    ],
    tuple[int, Mapping[str, Any]],
]
DockerJson = Callable[[Sequence[str]], Any]
EndpointProbe = Callable[[Mapping[str, Any]], Mapping[str, Any]]


def _canonical(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _strict_json(raw: bytes, label: str) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise HubLifecycleLabError(f"{label} contains duplicate JSON keys")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise HubLifecycleLabError(f"{label} is not strict JSON") from exc
    if not isinstance(value, dict):
        raise HubLifecycleLabError(f"{label} must be a JSON object")
    return value


def _read_regular(path: Path, label: str) -> bytes:
    if not path.is_absolute() or path.is_symlink():
        raise HubLifecycleLabError(f"{label} must be one absolute regular file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise HubLifecycleLabError(f"{label} is unavailable") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or not 1 <= before.st_size <= MAX_JSON_BYTES:
            raise HubLifecycleLabError(f"{label} is empty, oversized or unsafe")
        raw = os.read(descriptor, MAX_JSON_BYTES + 1)
        after = os.fstat(descriptor)
        if len(raw) > MAX_JSON_BYTES or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise HubLifecycleLabError(f"{label} changed while it was read")
        return raw
    finally:
        os.close(descriptor)


def _write_new(path: Path, value: Mapping[str, Any], *, mode: int) -> None:
    if not path.is_absolute() or path.parent.is_symlink():
        raise HubLifecycleLabError("Hub lifecycle output must use an absolute safe path")
    raw = _canonical(value)
    if len(raw) > MAX_JSON_BYTES:
        raise HubLifecycleLabError("Hub lifecycle output is oversized")
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        written = 0
        while written < len(raw):
            count = os.write(descriptor, raw[written:])
            if count <= 0:
                raise HubLifecycleLabError("Hub lifecycle output could not be written")
            written += count
        os.fsync(descriptor)
        os.fchmod(descriptor, mode)
    finally:
        os.close(descriptor)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _private_paperless_secret_parent(
    path: Path,
    *,
    public_directories: Sequence[Path],
) -> Path:
    if (
        not path.is_absolute()
        or path.name != PAPERLESS_PRIVATE_SECRET_NAME
        or path.is_symlink()
        or path.parent.is_symlink()
    ):
        raise HubLifecycleLabError("Paperless private password output is unsafe")
    parent = path.parent.resolve(strict=True)
    info = parent.stat()
    if not parent.is_dir() or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise HubLifecycleLabError(
            "Paperless private password directory must be owner-only mode 0700"
        )
    for public_directory in public_directories:
        public_root = public_directory.resolve(strict=True)
        if _is_within(parent, public_root):
            raise HubLifecycleLabError(
                "Paperless private password must stay outside public evidence"
            )
    if path.exists():
        raise HubLifecycleLabError("Paperless private password output must be new")
    return parent


def _write_private_paperless_secret(
    path: Path,
    *,
    plan: Mapping[str, Any],
    password: str,
    public_directories: Sequence[Path],
) -> dict[str, Any]:
    if re.fullmatch(r"[A-Za-z0-9]{24}", password) is None:
        raise HubLifecycleLabError("Paperless private password handoff is invalid")
    parent = _private_paperless_secret_parent(
        path,
        public_directories=public_directories,
    )
    value = {
        "schemaVersion": 1,
        "kind": PAPERLESS_PRIVATE_SECRET_KIND,
        "appId": "paperless-ngx",
        "secretName": "admin-password",
        "hubLifecyclePlanId": plan["planId"],
        "releaseCandidate": plan["releaseCandidate"],
        "password": password,
    }
    _write_new(path, value, mode=0o400)
    directory = os.open(parent, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return value


def _remove_private_paperless_secret(path: Path) -> None:
    path.unlink()
    directory = os.open(
        path.parent.resolve(strict=True),
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _origin(value: str) -> str:
    parsed = urlsplit(str(value).strip())
    try:
        port = parsed.port
    except ValueError as exc:
        raise HubLifecycleLabError("Hub lifecycle base URL is invalid") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or port is None
        and ":" in parsed.netloc.rsplit("]", 1)[-1]
    ):
        raise HubLifecycleLabError("Hub lifecycle base URL must be one HTTP(S) origin")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _http_request(
    method: str,
    url: str,
    payload: Mapping[str, Any] | None,
    token: str | None,
    extra_headers: Mapping[str, str] | None,
    timeout: float,
) -> tuple[int, Mapping[str, Any]]:
    parsed: SplitResult = urlsplit(url)
    if _origin(urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))) == "":  # defensive
        raise HubLifecycleLabError("Hub lifecycle request URL is invalid")
    if parsed.path == "" or not parsed.path.startswith("/") or parsed.query or parsed.fragment:
        raise HubLifecycleLabError("Hub lifecycle request target is invalid")
    body = (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode() if payload else None
    )
    headers = {"Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if extra_headers:
        headers.update(extra_headers)
    connection_type = (
        http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    )
    connection = connection_type(parsed.hostname, parsed.port, timeout=timeout)
    try:
        connection.request(method, parsed.path, body=body, headers=headers)
        response = connection.getresponse()
        raw = response.read(MAX_JSON_BYTES + 1)
    finally:
        connection.close()
    if len(raw) > MAX_JSON_BYTES:
        raise HubLifecycleLabError("Hub lifecycle API response is oversized")
    return response.status, _strict_json(raw, "Hub lifecycle API response")


def _docker_json(arguments: Sequence[str]) -> Any:
    try:
        docker_info = DOCKER_PATH.lstat()
    except OSError as exc:
        raise HubLifecycleLabError("trusted Docker CLI is unavailable") from exc
    if (
        not stat.S_ISREG(docker_info.st_mode)
        or docker_info.st_uid != 0
        or stat.S_IMODE(docker_info.st_mode) & 0o022
        or not os.access(DOCKER_PATH, os.X_OK)
    ):
        raise HubLifecycleLabError("trusted Docker CLI is unsafe")
    optional_inspect = len(arguments) == 2 and arguments[0] == "inspect-optional"
    optional_volume = len(arguments) == 2 and arguments[0] == "volume-inspect-optional"
    if optional_inspect:
        effective_arguments: Sequence[str] = ("inspect", arguments[1])
    elif optional_volume:
        effective_arguments = ("volume", "inspect", arguments[1])
    else:
        effective_arguments = arguments
    result = subprocess.run(  # nosec B603
        [str(DOCKER_PATH), *effective_arguments],
        check=False,
        text=True,
        capture_output=True,
        timeout=60,
    )
    if result.returncode != 0:
        if optional_inspect and "No such object:" in result.stderr:
            return []
        if optional_volume and "No such volume:" in result.stderr:
            return []
        raise HubLifecycleLabError("Docker inspection failed during the Hub lifecycle lab")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise HubLifecycleLabError("Docker inspection returned invalid JSON") from exc


def _request(
    request: HttpRequest,
    method: str,
    url: str,
    *,
    payload: Mapping[str, Any] | None = None,
    token: str | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = 30,
    expected: int = 200,
) -> Mapping[str, Any]:
    status, value = request(method, url, payload, token, headers, timeout)
    if status != expected:
        raise HubLifecycleLabError(f"Hub lifecycle API returned HTTP {status} for {method} {url}")
    return value


def _login(base_url: str, password: str, request: HttpRequest) -> str:
    value = _request(
        request,
        "POST",
        f"{base_url}/api/auth/local/login",
        payload={"username": "admin", "password": password},
    )
    token = value.get("access_token")
    if value.get("success") is not True or not isinstance(token, str) or not token:
        raise HubLifecycleLabError("Hub lifecycle login returned no access token")
    return token


def _public_endpoint_contract(ports: Any) -> dict[str, Any]:
    if not isinstance(ports, list):
        raise HubLifecycleLabError("Hub public endpoint has no bounded port contract")
    web_port = next(
        (
            port.get("host")
            for port in ports
            if isinstance(port, dict) and port.get("protocol") == "tcp"
        ),
        None,
    )
    if not isinstance(web_port, int) or isinstance(web_port, bool) or not 1 <= web_port <= 65535:
        raise HubLifecycleLabError("Hub public endpoint has no TCP responder port")
    return {
        "scheme": "http",
        "host": "127.0.0.1",
        "port": web_port,
        "path": "/",
    }


def _public_endpoint_probe(endpoint: Mapping[str, Any]) -> dict[str, Any]:
    """Wait for one catalog-bound public HTTP responder without retaining page content."""

    if endpoint != _public_endpoint_contract([{"host": endpoint.get("port"), "protocol": "tcp"}]):
        raise HubLifecycleLabError("Hub public endpoint probe contract is invalid")
    port = int(endpoint["port"])
    for attempt in range(1, PUBLIC_ENDPOINT_MAX_ATTEMPTS + 1):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            connection.request(
                "GET",
                "/",
                headers={
                    "Accept": "text/html,application/json;q=0.9,*/*;q=0.1",
                    "Connection": "close",
                    "Host": f"127.0.0.1:{port}",
                    "User-Agent": "Echo-Hub-Physical-Lifecycle/1",
                },
            )
            response = connection.getresponse()
            raw = response.read(PUBLIC_ENDPOINT_SAMPLE_BYTES + 1)
            status = int(response.status)
            if 200 <= status < 500:
                sample = raw[:PUBLIC_ENDPOINT_SAMPLE_BYTES]
                raw_media_type = str(response.getheader("Content-Type") or "")
                media_type = raw_media_type.partition(";")[0].strip().lower()
                if re.fullmatch(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+", media_type) is None:
                    media_type = "unknown"
                return {
                    **dict(endpoint),
                    "status": status,
                    "mediaType": media_type,
                    "sampleBytes": len(sample),
                    "sampleSha256": _sha256(sample),
                    "sampleTruncated": len(raw) > PUBLIC_ENDPOINT_SAMPLE_BYTES,
                    "attempts": attempt,
                }
        except (OSError, http.client.HTTPException):
            pass
        finally:
            connection.close()
        if attempt < PUBLIC_ENDPOINT_MAX_ATTEMPTS:
            time.sleep(PUBLIC_ENDPOINT_RETRY_SECONDS)
    raise HubLifecycleLabError("Hub public endpoint did not become HTTP-ready")


def _artifact_contract(app: Mapping[str, Any]) -> dict[str, Any]:
    app_id = app.get("id")
    package = app.get("package")
    bundle = app.get("bundle")
    if app_id not in APPS or (package is None) == (bundle is None):
        raise HubLifecycleLabError("Hub catalog app has an invalid artifact contract")
    artifact = package if isinstance(package, dict) else bundle
    if not isinstance(artifact, dict):
        raise HubLifecycleLabError("Hub catalog app artifact is invalid")
    if isinstance(bundle, dict):
        services = bundle.get("services")
        volumes = bundle.get("volumes")
        if not isinstance(services, list) or not services or not isinstance(volumes, list):
            raise HubLifecycleLabError("Hub bundle contract is incomplete")
        public_service = bundle.get("publicService")
        service_contracts = []
        for service in services:
            if not isinstance(service, dict) or not APP_ID.fullmatch(str(service.get("id") or "")):
                raise HubLifecycleLabError("Hub bundle service is invalid")
            service_contracts.append(
                {
                    "id": service["id"],
                    "image": service.get("image"),
                    "public": service["id"] == public_service,
                    "ports": service.get("ports"),
                    "mounts": service.get("mounts"),
                    "networks": service.get("networks"),
                    "networkMode": service.get("networkMode", "bridge"),
                    "hasSecrets": bool(service.get("secrets")),
                    "runtime": service.get("runtime"),
                    "healthcheck": service.get("healthcheck") is not None,
                }
            )
        data_volumes = [
            {
                "name": volume.get("name"),
                "source": volume.get("source"),
                "relativePath": volume.get("relativePath"),
            }
            for volume in volumes
            if isinstance(volume, dict)
        ]
        return {
            "kind": "bundle",
            "digest": _sha256(_canonical(artifact)),
            "providers": bundle.get("providers") or [],
            "services": service_contracts,
            "volumes": data_volumes,
            "networks": bundle.get("networks"),
            "endpoint": _public_endpoint_contract(
                next(service["ports"] for service in service_contracts if service["public"] is True)
            ),
        }
    volumes = package.get("volumes") if isinstance(package, dict) else None
    if not isinstance(volumes, list):
        raise HubLifecycleLabError("Hub package volume contract is incomplete")
    return {
        "kind": "package",
        "digest": _sha256(_canonical(artifact)),
        "providers": [],
        "services": [
            {
                "id": "app",
                "image": package.get("image"),
                "public": True,
                "ports": package.get("ports"),
                "mounts": volumes,
                "networks": [],
                "networkMode": "bridge",
                "hasSecrets": False,
                "runtime": None,
                "healthcheck": False,
            }
        ],
        "volumes": [
            {
                "name": volume.get("name"),
                "source": volume.get("source"),
                "relativePath": None,
            }
            for volume in volumes
            if isinstance(volume, dict)
        ],
        "networks": [],
        "endpoint": _public_endpoint_contract(package.get("ports")),
    }


def _catalog_snapshot(
    catalog: Mapping[str, Any],
    *,
    expected_installed: Sequence[str] = (),
) -> dict[str, Any]:
    if (
        catalog.get("schema") != "echo.hub.catalog-response.v1"
        or not SHA256.fullmatch(str(catalog.get("digest") or ""))
        or catalog.get("runtime") != {"available": True, "error": None}
        or catalog.get("architecture") not in {"amd64", "arm64"}
        or not isinstance(catalog.get("apps"), list)
    ):
        raise HubLifecycleLabError("Hub catalog is not ready for the physical lifecycle lab")
    expected_installed_set = set(expected_installed)
    if len(expected_installed_set) != len(expected_installed) or not expected_installed_set <= set(
        APPS
    ):
        raise HubLifecycleLabError("Hub catalog expected installation set is invalid")
    by_id = {
        str(app.get("id")): app
        for app in catalog["apps"]
        if isinstance(app, dict) and isinstance(app.get("id"), str)
    }
    if any(app_id not in by_id for app_id in APPS):
        raise HubLifecycleLabError("Hub catalog does not contain every required physical app")
    apps: dict[str, Any] = {}
    for app_id in APPS:
        app = by_id[app_id]
        installation = app.get("installation")
        is_expected_installed = app_id in expected_installed_set
        blockers = app.get("installBlockers")
        installation_state_matches = (
            installation.get("installed") is is_expected_installed
            if isinstance(installation, dict)
            else False
        )
        availability_matches = (
            app.get("installable") is False
            and isinstance(blockers, list)
            and set(blockers) == {"PORT_IN_USE", "ALREADY_INSTALLED"}
            if is_expected_installed
            else app.get("installable") is True and blockers == []
        )
        if (
            app.get("integrationStatus") != "available"
            or not installation_state_matches
            or not availability_matches
        ):
            raise HubLifecycleLabError(
                f"Hub app {app_id} must be available and match the expected physical state"
            )
        apps[app_id] = _artifact_contract(app)
    return {
        "version": catalog.get("version"),
        "digest": catalog["digest"],
        "architecture": catalog["architecture"],
        "apps": apps,
    }


def _candidate_bundle_identity(
    *,
    candidate_index: Path,
    bundle_root: Path,
    trusted_uid: int,
) -> tuple[dict[str, str], dict[str, Any]]:
    try:
        candidate = operations_lab._candidate_identity(
            candidate_index,
            trusted_uid=trusted_uid,
        )
        root = bundle_root.resolve(strict=True)
        if bundle_root.is_symlink() or not root.is_dir():
            raise HubLifecycleLabError("Hub lifecycle operations bundle root is unsafe")
        base = operations_lab._operations_bundle_identity(
            root,
            candidate,
            trusted_uid=trusted_uid,
        )
        manifest_raw = systemd._safe_regular(
            root / "bundle-manifest.json",
            "Hub lifecycle operations bundle manifest",
            maximum=systemd.MAX_PLAN_BYTES,
            trusted_uid=trusted_uid,
            private=False,
            exact_mode=0o644,
        )
        manifest = _strict_json(manifest_raw, "Hub lifecycle operations bundle manifest")
        artifact = manifest.get("artifact")
        files = manifest.get("files")
        record = files.get("hub_lifecycle_lab.py") if isinstance(files, dict) else None
        tool_raw = systemd._safe_regular(
            root / "hub_lifecycle_lab.py",
            "candidate Hub lifecycle lab tool",
            maximum=systemd.MAX_PLAN_BYTES,
            trusted_uid=trusted_uid,
            private=False,
            exact_mode=0o755,
        )
    except (operations_lab.OperationsSystemdLabError, systemd.OperationsSystemdError) as exc:
        raise HubLifecycleLabError(str(exc)) from exc
    if (
        not isinstance(artifact, dict)
        or not isinstance(artifact.get("entrypoints"), dict)
        or artifact["entrypoints"].get("hubLifecycleLab")
        != "./hub_lifecycle_lab.py plan|run|verify"
        or not isinstance(record, dict)
        or set(record) != {"sha256", "size", "mode"}
        or record.get("sha256") != _sha256(tool_raw)
        or record.get("size") != len(tool_raw)
        or record.get("mode") != "0755"
    ):
        raise HubLifecycleLabError("Hub lifecycle lab tool is not from the release candidate")
    return candidate, {
        **base,
        "rootPath": str(root),
        "hubLabSha256": _sha256(tool_raw),
        "hubLabSize": len(tool_raw),
    }


def _running_candidate(image_reference: str, docker: DockerJson) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for role, name in (
        ("main", "echo-os"),
        ("proxy", "echo-docker-control"),
        ("discovery", "echo-lan-discovery"),
    ):
        container = _inspect_one(name, docker)
        config = container.get("Config")
        state = container.get("State")
        labels = config.get("Labels") if isinstance(config, dict) else None
        container_id = str(container.get("Id") or "")
        if (
            not isinstance(config, dict)
            or not isinstance(state, dict)
            or not isinstance(labels, dict)
            or config.get("Image") != image_reference
            or state.get("Running") is not True
            or labels.get("sh.echo.control-protected") != "true"
            or SHA256.fullmatch(container_id) is None
            or role == "main"
            and labels.get("sh.echo.hub.nas-provider") != "true"
            or role == "proxy"
            and labels.get("sh.echo.hub.data-copy-provider") != "true"
            or role == "discovery"
            and labels.get("sh.echo.hub.lan-discovery-provider") != "true"
        ):
            raise HubLifecycleLabError("running appliance is not the reviewed release candidate")
        if role == "discovery":
            host = container.get("HostConfig")
            mounts = container.get("Mounts")
            health = state.get("Health")
            security_options = host.get("SecurityOpt") if isinstance(host, dict) else None
            tmpfs = host.get("Tmpfs") if isinstance(host, dict) else None
            if (
                config.get("User") != "65534:65534"
                or not isinstance(host, dict)
                or host.get("NetworkMode") != "host"
                or host.get("Privileged") is not False
                or host.get("CapDrop") != ["ALL"]
                or not isinstance(security_options, list)
                or not any(
                    str(option).startswith("no-new-privileges") for option in security_options
                )
                or host.get("ReadonlyRootfs") is not True
                or host.get("PidsLimit") != 32
                or host.get("Memory") != 64 * 1024 * 1024
                or not isinstance(tmpfs, dict)
                or set(tmpfs) != {"/tmp"}
                or not all(option in str(tmpfs["/tmp"]) for option in ("noexec", "nosuid", "nodev"))
                or mounts != []
                or not isinstance(health, dict)
                or health.get("Status") != "healthy"
            ):
                raise HubLifecycleLabError(
                    "running LAN discovery provider violates its minimum-privilege contract"
                )
        result[role] = {"containerId": container_id, "image": image_reference}
    return result


def _storage_volume_names(snapshot: Mapping[str, Any]) -> list[str]:
    apps = snapshot.get("apps")
    if not isinstance(apps, dict):
        raise HubLifecycleLabError("Hub lifecycle catalog storage contract is invalid")
    names: list[str] = []
    for app_id, contract in apps.items():
        if not isinstance(contract, dict):
            raise HubLifecycleLabError("Hub lifecycle app storage contract is invalid")
        for volume in contract.get("volumes") or []:
            if isinstance(volume, dict) and volume.get("source") == "app-data":
                names.append(f"echo-hub-{app_id}-{volume['name']}")
        for service in contract.get("services") or []:
            if isinstance(service, dict) and service.get("hasSecrets") is True:
                names.append(f"echo-hub-{app_id}-secrets-{service['id']}")
    return sorted(names)


def _fresh_storage(
    snapshot: Mapping[str, Any],
    docker: DockerJson,
) -> dict[str, Any]:
    names = _storage_volume_names(snapshot)
    for name in names:
        value = docker(("volume-inspect-optional", name))
        if value != []:
            raise HubLifecycleLabError(
                "Hub lifecycle lab requires a fresh appliance without retained app volumes"
            )
    nas_root = _nas_source(docker)
    apps = snapshot.get("apps")
    if not isinstance(apps, dict):
        raise HubLifecycleLabError("Hub lifecycle catalog storage contract is invalid")
    relative_paths = sorted(
        {
            str(volume["relativePath"])
            for contract in apps.values()
            if isinstance(contract, dict)
            for volume in contract.get("volumes") or []
            if isinstance(volume, dict)
            and volume.get("source") == "nas-data"
            and isinstance(volume.get("relativePath"), str)
        }
    )
    for relative_path in relative_paths:
        directory = Path(nas_root) / relative_path
        if directory.exists() or directory.is_symlink():
            raise HubLifecycleLabError(
                "Hub lifecycle lab requires absent dedicated NAS test directories"
            )
    return {
        "namedVolumesAbsent": True,
        "nasDirectoriesAbsent": True,
        "checkedVolumeCount": len(names),
        "checkedNasDirectoryCount": len(relative_paths),
    }


def build_plan(
    *,
    base_url: str,
    catalog: Mapping[str, Any],
    candidate_index: Path,
    bundle_root: Path,
    output: Path,
    trusted_uid: int | None = None,
    docker: DockerJson = _docker_json,
) -> dict[str, Any]:
    origin = _origin(base_url)
    snapshot = _catalog_snapshot(catalog)
    candidate, bundle = _candidate_bundle_identity(
        candidate_index=candidate_index,
        bundle_root=bundle_root,
        trusted_uid=os.getuid() if trusted_uid is None else trusted_uid,
    )
    runtime = _running_candidate(candidate["immutableReference"], docker)
    baseline = _fresh_storage(snapshot, docker)
    identity: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": PLAN_KIND,
        "baseUrl": origin,
        "releaseCandidate": candidate,
        "operationsBundle": bundle,
        "runtime": runtime,
        "baseline": baseline,
        "catalog": snapshot,
        "apps": list(APPS),
        "phases": list(PHASES),
        "retention": {
            "namedVolumes": True,
            "nasData": True,
            "generatedSecrets": True,
        },
    }
    plan_id = _sha256(_canonical(identity))
    value = {
        **identity,
        "planId": plan_id,
        "confirmation": f"RUN ECHO HUB LIFECYCLE {plan_id}",
    }
    _write_new(output, value, mode=0o400)
    return value


def _validate_catalog_snapshot_value(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "digest", "architecture", "apps"}
        or not isinstance(value["version"], str)
        or not value["version"]
        or SHA256.fullmatch(str(value["digest"])) is None
        or value["architecture"] not in {"amd64", "arm64"}
        or not isinstance(value["apps"], dict)
        or set(value["apps"]) != set(APPS)
    ):
        raise HubLifecycleLabError("Hub lifecycle catalog snapshot is invalid")
    for app_id in APPS:
        contract = value["apps"][app_id]
        if (
            not isinstance(contract, dict)
            or set(contract)
            != {
                "kind",
                "digest",
                "providers",
                "services",
                "volumes",
                "networks",
                "endpoint",
            }
            or contract["kind"] not in {"package", "bundle"}
            or SHA256.fullmatch(str(contract["digest"])) is None
            or not isinstance(contract["services"], list)
            or not contract["services"]
            or not isinstance(contract["volumes"], list)
            or not isinstance(contract["networks"], list)
            or not isinstance(contract["providers"], list)
            or len(contract["providers"]) != len(set(contract["providers"]))
            or not set(contract["providers"]) <= {"lan-discovery"}
            or contract["kind"] == "package"
            and contract["providers"]
        ):
            raise HubLifecycleLabError("Hub lifecycle app catalog contract is invalid")
        volume_names: set[str] = set()
        for volume in contract["volumes"]:
            if (
                not isinstance(volume, dict)
                or set(volume) != {"name", "source", "relativePath"}
                or APP_ID.fullmatch(str(volume["name"])) is None
                or volume["name"] in volume_names
                or volume["source"] not in {"app-data", "nas-data", "nas-root"}
                or (
                    volume["source"] == "nas-data"
                    and (
                        not isinstance(volume["relativePath"], str)
                        or not volume["relativePath"]
                        or volume["relativePath"].startswith("/")
                        or ".." in Path(volume["relativePath"]).parts
                    )
                )
                or (volume["source"] != "nas-data" and volume["relativePath"] is not None)
            ):
                raise HubLifecycleLabError("Hub lifecycle volume catalog contract is invalid")
            volume_names.add(volume["name"])
        network_names: set[str] = set()
        for network in contract["networks"]:
            if (
                not isinstance(network, dict)
                or set(network) != {"name", "internal"}
                or APP_ID.fullmatch(str(network["name"])) is None
                or network["name"] in network_names
                or not isinstance(network["internal"], bool)
            ):
                raise HubLifecycleLabError("Hub lifecycle network catalog contract is invalid")
            network_names.add(network["name"])
        service_ids: set[str] = set()
        public_count = 0
        public_service: dict[str, Any] | None = None
        for service in contract["services"]:
            if (
                not isinstance(service, dict)
                or set(service)
                != {
                    "id",
                    "image",
                    "public",
                    "ports",
                    "mounts",
                    "networks",
                    "networkMode",
                    "hasSecrets",
                    "runtime",
                    "healthcheck",
                }
                or APP_ID.fullmatch(str(service["id"])) is None
                or service["id"] in service_ids
                or IMMUTABLE_IMAGE_REFERENCE.fullmatch(str(service["image"])) is None
                or not isinstance(service["public"], bool)
                or not isinstance(service["ports"], list)
                or not isinstance(service["mounts"], list)
                or not isinstance(service["networks"], list)
                or service["networkMode"] not in {"bridge", "host"}
                or any(not isinstance(name, str) for name in service["networks"])
                or any(name not in network_names for name in service["networks"])
                or len(set(service["networks"])) != len(service["networks"])
                or not isinstance(service["hasSecrets"], bool)
                or not isinstance(service["healthcheck"], bool)
            ):
                raise HubLifecycleLabError("Hub lifecycle service catalog contract is invalid")
            service_ids.add(service["id"])
            public_count += int(service["public"])
            if service["public"]:
                public_service = service
            for port in service["ports"]:
                if (
                    not isinstance(port, dict)
                    or set(port) != {"container", "host", "protocol"}
                    or not all(
                        isinstance(port[key], int) and not isinstance(port[key], bool)
                        for key in ("container", "host")
                    )
                    or not all(1 <= port[key] <= 65535 for key in ("container", "host"))
                    or port["protocol"] not in {"tcp", "udp"}
                ):
                    raise HubLifecycleLabError("Hub lifecycle port catalog contract is invalid")
            for mount in service["mounts"]:
                logical_name = (
                    (mount.get("name") if contract["kind"] == "package" else mount.get("volume"))
                    if isinstance(mount, dict)
                    else None
                )
                expected_mount_keys = (
                    {"source", "name", "target", "readOnly"}
                    if contract["kind"] == "package"
                    else {"volume", "target", "readOnly"}
                )
                if (
                    not isinstance(mount, dict)
                    or set(mount) != expected_mount_keys
                    or not isinstance(logical_name, str)
                    or logical_name not in volume_names
                    or not isinstance(mount["target"], str)
                    or not mount["target"].startswith("/")
                    or not isinstance(mount["readOnly"], bool)
                ):
                    raise HubLifecycleLabError("Hub lifecycle mount catalog contract is invalid")
            runtime = service["runtime"]
            if contract["kind"] == "package":
                if runtime is not None or service["networks"] or service["hasSecrets"]:
                    raise HubLifecycleLabError("Hub package catalog contract is invalid")
            elif (
                not isinstance(runtime, dict)
                or set(runtime) != {"profile", "memoryMiB", "pids", "shmSizeMiB", "readOnlyRootfs"}
                or runtime["profile"]
                not in {"unprivileged", "data-root-dropper", "web-root-dropper"}
                or any(
                    not isinstance(runtime[key], int)
                    or isinstance(runtime[key], bool)
                    or runtime[key] <= 0
                    for key in ("memoryMiB", "pids", "shmSizeMiB")
                )
                or not isinstance(runtime["readOnlyRootfs"], bool)
            ):
                raise HubLifecycleLabError("Hub bundle runtime catalog contract is invalid")
            if service["networkMode"] == "host":
                if (
                    contract["kind"] != "bundle"
                    or service["public"] is not True
                    or service["networks"]
                    or runtime["profile"] != "unprivileged"
                    or any(
                        port["protocol"] != "tcp" or port["container"] != port["host"]
                        for port in service["ports"]
                    )
                ):
                    raise HubLifecycleLabError("Hub host-network service contract is invalid")
            elif contract["kind"] == "bundle" and not service["networks"]:
                raise HubLifecycleLabError("Hub bridge service network contract is incomplete")
        if (
            public_count != 1
            or public_service is None
            or contract["endpoint"] != _public_endpoint_contract(public_service["ports"])
            or (contract["kind"] == "package" and contract["networks"])
        ):
            raise HubLifecycleLabError("Hub lifecycle public service contract is invalid")
    return value


def _validate_plan_value(value: dict[str, Any]) -> dict[str, Any]:
    required = {
        "schemaVersion",
        "kind",
        "baseUrl",
        "releaseCandidate",
        "operationsBundle",
        "runtime",
        "baseline",
        "catalog",
        "apps",
        "phases",
        "retention",
        "planId",
        "confirmation",
    }
    if (
        set(value) != required
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != PLAN_KIND
    ):
        raise HubLifecycleLabError("Hub lifecycle plan has an invalid contract")
    identity = {key: value[key] for key in value if key not in {"planId", "confirmation"}}
    expected_id = _sha256(_canonical(identity))
    catalog = _validate_catalog_snapshot_value(value.get("catalog"))
    candidate = value.get("releaseCandidate")
    bundle = value.get("operationsBundle")
    runtime = value.get("runtime")
    candidate_keys = {
        "indexPath",
        "indexId",
        "indexSha256",
        "osRepository",
        "sourceRevision",
        "agentRepository",
        "agentRevision",
        "releaseTag",
        "applianceManifestSha256",
        "immutableReference",
        "operationsArtifactId",
        "operationsArchiveSha256",
    }
    bundle_keys = {
        "artifactId",
        "archiveSha256",
        "imageReference",
        "manifestSha256",
        "labToolSha256",
        "labToolSize",
        "rootPath",
        "hubLabSha256",
        "hubLabSize",
    }
    expected_volume_count = len(_storage_volume_names(catalog))
    expected_nas_directory_count = len(
        {
            volume["relativePath"]
            for contract in catalog["apps"].values()
            for volume in contract["volumes"]
            if volume["source"] == "nas-data"
        }
    )
    if (
        value.get("planId") != expected_id
        or value.get("confirmation") != f"RUN ECHO HUB LIFECYCLE {expected_id}"
        or value.get("apps") != list(APPS)
        or value.get("phases") != list(PHASES)
        or value.get("retention")
        != {"namedVolumes": True, "nasData": True, "generatedSecrets": True}
        or _origin(str(value.get("baseUrl") or "")) != value.get("baseUrl")
        or not isinstance(candidate, dict)
        or set(candidate) != candidate_keys
        or any(
            SHA256.fullmatch(str(candidate[key])) is None
            for key in (
                "indexId",
                "indexSha256",
                "applianceManifestSha256",
                "operationsArchiveSha256",
            )
        )
        or re.fullmatch(r"[0-9a-f]{40}", str(candidate["sourceRevision"])) is None
        or re.fullmatch(r"[0-9a-f]{40}", str(candidate["agentRevision"])) is None
        or IMMUTABLE_IMAGE_REFERENCE.fullmatch(str(candidate["immutableReference"])) is None
        or not all(
            isinstance(candidate[key], str) and candidate[key]
            for key in (
                "indexPath",
                "osRepository",
                "agentRepository",
                "releaseTag",
                "operationsArtifactId",
            )
        )
        or not Path(candidate["indexPath"]).is_absolute()
        or not isinstance(bundle, dict)
        or set(bundle) != bundle_keys
        or bundle["artifactId"] != candidate["operationsArtifactId"]
        or bundle["archiveSha256"] != candidate["operationsArchiveSha256"]
        or bundle["imageReference"] != candidate["immutableReference"]
        or any(
            SHA256.fullmatch(str(bundle[key])) is None
            for key in ("manifestSha256", "labToolSha256", "hubLabSha256")
        )
        or any(
            not isinstance(bundle[key], int) or isinstance(bundle[key], bool) or bundle[key] <= 0
            for key in ("labToolSize", "hubLabSize")
        )
        or not isinstance(bundle["rootPath"], str)
        or not Path(bundle["rootPath"]).is_absolute()
        or not isinstance(runtime, dict)
        or set(runtime) != {"main", "proxy", "discovery"}
        or any(
            not isinstance(runtime[role], dict)
            or set(runtime[role]) != {"containerId", "image"}
            or SHA256.fullmatch(str(runtime[role]["containerId"])) is None
            or runtime[role]["image"] != candidate["immutableReference"]
            for role in ("main", "proxy", "discovery")
        )
        or value.get("baseline")
        != {
            "namedVolumesAbsent": True,
            "nasDirectoriesAbsent": True,
            "checkedVolumeCount": expected_volume_count,
            "checkedNasDirectoryCount": expected_nas_directory_count,
        }
    ):
        raise HubLifecycleLabError("Hub lifecycle plan identity is invalid")
    return value


def load_plan(path: Path) -> dict[str, Any]:
    if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) != 0o400:
        raise HubLifecycleLabError("Hub lifecycle plan must be mode 0400")
    value = _strict_json(_read_regular(path, "Hub lifecycle plan"), "Hub lifecycle plan")
    return _validate_plan_value(value)


def _inspect_one(name: str, docker: DockerJson) -> Mapping[str, Any]:
    if CONTAINER_NAME.fullmatch(name) is None:
        raise HubLifecycleLabError("Hub lifecycle container name is invalid")
    value = docker(("inspect", name))
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise HubLifecycleLabError(f"Hub lifecycle container {name} is unavailable")
    return value[0]


def _expected_container_name(app_id: str, service_id: str, public: bool) -> str:
    return f"echo-hub-{app_id}" if public else f"echo-hub-{app_id}--{service_id}"


def _volume_fingerprint(
    name: str,
    docker: DockerJson,
    *,
    expected_labels: Mapping[str, str],
) -> dict[str, Any]:
    if CONTAINER_NAME.fullmatch(name) is None:
        raise HubLifecycleLabError("Hub lifecycle volume name is invalid")
    value = docker(("volume", "inspect", name))
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise HubLifecycleLabError(f"Hub lifecycle volume {name} is unavailable")
    volume = value[0]
    labels = volume.get("Labels")
    mountpoint = volume.get("Mountpoint")
    if (
        volume.get("Name") != name
        or not isinstance(labels, dict)
        or any(labels.get(key) != value for key, value in expected_labels.items())
        or not isinstance(mountpoint, str)
    ):
        raise HubLifecycleLabError(f"Hub lifecycle volume {name} is malformed")
    return {
        "name": name,
        "mountpointSha256": _sha256(mountpoint.encode()),
        "labels": labels,
    }


def _nas_source(docker: DockerJson) -> str:
    container = _inspect_one("echo-os", docker)
    config = container.get("Config")
    mounts = container.get("Mounts")
    labels = config.get("Labels") if isinstance(config, dict) else None
    candidates = [
        mount
        for mount in mounts or []
        if isinstance(mount, dict) and mount.get("Destination") == "/data/nas"
    ]
    if (
        not isinstance(labels, dict)
        or labels.get("sh.echo.hub.nas-provider") != "true"
        or labels.get("sh.echo.control-protected") != "true"
        or len(candidates) != 1
        or candidates[0].get("Type") != "bind"
    ):
        raise HubLifecycleLabError("Hub lifecycle NAS provider is not trusted")
    source = str(candidates[0].get("Source") or "")
    if not source.startswith("/") or source == "/" or "\x00" in source:
        raise HubLifecycleLabError("Hub lifecycle NAS provider source is invalid")
    return source.rstrip("/")


def _network_contract(
    *,
    app_id: str,
    service: Mapping[str, Any],
    container: Mapping[str, Any],
    contract: Mapping[str, Any],
    docker: DockerJson,
) -> list[dict[str, Any]]:
    expected_names = service.get("networks")
    definitions = contract.get("networks")
    settings = container.get("NetworkSettings")
    observed = settings.get("Networks") if isinstance(settings, dict) else None
    if not isinstance(expected_names, list) or not isinstance(definitions, list):
        raise HubLifecycleLabError("Hub lifecycle network contract is malformed")
    if contract.get("kind") == "package":
        return []
    if service.get("networkMode") == "host":
        host = container.get("HostConfig")
        if not isinstance(host, dict) or host.get("NetworkMode") != "host":
            raise HubLifecycleLabError("Hub lifecycle host network contract is invalid")
        return []
    if not isinstance(observed, dict) or len(observed) != len(expected_names):
        raise HubLifecycleLabError("Hub lifecycle container network set is invalid")
    by_name = {
        str(item.get("name")): item
        for item in definitions
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    result = []
    for logical_name in expected_names:
        matches = []
        for docker_name in observed:
            value = docker(("network", "inspect", str(docker_name)))
            if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
                raise HubLifecycleLabError("Hub lifecycle network inspection is invalid")
            labels = value[0].get("Labels")
            if (
                isinstance(labels, dict)
                and labels.get("sh.echo.hub.bundle-app-id") == app_id
                and labels.get("sh.echo.hub.bundle-network") == logical_name
            ):
                matches.append(value[0])
        definition = by_name.get(str(logical_name))
        if (
            len(matches) != 1
            or not isinstance(definition, dict)
            or matches[0].get("Internal") is not definition.get("internal")
        ):
            raise HubLifecycleLabError("Hub lifecycle network isolation contract is invalid")
        result.append(
            {
                "name": logical_name,
                "internal": matches[0].get("Internal"),
                "id": str(matches[0].get("Id") or "")[:12],
            }
        )
    return result


def _expected_mounts(
    *,
    app_id: str,
    service: Mapping[str, Any],
    contract: Mapping[str, Any],
    nas_source: str,
) -> set[tuple[str, str, str, bool]]:
    volumes = contract.get("volumes")
    mounts = service.get("mounts")
    if not isinstance(volumes, list) or not isinstance(mounts, list):
        raise HubLifecycleLabError("Hub lifecycle mount contract is malformed")
    definitions = {
        str(item.get("name")): item
        for item in volumes
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    expected: set[tuple[str, str, str, bool]] = set()
    for mount in mounts:
        if not isinstance(mount, dict):
            raise HubLifecycleLabError("Hub lifecycle mount contract is malformed")
        logical_name = str(mount.get("volume") or mount.get("name") or "")
        definition = definitions.get(logical_name)
        if not isinstance(definition, dict):
            raise HubLifecycleLabError("Hub lifecycle mount references an unknown volume")
        source_kind = definition.get("source")
        if source_kind == "app-data":
            mount_type = "volume"
            source = f"echo-hub-{app_id}-{logical_name}"
        elif source_kind in {"nas-data", "nas-root"}:
            mount_type = "bind"
            relative = definition.get("relativePath")
            source = nas_source if relative is None else f"{nas_source}/{relative}"
        else:
            raise HubLifecycleLabError("Hub lifecycle mount source is unsupported")
        expected.add(
            (
                mount_type,
                source,
                str(mount.get("target") or ""),
                not bool(mount.get("readOnly")),
            )
        )
    if service.get("hasSecrets") is True:
        expected.add(
            (
                "volume",
                f"echo-hub-{app_id}-secrets-{service['id']}",
                "/run/secrets",
                False,
            )
        )
    return expected


def _runtime_contract(service: Mapping[str, Any], host: Mapping[str, Any]) -> None:
    if (
        host.get("Privileged") is True
        or host.get("Init") is not True
        or host.get("RestartPolicy") != {"Name": "unless-stopped", "MaximumRetryCount": 0}
        or host.get("CapDrop") != ["ALL"]
        or not any(
            str(item).startswith("no-new-privileges") for item in host.get("SecurityOpt") or []
        )
    ):
        raise HubLifecycleLabError("Hub lifecycle container has an unsafe runtime profile")
    runtime = service.get("runtime")
    if runtime is None:
        if host.get("PidsLimit") != 512 or host.get("CapAdd") not in (None, []):
            raise HubLifecycleLabError("Hub package runtime bounds are invalid")
        return
    if not isinstance(runtime, dict):
        raise HubLifecycleLabError("Hub bundle runtime contract is malformed")
    cap_add = {
        "unprivileged": [],
        "data-root-dropper": ["CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"],
        "web-root-dropper": [
            "CHOWN",
            "DAC_OVERRIDE",
            "FOWNER",
            "NET_BIND_SERVICE",
            "SETGID",
            "SETUID",
        ],
    }.get(runtime.get("profile"))
    if (
        cap_add is None
        or (host.get("CapAdd") or []) != cap_add
        or host.get("PidsLimit") != runtime.get("pids")
        or host.get("Memory") != int(runtime.get("memoryMiB") or 0) * 1024 * 1024
        or host.get("ShmSize") != int(runtime.get("shmSizeMiB") or 0) * 1024 * 1024
        or host.get("ReadonlyRootfs") is not runtime.get("readOnlyRootfs")
    ):
        raise HubLifecycleLabError("Hub bundle runtime bounds are invalid")


def inspect_installation(
    app_id: str,
    contract: Mapping[str, Any],
    docker: DockerJson = _docker_json,
) -> dict[str, Any]:
    if app_id not in APPS or contract.get("kind") not in {"package", "bundle"}:
        raise HubLifecycleLabError("Hub lifecycle installation contract is invalid")
    services = contract.get("services")
    volumes = contract.get("volumes")
    if not isinstance(services, list) or not isinstance(volumes, list):
        raise HubLifecycleLabError("Hub lifecycle installation contract is incomplete")
    nas_root = _nas_source(docker)
    observed_services: dict[str, Any] = {}
    for service in services:
        if not isinstance(service, dict):
            raise HubLifecycleLabError("Hub lifecycle service contract is malformed")
        service_id = str(service.get("id") or "")
        public = service.get("public") is True
        name = _expected_container_name(app_id, service_id, public)
        container = _inspect_one(name, docker)
        config = container.get("Config")
        host = container.get("HostConfig")
        state = container.get("State")
        mounts = container.get("Mounts")
        if not all(isinstance(item, dict) for item in (config, host, state)) or not isinstance(
            mounts, list
        ):
            raise HubLifecycleLabError(f"Hub lifecycle container {name} is malformed")
        labels = config.get("Labels")
        if not isinstance(labels, dict):
            raise HubLifecycleLabError(f"Hub lifecycle container {name} has invalid labels")
        _runtime_contract(service, host)
        expected_mounts = _expected_mounts(
            app_id=app_id,
            service=service,
            contract=contract,
            nas_source=nas_root,
        )
        observed_mounts = {
            (
                str(mount.get("Type") or ""),
                str(mount.get("Source") or ""),
                str(mount.get("Destination") or ""),
                mount.get("RW") is True,
            )
            for mount in mounts
            if isinstance(mount, dict)
        }
        host_network = service.get("networkMode") == "host"
        expected_ports = (
            {}
            if host_network
            else {
                f"{port['container']}/{port['protocol']}": [
                    {"HostIp": "0.0.0.0", "HostPort": str(port["host"])}
                ]
                for port in service.get("ports") or []
                if isinstance(port, dict)
            }
        )
        if contract.get("kind") == "bundle" and (
            labels.get("sh.echo.hub.bundle-app-id") != app_id
            or (
                public
                and labels.get("sh.echo.hub.app-id") != app_id
                or not public
                and ("sh.echo.hub.app-id" in labels or labels.get("sh.echo.hide") != "1")
            )
        ):
            raise HubLifecycleLabError(f"Hub lifecycle container {name} has invalid visibility")
        if (
            config.get("Image") != service.get("image")
            or state.get("Running") is not True
            or not isinstance(labels, dict)
            or labels.get("sh.echo.hub.managed") != "true"
            or (
                labels.get("sh.echo.hub.app-id") != app_id
                and labels.get("sh.echo.hub.bundle-app-id") != app_id
            )
            or observed_mounts != expected_mounts
            or host.get("PortBindings") != expected_ports
            or (host_network and host.get("NetworkMode") != "host")
            or (not host_network and host.get("NetworkMode") == "host")
            or any(
                str(mount.get("Destination")) == "/var/run/docker.sock"
                for mount in mounts
                if isinstance(mount, dict)
            )
        ):
            raise HubLifecycleLabError(
                f"Hub lifecycle container {name} violates its runtime contract"
            )
        if service.get("healthcheck") is True:
            health = state.get("Health")
            if not isinstance(health, dict) or health.get("Status") != "healthy":
                raise HubLifecycleLabError(f"Hub lifecycle container {name} is not healthy")
        observed_services[service_id] = {
            "containerId": str(container.get("Id") or "")[:12],
            "image": config.get("Image"),
            "running": True,
            "healthy": state.get("Health", {}).get("Status") == "healthy"
            if isinstance(state.get("Health"), dict)
            else None,
            "mounts": sorted(
                (
                    {
                        "type": mount_type,
                        "sourceSha256": _sha256(source.encode()),
                        "destination": destination,
                        "rw": rw,
                    }
                    for mount_type, source, destination, rw in observed_mounts
                ),
                key=lambda item: (item["destination"], item["type"]),
            ),
            "ports": expected_ports,
            "networks": _network_contract(
                app_id=app_id,
                service=service,
                container=container,
                contract=contract,
                docker=docker,
            ),
        }
    fingerprints: dict[str, Any] = {}
    for volume in volumes:
        if not isinstance(volume, dict) or not isinstance(volume.get("name"), str):
            raise HubLifecycleLabError("Hub lifecycle volume contract is malformed")
        if volume.get("source") == "app-data":
            name = f"echo-hub-{app_id}-{volume['name']}"
            expected_labels = (
                {
                    "sh.echo.hub.managed": "true",
                    "sh.echo.hub.bundle-app-id": app_id,
                    "sh.echo.hub.bundle-volume": volume["name"],
                    "sh.echo.hub.bundle-volume-role": "data",
                }
                if contract.get("kind") == "bundle"
                else {
                    "sh.echo.hub.managed": "true",
                    "sh.echo.hub.app-id": app_id,
                    "sh.echo.hub.volume-name": volume["name"],
                }
            )
            fingerprints[volume["name"]] = _volume_fingerprint(
                name,
                docker,
                expected_labels=expected_labels,
            )
    if contract.get("kind") == "bundle":
        for service in services:
            if isinstance(service, dict) and service.get("hasSecrets") is True:
                logical_name = f"secrets-{service['id']}"
                fingerprints[logical_name] = _volume_fingerprint(
                    f"echo-hub-{app_id}-{logical_name}",
                    docker,
                    expected_labels={
                        "sh.echo.hub.managed": "true",
                        "sh.echo.hub.bundle-app-id": app_id,
                        "sh.echo.hub.bundle-volume": service["id"],
                        "sh.echo.hub.bundle-volume-role": "secrets",
                    },
                )
    return {"services": observed_services, "volumes": fingerprints}


def _assert_absent(app_id: str, contract: Mapping[str, Any], docker: DockerJson) -> None:
    services = contract.get("services")
    if not isinstance(services, list):
        raise HubLifecycleLabError("Hub lifecycle service contract is incomplete")
    for service in services:
        if not isinstance(service, dict):
            raise HubLifecycleLabError("Hub lifecycle service contract is malformed")
        name = _expected_container_name(
            app_id,
            str(service.get("id") or ""),
            service.get("public") is True,
        )
        value = docker(("inspect-optional", name))
        if isinstance(value, list) and value:
            raise HubLifecycleLabError(f"Hub lifecycle uninstall retained container {name}")
        if value != []:
            raise HubLifecycleLabError("Hub lifecycle optional inspection returned invalid data")


def _valid_progress_counter(value: Any) -> bool:
    return value is None or (
        isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 4096
    )


def _validate_operation_status(
    value: Any,
    *,
    operation: str,
    app_id: str,
    plan_id: str,
    catalog_digest: str,
) -> dict[str, Any]:
    required = {
        "schema",
        "operationId",
        "operation",
        "appId",
        "planId",
        "catalogDigest",
        "status",
        "createdAt",
        "updatedAt",
        "startedAt",
        "finishedAt",
        "error",
        "warning",
        "progress",
        "credentialsAvailable",
        "result",
    }
    progress = value.get("progress") if isinstance(value, dict) else None
    progress_fields = {
        "schema",
        "stage",
        "step",
        "completed",
        "total",
        "unit",
        "item",
        "items",
        "sequence",
    }
    error = value.get("error") if isinstance(value, dict) else None
    warning = value.get("warning") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or set(value) != required
        or value.get("schema") != "echo.hub.operation.v1"
        or re.fullmatch(r"[0-9a-f]{32}", str(value.get("operationId") or "")) is None
        or value.get("operation") != operation
        or value.get("appId") != app_id
        or value.get("planId") != plan_id
        or value.get("catalogDigest") != catalog_digest
        or value.get("status") not in {"queued", "running", "succeeded", "failed", "interrupted"}
        or not all(
            isinstance(value.get(field), str) and value[field]
            for field in ("createdAt", "updatedAt")
        )
        or any(
            value.get(field) is not None and (not isinstance(value[field], str) or not value[field])
            for field in ("startedAt", "finishedAt")
        )
        or error is not None
        and (
            not isinstance(error, dict)
            or set(error) != {"code", "message", "recoveryAction"}
            or not all(isinstance(error.get(field), str) and error[field] for field in error)
        )
        or warning is not None
        and (
            not isinstance(warning, dict)
            or set(warning) != {"code", "message"}
            or not all(isinstance(warning.get(field), str) and warning[field] for field in warning)
        )
        or not isinstance(progress, dict)
        or set(progress) != progress_fields
        or progress.get("schema") != "echo.hub.progress.v1"
        or progress.get("stage")
        not in {
            "queued",
            "validating",
            "pulling",
            "preparing",
            "snapshotting",
            "stopping",
            "starting",
            "verifying",
            "switching",
            "removing",
            "rolling-back",
            "completed",
            "failed",
            "interrupted",
        }
        or progress.get("step")
        not in {
            "waiting",
            "checking-plan",
            "pulling-image",
            "creating-resources",
            "snapshotting-data",
            "stopping-services",
            "starting-services",
            "checking-health",
            "switching-services",
            "removing-services",
            "restoring-state",
            "finished",
            "operation-failed",
            "runtime-restarted",
        }
        or any(
            not _valid_progress_counter(progress.get(field))
            for field in ("completed", "total", "item", "items")
        )
        or progress.get("unit") not in {None, "layers", "images", "services", "volumes"}
        or not isinstance(progress.get("sequence"), int)
        or isinstance(progress.get("sequence"), bool)
        or progress["sequence"] < 0
        or value.get("credentialsAvailable") is not False
        or value.get("result") is not None
        and not isinstance(value["result"], dict)
    ):
        raise HubLifecycleLabError("Hub lifecycle background operation contract is invalid")
    completed = progress["completed"]
    total = progress["total"]
    item = progress["item"]
    items = progress["items"]
    if (
        (completed is None) is not (total is None)
        or completed is not None
        and (total <= 0 or completed > total)
        or (item is None) is not (items is None)
        or item is not None
        and (item <= 0 or items <= 0 or item > items)
    ):
        raise HubLifecycleLabError("Hub lifecycle background operation progress is invalid")
    return value


def _validate_control_result(
    value: Any,
    *,
    operation: str,
    app_id: str,
    plan_id: str,
    catalog_digest: str,
    service_count: int,
) -> dict[str, Any]:
    required = {
        "schema",
        "appId",
        "planId",
        "catalogDigest",
        "containerId",
        "state",
        "serviceCount",
        "dataVolumesRetained",
        "nasDataRetained",
        "rollback",
    }
    rollback = value.get("rollback") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or set(value) != required
        or value.get("schema") != f"echo.hub.{operation}-result.v1"
        or value.get("appId") != app_id
        or value.get("planId") != plan_id
        or value.get("catalogDigest") != catalog_digest
        or re.fullmatch(r"[0-9a-f]{12}", str(value.get("containerId") or "")) is None
        or value.get("state") != ("stopped" if operation == "stop" else "running")
        or value.get("serviceCount") != service_count
        or value.get("dataVolumesRetained") is not True
        or value.get("nasDataRetained") is not True
        or rollback != {"previousRunningStateRestoredOnFailure": True}
    ):
        raise HubLifecycleLabError(f"Hub lifecycle {operation} result for {app_id} is invalid")
    return value


def _await_control_operation(
    *,
    base_url: str,
    app_id: str,
    operation: str,
    plan_id: str,
    catalog_digest: str,
    service_order: Sequence[str],
    token: str,
    approval_token: str,
    intent_id: str,
    request: HttpRequest,
) -> dict[str, Any]:
    queued = _request(
        request,
        "POST",
        f"{base_url}/api/appliance/hub/plans/{operation}/queue",
        payload={"appId": app_id, "planId": plan_id},
        token=token,
        headers={APPROVAL_HEADER: approval_token, INTENT_HEADER: intent_id},
        timeout=CONTROL_OPERATION_TIMEOUT_SECONDS,
        expected=202,
    )
    current = _validate_operation_status(
        queued,
        operation=operation,
        app_id=app_id,
        plan_id=plan_id,
        catalog_digest=catalog_digest,
    )
    operation_id = current["operationId"]
    deadline = time.monotonic() + CONTROL_OPERATION_TIMEOUT_SECONDS
    while current["status"] in {"queued", "running"}:
        if time.monotonic() >= deadline:
            raise HubLifecycleLabError(
                f"Hub lifecycle {operation} operation for {app_id} timed out"
            )
        current = _validate_operation_status(
            _request(
                request,
                "GET",
                f"{base_url}/api/appliance/hub/operations/{operation_id}",
                token=token,
            ),
            operation=operation,
            app_id=app_id,
            plan_id=plan_id,
            catalog_digest=catalog_digest,
        )
        if current["operationId"] != operation_id:
            raise HubLifecycleLabError("Hub lifecycle background operation identity changed")
        if current["status"] in {"queued", "running"}:
            time.sleep(CONTROL_OPERATION_POLL_SECONDS)
    if current["status"] != "succeeded":
        error = current.get("error")
        code = error.get("code") if isinstance(error, dict) else current["status"].upper()
        raise HubLifecycleLabError(
            f"Hub lifecycle {operation} operation for {app_id} failed ({code})"
        )
    result = _validate_control_result(
        current.get("result"),
        operation=operation,
        app_id=app_id,
        plan_id=plan_id,
        catalog_digest=catalog_digest,
        service_count=len(service_order),
    )
    return {
        "operation": operation,
        "operationId": operation_id,
        "planId": plan_id,
        "serviceOrder": list(service_order),
        "result": result,
    }


def _operation(
    *,
    base_url: str,
    app_id: str,
    operation: str,
    password: str,
    token: str,
    request: HttpRequest,
    expected_service_order: Sequence[str] | None = None,
) -> Mapping[str, Any]:
    if operation not in {"install", "uninstall", *CONTROL_OPERATIONS} or app_id not in APPS:
        raise HubLifecycleLabError("Hub lifecycle operation is invalid")
    plan = _request(
        request,
        "POST",
        f"{base_url}/api/appliance/hub/plans/{operation}",
        payload={"appId": app_id},
        token=token,
    )
    plan_id = plan.get("planId")
    if (
        plan.get("ready") is not True
        or not isinstance(plan_id, str)
        or SHA256.fullmatch(plan_id) is None
    ):
        raise HubLifecycleLabError(f"Hub lifecycle {operation} plan for {app_id} is blocked")
    if operation in CONTROL_OPERATIONS:
        service_order = list(expected_service_order or ())
        desired = plan.get("desired")
        expected_state = "stopped" if operation == "stop" else "running"
        if (
            not service_order
            or len(service_order) != len(set(service_order))
            or any(APP_ID.fullmatch(str(service_id)) is None for service_id in service_order)
            or plan.get("schema") != f"echo.hub.{operation}-plan.v1"
            or plan.get("operation") != operation
            or plan.get("approvalAction") != f"hub.app.{operation}"
            or plan.get("approvalTarget") != plan_id
            or not isinstance(desired, dict)
            or set(desired)
            != {
                "appId",
                "catalogDigest",
                "state",
                "serviceOrder",
                "dataVolumesRetained",
                "nasDataRetained",
            }
            or desired.get("appId") != app_id
            or SHA256.fullmatch(str(desired.get("catalogDigest") or "")) is None
            or desired.get("state") != expected_state
            or desired.get("serviceOrder") != service_order
            or desired.get("dataVolumesRetained") is not True
            or desired.get("nasDataRetained") is not True
        ):
            raise HubLifecycleLabError(
                f"Hub lifecycle {operation} plan for {app_id} has an invalid contract"
            )
    action = f"hub.app.{operation}"
    intent_id = f"physical.hub.{operation}.{app_id}.{plan_id[:12]}"
    approval = _request(
        request,
        "POST",
        f"{base_url}/api/appliance/approvals",
        payload={
            "action": action,
            "target": plan_id,
            "intentId": intent_id,
            "password": password,
        },
        token=token,
    )
    approval_token = approval.get("approvalToken")
    if not isinstance(approval_token, str) or not approval_token:
        raise HubLifecycleLabError("Hub lifecycle approval returned no token")
    if operation in CONTROL_OPERATIONS:
        return _await_control_operation(
            base_url=base_url,
            app_id=app_id,
            operation=operation,
            plan_id=plan_id,
            catalog_digest=plan["desired"]["catalogDigest"],
            service_order=list(expected_service_order or ()),
            token=token,
            approval_token=approval_token,
            intent_id=intent_id,
            request=request,
        )
    result = _request(
        request,
        "POST",
        f"{base_url}/api/appliance/hub/plans/{operation}/apply",
        payload={"appId": app_id, "planId": plan_id},
        token=token,
        headers={APPROVAL_HEADER: approval_token, INTENT_HEADER: intent_id},
        timeout=3600,
    )
    expected_schema = f"echo.hub.{operation}-result.v1"
    expected_state = "running" if operation == "install" else "not-installed"
    if (
        result.get("schema") != expected_schema
        or result.get("appId") != app_id
        or result.get("state") != expected_state
    ):
        raise HubLifecycleLabError(f"Hub lifecycle {operation} result for {app_id} is invalid")
    return result


def _validate_public_endpoint_evidence(
    endpoint: Mapping[str, Any],
    observed: Any,
) -> dict[str, Any]:
    expected_keys = {
        "scheme",
        "host",
        "port",
        "path",
        "status",
        "mediaType",
        "sampleBytes",
        "sampleSha256",
        "sampleTruncated",
        "attempts",
    }
    if (
        not isinstance(observed, dict)
        or set(observed) != expected_keys
        or any(observed.get(key) != endpoint.get(key) for key in ("scheme", "host", "port", "path"))
        or not isinstance(observed.get("status"), int)
        or isinstance(observed.get("status"), bool)
        or not 200 <= observed["status"] < 500
        or not isinstance(observed.get("mediaType"), str)
        or (
            observed["mediaType"] != "unknown"
            and re.fullmatch(
                r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+",
                observed["mediaType"],
            )
            is None
        )
        or not isinstance(observed.get("sampleBytes"), int)
        or isinstance(observed.get("sampleBytes"), bool)
        or not 0 <= observed["sampleBytes"] <= PUBLIC_ENDPOINT_SAMPLE_BYTES
        or SHA256.fullmatch(str(observed.get("sampleSha256") or "")) is None
        or not isinstance(observed.get("sampleTruncated"), bool)
        or observed["sampleTruncated"] is True
        and observed["sampleBytes"] != PUBLIC_ENDPOINT_SAMPLE_BYTES
        or not isinstance(observed.get("attempts"), int)
        or isinstance(observed.get("attempts"), bool)
        or not 1 <= observed["attempts"] <= PUBLIC_ENDPOINT_MAX_ATTEMPTS
    ):
        raise HubLifecycleLabError("Hub lifecycle public endpoint evidence is invalid")
    return observed


def _validate_runtime_health(
    app_id: str,
    contract: Mapping[str, Any],
    detail: Any,
) -> dict[str, Any]:
    """Require the public Hub API to project real, bounded post-install health."""

    if not isinstance(detail, dict):
        raise HubLifecycleLabError("Hub lifecycle runtime detail is invalid")
    app = detail.get("app")
    runtime = detail.get("appRuntime")
    if (
        not isinstance(app, dict)
        or app.get("id") != app_id
        or not isinstance(app.get("installation"), dict)
        or app["installation"].get("installed") is not True
        or not isinstance(runtime, dict)
        or set(runtime) != {"schema", "status", "summary", "services"}
        or runtime.get("schema") != "echo.hub.runtime.v1"
        or runtime.get("status") != "healthy"
    ):
        raise HubLifecycleLabError(f"Hub lifecycle runtime health for {app_id} is invalid")
    summary = runtime.get("summary")
    services = runtime.get("services")
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
    expected = {service["id"]: service for service in contract["services"]}

    def bounded_number(value: Any, maximum: float) -> bool:
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and 0 <= value <= maximum
        )

    if (
        not isinstance(summary, dict)
        or set(summary) != summary_fields
        or not isinstance(services, list)
        or len(services) != len(expected)
        or summary.get("serviceCount") != len(expected)
        or summary.get("runningServices") != len(expected)
        or summary.get("healthyServices") != len(expected)
        or not isinstance(summary.get("restartCount"), int)
        or isinstance(summary.get("restartCount"), bool)
        or not 0 <= summary["restartCount"] <= 64_000_000
        or not bounded_number(summary.get("cpuPercent"), 409_600)
        or not isinstance(summary.get("memoryUsageBytes"), int)
        or not 0 <= summary["memoryUsageBytes"] <= 2**53 - 1
        or not isinstance(summary.get("memoryLimitBytes"), int)
        or not 1 <= summary["memoryLimitBytes"] <= 2**53 - 1
        or summary["memoryUsageBytes"] > summary["memoryLimitBytes"]
        or not isinstance(summary.get("pids"), int)
        or not 0 <= summary["pids"] <= 67_108_864
    ):
        raise HubLifecycleLabError(f"Hub lifecycle runtime summary for {app_id} is invalid")
    observed_ids: set[str] = set()
    for service in services:
        if not isinstance(service, dict) or set(service) != service_fields:
            raise HubLifecycleLabError(f"Hub lifecycle runtime service for {app_id} is invalid")
        service_id = str(service.get("id") or "")
        definition = expected.get(service_id)
        if (
            definition is None
            or service_id in observed_ids
            or service.get("public") is not definition["public"]
            or service.get("role") not in {"app", "database", "cache", "worker"}
            or service.get("state") != "running"
            or service.get("health") not in {"healthy", "not-configured"}
            or not isinstance(service.get("restartCount"), int)
            or isinstance(service.get("restartCount"), bool)
            or not 0 <= service["restartCount"] <= 1_000_000
            or service.get("oomKilled") is not False
            or service.get("exitCode") not in {None, 0}
            or not bounded_number(service.get("cpuPercent"), 409_600)
            or not isinstance(service.get("memoryUsageBytes"), int)
            or not 0 <= service["memoryUsageBytes"] <= 2**53 - 1
            or not isinstance(service.get("memoryLimitBytes"), int)
            or not 1 <= service["memoryLimitBytes"] <= 2**53 - 1
            or service["memoryUsageBytes"] > service["memoryLimitBytes"]
            or not isinstance(service.get("pids"), int)
            or not 0 <= service["pids"] <= 1_048_576
        ):
            raise HubLifecycleLabError(f"Hub lifecycle runtime service for {app_id} is invalid")
        observed_ids.add(service_id)
    if observed_ids != set(expected):
        raise HubLifecycleLabError(f"Hub lifecycle runtime service set for {app_id} is invalid")
    return _validate_runtime_health_evidence(contract, {"status": "healthy", **summary})


def _validate_runtime_health_evidence(
    contract: Mapping[str, Any],
    observed: Any,
) -> dict[str, Any]:
    fields = {
        "status",
        "serviceCount",
        "runningServices",
        "healthyServices",
        "restartCount",
        "cpuPercent",
        "memoryUsageBytes",
        "memoryLimitBytes",
        "pids",
    }
    service_count = len(contract["services"])
    if (
        not isinstance(observed, dict)
        or set(observed) != fields
        or observed.get("status") != "healthy"
        or observed.get("serviceCount") != service_count
        or observed.get("runningServices") != service_count
        or observed.get("healthyServices") != service_count
        or not isinstance(observed.get("restartCount"), int)
        or isinstance(observed.get("restartCount"), bool)
        or not 0 <= observed["restartCount"] <= 64_000_000
        or not isinstance(observed.get("cpuPercent"), (int, float))
        or isinstance(observed.get("cpuPercent"), bool)
        or not 0 <= observed["cpuPercent"] <= 409_600
        or not isinstance(observed.get("memoryUsageBytes"), int)
        or not 0 <= observed["memoryUsageBytes"] <= 2**53 - 1
        or not isinstance(observed.get("memoryLimitBytes"), int)
        or not 1 <= observed["memoryLimitBytes"] <= 2**53 - 1
        or observed["memoryUsageBytes"] > observed["memoryLimitBytes"]
        or not isinstance(observed.get("pids"), int)
        or not 0 <= observed["pids"] <= 67_108_864
    ):
        raise HubLifecycleLabError("Hub lifecycle runtime health evidence is invalid")
    return observed


def _runtime_health_evidence(
    *,
    base_url: str,
    app_id: str,
    contract: Mapping[str, Any],
    token: str,
    request: HttpRequest,
) -> dict[str, Any]:
    detail = _request(
        request,
        "GET",
        f"{base_url}/api/appliance/hub/apps/{app_id}",
        token=token,
    )
    return _validate_runtime_health(app_id, contract, detail)


def _stopped_container_evidence(
    app_id: str,
    contract: Mapping[str, Any],
    docker: DockerJson,
) -> dict[str, Any]:
    services = contract.get("services")
    if not isinstance(services, list) or not services:
        raise HubLifecycleLabError("Hub lifecycle stopped service contract is invalid")
    observed: dict[str, Any] = {}
    for service in services:
        if not isinstance(service, dict):
            raise HubLifecycleLabError("Hub lifecycle stopped service contract is malformed")
        service_id = str(service.get("id") or "")
        name = _expected_container_name(app_id, service_id, service.get("public") is True)
        container = _inspect_one(name, docker)
        config = container.get("Config")
        host = container.get("HostConfig")
        state = container.get("State")
        labels = config.get("Labels") if isinstance(config, dict) else None
        container_id = str(container.get("Id") or "")[:12]
        if (
            not isinstance(config, dict)
            or not isinstance(host, dict)
            or not isinstance(state, dict)
            or not isinstance(labels, dict)
            or re.fullmatch(r"[0-9a-f]{12}", container_id) is None
            or config.get("Image") != service.get("image")
            or labels.get("sh.echo.hub.managed") != "true"
            or state.get("Running") is not False
            or state.get("Status") != "exited"
            or state.get("OOMKilled") is not False
            or state.get("ExitCode") != 0
        ):
            raise HubLifecycleLabError(
                f"Hub lifecycle stopped container {name} has an invalid state"
            )
        if contract.get("kind") == "bundle":
            if (
                labels.get("sh.echo.hub.bundle-app-id") != app_id
                or labels.get("sh.echo.hub.bundle-service") != service_id
                or (service.get("public") is True and labels.get("sh.echo.hub.app-id") != app_id)
            ):
                raise HubLifecycleLabError(
                    f"Hub lifecycle stopped container {name} has invalid identity"
                )
        elif labels.get("sh.echo.hub.app-id") != app_id:
            raise HubLifecycleLabError(
                f"Hub lifecycle stopped container {name} has invalid identity"
            )
        _runtime_contract(service, host)
        observed[service_id] = {
            "containerId": container_id,
            "running": False,
            "state": "exited",
            "exitCode": 0,
        }
    return observed


def _stopped_runtime_evidence(
    *,
    base_url: str,
    app_id: str,
    contract: Mapping[str, Any],
    token: str,
    request: HttpRequest,
) -> dict[str, Any]:
    detail = _request(
        request,
        "GET",
        f"{base_url}/api/appliance/hub/apps/{app_id}",
        token=token,
    )
    app = detail.get("app") if isinstance(detail, dict) else None
    runtime = detail.get("appRuntime") if isinstance(detail, dict) else None
    diagnostics = detail.get("diagnostics") if isinstance(detail, dict) else None
    summary = runtime.get("summary") if isinstance(runtime, dict) else None
    services = runtime.get("services") if isinstance(runtime, dict) else None
    expected = {service["id"]: service for service in contract["services"]}
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
        not isinstance(app, dict)
        or app.get("id") != app_id
        or not isinstance(app.get("installation"), dict)
        or app["installation"].get("installed") is not True
        or not isinstance(runtime, dict)
        or set(runtime) != {"schema", "status", "summary", "services"}
        or runtime.get("schema") != "echo.hub.runtime.v1"
        or runtime.get("status") != "stopped"
        or not isinstance(summary, dict)
        or set(summary) != summary_fields
        or summary.get("serviceCount") != len(expected)
        or summary.get("runningServices") != 0
        or summary.get("healthyServices") != 0
        or not isinstance(summary.get("restartCount"), int)
        or isinstance(summary.get("restartCount"), bool)
        or not 0 <= summary["restartCount"] <= 64_000_000
        or any(
            summary.get(field) is not None
            for field in ("cpuPercent", "memoryUsageBytes", "memoryLimitBytes", "pids")
        )
        or not isinstance(services, list)
        or len(services) != len(expected)
        or diagnostics
        != {"schema": "echo.hub.diagnostics.v1", "status": "stopped", "incidents": []}
    ):
        raise HubLifecycleLabError(f"Hub lifecycle stopped runtime for {app_id} is invalid")
    bounded_services: dict[str, Any] = {}
    for service in services:
        definition = (
            expected.get(str(service.get("id") or "")) if isinstance(service, dict) else None
        )
        if (
            not isinstance(service, dict)
            or set(service) != service_fields
            or definition is None
            or service["id"] in bounded_services
            or service.get("role") not in {"app", "database", "cache", "worker"}
            or service.get("public") is not definition["public"]
            or service.get("state") != "exited"
            or service.get("health")
            not in {"healthy", "unhealthy", "starting", "not-configured", "unknown"}
            or not isinstance(service.get("restartCount"), int)
            or isinstance(service.get("restartCount"), bool)
            or not 0 <= service["restartCount"] <= 1_000_000
            or service.get("oomKilled") is not False
            or service.get("exitCode") not in {None, 0}
            or any(
                service.get(field) is not None
                for field in ("cpuPercent", "memoryUsageBytes", "memoryLimitBytes", "pids")
            )
        ):
            raise HubLifecycleLabError(
                f"Hub lifecycle stopped runtime service for {app_id} is invalid"
            )
        bounded_services[service["id"]] = {
            "state": service["state"],
            "health": service["health"],
            "restartCount": service["restartCount"],
            "oomKilled": service["oomKilled"],
            "exitCode": service["exitCode"],
        }
    if set(bounded_services) != set(expected) or summary["restartCount"] != sum(
        service["restartCount"] for service in bounded_services.values()
    ):
        raise HubLifecycleLabError(f"Hub lifecycle stopped service set for {app_id} is invalid")
    return {
        "status": "stopped",
        "serviceCount": len(expected),
        "runningServices": 0,
        "healthyServices": 0,
        "restartCount": summary["restartCount"],
        "services": bounded_services,
        "diagnosticsStatus": "stopped",
    }


def _exercise_lifecycle_control(
    *,
    base_url: str,
    app_id: str,
    contract: Mapping[str, Any],
    initial_installation: Mapping[str, Any],
    password: str,
    token: str,
    request: HttpRequest,
    docker: DockerJson,
    endpoint_probe: EndpointProbe,
) -> dict[str, Any]:
    service_order = [str(service["id"]) for service in contract["services"]]
    public_service = next(service for service in contract["services"] if service["public"])
    public_container_id = initial_installation["services"][public_service["id"]]["containerId"]

    stopped = dict(
        _operation(
            base_url=base_url,
            app_id=app_id,
            operation="stop",
            password=password,
            token=token,
            request=request,
            expected_service_order=service_order,
        )
    )
    stopped_containers = _stopped_container_evidence(app_id, contract, docker)
    if any(
        stopped_containers[service_id]["containerId"]
        != initial_installation["services"][service_id]["containerId"]
        for service_id in service_order
    ):
        raise HubLifecycleLabError(
            f"Hub lifecycle stop replaced a catalog-owned container for {app_id}"
        )
    stopped_runtime = _stopped_runtime_evidence(
        base_url=base_url,
        app_id=app_id,
        contract=contract,
        token=token,
        request=request,
    )

    started = dict(
        _operation(
            base_url=base_url,
            app_id=app_id,
            operation="start",
            password=password,
            token=token,
            request=request,
            expected_service_order=service_order,
        )
    )
    started_installation = inspect_installation(app_id, contract, docker)
    if started_installation != initial_installation:
        raise HubLifecycleLabError(
            f"Hub lifecycle start changed retained installation state for {app_id}"
        )
    started_endpoint = _validate_public_endpoint_evidence(
        contract["endpoint"],
        dict(endpoint_probe(contract["endpoint"])),
    )
    started_runtime = _runtime_health_evidence(
        base_url=base_url,
        app_id=app_id,
        contract=contract,
        token=token,
        request=request,
    )

    restarted = dict(
        _operation(
            base_url=base_url,
            app_id=app_id,
            operation="restart",
            password=password,
            token=token,
            request=request,
            expected_service_order=service_order,
        )
    )
    restarted_installation = inspect_installation(app_id, contract, docker)
    if restarted_installation != initial_installation:
        raise HubLifecycleLabError(
            f"Hub lifecycle restart changed retained installation state for {app_id}"
        )
    restarted_endpoint = _validate_public_endpoint_evidence(
        contract["endpoint"],
        dict(endpoint_probe(contract["endpoint"])),
    )
    restarted_runtime = _runtime_health_evidence(
        base_url=base_url,
        app_id=app_id,
        contract=contract,
        token=token,
        request=request,
    )
    for operation in (stopped, started, restarted):
        if operation["result"]["containerId"] != public_container_id:
            raise HubLifecycleLabError(
                f"Hub lifecycle control changed the public container identity for {app_id}"
            )
    return {
        "serviceOrder": service_order,
        "stop": {
            "operation": stopped,
            "containers": stopped_containers,
            "runtime": stopped_runtime,
        },
        "start": {
            "operation": started,
            "installation": started_installation,
            "publicEndpoint": started_endpoint,
            "runtimeHealth": started_runtime,
        },
        "restart": {
            "operation": restarted,
            "installation": restarted_installation,
            "publicEndpoint": restarted_endpoint,
            "runtimeHealth": restarted_runtime,
        },
    }


def run_plan(
    *,
    plan_path: Path,
    confirmation: str,
    password: str,
    output: Path,
    request: HttpRequest = _http_request,
    docker: DockerJson = _docker_json,
    endpoint_probe: EndpointProbe = _public_endpoint_probe,
    private_paperless_secret_output: Path | None = None,
) -> dict[str, Any]:
    plan = load_plan(plan_path)
    if confirmation != plan["confirmation"]:
        raise HubLifecycleLabError("Hub lifecycle confirmation does not match the reviewed plan")
    if not password:
        raise HubLifecycleLabError("Hub lifecycle administrator password is unavailable")
    base_url = str(plan["baseUrl"])
    candidate, bundle = _candidate_bundle_identity(
        candidate_index=Path(str(plan["releaseCandidate"].get("indexPath") or "")),
        bundle_root=Path(str(plan["operationsBundle"].get("rootPath") or "")),
        trusted_uid=os.getuid(),
    )
    if candidate != plan["releaseCandidate"] or bundle != plan["operationsBundle"]:
        raise HubLifecycleLabError("Hub lifecycle candidate or operations bundle changed")
    if _running_candidate(candidate["immutableReference"], docker) != plan["runtime"]:
        raise HubLifecycleLabError("Hub lifecycle running candidate changed")
    if _fresh_storage(plan["catalog"], docker) != plan["baseline"]:
        raise HubLifecycleLabError("Hub lifecycle fresh-storage baseline changed")
    token = _login(base_url, password, request)
    live_catalog = _request(
        request,
        "GET",
        f"{base_url}/api/appliance/hub/catalog",
        token=token,
    )
    if _catalog_snapshot(live_catalog) != plan["catalog"]:
        raise HubLifecycleLabError("Hub catalog changed after the lifecycle plan was reviewed")
    apps = plan["catalog"]["apps"]
    first: dict[str, Any] = {}
    second: dict[str, Any] = {}
    installed: list[str] = []
    private_secret_written = False
    if private_paperless_secret_output is not None:
        _private_paperless_secret_parent(
            private_paperless_secret_output,
            public_directories=(plan_path.parent, output.parent),
        )
    try:
        for app_id in APPS:
            result = _operation(
                base_url=base_url,
                app_id=app_id,
                operation="install",
                password=password,
                token=token,
                request=request,
            )
            installed.append(app_id)
            inspected = inspect_installation(app_id, apps[app_id], docker)
            endpoint = _validate_public_endpoint_evidence(
                apps[app_id]["endpoint"],
                dict(endpoint_probe(apps[app_id]["endpoint"])),
            )
            first[app_id] = {
                "installation": inspected,
                "revealedSecretNames": sorted((result.get("revealedSecrets") or {}).keys()),
                "publicEndpoint": endpoint,
                "runtimeHealth": _runtime_health_evidence(
                    base_url=base_url,
                    app_id=app_id,
                    contract=apps[app_id],
                    token=token,
                    request=request,
                ),
            }
            if app_id == "paperless-ngx" and private_paperless_secret_output is not None:
                revealed = result.get("revealedSecrets")
                paperless_password = (
                    revealed.get("admin-password") if isinstance(revealed, dict) else None
                )
                if not isinstance(paperless_password, str):
                    raise HubLifecycleLabError(
                        "Paperless first install did not reveal its private administrator password"
                    )
                _write_private_paperless_secret(
                    private_paperless_secret_output,
                    plan=plan,
                    password=paperless_password,
                    public_directories=(plan_path.parent, output.parent),
                )
                private_secret_written = True
            first[app_id]["lifecycleControl"] = _exercise_lifecycle_control(
                base_url=base_url,
                app_id=app_id,
                contract=apps[app_id],
                initial_installation=inspected,
                password=password,
                token=token,
                request=request,
                docker=docker,
                endpoint_probe=endpoint_probe,
            )
        for app_id in reversed(APPS):
            result = _operation(
                base_url=base_url,
                app_id=app_id,
                operation="uninstall",
                password=password,
                token=token,
                request=request,
            )
            installed.remove(app_id)
            if (
                result.get("dataVolumesRetained") is not True
                or result.get("nasDataRetained") is not True
            ):
                raise HubLifecycleLabError(
                    f"Hub lifecycle uninstall for {app_id} did not retain data"
                )
            _assert_absent(app_id, apps[app_id], docker)
        for app_id in APPS:
            result = _operation(
                base_url=base_url,
                app_id=app_id,
                operation="install",
                password=password,
                token=token,
                request=request,
            )
            installed.append(app_id)
            inspected = inspect_installation(app_id, apps[app_id], docker)
            if inspected["volumes"] != first[app_id]["installation"]["volumes"]:
                raise HubLifecycleLabError(
                    f"Hub lifecycle reinstall changed retained volumes for {app_id}"
                )
            secret_names = sorted((result.get("revealedSecrets") or {}).keys())
            if secret_names:
                raise HubLifecycleLabError(
                    f"Hub lifecycle reinstall re-revealed secrets for {app_id}"
                )
            endpoint = _validate_public_endpoint_evidence(
                apps[app_id]["endpoint"],
                dict(endpoint_probe(apps[app_id]["endpoint"])),
            )
            second[app_id] = {
                "installation": inspected,
                "revealedSecretNames": secret_names,
                "publicEndpoint": endpoint,
                "runtimeHealth": _runtime_health_evidence(
                    base_url=base_url,
                    app_id=app_id,
                    contract=apps[app_id],
                    token=token,
                    request=request,
                ),
            }
        for app_id in reversed(APPS):
            result = _operation(
                base_url=base_url,
                app_id=app_id,
                operation="uninstall",
                password=password,
                token=token,
                request=request,
            )
            installed.remove(app_id)
            if (
                result.get("dataVolumesRetained") is not True
                or result.get("nasDataRetained") is not True
            ):
                raise HubLifecycleLabError(f"Hub lifecycle final uninstall for {app_id} lost data")
            _assert_absent(app_id, apps[app_id], docker)
    except Exception:
        for app_id in reversed(installed):
            with suppress(Exception):
                _operation(
                    base_url=base_url,
                    app_id=app_id,
                    operation="uninstall",
                    password=password,
                    token=token,
                    request=request,
                )
        if private_secret_written and private_paperless_secret_output is not None:
            with suppress(OSError):
                _remove_private_paperless_secret(private_paperless_secret_output)
        raise
    value: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": RESULT_KIND,
        "planId": plan["planId"],
        "releaseCandidate": plan["releaseCandidate"],
        "operationsBundle": plan["operationsBundle"],
        "runtime": plan["runtime"],
        "catalogDigest": plan["catalog"]["digest"],
        "architecture": plan["catalog"]["architecture"],
        "apps": list(APPS),
        "firstInstall": first,
        "reinstall": second,
        "finalState": "not-installed-data-retained",
        "allPassed": True,
        "completedAtUnix": int(time.time()),
    }
    value["resultId"] = _sha256(_canonical(value))
    try:
        _write_new(output, value, mode=0o444)
    except Exception:
        if private_secret_written and private_paperless_secret_output is not None:
            with suppress(OSError):
                _remove_private_paperless_secret(private_paperless_secret_output)
        raise
    return value


def _expected_volume_labels(
    app_id: str,
    contract: Mapping[str, Any],
    logical_name: str,
) -> dict[str, str]:
    if logical_name.startswith("secrets-"):
        return {
            "sh.echo.hub.managed": "true",
            "sh.echo.hub.bundle-app-id": app_id,
            "sh.echo.hub.bundle-volume": logical_name.removeprefix("secrets-"),
            "sh.echo.hub.bundle-volume-role": "secrets",
        }
    if contract["kind"] == "bundle":
        return {
            "sh.echo.hub.managed": "true",
            "sh.echo.hub.bundle-app-id": app_id,
            "sh.echo.hub.bundle-volume": logical_name,
            "sh.echo.hub.bundle-volume-role": "data",
        }
    return {
        "sh.echo.hub.managed": "true",
        "sh.echo.hub.app-id": app_id,
        "sh.echo.hub.volume-name": logical_name,
    }


def _validate_service_evidence(
    *,
    app_id: str,
    definition: Mapping[str, Any],
    contract: Mapping[str, Any],
    observed: Any,
) -> None:
    expected_ports = (
        {}
        if definition.get("networkMode") == "host"
        else {
            f"{port['container']}/{port['protocol']}": [
                {"HostIp": "0.0.0.0", "HostPort": str(port["host"])}
            ]
            for port in definition["ports"]
        }
    )
    network_definitions = {network["name"]: network["internal"] for network in contract["networks"]}
    expected_networks = definition["networks"]
    if (
        not isinstance(observed, dict)
        or set(observed)
        != {"containerId", "image", "running", "healthy", "mounts", "ports", "networks"}
        or re.fullmatch(r"[0-9a-f]{12}", str(observed["containerId"])) is None
        or observed["image"] != definition["image"]
        or observed["running"] is not True
        or observed["healthy"] is not (True if definition["healthcheck"] else None)
        or observed["ports"] != expected_ports
        or not isinstance(observed["mounts"], list)
        or not isinstance(observed["networks"], list)
        or len(observed["networks"]) != len(expected_networks)
    ):
        raise HubLifecycleLabError("Hub lifecycle service evidence is invalid")
    networks: dict[str, Any] = {}
    for network in observed["networks"]:
        if (
            not isinstance(network, dict)
            or set(network) != {"name", "internal", "id"}
            or not isinstance(network["name"], str)
            or network["name"] in networks
            or network["name"] not in network_definitions
            or network["internal"] is not network_definitions[network["name"]]
            or re.fullmatch(r"[0-9a-f]{12}", str(network["id"])) is None
        ):
            raise HubLifecycleLabError("Hub lifecycle network evidence is invalid")
        networks[network["name"]] = network
    if set(networks) != set(expected_networks):
        raise HubLifecycleLabError("Hub lifecycle network evidence is incomplete")

    volume_definitions = {volume["name"]: volume for volume in contract["volumes"]}
    expected_mounts: dict[str, tuple[str, bool, str | None]] = {}
    for mount in definition["mounts"]:
        logical_name = mount.get("name") or mount.get("volume")
        volume = volume_definitions[logical_name]
        source_hash = None
        mount_type = "bind"
        if volume["source"] == "app-data":
            mount_type = "volume"
            source_hash = _sha256(f"echo-hub-{app_id}-{logical_name}".encode())
        expected_mounts[mount["target"]] = (
            mount_type,
            not mount["readOnly"],
            source_hash,
        )
    if definition["hasSecrets"]:
        expected_mounts["/run/secrets"] = (
            "volume",
            False,
            _sha256(f"echo-hub-{app_id}-secrets-{definition['id']}".encode()),
        )
    if len(observed["mounts"]) != len(expected_mounts):
        raise HubLifecycleLabError("Hub lifecycle mount evidence is incomplete")
    destinations: set[str] = set()
    for mount in observed["mounts"]:
        if (
            not isinstance(mount, dict)
            or set(mount) != {"type", "sourceSha256", "destination", "rw"}
            or not isinstance(mount["destination"], str)
            or mount["destination"] in destinations
            or mount["destination"] not in expected_mounts
            or SHA256.fullmatch(str(mount["sourceSha256"])) is None
            or not isinstance(mount["rw"], bool)
        ):
            raise HubLifecycleLabError("Hub lifecycle mount evidence is invalid")
        expected_type, expected_rw, expected_source = expected_mounts[mount["destination"]]
        if (
            mount["type"] != expected_type
            or mount["rw"] is not expected_rw
            or expected_source is not None
            and mount["sourceSha256"] != expected_source
        ):
            raise HubLifecycleLabError("Hub lifecycle mount evidence violates the catalog")
        destinations.add(mount["destination"])


def _validate_installation_evidence(
    app_id: str,
    contract: Mapping[str, Any],
    installation: Any,
) -> dict[str, Any]:
    expected_services = {service["id"] for service in contract["services"]}
    expected_volumes = {
        volume["name"] for volume in contract["volumes"] if volume["source"] == "app-data"
    }
    expected_volumes.update(
        f"secrets-{service['id']}"
        for service in contract["services"]
        if service["hasSecrets"] is True
    )
    if (
        not isinstance(installation, dict)
        or set(installation) != {"services", "volumes"}
        or not isinstance(installation["services"], dict)
        or set(installation["services"]) != expected_services
        or not isinstance(installation["volumes"], dict)
        or set(installation["volumes"]) != expected_volumes
    ):
        raise HubLifecycleLabError("Hub lifecycle installation evidence is invalid")
    for service_id, observed in installation["services"].items():
        definition = next(
            service for service in contract["services"] if service["id"] == service_id
        )
        _validate_service_evidence(
            app_id=app_id,
            definition=definition,
            contract=contract,
            observed=observed,
        )
    for logical_name, observed in installation["volumes"].items():
        expected_name = f"echo-hub-{app_id}-{logical_name}"
        labels = observed.get("labels") if isinstance(observed, dict) else None
        expected_labels = _expected_volume_labels(app_id, contract, logical_name)
        if (
            not isinstance(observed, dict)
            or set(observed) != {"name", "mountpointSha256", "labels"}
            or observed["name"] != expected_name
            or SHA256.fullmatch(str(observed["mountpointSha256"])) is None
            or not isinstance(labels, dict)
            or any(labels.get(key) != expected for key, expected in expected_labels.items())
        ):
            raise HubLifecycleLabError("Hub lifecycle retained volume evidence is invalid")
    return installation


def _validate_control_operation_evidence(
    *,
    plan: Mapping[str, Any],
    app_id: str,
    operation: str,
    service_order: Sequence[str],
    public_container_id: str,
    observed: Any,
) -> dict[str, Any]:
    if (
        not isinstance(observed, dict)
        or set(observed) != {"operation", "operationId", "planId", "serviceOrder", "result"}
        or observed.get("operation") != operation
        or re.fullmatch(r"[0-9a-f]{32}", str(observed.get("operationId") or "")) is None
        or SHA256.fullmatch(str(observed.get("planId") or "")) is None
        or observed.get("serviceOrder") != list(service_order)
    ):
        raise HubLifecycleLabError("Hub lifecycle control operation evidence is invalid")
    result = _validate_control_result(
        observed.get("result"),
        operation=operation,
        app_id=app_id,
        plan_id=observed["planId"],
        catalog_digest=plan["catalog"]["digest"],
        service_count=len(service_order),
    )
    if result["containerId"] != public_container_id:
        raise HubLifecycleLabError("Hub lifecycle control public container identity changed")
    return observed


def _validate_stopped_control_evidence(
    *,
    contract: Mapping[str, Any],
    initial_installation: Mapping[str, Any],
    observed_containers: Any,
    observed_runtime: Any,
) -> None:
    expected_services = {service["id"]: service for service in contract["services"]}
    if not isinstance(observed_containers, dict) or set(observed_containers) != set(
        expected_services
    ):
        raise HubLifecycleLabError("Hub lifecycle stopped container evidence is incomplete")
    for service_id, container in observed_containers.items():
        if (
            not isinstance(container, dict)
            or set(container) != {"containerId", "running", "state", "exitCode"}
            or container.get("containerId")
            != initial_installation["services"][service_id]["containerId"]
            or container.get("running") is not False
            or container.get("state") != "exited"
            or container.get("exitCode") != 0
        ):
            raise HubLifecycleLabError("Hub lifecycle stopped container evidence is invalid")
    runtime_fields = {
        "status",
        "serviceCount",
        "runningServices",
        "healthyServices",
        "restartCount",
        "services",
        "diagnosticsStatus",
    }
    runtime_services = (
        observed_runtime.get("services") if isinstance(observed_runtime, dict) else None
    )
    if (
        not isinstance(observed_runtime, dict)
        or set(observed_runtime) != runtime_fields
        or observed_runtime.get("status") != "stopped"
        or observed_runtime.get("serviceCount") != len(expected_services)
        or observed_runtime.get("runningServices") != 0
        or observed_runtime.get("healthyServices") != 0
        or not isinstance(observed_runtime.get("restartCount"), int)
        or isinstance(observed_runtime.get("restartCount"), bool)
        or not 0 <= observed_runtime["restartCount"] <= 64_000_000
        or observed_runtime.get("diagnosticsStatus") != "stopped"
        or not isinstance(runtime_services, dict)
        or set(runtime_services) != set(expected_services)
    ):
        raise HubLifecycleLabError("Hub lifecycle stopped runtime evidence is invalid")
    restart_total = 0
    for service in runtime_services.values():
        if (
            not isinstance(service, dict)
            or set(service) != {"state", "health", "restartCount", "oomKilled", "exitCode"}
            or service.get("state") != "exited"
            or service.get("health")
            not in {"healthy", "unhealthy", "starting", "not-configured", "unknown"}
            or not isinstance(service.get("restartCount"), int)
            or isinstance(service.get("restartCount"), bool)
            or not 0 <= service["restartCount"] <= 1_000_000
            or service.get("oomKilled") is not False
            or service.get("exitCode") not in {None, 0}
        ):
            raise HubLifecycleLabError("Hub lifecycle stopped runtime service evidence is invalid")
        restart_total += service["restartCount"]
    if observed_runtime["restartCount"] != restart_total:
        raise HubLifecycleLabError("Hub lifecycle stopped restart evidence is invalid")


def _validate_lifecycle_control_evidence(
    *,
    plan: Mapping[str, Any],
    app_id: str,
    contract: Mapping[str, Any],
    initial_installation: Mapping[str, Any],
    observed: Any,
) -> None:
    if not isinstance(observed, dict) or set(observed) != {
        "serviceOrder",
        "stop",
        "start",
        "restart",
    }:
        raise HubLifecycleLabError("Hub lifecycle control evidence is malformed")
    service_order = [service["id"] for service in contract["services"]]
    if observed.get("serviceOrder") != service_order:
        raise HubLifecycleLabError("Hub lifecycle service order evidence is invalid")
    public_service = next(service for service in contract["services"] if service["public"])
    public_container_id = initial_installation["services"][public_service["id"]]["containerId"]
    stop = observed.get("stop")
    if not isinstance(stop, dict) or set(stop) != {"operation", "containers", "runtime"}:
        raise HubLifecycleLabError("Hub lifecycle stop evidence is malformed")
    _validate_control_operation_evidence(
        plan=plan,
        app_id=app_id,
        operation="stop",
        service_order=service_order,
        public_container_id=public_container_id,
        observed=stop["operation"],
    )
    _validate_stopped_control_evidence(
        contract=contract,
        initial_installation=initial_installation,
        observed_containers=stop["containers"],
        observed_runtime=stop["runtime"],
    )
    operation_ids = {stop["operation"]["operationId"]}
    plan_ids = {stop["operation"]["planId"]}
    for operation in ("start", "restart"):
        record = observed.get(operation)
        if not isinstance(record, dict) or set(record) != {
            "operation",
            "installation",
            "publicEndpoint",
            "runtimeHealth",
        }:
            raise HubLifecycleLabError("Hub lifecycle running control evidence is malformed")
        _validate_control_operation_evidence(
            plan=plan,
            app_id=app_id,
            operation=operation,
            service_order=service_order,
            public_container_id=public_container_id,
            observed=record["operation"],
        )
        _validate_installation_evidence(app_id, contract, record["installation"])
        if record["installation"] != initial_installation:
            raise HubLifecycleLabError("Hub lifecycle control changed installation evidence")
        _validate_public_endpoint_evidence(contract["endpoint"], record["publicEndpoint"])
        _validate_runtime_health_evidence(contract, record["runtimeHealth"])
        operation_ids.add(record["operation"]["operationId"])
        plan_ids.add(record["operation"]["planId"])
    if len(operation_ids) != 3 or len(plan_ids) != 3:
        raise HubLifecycleLabError("Hub lifecycle control operation identities are not unique")


def _validate_result_value(
    plan: Mapping[str, Any],
    value: dict[str, Any],
    *,
    now: int | None = None,
) -> dict[str, Any]:
    required = {
        "schemaVersion",
        "kind",
        "planId",
        "releaseCandidate",
        "operationsBundle",
        "runtime",
        "catalogDigest",
        "architecture",
        "apps",
        "firstInstall",
        "reinstall",
        "finalState",
        "allPassed",
        "completedAtUnix",
        "resultId",
    }
    unsigned = dict(value)
    result_id = unsigned.pop("resultId", None)
    if (
        set(value) != required
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != RESULT_KIND
        or value.get("planId") != plan["planId"]
        or value.get("releaseCandidate") != plan["releaseCandidate"]
        or value.get("operationsBundle") != plan["operationsBundle"]
        or value.get("runtime") != plan["runtime"]
        or value.get("catalogDigest") != plan["catalog"]["digest"]
        or value.get("architecture") != plan["catalog"]["architecture"]
        or value.get("apps") != list(APPS)
        or value.get("finalState") != "not-installed-data-retained"
        or value.get("allPassed") is not True
        or not isinstance(value.get("completedAtUnix"), int)
        or isinstance(value.get("completedAtUnix"), bool)
        or not 0 < value["completedAtUnix"] <= (int(time.time()) if now is None else now) + 300
        or result_id != _sha256(_canonical(unsigned))
    ):
        raise HubLifecycleLabError("Hub lifecycle result identity is invalid")
    first = value.get("firstInstall")
    second = value.get("reinstall")
    if (
        not isinstance(first, dict)
        or not isinstance(second, dict)
        or set(first) != set(APPS)
        or set(second) != set(APPS)
    ):
        raise HubLifecycleLabError("Hub lifecycle result app set is invalid")
    for app_id in APPS:
        contract = plan["catalog"]["apps"][app_id]
        expected_services = {service["id"] for service in contract["services"]}
        for phase, record in (("first", first[app_id]), ("second", second[app_id])):
            expected_record_fields = {
                "installation",
                "revealedSecretNames",
                "publicEndpoint",
                "runtimeHealth",
            }
            if phase == "first":
                expected_record_fields.add("lifecycleControl")
            if not isinstance(record, dict) or set(record) != expected_record_fields:
                raise HubLifecycleLabError("Hub lifecycle app result is malformed")
            expected_revealed = (
                ["admin-password"]
                if app_id in {"nextcloud", "qbittorrent", "syncthing", "paperless-ngx"}
                and phase == "first"
                else []
            )
            installation = record["installation"]
            if record["revealedSecretNames"] != expected_revealed:
                raise HubLifecycleLabError("Hub lifecycle app result contract is invalid")
            _validate_public_endpoint_evidence(contract["endpoint"], record["publicEndpoint"])
            _validate_runtime_health_evidence(contract, record["runtimeHealth"])
            _validate_installation_evidence(app_id, contract, installation)
            if phase == "first":
                _validate_lifecycle_control_evidence(
                    plan=plan,
                    app_id=app_id,
                    contract=contract,
                    initial_installation=installation,
                    observed=record["lifecycleControl"],
                )
        if first[app_id]["installation"]["volumes"] != second[app_id]["installation"]["volumes"]:
            raise HubLifecycleLabError("Hub lifecycle retained volume evidence changed")
        for service_id in expected_services:
            first_service = dict(first[app_id]["installation"]["services"][service_id])
            second_service = dict(second[app_id]["installation"]["services"][service_id])
            first_service.pop("containerId")
            second_service.pop("containerId")
            if first_service != second_service:
                raise HubLifecycleLabError("Hub lifecycle reinstall runtime evidence changed")
    return value


def validate_evidence_bytes(
    plan_raw: bytes,
    result_raw: bytes,
    *,
    expected_candidate: Mapping[str, str] | None = None,
    now: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    plan = _validate_plan_value(_strict_json(plan_raw, "Hub lifecycle plan"))
    result = _validate_result_value(
        plan,
        _strict_json(result_raw, "Hub lifecycle result"),
        now=now,
    )
    if expected_candidate is not None:
        shared_fields = (
            "indexId",
            "sourceRevision",
            "agentRevision",
            "releaseTag",
            "operationsArtifactId",
            "operationsArchiveSha256",
            "immutableReference",
        )
        if any(
            field not in expected_candidate
            or plan["releaseCandidate"][field] != expected_candidate[field]
            for field in shared_fields
        ):
            raise HubLifecycleLabError(
                "Hub lifecycle evidence belongs to another release candidate"
            )
    return plan, result


def verify_result(
    *,
    plan_path: Path,
    result_path: Path,
    docker: DockerJson = _docker_json,
) -> dict[str, Any]:
    plan = load_plan(plan_path)
    if os.name != "nt" and stat.S_IMODE(result_path.stat().st_mode) != 0o444:
        raise HubLifecycleLabError("Hub lifecycle result must be mode 0444")
    value = _validate_result_value(
        plan,
        _strict_json(
            _read_regular(result_path, "Hub lifecycle result"),
            "Hub lifecycle result",
        ),
    )
    candidate, bundle = _candidate_bundle_identity(
        candidate_index=Path(str(plan["releaseCandidate"]["indexPath"])),
        bundle_root=Path(str(plan["operationsBundle"]["rootPath"])),
        trusted_uid=os.getuid(),
    )
    if candidate != plan["releaseCandidate"] or bundle != plan["operationsBundle"]:
        raise HubLifecycleLabError("Hub lifecycle verification inputs changed")
    if _running_candidate(candidate["immutableReference"], docker) != plan["runtime"]:
        raise HubLifecycleLabError("Hub lifecycle verified runtime changed")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan")
    plan.add_argument("--base-url", default="http://127.0.0.1:8000")
    plan.add_argument("--password-env", default="ECHO_ADMIN_PASSWORD")
    plan.add_argument("--candidate-index", required=True, type=Path)
    plan.add_argument("--bundle-root", required=True, type=Path)
    plan.add_argument("--output", required=True, type=Path)
    run = subparsers.add_parser("run")
    run.add_argument("--plan", required=True, type=Path)
    run.add_argument("--confirmation", required=True)
    run.add_argument("--password-env", default="ECHO_ADMIN_PASSWORD")
    run.add_argument("--private-paperless-secret-output", type=Path)
    run.add_argument("--output", required=True, type=Path)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--plan", required=True, type=Path)
    verify.add_argument("--result", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if sys.platform != "linux" or os.geteuid() != 0:
            raise HubLifecycleLabError(
                "Hub lifecycle lab commands require Linux root on the appliance host"
            )
        password = os.environ.get(args.password_env, "")
        if args.command == "plan":
            if not password:
                raise HubLifecycleLabError("Hub lifecycle administrator password is unavailable")
            base_url = _origin(args.base_url)
            token = _login(base_url, password, _http_request)
            catalog = _request(
                _http_request,
                "GET",
                f"{base_url}/api/appliance/hub/catalog",
                token=token,
            )
            result = build_plan(
                base_url=base_url,
                catalog=catalog,
                candidate_index=args.candidate_index,
                bundle_root=args.bundle_root,
                output=args.output,
            )
        elif args.command == "run":
            result = run_plan(
                plan_path=args.plan,
                confirmation=args.confirmation,
                password=password,
                output=args.output,
                private_paperless_secret_output=args.private_paperless_secret_output,
            )
        else:
            result = verify_result(plan_path=args.plan, result_path=args.result)
    except (HubLifecycleLabError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
