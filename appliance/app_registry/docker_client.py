"""Docker Engine API 的最小客户端(unix socket / TCP)。

刻意不引入 docker-py:启动器只需要 list/start/stop 三个调用,
httpx(runtime 既有依赖)走 UDS 即可,少一个重型依赖。
"""

from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_SOCKET = "/var/run/docker.sock"


class DockerUnavailable(RuntimeError):
    """Docker socket 不存在或 Engine API 不可达。"""


class DockerClient:
    """同步客户端;FastAPI 路由经 anyio 线程池调用,不阻塞事件循环。

    ``base_url`` 指定时走 TCP(远程 Engine / 测试),否则走 unix socket
    (可用环境变量 ``OCTOPUS_DOCKER_SOCK`` 覆盖路径)。
    """

    def __init__(
        self,
        socket_path: str | None = None,
        base_url: str | None = None,
        timeout: float = 5.0,
    ) -> None:
        self._socket_path = (
            socket_path or os.environ.get("OCTOPUS_DOCKER_SOCK") or DEFAULT_SOCKET
        )
        self._base_url = base_url
        self._timeout = timeout

    def _client(self) -> httpx.Client:
        if self._base_url:
            return httpx.Client(base_url=self._base_url, timeout=self._timeout)
        if not os.path.exists(self._socket_path):
            raise DockerUnavailable(f"docker socket not found: {self._socket_path}")
        transport = httpx.HTTPTransport(uds=self._socket_path)
        return httpx.Client(
            transport=transport, base_url="http://docker", timeout=self._timeout
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            with self._client() as client:
                response = client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise DockerUnavailable(f"docker engine unreachable: {exc}") from exc
        return response

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
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise DockerUnavailable("unexpected /containers/json payload")
        return payload

    def start(self, container_id: str) -> None:
        response = self._request("POST", f"/containers/{container_id}/start")
        # 304 = already started — treat as success for launcher purposes.
        if response.status_code not in (204, 304):
            response.raise_for_status()

    def stop(self, container_id: str) -> None:
        response = self._request("POST", f"/containers/{container_id}/stop")
        if response.status_code not in (204, 304):
            response.raise_for_status()
