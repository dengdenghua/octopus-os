"""Tests for the 18 new channel adapters."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from unittest.mock import MagicMock, patch

import pytest
from runtime.adapters.channels import (
    Attachment,
    BlueBubblesChannel,
    BlueBubblesError,
    Channel,
    ChannelManager,
    EmailChannel,
    EmailError,
    GoogleChatChannel,
    HomeAssistantChannel,
    HomeAssistantError,
    LineChannel,
    LineError,
    LineSignatureError,
    MatrixChannel,
    MatrixError,
    MatrixSignatureError,
    MattermostChannel,
    MattermostError,
    MattermostSignatureError,
    NtfyChannel,
    NtfyError,
    OpenWebUIChannel,
    OpenWebUIError,
    OutboundMessage,
    QQBotChannel,
    QQBotError,
    SignalChannel,
    SignalError,
    SignalSignatureError,
    SimpleXChannel,
    SimpleXError,
    SmsChannel,
    SmsError,
    SmsSignatureError,
    TeamsChannel,
    WebhooksChannel,
    WebhooksSignatureError,
    WeComChannel,
    WhatsAppChannel,
    WhatsAppError,
    WhatsAppSignatureError,
    YuanbaoChannel,
    YuanbaoError,
    YuanbaoSignatureError,
)


class _FakeHttpResp:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body if body is not None else {"ok": True}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


class _FakeHttpClient:
    def __init__(self, resp=None):
        self.resp = resp or _FakeHttpResp()
        self.calls: list[dict] = []

    def post(
        self, url, json=None, headers=None, data=None, content=None, params=None, files=None, **_kw
    ):
        self.calls.append(
            {
                "method": "post",
                "url": url,
                "json": json,
                "headers": headers,
                "data": data,
                "content": content,
                "params": params,
                "files": files,
            }
        )
        return self.resp

    def put(self, url, json=None, headers=None, **_kw):
        self.calls.append(
            {
                "method": "put",
                "url": url,
                "json": json,
                "headers": headers,
            }
        )
        return self.resp

    def patch(self, url, json=None, headers=None, **_kw):
        self.calls.append(
            {
                "method": "patch",
                "url": url,
                "json": json,
                "headers": headers,
            }
        )
        return self.resp

    def get(self, url, headers=None, **_kw):
        self.calls.append(
            {
                "method": "get",
                "url": url,
                "headers": headers,
            }
        )
        return self.resp


def _signal_sig(secret: str, body: bytes) -> str:
    return secret


def _whatsapp_sig(secret: str, body: bytes) -> str:
    mac = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={mac}"


def _sms_sig(auth_token: str, url: str, body: bytes) -> str:
    from urllib.parse import parse_qs

    params = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    sorted_params = sorted(params.items())
    data = url
    for key, values in sorted_params:
        for value in values:
            data += key + value
    mac = hmac.new(
        auth_token.encode("utf-8"),
        data.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return base64.b64encode(mac).decode("utf-8")


def _line_sig(secret: str, body: bytes) -> str:
    mac = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(mac).decode("utf-8")


def _webhooks_sig(secret: str, data: bytes) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        data,
        hashlib.sha256,
    ).hexdigest()


class _FakeChannel(Channel):
    def __init__(self, channel_id: str = "fake"):
        self.channel_id = channel_id
        self.sent: list[OutboundMessage] = []

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def send(self, msg: OutboundMessage) -> None:
        self.sent.append(msg)


# ═══════════════════════════════════════════════════════════
# SignalChannel
# ═══════════════════════════════════════════════════════════


class TestSignalChannel:
    def test_constructor(self):
        http = _FakeHttpClient()
        ch = SignalChannel(phone_number="+1234", http_client=http)
        assert ch.channel_id == "signal"
        assert ch.api_base_url == "http://localhost:8080"

    def test_constructor_requires_phone_number(self):
        with pytest.raises(ValueError, match="phone_number"):
            SignalChannel(phone_number="")

    def test_send_posts_to_v2_send(self):
        http = _FakeHttpClient()
        ch = SignalChannel(phone_number="+1234", http_client=http)
        ch.send(
            OutboundMessage(
                channel_id="signal",
                thread_id="+5678:1",
                content="hi",
            )
        )
        assert len(http.calls) == 1
        call = http.calls[0]
        assert call["url"].endswith("/v2/send")
        assert call["json"]["message"] == "hi"
        assert call["json"]["number"] == "+1234"
        assert "+5678" in call["json"]["recipients"]

    def test_send_http_error_raises(self):
        http = _FakeHttpClient(_FakeHttpResp(status_code=500, body={"error": "x"}))
        ch = SignalChannel(phone_number="+1234", http_client=http)
        with pytest.raises(SignalError, match="HTTP 500"):
            ch.send(
                OutboundMessage(
                    channel_id="signal",
                    thread_id="+5678:1",
                    content="hi",
                )
            )

    def test_handle_webhook_parses_envelope(self):
        ch = SignalChannel(phone_number="+1234", webhook_secret="s3cret")
        payload = json.dumps(
            {
                "envelope": {
                    "source": "+5678",
                    "sourceNumber": "+5678",
                    "sourceName": "Alice",
                    "dataMessage": {
                        "message": "hello agent",
                        "timestamp": 1700000000000,
                    },
                },
            }
        ).encode()
        headers = {"X-Signal-Secret": "s3cret"}
        msg = ch.handle_webhook(body=payload, headers=headers)
        assert msg is not None
        assert msg.channel_id == "signal"
        assert msg.content == "hello agent"
        assert msg.sender_id == "+5678"
        assert msg.metadata["platform"] == "signal"

    def test_handle_webhook_bad_secret(self):
        ch = SignalChannel(phone_number="+1234", webhook_secret="s3cret")
        payload = json.dumps(
            {
                "envelope": {
                    "source": "+5678",
                    "dataMessage": {"message": "hi", "timestamp": 1},
                },
            }
        ).encode()
        headers = {"X-Signal-Secret": "wrong"}
        with pytest.raises(SignalSignatureError, match="mismatch"):
            ch.handle_webhook(body=payload, headers=headers)

    def test_handle_webhook_no_envelope(self):
        ch = SignalChannel(phone_number="+1234")
        payload = json.dumps({"something": "else"}).encode()
        msg = ch.handle_webhook(body=payload, headers={})
        assert msg is None

    def test_supports_edit_false(self):
        ch = SignalChannel(phone_number="+1234")
        assert ch.supports_edit is False


# ═══════════════════════════════════════════════════════════
# WhatsAppChannel
# ═══════════════════════════════════════════════════════════


class TestWhatsAppChannel:
    def test_constructor(self):
        http = _FakeHttpClient()
        ch = WhatsAppChannel(
            phone_number_id="pid",
            access_token="at",
            http_client=http,
        )
        assert ch.channel_id == "whatsapp"

    def test_constructor_requires_phone_number_id(self):
        with pytest.raises(ValueError, match="phone_number_id"):
            WhatsAppChannel(phone_number_id="", access_token="at")

    def test_constructor_requires_access_token(self):
        with pytest.raises(ValueError, match="access_token"):
            WhatsAppChannel(phone_number_id="pid", access_token="")

    def test_send_posts_to_messages(self):
        http = _FakeHttpClient()
        ch = WhatsAppChannel(
            phone_number_id="pid123",
            access_token="at",
            http_client=http,
        )
        ch.send(
            OutboundMessage(
                channel_id="whatsapp",
                thread_id="+5678:msg1",
                content="hi",
            )
        )
        assert len(http.calls) == 1
        call = http.calls[0]
        assert "/pid123/messages" in call["url"]
        assert call["json"]["messaging_product"] == "whatsapp"
        assert call["json"]["type"] == "text"
        assert call["json"]["text"]["body"] == "hi"
        assert call["headers"]["Authorization"] == "Bearer at"

    def test_send_api_error_raises(self):
        http = _FakeHttpClient(
            _FakeHttpResp(
                body={
                    "error": {"message": "bad request"},
                }
            )
        )
        ch = WhatsAppChannel(
            phone_number_id="pid",
            access_token="at",
            http_client=http,
        )
        with pytest.raises(WhatsAppError, match="bad request"):
            ch.send(
                OutboundMessage(
                    channel_id="whatsapp",
                    thread_id="+5678:msg1",
                    content="hi",
                )
            )

    def test_handle_webhook_verifies_signature(self):
        secret = "app_secret_123"
        ch = WhatsAppChannel(
            phone_number_id="pid",
            access_token="at",
            app_secret=secret,
        )
        payload_dict = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "type": "text",
                                        "text": {"body": "hello"},
                                        "from": "+5678",
                                        "id": "wamid1",
                                        "timestamp": "1700000000",
                                    }
                                ],
                                "contacts": [{"wa_id": "+5678"}],
                            },
                        }
                    ],
                }
            ],
        }
        body = json.dumps(payload_dict).encode()
        sig = _whatsapp_sig(secret, body)
        msg = ch.handle_webhook(body=body, headers={"X-Hub-Signature-256": sig})
        assert msg is not None
        assert msg.content == "hello"
        assert msg.metadata["platform"] == "whatsapp"

    def test_handle_webhook_bad_signature(self):
        ch = WhatsAppChannel(
            phone_number_id="pid",
            access_token="at",
            app_secret="secret",
        )
        body = json.dumps({"object": "whatsapp_business_account"}).encode()
        with pytest.raises(WhatsAppSignatureError, match="mismatch"):
            ch.handle_webhook(
                body=body,
                headers={"X-Hub-Signature-256": "sha256=bad"},
            )

    def test_verify_webhook_get(self):
        ch = WhatsAppChannel(
            phone_number_id="pid",
            access_token="at",
            verify_token="vtok",
        )
        result = ch.verify_webhook(
            mode="subscribe",
            token="vtok",
            challenge="ch123",
        )
        assert result == "ch123"

    def test_verify_webhook_bad_mode(self):
        ch = WhatsAppChannel(
            phone_number_id="pid",
            access_token="at",
            verify_token="vtok",
        )
        with pytest.raises(WhatsAppSignatureError, match="hub.mode"):
            ch.verify_webhook(mode="unsubscribe", token="vtok", challenge="c")

    def test_verify_webhook_bad_token(self):
        ch = WhatsAppChannel(
            phone_number_id="pid",
            access_token="at",
            verify_token="vtok",
        )
        with pytest.raises(WhatsAppSignatureError, match="mismatch"):
            ch.verify_webhook(mode="subscribe", token="wrong", challenge="c")


# ═══════════════════════════════════════════════════════════
# EmailChannel
# ═══════════════════════════════════════════════════════════


class TestEmailChannel:
    def test_constructor(self):
        ch = EmailChannel(
            smtp_host="smtp.example.com",
            imap_host="imap.example.com",
            username="u",
            password="p",
            from_address="f@a.com",
        )
        assert ch.channel_id == "email"

    def test_constructor_requires_smtp_host(self):
        with pytest.raises(ValueError, match="smtp_host"):
            EmailChannel(
                smtp_host="",
                imap_host="imap",
                username="u",
                password="p",
                from_address="f@a.com",
            )

    def test_constructor_requires_imap_host(self):
        with pytest.raises(ValueError, match="imap_host"):
            EmailChannel(
                smtp_host="smtp",
                imap_host="",
                username="u",
                password="p",
                from_address="f@a.com",
            )

    def test_constructor_requires_username(self):
        with pytest.raises(ValueError, match="username"):
            EmailChannel(
                smtp_host="smtp",
                imap_host="imap",
                username="",
                password="p",
                from_address="f@a.com",
            )

    def test_constructor_requires_password(self):
        with pytest.raises(ValueError, match="password"):
            EmailChannel(
                smtp_host="smtp",
                imap_host="imap",
                username="u",
                password="",
                from_address="f@a.com",
            )

    def test_constructor_requires_from_address(self):
        with pytest.raises(ValueError, match="from_address"):
            EmailChannel(
                smtp_host="smtp",
                imap_host="imap",
                username="u",
                password="p",
                from_address="",
            )

    @patch("runtime.adapters.channels.email.smtplib.SMTP")
    def test_send_uses_smtp(self, mock_smtp_cls):
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
        ch = EmailChannel(
            smtp_host="smtp.example.com",
            imap_host="imap.example.com",
            username="u",
            password="p",
            from_address="f@a.com",
        )
        ch.send(
            OutboundMessage(
                channel_id="email",
                thread_id="<msg123@a.com>",
                content="hello",
                metadata={"recipient": "to@b.com", "subject": "Re: test"},
            )
        )
        mock_smtp_cls.assert_called_once_with("smtp.example.com", 587)
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once_with("u", "p")
        mock_server.send_message.assert_called_once()

    @patch("runtime.adapters.channels.email.smtplib.SMTP")
    def test_send_smtp_error_raises(self, mock_smtp_cls):
        mock_smtp_cls.return_value.__enter__ = MagicMock(
            side_effect=Exception("connection refused"),
        )
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
        ch = EmailChannel(
            smtp_host="smtp",
            imap_host="imap",
            username="u",
            password="p",
            from_address="f@a.com",
        )
        with pytest.raises(EmailError, match="smtp send failed"):
            ch.send(
                OutboundMessage(
                    channel_id="email",
                    thread_id="t",
                    content="hi",
                )
            )

    @patch("runtime.adapters.channels.email.imaplib.IMAP4_SSL")
    def test_poll_uses_imap(self, mock_imap_cls):
        mock_imap = MagicMock()
        mock_imap_cls.return_value = mock_imap
        raw_email = (
            b"From: sender@example.com\r\n"
            b"Message-ID: <msg123@example.com>\r\n"
            b"Subject: Test\r\n"
            b"Date: Sun, 01 Jan 2023 00:00:00 +0000\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n"
            b"\r\n"
            b"Hello world"
        )
        mock_imap.search.return_value = ("OK", [b"1"])
        mock_imap.fetch.return_value = ("OK", [(None, raw_email)])
        ch = EmailChannel(
            smtp_host="smtp",
            imap_host="imap",
            username="u",
            password="p",
            from_address="f@a.com",
        )
        ch._imap = mock_imap
        msgs = ch.poll()
        mock_imap.select.assert_called_once_with("INBOX")
        mock_imap.search.assert_called_once_with(None, "UNSEEN")
        assert len(msgs) == 1
        assert msgs[0].content == "Hello world"
        assert msgs[0].sender_id == "sender@example.com"
        assert msgs[0].metadata["platform"] == "email"


# ═══════════════════════════════════════════════════════════
# SmsChannel
# ═══════════════════════════════════════════════════════════


class TestSmsChannel:
    def test_constructor(self):
        http = _FakeHttpClient()
        ch = SmsChannel(
            account_sid="sid",
            auth_token="tok",
            from_number="+1234",
            http_client=http,
        )
        assert ch.channel_id == "sms"

    def test_constructor_requires_account_sid(self):
        with pytest.raises(ValueError, match="account_sid"):
            SmsChannel(account_sid="", auth_token="t", from_number="+1")

    def test_constructor_requires_auth_token(self):
        with pytest.raises(ValueError, match="auth_token"):
            SmsChannel(account_sid="s", auth_token="", from_number="+1")

    def test_constructor_requires_from_number(self):
        with pytest.raises(ValueError, match="from_number"):
            SmsChannel(account_sid="s", auth_token="t", from_number="")

    def test_send_posts_form_data(self):
        http = _FakeHttpClient()
        ch = SmsChannel(
            account_sid="sid",
            auth_token="tok",
            from_number="+1234",
            http_client=http,
        )
        ch.send(
            OutboundMessage(
                channel_id="sms",
                thread_id="+5678:SM123",
                content="hi",
            )
        )
        assert len(http.calls) == 1
        call = http.calls[0]
        assert "sid/Messages.json" in call["url"]
        assert call["data"] is not None
        assert call["data"]["From"] == "+1234"
        assert call["data"]["To"] == "+5678"
        assert call["data"]["Body"] == "hi"

    def test_send_http_error_raises(self):
        http = _FakeHttpClient(_FakeHttpResp(status_code=500, body={}))
        ch = SmsChannel(
            account_sid="sid",
            auth_token="tok",
            from_number="+1234",
            http_client=http,
        )
        with pytest.raises(SmsError, match="HTTP 500"):
            ch.send(
                OutboundMessage(
                    channel_id="sms",
                    thread_id="+5678:SM123",
                    content="hi",
                )
            )

    def test_handle_webhook_verifies_signature(self):
        webhook_url = "https://example.com/webhook"
        ch = SmsChannel(
            account_sid="sid",
            auth_token="tok123",
            from_number="+1234",
            webhook_url=webhook_url,
        )
        body_str = "Body=hello&From=%2B5678&MessageSid=SM123&MessageDate=2024-01-01T00%3A00%3A00"
        body = body_str.encode("utf-8")
        sig = _sms_sig("tok123", webhook_url, body)
        msg = ch.handle_webhook(
            body=body,
            headers={"X-Twilio-Signature": sig},
        )
        assert msg is not None
        assert msg.content == "hello"
        assert msg.metadata["platform"] == "sms"

    def test_handle_webhook_bad_signature(self):
        ch = SmsChannel(
            account_sid="sid",
            auth_token="tok",
            from_number="+1234",
            webhook_url="https://example.com/webhook",
        )
        body = b"Body=hello&From=%2B5678&MessageSid=SM123"
        with pytest.raises(SmsSignatureError, match="mismatch"):
            ch.handle_webhook(
                body=body,
                headers={"X-Twilio-Signature": "bad_sig"},
            )


# ═══════════════════════════════════════════════════════════
# MattermostChannel
# ═══════════════════════════════════════════════════════════


class TestMattermostChannel:
    def test_constructor(self):
        http = _FakeHttpClient()
        ch = MattermostChannel(
            bot_token="bt",
            server_url="https://mm.example.com",
            http_client=http,
        )
        assert ch.channel_id == "mattermost"
        assert ch.supports_edit is True
        assert ch.supports_typing is True
        assert ch.supports_reactions is True

    def test_constructor_requires_bot_token(self):
        with pytest.raises(ValueError, match="bot_token"):
            MattermostChannel(bot_token="", server_url="https://mm.example.com")

    def test_constructor_requires_server_url(self):
        with pytest.raises(ValueError, match="server_url"):
            MattermostChannel(bot_token="bt", server_url="")

    def test_send_posts_to_api_v4_posts(self):
        http = _FakeHttpClient()
        ch = MattermostChannel(
            bot_token="bt",
            server_url="https://mm.example.com",
            http_client=http,
        )
        ch.send(
            OutboundMessage(
                channel_id="mattermost",
                thread_id="ch1:post1",
                content="hi",
            )
        )
        assert len(http.calls) == 1
        call = http.calls[0]
        assert call["url"] == "https://mm.example.com/api/v4/posts"
        assert call["json"]["channel_id"] == "ch1"
        assert call["json"]["message"] == "hi"
        assert call["json"]["root_id"] == "post1"
        assert call["headers"]["Authorization"] == "Bearer bt"

    def test_send_http_error_raises(self):
        http = _FakeHttpClient(_FakeHttpResp(status_code=403, body={}))
        ch = MattermostChannel(
            bot_token="bt",
            server_url="https://mm.example.com",
            http_client=http,
        )
        with pytest.raises(MattermostError, match="HTTP 403"):
            ch.send(
                OutboundMessage(
                    channel_id="mattermost",
                    thread_id="ch1:p1",
                    content="hi",
                )
            )

    def test_handle_webhook_verifies_token(self):
        ch = MattermostChannel(
            bot_token="bt",
            server_url="https://mm.example.com",
        )
        payload = json.dumps(
            {
                "token": "bt",
                "text": "hello",
                "user_id": "u1",
                "channel_id": "ch1",
                "post_id": "p1",
            }
        ).encode()
        msg = ch.handle_webhook(body=payload, headers={})
        assert msg is not None
        assert msg.content == "hello"
        assert msg.metadata["platform"] == "mattermost"

    def test_handle_webhook_bad_token(self):
        ch = MattermostChannel(
            bot_token="bt",
            server_url="https://mm.example.com",
        )
        payload = json.dumps(
            {
                "token": "wrong",
                "text": "hi",
                "channel_id": "ch1",
            }
        ).encode()
        with pytest.raises(MattermostSignatureError, match="mismatch"):
            ch.handle_webhook(body=payload, headers={})

    def test_edit_posts_to_api_v4_posts_id(self):
        http = _FakeHttpClient()
        ch = MattermostChannel(
            bot_token="bt",
            server_url="https://mm.example.com",
            http_client=http,
        )
        msg = OutboundMessage(
            channel_id="mattermost",
            thread_id="ch1:p1",
            content="edited",
        )
        ch.edit(msg, "post123")
        assert len(http.calls) == 1
        call = http.calls[0]
        assert call["url"] == "https://mm.example.com/api/v4/posts/post123"
        assert call["json"]["message"] == "edited"

    def test_send_typing_is_noop(self):
        ch = MattermostChannel(
            bot_token="bt",
            server_url="https://mm.example.com",
        )
        ch.send_typing("ch1:p1")

    def test_add_reaction_posts_to_reactions(self):
        http = _FakeHttpClient()
        ch = MattermostChannel(
            bot_token="bt",
            server_url="https://mm.example.com",
            http_client=http,
        )
        ch.add_reaction("ch1:p1", "post1", "+1")
        assert len(http.calls) == 1
        call = http.calls[0]
        assert call["url"] == "https://mm.example.com/api/v4/reactions"
        assert call["json"]["post_id"] == "post1"
        assert call["json"]["emoji_name"] == "+1"


# ═══════════════════════════════════════════════════════════
# MatrixChannel
# ═══════════════════════════════════════════════════════════


class TestMatrixChannel:
    def test_constructor(self):
        http = _FakeHttpClient()
        ch = MatrixChannel(
            homeserver_url="https://matrix.org",
            access_token="at",
            http_client=http,
        )
        assert ch.channel_id == "matrix"
        assert ch.supports_edit is True
        assert ch.supports_typing is True
        assert ch.supports_reactions is True

    def test_constructor_requires_homeserver_url(self):
        with pytest.raises(ValueError, match="homeserver_url"):
            MatrixChannel(homeserver_url="", access_token="at")

    def test_constructor_requires_access_token(self):
        with pytest.raises(ValueError, match="access_token"):
            MatrixChannel(homeserver_url="https://matrix.org", access_token="")

    def test_send_puts_to_rooms_send(self):
        http = _FakeHttpClient()
        ch = MatrixChannel(
            homeserver_url="https://matrix.org",
            access_token="at",
            http_client=http,
        )
        ch.send(
            OutboundMessage(
                channel_id="matrix",
                thread_id="!room1:event1",
                content="hi",
            )
        )
        assert len(http.calls) == 1
        call = http.calls[0]
        assert call["method"] == "put"
        assert "/_matrix/client/v3/rooms/!room1/send/m.room.message/" in call["url"]
        assert call["json"]["msgtype"] == "m.text"
        assert call["json"]["body"] == "hi"
        assert call["headers"]["Authorization"] == "Bearer at"

    def test_send_http_error_raises(self):
        http = _FakeHttpClient(_FakeHttpResp(status_code=403, body={}))
        ch = MatrixChannel(
            homeserver_url="https://matrix.org",
            access_token="at",
            http_client=http,
        )
        with pytest.raises(MatrixError, match="HTTP 403"):
            ch.send(
                OutboundMessage(
                    channel_id="matrix",
                    thread_id="!room1:e1",
                    content="hi",
                )
            )

    def test_handle_webhook_verifies_as_token(self):
        ch = MatrixChannel(
            homeserver_url="https://matrix.org",
            access_token="at",
        )
        payload = json.dumps(
            {
                "as_token": "at",
                "events": [
                    {
                        "type": "m.room.message",
                        "content": {"msgtype": "m.text", "body": "hello"},
                        "sender": "@alice:matrix.org",
                        "room_id": "!room1:matrix.org",
                        "event_id": "$event1",
                        "origin_server_ts": 1700000000000,
                    }
                ],
            }
        ).encode()
        msg = ch.handle_webhook(body=payload, headers={})
        assert msg is not None
        assert msg.content == "hello"
        assert msg.sender_id == "@alice:matrix.org"
        assert msg.metadata["platform"] == "matrix"

    def test_handle_webhook_bad_as_token(self):
        ch = MatrixChannel(
            homeserver_url="https://matrix.org",
            access_token="at",
        )
        payload = json.dumps(
            {
                "as_token": "wrong",
                "events": [],
            }
        ).encode()
        with pytest.raises(MatrixSignatureError, match="mismatch"):
            ch.handle_webhook(body=payload, headers={})

    def test_edit_puts_to_rooms_messages(self):
        http = _FakeHttpClient()
        ch = MatrixChannel(
            homeserver_url="https://matrix.org",
            access_token="at",
            http_client=http,
        )
        msg = OutboundMessage(
            channel_id="matrix",
            thread_id="!room1:e1",
            content="edited",
        )
        ch.edit(msg, "!room1:$event1")
        assert len(http.calls) == 1
        call = http.calls[0]
        assert call["method"] == "put"
        assert "/_matrix/client/v3/rooms/!room1/messages/$event1" in call["url"]
        assert call["json"]["body"] == "edited"

    def test_send_typing_puts_typing(self):
        http = _FakeHttpClient()
        ch = MatrixChannel(
            homeserver_url="https://matrix.org",
            access_token="at",
            http_client=http,
        )
        ch._user_id = "@bot:matrix.org"
        ch.send_typing("!room1:e1")
        assert len(http.calls) == 1
        call = http.calls[0]
        assert call["method"] == "put"
        assert "/typing/@bot:matrix.org" in call["url"]
        assert call["json"]["typing"] is True

    def test_add_reaction_puts_reaction(self):
        http = _FakeHttpClient()
        ch = MatrixChannel(
            homeserver_url="https://matrix.org",
            access_token="at",
            http_client=http,
        )
        ch.add_reaction("!room1:e1", "$event1", "👍")
        assert len(http.calls) == 1
        call = http.calls[0]
        assert call["method"] == "put"
        assert "/send/m.reaction/" in call["url"]
        assert call["json"]["m.relates_to"]["key"] == "👍"
        assert call["json"]["m.relates_to"]["event_id"] == "$event1"


# ═══════════════════════════════════════════════════════════
# WeComChannel
# ═══════════════════════════════════════════════════════════


class TestWeComChannel:
    def test_constructor(self):
        ch = WeComChannel(
            corp_id="c1",
            agent_id="a1",
            secret="s1",
            token="t1",
            encoding_aes_key="key123",
        )
        assert ch.channel_id == "wecom"
        assert ch.supports_edit is True

    def test_constructor_requires_corp_id(self):
        with pytest.raises(ValueError, match="corp_id"):
            WeComChannel(
                corp_id="",
                agent_id="a",
                secret="s",
                token="t",
                encoding_aes_key="k",
            )

    def test_constructor_requires_agent_id(self):
        with pytest.raises(ValueError, match="agent_id"):
            WeComChannel(
                corp_id="c",
                agent_id="",
                secret="s",
                token="t",
                encoding_aes_key="k",
            )

    def test_constructor_requires_secret(self):
        with pytest.raises(ValueError, match="secret"):
            WeComChannel(
                corp_id="c",
                agent_id="a",
                secret="",
                token="t",
                encoding_aes_key="k",
            )

    def test_constructor_requires_token(self):
        with pytest.raises(ValueError, match="token"):
            WeComChannel(
                corp_id="c",
                agent_id="a",
                secret="s",
                token="",
                encoding_aes_key="k",
            )

    def test_constructor_requires_encoding_aes_key(self):
        with pytest.raises(ValueError, match="encoding_aes_key"):
            WeComChannel(
                corp_id="c",
                agent_id="a",
                secret="s",
                token="t",
                encoding_aes_key="",
            )

    def test_handle_webhook_xml_parsing(self):
        ch = WeComChannel(
            corp_id="c1",
            agent_id="a1",
            secret="s1",
            token="t1",
            encoding_aes_key="key123",
        )
        decrypted_xml = (
            "<xml>"
            "<Content>hello agent</Content>"
            "<FromUserName>user1</FromUserName>"
            "<MsgId>12345</MsgId>"
            "<CreateTime>1700000000</CreateTime>"
            "<MsgType>text</MsgType>"
            "</xml>"
        )
        with (
            patch.object(ch, "_verify_signature"),
            patch.object(ch, "_decrypt_message", return_value=decrypted_xml),
        ):
            body = (
                b"<xml>"
                b"<MsgSignature>sig</MsgSignature>"
                b"<TimeStamp>1700000000</TimeStamp>"
                b"<Nonce>nonce</Nonce>"
                b"<Encrypt>encrypted</Encrypt>"
                b"</xml>"
            )
            msg = ch.handle_webhook(body=body, headers={})
            assert msg is not None
            assert msg.channel_id == "wecom"
            assert msg.content == "hello agent"
            assert msg.sender_id == "user1"
            assert msg.metadata["platform"] == "wecom"

    def test_handle_webhook_no_encrypt(self):
        ch = WeComChannel(
            corp_id="c1",
            agent_id="a1",
            secret="s1",
            token="t1",
            encoding_aes_key="key123",
        )
        body = b"<xml><MsgSignature>s</MsgSignature><TimeStamp>t</TimeStamp><Nonce>n</Nonce></xml>"
        msg = ch.handle_webhook(body=body, headers={})
        assert msg is None

    def test_edit_posts_to_message_update(self):
        http = _FakeHttpClient()
        ch = WeComChannel(
            corp_id="c1",
            agent_id="a1",
            secret="s1",
            token="t1",
            encoding_aes_key="key123",
            http_client=http,
        )
        ch._access_token = "fake_token"
        ch._access_token_expires_at = time.time() + 7200
        msg = OutboundMessage(
            channel_id="wecom",
            thread_id="user1:msg1",
            content="edited",
            metadata={"touser": "user1"},
        )
        ch.edit(msg, "orig_msg_id")
        assert len(http.calls) == 1
        call = http.calls[0]
        assert "/message/update" in call["url"]
        assert call["json"]["message_id"] == "orig_msg_id"
        assert call["json"]["text"]["content"] == "edited"


# ═══════════════════════════════════════════════════════════
# QQBotChannel
# ═══════════════════════════════════════════════════════════


class TestQQBotChannel:
    def test_constructor(self):
        http = _FakeHttpClient()
        ch = QQBotChannel(
            app_id="aid",
            app_secret="asec",
            http_client=http,
        )
        assert ch.channel_id == "qqbot"
        assert ch.supports_edit is True

    def test_constructor_requires_app_id(self):
        with pytest.raises(ValueError, match="app_id"):
            QQBotChannel(app_id="", app_secret="s")

    def test_constructor_requires_app_secret(self):
        with pytest.raises(ValueError, match="app_secret"):
            QQBotChannel(app_id="a", app_secret="")

    def test_token_caching(self):
        http = _FakeHttpClient(
            _FakeHttpResp(
                body={
                    "access_token": "tok123",
                    "expires_in": 7200,
                }
            )
        )
        ch = QQBotChannel(
            app_id="aid",
            app_secret="asec",
            http_client=http,
        )
        token1 = ch._ensure_token()
        assert token1 == "tok123"
        assert len(http.calls) == 1
        token2 = ch._ensure_token()
        assert token2 == "tok123"
        assert len(http.calls) == 1

    def test_send_posts_to_channels_messages(self):
        http = _FakeHttpClient(_FakeHttpResp(body={"id": "msg1"}))
        ch = QQBotChannel(
            app_id="aid",
            app_secret="asec",
            http_client=http,
        )
        ch._access_token = "tok"
        ch._token_expires_at = time.time() + 3600
        ch.send(
            OutboundMessage(
                channel_id="qqbot",
                thread_id="ch1",
                content="hi",
                metadata={"qq_channel_id": "ch1"},
            )
        )
        send_calls = [c for c in http.calls if "/channels/ch1/messages" in c["url"]]
        assert len(send_calls) == 1
        call = send_calls[0]
        assert call["json"]["content"] == "hi"
        assert call["headers"]["Authorization"] == "QQBot tok"

    def test_send_error_raises(self):
        http = _FakeHttpClient(_FakeHttpResp(body={"error": "bad"}))
        ch = QQBotChannel(
            app_id="aid",
            app_secret="asec",
            http_client=http,
        )
        ch._access_token = "tok"
        ch._token_expires_at = time.time() + 3600
        with pytest.raises(QQBotError, match="unexpected"):
            ch.send(
                OutboundMessage(
                    channel_id="qqbot",
                    thread_id="ch1",
                    content="hi",
                    metadata={"qq_channel_id": "ch1"},
                )
            )

    def test_edit_patches_message(self):
        http = _FakeHttpClient(_FakeHttpResp(body={"id": "msg1"}))
        ch = QQBotChannel(
            app_id="aid",
            app_secret="asec",
            http_client=http,
        )
        ch._access_token = "tok"
        ch._token_expires_at = time.time() + 3600
        msg = OutboundMessage(
            channel_id="qqbot",
            thread_id="ch1:old",
            content="edited",
        )
        ch.edit(msg, "orig_id")
        assert len(http.calls) == 1
        call = http.calls[0]
        assert call["method"] == "patch"
        assert "/channels/ch1/messages/orig_id" in call["url"]
        assert call["json"]["content"] == "edited"


# ═══════════════════════════════════════════════════════════
# TeamsChannel
# ═══════════════════════════════════════════════════════════


class TestTeamsChannel:
    @patch("runtime.adapters.channels.teams.HTTPX_AVAILABLE", True)
    def test_constructor(self):
        http = _FakeHttpClient()
        ch = TeamsChannel(
            app_id="aid",
            app_password="apw",
            http_client=http,
        )
        assert ch.channel_id == "teams"

    @patch("runtime.adapters.channels.teams.HTTPX_AVAILABLE", True)
    def test_constructor_requires_app_id(self):
        with pytest.raises(ValueError, match="app_id"):
            TeamsChannel(app_id="", app_password="pw")

    @patch("runtime.adapters.channels.teams.HTTPX_AVAILABLE", True)
    def test_constructor_requires_app_password(self):
        with pytest.raises(ValueError, match="app_password"):
            TeamsChannel(app_id="id", app_password="")

    @patch("runtime.adapters.channels.teams.HTTPX_AVAILABLE", True)
    def test_token_caching_via_oauth2(self):
        http = _FakeHttpClient(
            _FakeHttpResp(
                body={
                    "access_token": "tok123",
                    "expires_in": 3600,
                }
            )
        )
        ch = TeamsChannel(
            app_id="aid",
            app_password="apw",
            http_client=http,
        )
        token1 = ch._ensure_token()
        assert token1 == "tok123"
        assert len(http.calls) == 1
        token2 = ch._ensure_token()
        assert token2 == "tok123"
        assert len(http.calls) == 1

    @patch("runtime.adapters.channels.teams.HTTPX_AVAILABLE", True)
    def test_handle_webhook_parses_activity(self):
        ch = TeamsChannel(app_id="aid", app_password="apw")
        payload = json.dumps(
            {
                "type": "message",
                "text": "hello agent",
                "from": {"id": "user1"},
                "conversation": {"id": "conv1"},
                "id": "act1",
                "timestamp": "2024-01-01T00:00:00Z",
                "serviceUrl": "https://s.botframework.com",
            }
        ).encode()
        msg = ch.handle_webhook(body=payload, headers={})
        assert msg is not None
        assert msg.content == "hello agent"
        assert msg.sender_id == "user1"
        assert msg.metadata["platform"] == "teams"
        assert msg.metadata["teams_conversation_id"] == "conv1"
        assert ch._service_url == "https://s.botframework.com"

    @patch("runtime.adapters.channels.teams.HTTPX_AVAILABLE", True)
    def test_handle_webhook_ping(self):
        ch = TeamsChannel(app_id="aid", app_password="apw")
        payload = json.dumps({"type": "ping"}).encode()
        result = ch.handle_webhook(body=payload, headers={})
        assert result == {"type": "ping"}

    @patch("runtime.adapters.channels.teams.HTTPX_AVAILABLE", True)
    def test_handle_webhook_non_message(self):
        ch = TeamsChannel(app_id="aid", app_password="apw")
        payload = json.dumps({"type": "conversationUpdate"}).encode()
        result = ch.handle_webhook(body=payload, headers={})
        assert result is None


# ═══════════════════════════════════════════════════════════
# LineChannel
# ═══════════════════════════════════════════════════════════


class TestLineChannel:
    def test_constructor(self):
        http = _FakeHttpClient()
        ch = LineChannel(
            channel_access_token="cat",
            channel_secret="cs",
            http_client=http,
        )
        assert ch.channel_id == "line"

    def test_constructor_requires_channel_access_token(self):
        with pytest.raises(ValueError, match="channel_access_token"):
            LineChannel(channel_access_token="", channel_secret="cs")

    def test_constructor_requires_channel_secret(self):
        with pytest.raises(ValueError, match="channel_secret"):
            LineChannel(channel_access_token="cat", channel_secret="")

    def test_handle_webhook_verifies_signature(self):
        secret = "my_secret"
        ch = LineChannel(channel_access_token="cat", channel_secret=secret)
        payload_dict = {
            "events": [
                {
                    "type": "message",
                    "replyToken": "rt123",
                    "source": {"userId": "U123", "type": "user"},
                    "message": {"type": "text", "text": "hello", "id": "msg1"},
                    "timestamp": 1700000000000,
                }
            ],
        }
        body = json.dumps(payload_dict).encode()
        sig = _line_sig(secret, body)
        msg = ch.handle_webhook(body=body, headers={"X-Line-Signature": sig})
        assert msg is not None
        assert msg.content == "hello"
        assert msg.metadata["platform"] == "line"
        assert msg.metadata["replyToken"] == "rt123"

    def test_handle_webhook_bad_signature(self):
        ch = LineChannel(channel_access_token="cat", channel_secret="secret")
        body = json.dumps({"events": []}).encode()
        with pytest.raises(LineSignatureError, match="mismatch"):
            ch.handle_webhook(
                body=body,
                headers={"X-Line-Signature": "bad_sig"},
            )

    def test_handle_webhook_missing_signature(self):
        ch = LineChannel(channel_access_token="cat", channel_secret="secret")
        body = json.dumps({"events": []}).encode()
        with pytest.raises(LineSignatureError, match="missing"):
            ch.handle_webhook(body=body, headers={})

    def test_send_uses_reply(self):
        http = _FakeHttpClient()
        ch = LineChannel(
            channel_access_token="cat",
            channel_secret="cs",
            http_client=http,
        )
        ch.send(
            OutboundMessage(
                channel_id="line",
                thread_id="U123:msg1",
                content="hi",
                metadata={"replyToken": "rt123"},
            )
        )
        assert len(http.calls) == 1
        call = http.calls[0]
        assert "/v2/bot/message/reply" in call["url"]
        assert call["json"]["replyToken"] == "rt123"
        assert call["json"]["messages"][0]["text"] == "hi"

    def test_send_uses_push(self):
        http = _FakeHttpClient()
        ch = LineChannel(
            channel_access_token="cat",
            channel_secret="cs",
            http_client=http,
        )
        ch.send(
            OutboundMessage(
                channel_id="line",
                thread_id="U123:msg1",
                content="hi",
            )
        )
        assert len(http.calls) == 1
        call = http.calls[0]
        assert "/v2/bot/message/push" in call["url"]
        assert call["json"]["to"] == "U123"
        assert call["json"]["messages"][0]["text"] == "hi"

    def test_send_push_no_target_raises(self):
        http = _FakeHttpClient()
        ch = LineChannel(
            channel_access_token="cat",
            channel_secret="cs",
            http_client=http,
        )
        with pytest.raises(LineError, match="no target"):
            ch.send(
                OutboundMessage(
                    channel_id="line",
                    thread_id=":msg1",
                    content="hi",
                )
            )


# ═══════════════════════════════════════════════════════════
# HomeAssistantChannel
# ═══════════════════════════════════════════════════════════


class TestHomeAssistantChannel:
    def test_constructor(self):
        http = _FakeHttpClient()
        ch = HomeAssistantChannel(
            ha_url="http://ha.local:8123",
            long_lived_token="llt",
            http_client=http,
        )
        assert ch.channel_id == "homeassistant"

    def test_constructor_requires_ha_url(self):
        with pytest.raises(ValueError, match="ha_url"):
            HomeAssistantChannel(ha_url="", long_lived_token="llt")

    def test_constructor_requires_long_lived_token(self):
        with pytest.raises(ValueError, match="long_lived_token"):
            HomeAssistantChannel(ha_url="http://ha.local:8123", long_lived_token="")

    def test_send_posts_to_api_services_notify(self):
        http = _FakeHttpClient()
        ch = HomeAssistantChannel(
            ha_url="http://ha.local:8123",
            long_lived_token="llt",
            http_client=http,
        )
        ch.send(
            OutboundMessage(
                channel_id="homeassistant",
                thread_id="ha:entity1",
                content="alert!",
            )
        )
        assert len(http.calls) == 1
        call = http.calls[0]
        assert call["url"] == "http://ha.local:8123/api/services/notify/persistent_notification"
        assert call["json"]["message"] == "alert!"
        assert call["headers"]["Authorization"] == "Bearer llt"

    def test_send_http_error_raises(self):
        http = _FakeHttpClient(_FakeHttpResp(status_code=401, body={}))
        ch = HomeAssistantChannel(
            ha_url="http://ha.local:8123",
            long_lived_token="llt",
            http_client=http,
        )
        with pytest.raises(HomeAssistantError, match="HTTP 401"):
            ch.send(
                OutboundMessage(
                    channel_id="homeassistant",
                    thread_id="t",
                    content="hi",
                )
            )

    def test_handle_webhook_parses_json(self):
        ch = HomeAssistantChannel(
            ha_url="http://ha.local:8123",
            long_lived_token="llt",
        )
        payload = json.dumps(
            {
                "message": "door opened",
                "entity_id": "binary_sensor.front_door",
                "user_id": "user1",
                "timestamp": 1700000000,
            }
        ).encode()
        msg = ch.handle_webhook(body=payload, headers={})
        assert msg is not None
        assert msg.content == "door opened"
        assert msg.metadata["platform"] == "homeassistant"
        assert msg.metadata["entity_id"] == "binary_sensor.front_door"


# ═══════════════════════════════════════════════════════════
# BlueBubblesChannel
# ═══════════════════════════════════════════════════════════


class TestBlueBubblesChannel:
    def test_constructor(self):
        http = _FakeHttpClient()
        ch = BlueBubblesChannel(
            server_url="http://bb.local:3000",
            api_key="ak",
            http_client=http,
        )
        assert ch.channel_id == "bluebubbles"

    def test_constructor_requires_server_url(self):
        with pytest.raises(ValueError, match="server_url"):
            BlueBubblesChannel(server_url="", api_key="ak")

    def test_constructor_requires_api_key(self):
        with pytest.raises(ValueError, match="api_key"):
            BlueBubblesChannel(server_url="http://bb.local:3000", api_key="")

    def test_send_posts_to_api_v1_message_text(self):
        http = _FakeHttpClient(_FakeHttpResp(body={"status": 200}))
        ch = BlueBubblesChannel(
            server_url="http://bb.local:3000",
            api_key="ak",
            http_client=http,
        )
        ch.send(
            OutboundMessage(
                channel_id="bluebubbles",
                thread_id="chatGuid1:msg1",
                content="hi",
            )
        )
        assert len(http.calls) == 1
        call = http.calls[0]
        assert call["url"] == "http://bb.local:3000/api/v1/message/text"
        assert call["json"]["message"] == "hi"
        assert call["json"]["chatGuid"] == "chatGuid1"
        assert call["headers"]["Authorization"] == "Bearer ak"

    def test_send_error_raises(self):
        http = _FakeHttpClient(
            _FakeHttpResp(
                body={
                    "status": 500,
                    "message": "server error",
                }
            )
        )
        ch = BlueBubblesChannel(
            server_url="http://bb.local:3000",
            api_key="ak",
            http_client=http,
        )
        with pytest.raises(BlueBubblesError, match="server error"):
            ch.send(
                OutboundMessage(
                    channel_id="bluebubbles",
                    thread_id="cg1:m1",
                    content="hi",
                )
            )

    def test_handle_webhook_parses_message(self):
        ch = BlueBubblesChannel(
            server_url="http://bb.local:3000",
            api_key="ak",
        )
        payload = json.dumps(
            {
                "data": {
                    "message": {
                        "guid": "msg-guid-1",
                        "text": "hello from iMessage",
                        "isFromMe": False,
                        "chats": [{"guid": "chat-guid-1"}],
                        "sender": "sender1",
                        "dateCreated": 1700000000000,
                    },
                },
            }
        ).encode()
        msg = ch.handle_webhook(body=payload, headers={})
        assert msg is not None
        assert msg.content == "hello from iMessage"
        assert msg.metadata["platform"] == "bluebubbles"
        assert msg.metadata["chat_guid"] == "chat-guid-1"

    def test_handle_webhook_from_me_filtered(self):
        ch = BlueBubblesChannel(
            server_url="http://bb.local:3000",
            api_key="ak",
        )
        payload = json.dumps(
            {
                "data": {
                    "message": {
                        "guid": "msg1",
                        "text": "hi",
                        "isFromMe": True,
                        "chats": [{"guid": "cg1"}],
                    },
                },
            }
        ).encode()
        msg = ch.handle_webhook(body=payload, headers={})
        assert msg is None


# ═══════════════════════════════════════════════════════════
# NtfyChannel
# ═══════════════════════════════════════════════════════════


class TestNtfyChannel:
    def test_constructor(self):
        http = _FakeHttpClient()
        ch = NtfyChannel(topic="mytopic", http_client=http)
        assert ch.channel_id == "ntfy"

    def test_constructor_requires_topic(self):
        with pytest.raises(ValueError, match="topic"):
            NtfyChannel(topic="")

    def test_send_posts_plain_text_to_topic(self):
        http = _FakeHttpClient()
        ch = NtfyChannel(
            server_url="https://ntfy.example.com",
            topic="alerts",
            http_client=http,
        )
        ch.send(
            OutboundMessage(
                channel_id="ntfy",
                thread_id="ntfy:alerts",
                content="fire!",
            )
        )
        assert len(http.calls) == 1
        call = http.calls[0]
        assert call["url"] == "https://ntfy.example.com/alerts"
        assert call["content"] == "fire!"
        assert call["headers"]["Title"] == "Echo Agent"

    def test_send_http_error_raises(self):
        http = _FakeHttpClient(_FakeHttpResp(status_code=500, body={}))
        ch = NtfyChannel(topic="t", http_client=http)
        with pytest.raises(NtfyError, match="HTTP 500"):
            ch.send(
                OutboundMessage(
                    channel_id="ntfy",
                    thread_id="ntfy:t",
                    content="hi",
                )
            )

    def test_handle_webhook_parses_json(self):
        ch = NtfyChannel(topic="alerts")
        payload = json.dumps(
            {
                "message": "server down",
                "topic": "alerts",
                "title": "Alert",
                "time": 1700000000,
            }
        ).encode()
        msg = ch.handle_webhook(body=payload, headers={})
        assert msg is not None
        assert msg.content == "server down"
        assert msg.metadata["platform"] == "ntfy"
        assert msg.metadata["topic"] == "alerts"


# ═══════════════════════════════════════════════════════════
# WebhooksChannel
# ═══════════════════════════════════════════════════════════


class TestWebhooksChannel:
    def test_constructor(self):
        ch = WebhooksChannel(webhook_secret="s3cret", outbound_url="http://hook")
        assert ch.channel_id == "webhooks"

    def test_send_posts_with_hmac_signature(self):
        http = _FakeHttpClient()
        ch = WebhooksChannel(
            webhook_secret="s3cret",
            outbound_url="http://hook.example.com/incoming",
            http_client=http,
        )
        ch.send(
            OutboundMessage(
                channel_id="webhooks",
                thread_id="webhook:id1",
                content="hi",
            )
        )
        assert len(http.calls) == 1
        call = http.calls[0]
        assert call["url"] == "http://hook.example.com/incoming"
        assert "X-Webhook-Signature" in call["headers"]
        raw_body = call["content"]
        parsed = json.loads(raw_body)
        assert parsed["text"] == "hi"
        assert parsed["source"] == "echo-agent"
        expected_sig = _webhooks_sig("s3cret", raw_body)
        assert call["headers"]["X-Webhook-Signature"] == expected_sig

    def test_send_no_outbound_url_is_noop(self):
        http = _FakeHttpClient()
        ch = WebhooksChannel(
            webhook_secret="s3cret",
            outbound_url="",
            http_client=http,
        )
        ch.send(
            OutboundMessage(
                channel_id="webhooks",
                thread_id="t",
                content="hi",
            )
        )
        assert len(http.calls) == 0

    def test_handle_webhook_verifies_signature(self):
        secret = "s3cret"
        ch = WebhooksChannel(webhook_secret=secret)
        payload_dict = {"text": "hello", "id": "id1", "timestamp": 1700000000}
        raw_body = json.dumps(payload_dict).encode()
        sig = _webhooks_sig(secret, raw_body)
        msg = ch.handle_webhook(
            body=raw_body,
            headers={"X-Webhook-Signature": sig},
        )
        assert msg is not None
        assert msg.content == "hello"
        assert msg.metadata["platform"] == "webhooks"

    def test_handle_webhook_bad_signature(self):
        ch = WebhooksChannel(webhook_secret="s3cret")
        body = json.dumps({"text": "hello"}).encode()
        with pytest.raises(WebhooksSignatureError, match="mismatch"):
            ch.handle_webhook(
                body=body,
                headers={"X-Webhook-Signature": "bad_sig"},
            )

    def test_handle_webhook_no_secret_skips_verify(self):
        ch = WebhooksChannel(webhook_secret="")
        payload = json.dumps({"text": "hello", "id": "id1"}).encode()
        msg = ch.handle_webhook(body=payload, headers={})
        assert msg is not None
        assert msg.content == "hello"


# ═══════════════════════════════════════════════════════════
# GoogleChatChannel
# ═══════════════════════════════════════════════════════════


class TestGoogleChatChannel:
    def test_constructor(self):
        http = _FakeHttpClient()
        ch = GoogleChatChannel(
            service_account_key={
                "client_email": "test@test.iam.gserviceaccount.com",
                "private_key": "dummy",
            },
            http_client=http,
        )
        assert ch.channel_id == "google_chat"

    def test_constructor_invalid_key_type(self):
        with pytest.raises(TypeError, match="service_account_key"):
            GoogleChatChannel(service_account_key=12345)

    def test_constructor_key_file_not_found(self):
        with pytest.raises(ValueError, match="file not found"):
            GoogleChatChannel(service_account_key="/nonexistent/path.json")

    def test_handle_webhook_parses_event(self):
        ch = GoogleChatChannel(
            service_account_key={
                "client_email": "test@test.iam.gserviceaccount.com",
                "private_key": "dummy",
            }
        )
        payload = json.dumps(
            {
                "event": {
                    "type": "MESSAGE",
                    "message": {
                        "text": "hello chat",
                        "name": "spaces/AAA/messages/BBB",
                    },
                    "sender": {
                        "name": "users/123",
                        "displayName": "Alice",
                    },
                    "space": {
                        "name": "spaces/AAA",
                    },
                    "eventTime": "2024-01-01T00:00:00Z",
                },
            }
        ).encode()
        msg = ch.handle_webhook(body=payload, headers={})
        assert msg is not None
        assert msg.content == "hello chat"
        assert msg.metadata["platform"] == "google_chat"
        assert msg.metadata["google_chat_space"] == "spaces/AAA"
        assert msg.metadata["google_chat_sender_display"] == "Alice"

    def test_handle_webhook_non_message_event(self):
        ch = GoogleChatChannel(
            service_account_key={
                "client_email": "test@test.iam.gserviceaccount.com",
                "private_key": "dummy",
            }
        )
        payload = json.dumps(
            {
                "event": {"type": "ADDED_TO_SPACE"},
            }
        ).encode()
        result = ch.handle_webhook(body=payload, headers={})
        assert result is None


# ═══════════════════════════════════════════════════════════
# SimpleXChannel
# ═══════════════════════════════════════════════════════════


class TestSimpleXChannel:
    def test_constructor(self):
        http = _FakeHttpClient()
        ch = SimpleXChannel(http_client=http)
        assert ch.channel_id == "simplex"

    def test_send_posts_to_v1_chat_item(self):
        http = _FakeHttpClient()
        ch = SimpleXChannel(http_client=http)
        ch.send(
            OutboundMessage(
                channel_id="simplex",
                thread_id="contact1:item1",
                content="hi",
            )
        )
        assert len(http.calls) == 1
        call = http.calls[0]
        assert call["url"].endswith("/v1/chat/item")
        assert call["json"]["chat"]["id"] == "contact1"
        assert call["json"]["content"]["msg"]["text"] == "hi"

    def test_send_http_error_raises(self):
        http = _FakeHttpClient(_FakeHttpResp(status_code=500, body={}))
        ch = SimpleXChannel(http_client=http)
        with pytest.raises(SimpleXError, match="HTTP 500"):
            ch.send(
                OutboundMessage(
                    channel_id="simplex",
                    thread_id="c1:i1",
                    content="hi",
                )
            )

    def test_handle_webhook_parses_event(self):
        ch = SimpleXChannel()
        payload = json.dumps(
            {
                "chatInfo": {
                    "contact": {
                        "displayName": "Bob",
                        "contactId": "c1",
                    },
                },
                "chatItem": {
                    "content": {
                        "msg": {
                            "content": {
                                "text": "hello simplex",
                            },
                        },
                    },
                    "meta": {
                        "itemId": "item1",
                        "createdAt": 1700000000,
                    },
                },
            }
        ).encode()
        msg = ch.handle_webhook(body=payload, headers={})
        assert msg is not None
        assert msg.content == "hello simplex"
        assert msg.sender_id == "Bob"
        assert msg.metadata["platform"] == "simplex"
        assert msg.metadata["contact_id"] == "c1"


# ═══════════════════════════════════════════════════════════
# OpenWebUIChannel
# ═══════════════════════════════════════════════════════════


class TestOpenWebUIChannel:
    def test_constructor(self):
        http = _FakeHttpClient()
        ch = OpenWebUIChannel(
            base_url="http://owui.local:8080",
            api_key="ak",
            http_client=http,
        )
        assert ch.channel_id == "open_webui"

    def test_constructor_requires_base_url(self):
        with pytest.raises(ValueError, match="base_url"):
            OpenWebUIChannel(base_url="", api_key="ak")

    def test_constructor_requires_api_key(self):
        with pytest.raises(ValueError, match="api_key"):
            OpenWebUIChannel(base_url="http://owui.local:8080", api_key="")

    def test_send_posts_to_api_chat_completions(self):
        http = _FakeHttpClient()
        ch = OpenWebUIChannel(
            base_url="http://owui.local:8080",
            api_key="ak",
            http_client=http,
        )
        ch.send(
            OutboundMessage(
                channel_id="open_webui",
                thread_id="webui:chat1",
                content="hi",
            )
        )
        assert len(http.calls) == 1
        call = http.calls[0]
        assert call["url"] == "http://owui.local:8080/api/chat/completions"
        assert call["json"]["model"] == "echo-agent"
        assert call["json"]["messages"][0]["content"] == "hi"
        assert call["json"]["stream"] is False
        assert call["headers"]["Authorization"] == "Bearer ak"

    def test_send_http_error_raises(self):
        http = _FakeHttpClient(_FakeHttpResp(status_code=500, body={}))
        ch = OpenWebUIChannel(
            base_url="http://owui.local:8080",
            api_key="ak",
            http_client=http,
        )
        with pytest.raises(OpenWebUIError, match="HTTP 500"):
            ch.send(
                OutboundMessage(
                    channel_id="open_webui",
                    thread_id="t",
                    content="hi",
                )
            )

    def test_handle_webhook_parses_messages(self):
        ch = OpenWebUIChannel(base_url="http://owui.local:8080", api_key="ak")
        payload = json.dumps(
            {
                "messages": [{"role": "user", "content": "hello webui"}],
                "chat_id": "chat1",
                "user": {"id": "user1"},
                "timestamp": 1700000000,
            }
        ).encode()
        msg = ch.handle_webhook(body=payload, headers={})
        assert msg is not None
        assert msg.content == "hello webui"
        assert msg.metadata["platform"] == "open_webui"
        assert msg.metadata["chat_id"] == "chat1"


# ═══════════════════════════════════════════════════════════
# YuanbaoChannel
# ═══════════════════════════════════════════════════════════


class TestYuanbaoChannel:
    def test_constructor(self):
        http = _FakeHttpClient()
        ch = YuanbaoChannel(
            bot_id="bot1",
            bot_token="bt",
            http_client=http,
        )
        assert ch.channel_id == "yuanbao"
        assert ch.supports_edit is True

    def test_constructor_requires_bot_id(self):
        with pytest.raises(ValueError, match="bot_id"):
            YuanbaoChannel(bot_id="", bot_token="bt")

    def test_constructor_requires_bot_token(self):
        with pytest.raises(ValueError, match="bot_token"):
            YuanbaoChannel(bot_id="bot1", bot_token="")

    def test_handle_webhook_verifies_token(self):
        ch = YuanbaoChannel(bot_id="bot1", bot_token="bt")
        payload = json.dumps(
            {
                "token": "bt",
                "content": {"type": "text", "text": "hello yuanbao"},
                "from_user": {"openid": "user1"},
                "chat_id": "chat1",
                "message_id": "msg1",
                "create_time": 1700000000000,
            }
        ).encode()
        msg = ch.handle_webhook(body=payload, headers={})
        assert msg is not None
        assert msg.content == "hello yuanbao"
        assert msg.metadata["platform"] == "yuanbao"
        assert msg.metadata["chat_id"] == "chat1"

    def test_handle_webhook_bad_token(self):
        ch = YuanbaoChannel(bot_id="bot1", bot_token="bt")
        payload = json.dumps(
            {
                "token": "wrong",
                "content": {"type": "text", "text": "hi"},
                "chat_id": "chat1",
            }
        ).encode()
        with pytest.raises(YuanbaoSignatureError, match="mismatch"):
            ch.handle_webhook(body=payload, headers={})

    def test_edit_posts_to_api_bot_message_mid(self):
        http = _FakeHttpClient()
        ch = YuanbaoChannel(
            bot_id="bot1",
            bot_token="bt",
            http_client=http,
        )
        msg = OutboundMessage(
            channel_id="yuanbao",
            thread_id="chat1:msg1",
            content="edited",
        )
        ch.edit(msg, "orig_mid")
        assert len(http.calls) == 1
        call = http.calls[0]
        assert call["url"].endswith("/api/bot/bot1/message/orig_mid")
        assert call["json"]["content"]["text"] == "edited"
        assert call["headers"]["Authorization"] == "Bearer bt"

    def test_edit_error_raises(self):
        http = _FakeHttpClient(_FakeHttpResp(body={"code": 1, "msg": "fail"}))
        ch = YuanbaoChannel(
            bot_id="bot1",
            bot_token="bt",
            http_client=http,
        )
        msg = OutboundMessage(
            channel_id="yuanbao",
            thread_id="c1:m1",
            content="x",
        )
        with pytest.raises(YuanbaoError, match="yuanbao edit failed"):
            ch.edit(msg, "mid1")


# ═══════════════════════════════════════════════════════════
# Channel Base Features
# ═══════════════════════════════════════════════════════════


class TestChannelBaseFeatures:
    def test_attachment_dataclass_creation(self):
        att = Attachment(
            content_type="image/png",
            data=b"\x89PNG",
            url="http://x.png",
            filename="x.png",
            metadata={"size": 100},
        )
        assert att.content_type == "image/png"
        assert att.data == b"\x89PNG"
        assert att.url == "http://x.png"
        assert att.filename == "x.png"
        assert att.metadata["size"] == 100

    def test_channel_supports_edit_default_false(self):
        ch = _FakeChannel()
        assert ch.supports_edit is False

    def test_channel_supports_typing_default_false(self):
        ch = _FakeChannel()
        assert ch.supports_typing is False

    def test_channel_supports_reactions_default_false(self):
        ch = _FakeChannel()
        assert ch.supports_reactions is False

    def test_edit_falls_back_to_send_when_not_supported(self):
        ch = _FakeChannel()
        msg = OutboundMessage(
            channel_id="fake",
            thread_id="t1",
            content="hi",
        )
        ch.edit(msg, "original_id")
        assert len(ch.sent) == 1
        assert ch.sent[0].content == "hi"

    def test_send_typing_is_noop_when_not_supported(self):
        ch = _FakeChannel()
        ch.send_typing("thread1")

    def test_add_reaction_is_noop_when_not_supported(self):
        ch = _FakeChannel()
        ch.add_reaction("thread1", "msg1", "👍")

    def test_manager_edit_on_channel_falls_back_to_send(self):
        ch = _FakeChannel("test_ch")
        m = ChannelManager(
            stack=MagicMock(),
            agent_registry=MagicMock(),
        )
        m.register(ch)
        msg = OutboundMessage(
            channel_id="test_ch",
            thread_id="t1",
            content="edited",
        )
        m.edit_on_channel("test_ch", msg, "orig_id")
        assert len(ch.sent) == 1
        assert ch.sent[0].content == "edited"

    def test_manager_deliver_cron_result_sends_via_channel(self):
        ch = _FakeChannel("cron_ch")
        m = ChannelManager(
            stack=MagicMock(),
            agent_registry=MagicMock(),
        )
        m.register(ch)
        m.deliver_cron_result("cron_ch", "thread1", "cron output")
        assert len(ch.sent) == 1
        assert ch.sent[0].content == "cron output"
        assert ch.sent[0].metadata["source"] == "cron"

    def test_manager_channel_supports_edit_returns_correct_bool(self):
        ch = _FakeChannel("no_edit")
        m = ChannelManager(
            stack=MagicMock(),
            agent_registry=MagicMock(),
        )
        m.register(ch)
        assert m.channel_supports_edit("no_edit") is False
        assert m.channel_supports_edit("nonexistent") is False

    def test_manager_channel_supports_edit_true_for_edit_channel(self):
        http = _FakeHttpClient()
        ch = MattermostChannel(
            bot_token="bt",
            server_url="https://mm.example.com",
            http_client=http,
            channel_id="mm_edit",
        )
        m = ChannelManager(
            stack=MagicMock(),
            agent_registry=MagicMock(),
        )
        m.register(ch)
        assert m.channel_supports_edit("mm_edit") is True
