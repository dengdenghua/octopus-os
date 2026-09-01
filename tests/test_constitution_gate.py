"""
Red-team tests for the constitution gate.

These pin the **minimum viable gate behavior** — if any of these
flips from passing to failing, the gate has lost its core value
and any ``channels/*`` adapter depending on it is exposed.

Covered contracts
-----------------

1. **PII in outbound text gets scrubbed** · email / phone / CN-ID
   in a message headed to an external channel come back replaced
   by ``[REDACTED:*]`` placeholders.
2. **Secret patterns BLOCK, not Rewrite** · An API key in outbound
   text is never sanitized and sent · the whole message is blocked.
3. **Owner destinations bypass PII Rewrite** · The user seeing
   their own email in their own IDE isn't a leak.
4. **Owner destinations do NOT bypass Secret Block** · Even the
   owner's own log shouldn't accumulate raw API keys · once the
   log is screenshot/pasted the secret is gone.
5. **Gate never raises** · Whatever input · verdict comes back
   as a structured value.

The gate is the seam between "LLM just said something" and "it's
about to go on a wire". Blocking correctness here is mouth-of-dam
important — everything downstream trusts the verdict.
"""

from __future__ import annotations

from runtime.safety.validation import Verdict, check_outbound
from runtime.safety.validation.rules import (
    scan_pii,
    scan_secrets,
    scrub_pii,
)

# ═══════════════════════════════════════════════════════════
# 1. PII scrubbing
# ═══════════════════════════════════════════════════════════


class TestPIIScrub:
    def test_email_scrubbed_to_external_destination(self):
        verdict = check_outbound(
            message="Contact me at alice@example.com for details",
            destination="channels:discord:ch-1",
        )
        assert verdict.action == "rewrite"
        assert "alice@example.com" not in verdict.sanitized_text
        assert "[REDACTED:email]" in verdict.sanitized_text
        assert len(verdict.violations) >= 1
        assert verdict.violations[0].clause_id == "PRIV-2"

    def test_cn_mobile_scrubbed(self):
        verdict = check_outbound(
            message="Call back on 13812345678 after 3pm.",
            destination="channels:feishu:xyz",
        )
        assert verdict.action == "rewrite"
        assert "13812345678" not in verdict.sanitized_text
        assert "[REDACTED:phone]" in verdict.sanitized_text

    def test_multiple_pii_in_one_message(self):
        verdict = check_outbound(
            message="Alice: alice@example.com / 13812345678",
            destination="channels:slack:u-alice",
        )
        assert verdict.action == "rewrite"
        assert "alice@example.com" not in verdict.sanitized_text
        assert "13812345678" not in verdict.sanitized_text
        assert len(verdict.violations) == 2

    def test_clean_message_allowed_unchanged(self):
        msg = "Hello from the agent · here is a summary of today's tasks."
        verdict = check_outbound(
            message=msg,
            destination="channels:slack:chan",
        )
        assert verdict.action == "allow"
        assert verdict.sanitized_text == msg
        assert verdict.violations == []


# ═══════════════════════════════════════════════════════════
# 2. Secret BLOCK (not Rewrite)
# ═══════════════════════════════════════════════════════════


class TestSecretBlock:
    def test_openai_api_key_blocked(self):
        """``sk-`` + 20+ chars → BLOCK the whole send."""
        # Synthetic fake key · matches the pattern but not real.
        verdict = check_outbound(
            message="Here's the config: sk-abc123def456ghi789jkl012mno345",
            destination="channels:discord:ch-2",
        )
        assert verdict.action == "block"
        assert verdict.sanitized_text == ""
        assert any(v.clause_id == "PRIV-4" for v in verdict.violations)

    def test_anthropic_api_key_blocked(self):
        verdict = check_outbound(
            message=("ANTHROPIC_API_KEY=sk-ant-abcdefghijklmnopqrstuvwxyz0123456789abcd"),
            destination="channels:weixin:bot-1",
        )
        assert verdict.action == "block"

    def test_aws_access_key_blocked(self):
        verdict = check_outbound(
            message="aws_access_key_id: AKIAIOSFODNN7EXAMPLE",
            destination="channels:telegram:grp",
        )
        assert verdict.action == "block"

    def test_github_pat_blocked(self):
        verdict = check_outbound(
            message="token = 'ghp_" + "X" * 36 + "'",
            destination="channels:discord:ch-3",
        )
        assert verdict.action == "block"

    def test_private_key_material_blocked(self):
        verdict = check_outbound(
            message="-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA...",
            destination="channels:slack:general",
        )
        assert verdict.action == "block"

    def test_secret_in_journal_hash_not_plaintext(self):
        """Verdict carries a HASH of the original · never the raw
        text. Secret leak via journal logging is a real threat ·
        this pin ensures the verdict itself can be safely
        recorded."""
        verdict = check_outbound(
            message="sk-secretthatshouldNeverBeLoggedIn_j0urnal_abcdef",
            destination="channels:discord:ch-1",
        )
        # Short snippet allowed (starts with first 6 chars) · full
        # secret not exposed.
        for hit in verdict.violations:
            assert "secretthat" not in hit.matched_text_short  # middle gone
            assert hit.matched_text_short.endswith(")")  # len suffix


# ═══════════════════════════════════════════════════════════
# 3. Owner destinations bypass PII rewrite
# ═══════════════════════════════════════════════════════════


class TestOwnerBypass:
    def test_owner_destination_keeps_pii(self):
        """Owner seeing their own email in their own IDE · not
        a leak · don't molest the text."""
        msg = "Your email is alice@example.com"
        verdict = check_outbound(message=msg, destination="owner")
        assert verdict.action == "allow"
        assert verdict.sanitized_text == msg

    def test_owner_prefixed_destination_also_bypasses(self):
        verdict = check_outbound(
            message="Phone 13812345678",
            destination="owner:ide",
        )
        assert verdict.action == "allow"


# ═══════════════════════════════════════════════════════════
# 4. Owner destinations do NOT bypass secret block
# ═══════════════════════════════════════════════════════════


class TestOwnerDoesNotBypassSecrets:
    def test_secret_still_blocked_to_owner(self):
        """Even to owner's own surface · API keys are not allowed
        to flow · because owner MIGHT paste that surface elsewhere
        later. Defense-in-depth."""
        verdict = check_outbound(
            message="Your key: sk-abc123def456ghi789jkl012mno345",
            destination="owner",
        )
        assert verdict.action == "block"


# ═══════════════════════════════════════════════════════════
# 5. Gate never raises
# ═══════════════════════════════════════════════════════════


class TestGateNeverRaises:
    def test_empty_message(self):
        verdict = check_outbound(message="", destination="channels:x:y")
        assert verdict.action == "allow"

    def test_very_long_message(self):
        verdict = check_outbound(
            message="x" * 100_000,
            destination="channels:x:y",
        )
        assert isinstance(verdict, Verdict)

    def test_non_ascii_message(self):
        verdict = check_outbound(
            message="你好 · 发送这条消息",
            destination="channels:discord:ch-1",
        )
        assert verdict.action == "allow"

    def test_message_with_newlines_and_unicode_pii(self):
        verdict = check_outbound(
            message="姓名: 王小明\n邮箱: xiaoming@example.com\n电话: 13912345678",
            destination="channels:weixin:g",
        )
        assert verdict.action == "rewrite"
        # Both email + phone scrubbed
        assert "xiaoming@example.com" not in verdict.sanitized_text
        assert "13912345678" not in verdict.sanitized_text


# ═══════════════════════════════════════════════════════════
# Pure-function rules (unit)
# ═══════════════════════════════════════════════════════════


class TestRulesUnit:
    def test_scan_pii_finds_email(self):
        hits = scan_pii("ping alice@example.com please")
        assert len(hits) == 1
        assert hits[0].category == "pii"

    def test_scan_pii_empty_for_clean_text(self):
        assert scan_pii("hello world · no identifiers here") == []

    def test_scrub_pii_idempotent_on_clean_text(self):
        text = "no pii here"
        out, hits = scrub_pii(text)
        assert out == text
        assert hits == []

    def test_scrub_pii_replaces_all_instances(self):
        out, hits = scrub_pii("a@a.com b@b.com")
        assert "@" not in out.replace("[REDACTED:email]", "")
        assert len(hits) == 2

    def test_scan_secrets_finds_openai_key(self):
        hits = scan_secrets("key=sk-abcdefghijklmnopqrstuvwxyz0123456789")
        assert len(hits) >= 1
        assert hits[0].category == "secret"

    def test_scan_secrets_does_not_match_short_sk_token(self):
        """``sk-abc`` (too short) shouldn't count · would false-
        positive on innocent strings like ``sk-1.23`` (a version)."""
        hits = scan_secrets("build sk-v1 for release")
        assert hits == []
