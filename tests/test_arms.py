"""Implementation note."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from runtime.core.graph_runtime import GraphRuntime
from runtime.execution.arms import (
    Arm,
    ArmPool,
    Worker,
    make_code_arm,
    make_file_arm,
    make_search_arm,
)
from runtime.execution.suckers import Skill, SkillRegistry
from runtime.execution.tool_engine import ToolExecutor
from runtime.memory.journal import InMemoryJournal
from runtime.platform.models import (
    ArmAssignment,
    ArmId,
    Budget,
    BudgetLimits,
    BudgetSpec,
    ContextPacketRef,
    SkillId,
    TaskGraph,
    TaskNode,
)
from runtime.safety.auth import TrustEngine

# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def registry() -> SkillRegistry:
    r = SkillRegistry()
    r.register(
        Skill(
            name="read_file",
            description="read a file",
            affinity=["code", "file"],
            trusted_source="skill://public/read_file",
            handler=lambda path="X", **kw: {"path": path, "lines": 10},
        ),
        verify_tests=False,
    )
    r.register(
        Skill(
            name="count_words",
            description="count words",
            affinity=["code"],
            trusted_source="skill://public/count_words",
            handler=lambda text="", **kw: {"count": len(text.split())},
        ),
        verify_tests=False,
    )
    r.register(
        Skill(
            name="hash_text",
            description="hash text",
            affinity=["code"],
            trusted_source="skill://public/hash_text",
            handler=lambda text="", **kw: {"hash": f"h:{len(text)}"},
        ),
        verify_tests=False,
    )
    r.register(
        Skill(
            name="list_dir",
            description="list a dir",
            affinity=["file"],
            trusted_source="skill://public/list_dir",
            handler=lambda path="/", **kw: {"entries": ["a", "b"]},
        ),
        verify_tests=False,
    )
    r.register(
        Skill(
            name="boom_skill",
            description="always raises",
            affinity=["code"],
            trusted_source="skill://public/boom_skill",
            handler=lambda **kw: (_ for _ in ()).throw(ValueError("kapow")),
        ),
        verify_tests=False,
    )
    return r


@pytest.fixture
def runtime(registry) -> GraphRuntime:
    journal = InMemoryJournal()
    executor = ToolExecutor(
        registry=registry,
        immunity=TrustEngine(trusted_sources=["skill://public/*"]),
        journal=journal,
    )
    return GraphRuntime(executor=executor, journal=journal)


@pytest.fixture
def budget() -> Budget:
    from runtime.platform.models import TaskId

    return Budget(task_id=TaskId(uuid4()), limits=BudgetLimits(tokens=10_000, usd=1.0))


def _make_assignment(
    nodes: list[TaskNode],
    *,
    arm_id: ArmId = ArmId("any_arm"),  # noqa: B008 — test helper; the call is deterministic
) -> ArmAssignment:
    graph = TaskGraph(
        nodes=nodes,
        budget=BudgetSpec(tokens=10_000, usd=0.10),
        task_type="test",
    )
    return ArmAssignment(
        arm_id=arm_id,
        subgraph=graph,
        context_ref=ContextPacketRef(packet_id=uuid4(), budget_tokens=1_000),
        deadline=datetime.now(UTC) + timedelta(minutes=5),
    )


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestArmAlias:
    def test_arm_is_worker(self):
        """Implementation note."""
        assert Arm is Worker

    def test_arm_constructable(self, runtime):
        arm = Arm(
            arm_id=ArmId("generic"),
            affinity=["x"],
            allowed_skills=[SkillId("read_file")],
            runtime=runtime,
        )
        assert isinstance(arm, Worker)
        assert arm.arm_id == "generic"


# ═══════════════════════════════════════════════════════════
# Worker.can_handle
# ═══════════════════════════════════════════════════════════


class TestCanHandle:
    def test_all_skills_in_whitelist(self, runtime):
        arm = Worker(
            arm_id=ArmId("code_arm"),
            affinity=["code"],
            allowed_skills=[SkillId("read_file"), SkillId("count_words")],
            runtime=runtime,
        )
        task = _make_assignment(
            [
                TaskNode(node_id="n0", skill_ref=SkillId("read_file")),
                TaskNode(node_id="n1", skill_ref=SkillId("count_words")),
            ]
        )
        assert arm.can_handle(task) is True

    def test_skill_not_in_whitelist_rejected(self, runtime):
        """Implementation note."""
        arm = Worker(
            arm_id=ArmId("narrow_arm"),
            affinity=["code"],
            allowed_skills=[SkillId("read_file")],
            runtime=runtime,
        )
        task = _make_assignment(
            [
                TaskNode(node_id="n0", skill_ref=SkillId("read_file")),
                # Implementation note.
                TaskNode(node_id="n1", skill_ref=SkillId("exec_shell")),
            ]
        )
        assert arm.can_handle(task) is False

    def test_node_without_skill_ref_ignored(self, runtime):
        """Implementation note."""
        arm = Worker(
            arm_id=ArmId("a"),
            affinity=[],
            allowed_skills=[SkillId("read_file")],
            runtime=runtime,
        )
        task = _make_assignment(
            [
                TaskNode(node_id="n0", skill_ref=SkillId("read_file")),
                TaskNode(node_id="n1", kind="validator", skill_ref=None),
            ]
        )
        assert arm.can_handle(task) is True

    def test_empty_whitelist_rejects_non_atomic(self, runtime):
        """Implementation note."""
        arm = Worker(
            arm_id=ArmId("empty"),
            affinity=[],
            allowed_skills=[],
            runtime=runtime,
        )
        task_atomic = _make_assignment(
            [
                TaskNode(node_id="n0", skill_ref=SkillId("read_file")),
            ]
        )
        # Implementation note.
        assert arm.can_handle(task_atomic) is True

        task_role = _make_assignment(
            [
                TaskNode(node_id="n0", skill_ref=SkillId("exec_shell")),
            ]
        )
        # Implementation note.
        assert arm.can_handle(task_role) is False


# ═══════════════════════════════════════════════════════════
# Worker.handle
# ═══════════════════════════════════════════════════════════


class TestHandleSuccess:
    def test_handle_runs_subgraph_and_returns_success(self, runtime, budget):
        arm = Worker(
            arm_id=ArmId("code_arm"),
            affinity=["code"],
            allowed_skills=[SkillId("read_file")],
            runtime=runtime,
        )
        task = _make_assignment(
            [
                TaskNode(
                    node_id="n0",
                    skill_ref=SkillId("read_file"),
                    args_template={"path": "a.py"},
                ),
            ]
        )
        result = arm.handle(task, budget)
        assert result.status == "success"
        assert result.arm_id == "code_arm"
        assert result.task_id == task.subgraph.task_id
        assert result.trajectory_id is not None
        assert "n0" in result.outputs
        # Implementation note.
        assert result.outputs["n0"]["path"] == "a.py"

    def test_handle_multi_step_aggregates_outputs(self, runtime, budget):
        arm = Worker(
            arm_id=ArmId("code_arm"),
            affinity=["code"],
            allowed_skills=[SkillId("read_file"), SkillId("count_words")],
            runtime=runtime,
        )
        task = _make_assignment(
            [
                TaskNode(
                    node_id="n0",
                    skill_ref=SkillId("read_file"),
                    args_template={"path": "x.py"},
                ),
                TaskNode(
                    node_id="n1",
                    skill_ref=SkillId("count_words"),
                    args_template={"text": "hello world"},
                ),
            ]
        )
        result = arm.handle(task, budget)
        assert result.status == "success"
        assert "n0" in result.outputs
        assert "n1" in result.outputs
        assert result.outputs["n1"]["count"] == 2


class TestHandleRejection:
    def test_handle_rejects_when_skill_not_allowed(self, runtime, budget):
        arm = Worker(
            arm_id=ArmId("narrow"),
            affinity=["code"],
            allowed_skills=[SkillId("read_file")],
            runtime=runtime,
        )
        # Implementation note.
        task = _make_assignment(
            [
                TaskNode(node_id="n0", skill_ref=SkillId("exec_shell")),
            ]
        )
        result = arm.handle(task, budget)
        assert result.status == "failed"
        assert "not in allowed_skills" in result.reason
        # Implementation note.
        assert budget.tokens_spent == 0
        assert result.trajectory_id is None


class TestHandleFailure:
    def test_handle_skill_handler_raises_yields_failed(self, runtime, budget):
        arm = Worker(
            arm_id=ArmId("code_arm"),
            affinity=["code"],
            allowed_skills=[SkillId("boom_skill")],
            runtime=runtime,
        )
        task = _make_assignment(
            [
                TaskNode(node_id="n0", skill_ref=SkillId("boom_skill")),
            ]
        )
        result = arm.handle(task, budget)
        # Implementation note.
        assert result.status == "failed"
        assert "ValueError" in result.reason


# ═══════════════════════════════════════════════════════════
# ArmPool
# ═══════════════════════════════════════════════════════════


class TestArmPool:
    def test_len_and_add(self, runtime):
        pool = ArmPool([make_code_arm(runtime)])
        assert len(pool) == 1
        pool.add(make_file_arm(runtime))
        assert len(pool) == 2
        assert len(pool.all_arms()) == 2

    def test_pick_for_selects_matching_arm(self, runtime):
        code = make_code_arm(runtime)
        file_ = make_file_arm(runtime)
        pool = ArmPool([code, file_])

        # Implementation note.
        task = _make_assignment(
            [
                TaskNode(node_id="n0", skill_ref=SkillId("list_dir")),
            ]
        )
        picked = pool.pick_for(task)
        assert picked is file_

    def test_pick_for_picks_better_coverage(self, runtime):
        """Implementation note."""
        narrow = Worker(
            arm_id=ArmId("narrow"),
            affinity=["code"],
            allowed_skills=[SkillId("list_dir"), SkillId("boom_skill")],
            runtime=runtime,
        )
        Worker(
            arm_id=ArmId("wide"),
            affinity=["code"],
            allowed_skills=[SkillId("list_dir"), SkillId("boom_skill")],
            runtime=runtime,
        )
        # Implementation note.
        wide2 = Worker(
            arm_id=ArmId("wide2"),
            affinity=["code", "file", "extra"],
            allowed_skills=[SkillId("list_dir"), SkillId("boom_skill")],
            runtime=runtime,
        )
        pool = ArmPool([narrow, wide2])
        task = _make_assignment(
            [
                TaskNode(node_id="n0", skill_ref=SkillId("list_dir")),
                TaskNode(node_id="n1", skill_ref=SkillId("boom_skill")),
            ]
        )
        picked = pool.pick_for(task)
        # Implementation note.
        assert picked is wide2

    def test_pick_for_returns_none_when_no_match(self, runtime):
        pool = ArmPool([make_code_arm(runtime)])
        # Implementation note.
        task = _make_assignment(
            [
                TaskNode(node_id="n0", skill_ref=SkillId("web_search")),
            ]
        )
        assert pool.pick_for(task) is None

    def test_pick_for_empty_pool_returns_none(self, runtime):
        pool = ArmPool([])
        task = _make_assignment(
            [
                TaskNode(node_id="n0", skill_ref=SkillId("read_file")),
            ]
        )
        assert pool.pick_for(task) is None

    def test_iter_exposes_arms(self, runtime):
        a = make_code_arm(runtime)
        b = make_file_arm(runtime)
        pool = ArmPool([a, b])
        assert list(pool) == [a, b]


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestSpecialized:
    def test_make_code_arm(self, runtime):
        arm = make_code_arm(runtime)
        assert arm.arm_id == "code_arm"
        assert "code" in arm.affinity
        assert "git" in arm.affinity
        # Implementation note.
        assert SkillId("write_text_file") in arm.allowed_skills
        assert SkillId("edit_text_file") in arm.allowed_skills
        assert SkillId("git_status") in arm.allowed_skills
        assert SkillId("git_commit") in arm.allowed_skills
        # Implementation note.
        assert SkillId("exec_shell") not in arm.allowed_skills
        assert SkillId("git_push") not in arm.allowed_skills

    def test_make_code_arm_with_exec(self, runtime):
        arm = make_code_arm(runtime, enable_exec=True)
        assert SkillId("exec_shell") in arm.allowed_skills
        assert "shell" in arm.affinity
        assert SkillId("git_push") not in arm.allowed_skills

    def test_make_code_arm_with_git_network(self, runtime):
        arm = make_code_arm(runtime, enable_git_network=True)
        assert SkillId("git_push") in arm.allowed_skills
        assert SkillId("git_pull") in arm.allowed_skills
        assert SkillId("git_create_pr") in arm.allowed_skills
        assert "network" in arm.affinity

    def test_make_code_arm_full(self, runtime):
        arm = make_code_arm(runtime, enable_exec=True, enable_git_network=True)
        assert SkillId("exec_shell") in arm.allowed_skills
        assert SkillId("git_push") in arm.allowed_skills
        assert SkillId("write_text_file") in arm.allowed_skills

    def test_make_search_arm(self, runtime):
        arm = make_search_arm(runtime)
        assert arm.arm_id == "search_arm"
        assert "search" in arm.affinity
        # Implementation note.
        assert SkillId("read_file") not in arm.allowed_skills

    def test_make_file_arm(self, runtime):
        arm = make_file_arm(runtime)
        assert arm.arm_id == "file_arm"
        assert "file" in arm.affinity
        assert SkillId("list_dir") in arm.allowed_skills

    def test_specialized_arms_are_distinct_instances(self, runtime):
        a1 = make_code_arm(runtime)
        a2 = make_code_arm(runtime)
        # Implementation note.
        assert a1 is not a2
        assert a1.arm_id == a2.arm_id

    def test_code_arm_handles_code_task(self, runtime, budget):
        arm = make_code_arm(runtime)
        task = _make_assignment(
            [
                TaskNode(
                    node_id="n0",
                    skill_ref=SkillId("read_file"),
                    args_template={"path": "foo.py"},
                ),
            ]
        )
        assert arm.can_handle(task)
        result = arm.handle(task, budget)
        assert result.status == "success"
