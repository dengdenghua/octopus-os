"""企业版 PM 工具(D① 编程接入):路由到正确端点 + 鉴权头 + 参数校验。"""

from __future__ import annotations

import httpx
import pytest

from appliance import pm_skills
from runtime.execution.suckers.registry import SkillRegistry


class _Capture:
    def __init__(self, status: int = 200, payload=None) -> None:
        self.calls: list[dict] = []
        self.status = status
        self.payload = payload if payload is not None else {"ok": 1}

    def __call__(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return httpx.Response(
            self.status,
            json=self.payload,
            request=httpx.Request(method, url),
        )


@pytest.fixture()
def cap(monkeypatch):
    monkeypatch.setenv("OCTOPUS_PM_URL", "http://pm.test:8100")
    monkeypatch.setenv("OCTOPUS_PM_TOKEN", "tok-abc")
    monkeypatch.setenv("OCTOPUS_PM_TENANT", "acme")
    c = _Capture()
    monkeypatch.setattr(httpx, "request", c)
    return c


class TestRegistration:
    def test_registers_three_when_configured(self, monkeypatch):
        monkeypatch.setenv("OCTOPUS_PM_URL", "http://pm.test:8100")
        assert pm_skills.register_pm_skills(SkillRegistry()) == 3

    def test_registers_zero_when_unconfigured(self, monkeypatch):
        monkeypatch.delenv("OCTOPUS_PM_URL", raising=False)
        assert pm_skills.register_pm_skills(SkillRegistry()) == 0


class TestHandlers:
    def test_list_projects_endpoint_and_headers(self, cap):
        out = pm_skills._pm_list_projects()
        assert out["ok"] is True
        call = cap.calls[0]
        assert call["method"] == "GET"
        assert call["url"] == "http://pm.test:8100/api/v1/projects"
        assert call["headers"]["Authorization"] == "Bearer tok-abc"
        assert call["headers"]["X-Tenant-ID"] == "acme"

    def test_list_tasks_endpoint(self, cap):
        pm_skills._pm_list_tasks(project_id="proj-9")
        assert cap.calls[0]["url"] == "http://pm.test:8100/api/v1/projects/proj-9/tasks"

    def test_list_tasks_missing_id(self, cap):
        assert "error" in pm_skills._pm_list_tasks(project_id="")
        assert cap.calls == []  # 校验失败不打网络

    def test_create_task_posts_payload(self, cap):
        pm_skills._pm_create_task(
            project_id="proj-9", title="选型 BOM", role="硬件"
        )
        call = cap.calls[0]
        assert call["method"] == "POST"
        assert call["url"] == "http://pm.test:8100/api/v1/projects/proj-9/tasks"
        assert call["json"]["title"] == "选型 BOM"
        assert call["json"]["role"] == "硬件"

    def test_create_task_missing_args(self, cap):
        assert "error" in pm_skills._pm_create_task(project_id="p", title="")
        assert cap.calls == []

    def test_unconfigured_returns_error(self, monkeypatch):
        monkeypatch.delenv("OCTOPUS_PM_URL", raising=False)
        assert pm_skills._pm_list_projects()["error"] == "OCTOPUS_PM_URL not configured"

    def test_http_error_surfaced(self, monkeypatch):
        monkeypatch.setenv("OCTOPUS_PM_URL", "http://pm.test:8100")
        monkeypatch.setattr(httpx, "request", _Capture(status=401, payload={"detail": "no"}))
        out = pm_skills._pm_list_projects()
        assert "error" in out and "401" in out["error"]
