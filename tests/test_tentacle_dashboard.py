"""Tentacle Dashboard 集成测试.

使用 FastAPI TestClient（同步），无需启动真实 HTTP 服务器.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from runtime.core.cerebrum.planner import Rule, StaticPlanner
from runtime.tentacle.base import ToolCall
from runtime.tentacle.coordinator import TentacleCoordinator
from runtime.tentacle.dashboard import create_tentacle_router
from runtime.tentacle.mobile.cerebrum_adapter import CerebrumDecisionAdapter
from runtime.tentacle.mobile.device import MobileDevice


@pytest.fixture
def app_and_coord():
    """创建 FastAPI app + coordinator（不启动真实服务器）."""
    planner = StaticPlanner(
        rules=[
            Rule(
                name="open_app",
                keywords=["打开", "open"],
                skill_sequence=["android.open_app"],
                node_args_templates=[{"package": "com.example.app"}],
            ),
        ],
        fallback_skill="android.find_text",
    )
    adapter = CerebrumDecisionAdapter(planner=planner)

    async def _engine(task: str, device: MobileDevice) -> list[ToolCall]:
        return await adapter.decide(task, device)

    coord = TentacleCoordinator(
        host="127.0.0.1",
        port=18771,
        decision_engine=_engine,
        dashboard_port=None,  # 不启动真实 Dashboard
    )

    # 注册 mock 设备
    import asyncio

    device = MobileDevice(
        tentacle_id="android-dash-001",
        device_meta={"brand": "Google", "model": "Pixel 8"},
    )
    # Python 3.12 no longer creates a main-thread loop implicitly. Keep both
    # operations on the same explicit loop, matching the pre-3.12 semantics.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(device.connect())
    loop.run_until_complete(coord.pool.register(device))

    app = FastAPI()
    app.include_router(create_tentacle_router(coord, require_auth=False))

    return app, coord


@pytest.fixture
def client(app_and_coord):
    app, _ = app_and_coord
    return TestClient(app)


def test_dashboard_html(client):
    """Dashboard 应返回 HTML 页面."""
    r = client.get("/api/tentacle/dashboard")
    assert r.status_code == 200
    assert "Echo Tentacle Dashboard" in r.text


def test_list_devices(client):
    """设备列表应包含 mock 设备."""
    r = client.get("/api/tentacle/devices")
    assert r.status_code == 200
    data = r.json()
    ids = [d["tentacle_id"] for d in data]
    assert "android-dash-001" in ids


def test_device_detail(client):
    """设备详情应返回完整信息."""
    r = client.get("/api/tentacle/devices/android-dash-001")
    assert r.status_code == 200
    data = r.json()
    assert data["tentacle_id"] == "android-dash-001"
    assert data["is_online"] is True


def test_device_detail_not_found(client):
    """不存在的设备应返回 404."""
    r = client.get("/api/tentacle/devices/nonexistent")
    assert r.status_code == 404


def test_submit_task(client):
    """提交任务应返回执行结果."""
    r = client.post(
        "/api/tentacle/task",
        json={
            "task": "打开示例应用",
            "tentacle_id": "android-dash-001",
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True
    assert data["steps"] == 1
    assert data["results"][0]["tool"] == "android.open_app"


def test_submit_task_auto_select(client):
    """不指定设备时应自动选择."""
    r = client.post("/api/tentacle/task", json={"task": "打开应用"})
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True


def test_submit_task_empty(client):
    """空任务应返回 400."""
    r = client.post("/api/tentacle/task", json={"task": ""})
    assert r.status_code == 400


def test_stats(client):
    """统计应包含设备信息."""
    r = client.get("/api/tentacle/stats")
    assert r.status_code == 200
    data = r.json()
    assert data["pool"]["total"] >= 1
    assert data["pool"]["online"] >= 1


def test_task_history(client):
    """执行任务后应出现在历史记录中."""
    # 先执行一个任务
    client.post(
        "/api/tentacle/task",
        json={
            "task": "打开应用",
            "tentacle_id": "android-dash-001",
        },
    )
    # 查看历史
    r = client.get("/api/tentacle/tasks")
    assert r.status_code == 200
    data = r.json()
    assert len(data) >= 1
    assert data[0]["task"] == "打开应用"
