#!/usr/bin/env python3
"""Prove candidate-bound Syncthing and Home Assistant LAN discovery on real devices."""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import ipaddress
import json
import os
import platform
import re
import secrets
import socket
import stat
import struct
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

try:
    from deploy.appliance import hub_lifecycle_lab as hub_lab
except ModuleNotFoundError:
    import hub_lifecycle_lab as hub_lab

SCHEMA_VERSION = 2
PLAN_KIND = "echo.lan-discovery-functional-physical-plan"
PROBE_KIND = "echo.lan-discovery-functional-physical-probe"
RESULT_KIND = "echo.lan-discovery-functional-physical-result"
CREDENTIAL_KIND = "echo.lan-discovery-private-credentials"
PLAN_NAME = "lan-discovery-functional-plan.json"
RESULT_NAME = "lan-discovery-functional-result.json"
SYNCTHING_NAS_NAME = "lan-syncthing-nas.json"
SYNCTHING_COMPANION_NAME = "lan-syncthing-companion.json"
HOME_ASSISTANT_NAME = "lan-home-assistant.json"
PROBE_NAMES = (SYNCTHING_NAS_NAME, SYNCTHING_COMPANION_NAME, HOME_ASSISTANT_NAME)
NAS_CREDENTIAL_NAME = "lan-discovery-nas-credentials.json"
COMPANION_CREDENTIAL_NAME = "lan-discovery-companion-credentials.json"
APP_IDS = ("syncthing", "home-assistant")
SYNCTHING_ROLES = ("nas", "companion")
WORKFLOW = [
    "syncthing-nas-local-discovery",
    "syncthing-companion-local-discovery",
    "syncthing-direct-lan-connection",
    "home-assistant-zeroconf-entry",
    "home-assistant-ssdp-entry",
    "home-assistant-reversible-control",
]
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
HTTP_TIMEOUT_SECONDS = 30
CONTROL_TIMEOUT_SECONDS = 30
CONTROL_POLL_SECONDS = 1.0
PROBE_MAX_AGE_SECONDS = 60 * 60
PROBE_FUTURE_SKEW_SECONDS = 5 * 60
PROBE_MAX_SKEW_SECONDS = 10 * 60
SHA256 = re.compile(r"^[0-9a-f]{64}$")
DEVICE_ID = re.compile(r"^[A-Z0-9]{7}(?:-[A-Z0-9]{7}){7}$")
ENTITY_ID = re.compile(r"^(?:light|switch)\.[a-z0-9_]+$")
DIRECT_CONNECTION_TYPES = {"tcp-client", "tcp-server", "quic-client", "quic-server"}
DISCOVERY_SOURCES = ("zeroconf", "ssdp")


class LanDiscoveryFunctionalLabError(RuntimeError):
    """LAN discovery evidence is unsafe, stale, private or incomplete."""


HttpRequest = Callable[..., tuple[int, Mapping[str, str], bytes]]
WebSocketQuery = Callable[[str, str, Sequence[Mapping[str, Any]]], list[Any]]


def _canonical(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _opaque(plan_id: str, label: str, value: str) -> str:
    return _sha256(f"{plan_id}\0{label}\0{value}".encode())


def _strict_json(raw: bytes, label: str) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise LanDiscoveryFunctionalLabError(f"{label} contains duplicate JSON keys")
            result[key] = value
        return result

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise LanDiscoveryFunctionalLabError(f"{label} is not strict JSON") from exc


def _origin(value: str, *, expected_port: int | None = None) -> str:
    parsed = urlsplit(str(value).strip())
    try:
        port = parsed.port
    except ValueError as exc:
        raise LanDiscoveryFunctionalLabError("LAN service URL is invalid") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or port is None
        or expected_port is not None
        and port != expected_port
    ):
        raise LanDiscoveryFunctionalLabError(
            "LAN functional APIs must use their catalog loopback ports"
        )
    return urlunsplit(("http", f"127.0.0.1:{port}", "", "", ""))


def _read_public(path: Path, label: str, *, mode: int) -> tuple[bytes, Any]:
    try:
        info = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(info.st_mode)
            or os.name != "nt"
            and stat.S_IMODE(info.st_mode) != mode
        ):
            raise LanDiscoveryFunctionalLabError(f"{label} has an unsafe mode or type")
        raw = hub_lab._read_regular(path, label)
    except (OSError, hub_lab.HubLifecycleLabError) as exc:
        raise LanDiscoveryFunctionalLabError(str(exc)) from exc
    return raw, _strict_json(raw, label)


def _write_new(path: Path, value: Mapping[str, Any], *, mode: int) -> None:
    try:
        hub_lab._write_new(path, value, mode=mode)
    except hub_lab.HubLifecycleLabError as exc:
        raise LanDiscoveryFunctionalLabError(str(exc)) from exc


def _bundle_identity(
    *, candidate_index: Path, bundle_root: Path, trusted_uid: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        candidate, bundle = hub_lab._candidate_bundle_identity(
            candidate_index=candidate_index,
            bundle_root=bundle_root,
            trusted_uid=trusted_uid,
        )
        root = Path(bundle["rootPath"])
        tool = root / "lan_discovery_functional_lab.py"
        raw = hub_lab._read_regular(tool, "candidate LAN discovery functional lab tool")
        manifest = hub_lab._strict_json(
            hub_lab._read_regular(root / "bundle-manifest.json", "operations bundle manifest"),
            "operations bundle manifest",
        )
        info = tool.lstat()
    except (OSError, hub_lab.HubLifecycleLabError) as exc:
        raise LanDiscoveryFunctionalLabError(str(exc)) from exc
    artifact = manifest.get("artifact") if isinstance(manifest, dict) else None
    files = manifest.get("files") if isinstance(manifest, dict) else None
    record = files.get(tool.name) if isinstance(files, dict) else None
    entrypoint = (
        artifact.get("entrypoints", {}).get("lanDiscoveryFunctionalLab")
        if isinstance(artifact, dict)
        else None
    )
    if (
        entrypoint
        != "./lan_discovery_functional_lab.py plan|credentials|syncthing|home-assistant|verify"
        or record != {"sha256": _sha256(raw), "size": len(raw), "mode": "0755"}
        or info.st_uid != trusted_uid
        or stat.S_IMODE(info.st_mode) != 0o755
    ):
        raise LanDiscoveryFunctionalLabError(
            "LAN discovery functional tool is not from the release candidate"
        )
    return candidate, {
        **bundle,
        "lanDiscoveryLabSha256": _sha256(raw),
        "lanDiscoveryLabSize": len(raw),
    }


def _validate_discovery_network_contract(catalog: Mapping[str, Any]) -> None:
    apps = catalog.get("apps") if isinstance(catalog, dict) else None
    syncthing = apps.get("syncthing") if isinstance(apps, dict) else None
    home_assistant = apps.get("home-assistant") if isinstance(apps, dict) else None
    home_services = home_assistant.get("services") if isinstance(home_assistant, dict) else None
    if (
        not isinstance(syncthing, dict)
        or syncthing.get("providers") != ["lan-discovery"]
        or not isinstance(home_services, list)
        or not any(
            isinstance(service, dict)
            and service.get("public") is True
            and service.get("networkMode") == "host"
            for service in home_services
        )
    ):
        raise LanDiscoveryFunctionalLabError(
            "candidate app networking cannot provide real LAN discovery"
        )


def build_plan(
    *,
    syncthing_base_url: str,
    home_assistant_base_url: str,
    catalog: Mapping[str, Any],
    candidate_index: Path,
    bundle_root: Path,
    output: Path,
    trusted_uid: int | None = None,
    docker: hub_lab.DockerJson = hub_lab._docker_json,
) -> dict[str, Any]:
    if output.name != PLAN_NAME:
        raise LanDiscoveryFunctionalLabError(f"LAN functional plan must use {PLAN_NAME}")
    uid = os.getuid() if trusted_uid is None else trusted_uid
    snapshot = hub_lab._catalog_snapshot(catalog, expected_installed=APP_IDS)
    _validate_discovery_network_contract(snapshot)
    syncthing_contract = snapshot["apps"]["syncthing"]
    home_assistant_contract = snapshot["apps"]["home-assistant"]
    candidate, bundle = _bundle_identity(
        candidate_index=candidate_index,
        bundle_root=bundle_root,
        trusted_uid=uid,
    )
    identity: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": PLAN_KIND,
        "baseUrls": {
            "syncthing": _origin(
                syncthing_base_url,
                expected_port=syncthing_contract["endpoint"]["port"],
            ),
            "homeAssistant": _origin(
                home_assistant_base_url,
                expected_port=home_assistant_contract["endpoint"]["port"],
            ),
        },
        "releaseCandidate": candidate,
        "operationsBundle": bundle,
        "runtime": hub_lab._running_candidate(candidate["immutableReference"], docker),
        "catalog": snapshot,
        "installations": {
            app_id: hub_lab.inspect_installation(app_id, snapshot["apps"][app_id], docker)
            for app_id in APP_IDS
        },
        "workflow": WORKFLOW,
    }
    plan_id = _sha256(_canonical(identity))
    value = {
        **identity,
        "planId": plan_id,
        "confirmation": f"RUN ECHO LAN DISCOVERY FUNCTIONAL LAB {plan_id}",
    }
    _verify_local_tool(value)
    try:
        _write_new(output, value, mode=0o400)
    except FileExistsError as exc:
        raise LanDiscoveryFunctionalLabError("LAN functional plan output already exists") from exc
    return value


def _validate_plan_value(value: Any) -> dict[str, Any]:
    required = {
        "schemaVersion",
        "kind",
        "baseUrls",
        "releaseCandidate",
        "operationsBundle",
        "runtime",
        "catalog",
        "installations",
        "workflow",
        "planId",
        "confirmation",
    }
    if (
        not isinstance(value, dict)
        or set(value) != required
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != PLAN_KIND
    ):
        raise LanDiscoveryFunctionalLabError("LAN functional plan contract is invalid")
    identity = {key: item for key, item in value.items() if key not in {"planId", "confirmation"}}
    plan_id = _sha256(_canonical(identity))
    try:
        catalog = hub_lab._validate_catalog_snapshot_value(value["catalog"])
        _validate_discovery_network_contract(catalog)
        for app_id in APP_IDS:
            hub_lab._validate_installation_evidence(
                app_id,
                catalog["apps"][app_id],
                value["installations"][app_id],
            )
    except (KeyError, TypeError, hub_lab.HubLifecycleLabError) as exc:
        raise LanDiscoveryFunctionalLabError(str(exc)) from exc
    candidate = value["releaseCandidate"]
    bundle = value["operationsBundle"]
    runtime = value["runtime"]
    base_urls = value["baseUrls"]
    if (
        value["planId"] != plan_id
        or value["confirmation"] != f"RUN ECHO LAN DISCOVERY FUNCTIONAL LAB {plan_id}"
        or value["workflow"] != WORKFLOW
        or not isinstance(value["installations"], dict)
        or set(value["installations"]) != set(APP_IDS)
        or not isinstance(base_urls, dict)
        or set(base_urls) != {"syncthing", "homeAssistant"}
        or _origin(
            base_urls["syncthing"],
            expected_port=catalog["apps"]["syncthing"]["endpoint"]["port"],
        )
        != base_urls["syncthing"]
        or _origin(
            base_urls["homeAssistant"],
            expected_port=catalog["apps"]["home-assistant"]["endpoint"]["port"],
        )
        != base_urls["homeAssistant"]
        or not isinstance(candidate, dict)
        or not isinstance(bundle, dict)
        or bundle.get("artifactId") != candidate.get("operationsArtifactId")
        or bundle.get("archiveSha256") != candidate.get("operationsArchiveSha256")
        or bundle.get("imageReference") != candidate.get("immutableReference")
        or SHA256.fullmatch(str(bundle.get("lanDiscoveryLabSha256") or "")) is None
        or not isinstance(bundle.get("lanDiscoveryLabSize"), int)
        or isinstance(bundle.get("lanDiscoveryLabSize"), bool)
        or bundle["lanDiscoveryLabSize"] <= 0
        or not isinstance(runtime, dict)
        or set(runtime) != {"main", "proxy", "discovery"}
        or any(
            not isinstance(runtime.get(role), dict)
            or runtime[role].get("image") != candidate.get("immutableReference")
            or SHA256.fullmatch(str(runtime[role].get("containerId") or "")) is None
            for role in runtime
        )
    ):
        raise LanDiscoveryFunctionalLabError("LAN functional plan identity is invalid")
    return value


def load_plan(path: Path) -> dict[str, Any]:
    if path.name != PLAN_NAME:
        raise LanDiscoveryFunctionalLabError(f"LAN functional plan must use {PLAN_NAME}")
    _raw, value = _read_public(path, "LAN functional plan", mode=0o400)
    return _validate_plan_value(value)


def _verify_local_tool(
    plan: Mapping[str, Any],
    tool_path: Path | None = None,
) -> dict[str, Any]:
    path = Path(__file__).absolute() if tool_path is None else tool_path
    try:
        info = path.lstat()
        raw = hub_lab._read_regular(path, "local LAN discovery functional lab tool")
    except (OSError, hub_lab.HubLifecycleLabError) as exc:
        raise LanDiscoveryFunctionalLabError(str(exc)) from exc
    bundle = plan.get("operationsBundle")
    if (
        path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or os.name != "nt"
        and stat.S_IMODE(info.st_mode) != 0o755
        or not isinstance(bundle, dict)
        or bundle.get("lanDiscoveryLabSha256") != _sha256(raw)
        or bundle.get("lanDiscoveryLabSize") != len(raw)
    ):
        raise LanDiscoveryFunctionalLabError(
            "local LAN discovery tool differs from the release candidate"
        )
    return {"sha256": _sha256(raw), "size": len(raw)}


def create_private_credentials(
    *,
    plan_path: Path,
    role: str,
    syncthing_username: str,
    syncthing_password: str,
    home_assistant_token: str = "",
    control_entity_id: str = "",
    output: Path,
) -> dict[str, Any]:
    plan = load_plan(plan_path)
    _verify_local_tool(plan)
    if role not in SYNCTHING_ROLES:
        raise LanDiscoveryFunctionalLabError("LAN private credential role is invalid")
    if output.exists() or output.is_symlink():
        raise LanDiscoveryFunctionalLabError("LAN private credential output already exists")
    expected_name = NAS_CREDENTIAL_NAME if role == "nas" else COMPANION_CREDENTIAL_NAME
    try:
        parent = output.parent.resolve(strict=True)
        parent_info = parent.stat()
        public_parent = plan_path.parent.resolve(strict=True)
    except OSError as exc:
        raise LanDiscoveryFunctionalLabError(
            "LAN private credential directory is unavailable"
        ) from exc
    if (
        output.name != expected_name
        or not output.is_absolute()
        or output.parent.is_symlink()
        or output.parent != parent
        or not parent.is_dir()
        or parent == public_parent
        or public_parent in parent.parents
        or parent_info.st_uid != os.getuid()
        or stat.S_IMODE(parent_info.st_mode) != 0o700
        or not isinstance(syncthing_username, str)
        or not 1 <= len(syncthing_username) <= 128
        or not isinstance(syncthing_password, str)
        or not 8 <= len(syncthing_password) <= 512
        or any(character in syncthing_username + syncthing_password for character in "\r\n\x00")
    ):
        raise LanDiscoveryFunctionalLabError(
            "LAN private credentials require one separate owner-only directory"
        )
    value: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": CREDENTIAL_KIND,
        "planId": plan["planId"],
        "role": role,
        "syncthing": {
            "username": syncthing_username,
            "password": syncthing_password,
        },
    }
    if role == "nas":
        if (
            not isinstance(home_assistant_token, str)
            or not 32 <= len(home_assistant_token) <= 4096
            or any(character in home_assistant_token for character in "\r\n\x00")
            or not isinstance(control_entity_id, str)
            or ENTITY_ID.fullmatch(control_entity_id) is None
        ):
            raise LanDiscoveryFunctionalLabError(
                "NAS private credentials require a Home Assistant token and reversible entity"
            )
        value["homeAssistant"] = {
            "token": home_assistant_token,
            "controlEntityId": control_entity_id,
        }
    elif home_assistant_token or control_entity_id:
        raise LanDiscoveryFunctionalLabError(
            "companion credentials cannot contain Home Assistant secrets"
        )
    _write_new(output, value, mode=0o400)
    return value


def _private_credentials(
    path: Path,
    plan: Mapping[str, Any],
    role: str,
    *,
    plan_path: Path,
) -> dict[str, str]:
    expected_name = NAS_CREDENTIAL_NAME if role == "nas" else COMPANION_CREDENTIAL_NAME
    try:
        info = path.lstat()
        parent = path.parent.resolve(strict=True)
        parent_info = parent.stat()
        public_parent = plan_path.parent.resolve(strict=True)
        if (
            path.name != expected_name
            or not path.is_absolute()
            or path.is_symlink()
            or not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o400
            or not parent.is_dir()
            or path.parent != parent
            or parent == public_parent
            or public_parent in parent.parents
            or parent_info.st_uid != os.getuid()
            or stat.S_IMODE(parent_info.st_mode) != 0o700
        ):
            raise LanDiscoveryFunctionalLabError(
                "LAN private credentials must use the fixed owner-only path contract"
            )
        raw = hub_lab._read_regular(path, "LAN private credentials")
    except (OSError, hub_lab.HubLifecycleLabError) as exc:
        raise LanDiscoveryFunctionalLabError(str(exc)) from exc
    value = _strict_json(raw, "LAN private credentials")
    required = {"schemaVersion", "kind", "planId", "role", "syncthing"}
    if role == "nas":
        required.add("homeAssistant")
    if (
        not isinstance(value, dict)
        or set(value) != required
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != CREDENTIAL_KIND
        or value.get("planId") != plan["planId"]
        or value.get("role") != role
        or not isinstance(value.get("syncthing"), dict)
        or set(value["syncthing"]) != {"username", "password"}
    ):
        raise LanDiscoveryFunctionalLabError("LAN private credential contract is invalid")
    username = value["syncthing"].get("username")
    password = value["syncthing"].get("password")
    if (
        not isinstance(username, str)
        or not 1 <= len(username) <= 128
        or not isinstance(password, str)
        or not 8 <= len(password) <= 512
        or any(character in username + password for character in "\r\n\x00")
    ):
        raise LanDiscoveryFunctionalLabError("Syncthing private credentials are invalid")
    result = {"syncthingUsername": username, "syncthingPassword": password}
    if role == "nas":
        home_assistant = value.get("homeAssistant")
        if (
            not isinstance(home_assistant, dict)
            or set(home_assistant) != {"token", "controlEntityId"}
            or not isinstance(home_assistant.get("token"), str)
            or not 32 <= len(home_assistant["token"]) <= 4096
            or any(character in home_assistant["token"] for character in "\r\n\x00")
            or not isinstance(home_assistant.get("controlEntityId"), str)
            or ENTITY_ID.fullmatch(home_assistant["controlEntityId"]) is None
        ):
            raise LanDiscoveryFunctionalLabError("Home Assistant private credentials are invalid")
        result["homeAssistantToken"] = home_assistant["token"]
        result["controlEntityId"] = home_assistant["controlEntityId"]
    return result


def _http_request(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
    maximum: int,
    timeout: float,
) -> tuple[int, Mapping[str, str], bytes]:
    parsed = urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.port is None:
        raise LanDiscoveryFunctionalLabError("LAN API request escaped loopback")
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=timeout)
    try:
        connection.request(
            method,
            urlunsplit(("", "", parsed.path or "/", parsed.query, "")),
            body,
            dict(headers),
        )
        response = connection.getresponse()
        raw = response.read(maximum + 1)
        response_headers = {key.lower(): value for key, value in response.getheaders()}
        status = int(response.status)
    finally:
        connection.close()
    if len(raw) > maximum:
        raise LanDiscoveryFunctionalLabError("LAN API response is oversized")
    return status, response_headers, raw


def _api_json(
    request: HttpRequest,
    method: str,
    url: str,
    *,
    headers: Mapping[str, str],
    payload: Mapping[str, Any] | None = None,
    expected: int = 200,
) -> Any:
    body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
    actual_headers = {"Accept": "application/json", "Connection": "close", **headers}
    if body is not None:
        actual_headers["Content-Type"] = "application/json"
    status, _response_headers, raw = request(
        method,
        url,
        actual_headers,
        body,
        MAX_RESPONSE_BYTES,
        HTTP_TIMEOUT_SECONDS,
    )
    if status != expected:
        raise LanDiscoveryFunctionalLabError(f"LAN API returned HTTP {status}")
    return _strict_json(raw, "LAN API response") if raw else None


def _machine_identity() -> str:
    for candidate in (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id")):
        try:
            value = candidate.read_text(encoding="ascii").strip()
        except (OSError, UnicodeError):
            continue
        if re.fullmatch(r"[0-9a-fA-F]{32}", value):
            return f"machine-id:{value.casefold()}"
    fallback = "\0".join((platform.system(), platform.machine(), platform.node()))
    if not platform.node():
        raise LanDiscoveryFunctionalLabError("physical machine identity is unavailable")
    return f"platform:{fallback}"


def _split_host_port(value: str) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, int]:
    address = str(value).strip()
    try:
        if address.startswith("["):
            closing = address.index("]")
            host = address[1:closing]
            port = int(address[closing + 2 :])
        else:
            host, raw_port = address.rsplit(":", 1)
            port = int(raw_port)
        ip = ipaddress.ip_address(host.split("%", 1)[0])
    except (ValueError, IndexError) as exc:
        raise LanDiscoveryFunctionalLabError("Syncthing connection address is invalid") from exc
    if not 1 <= port <= 65535:
        raise LanDiscoveryFunctionalLabError("Syncthing connection port is invalid")
    return ip, port


def _syncthing_details(
    *,
    plan: Mapping[str, Any],
    role: str,
    status: Any,
    devices: Any,
    discovery: Any,
    connections: Any,
    machine_identity: str,
) -> dict[str, Any]:
    if role not in SYNCTHING_ROLES:
        raise LanDiscoveryFunctionalLabError("Syncthing probe role is invalid")
    local_id = status.get("myID") if isinstance(status, dict) else None
    discovery_status = status.get("discoveryStatus") if isinstance(status, dict) else None
    connection_map = connections.get("connections") if isinstance(connections, dict) else None
    if (
        not isinstance(local_id, str)
        or DEVICE_ID.fullmatch(local_id) is None
        or not isinstance(devices, list)
        or not isinstance(discovery, dict)
        or not isinstance(connection_map, dict)
        or not isinstance(discovery_status, dict)
        or not any(
            isinstance(name, str)
            and name.casefold().endswith("local")
            and isinstance(item, dict)
            and item.get("error") is None
            for name, item in discovery_status.items()
        )
    ):
        raise LanDiscoveryFunctionalLabError("Syncthing local discovery is not healthy")
    configured: dict[str, Mapping[str, Any]] = {}
    for item in devices:
        if isinstance(item, dict) and isinstance(item.get("deviceID"), str):
            configured[item["deviceID"]] = item
    eligible: list[tuple[str, Mapping[str, Any], str]] = []
    for peer_id, connection in connection_map.items():
        config = configured.get(peer_id)
        cached = discovery.get(peer_id)
        if (
            peer_id == local_id
            or DEVICE_ID.fullmatch(str(peer_id)) is None
            or not isinstance(config, dict)
            or config.get("addresses") != ["dynamic"]
            or not isinstance(cached, list)
            or not cached
            or not isinstance(connection, dict)
            or connection.get("connected") is not True
            or connection.get("isLocal") is not True
            or connection.get("type") not in DIRECT_CONNECTION_TYPES
            or not isinstance(connection.get("address"), str)
        ):
            continue
        ip, port = _split_host_port(connection["address"])
        if not ip.is_private or ip.is_loopback or port != 22000:
            continue
        normalized = {str(item).removeprefix("tcp://").removeprefix("quic://") for item in cached}
        if connection["address"] not in normalized:
            continue
        eligible.append((peer_id, connection, connection["address"]))
    if len(eligible) != 1:
        raise LanDiscoveryFunctionalLabError(
            "Syncthing probe needs exactly one dynamic, locally discovered direct peer"
        )
    peer_id, connection, _address = eligible[0]
    traffic = connection.get("inBytesTotal", 0) + connection.get("outBytesTotal", 0)
    if not isinstance(traffic, int) or isinstance(traffic, bool) or traffic <= 0:
        raise LanDiscoveryFunctionalLabError("Syncthing direct peer has no observed traffic")
    version = connection.get("clientVersion")
    if not isinstance(version, str) or not version:
        raise LanDiscoveryFunctionalLabError("Syncthing direct peer version is unavailable")
    plan_id = plan["planId"]
    return {
        "role": role,
        "machineIdentitySha256": _opaque(plan_id, "machine", machine_identity),
        "localDeviceSha256": _opaque(plan_id, "syncthing-device", local_id),
        "peerDeviceSha256": _opaque(plan_id, "syncthing-device", peer_id),
        "configuredAddressesAreDynamic": True,
        "localDiscoveryHealthy": True,
        "peerFoundInDiscoveryCache": True,
        "connectionAddressMatchedDiscovery": True,
        "connectionType": connection["type"],
        "connectionIsLocal": True,
        "connectionAddressPrivate": True,
        "trafficBytes": traffic,
        "clientVersionSha256": _opaque(plan_id, "syncthing-version", version),
    }


def run_syncthing_probe(
    *,
    plan_path: Path,
    role: str,
    username: str,
    password: str,
    confirmation: str,
    output: Path,
    request: HttpRequest = _http_request,
    machine_identity: str | None = None,
    observed_at_unix: int | None = None,
) -> dict[str, Any]:
    plan = load_plan(plan_path)
    _verify_local_tool(plan)
    expected_name = SYNCTHING_NAS_NAME if role == "nas" else SYNCTHING_COMPANION_NAME
    if output.name != expected_name or confirmation != plan["confirmation"]:
        raise LanDiscoveryFunctionalLabError("Syncthing probe output or confirmation is invalid")
    if (
        not username
        or not password
        or any(character in username + password for character in "\r\n\x00")
    ):
        raise LanDiscoveryFunctionalLabError("Syncthing credentials are unavailable")
    authorization = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    headers = {"Authorization": f"Basic {authorization}"}
    base_url = plan["baseUrls"]["syncthing"]
    status = _api_json(request, "GET", f"{base_url}/rest/system/status", headers=headers)
    devices = _api_json(request, "GET", f"{base_url}/rest/config/devices", headers=headers)
    discovery = _api_json(request, "GET", f"{base_url}/rest/system/discovery", headers=headers)
    connections = _api_json(
        request,
        "GET",
        f"{base_url}/rest/system/connections",
        headers=headers,
    )
    payload = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": PROBE_KIND,
        "planId": plan["planId"],
        "probe": f"syncthing-{role}",
        "observedAtUnix": int(time.time()) if observed_at_unix is None else observed_at_unix,
        "passed": True,
        "details": _syncthing_details(
            plan=plan,
            role=role,
            status=status,
            devices=devices,
            discovery=discovery,
            connections=connections,
            machine_identity=_machine_identity() if machine_identity is None else machine_identity,
        ),
    }
    _write_new(output, payload, mode=0o444)
    return payload


def _read_exact(sock: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise LanDiscoveryFunctionalLabError("Home Assistant WebSocket closed early")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _ws_send_raw(sock: socket.socket, raw: bytes, *, opcode: int) -> None:
    key = secrets.token_bytes(4)
    length = len(raw)
    if length < 126:
        header = bytes((0x80 | opcode, 0x80 | length))
    elif length <= 0xFFFF:
        header = bytes((0x80 | opcode, 0x80 | 126)) + struct.pack("!H", length)
    else:
        header = bytes((0x80 | opcode, 0x80 | 127)) + struct.pack("!Q", length)
    masked = bytes(value ^ key[index % 4] for index, value in enumerate(raw))
    sock.sendall(header + key + masked)


def _ws_send(sock: socket.socket, payload: Mapping[str, Any]) -> None:
    _ws_send_raw(
        sock,
        json.dumps(payload, separators=(",", ":")).encode(),
        opcode=1,
    )


def _ws_receive(sock: socket.socket) -> Any:
    fragments: list[bytes] = []
    message_opcode: int | None = None
    while True:
        first, second = _read_exact(sock, 2)
        final = bool(first & 0x80)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", _read_exact(sock, 2))[0]
        elif length == 127:
            length = struct.unpack("!Q", _read_exact(sock, 8))[0]
        if length > MAX_RESPONSE_BYTES or masked:
            raise LanDiscoveryFunctionalLabError("Home Assistant WebSocket frame is unsafe")
        raw = _read_exact(sock, length)
        if opcode == 8:
            raise LanDiscoveryFunctionalLabError("Home Assistant WebSocket closed")
        if opcode == 9:
            _ws_send_raw(sock, raw, opcode=10)
            continue
        if opcode in {1, 2}:
            message_opcode = opcode
            fragments = [raw]
        elif opcode == 0 and message_opcode is not None:
            fragments.append(raw)
        else:
            raise LanDiscoveryFunctionalLabError("Home Assistant WebSocket opcode is invalid")
        if final:
            if message_opcode != 1:
                raise LanDiscoveryFunctionalLabError(
                    "Home Assistant returned binary WebSocket data"
                )
            return _strict_json(b"".join(fragments), "Home Assistant WebSocket response")


def _websocket_query(
    base_url: str,
    token: str,
    messages: Sequence[Mapping[str, Any]],
) -> list[Any]:
    parsed = urlsplit(_origin(base_url))
    key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
    expected_accept = base64.b64encode(
        hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
    ).decode("ascii")
    sock = socket.create_connection((parsed.hostname or "127.0.0.1", parsed.port or 80), 30)
    sock.settimeout(HTTP_TIMEOUT_SECONDS)
    try:
        request = (
            "GET /api/websocket HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{parsed.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).encode("ascii")
        sock.sendall(request)
        response = b""
        while b"\r\n\r\n" not in response and len(response) <= 16 * 1024:
            response += _read_exact(sock, 1)
        head, separator, remainder = response.partition(b"\r\n\r\n")
        if not separator or remainder or not head.startswith(b"HTTP/1.1 101 "):
            raise LanDiscoveryFunctionalLabError("Home Assistant WebSocket upgrade failed")
        headers: dict[str, str] = {}
        for line in head.split(b"\r\n")[1:]:
            name, colon, value = line.partition(b":")
            if not colon:
                raise LanDiscoveryFunctionalLabError("Home Assistant WebSocket headers are invalid")
            headers[name.decode("ascii").casefold()] = value.decode("ascii").strip()
        if headers.get("sec-websocket-accept") != expected_accept:
            raise LanDiscoveryFunctionalLabError("Home Assistant WebSocket identity failed")
        required = _ws_receive(sock)
        if (
            not isinstance(required, dict)
            or set(required) != {"type", "ha_version"}
            or required.get("type") != "auth_required"
            or not isinstance(required.get("ha_version"), str)
        ):
            raise LanDiscoveryFunctionalLabError("Home Assistant WebSocket auth flow is invalid")
        _ws_send(sock, {"type": "auth", "access_token": token})
        authenticated = _ws_receive(sock)
        if not isinstance(authenticated, dict) or authenticated.get("type") != "auth_ok":
            raise LanDiscoveryFunctionalLabError("Home Assistant WebSocket authentication failed")
        results: list[Any] = []
        for index, message in enumerate(messages, start=1):
            _ws_send(sock, {"id": index, **message})
            response_value = _ws_receive(sock)
            if (
                not isinstance(response_value, dict)
                or response_value.get("id") != index
                or response_value.get("type") != "result"
                or response_value.get("success") is not True
                or "result" not in response_value
            ):
                raise LanDiscoveryFunctionalLabError(
                    "Home Assistant WebSocket command did not succeed"
                )
            results.append(response_value["result"])
        return results
    finally:
        sock.close()


def _ha_state(
    request: HttpRequest,
    base_url: str,
    token: str,
    entity_id: str,
) -> Mapping[str, Any]:
    value = _api_json(
        request,
        "GET",
        f"{base_url}/api/states/{entity_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    if not isinstance(value, dict) or value.get("entity_id") != entity_id:
        raise LanDiscoveryFunctionalLabError("Home Assistant entity state is invalid")
    return value


def _wait_ha_state(
    *,
    request: HttpRequest,
    base_url: str,
    token: str,
    entity_id: str,
    expected: str,
    clock: Callable[[], float],
    sleeper: Callable[[float], None],
) -> None:
    deadline = clock() + CONTROL_TIMEOUT_SECONDS
    while clock() <= deadline:
        if _ha_state(request, base_url, token, entity_id).get("state") == expected:
            return
        sleeper(CONTROL_POLL_SECONDS)
    raise LanDiscoveryFunctionalLabError("Home Assistant physical state did not converge")


def _ha_details(
    *,
    plan: Mapping[str, Any],
    token: str,
    control_entity_id: str,
    request: HttpRequest,
    websocket_query: WebSocketQuery,
    clock: Callable[[], float],
    sleeper: Callable[[float], None],
) -> dict[str, Any]:
    base_url = plan["baseUrls"]["homeAssistant"]
    entries, registry_entry = websocket_query(
        base_url,
        token,
        [
            {"type": "config_entries/get"},
            {"type": "config/entity_registry/get", "entity_id": control_entity_id},
        ],
    )
    if not isinstance(entries, list) or not isinstance(registry_entry, dict):
        raise LanDiscoveryFunctionalLabError("Home Assistant discovery registry is invalid")
    discovered: dict[str, Mapping[str, Any]] = {}
    for source in DISCOVERY_SOURCES:
        matching = sorted(
            (
                item
                for item in entries
                if isinstance(item, dict)
                and item.get("source") == source
                and item.get("state") == "loaded"
                and isinstance(item.get("entry_id"), str)
                and isinstance(item.get("domain"), str)
            ),
            key=lambda item: item["entry_id"],
        )
        if not matching:
            raise LanDiscoveryFunctionalLabError(
                f"Home Assistant has no loaded {source} discovery entry"
            )
        discovered[source] = matching[0]
    config_entry_id = registry_entry.get("config_entry_id")
    if config_entry_id is None:
        ids = registry_entry.get("config_entry_ids")
        config_entry_id = ids[0] if isinstance(ids, list) and len(ids) == 1 else None
    matching_control_entries = [
        item
        for item in entries
        if isinstance(item, dict)
        and item.get("entry_id") == config_entry_id
        and item.get("source") in DISCOVERY_SOURCES
        and item.get("state") == "loaded"
    ]
    if len(matching_control_entries) != 1:
        raise LanDiscoveryFunctionalLabError(
            "Home Assistant control entity is not bound to one loaded LAN discovery entry"
        )
    control_entry = matching_control_entries[0]
    discovered[control_entry["source"]] = control_entry
    domain = control_entity_id.split(".", 1)[0]
    initial = _ha_state(request, base_url, token, control_entity_id).get("state")
    if initial not in {"on", "off"}:
        raise LanDiscoveryFunctionalLabError("Home Assistant control entity is not reversible")
    changed = "off" if initial == "on" else "on"
    headers = {"Authorization": f"Bearer {token}"}
    control_started = False
    try:
        _api_json(
            request,
            "POST",
            f"{base_url}/api/services/{domain}/turn_{changed}",
            headers=headers,
            payload={"entity_id": control_entity_id},
        )
        control_started = True
        _wait_ha_state(
            request=request,
            base_url=base_url,
            token=token,
            entity_id=control_entity_id,
            expected=changed,
            clock=clock,
            sleeper=sleeper,
        )
    finally:
        if control_started:
            _api_json(
                request,
                "POST",
                f"{base_url}/api/services/{domain}/turn_{initial}",
                headers=headers,
                payload={"entity_id": control_entity_id},
            )
            _wait_ha_state(
                request=request,
                base_url=base_url,
                token=token,
                entity_id=control_entity_id,
                expected=initial,
                clock=clock,
                sleeper=sleeper,
            )
    plan_id = plan["planId"]
    public_entries = [
        {
            "source": source,
            "entryIdSha256": _opaque(plan_id, "ha-entry", discovered[source]["entry_id"]),
            "domainSha256": _opaque(plan_id, "ha-domain", discovered[source]["domain"]),
            "state": "loaded",
        }
        for source in DISCOVERY_SOURCES
    ]
    return {
        "discoveredEntries": public_entries,
        "control": {
            "entityIdSha256": _opaque(plan_id, "ha-entity", control_entity_id),
            "domain": domain,
            "configEntryIdSha256": _opaque(plan_id, "ha-entry", config_entry_id),
            "source": control_entry["source"],
            "initialState": initial,
            "changedState": changed,
            "restoredState": initial,
            "stateChanged": True,
            "stateRestored": True,
        },
    }


def run_home_assistant_probe(
    *,
    plan_path: Path,
    token: str,
    control_entity_id: str,
    confirmation: str,
    output: Path,
    request: HttpRequest = _http_request,
    websocket_query: WebSocketQuery = _websocket_query,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    observed_at_unix: int | None = None,
) -> dict[str, Any]:
    plan = load_plan(plan_path)
    _verify_local_tool(plan)
    if (
        output.name != HOME_ASSISTANT_NAME
        or confirmation != plan["confirmation"]
        or ENTITY_ID.fullmatch(control_entity_id) is None
        or not token
    ):
        raise LanDiscoveryFunctionalLabError(
            "Home Assistant probe output, confirmation or private input is invalid"
        )
    payload = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": PROBE_KIND,
        "planId": plan["planId"],
        "probe": "home-assistant",
        "observedAtUnix": int(time.time()) if observed_at_unix is None else observed_at_unix,
        "passed": True,
        "details": _ha_details(
            plan=plan,
            token=token,
            control_entity_id=control_entity_id,
            request=request,
            websocket_query=websocket_query,
            clock=clock,
            sleeper=sleeper,
        ),
    }
    _write_new(output, payload, mode=0o444)
    return payload


def _validate_syncthing_details(value: Any, role: str) -> dict[str, Any]:
    required = {
        "role",
        "machineIdentitySha256",
        "localDeviceSha256",
        "peerDeviceSha256",
        "configuredAddressesAreDynamic",
        "localDiscoveryHealthy",
        "peerFoundInDiscoveryCache",
        "connectionAddressMatchedDiscovery",
        "connectionType",
        "connectionIsLocal",
        "connectionAddressPrivate",
        "trafficBytes",
        "clientVersionSha256",
    }
    if (
        not isinstance(value, dict)
        or set(value) != required
        or value.get("role") != role
        or any(
            SHA256.fullmatch(str(value.get(key) or "")) is None
            for key in (
                "machineIdentitySha256",
                "localDeviceSha256",
                "peerDeviceSha256",
                "clientVersionSha256",
            )
        )
        or any(
            value.get(key) is not True
            for key in (
                "configuredAddressesAreDynamic",
                "localDiscoveryHealthy",
                "peerFoundInDiscoveryCache",
                "connectionAddressMatchedDiscovery",
                "connectionIsLocal",
                "connectionAddressPrivate",
            )
        )
        or value.get("connectionType") not in DIRECT_CONNECTION_TYPES
        or not isinstance(value.get("trafficBytes"), int)
        or isinstance(value.get("trafficBytes"), bool)
        or value["trafficBytes"] <= 0
    ):
        raise LanDiscoveryFunctionalLabError("Syncthing functional evidence is invalid")
    return value


def _validate_ha_details(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"discoveredEntries", "control"}:
        raise LanDiscoveryFunctionalLabError("Home Assistant functional evidence is invalid")
    entries = value["discoveredEntries"]
    control = value["control"]
    if (
        not isinstance(entries, list)
        or len(entries) != 2
        or [item.get("source") if isinstance(item, dict) else None for item in entries]
        != list(DISCOVERY_SOURCES)
        or any(
            not isinstance(item, dict)
            or set(item) != {"source", "entryIdSha256", "domainSha256", "state"}
            or item.get("state") != "loaded"
            or SHA256.fullmatch(str(item.get("entryIdSha256") or "")) is None
            or SHA256.fullmatch(str(item.get("domainSha256") or "")) is None
            for item in entries
        )
        or not isinstance(control, dict)
        or set(control)
        != {
            "entityIdSha256",
            "domain",
            "configEntryIdSha256",
            "source",
            "initialState",
            "changedState",
            "restoredState",
            "stateChanged",
            "stateRestored",
        }
        or SHA256.fullmatch(str(control.get("entityIdSha256") or "")) is None
        or SHA256.fullmatch(str(control.get("configEntryIdSha256") or "")) is None
        or control.get("domain") not in {"light", "switch"}
        or control.get("source") not in DISCOVERY_SOURCES
        or control.get("initialState") not in {"on", "off"}
        or control.get("changedState") != ("off" if control.get("initialState") == "on" else "on")
        or control.get("restoredState") != control.get("initialState")
        or control.get("stateChanged") is not True
        or control.get("stateRestored") is not True
        or control.get("configEntryIdSha256") not in {item["entryIdSha256"] for item in entries}
    ):
        raise LanDiscoveryFunctionalLabError("Home Assistant functional evidence is invalid")
    return value


def _validate_probe_value(value: Any, plan: Mapping[str, Any], probe: str) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "schemaVersion",
            "kind",
            "planId",
            "probe",
            "observedAtUnix",
            "passed",
            "details",
        }
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != PROBE_KIND
        or value.get("planId") != plan["planId"]
        or value.get("probe") != probe
        or not isinstance(value.get("observedAtUnix"), int)
        or isinstance(value.get("observedAtUnix"), bool)
        or value["observedAtUnix"] <= 0
        or value.get("passed") is not True
    ):
        raise LanDiscoveryFunctionalLabError("LAN functional probe contract is invalid")
    if probe.startswith("syncthing-"):
        _validate_syncthing_details(value["details"], probe.removeprefix("syncthing-"))
    else:
        _validate_ha_details(value["details"])
    return value


def _load_probe(path: Path, plan: Mapping[str, Any], probe: str) -> tuple[dict[str, Any], bytes]:
    raw, value = _read_public(path, f"LAN functional probe {probe}", mode=0o444)
    _validate_probe_value(value, plan, probe)
    return value, raw


def validate_probe_artifacts(
    plan: Mapping[str, Any],
    result: Mapping[str, Any],
    artifacts: Mapping[str, bytes],
) -> None:
    probes = {
        SYNCTHING_NAS_NAME: ("syncthing-nas", result["syncthing"]["nas"]),
        SYNCTHING_COMPANION_NAME: (
            "syncthing-companion",
            result["syncthing"]["companion"],
        ),
        HOME_ASSISTANT_NAME: ("home-assistant", result["homeAssistant"]),
    }
    if set(artifacts) != set(PROBE_NAMES):
        raise LanDiscoveryFunctionalLabError("LAN functional probe artifact set is incomplete")
    for name, (probe, expected_details) in probes.items():
        raw = artifacts[name]
        record = result["probeArtifacts"][name]
        if record != {"sha256": _sha256(raw), "size": len(raw)}:
            raise LanDiscoveryFunctionalLabError(
                "LAN functional result does not bind its probe artifact bytes"
            )
        value = _validate_probe_value(
            _strict_json(raw, f"LAN functional probe artifact {name}"),
            plan,
            probe,
        )
        if value["details"] != expected_details:
            raise LanDiscoveryFunctionalLabError(
                "LAN functional probe details differ from the combined result"
            )
    _validate_probe_times(
        {
            name: _strict_json(raw, f"LAN functional probe time {name}")
            for name, raw in artifacts.items()
        },
        result["completedAtUnix"],
    )


def _validate_probe_times(
    probes: Mapping[str, Mapping[str, Any]],
    completed_at_unix: int,
) -> None:
    if set(probes) != set(PROBE_NAMES):
        raise LanDiscoveryFunctionalLabError("LAN functional probe time set is incomplete")
    observed_values = [probe.get("observedAtUnix") for probe in probes.values()]
    if (
        not isinstance(completed_at_unix, int)
        or isinstance(completed_at_unix, bool)
        or completed_at_unix <= 0
        or any(
            not isinstance(value, int) or isinstance(value, bool) or value <= 0
            for value in observed_values
        )
    ):
        raise LanDiscoveryFunctionalLabError(
            "LAN functional probes are stale, future-dated or from different lab windows"
        )
    observed = [int(value) for value in observed_values]
    if (
        any(
            value > completed_at_unix + PROBE_FUTURE_SKEW_SECONDS
            or completed_at_unix - value > PROBE_MAX_AGE_SECONDS
            for value in observed
        )
        or max(observed) - min(observed) > PROBE_MAX_SKEW_SECONDS
    ):
        raise LanDiscoveryFunctionalLabError(
            "LAN functional probes are stale, future-dated or from different lab windows"
        )


def _validate_cross_device(nas: Mapping[str, Any], companion: Mapping[str, Any]) -> None:
    if (
        nas["machineIdentitySha256"] == companion["machineIdentitySha256"]
        or nas["localDeviceSha256"] != companion["peerDeviceSha256"]
        or companion["localDeviceSha256"] != nas["peerDeviceSha256"]
        or nas["localDeviceSha256"] == companion["localDeviceSha256"]
    ):
        raise LanDiscoveryFunctionalLabError(
            "Syncthing probes do not prove two distinct mutually discovered physical devices"
        )


def _validate_result_value(
    plan: Mapping[str, Any], value: Any, *, now: int | None = None
) -> dict[str, Any]:
    required = {
        "schemaVersion",
        "kind",
        "planId",
        "releaseCandidate",
        "operationsBundle",
        "catalogDigest",
        "architecture",
        "syncthing",
        "homeAssistant",
        "probeArtifacts",
        "checks",
        "allPassed",
        "completedAtUnix",
        "resultId",
    }
    unsigned = dict(value) if isinstance(value, dict) else {}
    result_id = unsigned.pop("resultId", None)
    syncthing = value.get("syncthing") if isinstance(value, dict) else None
    checks = value.get("checks") if isinstance(value, dict) else None
    artifacts = value.get("probeArtifacts") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or set(value) != required
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != RESULT_KIND
        or value.get("planId") != plan["planId"]
        or value.get("releaseCandidate") != plan["releaseCandidate"]
        or value.get("operationsBundle") != plan["operationsBundle"]
        or value.get("catalogDigest") != plan["catalog"]["digest"]
        or value.get("architecture") != plan["catalog"]["architecture"]
        or not isinstance(syncthing, dict)
        or set(syncthing) != set(SYNCTHING_ROLES)
        or not isinstance(artifacts, dict)
        or set(artifacts) != set(PROBE_NAMES)
        or any(
            not isinstance(record, dict)
            or set(record) != {"sha256", "size"}
            or SHA256.fullmatch(str(record.get("sha256") or "")) is None
            or not isinstance(record.get("size"), int)
            or isinstance(record.get("size"), bool)
            or record["size"] <= 0
            for record in artifacts.values()
        )
        or checks
        != {
            "syncthingLanDiscoveryVerified": True,
            "syncthingDirectLanConnectionVerified": True,
            "homeAssistantZeroconfDiscoveryVerified": True,
            "homeAssistantSsdpDiscoveryVerified": True,
            "homeAssistantReversibleControlVerified": True,
        }
        or value.get("allPassed") is not True
        or not isinstance(value.get("completedAtUnix"), int)
        or isinstance(value.get("completedAtUnix"), bool)
        or not 0 < value["completedAtUnix"] <= (int(time.time()) if now is None else now) + 300
        or result_id != _sha256(_canonical(unsigned))
    ):
        raise LanDiscoveryFunctionalLabError("LAN functional result identity is invalid")
    nas = _validate_syncthing_details(syncthing["nas"], "nas")
    companion = _validate_syncthing_details(syncthing["companion"], "companion")
    _validate_cross_device(nas, companion)
    _validate_ha_details(value["homeAssistant"])
    return value


def verify_evidence(
    *,
    plan_path: Path,
    syncthing_nas_path: Path,
    syncthing_companion_path: Path,
    home_assistant_path: Path,
    output: Path,
    now: int | None = None,
) -> dict[str, Any]:
    plan = load_plan(plan_path)
    _verify_local_tool(plan)
    if output.name != RESULT_NAME:
        raise LanDiscoveryFunctionalLabError(f"LAN functional result must use {RESULT_NAME}")
    probes: dict[str, tuple[dict[str, Any], bytes]] = {
        SYNCTHING_NAS_NAME: _load_probe(syncthing_nas_path, plan, "syncthing-nas"),
        SYNCTHING_COMPANION_NAME: _load_probe(
            syncthing_companion_path, plan, "syncthing-companion"
        ),
        HOME_ASSISTANT_NAME: _load_probe(home_assistant_path, plan, "home-assistant"),
    }
    nas = probes[SYNCTHING_NAS_NAME][0]["details"]
    companion = probes[SYNCTHING_COMPANION_NAME][0]["details"]
    _validate_cross_device(nas, companion)
    completed = int(time.time()) if now is None else now
    _validate_probe_times(
        {name: probe for name, (probe, _raw) in probes.items()},
        completed,
    )
    value: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": RESULT_KIND,
        "planId": plan["planId"],
        "releaseCandidate": plan["releaseCandidate"],
        "operationsBundle": plan["operationsBundle"],
        "catalogDigest": plan["catalog"]["digest"],
        "architecture": plan["catalog"]["architecture"],
        "syncthing": {"nas": nas, "companion": companion},
        "homeAssistant": probes[HOME_ASSISTANT_NAME][0]["details"],
        "probeArtifacts": {
            name: {"sha256": _sha256(raw), "size": len(raw)}
            for name, (_probe, raw) in probes.items()
        },
        "checks": {
            "syncthingLanDiscoveryVerified": True,
            "syncthingDirectLanConnectionVerified": True,
            "homeAssistantZeroconfDiscoveryVerified": True,
            "homeAssistantSsdpDiscoveryVerified": True,
            "homeAssistantReversibleControlVerified": True,
        },
        "allPassed": True,
        "completedAtUnix": completed,
    }
    value["resultId"] = _sha256(_canonical(value))
    _validate_result_value(plan, value, now=completed)
    _write_new(output, value, mode=0o444)
    return value


def validate_evidence_bytes(
    plan_raw: bytes,
    result_raw: bytes,
    *,
    expected_candidate: Mapping[str, Any] | None = None,
    now: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    plan = _validate_plan_value(_strict_json(plan_raw, "LAN functional plan"))
    result = _validate_result_value(
        plan,
        _strict_json(result_raw, "LAN functional result"),
        now=now,
    )
    if expected_candidate is not None:
        shared = (
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
            or plan["releaseCandidate"].get(field) != expected_candidate[field]
            for field in shared
        ):
            raise LanDiscoveryFunctionalLabError(
                "LAN functional evidence belongs to another release candidate"
            )
    return plan, result


def verify_result(*, plan_path: Path, result_path: Path) -> dict[str, Any]:
    plan_raw, _plan_value = _read_public(plan_path, "LAN functional plan", mode=0o400)
    result_raw, _result_value = _read_public(result_path, "LAN functional result", mode=0o444)
    return validate_evidence_bytes(plan_raw, result_raw)[1]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("--echo-base-url", default="http://127.0.0.1:8000")
    plan.add_argument("--echo-password-env", default="ECHO_ADMIN_PASSWORD")
    plan.add_argument("--syncthing-base-url", default="http://127.0.0.1:3007")
    plan.add_argument("--home-assistant-base-url", default="http://127.0.0.1:8123")
    plan.add_argument("--candidate-index", required=True, type=Path)
    plan.add_argument("--bundle-root", required=True, type=Path)
    plan.add_argument("--output", required=True, type=Path)
    credentials = commands.add_parser("credentials")
    credentials.add_argument("--plan", required=True, type=Path)
    credentials.add_argument("--role", choices=SYNCTHING_ROLES, required=True)
    credentials.add_argument("--syncthing-username", default="admin")
    credentials.add_argument(
        "--syncthing-password-env",
        default="SYNCTHING_ADMIN_PASSWORD",
    )
    credentials.add_argument(
        "--home-assistant-token-env",
        default="HOME_ASSISTANT_TOKEN",
    )
    credentials.add_argument(
        "--control-entity-env",
        default="HOME_ASSISTANT_CONTROL_ENTITY",
    )
    credentials.add_argument("--output", required=True, type=Path)
    syncthing = commands.add_parser("syncthing")
    syncthing.add_argument("--plan", required=True, type=Path)
    syncthing.add_argument("--role", choices=SYNCTHING_ROLES, required=True)
    syncthing.add_argument("--credentials", required=True, type=Path)
    syncthing.add_argument("--confirmation", required=True)
    syncthing.add_argument("--output", required=True, type=Path)
    home_assistant = commands.add_parser("home-assistant")
    home_assistant.add_argument("--plan", required=True, type=Path)
    home_assistant.add_argument("--credentials", required=True, type=Path)
    home_assistant.add_argument("--confirmation", required=True)
    home_assistant.add_argument("--output", required=True, type=Path)
    verify = commands.add_parser("verify")
    verify.add_argument("--plan", required=True, type=Path)
    verify.add_argument("--syncthing-nas", required=True, type=Path)
    verify.add_argument("--syncthing-companion", required=True, type=Path)
    verify.add_argument("--home-assistant", required=True, type=Path)
    verify.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "plan":
            if sys.platform != "linux" or os.geteuid() != 0:
                raise LanDiscoveryFunctionalLabError("LAN functional plans require Linux root")
            password = os.environ.get(args.echo_password_env, "")
            if not password:
                raise LanDiscoveryFunctionalLabError("Echo administrator password is unavailable")
            echo_base_url = hub_lab._origin(args.echo_base_url)
            token = hub_lab._login(echo_base_url, password, hub_lab._http_request)
            catalog = hub_lab._request(
                hub_lab._http_request,
                "GET",
                f"{echo_base_url}/api/appliance/hub/catalog",
                token=token,
            )
            result = build_plan(
                syncthing_base_url=args.syncthing_base_url,
                home_assistant_base_url=args.home_assistant_base_url,
                catalog=catalog,
                candidate_index=args.candidate_index,
                bundle_root=args.bundle_root,
                output=args.output,
            )
        elif args.command == "credentials":
            result = create_private_credentials(
                plan_path=args.plan,
                role=args.role,
                syncthing_username=args.syncthing_username,
                syncthing_password=os.environ.get(args.syncthing_password_env, ""),
                home_assistant_token=(
                    os.environ.get(args.home_assistant_token_env, "") if args.role == "nas" else ""
                ),
                control_entity_id=(
                    os.environ.get(args.control_entity_env, "") if args.role == "nas" else ""
                ),
                output=args.output,
            )
        elif args.command == "syncthing":
            plan = load_plan(args.plan)
            credentials = _private_credentials(
                args.credentials,
                plan,
                args.role,
                plan_path=args.plan,
            )
            result = run_syncthing_probe(
                plan_path=args.plan,
                role=args.role,
                username=credentials["syncthingUsername"],
                password=credentials["syncthingPassword"],
                confirmation=args.confirmation,
                output=args.output,
            )
        elif args.command == "home-assistant":
            if sys.platform != "linux" or os.geteuid() != 0:
                raise LanDiscoveryFunctionalLabError(
                    "Home Assistant physical control probe requires Linux root on the NAS"
                )
            plan = load_plan(args.plan)
            credentials = _private_credentials(
                args.credentials,
                plan,
                "nas",
                plan_path=args.plan,
            )
            result = run_home_assistant_probe(
                plan_path=args.plan,
                token=credentials["homeAssistantToken"],
                control_entity_id=credentials["controlEntityId"],
                confirmation=args.confirmation,
                output=args.output,
            )
        else:
            result = verify_evidence(
                plan_path=args.plan,
                syncthing_nas_path=args.syncthing_nas,
                syncthing_companion_path=args.syncthing_companion,
                home_assistant_path=args.home_assistant,
                output=args.output,
            )
    except (
        OSError,
        KeyError,
        TypeError,
        ValueError,
        hub_lab.HubLifecycleLabError,
        LanDiscoveryFunctionalLabError,
    ) as exc:
        print(f"LAN_DISCOVERY_FUNCTIONAL_LAB_ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"LAN_DISCOVERY_FUNCTIONAL_LAB_OK kind={result['kind']} plan={result['planId']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
