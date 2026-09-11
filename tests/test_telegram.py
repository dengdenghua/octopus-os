"""Implementation note."""

from __future__ import annotations

import json
from typing import Any

import pytest

from runtime.adapters.channels import (
    InboundMessage,
    OutboundMessage,
    TelegramChannel,
    TelegramError,
    TelegramSecretMismatch,
)

# ═══════════════════════════════════════════════════════════
# HTTP mock
# ═══════════════════════════════════════════════════════════


class _FakeResp:
    def __init__(self, *, status_code=200, body=None, text=""):
        self.status_code = status_code
        self._body = body if body is not None else {"ok": True}
        self.text = text or json.dumps(self._body)

    def json(self):
        return self._body


class _FakeHttp:
    def __init__(self, resp: _FakeResp | None = None):
        self.resp = resp or _FakeResp()
        self.calls: list[dict[str, Any]] = []

    def post(self, url, json=None, headers=None, **_kw):
        self.calls.append({"url": url, "json": json, "headers": headers})
        return self.resp


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestConstruct:
    def test_bot_token_required(self):
        with pytest.raises(ValueError, match="bot_token"):
            TelegramChannel(bot_token="")

    def test_defaults(self):
        ch = TelegramChannel(bot_token="123:abc")
        assert ch.channel_id == "telegram"
        assert ch._webhook_secret == ""
        assert ch.api_base_url.startswith("https://api.telegram.org")


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestSecretVerification:
    def test_matching_secret_accepted(self):
        ch = TelegramChannel(
            bot_token="123:abc",
            webhook_secret="my-secret",
        )
        body = json.dumps({"update_id": 1}).encode("utf-8")
        result = ch.handle_webhook(
            body=body,
            headers={"X-Telegram-Bot-Api-Secret-Token": "my-secret"},
        )
        assert result is None  # Implementation note.

    def test_mismatching_secret_raises(self):
        ch = TelegramChannel(
            bot_token="123:abc",
            webhook_secret="my-secret",
        )
        body = json.dumps({"update_id": 1}).encode("utf-8")
        with pytest.raises(TelegramSecretMismatch):
            ch.handle_webhook(
                body=body,
                headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
            )

    def test_missing_secret_header_rejected(self):
        ch = TelegramChannel(
            bot_token="123:abc",
            webhook_secret="my-secret",
        )
        body = json.dumps({"update_id": 1}).encode("utf-8")
        with pytest.raises(TelegramSecretMismatch):
            ch.handle_webhook(body=body, headers={})

    def test_no_secret_configured_allows_any_headers(self):
        ch = TelegramChannel(bot_token="123:abc", webhook_secret="")
        body = json.dumps({"update_id": 1}).encode("utf-8")
        # Implementation note.
        assert ch.handle_webhook(body=body, headers={}) is None

    def test_secret_header_case_insensitive(self):
        ch = TelegramChannel(
            bot_token="123:abc",
            webhook_secret="s",
        )
        body = json.dumps({"update_id": 1}).encode("utf-8")
        # Implementation note.
        ch.handle_webhook(
            body=body,
            headers={"x-telegram-bot-api-secret-token": "s"},
        )


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


def _text_update(
    *,
    chat_id: int = 12345,
    message_id: int = 1,
    text: str = "hello bot",
    is_bot: bool = False,
    chat_type: str = "private",
) -> bytes:
    return json.dumps(
        {
            "update_id": 100,
            "message": {
                "message_id": message_id,
                "from": {
                    "id": 777,
                    "is_bot": is_bot,
                    "first_name": "Alice",
                    "username": "alice_tg",
                },
                "chat": {
                    "id": chat_id,
                    "type": chat_type,
                },
                "date": 1700000000,
                "text": text,
            },
        }
    ).encode("utf-8")


class TestMessageParse:
    def test_text_message_parsed(self):
        ch = TelegramChannel(bot_token="t")
        msg = ch.handle_webhook(body=_text_update(), headers={})
        assert isinstance(msg, InboundMessage)
        assert msg.channel_id == "telegram"
        assert msg.thread_id == "12345:1"
        assert msg.sender_id == "777"
        assert msg.content == "hello bot"
        assert msg.metadata["chat_id"] == 12345
        assert msg.metadata["reply_to_message_id"] == 1
        assert msg.metadata["sender_username"] == "alice_tg"
        assert msg.received_at is not None

    def test_bot_sender_filtered(self):
        ch = TelegramChannel(bot_token="t")
        result = ch.handle_webhook(
            body=_text_update(is_bot=True),
            headers={},
        )
        assert result is None

    def test_empty_text_ignored(self):
        ch = TelegramChannel(bot_token="t")
        result = ch.handle_webhook(
            body=_text_update(text="   "),
            headers={},
        )
        assert result is None

    def test_group_chat_parsed(self):
        ch = TelegramChannel(bot_token="t")
        msg = ch.handle_webhook(
            body=_text_update(chat_type="group"),
            headers={},
        )
        assert isinstance(msg, InboundMessage)
        assert msg.metadata["chat_type"] == "group"

    def test_callback_query_ignored(self):
        """Implementation note."""
        ch = TelegramChannel(bot_token="t")
        body = json.dumps(
            {
                "update_id": 5,
                "callback_query": {"data": "x"},
            }
        ).encode("utf-8")
        assert ch.handle_webhook(body=body, headers={}) is None

    def test_edited_message_ignored(self):
        """Implementation note."""
        ch = TelegramChannel(bot_token="t")
        body = json.dumps(
            {
                "update_id": 6,
                "edited_message": {"message_id": 1, "text": "edited"},
            }
        ).encode("utf-8")
        assert ch.handle_webhook(body=body, headers={}) is None

    def test_non_text_message_with_photo_returns_attachment(self):
        ch = TelegramChannel(bot_token="t")
        body = json.dumps(
            {
                "update_id": 7,
                "message": {
                    "message_id": 1,
                    "from": {"id": 1, "is_bot": False},
                    "chat": {"id": 1, "type": "private"},
                    "date": 1700000000,
                    "photo": [{"file_id": "x"}],
                },
            }
        ).encode("utf-8")
        result = ch.handle_webhook(body=body, headers={})
        assert result is not None
        assert len(result.attachments) == 1
        assert result.attachments[0].content_type == "image/jpeg"
        assert result.attachments[0].url == "x"

    def test_bad_json_raises(self):
        ch = TelegramChannel(bot_token="t")
        with pytest.raises(ValueError, match="bad json"):
            ch.handle_webhook(body=b"{not-json", headers={})


# ═══════════════════════════════════════════════════════════
# send
# ═══════════════════════════════════════════════════════════


class TestSend:
    def test_send_hits_bot_url(self):
        http = _FakeHttp()
        ch = TelegramChannel(bot_token="TOKEN:abc", http_client=http)
        ch.send(
            OutboundMessage(
                channel_id="telegram",
                thread_id="12345:1",
                content="reply text",
            )
        )
        assert len(http.calls) == 1
        url = http.calls[0]["url"]
        assert "/botTOKEN:abc/sendMessage" in url
        body = http.calls[0]["json"]
        assert str(body["chat_id"]) == "12345"
        assert body["text"] == "reply text"
        assert body["reply_to_message_id"] == 1

    def test_metadata_overrides_thread_id(self):
        http = _FakeHttp()
        ch = TelegramChannel(bot_token="t", http_client=http)
        ch.send(
            OutboundMessage(
                channel_id="telegram",
                thread_id="999:fallback",
                content="x",
                metadata={
                    "chat_id": 88,
                    "reply_to_message_id": 42,
                },
            )
        )
        body = http.calls[0]["json"]
        assert str(body["chat_id"]) == "88"
        assert body["reply_to_message_id"] == 42

    def test_send_without_reply_to(self):
        http = _FakeHttp()
        ch = TelegramChannel(bot_token="t", http_client=http)
        ch.send(
            OutboundMessage(
                channel_id="telegram",
                thread_id="100",  # Implementation note.
                content="x",
            )
        )
        body = http.calls[0]["json"]
        assert "reply_to_message_id" not in body

    def test_telegram_api_error_raises(self):
        http = _FakeHttp(
            _FakeResp(
                body={
                    "ok": False,
                    "description": "chat not found",
                }
            )
        )
        ch = TelegramChannel(bot_token="t", http_client=http)
        with pytest.raises(TelegramError, match="chat not found"):
            ch.send(
                OutboundMessage(
                    channel_id="telegram",
                    thread_id="c:1",
                    content="x",
                )
            )

    def test_http_error_raises(self):
        http = _FakeHttp(_FakeResp(status_code=500, text="upstream"))
        ch = TelegramChannel(bot_token="t", http_client=http)
        with pytest.raises(TelegramError, match="HTTP 500"):
            ch.send(
                OutboundMessage(
                    channel_id="telegram",
                    thread_id="c:1",
                    content="x",
                )
            )


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestE2E:
    def test_inbound_then_outbound_uses_same_chat(self):
        ch = TelegramChannel(bot_token="t", http_client=_FakeHttp())
        msg = ch.handle_webhook(body=_text_update(), headers={})
        assert isinstance(msg, InboundMessage)
        # Implementation note.
        out = OutboundMessage(
            channel_id="telegram",
            thread_id=msg.thread_id,
            content="thanks",
            metadata={
                "chat_id": msg.metadata["chat_id"],
                "reply_to_message_id": msg.metadata["reply_to_message_id"],
            },
        )
        ch.send(out)
        call_body = ch._http.calls[-1]["json"]
        # Implementation note.
        assert str(call_body["chat_id"]) == "12345"
        assert call_body["reply_to_message_id"] == 1
