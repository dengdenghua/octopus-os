"""Implementation note."""

from __future__ import annotations

import base64
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from runtime.adapters.channels import (
    ChannelManager,
    InboundMessage,
    OutboundMessage,
    QRLoginTimeout,
    WeixinBotChannel,
    WeixinBotError,
)
from runtime.core.graph_runtime import GraphRuntime
from runtime.execution.agents import AgentRegistry, make_general_agent

# ═══════════════════════════════════════════════════════════
# HTTP mock
# ═══════════════════════════════════════════════════════════


class _FakeResp:
    def __init__(
        self,
        *,
        status_code: int = 200,
        body: Any = None,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.text = text or json.dumps(self._body)

    def json(self):
        return self._body


class _SeqHttp:
    """Implementation note."""

    def __init__(self, responses: list[_FakeResp]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def _serve(self):
        if not self._responses:
            raise AssertionError(
                "no more fake responses · unexpected call count",
            )
        return self._responses.pop(0)

    def get(self, url, params=None, headers=None, **_kw):
        self.calls.append(
            {
                "method": "GET",
                "url": url,
                "params": params,
                "headers": headers,
            }
        )
        return self._serve()

    def post(self, url, params=None, json=None, headers=None, **_kw):
        self.calls.append(
            {
                "method": "POST",
                "url": url,
                "params": params,
                "json": json,
                "headers": headers,
            }
        )
        return self._serve()


class _RouteHttp:
    """Implementation note."""

    def __init__(self, routes: dict[str, Any]) -> None:
        self._routes = routes
        self.calls: list[dict[str, Any]] = []

    def _serve(self, url: str):
        for path, gen in self._routes.items():
            if path in url:
                if callable(gen):
                    return gen()
                return gen
        raise AssertionError(f"no route for {url}")

    def get(self, url, params=None, headers=None, **_kw):
        self.calls.append(
            {
                "method": "GET",
                "url": url,
                "params": params,
                "headers": headers,
            }
        )
        return self._serve(url)

    def post(self, url, params=None, json=None, headers=None, **_kw):
        self.calls.append(
            {
                "method": "POST",
                "url": url,
                "params": params,
                "json": json,
                "headers": headers,
            }
        )
        return self._serve(url)


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestConstruct:
    def test_default_channel_id(self):
        ch = WeixinBotChannel(bot_token="t")
        assert ch.channel_id == "weixin_bot"

    def test_custom_channel_id(self):
        ch = WeixinBotChannel(bot_token="t", channel_id="wx_main")
        assert ch.channel_id == "wx_main"

    def test_api_base_strips_slash(self):
        ch = WeixinBotChannel(
            bot_token="t",
            api_base_url="https://example.com/",
        )
        assert ch.api_base_url == "https://example.com"

    def test_load_token_from_file(self, tmp_path: Path):
        p = tmp_path / "tok.json"
        p.write_text(json.dumps({"bot_token": "persisted", "baseurl": ""}))
        ch = WeixinBotChannel(token_path=p)
        assert ch._bot_token == "persisted"

    def test_missing_token_file_is_ok(self, tmp_path: Path):
        ch = WeixinBotChannel(token_path=tmp_path / "nope.json")
        assert ch._bot_token is None


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestQRLogin:
    def test_happy_path(self, tmp_path: Path):
        tiny_png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * 100).decode()
        http = _SeqHttp(
            [
                _FakeResp(
                    body={
                        "qrcode": "QR-ABC",
                        "qrcode_img_content": tiny_png,
                    }
                ),
                _FakeResp(body={"status": "pending"}),
                _FakeResp(
                    body={
                        "status": "confirmed",
                        "bot_token": "LIVE-TOKEN",
                        "baseurl": "https://ilinkai.weixin.qq.com",
                    }
                ),
            ]
        )
        ch = WeixinBotChannel(
            http_client=http,
            token_path=tmp_path / "tok.json",
            qr_png_path=tmp_path / "qr.png",
            qr_poll_interval_s=0.01,
            qr_poll_timeout_s=5.0,
        )
        ch._qr_login_flow()
        assert ch._bot_token == "LIVE-TOKEN"
        # Implementation note.
        stored = json.loads((tmp_path / "tok.json").read_text())
        assert stored["bot_token"] == "LIVE-TOKEN"
        # Implementation note.
        assert (tmp_path / "qr.png").exists()

    def test_rejected_raises(self, tmp_path: Path):
        http = _SeqHttp(
            [
                _FakeResp(
                    body={
                        "qrcode": "Q",
                        "qrcode_img_content": base64.b64encode(b"ok").decode(),
                    }
                ),
                _FakeResp(body={"status": "rejected"}),
            ]
        )
        ch = WeixinBotChannel(
            http_client=http,
            qr_png_path=tmp_path / "qr.png",
            qr_poll_interval_s=0.01,
        )
        with pytest.raises(WeixinBotError, match="rejected"):
            ch._qr_login_flow()

    def test_expired_raises(self, tmp_path: Path):
        http = _SeqHttp(
            [
                _FakeResp(
                    body={
                        "qrcode": "Q",
                        "qrcode_img_content": base64.b64encode(b"ok").decode(),
                    }
                ),
                _FakeResp(body={"status": "expired"}),
            ]
        )
        ch = WeixinBotChannel(
            http_client=http,
            qr_png_path=tmp_path / "qr.png",
            qr_poll_interval_s=0.01,
        )
        with pytest.raises(WeixinBotError, match="expired"):
            ch._qr_login_flow()

    def test_timeout_raises(self, tmp_path: Path):
        # Implementation note.
        def _forever_pending():
            return _FakeResp(body={"status": "pending"})

        http = _RouteHttp(
            {
                "/get_bot_qrcode": _FakeResp(
                    body={
                        "qrcode": "Q",
                        "qrcode_img_content": base64.b64encode(b"ok").decode(),
                    }
                ),
                "/get_qrcode_status": _forever_pending,
            }
        )
        ch = WeixinBotChannel(
            http_client=http,
            qr_png_path=tmp_path / "qr.png",
            qr_poll_interval_s=0.01,
            qr_poll_timeout_s=0.05,
        )
        with pytest.raises(QRLoginTimeout):
            ch._qr_login_flow()

    def test_bad_qr_response_shape(self, tmp_path: Path):
        http = _SeqHttp(
            [
                _FakeResp(body={"no_qrcode": "here"}),
            ]
        )
        ch = WeixinBotChannel(
            http_client=http,
            qr_png_path=tmp_path / "qr.png",
        )
        with pytest.raises(WeixinBotError, match="shape"):
            ch._qr_login_flow()


# ═══════════════════════════════════════════════════════════
# Headers
# ═══════════════════════════════════════════════════════════


class TestHeaders:
    def test_authorized_headers_include_bearer(self):
        ch = WeixinBotChannel(bot_token="my-token")
        h = ch._headers(auth=True)
        assert h["Authorization"] == "Bearer my-token"
        assert h["AuthorizationType"] == "ilink_bot_token"
        assert "X-WECHAT-UIN" in h
        assert h["Content-Type"] == "application/json"

    def test_unauthed_headers_skip_bearer(self):
        ch = WeixinBotChannel()
        h = ch._headers(auth=False)
        assert "Authorization" not in h

    def test_authed_without_token_raises(self):
        ch = WeixinBotChannel()  # no token
        with pytest.raises(WeixinBotError, match="bot_token"):
            ch._headers(auth=True)


# ═══════════════════════════════════════════════════════════
# send message
# ═══════════════════════════════════════════════════════════


class TestSend:
    def test_text_send_payload(self):
        http = _SeqHttp([_FakeResp(body={"ret": 0})])
        ch = WeixinBotChannel(bot_token="T", http_client=http)
        msg = OutboundMessage(
            channel_id="weixin_bot",
            thread_id="user@im.wechat",
            content="你好",
            metadata={"context_token": "CTX-123"},
        )
        ch.send(msg)
        assert len(http.calls) == 1
        call = http.calls[0]
        assert call["url"].endswith("/sendmessage")
        body = call["json"]["msg"]
        assert body["to_user_id"] == "user@im.wechat"
        assert body["context_token"] == "CTX-123"
        assert body["message_type"] == 2
        assert body["item_list"][0]["type"] == 1
        assert body["item_list"][0]["text_item"]["text"] == "你好"

    def test_to_user_id_from_metadata_preferred(self):
        """Implementation note."""
        http = _SeqHttp([_FakeResp(body={"ret": 0})])
        ch = WeixinBotChannel(bot_token="T", http_client=http)
        ch.send(
            OutboundMessage(
                channel_id="weixin_bot",
                thread_id="fallback",
                content="x",
                metadata={"to_user_id": "specific@im.wechat", "context_token": ""},
            )
        )
        assert http.calls[0]["json"]["msg"]["to_user_id"] == "specific@im.wechat"

    def test_send_without_token_raises(self):
        ch = WeixinBotChannel()
        with pytest.raises(WeixinBotError, match="logged in"):
            ch.send(
                OutboundMessage(
                    channel_id="weixin_bot",
                    thread_id="x",
                    content="y",
                )
            )


# ═══════════════════════════════════════════════════════════
# parse_inbound
# ═══════════════════════════════════════════════════════════


class TestParseInbound:
    def test_text_message(self):
        ch = WeixinBotChannel(bot_token="t")
        msg = ch._parse_inbound(
            {
                "msg_id": "M1",
                "from_user_id": "user@im.wechat",
                "conversation_id": "conv-abc",
                "context_token": "CTX",
                "item_list": [
                    {"type": 1, "text_item": {"text": "hello"}},
                ],
            }
        )
        assert msg is not None
        assert msg.channel_id == "weixin_bot"
        assert msg.thread_id == "conv-abc"
        assert msg.sender_id == "user@im.wechat"
        assert msg.content == "hello"
        assert msg.metadata["context_token"] == "CTX"
        assert msg.metadata["to_user_id"] == "user@im.wechat"

    def test_fallback_to_sender_as_thread(self):
        ch = WeixinBotChannel(bot_token="t")
        msg = ch._parse_inbound(
            {
                "sender_id": "user@im.wechat",
                "item_list": [{"type": 1, "text_item": {"text": "x"}}],
            }
        )
        assert msg is not None
        assert msg.thread_id == "user@im.wechat"

    def test_non_text_filtered(self):
        ch = WeixinBotChannel(bot_token="t")
        msg = ch._parse_inbound(
            {
                "from_user_id": "u",
                "conversation_id": "c",
                "item_list": [{"type": 2, "image_item": {"url": "..."}}],
            }
        )
        assert msg is None

    def test_empty_text_filtered(self):
        ch = WeixinBotChannel(bot_token="t")
        msg = ch._parse_inbound(
            {
                "from_user_id": "u",
                "conversation_id": "c",
                "item_list": [{"type": 1, "text_item": {"text": ""}}],
            }
        )
        assert msg is None

    def test_malformed_returns_none(self):
        ch = WeixinBotChannel(bot_token="t")
        assert ch._parse_inbound("not a dict") is None
        assert ch._parse_inbound({}) is None


# ═══════════════════════════════════════════════════════════
# long-poll loop · mock getupdates
# ═══════════════════════════════════════════════════════════


class TestLongPoll:
    def test_single_message_dispatched(self):
        # Implementation note.
        responses = [
            _FakeResp(
                body={
                    "ret": 0,
                    "get_updates_buf": "cursor-v2",
                    "msgs": [
                        {
                            "msg_id": "M1",
                            "from_user_id": "u@wx",
                            "conversation_id": "conv-1",
                            "context_token": "CTX-1",
                            "item_list": [
                                {"type": 1, "text_item": {"text": "hello"}},
                            ],
                        }
                    ],
                }
            ),
            _FakeResp(
                body={
                    "ret": 0,
                    "get_updates_buf": "cursor-v3",
                    "msgs": [],
                }
            ),
        ]
        remaining = list(responses)

        def _serve_or_stop():
            if remaining:
                return remaining.pop(0)
            # Implementation note.
            time.sleep(0.05)
            return _FakeResp(
                body={
                    "ret": 0,
                    "get_updates_buf": "",
                    "msgs": [],
                }
            )

        http = _RouteHttp({"/getupdates": _serve_or_stop})

        dispatched: list[InboundMessage] = []
        ev = threading.Event()

        def fake_dispatcher(msg):
            dispatched.append(msg)
            ev.set()

        ch = WeixinBotChannel(bot_token="T", http_client=http)
        ch.bind_dispatcher(fake_dispatcher)
        ch.start()
        try:
            assert ev.wait(2.0), "dispatcher not called within timeout"
        finally:
            ch.stop()

        assert len(dispatched) == 1
        assert dispatched[0].content == "hello"
        # Implementation note.
        # Implementation note.

    def test_cursor_sent_in_payload(self):
        got_bodies: list[dict] = []
        stop_after_first = threading.Event()

        def _route():
            if len(got_bodies) >= 1:
                stop_after_first.set()
                time.sleep(0.05)
            return _FakeResp(
                body={
                    "ret": 0,
                    "get_updates_buf": "CURSOR-NEW",
                    "msgs": [],
                }
            )

        class _CaptureHttp:
            def post(self, url, params=None, json=None, headers=None, **_kw):
                got_bodies.append(json)
                return _route()

            def get(self, *a, **kw):
                raise AssertionError("unexpected GET")

        ch = WeixinBotChannel(bot_token="T", http_client=_CaptureHttp())
        ch.bind_dispatcher(lambda m: None)
        ch.start()
        try:
            stop_after_first.wait(2.0)
        finally:
            ch.stop()

        # Implementation note.
        assert got_bodies[0]["get_updates_buf"] == ""
        assert got_bodies[0]["base_info"]["channel_version"]


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestAuthExpired:
    def test_401_clears_token_file(self, tmp_path: Path):
        token_path = tmp_path / "tok.json"
        token_path.write_text(json.dumps({"bot_token": "OLD"}))
        http = _SeqHttp(
            [
                _FakeResp(status_code=401, text='{"err":"expired"}'),
            ]
        )
        ch = WeixinBotChannel(
            bot_token="OLD",
            http_client=http,
            token_path=token_path,
        )
        with pytest.raises(WeixinBotError, match="HTTP 401"):
            ch.send(
                OutboundMessage(
                    channel_id="weixin_bot",
                    thread_id="x",
                    content="y",
                    metadata={"to_user_id": "u", "context_token": ""},
                )
            )
        # Implementation note.
        assert ch._bot_token is None
        assert not token_path.exists()


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class _FakeExecutor:
    journal = None


def _rt():
    return GraphRuntime(executor=_FakeExecutor(), journal=None)


def _build_stack(tmp_path: Path):
    from runtime.core.cerebrum import StaticPlanner
    from runtime.core.cerebrum.planner import Rule
    from runtime.execution.suckers import SkillRegistry
    from runtime.execution.suckers.builtins import register_all
    from runtime.execution.tool_engine import ToolExecutor
    from runtime.memory.journal import JSONLJournal
    from runtime.platform.models import BudgetSpec, SkillId
    from runtime.safety.auth import TrustEngine

    journal = JSONLJournal(tmp_path / "events.jsonl")
    registry = SkillRegistry()
    register_all(registry)
    executor = ToolExecutor(
        registry=registry,
        immunity=TrustEngine(trusted_sources=["skill://public/*"]),
        journal=journal,
    )
    runtime = GraphRuntime(executor=executor, journal=journal)
    planner = StaticPlanner(
        rules=[
            Rule(
                name="default",
                intent_types=["task"],
                skill_sequence=[SkillId("list_cwd")],
            )
        ],
        default_budget=BudgetSpec(tokens=10_000, usd=0.10),
        fallback_skill=SkillId("list_cwd"),
    )

    class _S:
        pass

    s = _S()
    s.planner = planner
    s.runtime = runtime
    s.registry = registry
    s.journal = journal
    return s


class TestManagerIntegration:
    def test_register_binds_dispatcher(self, tmp_path: Path):
        stack = _build_stack(tmp_path)
        reg = AgentRegistry()
        reg.register(make_general_agent(_rt()))

        http_send = _SeqHttp([_FakeResp(body={"ret": 0})])
        ch = WeixinBotChannel(
            bot_token="TOK",
            http_client=http_send,
        )
        m = ChannelManager(
            stack=stack,
            agent_registry=reg,
            default_agent_id="general",
        )
        # Implementation note.
        m.register(ch)
        assert ch._dispatcher is not None

        # Implementation note.
        msg = InboundMessage(
            channel_id="weixin_bot",
            thread_id="conv",
            sender_id="u@im.wechat",
            content="list files",
            metadata={"context_token": "CTX", "to_user_id": "u@im.wechat"},
        )
        out = m.process_inbound(msg)
        assert out.channel_id == "weixin_bot"
        # Implementation note.
        assert len(http_send.calls) == 1
        post = http_send.calls[0]["json"]["msg"]
        assert post["context_token"] == "CTX"
        assert post["to_user_id"] == "u@im.wechat"
