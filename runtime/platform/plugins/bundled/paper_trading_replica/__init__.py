"""模拟炒股·复刻版(paper_trading_replica)插件 — 从「模拟炒股」主插件拆出的复刻交易页。

背景:模拟炒股主插件(paper_trading)前端已改为内嵌平台**原版网页**(iframe)。
原先 1:1 复刻的交易页被拆到这里,作为一个独立、可插拔的模块**暂时停放在插件中心**,
留待后续继续打磨(自选星标 / 持仓带选 / toast / 图表周期等已完成的功能都在这份页面里)。

- 页面路由 ``/api/plugins/paper-trading-replica/page``;
- 页面复用主插件的后端 API(``/api/plugins/paper-trading/*`` 行情/自选/平台交易),
  不重复实现后端,只提供前端复刻页。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from runtime.platform.plugins.plugin_base import ModulePlugin

try:
    from fastapi import APIRouter
    from fastapi.responses import HTMLResponse
except ImportError:  # pragma: no cover
    APIRouter = None  # type: ignore[assignment,misc]
    HTMLResponse = None  # type: ignore[assignment,misc]


class PaperTradingReplicaPlugin(ModulePlugin):
    name = "paper_trading_replica"
    display_name = "模拟炒股 · 复刻版(待打磨)"
    version = "0.1.0"
    description = (
        "模拟炒股「复刻版」交易页 —— 从主插件拆出的 1:1 复刻界面,复用主插件后端"
        "(行情/自选/平台配资交易)。已停放在插件中心,暂不在工作台前端展示,留待继续打磨。"
    )
    author = "Echo"

    # ── 路由:页面 ────────────────────────────────────────

    def register_routes(self) -> None:
        if self.ctx is None or APIRouter is None:
            return
        app = self.ctx.fastapi_app
        if app is None:
            return
        plugin_dir = self.ctx.plugin_dir
        page_path = Path(plugin_dir) / "page" / "index.html"

        router = APIRouter(
            prefix="/api/plugins/paper-trading-replica", tags=["paper_trading_replica"]
        )

        @router.get("/page", response_class=HTMLResponse)
        def serve_page() -> HTMLResponse:
            html = "复刻版页面缺失(page/index.html)"
            try:
                if page_path.exists():
                    html = page_path.read_text(encoding="utf-8")
            except Exception as exc:  # noqa: BLE001 — 页面读坏不致命
                html = f"读取页面失败: {exc}"
            return HTMLResponse(content=html)

        @router.get("/health")
        def health() -> dict[str, Any]:
            return {
                "ok": True,
                "plugin": self.name,
                "backend": "/api/plugins/paper-trading",
                "note": "页面复用主插件 paper_trading 的后端 API,请确保主插件已加载。",
            }

        app.include_router(router)
