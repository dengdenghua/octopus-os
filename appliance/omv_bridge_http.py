"""Bounded Unix-socket HTTP transport for the host OMV bridge."""

from __future__ import annotations

import json
import socketserver
import stat
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, urlsplit

from appliance.omv_bridge_errors import (
    OmvBridgeConflict,
    OmvBridgeError,
    OmvBridgeValidationError,
)
from appliance.omv_protocol import (
    GROUP_CONTROL_CAPABILITY,
    NFS_CONTROL_CAPABILITY,
    QUOTA_CONTROL_CAPABILITY,
    SHARE_PRIVILEGE_CONTROL_CAPABILITY,
    SHARED_FOLDER_CONTROL_CAPABILITY,
    SMB_CONTROL_CAPABILITY,
    USER_CONTROL_CAPABILITY,
    USER_PASSWORD_CONTROL_CAPABILITY,
)

MAX_REQUEST_BODY_BYTES = 64 * 1024


class OmvBridgeService(Protocol):
    """Explicit service surface exposed by the Unix-socket transport."""

    def filesystems(self) -> list[dict[str, Any]]: ...
    def smart_devices(self) -> list[dict[str, Any]]: ...
    def storage_topology(self) -> dict[str, Any]: ...
    def sharing_overview(self) -> dict[str, Any]: ...
    def share_privileges(self, share_uuid: str) -> list[dict[str, Any]]: ...
    def smart(self, devicefile: str) -> dict[str, Any]: ...
    def plan_group(self, desired: Any) -> dict[str, Any]: ...
    def apply_group(self, desired: Any, plan_id: Any) -> dict[str, Any]: ...
    def plan_user(self, desired: Any) -> dict[str, Any]: ...
    def apply_user(self, desired: Any, plan_id: Any) -> dict[str, Any]: ...
    def plan_user_password(self, desired: Any) -> dict[str, Any]: ...
    def apply_user_password(self, desired: Any, plan_id: Any) -> dict[str, Any]: ...
    def plan_shared_folder(self, desired: Any) -> dict[str, Any]: ...
    def apply_shared_folder(self, desired: Any, plan_id: Any) -> dict[str, Any]: ...
    def plan_share_privilege(self, desired: Any) -> dict[str, Any]: ...
    def apply_share_privilege(self, desired: Any, plan_id: Any) -> dict[str, Any]: ...
    def plan_smb_share(self, desired: Any) -> dict[str, Any]: ...
    def apply_smb_share(self, desired: Any, plan_id: Any) -> dict[str, Any]: ...
    def plan_nfs_share(self, desired: Any) -> dict[str, Any]: ...
    def apply_nfs_share(self, desired: Any, plan_id: Any) -> dict[str, Any]: ...
    def plan_filesystem_quota(self, desired: Any) -> dict[str, Any]: ...
    def apply_filesystem_quota(self, desired: Any, plan_id: Any) -> dict[str, Any]: ...


class _ThreadingUnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


class OmvBridgeHttpServer(_ThreadingUnixServer):
    def __init__(self, socket_path: str, service: OmvBridgeService) -> None:
        self.service = service
        super().__init__(socket_path, OmvBridgeRequestHandler)


class OmvBridgeRequestHandler(BaseHTTPRequestHandler):
    server: OmvBridgeHttpServer
    protocol_version = "HTTP/1.1"
    server_version = "EchoOmvBridge/1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _send_json(self, status_code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        parsed = urlsplit(self.path)
        try:
            if parsed.path == "/health" and not parsed.query:
                self._send_json(200, {"ok": True})
                return
            if parsed.path == "/v1/capabilities" and not parsed.query:
                self._send_json(
                    200,
                    {
                        "capabilities": [
                            SHARED_FOLDER_CONTROL_CAPABILITY,
                            SHARE_PRIVILEGE_CONTROL_CAPABILITY,
                            SMB_CONTROL_CAPABILITY,
                            NFS_CONTROL_CAPABILITY,
                            QUOTA_CONTROL_CAPABILITY,
                            GROUP_CONTROL_CAPABILITY,
                            USER_CONTROL_CAPABILITY,
                            USER_PASSWORD_CONTROL_CAPABILITY,
                        ]
                    },
                )
                return
            if parsed.path == "/v1/filesystems" and not parsed.query:
                self._send_json(
                    200,
                    {"filesystems": self.server.service.filesystems()},
                )
                return
            if parsed.path == "/v1/smart/devices" and not parsed.query:
                self._send_json(
                    200,
                    {"devices": self.server.service.smart_devices()},
                )
                return
            if parsed.path == "/v1/storage-topology" and not parsed.query:
                self._send_json(200, self.server.service.storage_topology())
                return
            if parsed.path == "/v1/sharing" and not parsed.query:
                self._send_json(200, self.server.service.sharing_overview())
                return
            if parsed.path == "/v1/sharing/privileges":
                query = parse_qs(parsed.query, keep_blank_values=True)
                values = query.get("uuid", [])
                if len(values) != 1:
                    self._send_json(400, {"error": "one UUID is required"})
                    return
                self._send_json(
                    200,
                    {"privileges": self.server.service.share_privileges(values[0])},
                )
                return
            if parsed.path == "/v1/smart":
                query = parse_qs(parsed.query, keep_blank_values=True)
                values = query.get("devicefile", [])
                if len(values) != 1:
                    self._send_json(400, {"error": "one devicefile is required"})
                    return
                self._send_json(200, {"smart": self.server.service.smart(values[0])})
                return
            self._send_json(404, {"error": "not found"})
        except OmvBridgeError as exc:
            self._send_json(502, {"error": str(exc)})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        parsed = urlsplit(self.path)
        if parsed.query or parsed.path not in {
            "/v1/sharing/folders/plan",
            "/v1/sharing/folders/apply",
            "/v1/sharing/privileges/plan",
            "/v1/sharing/privileges/apply",
            "/v1/sharing/smb/plan",
            "/v1/sharing/smb/apply",
            "/v1/sharing/nfs/plan",
            "/v1/sharing/nfs/apply",
            "/v1/quota/plan",
            "/v1/quota/apply",
            "/v1/accounts/groups/plan",
            "/v1/accounts/groups/apply",
            "/v1/accounts/users/plan",
            "/v1/accounts/users/apply",
            "/v1/accounts/users/password/plan",
            "/v1/accounts/users/password/apply",
        }:
            self._send_json(405, {"error": "mutation is not allowed"})
            return
        if self.headers.get("Transfer-Encoding") is not None:
            self._send_json(400, {"error": "chunked request bodies are not allowed"})
            return
        lengths = self.headers.get_all("Content-Length", failobj=[])
        if len(lengths) != 1 or not lengths[0].isdecimal():
            self._send_json(411, {"error": "one content length is required"})
            return
        length = int(lengths[0])
        if not 0 < length <= MAX_REQUEST_BODY_BYTES:
            self._send_json(413, {"error": "request body exceeds the safety limit"})
            return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().casefold()
        if content_type != "application/json":
            self._send_json(415, {"error": "application/json is required"})
            return
        try:
            body = json.loads(self.rfile.read(length))
        except (UnicodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "request body is invalid JSON"})
            return
        try:
            if parsed.path == "/v1/accounts/groups/plan":
                if not isinstance(body, dict) or set(body) != {"desired"}:
                    raise OmvBridgeValidationError("group plan request has unexpected fields")
                self._send_json(200, self.server.service.plan_group(body["desired"]))
                return
            if parsed.path == "/v1/accounts/groups/apply":
                if not isinstance(body, dict) or set(body) != {"desired", "planId"}:
                    raise OmvBridgeValidationError("group apply request has unexpected fields")
                self._send_json(
                    200,
                    self.server.service.apply_group(body["desired"], body["planId"]),
                )
                return
            if parsed.path == "/v1/accounts/users/plan":
                if not isinstance(body, dict) or set(body) != {"desired"}:
                    raise OmvBridgeValidationError("user plan request has unexpected fields")
                self._send_json(200, self.server.service.plan_user(body["desired"]))
                return
            if parsed.path == "/v1/accounts/users/apply":
                if not isinstance(body, dict) or set(body) != {"desired", "planId"}:
                    raise OmvBridgeValidationError("user apply request has unexpected fields")
                self._send_json(
                    200,
                    self.server.service.apply_user(body["desired"], body["planId"]),
                )
                return
            if parsed.path == "/v1/accounts/users/password/plan":
                if not isinstance(body, dict) or set(body) != {"desired"}:
                    raise OmvBridgeValidationError(
                        "user password plan request has unexpected fields"
                    )
                self._send_json(
                    200,
                    self.server.service.plan_user_password(body["desired"]),
                )
                return
            if parsed.path == "/v1/accounts/users/password/apply":
                if not isinstance(body, dict) or set(body) != {"desired", "planId"}:
                    raise OmvBridgeValidationError(
                        "user password apply request has unexpected fields"
                    )
                self._send_json(
                    200,
                    self.server.service.apply_user_password(body["desired"], body["planId"]),
                )
                return
            if parsed.path == "/v1/sharing/folders/plan":
                if not isinstance(body, dict) or set(body) != {"desired"}:
                    raise OmvBridgeValidationError(
                        "shared folder plan request has unexpected fields"
                    )
                self._send_json(200, self.server.service.plan_shared_folder(body["desired"]))
                return
            if parsed.path == "/v1/sharing/folders/apply":
                if not isinstance(body, dict) or set(body) != {"desired", "planId"}:
                    raise OmvBridgeValidationError(
                        "shared folder apply request has unexpected fields"
                    )
                self._send_json(
                    200,
                    self.server.service.apply_shared_folder(body["desired"], body["planId"]),
                )
                return
            if parsed.path == "/v1/sharing/privileges/plan":
                if not isinstance(body, dict) or set(body) != {"desired"}:
                    raise OmvBridgeValidationError(
                        "share privilege plan request has unexpected fields"
                    )
                self._send_json(200, self.server.service.plan_share_privilege(body["desired"]))
                return
            if parsed.path == "/v1/sharing/privileges/apply":
                if not isinstance(body, dict) or set(body) != {"desired", "planId"}:
                    raise OmvBridgeValidationError(
                        "share privilege apply request has unexpected fields"
                    )
                self._send_json(
                    200,
                    self.server.service.apply_share_privilege(body["desired"], body["planId"]),
                )
                return
            if parsed.path == "/v1/sharing/smb/plan":
                if not isinstance(body, dict) or set(body) != {"desired"}:
                    raise OmvBridgeValidationError("SMB plan request has unexpected fields")
                self._send_json(200, self.server.service.plan_smb_share(body["desired"]))
                return
            if parsed.path == "/v1/sharing/smb/apply":
                if not isinstance(body, dict) or set(body) != {"desired", "planId"}:
                    raise OmvBridgeValidationError("SMB apply request has unexpected fields")
                self._send_json(
                    200,
                    self.server.service.apply_smb_share(body["desired"], body["planId"]),
                )
                return
            if parsed.path == "/v1/sharing/nfs/plan":
                if not isinstance(body, dict) or set(body) != {"desired"}:
                    raise OmvBridgeValidationError("NFS plan request has unexpected fields")
                self._send_json(200, self.server.service.plan_nfs_share(body["desired"]))
                return
            if parsed.path == "/v1/sharing/nfs/apply":
                if not isinstance(body, dict) or set(body) != {"desired", "planId"}:
                    raise OmvBridgeValidationError("NFS apply request has unexpected fields")
                self._send_json(
                    200,
                    self.server.service.apply_nfs_share(body["desired"], body["planId"]),
                )
                return
            if parsed.path == "/v1/quota/plan":
                if not isinstance(body, dict) or set(body) != {"desired"}:
                    raise OmvBridgeValidationError("quota plan request has unexpected fields")
                self._send_json(
                    200,
                    self.server.service.plan_filesystem_quota(body["desired"]),
                )
                return
            if not isinstance(body, dict) or set(body) != {"desired", "planId"}:
                raise OmvBridgeValidationError("quota apply request has unexpected fields")
            self._send_json(
                200,
                self.server.service.apply_filesystem_quota(
                    body["desired"],
                    body["planId"],
                ),
            )
        except OmvBridgeValidationError as exc:
            self._send_json(422, {"error": str(exc)})
        except OmvBridgeConflict as exc:
            self._send_json(409, {"error": str(exc)})
        except OmvBridgeError as exc:
            self._send_json(502, {"error": str(exc)})


def create_server(socket_path: Path | str, service: OmvBridgeService) -> OmvBridgeHttpServer:
    path = Path(socket_path)
    if not path.is_absolute() or path.is_symlink():
        raise OmvBridgeError("OMV bridge socket path must be an absolute non-symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise OmvBridgeError("OMV bridge socket directory must not be a symlink")
    if path.exists():
        info = path.lstat()
        if not stat.S_ISSOCK(info.st_mode):
            raise OmvBridgeError("refusing to replace a non-socket bridge path")
        path.unlink()
    server = OmvBridgeHttpServer(str(path), service)
    path.chmod(0o660)
    return server


__all__ = [
    "MAX_REQUEST_BODY_BYTES",
    "OmvBridgeHttpServer",
    "OmvBridgeRequestHandler",
    "OmvBridgeService",
    "create_server",
]
