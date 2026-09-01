"""Least-privilege Docker control proxy for the Echo NAS appliance.

Only launcher operations and catalog-verified Hub lifecycle actions are reachable over HTTP:

* ``GET /_ping`` and ``GET /health``
* ``GET /containers/json?all=true|false``
* ``GET /hub/storage`` (sanitized Docker data-filesystem capacity only)
* ``GET /hub/apps/{app-id}/runtime`` (sanitized health/resource summary only)
* ``POST /containers/{id}/start``
* ``POST /containers/{id}/stop``
* ``POST /hub/apps/{app-id}/install`` with plan/catalog identities only
* ``POST /hub/apps/{app-id}/update`` with plan/catalog identities only
* ``POST /hub/apps/{app-id}/uninstall`` with plan/catalog identities only
* ``POST /hub/apps/{app-id}/start|stop|restart`` for the complete verified service set
* the same six paths with ``/stream`` for bounded, secret-free NDJSON progress

The public Echo/Agent process never receives the host Docker socket.  This
small sidecar owns it, drops root while retaining only the socket's numeric
group, and exposes the narrow API on an internal Compose network. Raw Docker
create/delete/exec/images/volumes/networks and arbitrary proxying do not exist.
"""

from __future__ import annotations

import argparse
import hmac
import ipaddress
import json
import os
import re
import shutil
import stat
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qs, urlsplit

from appliance.app_registry.docker_client import (
    DOCKER_STORAGE_SCHEMA,
    DockerClient,
    DockerConflict,
    DockerUnavailable,
)
from appliance.hub.catalog import HubCatalog
from appliance.hub.docker_installer import HubDockerInstaller, HubInstallRejected
from appliance.hub.progress import HUB_STREAM_SCHEMA, validate_hub_progress
from appliance.hub.runtime import HubRuntimeInspector, validate_hub_runtime

_CONTAINER_ACTION = re.compile(r"^/containers/([0-9a-f]{12,64})/(start|stop)$")
_HUB_OPERATION = re.compile(
    r"^/hub/apps/([a-z][a-z0-9]*(?:-[a-z0-9]+)*)/"
    r"(install|update|uninstall|start|stop|restart)$"
)
_HUB_OPERATION_STREAM = re.compile(
    r"^/hub/apps/([a-z][a-z0-9]*(?:-[a-z0-9]+)*)/"
    r"(install|update|uninstall|start|stop|restart)/stream$"
)
_HUB_RUNTIME = re.compile(r"^/hub/apps/([a-z][a-z0-9]*(?:-[a-z0-9]+)*)/runtime$")
_TRUE_VALUES = {"1", "true"}
_FALSE_VALUES = {"0", "false"}
_LEGACY_LABEL_NAMESPACE = "sh.octo" + "pus"
_PROTECTED_LABELS = (
    "sh.echo.control-protected",
    f"{_LEGACY_LABEL_NAMESPACE}.control-protected",
)


def _proxy_token() -> str:
    token = os.environ.get("ECHO_DOCKER_PROXY_TOKEN", "").strip()
    if token and not 32 <= len(token) <= 512:
        raise RuntimeError("ECHO_DOCKER_PROXY_TOKEN must contain 32-512 characters")
    return token


def _loopback_bind(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _protected_ids() -> frozenset[str]:
    raw = os.environ.get("ECHO_PROTECTED_CONTAINER_IDS", "")
    ids = {part.strip().lower() for part in raw.split(",") if part.strip()}
    # 仅接受 12-64 hex，过滤非法
    return frozenset(i for i in ids if re.fullmatch(r"[0-9a-f]{12,64}", i))


def _truthy(value: Any) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def hmac_compare(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _resolve_container(client: DockerClient, container_id: str) -> tuple[str, dict[str, Any]]:
    """Resolve one short/full id to exactly one engine record.

    Docker accepts unique prefixes, but bidirectional prefix matching can bind
    a caller-controlled longer value to a different protected container.  The
    proxy resolves only in Docker's canonical direction and rejects ambiguity.
    """

    query = container_id.lower()
    matches: list[tuple[str, dict[str, Any]]] = []
    for container in client.list_containers(include_stopped=True):
        current = str(container.get("Id") or "").lower()
        if re.fullmatch(r"[0-9a-f]{64}", current) is not None and current.startswith(query):
            matches.append((current, container))
    if len(matches) != 1:
        raise DockerConflict("container target is missing or ambiguous")
    return matches[0]


def _target_is_protected(container_id: str, container: dict[str, Any]) -> bool:
    if any(container_id.startswith(protected_id) for protected_id in _protected_ids()):
        return True
    labels = container.get("Labels") or {}
    return isinstance(labels, dict) and any(
        _truthy(labels.get(label)) for label in _PROTECTED_LABELS
    )


def _empty_storage_contract(status: str = "unavailable") -> dict[str, Any]:
    return {
        "schema": DOCKER_STORAGE_SCHEMA,
        "status": status,
        "totalBytes": None,
        "freeBytes": None,
        "usedPercent": None,
    }


def _docker_storage_contract(
    client: DockerClient,
    *,
    mount_path: Path | None,
    expected_root: str | None,
) -> dict[str, Any]:
    """Bind a read-only mount to Docker's own declared data root.

    Neither host path is returned to the unprivileged application.  A custom
    or stale compose mount fails closed instead of reporting capacity for an
    unrelated filesystem.
    """

    if mount_path is None or expected_root is None:
        return _empty_storage_contract()
    parsed_expected = PurePosixPath(expected_root)
    if (
        not parsed_expected.is_absolute()
        or ".." in parsed_expected.parts
        or str(parsed_expected) != expected_root
        or expected_root == "/"
    ):
        return _empty_storage_contract("mismatch")
    try:
        if client.docker_root_dir() != expected_root:
            return _empty_storage_contract("mismatch")
        mount_stat = mount_path.lstat()
        if not stat.S_ISDIR(mount_stat.st_mode) or stat.S_ISLNK(mount_stat.st_mode):
            return _empty_storage_contract("mismatch")
        usage = shutil.disk_usage(mount_path)
    except (AttributeError, DockerUnavailable, OSError):
        return _empty_storage_contract()
    if usage.total <= 0 or not 0 <= usage.free <= usage.total:
        return _empty_storage_contract()
    return {
        "schema": DOCKER_STORAGE_SCHEMA,
        "status": "observed",
        "totalBytes": usage.total,
        "freeBytes": usage.free,
        "usedPercent": round((usage.used / usage.total) * 100, 1),
    }


def _handler_for(
    client: DockerClient,
    hub_installer: HubDockerInstaller,
    hub_runtime: HubRuntimeInspector,
    *,
    docker_data_root_mount: Path | None,
    expected_docker_root: str | None,
) -> type[BaseHTTPRequestHandler]:
    class DockerControlHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "EchoDockerControl/1"
        sys_version = ""

        def _send_bytes(
            self,
            status_code: int,
            body: bytes = b"",
            *,
            content_type: str = "application/json; charset=utf-8",
        ) -> None:
            # Never keep a connection whose rejected body was intentionally not
            # consumed; one request per connection also removes HTTP desync as a
            # concern from this tiny privileged boundary.
            self.close_connection = True
            self.send_response(status_code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if body and self.command != "HEAD":
                self.wfile.write(body)

        def _send_json(self, status_code: int, value: Any) -> None:
            body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
            self._send_bytes(status_code, body)

        def _start_hub_stream(self) -> None:
            self.close_connection = True
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Connection", "close")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()

        def _write_hub_stream_event(self, value: dict[str, Any]) -> None:
            if getattr(self, "_hub_stream_disconnected", False):
                return
            body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(body) > 256 * 1024:
                raise ValueError("Hub stream event is too large")
            try:
                self.wfile.write(body + b"\n")
                self.wfile.flush()
            except OSError:
                # The approved sidecar operation must keep running even if the
                # unprivileged caller restarts while Docker is still working.
                self._hub_stream_disconnected = True

        def _error(self, status_code: int, detail: str) -> None:
            self._send_json(status_code, {"detail": detail})

        def _request_target(self) -> tuple[str, dict[str, list[str]]] | None:
            parsed = urlsplit(self.path)
            if parsed.scheme or parsed.netloc or parsed.fragment:
                self._error(400, "invalid request target")
                return None
            # 拒绝 // 空 path 等异常
            if not parsed.path or parsed.path.startswith("//"):
                self._error(400, "invalid request target")
                return None
            return parsed.path, parse_qs(parsed.query, keep_blank_values=True)

        def _docker_available(self) -> bool:
            if client.ping():
                return True
            self._error(503, "docker engine unavailable")
            return False

        def _check_auth(self) -> bool:
            expected = _proxy_token()
            if not expected:
                return True
            got = self.headers.get("X-Echo-Proxy-Token", "").strip()
            if not got or not hmac_compare(got, expected):
                self._error(401, "proxy token required")
                return False
            return True

        def _read_hub_operation_body(self) -> dict[str, str] | None:
            if self.headers.get("Transfer-Encoding"):
                self._error(400, "chunked request bodies are not allowed")
                return None
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                self._error(415, "Hub operation requires application/json")
                return None
            try:
                content_length = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                self._error(400, "invalid content length")
                return None
            if not 1 <= content_length <= 2048:
                self._error(413, "Hub operation request body is too large or empty")
                return None
            try:
                value = json.loads(self.rfile.read(content_length))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._error(400, "Hub operation request is invalid JSON")
                return None
            if not isinstance(value, dict) or set(value) != {"planId", "catalogDigest"}:
                self._error(400, "Hub operation request fields are invalid")
                return None
            plan_id = value.get("planId")
            catalog_digest = value.get("catalogDigest")
            if (
                not isinstance(plan_id, str)
                or re.fullmatch(r"[0-9a-f]{64}", plan_id) is None
                or not isinstance(catalog_digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", catalog_digest) is None
            ):
                self._error(400, "Hub operation identities are invalid")
                return None
            return {"planId": plan_id, "catalogDigest": catalog_digest}

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if not self._check_auth():
                return
            target = self._request_target()
            if target is None:
                return
            path, query = target
            if path in {"/_ping", "/health"}:
                if query:
                    self._error(400, "query parameters are not allowed")
                elif self._docker_available():
                    self._send_bytes(200, b"OK", content_type="text/plain; charset=utf-8")
                return
            if path == "/hub/storage":
                if query:
                    self._error(400, "query parameters are not allowed")
                    return
                self._send_json(
                    200,
                    _docker_storage_contract(
                        client,
                        mount_path=docker_data_root_mount,
                        expected_root=expected_docker_root,
                    ),
                )
                return
            runtime_match = _HUB_RUNTIME.fullmatch(path)
            if runtime_match is not None:
                if query:
                    self._error(400, "query parameters are not allowed")
                    return
                try:
                    runtime = hub_runtime.inspect(runtime_match.group(1))
                    self._send_json(200, validate_hub_runtime(runtime))
                except KeyError:
                    self._error(404, "Hub app is not present in the trusted catalog")
                except (DockerUnavailable, OSError, ValueError):
                    self._error(503, "Hub runtime is unavailable")
                return
            if path != "/containers/json":
                self._error(404, "docker operation is not exposed")
                return
            if set(query) - {"all"} or len(query.get("all", [])) > 1:
                self._error(400, "only one all query parameter is allowed")
                return
            raw_all = (query.get("all") or ["false"])[0].casefold()
            if raw_all in _TRUE_VALUES:
                include_stopped = True
            elif raw_all in _FALSE_VALUES:
                include_stopped = False
            else:
                self._error(400, "all must be true or false")
                return
            try:
                self._send_json(200, client.list_containers(include_stopped=include_stopped))
            except DockerUnavailable:
                self._error(503, "docker engine unavailable")

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if not self._check_auth():
                return
            target = self._request_target()
            if target is None:
                return
            path, query = target
            if query:
                self._error(400, "query parameters are not allowed")
                return
            if self.headers.get("Transfer-Encoding"):
                self._error(400, "request bodies are not allowed")
                return
            # 拒绝重复 Host / 异常头
            if self.headers.get("Host", "").count(",") > 0:
                self._error(400, "invalid host header")
                return
            hub_match = _HUB_OPERATION.fullmatch(path)
            if hub_match is not None:
                body = self._read_hub_operation_body()
                if body is None:
                    return
                try:
                    operation = hub_match.group(2)
                    result = getattr(hub_installer, operation)(
                        hub_match.group(1),
                        plan_id=body["planId"],
                        catalog_digest=body["catalogDigest"],
                    )
                except HubInstallRejected as exc:
                    self._error(409, str(exc))
                    return
                except (DockerUnavailable, OSError):
                    self._error(503, "Hub installer is unavailable")
                    return
                self._send_json(201 if operation == "install" else 200, result)
                return
            stream_match = _HUB_OPERATION_STREAM.fullmatch(path)
            if stream_match is not None:
                body = self._read_hub_operation_body()
                if body is None:
                    return
                self._start_hub_stream()

                def emit(progress: dict[str, Any]) -> None:
                    self._write_hub_stream_event(
                        {
                            "schema": HUB_STREAM_SCHEMA,
                            "type": "progress",
                            "progress": validate_hub_progress(progress),
                        }
                    )

                operation = stream_match.group(2)
                try:
                    result = getattr(hub_installer, operation)(
                        stream_match.group(1),
                        plan_id=body["planId"],
                        catalog_digest=body["catalogDigest"],
                        progress=emit,
                    )
                except HubInstallRejected:
                    self._write_hub_stream_event(
                        {"schema": HUB_STREAM_SCHEMA, "type": "error", "code": "CONFLICT"}
                    )
                    return
                except (DockerUnavailable, OSError):
                    self._write_hub_stream_event(
                        {
                            "schema": HUB_STREAM_SCHEMA,
                            "type": "error",
                            "code": "UNAVAILABLE",
                        }
                    )
                    return
                except Exception:  # noqa: BLE001 - emit only a bounded code across the boundary
                    self._write_hub_stream_event(
                        {
                            "schema": HUB_STREAM_SCHEMA,
                            "type": "error",
                            "code": "INTERNAL",
                        }
                    )
                    return
                self._write_hub_stream_event(
                    {"schema": HUB_STREAM_SCHEMA, "type": "result", "result": result}
                )
                return
            try:
                content_length = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                self._error(400, "invalid content length")
                return
            if content_length != 0:
                self._error(400, "request bodies are not allowed")
                return
            matched = _CONTAINER_ACTION.fullmatch(path)
            if matched is None:
                self._error(404, "docker operation is not exposed")
                return
            container_id, action = matched.groups()
            try:
                resolved_id, container = _resolve_container(client, container_id)
                if _target_is_protected(resolved_id, container):
                    self._error(403, "protected appliance containers cannot be controlled")
                    return
                getattr(client, action)(resolved_id)
            except DockerConflict:
                self._error(409, "container target is missing or ambiguous")
                return
            except DockerUnavailable:
                self._error(503, "docker engine unavailable")
                return
            self._send_bytes(204)

        def do_DELETE(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            self._error(405, "method is not exposed")

        def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            self._error(405, "method is not exposed")

        def do_PATCH(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            self._error(405, "method is not exposed")

        def log_message(self, message: str, *args: Any) -> None:
            print(
                f"docker-control client={self.client_address[0]} "
                f"method={self.command} path={self.path!r} detail={message % args}",
                file=sys.stderr,
                flush=True,
            )

    return DockerControlHandler


def create_proxy_server(
    client: DockerClient,
    *,
    # Cross-container access requires all interfaces inside the internal-only
    # Compose network; neither compose file publishes this port to the host.
    host: str = "0.0.0.0",  # nosec B104
    port: int = 2375,
    hub_installer: HubDockerInstaller | None = None,
    docker_data_root_mount: Path | None = None,
    expected_docker_root: str | None = None,
) -> ThreadingHTTPServer:
    if not _proxy_token() and not _loopback_bind(host):
        raise RuntimeError(
            "ECHO_DOCKER_PROXY_TOKEN is required when Docker control listens beyond loopback"
        )
    configured_mount = docker_data_root_mount
    if configured_mount is None:
        raw_mount = os.environ.get("ECHO_DOCKER_DATA_ROOT_MOUNT", "").strip()
        configured_mount = Path(raw_mount) if raw_mount else None
    configured_root = expected_docker_root
    if configured_root is None:
        configured_root = os.environ.get("ECHO_DOCKER_DATA_ROOT_EXPECTED", "").strip() or None
    installer = hub_installer or HubDockerInstaller(
        HubCatalog.load(),
        client,
        docker_capacity_provider=lambda: _docker_storage_contract(
            client,
            mount_path=configured_mount,
            expected_root=configured_root,
        ),
    )
    inspector_catalog = getattr(installer, "catalog", None)
    if not isinstance(inspector_catalog, HubCatalog):
        inspector_catalog = HubCatalog.load()
    runtime_inspector = HubRuntimeInspector(inspector_catalog, client)
    server = ThreadingHTTPServer(
        (host, port),
        _handler_for(
            client,
            installer,
            runtime_inspector,
            docker_data_root_mount=configured_mount,
            expected_docker_root=configured_root,
        ),
    )
    server.daemon_threads = True
    return server


def _drop_socket_privileges(socket_path: Path, username: str) -> None:
    """Drop root while retaining only the host socket's numeric group."""

    socket_stat = socket_path.stat()
    if not stat.S_ISSOCK(socket_stat.st_mode):
        raise RuntimeError(f"Docker control target is not a Unix socket: {socket_path}")
    if os.geteuid() != 0:
        return

    import pwd

    try:
        account = pwd.getpwnam(username)
    except KeyError as exc:
        raise RuntimeError(f"Docker control proxy user not found: {username}") from exc
    # 清理补充组，仅保留必要 gid
    import contextlib

    with contextlib.suppress(OSError):
        os.initgroups(username, account.pw_gid)
    groups = sorted({account.pw_gid, socket_stat.st_gid})
    os.setgroups(groups)
    os.setgid(account.pw_gid)
    os.setuid(account.pw_uid)
    if os.geteuid() == 0:
        raise RuntimeError("Docker control proxy failed to drop root privileges")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host",
        # Internal Compose network only; compose publishes no proxy port.
        default="0.0.0.0",  # nosec B104
    )
    parser.add_argument("--port", type=int, default=2375)
    parser.add_argument(
        "--socket",
        type=Path,
        default=Path(os.environ.get("ECHO_DOCKER_SOCK") or "/var/run/docker.sock"),
    )
    parser.add_argument("--user", default=os.environ.get("ECHO_DOCKER_PROXY_USER") or "echo")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")

    _drop_socket_privileges(args.socket, args.user)
    client = DockerClient(
        socket_path=str(args.socket),
        base_url="",
        allow_direct_socket=True,
    )
    server = create_proxy_server(client, host=args.host, port=args.port)
    print(
        f"Echo Docker control proxy listening on {args.host}:{args.port} "
        f"as uid={os.geteuid()} gid={os.getegid()}",
        file=sys.stderr,
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:  # pragma: no cover - interactive shutdown
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
