"""模拟炒股(paper_trading)插件 — 可插拔、带页面的模拟交易模块。

核心是自含行情模拟器(内置 A 股股票池 + 盘中随机游走报价),提供:

- 一个完整前端页面 ``/api/plugins/paper-trading/page``(行情/交易/平台交易/自选/持仓/成交);
- 一组交易 API(报价/下单/持仓/成交/重置);
- 一个 ``paper_trading.quote`` skill,让 agent 也能查询模拟报价;
- **平台配资盘**(``/platform/*``):申请资金 / 合约 / 持仓 / 真实买卖委托,
  写操作强制 ``confirm`` 二次确认后才真正提交到平台;
- 可选的**平台实时大盘**(``live_mode: true``):只读拉取配置的后端行情。

本地模拟账户持久化到 JSON,重启不丢;平台操作走平台账号。纯个人练习用。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import queue
import re
import threading
from pathlib import Path
from typing import Any

from runtime.execution.suckers.registry import Skill
from runtime.platform.plugins.plugin_base import ModulePlugin
from runtime.platform.process.paths import app_paths

from ._http_support import (
    _authenticated_host_page,
    _CheckInRequest,
    _CheckInScheduleIn,
    _CredentialsIn,
    _FavIn,
    _GroupIn,
    _OrderIn,
    _PlatformApplyIn,
    _PlatformCancelIn,
    _PlatformMoneyIn,
    _PlatformOrderIn,
    _PlatformSessionIn,
    _PlatformStockIn,  # noqa: F401 - retain the package's existing private symbol
    _proxy_disabled_page,
    _StockIn,
)
from .live import (
    DEFAULT_BASE_URL,
    LiveDataSource,
    LivePushClient,
    _normalize_push,
)
from .quote_hub import CallbackQuoteSourceAdapter, PollingQuoteSource, QuoteHub
from .service import PaperTradingEngine, WatchlistStore, _build_engine, is_trading_time
from .signin import (
    DEFAULT_SIGN_IN_HOUR,
    DEFAULT_SIGN_IN_MINUTE,
    DailySignInScheduler,
    PlatformSignInService,
)
from .upstream_url import secure_upstream_origin, upstream_origin

try:
    from fastapi import APIRouter, HTTPException, Request
    from fastapi.responses import HTMLResponse, Response, StreamingResponse
except ImportError:  # pragma: no cover
    APIRouter = None  # type: ignore[assignment,misc]
    HTTPException = None  # type: ignore[assignment,misc]
    Request = None  # type: ignore[assignment,misc]
    HTMLResponse = None  # type: ignore[assignment,misc]
    Response = None  # type: ignore[assignment,misc]
    StreamingResponse = None  # type: ignore[assignment,misc]

_logger = logging.getLogger(__name__)


def _explicitly_enabled(config: dict[str, Any], key: str) -> bool:
    """Accept only a real boolean ``true`` for security-sensitive switches."""
    return config.get(key) is True


def _enabled_by_default(config: dict[str, Any], key: str) -> bool:
    """Default to enabled while still rejecting false and string booleans."""
    return config.get(key, True) is True


def _quote_code(value: str) -> str:
    """Validate one A-share code and add the exchange suffix used upstream."""

    clean = str(value or "").strip().lower()
    if not clean:
        return ""
    if "." in clean:
        code, suffix = clean.rsplit(".", 1)
        if re.fullmatch(r"\d{6}", code) and suffix in {"sh", "sz", "bj"}:
            return f"{code}.{suffix}"
        return ""
    if not re.fullmatch(r"\d{6}", clean):
        return ""
    if clean.startswith(("4", "8", "92")):
        suffix = "bj"
    elif clean.startswith(("5", "6", "9")):
        suffix = "sh"
    else:
        suffix = "sz"
    return f"{clean}.{suffix}"


def _quote_codes(value: str, *, limit: int, required: bool = False) -> list[str]:
    raw = [part.strip() for part in str(value or "").split(",") if part.strip()]
    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw:
        code = _quote_code(item)
        if not code:
            raise ValueError(f"无效股票代码: {item}")
        if code not in seen:
            normalized.append(code)
            seen.add(code)
    if required and not normalized:
        raise ValueError("至少需要一个股票代码")
    if len(normalized) > limit:
        raise ValueError(f"每个连接最多订阅 {limit} 只股票")
    return normalized


class _LazyLivePushEventClient:
    """Create the credentialed upstream only when a real subscriber arrives."""

    def __init__(self, owner: PaperTradingPlugin) -> None:
        self._owner = owner

    def subscribe(self, event: str, params: list[str], callback: Any) -> None:
        push = self._owner._push_client()
        if push is None:
            raise RuntimeError("平台实时行情源尚未配置可信 HTTPS 或登录凭证")
        push.subscribe(event, params, callback)

    def unsubscribe(self, event: str, callback: Any) -> None:
        # Never call _push_client() during teardown: doing so could create a
        # fresh authenticated socket while the plugin is unloading.
        push = self._owner.push
        if push is not None:
            push.unsubscribe(event, callback)


class PaperTradingPlugin(ModulePlugin):
    name = "paper_trading"
    display_name = "模拟炒股"
    version = "0.6.0"
    description = (
        "模拟炒股练习插件 — 本地模拟交易面板 + 「平台交易」页对接平台配资盘"
        "(申请资金/合约/持仓/真实买卖委托,全部二次确认),可选用 live_mode 只读"
        "接入平台实时大盘。纯个人练习用。"
    )
    author = "Echo"

    def __init__(self) -> None:
        super().__init__()
        self.engine: PaperTradingEngine | None = None
        self.live: LiveDataSource | None = None
        self.quote_live: LiveDataSource | None = None
        self.push: LivePushClient | None = None
        self.quote_hub: QuoteHub | None = None
        self.watchlists: WatchlistStore | None = None
        self.sign_in_service: PlatformSignInService | None = None
        self.sign_in_scheduler: DailySignInScheduler | None = None
        self.auto_trade = False  # 程序化/agent 自动下单开关(默认关,须显式开启)
        self.proxy_origin = False  # on_load 后按默认开启配置和安全前置条件计算
        self._proxy_base_url = ""
        self._proxy_state_dir = "~/.echo/data/paper_trading"
        self._proxy_credentials_file = "~/.echo/data/paper_trading/credentials.json"
        self._authenticated_host = False
        self._trusted_single_user_local_proxy = False
        self._proxy_mounted = False
        self._host_app: Any = None
        self._push_lock = threading.RLock()
        self._push_accepting = False
        self._quote_hub_enabled = True
        self._quote_hub_max_codes_per_client = 100
        self._quote_hub_max_clients = 50
        self._quote_hub_max_union_codes = 500
        self._quote_hub_queue_size = 50

    # ── 生命周期 ─────────────────────────────────────────

    def _publish_trusted_local_proxy_state(self, enabled: bool) -> None:
        """Publish the narrow localhost auth exception on the owning app."""

        app = self._host_app
        if app is not None and getattr(app, "state", None) is not None:
            app.state.paper_trading_trusted_single_user_local_proxy = bool(enabled)

    def on_load(self, ctx: Any) -> None:
        # A failed reload must never leave the previous generation's public
        # localhost exception active in the host auth middleware.
        self._publish_trusted_local_proxy_state(False)
        self._proxy_mounted = False
        self._stop_sign_in()
        self._stop_quote_hub()
        self.sign_in_service = None
        self.sign_in_scheduler = None
        with self._push_lock:
            self._push_accepting = False
            self.live = None
            self.quote_live = None
        self._stop_push()
        cfg = dict(ctx.config or {})
        self.auto_trade = False
        self.proxy_origin = False
        self._quote_hub_enabled = _enabled_by_default(cfg, "quote_hub_enabled")
        self._quote_hub_max_codes_per_client = max(
            1, min(500, int(cfg.get("quote_hub_max_codes_per_client", 100)))
        )
        self._quote_hub_max_clients = max(1, min(1000, int(cfg.get("quote_hub_max_clients", 50))))
        self._quote_hub_max_union_codes = max(
            self._quote_hub_max_codes_per_client,
            min(5000, int(cfg.get("quote_hub_max_union_codes", 500))),
        )
        self._quote_hub_queue_size = max(2, min(500, int(cfg.get("quote_hub_queue_size", 50))))
        app = getattr(ctx, "fastapi_app", None)
        self._host_app = app
        self._publish_trusted_local_proxy_state(False)
        self._authenticated_host = bool(
            app is not None and getattr(getattr(app, "state", None), "echo_require_auth", False)
        )
        local_single_user_host = bool(
            app is not None
            and getattr(
                getattr(app, "state", None),
                "echo_allow_local_workspace_access",
                False,
            )
        )
        trusted_local_proxy_requested = _explicitly_enabled(
            cfg,
            "trusted_single_user_local_proxy",
        )
        self._trusted_single_user_local_proxy = bool(
            self._authenticated_host and local_single_user_host and trusted_local_proxy_requested
        )
        if trusted_local_proxy_requested and not self._trusted_single_user_local_proxy:
            _logger.warning(
                "paper_trading: 已拒绝 trusted_single_user_local_proxy（仅限开启认证的 "
                "local + loopback 单用户宿主）"
            )
        initial_cash = float(cfg.get("initial_cash", 1_000_000))
        data_dir = str(cfg.get("data_dir") or (app_paths().data_dir / "paper_trading"))
        self.engine = _build_engine(initial_cash=initial_cash, data_dir=data_dir)
        self.watchlists = WatchlistStore(data_dir=data_dir)
        credentials_file = str(cfg.get("credentials_file") or (Path(data_dir) / "credentials.json"))
        self._proxy_base_url = str(cfg.get("base_url") or DEFAULT_BASE_URL)
        secure_upstream = bool(secure_upstream_origin(self._proxy_base_url))
        proxy_upstream = bool(upstream_origin(self._proxy_base_url))
        # 可选平台实时行情(只读)。无凭证/失败自动降级,不影响本地模拟。
        live_requested = _explicitly_enabled(cfg, "live_mode")
        if live_requested and secure_upstream and not self._authenticated_host:
            self.live = LiveDataSource.from_config(
                cfg, state_dir=data_dir, credentials_file=credentials_file
            )
        elif live_requested and not secure_upstream:
            _logger.warning("paper_trading: 已拒绝启用平台行情与凭证（base_url 不是 HTTPS）")
        elif live_requested:
            _logger.warning("paper_trading: 已拒绝启用平台行情与凭证（主应用已开启认证）")
        # QuoteHub is a separate read-only market-data plane.  An authenticated
        # host may run it with one server-owned credential because account,
        # portfolio and trading routes remain disabled below.
        if self._quote_hub_enabled and secure_upstream:
            self.quote_live = self.live or LiveDataSource.from_config(
                cfg, state_dir=data_dir, credentials_file=credentials_file
            )
        elif self._quote_hub_enabled:
            _logger.warning(
                "paper_trading: 行情中心已就绪，但平台主源需配置可信 HTTPS/WSS 后才会连接"
            )
        auto_trade_requested = _explicitly_enabled(cfg, "auto_trade")
        self.auto_trade = (
            auto_trade_requested
            and self.live is not None
            and secure_upstream
            and not self._authenticated_host
        )
        if auto_trade_requested and not self.auto_trade:
            _logger.warning("paper_trading: 已拒绝启用自动交易（安全平台连接未启用）")
        proxy_requested = _enabled_by_default(cfg, "proxy_origin")
        unsafe_proxy_accepted = _enabled_by_default(cfg, "allow_same_origin_third_party_scripts")
        self.proxy_origin = (
            proxy_requested
            and unsafe_proxy_accepted
            and proxy_upstream
            and (not self._authenticated_host or self._trusted_single_user_local_proxy)
        )
        if proxy_requested and not self.proxy_origin:
            reasons = []
            if not unsafe_proxy_accepted:
                reasons.append("缺少 allow_same_origin_third_party_scripts=true")
            if not proxy_upstream:
                reasons.append("base_url 不是有效 HTTP(S) 地址")
            _logger.warning(
                "paper_trading: 已拒绝挂载同源原站代理（%s）",
                "；".join(reasons) or "安全前置条件不满足",
            )
        if self._authenticated_host and self._trusted_single_user_local_proxy:
            _logger.warning(
                "paper_trading: 已显式开启认证本机的单用户原站代理；"
                "auto_trade 仍强制关闭，共享或非 loopback 宿主不会进入此模式"
            )
        elif self._authenticated_host:
            _logger.warning(
                "paper_trading: 主应用已开启认证，已禁用共享账户、交易和原站代理；"
                "只读行情中心仍受主应用认证保护"
            )
        self._proxy_state_dir = data_dir
        self._proxy_credentials_file = credentials_file
        if self.proxy_origin:
            try:
                self.sign_in_service = PlatformSignInService(
                    base_url=self._proxy_base_url,
                    state_dir=data_dir,
                )
                self.sign_in_scheduler = DailySignInScheduler(
                    self.sign_in_service,
                    state_dir=data_dir,
                    enabled=_explicitly_enabled(cfg, "auto_sign_in"),
                    hour=int(cfg.get("auto_sign_in_hour", DEFAULT_SIGN_IN_HOUR)),
                    minute=int(cfg.get("auto_sign_in_minute", DEFAULT_SIGN_IN_MINUTE)),
                )
            except (TypeError, ValueError) as exc:
                _logger.warning("paper_trading: 自动签到初始化失败: %s", exc)
        with self._push_lock:
            self._push_accepting = (self.live or self.quote_live) is not None
        if self._quote_hub_enabled:
            sources: dict[str, Any] = {
                "platform_ws": CallbackQuoteSourceAdapter(_LazyLivePushEventClient(self))
            }
            if self.quote_live is not None:
                sources["platform_rest"] = PollingQuoteSource(
                    self.quote_live.client.fetch_real_quotes,
                    interval=max(
                        1.0,
                        min(
                            30.0,
                            float(cfg.get("quote_hub_platform_rest_interval", 3)),
                        ),
                    ),
                )
            self.quote_hub = QuoteHub(
                sources,
                primary="platform_ws",
                failure_threshold=max(1, min(20, int(cfg.get("quote_hub_failure_threshold", 3)))),
                recovery_threshold=max(1, min(20, int(cfg.get("quote_hub_recovery_threshold", 2)))),
                stale_after=max(3.0, min(300.0, float(cfg.get("quote_hub_stale_after", 12)))),
                subscriber_queue_size=self._quote_hub_queue_size,
                max_subscribers=self._quote_hub_max_clients,
                max_codes_per_subscriber=self._quote_hub_max_codes_per_client,
                max_union_codes=self._quote_hub_max_union_codes,
                primary_recovery_seconds=max(
                    0.0,
                    min(
                        3600.0,
                        float(cfg.get("quote_hub_primary_recovery_seconds", 120)),
                    ),
                ),
                health_check_enabled=is_trading_time,
            )
        super().on_load(ctx)

    def _stop_quote_hub(self) -> None:
        hub, self.quote_hub = self.quote_hub, None
        if hub is None:
            return
        try:
            hub.close()
        except Exception as exc:  # noqa: BLE001 - lifecycle cleanup must remain idempotent
            _logger.warning("paper_trading: 停止统一行情中心失败: %s", exc)

    def _stop_push(self) -> None:
        with self._push_lock:
            push, self.push = self.push, None
        if push is None:
            return
        try:
            push.stop()
        except Exception as exc:  # noqa: BLE001 - lifecycle cleanup must remain idempotent
            _logger.warning("paper_trading: 停止实时推送失败: %s", exc)

    def _stop_sign_in(self) -> None:
        scheduler = self.sign_in_scheduler
        if scheduler is None:
            return
        try:
            scheduler.stop()
        except Exception as exc:  # noqa: BLE001 - 生命周期清理必须幂等
            _logger.warning("paper_trading: 停止自动签到失败: %s", exc)

    def on_start(self, ctx: Any) -> None:
        self._publish_trusted_local_proxy_state(
            self._trusted_single_user_local_proxy and self._proxy_mounted
        )
        scheduler = self.sign_in_scheduler
        if scheduler is not None:
            scheduler.start()

    def on_stop(self, ctx: Any) -> None:
        self._publish_trusted_local_proxy_state(False)
        self._stop_sign_in()
        self._stop_quote_hub()
        with self._push_lock:
            self._push_accepting = False
            self.live = None
            self.quote_live = None
        self._stop_push()

    def on_unload(self, ctx: Any) -> None:
        self._publish_trusted_local_proxy_state(False)
        self._proxy_mounted = False
        self._stop_sign_in()
        self._stop_quote_hub()
        with self._push_lock:
            self._push_accepting = False
            self.live = None
            self.quote_live = None
        self._stop_push()

    # ── Skill:agent 可查询模拟报价 ────────────────────────

    def register_skills(self) -> None:
        if self.ctx is None or self.engine is None:
            return
        with contextlib.suppress(Exception):
            self.ctx.register_skill(
                Skill(
                    name="paper_trading.quote",
                    description=(
                        "查询模拟炒股(paper_trading)插件里某个 A 股的当前模拟报价"
                        "(现价/涨跌幅/昨收)。参数 code 必填,如 '600519'。纯模拟数据,"
                        "不连真实行情。"
                    ),
                    summary="查询模拟行情报价(code 必填)",
                    affinity=["trading", "stock", "quote", "market"],
                    cost_profile="low",
                    trusted_source="plugin://paper_trading",
                    handler=self._quote_skill,
                )
            )
        if self.quote_hub is not None and self.quote_live is not None:
            with contextlib.suppress(Exception):
                self.ctx.register_skill(
                    Skill(
                        name="paper_trading.live_quotes",
                        description=(
                            "从统一行情中心读取一组 A 股的最新真实行情快照。参数 codes 可为"
                            "逗号分隔字符串或代码列表。返回值带 source、received_at、seq 和 stale；"
                            "stale=true 的价格不得用于交易、自动信号或涨跌提醒。"
                        ),
                        summary="读取统一真实行情快照(codes 必填)",
                        affinity=["trading", "stock", "quote", "market", "realtime"],
                        cost_profile="low",
                        trusted_source="plugin://paper_trading/quote_hub",
                        handler=self._live_quotes_skill,
                    )
                )
        if self.auto_trade:
            with contextlib.suppress(Exception):
                self.ctx.register_skill(
                    Skill(
                        name="paper_trading.trade",
                        description=(
                            "在平台配资盘**真实下单**(平台为模拟盘):买入/卖出/申请资金/追加资金/"
                            "提盈/撤单。**仅当用户明确要求自动交易且 auto_trade 已开启时才可调用**;"
                            "未开启时一律拒绝。参数:action 必填(buy/sell/apply/add_capital/withdraw/"
                            "cancel)+ 对应字段(buy/sell 需要 contract_id、stock_code、stock_name、qty、"
                            "entrust_type(0限价/1市价)、price(限价必填);apply 需要 principal、multiple、"
                            "contract_type(1按天/2按周/3按月);add_capital/withdraw 需要 contract_id、money;"
                            "cancel 需要 order_id、contract_id)。qty 须为 100 的整数倍。这是真实操作,"
                            "提交即被平台受理、无法撤销,下单前务必核对参数。可用 dry_run=true 先试运行。"
                        ),
                        summary="平台真实下单(action 必填;仅 auto_trade 开启可用)",
                        affinity=["trading", "stock", "order", "trade", "position"],
                        cost_profile="low",
                        trusted_source="plugin://paper_trading",
                        handler=self._trade_skill,
                    )
                )

    def _quote_skill(self, code: str = "", **_kwargs: Any) -> dict[str, Any]:
        engine = self.engine
        if engine is None:
            return {"error": "paper_trading 插件未初始化"}
        if not code:
            return {"error": "需要 code 参数,如 600519"}
        quote = engine.quote(code)
        if quote is None:
            return {"error": f"未知股票代码: {code}"}
        return quote

    def _live_quotes_skill(self, codes: Any = "", **_kwargs: Any) -> dict[str, Any]:
        hub = self.quote_hub
        if hub is None:
            return {"ok": False, "error": "统一行情中心未启用", "quotes": []}
        raw = codes if isinstance(codes, (list, tuple, set)) else str(codes or "").split(",")
        try:
            normalized = [_quote_code(str(code)) for code in raw if str(code).strip()]
            if not normalized or any(not code for code in normalized):
                raise ValueError("需要有效的 codes 参数，如 600519,000001")
            if len(set(normalized)) > self._quote_hub_max_codes_per_client:
                raise ValueError(f"一次最多查询 {self._quote_hub_max_codes_per_client} 只股票")
        except ValueError as exc:
            return {"ok": False, "error": str(exc), "quotes": []}
        snapshot = hub.snapshot(normalized)
        quotes = snapshot.get("quotes") or []
        usable = bool(quotes) and all(not quote.get("stale") for quote in quotes)
        return {"ok": True, "usable": usable, **snapshot}

    def quote_snapshot(self, codes: Any = None) -> dict[str, Any]:
        """ServiceBus facade used by agents without exposing hub internals."""

        hub = self.quote_hub
        if hub is None:
            return {"type": "snapshot", "seq": 0, "source": "", "quotes": []}
        return hub.snapshot(codes)

    def quote_status(self) -> dict[str, Any]:
        hub = self.quote_hub
        return hub.status() if hub is not None else {"enabled": False, "state": "disabled"}

    # ── agent 自动下单 skill(auto_trade 开启后可用) ──────

    def _trade_skill(self, action: str = "", dry_run: bool = False, **kw: Any) -> dict[str, Any]:
        """``paper_trading.trade``:agent 直接向平台真实下单。

        仅在 ``auto_trade=true`` 时放行(等于用户授权程序化交易);缺授权一律拒绝。
        ``dry_run=true`` 只返回执行计划,不真正提交。
        """
        if not self.auto_trade:
            return {
                "ok": False,
                "error": (
                    "自动交易未开启(auto_trade=false):已拒绝自动下单。"
                    "请让用户在「平台交易」页人工操作,或开启 auto_trade 配置后重启插件。"
                ),
            }
        client = self._platform_client()
        if client is None:
            return {"ok": False, "error": "未连接平台账号,请先在「平台交易」页登录"}
        plan = self._trade_plan(client, action, **kw)
        if isinstance(plan, dict) and "error" in plan:
            return {"ok": False, "error": plan["error"]}
        what, fn, fn_kw = plan
        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "action": what,
                "params": dict(fn_kw),
                "note": "未提交:dry_run=true 仅生成执行计划",
            }
        return self._platform_write(fn, confirm=True, what=what, **fn_kw)

    def _trade_plan(self, client, action: str, **kw: Any) -> Any:
        """把 agent 的 action 参数翻译成平台客户端调用 (what, fn, kwargs)。"""
        action = (action or "").strip().lower()

        if action in ("buy", "sell"):
            missing = [
                k for k in ("contract_id", "stock_code", "stock_name", "qty") if not kw.get(k)
            ]
            if missing:
                return {
                    "error": f"{'买入' if action == 'buy' else '卖出'}缺少参数: {', '.join(missing)}"
                }
            entrust_type = int(kw.get("entrust_type", 0))
            price = kw.get("price")
            if entrust_type == 0 and not price:
                return {"error": "限价单需要 price(市价 entrust_type=1 可不填)"}
            qty = int(kw.get("qty") or 0)
            if qty <= 0 or qty % 100 != 0:
                return {"error": "数量必须为正的 100 整数倍"}
            fn = client.buy if action == "buy" else client.sell
            return (
                "真实买入" if action == "buy" else "真实卖出",
                fn,
                {
                    "contract_id": str(kw["contract_id"]),
                    "stock_code": str(kw["stock_code"]),
                    "stock_name": str(kw["stock_name"]),
                    "entrust_type": entrust_type,
                    "price": price,
                    "number": qty,
                },
            )
        if action == "apply":
            missing = [k for k in ("principal", "multiple") if not kw.get(k)]
            if missing:
                return {"error": f"申请资金缺少参数: {', '.join(missing)}"}
            principal = float(kw.get("principal") or 0)
            if principal < 100:
                return {"error": "保证金至少 100 元"}
            return (
                "申请资金",
                client.apply_contract,
                {
                    "contract_type": int(kw.get("contract_type", 1)),
                    "principal": principal,
                    "multiple": int(kw.get("multiple", 10)),
                },
            )
        if action in ("add_capital", "withdraw"):
            missing = [k for k in ("contract_id", "money") if not kw.get(k)]
            if missing:
                return {
                    "error": f"{'追加资金' if action == 'add_capital' else '提盈'}缺少参数: {', '.join(missing)}"
                }
            money = float(kw.get("money") or 0)
            if money <= 0:
                return {"error": "金额必须大于 0"}
            fn = client.add_capital if action == "add_capital" else client.withdraw_profit
            return (
                "追加资金" if action == "add_capital" else "提取盈利",
                fn,
                {"contract_id": str(kw["contract_id"]), "money": money},
            )
        if action == "cancel":
            missing = [k for k in ("order_id", "contract_id") if not kw.get(k)]
            if missing:
                return {"error": f"撤单缺少参数: {', '.join(missing)}"}
            return (
                "撤单",
                client.cancel_order,
                {"order_id": str(kw["order_id"]), "contract_id": str(kw["contract_id"])},
            )
        return {
            "error": f"未知 action: {action!r}(可选 buy/sell/apply/add_capital/withdraw/cancel)"
        }

    # ── 平台配资盘(真实交易)辅助 ──────────────────────────

    def _platform_client(self):
        """取已登录的平台客户端;未启用 live_mode / 未配凭证返回 None。"""
        live = self.live
        if live is None or not live.configured:
            return None
        return live.client

    def _push_client(self) -> LivePushClient | None:
        """懒创建并启动实时推送客户端(常驻 WS,行情推送而非轮询)。

        首次调用时启动后台线程;凭证缺失/依赖缺失时返回 None(纯降级,不抛异常)。
        供页面 SSE、量化策略回调、/live/push/* 使用。
        """
        with self._push_lock:
            live = self.live or self.quote_live
            if not self._push_accepting or live is None or not live.configured:
                return None
            push = self.push
            if push is None:
                try:
                    push = LivePushClient(
                        live.client,
                        host=live.client.base_url,
                        auto_start=True,
                    )
                except Exception as exc:  # noqa: BLE001
                    _logger.warning("paper_trading: 推送客户端创建失败: %s", exc)
                    return None
                self.push = push
            return push

    def _platform_read(self, fn, **kw):
        """只读平台接口:统一包装,失败/未登录优雅降级为 {ok:false}。"""
        client = self._platform_client()
        if client is None:
            return {
                "ok": False,
                "error": "未连接平台账号,请先在「平台交易」页登录(配置平台手机号+密码)",
            }
        try:
            return {"ok": True, "data": fn(**kw)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def _platform_write(self, fn, *, confirm: bool, what: str, **kw):
        """平台真实操作(申请资金/买卖/追加/提盈/撤单)。

        必须显式 ``confirm=True`` 才会真正打到平台;否则直接拒绝。
        任何网络/业务错误都包装为 {ok:false, error},不让页面崩。
        """
        if not confirm:
            return {
                "ok": False,
                "error": f"已拦截:该操作将在平台真实执行({what}),请在页面确认后重试",
            }
        client = self._platform_client()
        if client is None:
            return {
                "ok": False,
                "error": "未连接平台账号,请先在「平台交易」页登录",
            }
        try:
            return {"ok": True, "data": fn(**kw)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    # ── 页面 + API 路由 ───────────────────────────────────

    def register_routes(self) -> None:
        if self.ctx is None or APIRouter is None:
            return
        engine = self.engine
        if engine is None:
            return
        # degrade, don't crash:无 FastAPI 上下文(如纯 skill 环境)则不挂路由
        app = self.ctx.fastapi_app
        if app is None:
            return
        plugin_dir = self.ctx.plugin_dir
        page_path = Path(plugin_dir) / "page" / "index.html"

        router = APIRouter(prefix="/api/plugins/paper-trading", tags=["paper_trading"])

        # ── 统一行情中心 ───────────────────────────────────
        # These quote-only routes are safe to keep under an authenticated
        # host: the global auth middleware protects /api/plugins/*, while no
        # platform account, portfolio, credential or trading route is mounted.
        hub = self.quote_hub
        if hub is not None:

            def _public_quote_status() -> dict[str, Any]:
                status = dict(hub.status())
                # This endpoint is shared by every authenticated tenant.  Keep
                # aggregate health/capacity metrics, but never reveal another
                # tenant's watch symbols, reference counts or provider error
                # details (which may contain internal connection metadata).
                status.pop("subscribers", None)
                status.pop("subscribed_codes", None)
                status.pop("ref_counts", None)
                for source in status.get("sources", {}).values():
                    if isinstance(source, dict):
                        source.pop("last_error", None)
                status["enabled"] = True
                status["source_labels"] = {
                    "platform_ws": "平台直连",
                    "platform_rest": "平台快照备用",
                    "tdx": "通达信备用",
                    "westock": "腾讯自选股备用",
                }
                return status

            def _sse_frame(event: str, payload: dict[str, Any]) -> str:
                seq = int(payload.get("seq") or 0)
                body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                return f"id: {seq}\nevent: {event}\ndata: {body}\n\n"

            @router.get("/quotes/status")
            def quote_hub_status() -> dict[str, Any]:
                # Observability must never create a credentialed connection.
                return _public_quote_status()

            @router.get("/quotes/snapshot")
            def quote_hub_snapshot(codes: str = "") -> dict[str, Any]:
                try:
                    selected = _quote_codes(
                        codes,
                        limit=self._quote_hub_max_codes_per_client,
                    )
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
                snapshot = hub.snapshot(selected or None)
                return {"ok": True, **snapshot, "status": _public_quote_status()}

            @router.get("/quotes/stream")
            def quote_hub_stream(request: Request, codes: str = "") -> Any:
                if StreamingResponse is None:
                    raise HTTPException(status_code=503, detail="SSE 不可用")
                try:
                    selected = _quote_codes(
                        codes,
                        limit=self._quote_hub_max_codes_per_client,
                        required=True,
                    )
                    subscription = hub.subscribe(
                        selected,
                        queue_size=self._quote_hub_queue_size,
                        replay=False,
                    )
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
                except RuntimeError as exc:
                    raise HTTPException(status_code=429, detail=str(exc)) from exc
                hub.start()

                async def _gen():
                    try:
                        yield "retry: 3000\n\n"
                        yield _sse_frame("snapshot", hub.snapshot(selected))
                        yield _sse_frame("status", _public_quote_status())
                        while True:
                            if await request.is_disconnected():
                                break
                            try:
                                item = await asyncio.to_thread(subscription.get, 15.0)
                            except queue.Empty:
                                yield ": keepalive\n\n"
                                yield _sse_frame("status", _public_quote_status())
                                continue
                            kind = str(item.get("type") or "quotes")
                            if kind == "closed":
                                break
                            if kind == "source_changed":
                                yield _sse_frame("status", _public_quote_status())
                                if item.get("quotes"):
                                    yield _sse_frame("quote", item)
                            elif kind == "snapshot":
                                yield _sse_frame("snapshot", item)
                            else:
                                yield _sse_frame("quote", item)
                    finally:
                        subscription.close()

                return StreamingResponse(
                    _gen(),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache, no-transform",
                        "X-Accel-Buffering": "no",
                        "Connection": "keep-alive",
                    },
                )

        # The plugin owns one process-wide engine/platform account.  An
        # authenticated host therefore fails closed unless the host has
        # already proved its local + loopback posture and the operator opted
        # into the dedicated trusted-single-user flag.  Shared/authenticated
        # deployments keep only the inert explanation routes below.
        if self._authenticated_host and not self._trusted_single_user_local_proxy:

            @router.get("/page", response_class=HTMLResponse)
            @router.get("/watch", response_class=HTMLResponse)
            def serve_authenticated_host_notice() -> HTMLResponse:
                return HTMLResponse(
                    content=_authenticated_host_page(),
                    headers={
                        "Content-Security-Policy": (
                            "default-src 'none'; style-src 'unsafe-inline'; "
                            "base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
                        )
                    },
                )

            app.include_router(router)
            return

        # 平台原站同源反代(单用户无认证宿主默认开启)。
        # 开启后 /page 里的 iframe 才有东西可指。
        # 段名必须是 origin 而非 assets —— 详见 proxy.py 的说明。
        proxy_mounted = False
        if self.proxy_origin:
            from .proxy import register_origin_proxy

            proxy_mounted = register_origin_proxy(
                router,
                base_url=self._proxy_base_url,
                state_dir=self._proxy_state_dir,
                credentials_file=self._proxy_credentials_file,
            )
        self._proxy_mounted = proxy_mounted
        self._publish_trusted_local_proxy_state(
            self._trusted_single_user_local_proxy and proxy_mounted
        )

        @router.get("/page", response_class=HTMLResponse)
        def serve_page() -> HTMLResponse:
            if not proxy_mounted:
                return HTMLResponse(
                    content=_proxy_disabled_page(self._proxy_base_url),
                    headers={
                        "Content-Security-Policy": (
                            "default-src 'none'; style-src 'unsafe-inline'; "
                            "base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
                        )
                    },
                )
            html = "模拟炒股页面缺失(page/index.html)"
            try:
                if page_path.exists():
                    html = page_path.read_text(encoding="utf-8")
            except Exception as exc:  # noqa: BLE001
                html = f"读取页面失败: {exc}"
            return HTMLResponse(content=html)

        @router.get("/watch", response_class=HTMLResponse)
        def serve_watch_page() -> HTMLResponse:
            watch_html = "盯盘页面缺失(page/watch.html)"
            try:
                watch_path = Path(plugin_dir) / "page" / "watch.html"
                if watch_path.exists():
                    watch_html = watch_path.read_text(encoding="utf-8")
            except Exception as exc:  # noqa: BLE001
                watch_html = f"读取盯盘页面失败: {exc}"
            return HTMLResponse(content=watch_html)

        @router.get("/watch.js")
        def serve_watch_script() -> Response:
            script = "console.error('盯盘脚本缺失(page/watch.js)')"
            try:
                script_path = Path(plugin_dir) / "page" / "watch.js"
                if script_path.exists():
                    script = script_path.read_text(encoding="utf-8")
            except Exception as exc:  # noqa: BLE001
                script = f"console.error({json.dumps(f'读取盯盘脚本失败: {exc}')})"
            return Response(
                content=script,
                media_type="application/javascript",
                headers={"Cache-Control": "no-cache"},
            )

        # ── 平台每日签到 ───────────────────────────────────
        # 页面按钮和后台定时器都走这里。后端只允许签到当天，提交前后均查询状态，
        # 因此重复点击、自动任务重试和多实例竞争都会收敛为“今日已签”。

        @router.get("/check-in/status")
        def check_in_status() -> dict[str, Any]:
            service = self.sign_in_service
            scheduler = self.sign_in_scheduler
            if service is None:
                return {
                    "ok": False,
                    "signed": False,
                    "error": "平台签到未启用",
                    "schedule": {"enabled": False, "running": False},
                }
            result = service.status()
            result["schedule"] = (
                scheduler.snapshot()
                if scheduler is not None
                else {"enabled": False, "running": False}
            )
            return result

        @router.get("/check-in/config")
        def check_in_config() -> dict[str, Any]:
            service = self.sign_in_service
            if service is None:
                return {"ok": False, "error": "平台签到未启用"}
            return service.reward_config()

        @router.post("/check-in/session")
        def sync_check_in_session(payload: _PlatformSessionIn) -> dict[str, Any]:
            service = self.sign_in_service
            scheduler = self.sign_in_scheduler
            if service is None:
                return {"ok": False, "signed": False, "error": "平台签到未启用"}
            result = service.sync_browser_token(payload.token)
            if result.get("ok") and scheduler is not None and scheduler.enabled:
                result = scheduler.run_once()
            if scheduler is not None:
                result["schedule"] = scheduler.snapshot()
            return result

        @router.post("/check-in")
        def check_in(payload: _CheckInRequest) -> dict[str, Any]:
            if not payload.confirm:
                return {
                    "ok": False,
                    "signed": False,
                    "error": "已拦截：请确认后再签到",
                }
            service = self.sign_in_service
            scheduler = self.sign_in_scheduler
            if service is None:
                return {"ok": False, "signed": False, "error": "平台签到未启用"}
            result = scheduler.run_once() if scheduler is not None else service.sign_in()
            if scheduler is not None:
                result["schedule"] = scheduler.snapshot()
            return result

        @router.post("/check-in/schedule")
        def configure_check_in_schedule(payload: _CheckInScheduleIn) -> dict[str, Any]:
            scheduler = self.sign_in_scheduler
            if scheduler is None:
                return {"ok": False, "error": "自动签到未启用"}
            try:
                schedule = scheduler.configure(
                    enabled=payload.enabled,
                    hour=payload.hour,
                    minute=payload.minute,
                )
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            return {"ok": True, "schedule": schedule}

        # In the explicit authenticated-local mode expose only the original
        # site bridge and the narrow check-in surface.  Do not fall through to
        # the process-wide simulated account, order, platform-account or reset
        # APIs; those remain unavailable until state is principal-scoped.
        if self._authenticated_host:
            app.include_router(router)
            return

        @router.get("/symbols")
        def symbols() -> dict[str, Any]:
            return {"symbols": [{"code": c, "name": n} for c, n, _b in engine.universe]}

        @router.get("/quotes")
        def quotes() -> dict[str, Any]:
            engine.tick()
            return {"quotes": engine.quotes(), "trading": is_trading_time()}

        @router.get("/live/overview")
        def live_overview() -> dict[str, Any]:
            live = self.live
            if live is None:
                return {
                    "available": False,
                    "enabled": False,
                    "source": "",
                    "message": "未启用 live_mode,仅本地模拟行情",
                }
            return live.overview()

        @router.get("/live/watch")
        def live_watch(force: bool = False) -> dict[str, Any]:
            live = self.live
            if live is None:
                return {
                    "available": False,
                    "enabled": False,
                    "source": "",
                    "message": "未启用 live_mode,仅本地模拟行情",
                }
            return live.watch(force=force)

        @router.get("/live/status")
        def live_status() -> dict[str, Any]:
            live = self.live
            if live is None:
                return {
                    "enabled": False,
                    "configured": False,
                    "available": False,
                    "account": "",
                    "source": "",
                }
            return {
                "enabled": True,
                "configured": live.configured,
                "available": live.available,
                "account": live.account if live.configured else "",
                "source": live.client.base_url,
            }

        @router.post("/live/credentials")
        def set_credentials(payload: _CredentialsIn) -> dict[str, Any]:
            live = self.live
            if live is None:
                raise HTTPException(status_code=400, detail="未启用 live_mode")
            return live.save_credentials(payload.phone, payload.password)

        @router.post("/live/credentials/clear")
        def clear_credentials() -> dict[str, Any]:
            live = self.live
            if live is None:
                return {"ok": False, "message": "未启用 live_mode"}
            return live.clear_credentials()

        @router.get("/live/push/status")
        def live_push_status() -> dict[str, Any]:
            """实时推送连接状态(WS 是否在连、各事件订阅与最近推送时间)。"""
            push = self.push  # status inspection must not create a network connection
            raw = (
                push.status()
                if push is not None
                else {
                    "enabled": False,
                    "running": False,
                    "connected": False,
                }
            )
            return {
                **raw,
                "quote_hub": _public_quote_status() if hub is not None else {"enabled": False},
            }

        @router.get("/live/push/subscribe")
        def live_push_subscribe(event: str = "kLineRealTime", codes: str = "") -> dict[str, Any]:
            """订阅某个推送事件并返回其最新(已解码)快照。

            - ``event``: kLineRealTime(个股实时+十档盘口+分时) / todayStock(大盘) /
              stockPosition(持仓) / itemByStepDetailsV3(分时详情) 等;
            - ``codes``: 逗号分隔的代码列表,如 "605080.sh,003032.sz"(仅个股类事件需要)。
            订阅后该事件会持续推送到本地;可通过 /live/push/stream 或 Python 回调消费。
            """
            if event == "kLineRealTime" and hub is not None:
                try:
                    params = _quote_codes(
                        codes,
                        limit=self._quote_hub_max_codes_per_client,
                        required=True,
                    )
                    hub.subscribe(
                        params,
                        subscriber_id="legacy-http",
                        queue_size=self._quote_hub_queue_size,
                        replay=False,
                    )
                    hub.start()
                except (ValueError, RuntimeError) as exc:
                    return {"ok": False, "error": str(exc)}
                return {
                    "ok": True,
                    "event": event,
                    "params": params,
                    "latest": hub.snapshot(params),
                    "deprecated": True,
                }
            push = self._push_client()
            if push is None:
                return {"ok": False, "error": "推送未启用(缺凭证/依赖)"}
            params = [c.strip() for c in codes.split(",") if c.strip()]
            push.subscribe(event, params)
            return {"ok": True, "event": event, "params": params, "latest": push.latest(event)}

        @router.get("/live/push/latest")
        def live_push_latest(event: str = "kLineRealTime", light: bool = True) -> dict[str, Any]:
            """查询某个推送事件的当前最新快照(已解码)。

            - ``event``: kLineRealTime / todayStock / stockPosition 等;
            - ``light=true``: kLineRealTime 返回紧凑字段(默认,省流量)。
            供量化策略/页面按需取当前状态,无需订阅。
            """
            if event == "kLineRealTime" and hub is not None:
                snapshot = hub.snapshot()
                return {
                    "ok": True,
                    "event": event,
                    "latest": {"data": snapshot.get("quotes") or [], "raw": False},
                    "source": snapshot.get("source") or "",
                    "seq": snapshot.get("seq") or 0,
                }
            push = self._push_client()
            if push is None:
                return {"ok": False, "error": "推送未启用(缺凭证/依赖)"}
            latest = push.latest(event)
            if latest is None:
                return {"ok": True, "event": event, "latest": None}
            if light and event == "kLineRealTime":
                latest = _normalize_push(event, latest)
            return {"ok": True, "event": event, "latest": latest}

        @router.get("/live/push/stream")
        def live_push_stream(light: bool = True) -> Any:
            """SSE 实时推送流:把平台 WS 推送逐条转发给浏览器(替代轮询)。

            事件格式(每行一个):
                event: <event_name>
                data: <JSON>
            心跳每 15s 发一次 comment 保活。
            """
            push = self._push_client()
            if push is None or StreamingResponse is None:
                return {"ok": False, "error": "推送未启用(缺凭证/依赖)"}

            send_q: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue(
                maxsize=self._quote_hub_queue_size
            )

            def _on_event(event: str, data: dict[str, Any]) -> None:
                item = (event, _normalize_push(event, data) if light else data)
                try:
                    send_q.put_nowait(item)
                except queue.Full:
                    with contextlib.suppress(queue.Empty):
                        send_q.get_nowait()
                    with contextlib.suppress(queue.Full):
                        send_q.put_nowait(item)

            # 全事件回调:之后任何 /live/push/subscribe 订阅的新推送都会转发到这里。
            push.add_global_callback(_on_event)
            # 立即补发所有已订阅事件的最新快照,一接入就有数据。
            for ev in list(push._subs.keys()):
                latest = push.latest(ev)
                if latest is not None:
                    _on_event(ev, latest)

            def _gen():
                try:
                    while True:
                        try:
                            event, data = send_q.get(timeout=15)
                            yield f"event: {event}\n"
                            yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                        except queue.Empty:
                            yield ": keepalive\n\n"
                finally:
                    push.remove_global_callback(_on_event)

            return StreamingResponse(
                _gen(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache, no-transform",
                    "X-Accel-Buffering": "no",
                },
            )

        @router.post("/live/refresh")
        def refresh_live() -> dict[str, Any]:
            live = self.live
            if live is None:
                return {
                    "available": False,
                    "enabled": False,
                    "message": "未启用 live_mode,仅本地模拟行情",
                }
            return live.overview(force=True)

        # ── 平台配资盘(真实交易) ───────────────────────────
        # 只读:合约/持仓/委托/费率/档位/卖出面板;操作类一律要求 confirm。

        @router.get("/platform/status")
        def platform_status() -> dict[str, Any]:
            live = self.live
            if live is None or not live.configured:
                return {
                    "enabled": True,
                    "configured": False,
                    "available": False,
                    "account": "",
                    "source": self.live.client.base_url if self.live else "",
                    "auto_trade": self.auto_trade,
                }
            return {
                "enabled": True,
                "configured": True,
                "available": True,
                "account": live.account,
                "source": live.client.base_url,
                "auto_trade": self.auto_trade,
            }

        @router.get("/platform/overview")
        def platform_overview() -> dict[str, Any]:
            client = self._platform_client()
            if client is None:
                return {
                    "ok": False,
                    "error": "未连接平台账号,请先登录",
                    "member": {},
                    "contracts": [],
                }
            try:
                member = client.get_member_info()
                contracts = client.list_contracts()
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": str(exc), "member": {}, "contracts": []}
            return {"ok": True, "member": member, "contracts": contracts}

        @router.get("/platform/contracts")
        def platform_contracts() -> dict[str, Any]:
            if self.live is None:
                return {"ok": False, "error": "未启用平台接入"}
            return self._platform_read(self.live.client.list_contracts)

        @router.get("/platform/contract-details")
        def platform_contract_details() -> dict[str, Any]:
            if self.live is None:
                return {"ok": False, "error": "未启用平台接入"}
            return self._platform_read(self.live.client.contract_list_full)

        @router.get("/platform/positions")
        def platform_positions() -> dict[str, Any]:
            if self.live is None:
                return {"ok": False, "error": "未启用平台接入"}
            return self._platform_read(self.live.client.positions)

        @router.get("/platform/orders")
        def platform_orders(type_: int = 1, current: int = 1, size: int = 20) -> dict[str, Any]:
            if self.live is None:
                return {"ok": False, "error": "未启用平台接入"}
            return self._platform_read(
                self.live.client.orders, type_=type_, current=current, size=size
            )

        @router.get("/platform/money-records")
        def platform_money_records(
            contract_id: str = "",
            type_: int | str = "",
            date: str = "",
            current: int = 1,
            size: int = 20,
        ) -> dict[str, Any]:
            if self.live is None:
                return {"ok": False, "error": "未启用平台接入"}
            return self._platform_read(
                self.live.client.money_records,
                contract_id=contract_id,
                type_=type_,
                date=date,
                current=current,
                size=size,
            )

        @router.get("/platform/rate-table")
        def platform_rate_table() -> dict[str, Any]:
            if self.live is None:
                return {"ok": False, "error": "未启用平台接入"}
            return self._platform_read(self.live.client.rate_table)

        @router.get("/platform/apply-options")
        def platform_apply_options() -> dict[str, Any]:
            if self.live is None:
                return {"ok": False, "error": "未启用平台接入"}
            return self._platform_read(self.live.client.apply_options)

        @router.get("/platform/sell-panel")
        def platform_sell_panel(contract_id: str = "", stock_code: str = "") -> dict[str, Any]:
            if self.live is None:
                return {"ok": False, "error": "未启用平台接入"}
            return self._platform_read(
                self.live.client.sell_panel,
                contract_id=contract_id,
                stock_code=stock_code,
            )

        @router.post("/platform/apply-contract")
        def platform_apply_contract(payload: _PlatformApplyIn) -> dict[str, Any]:
            if self.live is None:
                return {"ok": False, "error": "未启用平台接入"}
            return self._platform_write(
                self.live.client.apply_contract,
                confirm=payload.confirm,
                what="申请配资资金",
                contract_type=payload.contract_type,
                principal=payload.principal,
                multiple=payload.multiple,
            )

        @router.post("/platform/buy")
        def platform_buy(payload: _PlatformOrderIn) -> dict[str, Any]:
            if self.live is None:
                return {"ok": False, "error": "未启用平台接入"}
            return self._platform_write(
                self.live.client.buy,
                confirm=payload.confirm,
                what="真实买入",
                contract_id=payload.contract_id,
                stock_code=payload.stock_code,
                stock_name=payload.stock_name,
                entrust_type=payload.entrust_type,
                price=payload.price,
                number=payload.qty,
            )

        @router.post("/platform/sell")
        def platform_sell(payload: _PlatformOrderIn) -> dict[str, Any]:
            if self.live is None:
                return {"ok": False, "error": "未启用平台接入"}
            return self._platform_write(
                self.live.client.sell,
                confirm=payload.confirm,
                what="真实卖出",
                contract_id=payload.contract_id,
                stock_code=payload.stock_code,
                stock_name=payload.stock_name,
                entrust_type=payload.entrust_type,
                price=payload.price,
                number=payload.qty,
            )

        @router.post("/platform/add-capital")
        def platform_add_capital(payload: _PlatformMoneyIn) -> dict[str, Any]:
            if self.live is None:
                return {"ok": False, "error": "未启用平台接入"}
            return self._platform_write(
                self.live.client.add_capital,
                confirm=payload.confirm,
                what="追加资金",
                contract_id=payload.contract_id,
                money=payload.money,
            )

        @router.post("/platform/withdraw-profit")
        def platform_withdraw_profit(payload: _PlatformMoneyIn) -> dict[str, Any]:
            if self.live is None:
                return {"ok": False, "error": "未启用平台接入"}
            return self._platform_write(
                self.live.client.withdraw_profit,
                confirm=payload.confirm,
                what="提取盈利",
                contract_id=payload.contract_id,
                money=payload.money,
            )

        @router.post("/platform/cancel-order")
        def platform_cancel_order(payload: _PlatformCancelIn) -> dict[str, Any]:
            if self.live is None:
                return {"ok": False, "error": "未启用平台接入"}
            return self._platform_write(
                self.live.client.cancel_order,
                confirm=payload.confirm,
                what="撤单",
                order_id=payload.order_id,
                contract_id=payload.contract_id,
            )

        @router.get("/quote/{code}")
        def quote(code: str) -> dict[str, Any]:
            q = engine.quote(code)
            if q is None:
                raise HTTPException(status_code=404, detail=f"未知股票: {code}")
            return q

        @router.get("/kline/{code}")
        def kline(code: str, days: int = 60) -> dict[str, Any]:
            if days < 5:
                days = 5
            if days > 250:
                days = 250
            candles = engine.kline(code, days=days)
            if not candles:
                raise HTTPException(status_code=404, detail=f"未知股票: {code}")
            return {"code": code, "candles": candles}

        @router.get("/orderbook/{code}")
        def orderbook(code: str, levels: int = 10) -> dict[str, Any]:
            if levels < 1:
                levels = 1
            if levels > 20:
                levels = 20
            book = engine.order_book(code, levels=levels)
            if book is None:
                raise HTTPException(status_code=404, detail=f"未知股票: {code}")
            return book

        @router.get("/watchlists")
        def watchlists() -> dict[str, Any]:
            wl = self.watchlists
            if wl is None:
                return {"groups": []}
            groups = []
            for g in wl.list():
                quotes = []
                for code in g["codes"]:
                    q = engine.quote(code)
                    if q:
                        quotes.append(q)
                groups.append({**g, "quotes": quotes})
            return {
                "groups": groups,
                "default_group_id": wl.default_group().id,
                "universe": [{"code": c, "name": n} for c, n, _b in engine.universe],
            }

        @router.post("/watchlists")
        def create_group(payload: _GroupIn) -> dict[str, Any]:
            wl = self.watchlists
            if wl is None:
                raise HTTPException(status_code=400, detail="自选未初始化")
            return wl.create_group(payload.name)

        @router.patch("/watchlists/{group_id}")
        def rename_group(group_id: str, payload: _GroupIn) -> dict[str, Any]:
            wl = self.watchlists
            if wl is None:
                raise HTTPException(status_code=400, detail="自选未初始化")
            return wl.rename_group(group_id, payload.name)

        @router.delete("/watchlists/{group_id}")
        def delete_group(group_id: str) -> dict[str, Any]:
            wl = self.watchlists
            if wl is None:
                raise HTTPException(status_code=400, detail="自选未初始化")
            return wl.delete_group(group_id)

        @router.post("/watchlists/{group_id}/stocks")
        def add_stock(group_id: str, payload: _StockIn) -> dict[str, Any]:
            wl = self.watchlists
            if wl is None:
                raise HTTPException(status_code=400, detail="自选未初始化")
            if engine.quote(payload.code) is None:
                return {"ok": False, "message": f"未知股票: {payload.code}"}
            return wl.add_stock(group_id, payload.code)

        @router.delete("/watchlists/{group_id}/stocks/{code}")
        def remove_stock(group_id: str, code: str) -> dict[str, Any]:
            wl = self.watchlists
            if wl is None:
                raise HTTPException(status_code=400, detail="自选未初始化")
            return wl.remove_stock(group_id, code)

        @router.post("/watchlists/fav")
        def toggle_fav(payload: _FavIn) -> dict[str, Any]:
            wl = self.watchlists
            if wl is None:
                raise HTTPException(status_code=400, detail="自选未初始化")
            if engine.quote(payload.code) is None:
                return {"ok": False, "message": f"未知股票: {payload.code}"}
            if wl.has_code(payload.code):
                # 已在自选中 → 从所有分组移除
                for g in wl.list():
                    wl.remove_stock(g["id"], payload.code)
                return {"ok": True, "code": payload.code, "in_watchlist": False}
            default_group = wl.default_group()
            return wl.add_stock(default_group.id, payload.code)

        @router.get("/account")
        def account() -> dict[str, Any]:
            return engine.account()

        @router.get("/orders")
        def orders(limit: int = 100) -> dict[str, Any]:
            return {"orders": engine.orders(limit=limit)}

        @router.post("/orders")
        def create_order(payload: _OrderIn) -> dict[str, Any]:
            return engine.place_order(
                code=payload.code,
                side=payload.side,
                order_type=payload.order_type,
                price=payload.price,
                qty=payload.qty,
            )

        @router.post("/reset")
        def reset() -> dict[str, Any]:
            return engine.reset()

        app.include_router(router)

    @property
    def capabilities(self) -> list[Any]:
        from runtime.platform.plugins.plugin_base import ProvidedCapability

        caps = super().capabilities
        caps.append(
            ProvidedCapability(
                type="api",
                name=f"{self.name}.page",
                description="带页面的模拟炒股面板(/api/plugins/paper-trading/page)",
            )
        )
        caps.append(
            ProvidedCapability(
                type="api",
                name=f"{self.name}.quote_hub",
                description="统一实时行情中心(合并订阅、故障切换、REST/SSE 分发)",
            )
        )
        return caps


__all__ = ["PaperTradingPlugin"]
