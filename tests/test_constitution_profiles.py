"""
Tests for constitution profile system · strict / normal / lax.

Contract pinned
---------------

1. Default profile is ``strict``
2. ``set_profile`` accepts strict/normal/lax · rejects others
3. strict · PII rewritten · judge block authoritative
4. normal · PII rewritten · judge block downgrades to allow + audit reason
5. lax · PII allowed · judge block downgrades to allow + audit reason
6. Secrets always block (even in lax · hard floor)
7. Owner destination bypasses PII in all profiles (already contracted)
8. Audit reasons carry the original judgment for log pipelines
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


class TestProfileBasics:
    def test_default_strict(self):
        from runtime.safety.validation import get_profile

        assert get_profile() == "strict"

    def test_set_valid(self):
        from runtime.safety.validation import get_profile, set_profile

        for p in ("strict", "normal", "lax"):
            set_profile(p)
            assert get_profile() == p

    def test_set_invalid_raises(self):
        from runtime.safety.validation import set_profile

        with pytest.raises(ValueError):
            set_profile("paranoid")  # type: ignore[arg-type]


class TestPIIBehavior:
    PII_MSG = "contact me at user@example.com please"

    def test_strict_rewrites(self):
        from runtime.safety.validation import check_outbound, set_profile

        set_profile("strict")
        v = check_outbound(self.PII_MSG, "channels:x:y")
        assert v.action == "rewrite"
        assert "user@example.com" not in v.sanitized_text

    def test_normal_rewrites(self):
        from runtime.safety.validation import check_outbound, set_profile

        set_profile("normal")
        v = check_outbound(self.PII_MSG, "channels:x:y")
        assert v.action == "rewrite"

    def test_lax_allows_with_audit(self):
        from runtime.safety.validation import check_outbound, set_profile

        set_profile("lax")
        v = check_outbound(self.PII_MSG, "channels:x:y")
        assert v.action == "allow"
        # PII still recorded in violations for audit
        assert len(v.violations) >= 1
        assert "audit_only" in v.reason
        # Text UNCHANGED · lax = allow passthrough
        assert v.sanitized_text == self.PII_MSG


class TestJudgeBehavior:
    def _install_blocking_judge(self):
        from runtime.safety.validation import set_judge
        from runtime.safety.validation.judge import JudgeVerdict

        set_judge(
            lambda m, d, s: JudgeVerdict(action="block", reason="policy"),
        )

    def _install_escalating_judge(self):
        from runtime.safety.validation import set_judge
        from runtime.safety.validation.judge import JudgeVerdict

        set_judge(
            lambda m, d, s: JudgeVerdict(
                action="human_gate",
                reason="ambiguous",
            ),
        )

    def test_strict_judge_block_authoritative(self):
        from runtime.safety.validation import check_outbound, set_profile

        self._install_blocking_judge()
        set_profile("strict")
        v = check_outbound("clean text", "channels:x:y")
        assert v.action == "block"
        assert "judge_block" in v.reason

    def test_normal_judge_block_downgrades(self):
        from runtime.safety.validation import check_outbound, set_profile

        self._install_blocking_judge()
        set_profile("normal")
        v = check_outbound("clean text", "channels:x:y")
        assert v.action == "allow"
        assert "audit_only_judge_block" in v.reason
        assert "policy" in v.reason

    def test_lax_judge_block_downgrades(self):
        from runtime.safety.validation import check_outbound, set_profile

        self._install_blocking_judge()
        set_profile("lax")
        v = check_outbound("clean text", "channels:x:y")
        assert v.action == "allow"
        assert "audit_only_judge_block" in v.reason

    def test_strict_judge_escalate_authoritative(self):
        from runtime.safety.validation import check_outbound, set_profile

        self._install_escalating_judge()
        set_profile("strict")
        v = check_outbound("clean text", "channels:x:y")
        assert v.action == "human_gate"

    def test_normal_judge_escalate_downgrades(self):
        from runtime.safety.validation import check_outbound, set_profile

        self._install_escalating_judge()
        set_profile("normal")
        v = check_outbound("clean text", "channels:x:y")
        assert v.action == "allow"
        assert "audit_only_judge_escalate" in v.reason


class TestHardFloor:
    def test_secrets_block_in_lax(self):
        """Lax profile still blocks credential leaks · hard floor."""
        from runtime.safety.validation import check_outbound, set_profile

        set_profile("lax")
        v = check_outbound(
            "my key is sk-ant-api03-abcdefghijklmnop",
            "channels:x:y",
        )
        assert v.action == "block"

    def test_secrets_block_in_normal(self):
        from runtime.safety.validation import check_outbound, set_profile

        set_profile("normal")
        v = check_outbound(
            "export OPENAI_KEY=sk-abcdef1234567890abcdef1234",
            "channels:x:y",
        )
        assert v.action == "block"


class TestOwnerBypass:
    def test_owner_bypass_all_profiles(self):
        """Owner destination already bypasses PII rewrite · verify
        that contract still holds across profiles."""
        from runtime.safety.validation import check_outbound, set_profile

        for p in ("strict", "normal", "lax"):
            set_profile(p)
            v = check_outbound(
                "mail me user@example.com",
                "owner:ide",
            )
            assert v.action == "allow", f"profile={p}"
