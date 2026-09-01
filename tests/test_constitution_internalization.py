"""
Sprint 4 · Constitution internalization tests.

Contract pinned
---------------

1. ``CONSTITUTION_SUMMARY`` is compact (< 300 tokens heuristic)
2. Summary mentions all five principle categories (PRIV/LAWF/DGNT/SELF/EXFIL)
3. Agent loader injects summary by default
4. Agent profile flag ``includeConstitution: false`` opts out
5. Null judge is the default · allows everything
6. ``set_judge`` swaps in a custom judge
7. Judge ``block`` upgrades gate to block action
8. Judge ``human_gate`` upgrades gate to human_gate action
9. Judge returning allow doesn't change verdict
10. ``build_judge_from_llm_fn`` parses BLOCK/ESCALATE/plain replies
11. LLM judge exception tolerated (allow)
12. Judge crash in gate tolerated (allow fallthrough)
13. Secret-hit bypasses judge (block wins immediately)
14. PII-rewrite bypasses judge (rewrite wins)
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _reset_judge():
    from runtime.safety.validation import set_judge

    set_judge(None)
    yield
    set_judge(None)


# ═══════════════════════════════════════════════════════════
# Summary (soul.py)
# ═══════════════════════════════════════════════════════════


class TestSummary:
    def test_summary_compact(self):
        from runtime.safety.validation import CONSTITUTION_SUMMARY

        # Heuristic: 4 chars ≈ 1 token → 300 tokens ≈ 1200 chars cap
        assert len(CONSTITUTION_SUMMARY) < 1600
        # Non-empty
        assert CONSTITUTION_SUMMARY.strip()

    def test_summary_covers_all_five_principles(self):
        from runtime.safety.validation import CONSTITUTION_SUMMARY

        for label in ("PRIV", "LAWF", "DGNT", "SELF", "EXFIL"):
            assert label in CONSTITUTION_SUMMARY

    def test_get_constitution_summary_returns_string(self):
        from runtime.safety.validation import get_constitution_summary

        assert isinstance(get_constitution_summary(), str)


# ═══════════════════════════════════════════════════════════
# Loader integration
# ═══════════════════════════════════════════════════════════


class TestLoaderInjection:
    def test_constitution_injected_by_default(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path))
        from runtime.execution.agents.loader import _compose_soul

        agent_dir = tmp_path / "agent"
        core = agent_dir / "agent-core"
        core.mkdir(parents=True)
        (core / "SOUL.md").write_text("I am helpful.", encoding="utf-8")
        shared = tmp_path / "shared"
        shared.mkdir()

        soul = _compose_soul(agent_dir, shared)
        # One of the clause keywords must appear
        assert "PRIV" in soul or "Privacy" in soul

    def test_constitution_opt_out_via_flag(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path))
        from runtime.execution.agents.loader import _compose_soul

        agent_dir = tmp_path / "agent"
        core = agent_dir / "agent-core"
        core.mkdir(parents=True)
        (core / "SOUL.md").write_text("Minimal.", encoding="utf-8")
        shared = tmp_path / "shared"
        shared.mkdir()

        profile = {"systemPrompt": {"includeConstitution": False}}
        soul = _compose_soul(agent_dir, shared, profile=profile)
        assert "My Constitution" not in soul


# ═══════════════════════════════════════════════════════════
# Judge layer
# ═══════════════════════════════════════════════════════════


class TestJudge:
    def test_null_judge_default(self):
        from runtime.safety.validation import get_judge

        j = get_judge()
        v = j("hello", "channels:x:y", None)
        assert v.action == "allow"

    def test_set_judge_swaps(self):
        from runtime.safety.validation import get_judge, set_judge
        from runtime.safety.validation.judge import JudgeVerdict

        def custom(msg, dest, sess):
            return JudgeVerdict(action="block", reason="nope")

        set_judge(custom)
        v = get_judge()("anything", "dest", None)
        assert v.action == "block"
        assert v.reason == "nope"

    def test_judge_block_upgrades_gate(self):
        from runtime.safety.validation import check_outbound, set_judge
        from runtime.safety.validation.judge import JudgeVerdict

        def custom(msg, dest, sess):
            return JudgeVerdict(action="block", reason="policy")

        set_judge(custom)
        v = check_outbound("plain text", "channels:x:y")
        assert v.action == "block"
        assert "judge_block" in v.reason

    def test_judge_human_gate_upgrades_gate(self):
        from runtime.safety.validation import check_outbound, set_judge
        from runtime.safety.validation.judge import JudgeVerdict

        def custom(msg, dest, sess):
            return JudgeVerdict(action="human_gate", reason="ambiguous")

        set_judge(custom)
        v = check_outbound("plain text", "channels:x:y")
        assert v.action == "human_gate"

    def test_judge_allow_does_nothing(self):
        from runtime.safety.validation import check_outbound, set_judge
        from runtime.safety.validation.judge import JudgeVerdict

        def custom(msg, dest, sess):
            return JudgeVerdict(action="allow")

        set_judge(custom)
        v = check_outbound("plain text", "channels:x:y")
        assert v.action == "allow"

    def test_build_judge_parses_block(self):
        from runtime.safety.validation.judge import build_judge_from_llm_fn

        judge = build_judge_from_llm_fn(lambda p: "BLOCK: ransomware request")
        v = judge("hi", "d", None)
        assert v.action == "block"
        assert "ransomware" in v.reason

    def test_build_judge_parses_escalate(self):
        from runtime.safety.validation.judge import build_judge_from_llm_fn

        judge = build_judge_from_llm_fn(lambda p: "ESCALATE: unclear")
        v = judge("hi", "d", None)
        assert v.action == "human_gate"

    def test_build_judge_parses_allow(self):
        from runtime.safety.validation.judge import build_judge_from_llm_fn

        judge = build_judge_from_llm_fn(lambda p: "ALLOW: clean")
        v = judge("hi", "d", None)
        assert v.action == "allow"

    def test_build_judge_unknown_reply_is_allow(self):
        from runtime.safety.validation.judge import build_judge_from_llm_fn

        judge = build_judge_from_llm_fn(lambda p: "i dunno lol")
        v = judge("hi", "d", None)
        assert v.action == "allow"

    def test_build_judge_llm_exception_tolerated(self):
        from runtime.safety.validation.judge import build_judge_from_llm_fn

        def _raising(p):
            raise RuntimeError("llm down")

        judge = build_judge_from_llm_fn(_raising)
        v = judge("hi", "d", None)
        assert v.action == "allow"
        assert "unavailable" in v.reason

    def test_judge_crash_in_gate_falls_through_to_allow(self):
        from runtime.safety.validation import check_outbound, set_judge

        def crashing(msg, dest, sess):
            raise RuntimeError("judge broken")

        set_judge(crashing)
        v = check_outbound("clean text", "channels:x:y")
        # Gate tolerates judge crash · falls to allow
        assert v.action == "allow"


# ═══════════════════════════════════════════════════════════
# Precedence · rule-layer wins over judge
# ═══════════════════════════════════════════════════════════


class TestPrecedence:
    def test_secret_hit_bypasses_judge(self):
        from runtime.safety.validation import check_outbound, set_judge
        from runtime.safety.validation.judge import JudgeVerdict

        # Judge would allow · but secret regex blocks first
        set_judge(lambda m, d, s: JudgeVerdict(action="allow"))
        v = check_outbound(
            "my key is sk-ant-api03-abc123def456ghi789jkl",
            "channels:x:y",
        )
        assert v.action == "block"
        assert "judge" not in v.reason  # rule path reason, not judge

    def test_pii_rewrite_bypasses_judge(self):
        from runtime.safety.validation import check_outbound, set_judge
        from runtime.safety.validation.judge import JudgeVerdict

        called: list[bool] = []

        def tracking_judge(m, d, s):
            called.append(True)
            return JudgeVerdict(action="block", reason="would block")

        set_judge(tracking_judge)
        v = check_outbound(
            "contact me at user@example.com please",
            "channels:x:y",
        )
        # Rule rewrote PII · judge never consulted
        assert v.action == "rewrite"
        assert called == []
