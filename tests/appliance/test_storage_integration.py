"""OS 与 echo-storage 集成测试。

覆盖:
- storage_spawner 的探测/启动/幂等逻辑(使用 mock,不真的启动子进程)。
- appliance config 端点正确暴露 storage_url。

Agent 内部 arm/skill 由独立 Agent 仓库及其测试负责；OS 不再导入或复制这些实现。
镜像侧兼容契约由 agent_bundle.py 校验 wheel 身份与 echo-agent 入口点。
"""

from __future__ import annotations

from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


class TestStorageSpawner:
    @pytest.fixture(autouse=True)
    def _reset_storage_process(self):
        """每个测试前后清理 storage_spawner 的全局子进程引用,避免 mock 污染。"""
        import appliance.storage_spawner as spawner

        spawner._storage_process = None
        yield
        spawner._storage_process = None

    def test_autostart_disabled_returns_reason(self, monkeypatch):
        monkeypatch.delenv("ECHO_STORAGE_AUTOSTART", raising=False)
        from appliance.storage_spawner import start_storage_service

        result = start_storage_service()
        assert result["started"] is False
        assert result["error"] == "ECHO_STORAGE_AUTOSTART is not set"

    def test_already_running_returns_already_running(self, monkeypatch):
        monkeypatch.setenv("ECHO_STORAGE_AUTOSTART", "1")
        from appliance.storage_spawner import start_storage_service

        with mock.patch(
            "appliance.storage_spawner._probe_manifest",
            return_value=True,
        ):
            result = start_storage_service()
        assert result["started"] is False
        assert result["already_running"] is True
        assert result["error"] is None

    def test_missing_executable_returns_error(self, monkeypatch):
        monkeypatch.setenv("ECHO_STORAGE_AUTOSTART", "1")
        from appliance.storage_spawner import start_storage_service

        with (
            mock.patch(
                "appliance.storage_spawner._probe_manifest",
                return_value=False,
            ),
            mock.patch(
                "appliance.storage_spawner._storage_executable",
                return_value=None,
            ),
        ):
            result = start_storage_service()
        assert result["started"] is False
        assert "executable not found" in result["error"]

    def test_spawns_and_waits_for_health(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ECHO_STORAGE_AUTOSTART", "1")
        monkeypatch.setenv("ECHO_STORAGE_PORT", "19999")
        from appliance.storage_spawner import start_storage_service

        fake_proc = mock.Mock(spec=["poll", "stdout", "stderr"])
        fake_proc.poll.return_value = None
        fake_proc.stdout.read.return_value = b""
        fake_proc.stderr.read.return_value = b""

        with (
            mock.patch(
                "appliance.storage_spawner._probe_manifest",
                side_effect=[False, True],
            ),
            mock.patch(
                "appliance.storage_spawner._storage_executable",
                return_value="/fake/echo-storage",
            ),
            mock.patch(
                "subprocess.Popen",
                return_value=fake_proc,
            ) as popen_mock,
        ):
            result = start_storage_service()

        assert result["started"] is True
        assert result["already_running"] is False
        assert result["error"] is None
        assert result["url"] == "http://127.0.0.1:19999"
        popen_mock.assert_called_once()
        cmd = popen_mock.call_args[0][0]
        assert cmd[:4] == ["/fake/echo-storage", "serve", "--host", "127.0.0.1"]

    def test_early_exit_returns_error(self, monkeypatch):
        monkeypatch.setenv("ECHO_STORAGE_AUTOSTART", "1")
        from appliance.storage_spawner import start_storage_service

        fake_proc = mock.Mock(spec=["poll", "stdout", "stderr", "returncode"])
        fake_proc.poll.return_value = 1
        fake_proc.returncode = 1
        fake_proc.stdout.read.return_value = b"boom"
        fake_proc.stderr.read.return_value = b""

        with (
            mock.patch(
                "appliance.storage_spawner._probe_manifest",
                return_value=False,
            ),
            mock.patch(
                "appliance.storage_spawner._storage_executable",
                return_value="/fake/echo-storage",
            ),
            mock.patch("subprocess.Popen", return_value=fake_proc),
        ):
            result = start_storage_service()

        assert result["started"] is False
        assert "exited with code 1" in result["error"]


class TestApplianceConfig:
    def test_config_exposes_storage_url_when_autostart_enabled(self, monkeypatch):
        monkeypatch.setenv("ECHO_APPLIANCE", "1")
        monkeypatch.setenv("ECHO_DATA_DIR", "/tmp")
        monkeypatch.setenv("ECHO_ADMIN_PASSWORD", "test-pass-123456")
        monkeypatch.setenv("ECHO_STORAGE_AUTOSTART", "1")
        monkeypatch.setenv("ECHO_STORAGE_PORT", "18888")

        from appliance.agent_ui import mount_agent_ui

        app = FastAPI()
        mount_agent_ui(app)
        client = TestClient(app)
        resp = client.get("/api/appliance/config")
        assert resp.status_code == 200
        body = resp.json()
        assert body["storage_url"] == "http://127.0.0.1:18888"

    def test_config_storage_url_null_by_default(self, monkeypatch):
        monkeypatch.delenv("ECHO_STORAGE_AUTOSTART", raising=False)
        from appliance.agent_ui import mount_agent_ui

        app = FastAPI()
        mount_agent_ui(app)
        client = TestClient(app)
        resp = client.get("/api/appliance/config")
        assert resp.status_code == 200
        body = resp.json()
        assert body["storage_url"] is None
