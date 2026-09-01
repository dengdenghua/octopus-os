"""Docker Engine API 的最小客户端(unix socket / TCP)。

刻意不引入 docker-py:启动器只需要 list/start/stop 三个调用,
httpx(runtime 既有依赖)走 UDS 即可,少一个重型依赖。
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import secrets
import tarfile
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlsplit

import httpx

from appliance.hub.progress import (
    HUB_STREAM_SCHEMA,
    HubProgressCallback,
    hub_progress,
    validate_hub_progress,
)
from appliance.hub.runtime import validate_hub_runtime

DEFAULT_SOCKET = "/var/run/docker.sock"
_HUB_APP_ID = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_PLAN_ID = re.compile(r"^[0-9a-f]{64}$")
_VOLUME_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,254}$")
_SECRET_FILE_NAME = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_DATA_COPY_PROVIDER_LABEL = "sh.echo.hub.data-copy-provider"
_NAS_PROVIDER_LABEL = "sh.echo.hub.nas-provider"
DOCKER_STORAGE_SCHEMA = "echo.hub.docker-storage.v1"
_DATA_COPY_SCRIPT = """\
import os
import shutil
import subprocess

destination = "/destination"
if os.environ.get("ECHO_CLEAR_DESTINATION") == "1":
    for entry in os.scandir(destination):
        path = entry.path
        if entry.is_dir(follow_symlinks=False):
            shutil.rmtree(path)
        else:
            os.unlink(path)
subprocess.run(["/bin/cp", "-a", "/source/.", "/destination/"], check=True)
"""
_NAS_DIRECTORY_SCRIPT = """\
import os
import sys

root_fd = os.open("/nas", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    current_fd = root_fd
    for segment in sys.argv[1].split("/"):
        created = False
        try:
            os.mkdir(segment, mode=0o770, dir_fd=current_fd)
            created = True
        except FileExistsError:
            pass
        next_fd = os.open(
            segment,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=current_fd,
        )
        if created:
            parent = os.fstat(current_fd)
            os.fchown(next_fd, parent.st_uid, parent.st_gid)
            os.fchmod(next_fd, 0o770)
        if current_fd != root_fd:
            os.close(current_fd)
        current_fd = next_fd
    if current_fd != root_fd:
        os.close(current_fd)
finally:
    os.close(root_fd)
"""


class DockerUnavailable(RuntimeError):
    """Docker socket 不存在或 Engine API 不可达。"""


class DockerControlDenied(DockerUnavailable):
    """The narrow proxy refused control of a protected appliance container."""


class DockerConflict(DockerUnavailable):
    """The verified Docker operation no longer matches current engine state."""


def _normalized_base_url(value: str | None) -> str | None:
    configured = str(value or "").strip()
    if not configured:
        return None
    parsed = urlsplit(configured)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise DockerUnavailable(
            "ECHO_DOCKER_HOST must be an http(s) origin without credentials or a path"
        )
    return configured.rstrip("/")


class DockerClient:
    """同步客户端;FastAPI 路由经 anyio 线程池调用,不阻塞事件循环。

    ``base_url`` / ``ECHO_DOCKER_HOST`` 指定时走受限 HTTP 控制代理；否则走
    unix socket（仅供代理进程和显式本地开发，可用 ``ECHO_DOCKER_SOCK`` 覆盖）。
    """

    def __init__(
        self,
        socket_path: str | None = None,
        base_url: str | None = None,
        timeout: float = 5.0,
        allow_direct_socket: bool | None = None,
        proxy_token: str | None = None,
    ) -> None:
        self._socket_path = socket_path or os.environ.get("ECHO_DOCKER_SOCK") or DEFAULT_SOCKET
        configured_base = base_url if base_url is not None else os.environ.get("ECHO_DOCKER_HOST")
        self._base_url = _normalized_base_url(configured_base)
        self._timeout = timeout
        configured_token = (
            proxy_token
            if proxy_token is not None
            else os.environ.get("ECHO_DOCKER_PROXY_TOKEN", "")
        )
        self._proxy_token = str(configured_token or "").strip()
        if self._proxy_token and not 32 <= len(self._proxy_token) <= 512:
            raise DockerUnavailable("ECHO_DOCKER_PROXY_TOKEN must contain 32-512 characters")
        self._allow_direct_socket = (
            os.environ.get("ECHO_APPLIANCE") != "1"
            if allow_direct_socket is None
            else allow_direct_socket
        )

    def _client(self) -> httpx.Client:
        if self._base_url:
            headers = {"X-Echo-Proxy-Token": self._proxy_token} if self._proxy_token else None
            return httpx.Client(
                base_url=self._base_url,
                timeout=self._timeout,
                headers=headers,
            )
        if not self._allow_direct_socket:
            raise DockerUnavailable(
                "direct Docker socket access is disabled in appliance mode; "
                "configure ECHO_DOCKER_HOST"
            )
        if not os.path.exists(self._socket_path):
            raise DockerUnavailable(f"docker socket not found: {self._socket_path}")
        transport = httpx.HTTPTransport(uds=self._socket_path)
        return httpx.Client(transport=transport, base_url="http://docker", timeout=self._timeout)

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            with self._client() as client:
                response = client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise DockerUnavailable("docker control endpoint is unreachable") from exc
        return response

    @staticmethod
    def _require_success(response: httpx.Response, allowed: set[int]) -> None:
        if response.status_code in allowed:
            return
        if response.status_code == 403:
            raise DockerControlDenied("protected appliance container cannot be controlled")
        if response.status_code == 409:
            raise DockerConflict("verified Docker operation conflicts with current engine state")
        raise DockerUnavailable(f"docker control endpoint returned HTTP {response.status_code}")

    def ping(self) -> bool:
        try:
            return self._request("GET", "/_ping").status_code == 200
        except DockerUnavailable:
            return False

    def list_containers(self, include_stopped: bool = True) -> list[dict[str, Any]]:
        response = self._request(
            "GET",
            "/containers/json",
            params={"all": "true" if include_stopped else "false"},
        )
        self._require_success(response, {200})
        try:
            payload = response.json()
        except ValueError as exc:
            raise DockerUnavailable("docker control endpoint returned invalid JSON") from exc
        if not isinstance(payload, list):
            raise DockerUnavailable("unexpected /containers/json payload")
        return payload

    def hub_storage_capacity(self) -> dict[str, Any]:
        """Read the proxy's sanitized Docker data-filesystem capacity contract."""

        response = self._request("GET", "/hub/storage")
        self._require_success(response, {200})
        try:
            payload = response.json()
        except ValueError as exc:
            raise DockerUnavailable("Docker storage endpoint returned invalid JSON") from exc
        if not isinstance(payload, dict) or set(payload) != {
            "schema",
            "status",
            "totalBytes",
            "freeBytes",
            "usedPercent",
        }:
            raise DockerUnavailable("Docker storage endpoint returned an invalid result")
        status = payload.get("status")
        total = payload.get("totalBytes")
        free = payload.get("freeBytes")
        used_percent = payload.get("usedPercent")
        if payload.get("schema") != DOCKER_STORAGE_SCHEMA or status not in {
            "observed",
            "unavailable",
            "mismatch",
        }:
            raise DockerUnavailable("Docker storage endpoint returned an invalid result")
        if status == "observed":
            if (
                not isinstance(total, int)
                or isinstance(total, bool)
                or total <= 0
                or not isinstance(free, int)
                or isinstance(free, bool)
                or not 0 <= free <= total
                or not isinstance(used_percent, (int, float))
                or isinstance(used_percent, bool)
                or not 0 <= used_percent <= 100
            ):
                raise DockerUnavailable("Docker storage endpoint returned invalid capacity")
        elif total is not None or free is not None or used_percent is not None:
            raise DockerUnavailable("Docker storage endpoint leaked unverified capacity")
        return payload

    def hub_app_runtime(self, app_id: str) -> dict[str, Any]:
        """Read one catalog-owned app's sanitized sidecar health projection."""

        if _HUB_APP_ID.fullmatch(app_id) is None:
            raise DockerUnavailable("invalid Hub app id")
        response = self._request("GET", f"/hub/apps/{app_id}/runtime")
        self._require_success(response, {200})
        try:
            payload = response.json()
        except ValueError as exc:
            raise DockerUnavailable("Hub runtime endpoint returned invalid JSON") from exc
        try:
            return validate_hub_runtime(payload)
        except ValueError as exc:
            raise DockerUnavailable("Hub runtime endpoint returned an invalid result") from exc

    def docker_root_dir(self) -> str:
        """Read DockerRootDir for the privileged proxy's local consistency check only."""

        response = self._request("GET", "/info")
        self._require_success(response, {200})
        try:
            payload = response.json()
        except ValueError as exc:
            raise DockerUnavailable("Docker info returned invalid JSON") from exc
        raw = payload.get("DockerRootDir") if isinstance(payload, dict) else None
        if not isinstance(raw, str) or not 1 <= len(raw) <= 512 or "\x00" in raw:
            raise DockerUnavailable("Docker info omitted its data root")
        path = PurePosixPath(raw)
        if not path.is_absolute() or ".." in path.parts or str(path) != raw or raw == "/":
            raise DockerUnavailable("Docker info returned an unsafe data root")
        return raw

    def start(self, container_id: str) -> None:
        response = self._request("POST", f"/containers/{container_id}/start")
        # 304 = already started — treat as success for launcher purposes.
        self._require_success(response, {204, 304})

    def stop(self, container_id: str) -> None:
        response = self._request("POST", f"/containers/{container_id}/stop")
        self._require_success(response, {204, 304})

    def install_hub_app(
        self,
        app_id: str,
        *,
        plan_id: str,
        catalog_digest: str,
    ) -> dict[str, Any]:
        """Ask the bounded proxy to install one catalog-defined Hub app.

        The payload contains identities only.  Image, ports, volumes and
        environment are independently resolved by the privileged proxy from
        its own trusted catalog; callers cannot submit Docker configuration.
        """

        if _HUB_APP_ID.fullmatch(app_id) is None:
            raise DockerUnavailable("invalid Hub app id")
        if _PLAN_ID.fullmatch(plan_id) is None or _PLAN_ID.fullmatch(catalog_digest) is None:
            raise DockerUnavailable("invalid Hub install identity")
        response = self._request(
            "POST",
            f"/hub/apps/{app_id}/install",
            json={"planId": plan_id, "catalogDigest": catalog_digest},
        )
        self._require_success(response, {200, 201})
        try:
            payload = response.json()
        except ValueError as exc:
            raise DockerUnavailable("Hub installer returned invalid JSON") from exc
        if not isinstance(payload, dict) or payload.get("schema") != "echo.hub.install-result.v1":
            raise DockerUnavailable("Hub installer returned an invalid result")
        return payload

    def install_hub_app_with_progress(
        self,
        app_id: str,
        *,
        plan_id: str,
        catalog_digest: str,
        progress: HubProgressCallback,
    ) -> dict[str, Any]:
        return self._stream_hub_app(
            "install",
            app_id,
            plan_id=plan_id,
            catalog_digest=catalog_digest,
            progress=progress,
        )

    def uninstall_hub_app(
        self,
        app_id: str,
        *,
        plan_id: str,
        catalog_digest: str,
    ) -> dict[str, Any]:
        """Remove one verified Hub container while retaining all data volumes."""

        if _HUB_APP_ID.fullmatch(app_id) is None:
            raise DockerUnavailable("invalid Hub app id")
        if _PLAN_ID.fullmatch(plan_id) is None or _PLAN_ID.fullmatch(catalog_digest) is None:
            raise DockerUnavailable("invalid Hub uninstall identity")
        response = self._request(
            "POST",
            f"/hub/apps/{app_id}/uninstall",
            json={"planId": plan_id, "catalogDigest": catalog_digest},
        )
        self._require_success(response, {200})
        try:
            payload = response.json()
        except ValueError as exc:
            raise DockerUnavailable("Hub uninstaller returned invalid JSON") from exc
        if not isinstance(payload, dict) or payload.get("schema") != "echo.hub.uninstall-result.v1":
            raise DockerUnavailable("Hub uninstaller returned an invalid result")
        return payload

    def uninstall_hub_app_with_progress(
        self,
        app_id: str,
        *,
        plan_id: str,
        catalog_digest: str,
        progress: HubProgressCallback,
    ) -> dict[str, Any]:
        return self._stream_hub_app(
            "uninstall",
            app_id,
            plan_id=plan_id,
            catalog_digest=catalog_digest,
            progress=progress,
        )

    def update_hub_app(
        self,
        app_id: str,
        *,
        plan_id: str,
        catalog_digest: str,
    ) -> dict[str, Any]:
        """Replace one verified Hub container while retaining its data volumes."""

        if _HUB_APP_ID.fullmatch(app_id) is None:
            raise DockerUnavailable("invalid Hub app id")
        if _PLAN_ID.fullmatch(plan_id) is None or _PLAN_ID.fullmatch(catalog_digest) is None:
            raise DockerUnavailable("invalid Hub update identity")
        response = self._request(
            "POST",
            f"/hub/apps/{app_id}/update",
            json={"planId": plan_id, "catalogDigest": catalog_digest},
        )
        self._require_success(response, {200})
        try:
            payload = response.json()
        except ValueError as exc:
            raise DockerUnavailable("Hub updater returned invalid JSON") from exc
        if not isinstance(payload, dict) or payload.get("schema") != "echo.hub.update-result.v1":
            raise DockerUnavailable("Hub updater returned an invalid result")
        return payload

    def update_hub_app_with_progress(
        self,
        app_id: str,
        *,
        plan_id: str,
        catalog_digest: str,
        progress: HubProgressCallback,
    ) -> dict[str, Any]:
        return self._stream_hub_app(
            "update",
            app_id,
            plan_id=plan_id,
            catalog_digest=catalog_digest,
            progress=progress,
        )

    def _control_hub_app(
        self,
        operation: str,
        app_id: str,
        *,
        plan_id: str,
        catalog_digest: str,
    ) -> dict[str, Any]:
        if operation not in {"start", "stop", "restart"}:
            raise DockerUnavailable("invalid Hub control operation")
        if _HUB_APP_ID.fullmatch(app_id) is None:
            raise DockerUnavailable("invalid Hub app id")
        if _PLAN_ID.fullmatch(plan_id) is None or _PLAN_ID.fullmatch(catalog_digest) is None:
            raise DockerUnavailable("invalid Hub control identity")
        response = self._request(
            "POST",
            f"/hub/apps/{app_id}/{operation}",
            json={"planId": plan_id, "catalogDigest": catalog_digest},
        )
        self._require_success(response, {200})
        try:
            payload = response.json()
        except ValueError as exc:
            raise DockerUnavailable("Hub controller returned invalid JSON") from exc
        if (
            not isinstance(payload, dict)
            or payload.get("schema") != f"echo.hub.{operation}-result.v1"
        ):
            raise DockerUnavailable("Hub controller returned an invalid result")
        return payload

    def start_hub_app(self, app_id: str, *, plan_id: str, catalog_digest: str) -> dict[str, Any]:
        return self._control_hub_app(
            "start", app_id, plan_id=plan_id, catalog_digest=catalog_digest
        )

    def stop_hub_app(self, app_id: str, *, plan_id: str, catalog_digest: str) -> dict[str, Any]:
        return self._control_hub_app("stop", app_id, plan_id=plan_id, catalog_digest=catalog_digest)

    def restart_hub_app(self, app_id: str, *, plan_id: str, catalog_digest: str) -> dict[str, Any]:
        return self._control_hub_app(
            "restart", app_id, plan_id=plan_id, catalog_digest=catalog_digest
        )

    def start_hub_app_with_progress(
        self,
        app_id: str,
        *,
        plan_id: str,
        catalog_digest: str,
        progress: HubProgressCallback,
    ) -> dict[str, Any]:
        return self._stream_hub_app(
            "start",
            app_id,
            plan_id=plan_id,
            catalog_digest=catalog_digest,
            progress=progress,
        )

    def stop_hub_app_with_progress(
        self,
        app_id: str,
        *,
        plan_id: str,
        catalog_digest: str,
        progress: HubProgressCallback,
    ) -> dict[str, Any]:
        return self._stream_hub_app(
            "stop",
            app_id,
            plan_id=plan_id,
            catalog_digest=catalog_digest,
            progress=progress,
        )

    def restart_hub_app_with_progress(
        self,
        app_id: str,
        *,
        plan_id: str,
        catalog_digest: str,
        progress: HubProgressCallback,
    ) -> dict[str, Any]:
        return self._stream_hub_app(
            "restart",
            app_id,
            plan_id=plan_id,
            catalog_digest=catalog_digest,
            progress=progress,
        )

    def _stream_hub_app(
        self,
        operation: str,
        app_id: str,
        *,
        plan_id: str,
        catalog_digest: str,
        progress: HubProgressCallback,
    ) -> dict[str, Any]:
        if operation not in {"install", "update", "uninstall", "start", "stop", "restart"}:
            raise DockerUnavailable("invalid Hub operation")
        if _HUB_APP_ID.fullmatch(app_id) is None:
            raise DockerUnavailable("invalid Hub app id")
        if _PLAN_ID.fullmatch(plan_id) is None or _PLAN_ID.fullmatch(catalog_digest) is None:
            raise DockerUnavailable("invalid Hub operation identity")
        timeout = httpx.Timeout(
            connect=self._timeout,
            read=None,
            write=self._timeout,
            pool=self._timeout,
        )
        expected_schema = f"echo.hub.{operation}-result.v1"
        result: dict[str, Any] | None = None
        try:
            with (
                self._client() as client,
                client.stream(
                    "POST",
                    f"/hub/apps/{app_id}/{operation}/stream",
                    json={"planId": plan_id, "catalogDigest": catalog_digest},
                    timeout=timeout,
                ) as response,
            ):
                self._require_success(response, {200})
                for line in response.iter_lines():
                    if not line or len(line.encode("utf-8")) > 256 * 1024:
                        continue
                    try:
                        envelope = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise DockerUnavailable(
                            "Hub operation stream returned invalid JSON"
                        ) from exc
                    if (
                        not isinstance(envelope, dict)
                        or envelope.get("schema") != HUB_STREAM_SCHEMA
                    ):
                        raise DockerUnavailable("Hub operation stream returned an invalid event")
                    event_type = envelope.get("type")
                    if event_type == "progress" and set(envelope) == {
                        "schema",
                        "type",
                        "progress",
                    }:
                        progress(validate_hub_progress(envelope.get("progress")))
                    elif event_type == "result" and set(envelope) == {
                        "schema",
                        "type",
                        "result",
                    }:
                        candidate = envelope.get("result")
                        if (
                            not isinstance(candidate, dict)
                            or candidate.get("schema") != expected_schema
                        ):
                            raise DockerUnavailable(
                                "Hub operation stream returned an invalid result"
                            )
                        result = candidate
                    elif event_type == "error" and set(envelope) == {
                        "schema",
                        "type",
                        "code",
                    }:
                        code = envelope.get("code")
                        if code == "CONFLICT":
                            raise DockerConflict(
                                "verified Docker operation conflicts with current engine state"
                            )
                        if code == "DENIED":
                            raise DockerControlDenied("Hub operation was denied")
                        raise DockerUnavailable("Hub installer is unavailable")
                    else:
                        raise DockerUnavailable("Hub operation stream returned an invalid event")
        except httpx.HTTPError as exc:
            raise DockerUnavailable("docker control endpoint is unreachable") from exc
        if result is None:
            raise DockerUnavailable("Hub operation stream ended before its result")
        return result

    # The methods below are used only by the privileged, catalog-verifying
    # sidecar.  They are not forwarded as HTTP routes to the main Echo process.
    def pull_image(self, image: str) -> None:
        self.pull_image_with_progress(image, None)

    def pull_image_with_progress(
        self,
        image: str,
        progress: HubProgressCallback | None,
    ) -> None:
        timeout = httpx.Timeout(
            connect=self._timeout,
            read=None,
            write=self._timeout,
            pool=self._timeout,
        )
        layers: set[str] = set()
        completed: set[str] = set()
        last_reported: tuple[int, int] | None = None
        try:
            with (
                self._client() as client,
                client.stream(
                    "POST",
                    "/images/create",
                    params={"fromImage": image},
                    timeout=timeout,
                ) as response,
            ):
                self._require_success(response, {200})
                for line in response.iter_lines():
                    if not line or len(line.encode("utf-8")) > 64 * 1024:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise DockerUnavailable("Docker image pull returned invalid JSON") from exc
                    if not isinstance(event, dict):
                        raise DockerUnavailable("Docker image pull returned an invalid event")
                    if event.get("error"):
                        raise DockerUnavailable("Docker image pull failed")
                    layer_id = event.get("id")
                    status = event.get("status")
                    if (
                        isinstance(layer_id, str)
                        and re.fullmatch(r"[0-9a-f]{6,64}", layer_id) is not None
                        and isinstance(status, str)
                    ):
                        layers.add(layer_id)
                        if status in {"Already exists", "Pull complete"}:
                            completed.add(layer_id)
                        current_state = (min(len(completed), len(layers)), len(layers))
                        if progress is not None and current_state != last_reported:
                            progress(
                                hub_progress(
                                    "pulling",
                                    "pulling-image",
                                    completed=current_state[0],
                                    total=current_state[1],
                                    unit="layers",
                                )
                            )
                            last_reported = current_state
        except httpx.HTTPError as exc:
            raise DockerUnavailable("docker control endpoint is unreachable") from exc
        if progress is not None:
            if layers:
                if last_reported != (len(layers), len(layers)):
                    progress(
                        hub_progress(
                            "pulling",
                            "pulling-image",
                            completed=len(layers),
                            total=len(layers),
                            unit="layers",
                        )
                    )
            else:
                progress(
                    hub_progress(
                        "pulling",
                        "pulling-image",
                        completed=1,
                        total=1,
                        unit="images",
                    )
                )

    def inspect_container(self, container_id: str) -> dict[str, Any] | None:
        response = self._request("GET", f"/containers/{container_id}/json")
        if response.status_code == 404:
            return None
        self._require_success(response, {200})
        try:
            payload = response.json()
        except ValueError as exc:
            raise DockerUnavailable("Docker inspect returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise DockerUnavailable("Docker inspect returned an invalid result")
        return payload

    def container_stats(self, container_id: str) -> dict[str, Any]:
        """Read one-shot raw stats inside the privileged sidecar only."""

        response = self._request(
            "GET",
            f"/containers/{container_id}/stats",
            params={"stream": "false"},
        )
        self._require_success(response, {200})
        try:
            payload = response.json()
        except ValueError as exc:
            raise DockerUnavailable("Docker stats returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise DockerUnavailable("Docker stats returned an invalid result")
        return payload

    def inspect_volume(self, name: str) -> dict[str, Any] | None:
        self._require_volume_name(name)
        response = self._request("GET", f"/volumes/{name}")
        if response.status_code == 404:
            return None
        self._require_success(response, {200})
        try:
            payload = response.json()
        except ValueError as exc:
            raise DockerUnavailable("Docker volume inspect returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise DockerUnavailable("Docker volume inspect returned an invalid result")
        return payload

    def create_volume(self, name: str, *, labels: dict[str, str]) -> bool:
        self._require_volume_name(name)
        existing = self._request("GET", f"/volumes/{name}")
        if existing.status_code == 200:
            try:
                payload = existing.json()
            except ValueError as exc:
                raise DockerUnavailable("Docker volume inspect returned invalid JSON") from exc
            existing_labels = payload.get("Labels") if isinstance(payload, dict) else None
            if not isinstance(existing_labels, dict) or any(
                existing_labels.get(key) != value for key, value in labels.items()
            ):
                raise DockerControlDenied(
                    "existing Hub data volume does not belong to this verified install plan"
                )
            return False
        if existing.status_code != 404:
            self._require_success(existing, {200})
        response = self._request(
            "POST",
            "/volumes/create",
            json={"Name": name, "Driver": "local", "Labels": labels},
        )
        self._require_success(response, {201})
        return True

    def create_network(
        self,
        name: str,
        *,
        internal: bool,
        labels: dict[str, str],
    ) -> bool:
        self._require_network_name(name)
        existing = self._request("GET", f"/networks/{name}")
        if existing.status_code == 200:
            try:
                payload = existing.json()
            except ValueError as exc:
                raise DockerUnavailable("Docker network inspect returned invalid JSON") from exc
            existing_labels = payload.get("Labels") if isinstance(payload, dict) else None
            if (
                not isinstance(payload, dict)
                or payload.get("Driver") != "bridge"
                or payload.get("Internal") is not internal
                or not isinstance(existing_labels, dict)
                or any(existing_labels.get(key) != value for key, value in labels.items())
            ):
                raise DockerControlDenied(
                    "existing Hub network does not belong to this verified install plan"
                )
            return False
        if existing.status_code != 404:
            self._require_success(existing, {200})
        response = self._request(
            "POST",
            "/networks/create",
            json={
                "Name": name,
                "Driver": "bridge",
                "Internal": internal,
                "Attachable": False,
                "CheckDuplicate": True,
                "Labels": labels,
            },
        )
        self._require_success(response, {201})
        return True

    def remove_network(self, name: str) -> None:
        self._require_network_name(name)
        response = self._request("DELETE", f"/networks/{name}")
        self._require_success(response, {204, 404})

    def write_secret_volume(self, volume: str, files: dict[str, bytes]) -> None:
        """Write bounded generated secrets without placing values in container config."""

        self._require_volume_name(volume)
        self._validate_secret_files(files)
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w", format=tarfile.USTAR_FORMAT) as output:
            for name, content in sorted(files.items()):
                member = tarfile.TarInfo(name=name)
                member.size = len(content)
                member.mode = 0o444
                member.uid = 0
                member.gid = 0
                member.mtime = 0
                output.addfile(member, io.BytesIO(content))
        helper_id = self._secret_volume_helper(volume)
        try:
            response = self._request(
                "PUT",
                f"/containers/{helper_id}/archive",
                params={"path": "/secrets"},
                headers={"Content-Type": "application/x-tar"},
                content=archive.getvalue(),
            )
            self._require_success(response, {200})
            self._verify_secret_files(helper_id, files)
        finally:
            self.remove_container(helper_id, force=True)

    def verify_secret_volume(self, volume: str, file_names: tuple[str, ...]) -> None:
        self._require_volume_name(volume)
        files = {name: b"x" for name in file_names}
        self._validate_secret_files(files)
        helper_id = self._secret_volume_helper(volume)
        try:
            self._verify_secret_files(helper_id, files)
        finally:
            self.remove_container(helper_id, force=True)

    def ensure_nas_subdirectory(self, relative_path: str) -> str:
        self._require_nas_relative_path(relative_path)
        nas_source = self._nas_source()
        helper_id: str | None = None
        try:
            helper_id = self.create_container(
                f"echo-hub-nas-dir-{secrets.token_hex(8)}",
                {
                    "Image": self._data_copy_image(),
                    "Entrypoint": ["python", "-c"],
                    "Cmd": [_NAS_DIRECTORY_SCRIPT, relative_path],
                    "Labels": {
                        "sh.echo.hub.managed": "true",
                        "sh.echo.hub.role": "nas-directory-helper",
                        "sh.echo.hide": "1",
                        "sh.echo.control-protected": "true",
                    },
                    "NetworkDisabled": True,
                    "HostConfig": {
                        "Mounts": [
                            {
                                "Type": "bind",
                                "Source": nas_source,
                                "Target": "/nas",
                                "ReadOnly": False,
                                "BindOptions": {"Propagation": "rprivate"},
                            }
                        ],
                        "NetworkMode": "none",
                        "CapDrop": ["ALL"],
                        "CapAdd": ["CHOWN", "DAC_OVERRIDE", "FOWNER"],
                        "SecurityOpt": ["no-new-privileges"],
                        "ReadonlyRootfs": True,
                        "PidsLimit": 16,
                        "Memory": 67108864,
                        "Init": True,
                    },
                },
            )
            self.start(helper_id)
            if self.wait_container(helper_id) != 0:
                raise DockerUnavailable("Hub NAS data directory preparation failed")
        finally:
            if helper_id is not None:
                self.remove_container(helper_id, force=True)
        return f"{nas_source.rstrip('/')}/{relative_path}"

    def create_container(self, name: str, config: dict[str, Any]) -> str:
        response = self._request("POST", "/containers/create", params={"name": name}, json=config)
        self._require_success(response, {201})
        try:
            payload = response.json()
        except ValueError as exc:
            raise DockerUnavailable("Docker create returned invalid JSON") from exc
        container_id = str(payload.get("Id") or "") if isinstance(payload, dict) else ""
        if not 12 <= len(container_id) <= 64 or re.fullmatch(r"[0-9a-f]+", container_id) is None:
            raise DockerUnavailable("Docker create returned an invalid container id")
        return container_id

    def remove_container(self, container_id: str, *, force: bool = False) -> None:
        response = self._request(
            "DELETE",
            f"/containers/{container_id}",
            params={"force": "true" if force else "false", "v": "false"},
        )
        self._require_success(response, {204, 404})

    def rename_container(self, container_id: str, name: str) -> None:
        if re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,127}", name) is None:
            raise DockerUnavailable("invalid Docker container name")
        response = self._request(
            "POST",
            f"/containers/{container_id}/rename",
            params={"name": name},
        )
        self._require_success(response, {204})

    def wait_container(self, container_id: str) -> int:
        response = self._request(
            "POST",
            f"/containers/{container_id}/wait",
            params={"condition": "not-running"},
            timeout=3600.0,
        )
        self._require_success(response, {200})
        try:
            payload = response.json()
        except ValueError as exc:
            raise DockerUnavailable("Docker wait returned invalid JSON") from exc
        status_code = payload.get("StatusCode") if isinstance(payload, dict) else None
        if not isinstance(status_code, int) or isinstance(status_code, bool):
            raise DockerUnavailable("Docker wait returned an invalid status")
        return status_code

    def remove_volume(self, name: str) -> None:
        self._require_volume_name(name)
        response = self._request(
            "DELETE",
            f"/volumes/{name}",
            params={"force": "false"},
        )
        self._require_success(response, {204, 404})

    def snapshot_volume(
        self,
        source: str,
        backup: str,
        *,
        labels: dict[str, str],
    ) -> None:
        self._require_volume_name(source)
        self._require_volume_name(backup)
        self.create_volume(backup, labels=labels)
        self._copy_volume(source, backup, clear_destination=True)

    def restore_volume(self, backup: str, destination: str) -> None:
        self._require_volume_name(backup)
        self._require_volume_name(destination)
        self._copy_volume(backup, destination, clear_destination=True)

    @staticmethod
    def _require_volume_name(name: str) -> None:
        if _VOLUME_NAME.fullmatch(name) is None:
            raise DockerUnavailable("invalid Docker volume name")

    @staticmethod
    def _require_network_name(name: str) -> None:
        if _VOLUME_NAME.fullmatch(name) is None:
            raise DockerUnavailable("invalid Docker network name")

    @staticmethod
    def _validate_secret_files(files: dict[str, bytes]) -> None:
        if not isinstance(files, dict) or not 1 <= len(files) <= 32:
            raise DockerUnavailable("Hub secret file set is invalid")
        for name, content in files.items():
            if (
                _SECRET_FILE_NAME.fullmatch(str(name)) is None
                or not isinstance(content, bytes)
                or not 1 <= len(content) <= 512
                or b"\x00" in content
            ):
                raise DockerUnavailable("Hub secret file is invalid")

    def _secret_volume_helper(self, volume: str) -> str:
        helper_id = self.create_container(
            f"echo-hub-secret-{secrets.token_hex(8)}",
            {
                "Image": self._data_copy_image(),
                "Entrypoint": ["python", "-c"],
                "Cmd": ["import time; time.sleep(300)"],
                "Labels": {
                    "sh.echo.hub.managed": "true",
                    "sh.echo.hub.role": "secret-volume-helper",
                    "sh.echo.hide": "1",
                    "sh.echo.control-protected": "true",
                },
                "NetworkDisabled": True,
                "HostConfig": {
                    "Mounts": [
                        {
                            "Type": "volume",
                            "Source": volume,
                            "Target": "/secrets",
                            "ReadOnly": False,
                        }
                    ],
                    "NetworkMode": "none",
                    "CapDrop": ["ALL"],
                    "SecurityOpt": ["no-new-privileges"],
                    "ReadonlyRootfs": True,
                    "PidsLimit": 16,
                    "Memory": 67108864,
                    "Init": True,
                },
            },
        )
        try:
            self.start(helper_id)
        except Exception:
            with contextlib.suppress(Exception):
                self.remove_container(helper_id, force=True)
            raise
        return helper_id

    def _verify_secret_files(self, helper_id: str, files: dict[str, bytes]) -> None:
        for name in files:
            response = self._request(
                "HEAD",
                f"/containers/{helper_id}/archive",
                params={"path": f"/secrets/{name}"},
            )
            self._require_success(response, {200})

    def _data_copy_image(self) -> str:
        own_id = str(os.environ.get("HOSTNAME") or "").strip().lower()
        if re.fullmatch(r"[0-9a-f]{12,64}", own_id) is None:
            raise DockerUnavailable("Hub data-copy provider identity is unavailable")
        for container in self.list_containers(include_stopped=True):
            container_id = str(container.get("Id") or "").lower()
            labels = container.get("Labels") or {}
            names = container.get("Names") or []
            image_id = str(container.get("ImageID") or "").lower()
            if (
                isinstance(labels, dict)
                and isinstance(names, list)
                and container_id.startswith(own_id)
                and labels.get(_DATA_COPY_PROVIDER_LABEL) == "true"
                and labels.get("sh.echo.control-protected") == "true"
                and "/echo-docker-control" in names
                and re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is not None
            ):
                return image_id
        raise DockerUnavailable("trusted Hub data-copy provider is unavailable")

    def _nas_source(self) -> str:
        providers: list[str] = []
        for container in self.list_containers(include_stopped=True):
            labels = container.get("Labels") or {}
            names = container.get("Names") or []
            if (
                not isinstance(labels, dict)
                or not isinstance(names, list)
                or labels.get(_NAS_PROVIDER_LABEL) != "true"
                or labels.get("sh.echo.control-protected") != "true"
                or "/echo-os" not in names
            ):
                continue
            mounts = [
                mount
                for mount in container.get("Mounts") or []
                if isinstance(mount, dict) and mount.get("Destination") == "/data/nas"
            ]
            if len(mounts) != 1 or mounts[0].get("Type") != "bind":
                continue
            source = str(mounts[0].get("Source") or "")
            if source.startswith("/") and source != "/" and "\x00" not in source:
                providers.append(source.rstrip("/"))
        if len(providers) != 1:
            raise DockerUnavailable("trusted Hub NAS data provider is unavailable")
        return providers[0]

    @staticmethod
    def _require_nas_relative_path(relative_path: str) -> None:
        if not isinstance(relative_path, str) or not 1 <= len(relative_path) <= 192:
            raise DockerUnavailable("invalid Hub NAS data path")
        parts = relative_path.split("/")
        if (
            not 1 <= len(parts) <= 4
            or "/".join(parts) != relative_path
            or any(_HUB_APP_ID.fullmatch(part) is None for part in parts)
        ):
            raise DockerUnavailable("invalid Hub NAS data path")

    def _copy_volume(
        self,
        source: str,
        destination: str,
        *,
        clear_destination: bool,
    ) -> None:
        helper_id: str | None = None
        try:
            helper_id = self.create_container(
                f"echo-hub-copy-{secrets.token_hex(8)}",
                {
                    "Image": self._data_copy_image(),
                    "Entrypoint": ["python", "-c"],
                    "Cmd": [_DATA_COPY_SCRIPT],
                    "Env": [f"ECHO_CLEAR_DESTINATION={'1' if clear_destination else '0'}"],
                    "Labels": {
                        "sh.echo.hub.managed": "true",
                        "sh.echo.hub.role": "data-copy-helper",
                        "sh.echo.hide": "1",
                        "sh.echo.control-protected": "true",
                    },
                    "NetworkDisabled": True,
                    "HostConfig": {
                        "Mounts": [
                            {
                                "Type": "volume",
                                "Source": source,
                                "Target": "/source",
                                "ReadOnly": True,
                            },
                            {
                                "Type": "volume",
                                "Source": destination,
                                "Target": "/destination",
                                "ReadOnly": False,
                            },
                        ],
                        "NetworkMode": "none",
                        "CapDrop": ["ALL"],
                        "CapAdd": ["CHOWN", "DAC_OVERRIDE", "FOWNER"],
                        "SecurityOpt": ["no-new-privileges"],
                        "ReadonlyRootfs": True,
                        "PidsLimit": 64,
                        "Memory": 268435456,
                        "Init": True,
                    },
                },
            )
            self.start(helper_id)
            if self.wait_container(helper_id) != 0:
                raise DockerUnavailable("Hub data volume copy failed")
        finally:
            if helper_id is not None:
                self.remove_container(helper_id, force=True)
