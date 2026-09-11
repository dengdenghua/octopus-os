"""Narrow privileged broker for native Echo OS storage write planning and apply.

The browser-facing Agent remains unprivileged. It asks this root-owned
Unix-socket service to calculate one of the explicitly paired ``plan``
functions, then completes its normal approval and audit envelope before asking
the broker to execute the matching ``apply`` function. There is deliberately
no TCP listener, shell command endpoint, import-by-name endpoint, or generic
file API here.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import socket
import socketserver
import stat
import struct
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

if os.name == "posix":
    import grp
    import pwd
else:  # pragma: no cover - the service is Linux-only; unit tests import it on Windows
    grp = None  # type: ignore[assignment]
    pwd = None  # type: ignore[assignment]

SCHEMA = "echo.native-storage-broker.v1"
HEALTH_SCHEMA = "echo.native-storage-broker.health.v1"
MAX_MESSAGE_BYTES = 1024 * 1024
MAX_PLAN_ID_BYTES = 256
DEFAULT_SOCKET = Path("/run/echo-storage-broker/broker.sock")
_OPERATION_PATTERN = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+")

# Values are fixed source-owned import targets.  Never accept a module or
# function name supplied by a client and import it dynamically.
APPLY_OPERATION_TARGETS: Mapping[str, tuple[str, str]] = {
    "btrfs_scrub_schedule_policy.apply_policy": (
        "appliance.btrfs_scrub_schedule_policy",
        "apply_policy",
    ),
    "btrfs_snapshot_lock_policy.apply_lock": (
        "appliance.btrfs_snapshot_lock_policy",
        "apply_lock",
    ),
    "btrfs_snapshot_schedule_policy.apply_policy": (
        "appliance.btrfs_snapshot_schedule_policy",
        "apply_policy",
    ),
    "disk_idle_policy.apply_policy": ("appliance.disk_idle_policy", "apply_policy"),
    "mdraid_check_schedule_policy.apply_policy": (
        "appliance.mdraid_check_schedule_policy",
        "apply_policy",
    ),
    "native_btrfs_snapshot.apply_snapshot": (
        "appliance.native_btrfs_snapshot",
        "apply_snapshot",
    ),
    "native_btrfs_snapshot.apply_snapshot_delete": (
        "appliance.native_btrfs_snapshot",
        "apply_snapshot_delete",
    ),
    "native_btrfs_snapshot.apply_snapshot_restore_copy": (
        "appliance.native_btrfs_snapshot",
        "apply_snapshot_restore_copy",
    ),
    "native_smart.apply_smart_self_test": (
        "appliance.native_smart",
        "apply_smart_self_test",
    ),
    "native_storage.apply_btrfs_raid1": (
        "appliance.native_storage",
        "apply_btrfs_raid1",
    ),
    "native_storage.apply_btrfs_replace": (
        "appliance.native_storage",
        "apply_btrfs_replace",
    ),
    "native_storage.apply_btrfs_scrub": (
        "appliance.native_storage",
        "apply_btrfs_scrub",
    ),
    "native_storage.apply_ext4_check": (
        "appliance.native_storage",
        "apply_ext4_check",
    ),
    "native_storage.apply_ext4_volume": (
        "appliance.native_storage",
        "apply_ext4_volume",
    ),
    "native_storage.apply_group": ("appliance.native_storage", "apply_group"),
    "native_storage.apply_mdraid1": ("appliance.native_storage", "apply_mdraid1"),
    "native_storage.apply_mdraid1_replace": (
        "appliance.native_storage",
        "apply_mdraid1_replace",
    ),
    "native_storage.apply_mdraid_check": (
        "appliance.native_storage",
        "apply_mdraid_check",
    ),
    "native_storage.apply_nfs": ("appliance.native_storage", "apply_nfs"),
    "native_storage.apply_nfs_remove": (
        "appliance.native_storage",
        "apply_nfs_remove",
    ),
    "native_storage.apply_quota": ("appliance.native_storage", "apply_quota"),
    "native_storage.apply_share_privilege": (
        "appliance.native_storage",
        "apply_share_privilege",
    ),
    "native_storage.apply_shared_folder": (
        "appliance.native_storage",
        "apply_shared_folder",
    ),
    "native_storage.apply_shared_folder_delete": (
        "appliance.native_storage",
        "apply_shared_folder_delete",
    ),
    "native_storage.apply_shared_folder_detach": (
        "appliance.native_storage",
        "apply_shared_folder_detach",
    ),
    "native_storage.apply_shared_folder_rename": (
        "appliance.native_storage",
        "apply_shared_folder_rename",
    ),
    "native_storage.apply_smb": ("appliance.native_storage", "apply_smb"),
    "native_storage.apply_user": ("appliance.native_storage", "apply_user"),
    "native_storage.apply_user_password": (
        "appliance.native_storage",
        "apply_user_password",
    ),
    "native_storage.apply_zfs_mirror": (
        "appliance.native_storage",
        "apply_zfs_mirror",
    ),
    "native_storage.apply_zfs_mirror_replace": (
        "appliance.native_storage",
        "apply_zfs_mirror_replace",
    ),
    "native_storage.apply_zfs_pool_export": (
        "appliance.native_storage",
        "apply_zfs_pool_export",
    ),
    "native_storage.apply_zfs_pool_import": (
        "appliance.native_storage",
        "apply_zfs_pool_import",
    ),
    "native_storage.apply_zfs_scrub": (
        "appliance.native_storage",
        "apply_zfs_scrub",
    ),
    "native_time_machine.apply_time_machine": (
        "appliance.native_time_machine",
        "apply_time_machine",
    ),
    "native_dlna.apply_dlna": ("appliance.native_dlna", "apply_dlna"),
    "native_webdav_control.apply_webdav": (
        "appliance.native_webdav_control",
        "apply_webdav",
    ),
    "nut_device_config.apply_config": ("appliance.nut_device_config", "apply_config"),
    "smart_schedule_policy.apply_policy": (
        "appliance.smart_schedule_policy",
        "apply_policy",
    ),
    "ups_shutdown_policy.apply_policy": (
        "appliance.ups_shutdown_policy",
        "apply_policy",
    ),
}

# Every write slice has a same-module ``plan_*`` peer. Derive that second
# allowlist from the explicit apply table so adding a write cannot accidentally
# expose apply without the root-side stale-plan calculation used to bind it.
PLAN_OPERATION_TARGETS: Mapping[str, tuple[str, str]] = {
    f"{operation.rsplit('.', 1)[0]}.{function.replace('apply', 'plan', 1)}": (
        module,
        function.replace("apply", "plan", 1),
    )
    for operation, (module, function) in APPLY_OPERATION_TARGETS.items()
}
READ_OPERATION_TARGETS: Mapping[str, tuple[str, str]] = {
    "native_storage.sharing_overview": (
        "appliance.native_storage",
        "sharing_overview",
    ),
    "native_storage.share_privileges": (
        "appliance.native_storage",
        "share_privileges",
    ),
}
SYSTEM_OPERATION_TARGETS: Mapping[str, tuple[str, str]] = {
    # No caller-provided ports, addresses, or rules cross this boundary. The
    # root side derives the complete desired set from the signed Hub catalog
    # and exact Docker ownership labels, then uses native_firewall's rollback.
    "native_hub_firewall.sync": ("appliance.native_hub_firewall", "sync"),
}
OPERATION_TARGETS: Mapping[str, tuple[str, str]] = {
    **READ_OPERATION_TARGETS,
    **SYSTEM_OPERATION_TARGETS,
    **PLAN_OPERATION_TARGETS,
    **APPLY_OPERATION_TARGETS,
}


class NativeStorageBrokerError(OSError):
    """The privileged storage broker was unavailable or rejected a request."""


def _strict_json_loads(raw: bytes) -> Any:
    def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("native storage broker JSON contains duplicate keys")
            value[key] = item
        return value

    def _constant(_value: str) -> None:
        raise ValueError("native storage broker JSON contains a non-finite number")

    try:
        return json.loads(raw, object_pairs_hook=_object, parse_constant=_constant)
    except UnicodeDecodeError as exc:
        raise ValueError("native storage broker JSON is not UTF-8") from exc


def operation_for_callable(function: Callable[..., Any]) -> str:
    """Return the source-owned operation id for one routed plan/apply function."""

    matches: list[str] = []
    for operation, (module_name, function_name) in OPERATION_TARGETS.items():
        target = getattr(importlib.import_module(module_name), function_name, None)
        if target is function:
            matches.append(operation)
    if len(matches) != 1:
        raise RuntimeError("native storage function is not broker-authorized or is ambiguous")
    return matches[0]


def _resolve_operation(operation: str) -> Callable[..., dict[str, Any]]:
    target = OPERATION_TARGETS.get(operation)
    if target is None:
        raise ValueError("native storage operation is not allowed")
    module_name, function_name = target
    function = getattr(importlib.import_module(module_name), function_name, None)
    if not callable(function) or operation_for_callable(function) != operation:
        raise RuntimeError("native storage operation target is unavailable")
    return function


def dispatch_request(request: Any) -> dict[str, Any]:
    """Validate and synchronously execute one bounded broker request."""

    if not isinstance(request, dict) or set(request) != {
        "schema",
        "requestId",
        "operation",
        "desired",
        "planId",
    }:
        raise ValueError("native storage broker request has an invalid shape")
    if request["schema"] != SCHEMA:
        raise ValueError("native storage broker request schema is unsupported")
    request_id = request["requestId"]
    operation = request["operation"]
    desired = request["desired"]
    plan_id = request["planId"]
    if not isinstance(request_id, str) or re.fullmatch(r"[0-9a-f]{32}", request_id) is None:
        raise ValueError("native storage broker request id is invalid")
    if not isinstance(operation, str) or _OPERATION_PATTERN.fullmatch(operation) is None:
        raise ValueError("native storage broker operation is invalid")
    if not isinstance(desired, dict):
        raise ValueError("native storage broker desired state must be an object")
    if (
        not isinstance(plan_id, str)
        or not plan_id
        or len(plan_id.encode("utf-8")) > MAX_PLAN_ID_BYTES
    ):
        raise ValueError("native storage broker plan id is invalid")
    if operation == "broker.health":
        if desired or plan_id != "probe":
            raise ValueError("native storage broker health request is invalid")
        return {
            "schema": HEALTH_SCHEMA,
            "status": "ok",
            "operationCount": len(OPERATION_TARGETS),
        }
    function = _resolve_operation(operation)
    if operation in SYSTEM_OPERATION_TARGETS:
        if desired or plan_id != "sync":
            raise ValueError("native system synchronization request is invalid")
        return function()
    if operation in READ_OPERATION_TARGETS:
        if plan_id != "read":
            raise ValueError("native storage broker read request is invalid")
        if operation == "native_storage.sharing_overview":
            if desired:
                raise ValueError("native storage broker overview request is invalid")
            return {"value": function()}
        if set(desired) != {"shareUuid"} or not isinstance(desired["shareUuid"], str):
            raise ValueError("native storage broker privilege request is invalid")
        if (
            re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", desired["shareUuid"])
            is None
        ):
            raise ValueError("native storage broker share UUID is invalid")
        return {"value": function(desired["shareUuid"])}
    if operation in PLAN_OPERATION_TARGETS:
        if plan_id != "preview":
            raise ValueError("native storage broker plan request is invalid")
        return function(desired)
    return function(desired, plan_id)


def _response(request_id: str, *, result: Any = None, error: dict[str, str] | None = None) -> bytes:
    value: dict[str, Any] = {
        "schema": SCHEMA,
        "requestId": request_id,
        "ok": error is None,
    }
    if error is None:
        value["result"] = result
    else:
        value["error"] = error
    encoded = (
        json.dumps(
            value,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )
    if len(encoded) > MAX_MESSAGE_BYTES:
        raise RuntimeError("native storage broker response exceeds the protocol limit")
    return encoded


def _peer_identity(connection: socket.socket) -> tuple[int, int, int]:
    if not sys.platform.startswith("linux") or not hasattr(socket, "SO_PEERCRED"):
        raise PermissionError("native storage broker requires Linux peer credentials")
    size = struct.calcsize("3i")
    return struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, size))


class _BrokerHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        self.connection.settimeout(120.0)
        request_id = "0" * 32
        try:
            _pid, uid, _gid = _peer_identity(self.connection)
            if uid not in self.server.allowed_uids:  # type: ignore[attr-defined]
                raise PermissionError("native storage broker peer is not authorized")
            raw = self.rfile.readline(MAX_MESSAGE_BYTES + 1)
            if not raw.endswith(b"\n") or len(raw) > MAX_MESSAGE_BYTES:
                raise ValueError("native storage broker request exceeds the protocol limit")
            request = _strict_json_loads(raw)
            if (
                isinstance(request, dict)
                and isinstance(request.get("requestId"), str)
                and re.fullmatch(r"[0-9a-f]{32}", request["requestId"]) is not None
            ):
                request_id = request["requestId"]
            result = dispatch_request(request)
            payload = _response(request_id, result=result)
        except (ValueError, json.JSONDecodeError) as exc:
            payload = _response(
                request_id,
                error={"type": "invalid-request", "message": str(exc)},
            )
        except PermissionError as exc:
            payload = _response(
                request_id,
                error={"type": "permission-denied", "message": str(exc)},
            )
        except OSError:
            payload = _response(
                request_id,
                error={"type": "operation-unavailable", "message": "storage operation failed"},
            )
        except Exception:
            payload = _response(
                request_id,
                error={"type": "internal-error", "message": "storage broker failed closed"},
            )
        self.wfile.write(payload)


_UnixStreamServer = getattr(socketserver, "UnixStreamServer", socketserver.TCPServer)


class NativeStorageBrokerServer(_UnixStreamServer):  # type: ignore[misc, valid-type]
    """Single-writer Unix server with a fixed peer UID allowlist."""

    def __init__(self, path: Path, *, allowed_uids: frozenset[int]) -> None:
        self.allowed_uids = allowed_uids
        super().__init__(str(path), _BrokerHandler)


def _read_response(connection: socket.socket) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = connection.recv(min(65536, MAX_MESSAGE_BYTES + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > MAX_MESSAGE_BYTES:
            raise NativeStorageBrokerError("native storage broker response is too large")
        if b"\n" in chunk:
            break
    raw = b"".join(chunks)
    if not raw.endswith(b"\n") or raw.count(b"\n") != 1:
        raise NativeStorageBrokerError("native storage broker response is incomplete")
    return raw


class NativeStorageBrokerClient:
    def __init__(self, path: Path | str, *, timeout: float = 120.0) -> None:
        self.path = Path(path)
        self.timeout = timeout

    def _validate_socket(self) -> None:
        if not self.path.is_absolute() or self.path.is_symlink():
            raise NativeStorageBrokerError("native storage broker socket path is unsafe")
        try:
            info = self.path.stat()
            parent = self.path.parent.stat()
        except OSError as exc:
            raise NativeStorageBrokerError("native storage broker socket is unavailable") from exc
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o007:
            raise NativeStorageBrokerError("native storage broker socket ownership is unsafe")
        if parent.st_uid != 0 or parent.st_mode & 0o022:
            raise NativeStorageBrokerError("native storage broker directory ownership is unsafe")

    def _exchange(
        self,
        operation: str,
        desired: dict[str, Any],
        plan_id: str,
        *,
        health: bool = False,
    ) -> dict[str, Any]:
        import uuid

        if operation not in OPERATION_TARGETS and not (health and operation == "broker.health"):
            raise NativeStorageBrokerError("native storage operation is not broker-authorized")
        request_id = uuid.uuid4().hex
        request = {
            "schema": SCHEMA,
            "requestId": request_id,
            "operation": operation,
            "desired": desired,
            "planId": plan_id,
        }
        try:
            raw = (
                json.dumps(
                    request,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                ).encode("utf-8")
                + b"\n"
            )
        except (TypeError, ValueError) as exc:
            raise NativeStorageBrokerError(
                "native storage broker request is not JSON-safe"
            ) from exc
        if len(raw) > MAX_MESSAGE_BYTES:
            raise NativeStorageBrokerError("native storage broker request is too large")
        if not hasattr(socket, "AF_UNIX"):
            raise NativeStorageBrokerError("native storage broker requires Unix sockets")
        self._validate_socket()
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(self.timeout)
        try:
            connection.connect(str(self.path))
            connection.sendall(raw)
            connection.shutdown(socket.SHUT_WR)
            response_raw = _read_response(connection)
        except OSError as exc:
            raise NativeStorageBrokerError("native storage broker is unavailable") from exc
        finally:
            connection.close()
        try:
            response = _strict_json_loads(response_raw)
        except (ValueError, json.JSONDecodeError) as exc:
            raise NativeStorageBrokerError("native storage broker response is invalid") from exc
        if not isinstance(response, dict) or response.get("schema") != SCHEMA:
            raise NativeStorageBrokerError("native storage broker response schema is invalid")
        if response.get("requestId") != request_id or not isinstance(response.get("ok"), bool):
            raise NativeStorageBrokerError("native storage broker response identity is invalid")
        if response["ok"] is not True:
            if set(response) != {"schema", "requestId", "ok", "error"}:
                raise NativeStorageBrokerError("native storage broker error response is invalid")
            error = response.get("error")
            message = error.get("message") if isinstance(error, dict) else None
            raise NativeStorageBrokerError(
                str(message or "native storage broker rejected the request")
            )
        if set(response) != {"schema", "requestId", "ok", "result"}:
            raise NativeStorageBrokerError("native storage broker success response is invalid")
        result = response.get("result")
        if not isinstance(result, dict):
            raise NativeStorageBrokerError("native storage broker result is invalid")
        return result

    def call(self, operation: str, desired: dict[str, Any], plan_id: str) -> dict[str, Any]:
        if operation not in APPLY_OPERATION_TARGETS:
            raise NativeStorageBrokerError(
                "native storage apply operation is not broker-authorized"
            )
        return self._exchange(operation, desired, plan_id)

    def plan(self, operation: str, desired: dict[str, Any]) -> dict[str, Any]:
        if operation not in PLAN_OPERATION_TARGETS:
            raise NativeStorageBrokerError("native storage plan operation is not broker-authorized")
        return self._exchange(operation, desired, "preview")

    def read(self, operation: str, desired: dict[str, Any]) -> Any:
        if operation not in READ_OPERATION_TARGETS:
            raise NativeStorageBrokerError("native storage read operation is not broker-authorized")
        result = self._exchange(operation, desired, "read")
        if set(result) != {"value"}:
            raise NativeStorageBrokerError("native storage broker read result is invalid")
        return result["value"]

    def system(self, operation: str) -> dict[str, Any]:
        if operation not in SYSTEM_OPERATION_TARGETS:
            raise NativeStorageBrokerError("native system synchronization is not broker-authorized")
        return self._exchange(operation, {}, "sync")

    def probe(self) -> dict[str, Any]:
        result = self._exchange("broker.health", {}, "probe", health=True)
        if result != {
            "schema": HEALTH_SCHEMA,
            "status": "ok",
            "operationCount": len(OPERATION_TARGETS),
        }:
            raise NativeStorageBrokerError("native storage broker health result is invalid")
        return result


def _safe_socket_path(path: Path) -> None:
    if not path.is_absolute() or path.name in {"", ".", ".."}:
        raise RuntimeError("native storage broker socket path must be absolute")
    parent = path.parent
    info = parent.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise RuntimeError("native storage broker runtime directory is unsafe")
    if path.exists() or path.is_symlink():
        socket_info = path.lstat()
        if not stat.S_ISSOCK(socket_info.st_mode) or socket_info.st_uid != 0:
            raise RuntimeError("native storage broker refuses an unsafe stale socket")
        path.unlink()


def serve(path: Path, *, users: Sequence[str], group: str) -> None:
    if grp is None or pwd is None or not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise RuntimeError("native storage broker must start as root")
    if not users or any(not user.strip() for user in users):
        raise RuntimeError("native storage broker requires an allowed user")
    allowed_uids = frozenset({0, *(pwd.getpwnam(user).pw_uid for user in users)})
    gid = grp.getgrnam(group).gr_gid
    _safe_socket_path(path)
    server = NativeStorageBrokerServer(path, allowed_uids=allowed_uids)
    identity = path.stat()
    try:
        os.chown(path, 0, gid, follow_symlinks=False)
        path.chmod(0o660, follow_symlinks=False)
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
        try:
            current = path.lstat()
            if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                path.unlink()
        except FileNotFoundError:
            pass


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", type=Path, default=DEFAULT_SOCKET)
    parser.add_argument("--user", action="append", dest="users")
    parser.add_argument("--group", default="echo")
    args = parser.parse_args(argv)
    try:
        serve(args.socket, users=args.users or ["echo"], group=args.group)
    except (KeyError, OSError, RuntimeError) as exc:
        print(f"Echo native storage broker failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
