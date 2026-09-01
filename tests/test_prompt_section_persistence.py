"""Implementation note."""

from __future__ import annotations

import json
from uuid import uuid4

from runtime.core.cerebrum import LLMPlanner
from runtime.core.cerebrum.prompt_persistence import dump_section, load_section
from runtime.execution.suckers import Skill, SkillRegistry
from runtime.memory.hemolymph import ContextComposer
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
    Step,
    TaskId,
    ToolCall,
    Trajectory,
    TrajectoryOutcome,
)
from runtime.sensing.model_router import MockModelRouter

# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestDumpLoad:
    def test_roundtrip_preserves_body(self, tmp_path):
        section = "LEARNED MITIGATIONS:\n  - foo\n  - bar"
        path = tmp_path / "rules.txt"
        dump_section(path, section, label="rules")
        assert load_section(path) == section

    def test_missing_file_returns_empty(self, tmp_path):
        assert load_section(tmp_path / "nope.txt") == ""

    def test_header_includes_metadata(self, tmp_path):
        section = "hello"
        path = tmp_path / "s.txt"
        dump_section(path, section, label="my-test")
        text = path.read_text(encoding="utf-8")
        assert text.startswith("# ")
        assert "my-test" in text
        assert "chars:" in text
        assert "written_at:" in text
        # Implementation note.
        assert text.endswith(section)

    def test_empty_section_ok(self, tmp_path):
        path = tmp_path / "empty.txt"
        dump_section(path, "", label="rules")
        assert load_section(path) == ""

    def test_auto_creates_parent(self, tmp_path):
        nested = tmp_path / "a" / "b" / "c" / "rules.txt"
        dump_section(nested, "x", label="rules")
        assert nested.exists()

    def test_body_with_leading_hash_preserved(self, tmp_path):
        """Implementation note."""
        section = "first line\n# a comment inside body\nthird"
        path = tmp_path / "s.txt"
        dump_section(path, section, label="x")
        reloaded = load_section(path)
        assert reloaded == section


# ═══════════════════════════════════════════════════════════
# LLMPlanner auto_persist
# ═══════════════════════════════════════════════════════════


def _minimal_planner(
    *,
    rules_path=None,
    memories_path=None,
    initial_rules: str = "",
    initial_memories: str = "",
):
    reg = SkillRegistry()
    reg.register(
        Skill(
            name="list_cwd",
            trusted_source="skill://public/list_cwd",
            handler=lambda **kw: {"path": "."},
        ),
        verify_tests=False,
    )
    return LLMPlanner(
        router=MockModelRouter(
            response=json.dumps({"reasoning": "r", "nodes": [{"skill": "list_cwd", "args": {}}]}),
        ),
        registry=reg,
        composer=ContextComposer(registry=reg, journal=InMemoryJournal()),
        learned_rules_section=initial_rules,
        learned_memories_section=initial_memories,
        auto_persist_rules_path=rules_path,
        auto_persist_memories_path=memories_path,
    )


def _seeded_journal_with_failures():
    from runtime.safety.recovery import RuleExtractor  # noqa: F401 · ensure import

    j = InMemoryJournal()

    def _mk_failed():
        call = ToolCall(caller="arms/a", sucker_id="list_cwd", args={})
        step = Step(
            step_id=0,
            node_id="n0",
            action=call,
            result=ExecutionResult(
                call_id=call.call_id,
                status="failed",
                error_type="timeout",
            ),
        )
        return Trajectory(
            task_id=TaskId(uuid4()),
            arm_id=ArmId("a"),
            steps=[step],
            outcome=TrajectoryOutcome(success=False),
        )

    for _ in range(4):
        j.write_trajectory(_mk_failed())
    return j


class TestPlannerAutoPersist:
    def test_no_persist_by_default(self, tmp_path):
        """Implementation note."""
        planner = _minimal_planner()
        planner.update_learned_rules([])
        # Implementation note.
        assert not any(tmp_path.iterdir())

    def test_rules_persist_on_update(self, tmp_path):
        path = tmp_path / "rules.txt"
        planner = _minimal_planner(rules_path=path)
        # Implementation note.
        planner.learn_from_journal(_seeded_journal_with_failures())
        # Implementation note.
        assert planner.learned_rules_section != ""
        assert path.exists()
        reloaded = load_section(path)
        assert reloaded == planner.learned_rules_section

    def test_memories_persist_on_update(self, tmp_path):
        from runtime.platform.models import CostEntry

        # Implementation note.
        j = InMemoryJournal()
        for _ in range(5):
            j.write_trajectory(
                Trajectory(
                    task_id=TaskId(uuid4()),
                    arm_id=ArmId("a"),
                    strategy_id="s",
                    steps=[],
                    outcome=TrajectoryOutcome(
                        success=True,
                        cost=CostEntry(tokens_in=10, tokens_out=10, usd=0.0001),
                    ),
                )
            )

        path = tmp_path / "memories.txt"
        planner = _minimal_planner(memories_path=path)
        planner.learn_memories_from_journal(j)

        assert planner.learned_memories_section != ""
        assert path.exists()
        assert load_section(path) == planner.learned_memories_section

    def test_load_from_disk_on_construct(self, tmp_path):
        """Implementation note."""
        path = tmp_path / "rules.txt"
        dump_section(path, "PRE-EXISTING RULES:\n  - be careful", label="rules")

        planner = _minimal_planner(rules_path=path)
        assert "PRE-EXISTING" in planner.learned_rules_section

    def test_constructor_initial_section_wins_if_no_file(self, tmp_path):
        """Implementation note."""
        planner = _minimal_planner(
            rules_path=tmp_path / "nope.txt",
            initial_rules="passed-in rules",
        )
        assert planner.learned_rules_section == "passed-in rules"

    def test_file_load_overrides_constructor_arg(self, tmp_path):
        """Implementation note."""
        path = tmp_path / "rules.txt"
        dump_section(path, "from-disk", label="rules")
        planner = _minimal_planner(
            rules_path=path,
            initial_rules="from-arg",
        )
        assert planner.learned_rules_section == "from-disk"

    def test_io_failure_does_not_crash(self, tmp_path):
        """Implementation note."""
        # Implementation note.
        blocker = tmp_path / "blocker"
        blocker.write_text("file", encoding="utf-8")
        bad_path = blocker / "cant-create.txt"

        planner = _minimal_planner(rules_path=bad_path)
        # Implementation note.
        planner.learn_from_journal(_seeded_journal_with_failures())
        # Implementation note.
        assert planner._rules_updated_count >= 1


# ═══════════════════════════════════════════════════════════
# config.learn.rules_persist_path / memories_persist_path
# ═══════════════════════════════════════════════════════════


class TestConfigDrivenPersistence:
    def test_builder_wires_paths_to_planner(self, tmp_path):
        rules_path = tmp_path / "rules.txt"
        memories_path = tmp_path / "mem.txt"
        cfg = AgentConfig(
            planner=PlannerConfig(
                type="llm",
                model="mock/cp",
                mock_response=json.dumps({"nodes": []}),
            ),
            learn=LearnConfig(
                rules_persist_path=str(rules_path),
                memories_persist_path=str(memories_path),
            ),
        )
        stack = build_from_config(cfg)
        assert str(stack.planner.auto_persist_rules_path) == str(rules_path)
        assert str(stack.planner.auto_persist_memories_path) == str(memories_path)

    def test_builder_without_paths_no_persist(self):
        cfg = AgentConfig(
            planner=PlannerConfig(
                type="llm",
                model="mock/x",
                mock_response=json.dumps({"nodes": []}),
            ),
        )
        stack = build_from_config(cfg)
        assert stack.planner.auto_persist_rules_path is None
        assert stack.planner.auto_persist_memories_path is None


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestRestartScenario:
    def test_cross_session_rules_restore(self, tmp_path):
        path = tmp_path / "rules.txt"

        # Implementation note.
        s1 = _minimal_planner(rules_path=path)
        s1.learn_from_journal(_seeded_journal_with_failures())
        section_s1 = s1.learned_rules_section
        assert section_s1 != ""
        assert path.exists()

        # Implementation note.
        s2 = _minimal_planner(rules_path=path)
        assert s2.learned_rules_section == section_s1
        # Implementation note.
        assert s2.recipe_hash() == s1.recipe_hash()
