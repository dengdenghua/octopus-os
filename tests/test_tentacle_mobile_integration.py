"""端到端集成测试 —— TentacleCoordinator + WebSocket Server + MobileDevice.

模拟手机端连接 → 发送 device/hello → 心跳 → 电脑端发送 tool/execute → 手机端返回结果.

需要 websockets 库：pip install websockets
"""

from __future__ import annotations

import asyncio
import json

import pytest
import pytest_asyncio
from runtime.tentacle.base import ToolCall
from runtime.tentacle.coordinator import TentacleCoordinator
from runtime.tentacle.mobile.device import MobileDevice

# ── fixtures ──────────────────────────────────────────────


@pytest_asyncio.fixture
async def coordinator():
    """启动协调器，测试结束后停止."""
    # Bind to an ephemeral free port to avoid Windows TIME_WAIT collisions
    # when many fixtures spin up and tear down ws_servers in rapid succession.
    # Disable the dashboard (port 8766 by default) to prevent the same kind
    # of collision on the dashboard socket.
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    coord = TentacleCoordinator(host="127.0.0.1", port=port, dashboard_port=None)
    coord._test_port = port  # expose for client fixture
    await coord.start()
    yield coord
    await coord.stop()


@pytest_asyncio.fixture
async def mock_mobile_client(coordinator):
    """模拟 Echo Mobile 客户端."""
    from websockets import connect

    ws = await connect(f"ws://127.0.0.1:{coordinator._test_port}")

    # 发送 device/hello
    hello_msg = {
        "jsonrpc": "2.0",
        "method": "device/hello",
        "params": {
            "tentacle_id": "android-test-001",
            "platform": "android",
            "brand": "Google",
            "model": "Pixel 8",
            "android_version": "14",
            "sdk": 34,
            "screen_size": [1080, 2400],
            "capabilities": ["android.tap", "android.swipe"],
            "version": "0.1.0",
        },
        "id": "hello-1",
    }
    await ws.send(json.dumps(hello_msg))

    # 等待注册确认
    response = await asyncio.wait_for(ws.recv(), timeout=2.0)
    data = json.loads(response)
    assert data.get("result", {}).get("registered") is True

    yield ws

    await ws.close()


# ── 基础测试 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_device_registration(coordinator, mock_mobile_client):
    """设备注册后应出现在 Pool 中."""
    device = coordinator.pool.get("android-test-001")
    assert device is not None
    assert device.tentacle_id == "android-test-001"
    assert device.is_online
    assert "android.tap" in device.capabilities


@pytest.mark.asyncio
async def test_pool_stats_after_registration(coordinator, mock_mobile_client):
    """注册后 Pool 统计应正确."""
    stats = coordinator.pool.stats()
    assert stats["total"] == 1
    assert stats["online"] == 1
    assert stats["offline"] == 0
    assert stats["busy"] == 0


@pytest.mark.asyncio
async def test_heartbeat_updates_meta(coordinator, mock_mobile_client):
    """心跳应更新设备元信息."""
    heartbeat_msg = {
        "jsonrpc": "2.0",
        "method": "device/heartbeat",
        "params": {
            "tentacle_id": "android-test-001",
            "ts": 1700000000000,
            "online": True,
            "current_app": "com.tencent.mm",
            "battery": 85,
            "is_charging": False,
            "last_screen_tree_hash": "abc123",
        },
        "id": "hb-1",
    }
    await mock_mobile_client.send(json.dumps(heartbeat_msg))
    await asyncio.sleep(0.1)

    device = coordinator.pool.get("android-test-001")
    assert device.meta.get("current_app") == "com.tencent.mm"
    assert device.meta.get("battery") == 85


# ── tool/execute 往返测试 ─────────────────────────────────


@pytest.mark.asyncio
async def test_tool_execute_roundtrip(coordinator, mock_mobile_client):
    """tool/execute 往返：电脑发指令 → 手机执行 → 返回结果."""
    device = coordinator.pool.get("android-test-001")
    assert device is not None

    # 手机端监听 tool/execute
    async def mobile_handler():
        while True:
            msg = await mock_mobile_client.recv()
            data = json.loads(msg)
            if data.get("method") == "tool/execute":
                call_id = data["id"]
                result_msg = {
                    "jsonrpc": "2.0",
                    "method": "tool/result",
                    "params": {
                        "call_id": call_id,
                        "success": True,
                        "data": "Tapped at (100, 200)",
                        "duration_ms": 150,
                    },
                    "id": call_id,
                }
                await mock_mobile_client.send(json.dumps(result_msg))
                break

    handler_task = asyncio.create_task(mobile_handler())

    call = ToolCall(
        call_id="tc-1",
        tentacle_id="android-test-001",
        tool="android.tap",
        args={"x": 100, "y": 200},
    )
    result = await device.execute(call)

    await handler_task

    assert result.success is True
    assert result.data == "Tapped at (100, 200)"
    assert result.duration_ms == 150


@pytest.mark.asyncio
async def test_tool_execute_device_offline(coordinator):
    """设备离线时 execute 应返回错误."""
    call = ToolCall(
        call_id="tc-2",
        tentacle_id="android-offline",
        tool="android.tap",
        args={"x": 100, "y": 200},
    )
    device = MobileDevice(
        tentacle_id="android-offline",
        ws_server=coordinator.ws_server,
    )
    await coordinator.pool.register(device)

    result = await device.execute(call)

    assert result.success is False
    assert result.error_code == -32011  # DEVICE_OFFLINE


@pytest.mark.asyncio
async def test_unknown_tool_returns_error(coordinator, mock_mobile_client):
    """未知工具应返回 TOOL_NOT_FOUND."""
    device = coordinator.pool.get("android-test-001")

    call = ToolCall(
        call_id="tc-3",
        tentacle_id="android-test-001",
        tool="android.nonexistent_tool",
        args={},
    )
    result = await device.execute(call)

    assert result.success is False
    assert result.error_code == -32003  # TOOL_NOT_FOUND


# ── task/execute 端到端测试 ───────────────────────────────


@pytest.mark.asyncio
async def test_task_execute_no_decision_engine(coordinator, mock_mobile_client):
    """无决策引擎时 task/execute 返回提示信息."""
    # 手机端发送 task/execute
    task_msg = {
        "jsonrpc": "2.0",
        "method": "task/execute",
        "params": {
            "task_id": "task-1",
            "task": "打开微信",
            "intent": "MOBILE",
            "confidence": 0.95,
            "tentacle_id": "android-test-001",
        },
        "id": "task-1",
    }
    await mock_mobile_client.send(json.dumps(task_msg))

    # 等待 task/result
    response = await asyncio.wait_for(mock_mobile_client.recv(), timeout=5.0)
    data = json.loads(response)

    assert data["method"] == "task/result"
    assert data["params"]["success"] is True
    assert "No decision engine" in data["params"]["response"]


@pytest.mark.asyncio
async def test_task_execute_with_decision_engine(coordinator, mock_mobile_client):
    """有决策引擎时 task/execute 执行 tool_call 列表."""

    # 定义简单决策引擎：生成两个 tap 指令
    async def simple_decision(task: str, device: MobileDevice) -> list[ToolCall]:
        return [
            ToolCall(
                call_id="tc-a",
                tentacle_id=device.tentacle_id,
                tool="android.tap",
                args={"x": 100, "y": 200},
            ),
            ToolCall(
                call_id="tc-b",
                tentacle_id=device.tentacle_id,
                tool="android.tap",
                args={"x": 300, "y": 400},
            ),
        ]

    # 重启协调器带决策引擎
    await coordinator.stop()
    import socket as _socket

    _s = _socket.socket()
    _s.bind(("127.0.0.1", 0))
    _engine_port = _s.getsockname()[1]
    _s.close()
    coord_with_engine = TentacleCoordinator(
        host="127.0.0.1",
        port=_engine_port,
        decision_engine=simple_decision,
        dashboard_port=None,
    )
    await coord_with_engine.start()

    try:
        # 重新连接
        from websockets import connect

        ws = await connect(f"ws://127.0.0.1:{_engine_port}")

        hello_msg = {
            "jsonrpc": "2.0",
            "method": "device/hello",
            "params": {
                "tentacle_id": "android-test-002",
                "platform": "android",
                "brand": "Google",
                "model": "Pixel 8",
                "android_version": "14",
                "sdk": 34,
                "screen_size": [1080, 2400],
                "capabilities": ["android.tap"],
                "version": "0.1.0",
            },
            "id": "hello-2",
        }
        await ws.send(json.dumps(hello_msg))
        resp = await asyncio.wait_for(ws.recv(), timeout=2.0)
        assert json.loads(resp)["result"]["registered"] is True

        # 手机端监听 tool/execute 并返回结果，然后等待 task/result
        received_calls = []
        task_result = None

        async def mobile_handler():
            nonlocal task_result
            while task_result is None:
                msg = await ws.recv()
                data = json.loads(msg)
                if data.get("method") == "tool/execute":
                    call_id = data["id"]
                    received_calls.append(call_id)
                    result_msg = {
                        "jsonrpc": "2.0",
                        "method": "tool/result",
                        "params": {
                            "call_id": call_id,
                            "success": True,
                            "data": f"Executed {call_id}",
                            "duration_ms": 100,
                        },
                        "id": call_id,
                    }
                    await ws.send(json.dumps(result_msg))
                elif data.get("method") == "task/result":
                    task_result = data
                    break

        handler_task = asyncio.create_task(mobile_handler())

        # 发送 task/execute
        task_msg = {
            "jsonrpc": "2.0",
            "method": "task/execute",
            "params": {
                "task_id": "task-2",
                "task": "点击两个按钮",
                "intent": "MOBILE",
                "confidence": 0.9,
                "tentacle_id": "android-test-002",
            },
            "id": "task-2",
        }
        await ws.send(json.dumps(task_msg))

        # 等待 handler 完成（收到 task/result）
        await asyncio.wait_for(handler_task, timeout=10.0)

        assert task_result is not None
        assert task_result["method"] == "task/result"
        assert task_result["params"]["success"] is True
        assert task_result["params"]["steps"] == 2
        assert "tc-a" in received_calls
        assert "tc-b" in received_calls

        await ws.close()
    finally:
        await coord_with_engine.stop()


@pytest.mark.asyncio
async def test_task_execute_device_not_found(coordinator, mock_mobile_client):
    """task/execute 指定不存在的设备应返回错误."""
    task_msg = {
        "jsonrpc": "2.0",
        "method": "task/execute",
        "params": {
            "task_id": "task-3",
            "task": "打开微信",
            "intent": "MOBILE",
            "confidence": 0.95,
            "tentacle_id": "android-nonexistent",
        },
        "id": "task-3",
    }
    await mock_mobile_client.send(json.dumps(task_msg))

    response = await asyncio.wait_for(mock_mobile_client.recv(), timeout=2.0)
    data = json.loads(response)

    assert data["method"] == "task/result"
    assert data["params"]["success"] is False
    assert "not found" in data["params"]["response"]


# ── 断开与统计 ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_device_disconnect_removes_from_pool(coordinator, mock_mobile_client):
    """设备断开后应从 Pool 中移除."""
    await mock_mobile_client.close()
    await asyncio.sleep(0.2)

    device = coordinator.pool.get("android-test-001")
    assert device is None or not device.is_online


@pytest.mark.asyncio
async def test_ws_server_stats(coordinator, mock_mobile_client):
    """WebSocket Server 统计应正确."""
    stats = coordinator.stats()
    assert stats["ws_connected"] == 1
    assert stats["pool"]["total"] == 1
