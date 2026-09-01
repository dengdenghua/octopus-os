"""
Per-agent / per-call clause overrides for the constitution gate.

Contract pinned
---------------

1. ``overrides={"PRIV-2": "journal"}`` · PII hits on PRIV-2 downgrade
   to audit-only allow · message text NOT rewritten
2. Mixed clauses · only PRIV-2 overridden but another clause hits →
   falls through to normal rewrite (override doesn't shield non-PRIV-2)
3. ``overrides={"PRIV-2": "block"}`` · PII hit upgrades to block
   even under strict profile (which would normally rewrite)
4. Secret ``PRIV-4`` + ``"journal"`` override → STILL blocks (hard
   floor · secrets cannot be journal-ed) · warning logged
5. Session agent carries overrides via ``agent.capabilities
   .constitution_overrides`` · explicit kwarg wins on collision
6. Unknown override values silently dropped (can't typo-weaken)
7. Overrides don't affect clean messages (no-op)
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset():
    from runtime.safety.validation import (
        reset_profile_for_tests,
        set_judge,
    )

    reset_profile_for_tests()
    set_judge(None)
    yield
    reset_profile_for_tests()
    set_judge(None)


class _StubAgent:
    def __init__(self, overrides=None):
        self.agent_id = "test-agent"
        self.capabilities = {}
        if overrides is not None:
            self.capabilities["constitution_overrides"] = overrides


def _session_with_agent(overrides):
    from runtime.platform.process.session import Session

    return Session(
        actor="u",
        agent=_StubAgent(overrides),
        thread_id="t-override",
        metadata={},
    )


# ═══════════════════════════════════════════════════════════
# PII · journal override
# ═══════════════════════════════════════════════════════════


class TestPIIJournal:
    MSG = "reach me at user@example.com please"

    def test_journal_override_allows_unchanged(self):
        from runtime.safety.validation import check_outbound

        v = check_outbound(
            self.MSG,
            "channels:x:y",
            overrides={"PRIV-2": "journal"},
        )
        assert v.action == "allow"
        assert v.sanitized_text == self.MSG
        assert "audit_only_override" in v.reason
        # Violations still recorded for audit
        assert len(v.violations) >= 1

    def test_agent_level_override_honored(self):
        from runtime.safety.validation import check_outbound

        sess = _session_with_agent({"PRIV-2": "journal"})
        v = check_outbound(self.MSG, "channels:x:y", session=sess)
        assert v.action == "allow"
        assert v.sanitized_text == self.MSG

    def test_explicit_overrides_merge_with_agent(self):
        """Per-call kwarg wins on same clause · supplements on new ones."""
        from runtime.safety.validation import check_outbound

        sess = _session_with_agent({"PRIV-2": "journal"})
        # Caller flips PRIV-2 to block · overrides session's journal
        v = check_outbound(
            self.MSG,
            "channels:x:y",
            session=sess,
            overrides={"PRIV-2": "block"},
        )
        assert v.action == "block"


# ═══════════════════════════════════════════════════════════
# PII · block override (upgrade strict/normal's rewrite to block)
# ═══════════════════════════════════════════════════════════


class TestPIIBlockUpgrade:
    def test_block_override_forces_block(self):
        from runtime.safety.validation import check_outbound

        v = check_outbound(
            "call me at user@example.com now",
            "channels:x:y",
            overrides={"PRIV-2": "block"},
        )
        assert v.action == "block"
        assert "override block" in v.reason

    def test_block_override_beats_lax_profile(self):
        """Lax profile would allow+audit · block override upgrades."""
        from runtime.safety.validation import check_outbound, set_profile

        set_profile("lax")
        v = check_outbound(
            "email user@example.com",
            "channels:x:y",
            overrides={"PRIV-2": "block"},
        )
        assert v.action == "block"


# ═══════════════════════════════════════════════════════════
# Secret · journal override IGNORED (hard floor)
# ═══════════════════════════════════════════════════════════


class TestSecretHardFloor:
    def test_journal_on_secret_still_blocks(self, caplog):
        import logging

        from runtime.safety.validation import check_outbound

        caplog.set_level(logging.WARNING)

        v = check_outbound(
            "key is sk-ant-abc123def456ghi789jkl",
            "channels:x:y",
            overrides={"PRIV-4": "journal"},
        )
        assert v.action == "block"
        assert any("IGNORED" in r.message and "secret" in r.message for r in caplog.records)


# ═══════════════════════════════════════════════════════════
# Mixed-clause semantics
# ═══════════════════════════════════════════════════════════


class TestMixedClauses:
    def test_unknown_override_value_dropped(self):
        """Typo-like values ({"PRIV-2": "ignore"}) must not sneak through."""
        from runtime.safety.validation import check_outbound

        v = check_outbound(
            "email user@example.com",
            "channels:x:y",
            overrides={"PRIV-2": "ignore"},  # type: ignore[dict-item]
        )
        # Should behave as if no override · strict profile rewrites
        assert v.action == "rewrite"


# ═══════════════════════════════════════════════════════════
# No-op cases
# ═══════════════════════════════════════════════════════════


class TestNoOp:
    def test_clean_message_unaffected_by_override(self):
        from runtime.safety.validation import check_outbound

        v = check_outbound(
            "nothing sensitive here at all",
            "channels:x:y",
            overrides={"PRIV-2": "block"},  # no hit · doesn't matter
        )
        assert v.action == "allow"
