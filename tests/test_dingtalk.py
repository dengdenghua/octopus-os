"""Implementation note."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.parse
from unittest.mock import MagicMock

import pytest

from runtime.adapters.channels import (
    DingTalkChannel,
    DingTalkError,
    DingTalkSignatureError,
    InboundMessage,
    OutboundMessage,
)

# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestConstruction:
    def test_requires_webhook_url(self):
        with pytest.raises(ValueError):
            DingTalkChannel(webhook_url="")

    def test_webhook_must_be_http(self):
        with pytest.raises(ValueError):
            DingTalkChannel(webhook_url="ftp://evil")

    def test_defaults(self):
        ch = DingTalkChannel(webhook_url="https://oapi.example/send")
        assert ch.channel_id == "dingtalk"
        assert ch._secret is None


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestSign:
    def test_sign_deterministic(self):
        ch = DingTalkChannel(
            webhook_url="https://x/y",
            secret="SECxxxxxxxxxxxxxxxxxxxxxxx",
        )
        ts = "1700000000000"
        s1 = ch.sign(ts)
        s2 = ch.sign(ts)
        assert s1 == s2
        assert s1  # Implementation note.

    def test_sign_matches_dingtalk_formula(self):
        """Implementation note."""
        secret = "MySecret"
        ts = "12345"
        expected_raw = hmac.new(
            secret.encode(),
            f"{ts}\n{secret}".encode(),
            hashlib.sha256,
        ).digest()
        expected_b64 = base64.b64encode(expected_raw)
        expected = urllib.parse.quote_plus(expected_b64)

        ch = DingTalkChannel(webhook_url="https://x/y", secret=secret)
        assert ch.sign(ts) == expected

    def test_sign_without_secret_raises(self):
        ch = DingTalkChannel(webhook_url="https://x/y")
        with pytest.raises(DingTalkError, match="secret"):
            ch.sign("12345")


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestVerifySignature:
    def _make(self, secret="MySecret"):
        return DingTalkChannel(webhook_url="https://x/y", secret=secret)

    def test_valid_signature(self):
        ch = self._make()
        ts = str(int(time.time() * 1000))
        sig = ch.sign(ts)
        # Implementation note.
        ch.verify_signature(body=b"{}", timestamp=ts, signature=sig)

    def test_rejects_tampered_signature(self):
        ch = self._make()
        ts = str(int(time.time() * 1000))
        with pytest.raises(DingTalkSignatureError, match="mismatch"):
            ch.verify_signature(body=b"{}", timestamp=ts, signature="notreal")

    def test_rejects_old_timestamp(self):
        ch = DingTalkChannel(
            webhook_url="https://x/y",
            secret="S",
            max_age_seconds=10,
        )
        old_ts = str(int((time.time() - 60) * 1000))
        sig = ch.sign(old_ts)
        with pytest.raises(DingTalkSignatureError, match="drift"):
            ch.verify_signature(body=b"{}", timestamp=old_ts, signature=sig)

    def test_missing_fields(self):
        ch = self._make()
        with pytest.raises(DingTalkSignatureError, match="timestamp"):
            ch.verify_signature(body=b"{}", timestamp="", signature="x")
        with pytest.raises(DingTalkSignatureError, match="signature"):
            ch.verify_signature(body=b"{}", timestamp="12345", signature="")

    def test_invalid_timestamp_format(self):
        ch = self._make()
        with pytest.raises(DingTalkSignatureError, match="invalid timestamp"):
            ch.verify_signature(body=b"{}", timestamp="abc", signature="x")

    def test_no_secret_loose_mode_allows(self):
        """Implementation note."""
        ch = DingTalkChannel(webhook_url="https://x/y", secret=None)
        ch.verify_signature(body=b"{}", timestamp="", signature="")

    def test_accepts_base64_without_urlencode(self):
        """Implementation note."""
        secret = "MySecret"
        ch = DingTalkChannel(webhook_url="https://x/y", secret=secret)
        ts = str(int(time.time() * 1000))
        raw_sig = base64.b64encode(
            hmac.new(
                secret.encode(),
                f"{ts}\n{secret}".encode(),
                hashlib.sha256,
            ).digest(),
        ).decode("utf-8")
        ch.verify_signature(body=b"{}", timestamp=ts, signature=raw_sig)


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestHandleWebhook:
    def _headers(self, ch: DingTalkChannel, ts: str) -> dict[str, str]:
        return {
            "timestamp": ts,
            "sign": ch.sign(ts),
        }

    def test_parses_text_message(self):
        ch = DingTalkChannel(webhook_url="https://x/y", secret="S")
        ts = str(int(time.time() * 1000))
        payload = {
            "msgtype": "text",
            "text": {"content": "hello"},
            "senderId": "user-1",
            "senderNick": "Alice",
            "conversationId": "chat-abc",
            "conversationTitle": "test-group",
            "conversationType": "2",
            "chatbotUserId": "bot",
            "createAt": int(time.time() * 1000),
        }
        result = ch.handle_webhook(
            body=json.dumps(payload).encode(),
            headers=self._headers(ch, ts),
        )
        assert isinstance(result, InboundMessage)
        assert result.content == "hello"
        assert result.thread_id == "chat-abc"
        assert result.sender_id == "user-1"
        assert result.channel_id == "dingtalk"
        assert result.metadata["sender_nick"] == "Alice"
        assert result.metadata["conversation_title"] == "test-group"
        assert result.received_at is not None

    def test_non_text_msgtype_returns_none(self):
        ch = DingTalkChannel(webhook_url="https://x/y", secret="S")
        ts = str(int(time.time() * 1000))
        payload = {"msgtype": "image", "text": {"content": "shouldnt"}}
        result = ch.handle_webhook(
            body=json.dumps(payload).encode(),
            headers=self._headers(ch, ts),
        )
        assert result is None

    def test_empty_content_returns_none(self):
        ch = DingTalkChannel(webhook_url="https://x/y", secret="S")
        ts = str(int(time.time() * 1000))
        payload = {"msgtype": "text", "text": {"content": "   "}}
        result = ch.handle_webhook(
            body=json.dumps(payload).encode(),
            headers=self._headers(ch, ts),
        )
        assert result is None

    def test_bad_json_raises(self):
        ch = DingTalkChannel(webhook_url="https://x/y", secret="S")
        ts = str(int(time.time() * 1000))
        with pytest.raises(DingTalkSignatureError, match="JSON"):
            ch.handle_webhook(
                body=b"not-json",
                headers=self._headers(ch, ts),
            )

    def test_signature_missing_rejected(self):
        ch = DingTalkChannel(webhook_url="https://x/y", secret="S")
        with pytest.raises(DingTalkSignatureError):
            ch.handle_webhook(
                body=b'{"msgtype":"text","text":{"content":"x"}}',
                headers={},
            )

    def test_loose_mode_no_signature_needed(self):
        """Implementation note."""
        ch = DingTalkChannel(webhook_url="https://x/y", secret=None)
        payload = {
            "msgtype": "text",
            "text": {"content": "hi"},
            "senderId": "u",
            "conversationId": "c",
        }
        result = ch.handle_webhook(
            body=json.dumps(payload).encode(),
            headers={},
        )
        assert isinstance(result, InboundMessage)
        assert result.content == "hi"


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestSend:
    def test_send_posts_to_webhook(self):
        mock_http = MagicMock()
        mock_http.post = MagicMock(
            return_value=MagicMock(
                status_code=200,
                text="{}",
                json=lambda: {"errcode": 0, "errmsg": "ok"},
            ),
        )
        ch = DingTalkChannel(
            webhook_url="https://oapi.example/send?access_token=T",
            secret="S",
            http_client=mock_http,
        )
        ch.send(
            OutboundMessage(
                channel_id="dingtalk",
                thread_id="c",
                content="reply",
            )
        )
        assert mock_http.post.called
        call = mock_http.post.call_args
        url = call.args[0]
        body = call.kwargs["json"]
        assert "access_token=T" in url
        assert "timestamp=" in url
        assert "sign=" in url
        assert body["msgtype"] == "text"
        assert body["text"]["content"] == "reply"
        assert len(ch.send_log) == 1

    def test_send_without_secret_no_sign(self):
        mock_http = MagicMock()
        mock_http.post = MagicMock(
            return_value=MagicMock(
                status_code=200,
                text="{}",
                json=lambda: {"errcode": 0},
            )
        )
        ch = DingTalkChannel(
            webhook_url="https://oapi.example/send?access_token=T",
            http_client=mock_http,
        )
        ch.send(
            OutboundMessage(
                channel_id="dingtalk",
                thread_id="c",
                content="hi",
            )
        )
        url = mock_http.post.call_args.args[0]
        assert "sign=" not in url

    def test_send_with_at_metadata(self):
        mock_http = MagicMock()
        mock_http.post = MagicMock(
            return_value=MagicMock(
                status_code=200,
                text="{}",
                json=lambda: {"errcode": 0},
            )
        )
        ch = DingTalkChannel(
            webhook_url="https://oapi.example/send",
            http_client=mock_http,
        )
        ch.send(
            OutboundMessage(
                channel_id="dingtalk",
                thread_id="c",
                content="hi",
                metadata={"at": {"atMobiles": ["13800000000"], "isAtAll": False}},
            )
        )
        body = mock_http.post.call_args.kwargs["json"]
        assert body["at"]["atMobiles"] == ["13800000000"]

    def test_empty_content_noop(self):
        mock_http = MagicMock()
        ch = DingTalkChannel(
            webhook_url="https://x/y",
            http_client=mock_http,
        )
        ch.send(OutboundMessage(channel_id="dingtalk", thread_id="c", content=""))
        assert not mock_http.post.called
        assert ch.send_log == []

    def test_http_error_raises(self):
        mock_http = MagicMock()
        mock_http.post = MagicMock(
            return_value=MagicMock(
                status_code=500,
                text="server down",
                json=lambda: {},
            )
        )
        ch = DingTalkChannel(webhook_url="https://x/y", http_client=mock_http)
        with pytest.raises(DingTalkError, match="HTTP 500"):
            ch.send(
                OutboundMessage(
                    channel_id="dingtalk",
                    thread_id="c",
                    content="hi",
                )
            )

    def test_dingtalk_errcode_raises(self):
        mock_http = MagicMock()
        mock_http.post = MagicMock(
            return_value=MagicMock(
                status_code=200,
                text="{}",
                json=lambda: {"errcode": 310000, "errmsg": "keyword not match"},
            )
        )
        ch = DingTalkChannel(webhook_url="https://x/y", http_client=mock_http)
        with pytest.raises(DingTalkError, match="errcode"):
            ch.send(
                OutboundMessage(
                    channel_id="dingtalk",
                    thread_id="c",
                    content="hi",
                )
            )


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestLifecycle:
    def test_start_stop_are_noop(self):
        ch = DingTalkChannel(webhook_url="https://x/y")
        ch.start()
        ch.stop()
        ch.start()  # Implementation note.
        ch.stop()
