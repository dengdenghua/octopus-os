#!/usr/bin/env python3
"""Verify a running Echo appliance Compose stack against its security contract."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import platform
import re
import secrets
import shlex
import socket
import stat
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import SplitResult, urlencode, urlsplit, urlunsplit

_CONTAINER_ID = re.compile(r"^[0-9a-f]{64}$")
_OMV_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_MAX_OMV_HOST_FILE_BYTES = 4 * 1024 * 1024
_MAX_OS_RELEASE_BYTES = 64 * 1024
_OMV_NATIVE_UNIT = Path("/usr/lib/systemd/system/echo-omv-bridge.service")
_OMV_MANAGED_UNIT = Path("/etc/systemd/system/echo-omv-bridge.service")
_NAS_TRANSFER_CHUNK_BYTES = 8 * 1024 * 1024
_MIN_NAS_TRANSFER_TEST_BYTES = 16 * 1024 * 1024
_MAX_NAS_TRANSFER_TEST_BYTES = 10 * 1024**3
_NAS_TRANSFER_PATTERN = hashlib.sha256(b"Echo OS NAS transfer verification v1").digest()
_MAX_FAMILY_FIXTURE_BYTES = 32 * 1024


class VerificationError(RuntimeError):
    pass


def _http(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    raw_body: bytes | None = None,
    token: str | None = None,
    extra_headers: dict[str, str] | None = None,
    timeout: float = 5,
) -> tuple[int, bytes, dict[str, str]]:
    if payload is not None and raw_body is not None:
        raise VerificationError("HTTP probe cannot send JSON and raw bytes together")
    body = json.dumps(payload).encode() if payload is not None else raw_body
    headers = {"Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if extra_headers:
        headers.update(extra_headers)
    parsed = _validated_http_url(url)
    connection_type = (
        http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    )
    connection = connection_type(parsed.hostname, parsed.port, timeout=timeout)
    target = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    try:
        connection.request(method, target, body=body, headers=headers)
        response = connection.getresponse()
        return response.status, response.read(), dict(response.getheaders())
    finally:
        connection.close()


def _validated_http_url(url: str) -> SplitResult:
    parsed = urlsplit(url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise VerificationError(f"invalid HTTP verification URL: {url}") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or port is None
        and ":" in parsed.netloc.rsplit("]", 1)[-1]
    ):
        raise VerificationError(f"invalid HTTP verification URL: {url}")
    return parsed


def _json_body(body: bytes, context: str) -> Any:
    try:
        return json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError(f"{context} did not return JSON") from exc


def _unix_http_status(socket_path: str, method: str, target: str) -> int:
    if (
        method not in {"GET", "POST"}
        or not os.path.isabs(socket_path)
        or not target.startswith("/")
        or "\r" in target
        or "\n" in target
    ):
        raise VerificationError("invalid Unix socket verification target")
    request = (
        f"{method} {target} HTTP/1.1\r\n"
        "Host: echo-omv\r\n"
        "Connection: close\r\n"
        "Content-Length: 0\r\n\r\n"
    ).encode("ascii")
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(5)
    try:
        client.connect(socket_path)
        client.sendall(request)
        response = client.recv(4096)
    finally:
        client.close()
    try:
        status_line = response.split(b"\r\n", 1)[0].decode("ascii")
        return int(status_line.split(" ", 2)[1])
    except (UnicodeDecodeError, IndexError, ValueError) as exc:
        raise VerificationError("OMV Unix bridge returned an invalid HTTP response") from exc


def _assert_no_omv_secrets(value: Any, *, path: str = "response") -> None:
    forbidden_keys = (
        "serial",
        "password",
        "sshpubkey",
        "absdirpath",
        "extraoption",
        "authorization",
        "cookie",
        "secret",
        "wwn",
    )
    if isinstance(value, dict):
        for key, item in value.items():
            label = str(key)
            if any(marker in label.casefold() for marker in forbidden_keys):
                raise VerificationError(f"OMV response exposed forbidden field at {path}.{label}")
            _assert_no_omv_secrets(item, path=f"{path}.{label}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _assert_no_omv_secrets(item, path=f"{path}[{index}]")
        return
    if isinstance(value, str) and "/dev/disk/by-id/" in value:
        raise VerificationError(f"OMV response exposed a by-id path at {path}")


def _read_managed_host_file(path: Path, *, expected_uid: int) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise VerificationError(f"managed OMV host file is unavailable: {path}") from exc
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != expected_uid
            or stat.S_IMODE(info.st_mode) != 0o644
            or not 0 <= info.st_size <= _MAX_OMV_HOST_FILE_BYTES
        ):
            raise VerificationError(f"managed OMV host file is unsafe: {path}")
        data = os.read(descriptor, _MAX_OMV_HOST_FILE_BYTES + 1)
        if len(data) > _MAX_OMV_HOST_FILE_BYTES:
            raise VerificationError(f"managed OMV host file is oversized: {path}")
        return data
    finally:
        os.close(descriptor)


def _assert_omv_supported_host(
    *,
    os_release_path: Path = Path("/usr/lib/os-release"),
    dpkg_query_path: Path = Path("/usr/bin/dpkg-query"),
    expected_uid: int = 0,
    command_runner: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
) -> dict[str, Any]:
    if not os_release_path.is_absolute() or os_release_path.is_symlink():
        raise VerificationError("OMV host os-release path is not one trusted file")
    release_payload = _read_managed_host_file(os_release_path, expected_uid=expected_uid)
    if len(release_payload) > _MAX_OS_RELEASE_BYTES:
        raise VerificationError("OMV host os-release is oversized")
    try:
        release_text = release_payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise VerificationError("OMV host os-release is not UTF-8") from exc
    release: dict[str, str] = {}
    for raw_line in release_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise VerificationError("OMV host os-release contains an invalid line")
        key, encoded = line.split("=", 1)
        if re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", key) is None or key in release:
            raise VerificationError("OMV host os-release contains an invalid or duplicate key")
        try:
            decoded = shlex.split(encoded, comments=False, posix=True)
        except ValueError as exc:
            raise VerificationError("OMV host os-release contains an invalid value") from exc
        if len(decoded) != 1 or len(decoded[0]) > 255:
            raise VerificationError("OMV host os-release contains an invalid value")
        release[key] = decoded[0]
    if release.get("ID", "").casefold() != "debian" or release.get("VERSION_ID") != "13":
        raise VerificationError("OMV host is outside the Debian 13 support matrix")

    try:
        resolved_query = dpkg_query_path.resolve(strict=True)
        query_info = resolved_query.stat()
    except OSError as exc:
        raise VerificationError("trusted dpkg-query is unavailable") from exc
    if (
        not dpkg_query_path.is_absolute()
        or not stat.S_ISREG(query_info.st_mode)
        or query_info.st_uid != expected_uid
        or stat.S_IMODE(query_info.st_mode) & 0o022
        or not os.access(resolved_query, os.X_OK)
    ):
        raise VerificationError("dpkg-query is not a trusted host executable")
    runner = command_runner or (
        lambda command: subprocess.run(  # nosec B603
            command,
            check=False,
            text=True,
            capture_output=True,
            timeout=15,
        )
    )
    result = runner(
        [
            str(dpkg_query_path),
            "-W",
            "-f=${Version}",
            "openmediavault",
        ]
    )
    if result.returncode != 0 or not isinstance(result.stdout, str):
        raise VerificationError("installed openmediavault package could not be queried")
    omv_version = result.stdout.strip()
    match = re.fullmatch(
        r"(?:[0-9]+:)?([0-9]+)(?:[.+~:-][0-9A-Za-z.+~:-]+)?",
        omv_version,
    )
    if len(omv_version) > 128 or match is None:
        raise VerificationError("installed openmediavault package version is invalid")
    omv_major = int(match.group(1))
    if omv_major != 8:
        raise VerificationError("OMV host is outside the OMV 8 support matrix")
    return {
        "distribution": "debian",
        "distribution_version": "13",
        "omv_version": omv_version,
        "omv_major": omv_major,
        "support_matrix": "debian-13+omv-8",
    }


def _resolve_omv_unit_path(value: str) -> tuple[str, str]:
    normalized = str(value or "").strip()
    if normalized != "auto":
        path = Path(normalized)
        if not path.is_absolute():
            raise VerificationError("OMV unit path must be absolute or auto")
        return str(path), "explicit"
    candidates = [
        (path, mode)
        for path, mode in (
            (_OMV_NATIVE_UNIT, "nativePluginPackage"),
            (_OMV_MANAGED_UNIT, "managedHostBundle"),
        )
        if path.exists() or path.is_symlink()
    ]
    if len(candidates) != 1:
        raise VerificationError(
            "OMV unit auto-detection requires exactly one native-package or managed unit"
        )
    path, mode = candidates[0]
    return str(path), mode


def _assert_omv_host_install(
    unit_path: str,
    code_root: str,
    *,
    expected_uid: int = 0,
    supported_host_check: Callable[[], dict[str, Any]] = _assert_omv_supported_host,
) -> dict[str, Any]:
    unit = Path(unit_path)
    root = Path(code_root)
    if not unit.is_absolute() or not root.is_absolute():
        raise VerificationError("OMV host install paths must be absolute")
    for directory in (root, root / "appliance"):
        try:
            info = directory.lstat()
        except OSError as exc:
            raise VerificationError(
                f"managed OMV host directory is unavailable: {directory}"
            ) from exc
        if (
            not stat.S_ISDIR(info.st_mode)
            or directory.is_symlink()
            or info.st_uid != expected_uid
            or stat.S_IMODE(info.st_mode) != 0o755
        ):
            raise VerificationError(f"managed OMV host directory is unsafe: {directory}")
    try:
        unit_text = _read_managed_host_file(unit, expected_uid=expected_uid).decode(
            "utf-8", errors="strict"
        )
    except UnicodeDecodeError as exc:
        raise VerificationError("managed OMV systemd unit is not UTF-8") from exc
    expected_root = str(root)
    if (
        f"WorkingDirectory={expected_root}" not in unit_text
        or f"Environment=PYTHONPATH={expected_root}" not in unit_text
        or "/opt/echo-os" in unit_text
    ):
        raise VerificationError("OMV systemd unit does not use the managed root-only code path")
    code_files = (root / "appliance" / "__init__.py", root / "appliance" / "omv_bridge.py")
    for code_file in code_files:
        _read_managed_host_file(code_file, expected_uid=expected_uid)
    host_platform = supported_host_check()
    return {
        "unit_path": str(unit),
        "code_root": expected_root,
        "root_owned": expected_uid == 0,
        "repository_executed_as_root": False,
        **host_platform,
    }


def _response_header(headers: dict[str, str], name: str) -> str:
    return str(
        next(
            (
                value
                for header_name, value in headers.items()
                if header_name.casefold() == name.casefold()
            ),
            "",
        )
    )


def _assert_session_cookie(headers: dict[str, str], *, require_secure: bool) -> str:
    cookie = _response_header(headers, "set-cookie")
    raw_parts = [part.strip() for part in cookie.split(";") if part.strip()]
    cookie_pair = raw_parts[0] if raw_parts else ""
    attributes = {part.casefold() for part in raw_parts[1:]}
    required_attributes = {"httponly", "samesite=lax", "path=/"}
    cookie_name = cookie_pair.partition("=")[0].casefold()
    # The current Agent and the previous appliance release use different
    # host-only cookie names. Both are supported during the bounded migration;
    # every security attribute remains mandatory.
    if cookie_name not in {
        "echo_session",
    } or not required_attributes.issubset(attributes):
        raise VerificationError("login did not set the hardened browser session cookie")
    if any(attribute.startswith("domain=") for attribute in attributes):
        raise VerificationError("session cookie must remain host-only")
    if require_secure and "secure" not in attributes:
        raise VerificationError("HTTPS login did not set a Secure session cookie")
    return cookie_pair


def _wait_for_bundle(base_url: str, wait_seconds: float) -> dict[str, Any]:
    deadline = time.monotonic() + wait_seconds
    last_error = "not attempted"
    while time.monotonic() < deadline:
        try:
            status, body, _headers = _http("GET", f"{base_url}/api/appliance/config")
            if status == 200:
                value = _json_body(body, "appliance config")
                if value.get("agent_bundle", {}).get("verified") is True:
                    return value
                last_error = "bundle is not verified"
            else:
                last_error = f"HTTP {status}"
        except (OSError, http.client.HTTPException, VerificationError) as exc:
            last_error = str(exc)
        time.sleep(1)
    raise VerificationError(f"Echo appliance did not become ready: {last_error}")


def _docker_json(*args: str) -> Any:
    result = subprocess.run(
        ["docker", *args],
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(result.stdout)


def _inspect(container: str) -> dict[str, Any]:
    value = _docker_json("inspect", container)
    if not isinstance(value, list) or len(value) != 1:
        raise VerificationError(f"unexpected docker inspect result for {container}")
    return value[0]


def _restart_main_for_nas_transfer(
    container: str,
    *,
    base_url: str,
    wait_seconds: float,
) -> None:
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", container) is None:
        raise VerificationError("NAS transfer restart container name is invalid")
    subprocess.run(
        ["docker", "restart", container],
        check=True,
        text=True,
        capture_output=True,
        timeout=max(30, int(wait_seconds)),
    )
    _wait_for_bundle(base_url, wait_seconds)


def _process_status(container: str) -> dict[str, list[str]]:
    result = subprocess.run(
        ["docker", "exec", container, "cat", "/proc/1/status"],
        check=True,
        text=True,
        capture_output=True,
    )
    fields: dict[str, list[str]] = {}
    for line in result.stdout.splitlines():
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        fields[name] = value.split()
    return fields


def _normalized_architecture(value: str) -> str:
    aliases = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }
    normalized = aliases.get(value.strip().casefold())
    if normalized is None:
        raise VerificationError(f"unsupported appliance architecture: {value!r}")
    return normalized


def _assert_runtime_architecture(container: str, expected: str | None) -> str:
    result = subprocess.run(
        ["docker", "exec", container, "uname", "-m"],
        check=True,
        text=True,
        capture_output=True,
    )
    container_arch = _normalized_architecture(result.stdout)
    host_arch = _normalized_architecture(platform.machine())
    if container_arch != host_arch:
        raise VerificationError(
            f"container architecture {container_arch} does not match host {host_arch}"
        )
    if expected is not None and container_arch != expected:
        raise VerificationError(f"appliance architecture is {container_arch}, expected {expected}")
    return container_arch


def _assert_permanent_privilege_drop(
    container: str,
    fields: dict[str, list[str]],
) -> None:
    try:
        effective_capabilities = int(fields["CapEff"][0], 16)
        no_new_privileges = fields["NoNewPrivs"]
    except (KeyError, IndexError, ValueError) as exc:
        raise VerificationError(
            f"{container} exposed an incomplete /proc/1/status privilege record"
        ) from exc
    if effective_capabilities != 0:
        raise VerificationError(
            f"{container} retained effective Linux capabilities: 0x{effective_capabilities:x}"
        )
    if no_new_privileges != ["1"]:
        raise VerificationError(
            f"{container} does not have NoNewPrivs enabled: {no_new_privileges}"
        )


def _assert_runtime_identity(container: str, uid: int, gid: int) -> None:
    fields = _process_status(container)
    try:
        observed_uids = [int(item) for item in fields["Uid"]]
        observed_gids = [int(item) for item in fields["Gid"]]
    except (KeyError, ValueError) as exc:
        raise VerificationError(f"{container} exposed invalid runtime identity fields") from exc
    if observed_uids != [uid] * 4 or observed_gids != [gid] * 4:
        raise VerificationError(
            f"{container} runs as an unexpected identity: "
            f"uids={observed_uids}, gids={observed_gids}"
        )
    _assert_permanent_privilege_drop(container, fields)


def _assert_nonroot_runtime_identity(container: str) -> None:
    fields = _process_status(container)
    try:
        observed_uids = [int(item) for item in fields["Uid"]]
        observed_gids = [int(item) for item in fields["Gid"]]
    except (KeyError, ValueError) as exc:
        raise VerificationError(f"{container} exposed invalid runtime identity fields") from exc
    if any(item == 0 for item in observed_uids) or any(item == 0 for item in observed_gids):
        raise VerificationError(
            f"{container} still runs as root: uids={observed_uids}, gids={observed_gids}"
        )
    _assert_permanent_privilege_drop(container, fields)


def _assert_state_owner(container: str, uid: int, gid: int) -> None:
    code = """
import os
from pathlib import Path
root = Path('/data')
marker = root / '.echo-runtime-owner'
assert marker.read_text() == 'EXPECTED_UID:EXPECTED_GID\\n'
paths = [root, marker, root / 'appliance-auth.json', root / 'echo-agent-config.yaml']
paths.extend(
    path
    for path in (root / 'appliance-audit.jsonl', root / 'appliance-audit.jsonl.checkpoint')
    if path.exists()
)
for path in paths:
    info = os.stat(path, follow_symlinks=False)
    assert (info.st_uid, info.st_gid) == (EXPECTED_UID, EXPECTED_GID), (path, info.st_uid, info.st_gid)
print('state ownership ok')
""".replace("EXPECTED_UID", str(uid)).replace("EXPECTED_GID", str(gid))
    subprocess.run(
        ["docker", "exec", container, "python", "-c", code],
        check=True,
        text=True,
        capture_output=True,
    )


def _assert_runtime_secret_indirection(container: str) -> None:
    code = """
import json
from pathlib import Path
import yaml

config_path = Path('/data/echo-agent-config.yaml')
store_path = Path('/data/appliance-auth.json')
raw = config_path.read_text()
config = yaml.safe_load(raw)
store = json.loads(store_path.read_text())
auth = config['local_auth']
assert auth['users']['admin'] == '$ECHO_APPLIANCE_ADMIN_PASSWORD_HASH'
assert auth['jwt_secret'] == '$ECHO_APPLIANCE_JWT_SECRET'
assert store['password_hash'] not in raw
assert store['jwt_secret'] not in raw
environment = {}
for item in Path('/proc/1/environ').read_bytes().split(b'\\0'):
    if b'=' in item:
        key, value = item.split(b'=', 1)
        environment[key.decode()] = value.decode()
assert environment['ECHO_APPLIANCE_ADMIN_PASSWORD_HASH'] == store['password_hash']
assert environment['ECHO_APPLIANCE_JWT_SECRET'] == store['jwt_secret']
print('runtime secret indirection ok')
"""
    subprocess.run(
        ["docker", "exec", container, "python", "-c", code],
        check=True,
        text=True,
        capture_output=True,
    )


def _assert_internal_proxy_policy(main_container: str, protected_id: str) -> None:
    code = f"""
import json
import urllib.error
import urllib.request

base = 'http://docker-control:2375'
checks = [
    ('GET', '/_ping', 200, None),
    ('GET', '/containers/json?all=true', 200, None),
    ('GET', '/hub/storage', 200, None),
    ('GET', '/hub/storage?path=%2F', 400, None),
    ('GET', '/info', 404, None),
    ('GET', '/images/json', 404, None),
    ('POST', '/containers/create', 404, None),
    ('DELETE', '/containers/{"a" * 12}', 405, None),
    ('GET', '/containers/json?filters=%7B%7D', 400, None),
    ('POST', '/containers/{protected_id}/stop', 403, None),
    ('POST', '/containers/{"a" * 12}/start', 400, b'unexpected'),
]
observed = []
for method, path, expected, body in checks:
    request = urllib.request.Request(base + path, data=body, method=method)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    assert status == expected, (method, path, status, expected)
    observed.append([method, path, status])
injected = json.dumps({{
    'planId': 'a' * 64,
    'catalogDigest': 'b' * 64,
    'HostConfig': {{'Privileged': True}},
}}).encode()
request = urllib.request.Request(
    base + '/hub/apps/jellyfin/install',
    data=injected,
    method='POST',
    headers={{'Content-Type': 'application/json'}},
)
try:
    with urllib.request.urlopen(request, timeout=5) as response:
        injected_status = response.status
except urllib.error.HTTPError as exc:
    injected_status = exc.code
assert injected_status == 400, injected_status
observed.append(['POST', '/hub/apps/jellyfin/install+HostConfig', injected_status])
request = urllib.request.Request(
    base + '/hub/apps/jellyfin/update',
    data=injected,
    method='POST',
    headers={{'Content-Type': 'application/json'}},
)
try:
    with urllib.request.urlopen(request, timeout=5) as response:
        update_injected_status = response.status
except urllib.error.HTTPError as exc:
    update_injected_status = exc.code
assert update_injected_status == 400, update_injected_status
observed.append([
    'POST',
    '/hub/apps/jellyfin/update+HostConfig',
    update_injected_status,
])
request = urllib.request.Request(
    base + '/hub/apps/jellyfin/uninstall',
    data=injected,
    method='POST',
    headers={{'Content-Type': 'application/json'}},
)
try:
    with urllib.request.urlopen(request, timeout=5) as response:
        uninstall_injected_status = response.status
except urllib.error.HTTPError as exc:
    uninstall_injected_status = exc.code
assert uninstall_injected_status == 400, uninstall_injected_status
observed.append([
    'POST',
    '/hub/apps/jellyfin/uninstall+HostConfig',
    uninstall_injected_status,
])
print(json.dumps(observed))
"""
    subprocess.run(
        ["docker", "exec", main_container, "python", "-c", code],
        check=True,
        text=True,
        capture_output=True,
    )


def _assert_browser_boundary(base_url: str, token: str) -> None:
    policy_status, _body, policy_headers = _http(
        "GET",
        f"{base_url}/api/appliance/config",
    )
    if policy_status != 200:
        raise VerificationError(f"browser policy probe returned HTTP {policy_status}")
    policy = _response_header(policy_headers, "content-security-policy")
    for required in (
        "default-src 'self'",
        "object-src 'none'",
        "frame-ancestors 'self'",
        "script-src 'self' 'wasm-unsafe-eval'",
        "frame-src 'self'",
    ):
        if required not in policy:
            raise VerificationError(f"browser policy is missing {required!r}: {policy}")
    frame_directive = next(
        (item for item in policy.split("; ") if item.startswith("frame-src ")),
        "",
    )
    frame_sources = frame_directive.split()[1:]
    if any(source in {"*", "http:", "https:"} for source in frame_sources):
        raise VerificationError(f"iframe policy is too broad: {frame_directive}")
    expected_headers = {
        "x-frame-options": "SAMEORIGIN",
        "x-content-type-options": "nosniff",
        "referrer-policy": "no-referrer",
    }
    for name, expected in expected_headers.items():
        actual = _response_header(policy_headers, name)
        if actual != expected:
            raise VerificationError(
                f"browser security header {name} is {actual!r}, expected {expected!r}"
            )

    cross_origin, _body, _headers = _http(
        "POST",
        f"{base_url}/api/appliance/apps/{'a' * 12}/start",
        token=token,
        extra_headers={"Origin": "https://attacker.invalid"},
    )
    if cross_origin != 403:
        raise VerificationError(f"cross-origin appliance mutation returned HTTP {cross_origin}")

    rebound, _body, _headers = _http(
        "GET",
        f"{base_url}/api/appliance/config",
        extra_headers={"Host": "rebind.attacker.invalid"},
    )
    if rebound != 400:
        raise VerificationError(f"untrusted Host probe returned HTTP {rebound}")


def _assert_photos_contract(base_url: str, token: str) -> dict[str, Any]:
    unauthenticated, _body, _headers = _http(
        "GET",
        f"{base_url}/api/appliance/photos/library?limit=1",
    )
    if unauthenticated != 401:
        raise VerificationError(f"unauthenticated photo library returned HTTP {unauthenticated}")

    library_status, library_body, _headers = _http(
        "GET",
        f"{base_url}/api/appliance/photos/library?limit=5",
        token=token,
    )
    library = _json_body(library_body, "photo library")
    if (
        library_status != 200
        or library.get("schema") != "echo.photos.library.v1"
        or not isinstance(library.get("items"), list)
        or not isinstance(library.get("total"), int)
    ):
        raise VerificationError(f"photo library contract is invalid: {library}")
    for item in library["items"]:
        path = str(item.get("path") or "") if isinstance(item, dict) else ""
        parts = path.split("/")
        if (
            not path
            or path.startswith("/")
            or ".." in parts
            or any(part == ".echo-trash" or part.startswith(".echo-") for part in parts)
        ):
            raise VerificationError("photo library exposed an unsafe or absolute path")

    status_code, status_body, _headers = _http(
        "GET",
        f"{base_url}/api/appliance/photos/status",
        token=token,
    )
    photos_status = _json_body(status_body, "photo status")
    index = photos_status.get("index", {})
    if (
        status_code != 200
        or photos_status.get("schema") != "echo.photos.status.v1"
        or not isinstance(index, dict)
        or index.get("backendAvailable") is not True
        or index.get("maxFiles") != 4000
    ):
        raise VerificationError(f"photo status contract is invalid: {photos_status}")

    plan_status, plan_body, _headers = _http(
        "POST",
        f"{base_url}/api/appliance/photos/plans/index",
        payload={"includeFaces": False},
        token=token,
    )
    plan = _json_body(plan_body, "photo index plan")
    plan_id = str(plan.get("planId") or "")
    if (
        plan_status != 200
        or plan.get("schema") != "echo.photos.index-plan.v1"
        or re.fullmatch(r"[0-9a-f]{64}", plan_id) is None
        or plan.get("approvalAction") != "photos.index.build"
        or plan.get("approvalTarget") != plan_id
        or plan.get("requiresApproval") is not True
    ):
        raise VerificationError(f"photo index plan contract is invalid: {plan}")

    traversal_status, _body, _headers = _http(
        "GET",
        f"{base_url}/api/appliance/photos/thumbnail?path=../outside.jpg&size=64",
        token=token,
    )
    if traversal_status != 400:
        raise VerificationError(f"photo thumbnail traversal returned HTTP {traversal_status}")

    thumbnail_verified = False
    if library["items"]:
        relative = str(library["items"][0]["path"])
        thumbnail_status, thumbnail, thumbnail_headers = _http(
            "GET",
            f"{base_url}/api/appliance/photos/thumbnail?"
            f"{urlencode({'path': relative, 'size': 64})}",
            token=token,
        )
        content_type = _response_header(thumbnail_headers, "content-type").split(";", 1)[0]
        if (
            thumbnail_status != 200
            or not thumbnail.startswith(b"RIFF")
            or content_type != "image/webp"
            or not _response_header(thumbnail_headers, "etag")
        ):
            raise VerificationError("photo thumbnail contract is invalid")
        thumbnail_verified = True

    return {
        "library": int(library["total"]),
        "listed": len(library["items"]),
        "thumbnailVerified": thumbnail_verified,
        "agentIndexBackend": True,
        "indexReady": plan.get("ready") is True,
        "indexPlanId": plan_id,
        "writeExecuted": False,
    }


def _assert_storage_usage_contract(base_url: str, token: str) -> dict[str, Any]:
    unauthenticated, _body, _headers = _http(
        "GET",
        f"{base_url}/api/appliance/files/usage",
    )
    if unauthenticated != 401:
        raise VerificationError(f"unauthenticated storage usage returned HTTP {unauthenticated}")

    status, body, _headers = _http(
        "GET",
        f"{base_url}/api/appliance/files/usage?fresh=true",
        token=token,
    )
    usage = _json_body(body, "storage usage")
    disk = usage.get("disk")
    library = usage.get("library")
    categories = usage.get("categories")
    top_folders = usage.get("topFolders")
    trash = usage.get("trash")
    uploads = usage.get("uploads")
    quotas = usage.get("quotas")
    if (
        status != 200
        or usage.get("schema") != "echo.storage.usage.v1"
        or usage.get("readOnly") is not True
        or not isinstance(disk, dict)
        or not isinstance(library, dict)
        or not isinstance(categories, list)
        or not isinstance(top_folders, list)
        or not isinstance(trash, dict)
        or not isinstance(uploads, dict)
        or not isinstance(quotas, list)
    ):
        raise VerificationError(f"storage usage contract is invalid: {usage}")

    def non_negative_integer(value: object) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0

    for key in (
        "totalBytes",
        "usedBytes",
        "freeBytes",
        "reserveBytes",
        "availableForUploadsBytes",
    ):
        if not non_negative_integer(disk.get(key)):
            raise VerificationError(f"storage usage disk field is invalid: {key}")
    if disk["totalBytes"] != disk["usedBytes"] + disk["freeBytes"]:
        raise VerificationError("storage usage disk totals are inconsistent")
    used_percent = disk.get("usedPercent")
    if (
        not isinstance(used_percent, (int, float))
        or isinstance(used_percent, bool)
        or not 0 <= used_percent <= 100
    ):
        raise VerificationError("storage usage percentage is invalid")

    for key in (
        "logicalBytes",
        "files",
        "directories",
        "scannedEntries",
        "maxEntries",
        "skippedLinks",
    ):
        if not non_negative_integer(library.get(key)):
            raise VerificationError(f"storage usage library field is invalid: {key}")
    if library["maxEntries"] < 1 or library["scannedEntries"] > library["maxEntries"]:
        raise VerificationError("storage usage scan bound is invalid")
    if not isinstance(library.get("truncated"), bool):
        raise VerificationError("storage usage truncation flag is invalid")

    expected_categories = {
        "photos",
        "videos",
        "audio",
        "documents",
        "archives",
        "other",
    }
    observed_categories: set[str] = set()
    classified_bytes = 0
    for category in categories:
        if not isinstance(category, dict):
            raise VerificationError("storage usage category is not an object")
        category_id = str(category.get("id") or "")
        if category_id in observed_categories or category_id not in expected_categories:
            raise VerificationError(f"storage usage category id is invalid: {category_id!r}")
        if not non_negative_integer(category.get("bytes")) or not non_negative_integer(
            category.get("files")
        ):
            raise VerificationError(f"storage usage category counts are invalid: {category_id}")
        observed_categories.add(category_id)
        classified_bytes += int(category["bytes"])
    if observed_categories != expected_categories or classified_bytes != library["logicalBytes"]:
        raise VerificationError("storage usage categories do not bind the logical byte total")

    for folder in top_folders:
        name = str(folder.get("name") or "") if isinstance(folder, dict) else ""
        if (
            not name
            or name in {".", "..", ".echo-trash"}
            or name.startswith(".echo-")
            or "/" in name
            or "\\" in name
            or not non_negative_integer(folder.get("bytes"))
            or not non_negative_integer(folder.get("files"))
        ):
            raise VerificationError("storage usage exposed an unsafe top-level folder")
    for key in ("bytes", "files"):
        if not non_negative_integer(trash.get(key)):
            raise VerificationError(f"storage usage trash field is invalid: {key}")
    for key in ("reservedBytes", "active"):
        if not non_negative_integer(uploads.get(key)):
            raise VerificationError(f"storage usage upload field is invalid: {key}")
    if any(key.casefold() in {"root", "mountpoint", "devicefile"} for key in usage):
        raise VerificationError("storage usage exposed a host filesystem field")

    return {
        "diskUsedPercent": used_percent,
        "libraryBytes": library["logicalBytes"],
        "files": library["files"],
        "scanBounded": True,
        "writeExecuted": False,
    }


def _assert_device_link_contract(base_url: str, token: str) -> dict[str, Any]:
    unauthenticated, _body, _headers = _http(
        "GET",
        f"{base_url}/api/appliance/device-link",
    )
    if unauthenticated != 401:
        raise VerificationError(f"unauthenticated device link returned HTTP {unauthenticated}")

    status, body, _headers = _http(
        "GET",
        f"{base_url}/api/appliance/device-link",
        token=token,
    )
    payload = _json_body(body, "device link")
    devices = payload.get("devices") if isinstance(payload, dict) else None
    transport = payload.get("transport") if isinstance(payload, dict) else None
    remote_access = payload.get("remoteAccess") if isinstance(payload, dict) else None
    remote_transport = remote_access.get("transport") if isinstance(remote_access, dict) else None
    remote_features = remote_access.get("features") if isinstance(remote_access, dict) else None
    if (
        status != 200
        or payload.get("schema") != "echo.device-link.v1"
        or payload.get("mode") not in {"echo-managed", "agent-shared"}
        or payload.get("scope") != "lan"
        or not isinstance(payload.get("enabled"), bool)
        or not isinstance(payload.get("listenerActive"), bool)
        or not isinstance(payload.get("canManageListener"), bool)
        or not isinstance(payload.get("canPair"), bool)
        or not isinstance(devices, list)
        or len(devices) > 64
        or not isinstance(transport, dict)
        or transport.get("protocol") != "websocket"
        or transport.get("authenticated") is not True
        or not isinstance(transport.get("encrypted"), bool)
        or not isinstance(remote_access, dict)
        or remote_access.get("schema") != "echo.remote-access.v1"
        or remote_access.get("provider") not in {"none", "tailscale"}
        or not isinstance(remote_access.get("configured"), bool)
        or not isinstance(remote_access.get("available"), bool)
        or remote_access.get("state") not in {"not-configured", "connecting", "connected"}
        or remote_access.get("scope") not in {"none", "private-network"}
        or not isinstance(remote_transport, dict)
        or not isinstance(remote_transport.get("encrypted"), bool)
        or not isinstance(remote_transport.get("tailnetOnly"), bool)
        or not isinstance(remote_features, dict)
        or any(
            not isinstance(remote_features.get(key), bool)
            for key in ("desktopWeb", "deviceLink", "fileSync", "photoSync")
        )
    ):
        raise VerificationError(f"device link contract is invalid: {payload}")
    ws_port = payload.get("wsPort")
    if not isinstance(ws_port, int) or isinstance(ws_port, bool) or not 1 <= ws_port <= 65535:
        raise VerificationError("device link WebSocket port is invalid")

    forbidden = ("token", "secret", "credential", "digest", "udid", "serial")

    def assert_public(value: Any, path: str = "deviceLink") -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                label = str(key)
                if any(marker in label.casefold() for marker in forbidden):
                    raise VerificationError(
                        f"device link exposed a credential-shaped field at {path}.{label}"
                    )
                assert_public(item, f"{path}.{label}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                assert_public(item, f"{path}[{index}]")

    assert_public(payload)
    seen: set[str] = set()
    online = 0
    for device in devices:
        if not isinstance(device, dict):
            raise VerificationError("device link returned a non-object device")
        device_id = device.get("id")
        if (
            not isinstance(device_id, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", device_id) is None
            or device_id in seen
            or not isinstance(device.get("online"), bool)
            or not isinstance(device.get("individuallyRevocable"), bool)
        ):
            raise VerificationError(f"device link device is invalid: {device}")
        seen.add(device_id)
        online += int(device["online"])
    if payload.get("pairedDeviceCount") != len(devices):
        raise VerificationError("device link paired count is inconsistent")
    if payload.get("onlineDeviceCount") != online:
        raise VerificationError("device link online count is inconsistent")
    remote_endpoint = remote_access.get("endpoint")
    endpoint_is_safe = False
    if isinstance(remote_endpoint, str):
        parsed_endpoint = urlsplit(remote_endpoint)
        endpoint_hostname = (parsed_endpoint.hostname or "").rstrip(".").casefold()
        endpoint_label = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
        endpoint_is_safe = bool(
            parsed_endpoint.scheme == "https"
            and parsed_endpoint.netloc == endpoint_hostname
            and endpoint_hostname.endswith(".ts.net")
            and endpoint_hostname != "ts.net"
            and len(endpoint_hostname) <= 253
            and all(endpoint_label.fullmatch(part) for part in endpoint_hostname.split("."))
            and parsed_endpoint.path == ""
            and not parsed_endpoint.query
            and not parsed_endpoint.fragment
        )
    if remote_access["available"]:
        if (
            remote_access["configured"] is not True
            or remote_access["state"] != "connected"
            or remote_access["scope"] != "private-network"
            or remote_transport["encrypted"] is not True
            or remote_transport["tailnetOnly"] is not True
            or remote_features["desktopWeb"] is not True
            or not endpoint_is_safe
        ):
            raise VerificationError("remote access claimed availability without a safe endpoint")
    elif remote_features["desktopWeb"]:
        raise VerificationError("remote desktop was advertised while remote access is unavailable")
    if remote_features["deviceLink"] and remote_transport["encrypted"] is not True:
        raise VerificationError("device link claimed remote access without encrypted transport")

    return {
        "mode": payload["mode"],
        "enabled": payload["enabled"],
        "listenerActive": payload["listenerActive"],
        "pairedDevices": len(devices),
        "onlineDevices": online,
        "remoteAccess": bool(remote_access["available"]),
        "remoteProvider": remote_access["provider"],
        "writeExecuted": False,
    }


def _assert_device_sync_contract(base_url: str, token: str) -> dict[str, Any]:
    unauthenticated, _body, _headers = _http("GET", f"{base_url}/api/appliance/sync")
    if unauthenticated != 401:
        raise VerificationError(f"unauthenticated device sync returned HTTP {unauthenticated}")
    status, body, _headers = _http("GET", f"{base_url}/api/appliance/sync", token=token)
    payload = _json_body(body, "device sync")
    devices = payload.get("devices") if isinstance(payload, dict) else None
    roots = payload.get("roots") if isinstance(payload, dict) else None
    if (
        status != 200
        or payload.get("schema") != "echo.device-sync.v1"
        or payload.get("mode") not in {"echo-managed", "agent-shared"}
        or not isinstance(payload.get("available"), bool)
        or payload.get("conflictPolicy") != "keep-both"
        or not isinstance(devices, list)
        or len(devices) > 64
        or not isinstance(roots, dict)
        or any(
            not isinstance(roots.get(scope), str) or not roots[scope].startswith("Mobile Uploads/")
            for scope in ("photos", "files")
        )
    ):
        raise VerificationError(f"device sync contract is invalid: {payload}")

    def assert_public(value: Any, path: str = "deviceSync") -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                label = str(key)
                if any(
                    marker in label.casefold()
                    for marker in ("credential", "authorization", "token", "secret", "digest")
                ):
                    raise VerificationError(
                        f"device sync exposed credential-shaped data at {path}.{label}"
                    )
                assert_public(item, f"{path}.{label}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                assert_public(item, f"{path}[{index}]")

    assert_public(payload)
    for device in devices:
        grants = device.get("grants") if isinstance(device, dict) else None
        summary = device.get("summary") if isinstance(device, dict) else None
        if (
            not isinstance(device, dict)
            or not isinstance(device.get("id"), str)
            or not isinstance(device.get("online"), bool)
            or not isinstance(grants, dict)
            or any(not isinstance(grants.get(scope), bool) for scope in ("photos", "files"))
            or not isinstance(summary, dict)
        ):
            raise VerificationError(f"device sync returned an invalid device: {device}")
        for scope in ("photos", "files"):
            values = summary.get(scope)
            if not isinstance(values, dict) or any(
                not isinstance(values.get(key), int)
                or isinstance(values.get(key), bool)
                or values[key] < 0
                for key in ("committed", "uploading", "conflicts", "bytes")
            ):
                raise VerificationError(
                    f"device sync returned an invalid {scope} summary: {values}"
                )
    return {
        "available": payload["available"],
        "mode": payload["mode"],
        "devices": len(devices),
        "conflictPolicy": payload["conflictPolicy"],
        "writeExecuted": False,
    }


def _assert_login_rate_limit(base_url: str) -> None:
    username = f"rate-probe-{secrets.token_hex(6)}"
    observed: list[int] = []
    retry_after = ""
    for _attempt in range(6):
        status, _body, headers = _http(
            "POST",
            f"{base_url}/api/auth/local/login",
            payload={"username": username, "password": "deliberately-wrong"},
        )
        observed.append(status)
        if status == 429:
            retry_after = _response_header(headers, "retry-after")
            break
        if status != 401:
            raise VerificationError(f"failed-login probe returned HTTP {status}: {observed}")
    if not observed or observed[-1] != 429:
        raise VerificationError(f"login brute-force limit did not engage: {observed}")
    if not retry_after.isdigit() or int(retry_after) < 1:
        raise VerificationError(f"login rate limit returned invalid Retry-After: {retry_after!r}")


def _assert_web_surfaces(base_url: str) -> None:
    desktop_status, desktop_body, _headers = _http("GET", f"{base_url}/")
    if desktop_status != 200 or b"<title>Echo OS</title>" not in desktop_body:
        raise VerificationError("canonical appliance root did not serve the Echo OS desktop")
    logo_status, logo_body, _headers = _http("GET", f"{base_url}/images/echo.svg")
    if logo_status != 200 or b"<svg" not in logo_body:
        raise VerificationError("Echo OS public assets were not served at root")
    config_status, config_body, _headers = _http("GET", f"{base_url}/api/appliance/config")
    config = _json_body(config_body, "appliance config")
    if (
        config_status != 200
        or config.get("agent_ui_base") is not None
        or config.get("agent_workspace_url") is not None
    ):
        raise VerificationError("retired Agent WebUI surface is still exposed")


def _assert_agent_assets_contract(base_url: str, token: str) -> dict[str, Any]:
    endpoint = f"{base_url}/api/appliance/agent-assets/catalog?limit=80"
    unauthenticated, _body, _headers = _http("GET", endpoint)
    if unauthenticated != 401:
        raise VerificationError(
            f"unauthenticated Agent asset catalog returned HTTP {unauthenticated}"
        )
    status, body, _headers = _http("GET", endpoint, token=token)
    payload = _json_body(body, "Agent asset catalog")
    expected_keys = {
        "schema",
        "available",
        "plugins",
        "skills",
        "installed",
        "pluginStates",
        "unavailableSources",
    }
    plugins = payload.get("plugins") if isinstance(payload, dict) else None
    skills = payload.get("skills") if isinstance(payload, dict) else None
    installed = payload.get("installed") if isinstance(payload, dict) else None
    plugin_states = payload.get("pluginStates") if isinstance(payload, dict) else None
    unavailable = payload.get("unavailableSources") if isinstance(payload, dict) else None
    if (
        status != 200
        or not isinstance(payload, dict)
        or set(payload) != expected_keys
        or payload.get("schema") != "echo.agent-assets.v6"
        or not isinstance(payload.get("available"), bool)
        or not isinstance(plugins, list)
        or len(plugins) > 80
        or not isinstance(skills, list)
        or len(skills) > 80
        or not isinstance(installed, dict)
        or set(installed) != {"plugins", "skills"}
        or not isinstance(plugin_states, list)
        or len(plugin_states) > 80
        or not isinstance(unavailable, list)
        or any(
            not isinstance(source, str) or source not in {"plugins", "skills", "plugin-statuses"}
            for source in unavailable
        )
        or len(unavailable) != len(set(unavailable))
    ):
        raise VerificationError(f"Agent asset catalog contract is invalid: {payload}")

    def public_text(value: Any, maximum: int) -> bool:
        return (
            isinstance(value, str)
            and value == value.strip()
            and 0 < len(value) <= maximum
            and not any(ord(character) < 32 or ord(character) == 127 for character in value)
        )

    plugin_fields = {
        "id": 256,
        "plugin": 256,
        "name": 256,
        "name_zh": 256,
        "description": 1_000,
        "source": 512,
        "version": 64,
        "kind": 32,
        "category": 128,
        "author": 256,
        "release_summary": 1_000,
        "host_api": 160,
    }
    requirement_fields = {
        "permissions",
        "authModes",
        "dependencies",
        "runtimeDependencies",
        "connectors",
    }
    permission_values = {
        "account.credentials",
        "content.read",
        "content.write",
        "interaction.user",
        "network.remote",
        "process.local",
    }
    auth_mode_values = {
        "connected-account",
        "mcp",
        "oauth",
        "oneid-token",
        "server-side",
        "token",
    }

    def public_list(value: Any) -> bool:
        return (
            isinstance(value, list)
            and len(value) <= 64
            and len(value) == len(set(value))
            and all(public_text(item, 160) for item in value)
        )

    skill_fields = {
        "name": 256,
        "description": 1_000,
        "source": 512,
        "author": 256,
        "version": 64,
    }
    plugin_ids: list[str] = []
    for item in plugins:
        if (
            not isinstance(item, dict)
            or not set(item) <= set(plugin_fields) | requirement_fields
            or not requirement_fields <= set(item)
            or not all(
                public_text(value, plugin_fields[key])
                for key, value in item.items()
                if key in plugin_fields
            )
            or any(not public_list(item[field]) for field in requirement_fields)
            or not set(item["permissions"]) <= permission_values
            or not set(item["authModes"]) <= auth_mode_values
        ):
            raise VerificationError(f"Agent plugin projection exposed unsafe fields: {item}")
        identity = item.get("plugin") or item.get("id")
        if not isinstance(identity, str):
            raise VerificationError(f"Agent plugin projection has no public identity: {item}")
        if "kind" in item and item["kind"] not in {"plugin", "connector", "workbench"}:
            raise VerificationError(f"Agent plugin projection has an invalid kind: {item}")
        plugin_ids.append(identity)
    skill_ids: list[str] = []
    for item in skills:
        if (
            not isinstance(item, dict)
            or not set(item) <= set(skill_fields)
            or not all(public_text(value, skill_fields[key]) for key, value in item.items())
            or not isinstance(item.get("name"), str)
        ):
            raise VerificationError(f"Agent skill projection exposed unsafe fields: {item}")
        skill_ids.append(item["name"])
    installed_plugins = installed["plugins"]
    installed_skills = installed["skills"]
    if (
        len(plugin_ids) != len(set(plugin_ids))
        or len(skill_ids) != len(set(skill_ids))
        or not isinstance(installed_plugins, list)
        or not isinstance(installed_skills, list)
        or any(not public_text(item, 256) for item in installed_plugins + installed_skills)
        or len(installed_plugins) != len(set(installed_plugins))
        or len(installed_skills) != len(set(installed_skills))
        or (
            payload["available"] is False
            and bool(plugins or skills or installed_plugins or installed_skills)
        )
    ):
        raise VerificationError("Agent asset catalog identity or availability is inconsistent")
    state_ids: list[str] = []
    update_count = 0
    attention_count = 0
    workbench_count = 0
    publisher_verified_count = 0
    unverified_installed_count = 0
    incompatible_count = 0
    required_state_fields = {
        "id",
        "catalogId",
        "kind",
        "source",
        "state",
        "installed",
        "enabled",
        "rollbackAvailable",
        "recoveryCount",
        "trustLevel",
        "integrityVerified",
        "publisherVerified",
        "compatibility",
        "permissionsGranted",
        "permissionReviewRequired",
        "permissionActive",
        *requirement_fields,
    }
    for state in plugin_states:
        if (
            not isinstance(state, dict)
            or not required_state_fields <= set(state)
            or not set(state)
            <= required_state_fields
            | {"version", "availableVersion", "publisher", "hostApi", "releaseSummary"}
            or not public_text(state.get("id"), 256)
            or state["id"] not in plugin_ids
            or not public_text(state.get("catalogId"), 256)
            or state.get("kind") not in {"plugin", "connector", "workbench"}
            or state.get("source") not in {"factory", "cloud"}
            or state.get("state")
            not in {"available", "enabled", "disabled", "update_available", "broken"}
            or not isinstance(state.get("installed"), bool)
            or not isinstance(state.get("enabled"), bool)
            or state["enabled"]
            and not state["installed"]
            or not isinstance(state.get("rollbackAvailable"), bool)
            or not isinstance(state.get("recoveryCount"), int)
            or isinstance(state.get("recoveryCount"), bool)
            or not 0 <= state["recoveryCount"] <= 1_000
            or any(
                field in state and not public_text(state[field], 64)
                for field in ("version", "availableVersion")
            )
            or state.get("trustLevel")
            not in {"system", "publisher", "local_integrity", "catalog", "unverified"}
            or not isinstance(state.get("integrityVerified"), bool)
            or not isinstance(state.get("publisherVerified"), bool)
            or state["publisherVerified"]
            and (
                not state["integrityVerified"]
                or state["trustLevel"] != "publisher"
                or not state["installed"]
            )
            or state["trustLevel"] == "publisher"
            and not state["publisherVerified"]
            or state["trustLevel"] == "local_integrity"
            and (
                not state["integrityVerified"]
                or state["publisherVerified"]
                or not state["installed"]
            )
            or state["trustLevel"] in {"catalog", "unverified"}
            and (state["integrityVerified"] or state["publisherVerified"])
            or state["trustLevel"] == "catalog"
            and state["installed"]
            or state["trustLevel"] == "unverified"
            and not state["installed"]
            or state["trustLevel"] == "system"
            and state["source"] != "factory"
            or "publisher" in state
            and (not public_text(state["publisher"], 256) or not state["publisherVerified"])
            or state.get("compatibility") not in {"compatible", "incompatible", "not_checked"}
            or state["compatibility"] == "incompatible"
            and (state["state"] != "broken" or state["enabled"])
            or "hostApi" in state
            and not public_text(state["hostApi"], 160)
            or any(not public_list(state[field]) for field in requirement_fields)
            or not set(state["permissions"]) <= permission_values
            or not public_list(state["permissionsGranted"])
            or not set(state["permissionsGranted"]) <= set(state["permissions"])
            or not isinstance(state["permissionReviewRequired"], bool)
            or not isinstance(state["permissionActive"], bool)
            or state["permissionActive"]
            and (
                not state["installed"]
                or not state["enabled"]
                or set(state["permissionsGranted"]) != set(state["permissions"])
            )
            or state["permissionReviewRequired"]
            and (
                not state["installed"]
                or set(state["permissionsGranted"]) == set(state["permissions"])
            )
            or not set(state["authModes"]) <= auth_mode_values
            or "releaseSummary" in state
            and not public_text(state["releaseSummary"], 1_000)
            or state["installed"] is not (state["id"] in installed_plugins)
            or state["state"] == "available"
            and state["installed"]
            or state["state"] in {"enabled", "update_available"}
            and (not state["installed"] or not state["enabled"])
            or state["state"] == "disabled"
            and (not state["installed"] or state["enabled"])
        ):
            raise VerificationError(f"Agent plugin lifecycle exposed unsafe fields: {state}")
        state_ids.append(state["id"])
        update_count += int(state["state"] == "update_available")
        attention_count += int(state["state"] == "broken" or state["permissionReviewRequired"])
        workbench_count += int(state["kind"] == "workbench")
        publisher_verified_count += int(state["publisherVerified"])
        unverified_installed_count += int(
            state["installed"] and state["trustLevel"] == "unverified"
        )
        incompatible_count += int(state["compatibility"] == "incompatible")
    if len(state_ids) != len(set(state_ids)):
        raise VerificationError("Agent plugin lifecycle identities are duplicated")
    return {
        "available": payload["available"],
        "plugins": len(plugins),
        "skills": len(skills),
        "installed": len(installed_plugins) + len(installed_skills),
        "workbenches": workbench_count,
        "updates": update_count,
        "attention": attention_count,
        "publisherVerified": publisher_verified_count,
        "unverifiedInstalled": unverified_installed_count,
        "incompatible": incompatible_count,
        "privateFieldsExposed": False,
    }


def _assert_agent_capabilities_contract(base_url: str, token: str) -> dict[str, Any]:
    missing_id = "echo-delivery-probe-missing"
    endpoint = f"{base_url}/api/appliance/agent-capabilities/{missing_id}"
    unauthenticated, _body, _headers = _http("GET", endpoint)
    if unauthenticated != 401:
        raise VerificationError(
            f"unauthenticated Agent capability endpoint returned HTTP {unauthenticated}"
        )

    status, body, _headers = _http("GET", endpoint, token=token)
    payload = _json_body(body, "Agent capability error")
    detail = payload.get("detail") if isinstance(payload, dict) else None
    expected_errors = {
        404: ("CAPABILITY_NOT_FOUND", "capability was not found"),
        503: (
            "AGENT_CAPABILITY_UNAVAILABLE",
            "Agent capability service is unavailable",
        ),
    }
    expected = expected_errors.get(status)
    if (
        expected is None
        or not isinstance(detail, dict)
        or set(detail) != {"code", "message"}
        or (detail.get("code"), detail.get("message")) != expected
    ):
        raise VerificationError(f"Agent capability error contract is invalid: {payload}")

    plan_id = "a" * 64
    approval_status, approval_body, _headers = _http(
        "POST",
        f"{base_url}/api/appliance/agent-capabilities/plans/authorize/apply",
        token=token,
        payload={
            "capabilityId": missing_id,
            "planId": plan_id,
            "permissions": ["account.credentials"],
            "activate": True,
        },
    )
    if approval_status != 403:
        raise VerificationError(
            f"Agent capability authorization bypassed step-up: HTTP {approval_status}"
        )
    if b"account.credentials" in approval_body or missing_id.encode() in approval_body:
        raise VerificationError("Agent capability approval failure echoed request details")

    permission_status, permission_body, _headers = _http(
        "POST",
        f"{base_url}/api/appliance/agent-capabilities/plans/authorize/apply",
        token=token,
        payload={
            "capabilityId": missing_id,
            "planId": plan_id,
            "permissions": ["private.agent.root"],
            "activate": True,
        },
    )
    if permission_status != 422 or b"private.agent.root" in permission_body:
        raise VerificationError("Agent capability permission allowlist is not enforced")

    secret_marker = "echo-delivery-secret-marker"
    credential_status, credential_body, _headers = _http(
        "POST",
        f"{base_url}/api/appliance/agent-capabilities/connect",
        token=token,
        payload={
            "capabilityId": missing_id,
            "tokens": {"API_TOKEN": secret_marker + "x" * 8_193},
        },
    )
    if credential_status != 422 or secret_marker.encode() in credential_body:
        raise VerificationError("Agent capability credential bounds are not enforced safely")

    return {
        "available": status == 404,
        "authenticationRequired": True,
        "stepUpRequired": True,
        "permissionAllowlist": True,
        "credentialBounds": True,
        "boundedErrors": True,
        "writeExecuted": False,
    }


def _assert_hub_resource_preflight(base_url: str, token: str) -> dict[str, Any]:
    endpoint = f"{base_url}/api/appliance/hub/apps/jellyfin"
    unauthenticated, _body, _headers = _http("GET", endpoint)
    if unauthenticated != 401:
        raise VerificationError(f"unauthenticated Hub detail returned HTTP {unauthenticated}")
    status, body, _headers = _http("GET", endpoint, token=token)
    payload = _json_body(body, "Hub application detail")
    expected_top = {
        "schema",
        "catalogDigest",
        "architecture",
        "runtime",
        "appRuntime",
        "diagnostics",
        "app",
        "resourcePreflight",
    }
    if (
        status != 200
        or not isinstance(payload, dict)
        or set(payload) != expected_top
        or payload.get("schema") != "echo.hub.app-detail.v1"
        or re.fullmatch(r"[0-9a-f]{64}", str(payload.get("catalogDigest") or "")) is None
    ):
        raise VerificationError(f"Hub application detail contract is invalid: {payload}")
    app = payload.get("app")
    app_runtime = payload.get("appRuntime")
    diagnostics = payload.get("diagnostics")
    preflight = payload.get("resourcePreflight")
    if (
        not isinstance(app, dict)
        or app.get("id") != "jellyfin"
        or not isinstance(app.get("package"), dict)
        or not isinstance(preflight, dict)
        or set(preflight)
        != {
            "schema",
            "readyForInstall",
            "blockingIssues",
            "checks",
            "runtime",
            "network",
            "storage",
            "notices",
        }
        or preflight.get("schema") != "echo.hub.resource-preflight.v1"
        or not isinstance(preflight.get("readyForInstall"), bool)
        or not isinstance(preflight.get("blockingIssues"), list)
        or len(preflight["blockingIssues"]) > 16
        or not isinstance(preflight.get("notices"), list)
        or len(preflight["notices"]) > 8
    ):
        raise VerificationError(f"Hub resource preflight envelope is invalid: {preflight}")
    runtime_summary_fields = {
        "serviceCount",
        "runningServices",
        "healthyServices",
        "restartCount",
        "cpuPercent",
        "memoryUsageBytes",
        "memoryLimitBytes",
        "pids",
    }
    runtime_service_fields = {
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
    runtime_summary = app_runtime.get("summary") if isinstance(app_runtime, dict) else None
    runtime_services = app_runtime.get("services") if isinstance(app_runtime, dict) else None
    if (
        not isinstance(app_runtime, dict)
        or set(app_runtime) != {"schema", "status", "summary", "services"}
        or app_runtime.get("schema") != "echo.hub.runtime.v1"
        or app_runtime.get("status")
        not in {"healthy", "degraded", "starting", "stopped", "not-installed", "unavailable"}
        or not isinstance(runtime_summary, dict)
        or set(runtime_summary) != runtime_summary_fields
        or not isinstance(runtime_services, list)
        or len(runtime_services) > 64
        or runtime_summary.get("serviceCount") != len(runtime_services)
        or not isinstance(runtime_summary.get("runningServices"), int)
        or not isinstance(runtime_summary.get("healthyServices"), int)
        or not isinstance(runtime_summary.get("restartCount"), int)
        or any(
            not isinstance(service, dict)
            or set(service) != runtime_service_fields
            or re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", str(service.get("id") or "")) is None
            or service.get("role") not in {"app", "database", "cache", "worker"}
            or not isinstance(service.get("public"), bool)
            or service.get("state")
            not in {
                "created",
                "running",
                "paused",
                "restarting",
                "removing",
                "exited",
                "dead",
                "unknown",
            }
            or service.get("health")
            not in {"healthy", "unhealthy", "starting", "not-configured", "unknown"}
            or not isinstance(service.get("restartCount"), int)
            or not isinstance(service.get("oomKilled"), bool)
            for service in runtime_services
        )
    ):
        raise VerificationError(f"Hub runtime health contract is invalid: {app_runtime}")
    diagnostic_incidents = diagnostics.get("incidents") if isinstance(diagnostics, dict) else None
    if (
        not isinstance(diagnostics, dict)
        or set(diagnostics) != {"schema", "status", "incidents"}
        or diagnostics.get("schema") != "echo.hub.diagnostics.v1"
        or diagnostics.get("status")
        not in {"ok", "attention", "observing", "stopped", "not-installed", "unavailable"}
        or not isinstance(diagnostic_incidents, list)
        or len(diagnostic_incidents) > 64
        or any(
            not isinstance(incident, dict)
            or set(incident) != {"code", "severity", "serviceId", "recovery"}
            or incident.get("code")
            not in {
                "OOM_KILLED",
                "HEALTHCHECK_FAILED",
                "RESTART_LOOP",
                "CRASHED",
                "SERVICE_STOPPED",
                "STATE_UNAVAILABLE",
            }
            or incident.get("severity") not in {"warning", "error", "critical"}
            or re.fullmatch(
                r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*",
                str(incident.get("serviceId") or ""),
            )
            is None
            or incident.get("recovery") not in {"restart", "inspect"}
            for incident in diagnostic_incidents
        )
    ):
        raise VerificationError(f"Hub diagnostics contract is invalid: {diagnostics}")
    runtime = preflight.get("runtime")
    network = preflight.get("network")
    storage = preflight.get("storage")
    if (
        not isinstance(runtime, dict)
        or set(runtime)
        != {
            "serviceCount",
            "memoryLimitMiB",
            "pidsLimit",
            "shmLimitMiB",
            "healthcheckedServices",
        }
        or runtime.get("serviceCount") != 1
        or not isinstance(runtime.get("memoryLimitMiB"), int)
        or not 128 <= runtime["memoryLimitMiB"] <= 8192
        or not isinstance(runtime.get("pidsLimit"), int)
        or not 64 <= runtime["pidsLimit"] <= 2048
        or not isinstance(network, dict)
        or set(network) != {"mode", "ports", "requiredProviders", "providersReady"}
        or network.get("mode") != "bridge"
        or not isinstance(network.get("providersReady"), bool)
        or not isinstance(network.get("ports"), list)
        or len(network["ports"]) != 1
    ):
        raise VerificationError(f"Hub runtime/network preflight is invalid: {preflight}")
    port = network["ports"][0]
    if (
        not isinstance(port, dict)
        or set(port) != {"container", "host", "protocol", "status"}
        or port.get("container") != 8096
        or port.get("host") != 8096
        or port.get("protocol") != "tcp"
        or port.get("status") not in {"available", "owned", "conflict"}
    ):
        raise VerificationError(f"Hub port preflight is invalid: {port}")
    if not isinstance(storage, dict) or set(storage) != {
        "appDataVolumes",
        "nasVolumes",
        "nasAccess",
        "snapshotVolumes",
        "nasCapacity",
        "imageStorage",
    }:
        raise VerificationError(f"Hub storage preflight is invalid: {storage}")
    capacity = storage.get("nasCapacity")
    image_storage = storage.get("imageStorage")
    if (
        storage.get("appDataVolumes") != 2
        or storage.get("nasVolumes") != 1
        or storage.get("nasAccess") != "read-only"
        or storage.get("snapshotVolumes") != 2
        or not isinstance(capacity, dict)
        or set(capacity) != {"status", "totalBytes", "freeBytes", "usedPercent"}
        or capacity.get("status") != "observed"
        or not isinstance(capacity.get("totalBytes"), int)
        or not isinstance(capacity.get("freeBytes"), int)
        or not 0 <= capacity["freeBytes"] <= capacity["totalBytes"]
        or not isinstance(capacity.get("usedPercent"), (int, float))
        or not 0 <= capacity["usedPercent"] <= 100
    ):
        raise VerificationError(f"Hub capacity preflight is invalid: {storage}")
    architecture = str(payload.get("architecture") or "")
    expected_download_bytes = {
        "amd64": 689739878,
        "arm64": 404408173,
    }.get(architecture)
    if expected_download_bytes is None:
        raise VerificationError(f"Hub preflight architecture is unsupported: {architecture}")
    expected_required_bytes = max(
        expected_download_bytes * 3,
        expected_download_bytes + 512 * 1024 * 1024,
    )
    if not isinstance(image_storage, dict) or set(image_storage) != {
        "status",
        "downloadBytes",
        "blobCount",
        "requiredFreeBytes",
        "reservePolicy",
        "capacity",
    }:
        raise VerificationError(f"Hub image storage preflight is invalid: {image_storage}")
    docker_capacity = image_storage.get("capacity")
    if (
        image_storage.get("status") != "sufficient"
        or image_storage.get("downloadBytes") != expected_download_bytes
        or image_storage.get("blobCount") != 11
        or image_storage.get("requiredFreeBytes") != expected_required_bytes
        or image_storage.get("reservePolicy") != "compressed-times-three-or-plus-512MiB"
        or not isinstance(docker_capacity, dict)
        or set(docker_capacity) != {"schema", "status", "totalBytes", "freeBytes", "usedPercent"}
        or docker_capacity.get("schema") != "echo.hub.docker-storage.v1"
        or docker_capacity.get("status") != "observed"
        or not isinstance(docker_capacity.get("totalBytes"), int)
        or not isinstance(docker_capacity.get("freeBytes"), int)
        or not 0 <= docker_capacity["freeBytes"] <= docker_capacity["totalBytes"]
        or docker_capacity["freeBytes"] < expected_required_bytes
        or not isinstance(docker_capacity.get("usedPercent"), (int, float))
        or not 0 <= docker_capacity["usedPercent"] <= 100
    ):
        raise VerificationError(f"Hub Docker capacity preflight is invalid: {image_storage}")
    checks = preflight.get("checks")
    if (
        not isinstance(checks, list)
        or len(checks) != 6
        or any(
            not isinstance(check, dict)
            or set(check) != {"id", "status", "blocking"}
            or check.get("status")
            not in {
                "pass",
                "fail",
                "unavailable",
                "mismatch",
                "observed",
                "not-requested",
            }
            or not isinstance(check.get("blocking"), bool)
            for check in checks
        )
    ):
        raise VerificationError(f"Hub checks are invalid: {checks}")
    return {
        "appId": "jellyfin",
        "memoryLimitMiB": runtime["memoryLimitMiB"],
        "port": 8096,
        "portStatus": port["status"],
        "nasCapacityObserved": True,
        "dockerCapacityObserved": True,
        "imageDownloadBytes": expected_download_bytes,
        "imageStorageSufficient": True,
        "runtimeStatus": app_runtime["status"],
        "runtimeSecretFieldsExposed": False,
        "diagnosticsStatus": diagnostics["status"],
        "diagnosticIncidentCount": len(diagnostic_incidents),
        "diagnosticsSecretFieldsExposed": False,
    }


def _assert_hub_operations_contract(base_url: str, token: str) -> dict[str, Any]:
    endpoint = f"{base_url}/api/appliance/hub/operations?limit=20"
    unauthenticated, _body, _headers = _http("GET", endpoint)
    if unauthenticated != 401:
        raise VerificationError(f"unauthenticated Hub operations returned HTTP {unauthenticated}")
    status, body, _headers = _http("GET", endpoint, token=token)
    payload = _json_body(body, "Hub operations")
    if (
        status != 200
        or not isinstance(payload, dict)
        or set(payload) != {"schema", "operations", "total"}
        or payload.get("schema") != "echo.hub.operations.v1"
        or not isinstance(payload.get("operations"), list)
        or len(payload["operations"]) > 20
        or payload.get("total") != len(payload["operations"])
    ):
        raise VerificationError(f"Hub operation list contract is invalid: {payload}")
    expected_fields = {
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
    for operation in payload["operations"]:
        progress = operation.get("progress") if isinstance(operation, dict) else None
        if (
            not isinstance(operation, dict)
            or set(operation) != expected_fields
            or operation.get("schema") != "echo.hub.operation.v1"
            or re.fullmatch(r"[0-9a-f]{32}", str(operation.get("operationId") or "")) is None
            or operation.get("operation")
            not in {"install", "update", "uninstall", "start", "stop", "restart"}
            or operation.get("status")
            not in {"queued", "running", "succeeded", "failed", "interrupted"}
            or re.fullmatch(r"[0-9a-f]{64}", str(operation.get("planId") or "")) is None
            or re.fullmatch(r"[0-9a-f]{64}", str(operation.get("catalogDigest") or "")) is None
            or not isinstance(operation.get("credentialsAvailable"), bool)
            or not isinstance(progress, dict)
            or set(progress)
            != {
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
            or not isinstance(progress.get("step"), str)
            or not isinstance(progress.get("sequence"), int)
            or isinstance(progress.get("sequence"), bool)
            or progress["sequence"] < 0
            or (
                operation.get("result") is not None
                and not isinstance(operation.get("result"), dict)
            )
            or (
                isinstance(operation.get("result"), dict)
                and "revealedSecrets" in operation["result"]
            )
        ):
            raise VerificationError(f"Hub operation entry is unsafe: {operation}")
    return {
        "authenticated": True,
        "operations": len(payload["operations"]),
        "oneTimeCredentialsExcluded": True,
    }


def _nas_transfer_bytes(offset: int, length: int) -> bytes:
    if offset < 0 or length < 0:
        raise VerificationError("NAS transfer byte range must not be negative")
    pattern_size = len(_NAS_TRANSFER_PATTERN)
    pattern_offset = offset % pattern_size
    repeats = (pattern_offset + length + pattern_size - 1) // pattern_size
    return (_NAS_TRANSFER_PATTERN * repeats)[pattern_offset : pattern_offset + length]


def _nas_transfer_sha256(start: int, length: int) -> str:
    digest = hashlib.sha256()
    offset = start
    remaining = length
    while remaining:
        chunk_length = min(_NAS_TRANSFER_CHUNK_BYTES, remaining)
        digest.update(_nas_transfer_bytes(offset, chunk_length))
        offset += chunk_length
        remaining -= chunk_length
    return digest.hexdigest()


def _normalized_nas_test_path(value: str) -> str:
    raw = str(value or "").strip().strip("/")
    if len(raw) > 512 or "\\" in raw or "\x00" in raw or "\r" in raw or "\n" in raw:
        raise VerificationError("NAS transfer test path is invalid")
    if not raw:
        return ""
    parts = PurePosixPath(raw).parts
    if any(
        part in {"", ".", "..", ".echo-trash"} or part.startswith(".echo-upload-") for part in parts
    ):
        raise VerificationError("NAS transfer test path uses a reserved component")
    return PurePosixPath(*parts).as_posix()


def _read_family_isolation_fixture(path: str) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.is_absolute() or target.is_symlink():
        raise VerificationError("family isolation fixture must be one absolute regular file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(target, flags)
    except OSError as exc:
        raise VerificationError("family isolation fixture is unavailable") from exc
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) not in {0o400, 0o600}
            or not 1 <= info.st_size <= _MAX_FAMILY_FIXTURE_BYTES
        ):
            raise VerificationError("family isolation fixture permissions are unsafe")
        raw = os.read(descriptor, _MAX_FAMILY_FIXTURE_BYTES + 1)
    finally:
        os.close(descriptor)
    try:
        value = json.loads(raw, object_pairs_hook=lambda pairs: _strict_object(pairs))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise VerificationError("family isolation fixture is not strict JSON") from exc
    if (
        not isinstance(value, dict)
        or set(value) != {"schemaVersion", "kind", "members"}
        or value.get("schemaVersion") != 1
        or value.get("kind") != "echo.family-isolation-acceptance.v1"
        or not isinstance(value.get("members"), list)
        or len(value["members"]) != 2
    ):
        raise VerificationError("family isolation fixture contract is invalid")
    members: list[dict[str, Any]] = []
    usernames: set[str] = set()
    expected_fields = {
        "username",
        "password",
        "visibleRoots",
        "hiddenRoots",
        "readableFile",
        "deniedFile",
        "readablePhoto",
        "deniedPhoto",
    }
    for raw_member in value["members"]:
        if not isinstance(raw_member, dict) or set(raw_member) != expected_fields:
            raise VerificationError("family isolation member fixture is invalid")
        username = raw_member.get("username")
        password = raw_member.get("password")
        visible = raw_member.get("visibleRoots")
        hidden = raw_member.get("hiddenRoots")
        if (
            not isinstance(username, str)
            or re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", username) is None
            or username in usernames
            or not isinstance(password, str)
            or not 12 <= len(password.encode("utf-8")) <= 72
            or any(ord(character) < 32 for character in password)
            or not isinstance(visible, list)
            or not isinstance(hidden, list)
            or not 1 <= len(visible) <= 16
            or not 1 <= len(hidden) <= 16
            or any(not isinstance(item, str) for item in visible + hidden)
        ):
            raise VerificationError("family isolation member fixture is invalid")
        normalized_visible = [_normalized_nas_test_path(item) for item in visible]
        normalized_hidden = [_normalized_nas_test_path(item) for item in hidden]
        if (
            any("/" in item or not item for item in normalized_visible + normalized_hidden)
            or len(set(normalized_visible)) != len(normalized_visible)
            or len(set(normalized_hidden)) != len(normalized_hidden)
            or set(normalized_visible) & set(normalized_hidden)
        ):
            raise VerificationError("family isolation root fixture is invalid")
        paths = {
            field: _normalized_nas_test_path(str(raw_member[field]))
            for field in ("readableFile", "deniedFile", "readablePhoto", "deniedPhoto")
        }
        if (
            not all(paths.values())
            or paths["readableFile"].split("/", 1)[0] not in normalized_visible
            or paths["readablePhoto"].split("/", 1)[0] not in normalized_visible
            or paths["deniedFile"].split("/", 1)[0] not in normalized_hidden
            or paths["deniedPhoto"].split("/", 1)[0] not in normalized_hidden
        ):
            raise VerificationError("family isolation path fixture is inconsistent")
        usernames.add(username)
        members.append(
            {
                "username": username,
                "password": password,
                "visibleRoots": normalized_visible,
                "hiddenRoots": normalized_hidden,
                **paths,
            }
        )
    return members


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _assert_family_isolation_contract(
    base_url: str,
    *,
    fixture_path: str,
) -> dict[str, Any]:
    members = _read_family_isolation_fixture(fixture_path)
    for member in members:
        status, body, _headers = _http(
            "POST",
            f"{base_url}/api/auth/local/login",
            payload={"username": member["username"], "password": member["password"]},
        )
        login = _json_body(body, "family member login")
        token = str(login.get("access_token") or "") if isinstance(login, dict) else ""
        if (
            status != 200
            or not isinstance(login, dict)
            or not token
            or login.get("actor_id") != f"local:{member['username']}"
        ):
            raise VerificationError("family member login contract failed")

        account_status, account_body, _headers = _http(
            "GET",
            f"{base_url}/api/appliance/accounts",
            token=token,
        )
        directory = _json_body(account_body, "family member account directory")
        accounts = directory.get("accounts") if isinstance(directory, dict) else None
        if (
            account_status != 200
            or not isinstance(directory, dict)
            or directory.get("canManage") is not False
            or not isinstance(accounts, list)
            or len(accounts) != 1
            or accounts[0].get("username") != member["username"]
        ):
            raise VerificationError("family member account directory isolation failed")

        list_status, list_body, _headers = _http(
            "GET",
            f"{base_url}/api/appliance/files/list",
            token=token,
        )
        listing = _json_body(list_body, "family member file root")
        entries = listing.get("entries") if isinstance(listing, dict) else None
        names = (
            {
                item.get("name")
                for item in entries
                if isinstance(item, dict) and isinstance(item.get("name"), str)
            }
            if isinstance(entries, list)
            else set()
        )
        if (
            list_status != 200
            or not isinstance(entries, list)
            or not set(member["visibleRoots"]) <= names
            or set(member["hiddenRoots"]) & names
        ):
            raise VerificationError("family member root projection failed")

        probes = (
            ("files/download", member["readableFile"], 200),
            ("files/download", member["deniedFile"], 403),
            ("photos/original", member["readablePhoto"], 200),
            ("photos/original", member["deniedPhoto"], 403),
        )
        for endpoint, path, expected in probes:
            probe_status, _body, _headers = _http(
                "GET",
                f"{base_url}/api/appliance/{endpoint}?{urlencode({'path': path})}",
                token=token,
            )
            if probe_status != expected:
                raise VerificationError("family member path authorization probe failed")

        management_status, _body, _headers = _http(
            "POST",
            f"{base_url}/api/appliance/accounts/status/plan",
            payload={"username": member["username"], "active": False},
            token=token,
        )
        if management_status != 403:
            raise VerificationError("family member reached administrator account controls")

    identities = "\n".join(sorted(member["username"] for member in members)).encode()
    policy = json.dumps(
        [
            {key: value for key, value in member.items() if key not in {"username", "password"}}
            for member in members
        ],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return {
        "verified": True,
        "memberCount": len(members),
        "identitySetSha256": hashlib.sha256(identities).hexdigest(),
        "policySetSha256": hashlib.sha256(policy).hexdigest(),
        "accountDirectoryIsolated": True,
        "fileProjectionVerified": True,
        "photoProjectionVerified": True,
        "memberManagementRejected": True,
        "secretsReturned": False,
    }


def _http_stream_sha256(
    url: str,
    *,
    token: str,
    extra_headers: dict[str, str] | None = None,
    timeout: float = 120,
) -> tuple[int, int, str, dict[str, str]]:
    headers = {"Accept": "application/octet-stream", "Authorization": f"Bearer {token}"}
    if extra_headers:
        headers.update(extra_headers)
    parsed = _validated_http_url(url)
    connection_type = (
        http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    )
    connection = connection_type(parsed.hostname, parsed.port, timeout=timeout)
    target = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    digest = hashlib.sha256()
    read_bytes = 0
    try:
        connection.request("GET", target, body=None, headers=headers)
        response = connection.getresponse()
        while chunk := response.read(1024 * 1024):
            digest.update(chunk)
            read_bytes += len(chunk)
        return response.status, read_bytes, digest.hexdigest(), dict(response.getheaders())
    finally:
        connection.close()


def _assert_nas_large_transfer(
    base_url: str,
    *,
    token: str,
    directory: str,
    size: int,
    confirmation: str | None,
    require_write: bool,
    restart_container: str | None = None,
    restart_callback: Callable[[], None] | None = None,
) -> dict[str, Any]:
    normalized_directory = _normalized_nas_test_path(directory)
    if not _MIN_NAS_TRANSFER_TEST_BYTES <= size <= _MAX_NAS_TRANSFER_TEST_BYTES:
        raise VerificationError(
            "NAS transfer test size must be between "
            f"{_MIN_NAS_TRANSFER_TEST_BYTES} and {_MAX_NAS_TRANSFER_TEST_BYTES} bytes"
        )
    path_label = normalized_directory or "ROOT"
    if (
        restart_container is not None
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", restart_container) is None
    ):
        raise VerificationError("NAS transfer restart container name is invalid")
    if restart_callback is not None and restart_container is None:
        raise VerificationError("NAS transfer restart callback is not bound to a container")
    parsed_base = _validated_http_url(base_url)
    origin = urlunsplit((parsed_base.scheme, parsed_base.netloc, "", "", ""))
    expected_confirmation = f"VERIFY ECHO NAS TRANSFER {size} {path_label} ON {origin}"
    if restart_container:
        expected_confirmation += f" AND RESTART {restart_container}"
    if confirmation is None and not require_write:
        return {
            "writeExecuted": False,
            "size": size,
            "directory": normalized_directory,
            "chunkBytes": _NAS_TRANSFER_CHUNK_BYTES,
            "confirmationRequired": expected_confirmation,
            "cleanup": "uploaded probe will be moved to the recoverable recycle bin",
            "restartMain": restart_container,
        }
    if confirmation != expected_confirmation:
        raise VerificationError("NAS transfer write confirmation does not match size and path")
    if require_write is not True:
        raise VerificationError("NAS transfer write confirmation requires --require-nas-transfer")
    if restart_container and restart_callback is None:
        raise VerificationError("NAS transfer restart was requested without a restart callback")

    filename = f"echo-transfer-verify-{secrets.token_hex(8)}.bin"
    target = "/".join(part for part in (normalized_directory, filename) if part)
    expected_digest = _nas_transfer_sha256(0, size)
    active_sessions: set[str] = set()
    target_may_exist = False

    def request_json(
        method: str,
        endpoint: str,
        *,
        payload: dict[str, Any] | None = None,
        raw_body: bytes | None = None,
        extra_headers: dict[str, str] | None = None,
        timeout: float = 30,
        context: str,
    ) -> tuple[int, Any]:
        status, body, _headers = _http(
            method,
            f"{base_url}{endpoint}",
            payload=payload,
            raw_body=raw_body,
            token=token,
            extra_headers=extra_headers,
            timeout=timeout,
        )
        return status, _json_body(body, context)

    def create_session(name: str, expected_size: int, digest: str) -> dict[str, Any]:
        status, response = request_json(
            "POST",
            "/api/appliance/files/upload/sessions",
            payload={
                "path": normalized_directory,
                "filename": name,
                "size": expected_size,
                "sha256": digest,
                "overwrite": False,
            },
            context="NAS transfer session creation",
        )
        session_id = str(response.get("sessionId") or "") if isinstance(response, dict) else ""
        if status != 200 or not re.fullmatch(r"[0-9a-f]{32}", session_id):
            raise VerificationError(f"NAS transfer session creation failed: HTTP {status}")
        active_sessions.add(session_id)
        return response

    try:
        preflight_status, preflight = request_json(
            "POST",
            "/api/appliance/files/upload/preflight",
            payload={
                "path": normalized_directory,
                "filename": filename,
                "size": size,
                "overwrite": False,
            },
            context="NAS transfer capacity preflight",
        )
        if (
            preflight_status != 200
            or preflight.get("target") != target
            or preflight.get("expectedBytes") != size
            or int(preflight.get("availableBytes", -1)) < size
            or int(preflight.get("reserveBytes", -1)) < 0
        ):
            raise VerificationError("NAS transfer capacity preflight is invalid")

        session = create_session(filename, size, expected_digest)
        session_id = str(session["sessionId"])
        if (
            session.get("target") != target
            or session.get("expectedBytes") != size
            or session.get("uploadedBytes") != 0
            or session.get("chunkBytes") != _NAS_TRANSFER_CHUNK_BYTES
            or session.get("sha256Expected") is not True
        ):
            raise VerificationError(f"NAS transfer session contract is invalid: {session}")

        first_length = min(_NAS_TRANSFER_CHUNK_BYTES, size)
        first_chunk = _nas_transfer_bytes(0, first_length)
        first_headers = {
            "Content-Type": "application/octet-stream",
            "Upload-Offset": "0",
            "Upload-Chunk-SHA256": hashlib.sha256(first_chunk).hexdigest(),
        }
        status, first_response = request_json(
            "PUT",
            f"/api/appliance/files/upload/sessions/{session_id}/chunk",
            raw_body=first_chunk,
            extra_headers=first_headers,
            timeout=120,
            context="NAS transfer first chunk",
        )
        if status != 200 or first_response.get("uploadedBytes") != first_length:
            raise VerificationError(f"NAS transfer first chunk failed: HTTP {status}")

        conflict_status, conflict = request_json(
            "PUT",
            f"/api/appliance/files/upload/sessions/{session_id}/chunk",
            raw_body=first_chunk,
            extra_headers=first_headers,
            timeout=120,
            context="NAS transfer replay probe",
        )
        if (
            conflict_status != 409
            or conflict.get("detail", {}).get("uploadedBytes") != first_length
        ):
            raise VerificationError(
                f"NAS transfer replay did not return the committed offset: {conflict_status}"
            )

        restart_verified = False
        if restart_container is not None:
            assert restart_callback is not None
            restart_callback()
            restart_verified = True

        status, recovered = request_json(
            "GET",
            f"/api/appliance/files/upload/sessions/{session_id}",
            context="NAS transfer recovery query",
        )
        if status != 200 or recovered.get("uploadedBytes") != first_length:
            raise VerificationError("NAS transfer recovery query returned the wrong offset")

        offset = first_length
        while offset < size:
            length = min(_NAS_TRANSFER_CHUNK_BYTES, size - offset)
            chunk = _nas_transfer_bytes(offset, length)
            status, progress = request_json(
                "PUT",
                f"/api/appliance/files/upload/sessions/{session_id}/chunk",
                raw_body=chunk,
                extra_headers={
                    "Content-Type": "application/octet-stream",
                    "Upload-Offset": str(offset),
                    "Upload-Chunk-SHA256": hashlib.sha256(chunk).hexdigest(),
                },
                timeout=120,
                context="NAS transfer chunk",
            )
            offset += length
            if status != 200 or progress.get("uploadedBytes") != offset:
                raise VerificationError(f"NAS transfer chunk failed at offset {offset - length}")

        target_may_exist = True
        status, completed = request_json(
            "POST",
            f"/api/appliance/files/upload/sessions/{session_id}/complete",
            payload={},
            timeout=300,
            context="NAS transfer completion",
        )
        if (
            status != 200
            or completed.get("sha256") != expected_digest
            or completed.get("hashVerified") is not True
            or completed.get("entry", {}).get("path") != target
            or completed.get("entry", {}).get("size") != size
        ):
            raise VerificationError(f"NAS transfer completion is invalid: HTTP {status}")
        active_sessions.discard(session_id)

        download_url = f"{base_url}/api/appliance/files/download?{urlencode({'path': target})}"
        status, downloaded_bytes, downloaded_digest, headers = _http_stream_sha256(
            download_url,
            token=token,
            timeout=300,
        )
        if (
            status != 200
            or downloaded_bytes != size
            or downloaded_digest != expected_digest
            or _response_header(headers, "content-length") != str(size)
        ):
            raise VerificationError("NAS full download size, digest, or Content-Length mismatched")

        range_length = min(1024 * 1024, size)
        range_start = size - range_length
        range_digest = _nas_transfer_sha256(range_start, range_length)
        range_status, range_bytes, actual_range_digest, range_headers = _http_stream_sha256(
            download_url,
            token=token,
            extra_headers={"Range": f"bytes={range_start}-"},
            timeout=120,
        )
        expected_content_range = f"bytes {range_start}-{size - 1}/{size}"
        if (
            range_status != 206
            or range_bytes != range_length
            or actual_range_digest != range_digest
            or _response_header(range_headers, "content-range") != expected_content_range
        ):
            raise VerificationError("NAS Range download contract mismatched")

        cancel_name = f"echo-transfer-cancel-{secrets.token_hex(6)}.bin"
        cancel_payload = b"xy"
        cancel_digest = hashlib.sha256(cancel_payload).hexdigest()
        cancel_session = create_session(cancel_name, len(cancel_payload), cancel_digest)
        cancel_id = str(cancel_session["sessionId"])
        cancel_chunk = cancel_payload[:1]
        staged_status, staged = request_json(
            "PUT",
            f"/api/appliance/files/upload/sessions/{cancel_id}/chunk",
            raw_body=cancel_chunk,
            extra_headers={
                "Content-Type": "application/octet-stream",
                "Upload-Offset": "0",
                "Upload-Chunk-SHA256": hashlib.sha256(cancel_chunk).hexdigest(),
            },
            context="NAS transfer cancellation staging",
        )
        if staged_status != 200 or staged.get("uploadedBytes") != 1:
            raise VerificationError("NAS transfer cancellation staging failed")
        cancel_status, cancelled = request_json(
            "DELETE",
            f"/api/appliance/files/upload/sessions/{cancel_id}",
            context="NAS transfer cancellation",
        )
        if cancel_status != 200 or cancelled.get("cancelled") is not True:
            raise VerificationError("NAS transfer cancellation failed")
        active_sessions.discard(cancel_id)
        missing_status, _missing = request_json(
            "GET",
            f"/api/appliance/files/upload/sessions/{cancel_id}",
            context="NAS cancelled session lookup",
        )
        if missing_status != 404:
            raise VerificationError("cancelled NAS transfer session remained readable")
        list_status, directory_listing = request_json(
            "GET",
            f"/api/appliance/files/list?{urlencode({'path': normalized_directory})}",
            context="NAS cancelled target listing",
        )
        if list_status != 200 or any(
            item.get("name") == cancel_name for item in directory_listing.get("entries", [])
        ):
            raise VerificationError("cancelled NAS transfer created a target file")

        trash_status, trashed = request_json(
            "POST",
            "/api/appliance/files/trash",
            payload={"path": target},
            context="NAS transfer recoverable cleanup",
        )
        trash_id = str(trashed.get("trashed", {}).get("id") or "")
        if trash_status != 200 or not re.fullmatch(r"[0-9a-f]{32}", trash_id):
            raise VerificationError("NAS transfer probe could not be moved to the recycle bin")
        target_may_exist = False
        list_status, trash_listing = request_json(
            "GET",
            "/api/appliance/files/trash",
            context="NAS transfer recycle-bin verification",
        )
        matching = [
            item
            for item in trash_listing.get("entries", [])
            if item.get("id") == trash_id
            and item.get("original") == target
            and item.get("name") == filename
        ]
        if list_status != 200 or len(matching) != 1:
            raise VerificationError("NAS transfer probe was not recoverable from the recycle bin")

        restore_status, restored = request_json(
            "POST",
            "/api/appliance/files/trash/restore",
            payload={"id": trash_id},
            context="NAS transfer recycle-bin restoration",
        )
        restored_entry = restored.get("entry", {})
        if (
            restore_status != 200
            or restored.get("ok") is not True
            or not isinstance(restored_entry, dict)
            or restored_entry.get("path") != target
            or restored_entry.get("size") != size
        ):
            raise VerificationError("NAS transfer probe could not be restored from recycle bin")
        target_may_exist = True
        restored_status, restored_bytes, restored_digest, restored_headers = _http_stream_sha256(
            f"{base_url}/api/appliance/files/download?{urlencode({'path': target})}",
            token=token,
            timeout=300,
        )
        if (
            restored_status != 200
            or restored_bytes != size
            or restored_digest != expected_digest
            or _response_header(restored_headers, "content-length") != str(size)
        ):
            raise VerificationError("restored NAS transfer payload changed in recycle bin")
        retrash_status, retrash = request_json(
            "POST",
            "/api/appliance/files/trash",
            payload={"path": target},
            context="NAS transfer restored-probe cleanup",
        )
        final_trash_id = str(retrash.get("trashed", {}).get("id") or "")
        if retrash_status != 200 or not re.fullmatch(r"[0-9a-f]{32}", final_trash_id):
            raise VerificationError("restored NAS transfer probe could not be cleaned up safely")
        target_may_exist = False

        return {
            "writeExecuted": True,
            "size": size,
            "directory": normalized_directory,
            "chunkBytes": _NAS_TRANSFER_CHUNK_BYTES,
            "sha256": expected_digest,
            "availableBytes": preflight["availableBytes"],
            "reserveBytes": preflight["reserveBytes"],
            "offsetRecovery": first_length,
            "restartVerified": restart_verified,
            "fullDownload": downloaded_bytes,
            "rangeStart": range_start,
            "rangeBytes": range_bytes,
            "cancelVerified": True,
            "recoverableTrashId": final_trash_id,
            "recycleRestoreVerified": True,
            "restoredSha256": restored_digest,
            "physicallyDeleted": False,
        }
    except Exception as exc:
        cleanup_failures: list[str] = []
        for session_id in sorted(active_sessions):
            try:
                status, _body, _headers = _http(
                    "DELETE",
                    f"{base_url}/api/appliance/files/upload/sessions/{session_id}",
                    token=token,
                )
                if status not in {200, 404}:
                    cleanup_failures.append(f"session {session_id}: HTTP {status}")
            except Exception as cleanup_exc:
                cleanup_failures.append(f"session {session_id}: {cleanup_exc}")
        if target_may_exist:
            try:
                status, _body, _headers = _http(
                    "POST",
                    f"{base_url}/api/appliance/files/trash",
                    payload={"path": target},
                    token=token,
                )
                if status not in {200, 404}:
                    cleanup_failures.append(f"target {target}: HTTP {status}")
            except Exception as cleanup_exc:
                cleanup_failures.append(f"target {target}: {cleanup_exc}")
        if cleanup_failures:
            raise VerificationError(
                f"NAS transfer verification failed ({exc}); cleanup also failed: "
                + ", ".join(cleanup_failures)
            ) from exc
        if isinstance(exc, VerificationError):
            raise
        raise VerificationError(f"NAS transfer verification failed: {exc}") from exc


def _assert_omv_integration(
    base_url: str,
    *,
    token: str,
    main: dict[str, Any],
    proxy: dict[str, Any],
    socket_path: str,
    expected_gid: int,
) -> dict[str, Any]:
    try:
        socket_info = os.lstat(socket_path)
    except OSError as exc:
        raise VerificationError(f"OMV bridge socket is unavailable: {socket_path}") from exc
    if not stat.S_ISSOCK(socket_info.st_mode):
        raise VerificationError("OMV bridge path is not a Unix socket")
    mode = stat.S_IMODE(socket_info.st_mode)
    if mode != 0o660 or socket_info.st_gid != expected_gid:
        raise VerificationError(
            "OMV bridge socket ownership is unsafe: "
            f"mode={mode:o}, gid={socket_info.st_gid}, expected_gid={expected_gid}"
        )
    if _unix_http_status(socket_path, "GET", "/health") != 200:
        raise VerificationError("OMV Unix bridge health check failed")
    if _unix_http_status(socket_path, "POST", "/v1/sharing") != 405:
        raise VerificationError("OMV Unix bridge accepted a mutating HTTP method")

    main_mounts = main.get("Mounts") or []
    proxy_mounts = proxy.get("Mounts") or []
    bridge_mounts = [item for item in main_mounts if item.get("Destination") == "/run/echo-omv"]
    if len(bridge_mounts) != 1 or bridge_mounts[0].get("RW") is not False:
        raise VerificationError("Echo main container lacks one read-only OMV bridge mount")
    if any(item.get("Destination") == "/run/echo-omv" for item in proxy_mounts):
        raise VerificationError("docker-control unexpectedly owns the OMV bridge mount")

    unauthenticated_paths = [
        "/api/appliance/omv/status",
        "/api/appliance/omv/health",
        "/api/appliance/omv/filesystems",
        "/api/appliance/omv/smart/devices",
        "/api/appliance/omv/topology",
        "/api/appliance/omv/sharing",
    ]
    for path in unauthenticated_paths:
        status, _body, _headers = _http("GET", f"{base_url}{path}")
        if status != 401:
            raise VerificationError(f"unauthenticated OMV endpoint returned {status}: {path}")
    unauthenticated_folder_plan, _body, _headers = _http(
        "POST",
        f"{base_url}/api/appliance/omv/sharing/folders/plan",
        payload={
            "schema": "echo.omv.shared-folder-desired.v1",
            "mountPointRef": "11111111-2222-4333-8444-555555555555",
            "name": "verification",
            "comment": "verification",
        },
    )
    if unauthenticated_folder_plan != 401:
        raise VerificationError(
            f"unauthenticated OMV shared folder plan returned {unauthenticated_folder_plan}"
        )
    unauthenticated_privilege_plan, _body, _headers = _http(
        "POST",
        f"{base_url}/api/appliance/omv/sharing/privileges/plan",
        payload={
            "schema": "echo.omv.share-privilege-desired.v1",
            "sharedFolderRef": "11111111-2222-4333-8444-555555555555",
            "principalType": "user",
            "principalName": "verification",
            "permission": "read",
        },
    )
    if unauthenticated_privilege_plan != 401:
        raise VerificationError(
            f"unauthenticated OMV share privilege plan returned {unauthenticated_privilege_plan}"
        )
    unauthenticated_plan, _body, _headers = _http(
        "POST",
        f"{base_url}/api/appliance/omv/sharing/smb/plan",
        payload={
            "schema": "echo.omv.smb-share-desired.v1",
            "sharedFolderRef": "11111111-2222-4333-8444-555555555555",
            "enabled": True,
            "readOnly": True,
            "browseable": True,
            "recycleBin": True,
            "comment": "verification probe",
        },
    )
    if unauthenticated_plan != 401:
        raise VerificationError(f"unauthenticated OMV SMB plan returned {unauthenticated_plan}")
    unauthenticated_nfs, _body, _headers = _http(
        "POST",
        f"{base_url}/api/appliance/omv/sharing/nfs/plan",
        payload={
            "schema": "echo.omv.nfs-share-desired.v1",
            "sharedFolderRef": "11111111-2222-4333-8444-555555555555",
            "clientCidr": "192.168.1.0/24",
            "readOnly": True,
            "comment": "verification",
        },
    )
    if unauthenticated_nfs != 401:
        raise VerificationError(f"unauthenticated OMV NFS plan returned {unauthenticated_nfs}")
    unauthenticated_quota, _body, _headers = _http(
        "POST",
        f"{base_url}/api/appliance/omv/quota/plan",
        payload={
            "schema": "echo.omv.filesystem-quota-desired.v1",
            "filesystemUuid": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            "subjectType": "user",
            "subjectName": "verification",
            "hardLimitBytes": 1024,
        },
    )
    if unauthenticated_quota != 401:
        raise VerificationError(f"unauthenticated OMV quota plan returned {unauthenticated_quota}")

    status_code, status_body, _headers = _http(
        "GET", f"{base_url}/api/appliance/omv/status", token=token
    )
    status_payload = _json_body(status_body, "OMV status")
    if (
        status_code != 200
        or status_payload.get("configured") is not True
        or status_payload.get("available") is not True
        or status_payload.get("readOnly") is not False
        or status_payload.get("capabilities")
        != [
            "shared-folder.create.simple.v1",
            "shared-folder.privilege.simple.v1",
            "smb.share.desired.v1",
            "nfs.share.private-network.v1",
            "filesystem.quota.user-group.v1",
        ]
    ):
        raise VerificationError(f"OMV constrained bridge is not ready: {status_payload}")
    _assert_no_omv_secrets(status_payload)

    responses: dict[str, Any] = {}
    for label, path in (
        ("health", "/api/appliance/omv/health"),
        ("filesystems", "/api/appliance/omv/filesystems"),
        ("devices", "/api/appliance/omv/smart/devices"),
        ("topology", "/api/appliance/omv/topology"),
        ("sharing", "/api/appliance/omv/sharing"),
    ):
        response_status, response_body, _headers = _http("GET", f"{base_url}{path}", token=token)
        payload = _json_body(response_body, f"OMV {label}")
        if response_status != 200 or payload.get("readOnly") is not True:
            raise VerificationError(f"OMV {label} endpoint failed: {payload}")
        _assert_no_omv_secrets(payload, path=label)
        responses[label] = payload

    health = responses["health"]
    if (
        health.get("monitoring") is not True
        or health.get("persistenceHealthy") is not True
        or not isinstance(health.get("checkedAt"), str)
        or health.get("state") not in {"healthy", "warning", "critical", "unavailable"}
        or not isinstance(health.get("activeAlerts"), list)
        or not isinstance(health.get("events"), list)
    ):
        raise VerificationError(f"OMV continuous health monitor is not ready: {health}")

    folders = responses["sharing"].get("sharedFolders")
    folder_targets = responses["sharing"].get("sharedFolderTargets")
    if not isinstance(folders, list) or not isinstance(folder_targets, list):
        raise VerificationError("OMV sharing response omitted sharedFolders or sharedFolderTargets")
    if folders:
        share_uuid = str(folders[0].get("uuid") or "")
        privilege_status, privilege_body, _headers = _http(
            "GET",
            f"{base_url}/api/appliance/omv/sharing/{share_uuid}/privileges",
            token=token,
        )
        privilege_payload = _json_body(privilege_body, "OMV share privileges")
        if privilege_status != 200 or privilege_payload.get("readOnly") is not True:
            raise VerificationError(f"OMV privilege endpoint failed: {privilege_payload}")
        _assert_no_omv_secrets(privilege_payload, path="privileges")

    injected, _body, _headers = _http(
        "GET",
        f"{base_url}/api/appliance/omv/smart?devicefile=/dev/sda%3Bshutdown",
        token=token,
    )
    if injected != 422:
        raise VerificationError(f"OMV device injection probe returned HTTP {injected}")
    invalid_uuid, _body, _headers = _http(
        "GET",
        f"{base_url}/api/appliance/omv/sharing/not-a-uuid/privileges",
        token=token,
    )
    if invalid_uuid != 422:
        raise VerificationError(f"OMV UUID injection probe returned HTTP {invalid_uuid}")
    read_only, _body, _headers = _http("POST", f"{base_url}/api/appliance/omv/sharing", token=token)
    if read_only != 405:
        raise VerificationError(f"OMV Echo API accepted POST with HTTP {read_only}")
    invalid_folder_plan, _body, _headers = _http(
        "POST",
        f"{base_url}/api/appliance/omv/sharing/folders/plan",
        token=token,
        payload={
            "schema": "echo.omv.shared-folder-desired.v1",
            "mountPointRef": "11111111-2222-4333-8444-555555555555",
            "name": "verification",
            "comment": "verification probe",
            "relativePath": "../../etc",
        },
    )
    if invalid_folder_plan != 422:
        raise VerificationError(
            f"OMV shared folder path injection probe returned HTTP {invalid_folder_plan}"
        )
    invalid_privilege_plan, _body, _headers = _http(
        "POST",
        f"{base_url}/api/appliance/omv/sharing/privileges/plan",
        token=token,
        payload={
            "schema": "echo.omv.share-privilege-desired.v1",
            "sharedFolderRef": "11111111-2222-4333-8444-555555555555",
            "principalType": "user",
            "principalName": "verification",
            "permission": "read",
            "recursive": True,
        },
    )
    if invalid_privilege_plan != 422:
        raise VerificationError(
            f"OMV share privilege field injection probe returned HTTP {invalid_privilege_plan}"
        )
    invalid_plan, _body, _headers = _http(
        "POST",
        f"{base_url}/api/appliance/omv/sharing/smb/plan",
        token=token,
        payload={
            "schema": "echo.omv.smb-share-desired.v1",
            "sharedFolderRef": "11111111-2222-4333-8444-555555555555",
            "enabled": True,
            "readOnly": True,
            "browseable": True,
            "recycleBin": True,
            "comment": "verification probe",
            "guest": "only",
        },
    )
    if invalid_plan != 422:
        raise VerificationError(f"OMV SMB field injection probe returned HTTP {invalid_plan}")
    invalid_quota, _body, _headers = _http(
        "POST",
        f"{base_url}/api/appliance/omv/quota/plan",
        token=token,
        payload={
            "schema": "echo.omv.filesystem-quota-desired.v1",
            "filesystemUuid": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            "subjectType": "user",
            "subjectName": "verification",
            "hardLimitBytes": 1024,
            "sharedFolderPath": "/srv/verification",
        },
    )
    if invalid_quota != 422:
        raise VerificationError(f"OMV quota field injection probe returned HTTP {invalid_quota}")

    topology_devices = responses["topology"].get("devices")
    physical_devices = responses["devices"].get("devices")
    return {
        "available": True,
        "socket_mode": "0660",
        "socket_gid": socket_info.st_gid,
        "bridge_post": 405,
        "api_post": 405,
        "shared_folder_control": True,
        "share_privilege_control": True,
        "smb_control": True,
        "quota_control": True,
        "physical_devices": len(physical_devices) if isinstance(physical_devices, list) else 0,
        "topology_devices": len(topology_devices) if isinstance(topology_devices, list) else 0,
        "shared_folders": len(folders),
        "health_state": health["state"],
        "active_alerts": len(health["activeAlerts"]),
        "alert_history_events": len(health["events"]),
        "alert_state_persistent": True,
        "sensitive_fields_exposed": False,
    }


def _omv_sharing_snapshot(base_url: str, *, token: str) -> dict[str, Any]:
    status, body, _headers = _http(
        "GET",
        f"{base_url}/api/appliance/omv/sharing",
        token=token,
    )
    payload = _json_body(body, "OMV sharing snapshot")
    if (
        status != 200
        or not isinstance(payload, dict)
        or not isinstance(payload.get("sharedFolders"), list)
        or not isinstance(payload.get("sharedFolderTargets"), list)
        or not isinstance(payload.get("smb"), dict)
        or not isinstance(payload["smb"].get("shares"), list)
    ):
        raise VerificationError(f"OMV sharing snapshot is invalid: {payload}")
    _assert_no_omv_secrets(payload, path="sharing")
    return payload


def _simple_smb_desired(share: dict[str, Any]) -> dict[str, Any]:
    expected = {
        "sharedFolderRef": str,
        "enabled": bool,
        "readOnly": bool,
        "browseable": bool,
        "recycleBin": bool,
        "comment": str,
    }
    for key, expected_type in expected.items():
        value = share.get(key)
        if not isinstance(value, expected_type) or (
            expected_type is str and any(character < " " for character in value)
        ):
            raise VerificationError(f"OMV SMB test share has invalid field: {key}")
    folder_ref = share["sharedFolderRef"]
    if _OMV_UUID.fullmatch(folder_ref) is None or len(share["comment"]) > 512:
        raise VerificationError("OMV SMB test share identity or comment is invalid")
    if share.get("guest") != "no":
        raise VerificationError("OMV SMB write verification requires a private non-guest share")
    return {
        "schema": "echo.omv.smb-share-desired.v1",
        "sharedFolderRef": folder_ref.lower(),
        "enabled": share["enabled"],
        "readOnly": share["readOnly"],
        "browseable": share["browseable"],
        "recycleBin": share["recycleBin"],
        "comment": share["comment"],
    }


def _plan_omv_smb(
    base_url: str,
    *,
    token: str,
    desired: dict[str, Any],
) -> dict[str, Any]:
    status, body, _headers = _http(
        "POST",
        f"{base_url}/api/appliance/omv/sharing/smb/plan",
        token=token,
        payload=desired,
    )
    plan = _json_body(body, "OMV SMB plan")
    if (
        status != 200
        or not isinstance(plan, dict)
        or not re.fullmatch(r"[0-9a-f]{64}", str(plan.get("planId") or ""))
        or plan.get("desired") != desired
        or plan.get("operation") not in {"update", "none"}
        or not isinstance(plan.get("changes"), list)
    ):
        raise VerificationError(f"OMV SMB plan is invalid: HTTP {status}, {plan}")
    return plan


def _issue_omv_smb_approval(
    base_url: str,
    *,
    token: str,
    password: str,
    plan_id: str,
    intent_id: str,
) -> str:
    status, body, _headers = _http(
        "POST",
        f"{base_url}/api/appliance/approvals",
        token=token,
        payload={
            "action": "omv.smb.apply",
            "target": plan_id,
            "password": password,
            "intentId": intent_id,
        },
    )
    response = _json_body(body, "OMV SMB approval")
    approval_token = str(response.get("approvalToken") or "") if isinstance(response, dict) else ""
    if status != 200 or not approval_token or response.get("target") != plan_id:
        raise VerificationError(f"OMV SMB approval was not issued: HTTP {status}")
    return approval_token


def _apply_omv_smb(
    base_url: str,
    *,
    token: str,
    password: str,
    desired: dict[str, Any],
    plan: dict[str, Any],
    intent_id: str,
) -> dict[str, Any]:
    plan_id = str(plan["planId"])
    approval_token = _issue_omv_smb_approval(
        base_url,
        token=token,
        password=password,
        plan_id=plan_id,
        intent_id=intent_id,
    )
    status, body, _headers = _http(
        "POST",
        f"{base_url}/api/appliance/omv/sharing/smb/apply",
        token=token,
        payload={"desired": desired, "planId": plan_id},
        extra_headers={
            "X-Echo-Approval": approval_token,
            "X-Echo-Intent": intent_id,
        },
    )
    response = _json_body(body, "OMV SMB apply")
    if (
        status != 200
        or not isinstance(response, dict)
        or response.get("planId") != plan_id
        or response.get("applied") is not True
        or response.get("verified") is not True
    ):
        raise VerificationError(f"OMV SMB apply failed: HTTP {status}, {response}")
    if password in json.dumps(response, ensure_ascii=False) or approval_token in json.dumps(
        response, ensure_ascii=False
    ):
        raise VerificationError("OMV SMB apply response exposed an approval credential")
    return response


def _find_smb_share(snapshot: dict[str, Any], folder_uuid: str) -> dict[str, Any]:
    matching = [
        item
        for item in snapshot["smb"]["shares"]
        if isinstance(item, dict) and str(item.get("sharedFolderRef") or "").lower() == folder_uuid
    ]
    if len(matching) != 1:
        raise VerificationError(
            "OMV SMB write verification requires exactly one existing SMB rule "
            "for the designated test folder"
        )
    return matching[0]


def _assert_omv_smb_reversible_write(
    base_url: str,
    *,
    token: str,
    password: str,
    folder_uuid: str,
    confirmation: str | None,
    require_write: bool,
) -> dict[str, Any]:
    normalized_folder = folder_uuid.strip().lower()
    if _OMV_UUID.fullmatch(normalized_folder) is None:
        raise VerificationError("OMV SMB test folder must be one exact UUID")
    expected_confirmation = f"VERIFY ECHO OMV SMB WRITE {normalized_folder}"
    if confirmation and confirmation != expected_confirmation:
        raise VerificationError("OMV SMB write confirmation does not match the test folder")
    if require_write and confirmation != expected_confirmation:
        raise VerificationError(
            "OMV SMB reversible write is required; preview first, then pass "
            f"--omv-smb-write-confirm '{expected_confirmation}'"
        )
    if confirmation == expected_confirmation and require_write is not True:
        raise VerificationError("OMV SMB write confirmation also requires --require-omv-smb-write")

    before_snapshot = _omv_sharing_snapshot(base_url, token=token)
    original_share = _find_smb_share(before_snapshot, normalized_folder)
    original = _simple_smb_desired(original_share)
    probe = {
        **original,
        "comment": f"Echo reversible verification {secrets.token_hex(8)}",
    }
    probe_plan = _plan_omv_smb(base_url, token=token, desired=probe)
    changes = probe_plan["changes"]
    if (
        probe_plan.get("operation") != "update"
        or len(changes) != 1
        or changes[0].get("field") != "comment"
        or changes[0].get("before") != original["comment"]
        or changes[0].get("after") != probe["comment"]
    ):
        raise VerificationError(f"OMV SMB verification plan is not comment-only: {changes}")
    preview = {
        "folderUuid": normalized_folder,
        "folderName": str(original_share.get("sharedFolderName") or "")[:255],
        "operation": "update",
        "changeFields": ["comment"],
        "confirmationRequired": expected_confirmation,
        "writeExecuted": False,
        "restored": False,
    }
    if confirmation != expected_confirmation:
        return preview

    probe_intent = f"omv-smb-verify-{secrets.token_hex(8)}"
    restore_intent = f"omv-smb-restore-{secrets.token_hex(8)}"
    changed = False
    try:
        _apply_omv_smb(
            base_url,
            token=token,
            password=password,
            desired=probe,
            plan=probe_plan,
            intent_id=probe_intent,
        )
        changed = True
        observed = _simple_smb_desired(
            _find_smb_share(
                _omv_sharing_snapshot(base_url, token=token),
                normalized_folder,
            )
        )
        if observed != probe:
            raise VerificationError("OMV SMB probe state did not match the applied desired state")

        restore_plan = _plan_omv_smb(base_url, token=token, desired=original)
        if restore_plan.get("operation") != "update":
            raise VerificationError("OMV SMB restore did not produce an update plan")
        _apply_omv_smb(
            base_url,
            token=token,
            password=password,
            desired=original,
            plan=restore_plan,
            intent_id=restore_intent,
        )
        changed = False
    except Exception as primary_exc:
        if changed:
            try:
                emergency_plan = _plan_omv_smb(base_url, token=token, desired=original)
                if emergency_plan.get("operation") == "update":
                    _apply_omv_smb(
                        base_url,
                        token=token,
                        password=password,
                        desired=original,
                        plan=emergency_plan,
                        intent_id=f"omv-smb-emergency-restore-{secrets.token_hex(6)}",
                    )
            except Exception as restore_exc:
                raise VerificationError(
                    "OMV SMB verification failed and emergency restoration also failed; "
                    "inspect the designated test share immediately"
                ) from restore_exc
        raise primary_exc

    final_share = _simple_smb_desired(
        _find_smb_share(
            _omv_sharing_snapshot(base_url, token=token),
            normalized_folder,
        )
    )
    if final_share != original:
        raise VerificationError("OMV SMB test share was not restored exactly")

    audit_status, audit_body, _headers = _http(
        "GET",
        f"{base_url}/api/appliance/audit/events?limit=100",
        token=token,
    )
    audit = _json_body(audit_body, "OMV SMB audit events")
    if audit_status != 200 or audit.get("verification", {}).get("ok") is not True:
        raise VerificationError("OMV SMB verification could not validate the audit chain")
    events = [
        event.get("payload", {})
        for event in audit.get("events", [])
        if event.get("kind") == "appliance_action"
    ]
    for intent_id in (probe_intent, restore_intent):
        outcomes = {
            event.get("outcome")
            for event in events
            if event.get("action") == "omv.smb.apply"
            and event.get("metadata", {}).get("intentId") == intent_id
        }
        if not {"attempted", "succeeded"}.issubset(outcomes):
            raise VerificationError(
                f"OMV SMB audit outcomes are incomplete for {intent_id}: {sorted(outcomes)}"
            )
    serialized_audit = json.dumps(audit, ensure_ascii=False)
    if password in serialized_audit:
        raise VerificationError("OMV SMB audit trail exposed the administrator password")
    return {
        **preview,
        "writeExecuted": True,
        "restored": True,
        "applyVerified": True,
        "auditVerified": True,
    }


def _validated_omv_quota_target(
    filesystem_uuid: str,
    subject_type: str,
    subject_name: str,
    hard_limit_bytes: int,
) -> dict[str, Any]:
    normalized_uuid = str(filesystem_uuid or "").strip().lower()
    normalized_type = str(subject_type or "").strip().lower()
    normalized_name = str(subject_name or "").strip()
    if _OMV_UUID.fullmatch(normalized_uuid) is None:
        raise VerificationError("OMV quota test filesystem must be one exact UUID")
    if normalized_type not in {"user", "group"}:
        raise VerificationError("OMV quota test subject type must be user or group")
    if re.fullmatch(r"[A-Za-z0-9_.@-]{1,255}", normalized_name) is None:
        raise VerificationError(
            "OMV quota test subject name must be one unambiguous POSIX account name"
        )
    if (
        isinstance(hard_limit_bytes, bool)
        or not isinstance(hard_limit_bytes, int)
        or hard_limit_bytes < 1024
        or hard_limit_bytes > 2**63 - 1
        or hard_limit_bytes % 1024 != 0
    ):
        raise VerificationError(
            "OMV quota test bytes must be a positive multiple of 1024 within int64"
        )
    return {
        "schema": "echo.omv.filesystem-quota-desired.v1",
        "filesystemUuid": normalized_uuid,
        "subjectType": normalized_type,
        "subjectName": normalized_name,
        "hardLimitBytes": hard_limit_bytes,
    }


def _plan_omv_quota(
    base_url: str,
    *,
    token: str,
    desired: dict[str, Any],
) -> dict[str, Any]:
    status, body, _headers = _http(
        "POST",
        f"{base_url}/api/appliance/omv/quota/plan",
        token=token,
        payload=desired,
    )
    plan = _json_body(body, "OMV quota plan")
    subject = plan.get("subject") if isinstance(plan, dict) else None
    filesystem = plan.get("filesystem") if isinstance(plan, dict) else None
    safety = plan.get("safety") if isinstance(plan, dict) else None
    if (
        status != 200
        or not isinstance(plan, dict)
        or plan.get("schema") != "echo.omv.filesystem-quota-plan.v1"
        or re.fullmatch(r"[0-9a-f]{64}", str(plan.get("planId") or "")) is None
        or plan.get("desired") != desired
        or plan.get("operation") not in {"update", "none"}
        or not isinstance(plan.get("changes"), list)
        or not isinstance(subject, dict)
        or subject.get("type") != desired["subjectType"]
        or subject.get("name") != desired["subjectName"]
        or isinstance(subject.get("hardLimitBytes"), bool)
        or not isinstance(subject.get("hardLimitBytes"), int)
        or not isinstance(subject.get("used"), str)
        or not isinstance(filesystem, dict)
        or filesystem.get("uuid") != desired["filesystemUuid"]
        or filesystem.get("readOnly") is not False
        or filesystem.get("supportsQuota") is not True
        or not isinstance(safety, dict)
        or safety.get("scope") != "filesystemUserOrGroup"
        or safety.get("protocolCoverage") != ["local", "SMB", "NFS"]
        or safety.get("sharedFolderQuota") != "notSupportedByOmvQuotaRpc"
    ):
        raise VerificationError(f"OMV quota plan is invalid: HTTP {status}, {plan}")
    return plan


def _issue_omv_quota_approval(
    base_url: str,
    *,
    token: str,
    password: str,
    plan_id: str,
    intent_id: str,
) -> str:
    status, body, _headers = _http(
        "POST",
        f"{base_url}/api/appliance/approvals",
        token=token,
        payload={
            "action": "omv.quota.apply",
            "target": plan_id,
            "password": password,
            "intentId": intent_id,
        },
    )
    response = _json_body(body, "OMV quota approval")
    approval_token = str(response.get("approvalToken") or "") if isinstance(response, dict) else ""
    if status != 200 or not approval_token or response.get("target") != plan_id:
        raise VerificationError(f"OMV quota approval was not issued: HTTP {status}")
    return approval_token


def _apply_omv_quota(
    base_url: str,
    *,
    token: str,
    password: str,
    desired: dict[str, Any],
    plan: dict[str, Any],
    intent_id: str,
) -> dict[str, Any]:
    plan_id = str(plan["planId"])
    approval_token = _issue_omv_quota_approval(
        base_url,
        token=token,
        password=password,
        plan_id=plan_id,
        intent_id=intent_id,
    )
    status, body, _headers = _http(
        "POST",
        f"{base_url}/api/appliance/omv/quota/apply",
        token=token,
        payload={"desired": desired, "planId": plan_id},
        extra_headers={
            "X-Echo-Approval": approval_token,
            "X-Echo-Intent": intent_id,
        },
    )
    response = _json_body(body, "OMV quota apply")
    if (
        status != 200
        or not isinstance(response, dict)
        or response.get("planId") != plan_id
        or response.get("applied") is not True
        or response.get("verified") is not True
    ):
        raise VerificationError(f"OMV quota apply failed: HTTP {status}, {response}")
    serialized = json.dumps(response, ensure_ascii=False)
    if password in serialized or approval_token in serialized:
        raise VerificationError("OMV quota apply response exposed an approval credential")
    return response


def _assert_omv_quota_reversible_write(
    base_url: str,
    *,
    token: str,
    password: str,
    filesystem_uuid: str,
    subject_type: str,
    subject_name: str,
    probe_limit_bytes: int,
    confirmation: str | None,
    require_write: bool,
) -> dict[str, Any]:
    probe = _validated_omv_quota_target(
        filesystem_uuid,
        subject_type,
        subject_name,
        probe_limit_bytes,
    )
    probe_plan = _plan_omv_quota(base_url, token=token, desired=probe)
    original_limit = int(probe_plan["subject"]["hardLimitBytes"])
    expected_confirmation = (
        "VERIFY ECHO OMV QUOTA WRITE "
        f"{probe['filesystemUuid']} {probe['subjectType']} {probe['subjectName']} "
        f"FROM {original_limit} TO {probe_limit_bytes}"
    )
    if confirmation and confirmation != expected_confirmation:
        raise VerificationError(
            "OMV quota write confirmation does not match the current target and limits"
        )
    if require_write and confirmation != expected_confirmation:
        raise VerificationError(
            "OMV quota reversible write is required; preview first, then pass "
            f"--omv-quota-write-confirm '{expected_confirmation}'"
        )
    if confirmation == expected_confirmation and require_write is not True:
        raise VerificationError(
            "OMV quota write confirmation also requires --require-omv-quota-write"
        )
    if original_limit != 0 and probe_limit_bytes >= original_limit:
        raise VerificationError(
            "OMV quota probe must temporarily tighten the current limit; choose fewer bytes"
        )
    changes = probe_plan["changes"]
    if (
        probe_plan.get("operation") != "update"
        or len(changes) != 1
        or changes[0].get("field") != "hardLimitBytes"
        or changes[0].get("before") != original_limit
        or changes[0].get("after") != probe_limit_bytes
    ):
        raise VerificationError(f"OMV quota verification plan is not limit-only: {changes}")

    original = {**probe, "hardLimitBytes": original_limit}
    preview = {
        "filesystemUuid": probe["filesystemUuid"],
        "filesystemLabel": str(probe_plan["filesystem"].get("label") or "")[:255],
        "subjectType": probe["subjectType"],
        "subjectName": probe["subjectName"],
        "originalHardLimitBytes": original_limit,
        "probeHardLimitBytes": probe_limit_bytes,
        "used": probe_plan["subject"]["used"],
        "operation": "update",
        "changeFields": ["hardLimitBytes"],
        "scope": "filesystemUserOrGroup",
        "protocolCoverage": ["local", "SMB", "NFS"],
        "confirmationRequired": expected_confirmation,
        "writeExecuted": False,
        "restored": False,
    }
    if confirmation != expected_confirmation:
        return preview

    probe_intent = f"omv-quota-verify-{secrets.token_hex(8)}"
    restore_intent = f"omv-quota-restore-{secrets.token_hex(8)}"
    changed = False
    try:
        _apply_omv_quota(
            base_url,
            token=token,
            password=password,
            desired=probe,
            plan=probe_plan,
            intent_id=probe_intent,
        )
        changed = True
        observed_probe = _plan_omv_quota(base_url, token=token, desired=probe)
        if (
            observed_probe.get("operation") != "none"
            or observed_probe["subject"].get("hardLimitBytes") != probe_limit_bytes
        ):
            raise VerificationError("OMV quota probe state did not match the applied limit")

        restore_plan = _plan_omv_quota(base_url, token=token, desired=original)
        if restore_plan.get("operation") != "update":
            raise VerificationError("OMV quota restore did not produce an update plan")
        _apply_omv_quota(
            base_url,
            token=token,
            password=password,
            desired=original,
            plan=restore_plan,
            intent_id=restore_intent,
        )
        changed = False
    except Exception as primary_exc:
        if changed:
            try:
                emergency_plan = _plan_omv_quota(base_url, token=token, desired=original)
                if emergency_plan.get("operation") == "update":
                    _apply_omv_quota(
                        base_url,
                        token=token,
                        password=password,
                        desired=original,
                        plan=emergency_plan,
                        intent_id=f"omv-quota-emergency-restore-{secrets.token_hex(6)}",
                    )
            except Exception as restore_exc:
                raise VerificationError(
                    "OMV quota verification failed and emergency restoration also failed; "
                    "inspect the designated test subject immediately"
                ) from restore_exc
        raise primary_exc

    final_plan = _plan_omv_quota(base_url, token=token, desired=original)
    if (
        final_plan.get("operation") != "none"
        or final_plan["subject"].get("hardLimitBytes") != original_limit
    ):
        raise VerificationError("OMV quota test subject was not restored exactly")

    audit_status, audit_body, _headers = _http(
        "GET",
        f"{base_url}/api/appliance/audit/events?limit=100",
        token=token,
    )
    audit = _json_body(audit_body, "OMV quota audit events")
    if audit_status != 200 or audit.get("verification", {}).get("ok") is not True:
        raise VerificationError("OMV quota verification could not validate the audit chain")
    events = [
        event.get("payload", {})
        for event in audit.get("events", [])
        if event.get("kind") == "appliance_action"
    ]
    for intent_id in (probe_intent, restore_intent):
        outcomes = {
            event.get("outcome")
            for event in events
            if event.get("action") == "omv.quota.apply"
            and event.get("metadata", {}).get("intentId") == intent_id
        }
        if not {"attempted", "succeeded"}.issubset(outcomes):
            raise VerificationError(
                f"OMV quota audit outcomes are incomplete for {intent_id}: {sorted(outcomes)}"
            )
    if password in json.dumps(audit, ensure_ascii=False):
        raise VerificationError("OMV quota audit trail exposed the administrator password")
    return {
        **preview,
        "writeExecuted": True,
        "restored": True,
        "applyVerified": True,
        "auditVerified": True,
    }


def _assert_high_risk_approval(
    base_url: str,
    *,
    token: str,
    password: str,
    protected_container_id: str,
) -> dict[str, int]:
    endpoint = f"{base_url}/api/appliance/apps/{protected_container_id}/stop"
    denied, _body, _headers = _http("POST", endpoint, token=token)
    if denied != 403:
        raise VerificationError(f"unapproved protected stop returned HTTP {denied}")

    issued_status, issued_body, _headers = _http(
        "POST",
        f"{base_url}/api/appliance/approvals",
        token=token,
        payload={
            "action": "app.stop",
            "target": protected_container_id,
            "password": password,
        },
    )
    issued = _json_body(issued_body, "high-risk approval")
    approval_token = str(issued.get("approvalToken") or "")
    if issued_status != 200 or not approval_token:
        raise VerificationError(f"high-risk approval was not issued: HTTP {issued_status}")

    protected, _body, _headers = _http(
        "POST",
        endpoint,
        token=token,
        extra_headers={"X-Echo-Approval": approval_token},
    )
    if protected != 403:
        raise VerificationError(f"protected main-container stop returned HTTP {protected}")

    replayed, _body, _headers = _http(
        "POST",
        endpoint,
        token=token,
        extra_headers={"X-Echo-Approval": approval_token},
    )
    if replayed != 403:
        raise VerificationError(f"replayed high-risk approval returned HTTP {replayed}")

    audit_status, audit_body, _headers = _http(
        "GET",
        f"{base_url}/api/appliance/audit/verify",
        token=token,
    )
    audit_report = _json_body(audit_body, "appliance audit verification")
    if audit_status != 200 or audit_report.get("ok") is not True:
        raise VerificationError(f"appliance audit chain is unhealthy: {audit_report}")

    events_status, events_body, _headers = _http(
        "GET",
        f"{base_url}/api/appliance/audit/events?limit=20",
        token=token,
    )
    events_response = _json_body(events_body, "appliance audit events")
    if events_status != 200 or events_response.get("verification", {}).get("ok") is not True:
        raise VerificationError("authenticated audit event query failed")
    payloads = [
        event.get("payload", {})
        for event in events_response.get("events", [])
        if event.get("kind") == "appliance_action"
    ]
    approval_outcomes = [
        payload.get("outcome")
        for payload in payloads
        if payload.get("action") == "approval"
        and payload.get("target") == f"app.stop:{protected_container_id}"
    ]
    app_outcomes = [
        payload.get("outcome")
        for payload in payloads
        if payload.get("action") == "app.stop" and payload.get("target") == protected_container_id
    ]
    if not {"issued", "consumed", "denied"}.issubset(set(approval_outcomes)):
        raise VerificationError(f"approval audit outcomes are incomplete: {approval_outcomes}")
    if not {"attempted", "failed"}.issubset(set(app_outcomes)):
        raise VerificationError(f"app-control audit outcomes are incomplete: {app_outcomes}")
    serialized_events = json.dumps(events_response, ensure_ascii=False)
    if password in serialized_events or approval_token in serialized_events:
        raise VerificationError("approval credentials leaked into the audit trail")
    return {
        "approval": issued_status,
        "protected_stop": protected,
        "approval_replay": replayed,
        "audit_verify": audit_status,
    }


def verify(args: argparse.Namespace) -> dict[str, Any]:
    password = os.environ.get(args.password_env, "")
    if not password:
        raise VerificationError(f"{args.password_env} must be set for the smoke login")
    base_url = args.base_url.rstrip("/")
    config = _wait_for_bundle(base_url, args.wait_seconds)
    bundle = config["agent_bundle"]
    if args.require_clean_bundle and bundle.get("dirty") is not False:
        raise VerificationError("release smoke requires a clean Agent bundle")

    status, body, login_headers = _http(
        "POST",
        f"{base_url}/api/auth/local/login",
        payload={"username": "admin", "password": password},
    )
    if status != 200:
        raise VerificationError(f"admin login failed with HTTP {status}")
    login = _json_body(body, "admin login")
    token = str(login.get("access_token") or "")
    if not login.get("success") or not token:
        raise VerificationError("admin login returned no access token")
    session_cookie = _assert_session_cookie(
        login_headers,
        require_secure=base_url.casefold().startswith("https://"),
    )

    _assert_browser_boundary(base_url, token)
    _assert_login_rate_limit(base_url)

    unauthenticated, _body, _headers = _http("GET", f"{base_url}/api/appliance/apps")
    if unauthenticated != 401:
        raise VerificationError(f"unauthenticated apps endpoint returned {unauthenticated}")
    cookie_status, _body, _headers = _http(
        "GET",
        f"{base_url}/api/appliance/apps",
        extra_headers={"Cookie": session_cookie},
    )
    if cookie_status != 200:
        raise VerificationError(f"HttpOnly cookie authentication returned {cookie_status}")
    status, body, _headers = _http("GET", f"{base_url}/api/auth/local/whoami", token=token)
    whoami = _json_body(body, "whoami")
    if status != 200 or whoami.get("actor_id") != "local:admin":
        raise VerificationError(f"unexpected authenticated identity: {whoami}")
    status, body, _headers = _http("GET", f"{base_url}/api/appliance/apps", token=token)
    apps = _json_body(body, "apps")
    if status != 200 or apps.get("available") is not True:
        raise VerificationError(f"Docker-backed apps endpoint is unavailable: {apps}")
    _assert_web_surfaces(base_url)
    agent_assets_result = _assert_agent_assets_contract(base_url, token)
    agent_capabilities_result = _assert_agent_capabilities_contract(base_url, token)
    hub_resource_preflight = _assert_hub_resource_preflight(base_url, token)
    hub_operations = _assert_hub_operations_contract(base_url, token)
    storage_usage_result = _assert_storage_usage_contract(base_url, token)
    photos_result = _assert_photos_contract(base_url, token)
    device_link_result = _assert_device_link_contract(base_url, token)
    device_sync_result = _assert_device_sync_contract(base_url, token)
    family_fixture = str(getattr(args, "family_isolation_fixture", "") or "").strip()
    require_family_isolation = bool(getattr(args, "require_family_isolation", False))
    if require_family_isolation and not family_fixture:
        raise VerificationError("family isolation verification requires --family-isolation-fixture")
    family_isolation_result = (
        _assert_family_isolation_contract(base_url, fixture_path=family_fixture)
        if family_fixture
        else None
    )

    main = _inspect(args.main_container)
    proxy = _inspect(args.proxy_container)
    main_id = str(main.get("Id") or "")
    if not _CONTAINER_ID.fullmatch(main_id):
        raise VerificationError("main container has an invalid Docker id")
    main_mounts = main.get("Mounts") or []
    proxy_mounts = proxy.get("Mounts") or []
    if any(item.get("Destination") == "/var/run/docker.sock" for item in main_mounts):
        raise VerificationError("Echo main container still owns the raw Docker socket")
    if not any(item.get("Destination") == "/var/run/docker.sock" for item in proxy_mounts):
        raise VerificationError("docker-control does not own the expected Docker socket")
    docker_data_mounts = [
        item for item in proxy_mounts if item.get("Destination") == "/run/echo-host/docker-data"
    ]
    if (
        len(docker_data_mounts) != 1
        or docker_data_mounts[0].get("RW") is not False
        or not str(docker_data_mounts[0].get("Source") or "").startswith("/")
    ):
        raise VerificationError("docker-control lacks its read-only Docker data-root observer")
    published = proxy.get("NetworkSettings", {}).get("Ports") or {}
    if any(bindings for bindings in published.values()):
        raise VerificationError(f"docker-control unexpectedly publishes host ports: {published}")
    if proxy.get("HostConfig", {}).get("ReadonlyRootfs") is not True:
        raise VerificationError("docker-control root filesystem is not read-only")

    for inspected, name in ((main, args.main_container), (proxy, args.proxy_container)):
        labels = inspected.get("Config", {}).get("Labels") or {}
        if labels.get("sh.echo.control-protected") != "true":
            raise VerificationError(f"{name} lacks its control-protected label")
    proxy_labels = proxy.get("Config", {}).get("Labels") or {}
    if proxy_labels.get("sh.echo.hub.data-copy-provider") != "true":
        raise VerificationError("docker-control lacks its Hub data-copy provider label")
    proxy_networks = set(proxy.get("NetworkSettings", {}).get("Networks") or {})
    if len(proxy_networks) != 1:
        raise VerificationError(f"docker-control must have one isolated network: {proxy_networks}")
    network = _docker_json("network", "inspect", next(iter(proxy_networks)))[0]
    if network.get("Internal") is not True:
        raise VerificationError("docker-control network is not internal")

    _assert_runtime_identity(args.main_container, args.expected_uid, args.expected_gid)
    _assert_nonroot_runtime_identity(args.proxy_container)
    runtime_arch = _assert_runtime_architecture(args.main_container, args.expected_arch)
    _assert_state_owner(args.main_container, args.expected_uid, args.expected_gid)
    _assert_runtime_secret_indirection(args.main_container)
    _assert_internal_proxy_policy(args.main_container, main_id)

    nas_transfer_size = int(getattr(args, "nas_transfer_test_bytes", 0) or 0)
    nas_transfer_path = str(getattr(args, "nas_transfer_test_path", "") or "")
    nas_transfer_confirm = str(getattr(args, "nas_transfer_write_confirm", "") or "").strip()
    require_nas_transfer = bool(getattr(args, "require_nas_transfer", False))
    nas_transfer_restart_main = bool(getattr(args, "nas_transfer_restart_main", False))
    if (
        nas_transfer_path
        or nas_transfer_confirm
        or require_nas_transfer
        or nas_transfer_restart_main
    ) and not nas_transfer_size:
        raise VerificationError(
            "NAS transfer verification options require --nas-transfer-test-bytes"
        )
    nas_transfer_result = None
    if nas_transfer_size:
        restart_callback = None
        restart_container = None
        if nas_transfer_restart_main:
            restart_container = args.main_container

            def restart_callback() -> None:
                _restart_main_for_nas_transfer(
                    args.main_container,
                    base_url=base_url,
                    wait_seconds=args.wait_seconds,
                )

        nas_transfer_result = _assert_nas_large_transfer(
            base_url,
            token=token,
            directory=nas_transfer_path,
            size=nas_transfer_size,
            confirmation=nas_transfer_confirm or None,
            require_write=require_nas_transfer,
            restart_container=restart_container,
            restart_callback=restart_callback,
        )

    omv_result = None
    omv_smb_test_folder = str(getattr(args, "omv_smb_test_folder", "") or "").strip()
    omv_smb_write_confirm = str(getattr(args, "omv_smb_write_confirm", "") or "").strip()
    require_omv_smb_write = bool(getattr(args, "require_omv_smb_write", False))
    omv_quota_test_filesystem = str(getattr(args, "omv_quota_test_filesystem", "") or "").strip()
    omv_quota_test_subject_type = str(
        getattr(args, "omv_quota_test_subject_type", "") or ""
    ).strip()
    omv_quota_test_subject_name = str(
        getattr(args, "omv_quota_test_subject_name", "") or ""
    ).strip()
    omv_quota_test_bytes = getattr(args, "omv_quota_test_bytes", None)
    omv_quota_write_confirm = str(getattr(args, "omv_quota_write_confirm", "") or "").strip()
    require_omv_quota_write = bool(getattr(args, "require_omv_quota_write", False))
    if (
        omv_smb_test_folder or omv_smb_write_confirm or require_omv_smb_write
    ) and not args.require_omv:
        raise VerificationError("OMV SMB write verification requires --require-omv")
    if (omv_smb_write_confirm or require_omv_smb_write) and not omv_smb_test_folder:
        raise VerificationError("OMV SMB write verification requires --omv-smb-test-folder")
    quota_options_present = bool(
        omv_quota_test_filesystem
        or omv_quota_test_subject_type
        or omv_quota_test_subject_name
        or omv_quota_test_bytes is not None
        or omv_quota_write_confirm
        or require_omv_quota_write
    )
    if quota_options_present and not args.require_omv:
        raise VerificationError("OMV quota write verification requires --require-omv")
    quota_target_complete = bool(
        omv_quota_test_filesystem
        and omv_quota_test_subject_type
        and omv_quota_test_subject_name
        and omv_quota_test_bytes is not None
    )
    if quota_options_present and not quota_target_complete:
        raise VerificationError(
            "OMV quota verification requires filesystem, subject type, subject name, and bytes"
        )
    if args.require_omv:
        omv_unit, omv_install_mode = _resolve_omv_unit_path(args.omv_unit)
        omv_host_install = _assert_omv_host_install(
            omv_unit,
            args.omv_code_root,
        )
        omv_host_install["install_mode"] = omv_install_mode
        omv_result = _assert_omv_integration(
            base_url,
            token=token,
            main=main,
            proxy=proxy,
            socket_path=args.omv_socket,
            expected_gid=args.expected_gid,
        )
        omv_result["host_install"] = omv_host_install
        if omv_smb_test_folder:
            omv_result["smb_reversible_write"] = _assert_omv_smb_reversible_write(
                base_url,
                token=token,
                password=password,
                folder_uuid=omv_smb_test_folder,
                confirmation=omv_smb_write_confirm or None,
                require_write=require_omv_smb_write,
            )
        if quota_target_complete:
            omv_result["quota_reversible_write"] = _assert_omv_quota_reversible_write(
                base_url,
                token=token,
                password=password,
                filesystem_uuid=omv_quota_test_filesystem,
                subject_type=omv_quota_test_subject_type,
                subject_name=omv_quota_test_subject_name,
                probe_limit_bytes=omv_quota_test_bytes,
                confirmation=omv_quota_write_confirm or None,
                require_write=require_omv_quota_write,
            )

    approval_result = _assert_high_risk_approval(
        base_url,
        token=token,
        password=password,
        protected_container_id=main_id,
    )
    if _inspect(args.main_container).get("State", {}).get("Running") is not True:
        raise VerificationError("main container stopped despite the protected label")
    _assert_state_owner(args.main_container, args.expected_uid, args.expected_gid)

    result = {
        "bundle_verified": True,
        "bundle_source_id": bundle.get("source_id"),
        "bundle_dirty": bundle.get("dirty"),
        "login": 200,
        "session_cookie_httponly_lax": True,
        "session_cookie_auth": cookie_status,
        "runtime_secrets_indirect": True,
        "whoami": whoami["actor_id"],
        "apps_available": True,
        "desktop": 200,
        "workbench": 200,
        "agent_assets": agent_assets_result,
        "agent_capabilities": agent_capabilities_result,
        "hub_resource_preflight": hub_resource_preflight,
        "hub_operations": hub_operations,
        "main_has_docker_socket": False,
        "proxy_has_host_ports": False,
        "proxy_network_internal": True,
        "main_uid_gid": [args.expected_uid, args.expected_gid],
        "architecture": runtime_arch,
        "proxy_process_nonroot": True,
        "main_effective_capabilities": 0,
        "proxy_effective_capabilities": 0,
        "no_new_privileges": True,
        "origin_guard": 403,
        "host_guard": 400,
        "login_rate_limit": 429,
        "approval": approval_result["approval"],
        "approval_replay": approval_result["approval_replay"],
        "audit_verify": approval_result["audit_verify"],
        "protected_stop": approval_result["protected_stop"],
        "storage_usage": storage_usage_result,
        "photos": photos_result,
        "device_link": device_link_result,
        "device_sync": device_sync_result,
    }
    if omv_result is not None:
        result["omv"] = omv_result
    if nas_transfer_result is not None:
        result["nas_transfer"] = nas_transfer_result
    if family_isolation_result is not None:
        result["family_isolation"] = family_isolation_result
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--password-env", default="ECHO_ADMIN_PASSWORD")
    parser.add_argument("--main-container", default="echo-os")
    parser.add_argument("--proxy-container", default="echo-docker-control")
    parser.add_argument("--expected-uid", type=int, default=1000)
    parser.add_argument("--expected-gid", type=int, default=1000)
    parser.add_argument("--expected-arch", choices=("amd64", "arm64"))
    parser.add_argument("--wait-seconds", type=float, default=180)
    parser.add_argument("--require-clean-bundle", action="store_true")
    parser.add_argument(
        "--nas-transfer-test-bytes",
        type=int,
        default=0,
        help=(
            "preview or execute a deterministic resumable upload/download verification; "
            "use 1073741824 for the 1 GiB delivery gate"
        ),
    )
    parser.add_argument(
        "--nas-transfer-test-path",
        default="",
        help="existing NAS-relative directory used only for the transfer probe",
    )
    parser.add_argument(
        "--nas-transfer-write-confirm",
        default="",
        help="exact confirmation emitted by the transfer preview",
    )
    parser.add_argument(
        "--require-nas-transfer",
        action="store_true",
        help=(
            "fail unless resumable upload, digest, full/Range download, cancellation, "
            "and recoverable cleanup all pass"
        ),
    )
    parser.add_argument(
        "--nas-transfer-restart-main",
        action="store_true",
        help=(
            "after the first verified chunk, restart the main container and require the "
            "same persisted upload session to resume; changes the exact confirmation"
        ),
    )
    parser.add_argument("--require-omv", action="store_true")
    parser.add_argument(
        "--family-isolation-fixture",
        default="",
        help=(
            "absolute 0400/0600 strict-JSON fixture for two dedicated family test "
            "members and their real OMV-projected file/photo paths"
        ),
    )
    parser.add_argument(
        "--require-family-isolation",
        action="store_true",
        help="fail unless both dedicated members pass login and data-isolation probes",
    )
    parser.add_argument(
        "--omv-smb-test-folder",
        default="",
        help=(
            "existing private SMB shared-folder UUID used for a comment-only reversible "
            "write preview"
        ),
    )
    parser.add_argument(
        "--omv-smb-write-confirm",
        default="",
        help=(
            "exact confirmation emitted by the preview; temporarily changes only the "
            "designated SMB rule comment and restores it"
        ),
    )
    parser.add_argument(
        "--require-omv-smb-write",
        action="store_true",
        help="fail unless the reversible OMV SMB write, restoration, and audit all pass",
    )
    parser.add_argument(
        "--omv-quota-test-filesystem",
        default="",
        help="existing writable quota-capable filesystem UUID used for a reversible preview",
    )
    parser.add_argument(
        "--omv-quota-test-subject-type",
        choices=("user", "group"),
        default="",
        help="type of the dedicated existing OMV quota test subject",
    )
    parser.add_argument(
        "--omv-quota-test-subject-name",
        default="",
        help="name of the dedicated existing OMV quota test user or group",
    )
    parser.add_argument(
        "--omv-quota-test-bytes",
        type=int,
        default=None,
        help=(
            "temporary positive hard limit; must be a 1024-byte multiple and stricter "
            "than the current limit"
        ),
    )
    parser.add_argument(
        "--omv-quota-write-confirm",
        default="",
        help="exact confirmation emitted by the preview; restores the original limit",
    )
    parser.add_argument(
        "--require-omv-quota-write",
        action="store_true",
        help="fail unless reversible OMV quota apply, restoration, and audit all pass",
    )
    parser.add_argument("--omv-socket", default="/run/echo-omv/omv.sock")
    parser.add_argument(
        "--omv-unit",
        default="auto",
        help=(
            "auto-detect the native /usr/lib unit or managed /etc unit; an explicit "
            "absolute path is also accepted"
        ),
    )
    parser.add_argument(
        "--omv-code-root",
        default="/usr/lib/echo-os/omv-bridge",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = verify(args)
    except (VerificationError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
