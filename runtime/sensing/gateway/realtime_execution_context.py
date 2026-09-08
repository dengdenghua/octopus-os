"""Project a validated realtime turn into the shared engine request."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from runtime.execution.artifact_contracts import HandoffRecorder
from runtime.execution.request import (
    ExecutionDeadlineExceeded,
    ExecutionRequest,
    ExecutionResources,
    ExecutionTask,
    execution_request_scope,
)
from runtime.execution.tool_engine.session_metadata import project_tool_session_metadata
from runtime.platform.config.schema import BudgetConfig
from runtime.platform.models import ParsedIntent
from runtime.platform.process.scope import resolve_execution_scope
from runtime.platform.process.session import Session, session_scope
from runtime.protocol import Turn


@dataclass(slots=True)
class RealtimeExecutionContext:
    """One task and Session per bound turn; no additional lifecycle store."""

    task: ExecutionTask | None = None
    session: Session | None = None
    maximum_duration_s: float | None = None
    handoff_recorder: HandoffRecorder | None = None
    trusted_workspace_mode: str | None = None
    approval_provider: Any = None

    def _initialize(self, runtime: Any, turn: Turn, agent: Any, intent: ParsedIntent) -> None:
        from runtime.core.cerebrum.pause_control import turn_wall_time_cap_s
        from runtime.core.cerebrum.react_explicit_reads import _explicit_read_only_goal
        from runtime.core.cerebrum.todo_protocol import _is_read_only_analysis_goal

        # The intent has already passed the authenticated gateway. Still use
        # the tool projection so arbitrary client/engine state cannot become
        # a host Session field. Identity and workspace come from Turn itself.
        context = intent.user_context or {}
        # The gateway stamps its grants at the top level. Nested metadata is
        # still presentation input in legacy embeddings and must not regain
        # priority through the tool bridge's compatibility projection.
        metadata = project_tool_session_metadata(
            {key: value for key, value in context.items() if key != "metadata"}
        )
        actor = getattr(turn.params, "owner_actor_id", None)
        tenant = getattr(turn.params, "tenant_id", None)
        if bool(actor) != bool(tenant):
            raise ValueError("authenticated execution principal is incomplete")
        if tenant:
            metadata["tenant_id"] = tenant
            metadata["owner_actor_id"] = actor
        if turn.execution_workspace_path:
            metadata["workspace_path"] = turn.execution_workspace_path
            # The gateway has resolved this directory for the authenticated
            # turn. Chat/research still need to read their working directory,
            # even when only output/final is writable. A Python Path keeps
            # this grant out of JSON/client metadata projection.
            metadata["_host_workspace_read_root"] = Path(turn.execution_workspace_path)
            if self.trusted_workspace_mode is not None and context.get("mode") != "plan":
                metadata["mode"] = self.trusted_workspace_mode
        workspaces = getattr(runtime, "_workspaces", None)
        if workspaces is not None:
            metadata["_artifact_output_root"] = str(workspaces.layout(turn.thread_id).final)
        goal = str(intent.normalized_goal or intent.raw or "")
        if _explicit_read_only_goal(goal) or _is_read_only_analysis_goal(goal):
            metadata["_read_only_turn_enforced"] = True
        # Shallow Session copies share coordination tables across engine calls
        # in this turn. They never escape into the engine's serialized context.
        metadata.update(
            _file_write_leases={},
            _file_write_lease_handoffs={},
            _file_write_lease_history=[],
            _file_read_snapshots={},
        )
        session = Session(
            actor=actor,
            agent=agent if hasattr(agent, "agent_id") else None,
            thread_id=turn.thread_id,
            conversation_id=turn.thread_id,
            turn_id=turn.id,
            metadata=metadata,
        )
        config = getattr(getattr(getattr(runtime, "_stack", None), "config", None), "budget", None)
        budget = config if isinstance(config, BudgetConfig) else BudgetConfig()
        cap = turn_wall_time_cap_s(
            goal_mode=bool(context.get("goal_mode")),
            research_mode=context.get("personal_mode") == "research"
            or context.get("mode") == "research",
            swarm_mode=bool(context.get("serve_mesh") or context.get("team_mode")),
        )
        if self.maximum_duration_s is not None:
            cap = min(cap, self.maximum_duration_s) if cap > 0 else self.maximum_duration_s
        permissions = resolve_execution_scope(session)
        if metadata.get("_read_only_turn_enforced"):
            permissions = replace(permissions, writable_roots=())
        task = ExecutionTask(
            task_id=turn.id,
            thread_id=turn.thread_id,
            actor_id=actor,
            tenant_id=tenant,
            goal=goal,
            execution_engine=turn.execution.engine if turn.execution else None,
            approval_provider=self.approval_provider,
            authorization_intent=str(intent.raw or intent.normalized_goal or ""),
            server_auto_approve=bool(getattr(runtime, "_allow_client_auto_approve", False)),
            permissions=permissions,
            resources=ExecutionResources(
                token_target=budget.max_tokens,
                usd_target=budget.max_usd,
                deadline=time.monotonic() + cap if cap > 0 else None,
            ),
        )
        self.task = task
        metadata["_execution_task"] = task
        if self.handoff_recorder is not None:
            metadata["_execution_handoff_recorder"] = self.handoff_recorder
        self.session = session

    @asynccontextmanager
    async def activate(
        self, runtime: Any, turn: Turn, agent: Any, intent: ParsedIntent, instruction: str
    ) -> AsyncIterator[ExecutionRequest]:
        if self.task is None:
            self._initialize(runtime, turn, agent, intent)
        assert self.task is not None and self.session is not None
        request = ExecutionRequest(self.task, instruction)
        remaining = request.task.resources.remaining_seconds()
        with execution_request_scope(request), session_scope(self.session):
            timeout = asyncio.timeout(remaining)
            try:
                async with timeout:
                    yield request
            except TimeoutError as exc:
                if timeout.expired():
                    raise ExecutionDeadlineExceeded("task execution deadline exceeded") from exc
                raise


