"""Implementation note."""

from __future__ import annotations

import json

import pytest

from runtime.core.cerebrum import LLMPlanner
from runtime.core.graph_runtime import GraphRuntime
from runtime.execution.arms import (
    PRESET_FACTORIES,
    ArmPool,
    Worker,
    make_all_presets,
    make_coder_arm_v2,
    make_ecommerce_mind_arm,
    make_general_arm,
    make_shell_arm,
    make_vibe_selling_arm,
)
from runtime.execution.suckers import Skill, SkillRegistry
from runtime.memory.hemolymph import ContextComposer
from runtime.memory.journal import InMemoryJournal
from runtime.platform.models import ParsedIntent
from runtime.sensing.model_router import MockModelRouter


class _FakeExecutor:
    journal = None


def _fake_runtime():
    return GraphRuntime(executor=_FakeExecutor(), journal=None)


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestPresetConstructors:
    @pytest.mark.parametrize("factory", PRESET_FACTORIES)
    def test_constructor_produces_worker(self, factory):
        arm = factory(_fake_runtime())
        assert isinstance(arm, Worker)

    @pytest.mark.parametrize("factory", PRESET_FACTORIES)
    def test_metadata_populated(self, factory):
        arm = factory(_fake_runtime())
        assert arm.display_name
        assert arm.description
        assert arm.soul
        assert arm.icon

    @pytest.mark.parametrize("factory", PRESET_FACTORIES)
    def test_affinity_and_skills_non_empty(self, factory):
        arm = factory(_fake_runtime())
        assert len(arm.affinity) >= 3, "role needs enough affinity tags to route"
        assert len(arm.allowed_skills) >= 2, "role should have at least 2 skills"


class TestRoleIdentities:
    def test_six_distinct_arm_ids(self):
        arms = make_all_presets(_fake_runtime())
        ids = [str(a.arm_id) for a in arms]
        assert len(set(ids)) == 6, f"expected 6 distinct arms, got {ids}"

    def test_specific_ids(self):
        arms = make_all_presets(_fake_runtime())
        ids = {str(a.arm_id) for a in arms}
        assert ids == {
            "general_arm",
            "coder_arm_v2",
            "vibe_selling_arm",
            "ecommerce_mind_arm",
            "mobile_operator_arm",
            "mobile_browser_operator_arm",
        }

    def test_display_names_match_ghost_current_roster(self):
        """Implementation note."""
        rt = _fake_runtime()
        assert make_general_arm(rt).display_name == "Eve"
        assert make_coder_arm_v2(rt).display_name == "Kane"
        assert make_vibe_selling_arm(rt).display_name == "Luna"
        assert make_ecommerce_mind_arm(rt).display_name == "Shion"


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestRoleSkillSets:
    def test_shell_arm_exposes_background_exec(self):
        arm = make_shell_arm(_fake_runtime())
        names = {str(s) for s in arm.allowed_skills}
        assert "exec_shell" in names
        assert "background_exec" in names
        assert "read_background_output" in names
        assert "kill_background_exec" in names

    def test_coder_has_git_and_exec(self):
        arm = make_coder_arm_v2(_fake_runtime())
        # Implementation note.
        names = {str(s) for s in arm.allowed_skills}
        assert "git_commit" in names
        assert "exec_shell" in names
        assert "background_exec" in names
        assert "read_shell_output" in names
        assert "kill_shell" in names
        assert "edit_text_file" in names
        # Implementation note.
        assert arm.can_use("read_file")

    def test_general_has_readonly_only(self):
        """Implementation note."""
        arm = make_general_arm(_fake_runtime())
        # Implementation note.
        assert not arm.can_use("exec_shell")
        assert not arm.can_use("git_commit")
        assert not arm.can_use("write_text_file")
        # Implementation note.
        assert arm.can_use("read_file")
        assert arm.can_use("list_cwd")
        # Implementation note.
        assert arm.can_use("web_search")

    def test_vibe_selling_can_draft_copy(self):
        """Implementation note."""
        arm = make_vibe_selling_arm(_fake_runtime())
        # Implementation note.
        assert arm.can_use("write_text_file")
        assert arm.can_use("web_search")
        assert arm.can_use("browser_get")
        # Implementation note.
        assert arm.can_use("read_file")

    def test_ecommerce_mind_is_analytical_not_writing(self):
        """Implementation note."""
        arm = make_ecommerce_mind_arm(_fake_runtime())
        assert arm.can_use("web_search")
        assert arm.can_use("browser_get")
        # Implementation note.
        assert not arm.can_use("write_text_file")
        assert not arm.can_use("edit_text_file")
        # Implementation note.
        assert arm.can_use("read_file")
        assert arm.can_use("count_words")


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestSoulPropagation:
    def _mk_planner(self):
        reg = SkillRegistry()
        reg.register(
            Skill(
                name="read_file",
                description="read",
                trusted_source="skill://public/read_file",
                handler=lambda **_kw: {},
            ),
            verify_tests=False,
        )
        router = MockModelRouter(
            response=json.dumps(
                {
                    "reasoning": "x",
                    "nodes": [{"skill": "read_file", "args": {}}],
                }
            ),
        )
        composer = ContextComposer(registry=reg, journal=InMemoryJournal())
        return LLMPlanner(router=router, registry=reg, composer=composer), router

    def test_soul_prepended_to_system_prompt(self):
        planner, router = self._mk_planner()
        intent = ParsedIntent(
            raw="x",
            intent_type="task",
            normalized_goal="read a file",
        )
        planner.plan(
            intent,
            allowed_skills=["read_file"],
            soul="You are a cautious auditor. Verify before acting.",
        )
        sys_msgs = [m.content for m in router.call_log[0].messages if m.role == "system"]
        sys_text = "\n".join(sys_msgs)
        assert "cautious auditor" in sys_text
        assert "Agent Soul" in sys_text

    def test_no_soul_keeps_backward_compat(self):
        planner, router = self._mk_planner()
        intent = ParsedIntent(
            raw="x",
            intent_type="task",
            normalized_goal="read a file",
        )
        planner.plan(intent, allowed_skills=["read_file"])
        sys_text = "\n".join(m.content for m in router.call_log[0].messages if m.role == "system")
        assert "Agent Soul" not in sys_text


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestIntentRouting:
    def test_code_intent_routes_to_coder(self):
        pool = ArmPool(make_all_presets(_fake_runtime()))
        intent = ParsedIntent(
            raw="x",
            intent_type="task",
            normalized_goal="refactor this code and git commit the changes",
        )
        arm = pool.pick_for_intent(intent)
        assert arm is not None
        assert str(arm.arm_id) == "coder_arm_v2"

    def test_storefront_intent_routes_to_growth_or_commerce(self):
        pool = ArmPool(make_all_presets(_fake_runtime()))
        intent = ParsedIntent(
            raw="x",
            intent_type="task",
            normalized_goal="list new products on my Shopify storefront",
        )
        arm = pool.pick_for_intent(intent)
        assert arm is not None
        assert str(arm.arm_id) in {"vibe_selling_arm", "ecommerce_mind_arm"}

    def test_social_copy_intent_routes_to_vibe(self):
        pool = ArmPool(make_all_presets(_fake_runtime()))
        intent = ParsedIntent(
            raw="x",
            intent_type="task",
            normalized_goal="write social content for marketing campaign",
        )
        arm = pool.pick_for_intent(intent)
        assert arm is not None
        # Implementation note.
        assert str(arm.arm_id) == "vibe_selling_arm"

    def test_atomic_skills_usable_by_all_arms(self):
        """Implementation note."""
        arms = make_all_presets(_fake_runtime())
        atomic = ["list_cwd", "read_file", "file_stats", "count_words", "hash_text"]
        for arm in arms:
            for s in atomic:
                assert arm.can_use(s), f"{arm.arm_id} should use atomic {s}"
                assert str(s) not in {str(x) for x in arm.allowed_skills}, (
                    f"{s} should NOT be in {arm.arm_id}.allowed_skills (atomic is implicit)"
                )

    def test_role_skills_scoped_to_owner(self):
        """Implementation note."""
        general = make_general_arm(_fake_runtime())
        coder = make_coder_arm_v2(_fake_runtime())
        vibe = make_vibe_selling_arm(_fake_runtime())

        # Implementation note.
        assert coder.can_use("git_commit")
        assert not general.can_use("git_commit")
        assert not vibe.can_use("git_commit")

        # Implementation note.
        assert not vibe.can_use("browser_click")
        assert not general.can_use("browser_click")
        assert not coder.can_use("browser_click")

    def test_general_qa_intent_does_not_match_specialized(self):
        """Implementation note."""
        pool = ArmPool(make_all_presets(_fake_runtime()))
        intent = ParsedIntent(
            raw="x",
            intent_type="task",
            normalized_goal="something completely unrelated to any tag",
        )
        arm = pool.pick_for_intent(intent)
        # Implementation note.
        if arm is not None:
            # Implementation note.
            assert str(arm.arm_id) in {
                "general_arm",
                "coder_arm_v2",
                "vibe_selling_arm",
                "ecommerce_mind_arm",
            }

