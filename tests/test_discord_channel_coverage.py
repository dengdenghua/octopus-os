"""Dense coverage for discord channel verify/webhook/ts (audit Q-05)."""

from __future__ import annotations

import json

import pytest

from runtime.adapters.channels.discord import (
    DiscordChannel,
    DiscordError,
    DiscordSignatureError,
    _parse_discord_ts,
)

PUBLIC_KEY = "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
BAD_PUBLIC_KEY = "zzzz"


def _channel(**kw) -> DiscordChannel:
    kw.setdefault("bot_token", "tok")
    kw.setdefault("public_key", PUBLIC_KEY)
    kw.setdefault("channel_id", "discord")
    return DiscordChannel(**kw)


def test_constructor_validation() -> None:
    with pytest.raises(ValueError):
        DiscordChannel(bot_token="", public_key=PUBLIC_KEY)
    with pytest.raises(ValueError):
        DiscordChannel(bot_token="t", public_key=BAD_PUBLIC_KEY)


def test_verify_signature_error_paths() -> None:
    ch = _channel()
    with pytest.raises(DiscordSignatureError):
        ch.verify_signature(body=b"x", signature_hex="", timestamp="")
    with pytest.raises(DiscordSignatureError):
        ch.verify_signature(body=b"x", signature_hex="nothex", timestamp="t")


def test_verify_signature_success() -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    from cryptography.hazmat.primitives import serialization

    raw = pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    ch = DiscordChannel(bot_token="t", public_key=raw.hex())
    body = b'{"type":1}'
    ts = "1700000000"
    sig = priv.sign(ts.encode() + body).hex()
    ch.verify_signature(body=body, signature_hex=sig, timestamp=ts)  # no raise


def test_handle_webhook_ping_and_command(monkeypatch) -> None:
    ch = _channel()
    # verify_signature has its own tests; here we exercise the payload logic.
    monkeypatch.setattr(ch, "verify_signature", lambda **kw: None)

    ping = ch.handle_webhook(body=b'{"type":1}', headers={})
    assert ping == {"type": 1}

    payload = {
        "type": 2,
        "id": "i1",
        "channel_id": "c1",
        "guild_id": "g1",
        "member": {"user": {"id": "u1"}},
        "data": {"name": "ask", "options": [{"value": "hello"}]},
        "message": {"id": "m1", "attachments": [{"url": "http://a/1", "filename": "f.png"}]},
    }
    body = json.dumps(payload).encode()
    msg = ch.handle_webhook(body=body, headers={})
    assert msg is not None
    assert msg.sender_id == "u1"
    assert msg.thread_id == "c1"
    assert "hello" in msg.content
    assert msg.attachments and msg.attachments[0].filename == "f.png"

    with pytest.raises(ValueError):
        ch.handle_webhook(body=b"not json", headers={})


def test_parse_discord_ts() -> None:
    assert _parse_discord_ts("2026-08-17T12:00:00Z") is not None
    assert _parse_discord_ts("bad") is None
    assert _parse_discord_ts(None) is None


class _FakeHttp:
    def __init__(self, responses=None, status=200, data=None):
        self._responses = list(responses or [])
        self.status = status
        self.data = data if data is not None else {"id": "m1", "content": "hi"}
        self.calls = []

    def post(self, url, **kw):
        self.calls.append(("post", url))
        if self._responses:
            return self._responses.pop(0)
        return _Resp(self.status, self.data)


class _Resp:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self._data = data if data is not None else {"id": "m1", "content": "hi"}

    def json(self):
        return self._data


def _msg(**kw):
    from runtime.adapters.channels.base import OutboundMessage

    md = kw.pop("metadata", {})
    return OutboundMessage(channel_id="discord", thread_id="c1", content="hi", metadata=md, **kw)


def test_send_text_message() -> None:
    ch = _channel(http_client=_FakeHttp())
    ch.send(_msg(metadata={"discord_channel_id": "c1"}))
    assert len(ch.send_log) == 1
    assert ch._http.calls[0][0] == "post"

    from runtime.adapters.channels.base import OutboundMessage

    bad = _channel(http_client=_FakeHttp())
    # No discord_channel_id in metadata and no thread_id fallback → send must fail.
    orphan = OutboundMessage(channel_id="discord", thread_id=None, content="hi")
    with pytest.raises(DiscordError):
        bad.send(orphan)


def test_post_json_retry_and_error() -> None:
    retry = _FakeHttp(responses=[_Resp(429, {}), _Resp(200, {"id": "m"})])
    ch = _channel(http_client=retry)
    data = ch._post_json("http://x", body={}, authorization="Bot t")
    assert data["id"] == "m"
    assert len(retry.calls) == 2

    err = _FakeHttp(status=400, data={})
    ch2 = _channel(http_client=err)
    with pytest.raises(DiscordError):
        ch2._post_json("http://x", body={}, authorization="Bot t")

