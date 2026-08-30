"""模拟炒股(paper_trading)核心服务 — 自含模拟行情引擎 + 模拟账户。

设计目标:纯个人练习用,**完全不联网、不接任何真实行情或第三方后端**。
- 内置一批 A 股代码/名称/基准价,盘中用「随机游走」模拟报价(锚定昨收,涨跌幅
  限制在 ±10% 内),收盘后价格冻结。
- 账户规则贴近 A 股:整手(100 股)买入、T+1(当日买入次日才能卖)、
  买入佣金(万3,最低 5 元)、卖出另收印花税(0.05%)。
- 账户/持仓/成交记录持久化到 JSON(默认 ~/.echo/data/paper_trading/state.json),
  重启不丢。
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
import zlib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment,misc]

_logger = logging.getLogger(__name__)

# 内置 A 股股票池:(代码, 名称, 基准价)
DEFAULT_UNIVERSE: list[tuple[str, str, float]] = [
    ("600519", "贵州茅台", 1688.00),
    ("300750", "宁德时代", 245.60),
    ("002594", "比亚迪", 268.90),
    ("601318", "中国平安", 46.80),
    ("600036", "招商银行", 35.40),
    ("000858", "五粮液", 142.50),
    ("600030", "中信证券", 21.30),
    ("000001", "平安银行", 11.20),
    ("601899", "紫金矿业", 16.75),
    ("600900", "长江电力", 28.60),
    ("002415", "海康威视", 31.20),
    ("601012", "隆基绿能", 18.40),
    ("600276", "恒瑞医药", 43.50),
    ("000333", "美的集团", 63.80),
    ("002475", "立讯精密", 38.90),
    ("601888", "中国中免", 68.20),
    ("600887", "伊利股份", 27.30),
    ("000651", "格力电器", 41.60),
    ("601398", "工商银行", 5.62),
    ("600016", "民生银行", 4.05),
    ("601988", "中国银行", 4.88),
    ("002230", "科大讯飞", 48.70),
    ("300059", "东方财富", 15.30),
    ("688981", "中芯国际", 92.40),
    ("600941", "中国移动", 103.50),
    ("000725", "京东方A", 4.12),
    ("601857", "中国石油", 9.35),
    ("600028", "中国石化", 6.48),
    ("601668", "中国建筑", 5.89),
    ("002714", "牧原股份", 41.20),
]

_TZ = None
if ZoneInfo is not None:
    try:
        _TZ = ZoneInfo("Asia/Shanghai")
    except Exception:  # pragma: no cover
        _TZ = None

LOT = 100  # 一手 = 100 股
PRICE_LIMIT = 0.10  # ±10% 涨跌停
COMMISSION_RATE = 0.0003  # 佣金万3
COMMISSION_MIN = 5.0  # 最低 5 元
STAMP_TAX = 0.0005  # 卖出印花税 0.05%

MORNING_OPEN = dtime(9, 30)
MORNING_CLOSE = dtime(11, 30)
AFTERNOON_OPEN = dtime(13, 0)
AFTERNOON_CLOSE = dtime(15, 0)


def _now() -> datetime:
    if _TZ is not None:
        return datetime.now(_TZ)
    return datetime.now()


def is_trading_time(now: datetime | None = None) -> bool:
    """A 股交易时段(周一~周五 9:30-11:30 / 13:00-15:00)。"""
    now = now or _now()
    if now.weekday() >= 5:
        return False
    t = now.time()
    return (MORNING_OPEN <= t <= MORNING_CLOSE) or (AFTERNOON_OPEN <= t <= AFTERNOON_CLOSE)


def _build_engine(
    initial_cash: float = 1_000_000.0,
    data_dir: str = "~/.echo/data/paper_trading",
) -> PaperTradingEngine:
    """Construct and hydrate the persistent local simulation engine."""
    engine = PaperTradingEngine(initial_cash=initial_cash, data_dir=data_dir)
    engine.load()
    return engine


def _session_label(now: datetime | None = None) -> str:
    now = now or _now()
    if now.weekday() >= 5:
        return "休市(周末)"
    if is_trading_time(now):
        return "交易中"
    t = now.time()
    if t < MORNING_OPEN:
        return "未开盘"
    if MORNING_CLOSE < t < AFTERNOON_OPEN:
        return "午间休市"
    return "已收盘"


@dataclass
class Position:
    code: str
    name: str
    qty: int = 0
    locked: int = 0  # 当日买入、T+1 锁定不可卖的部分
    cost: float = 0.0  # 总成本(含买入费用)


@dataclass
class Order:
    id: str
    code: str
    name: str
    side: str  # "buy" | "sell"
    order_type: str  # "market" | "limit"
    price: float
    qty: int
    status: str = "filled"  # 仅记录已成交
    fee: float = 0.0
    time: str = ""


@dataclass
class PaperTradingState:
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    orders: list[Order] = field(default_factory=list)
    prev_close: dict[str, float] = field(default_factory=dict)
    drift: dict[str, float] = field(default_factory=dict)
    day: str = ""  # 用于 T+1 解锁的日期
    created_at: str = ""


class PaperTradingEngine:
    """自含模拟行情 + 模拟账户引擎。线程安全,单例服务。"""

    def __init__(
        self,
        initial_cash: float = 1_000_000.0,
        data_dir: str = "~/.echo/data/paper_trading",
        universe: list[tuple[str, str, float]] | None = None,
        seed: int | None = None,
    ) -> None:
        self.initial_cash = float(initial_cash)
        self.data_dir = Path(data_dir).expanduser()
        self.universe = list(universe or DEFAULT_UNIVERSE)
        self._rng = random.Random(seed) if seed is not None else random.Random()
        self._lock = threading.RLock()
        self.state = PaperTradingState(cash=self.initial_cash)
        self._init_universe_defaults()

    # ── 初始化/持久化 ──────────────────────────────────────

    def _init_universe_defaults(self) -> None:
        for code, _name, base in self.universe:
            self.state.prev_close.setdefault(code, base)
            self.state.drift.setdefault(code, 0.0)

    @property
    def state_file(self) -> Path:
        return self.data_dir / "state.json"

    def load(self) -> None:
        try:
            if not self.state_file.exists():
                self.state.created_at = _now().isoformat()
                self.save()
                return
            raw = json.loads(self.state_file.read_text(encoding="utf-8"))
            self.state.cash = float(raw.get("cash", self.initial_cash))
            self.state.positions = {}
            for code, pos in (raw.get("positions") or {}).items():
                self.state.positions[code] = Position(
                    code=pos["code"],
                    name=pos["name"],
                    qty=int(pos["qty"]),
                    locked=int(pos.get("locked", 0)),
                    cost=float(pos.get("cost", 0.0)),
                )
            self.state.orders = []
            for o in raw.get("orders") or []:
                self.state.orders.append(
                    Order(
                        id=o["id"],
                        code=o["code"],
                        name=o["name"],
                        side=o["side"],
                        order_type=o["order_type"],
                        price=float(o["price"]),
                        qty=int(o["qty"]),
                        status=o.get("status", "filled"),
                        fee=float(o.get("fee", 0.0)),
                        time=o.get("time", ""),
                    )
                )
            self.state.prev_close = {k: float(v) for k, v in (raw.get("prev_close") or {}).items()}
            self.state.drift = {k: float(v) for k, v in (raw.get("drift") or {}).items()}
            self.state.day = raw.get("day", "")
            self.state.created_at = raw.get("created_at", "")
            self._init_universe_defaults()
        except Exception as exc:  # noqa: BLE001 — 状态文件坏了就重置,不影响插件可用
            _logger.warning("paper_trading: 状态文件读取失败,重置账户: %s", exc)
            self.state = PaperTradingState(cash=self.initial_cash)
            self._init_universe_defaults()
            self.state.created_at = _now().isoformat()

    def save(self) -> None:
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "cash": self.state.cash,
                "positions": {
                    code: {
                        "code": p.code,
                        "name": p.name,
                        "qty": p.qty,
                        "locked": p.locked,
                        "cost": p.cost,
                    }
                    for code, p in self.state.positions.items()
                },
                "orders": [
                    {
                        "id": o.id,
                        "code": o.code,
                        "name": o.name,
                        "side": o.side,
                        "order_type": o.order_type,
                        "price": o.price,
                        "qty": o.qty,
                        "status": o.status,
                        "fee": o.fee,
                        "time": o.time,
                    }
                    for o in self.state.orders
                ],
                "prev_close": self.state.prev_close,
                "drift": self.state.drift,
                "day": self.state.day,
                "created_at": self.state.created_at,
            }
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.state_file)
        except Exception as exc:  # noqa: BLE001 — 持久化失败不致命
            _logger.warning("paper_trading: 状态保存失败: %s", exc)

    # ── 行情 ──────────────────────────────────────────────

    def _roll_day(self, now: datetime) -> None:
        """跨交易日则解锁 T+1 锁定的持仓。"""
        day = now.strftime("%Y-%m-%d")
        if self.state.day != day:
            for pos in self.state.positions.values():
                pos.locked = 0
            self.state.day = day

    def tick(self) -> None:
        """推进一次模拟行情:交易时段内随机游走,收盘冻结。"""
        now = _now()
        with self._lock:
            self._roll_day(now)
            if not is_trading_time(now):
                return
            for code, _name, _base in self.universe:
                step = self._rng.gauss(0, 0.0012)
                # 弱均值回归,防止漂移太远
                old = self.state.drift.get(code, 0.0)
                pull = -0.15 * old
                new_drift = min(PRICE_LIMIT, max(-PRICE_LIMIT, old + step + pull))
                self.state.drift[code] = new_drift
            self.save()

    def quote(self, code: str) -> dict[str, Any] | None:
        with self._lock:
            item = next((u for u in self.universe if u[0] == code), None)
            if item is None:
                return None
            _code, name, base = item
            prev = self.state.prev_close.get(code, base)
            price = round(prev * (1 + self.state.drift.get(code, 0.0)), 2)
            change = round(price - prev, 2)
            pct = round(change / prev * 100, 2) if prev else 0.0
            open_ = round(prev * (1 + self._rng.uniform(-0.004, 0.004)), 2)
            high = round(max(price, open_) * (1 + self._rng.uniform(0, 0.0015)), 2)
            low = round(min(price, open_) * (1 - self._rng.uniform(0, 0.0015)), 2)
            return {
                "code": code,
                "name": name,
                "price": price,
                "prev_close": prev,
                "change": change,
                "change_pct": pct,
                "open": open_,
                "high": high,
                "low": low,
            }

    def quotes(self) -> list[dict[str, Any]]:
        with self._lock:
            return [q for q in (self.quote(c) for c, _n, _b in self.universe) if q]

    def order_book(self, code: str, levels: int = 10) -> dict[str, Any] | None:
        """合成十档盘口(买/卖各 levels 档),围绕现价 ±0.01 步进。"""
        with self._lock:
            q = self.quote(code)
            if q is None:
                return None
            price = q["price"]
            tick = 0.01
            bids = [
                {
                    "level": i,
                    "price": round(max(0.01, price - i * tick), 2),
                    "size": int(self._rng.randint(100, 6000)),
                }
                for i in range(1, levels + 1)
            ]
            asks = [
                {
                    "level": i,
                    "price": round(price + i * tick, 2),
                    "size": int(self._rng.randint(100, 6000)),
                }
                for i in range(1, levels + 1)
            ]
            return {
                "code": code,
                "price": price,
                "prev_close": q["prev_close"],
                "bids": bids,
                "asks": asks,
            }

    def kline(self, code: str, days: int = 60) -> list[dict[str, Any]]:
        """生成确定性的合成日 K(seed 由代码派生),用于画图练习。"""
        with self._lock:
            item = next((u for u in self.universe if u[0] == code), None)
            if item is None:
                return []
            _code, name, base = item
            rng = random.Random(zlib.crc32(code.encode("utf-8")))
            prev = self.state.prev_close.get(code, base)
            # 生成 days 根日 K,从 days 个交易日前到昨天
            trade_days: list[date] = []
            d = date.today()
            while len(trade_days) < days:
                d -= timedelta(days=1)
                if d.weekday() < 5:
                    trade_days.append(d)
            trade_days.reverse()
            candles: list[dict[str, Any]] = []
            price = prev * 0.85
            for day in trade_days:
                open_ = price
                chg = rng.uniform(-0.045, 0.045)
                close = round(max(0.5, open_ * (1 + chg)), 2)
                high = round(max(open_, close) * (1 + rng.uniform(0, 0.01)), 2)
                low = round(min(open_, close) * (1 - rng.uniform(0, 0.01)), 2)
                candles.append(
                    {
                        "date": day.isoformat(),
                        "open": open_,
                        "close": close,
                        "high": high,
                        "low": low,
                        "volume": int(rng.uniform(1e5, 5e6)),
                    }
                )
                price = close
            return candles

    # ── 账户/下单 ────────────────────────────────────────

    def account(self) -> dict[str, Any]:
        with self._lock:
            self.tick()
            total_mv = 0.0
            positions = []
            for code, pos in self.state.positions.items():
                if pos.qty <= 0:
                    continue
                q = self.quote(code)
                price = q["price"] if q else 0.0
                mv = round(price * pos.qty, 2)
                total_mv += mv
                cost_price = (pos.cost / pos.qty) if pos.qty else 0.0
                pnl = round((price - cost_price) * pos.qty, 2)
                pnl_pct = round(pnl / pos.cost * 100, 2) if pos.cost else 0.0
                positions.append(
                    {
                        "code": code,
                        "name": pos.name,
                        "qty": pos.qty,
                        "sellable": pos.qty - pos.locked,
                        "locked": pos.locked,
                        "cost_price": round(cost_price, 3),
                        "price": price,
                        "market_value": mv,
                        "pnl": pnl,
                        "pnl_pct": pnl_pct,
                    }
                )
            market_value = round(total_mv, 2)
            total_assets = round(self.state.cash + market_value, 2)
            return {
                "cash": round(self.state.cash, 2),
                "market_value": market_value,
                "total_assets": total_assets,
                "total_pnl": round(total_assets - self.initial_cash, 2),
                "initial_cash": self.initial_cash,
                "positions": positions,
                "trading": is_trading_time(),
                "session": _session_label(),
            }

    def place_order(
        self,
        code: str,
        side: str,
        order_type: str = "market",
        price: float | None = None,
        qty: int = 100,
    ) -> dict[str, Any]:
        """下单。成功返回 {ok: True, order: {...}},失败返回 {ok: False, message}。"""
        with self._lock:
            self.tick()
            item = next((u for u in self.universe if u[0] == code), None)
            if item is None:
                return {"ok": False, "message": f"未知股票: {code}"}
            _code, name, base = item
            side = (side or "").lower()
            order_type = (order_type or "").lower()
            if side not in ("buy", "sell"):
                return {"ok": False, "message": "side 必须是 buy 或 sell"}
            if order_type not in ("market", "limit"):
                return {"ok": False, "message": "order_type 必须是 market 或 limit"}
            if qty <= 0 or qty % LOT != 0:
                return {"ok": False, "message": "数量必须是 100 的整数倍(手)"}
            if not is_trading_time():
                return {"ok": False, "message": "当前为非交易时段,无法下单"}

            q = self.quote(code)
            current = q["price"]
            prev = q["prev_close"]
            limit_up = round(prev * (1 + PRICE_LIMIT), 2)
            limit_down = round(prev * (1 - PRICE_LIMIT), 2)

            if order_type == "market":
                fill = current
            else:
                if price is None or price <= 0:
                    return {"ok": False, "message": "限价单需要有效的 price"}
                if side == "buy" and price < current:
                    return {"ok": False, "message": f"限价买低于现价({current}),未成交"}
                if side == "sell" and price > current:
                    return {"ok": False, "message": f"限价卖高于现价({current}),未成交"}
                fill = current

            if side == "buy":
                if price is not None and price > limit_up:
                    return {"ok": False, "message": f"买入价超过涨停价 {limit_up}"}
                if fill > limit_up:
                    fill = limit_up
                amount = fill * qty
                fee = max(COMMISSION_MIN, amount * COMMISSION_RATE)
                total_cost = amount + fee
                if total_cost > self.state.cash + 1e-6:
                    return {
                        "ok": False,
                        "message": (
                            f"可用资金不足(需 ¥{total_cost:,.2f},可用 ¥{self.state.cash:,.2f})"
                        ),
                    }
                self.state.cash -= total_cost
                pos = self.state.positions.setdefault(code, Position(code=code, name=name))
                pos.qty += qty
                pos.locked += qty
                pos.cost += total_cost
            else:
                pos = self.state.positions.get(code)
                sellable = (pos.qty - pos.locked) if pos else 0
                if sellable < qty:
                    locked = pos.locked if pos else 0
                    return {
                        "ok": False,
                        "message": (
                            f"可卖数量不足(可卖 {sellable},锁定 {locked},需 {qty};"
                            "T+1 当日买入次日可卖)"
                        ),
                    }
                if price is not None and price < limit_down:
                    return {"ok": False, "message": f"卖出价低于跌停价 {limit_down}"}
                if fill < limit_down:
                    fill = limit_down
                amount = fill * qty
                fee = max(COMMISSION_MIN, amount * COMMISSION_RATE) + amount * STAMP_TAX
                # 按比例扣减卖出部分的成本(含买入费用)
                cost_basis = pos.cost * qty / pos.qty
                pos.cost -= cost_basis
                pos.qty -= qty
                self.state.cash += amount - fee
                if pos.qty <= 0:
                    self.state.positions.pop(code, None)

            order = Order(
                id=f"{int(time.time() * 1000)}-{code}-{len(self.state.orders)}",
                code=code,
                name=name,
                side=side,
                order_type=order_type,
                price=round(fill, 2),
                qty=qty,
                status="filled",
                fee=round(fee, 2),
                time=_now().isoformat(timespec="seconds"),
            )
            self.state.orders.append(order)
            self.state.orders = self.state.orders[-200:]  # 只保留最近 200 条
            self.save()
            return {"ok": True, "order": order.__dict__}

    def orders(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            return [o.__dict__ for o in self.state.orders[-limit:][::-1]]

    def reset(self) -> dict[str, Any]:
        """重置账户到初始资金(练习清零)。"""
        with self._lock:
            self.state = PaperTradingState(cash=self.initial_cash)
            self._init_universe_defaults()
            self.state.created_at = _now().isoformat()
            self.save()
            return {"ok": True, "message": "已重置账户"}


__all__ = [
    "PaperTradingEngine",
    "DEFAULT_UNIVERSE",
    "is_trading_time",
    "_session_label",
]


# ── 自选股分组 ──────────────────────────────────────────


@dataclass
class WatchGroup:
    """一个自选股分组。"""

    id: str
    name: str
    codes: list[str] = field(default_factory=list)


DEFAULT_GROUP_ID = "default"
DEFAULT_GROUP_NAME = "默认自选"
MAX_GROUPS = 20
MAX_CODES_PER_GROUP = 200


def _new_id() -> str:
    import uuid

    return uuid.uuid4().hex[:8]


class WatchlistStore:
    """自选股分组存储,持久化到 ``<data_dir>/watchlists.json``。

    与模拟账户 ``state.json`` 独立:重置账户不清空自选偏好。
    线程安全;任何读文件失败都回退到「默认自选」空组,保证插件可用。
    """

    def __init__(self, data_dir: str = "~/.echo/data/paper_trading") -> None:
        self.data_dir = Path(data_dir).expanduser()
        self._lock = threading.RLock()
        self.groups: list[WatchGroup] = []
        self._load()

    # ── 持久化 ──────────────────────────────────────────

    @property
    def file(self) -> Path:
        return self.data_dir / "watchlists.json"

    def _load(self) -> None:
        try:
            if self.file.exists():
                raw = json.loads(self.file.read_text(encoding="utf-8"))
                groups = raw.get("groups") or []
                self.groups = [
                    WatchGroup(
                        id=str(g.get("id") or _new_id()),
                        name=str(g.get("name") or "分组"),
                        codes=[str(c) for c in (g.get("codes") or [])],
                    )
                    for g in groups
                ]
        except Exception as exc:  # noqa: BLE001 — 文件坏了就重置
            _logger.warning("paper_trading: 自选文件读取失败,重置: %s", exc)
            self.groups = []
        if not self.groups:
            self.groups = [WatchGroup(id=DEFAULT_GROUP_ID, name=DEFAULT_GROUP_NAME)]
            self.save()

    def save(self) -> None:
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "groups": [
                    {"id": g.id, "name": g.name, "codes": list(g.codes)} for g in self.groups
                ]
            }
            tmp = self.file.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.file)
        except Exception as exc:  # noqa: BLE001 — 持久化失败不致命
            _logger.warning("paper_trading: 自选保存失败: %s", exc)

    # ── 查询 ────────────────────────────────────────────

    def _find(self, group_id: str) -> WatchGroup | None:
        return next((g for g in self.groups if g.id == group_id), None)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [{"id": g.id, "name": g.name, "codes": list(g.codes)} for g in self.groups]

    def default_group(self) -> WatchGroup:
        with self._lock:
            return self._find(DEFAULT_GROUP_ID) or self.groups[0]

    def has_code(self, code: str) -> bool:
        with self._lock:
            return any(code in g.codes for g in self.groups)

    # ── 分组增删改 ──────────────────────────────────────

    def create_group(self, name: str) -> dict[str, Any]:
        name = (name or "").strip() or "新分组"
        with self._lock:
            if len(self.groups) >= MAX_GROUPS:
                return {"ok": False, "message": f"最多 {MAX_GROUPS} 个分组"}
            g = WatchGroup(id=_new_id(), name=name[:20])
            self.groups.append(g)
            self.save()
            return {"ok": True, "group": {"id": g.id, "name": g.name, "codes": []}}

    def rename_group(self, group_id: str, name: str) -> dict[str, Any]:
        name = (name or "").strip()
        with self._lock:
            g = self._find(group_id)
            if g is None:
                return {"ok": False, "message": "分组不存在"}
            if not name:
                return {"ok": False, "message": "分组名不能为空"}
            g.name = name[:20]
            self.save()
            return {"ok": True, "group": {"id": g.id, "name": g.name, "codes": list(g.codes)}}

    def delete_group(self, group_id: str) -> dict[str, Any]:
        with self._lock:
            g = self._find(group_id)
            if g is None:
                return {"ok": False, "message": "分组不存在"}
            self.groups.remove(g)
            if not self.groups:
                self.groups.append(WatchGroup(id=DEFAULT_GROUP_ID, name=DEFAULT_GROUP_NAME))
            self.save()
            return {"ok": True, "message": "已删除分组"}

    # ── 股票增删 ────────────────────────────────────────

    def add_stock(self, group_id: str, code: str) -> dict[str, Any]:
        code = (code or "").strip()
        with self._lock:
            g = self._find(group_id)
            if g is None:
                # 目标分组不存在时自动加入默认组
                g = self.default_group()
            if code not in g.codes:
                if len(g.codes) >= MAX_CODES_PER_GROUP:
                    return {"ok": False, "message": f"单组最多 {MAX_CODES_PER_GROUP} 只"}
                g.codes.append(code)
                self.save()
            return {
                "ok": True,
                "group_id": g.id,
                "code": code,
                "in_watchlist": True,
            }

    def remove_stock(self, group_id: str, code: str) -> dict[str, Any]:
        code = (code or "").strip()
        with self._lock:
            g = self._find(group_id)
            if g is None:
                return {"ok": False, "message": "分组不存在"}
            if code in g.codes:
                g.codes.remove(code)
                self.save()
            return {"ok": True, "group_id": g.id, "code": code, "in_watchlist": False}


__all__ = [
    "PaperTradingEngine",
    "DEFAULT_UNIVERSE",
    "is_trading_time",
    "_session_label",
    "WatchlistStore",
    "WatchGroup",
]
