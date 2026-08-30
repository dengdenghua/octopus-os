from __future__ import annotations

from types import SimpleNamespace

from runtime.core.cerebrum.react_step_evaluator import RuntimeStepEvaluator
from runtime.core.cerebrum.react_types import ReActResult
from runtime.execution.loops._controller_attempt import (
    _REPAIR_ATTEMPT_ALLOWED_SKILL_IDS,
)
from runtime.execution.loops.controller import LoopController
from runtime.execution.loops.models import LoopPolicy, LoopRun, LoopRunStatus, VerifierResult
from runtime.execution.loops.store import LoopRunStore
from runtime.execution.misc.capability_permissions import permission_group_for_skill
from runtime.execution.tool_engine._executor_helpers import (
    _check_task_capability_permission,
)
from runtime.platform.models import SkillId
from runtime.platform.process.session import Session, current_session, session_scope
from runtime.platform.process.task_supervisor import TaskCapabilityManifest, TaskSupervisor
from runtime.platform.runtime_policy.workspaces import WorkspaceManager


class _VerifierRegistry:
    def __init__(self, results: list[VerifierResult]) -> None:
        self._results = list(results)

    def run(self, profile: str, workspace_path: str) -> VerifierResult:
        return self._results.pop(0)


def test_exact_skill_allowlist_denies_unknown_and_domain_tools() -> None:
    manifest = TaskCapabilityManifest(
        allowed_skill_ids=["read_file", "edit_file"],
        groups={"builtin": True, "fs_write": True},
    )
    metadata = {
        "task_id": "repair-2",
        "task_capability_manifest": manifest.model_dump(mode="json"),
    }

    assert permission_group_for_skill("unregistered_tool") is None
    assert permission_group_for_skill("paper_trading.trade") is None
    with session_scope(Session(actor="alice", metadata=metadata)):
        for denied_skill in (
            "unregistered_tool",
            "exec_shell",
            "browser_click",
            "paper_trading.trade",
        ):
            allowed, reason = _check_task_capability_permission(SkillId(denied_skill))
            assert allowed is False
            assert reason == f"task capability skill disabled: {denied_skill} for task repair-2"

        assert _check_task_capability_permission(SkillId("read_file")) == (True, None)
        assert _check_task_capability_permission(SkillId("edit_file")) == (True, None)


def test_none_skill_allowlist_preserves_legacy_group_only_behavior() -> None:
    manifest = TaskCapabilityManifest(groups={"shell": False})
    assert manifest.allowed_skill_ids is None
    assert manifest.allows_skill("unregistered_tool") is True

    with session_scope(
        Session(metadata={"task_capability_manifest": manifest.model_dump(mode="json")})
    ):
        assert _check_task_capability_permission(SkillId("unregistered_tool")) == (
            True,
            None,
        )
        allowed, reason = _check_task_capability_permission(SkillId("exec_shell"))
        assert allowed is False
        assert reason == "task capability group disabled: shell"


def test_second_loop_attempt_uses_restricted_manifest_and_same_workspace(tmp_path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    store = LoopRunStore(tmp_path / "loop_runs.json")
    supervisor = TaskSupervisor.from_path(
        tmp_path / "task_runs.json",
        holder_id="loop-worker",
    )
    run = LoopRun(
        owner_id="alice",
        goal="repair one exact file",
        workspace_path=str(workspace),
        policy=LoopPolicy(max_attempts=2, max_iterations=1),
    )
    store.create(run)
    observed: list[dict[str, object]] = []

    # Intentionally retain the legacy runner signature: attempt_index must not
    # leak into custom/stub runner kwargs.
    def runner(*, stack, intent, agent, model=None, max_iterations=0, thread_id=None):
        session = current_session()
        assert session is not None
        manifest = TaskCapabilityManifest.model_validate(
            session.metadata["task_capability_manifest"]
        )
        observed.append(
            {
                "workspace": intent.user_context["workspace_path"],
                "session_workspace": session.metadata["workspace_path"],
                "manifest": manifest,
            }
        )
        return ReActResult(final_answer="attempt complete", success=True)

    controller = LoopController(
        store=store,
        stack=SimpleNamespace(name="stack"),
        workspace_manager=WorkspaceManager(tmp_path / "workspaces"),
        verifier_registry=_VerifierRegistry(
            [
                VerifierResult(
                    profile="auto",
                    kind="python",
                    failure_category="test_failure",
                    passed=False,
                    summary="first attempt failed verification",
                ),
                VerifierResult(
                    profile="auto",
                    kind="python",
                    passed=True,
                    summary="repair verified",
                ),
            ]
        ),
        task_supervisor=supervisor,
        react_runner=runner,
    )

    completed = controller.execute(run.run_id)

    assert completed.status == LoopRunStatus.COMPLETED
    assert len(observed) == 2
    first_manifest = observed[0]["manifest"]
    repair_manifest = observed[1]["manifest"]
    assert isinstance(first_manifest, TaskCapabilityManifest)
    assert isinstance(repair_manifest, TaskCapabilityManifest)
    assert first_manifest.allowed_skill_ids is None
    assert first_manifest.source == "loop_policy"
    assert repair_manifest.source == "loop_repair_attempt"
    assert repair_manifest.allowed_skill_ids == list(_REPAIR_ATTEMPT_ALLOWED_SKILL_IDS)
    assert repair_manifest.workspace_paths == [str(workspace)]
    assert repair_manifest.allows_group("fs_write") is True
    for disabled_group in ("web", "browser", "computer", "git", "shell", "memory"):
        assert repair_manifest.allows_group(disabled_group) is False
    assert observed[0]["workspace"] == observed[1]["workspace"] == str(workspace)
    assert observed[0]["session_workspace"] == observed[1]["session_workspace"] == str(workspace)


def test_default_loop_runner_gets_a_fresh_step_evaluator_per_attempt(
    tmp_path,
    monkeypatch,
) -> None:
    store = LoopRunStore(tmp_path / "loop_runs.json")
    workspace = tmp_path / "repo"
    workspace.mkdir()
    run = LoopRun(
        goal="repair a rejected tool call",
        workspace_path=str(workspace),
        policy=LoopPolicy(max_attempts=2, max_iterations=1),
    )
    store.create(run)
    evaluators: list[object] = []

    def default_runner(
        stack,
        intent,
        agent,
        *,
        model=None,
        max_iterations=0,
        thread_id=None,
        max_tokens_budget=0,
        max_usd_budget=0.0,
        step_evaluator=None,
    ):
        evaluators.append(step_evaluator)
        return ReActResult(
            final_answer="attempt complete",
            success=True,
            completion_receipt={"ready": True},
            completion_decision={"outcome": "completed", "success": True},
        )

    monkeypatch.setattr(
        "runtime.core.cerebrum.react_loop.run_react_loop",
        default_runner,
    )
    controller = LoopController(
        store=store,
        stack=SimpleNamespace(name="stack"),
        workspace_manager=WorkspaceManager(tmp_path / "workspaces"),
        verifier_registry=_VerifierRegistry(
            [
                VerifierResult(
                    profile="auto",
                    kind="python",
                    failure_category="test_failure",
                    passed=False,
                    summary="retry",
                ),
                VerifierResult(
                    profile="auto",
                    kind="python",
                    passed=True,
                    summary="verified",
                ),
            ]
        ),
    )

    completed = controller.execute(run.run_id)

    assert completed.status == LoopRunStatus.COMPLETED
    assert len(evaluators) == 2
    assert all(isinstance(evaluator, RuntimeStepEvaluator) for evaluator in evaluators)
    assert evaluators[0] is not evaluators[1]

