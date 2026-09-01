"""Tests for the ``paper_trading`` real-time push client (socket.io/engine.io).

关键点:只测协议层的纯函数/帧处理/回调分发,**不真连网**。
覆盖:

  1. ``_ws_sign``:握手(1234)与订阅(5678)两种签名与抓包值一致
  2. ``_gunzip_json_b64``:gzip+base64 解压、普通 JSON、非法输入
  3. ``LivePushClient._frame``:订阅帧格式正确(sign/token/isCompress)
  4. ``_on_frame``:ping 忽略、解码、回调、latest 快照、推送计数
  5. subscribe / unsubscribe / status 的线程安全语义
  6. 插件 ``_push_client``:未配凭证 → None(优雅降级)
"""

from __future__ import annotations

import base64
import gzip
import json

import pytest

from runtime.platform.plugins.bundled.paper_trading import PaperTradingPlugin
from runtime.platform.plugins.bundled.paper_trading.live import (
    DEFAULT_BASE_URL,
    LiveDataSource,
    LivePushClient,
    PlatformClient,
    _gunzip_json_b64,
    _secure_push_endpoint,
    _ws_sign,
)


def _gzip_b64(obj) -> str:
    raw = json.dumps(obj, ensure_ascii=False).encode()
    return base64.b64encode(gzip.compress(raw)).decode()


class TestWsSign:
    def test_handshake_key_1234(self):
        # 抓包实测:1787166486,key=1234
        assert _ws_sign(1234, 1787166486) == "06PTpdOq06XTo9Ok06TTptOq06Q="

    def test_subscribe_key_5678(self):
        # 抓包实测:1787166584,key=5678
        assert _ws_sign(5678, 1787166584) == "4Zif4ZiZ4ZiW4ZiZ4Zif4ZiY4ZiY4Zib4ZiW4Zia"


class TestGunzip:
    def test_gzip_b64(self):
        assert _gunzip_json_b64(_gzip_b64({"a": 1})) == {"a": 1}

    def test_plain_json(self):
        assert _gunzip_json_b64('{"a": 1}') == {"a": 1}

    def test_invalid(self):
        assert _gunzip_json_b64("not-json") == "not-json"

    def test_non_str(self):
        assert _gunzip_json_b64([1, 2]) == [1, 2]


class TestPushClient:
    def _client(self, credentials=True):
        c = PlatformClient("http://x/api", phone="1" * 11, password="pw")
        if credentials:
            c._token = "tok"
        return c

    def test_frame_format(self):
        c = self._client()
        push = LivePushClient(c, host="h", auto_start=False)
        frame = push._frame("kLineRealTime", ["605080.sh"], "TOKEN")
        assert frame.startswith('42["kLineRealTime",')
        arr = json.loads(frame[2:])
        assert arr[0] == "kLineRealTime"
        body = arr[1]
        assert body["url"] == "kLineRealTime"
        assert body["event"] == "subscribe"
        assert body["params"] == ["605080.sh"]
        assert body["token"] == "TOKEN"
        assert body["source"] == "h5"
        assert body["isCompress"] is True
        assert body["sign"]
        assert body["uuid"]

    def test_on_frame_dispatch(self):
        c = self._client()
        push = LivePushClient(c, host="h", auto_start=False)
        got = []
        push.subscribe("kLineRealTime", ["605080.sh"], lambda ev, d: got.append((ev, d)))
        data = {"code": 1, "data": _gzip_b64([{"stockCode": "605080", "currentPrice": 20.5}])}
        push._on_frame('42["kLineRealTime",' + json.dumps(data) + "]")
        assert len(got) == 1
        ev, d = got[0]
        assert ev == "kLineRealTime"
        assert d["data"][0]["currentPrice"] == 20.5
        # latest snapshot
        latest = push.latest("kLineRealTime")
        assert latest["data"][0]["stockCode"] == "605080"
        assert push.push_count("kLineRealTime") == 1

    def test_on_frame_ping_ignored(self):
        c = self._client()
        push = LivePushClient(c, host="h", auto_start=False)
        push.subscribe("todayStock", [], lambda *a: None)
        push._on_frame("2")  # engine.io ping,not a push
        assert push.latest("todayStock") is None

    def test_unsubscribe_stops_dispatch(self):
        c = self._client()
        push = LivePushClient(c, host="h", auto_start=False)
        got = []

        def _cb(ev: object, d: object) -> None:
            got.append(ev)

        cb = _cb
        push.subscribe("kLineRealTime", [], cb)
        push.unsubscribe("kLineRealTime", cb)
        push._on_frame('42["kLineRealTime",' + json.dumps({"data": _gzip_b64([])}) + "]")
        assert got == []

    def test_status_shape(self):
        c = self._client()
        push = LivePushClient(c, host="h", auto_start=False)
        push.subscribe("todayStock", [], lambda *a: None)
        st = push.status()
        assert st["enabled"] is True
        assert st["running"] is False
        assert "todayStock" in st["events"]

    def test_no_credentials_start_false(self):
        c = PlatformClient("http://x/api", phone="", password="")
        states = []
        push = LivePushClient(
            c,
            host="h",
            auto_start=False,
            state_callback=lambda state, error: states.append((state, error)),
        )
        assert push.start() is False
        assert "凭证" in push._last_error
        assert states == [("failure", "未配置平台凭证")]

    def test_state_callback_isolated_from_push_worker(self):
        c = self._client()

        def broken_callback(state, error):  # noqa: ARG001
            raise RuntimeError("observer failed")

        push = LivePushClient(c, host="h", auto_start=False, state_callback=broken_callback)
        push._notify_state("connected")

    def test_stop_keeps_live_worker_reference_to_prevent_duplicate_start(self):
        class _BlockedThread:
            alive = True
            joins = 0

            def is_alive(self):
                return self.alive

            def join(self, timeout=None):  # noqa: ARG002
                self.joins += 1

        c = self._client()
        push = LivePushClient(c, host="h", auto_start=False)
        blocked = _BlockedThread()
        push._thread = blocked

        push.stop()

        assert blocked.joins == 1
        assert push._thread is blocked
        # A reload/start attempt sees the still-live worker and is idempotent;
        # it cannot launch a second authenticated connection.
        assert push.start() is True
        assert push._thread is blocked

        blocked.alive = False
        push.stop()
        assert push._thread is None

    def test_https_api_derives_wss_push_and_https_origin(self):
        endpoint, origin = _secure_push_endpoint("https://up.test:9443/api")
        assert endpoint == "wss://up.test:9443"
        assert origin == "https://up.test:9443"

    @pytest.mark.parametrize("base_url", ["http://up.test/api", "ws://up.test", "up.test"])
    def test_push_rejects_non_https_base(self, base_url):
        from runtime.platform.plugins.bundled.paper_trading.live import PlatformClientError

        with pytest.raises(PlatformClientError, match="HTTPS"):
            _secure_push_endpoint(base_url)


class TestPluginPush:
    def test_push_client_none_without_credentials(self, tmp_path):
        plugin = PaperTradingPlugin()
        # 不触发 on_load 的完整插件,直接造一个未配凭证的 live
        plugin.live = LiveDataSource.from_config(
            {"base_url": DEFAULT_BASE_URL, "live_ttl": 30},
            state_dir=str(tmp_path),
            credentials_file=str(tmp_path / "nope.json"),
        )
        assert plugin._push_client() is None


class TestGlobalCallback:
    def test_global_callback_receives_all_events(self):
        c = PlatformClient("http://x/api", phone="1" * 11, password="pw")
        c._token = "tok"
        push = LivePushClient(c, host="h", auto_start=False)
        got = []
        push.add_global_callback(lambda ev, d: got.append(ev))
        push.subscribe("kLineRealTime", ["605080.sh"])
        push.subscribe("todayStock", [])
        push._on_frame('42["kLineRealTime",' + json.dumps({"data": _gzip_b64([])}) + "]")
        push._on_frame('42["todayStock",' + json.dumps({"data": _gzip_b64({})}) + "]")
        assert got == ["kLineRealTime", "todayStock"]

    def test_remove_global_callback(self):
        c = PlatformClient("http://x/api", phone="1" * 11, password="pw")
        c._token = "tok"
        push = LivePushClient(c, host="h", auto_start=False)
        got = []

        def _cb2(ev: object, d: object) -> None:
            got.append(ev)

        cb = _cb2
        push.add_global_callback(cb)
        push.remove_global_callback(cb)
        push.subscribe("todayStock", [])
        push._on_frame('42["todayStock",' + json.dumps({"data": _gzip_b64({})}) + "]")
        assert got == []


class TestNormalize:
    def test_normalize_quote_compact(self):
        from runtime.platform.plugins.bundled.paper_trading.live import _normalize_quote

        q = {
            "stockCode": "605080",
            "stockName": "浙江自然",
            "market": "CN",
            "exchangeType": "SH",
            "stockState": "交易中",
            "currentPrice": 20.31,
            "stockIncrease": -0.68,
            "stockRiseFall": -0.14,
            "openPrice": 20.38,
            "highPrice": 20.64,
            "lowPrice": 19.93,
            "yClose": 20.45,
            "vol": "230.42万",
            "amount": "4656.59万",
            "exchangeRate": 1.64,
            "amplitude": 3.47,
            "tenGearBuy": [
                {"price": 20.3, "vol": 100, "volStr": "100", "level": "买1"},
                {"price": 20.29, "vol": 200, "level": "买2"},
            ],
            "tenGearSell": [{"price": 20.32, "vol": 50, "level": "卖1"}],
            "lastUpdateDate": "08-19 16:00:00",
            "minuteKDataVOList": [{"time": "930", "price": 20.3}],
        }
        n = _normalize_quote(q)
        assert n["code"] == "605080"
        assert n["price"] == 20.31
        assert n["change_pct"] == -0.68
        assert n["bids"] == [
            {"level": "买1", "price": 20.3, "vol": 100},
            {"level": "买2", "price": 20.29, "vol": 200},
        ]
        assert n["asks"] == [{"level": "卖1", "price": 20.32, "vol": 50}]
        assert "minuteKDataVOList" not in n

    def test_normalize_push_kline(self):
        from runtime.platform.plugins.bundled.paper_trading.live import _normalize_push

        data = {
            "code": 1,
            "data": [{"stockCode": "605080", "currentPrice": 20.31, "tenGearBuy": []}],
        }
        out = _normalize_push("kLineRealTime", data)
        assert out["data"][0]["price"] == 20.31

    def test_normalize_push_passthrough(self):
        from runtime.platform.plugins.bundled.paper_trading.live import _normalize_push

        data = {"up": 1, "down": 2}
        assert _normalize_push("todayStock", data) is data

