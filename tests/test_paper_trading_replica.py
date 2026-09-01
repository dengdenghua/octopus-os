"""Tests for the bundled ``paper_trading_replica`` plugin (模拟炒股·复刻版).

复刻版已从主插件拆出,停放在插件中心,暂不在工作台前端展示。本测试确认:
  1. 插件可被发现(PluginHub discover),bundled 标记正确
  2. 可加载(ModulePlugin 实例化)
  3. 页面路由 ``/api/plugins/paper-trading-replica/page`` 返回 200,且页面**不再**
     包含视图切换条(viewSwitch / embedView),证明复刻版里不再内嵌原版网页
  4. 健康检查路由返回复用主插件后端的说明
"""

from __future__ import annotations

from pathlib import Path

from runtime.platform.plugins.bundled.paper_trading_replica import (
    PaperTradingReplicaPlugin,
)
from runtime.platform.plugins.plugin_base import ModuleContext
from runtime.platform.plugins.plugin_hub import PluginHub

PLUGIN_ID = "paper_trading_replica"
PLUGIN_DIR = (
    Path(__file__).resolve().parents[1]
    / "runtime"
    / "platform"
    / "plugins"
    / "bundled"
    / "paper_trading_replica"
)


def test_replica_plugin_is_discoverable_in_hub() -> None:
    hub = PluginHub()
    matches = [item for item in hub.discover() if item["id"] == PLUGIN_ID]

    assert len(matches) == 1
    assert matches[0]["bundled"] is True
    assert "复刻版" in matches[0]["name"]
    assert hub.load(PLUGIN_ID) is not None


def test_replica_plugin_serves_page_and_has_no_embed() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    plugin = PaperTradingReplicaPlugin()
    ctx = ModuleContext(
        plugin_name=PLUGIN_ID,
        plugin_dir=str(PLUGIN_DIR),
        manifest=None,
        fastapi_app=app,
        config={},
    )
    plugin.on_load(ctx)

    client = TestClient(app)
    resp = client.get("/api/plugins/paper-trading-replica/page")
    assert resp.status_code == 200
    html = resp.text

    # 复刻版页面应保留复刻主体(自选星标 / 交易面板),不应再含原版网页嵌入相关结构
    assert "viewSwitch" not in html
    assert "embedView" not in html
    assert "embedFrame" not in html
    assert "wlBody" in html  # 自选行情
    assert "bsSubmit" in html  # 买卖面板

    health = client.get("/api/plugins/paper-trading-replica/health").json()
    assert health["ok"] is True
    assert health["backend"] == "/api/plugins/paper-trading"

