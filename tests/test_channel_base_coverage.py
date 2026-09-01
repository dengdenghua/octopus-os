"""Dense coverage for the Channel ABC and message helpers (audit Q-05)."""

from __future__ import annotations

import sys

import pytest

from runtime.adapters.channels.base import (
    Attachment,
    Channel,
    InboundMessage,
    OutboundMessage,
    _sanitize_url,
    resolve_attachment_data,
)


def test_sanitize_url_scrubs_credentials() -> None:
    url = "https://x/hook?token=abc12345secret&bot=1234567890/prefix"
    cleaned = _sanitize_url(url)
    assert "abc12345***" in cleaned
    assert "1234567890" not in cleaned
    assert "***" in cleaned
    # No credentials -> unchanged
    plain = "https://example.com/path"
    assert _sanitize_url(plain) == plain


def test_resolve_attachment_data() -> None:
    att = Attachment(content_type="image/png", data=b"\x89PNG")
    assert resolve_attachment_data(att) == b"\x89PNG"
    # Non-http URL -> None
    assert (
        resolve_attachment_data(Attachment(content_type="text/plain", url="file:///etc/passwd"))
        is None
    )
    # Empty data and no url -> None
    assert resolve_attachment_data(Attachment(content_type="text/plain")) is None


def test_resolve_attachment_data_fetch(monkeypatch) -> None:
    class _Resp:
        status_code = 200
        content = b"downloaded"

    def _fake_get(url, **kw):
        assert url == "https://cdn.example.com/a.png"
        return _Resp()

    import types

    fake = types.ModuleType("httpx")
    fake.get = _fake_get
    monkeypatch.setitem(sys.modules, "httpx", fake)
    att = Attachment(content_type="image/png", url="https://cdn.example.com/a.png")
    assert resolve_attachment_data(att) == b"downloaded"

    # Non-200 -> None
    class _BadResp:
        status_code = 404
        content = b""

    fake.get = lambda *a, **kw: _BadResp()
    assert resolve_attachment_data(att) is None

    # Network error -> None
    def _boom(*a, **kw):
        raise OSError("offline")

    fake.get = _boom
    assert resolve_attachment_data(att) is None


class _ConcreteChannel(Channel):
    channel_id = "test"

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def send(self, msg: OutboundMessage) -> None:
        self.sent = msg


def test_bind_dispatch() -> None:
    ch = _ConcreteChannel()
    with pytest.raises(RuntimeError):
        ch._dispatch(InboundMessage(channel_id="c", thread_id="t"))

    got = []

    def handler(msg: InboundMessage) -> None:
        got.append(msg)

    ch.bind_dispatcher(handler)
    msg = InboundMessage(channel_id="c", thread_id="t", content="hi")
    assert ch._dispatch(msg) is None
    assert got == [msg]


def test_edit_typing_reaction_fallbacks() -> None:
    ch = _ConcreteChannel()
    out = OutboundMessage(channel_id="c", thread_id="t", content="hi")
    # Not supported -> falls back to send
    ch.edit(out, "orig-id")
    assert ch.sent is out
    ch.send_typing("t")  # no-op
    ch.add_reaction("t", "m", "👍")  # no-op
    assert ch.health_check() is True

    class _Capable(_ConcreteChannel):
        supports_edit = True
        supports_typing = True
        supports_reactions = True

    capable = _Capable()
    with pytest.raises(NotImplementedError):
        capable.edit(out, "orig-id")
    with pytest.raises(NotImplementedError):
        capable.send_typing("t")
    with pytest.raises(NotImplementedError):
        capable.add_reaction("t", "m", "👍")


def test_handle_webhook_not_implemented() -> None:
    ch = _ConcreteChannel()
    with pytest.raises(NotImplementedError):
        ch.handle_webhook(body=b"x", headers={})


def test_safe_send_degrades_without_constitution(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "runtime.safety.validation", None)
    monkeypatch.setitem(sys.modules, "runtime.platform.process.session", None)
    ch = _ConcreteChannel()
    verdict = ch.safe_send(OutboundMessage(channel_id="c", thread_id="t", content="hello"))
    assert verdict.action == "allow"
    assert verdict.sanitized == "hello"


def test_repr() -> None:
    assert "test" in repr(_ConcreteChannel())

