"""Implementation note."""

from __future__ import annotations

import json
from typing import Any

import pytest

pytest.importorskip("cryptography")
from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey,
)

from runtime.adapters.channels import (  # noqa: E402
    DiscordChannel,
    DiscordError,
    DiscordSignatureError,
    InboundMessage,
    OutboundMessage,
)

# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


def _new_keypair() -> tuple[Ed25519PrivateKey, str]:
    """Implementation note."""
    priv = Ed25519PrivateKey.generate()
    from cryptography.hazmat.primitives import serialization

    pub_bytes = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return priv, pub_bytes.hex()


def _sign(
    priv: Ed25519PrivateKey,
    *,
    timestamp: str,
    body: bytes,
) -> str:
    msg = timestamp.encode("utf-8") + body
    return priv.sign(msg).hex()


# ═══════════════════════════════════════════════════════════
# HTTP mock
# ═══════════════════════════════════════════════════════════


class _FakeResp:
    def __init__(self, *, status_code=200, body=None, text=""):
        self.status_code = status_code
        self._body = body if body is not None else {"id": "msg1"}
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
        _, pub = _new_keypair()
        with pytest.raises(ValueError, match="bot_token"):
            DiscordChannel(bot_token="", public_key=pub)

    def test_public_key_required(self):
        with pytest.raises(ValueError, match="public_key"):
            DiscordChannel(bot_token="x", public_key="")

    def test_bad_hex_public_key_rejected(self):
        with pytest.raises(ValueError, match="public_key"):
            DiscordChannel(bot_token="x", public_key="not-hex-at-all-xyz")

    def test_default_channel_id(self):
        _, pub = _new_keypair()
        ch = DiscordChannel(bot_token="x", public_key=pub)
        assert ch.channel_id == "discord"


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestSignature:
    def test_valid_signature_passes(self):
        priv, pub = _new_keypair()
        ch = DiscordChannel(bot_token="x", public_key=pub)
        body = b'{"type":1}'
        ts = "1700000000"
        sig = _sign(priv, timestamp=ts, body=body)
        ch.verify_signature(body=body, signature_hex=sig, timestamp=ts)

    def test_tampered_body_detected(self):
        priv, pub = _new_keypair()
        ch = DiscordChannel(bot_token="x", public_key=pub)
        sig = _sign(priv, timestamp="1700000000", body=b'{"type":1}')
        with pytest.raises(DiscordSignatureError, match="invalid"):
            ch.verify_signature(
                body=b'{"type":2}',  # Implementation note.
                signature_hex=sig,
                timestamp="1700000000",
            )

    def test_wrong_key_rejects(self):
        priv1, _ = _new_keypair()
        _, pub2 = _new_keypair()
        ch = DiscordChannel(bot_token="x", public_key=pub2)
        sig = _sign(priv1, timestamp="1", body=b"{}")
        with pytest.raises(DiscordSignatureError):
            ch.verify_signature(
                body=b"{}",
                signature_hex=sig,
                timestamp="1",
            )

    def test_missing_headers_raises(self):
        _, pub = _new_keypair()
        ch = DiscordChannel(bot_token="x", public_key=pub)
        with pytest.raises(DiscordSignatureError, match="missing"):
            ch.verify_signature(body=b"{}", signature_hex="", timestamp="")
        with pytest.raises(DiscordSignatureError, match="missing"):
            ch.verify_signature(body=b"{}", signature_hex="abc", timestamp="")

    def test_bad_hex_signature(self):
        _, pub = _new_keypair()
        ch = DiscordChannel(bot_token="x", public_key=pub)
        with pytest.raises(DiscordSignatureError, match="bad signature"):
            ch.verify_signature(
                body=b"{}",
                signature_hex="xyz-not-hex",
                timestamp="1",
            )


# ═══════════════════════════════════════════════════════════
# handle_webhook · PING / APPLICATION_COMMAND
# ═══════════════════════════════════════════════════════════


class TestWebhookFlow:
    def test_ping_returns_pong(self):
        priv, pub = _new_keypair()
        ch = DiscordChannel(bot_token="x", public_key=pub)
        body = json.dumps({"type": 1}).encode("utf-8")
        ts = "1700000000"
        sig = _sign(priv, timestamp=ts, body=body)
        result = ch.handle_webhook(
            body=body,
            headers={
                "X-Signature-Ed25519": sig,
                "X-Signature-Timestamp": ts,
            },
        )
        assert result == {"type": 1}

    def test_slash_command_becomes_inbound(self):
        priv, pub = _new_keypair()
        ch = DiscordChannel(bot_token="x", public_key=pub)
        payload = {
            "id": "int_abc",
            "type": 2,  # APPLICATION_COMMAND
            "channel_id": "chan_1",
            "guild_id": "guild_1",
            "member": {
                "user": {"id": "user_1", "username": "alice"},
            },
            "data": {
                "name": "ask",
                "options": [
                    {
                        "name": "query",
                        "type": 3,  # STRING
                        "value": "help me refactor",
                    }
                ],
            },
        }
        body = json.dumps(payload).encode("utf-8")
        ts = "1700000000"
        sig = _sign(priv, timestamp=ts, body=body)
        msg = ch.handle_webhook(
            body=body,
            headers={
                "X-Signature-Ed25519": sig,
                "X-Signature-Timestamp": ts,
            },
        )
        assert isinstance(msg, InboundMessage)
        assert msg.channel_id == "discord"
        assert msg.thread_id == "chan_1"
        assert msg.sender_id == "user_1"
        assert "/ask" in msg.content
        assert "help me refactor" in msg.content
        assert msg.metadata["guild_id"] == "guild_1"
        assert msg.metadata["command_name"] == "ask"

    def test_non_command_interaction_ignored(self):
        """Implementation note."""
        priv, pub = _new_keypair()
        ch = DiscordChannel(bot_token="x", public_key=pub)
        body = json.dumps({"type": 3, "data": {}}).encode("utf-8")
        ts = "1"
        sig = _sign(priv, timestamp=ts, body=body)
        result = ch.handle_webhook(
            body=body,
            headers={
                "X-Signature-Ed25519": sig,
                "X-Signature-Timestamp": ts,
            },
        )
        assert result is None

    def test_bad_signature_raises(self):
        _, pub = _new_keypair()
        ch = DiscordChannel(bot_token="x", public_key=pub)
        body = json.dumps({"type": 1}).encode("utf-8")
        with pytest.raises(DiscordSignatureError):
            ch.handle_webhook(
                body=body,
                headers={
                    "X-Signature-Ed25519": "deadbeef" * 8,  # Implementation note.
                    "X-Signature-Timestamp": "1",
                },
            )

    def test_bad_json_body_raises_after_sig_pass(self):
        priv, pub = _new_keypair()
        ch = DiscordChannel(bot_token="x", public_key=pub)
        body = b"{not json"
        ts = "1700000000"
        sig = _sign(priv, timestamp=ts, body=body)  # Implementation note.
        with pytest.raises(ValueError, match="bad json"):
            ch.handle_webhook(
                body=body,
                headers={
                    "X-Signature-Ed25519": sig,
                    "X-Signature-Timestamp": ts,
                },
            )


# ═══════════════════════════════════════════════════════════
# send
# ═══════════════════════════════════════════════════════════


class TestSend:
    def test_send_to_channel(self):
        _, pub = _new_keypair()
        http = _FakeHttp(_FakeResp(body={"id": "msg42", "content": "ok"}))
        ch = DiscordChannel(
            bot_token="TOKENxyz",
            public_key=pub,
            http_client=http,
        )
        ch.send(
            OutboundMessage(
                channel_id="discord",
                thread_id="123456",
                content="reply here",
            )
        )
        assert len(http.calls) == 1
        url = http.calls[0]["url"]
        assert "/channels/123456/messages" in url
        assert http.calls[0]["headers"]["Authorization"] == "Bot TOKENxyz"
        assert http.calls[0]["json"]["content"] == "reply here"

    def test_send_with_message_reference(self):
        _, pub = _new_keypair()
        http = _FakeHttp(_FakeResp(body={"id": "x"}))
        ch = DiscordChannel(
            bot_token="t",
            public_key=pub,
            http_client=http,
        )
        ch.send(
            OutboundMessage(
                channel_id="discord",
                thread_id="chan",
                content="reply",
                metadata={"message_reference_id": "orig_msg"},
            )
        )
        body = http.calls[0]["json"]
        assert body["message_reference"] == {"message_id": "orig_msg"}

    def test_discord_channel_id_metadata_overrides(self):
        _, pub = _new_keypair()
        http = _FakeHttp(_FakeResp(body={"id": "x"}))
        ch = DiscordChannel(
            bot_token="t",
            public_key=pub,
            http_client=http,
        )
        ch.send(
            OutboundMessage(
                channel_id="discord",
                thread_id="fallback",
                content="x",
                metadata={"discord_channel_id": "actual-chan"},
            )
        )
        assert "/channels/actual-chan/messages" in http.calls[0]["url"]

    def test_http_error_raises(self):
        _, pub = _new_keypair()
        http = _FakeHttp(_FakeResp(status_code=401, text="unauthorized"))
        ch = DiscordChannel(
            bot_token="bad",
            public_key=pub,
            http_client=http,
        )
        with pytest.raises(DiscordError, match="HTTP 401"):
            ch.send(
                OutboundMessage(
                    channel_id="discord",
                    thread_id="c",
                    content="x",
                )
            )
