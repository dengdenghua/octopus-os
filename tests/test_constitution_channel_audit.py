"""
Tests for the constitution-gate audit at channel registration.

When a third-party author writes their own ``Channel`` subclass
and the ``send()`` implementation doesn't call ``safe_send()``,
``ChannelManager.register()`` emits a loud stderr warning.

These tests pin:

1. Audit WARNS when ``send()`` bypasses the gate · so community
   developers get a visible signal.
2. Audit STAYS QUIET when ``send()`` calls ``self.safe_send()``
   (the recommended template).
3. ``safe_send()`` itself returns a verdict with the right
   action/sanitized fields · adapters consume that structurally.
4. Registration succeeds in both cases — audit is advisory,
   never blocks. A noisy third-party author can still run; the
   warning is their signal to fix it.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from runtime.adapters.channels.base import (
    Channel,
    OutboundMessage,
)
from runtime.adapters.channels.manager import ChannelManager

# ═══════════════════════════════════════════════════════════
# Test adapter classes · mock third-party implementations
# ═══════════════════════════════════════════════════════════


class _GateBypassingAdapter(Channel):
    """Represents a community adapter whose author didn't read
    the CONSTITUTION doc · send() goes straight to the platform."""

    channel_id = "test-bypass"

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def send(self, msg: OutboundMessage) -> None:
        # DELIBERATELY omits self.safe_send — that's what we audit
        # against. Real adapter would call requests.post(...) here.
        self.last_sent = msg.content


class _GatedAdapter(Channel):
    """Represents a correctly-written adapter · calls safe_send
    before the platform call."""

    channel_id = "test-gated"

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def send(self, msg: OutboundMessage) -> None:
        verdict = self.safe_send(msg)
        if verdict.action == "block":
            self.last_sent = None
            return
        self.last_sent = verdict.sanitized


class _CheckOutboundAdapter(Channel):
    """Adapter that calls ``check_outbound`` directly instead of
    ``self.safe_send`` · audit should still pass (marker present)."""

    channel_id = "test-direct"

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def send(self, msg: OutboundMessage) -> None:
        from runtime.safety.validation import check_outbound

        verdict = check_outbound(
            message=msg.content,
            destination=f"channels:{self.channel_id}:{msg.thread_id}",
        )
        if verdict.action == "block":
            return
        self.last_sent = verdict.sanitized_text


# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════


class _FakeStack:
    """Minimum viable BuiltStack stub for ChannelManager init ·
    just needs ``.planner`` and ``.runtime`` attributes present ·
    we never exercise them because the tests only drive
    registration, not actual inbound dispatch."""

    planner = None
    runtime = None


class _FakeAgentRegistry:
    def pick_for_intent(self, *_a: Any, **_kw: Any) -> None:
        return None


@pytest.fixture
def manager() -> ChannelManager:
    return ChannelManager(
        stack=_FakeStack(),
        agent_registry=_FakeAgentRegistry(),
        default_agent_id="test-agent",
    )


# ═══════════════════════════════════════════════════════════
# 1. Audit warns on bypass
# ═══════════════════════════════════════════════════════════


class TestAuditWarns:
    def test_bypassing_adapter_logs_warning(
        self,
        manager: ChannelManager,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.WARNING):
            manager.register(_GateBypassingAdapter())
        # Warning emitted · mentions constitution.md for discoverability
        warnings = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("constitution" in m.lower() for m in warnings), (
            f"no constitution warning · got: {warnings}"
        )
        assert any("_GateBypassingAdapter" in m for m in warnings), (
            f"warning doesn't name the adapter · got: {warnings}"
        )

    def test_bypass_registration_still_succeeds(
        self,
        manager: ChannelManager,
    ) -> None:
        """Audit is advisory · community author gets a warning but
        the channel still registers. Prevents the audit itself from
        being a breaking change for existing adapters."""
        manager.register(_GateBypassingAdapter())
        assert manager.has("test-bypass")


# ═══════════════════════════════════════════════════════════
# 2. Audit quiet on compliant adapters
# ═══════════════════════════════════════════════════════════


class TestAuditQuiet:
    def test_safe_send_adapter_no_warning(
        self,
        manager: ChannelManager,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.WARNING):
            manager.register(_GatedAdapter())
        warnings = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert not warnings, f"safe_send adapter should not warn · got: {warnings}"

    def test_direct_check_outbound_also_accepted(
        self,
        manager: ChannelManager,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.WARNING):
            manager.register(_CheckOutboundAdapter())
        warnings = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert not warnings


# ═══════════════════════════════════════════════════════════
# 3. safe_send returns usable verdict
# ═══════════════════════════════════════════════════════════


class TestSafeSendVerdict:
    def test_clean_message_allowed_through(
        self,
        manager: ChannelManager,
    ) -> None:
        ch = _GatedAdapter()
        manager.register(ch)
        ch.send(
            OutboundMessage(
                channel_id="test-gated",
                thread_id="t1",
                content="hello world",
            )
        )
        assert ch.last_sent == "hello world"

    def test_pii_rewritten_to_sanitized(
        self,
        manager: ChannelManager,
    ) -> None:
        ch = _GatedAdapter()
        manager.register(ch)
        ch.send(
            OutboundMessage(
                channel_id="test-gated",
                thread_id="t1",
                content="ping alice@example.com about it",
            )
        )
        assert ch.last_sent is not None
        assert "alice@example.com" not in ch.last_sent
        assert "[REDACTED:email]" in ch.last_sent

    def test_secret_blocked_not_sent(
        self,
        manager: ChannelManager,
    ) -> None:
        ch = _GatedAdapter()
        manager.register(ch)
        ch.send(
            OutboundMessage(
                channel_id="test-gated",
                thread_id="t1",
                content="token is sk-abc123def456ghi789jkl012mno345",
            )
        )
        # block → gated adapter's send() set last_sent to None
        assert ch.last_sent is None


# ═══════════════════════════════════════════════════════════
# 4. Audit survives un-inspectable adapters
# ═══════════════════════════════════════════════════════════


class TestAuditRobustness:
    def test_adapter_with_c_extension_send_does_not_crash(
        self,
        manager: ChannelManager,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If ``inspect.getsource`` raises AND the file-read fallback
        also fails (C-level method, dynamic class, etc.) · audit still
        warns · doesn't crash registration."""
        import inspect

        def _fail_getsource(obj: Any) -> str:
            raise OSError("source not available")

        monkeypatch.setattr(inspect, "getsource", _fail_getsource)

        # The audit also tries to fall back to reading the module's
        # __file__ directly (handles editable-install / linecache
        # mismatches). Simulate a truly opaque adapter by also
        # blanking the module's __file__ so that fallback fires
        # the warning path.
        import sys as _sys

        adapter_mod = _sys.modules.get(_GatedAdapter.__module__)
        if adapter_mod is not None:
            monkeypatch.setattr(adapter_mod, "__file__", "/nonexistent", raising=False)

        with caplog.at_level(logging.WARNING):
            manager.register(_GatedAdapter())
        warnings = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("not inspectable" in m for m in warnings), (
            f"should warn about uninspectable adapter · got: {warnings}"
        )
