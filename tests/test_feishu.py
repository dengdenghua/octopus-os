"""Implementation note."""

from __future__ import annotations

import json
from typing import Any

import pytest
from runtime.adapters.channels import (
    FeishuChannel,
    FeishuError,
    FeishuSignatureError,
    InboundMessage,
    OutboundMessage,
)

# ═══════════════════════════════════════════════════════════
# HTTP mock
# ═══════════════════════════════════════════════════════════


class _FakeResp:
    def __init__(self, *, status_code=200, body=None, text=""):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.text = text or json.dumps(body) if body else ""

    def json(self):
        return self._body


class _RouteHttp:
    """Implementation note."""

    def __init__(self, routes: dict[str, _FakeResp]):
        self.routes = routes
        self.calls: list[dict[str, Any]] = []

    def post(self, url, json=None, headers=None, **_kw):
        self.calls.append({"url": url, "json": json, "headers": headers})
        for path, resp in self.routes.items():
            if path in url:
                return resp
        raise AssertionError(f"no route configured for {url}")


def _token_ok_resp(token: str = "t_ok", expire: int = 7200) -> _FakeResp:
    return _FakeResp(
        body={
            "code": 0,
            "msg": "ok",
            "tenant_access_token": token,
            "expire": expire,
        }
    )


def _send_ok_resp() -> _FakeResp:
    return _FakeResp(body={"code": 0, "msg": "ok"})


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestConstruct:
    def test_requires_app_id(self):
        with pytest.raises(ValueError, match="app_id"):
            FeishuChannel(
                app_id="",
                app_secret="s",
                verification_token="v",
            )

    def test_requires_app_secret(self):
        with pytest.raises(ValueError, match="app_secret"):
            FeishuChannel(
                app_id="cli_x",
                app_secret="",
                verification_token="v",
            )

    def test_requires_verification_token(self):
        with pytest.raises(ValueError, match="verification_token"):
            FeishuChannel(
                app_id="cli_x",
                app_secret="s",
                verification_token="",
            )

    def test_default_channel_id(self):
        ch = FeishuChannel(
            app_id="cli_x",
            app_secret="s",
            verification_token="v",
        )
        assert ch.channel_id == "feishu"


# ═══════════════════════════════════════════════════════════
# URL verification handshake
# ═══════════════════════════════════════════════════════════


class TestURLVerification:
    def test_challenge_returned_when_token_matches(self):
        ch = FeishuChannel(
            app_id="a",
            app_secret="s",
            verification_token="TOKEN",
        )
        body = json.dumps(
            {
                "type": "url_verification",
                "challenge": "abc123",
                "token": "TOKEN",
            }
        ).encode("utf-8")
        result = ch.handle_webhook(body=body, headers={})
        assert result == {"challenge": "abc123"}

    def test_challenge_rejected_when_token_mismatches(self):
        ch = FeishuChannel(
            app_id="a",
            app_secret="s",
            verification_token="TOKEN",
        )
        body = json.dumps(
            {
                "type": "url_verification",
                "challenge": "abc",
                "token": "WRONG",
            }
        ).encode("utf-8")
        with pytest.raises(FeishuSignatureError, match="verification_token"):
            ch.handle_webhook(body=body, headers={})


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestEventHandling:
    def _mk_text_event_body(
        self,
        *,
        token: str = "TOKEN",
        chat_id: str = "oc_chat123",
        message_id: str = "om_msg456",
        text: str = "你好 agent",
        sender_type: str = "user",
    ) -> bytes:
        payload = {
            "schema": "2.0",
            "header": {
                "event_id": "evt_1",
                "token": token,
                "app_id": "cli_x",
                "tenant_key": "t1",
                "create_time": "1700000000000",
                "event_type": "im.message.receive_v1",
            },
            "event": {
                "sender": {
                    "sender_id": {
                        "open_id": "ou_user1",
                        "user_id": "u1",
                    },
                    "sender_type": sender_type,
                    "tenant_key": "t1",
                },
                "message": {
                    "message_id": message_id,
                    "root_id": message_id,
                    "parent_id": "",
                    "create_time": "1700000000500",
                    "chat_id": chat_id,
                    "chat_type": "p2p",
                    "message_type": "text",
                    "content": json.dumps({"text": text}, ensure_ascii=False),
                },
            },
        }
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def test_text_event_parsed(self):
        ch = FeishuChannel(
            app_id="a",
            app_secret="s",
            verification_token="TOKEN",
        )
        body = self._mk_text_event_body()
        msg = ch.handle_webhook(body=body, headers={})
        assert isinstance(msg, InboundMessage)
        assert msg.channel_id == "feishu"
        assert msg.content == "你好 agent"
        assert msg.thread_id == "oc_chat123:om_msg456"
        assert msg.sender_id == "ou_user1"
        assert msg.metadata["chat_id"] == "oc_chat123"
        assert msg.metadata["receive_id"] == "oc_chat123"

    def test_bad_token_raises(self):
        ch = FeishuChannel(
            app_id="a",
            app_secret="s",
            verification_token="RIGHT",
        )
        body = self._mk_text_event_body(token="WRONG")
        with pytest.raises(FeishuSignatureError):
            ch.handle_webhook(body=body, headers={})

    def test_bot_sender_filtered(self):
        """Implementation note."""
        ch = FeishuChannel(
            app_id="a",
            app_secret="s",
            verification_token="TOKEN",
        )
        body = self._mk_text_event_body(sender_type="app")
        result = ch.handle_webhook(body=body, headers={})
        assert result is None

    def test_image_message_returns_attachment(self):
        """Image messages now produce an InboundMessage with an
        Attachment carrying the image_key (used to be returned as
        None and dropped). Regression guard: keep image_key intact."""
        ch = FeishuChannel(
            app_id="a",
            app_secret="s",
            verification_token="TOKEN",
        )
        payload = json.loads(self._mk_text_event_body())
        payload["event"]["message"]["message_type"] = "image"
        payload["event"]["message"]["content"] = json.dumps({"image_key": "k"})
        body = json.dumps(payload).encode("utf-8")
        result = ch.handle_webhook(body=body, headers={})
        assert result is not None
        assert len(result.attachments) == 1
        att = result.attachments[0]
        assert att.content_type == "image"
        assert att.metadata.get("image_key") == "k"

    def test_wrong_event_type_ignored(self):
        ch = FeishuChannel(
            app_id="a",
            app_secret="s",
            verification_token="TOKEN",
        )
        payload = json.loads(self._mk_text_event_body())
        payload["header"]["event_type"] = "im.chat.member.bot.added_v1"
        body = json.dumps(payload).encode("utf-8")
        result = ch.handle_webhook(body=body, headers={})
        assert result is None

    def test_bad_json_body_raises(self):
        ch = FeishuChannel(
            app_id="a",
            app_secret="s",
            verification_token="T",
        )
        with pytest.raises(ValueError, match="bad json"):
            ch.handle_webhook(body=b"{not-json", headers={})


# ═══════════════════════════════════════════════════════════
# Tenant access token + send
# ═══════════════════════════════════════════════════════════


class TestSend:
    def test_send_acquires_token_then_posts(self):
        http = _RouteHttp(
            {
                "/auth/v3/tenant_access_token": _token_ok_resp("my-tat"),
                "/im/v1/messages": _send_ok_resp(),
            }
        )
        ch = FeishuChannel(
            app_id="cli_x",
            app_secret="s",
            verification_token="T",
            http_client=http,
        )
        ch.send(
            OutboundMessage(
                channel_id="feishu",
                thread_id="oc_chat:msg_root",
                content="回复内容",
                metadata={"receive_id": "oc_chat", "receive_id_type": "chat_id"},
            )
        )
        # Implementation note.
        assert len(http.calls) == 2
        assert "tenant_access_token" in http.calls[0]["url"]
        assert "/im/v1/messages" in http.calls[1]["url"]
        # Implementation note.
        assert http.calls[1]["headers"]["Authorization"] == "Bearer my-tat"
        # Implementation note.
        content_str = http.calls[1]["json"]["content"]
        assert json.loads(content_str)["text"] == "回复内容"

    def test_token_cached_between_sends(self):
        http = _RouteHttp(
            {
                "/auth/v3/tenant_access_token": _token_ok_resp("cached-tat", expire=7200),
                "/im/v1/messages": _send_ok_resp(),
            }
        )
        ch = FeishuChannel(
            app_id="a",
            app_secret="s",
            verification_token="T",
            http_client=http,
        )
        for _ in range(3):
            ch.send(
                OutboundMessage(
                    channel_id="feishu",
                    thread_id="c:m",
                    content="x",
                    metadata={"receive_id": "c"},
                )
            )
        # Implementation note.
        assert len(http.calls) == 4
        token_requests = [c for c in http.calls if "tenant_access_token" in c["url"]]
        assert len(token_requests) == 1

    def test_receive_id_from_metadata_preferred(self):
        http = _RouteHttp(
            {
                "/auth/v3/tenant_access_token": _token_ok_resp("x"),
                "/im/v1/messages": _send_ok_resp(),
            }
        )
        ch = FeishuChannel(
            app_id="a",
            app_secret="s",
            verification_token="T",
            http_client=http,
        )
        ch.send(
            OutboundMessage(
                channel_id="feishu",
                thread_id="fallback:x",
                content="y",
                metadata={"receive_id": "specific_chat"},
            )
        )
        body = http.calls[-1]["json"]
        assert body["receive_id"] == "specific_chat"

    def test_send_api_error_raises(self):
        http = _RouteHttp(
            {
                "/auth/v3/tenant_access_token": _token_ok_resp("x"),
                "/im/v1/messages": _FakeResp(
                    body={
                        "code": 230002,
                        "msg": "chat not found",
                    }
                ),
            }
        )
        ch = FeishuChannel(
            app_id="a",
            app_secret="s",
            verification_token="T",
            http_client=http,
        )
        with pytest.raises(FeishuError, match="230002"):
            ch.send(
                OutboundMessage(
                    channel_id="feishu",
                    thread_id="c:m",
                    content="x",
                    metadata={"receive_id": "c"},
                )
            )

    def test_token_fetch_api_error_raises(self):
        http = _RouteHttp(
            {
                "/auth/v3/tenant_access_token": _FakeResp(
                    body={
                        "code": 99991663,
                        "msg": "app_id invalid",
                    }
                ),
            }
        )
        ch = FeishuChannel(
            app_id="cli_bad",
            app_secret="s",
            verification_token="T",
            http_client=http,
        )
        with pytest.raises(FeishuError, match="refresh failed"):
            ch.send(
                OutboundMessage(
                    channel_id="feishu",
                    thread_id="c:m",
                    content="x",
                    metadata={"receive_id": "c"},
                )
            )

    def test_http_500_raises(self):
        http = _RouteHttp(
            {
                "/auth/v3/tenant_access_token": _FakeResp(
                    status_code=500,
                    text="upstream down",
                ),
            }
        )
        ch = FeishuChannel(
            app_id="a",
            app_secret="s",
            verification_token="T",
            http_client=http,
        )
        with pytest.raises(FeishuError, match="HTTP 500"):
            ch.send(
                OutboundMessage(
                    channel_id="feishu",
                    thread_id="c:m",
                    content="x",
                    metadata={"receive_id": "c"},
                )
            )


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestE2E:
    def test_inbound_produces_valid_message(self):
        ch = FeishuChannel(
            app_id="a",
            app_secret="s",
            verification_token="TOK",
        )
        body = json.dumps(
            {
                "schema": "2.0",
                "header": {
                    "token": "TOK",
                    "event_type": "im.message.receive_v1",
                },
                "event": {
                    "sender": {
                        "sender_id": {"open_id": "ou_1"},
                        "sender_type": "user",
                    },
                    "message": {
                        "message_id": "om_1",
                        "root_id": "om_1",
                        "chat_id": "oc_1",
                        "chat_type": "p2p",
                        "message_type": "text",
                        "content": json.dumps({"text": "hi agent"}),
                    },
                },
            }
        ).encode("utf-8")
        msg = ch.handle_webhook(body=body, headers={})
        assert isinstance(msg, InboundMessage)
        # Implementation note.
        assert msg.metadata["receive_id"] == "oc_1"
        assert msg.metadata["receive_id_type"] == "chat_id"
