"""Echo iOS Tentacle 单元测试.

验证：
1. ``runtime/tentacle/ios/`` 包可正常导入
2. 13 个 ``ios.*`` 能力声明完整（与 SKILL.md 一一对应）
3. ``IOSDevice`` 遵循 Tentacle Protocol（status / capabilities / heartbeat）
4. ``IOSDevice.execute`` 工具调度正确路由到 WDA 方法
5. ``DeviceHello`` iOS 字段解析与 ``to_meta`` 平台分支
6. ``_build_device_from_hello`` 工厂按平台创建对应 Tentacle
7. MCP ``load_all_skill_tools`` 同时加载 android + ios skills
8. ``_screenshot_tool_for`` / ``_platform_prefix`` 平台感知
9. ``VisionReAct.suggested_action_to_tool_calls`` 平台感知
"""

from __future__ import annotations

import pytest

from runtime.tentacle.base import TentacleStatus, TentacleType, ToolCall
from runtime.tentacle.coordinator import _build_device_from_hello
from runtime.tentacle.ios import IOS_CAPABILITIES, IOSDevice, WdaClient, WdaError
from runtime.tentacle.ios.capabilities import ios_capabilities, ios_skills_root
from runtime.tentacle.mobile.device import MobileDevice
from runtime.tentacle.mobile.mcp_server import (
    _find_skills_roots,
    _platform_prefix,
    _screenshot_tool_for,
    load_all_skill_tools,
)
from runtime.tentacle.mobile.vlm.client import SuggestedAction
from runtime.tentacle.mobile.vlm.react_with_vision import VisionReAct
from runtime.tentacle.transport.ws_server import DeviceHello

# ── 1. 包导入与能力声明 ─────────────────────────────────────


def test_ios_package_imports():
    """iOS tentacle 包可正常导入."""
    assert IOSDevice is not None
    assert WdaClient is not None
    assert WdaError is not None
    assert isinstance(IOS_CAPABILITIES, list)
    assert len(IOS_CAPABILITIES) > 0


def test_ios_skills_root_exists():
    """ios/skills 目录存在且可定位."""
    root = ios_skills_root()
    assert root.is_dir(), f"ios skills root not found: {root}"


def test_ios_capabilities_complete():
    """13 个 iOS 技能声明完整."""
    expected = {
        "ios.tap",
        "ios.double_tap",
        "ios.long_press",
        "ios.swipe",
        "ios.input_text",
        "ios.take_screenshot",
        "ios.home",
        "ios.find_element",
        "ios.get_screen_info",
        "ios.open_app",
        "ios.close_app",
        "ios.get_active_app",
        "ios.wait",
    }
    actual = set(ios_capabilities())
    assert expected == actual, f"missing: {expected - actual}, extra: {actual - expected}"
    # cached list matches manifest
    assert set(IOS_CAPABILITIES) == actual


# ── 2. IOSDevice 实例化与 Tentacle Protocol ──────────────────


def test_ios_device_construction():
    """IOSDevice 构造与默认值."""
    dev = IOSDevice("ios-test-1", base_url="http://localhost:8100")
    assert dev.tentacle_id == "ios-test-1"
    assert dev.tentacle_type == TentacleType.MOBILE
    assert dev.platform == "ios"
    assert dev.status == TentacleStatus.OFFLINE
    assert not dev.is_online
    assert len(dev.capabilities) == len(IOS_CAPABILITIES)
    assert dev.wda.base_url == "http://localhost:8100"
    assert dev.wda.is_connected is False


def test_wda_client_rejects_non_http_request_scheme():
    client = WdaClient()

    with pytest.raises(WdaError, match="must use http or https"):
        client._sync_request("GET", "file:///private/secret", None)


def test_ios_device_mark_online():
    """mark_online 同步切换状态（无需 WDA 连接）."""
    dev = IOSDevice("ios-test-2")
    assert dev.status == TentacleStatus.OFFLINE
    dev.mark_online()
    assert dev.status == TentacleStatus.ONLINE
    assert dev.is_online


# ── 3. 工具调度（mock WDA） ─────────────────────────────────


def _make_mocked_ios_device() -> IOSDevice:
    """构造一个 mark_online 且 WDA 方法全部 mock 的 IOSDevice."""
    dev = IOSDevice("ios-mock")
    dev.mark_online()

    async def fake_tap(x, y):
        return {"tapped": True}

    async def fake_double_tap(x, y):
        return {"double_tapped": True}

    async def fake_long_press(x, y, duration=2.0):
        return {"long_pressed": True}

    async def fake_swipe(x1, y1, x2, y2, duration=0.5):
        return {"swiped": True}

    async def fake_input_text(text):
        return {"entered": text}

    async def fake_screenshot():
        return "base64png"

    async def fake_home():
        return {"home": True}

    async def fake_find_element(
        *, accessibility_id=None, class_name=None, xpath=None, partial=False
    ):
        return {"ELEMENT": "elem-1"}

    async def fake_source():
        return {"tree": "<xml/>"}

    async def fake_window_size():
        return (393, 852)

    async def fake_launch_app(bundle_id):
        return {"launched": bundle_id}

    async def fake_terminate_app(bundle_id):
        return {"terminated": bundle_id}

    async def fake_active_app_info():
        return {"bundle_id": "com.apple.test"}

    async def fake_status():
        return {"state": "success"}

    dev._wda.tap = fake_tap
    dev._wda.double_tap = fake_double_tap
    dev._wda.long_press = fake_long_press
    dev._wda.swipe = fake_swipe
    dev._wda.input_text = fake_input_text
    dev._wda.screenshot = fake_screenshot
    dev._wda.home = fake_home
    dev._wda.find_element = fake_find_element
    dev._wda.source = fake_source
    dev._wda.window_size = fake_window_size
    dev._wda.launch_app = fake_launch_app
    dev._wda.terminate_app = fake_terminate_app
    dev._wda.active_app_info = fake_active_app_info
    dev._wda.status = fake_status
    return dev


@pytest.mark.asyncio
async def test_ios_execute_tap():
    """ios.tap 路由到 WDA.tap 并返回 ToolResult.ok."""
    dev = _make_mocked_ios_device()
    call = ToolCall(call_id="c1", tentacle_id="ios-mock", tool="ios.tap", args={"x": 100, "y": 200})
    result = await dev.execute(call)
    assert result.success
    assert result.data == {"tapped": True, "x": 100, "y": 200}
    assert result.duration_ms >= 0


@pytest.mark.asyncio
async def test_ios_execute_swipe():
    """ios.swipe 路由到 WDA.swipe."""
    dev = _make_mocked_ios_device()
    call = ToolCall(
        call_id="c2",
        tentacle_id="ios-mock",
        tool="ios.swipe",
        args={"x1": 100, "y1": 400, "x2": 100, "y2": 100, "duration_s": 0.3},
    )
    result = await dev.execute(call)
    assert result.success
    assert result.data["swiped"] is True
    assert result.data["from"] == [100, 400]
    assert result.data["to"] == [100, 100]


@pytest.mark.asyncio
async def test_ios_execute_screenshot():
    """ios.take_screenshot 返回 base64 数据."""
    dev = _make_mocked_ios_device()
    call = ToolCall(call_id="c3", tentacle_id="ios-mock", tool="ios.take_screenshot", args={})
    result = await dev.execute(call)
    assert result.success
    assert result.data["screenshot"] == "base64png"


@pytest.mark.asyncio
async def test_ios_execute_home():
    """ios.home 路由到 WDA.home."""
    dev = _make_mocked_ios_device()
    call = ToolCall(call_id="c4", tentacle_id="ios-mock", tool="ios.home", args={})
    result = await dev.execute(call)
    assert result.success
    assert result.data == {"home": True}


@pytest.mark.asyncio
async def test_ios_execute_open_app():
    """ios.open_app 路由到 WDA.launch_app 并更新 current_app."""
    dev = _make_mocked_ios_device()
    call = ToolCall(
        call_id="c5",
        tentacle_id="ios-mock",
        tool="ios.open_app",
        args={"bundle_id": "com.apple.mobilesafari"},
    )
    result = await dev.execute(call)
    assert result.success
    assert result.data == {"launched": "com.apple.mobilesafari"}
    assert dev.meta["current_app"] == "com.apple.mobilesafari"


@pytest.mark.asyncio
async def test_ios_execute_wait():
    """ios.wait 异步睡眠."""
    dev = _make_mocked_ios_device()
    call = ToolCall(call_id="c6", tentacle_id="ios-mock", tool="ios.wait", args={"duration_ms": 50})
    result = await dev.execute(call)
    assert result.success
    assert result.data == {"waited_ms": 50}


@pytest.mark.asyncio
async def test_ios_execute_unknown_tool():
    """未知工具返回 fail."""
    dev = _make_mocked_ios_device()
    call = ToolCall(call_id="c7", tentacle_id="ios-mock", tool="ios.nonexistent", args={})
    result = await dev.execute(call)
    assert not result.success
    assert "Unknown tool" in result.error_message


@pytest.mark.asyncio
async def test_ios_execute_offline_device():
    """离线设备执行返回 fail."""
    dev = IOSDevice("ios-offline")  # 未 mark_online
    call = ToolCall(call_id="c8", tentacle_id="ios-offline", tool="ios.tap", args={"x": 1, "y": 1})
    result = await dev.execute(call)
    assert not result.success
    assert "offline" in result.error_message.lower()


@pytest.mark.asyncio
async def test_ios_heartbeat_online():
    """在线设备心跳返回 online=True."""
    dev = _make_mocked_ios_device()
    hb = await dev.heartbeat()
    assert hb.online is True
    assert hb.tentacle_id == "ios-mock"


@pytest.mark.asyncio
async def test_ios_heartbeat_offline():
    """离线设备心跳返回 online=False."""
    dev = IOSDevice("ios-offline-hb")
    hb = await dev.heartbeat()
    assert hb.online is False


# ── 4. DeviceHello iOS 字段解析 ─────────────────────────────


def test_device_hello_ios_fields():
    """DeviceHello 解析 iOS 特定字段."""
    hello = DeviceHello(
        {
            "tentacle_id": "ios-udid-123",
            "platform": "ios",
            "model": "iPhone 15 Pro",
            "ios_version": "17.4",
            "udid": "00008101-XXXX",
            "wda_port": 8100,
            "bundle_id": "com.apple.mobilesafari",
            "screen_size": [393, 852],
        }
    )
    assert hello.platform == "ios"
    assert hello.ios_version == "17.4"
    assert hello.udid == "00008101-XXXX"
    assert hello.wda_port == 8100
    assert hello.bundle_id == "com.apple.mobilesafari"


def test_device_hello_ios_to_meta():
    """to_meta() iOS 分支返回 ios 特定字段."""
    hello = DeviceHello(
        {"tentacle_id": "x", "platform": "ios", "udid": "abc", "ios_version": "17.0"}
    )
    meta = hello.to_meta()
    assert meta["ios_version"] == "17.0"
    assert meta["udid"] == "abc"
    assert "wda_url" in meta
    assert "android_version" not in meta


def test_device_hello_android_to_meta_backward_compatible():
    """to_meta() Android 分支保持向后兼容（不含 ios 字段）."""
    hello = DeviceHello(
        {
            "tentacle_id": "x",
            "platform": "android",
            "brand": "Google",
            "android_version": "14",
            "sdk": 34,
        }
    )
    meta = hello.to_meta()
    assert meta["brand"] == "Google"
    assert meta["android_version"] == "14"
    assert "ios_version" not in meta
    assert "udid" not in meta


# ── 5. coordinator 工厂函数 ─────────────────────────────────


def test_build_device_from_hello_ios():
    """工厂函数为 iOS hello 创建 IOSDevice."""
    hello = DeviceHello(
        {
            "tentacle_id": "ios-factory",
            "platform": "ios",
            "udid": "udid-1",
            "ios_version": "17.0",
            "wda_port": 8100,
            "bundle_id": "com.test.app",
        }
    )
    device = _build_device_from_hello(hello, ws_server=None)
    assert isinstance(device, IOSDevice)
    assert device.platform == "ios"
    assert device.wda.bundle_id == "com.test.app"
    assert device.meta["udid"] == "udid-1"


def test_build_device_from_hello_android():
    """工厂函数为 Android hello 创建 MobileDevice（向后兼容）."""
    hello = DeviceHello(
        {
            "tentacle_id": "android-factory",
            "platform": "android",
            "brand": "Google",
        }
    )
    device = _build_device_from_hello(hello, ws_server=None)
    assert isinstance(device, MobileDevice)
    assert device.platform == "android"


# ── 6. MCP 双根加载 ─────────────────────────────────────────


def test_mcp_loads_both_android_and_ios_skills():
    """load_all_skill_tools 同时加载 android + ios skills."""
    tools = load_all_skill_tools()
    names = [t["name"] for t in tools]
    ios_names = [n for n in names if n.startswith("ios_")]
    android_names = [n for n in names if n.startswith("android_")]
    assert len(ios_names) == 13, f"expected 13 ios tools, got {len(ios_names)}"
    assert len(android_names) > 0, "no android tools loaded"
    # 无重复
    assert len(names) == len(set(names))


def test_find_skills_roots_returns_multiple():
    """_find_skills_roots 返回 mobile + ios 两个根目录."""
    roots = _find_skills_roots()
    assert len(roots) >= 2
    root_strs = [str(r) for r in roots]
    assert any("mobile" in r for r in root_strs)
    assert any("ios" in r for r in root_strs)


# ── 7. 平台感知辅助函数 ─────────────────────────────────────


def test_platform_prefix_ios():
    """_platform_prefix 对 iOS 设备返回 'ios'."""
    dev = IOSDevice("ios-x")
    assert _platform_prefix(dev) == "ios"


def test_platform_prefix_android():
    """_platform_prefix 对 Android 设备返回 'android'."""
    dev = MobileDevice("android-x")
    assert _platform_prefix(dev) == "android"


def test_screenshot_tool_for_ios():
    """_screenshot_tool_for 对 iOS 设备返回 'ios.take_screenshot'."""
    dev = IOSDevice("ios-x")
    assert _screenshot_tool_for(dev) == "ios.take_screenshot"


def test_screenshot_tool_for_android():
    """_screenshot_tool_for 对 Android 设备返回 'android.take_screenshot'."""
    dev = MobileDevice("android-x")
    assert _screenshot_tool_for(dev) == "android.take_screenshot"


# ── 8. VLM 平台感知 ─────────────────────────────────────────


def test_vlm_suggested_actions_ios():
    """VisionReAct 对 iOS 平台生成 ios.* 工具名."""
    actions = [
        SuggestedAction(action="tap", target="btn", coordinates=(100, 200)),
        SuggestedAction(action="swipe", target="list"),
        SuggestedAction(action="type", target="input", text="hello", coordinates=(50, 60)),
        SuggestedAction(action="long_press", target="icon", coordinates=(10, 20)),
    ]
    calls = VisionReAct.suggested_action_to_tool_calls(actions, "ios-dev", platform="ios")
    assert len(calls) > 0
    assert all(c.name.startswith("ios.") for c in calls)


def test_vlm_suggested_actions_android_default():
    """VisionReAct 默认平台（不传 platform）生成 android.* 工具名."""
    actions = [SuggestedAction(action="tap", target="btn", coordinates=(1, 2))]
    calls = VisionReAct.suggested_action_to_tool_calls(actions, "android-dev")
    assert all(c.name.startswith("android.") for c in calls)

