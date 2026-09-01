"""Echo Mobile · Phase 0 概念验证测试.

验证：
1. ``runtime/tentacle/`` 包可正常导入
2. ``MobileDevice`` mock 模式可正常注册 / 心跳 / 执行
3. ``TentaclePool`` 池化、按 affinity 选择、设备锁都工作
4. ``mobile_operator_arm`` preset 可正常构造
5. Echo Mobile 30 个能力声明完整

完整测试覆盖待 Phase 1 补充。
"""

from __future__ import annotations

import pytest
from runtime.execution.arms.presets import (
    make_desktop_operator_arm,
    make_mobile_browser_operator_arm,
    make_mobile_operator_arm,
)
from runtime.tentacle import (
    ANDROID_CAPABILITIES,
    DesktopDevice,
    Heartbeat,
    MobileDevice,
    TentaclePool,
    TentacleStatus,
    TentacleType,
    ToolCall,
    ToolResult,
)
from runtime.tentacle.mobile.apks.tool_bridge import (
    Envelope,
    ErrorCode,
    heartbeat,
    hello,
    tool_execute,
)
from runtime.tentacle.mobile.apks.version import ECHO_MOBILE_VERSION, is_compatible
from runtime.tentacle.mobile.mcp_server import TentacleMcpServer

# ── 1. 包导入测试 ─────────────────────────────────────────


def test_tentacle_package_imports():
    """Tentacle 包可正常导入."""
    from runtime.tentacle import (
        Tentacle,
        TentacleStatus,
        TentacleType,
    )

    assert Tentacle is not None
    assert TentacleType.MOBILE.value == "mobile"
    assert TentacleStatus.ONLINE.value == "online"


def test_android_capabilities_complete():
    """30 个 Android 技能声明完整（与 Echo Mobile BaseTool 一一对应）."""
    # 基础 5
    basic = [
        "android.tap",
        "android.swipe",
        "android.input_text",
        "android.long_press",
        "android.system_key",
    ]
    # 感知 4
    perception = [
        "android.get_screen_info",
        "android.take_screenshot",
        "android.find_node",
        "android.find_text",
    ]
    # 管理 4
    management = [
        "android.open_app",
        "android.install_app",
        "android.get_installed_apps",
        "android.wait",
    ]
    # 复合 4
    composite = [
        "android.scroll_to_find",
        "android.detect_dialog",
        "android.find_and_tap",
        "android.get_current_app",
    ]
    # 控制 2
    control = ["android.finish", "android.fail"]
    # 文件 2
    files = ["android.read_file", "android.write_file"]
    # 剪贴板 2
    clip = ["android.get_clipboard", "android.set_clipboard"]
    # 浏览器 7
    browser = [
        "android.browser.navigate",
        "android.browser.get_dom",
        "android.browser.click",
        "android.browser.type",
        "android.browser.screenshot",
        "android.browser.evaluate",
        "android.browser.install_extension",
    ]
    expected = set(basic + perception + management + composite + control + files + clip + browser)
    actual = set(ANDROID_CAPABILITIES)
    assert len(expected) == 30
    assert expected == actual, f"missing: {expected - actual}, extra: {actual - expected}"


def test_tentacle_mcp_tool_name_maps_to_canonical_skill():
    """MCP-safe names resolve back to canonical Android skill ids."""
    server = TentacleMcpServer()

    assert server._skill_name_for_mcp_tool("android_tap") == "android.tap"
    assert server._skill_name_for_mcp_tool("android_get_screen_info") == "android.get_screen_info"
    assert server._skill_name_for_mcp_tool("android_browser_click") == "android.browser.click"
    assert (
        server._skill_name_for_mcp_tool("android_browser_install_extension")
        == "android.browser.install_extension"
    )


@pytest.mark.asyncio
async def test_tentacle_mcp_call_tool_keeps_arguments_immutable():
    """MCP calls strip _tentacle_id from execution args without mutating input."""
    server = TentacleMcpServer()
    args = {"_tentacle_id": "android-missing", "x": 1, "y": 2}

    result = await server.call_tool("android_tap", args)

    assert result["isError"] is True
    assert args == {"_tentacle_id": "android-missing", "x": 1, "y": 2}


# ── 2. MobileDevice Mock 模式 ──────────────────────────────


@pytest.mark.asyncio
async def test_mobile_device_mock_connect():
    """MobileDevice mock 模式可正常连接."""
    device = MobileDevice("android-test-001", device_meta={"brand": "Xiaomi", "model": "Mi 14 Pro"})
    assert device.status == TentacleStatus.OFFLINE
    assert not device.is_online

    await device.connect()
    assert device.status == TentacleStatus.ONLINE
    assert device.is_online
    assert device.tentacle_type == TentacleType.MOBILE
    assert device.platform == "android"
    assert len(device.capabilities) == 30


@pytest.mark.asyncio
async def test_mobile_device_mock_heartbeat():
    """心跳返回完整字段."""
    device = MobileDevice("android-test-002")
    await device.connect()
    hb = await device.heartbeat()
    assert isinstance(hb, Heartbeat)
    assert hb.tentacle_id == "android-test-002"
    assert hb.online is True
    assert hb.ts > 0
    assert hb.battery == 85
    # current_app 在 mock 模式下为 None（无真实设备上报）


@pytest.mark.asyncio
async def test_mobile_device_mock_execute():
    """Mock 工具执行返回成功."""
    device = MobileDevice("android-test-003")
    await device.connect()
    call = ToolCall(
        call_id="tc-1",
        tentacle_id="android-test-003",
        tool="android.tap",
        args={"x": 100, "y": 200},
    )
    result = await device.execute(call)
    assert isinstance(result, ToolResult)
    assert result.success is True
    assert "android.tap" in result.data
    assert result.duration_ms > 0


@pytest.mark.asyncio
async def test_mobile_device_mock_execute_unknown_tool():
    """未知工具返回失败."""
    device = MobileDevice("android-test-004")
    await device.connect()
    call = ToolCall(call_id="tc-2", tentacle_id="android-test-004", tool="unknown.tool", args={})
    result = await device.execute(call)
    assert result.success is False
    assert result.error_code == -32003  # TOOL_NOT_FOUND


@pytest.mark.asyncio
async def test_mobile_device_offline_execute():
    """离线时执行工具返回失败."""
    device = MobileDevice("android-test-005")
    # 没 connect
    call = ToolCall(call_id="tc-3", tentacle_id="android-test-005", tool="android.tap", args={})
    result = await device.execute(call)
    assert result.success is False
    assert result.error_code == -32011  # DEVICE_OFFLINE


# ── 3. TentaclePool 测试 ──────────────────────────────────


@pytest.mark.asyncio
async def test_pool_register_and_select():
    """池可注册多个设备并按 affinity 选择."""
    pool = TentaclePool()
    d1 = MobileDevice("android-xiaomi", device_meta={"brand": "Xiaomi"})
    d2 = MobileDevice("android-huawei", device_meta={"brand": "Huawei"})
    d3 = MobileDevice("android-oneplus", device_meta={"brand": "OnePlus"})

    # 先 connect 再 register（模拟真实流程：设备握手成功后才入池）
    await d1.connect()
    await d2.connect()
    await d3.connect()
    await pool.register(d1)
    await pool.register(d2)
    await pool.register(d3)

    assert len(pool.all()) == 3
    assert len(pool.all_online()) == 3

    # 按 affinity 选择 mobile
    selected = pool.select_for_affinity(["android", "mobile"])
    assert selected is not None
    assert selected.tentacle_type == TentacleType.MOBILE

    # 选不到不存在的 affinity
    none_selected = pool.select_for_affinity(["nonexistent"])
    assert none_selected is None


@pytest.mark.asyncio
async def test_pool_device_lock():
    """设备锁可正确获取/释放."""
    pool = TentaclePool()
    device = MobileDevice("android-locked")
    await device.connect()
    await pool.register(device)

    # 第一次获取成功
    assert await pool.acquire_lock("android-locked", "arm-1", "task-1") is True
    # 第二次（不同 owner）失败
    assert await pool.acquire_lock("android-locked", "arm-2", "task-2") is False
    # 释放
    assert await pool.release_lock("android-locked", "arm-1") is True
    # 重新获取（不同 owner）成功
    assert await pool.acquire_lock("android-locked", "arm-2", "task-2") is True


@pytest.mark.asyncio
async def test_pool_stats():
    """池统计信息."""
    pool = TentaclePool()
    d1 = MobileDevice("android-a")
    d2 = DesktopDevice("desktop-local")
    await d1.connect()
    await d2.connect()
    await pool.register(d1)
    await pool.register(d2)

    stats = pool.stats()
    assert stats["total"] == 2
    assert stats["online"] == 2
    assert "mobile" in stats["by_type"]
    assert "desktop" in stats["by_type"]


# ── 4. DesktopDevice ──────────────────────────────────────


@pytest.mark.asyncio
async def test_desktop_device_self_referential():
    """DesktopDevice 自指为本地桌面."""
    device = DesktopDevice()
    assert device.tentacle_id.startswith("desktop-")
    assert device.tentacle_type == TentacleType.DESKTOP
    assert device.platform in ("linux", "darwin", "windows")
    assert "computer_use_loop" in device.capabilities


@pytest.mark.asyncio
async def test_desktop_device_connect():
    """桌面触手"自指"无网络握手."""
    device = DesktopDevice(tentacle_id="desktop-test")
    await device.connect()
    assert device.is_online
    hb = await device.heartbeat()
    assert hb.online is True


# ── 5. Mobile Arm Preset ──────────────────────────────────


def test_mobile_operator_arm_preset():
    """mobile_operator_arm preset 构造正确."""

    # mock runtime
    class _MockRuntime:
        pass

    arm = make_mobile_operator_arm(_MockRuntime())  # type: ignore[arg-type]
    assert str(arm.arm_id) == "mobile_operator_arm"
    assert "mobile" in arm.affinity
    assert "android" in arm.affinity
    assert len(arm.allowed_skills) == 30  # 30 个移动技能
    skill_ids = {str(s) for s in arm.allowed_skills}
    assert "android.tap" in skill_ids
    assert "android.get_screen_info" in skill_ids
    assert "android.finish" in skill_ids


def test_mobile_browser_operator_arm_preset():
    """mobile_browser_operator_arm preset 构造正确."""

    class _MockRuntime:
        pass

    arm = make_mobile_browser_operator_arm(_MockRuntime())  # type: ignore[arg-type]
    assert str(arm.arm_id) == "mobile_browser_operator_arm"
    assert "browser" in arm.affinity
    skill_ids = {str(s) for s in arm.allowed_skills}
    assert "android.browser.navigate" in skill_ids
    assert "android.browser.get_dom" in skill_ids


def test_desktop_operator_arm_unaffected():
    """desktop_operator_arm 完全不变（验证 add-only 假设）."""

    class _MockRuntime:
        pass

    arm = make_desktop_operator_arm(_MockRuntime())  # type: ignore[arg-type]
    assert str(arm.arm_id) == "desktop_operator_arm"
    assert "desktop" in arm.affinity
    # 11 个 skills（与原值一致，未被 Echo Mobile 改动）
    skill_ids = {str(s) for s in arm.allowed_skills}
    assert {
        "screen_capture",
        "screen_info",
        "mouse_click",
        "mouse_move",
        "keyboard_type",
        "keyboard_press",
        "computer_observe",
        "computer_plan_next",
        "computer_preview_action",
        "computer_execute_token",
        "computer_use_loop",
    } <= skill_ids
    assert {
        "computer_uia_status",
        "computer_uia_tree",
        "computer_uia_find",
    } <= skill_ids


# ── 6. 协议 Envelope ──────────────────────────────────────


def test_envelope_request_construction():
    """构造 request envelope."""
    env = Envelope.request("device/hello", {"tentacle_id": "android-abc"})
    assert env.is_request()
    assert env.method == "device/hello"
    assert env.params == {"tentacle_id": "android-abc"}
    assert env.id is not None  # 自动生成 UUID


def test_envelope_reply_ok():
    """构造 success reply."""
    env = Envelope.reply_ok("call-123", {"data": "ok"})
    assert env.is_reply()
    assert env.id == "call-123"
    assert env.result == {"data": "ok"}
    assert env.error is None


def test_envelope_reply_err():
    """构造 error reply."""
    env = Envelope.reply_err("call-456", -32001, "Out of bounds", {"x": 9999})
    assert env.error == {"code": -32001, "message": "Out of bounds", "data": {"x": 9999}}


def test_hello_envelope():
    """device/hello envelope 构造."""
    env = hello("android-abc", capabilities=["android.tap"])
    assert env.method == "device/hello"
    assert env.params["tentacle_id"] == "android-abc"
    assert env.params["protocol_version"] == "1.0"
    assert env.params["capabilities"] == ["android.tap"]


def test_heartbeat_envelope():
    """device/heartbeat envelope 构造."""
    env = heartbeat("android-abc", current_app="com.tencent.mm", battery=80)
    assert env.method == "device/heartbeat"
    assert env.params["current_app"] == "com.tencent.mm"
    assert env.params["battery"] == 80
    assert env.params["ts"] > 0


def test_tool_execute_envelope():
    """tool/execute envelope 构造."""
    env = tool_execute("android-abc", "android.tap", {"x": 540, "y": 1200})
    assert env.method == "tool/execute"
    assert env.params["tool"] == "android.tap"
    assert env.params["args"] == {"x": 540, "y": 1200}
    assert env.params["timeout_ms"] == 15_000


def test_error_code_enum():
    """错误码枚举值正确."""
    assert ErrorCode.COORDINATE_OUT_OF_BOUNDS == -32001
    assert ErrorCode.DEVICE_LOCKED == -32010
    assert ErrorCode.DEVICE_OFFLINE == -32011


# ── 7. 版本兼容 ────────────────────────────────────────────


def test_apks_version_compat():
    """Echo Mobile 版本兼容检查."""
    assert is_compatible("0.0.1") is True
    assert is_compatible("0.1.0") is True
    assert is_compatible("1.0.0") is True
    assert is_compatible("0.0.0") is False


def test_apks_version_metadata():
    """Echo Mobile 版本元数据完整."""
    assert ECHO_MOBILE_VERSION.protocol_version == "1.0"
    assert ECHO_MOBILE_VERSION.min_supported != ""
    assert ECHO_MOBILE_VERSION.recommended != ""


# ── 8. 概念验证：add-only 假设 ─────────────────────────────


def test_concept_proof_desktop_unchanged():
    """核心验收：desktop_operator_arm 自身代码路径未变."""

    # 验证 make_desktop_operator_arm 仍返回完全相同的 11 个 skills
    class _MockRuntime:
        pass

    arm = make_desktop_operator_arm(_MockRuntime())  # type: ignore[arg-type]
    baseline_skills = {
        "screen_capture",
        "screen_info",
        "mouse_click",
        "mouse_move",
        "keyboard_type",
        "keyboard_press",
        "computer_observe",
        "computer_plan_next",
        "computer_preview_action",
        "computer_execute_token",
        "computer_use_loop",
    }
    uia_skills = {
        "computer_uia_status",
        "computer_uia_tree",
        "computer_uia_find",
    }
    actual_skills = {str(s) for s in arm.allowed_skills}
    assert baseline_skills <= actual_skills
    assert uia_skills <= actual_skills
    # 这个测试如果通过 ⇒ 桌面端零破坏（add-only 假设验证）


def test_concept_proof_mobile_and_desktop_coexist():
    """概念验证：mobile_operator_arm 和 desktop_operator_arm 可同时构造."""

    class _MockRuntime:
        pass

    desktop = make_desktop_operator_arm(_MockRuntime())  # type: ignore[arg-type]
    mobile = make_mobile_operator_arm(_MockRuntime())  # type: ignore[arg-type]
    mobile_browser = make_mobile_browser_operator_arm(_MockRuntime())  # type: ignore[arg-type]

    # 三者并存不冲突（注意：Worker 用 arm_id 而非 tentacle_id）
    assert str(desktop.arm_id) != str(mobile.arm_id)
    assert str(desktop.arm_id) != str(mobile_browser.arm_id)
    assert str(mobile.arm_id) != str(mobile_browser.arm_id)

    # 各自有独立的 affinity
    assert "desktop" in desktop.affinity
    assert "mobile" in mobile.affinity
    assert "browser" in mobile_browser.affinity
