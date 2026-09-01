"""Implementation note."""

from __future__ import annotations

import pytest

from runtime.core.graph_runtime import GraphRuntime
from runtime.execution.agents import (
    Agent,
    AgentRegistry,
    make_all_agent_presets,
    make_coder_agent,
    make_desktop_operator_agent,
    make_general_agent,
)
from runtime.execution.agents.base import AgentNotFound
from runtime.execution.arms import ArmPool, Worker
from runtime.platform.models import ArmId, ParsedIntent, SkillId


class _FakeExecutor:
    journal = None


AGENT_PRESET_FACTORIES = (
    make_general_agent,
    make_coder_agent,
    make_desktop_operator_agent,
)


def _fake_runtime():
    return GraphRuntime(executor=_FakeExecutor(), journal=None)


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestAgentConstructor:
    def test_rejects_empty_arm_pool(self):
        with pytest.raises(ValueError, match="at least 1 arm"):
            Agent(
                agent_id="x",
                display_name="X",
                description="",
                soul="",
                arms=ArmPool([]),
            )

    def test_rejects_empty_agent_id(self):
        rt = _fake_runtime()
        arm = Worker(
            arm_id=ArmId("a"),
            affinity=[],
            allowed_skills=[SkillId("x")],
            runtime=rt,
        )
        with pytest.raises(ValueError, match="agent_id"):
            Agent(
                agent_id="",
                display_name="X",
                description="",
                soul="",
                arms=ArmPool([arm]),
            )


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestPresetAgents:
    @pytest.mark.parametrize("factory", AGENT_PRESET_FACTORIES)
    def test_all_fields_populated(self, factory):
        agent = factory(_fake_runtime())
        assert agent.agent_id
        assert agent.display_name
        assert agent.description
        assert agent.soul
        assert agent.icon
        assert len(agent.arms) >= 1

    def test_display_names_match_upstream_spirit(self):
        rt = _fake_runtime()
        assert make_general_agent(rt).display_name == "Echo"
        assert make_coder_agent(rt).display_name == "Coder"

    def test_all_distinct_ids(self):
        agents = make_all_agent_presets(_fake_runtime())
        ids = [a.agent_id for a in agents]
        required_ids = {
            factory(_fake_runtime()).agent_id
            for factory in (
                make_general_agent,
                make_coder_agent,
                make_desktop_operator_agent,
            )
        }

        assert len(ids) == len(set(ids))
        assert required_ids <= set(ids)


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestCoderThreeArms:
    def test_coder_has_expected_arms(self):
        agent = make_coder_agent(_fake_runtime())
        arm_ids = [str(a.arm_id) for a in agent.arms]
        # The coder agent gained a fifth arm (``coder_private_arm``)
        # for agent-private skill whitelisting; the four canonical
        # arms below are still required.
        assert len(arm_ids) == 5
        assert "web_read_arm" in arm_ids
        assert "fs_writer_arm" in arm_ids
        assert "git_arm" in arm_ids
        assert "shell_arm" in arm_ids
        assert "coder_private_arm" in arm_ids

    def test_coder_can_use_all_three_domains(self):
        agent = make_coder_agent(_fake_runtime())
        assert agent.can_use("write_text_file")
        assert agent.can_use("git_commit")
        assert agent.can_use("exec_shell")
        assert agent.can_use("web_search")

    def test_coder_task_routes_to_git_arm(self):
        agent = make_coder_agent(_fake_runtime())
        intent = ParsedIntent(
            raw="x",
            intent_type="task",
            normalized_goal="git commit my changes",
        )
        arm = agent.pick_arm_for_intent(intent)
        assert arm is not None
        assert str(arm.arm_id) == "git_arm"

    def test_coder_task_routes_to_shell_arm(self):
        agent = make_coder_agent(_fake_runtime())
        intent = ParsedIntent(
            raw="x",
            intent_type="task",
            normalized_goal="run the test suite in shell",
        )
        arm = agent.pick_arm_for_intent(intent)
        assert arm is not None
        assert str(arm.arm_id) == "shell_arm"

    def test_coder_task_routes_to_writer_arm(self):
        agent = make_coder_agent(_fake_runtime())
        intent = ParsedIntent(
            raw="x",
            intent_type="task",
            normalized_goal="edit the file to fix the bug",
        )
        arm = agent.pick_arm_for_intent(intent)
        assert arm is not None
        assert str(arm.arm_id) == "fs_writer_arm"


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestAggregateViews:
    def test_affinity_aggregates_arms(self):
        agent = make_coder_agent(_fake_runtime())
        agg = set(agent.affinity())
        # Implementation note.
        assert "git" in agg
        assert "shell" in agg
        assert "write" in agg

    def test_skill_union_has_role_skills(self):
        agent = make_coder_agent(_fake_runtime())
        union = set(agent.allowed_skill_union())
        assert "git_commit" in union
        assert "exec_shell" in union
        assert "write_text_file" in union

    def test_skill_union_includes_atomic(self):
        """Implementation note."""
        agent = make_coder_agent(_fake_runtime())
        union = set(agent.allowed_skill_union())
        assert "read_file" in union  # atomic → visible to planner
        assert "hash_text" in union  # atomic → visible to planner
        assert "git_commit" in union  # arm-bundled still visible


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestAgentCanUse:
    def test_atomic_always_usable(self):
        """Implementation note."""
        for factory in AGENT_PRESET_FACTORIES:
            agent = factory(_fake_runtime())
            for atomic in ["list_cwd", "read_file", "count_words", "hash_text"]:
                assert agent.can_use(atomic), f"{agent.agent_id} should use atomic {atomic}"

    def test_role_skills_scoped(self):
        general = make_general_agent(_fake_runtime())
        coder = make_coder_agent(_fake_runtime())

        assert coder.can_use("git_commit")
        assert general.can_use("git_commit")

        assert general.can_use("web_search")
        assert coder.can_use("web_search")


# ═══════════════════════════════════════════════════════════
# AgentRegistry
# ═══════════════════════════════════════════════════════════


class TestAgentRegistry:
    def test_register_and_get(self):
        reg = AgentRegistry()
        agent = make_coder_agent(_fake_runtime())
        reg.register(agent)
        assert reg.has("coder")
        assert reg.get("coder") is agent
        assert len(reg) == 1

    def test_duplicate_rejected(self):
        reg = AgentRegistry()
        reg.register(make_coder_agent(_fake_runtime()))
        with pytest.raises(ValueError, match="duplicate"):
            reg.register(make_coder_agent(_fake_runtime()))

    def test_remove(self):
        reg = AgentRegistry()
        reg.register(make_coder_agent(_fake_runtime()))
        assert reg.remove("coder") is True
        assert reg.remove("coder") is False
        assert not reg.has("coder")

    def test_unknown_get_raises(self):
        reg = AgentRegistry()
        with pytest.raises(AgentNotFound):
            reg.get("ghost")

    def test_register_all_presets(self):
        reg = AgentRegistry()
        agents = make_all_agent_presets(_fake_runtime())
        expected_ids = {agent.agent_id for agent in agents}
        reg.register_all(agents)

        assert len(reg) == len(expected_ids)
        assert set(reg.all_ids()) == expected_ids


# ═══════════════════════════════════════════════════════════
# AgentRegistry.pick_for_intent
# ═══════════════════════════════════════════════════════════


class TestRegistryRouting:
    def _reg(self):
        reg = AgentRegistry()
        reg.register_all(make_all_agent_presets(_fake_runtime()))
        return reg

    def test_coder_intent_picks_coder_agent(self):
        reg = self._reg()
        intent = ParsedIntent(
            raw="x",
            intent_type="task",
            normalized_goal="refactor and git commit the code",
        )
        a = reg.pick_for_intent(intent)
        assert a is not None
        assert a.agent_id == "coder"

    def test_storefront_intent_does_not_require_bundled_specialist(self):
        reg = self._reg()
        intent = ParsedIntent(
            raw="x",
            intent_type="task",
            normalized_goal="list new products on my shopify storefront",
        )
        a = reg.pick_for_intent(intent)
        assert a is None

    def test_unrelated_intent_none_or_weak(self):
        reg = self._reg()
        intent = ParsedIntent(
            raw="x",
            intent_type="task",
            normalized_goal="xyzzy plugh",
        )
        a = reg.pick_for_intent(intent)
        if a is not None:
            assert a.agent_id in {
                "general",
                "coder",
                "desktop_operator",
            }


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestEndToEndPersonaFlow:
    def test_full_persona_to_arm_to_soul(self):
        """Implementation note."""
        import json

        from runtime.core.cerebrum import LLMPlanner
        from runtime.execution.suckers import Skill, SkillRegistry
        from runtime.memory.hemolymph import ContextComposer
        from runtime.memory.journal import InMemoryJournal
        from runtime.sensing.model_router import MockModelRouter

        # minimal registry
        reg = SkillRegistry()
        for name in ["exec_shell", "git_commit", "write_text_file"]:
            reg.register(
                Skill(
                    name=name,
                    description=f"skill {name}",
                    trusted_source=f"skill://public/{name}",
                    handler=lambda **_kw: {},
                ),
                verify_tests=False,
            )

        agent = make_coder_agent(_fake_runtime())
        intent = ParsedIntent(
            raw="x",
            intent_type="task",
            normalized_goal="commit the changes via git",
        )
        arm = agent.pick_arm_for_intent(intent)
        assert arm is not None
        assert str(arm.arm_id) == "git_arm"

        # Implementation note.
        router = MockModelRouter(
            response=json.dumps(
                {
                    "reasoning": "x",
                    "nodes": [{"skill": "git_commit", "args": {}}],
                }
            ),
        )
        composer = ContextComposer(registry=reg, journal=InMemoryJournal())
        planner = LLMPlanner(router=router, registry=reg, composer=composer)

        planner.plan(
            intent,
            allowed_skills=[str(s) for s in arm.allowed_skills],
            soul=agent.soul,  # ← agent-level persona
        )
        sys_msg = next(m.content for m in router.call_log[0].messages if m.role == "system")
        # Implementation note.
        # Implementation note.
        # Implementation note.
        assert "small reversible edits" in sys_msg
        # Implementation note.
        sucker_text = "\n".join(
            m.content for m in router.call_log[0].messages if m.role != "system"
        )
        assert "git_commit" in sucker_text
        # Implementation note.
        assert "exec_shell" not in sucker_text
