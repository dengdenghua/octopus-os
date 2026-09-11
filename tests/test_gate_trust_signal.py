"""Tests for the trust-signal escalation in gate.check_outbound.

Verifies the contract:

1. ``enable_trust_signal=False`` (default) — legacy behaviour
   unchanged. No trust check, no escalation, no extra log.
2. Suspect trust + clean message + non-owner destination →
   ``human_gate`` with reason tagged ``trust_signal_escalate``.
3. Trust signal NEVER relaxes — block / rewrite outcomes stay.
4. Owner destinations exempt — owner sees their own data.
5. Trust scoring failure degrades to no-op (allow), not block.
"""

from __future__ import annotations

import pytest

from runtime.safety.validation import gate

# ══════════════════════════════════════════════════════════════════
# Defaults — opt-in flag preserves legacy behaviour
# ══════════════════════════════════════════════════════════════════


class TestTrustSignalDisabledByDefault:
    def test_clean_message_default_allows(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Even if trust would say suspect, default off → no escalation.
        monkeypatch.setattr(
            "runtime.safety.validation.gate.fetch_current_trust_score",
            lambda: 0.0,
            raising=False,
        )
        v = gate.check_outbound("hello world", destination="channels:slack:c1")
        assert v.action == "allow"

    def test_no_kwarg_means_default_off(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Belt-and-braces — the kwarg now defaults to None (tri-state)
        # which resolves to off when no env / yaml says otherwise.
        import inspect

        sig = inspect.signature(gate.check_outbound)
        assert sig.parameters["enable_trust_signal"].default is None


# ══════════════════════════════════════════════════════════════════
# enable_trust_signal=True paths
# ══════════════════════════════════════════════════════════════════


class TestSuspectTrustEscalates:
    def test_suspect_clean_message_becomes_human_gate(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from runtime.safety.validation import trust_signal

        monkeypatch.setattr(trust_signal, "fetch_current_trust_score", lambda **_: 0.05)
        v = gate.check_outbound(
            "Hello team, please review the docs.",
            destination="channels:slack:c1",
            enable_trust_signal=True,
        )
        assert v.action == "human_gate"
        assert "trust_signal_escalate" in v.reason
        assert "0.05" in v.reason

    def test_neutral_trust_does_not_escalate(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from runtime.safety.validation import trust_signal

        monkeypatch.setattr(trust_signal, "fetch_current_trust_score", lambda **_: 0.5)
        v = gate.check_outbound(
            "Hello team",
            destination="channels:slack:c1",
            enable_trust_signal=True,
        )
        assert v.action == "allow"

    def test_trusted_score_does_not_escalate(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from runtime.safety.validation import trust_signal

        monkeypatch.setattr(trust_signal, "fetch_current_trust_score", lambda **_: 0.95)
        v = gate.check_outbound(
            "Hello team",
            destination="channels:slack:c1",
            enable_trust_signal=True,
        )
        assert v.action == "allow"

    def test_owner_destination_exempt(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from runtime.safety.validation import trust_signal

        monkeypatch.setattr(trust_signal, "fetch_current_trust_score", lambda **_: 0.05)
        # Even with suspect trust, owner-destined messages don't
        # need human review (it's the owner's own surface).
        v = gate.check_outbound(
            "personal log line",
            destination="owner",
            enable_trust_signal=True,
        )
        assert v.action == "allow"
        assert "trust_signal_escalate" not in v.reason


# ══════════════════════════════════════════════════════════════════
# Hard-floor invariants — trust never relaxes
# ══════════════════════════════════════════════════════════════════


class TestTrustNeverRelaxes:
    def test_secret_still_blocks_when_trust_high(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Even if trust is perfect, a secret in the payload still blocks.
        from runtime.safety.validation import trust_signal

        monkeypatch.setattr(trust_signal, "fetch_current_trust_score", lambda **_: 1.0)
        v = gate.check_outbound(
            "API_KEY=sk-abcdefghijklmnopqrstuvwxyz1234567890",
            destination="channels:slack:c1",
            enable_trust_signal=True,
        )
        # Don't depend on exact rule wording — just that hard floor held.
        assert v.action == "block"

    def test_pii_rewrite_unchanged_when_trust_high(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # PII path should still rewrite (not allow) regardless of trust.
        from runtime.safety.validation import trust_signal

        monkeypatch.setattr(trust_signal, "fetch_current_trust_score", lambda **_: 1.0)
        v = gate.check_outbound(
            "Contact me at alice.smith@example.com",
            destination="channels:slack:c1",
            enable_trust_signal=True,
        )
        # Either rewrite (strict profile) or allow with audit (lax).
        # Both are correct; the point is trust didn't FORCE a block.
        assert v.action in ("allow", "rewrite")


# ══════════════════════════════════════════════════════════════════
# Failure modes — trust never crashes the gate
# ══════════════════════════════════════════════════════════════════


class TestTrustSignalFailureSafe:
    def test_trust_fetch_exception_falls_through_to_allow(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # If fetch_current_trust_score raises, gate must NOT crash —
        # it falls through to the existing allow.
        from runtime.safety.validation import trust_signal

        def boom(**_):
            raise RuntimeError("telemetry exploded")

        monkeypatch.setattr(trust_signal, "fetch_current_trust_score", boom)
        v = gate.check_outbound(
            "hello",
            destination="channels:slack:c1",
            enable_trust_signal=True,
        )
        assert v.action == "allow"

    def test_classify_returns_none_falls_through(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Defensive: if classify returns something non-suspect-shaped,
        # gate stays clean.
        from runtime.safety.validation import trust_signal

        monkeypatch.setattr(trust_signal, "fetch_current_trust_score", lambda **_: 0.5)
        v = gate.check_outbound(
            "hello",
            destination="channels:slack:c1",
            enable_trust_signal=True,
        )
        assert v.action == "allow"
