"""Tests for the bundled ``whale_eye`` plugin (鲸鱼之眼视觉判读 skill).

Covers:
  1. 插件可发现、可加载(与 project_wiki 同层的 bundled 插件)且展示中文名鲸鱼之眼
  2. ``whale_eye.read`` skill 注册进 SkillRegistry(注入假 registry)
  3. service 纯函数:resolve_config 缺 key 抛错 / image_to_data_url 编码
  4. judge 缺图片 / 缺 key 时的错误路径(不打真 agnes 网络)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from runtime.execution.suckers.registry import Skill
from runtime.platform.plugins.bundled.whale_eye import WhaleEyePlugin
from runtime.platform.plugins.bundled.whale_eye.service import (
    VisionUnavailableError,
    image_to_data_url,
    judge,
    resolve_config,
)
from runtime.platform.plugins.plugin_hub import PluginHub

PLUGIN_ID = "whale_eye"


def test_bundled_whale_eye_is_discoverable_and_loadable() -> None:
    hub = PluginHub()
    matches = [item for item in hub.discover() if item["id"] == PLUGIN_ID]

    assert len(matches) == 1
    assert matches[0]["bundled"] is True
    assert matches[0]["name"] == "鲸鱼之眼"  # display_name 折进 name,id 保持 ASCII
    assert hub.load(PLUGIN_ID) is not None


def test_list_plugins_shows_chinese_display_name() -> None:
    hub = PluginHub()
    hub.load(PLUGIN_ID)
    info = [p for p in hub.list_plugins() if p["id"] == PLUGIN_ID]

    assert len(info) == 1
    assert info[0]["name"] == "鲸鱼之眼"
    assert info[0]["display_name"] == "鲸鱼之眼"
    assert info[0]["id"] == "whale_eye"


def test_plugin_registers_skill_into_registry() -> None:
    plugin = WhaleEyePlugin()
    plugin.ctx = MagicMock()

    plugin.register_skills()

    assert plugin.ctx.register_skill.call_count == 1
    skill: Skill = plugin.ctx.register_skill.call_args[0][0]
    assert skill.name == "whale_eye.read"
    assert skill.trusted_source == "plugin://whale_eye"
    assert callable(skill.handler)


def test_skill_handler_returns_error_for_missing_image() -> None:
    plugin = WhaleEyePlugin()
    plugin.ctx = MagicMock()
    plugin.register_skills()
    skill: Skill = plugin.ctx.register_skill.call_args[0][0]

    result = skill.handler(image="", url="")
    assert isinstance(result, dict)
    assert "error" in result


def test_resolve_config_raises_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from runtime.platform.plugins.bundled.whale_eye import service as whale_eye_service

    # 让 custom_models.json 探测与 env 全部落空 → 无 key 路径
    monkeypatch.setattr(whale_eye_service, "_load_agnes_entry", lambda: {})
    monkeypatch.delenv("AGNES_API_KEY", raising=False)
    monkeypatch.delenv("AGNES_BASE_URL", raising=False)
    monkeypatch.delenv("AGNES_VISION_MODEL", raising=False)

    with pytest.raises(VisionUnavailableError):
        resolve_config(api_key="")
    # 显式传 key 则可用(不读文件/env)
    cfg = resolve_config(api_key="test-key")
    assert cfg["api_key"] == "test-key"
    assert cfg["model"] == "agnes-2.5-flash"


def test_image_to_data_url_encodes_png(tmp_path: Path) -> None:
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)

    url = image_to_data_url(shot)

    assert url.startswith("data:image/png;base64,")
    assert url.split(",", 1)[1] == "iVBORw0KGgoAAAAAAAAAAA=="


def test_chat_never_follows_redirect_or_forwards_key_to_private_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.platform.plugins.bundled.whale_eye import service as whale_eye_service

    captured: dict[str, object] = {}

    class _Response:
        status_code = 200

        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict[str, object]:
            return {"choices": [{"message": {"content": "ok"}}]}

    def _request(method, url, **kwargs):
        captured.update({"method": method, "url": url, **kwargs})
        return _Response()

    monkeypatch.setattr(whale_eye_service, "safe_httpx_request", _request)

    result = whale_eye_service._chat(
        {"base_url": "http://127.0.0.1:9000/v1", "model": "vision", "api_key": "secret"},
        "data:image/png;base64,AA==",
        "describe",
    )

    assert result == "ok"
    assert captured["follow_redirects"] is False
    assert captured["allow_private"] is True
    assert captured["read_cap_bytes"] == 8 * 1024 * 1024
    assert captured["headers"] == {
        "Content-Type": "application/json",
        "Authorization": "Bearer secret",
    }


def test_judge_missing_local_image(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from runtime.platform.plugins.bundled.whale_eye import service as whale_eye_service

    monkeypatch.setattr(whale_eye_service, "_load_agnes_entry", lambda: {})
    monkeypatch.delenv("AGNES_API_KEY", raising=False)

    result = judge(image=str(tmp_path / "nope.png"), output_dir=tmp_path / "out")

    assert "error" in result
    assert "图片不存在" in result["error"]


def test_judge_raises_without_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from runtime.platform.plugins.bundled.whale_eye import service as whale_eye_service

    monkeypatch.setattr(whale_eye_service, "_load_agnes_entry", lambda: {})
    monkeypatch.delenv("AGNES_API_KEY", raising=False)

    shot = tmp_path / "shot.png"
    shot.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)

    with pytest.raises(VisionUnavailableError):
        judge(image=str(shot), output_dir=tmp_path / "out")


def test_describe_image_retries_transient_failure() -> None:
    """A transient transcription failure retries once and succeeds."""
    from runtime.platform.plugins.bundled.whale_eye.service import describe_image

    with (
        # ``describe_image`` calls ``resolve_config`` first and returns None
        # early when no agnes api_key is reachable. Mocking only ``_chat``
        # left the test depending on the developer's untracked
        # data/custom_models.json — green here, red on CI and in any fresh
        # clone. Stub the config too so the retry contract is what is tested.
        patch(
            "runtime.platform.plugins.bundled.whale_eye.service.resolve_config",
            return_value={
                "base_url": "https://vision.invalid/v1",
                "model": "agnes-2.5-flash",
                "api_key": "test-key",
            },
        ),
        patch("runtime.platform.plugins.bundled.whale_eye.service._chat") as mock_chat,
    ):
        mock_chat.side_effect = [
            RuntimeError("transient timeout"),
            "这是一张截图: 界面顶部显示标题栏。",
        ]
        result = describe_image(image_b64="aGVsbG8=", timeout=30)
    assert result == "这是一张截图: 界面顶部显示标题栏。"
    assert mock_chat.call_count == 2


def test_describe_image_degrades_after_double_failure() -> None:
    """Two consecutive failures → None (caller drops the image, no crash)."""
    from runtime.platform.plugins.bundled.whale_eye.service import describe_image

    with (
        # ``describe_image`` calls ``resolve_config`` first and returns None
        # early when no agnes api_key is reachable. Mocking only ``_chat``
        # left the test depending on the developer's untracked
        # data/custom_models.json — green here, red on CI and in any fresh
        # clone. Stub the config too so the retry contract is what is tested.
        patch(
            "runtime.platform.plugins.bundled.whale_eye.service.resolve_config",
            return_value={
                "base_url": "https://vision.invalid/v1",
                "model": "agnes-2.5-flash",
                "api_key": "test-key",
            },
        ),
        patch("runtime.platform.plugins.bundled.whale_eye.service._chat") as mock_chat,
    ):
        mock_chat.side_effect = [RuntimeError("a"), RuntimeError("b")]
        result = describe_image(image_b64="aGVsbG8=", timeout=30)
    assert result is None
    assert mock_chat.call_count == 2


def test_describe_image_retries_empty_verdict() -> None:
    """An empty verdict counts as a failure and is retried once."""
    from runtime.platform.plugins.bundled.whale_eye.service import describe_image

    with (
        # ``describe_image`` calls ``resolve_config`` first and returns None
        # early when no agnes api_key is reachable. Mocking only ``_chat``
        # left the test depending on the developer's untracked
        # data/custom_models.json — green here, red on CI and in any fresh
        # clone. Stub the config too so the retry contract is what is tested.
        patch(
            "runtime.platform.plugins.bundled.whale_eye.service.resolve_config",
            return_value={
                "base_url": "https://vision.invalid/v1",
                "model": "agnes-2.5-flash",
                "api_key": "test-key",
            },
        ),
        patch("runtime.platform.plugins.bundled.whale_eye.service._chat") as mock_chat,
    ):
        mock_chat.side_effect = ["", "ok-description"]
        result = describe_image(image_b64="aGVsbG8=", timeout=30)
    assert result == "ok-description"
    assert mock_chat.call_count == 2

