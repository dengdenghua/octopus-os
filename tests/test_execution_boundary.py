from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import pytest

from runtime.execution.engines import (
    EngineId,
    EngineSelectionError,
    ExecutionAdmissionError,
    ExecutionPhase,
    ExecutionSupervisor,
    select_execution_route,
)
from runtime.execution.host_boundary import (
    create_host_execution_boundary,
    host_execution_scope,
    inherit_host_execution_session,
)
from runtime.execution.request import (
    ExecutionDeadlineExceeded,
    current_execution_request,
)
from runtime.platform.process.scope import resolve_execution_scope
from runtime.platform.process.session import current_session, session_scope
from runtime.platform.process.task_supervisor import TaskRunStatus, TaskSupervisor


def test_host_boundary_owns_identity_scope_and_budget(tmp_path) -> None:
    boundary = create_host_execution_boundary(
        task_id="task-1",
        thread_id="thread-1",
        actor_id="actor-1",
        tenant_id="tenant-1",
        goal="整理当前项目中的文档",
        timeout_s=30,
        metadata={"task_id": "forged", "_execution_request": "forged"},
    )
    assert boundary.request.task.task_id == "task-1"
    assert boundary.request.task.thread_id == "thread-1"
    assert boundary.session.metadata["task_id"] == "task-1"
    assert boundary.session.execution_request is boundary.request
    assert boundary.request.task.resources.token_target > 0
    assert boundary.request.task.resources.remaining_seconds() is not None

    with session_scope(boundary.session):
        assert current_execution_request() is boundary.request
    assert current_execution_request() is None


def test_host_execution_scope_admits_and_finalizes_shared_task_lease(tmp_path) -> None:
    supervisor = TaskSupervisor.from_path(tmp_path / "task-runs.json")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with host_execution_scope(
        supervisor=supervisor,
        task_id="projectos:P-lease",
        thread_id="thread-lease",
        actor_id="alice",
        tenant_id="tenant-a",
        goal="run the project",
        timeout_s=30,
        metadata={"mode": "code", "workspace_path": str(workspace)},
        workspace_path=str(workspace),
        kind="project_os",
    ) as session:
        assert session is current_session()
        assert session is not None
        request = current_execution_request()
        assert request is session.execution_request
        assert request is not None
        assert request.task.task_id == "projectos:P-lease"
        assert session.execution_lease is not None
        assert session.execution_lease.assert_allowed().task_id == "projectos:P-lease"
        assert request.task.permissions.allows_write(workspace / "result.txt")

    record = supervisor.store.get("projectos:P-lease")
    assert record is not None
    assert record.status == TaskRunStatus.COMPLETED
    assert record.lease is None
    assert record.thread_id == "thread-lease"


def test_host_execution_scope_inherits_realtime_parent_without_new_lease(tmp_path) -> None:
    supervisor = TaskSupervisor.from_path(tmp_path / "task-runs.json")
    boundary = create_host_execution_boundary(
        task_id="realtime-parent",
        thread_id="thread-parent",
        actor_id="alice",
        tenant_id="tenant-a",
        goal="parent",
        timeout_s=30,
    )

    with session_scope(boundary.session), host_execution_scope(
        supervisor=supervisor,
        task_id="projectos:child",
        thread_id="thread-parent",
        actor_id="alice",
        tenant_id="tenant-a",
        goal="child",
        timeout_s=30,
    ) as child:
        assert child is boundary.session
        assert current_execution_request() is boundary.request

    assert supervisor.store.get("projectos:child") is None


def test_child_cannot_replace_parent_boundary() -> None:
    parent = create_host_execution_boundary(
        task_id="task-1",
        thread_id="thread-1",
        actor_id="actor-1",
        tenant_id="tenant-1",
        goal="inspect",
        timeout_s=30,
    ).session
    child = inherit_host_execution_session(
        parent,
        thread_id="thread-1",
        actor_id="actor-1",
        tenant_id="tenant-1",
        metadata={"_execution_request": "forged", "child": True},
    )
    assert child.execution_request is parent.execution_request
    assert child.metadata["child"] is True
    with pytest.raises(ValueError, match="actor"):
        inherit_host_execution_session(
            parent,
            thread_id="thread-1",
            actor_id="other",
            tenant_id="tenant-1",
        )


def test_child_metadata_cannot_expand_host_permission_ceiling(tmp_path) -> None:
    boundary = create_host_execution_boundary(
        task_id="task-1",
        thread_id="thread-1",
        goal="inspect",
        timeout_s=30,
        metadata={"mode": "chat", "permission_mode": "default"},
    )
    child = inherit_host_execution_session(
        boundary.session,
        thread_id="thread-1",
        actor_id=None,
        tenant_id=None,
        metadata={
            "mode": "code",
            "permission_mode": "bypassPermissions",
            "execution_environment": "local",
            "workspace_path": str(tmp_path),
        },
    )
    with session_scope(child):
        scope = resolve_execution_scope(child)
    assert scope.mode == "chat"
    assert scope.permission_mode == "default"
    assert scope.execution_environment == "sandbox"
    assert not scope.allows_write(tmp_path / "outside.txt")


def test_deadline_is_absolute_and_expires() -> None:
    boundary = create_host_execution_boundary(
        task_id="task-1",
        thread_id="thread-1",
        goal="inspect",
        timeout_s=0.01,
    )
    time.sleep(0.02)
    with pytest.raises(ExecutionDeadlineExceeded):
        boundary.request.task.resources.remaining_seconds()


def test_route_selection_keeps_orchestration_on_native() -> None:
    route = select_execution_route(coding_task=True)
    assert route.engine is EngineId.CODEX
    assert route.driver_for(ExecutionPhase.PRIMARY) == "codex_app_server"
    route = select_execution_route(project_command=True)
    assert route.engine is EngineId.NATIVE
    with pytest.raises(EngineSelectionError, match="orchestration"):
        select_execution_route(requested_engine=EngineId.CODEX, project_command=True)


@dataclass
class _Adapter:
    engine: EngineId
    calls: list[ExecutionPhase]

    async def execute(self, request: object, phase: ExecutionPhase) -> None:
        self.calls.append(phase)


def test_supervisor_binds_one_adapter_and_rejects_duplicate_primary() -> None:
    adapter = _Adapter(EngineId.NATIVE, [])
    before: list[tuple[ExecutionPhase, int]] = []
    supervisor = ExecutionSupervisor(
        select_execution_route(),
        adapter,
        is_interrupted=lambda: False,
        before_invoke=lambda phase, number: _record(before, phase, number),
    )
    asyncio.run(supervisor.execute("request"))
    with pytest.raises(ExecutionAdmissionError, match="primary"):
        asyncio.run(supervisor.execute("request"))
    assert adapter.calls == [ExecutionPhase.PRIMARY]
    assert before == [(ExecutionPhase.PRIMARY, 1)]


async def _record(target: list[tuple[ExecutionPhase, int]], phase: ExecutionPhase, number: int) -> None:
    target.append((phase, number))
