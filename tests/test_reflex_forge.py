"""Tests for ReflexForge and EvolutionRouter."""

from __future__ import annotations

from unittest.mock import MagicMock

from runtime.safety.recovery.evolution_router import EvolutionRouter
from runtime.safety.recovery.reflex_forge import (
    ForgedReflexCandidate,
    ReflexForge,
    ReflexForgeConfig,
)

# ═══════════════════════════════════════════════════════════
# EvolutionRouter tests
# ═══════════════════════════════════════════════════════════


class TestEvolutionRouter:
    def test_pure_text_short_prompt_routes_to_reflex(self):
        router = EvolutionRouter()
        verdict = router.classify_candidate(
            prompt="你好",
            reply="你好！有什么可以帮你的吗？",
        )
        assert verdict.path == "reflex"
        assert verdict.confidence >= 0.8

    def test_tool_calls_route_to_skill(self):
        router = EvolutionRouter()
        verdict = router.classify_candidate(
            prompt="读取文件",
            reply="文件内容是...",
            has_tool_calls=True,
        )
        assert verdict.path == "skill"
        assert "tools" in verdict.reason

    def test_code_changes_route_to_skill(self):
        router = EvolutionRouter()
        verdict = router.classify_candidate(
            prompt="修改代码",
            reply="已修改",
            has_code_changes=True,
        )
        assert verdict.path == "skill"

    def test_multi_step_routes_to_skill(self):
        router = EvolutionRouter()
        verdict = router.classify_candidate(
            prompt="分析数据",
            reply="分析完成",
            step_count=3,
        )
        assert verdict.path == "skill"

    def test_tool_keyword_in_prompt_routes_to_skill(self):
        router = EvolutionRouter()
        verdict = router.classify_candidate(
            prompt="请搜索最新的新闻",
            reply="这是搜索结果",
        )
        assert verdict.path == "skill"
        assert "tool-dependent" in verdict.reason

    def test_dynamic_reply_markers_route_to_skill(self):
        router = EvolutionRouter()
        verdict = router.classify_candidate(
            prompt="查看这个文件",
            reply="文件路径是 /Users/test/file.txt",
        )
        assert verdict.path == "skill"
        assert "dynamic" in verdict.reason

    def test_code_block_in_reply_routes_to_skill(self):
        router = EvolutionRouter()
        verdict = router.classify_candidate(
            prompt="写个函数",
            reply="```python\ndef foo():\n    pass\n```",
        )
        assert verdict.path == "skill"

    def test_classify_batch_groups_correctly(self):
        router = EvolutionRouter()
        candidates = [
            {"prompt": "你好", "reply": "你好！"},
            {"prompt": "读取文件", "reply": "内容", "has_tool_calls": True},
            {"prompt": "今天天气", "reply": "天气不错"},
        ]
        grouped = router.classify_batch(candidates)
        assert len(grouped["reflex"]) == 2
        assert len(grouped["skill"]) == 1
        assert grouped["reflex"][0]["evolution_verdict"]["path"] == "reflex"


# ═══════════════════════════════════════════════════════════
# ReflexForge tests
# ═══════════════════════════════════════════════════════════


def _make_mock_fuzzy_cache(pairs: list[dict[str, str]]) -> MagicMock:
    """Build a mock FuzzyCacheTier with the given (prompt, reply) pairs."""
    fc = MagicMock()
    fc._lock = MagicMock()
    fc._store = {
        f"key_{i}": {"prompt": p["prompt"], "reply": p["reply"], "ts": 0.0}
        for i, p in enumerate(pairs)
    }
    return fc


class TestReflexForge:
    def test_propose_empty_cache_returns_empty(self):
        fc = _make_mock_fuzzy_cache([])
        forge = ReflexForge(fuzzy_cache=fc)
        assert forge.propose() == []

    def test_propose_clusters_similar_prompts(self):
        fc = _make_mock_fuzzy_cache(
            [
                {"prompt": "你好", "reply": "你好！"},
                {"prompt": "你好", "reply": "你好！"},
                {"prompt": "你好", "reply": "你好！"},
            ]
        )
        forge = ReflexForge(fuzzy_cache=fc)
        candidates = forge.propose()
        assert len(candidates) == 1
        assert candidates[0].sample_count == 3
        assert candidates[0].reply == "你好！"
        assert candidates[0].reply_consistency == 1.0

    def test_propose_filters_low_consistency(self):
        """When replies vary too much, the cluster is dropped."""
        fc = _make_mock_fuzzy_cache(
            [
                {"prompt": "你好", "reply": "回复A"},
                {"prompt": "你好", "reply": "回复B"},
                {"prompt": "你好", "reply": "回复C"},
            ]
        )
        forge = ReflexForge(
            fuzzy_cache=fc,
            config=ReflexForgeConfig(min_hits=3, reply_consistency_threshold=0.6),
        )
        candidates = forge.propose()
        # 3 different replies → top reply consistency = 1/3 ≈ 0.33 < 0.6
        assert candidates == []

    def test_propose_respects_min_hits(self):
        fc = _make_mock_fuzzy_cache(
            [
                {"prompt": "你好", "reply": "你好！"},
                {"prompt": "你好", "reply": "你好！"},
            ]
        )
        forge = ReflexForge(
            fuzzy_cache=fc,
            config=ReflexForgeConfig(min_hits=3),
        )
        assert forge.propose() == []

    def test_shadow_validate_passes_for_good_pattern(self):
        fc = _make_mock_fuzzy_cache(
            [
                {"prompt": "你好", "reply": "你好！"},
                {"prompt": "你好", "reply": "你好！"},
                {"prompt": "你好", "reply": "你好！"},
            ]
        )
        forge = ReflexForge(fuzzy_cache=fc)
        candidates = forge.propose()
        assert len(candidates) == 1
        passed, report = forge.shadow_validate(candidates[0])
        assert passed is True
        assert report["positive_rate"] == 1.0
        assert report["false_positives"] == 0

    def test_shadow_validate_fails_on_false_positive(self):
        """A pattern that matches negative samples should fail."""
        candidate = ForgedReflexCandidate(
            candidate_id="test1234567890ab",
            rule_id="forged_test",
            pattern=".*",  # matches everything — will false-positive
            reply="test reply",
            source_prompts=["你好"],
            source_reply_variants=["test reply"],
            sample_count=1,
            reply_consistency=1.0,
        )
        forge = ReflexForge()
        passed, report = forge.shadow_validate(candidate)
        assert passed is False
        assert report["false_positives"] > 0

    def test_run_promotes_valid_candidate(self, tmp_path):
        rules_file = tmp_path / "reflex_rules.yaml"
        fc = _make_mock_fuzzy_cache(
            [
                {"prompt": "你好", "reply": "你好！"},
                {"prompt": "你好", "reply": "你好！"},
                {"prompt": "你好", "reply": "你好！"},
            ]
        )
        forge = ReflexForge(
            fuzzy_cache=fc,
            config=ReflexForgeConfig(
                min_hits=3,
                rules_file=str(rules_file),
                auto_reload=False,
            ),
        )
        result = forge.run()
        assert len(result.promoted) == 1
        assert rules_file.exists()
        content = rules_file.read_text(encoding="utf-8")
        assert "forged_" in content
        assert "你好！" in content

    def test_run_skips_existing_rule(self, tmp_path):
        rules_file = tmp_path / "reflex_rules.yaml"
        rules_file.write_text("rules: []\n", encoding="utf-8")
        fc = _make_mock_fuzzy_cache(
            [
                {"prompt": "你好", "reply": "你好！"},
                {"prompt": "你好", "reply": "你好！"},
                {"prompt": "你好", "reply": "你好！"},
            ]
        )
        forge = ReflexForge(
            fuzzy_cache=fc,
            config=ReflexForgeConfig(
                min_hits=3,
                rules_file=str(rules_file),
                auto_reload=False,
            ),
        )
        # First run promotes the rule
        result1 = forge.run()
        assert len(result1.promoted) == 1
        # Second run should skip (rule already exists)
        result2 = forge.run()
        assert len(result2.promoted) == 0
        assert len(result2.retired) == 1

