from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.execution.loops.models import (
    LoopAttempt,
    LoopRun,
    LoopRunStatus,
    VerifierFinding,
    VerifierResult,
)
from runtime.execution.loops.store import LoopRunStore
from runtime.platform.process.task_supervisor import TaskRunStatus, TaskSupervisor
from runtime.safety.auth.identity import Identity, IdentityStore
from runtime.sensing.gateway.loop_router import create_loop_router


def _completed_review() -> dict[str, object]:
    replay = {
        "schema": "echo.task_run_replay.v1",
        "fingerprint": "abc123def4567890",
        "case_id": "task-run:abc123def4567890",
        "replayable": True,
        "step_count": 3,
        "steps": [
            {"kind": "task_start", "goal": "Ship the loop runtime", "mode": "code"},
            {
                "kind": "tool_end",
                "tool": "verifier:python_repo_patch",
                "tool_call_id": "verifier-1",
                "status": "success",
                "is_error": False,
                "output_preview": "all checks passed",
            },
            {
                "kind": "task_event",
                "event_type": "LOOP_RUN_COMPLETED",
                "status": "completed",
                "reason": "",
            },
        ],
        "safety": {
            "raw_messages_included": False,
            "tool_outputs_truncated": True,
            "approval_args_are_previews": True,
        },
    }
    return {
        "schema": "echo.task_run_review.v1",
        "task_id": "run-1",
        "thread_id": "run-1",
        "turn_id": "run-1",
        "agent_id": "loop_controller",
        "status": "completed",
        "score": 1.0,
        "score_reasons": ["status:completed"],
        "summary": {"attempt_count": 1, "verifier_profile": "python_repo_patch"},
        "findings": [],
        "replay": replay,
        "resume": {
            "available": False,
            "source": "loop_runs",
            "latest_checkpoint": {},
        },
        "learning_candidates": [],
        "backlog_candidates": [],
    }


class _StubController:
    def __init__(self, store: LoopRunStore) -> None:
        self.store = store
        self.execute_calls: list[str] = []
        self.restart_calls: list[str] = []
        self.resume_calls: list[str] = []

    def execute(self, run_id: str):
        self.execute_calls.append(run_id)
        return self.store.mutate(
            run_id,
            lambda current: current.model_copy(
                update={
                    "status": LoopRunStatus.COMPLETED,
                    "last_review": _completed_review(),
                }
            ),
        )

    def restart(
        self,
        run_id: str,
        *,
        goal: str | None = None,
        thread_id: str | None = None,
        workspace_path: str | None = None,
        reuse_workspace: bool = True,
        policy=None,
    ):
        self.restart_calls.append(run_id)
        source = self.store.get(run_id)
        if source is None:
            raise KeyError(run_id)
        if source.status in {
            LoopRunStatus.PENDING,
            LoopRunStatus.RUNNING,
            LoopRunStatus.VERIFYING,
            LoopRunStatus.REPAIRING,
        }:
            raise ValueError("loop run is still active")
        return self._spawn_child(
            source,
            goal=goal,
            thread_id=thread_id,
            workspace_path=workspace_path,
            reuse_workspace=reuse_workspace,
            policy=policy,
            resume_checkpoint_id=None,
        )

    def resume(
        self,
        run_id: str,
        *,
        goal: str | None = None,
        thread_id: str | None = None,
        workspace_path: str | None = None,
        reuse_workspace: bool = True,
        policy=None,
    ):
        self.resume_calls.append(run_id)
        source = self.store.get(run_id)
        if source is None:
            raise KeyError(run_id)
        if source.status not in {
            LoopRunStatus.FAILED,
            LoopRunStatus.CANCELLED,
            LoopRunStatus.INTERRUPTED,
        }:
            raise ValueError("loop run is not resumable")
        return self._spawn_child(
            source,
            goal=goal,
            thread_id=thread_id,
            workspace_path=workspace_path,
            reuse_workspace=reuse_workspace,
            policy=policy,
            resume_checkpoint_id=f"loop-run:{source.run_id}:attempt:{len(source.attempts)}",
        )

    def _spawn_child(
        self,
        source: LoopRun,
        *,
        goal: str | None,
        thread_id: str | None,
        workspace_path: str | None,
        reuse_workspace: bool,
        policy,
        resume_checkpoint_id: str | None,
    ) -> LoopRun:
        next_goal = str(goal or "").strip() or source.goal
        next_thread_id = thread_id if thread_id is not None else source.thread_id
        next_workspace_path = (
            workspace_path
            if workspace_path is not None
            else source.workspace_path
            if reuse_workspace
            else None
        )
        next_policy = (
            policy.model_copy(deep=True)
            if policy is not None
            else source.policy.model_copy(deep=True)
        )
        child = LoopRun(
            owner_id=source.owner_id,
            parent_run_id=source.run_id,
            origin_run_id=source.origin_run_id or source.run_id,
            resume_checkpoint_id=resume_checkpoint_id,
            goal=next_goal,
            mode=source.mode,
            thread_id=next_thread_id,
            workspace_path=next_workspace_path,
            policy=next_policy,
        )
        return self.store.create(child)


class _StubDispatcher:
    def __init__(self, store: LoopRunStore) -> None:
        self.store = store
        self.calls: list[str] = []
        self.cancel_calls: list[tuple[str, str]] = []
        self.running: set[str] = set()

    def submit(self, run_id: str) -> bool:
        self.calls.append(run_id)
        self.running.add(run_id)
        return True

    def is_running(self, run_id: str) -> bool:
        return run_id in self.running

    def cancel(self, run_id: str, *, reason: str = "cancelled by operator") -> dict[str, object]:
        self.cancel_calls.append((run_id, reason))
        self.running.discard(run_id)
        run = self.store.mutate(
            run_id,
            lambda current, reason=reason: current.model_copy(
                update={
                    "status": LoopRunStatus.CANCELLED,
                    "cancel_requested_at": current.cancel_requested_at or current.updated_at,
                    "cancel_reason": reason,
                    "last_error": reason,
                    "completed_at": current.completed_at or current.updated_at,
                }
            ),
        )
        return {
            "run": run,
            "reason": reason,
            "source_cancelled": True,
            "future_cancelled": True,
        }


def _build_client(tmp_path, *, include_task_supervisor: bool = False):
    identity_store = IdentityStore()
    identity_store.add(
        Identity(actor_id="alice", roles=("operator",)),
        api_key_plaintext="sk-alice",
    )
    identity_store.add(Identity(actor_id="bob"), api_key_plaintext="sk-bob")
    identity_store.add(
        Identity(actor_id="admin", roles=("admin",)),
        api_key_plaintext="sk-admin",
    )
    store = LoopRunStore(tmp_path / "loop_runs.json")
    controller = _StubController(store)
    dispatcher = _StubDispatcher(store)
    task_supervisor = (
        TaskSupervisor.from_path(tmp_path / "task_runs.json", holder_id="loop-worker")
        if include_task_supervisor
        else None
    )
    app = FastAPI()
    app.include_router(
        create_loop_router(
            store=store,
            controller=controller,
            dispatcher=dispatcher,
            task_supervisor=task_supervisor,
            identity_store=identity_store,
            require_auth=True,
        )
    )
    client = TestClient(app)
    return client, controller, dispatcher, store, task_supervisor


def test_loop_execution_rejects_ordinary_authenticated_user(tmp_path) -> None:
    client, _controller, _dispatcher, _store, _task_supervisor = _build_client(tmp_path)

    response = client.post(
        "/api/loops/start",
        headers={"Authorization": "Bearer sk-bob"},
        json={"goal": "scan host", "workspace_path": "/etc", "execute": True},
    )

    assert response.status_code == 403


def test_legacy_unowned_loop_is_hidden_except_for_admin(tmp_path) -> None:
    client, _controller, _dispatcher, store, _task_supervisor = _build_client(tmp_path)
    legacy = store.create(LoopRun(goal="legacy run", owner_id=None))

    assert (
        client.get(
            f"/api/loops/{legacy.run_id}",
            headers={"Authorization": "Bearer sk-bob"},
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/loops/{legacy.run_id}",
            headers={"Authorization": "Bearer sk-admin"},
        ).status_code
        == 200
    )
    admin_list = client.get(
        "/api/loops",
        headers={"Authorization": "Bearer sk-admin"},
    ).json()
    assert [item["run_id"] for item in admin_list["runs"]] == [legacy.run_id]


def test_loop_router_create_list_get_and_execute_with_owner_isolation(tmp_path) -> None:
    client, controller, dispatcher, _store, _task_supervisor = _build_client(tmp_path)

    created = client.post(
        "/api/loops/start",
        json={
            "goal": "Ship the loop runtime",
            "thread_id": "   ",
        },
        headers={"Authorization": "Bearer sk-alice"},
    )
    assert created.status_code == 200
    run = created.json()
    run_id = run["run_id"]
    assert run["tenant_id"] == "legacy:alice"
    assert run["thread_id"] is None
    assert run["status"] == "pending"

    listing = client.get(
        "/api/loops",
        headers={"Authorization": "Bearer sk-alice"},
    )
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["runs"][0]["run_id"] == run_id

    denied = client.get(
        f"/api/loops/{run_id}",
        headers={"Authorization": "Bearer sk-bob"},
    )
    assert denied.status_code == 404

    executed = client.post(
        f"/api/loops/{run_id}/execute",
        headers={"Authorization": "Bearer sk-alice"},
    )
    assert executed.status_code == 200
    assert executed.json()["status"] == "completed"
    assert controller.execute_calls == [run_id]
    assert dispatcher.calls == []

    review = client.get(
        f"/api/loops/{run_id}/review",
        headers={"Authorization": "Bearer sk-alice"},
    )
    assert review.status_code == 200
    assert review.json()["review"]["status"] == "completed"

    replay_case = client.get(
        f"/api/loops/{run_id}/replay-case",
        headers={"Authorization": "Bearer sk-alice"},
    )
    assert replay_case.status_code == 200
    assert replay_case.json()["replay_case"]["case_id"].startswith("task-run:")

    evaluation = client.get(
        f"/api/loops/{run_id}/replay-evaluation",
        headers={"Authorization": "Bearer sk-alice"},
    )
    assert evaluation.status_code == 200
    assert evaluation.json()["evaluation"]["passed"] is True

    overview = client.get(
        "/api/loops/overview",
        headers={"Authorization": "Bearer sk-alice"},
    )
    assert overview.status_code == 200
    assert overview.json()["total"] == 1
    assert overview.json()["reviewed_runs"] == 1
    assert overview.json()["by_status"]["completed"] == 1

    status = client.get(
        f"/api/loops/{run_id}/status",
        headers={"Authorization": "Bearer sk-alice"},
    )
    assert status.status_code == 200
    assert status.json()["is_running"] is False
    assert status.json()["review_available"] is True


def test_loop_router_execute_requires_controller_when_requested(tmp_path) -> None:
    identity_store = IdentityStore()
    identity_store.add(
        Identity(actor_id="alice", roles=("operator",)),
        api_key_plaintext="sk-alice",
    )
    store = LoopRunStore(tmp_path / "loop_runs.json")
    app = FastAPI()
    app.include_router(
        create_loop_router(
            store=store,
            controller=None,
            identity_store=identity_store,
            require_auth=True,
        )
    )
    client = TestClient(app)

    response = client.post(
        "/api/loops/start",
        json={"goal": "Execute immediately", "execute": True},
        headers={"Authorization": "Bearer sk-alice"},
    )

    assert response.status_code == 503
    assert store.count(owner_id="alice") == 0


def test_loop_router_status_degrades_without_dispatcher(tmp_path) -> None:
    identity_store = IdentityStore()
    identity_store.add(
        Identity(actor_id="alice", roles=("operator",)),
        api_key_plaintext="sk-alice",
    )
    store = LoopRunStore(tmp_path / "loop_runs.json")
    app = FastAPI()
    app.include_router(
        create_loop_router(
            store=store,
            controller=None,
            dispatcher=None,
            identity_store=identity_store,
            require_auth=True,
        )
    )
    client = TestClient(app)

    created = client.post(
        "/api/loops/start",
        json={"goal": "Inspect loop status"},
        headers={"Authorization": "Bearer sk-alice"},
    )
    assert created.status_code == 200
    run_id = created.json()["run_id"]

    status = client.get(
        f"/api/loops/{run_id}/status",
        headers={"Authorization": "Bearer sk-alice"},
    )
    assert status.status_code == 200
    assert status.json()["is_running"] is False
    assert status.json()["status"] == "pending"

    replay_case = client.get(
        f"/api/loops/{run_id}/replay-case",
        headers={"Authorization": "Bearer sk-alice"},
    )
    assert replay_case.status_code == 409

    cancelled = client.post(
        f"/api/loops/{run_id}/cancel",
        json={"reason": "skip this run"},
        headers={"Authorization": "Bearer sk-alice"},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["cancel_reason"] == "skip this run"


def test_loop_router_can_background_dispatch_from_start_and_endpoint(tmp_path) -> None:
    client, _controller, dispatcher, _store, _task_supervisor = _build_client(tmp_path)

    created = client.post(
        "/api/loops/start",
        json={
            "goal": "Run in background",
            "execute": True,
            "background": True,
        },
        headers={"Authorization": "Bearer sk-alice"},
    )
    assert created.status_code == 200
    run_id = created.json()["run_id"]
    assert dispatcher.calls == [run_id]

    status = client.get(
        f"/api/loops/{run_id}/status",
        headers={"Authorization": "Bearer sk-alice"},
    )
    assert status.status_code == 200
    assert status.json()["is_running"] is True
    assert status.json()["attempt_count"] == 0

    overview = client.get(
        "/api/loops/overview",
        headers={"Authorization": "Bearer sk-alice"},
    )
    assert overview.status_code == 200
    assert overview.json()["active_dispatches"] == 1
    assert overview.json()["active_run_ids"] == [run_id]

    dispatched = client.post(
        f"/api/loops/{run_id}/dispatch",
        headers={"Authorization": "Bearer sk-alice"},
    )
    assert dispatched.status_code == 200
    assert dispatcher.calls == [run_id, run_id]

    cancelled = client.post(
        f"/api/loops/{run_id}/cancel",
        json={"reason": "operator stop"},
        headers={"Authorization": "Bearer sk-alice"},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["cancel_reason"] == "operator stop"
    assert dispatcher.cancel_calls == [(run_id, "operator stop")]


def test_loop_router_can_restart_terminal_run_and_execute_child(tmp_path) -> None:
    client, controller, dispatcher, store, _task_supervisor = _build_client(tmp_path)
    source = store.create(
        LoopRun(
            owner_id="alice",
            goal="Ship the loop runtime",
            thread_id="thread-loop",
            workspace_path=str(tmp_path / "workspace"),
            status=LoopRunStatus.COMPLETED,
        )
    )

    restarted = client.post(
        f"/api/loops/{source.run_id}/restart",
        json={
            "goal": "Continue hardening",
            "execute": True,
        },
        headers={"Authorization": "Bearer sk-alice"},
    )

    assert restarted.status_code == 200
    child = restarted.json()
    assert child["run_id"] != source.run_id
    assert child["status"] == "completed"
    assert child["goal"] == "Continue hardening"
    assert child["thread_id"] == source.thread_id
    assert child["workspace_path"] == source.workspace_path
    assert child["parent_run_id"] == source.run_id
    assert child["origin_run_id"] == source.run_id
    assert child["resume_checkpoint_id"] is None
    assert controller.restart_calls == [source.run_id]
    assert controller.execute_calls == [child["run_id"]]
    assert dispatcher.calls == []


def test_loop_router_resume_rejects_completed_run(tmp_path) -> None:
    client, controller, _dispatcher, store, _task_supervisor = _build_client(tmp_path)
    source = store.create(
        LoopRun(
            owner_id="alice",
            goal="Already complete",
            status=LoopRunStatus.COMPLETED,
        )
    )

    resumed = client.post(
        f"/api/loops/{source.run_id}/resume",
        headers={"Authorization": "Bearer sk-alice"},
    )

    assert resumed.status_code == 409
    assert resumed.json()["detail"] == "loop run is not resumable"
    assert controller.resume_calls == [source.run_id]


def test_loop_router_can_resume_interrupted_run(tmp_path) -> None:
    """Audit R-02: a run reconciled to ``interrupted`` at startup (the
    process died mid-run) must be resumable like a failed run, not stuck
    behind "loop run is still active"."""
    client, controller, dispatcher, store, _task_supervisor = _build_client(tmp_path)
    source = store.create(
        LoopRun(
            owner_id="alice",
            goal="Crashed mid-flight",
            thread_id="thread-interrupted",
            workspace_path=str(tmp_path / "workspace"),
            status=LoopRunStatus.RUNNING,
        )
    )

    # Simulate the startup sweep that folds orphaned active runs.
    assert store.reconcile_interrupted() == [source.run_id]
    assert store.get(source.run_id).status is LoopRunStatus.INTERRUPTED

    resumed = client.post(
        f"/api/loops/{source.run_id}/resume",
        json={
            "execute": True,
            "background": True,
            "thread_id": "thread-resumed",
            "reuse_workspace": False,
        },
        headers={"Authorization": "Bearer sk-alice"},
    )

    assert resumed.status_code == 200
    child = resumed.json()
    assert child["status"] == "pending"
    assert child["goal"] == source.goal
    assert child["parent_run_id"] == source.run_id
    assert controller.resume_calls == [source.run_id]
    assert dispatcher.calls == [child["run_id"]]


def test_loop_router_can_resume_failed_run_in_background(tmp_path) -> None:
    client, controller, dispatcher, store, _task_supervisor = _build_client(tmp_path)
    source = store.create(
        LoopRun(
            owner_id="alice",
            origin_run_id="root-run",
            goal="Fix remaining verifier failures",
            thread_id="thread-failed",
            workspace_path=str(tmp_path / "workspace"),
            status=LoopRunStatus.FAILED,
        )
    )

    resumed = client.post(
        f"/api/loops/{source.run_id}/resume",
        json={
            "execute": True,
            "background": True,
            "thread_id": "thread-resumed",
            "reuse_workspace": False,
        },
        headers={"Authorization": "Bearer sk-alice"},
    )

    assert resumed.status_code == 200
    child = resumed.json()
    assert child["status"] == "pending"
    assert child["goal"] == source.goal
    assert child["thread_id"] == "thread-resumed"
    assert child["workspace_path"] is None
    assert child["parent_run_id"] == source.run_id
    assert child["origin_run_id"] == "root-run"
    assert (
        child["resume_checkpoint_id"] == f"loop-run:{source.run_id}:attempt:{len(source.attempts)}"
    )
    assert controller.resume_calls == [source.run_id]
    assert controller.execute_calls == []
    assert dispatcher.calls == [child["run_id"]]

    status = client.get(
        f"/api/loops/{child['run_id']}/status",
        headers={"Authorization": "Bearer sk-alice"},
    )
    assert status.status_code == 200
    assert status.json()["is_running"] is True
    assert status.json()["parent_run_id"] == source.run_id
    assert status.json()["origin_run_id"] == "root-run"
    assert status.json()["resume_checkpoint_id"] == child["resume_checkpoint_id"]
    recovery_audit = status.json()["recovery_audit"]
    assert recovery_audit["resumed_from"] == {
        "available": True,
        "parent_run_id": source.run_id,
        "origin_run_id": "root-run",
        "checkpoint_id": child["resume_checkpoint_id"],
    }
    assert recovery_audit["safety"]["raw_checkpoint_state_included"] is False


def test_loop_router_exposes_resume_proposal_for_failed_run(tmp_path) -> None:
    client, _controller, _dispatcher, store, _task_supervisor = _build_client(tmp_path)
    source = store.create(
        LoopRun(
            owner_id="alice",
            goal="Fix remaining verifier failures",
            thread_id="thread-failed",
            workspace_path=str(tmp_path / "workspace"),
            status=LoopRunStatus.FAILED,
            attempts=[
                LoopAttempt(
                    attempt_index=1,
                    prompt="Fix remaining verifier failures",
                    status="completed",
                    success=False,
                    final_answer="patched once",
                    verifier_result=VerifierResult(
                        profile="python_repo_patch",
                        kind="python",
                        passed=False,
                        findings=[
                            VerifierFinding(
                                name="pytest",
                                passed=False,
                                exit_code=1,
                                stderr="1 failing test remains",
                            )
                        ],
                        summary="failed checks: pytest",
                    ),
                )
            ],
            last_verifier_result=VerifierResult(
                profile="python_repo_patch",
                kind="python",
                passed=False,
                findings=[
                    VerifierFinding(
                        name="pytest",
                        passed=False,
                        exit_code=1,
                        stderr="1 failing test remains",
                    )
                ],
                summary="failed checks: pytest",
            ),
            last_error="failed checks: pytest",
        )
    )

    proposal = client.get(
        f"/api/loops/{source.run_id}/resume-proposal",
        headers={"Authorization": "Bearer sk-alice"},
    )

    assert proposal.status_code == 200
    body = proposal.json()["proposal"]
    assert body["checkpoint"]["task_id"] == source.run_id
    assert body["checkpoint"]["type"] == "loop_run"
    assert body["recovery_hints"]["phase"] == "failed"
    assert body["recovery_hints"]["failed_checks"] == ["pytest"]
    assert body["safety"]["raw_state_included"] is False


def test_loop_router_status_includes_recovery_audit_for_failed_run(tmp_path) -> None:
    client, _controller, _dispatcher, store, _task_supervisor = _build_client(tmp_path)
    source = store.create(
        LoopRun(
            owner_id="alice",
            goal="Fix remaining verifier failures",
            thread_id="thread-failed",
            workspace_path=str(tmp_path / "workspace"),
            status=LoopRunStatus.FAILED,
            attempts=[
                LoopAttempt(
                    attempt_index=1,
                    prompt="Fix remaining verifier failures",
                    status="completed",
                    success=False,
                    final_answer="patched once",
                    verifier_result=VerifierResult(
                        profile="python_repo_patch",
                        kind="python",
                        passed=False,
                        findings=[
                            VerifierFinding(
                                name="pytest",
                                passed=False,
                                exit_code=1,
                                stderr="1 failing test remains",
                            )
                        ],
                        summary="failed checks: pytest",
                    ),
                )
            ],
            last_verifier_result=VerifierResult(
                profile="python_repo_patch",
                kind="python",
                passed=False,
                findings=[
                    VerifierFinding(
                        name="pytest",
                        passed=False,
                        exit_code=1,
                        stderr="1 failing test remains",
                    )
                ],
                summary="failed checks: pytest",
            ),
            last_error="failed checks: pytest",
            last_review=_completed_review(),
        )
    )

    status = client.get(
        f"/api/loops/{source.run_id}/status",
        headers={"Authorization": "Bearer sk-alice"},
    )
    overview = client.get(
        "/api/loops/overview",
        headers={"Authorization": "Bearer sk-alice"},
    )

    assert status.status_code == 200
    audit = status.json()["recovery_audit"]
    assert audit["schema"] == "echo.loop_recovery_audit.v1"
    assert audit["checkpoint"]["available"] is True
    assert audit["checkpoint"]["id"] == f"loop-run:{source.run_id}:attempt:1"
    assert audit["resume"]["available"] is True
    assert audit["resume"]["latest_checkpoint_id"] == audit["checkpoint"]["id"]
    assert audit["review"]["available"] is True
    assert audit["review"]["score"] == 1.0
    assert audit["replay"]["replayable"] is True
    assert str(audit["replay"]["case_id"]).startswith("task-run:")
    assert audit["safety"]["raw_checkpoint_state_included"] is False
    assert audit["safety"]["raw_replay_steps_included"] is False

    assert overview.status_code == 200
    overview_audit = overview.json()["recovery_audit"]
    assert overview_audit["checkpoint_available_count"] == 1
    assert overview_audit["resume_available_count"] == 1
    assert overview_audit["replay_available_count"] == 1


def test_loop_router_resume_proposal_rejects_non_resumable_run(tmp_path) -> None:
    client, _controller, _dispatcher, store, _task_supervisor = _build_client(tmp_path)
    source = store.create(
        LoopRun(
            owner_id="alice",
            goal="Already complete",
            status=LoopRunStatus.COMPLETED,
        )
    )

    proposal = client.get(
        f"/api/loops/{source.run_id}/resume-proposal",
        headers={"Authorization": "Bearer sk-alice"},
    )

    assert proposal.status_code == 409
    assert proposal.json()["detail"] == "loop run is not resumable"


def test_loop_router_status_and_overview_include_task_lease_health(tmp_path) -> None:
    client, _controller, _dispatcher, store, task_supervisor = _build_client(
        tmp_path,
        include_task_supervisor=True,
    )
    assert task_supervisor is not None
    run = store.create(
        LoopRun(
            owner_id="alice",
            goal="Inspect task health",
            status=LoopRunStatus.RUNNING,
        )
    )
    task_supervisor.start_task(
        task_id=run.run_id,
        kind="loop",
        owner_id="alice",
        title=run.goal,
        goal=run.goal,
        status=TaskRunStatus.RUNNING,
    )

    def _expire(record):
        assert record.lease is not None
        return record.model_copy(
            update={
                "latest_checkpoint_id": "ckpt-loop",
                "lease": record.lease.model_copy(update={"expires_at": 1}),
            },
            deep=True,
        )

    task_supervisor.store.mutate(run.run_id, _expire)

    status = client.get(
        f"/api/loops/{run.run_id}/status",
        headers={"Authorization": "Bearer sk-alice"},
    )
    overview = client.get(
        "/api/loops/overview",
        headers={"Authorization": "Bearer sk-alice"},
    )

    assert status.status_code == 200
    status_body = status.json()
    assert status_body["task_run"]["task_id"] == run.run_id
    assert status_body["task_lease_health"]["state"] == "expired"
    assert status_body["task_lease_health"]["holder_id"] == "loop-worker"
    assert status_body["task_lease_health"]["recommended_action"] == "takeover_and_resume"
    assert status_body["task_lease_health"]["can_takeover"] is True
    assert status_body["task_lease_health"]["can_resume"] is True
    assert status_body["task_recovery"]["recommended_action"] == "takeover_and_resume"
    assert status_body["task_recovery"]["latest_checkpoint_id"] == "ckpt-loop"
    assert status_body["recovery_audit"]["checkpoint"]["available"] is True
    assert status_body["recovery_audit"]["replay"]["replayable"] is True
    assert status_body["recovery_audit"]["resume"]["available"] is False

    assert overview.status_code == 200
    health = overview.json()["task_health"]
    assert health["tracked_count"] == 1
    assert health["unhealthy_count"] == 1
    assert health["unhealthy_task_ids"] == [run.run_id]
    assert health["takeover_recommended_count"] == 1
    assert health["resumable_count"] == 1
    assert health["takeover_task_ids"] == [run.run_id]
    assert health["resumable_task_ids"] == [run.run_id]
    assert health["by_recommended_action"] == {"takeover_and_resume": 1}
    assert health["items"][0]["state"] == "expired"
    assert overview.json()["recovery_audit"]["checkpoint_available_count"] == 1
    assert overview.json()["recovery_audit"]["replay_available_count"] == 1

