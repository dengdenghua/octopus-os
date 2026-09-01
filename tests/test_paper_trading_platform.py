"""Tests for the ``paper_trading`` plugin's 平台配资盘(真实交易) integration.

安全红线:测试全部 mock,**绝不**真正连网 / 真实下单。
覆盖:

  1. 未启用/未登录 → 平台只读接口优雅降级 ``{ok:false}``
  2. 已 mock 客户端 → 合约 / 持仓 / 委托 / 费率 / 档位 / 卖出面板只读打通
  3. 真实操作(申请资金 / 买入 / 卖出 / 追加 / 提盈 / 撤单)必须 ``confirm:true``,
     否则被拦截,客户端方法**不会被调用**
  4. ``confirm:true`` + mock → 正确调用客户端对应方法并传参
  5. PlatformClient 各真实交易方法的请求路径 / 载荷正确(拦 ``_request``)
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.platform.plugins.bundled.paper_trading import PaperTradingPlugin
from runtime.platform.plugins.bundled.paper_trading.live import (
    DEFAULT_BASE_URL,
    LiveDataSource,
    PlatformClient,
)
from runtime.platform.plugins.plugin_base import ModuleContext

PLUGIN_DIR = (
    Path(__file__).resolve().parents[1]
    / "runtime"
    / "platform"
    / "plugins"
    / "bundled"
    / "paper_trading"
)


def _plugin(tmp_path: Path, live_mode: bool = False) -> tuple[PaperTradingPlugin, TestClient]:
    app = FastAPI()
    plugin = PaperTradingPlugin()
    ctx = ModuleContext(
        plugin_name="paper_trading",
        plugin_dir=str(PLUGIN_DIR),
        manifest=None,
        fastapi_app=app,
        config={
            "live_mode": live_mode,
            "initial_cash": 1_000_000,
            "data_dir": str(tmp_path / "pt"),
        },
    )
    plugin.on_load(ctx)
    return plugin, TestClient(app)


def _fake_client() -> MagicMock:
    client = MagicMock(spec=PlatformClient)
    client.base_url = DEFAULT_BASE_URL
    client.list_contracts.return_value = [
        {
            "contractId": "C1",
            "contractName": "按月10倍[081901]",
            "amountAvailable": 776.77,
            "totalTradersMoney": 170500,
        }
    ]
    client.positions.return_value = [{"stockCode": "600519", "stockName": "贵州茅台", "qty": 100}]
    client.orders.return_value = [
        {"stockCode": "600519", "stockName": "贵州茅台", "entrustNumber": 100}
    ]
    client.rate_table.return_value = [{"multiple": 10, "day": 0.15}]
    client.apply_options.return_value = {"types": [{"multiple": 10}]}
    client.sell_panel.return_value = {"sellNumber": 100}
    client.get_member_info.return_value = {"account": "HL51550949"}
    client.account_name = "HL51550949"
    client.configured = True
    return client


def _mock_live(client: MagicMock) -> MagicMock:
    live = MagicMock(spec=LiveDataSource)
    live.configured = True
    live.available = True
    live.account = "HL51550949"
    live.client = client
    return live


# ── 1. 未启用 → 降级 ─────────────────────────────────────


def test_platform_read_routes_degrade_when_live_disabled(tmp_path: Path) -> None:
    plugin, client = _plugin(tmp_path, live_mode=False)
    assert plugin.live is None

    assert client.get("/api/plugins/paper-trading/platform/contracts").json()["ok"] is False
    assert client.get("/api/plugins/paper-trading/platform/positions").json()["ok"] is False
    assert client.get("/api/plugins/paper-trading/platform/orders").json()["ok"] is False
    assert client.get("/api/plugins/paper-trading/platform/rate-table").json()["ok"] is False
    assert client.get("/api/plugins/paper-trading/platform/apply-options").json()["ok"] is False
    assert (
        client.get(
            "/api/plugins/paper-trading/platform/sell-panel?contractId=C1&stockCode=600519"
        ).json()["ok"]
        is False
    )
    st = client.get("/api/plugins/paper-trading/platform/status").json()
    assert st["configured"] is False


# ── 2. 已登录 → 只读打通 ─────────────────────────────────


def test_platform_read_routes_with_client(tmp_path: Path) -> None:
    plugin, client = _plugin(tmp_path, live_mode=False)
    live = _mock_live(_fake_client())
    plugin.live = live

    r = client.get("/api/plugins/paper-trading/platform/contracts").json()
    assert r["ok"] is True and r["data"][0]["contractName"] == "按月10倍[081901]"

    r = client.get("/api/plugins/paper-trading/platform/positions").json()
    assert r["ok"] is True and r["data"][0]["stockCode"] == "600519"

    r = client.get("/api/plugins/paper-trading/platform/orders?size=30").json()
    assert r["ok"] is True and r["data"][0]["stockCode"] == "600519"

    assert client.get("/api/plugins/paper-trading/platform/rate-table").json()["ok"] is True
    assert client.get("/api/plugins/paper-trading/platform/apply-options").json()["ok"] is True
    assert (
        client.get(
            "/api/plugins/paper-trading/platform/sell-panel?contractId=C1&stockCode=600519"
        ).json()["ok"]
        is True
    )

    r = client.get("/api/plugins/paper-trading/platform/overview").json()
    assert r["ok"] is True
    assert r["member"]["account"] == "HL51550949"
    assert r["contracts"][0]["contractId"] == "C1"

    st = client.get("/api/plugins/paper-trading/platform/status").json()
    assert st["configured"] is True and st["account"] == "HL51550949"


def test_platform_read_routes_degrade_on_error(tmp_path: Path) -> None:
    plugin, client = _plugin(tmp_path, live_mode=False)
    live = _mock_live(_fake_client())
    live.client.list_contracts.side_effect = RuntimeError("网络异常")
    plugin.live = live

    r = client.get("/api/plugins/paper-trading/platform/contracts").json()
    assert r["ok"] is False and "网络异常" in r["error"]


# ── 3. 真实操作:未 confirm 必须被拦截 ─────────────────────


def test_platform_write_blocked_without_confirm(tmp_path: Path) -> None:
    plugin, client = _plugin(tmp_path, live_mode=False)
    live = _mock_live(_fake_client())
    plugin.live = live

    # 不传 confirm / confirm=false → 全部拒绝,且不调用客户端方法
    r = client.post(
        "/api/plugins/paper-trading/platform/buy",
        json={"contract_id": "C1", "stock_code": "600519", "stock_name": "贵州茅台", "qty": 100},
    ).json()
    assert r["ok"] is False and "确认" in r["error"]
    live.client.buy.assert_not_called()

    r = client.post(
        "/api/plugins/paper-trading/platform/apply-contract",
        json={"contract_type": 3, "principal": 1000, "multiple": 10, "confirm": False},
    ).json()
    assert r["ok"] is False
    live.client.apply_contract.assert_not_called()

    r = client.post(
        "/api/plugins/paper-trading/platform/sell",
        json={"contract_id": "C1", "stock_code": "600519", "qty": 100},
    ).json()
    assert r["ok"] is False
    live.client.sell.assert_not_called()

    r = client.post(
        "/api/plugins/paper-trading/platform/add-capital",
        json={"contract_id": "C1", "money": 1000},
    ).json()
    assert r["ok"] is False
    live.client.add_capital.assert_not_called()

    r = client.post(
        "/api/plugins/paper-trading/platform/withdraw-profit",
        json={"contract_id": "C1", "money": 100},
    ).json()
    assert r["ok"] is False
    live.client.withdraw_profit.assert_not_called()

    r = client.post(
        "/api/plugins/paper-trading/platform/cancel-order",
        json={"order_id": "O1", "contract_id": "C1"},
    ).json()
    assert r["ok"] is False
    live.client.cancel_order.assert_not_called()


def test_platform_write_blocked_when_not_logged_in(tmp_path: Path) -> None:
    plugin, client = _plugin(tmp_path, live_mode=False)
    live = _mock_live(_fake_client())
    live.configured = False  # 未登录
    plugin.live = live

    r = client.post(
        "/api/plugins/paper-trading/platform/buy",
        json={"contract_id": "C1", "stock_code": "600519", "qty": 100, "confirm": True},
    ).json()
    assert r["ok"] is False and "登录" in r["error"]
    live.client.buy.assert_not_called()


# ── 4. confirm:true → 正确调用客户端 ──────────────────────


def test_platform_buy_with_confirm_calls_client(tmp_path: Path) -> None:
    plugin, client = _plugin(tmp_path, live_mode=False)
    live = _mock_live(_fake_client())
    live.client.buy.return_value = {"code": 1, "message": "已受理"}
    plugin.live = live

    r = client.post(
        "/api/plugins/paper-trading/platform/buy",
        json={
            "contract_id": "C1",
            "stock_code": "600519",
            "stock_name": "贵州茅台",
            "entrust_type": 0,
            "price": 1688.0,
            "qty": 200,
            "confirm": True,
        },
    ).json()
    assert r["ok"] is True
    live.client.buy.assert_called_once_with(
        contract_id="C1",
        stock_code="600519",
        stock_name="贵州茅台",
        entrust_type=0,
        price=1688.0,
        number=200,
    )


def test_platform_sell_and_apply_with_confirm(tmp_path: Path) -> None:
    plugin, client = _plugin(tmp_path, live_mode=False)
    live = _mock_live(_fake_client())
    live.client.sell.return_value = {"code": 1}
    live.client.apply_contract.return_value = {"code": 1}
    plugin.live = live

    r = client.post(
        "/api/plugins/paper-trading/platform/sell",
        json={
            "contract_id": "C1",
            "stock_code": "600519",
            "stock_name": "贵州茅台",
            "entrust_type": 1,
            "price": None,
            "qty": 100,
            "confirm": True,
        },
    ).json()
    assert r["ok"] is True
    live.client.sell.assert_called_once_with(
        contract_id="C1",
        stock_code="600519",
        stock_name="贵州茅台",
        entrust_type=1,
        price=None,
        number=100,
    )

    r = client.post(
        "/api/plugins/paper-trading/platform/apply-contract",
        json={"contract_type": 3, "principal": 5000, "multiple": 10, "confirm": True},
    ).json()
    assert r["ok"] is True
    live.client.apply_contract.assert_called_once_with(contract_type=3, principal=5000, multiple=10)


def test_platform_money_and_cancel_with_confirm(tmp_path: Path) -> None:
    plugin, client = _plugin(tmp_path, live_mode=False)
    live = _mock_live(_fake_client())
    live.client.add_capital.return_value = {"code": 1}
    live.client.withdraw_profit.return_value = {"code": 1}
    live.client.cancel_order.return_value = {"code": 1}
    plugin.live = live

    assert (
        client.post(
            "/api/plugins/paper-trading/platform/add-capital",
            json={"contract_id": "C1", "money": 2000, "confirm": True},
        ).json()["ok"]
        is True
    )
    live.client.add_capital.assert_called_once_with(contract_id="C1", money=2000)

    assert (
        client.post(
            "/api/plugins/paper-trading/platform/withdraw-profit",
            json={"contract_id": "C1", "money": 300, "confirm": True},
        ).json()["ok"]
        is True
    )
    live.client.withdraw_profit.assert_called_once_with(contract_id="C1", money=300)

    assert (
        client.post(
            "/api/plugins/paper-trading/platform/cancel-order",
            json={"order_id": "O9", "contract_id": "C1", "confirm": True},
        ).json()["ok"]
        is True
    )
    live.client.cancel_order.assert_called_once_with(order_id="O9", contract_id="C1")


def test_platform_write_propagates_client_error(tmp_path: Path) -> None:
    plugin, client = _plugin(tmp_path, live_mode=False)
    live = _mock_live(_fake_client())
    live.client.buy.side_effect = RuntimeError("余额不足")
    plugin.live = live

    r = client.post(
        "/api/plugins/paper-trading/platform/buy",
        json={"contract_id": "C1", "stock_code": "600519", "qty": 100, "confirm": True},
    ).json()
    assert r["ok"] is False and "余额不足" in r["error"]


# ── 5. PlatformClient 真实交易方法:路径/载荷 ──────────────


def _jwt(claims: dict) -> str:
    """构造含指定 claims 的伪 JWT(仅用于让 member_id 解码)。"""

    def enc(o) -> str:
        return base64.urlsafe_b64encode(json.dumps(o).encode()).rstrip(b"=").decode()

    return enc({"alg": "none"}) + "." + enc(claims) + "." + "sig"


def _client_with_stub_request():
    """拦 _request,记录调用并返回平台风格响应;token 解码出 memberId=M1。"""
    calls: list[tuple[str, str, dict]] = []

    def fake_request(method, path, payload=None, **kw):
        calls.append((method, path, payload))
        return {"code": 1, "data": {"ok": True}}

    client = PlatformClient(base_url=DEFAULT_BASE_URL)
    client._request = fake_request  # type: ignore[method-assign]
    client._token = _jwt({"memberId": "M1", "account": "HL51550949", "exp": 9999999999})
    return client, calls


def test_client_apply_contract_payload() -> None:
    client, calls = _client_with_stub_request()

    resp = client.apply_contract(3, 5000, 10)

    assert resp["data"]["ok"] is True
    method, path, payload = calls[-1]
    assert path == "/contract/applycontract/add"
    assert payload["contractType"] == 3
    assert payload["principal"] == 5000
    assert payload["multiple"] == 10
    assert payload["memberId"] == "M1"


def test_client_buy_sell_payload() -> None:
    client, calls = _client_with_stub_request()

    client.buy("C1", "600519", "贵州茅台", 0, 1688.0, 200)
    _, path, payload = calls[-1]
    assert path == "/stock/BuyStock/insert"
    assert payload["contractId"] == "C1"
    assert payload["entrustType"] == "0"
    assert payload["entrustPrice"] == 1688.0
    assert payload["entrustNumber"] == 200

    client.sell("C1", "600519", "贵州茅台", 1, None, 100)
    _, path, payload = calls[-1]
    assert path == "/stock/SellStock/insert"
    assert payload["entrustType"] == "1"
    assert "entrustPrice" not in payload  # 市价不传价
    assert payload["entrustNumber"] == 100


def test_client_money_and_cancel_payload() -> None:
    client, calls = _client_with_stub_request()

    client.add_capital("C1", 2000)
    _, path, payload = calls[-1]
    assert path == "/contract/appendcapital/additional"
    assert payload["money"] == 2000

    client.withdraw_profit("C1", 300)
    _, path, payload = calls[-1]
    assert path == "/contract/WithdrawProfit/extract"
    assert payload["money"] == 300

    client.cancel_order("O9", "C1")
    _, path, payload = calls[-1]
    assert path == "/stock/cancelOrder/cancel"
    assert payload["orderId"] == "O9"


# ── 6. agent 自动下单 skill(auto_trade) ─────────────────


def _plugin_with_skills(tmp_path: Path, auto_trade: bool, live=None):
    from unittest.mock import MagicMock

    plugin = PaperTradingPlugin()
    plugin.auto_trade = auto_trade
    plugin.ctx = MagicMock()
    plugin.live = live
    plugin.engine = MagicMock()
    plugin.register_skills()
    skills = [c[0][0] for c in plugin.ctx.register_skill.call_args_list]
    return plugin, skills


def test_trade_skill_not_registered_when_auto_trade_off(tmp_path: Path) -> None:
    plugin, skills = _plugin_with_skills(tmp_path, auto_trade=False)
    names = [s.name for s in skills]
    assert "paper_trading.quote" in names
    assert "paper_trading.trade" not in names  # 默认关:不暴露自动下单 skill


def test_trade_skill_registered_when_auto_trade_on(tmp_path: Path) -> None:
    plugin, skills = _plugin_with_skills(tmp_path, auto_trade=True)
    trade = next(s for s in skills if s.name == "paper_trading.trade")
    assert "auto_trade" in trade.description or "真实" in trade.description
    assert callable(trade.handler)


def test_trade_skill_rejects_when_off(tmp_path: Path) -> None:
    plugin, _ = _plugin_with_skills(tmp_path, auto_trade=False)
    r = plugin._trade_skill(action="buy", contract_id="C1", stock_code="600519", qty=100)
    assert r["ok"] is False
    assert "auto_trade=false" in r["error"] or "未开启" in r["error"]


def test_trade_skill_buy_calls_client_with_confirm(tmp_path: Path) -> None:
    client = _fake_client()
    client.buy.return_value = {"code": 1, "message": "已受理"}
    plugin, _ = _plugin_with_skills(tmp_path, auto_trade=True, live=_mock_live(client))

    r = plugin._trade_skill(
        action="buy",
        contract_id="C1",
        stock_code="600519",
        stock_name="贵州茅台",
        entrust_type=0,
        price=1688.0,
        qty=200,
    )
    assert r["ok"] is True
    client.buy.assert_called_once_with(
        contract_id="C1",
        stock_code="600519",
        stock_name="贵州茅台",
        entrust_type=0,
        price=1688.0,
        number=200,
    )


def test_trade_skill_dry_run_does_not_submit(tmp_path: Path) -> None:
    client = _fake_client()
    plugin, _ = _plugin_with_skills(tmp_path, auto_trade=True, live=_mock_live(client))

    r = plugin._trade_skill(
        action="buy",
        contract_id="C1",
        stock_code="600519",
        stock_name="贵州茅台",
        entrust_type=0,
        price=1688.0,
        qty=200,
        dry_run=True,
    )
    assert r["ok"] is True and r["dry_run"] is True
    assert r["action"] == "真实买入"
    client.buy.assert_not_called()  # dry_run 绝不真正下单


def test_trade_skill_validation(tmp_path: Path) -> None:
    client = _fake_client()
    plugin, _ = _plugin_with_skills(tmp_path, auto_trade=True, live=_mock_live(client))

    # 缺参数
    r = plugin._trade_skill(action="buy", contract_id="C1", qty=100)
    assert r["ok"] is False and "缺少参数" in r["error"]

    # 数量非 100 整数倍(市价单,先过价格校验再拦数量)
    r = plugin._trade_skill(
        action="sell",
        contract_id="C1",
        stock_code="600519",
        stock_name="贵州茅台",
        entrust_type=1,
        qty=150,
    )
    assert r["ok"] is False and "整数倍" in r["error"]

    # 限价单无价
    r = plugin._trade_skill(
        action="buy",
        contract_id="C1",
        stock_code="600519",
        stock_name="贵州茅台",
        entrust_type=0,
        qty=100,
    )
    assert r["ok"] is False and "price" in r["error"]

    # 未知 action
    r = plugin._trade_skill(action="hack")
    assert r["ok"] is False and "未知 action" in r["error"]

    client.buy.assert_not_called()
    client.sell.assert_not_called()


def test_trade_skill_apply_and_money(tmp_path: Path) -> None:
    client = _fake_client()
    client.apply_contract.return_value = {"code": 1}
    client.add_capital.return_value = {"code": 1}
    plugin, _ = _plugin_with_skills(tmp_path, auto_trade=True, live=_mock_live(client))

    r = plugin._trade_skill(action="apply", contract_type=3, principal=5000, multiple=10)
    assert r["ok"] is True
    client.apply_contract.assert_called_once_with(contract_type=3, principal=5000, multiple=10)

    r = plugin._trade_skill(action="add_capital", contract_id="C1", money=2000)
    assert r["ok"] is True
    client.add_capital.assert_called_once_with(contract_id="C1", money=2000)

    r = plugin._trade_skill(action="add_capital", contract_id="C1", money=-5)
    assert r["ok"] is False and "大于 0" in r["error"]


def test_trade_skill_needs_login(tmp_path: Path) -> None:
    plugin, _ = _plugin_with_skills(tmp_path, auto_trade=True, live=None)
    r = plugin._trade_skill(action="buy", contract_id="C1", stock_code="600519", qty=100)
    assert r["ok"] is False and "登录" in r["error"]


def test_on_load_reads_auto_trade_from_config(tmp_path: Path) -> None:
    """on_load 从 config 读取 auto_trade 开关。"""

    app = FastAPI()
    plugin = PaperTradingPlugin()
    ctx = ModuleContext(
        plugin_name="paper_trading",
        plugin_dir=str(PLUGIN_DIR),
        manifest=None,
        fastapi_app=app,
        config={
            "live_mode": True,
            "auto_trade": True,
            "base_url": "https://up.test/api",
            "data_dir": str(tmp_path / "pt"),
        },
    )
    plugin.on_load(ctx)
    assert plugin.auto_trade is True

    # 默认关闭
    plugin2 = PaperTradingPlugin()
    ctx2 = ModuleContext(
        plugin_name="paper_trading",
        plugin_dir=str(PLUGIN_DIR),
        manifest=None,
        fastapi_app=app,
        config={"live_mode": False, "data_dir": str(tmp_path / "pt2")},
    )
    plugin2.on_load(ctx2)
    assert plugin2.auto_trade is False

