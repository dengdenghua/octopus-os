"""Real-time push protocol support for the paper-trading platform client."""

from __future__ import annotations

import base64
import gzip
import json
import logging
import threading
import time
import urllib.parse
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

from .upstream_url import secure_upstream_origin

if TYPE_CHECKING:
    from .live import PlatformClient

_logger = logging.getLogger(__name__)


try:
    import asyncio
    from collections import defaultdict

    import websockets

    HAS_WEBSOCKETS = True
except Exception:  # pragma: no cover
    HAS_WEBSOCKETS = False


def _ws_sign(key: int, ts: float | None = None) -> str:
    """平台签名 ``getSignString(key)``:秒级时间戳逐位 XOR key 后 base64。

    - 握手 URL 用 key=1234;
    - 订阅消息体用 key=5678。
    """
    secs = str(int(ts if ts is not None else time.time()))
    return base64.b64encode("".join(chr(ord(ch) ^ key) for ch in secs).encode()).decode()


def _gunzip_json_b64(payload: Any) -> Any:
    """gzip+base64 推送解码。非 gzip 字符串(已是 JSON)直接解析,否则原样返回。"""
    if not isinstance(payload, str):
        return payload
    if payload.startswith("H4sI"):
        try:
            return json.loads(gzip.decompress(base64.b64decode(payload)))
        except Exception as exc:  # noqa: BLE001
            _logger.warning("paper_trading: 推送 gzip 解码失败: %s", exc)
            return payload
    try:
        return json.loads(payload)
    except ValueError:
        return payload


def _normalize_quote(q: dict[str, Any]) -> dict[str, Any]:
    """把平台实时报价压成紧凑字段(去掉全量分时,量化/盯盘够用)。

    输入 ``kLineRealTime`` 里的单条报价,输出:
    code/name/market/state + price/涨跌幅/涨跌额 + 开高低收 + 量额换手 +
    十档买一买二/卖一卖二 + 更新时间。
    """
    if not isinstance(q, dict):
        return {}
    asks, bids = q.get("tenGearSell") or [], q.get("tenGearBuy") or []

    def _top(levels: list[Any]) -> list[dict[str, Any]]:
        out = []
        for lv in levels:
            if not isinstance(lv, dict):
                continue
            price = lv.get("price")
            if price:
                out.append(
                    {
                        "level": lv.get("level") or "",
                        "price": float(price),
                        "vol": int(lv.get("vol") or 0),
                    }
                )
            if len(out) >= 2:
                break
        return out

    return {
        "code": q.get("stockCode") or "",
        "name": q.get("stockName") or "",
        "market": q.get("market") or "",
        "exchange": q.get("exchangeType") or "",
        "state": q.get("stockState") or "",
        "price": q.get("currentPrice"),
        "change_pct": q.get("stockIncrease"),
        "change": q.get("stockRiseFall"),
        "open": q.get("openPrice"),
        "high": q.get("highPrice"),
        "low": q.get("lowPrice"),
        "prev_close": q.get("yClose"),
        "volume": q.get("vol"),
        "amount": q.get("amount"),
        "turnover": q.get("exchangeRate"),
        "amplitude": q.get("amplitude"),
        "pe": q.get("pe"),
        "pb": q.get("pb"),
        "bids": _top(bids),
        "asks": _top(asks),
        "ts": q.get("lastUpdateDate") or "",
    }


def _normalize_push(event: str, data: Any) -> Any:
    """把某事件推送压成紧凑结构(SSE/量化默认用)。

    - ``kLineRealTime``: data 是列表 -> 逐条 ``_normalize_quote``;
    - 其余事件(todayStock/stockPosition 等)本来就紧凑,原样返回。
    """
    if event == "kLineRealTime" and isinstance(data, dict) and isinstance(data.get("data"), list):
        return {
            "data": [_normalize_quote(q) for q in data["data"] if isinstance(q, dict)],
            "raw": False,
        }
    return data


def _secure_push_endpoint(base_url: str) -> tuple[str, str]:
    """Derive a WSS endpoint and HTTPS Origin from a trusted API base URL."""
    from .live import PlatformClientError

    origin = secure_upstream_origin(base_url)
    if not origin:
        raise PlatformClientError("实时推送仅允许 HTTPS 上游")
    parts = urllib.parse.urlsplit(base_url)
    path = parts.path.rstrip("/")
    if path.endswith("/api"):
        path = path[:-4]
    origin_parts = urllib.parse.urlsplit(origin)
    return f"wss://{origin_parts.netloc}{path}", origin


class LivePushClient:
    """平台实时行情推送客户端(只读)。

    与对方 App 同协议,连一条常驻 WebSocket,把行情**推**给本地(而非轮询):

    - 握手 URL: ``/socket.io/?EIO=3&source=h5&sign=<_ws_sign(1234)>&transport=websocket``;
    - 连接后发 ``40`` 进入默认命名空间;
    - 订阅: ``42["<event>",{url,event:"subscribe",uuid,params,token,source:"h5",sign,isCompress}]``;
    - 推送: ``42["<event>",{code:1,data:"H4sI...gzip-base64"}]``(已自动解压);
    - 心跳: 服务端定时发 ``2``,客户端回 ``3``。

    后台线程维护连接,断线自动重连(重取 token、重新 sign、重订阅)。
    每个事件支持多个回调;同时保存按事件的 ``latest`` 快照,可被量化策略/页面读取。
    """

    EVENT_CONNECT = "connect"

    def __init__(
        self,
        client: PlatformClient,
        host: str = "114.66.32.152:58868",
        *,
        reconnect_delay: float = 3.0,
        reconnect_max: float = 30.0,
        subscribe_timeout: float = 20.0,
        socket_timeout: float = 15.0,
        auto_start: bool = True,
        state_callback: Any | None = None,
    ) -> None:
        self._client = client
        self._host = host or ""
        self._reconnect_delay = float(reconnect_delay)
        self._reconnect_max = float(reconnect_max)
        self._subscribe_timeout = float(subscribe_timeout)
        self._socket_timeout = float(socket_timeout)
        self._state_callback = state_callback
        self._callbacks: dict[str, list[Any]] = defaultdict(list)
        self._global_callbacks: list[Any] = []  # 全事件回调(SSE/策略通吃)
        self._subs: dict[str, tuple[Any, list[Any]]] = {}  # event -> (params, callbacks)
        self._latest: dict[str, Any] = {}
        self._latest_at: dict[str, float] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._connected = False
        self._last_error: str = ""
        self._started_at = 0.0
        self._connected_at = 0.0
        self._reconnect_count = 0
        self._push_count: dict[str, int] = defaultdict(int)
        self._loop: Any | None = None  # 当前 WS 事件循环(用于跨线程发订阅)
        self._ws: Any | None = None  # 当前连接对象
        if auto_start:
            self.start()

    # ── 生命周期 ─────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def connected(self) -> bool:
        return self._connected

    def start(self) -> bool:
        """启动后台推送线程;已启动则幂等返回。"""
        if not HAS_WEBSOCKETS:
            self._last_error = "缺少 websockets 依赖"
            self._notify_state("failure", self._last_error)
            return False
        if not self._client.has_credentials:
            self._last_error = "未配置平台凭证"
            self._notify_state("failure", self._last_error)
            return False
        with self._lock:
            if self.running:
                return True
            self._stop.clear()
            self._started_at = time.time()
            self._thread = threading.Thread(
                target=self._run_loop, name="paper-trading-push", daemon=True
            )
            self._thread.start()
            return True

    def stop(self) -> None:
        """Stop the worker and actively interrupt an in-flight receive.

        Merely setting ``_stop`` does not wake ``ws.recv()``; its timeout is
        longer than the plugin unload grace period. Keep the worker reference
        until it really exits so a reload cannot start a second credentialed
        connection while the old one is still alive.
        """
        self._stop.set()
        with self._lock:
            thread = self._thread
            loop = self._loop
            ws = self._ws
            self._connected = False

        if loop is not None and ws is not None and loop.is_running():
            close_coro = ws.close()
            try:
                if thread is threading.current_thread():
                    loop.create_task(close_coro)
                    close_coro = None
                else:
                    close_future = asyncio.run_coroutine_threadsafe(close_coro, loop)
                    close_coro = None
                    close_future.result(timeout=1.0)
            except Exception as exc:  # noqa: BLE001 - shutdown remains best-effort
                # ``run_coroutine_threadsafe`` can fail before taking ownership
                # (for example, the loop closed between is_running and submit).
                if close_coro is not None:
                    close_coro.close()
                _logger.debug("paper_trading: 推送连接关闭失败: %s", exc)

        if thread is not None and thread is not threading.current_thread() and thread.is_alive():
            thread.join(timeout=3.0)
        still_alive = thread is not None and thread.is_alive()
        with self._lock:
            if self._thread is thread and not still_alive:
                self._thread = None
        if still_alive:
            _logger.warning("paper_trading: 推送线程未在停止期限内退出；保留线程引用以阻止重复启动")

    # ── 订阅 / 回调 ──────────────────────────────────────

    def subscribe(
        self,
        event: str,
        params: list[Any] | None = None,
        callback: Any | None = None,
    ) -> None:
        """订阅某个推送事件。

        - ``event``: ``kLineRealTime``(个股实时)/ ``todayStock``(大盘)/
          ``itemByStepDetailsV3``(分时+盘口)/ ``stockPosition``(持仓) 等;
        - ``params``: 订阅参数(如个股代码列表 ["605080.sh","003032.sz"]);
        - ``callback``: 可选,收到该事件数据时调用 callback(event, data)。
        断线重连后会自动重新订阅所有已注册事件。
        """
        with self._lock:
            self._subs[event] = (list(params or []), self._callbacks[event])
            if callback is not None and callback not in self._callbacks[event]:
                self._callbacks[event].append(callback)
        # 已连上:立即向服务器发订阅帧,拿到实时/快照推送
        self._send_subscribe_now(event, list(params or []))

    def _send_subscribe_now(self, event: str, params: list[Any]) -> None:
        loop = self._loop
        if loop is None or self._ws is None:
            return
        token = self._client._token or ""
        try:
            asyncio.run_coroutine_threadsafe(self._send_frame(loop, event, params, token), loop)
        except Exception as exc:  # noqa: BLE001
            _logger.warning("paper_trading: 推送订阅帧失败(%s): %s", event, exc)

    async def _send_frame(self, loop: Any, event: str, params: list[Any], token: str) -> None:
        ws = self._ws
        if ws is None:
            return
        try:
            await ws.send(self._frame(event, params, token))
        except Exception as exc:  # noqa: BLE001
            _logger.warning("paper_trading: 发送订阅帧失败(%s): %s", event, exc)

    def unsubscribe(self, event: str, callback: Any | None = None) -> None:
        with self._lock:
            if callback is not None:
                self._callbacks[event] = [
                    c for c in self._callbacks[event] if c is not None and c != callback
                ]
                if self._callbacks[event]:
                    self._subs[event] = (
                        list(self._subs.get(event, ([], []))[0]),
                        self._callbacks[event],
                    )
                    return
            self._subs.pop(event, None)
            self._callbacks.pop(event, None)

    def add_global_callback(self, callback: Any) -> None:
        """注册全事件回调:任一推送事件都会调用 callback(event, data)。"""
        with self._lock:
            if callback not in self._global_callbacks:
                self._global_callbacks.append(callback)

    def remove_global_callback(self, callback: Any) -> None:
        with self._lock:
            self._global_callbacks = [c for c in self._global_callbacks if c is not callback]

    def latest(self, event: str) -> Any:
        """最近一次收到的(已解码)事件数据;无则 None。"""
        with self._lock:
            return self._latest.get(event)

    def latest_at(self, event: str) -> float:
        with self._lock:
            return self._latest_at.get(event, 0.0)

    def push_count(self, event: str) -> int:
        with self._lock:
            return self._push_count.get(event, 0)

    def status(self) -> dict[str, Any]:
        """连接状态摘要(供 /live/push/status)。"""
        with self._lock:
            return {
                "enabled": HAS_WEBSOCKETS and self._client.has_credentials,
                "running": self.running,
                "connected": self._connected,
                "host": self._host,
                "started_at": datetime.fromtimestamp(self._started_at).isoformat(timespec="seconds")
                if self._started_at
                else "",
                "connected_at": datetime.fromtimestamp(self._connected_at).isoformat(
                    timespec="seconds"
                )
                if self._connected_at
                else "",
                "reconnect_count": self._reconnect_count,
                "last_error": self._last_error,
                "events": {
                    ev: {
                        "params": params,
                        "pushes": self._push_count.get(ev, 0),
                        "last_at": datetime.fromtimestamp(self._latest_at.get(ev, 0)).isoformat(
                            timespec="seconds"
                        )
                        if self._latest_at.get(ev, 0)
                        else "",
                    }
                    for ev, (params, _cb) in self._subs.items()
                },
            }

    # ── 内部:后台线程 ────────────────────────────────────

    def _run_loop(self) -> None:
        backoff = self._reconnect_delay
        while not self._stop.is_set():
            try:
                asyncio.run(self._connect_once())
                backoff = self._reconnect_delay
            except Exception as exc:  # noqa: BLE001 — 重连循环,必须全兜住
                self._last_error = str(exc)[:200]
                if not self._stop.is_set():
                    self._notify_state("failure", self._last_error)
                    _logger.warning("paper_trading: 推送连接异常,%.0fs 后重连: %s", backoff, exc)
            self._connected = False
            if self._stop.is_set():
                break
            self._stop.wait(backoff)
            backoff = min(backoff * 2, self._reconnect_max)

    async def _connect_once(self) -> None:
        from .live import PlatformClientError

        client = self._client
        try:
            base, origin = _secure_push_endpoint(self._host)
        except PlatformClientError as exc:
            self._last_error = str(exc)
            _logger.warning("paper_trading: 已拒绝不安全的推送连接: %s", exc)
            raise
        try:
            token = client.login()  # token 未过期则不发网络请求
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"登录失败: {exc}"
            _logger.warning("paper_trading: 推送前登录失败: %s", exc)
            raise PlatformClientError(self._last_error) from exc
        if not self._host:
            self._last_error = "推送 host 为空"
            raise PlatformClientError(self._last_error)
        q = urllib.parse.urlencode(
            {"EIO": 3, "source": "h5", "sign": _ws_sign(1234), "transport": "websocket"}
        )
        url = f"{base}/socket.io/?{q}"
        _logger.info("paper_trading: 推送连接 %s", url[:80])
        try:
            async with websockets.connect(
                url,
                origin=cast(Any, origin),
                compression=None,
                proxy=None,
                ping_interval=None,
            ) as ws:
                self._loop = asyncio.get_running_loop()
                self._ws = ws
                # engine.io open 包
                await asyncio.wait_for(ws.recv(), timeout=self._subscribe_timeout)
                # socket.io v2 默认命名空间 CONNECT
                await ws.send("40")
                await asyncio.wait_for(ws.recv(), timeout=self._subscribe_timeout)
                self._connected = True
                self._connected_at = time.time()
                self._last_error = ""
                self._notify_state("connected", "")
                # 重订阅所有事件
                with self._lock:
                    subs = list(self._subs.items())
                for ev, (params, _cb) in subs:
                    await ws.send(self._frame(ev, params, token))
                    _logger.info("paper_trading: 已订阅 %s %s", ev, params)
                # 读循环
                while not self._stop.is_set():
                    try:
                        frame = await asyncio.wait_for(ws.recv(), timeout=self._socket_timeout)
                    except TimeoutError:
                        await ws.send("3")  # 超时兜底发 pong
                        continue
                    if frame == "2":  # engine.io ping -> pong
                        await ws.send("3")
                        continue
                    if isinstance(frame, bytes):
                        frame = frame.decode("utf-8", errors="replace")
                    self._on_frame(frame)
        finally:
            self._connected = False
            self._loop = None
            self._ws = None

    def _notify_state(self, state: str, error: str = "") -> None:
        callback = self._state_callback
        if callback is None:
            return
        try:
            callback(state, error)
        except Exception as exc:  # noqa: BLE001 - observer cannot stop reconnects
            _logger.warning("paper_trading: 推送状态回调异常(%s): %s", state, exc)

    def _frame(self, event: str, params: list[Any], token: str) -> str:
        payload = {
            "url": event,
            "event": "subscribe",
            "uuid": f"{int(time.time() * 1000)}-{abs(hash(event + str(params))) % 10_000_000}",
            "params": list(params or []),
            "token": token,
            "source": "h5",
            "sign": _ws_sign(5678),
            "isCompress": True,
        }
        return f'42["{event}",{json.dumps(payload, ensure_ascii=False)}]'

    def _on_frame(self, frame: str) -> None:
        if not frame.startswith("42"):
            return
        body = frame[2:]
        try:
            arr = json.loads(body)
        except ValueError:
            return
        if not isinstance(arr, list) or len(arr) < 2:
            return
        event, data = arr[0], arr[1]
        if event == self.EVENT_CONNECT:
            return
        decoded = data
        if isinstance(data, dict) and data.get("data") is not None:
            decoded = dict(data)
            decoded["data"] = _gunzip_json_b64(data.get("data"))
        with self._lock:
            self._latest[event] = decoded
            self._latest_at[event] = time.time()
            self._push_count[event] += 1
            callbacks = list(self._callbacks.get(event, [])) + list(self._global_callbacks)
        for cb in callbacks:
            try:
                cb(event, decoded)
            except Exception as exc:  # noqa: BLE001 — 回调异常不能拖垮推送
                _logger.warning("paper_trading: 推送回调异常(%s): %s", event, exc)


__all__ = [
    "HAS_WEBSOCKETS",
    "LivePushClient",
    "_gunzip_json_b64",
    "_normalize_push",
    "_normalize_quote",
    "_secure_push_endpoint",
    "_ws_sign",
]
