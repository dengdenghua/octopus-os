from __future__ import annotations

import io
import json
import re
import tarfile
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path
from socketserver import StreamRequestHandler, ThreadingUnixStreamServer
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance.app_registry.docker_client import (
    DockerClient,
    DockerConflict,
    DockerControlDenied,
    DockerUnavailable,
)
from appliance.app_registry.router import create_appliance_router
from appliance.docker_proxy import _drop_socket_privileges, create_proxy_server
from appliance.hub.catalog import HubCatalog
from appliance.hub.docker_installer import (
    INSTALL_RESULT_SCHEMA,
    RESTART_RESULT_SCHEMA,
    START_RESULT_SCHEMA,
    STOP_RESULT_SCHEMA,
    UNINSTALL_RESULT_SCHEMA,
    UPDATE_RESULT_SCHEMA,
)
from appliance.hub.progress import hub_progress


class _FakeDocker:
    def __init__(self) -> None:
        self.available = True
        self.calls: list[tuple[str, object]] = []
        self.containers = [
            {
                "Id": "a" * 64,
                "Names": ["/jellyfin"],
                "Labels": {},
            },
            {
                "Id": "b" * 64,
                "Names": ["/echo-os"],
                "Labels": {
                    "sh.echo.control-protected": "true",
                    "sh.echo.hide": "1",
                },
            },
        ]

    def ping(self) -> bool:
        self.calls.append(("ping", True))
        return self.available

    def list_containers(self, include_stopped: bool = True):
        self.calls.append(("list", include_stopped))
        if not self.available:
            raise DockerUnavailable("unavailable")
        return self.containers

    def docker_root_dir(self) -> str:
        self.calls.append(("root", "/var/lib/docker"))
        return "/var/lib/docker"

    def start(self, container_id: str) -> None:
        self.calls.append(("start", container_id))

    def stop(self, container_id: str) -> None:
        self.calls.append(("stop", container_id))


class _FakeHubInstaller:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def install(
        self,
        app_id: str,
        *,
        plan_id: str,
        catalog_digest: str,
        progress=None,
    ) -> dict:
        self.calls.append((app_id, plan_id, catalog_digest))
        if progress is not None:
            progress(hub_progress("validating", "checking-plan"))
            progress(
                hub_progress(
                    "pulling",
                    "pulling-image",
                    completed=4,
                    total=11,
                    unit="layers",
                    item=1,
                    items=1,
                )
            )
        return {
            "schema": INSTALL_RESULT_SCHEMA,
            "appId": app_id,
            "planId": plan_id,
            "catalogDigest": catalog_digest,
            "containerId": "c" * 12,
            "state": "running",
            "image": "fixture",
        }

    def uninstall(
        self,
        app_id: str,
        *,
        plan_id: str,
        catalog_digest: str,
        progress=None,
    ) -> dict:
        self.calls.append((app_id, plan_id, catalog_digest))
        return {
            "schema": UNINSTALL_RESULT_SCHEMA,
            "appId": app_id,
            "planId": plan_id,
            "catalogDigest": catalog_digest,
            "containerId": "c" * 12,
            "state": "not-installed",
            "dataVolumesRetained": True,
            "nasDataRetained": True,
        }

    def update(
        self,
        app_id: str,
        *,
        plan_id: str,
        catalog_digest: str,
        progress=None,
    ) -> dict:
        self.calls.append((app_id, plan_id, catalog_digest))
        return {
            "schema": UPDATE_RESULT_SCHEMA,
            "appId": app_id,
            "planId": plan_id,
            "catalogDigest": catalog_digest,
            "previousContainerId": "d" * 12,
            "containerId": "c" * 12,
            "previousImage": "old",
            "image": "new",
            "state": "running",
            "dataVolumesRetained": True,
            "nasDataRetained": True,
        }

    def _control(
        self,
        operation: str,
        app_id: str,
        *,
        plan_id: str,
        catalog_digest: str,
        progress=None,
    ) -> dict:
        self.calls.append((app_id, plan_id, catalog_digest))
        if progress is not None:
            progress(hub_progress("validating", "checking-plan"))
        schemas = {
            "start": START_RESULT_SCHEMA,
            "stop": STOP_RESULT_SCHEMA,
            "restart": RESTART_RESULT_SCHEMA,
        }
        return {
            "schema": schemas[operation],
            "appId": app_id,
            "planId": plan_id,
            "catalogDigest": catalog_digest,
            "containerId": "c" * 12,
            "state": "stopped" if operation == "stop" else "running",
            "serviceCount": 3,
            "dataVolumesRetained": True,
            "nasDataRetained": True,
        }

    def start(self, app_id: str, **kwargs) -> dict:
        return self._control("start", app_id, **kwargs)

    def stop(self, app_id: str, **kwargs) -> dict:
        return self._control("stop", app_id, **kwargs)

    def restart(self, app_id: str, **kwargs) -> dict:
        return self._control("restart", app_id, **kwargs)


class _FakeUnixDockerHandler(StreamRequestHandler):
    def handle(self) -> None:
        request_line = self.rfile.readline().decode("ascii").strip()
        method, target, _protocol = request_line.split(" ", 2)
        while self.rfile.readline() not in {b"\r\n", b"\n", b""}:
            pass
        self.server.calls.append((method, target))  # type: ignore[attr-defined]
        if method == "GET" and target == "/_ping":
            self._respond(200, b"OK", "text/plain")
        elif method == "GET" and target.startswith("/containers/json?"):
            payload = json.dumps([{"Id": "a" * 64, "Names": ["/jellyfin"], "Labels": {}}]).encode()
            self._respond(200, payload, "application/json")
        elif method == "POST" and re.fullmatch(rf"/containers/{'a' * 64}/(?:start|stop)", target):
            self._respond(204)
        else:
            self._respond(404, b'{"message":"not found"}', "application/json")

    def _respond(
        self,
        status: int,
        body: bytes = b"",
        content_type: str = "application/json",
    ) -> None:
        reason = {200: "OK", 204: "No Content", 404: "Not Found"}[status]
        headers = (
            f"HTTP/1.1 {status} {reason}\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Content-Type: {content_type}\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii")
        self.wfile.write(headers + body)


@pytest.fixture()
def proxy() -> Iterator[tuple[str, _FakeDocker]]:
    docker = _FakeDocker()
    server = create_proxy_server(docker, host="127.0.0.1", port=0)  # type: ignore[arg-type]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}", docker
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_proxy_exposes_only_launcher_operations(proxy) -> None:
    base_url, docker = proxy
    with httpx.Client(base_url=base_url) as client:
        assert client.get("/_ping").text == "OK"
        listed = client.get("/containers/json", params={"all": "true"})
        assert listed.status_code == 200
        assert listed.json()[0]["Names"] == ["/jellyfin"]
        assert client.post(f"/containers/{'a' * 12}/start").status_code == 204
        assert client.post(f"/containers/{'a' * 12}/stop").status_code == 204

    assert ("list", True) in docker.calls
    assert ("start", "a" * 64) in docker.calls
    assert ("stop", "a" * 64) in docker.calls


def test_proxy_rejects_an_ambiguous_container_prefix_without_control() -> None:
    docker = _FakeDocker()
    docker.containers = [
        {"Id": "a" * 63 + suffix, "Names": [f"/app-{suffix}"], "Labels": {}}
        for suffix in ("1", "2")
    ]
    server = create_proxy_server(docker, host="127.0.0.1", port=0)  # type: ignore[arg-type]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        response = httpx.post(f"http://{host}:{port}/containers/{'a' * 12}/start")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert response.status_code == 409
    assert not any(call[0] == "start" for call in docker.calls)


def test_non_loopback_proxy_requires_a_strong_shared_token(monkeypatch) -> None:
    monkeypatch.delenv("ECHO_DOCKER_PROXY_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="required when Docker control listens"):
        create_proxy_server(_FakeDocker(), host="0.0.0.0", port=0)  # type: ignore[arg-type]

    monkeypatch.setenv("ECHO_DOCKER_PROXY_TOKEN", "short")
    with pytest.raises(RuntimeError, match="32-512"):
        create_proxy_server(_FakeDocker(), host="0.0.0.0", port=0)  # type: ignore[arg-type]


def test_proxy_rejects_missing_token_and_client_sends_configured_token(monkeypatch) -> None:
    token = "proxy-test-token-which-is-long-enough-1234567890"
    monkeypatch.setenv("ECHO_DOCKER_PROXY_TOKEN", token)
    docker = _FakeDocker()
    server = create_proxy_server(docker, host="127.0.0.1", port=0)  # type: ignore[arg-type]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base_url = f"http://{host}:{port}"
    try:
        assert httpx.get(f"{base_url}/health").status_code == 401
        assert DockerClient(base_url=base_url, proxy_token=token).ping() is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_proxy_exposes_only_sanitized_verified_docker_capacity(tmp_path) -> None:
    docker = _FakeDocker()
    server = create_proxy_server(
        docker,
        host="127.0.0.1",
        port=0,
        docker_data_root_mount=tmp_path,
        expected_docker_root="/var/lib/docker",
    )  # type: ignore[arg-type]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        client = DockerClient(base_url=f"http://{host}:{port}")
        capacity = client.hub_storage_capacity()
        rejected_query = httpx.get(f"http://{host}:{port}/hub/storage?path=/")
        raw_info = httpx.get(f"http://{host}:{port}/info")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert capacity["schema"] == "echo.hub.docker-storage.v1"
    assert capacity["status"] == "observed"
    assert capacity["totalBytes"] > 0
    assert 0 <= capacity["freeBytes"] <= capacity["totalBytes"]
    assert "/var/lib/docker" not in json.dumps(capacity)
    assert str(tmp_path) not in json.dumps(capacity)
    assert rejected_query.status_code == 400
    assert raw_info.status_code == 404


def test_proxy_exposes_only_catalog_owned_sanitized_app_runtime() -> None:
    catalog = HubCatalog.load()
    app = catalog.get("jellyfin")
    assert app is not None and app.package is not None

    class RuntimeDocker(_FakeDocker):
        def __init__(self) -> None:
            super().__init__()
            self.containers = [
                {
                    "Id": "c" * 64,
                    "Names": ["/echo-hub-jellyfin"],
                    "Labels": {
                        "sh.echo.hub.managed": "true",
                        "sh.echo.hub.app-id": "jellyfin",
                        "sh.echo.hub.catalog-digest": catalog.digest,
                        "sh.echo.hub.plan-id": "d" * 64,
                        "sh.echo.hub.package-digest": app.package.digest,
                        "sh.echo.hub.version": app.version,
                    },
                }
            ]

        def inspect_container(self, container_id: str) -> dict:
            assert container_id == "c" * 64
            return {
                "State": {
                    "Status": "running",
                    "Running": True,
                    "OOMKilled": False,
                    "ExitCode": 0,
                },
                "RestartCount": 0,
                "Config": {"Env": ["PASSWORD=sidecar-only"]},
                "Mounts": [{"Source": "/srv/private"}],
            }

        def container_stats(self, container_id: str) -> dict:
            assert container_id == "c" * 64
            return {
                "cpu_stats": {
                    "cpu_usage": {"total_usage": 150},
                    "system_cpu_usage": 2_000,
                    "online_cpus": 1,
                },
                "precpu_stats": {
                    "cpu_usage": {"total_usage": 100},
                    "system_cpu_usage": 1_000,
                },
                "memory_stats": {"usage": 1024, "limit": 4096},
                "pids_stats": {"current": 3},
            }

    docker = RuntimeDocker()
    server = create_proxy_server(docker, host="127.0.0.1", port=0)  # type: ignore[arg-type]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base_url = f"http://{host}:{port}"
    try:
        runtime = DockerClient(base_url=base_url).hub_app_runtime("jellyfin")
        raw_inspect = httpx.get(f"{base_url}/containers/{'c' * 64}/json")
        raw_stats = httpx.get(f"{base_url}/containers/{'c' * 64}/stats?stream=false")
        rejected_query = httpx.get(f"{base_url}/hub/apps/jellyfin/runtime?verbose=true")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert runtime["status"] == "healthy"
    assert runtime["summary"]["memoryUsageBytes"] == 1024
    serialized = json.dumps(runtime)
    assert "sidecar-only" not in serialized
    assert "/srv/private" not in serialized
    assert raw_inspect.status_code == 404
    assert raw_stats.status_code == 404
    assert rejected_query.status_code == 400


def test_proxy_fails_closed_when_observer_mount_does_not_match_engine_root(tmp_path) -> None:
    docker = _FakeDocker()
    server = create_proxy_server(
        docker,
        host="127.0.0.1",
        port=0,
        docker_data_root_mount=tmp_path,
        expected_docker_root="/srv/docker",
    )  # type: ignore[arg-type]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        capacity = DockerClient(base_url=f"http://{host}:{port}").hub_storage_capacity()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert capacity == {
        "schema": "echo.hub.docker-storage.v1",
        "status": "mismatch",
        "totalBytes": None,
        "freeBytes": None,
        "usedPercent": None,
    }


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("POST", "/containers/create", 404),
        ("DELETE", f"/containers/{'a' * 12}", 405),
        ("GET", "/images/json", 404),
        ("POST", f"/containers/{'a' * 12}/exec", 404),
        ("GET", "/containers/json?all=maybe", 400),
        ("GET", "/containers/json?filters=%7B%7D", 400),
    ],
)
def test_proxy_rejects_every_unneeded_docker_surface(proxy, method, path, expected) -> None:
    base_url, _docker = proxy
    response = httpx.request(method, f"{base_url}{path}")
    assert response.status_code == expected
    assert response.json()["detail"]


def test_proxy_refuses_to_control_protected_appliance_containers(proxy) -> None:
    base_url, docker = proxy
    response = httpx.post(f"{base_url}/containers/{'b' * 12}/stop")
    assert response.status_code == 403
    assert ("stop", "b" * 12) not in docker.calls


def test_proxy_rejects_request_bodies_and_closes_the_connection(proxy) -> None:
    base_url, docker = proxy
    response = httpx.post(
        f"{base_url}/containers/{'a' * 12}/start",
        content=b"unexpected",
    )
    assert response.status_code == 400
    assert response.headers["connection"] == "close"
    assert ("start", "a" * 12) not in docker.calls


def test_docker_client_uses_the_restricted_http_proxy(proxy, monkeypatch) -> None:
    base_url, docker = proxy
    monkeypatch.setenv("ECHO_DOCKER_HOST", base_url)
    monkeypatch.setenv("ECHO_DOCKER_SOCK", "/definitely/not/mounted.sock")
    client = DockerClient()

    assert client.ping() is True
    assert len(client.list_containers()) == 2
    client.start("a" * 12)
    client.stop("a" * 12)

    assert ("start", "a" * 64) in docker.calls
    assert ("stop", "a" * 64) in docker.calls


def test_proxy_exposes_catalog_verified_hub_lifecycle_without_docker_config() -> None:
    docker = _FakeDocker()
    installer = _FakeHubInstaller()
    server = create_proxy_server(
        docker,
        host="127.0.0.1",
        port=0,
        hub_installer=installer,  # type: ignore[arg-type]
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    client = DockerClient(base_url=f"http://{host}:{port}")
    try:
        result = client.install_hub_app("demo-app", plan_id="a" * 64, catalog_digest="b" * 64)
        updated = client.update_hub_app("demo-app", plan_id="d" * 64, catalog_digest="b" * 64)
        removed = client.uninstall_hub_app("demo-app", plan_id="c" * 64, catalog_digest="b" * 64)
        started = client.start_hub_app("demo-app", plan_id="e" * 64, catalog_digest="b" * 64)
        stopped = client.stop_hub_app("demo-app", plan_id="f" * 64, catalog_digest="b" * 64)
        restarted = client.restart_hub_app("demo-app", plan_id="1" * 64, catalog_digest="b" * 64)
        injected = httpx.post(
            f"http://{host}:{port}/hub/apps/demo-app/install",
            json={
                "planId": "a" * 64,
                "catalogDigest": "b" * 64,
                "HostConfig": {"Privileged": True},
            },
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result["containerId"] == "c" * 12
    assert updated["previousContainerId"] == "d" * 12
    assert updated["dataVolumesRetained"] is True
    assert removed["state"] == "not-installed"
    assert removed["dataVolumesRetained"] is True
    assert started["state"] == "running"
    assert stopped["state"] == "stopped"
    assert restarted["serviceCount"] == 3
    assert installer.calls == [
        ("demo-app", "a" * 64, "b" * 64),
        ("demo-app", "d" * 64, "b" * 64),
        ("demo-app", "c" * 64, "b" * 64),
        ("demo-app", "e" * 64, "b" * 64),
        ("demo-app", "f" * 64, "b" * 64),
        ("demo-app", "1" * 64, "b" * 64),
    ]
    assert injected.status_code == 400


def test_proxy_streams_only_bounded_hub_progress_and_final_result() -> None:
    docker = _FakeDocker()
    installer = _FakeHubInstaller()
    server = create_proxy_server(
        docker,
        host="127.0.0.1",
        port=0,
        hub_installer=installer,  # type: ignore[arg-type]
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    progress: list[dict] = []
    try:
        result = DockerClient(base_url=f"http://{host}:{port}").install_hub_app_with_progress(
            "demo-app",
            plan_id="a" * 64,
            catalog_digest="b" * 64,
            progress=progress.append,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result["containerId"] == "c" * 12
    assert [event["stage"] for event in progress] == ["validating", "pulling"]
    assert progress[-1]["completed"] == 4
    assert progress[-1]["total"] == 11
    assert set(progress[-1]) == {
        "schema",
        "stage",
        "step",
        "completed",
        "total",
        "unit",
        "item",
        "items",
    }


def test_proxy_stream_maps_unexpected_installer_failure_to_bounded_internal_code() -> None:
    class BrokenInstaller(_FakeHubInstaller):
        def install(self, *_args, **_kwargs) -> dict:
            raise RuntimeError("private installer detail")

    server = create_proxy_server(
        _FakeDocker(),
        host="127.0.0.1",
        port=0,
        hub_installer=BrokenInstaller(),  # type: ignore[arg-type]
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        response = httpx.post(
            f"http://{host}:{port}/hub/apps/demo-app/install/stream",
            json={"planId": "a" * 64, "catalogDigest": "b" * 64},
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines()]
    assert events == [
        {
            "schema": "echo.hub.operation-stream.v1",
            "type": "error",
            "code": "INTERNAL",
        }
    ]
    assert "private installer detail" not in response.text


def test_docker_pull_converts_engine_events_to_layer_counts(monkeypatch) -> None:
    layer_a = "a" * 12
    layer_b = "b" * 12
    body = b"\n".join(
        json.dumps(event).encode()
        for event in [
            {"status": "Pulling fs layer", "id": layer_a},
            {"status": "Pulling fs layer", "id": layer_b},
            {
                "status": "Downloading",
                "id": layer_a,
                "progressDetail": {"current": 123, "total": 456},
            },
            {"status": "Pull complete", "id": layer_a},
            {"status": "Already exists", "id": layer_b},
            {"status": "Digest: sha256:" + "c" * 64},
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/images/create"
        return httpx.Response(200, content=body + b"\n")

    client = DockerClient(base_url="http://docker.invalid")
    monkeypatch.setattr(
        client,
        "_client",
        lambda: httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="http://docker.invalid",
        ),
    )
    progress: list[dict] = []

    client.pull_image_with_progress(
        "registry.example.invalid/app@sha256:" + "d" * 64,
        progress.append,
    )

    assert progress[-1]["completed"] == 2
    assert progress[-1]["total"] == 2
    assert progress[-1]["unit"] == "layers"
    assert len(progress) == 4
    assert all("id" not in event and "status" not in event for event in progress)


def test_docker_client_preserves_hub_plan_conflicts() -> None:
    class _ConflictInstaller(_FakeHubInstaller):
        def install(self, app_id: str, *, plan_id: str, catalog_digest: str) -> dict:
            from appliance.hub.docker_installer import HubInstallRejected

            raise HubInstallRejected("plan changed")

    docker = _FakeDocker()
    server = create_proxy_server(
        docker,
        host="127.0.0.1",
        port=0,
        hub_installer=_ConflictInstaller(),  # type: ignore[arg-type]
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        with pytest.raises(DockerConflict, match="conflicts"):
            DockerClient(base_url=f"http://{host}:{port}").install_hub_app(
                "demo-app",
                plan_id="a" * 64,
                catalog_digest="b" * 64,
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_hub_volume_snapshot_uses_only_the_protected_self_image(monkeypatch) -> None:
    class _CopyClient(DockerClient):
        def __init__(self) -> None:
            super().__init__(base_url="http://docker.invalid")
            self.calls: list[tuple[str, object]] = []
            self.config: dict | None = None

        def list_containers(self, include_stopped: bool = True) -> list[dict]:
            assert include_stopped is True
            return [
                {
                    "Id": "a" * 64,
                    "ImageID": "sha256:" + "b" * 64,
                    "Names": ["/echo-docker-control"],
                    "Labels": {
                        "sh.echo.hub.data-copy-provider": "true",
                        "sh.echo.control-protected": "true",
                    },
                },
                {
                    "Id": "c" * 64,
                    "ImageID": "sha256:" + "d" * 64,
                    "Names": ["/spoofed"],
                    "Labels": {
                        "sh.echo.hub.data-copy-provider": "true",
                        "sh.echo.control-protected": "true",
                    },
                },
            ]

        def create_volume(self, name: str, *, labels: dict[str, str]) -> None:
            self.calls.append(("volume", (name, labels)))

        def create_container(self, name: str, config: dict) -> str:
            self.calls.append(("create", name))
            self.config = config
            return "e" * 64

        def start(self, container_id: str) -> None:
            self.calls.append(("start", container_id))

        def wait_container(self, container_id: str) -> int:
            self.calls.append(("wait", container_id))
            return 0

        def remove_container(self, container_id: str, *, force: bool = False) -> None:
            self.calls.append(("remove", (container_id, force)))

    monkeypatch.setenv("HOSTNAME", "a" * 12)
    client = _CopyClient()
    client.snapshot_volume(
        "echo-hub-demo-data",
        "echo-hub-demo-data-rollback-123456789abc",
        labels={"sh.echo.hub.plan-id": "1" * 64},
    )

    assert client.config is not None
    assert client.config["Image"] == "sha256:" + "b" * 64
    assert client.config["NetworkDisabled"] is True
    assert client.config["HostConfig"]["NetworkMode"] == "none"
    assert client.config["HostConfig"]["CapDrop"] == ["ALL"]
    assert client.config["HostConfig"]["CapAdd"] == [
        "CHOWN",
        "DAC_OVERRIDE",
        "FOWNER",
    ]
    assert client.config["HostConfig"]["ReadonlyRootfs"] is True
    assert client.config["Labels"]["sh.echo.hide"] == "1"
    assert client.config["Labels"]["sh.echo.control-protected"] == "true"
    assert client.config["HostConfig"]["Mounts"] == [
        {
            "Type": "volume",
            "Source": "echo-hub-demo-data",
            "Target": "/source",
            "ReadOnly": True,
        },
        {
            "Type": "volume",
            "Source": "echo-hub-demo-data-rollback-123456789abc",
            "Target": "/destination",
            "ReadOnly": False,
        },
    ]
    assert client.calls[-1] == ("remove", ("e" * 64, True))

    monkeypatch.setenv("HOSTNAME", "f" * 12)
    with pytest.raises(DockerUnavailable, match="data-copy provider"):
        _CopyClient().snapshot_volume(
            "echo-hub-demo-data",
            "echo-hub-demo-data-rollback-abcdefabcdef",
            labels={"sh.echo.hub.plan-id": "2" * 64},
        )


def test_hub_secret_writer_never_places_secret_values_in_container_config() -> None:
    class _SecretClient(DockerClient):
        def __init__(self) -> None:
            super().__init__(base_url="http://docker.invalid")
            self.config: dict | None = None
            self.archive: bytes | None = None
            self.removed = False

        def _data_copy_image(self) -> str:
            return "sha256:" + "a" * 64

        def create_container(self, name: str, config: dict) -> str:
            assert re.fullmatch(r"echo-hub-secret-[0-9a-f]{16}", name)
            self.config = config
            return "b" * 64

        def start(self, container_id: str) -> None:
            assert container_id == "b" * 64

        def remove_container(self, container_id: str, *, force: bool = False) -> None:
            assert container_id == "b" * 64
            assert force is True
            self.removed = True

        def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
            if method == "PUT":
                assert path == f"/containers/{'b' * 64}/archive"
                assert kwargs["params"] == {"path": "/secrets"}
                self.archive = kwargs["content"]
                return httpx.Response(200)
            assert method == "HEAD"
            assert path == f"/containers/{'b' * 64}/archive"
            return httpx.Response(200)

    client = _SecretClient()
    values = {
        "admin-password": b"one-time-admin-secret",
        "database-password": b"persistent-database-secret",
    }
    client.write_secret_volume("echo-hub-nextcloud-secrets-app", values)

    assert client.config is not None
    serialized = json.dumps(client.config)
    assert all(value.decode() not in serialized for value in values.values())
    assert client.config["Image"] == "sha256:" + "a" * 64
    assert client.config["NetworkDisabled"] is True
    assert client.config["HostConfig"]["NetworkMode"] == "none"
    assert client.config["HostConfig"]["CapDrop"] == ["ALL"]
    assert client.config["HostConfig"]["Mounts"] == [
        {
            "Type": "volume",
            "Source": "echo-hub-nextcloud-secrets-app",
            "Target": "/secrets",
            "ReadOnly": False,
        }
    ]
    assert client.archive is not None
    with tarfile.open(fileobj=io.BytesIO(client.archive), mode="r:") as archive:
        members = {member.name: member for member in archive.getmembers()}
        assert set(members) == set(values)
        assert all(member.mode == 0o444 for member in members.values())
        assert {
            name: archive.extractfile(member).read()  # type: ignore[union-attr]
            for name, member in members.items()
        } == values
    assert client.removed is True


def test_hub_nas_directory_helper_uses_only_the_verified_system_bind() -> None:
    class _NasClient(DockerClient):
        def __init__(self, containers: list[dict] | None = None) -> None:
            super().__init__(base_url="http://docker.invalid")
            self.containers = containers or [
                {
                    "Id": "a" * 64,
                    "Names": ["/echo-os"],
                    "Labels": {
                        "sh.echo.hub.nas-provider": "true",
                        "sh.echo.control-protected": "true",
                    },
                    "Mounts": [
                        {
                            "Type": "bind",
                            "Source": "/srv/echo-nas",
                            "Destination": "/data/nas",
                            "RW": True,
                        }
                    ],
                }
            ]
            self.config: dict | None = None
            self.removed = False

        def list_containers(self, include_stopped: bool = True) -> list[dict]:
            assert include_stopped is True
            return self.containers

        def _data_copy_image(self) -> str:
            return "sha256:" + "b" * 64

        def create_container(self, name: str, config: dict) -> str:
            assert re.fullmatch(r"echo-hub-nas-dir-[0-9a-f]{16}", name)
            self.config = config
            return "c" * 64

        def start(self, container_id: str) -> None:
            assert container_id == "c" * 64

        def wait_container(self, container_id: str) -> int:
            assert container_id == "c" * 64
            return 0

        def remove_container(self, container_id: str, *, force: bool = False) -> None:
            assert container_id == "c" * 64
            assert force is True
            self.removed = True

    client = _NasClient()
    result = client.ensure_nas_subdirectory("photos/immich")

    assert result == "/srv/echo-nas/photos/immich"
    assert client.removed is True
    assert client.config is not None
    assert client.config["Image"] == "sha256:" + "b" * 64
    assert client.config["NetworkDisabled"] is True
    assert client.config["Cmd"][1] == "photos/immich"
    assert client.config["HostConfig"] == {
        "Mounts": [
            {
                "Type": "bind",
                "Source": "/srv/echo-nas",
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
    }

    for invalid in ("../escape", "/absolute", "photos//immich", "a/b/c/d/e"):
        with pytest.raises(DockerUnavailable, match="invalid Hub NAS data path"):
            _NasClient().ensure_nas_subdirectory(invalid)

    duplicate = [*client.containers, dict(client.containers[0], Id="d" * 64)]
    with pytest.raises(DockerUnavailable, match="NAS data provider"):
        _NasClient(duplicate).ensure_nas_subdirectory("photos/immich")

    wrong_mount = [
        {
            **client.containers[0],
            "Mounts": [
                {
                    "Type": "volume",
                    "Source": "spoofed",
                    "Destination": "/data/nas",
                }
            ],
        }
    ]
    with pytest.raises(DockerUnavailable, match="NAS data provider"):
        _NasClient(wrong_mount).ensure_nas_subdirectory("photos/immich")


def test_hub_network_creation_is_internal_and_plan_owned(monkeypatch) -> None:
    client = DockerClient()
    calls: list[tuple[str, str, dict]] = []
    responses = iter([httpx.Response(404), httpx.Response(201)])

    def request(method: str, path: str, **kwargs) -> httpx.Response:
        calls.append((method, path, kwargs))
        return next(responses)

    monkeypatch.setattr(client, "_request", request)
    labels = {
        "sh.echo.hub.bundle-app-id": "nextcloud",
        "sh.echo.hub.plan-id": "1" * 64,
    }
    assert (
        client.create_network(
            "echo-hub-nextcloud-backend-111111111111",
            internal=True,
            labels=labels,
        )
        is True
    )
    assert calls[-1][2]["json"] == {
        "Name": "echo-hub-nextcloud-backend-111111111111",
        "Driver": "bridge",
        "Internal": True,
        "Attachable": False,
        "CheckDuplicate": True,
        "Labels": labels,
    }

    monkeypatch.setattr(
        client,
        "_request",
        lambda *_args, **_kwargs: httpx.Response(
            200,
            json={"Driver": "bridge", "Internal": False, "Labels": labels},
        ),
    )
    with pytest.raises(DockerControlDenied, match="does not belong"):
        client.create_network(
            "echo-hub-nextcloud-backend-111111111111",
            internal=True,
            labels=labels,
        )


def test_echo_appliance_api_reaches_apps_only_through_the_proxy(proxy) -> None:
    base_url, docker = proxy
    app = FastAPI()
    app.include_router(create_appliance_router(docker=DockerClient(base_url=base_url)))

    with TestClient(app) as client:
        listed = client.get("/api/appliance/apps")
        started = client.post(f"/api/appliance/apps/{'a' * 12}/start")
        protected = client.post(f"/api/appliance/apps/{'b' * 12}/stop")

    assert listed.status_code == 200
    assert listed.json()["available"] is True
    assert listed.json()["apps"][0]["name"] == "jellyfin"
    assert started.json() == {"ok": True}
    assert protected.status_code == 403
    assert ("start", "a" * 64) in docker.calls
    assert ("stop", "b" * 12) not in docker.calls


def test_real_unix_socket_to_narrow_proxy_to_echo_client_chain() -> None:
    socket_root = Path(tempfile.mkdtemp(prefix="echo-docker-daemon-", dir="/tmp"))
    socket_path = socket_root / "d.sock"
    daemon = ThreadingUnixStreamServer(str(socket_path), _FakeUnixDockerHandler)
    daemon.daemon_threads = True
    daemon.calls = []  # type: ignore[attr-defined]
    daemon_thread = threading.Thread(target=daemon.serve_forever, daemon=True)
    daemon_thread.start()
    upstream = DockerClient(
        socket_path=str(socket_path),
        base_url="",
        allow_direct_socket=True,
    )
    proxy_server = create_proxy_server(upstream, host="127.0.0.1", port=0)
    proxy_thread = threading.Thread(target=proxy_server.serve_forever, daemon=True)
    proxy_thread.start()
    host, port = proxy_server.server_address
    client = DockerClient(base_url=f"http://{host}:{port}")
    try:
        assert client.ping() is True
        assert client.list_containers()[0]["Names"] == ["/jellyfin"]
        client.start("a" * 12)
        client.stop("a" * 12)
    finally:
        proxy_server.shutdown()
        proxy_server.server_close()
        proxy_thread.join(timeout=2)
        daemon.shutdown()
        daemon.server_close()
        daemon_thread.join(timeout=2)
        socket_path.unlink(missing_ok=True)
        socket_root.rmdir()

    assert ("GET", "/_ping") in daemon.calls  # type: ignore[attr-defined]
    assert ("POST", f"/containers/{'a' * 64}/start") in daemon.calls  # type: ignore[attr-defined]
    assert ("POST", f"/containers/{'a' * 64}/stop") in daemon.calls  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "value",
    [
        "file:///var/run/docker.sock",
        "http://user:password@docker-control:2375",
        "http://docker-control:2375/arbitrary-proxy-prefix",
        "http://docker-control:2375?target=elsewhere",
    ],
)
def test_docker_client_rejects_unsafe_proxy_origins(value: str) -> None:
    with pytest.raises(DockerUnavailable, match="ECHO_DOCKER_HOST"):
        DockerClient(base_url=value)


def test_appliance_mode_never_silently_falls_back_to_the_raw_socket(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ECHO_APPLIANCE", "1")
    monkeypatch.delenv("ECHO_DOCKER_HOST", raising=False)
    client = DockerClient(socket_path="/var/run/docker.sock")

    with pytest.raises(DockerUnavailable, match="direct Docker socket access is disabled"):
        client.list_containers()


def test_docker_transport_error_never_exposes_internal_endpoint(monkeypatch) -> None:
    client = DockerClient(base_url="http://private-docker-control.internal:2375")

    def _raise(_client, method, path, **_kwargs):
        request = httpx.Request(method, f"http://private-docker-control.internal:2375{path}")
        raise httpx.ConnectError("token=secret-at-private-host", request=request)

    monkeypatch.setattr(httpx.Client, "request", _raise)
    with pytest.raises(DockerUnavailable) as captured:
        client.list_containers()

    message = str(captured.value)
    assert message == "docker control endpoint is unreachable"
    assert "private-docker-control" not in message
    assert "secret" not in message


def test_proxy_drops_root_but_keeps_only_the_socket_group(monkeypatch) -> None:
    import pwd
    import socket

    socket_root = Path(tempfile.mkdtemp(prefix="echo-proxy-", dir="/tmp"))
    socket_path = socket_root / "docker.sock"
    unix_socket = socket.socket(socket.AF_UNIX)
    unix_socket.bind(str(socket_path))
    socket_gid = socket_path.stat().st_gid
    state: dict[str, object] = {"uid": 0}

    monkeypatch.setattr(pwd, "getpwnam", lambda _name: SimpleNamespace(pw_uid=991, pw_gid=992))
    monkeypatch.setattr("appliance.docker_proxy.os.geteuid", lambda: state["uid"])
    monkeypatch.setattr(
        "appliance.docker_proxy.os.setgroups",
        lambda groups: state.update(groups=list(groups)),
    )
    monkeypatch.setattr("appliance.docker_proxy.os.setgid", lambda gid: state.update(gid=gid))
    monkeypatch.setattr(
        "appliance.docker_proxy.os.setuid",
        lambda uid: state.update(uid=uid),
    )
    try:
        _drop_socket_privileges(socket_path, "echo")
    finally:
        unix_socket.close()
        socket_path.unlink(missing_ok=True)
        socket_root.rmdir()

    assert state == {
        "uid": 991,
        "gid": 992,
        "groups": sorted({992, socket_gid}),
    }


def test_proxy_refuses_a_regular_file_instead_of_a_docker_socket(tmp_path) -> None:
    target = tmp_path / "not-a-socket"
    target.write_text("unsafe")
    with pytest.raises(RuntimeError, match="not a Unix socket"):
        _drop_socket_privileges(target, "echo")
