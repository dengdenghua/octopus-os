"""启动器应用注册器:容器映射与 HTTP API。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance.app_registry.catalog import build_catalog, container_to_app
from appliance.app_registry.docker_client import DockerUnavailable
from appliance.app_registry.router import create_appliance_router


def _container(**overrides):
    base = {
        "Id": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
        "Names": ["/jellyfin"],
        "Image": "jellyfin/jellyfin:latest",
        "State": "running",
        "Status": "Up 3 hours",
        "Labels": {},
        "Ports": [
            {"PrivatePort": 8096, "PublicPort": 8096, "Type": "tcp", "IP": "0.0.0.0"},
        ],
    }
    base.update(overrides)
    return base


class TestCatalog:
    def test_basic_mapping(self):
        app = container_to_app(_container())
        assert app is not None
        assert app.id == "a1b2c3d4e5f6"
        assert app.name == "jellyfin"
        assert app.state == "running"
        assert app.web_port == 8096
        assert app.description == "jellyfin/jellyfin:latest"

    def test_label_cascade_prefers_octopus_then_casaos(self):
        app = container_to_app(
            _container(
                Labels={
                    "casaos.name": "Jellyfin CasaOS",
                    "sh.octopus.name": "影音中心",
                    "casaos.icon": "https://cdn.example/jellyfin.png",
                }
            )
        )
        assert app.name == "影音中心"
        assert app.icon == "https://cdn.example/jellyfin.png"

    def test_webui_label_beats_port_heuristic(self):
        app = container_to_app(
            _container(Labels={"casaos.webui": "http://nas.local:8096/web"})
        )
        assert app.web_url == "http://nas.local:8096/web"

    def test_web_port_preference_picks_80_over_higher(self):
        ports = [
            {"PrivatePort": 9999, "PublicPort": 9999, "Type": "tcp"},
            {"PrivatePort": 80, "PublicPort": 80, "Type": "tcp"},
        ]
        app = container_to_app(_container(Ports=ports))
        assert app.web_port == 80

    def test_udp_only_container_has_no_web_port(self):
        app = container_to_app(
            _container(Ports=[{"PrivatePort": 53, "PublicPort": 53, "Type": "udp"}])
        )
        assert app.web_port is None
        assert app.ports == []

    def test_hide_label_removes_app(self):
        assert container_to_app(_container(Labels={"sh.octopus.hide": "1"})) is None

    def test_catalog_orders_running_first_then_name(self):
        stopped = _container(
            Id="b" * 32, Names=["/aria2"], State="exited", Status="Exited"
        )
        running = _container(Id="c" * 32, Names=["/zulu"], State="running")
        hidden = _container(Id="d" * 32, Labels={"sh.octopus.hide": "true"})
        apps = build_catalog([stopped, hidden, running])
        assert [a.name for a in apps] == ["zulu", "aria2"]


class _StubDocker:
    def __init__(self, containers=None, fail=False):
        self.containers = containers if containers is not None else [_container()]
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    def list_containers(self, include_stopped: bool = True):
        if self.fail:
            raise DockerUnavailable("docker socket not found: /var/run/docker.sock")
        return self.containers

    def start(self, container_id: str):
        self.calls.append(("start", container_id))

    def stop(self, container_id: str):
        if self.fail:
            raise DockerUnavailable("gone")
        self.calls.append(("stop", container_id))


@pytest.fixture()
def client_factory():
    def make(stub: _StubDocker) -> TestClient:
        app = FastAPI()
        app.include_router(create_appliance_router(docker=stub))
        return TestClient(app)

    return make


class TestRouter:
    def test_list_apps(self, client_factory):
        response = client_factory(_StubDocker()).get("/api/appliance/apps")
        assert response.status_code == 200
        body = response.json()
        assert body["available"] is True
        assert body["apps"][0]["name"] == "jellyfin"

    def test_list_degrades_when_docker_missing(self, client_factory):
        body = client_factory(_StubDocker(fail=True)).get("/api/appliance/apps").json()
        assert body["available"] is False
        assert body["apps"] == []
        assert "docker socket" in body["error"]

    def test_start_validates_container_id(self, client_factory):
        client = client_factory(_StubDocker())
        assert client.post("/api/appliance/apps/../etc/start").status_code in (404, 422)
        assert client.post("/api/appliance/apps/ZZZZ/start").status_code == 422

    def test_start_and_stop_happy_path(self, client_factory):
        stub = _StubDocker()
        client = client_factory(stub)
        cid = "a1b2c3d4e5f6"
        assert client.post(f"/api/appliance/apps/{cid}/start").json() == {"ok": True}
        assert stub.calls == [("start", cid)]

    def test_stop_maps_unavailable_to_503(self, client_factory):
        client = client_factory(_StubDocker(fail=True))
        assert client.post("/api/appliance/apps/abcdef123456/stop").status_code == 503
