"""Implementation note."""

from __future__ import annotations

from uuid import uuid4

import pytest

from runtime.core.cerebrum import StaticPlanner
from runtime.core.cerebrum.planner import Rule
from runtime.core.cerebrum.rules_persistence import (
    dump_rules_to_file,
    dump_rules_to_yaml,
    load_rules_from_file,
    load_rules_from_yaml,
)
from runtime.memory.journal import InMemoryJournal
from runtime.platform.config import (
    AgentConfig,
    LearnConfig,
    PlannerConfig,
    build_from_config,
)
from runtime.platform.models import (
    ArmId,
    ExecutionResult,
    SkillId,
    Step,
    TaskId,
    ToolCall,
    Trajectory,
    TrajectoryOutcome,
)
from runtime.safety.recovery import RewriteProposal

# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


def _sample_rules() -> list[Rule]:
    return [
        Rule(
            name="file_probe",
            intent_types=["query", "task"],
            keywords=["file", "list"],
            skill_sequence=[
                SkillId("list_cwd"),
                SkillId("count_words"),
            ],
            node_args_templates=[
                None,
                {"text": "{n0.path}"},
            ],
            priority=10,
        ),
        Rule(
            name="default_list",
            intent_types=["query"],
            skill_sequence=[SkillId("list_cwd")],
            priority=0,
        ),
    ]


class TestDumpLoadRoundtrip:
    def test_roundtrip_preserves_all_fields(self, tmp_path):
        rules = _sample_rules()
        text = dump_rules_to_yaml(rules)
        reloaded = load_rules_from_yaml(text)
        assert len(reloaded) == len(rules)
        for orig, got in zip(rules, reloaded, strict=False):
            assert got.name == orig.name
            assert got.intent_types == orig.intent_types
            assert got.keywords == orig.keywords
            assert list(got.skill_sequence) == list(orig.skill_sequence)
            assert got.node_args_templates == orig.node_args_templates
            assert got.priority == orig.priority

    def test_file_roundtrip(self, tmp_path):
        rules = _sample_rules()
        path = tmp_path / "rules.yaml"
        dump_rules_to_file(rules, path)
        assert path.exists()
        reloaded = load_rules_from_file(path)
        assert len(reloaded) == 2

    def test_empty_rules_list(self):
        text = dump_rules_to_yaml([])
        assert "rules: []" in text
        assert load_rules_from_yaml(text) == []

    def test_missing_file_returns_empty(self, tmp_path):
        assert load_rules_from_file(tmp_path / "nope.yaml") == []

    def test_null_template_preserved(self, tmp_path):
        rules = [
            Rule(
                name="mixed",
                skill_sequence=[SkillId("a"), SkillId("b"), SkillId("c")],
                node_args_templates=[None, {"x": 1}, None],
            ),
        ]
        path = tmp_path / "r.yaml"
        dump_rules_to_file(rules, path)
        reloaded = load_rules_from_file(path)
        assert reloaded[0].node_args_templates == [None, {"x": 1}, None]

    def test_unicode_keywords_preserved(self, tmp_path):
        rules = [Rule(name="u", keywords=["并发", "多腕"])]
        path = tmp_path / "r.yaml"
        dump_rules_to_file(rules, path)
        reloaded = load_rules_from_file(path)
        assert reloaded[0].keywords == ["并发", "多腕"]


# ═══════════════════════════════════════════════════════════
# StaticPlanner auto_persist
# ═══════════════════════════════════════════════════════════


def _failure_journal() -> InMemoryJournal:
    """Implementation note."""
    j = InMemoryJournal()

    def _step(sid, sucker, ok=True):
        call = ToolCall(caller="arms/a", sucker_id=sucker, args={})
        return Step(
            step_id=sid,
            node_id=f"n{sid}",
            action=call,
            result=ExecutionResult(
                call_id=call.call_id,
                status="success" if ok else "failed",
                error_type=None if ok else "oops",
            ),
        )

    # Implementation note.
    for _ in range(5):
        j.write_trajectory(
            Trajectory(
                task_id=TaskId(uuid4()),
                arm_id=ArmId("a"),
                steps=[_step(0, "list_cwd"), _step(1, "count_words")],
                outcome=TrajectoryOutcome(success=True),
            )
        )
    return j


class TestStaticPlannerAutoPersist:
    def test_no_persist_by_default(self, tmp_path):
        """Implementation note."""
        StaticPlanner(rules=_sample_rules())
        # Implementation note.
        assert not any(tmp_path.iterdir())

    def test_apply_writes_file(self, tmp_path):
        path = tmp_path / "rules.yaml"
        planner = StaticPlanner(
            rules=[Rule(name="r1", skill_sequence=[SkillId("list_cwd")])],
            auto_persist_rules_path=path,
        )

        # Implementation note.
        p = RewriteProposal(
            kind="lower_rule_priority",
            target_rule_name="r1",
            rationale="test",
            confidence=0.9,
            severity="mid",
        )
        result = planner.apply_rewrite_proposals([p])
        assert result.applied_count >= 1
        assert path.exists()
        # Implementation note.
        reloaded = load_rules_from_file(path)
        assert len(reloaded) == 1
        # Implementation note.
        assert reloaded[0].name == "r1"

    def test_no_applied_no_write(self, tmp_path):
        """Implementation note."""
        path = tmp_path / "rules.yaml"
        planner = StaticPlanner(
            rules=[Rule(name="r1", skill_sequence=[SkillId("list_cwd")])],
            auto_persist_rules_path=path,
        )
        # Implementation note.
        p = RewriteProposal(
            kind="lower_rule_priority",
            target_rule_name="r1",
            rationale="too weak",
            confidence=0.1,  # Implementation note.
            severity="mid",
        )
        result = planner.apply_rewrite_proposals([p])
        assert result.applied_count == 0
        # Implementation note.
        assert not path.exists()

    def test_rewrite_from_journal_persists(self, tmp_path):
        path = tmp_path / "rules.yaml"
        planner = StaticPlanner(
            rules=[],
            auto_persist_rules_path=path,
        )
        result = planner.rewrite_from_journal(_failure_journal())
        if result.applied_count == 0:
            pytest.skip("no proposals applied · nothing to verify")
        assert path.exists()
        reloaded = load_rules_from_file(path)
        assert reloaded  # Implementation note.

    def test_construct_loads_existing_file(self, tmp_path):
        """Implementation note."""
        path = tmp_path / "rules.yaml"
        dump_rules_to_file(_sample_rules(), path)

        planner = StaticPlanner(rules=[], auto_persist_rules_path=path)
        assert len(planner.rules) == 2
        assert any(r.name == "file_probe" for r in planner.rules)

    def test_file_overrides_constructor_rules(self, tmp_path):
        """Implementation note."""
        path = tmp_path / "rules.yaml"
        dump_rules_to_file(
            [Rule(name="from_file")],
            path,
        )
        planner = StaticPlanner(
            rules=[Rule(name="from_arg")],
            auto_persist_rules_path=path,
        )
        assert any(r.name == "from_file" for r in planner.rules)
        assert not any(r.name == "from_arg" for r in planner.rules)

    def test_io_failure_swallowed(self, tmp_path):
        """Implementation note."""
        blocker = tmp_path / "blocker"
        blocker.write_text("file", encoding="utf-8")
        bad_path = blocker / "rules.yaml"

        planner = StaticPlanner(
            rules=[Rule(name="r1", skill_sequence=[SkillId("list_cwd")])],
            auto_persist_rules_path=bad_path,
        )
        p = RewriteProposal(
            kind="lower_rule_priority",
            target_rule_name="r1",
            rationale="x",
            confidence=0.9,
            severity="mid",
        )
        # Implementation note.
        planner.apply_rewrite_proposals([p])


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestConfigDriven:
    def test_builder_wires_path_to_static_planner(self, tmp_path):
        rules_path = tmp_path / "rules.yaml"
        cfg = AgentConfig(
            planner=PlannerConfig(type="static"),
            learn=LearnConfig(
                static_rules_persist_path=str(rules_path),
            ),
        )
        stack = build_from_config(cfg)
        assert str(stack.planner.auto_persist_rules_path) == str(rules_path)

    def test_builder_no_path_no_persist(self):
        cfg = AgentConfig(planner=PlannerConfig(type="static"))
        stack = build_from_config(cfg)
        assert stack.planner.auto_persist_rules_path is None


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestRestartScenario:
    def test_cross_session_rules_survive(self, tmp_path):
        """Implementation note."""
        path = tmp_path / "rules.yaml"

        # Session 1
        s1 = StaticPlanner(rules=[], auto_persist_rules_path=path)
        result = s1.rewrite_from_journal(_failure_journal())
        if result.applied_count == 0:
            pytest.skip("no rules applied")
        rule_names_s1 = {r.name for r in s1.rules}

        # Implementation note.
        s2 = StaticPlanner(rules=[], auto_persist_rules_path=path)
        rule_names_s2 = {r.name for r in s2.rules}
        assert rule_names_s2 == rule_names_s1
