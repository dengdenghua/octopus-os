"""L2 execution engine — the milestone-driven loop.

    while project_not_done:
        1. MS check        — activate the next milestone whose deps are met
        2. assign tasks    — decompose a fresh milestone into a task DAG
        3. agents execute  — run the ready frontier (role chosen by task type)
        4. QA evaluate     — gate each task output against the spec; retry/fail
        5. update MS       — when all tasks pass, gate the milestone & advance

The milestone is the stop condition (project done ⇔ all milestones done), not the
loop. Every step writes back through the store (resumable), and the four
intelligence hooks (generate / decompose / execute / qa / gate) are injected:
production wires them to LLM + the cowork subagent runner; tests/demo pass
deterministic stubs. The engine itself is pure orchestration.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import suppress
from typing import Any
from uuid import uuid4

from runtime.projectos.model import (
    ROLE_FOR_TASK,
    Milestone,
    Project,
    Task,
    ready_tasks,
)
from runtime.projectos.store import ProjectClaimActiveError, ProjectStore
from runtime.safety.auth.scope import TenantScope

MilestoneGenerator = Callable[[str], list[Milestone]]  # (project_goal) -> milestones
TaskDecomposer = Callable[[Milestone], list[Task]]  # (milestone) -> task DAG
Executor = Callable[[Task, dict[str, Any]], Any]  # (task, context) -> output
QAEvaluator = Callable[[Task, Milestone], dict[str, Any]]  # -> {"approved", "reason"}
MilestoneGate = Callable[[Milestone, list[Task]], dict[str, Any]]  # -> {"met", "reason"}

MAX_TASK_ATTEMPTS = 2
DEFAULT_RUN_MAX_TICKS = 50
HARD_MAX_RUN_TICKS = 200
MIN_RUN_TICKS = 1
DEFAULT_TASK_CLAIM_TIMEOUT_SECONDS = 60 * 60


def normalize_run_ticks(value: int | None) -> int:
    """Bound synchronous project runs so one request cannot monopolize workers."""
    try:
        ticks = int(value if value is not None else DEFAULT_RUN_MAX_TICKS)
    except (TypeError, ValueError):
        ticks = DEFAULT_RUN_MAX_TICKS
    return max(MIN_RUN_TICKS, min(ticks, HARD_MAX_RUN_TICKS))


def _error_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def stub_generate_milestones(goal: str) -> list[Milestone]:
    """No-LLM fallback: a generic Plan → Build → Verify phasing that fits almost
    any project, so the engine/CLI runs deterministically without a model router
    (production injects LLM hooks for goal-specific milestones)."""
    return [
        Milestone(
            id="MS1",
            name="plan",
            goal=f"Scope and plan: {goal}",
            success_criteria=["plan approved"],
        ),
        Milestone(
            id="MS2",
            name="build",
            goal=f"Build: {goal}",
            success_criteria=["implementation complete"],
            dependencies=["MS1"],
        ),
        Milestone(
            id="MS3",
            name="verify",
            goal=f"Verify and deliver: {goal}",
            success_criteria=["verified against goal"],
            dependencies=["MS2"],
        ),
    ]


def stub_decompose_tasks(ms: Milestone) -> list[Task]:
    """No-LLM fallback: a research → execution pair (a 2-node DAG).

    The research node defaults to ``team_mode="swarm"`` so a roster-aware
    engine (cowork_bridge injects ``run_task_team``) brainstorms it across the
    group; the execution node stays ``single`` unless a caller opts it into
    cluster."""
    return [
        Task(
            id=f"{ms.id}-T1",
            milestone_id=ms.id,
            type="research",
            goal=f"{ms.goal} — assess",
            team_mode="swarm",
            priority="P1",
            estimate=1.0,
            due_at=ms.due_at or "",
            acceptance_criteria=list(ms.success_criteria),
        ),
        Task(
            id=f"{ms.id}-T2",
            milestone_id=ms.id,
            type="code",
            goal=f"{ms.goal} — do",
            priority="P2",
            estimate=2.0,
            depends_on=[f"{ms.id}-T1"],
        ),
    ]


def _default_execute(task: Task, context: dict[str, Any]) -> str:
    return f"[{task.assigned_role}] output for «{task.goal}»"


def _default_qa(task: Task, milestone: Milestone) -> dict[str, Any]:
    ok = bool(task.output)
    return {"approved": ok, "reason": "non-empty output" if ok else "empty output"}


def _default_gate(milestone: Milestone, tasks: list[Task]) -> dict[str, Any]:
    met = bool(tasks) and all(t.status == "done" for t in tasks)
    return {"met": met, "reason": "all tasks done" if met else "tasks pending"}


def _default_assign(task: Task) -> str:
    """Default routing: the fixed role for the task type. A custom group injects
    an assigner that picks one of ITS actual members instead (see cowork_bridge)."""
    return ROLE_FOR_TASK.get(task.type, "engineer")


AgentAssigner = Callable[[Task], str]  # (task) -> concrete agent/member id
# (task, context) -> output — runs a task node as a *team* (swarm 蜂群 / cluster
# 集群) instead of a single agent. Injected by the cowork bridge so a project
# task can fan out to the group roster and reuse the cluster/swarm engines.
TaskTeamRunner = Callable[[Task, dict[str, Any]], Any]
ThreadContextResolver = Callable[[str], dict[str, Any]]


class ProjectEngine:
    def __init__(
        self,
        store: ProjectStore,
        *,
        generate_milestones: MilestoneGenerator,
        decompose_tasks: TaskDecomposer,
        execute_task: Executor = _default_execute,
        qa_task: QAEvaluator = _default_qa,
        gate_milestone: MilestoneGate = _default_gate,
        assign_agent: AgentAssigner = _default_assign,
        run_task_team: TaskTeamRunner | None = None,
        owner_id: str = "",
        tenant_id: str = "",
        scope: TenantScope | None = None,
        resolve_thread_context: ThreadContextResolver | None = None,
        task_claim_timeout_seconds: float = DEFAULT_TASK_CLAIM_TIMEOUT_SECONDS,
        required_execution_thread_id: str = "",
    ) -> None:
        if scope is None and owner_id and tenant_id:
            scope = TenantScope(tenant_id=tenant_id, actor_id=owner_id)
        self.scope = scope
        self.store = store.with_scope(scope) if scope is not None else store
        self._generate = generate_milestones
        self._decompose = decompose_tasks
        self._execute = execute_task
        self._qa = qa_task
        self._gate = gate_milestone
        self._assign = assign_agent
        self._run_task_team = run_task_team
        self.owner_id = owner_id
        self.tenant_id = tenant_id
        self._resolve_thread_context = resolve_thread_context
        self._task_claim_timeout_seconds = max(1.0, float(task_claim_timeout_seconds))
        self._required_execution_thread_id = required_execution_thread_id

    # ── planning ─────────────────────────────────────────────────────────────
    def plan(self, name: str, goal: str, *, project_id: str | None = None) -> Project:
        """Turn a one-line goal into a project with generated milestones."""
        pid = project_id or f"P-{uuid4().hex[:8]}"
        try:
            milestones = self._generate(goal)
        except Exception:  # noqa: BLE001 — planning hooks are external intelligence adapters
            milestones = stub_generate_milestones(goal)
        if not milestones:
            milestones = stub_generate_milestones(goal)
        from datetime import UTC, datetime

        project = Project(
            id=pid,
            name=name,
            goal=goal,
            milestone_ids=[m.id for m in milestones],
            status="running",
            owner_id=self.owner_id,
            tenant_id=self.tenant_id,
            owner=self.owner_id or "",
            created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        project, _resolved_milestones = self.store.create_project_plan(project, milestones)
        return project

    # ── the loop ─────────────────────────────────────────────────────────────
    def tick(self, project_id: str) -> dict[str, Any]:
        """One iteration of the loop. Returns the events it produced."""
        project = self.store.get_project(project_id)
        if project is None:
            return {"events": ["project_not_found"], "project_status": "failed"}
        if project.status in {"blocked", "done", "failed"}:
            return {
                "events": [f"project_not_runnable:{project.status}"],
                "project_status": project.status,
                "current_ms": project.current_ms,
            }
        events: list[str] = []

        stale_before = time.time() - self._task_claim_timeout_seconds
        orphaned_milestones = self.store.orphan_stale_milestone_claims(
            project_id,
            stale_before=stale_before,
        )
        orphaned = self.store.orphan_stale_task_claims(
            project_id,
            stale_before=stale_before,
        )
        if orphaned_milestones or orphaned:
            events.extend(
                f"milestone_decomposition_claim_orphaned:{milestone.id}"
                for milestone in orphaned_milestones
            )
            events.extend(f"task_claim_orphaned:{task.id}" for task in orphaned)
            reason = (
                "orphaned_decomposition_claim" if orphaned_milestones else "orphaned_task_claim"
            )
            events.append(f"project_blocked:{reason}")
            current = self.store.get_project(project_id)
            return {
                "events": events,
                "project_status": current.status if current else "failed",
                "current_ms": current.current_ms if current else None,
            }

        if not self._confirm_execution_binding(project, events):
            current = self.store.get_project(project_id)
            return {
                "events": events,
                "project_status": current.status if current else "failed",
                "current_ms": current.current_ms if current else None,
            }

        active = self._ensure_active_milestone(project, events)
        if active is None:
            current = self.store.get_project(project_id)
            return {
                "events": events,
                "project_status": current.status if current else "failed",
                "current_ms": current.current_ms if current else None,
            }

        self._ensure_tasks(project_id, active, events)
        self._run_frontier(project, active, events)
        self._gate_milestone(project, active, events)
        current = self.store.get_project(project_id)
        return {
            "events": events,
            "project_status": current.status if current else "failed",
            "current_ms": current.current_ms if current else None,
        }

    def run(self, project_id: str, *, max_ticks: int = DEFAULT_RUN_MAX_TICKS) -> dict[str, Any]:
        """Drive ticks until the project is done/failed/blocked or max_ticks."""
        history: list[dict[str, Any]] = []
        bounded_ticks = normalize_run_ticks(max_ticks)
        for _ in range(bounded_ticks):
            r = self.tick(project_id)
            history.append(r)
            if r["project_status"] in ("done", "failed", "blocked"):
                break
            if any(e == "no_runnable_milestone" for e in r["events"]):
                break  # blocked — nothing to advance
        final = self.store.get_project(project_id)
        result = {
            "ticks": len(history),
            "final_status": final.status if final else "failed",
            "history": history,
        }
        self._audit(
            project_id,
            "project.run",
            {
                "max_ticks": bounded_ticks,
                "ticks": result["ticks"],
                "final_status": result["final_status"],
                "history": history,
            },
        )
        return result

    def recover(
        self,
        project_id: str,
        *,
        task_ids: list[str] | None = None,
        reset_attempts: bool = True,
        clear_outputs: bool = True,
    ) -> dict[str, Any]:
        """Reopen a blocked project so the loop can continue.

        By default, only failed/rejected/blocked tasks in the current blocked
        milestone are retried. When operators pass explicit task ids, those
        tasks and their downstream dependants in the same milestone are reset
        together so stale outputs do not survive a partial rework.
        """
        project = self.store.get_project(project_id)
        if project is None:
            return {"events": ["project_not_found"], "project_status": "failed"}
        project = self.store.assert_no_active_claims(project.id)
        if project.status != "blocked":
            raise ValueError("only a blocked project can be recovered")

        events: list[str] = []
        selected = {str(task_id) for task_id in (task_ids or []) if str(task_id).strip()}
        milestones = self.store.milestones_for(project.id)
        target_ms_ids = {project.current_ms} if project.current_ms else set()
        target_ms_ids.update(ms.id for ms in milestones if ms.status == "blocked")

        changed = False
        first_reopened: str | None = None
        for ms in milestones:
            tasks = self.store.tasks_for_milestone(ms.id)
            explicit_here = {task.id for task in tasks if task.id in selected}
            if any(
                task.id in explicit_here and task.status not in {"failed", "rejected", "blocked"}
                for task in tasks
            ):
                raise ValueError("recovery tasks must be failed, rejected, or blocked")
            should_consider = (
                bool(explicit_here)
                or ms.id in target_ms_ids
                or any(task.status in {"failed", "rejected", "blocked"} for task in tasks)
            )
            if not should_consider:
                continue

            reset_ids = explicit_here
            if selected and explicit_here:
                reset_ids = self._with_downstream_tasks(tasks, reset_ids)
            elif not selected:
                reset_ids = {
                    task.id for task in tasks if task.status in {"failed", "rejected", "blocked"}
                }

            for task in tasks:
                if task.id not in reset_ids:
                    continue
                self._reset_task_for_rerun(
                    task,
                    reset_attempts=reset_attempts,
                    clear_outputs=clear_outputs,
                )
                events.append(f"task_recovered:{task.id}")
                changed = True

            if ms.status == "blocked" or reset_ids:
                ms.status = "in_progress"
                self.store.save_milestone(project.id, ms, allow_terminal_rewrite=True)
                events.append(f"milestone_reopened:{ms.id}")
                first_reopened = first_reopened or ms.id
                changed = True

        if changed:
            project.status = "running"
            if first_reopened:
                project.current_ms = first_reopened
            self.store.save_project(project, allow_terminal_rewrite=True)
            events.append("project_recovered")
        else:
            events.append("nothing_to_recover")

        current = self.store.get_project(project_id)
        result = {
            "events": events,
            "project_status": current.status if current else "failed",
            "current_ms": current.current_ms if current else None,
        }
        self._audit(
            project_id,
            "project.recover",
            {
                "task_ids": list(selected),
                "reset_attempts": reset_attempts,
                "clear_outputs": clear_outputs,
                **result,
            },
        )
        return result

    def intervene_task(
        self,
        project_id: str,
        task_id: str,
        *,
        action: str,
        assigned_agent: str | None = None,
        assigned_role: str | None = None,
        output: Any = None,
        reason: str = "",
        reset_attempts: bool = True,
        cascade: bool = True,
    ) -> dict[str, Any]:
        """Apply an operator intervention to one task.

        ``reassign`` and ``reset`` put work back on the DAG frontier. ``complete``
        and ``skip`` mark a task as accepted by the operator so the milestone
        gate can move on.
        """
        project = self.store.get_project(project_id)
        if project is None:
            return {"events": ["project_not_found"], "project_status": "failed"}
        task = self.store.get_task(task_id)
        if task is None:
            result = {
                "events": [f"task_not_found:{task_id}"],
                "project_status": project.status,
                "current_ms": project.current_ms,
            }
            self._audit(
                project_id,
                "task.intervention_rejected",
                {"task_id": task_id, "action": action, **result},
            )
            return result
        ms = self.store.get_milestone(task.milestone_id)
        if ms is None:
            result = {
                "events": [f"milestone_not_found:{task.milestone_id}"],
                "project_status": project.status,
                "current_ms": project.current_ms,
            }
            self._audit(
                project_id,
                "task.intervention_rejected",
                {"task_id": task_id, "action": action, **result},
            )
            return result

        action = str(action or "").strip().lower()
        events: list[str] = []
        tasks = self.store.tasks_for_milestone(ms.id)
        affected_ids = {task.id}
        if action == "reset" and cascade:
            affected_ids = self._with_downstream_tasks(tasks, affected_ids)
        if action in {"reassign", "reset", "complete", "skip"}:
            running_ids = tuple(
                current.id
                for current in tasks
                if current.id in affected_ids and current.status == "running"
            )
            if running_ids:
                raise ProjectClaimActiveError(project, task_ids=running_ids)
            self.store.assert_no_active_claims(
                project.id,
                task_ids=tuple(sorted(affected_ids)),
            )

        if action == "reassign":
            if assigned_agent is not None:
                task.assigned_agent = str(assigned_agent)
            if assigned_role is not None:
                task.assigned_role = str(assigned_role)
            self._reset_task_for_rerun(task, reset_attempts=reset_attempts, clear_outputs=True)
            events.append(f"task_reassigned:{task.id}")
        elif action == "reset":
            reset_ids = affected_ids
            for current_task in tasks:
                if current_task.id not in reset_ids:
                    continue
                self._reset_task_for_rerun(
                    current_task,
                    reset_attempts=reset_attempts,
                    clear_outputs=True,
                )
                events.append(f"task_reset:{current_task.id}")
        elif action == "complete":
            task.status = "done"
            task.output = output
            task.qa_verdict = {"approved": True, "reason": reason or "operator completed"}
            self.store.save_task(task, allow_terminal_rewrite=True)
            events.append(f"task_completed_by_operator:{task.id}")
        elif action == "skip":
            task.status = "done"
            task.output = {
                "skipped": True,
                "reason": reason or "operator skipped",
                "previous_output": task.output,
            }
            task.qa_verdict = {"approved": True, "reason": reason or "operator skipped"}
            self.store.save_task(task, allow_terminal_rewrite=True)
            events.append(f"task_skipped:{task.id}")
        else:
            result = {
                "events": [f"unknown_task_action:{action or '<empty>'}"],
                "project_status": project.status,
                "current_ms": project.current_ms,
            }
            self._audit(
                project_id,
                "task.intervention_rejected",
                {"task_id": task_id, "action": action, **result},
            )
            return result

        if ms.status in {"blocked", "done"} or project.status == "blocked":
            ms.status = "in_progress"
            self.store.save_milestone(project.id, ms, allow_terminal_rewrite=True)
            project.status = "running"
            project.current_ms = ms.id
            self.store.save_project(project, allow_terminal_rewrite=True)
            events.append(f"milestone_reopened:{ms.id}")
            events.append("project_recovered")

        current = self.store.get_project(project_id)
        result = {
            "events": events,
            "project_status": current.status if current else "failed",
            "current_ms": current.current_ms if current else None,
        }
        self._audit(
            project_id,
            "task.intervention",
            {
                "task_id": task_id,
                "action": action,
                "assigned_agent": assigned_agent,
                "assigned_role": assigned_role,
                "reason": reason,
                "reset_attempts": reset_attempts,
                "cascade": cascade,
                **result,
            },
        )
        return result

    # ── steps ────────────────────────────────────────────────────────────────
    def _confirm_execution_binding(self, project: Project, events: list[str]) -> bool:
        if project.status in {"done", "failed"}:
            return True
        thread_id = self._required_execution_thread_id or project.execution_thread_id
        if not thread_id:
            return True
        started = self.store.start_project_if_bound(project.id, thread_id)
        if started is not None:
            project.started_at = started.started_at
            project.execution_thread_id = started.execution_thread_id
            return True

        milestones = self.store.milestones_for(project.id)
        by_id = {milestone.id: milestone for milestone in milestones}
        blocked = by_id.get(project.current_ms or "")
        if blocked is None:
            blocked = next(
                (by_id[mid] for mid in project.milestone_ids if mid in by_id),
                None,
            )
        if blocked is not None and blocked.status not in {"done", "failed"}:
            blocked.status = "blocked"
            self.store.save_milestone(project.id, blocked)
            events.append(f"milestone_blocked:{blocked.id}")
        current = self.store.get_project(project.id)
        if current is not None:
            self._block_project(
                current,
                blocked.id if blocked is not None else current.current_ms,
                events,
                reason="execution_binding_lost",
            )
        events.append(f"project_execution_binding_lost:{thread_id}")
        self._audit(
            project.id,
            "project.execution_binding_lost",
            {"thread_id": thread_id, "recovery_required": True},
        )
        return False

    def _ensure_active_milestone(self, project: Project, events: list[str]) -> Milestone | None:
        mss = self.store.milestones_for(project.id)
        done = {m.id for m in mss if m.status == "done"}
        active = next((m for m in mss if m.status in ("active", "in_progress")), None)
        if active is not None:
            return active
        blocked = next((m for m in mss if m.status == "blocked"), None)
        if blocked is not None:
            self._block_project(project, blocked.id, events, reason="milestone_blocked")
            return None
        if mss and len(done) == len(mss):
            from datetime import UTC, datetime

            project.status = "done"
            project.finished_at = project.finished_at or datetime.now(UTC).isoformat(
                timespec="seconds"
            )
            saved = self.store.save_project(project)
            if saved.status == "done":
                events.append("project_done")
            else:
                events.append(f"project_terminal_write_ignored:{project.id}")
            return None
        nxt = next(
            (m for m in mss if m.status == "pending" and all(d in done for d in m.dependencies)),
            None,
        )
        if nxt is None:
            events.append("no_runnable_milestone")  # all blocked on unmet deps
            self._block_project(project, project.current_ms, events, reason="no_runnable_milestone")
            return None
        if not project.started_at:
            from datetime import UTC, datetime

            project.started_at = datetime.now(UTC).isoformat(timespec="seconds")
            self.store.save_project(project)
        nxt.status = "active"
        saved_ms = self.store.save_milestone(project.id, nxt)
        if saved_ms.status != "active":
            events.append(f"milestone_stale_activation_ignored:{nxt.id}")
            return None
        project.current_ms = nxt.id
        saved_project = self.store.save_project(project)
        if saved_project.current_ms != nxt.id or saved_project.status not in {
            "running",
            "planning",
        }:
            events.append(f"project_stale_activation_ignored:{project.id}")
            return None
        events.append(f"milestone_activated:{nxt.id}")
        return nxt

    def _ensure_tasks(self, project_id: str, ms: Milestone, events: list[str]) -> None:
        if self.store.tasks_for_milestone(ms.id):
            return
        claim = self.store.claim_milestone_decomposition(ms.id)
        if claim is None:
            canonical = self.store.get_milestone(ms.id)
            if canonical is not None:
                ms.status = canonical.status
                ms.task_ids = list(canonical.task_ids)
            self.store.tasks_for_milestone(ms.id)
            events.append(f"milestone_decompose_claim_ignored:{ms.id}")
            return
        claimed_ms, claim_id = claim
        project = self.store.get_project(project_id)
        try:
            new_tasks = self._decompose(claimed_ms)
        except Exception as exc:  # noqa: BLE001 — decompose hook failure should block, not crash tick
            events.append(f"tasks_decompose_failed:{ms.id}")
            saved_ms, committed = self.store.finalize_milestone_decomposition(
                project_id,
                ms.id,
                [],
                claim_id,
                blocked=True,
            )
            if committed and project is not None:
                ms.status = saved_ms.status if saved_ms is not None else "blocked"
                self._block_project(project, ms.id, events, reason="decompose_failed")
            elif not committed:
                events.append(f"milestone_stale_decompose_failure_ignored:{ms.id}")
            self._audit(
                project_id,
                "project.decompose_failed",
                {"milestone_id": ms.id, "error": _error_text(exc)},
            )
            return
        if not new_tasks:
            events.append(f"tasks_decompose_empty:{ms.id}")
            saved_ms, committed = self.store.finalize_milestone_decomposition(
                project_id,
                ms.id,
                [],
                claim_id,
                blocked=True,
            )
            if committed and project is not None:
                ms.status = saved_ms.status if saved_ms is not None else "blocked"
                self._block_project(project, ms.id, events, reason="decompose_empty")
            elif not committed:
                events.append(f"milestone_stale_decompose_empty_ignored:{ms.id}")
            self._audit(
                project_id,
                "project.decompose_empty",
                {"milestone_id": ms.id},
            )
            return
        for t in new_tasks:
            t.milestone_id = ms.id
            t.assigned_role = t.assigned_role or ROLE_FOR_TASK.get(t.type, "engineer")
        try:
            saved_ms, committed = self.store.finalize_milestone_decomposition(
                project_id,
                ms.id,
                new_tasks,
                claim_id,
            )
        except Exception as exc:  # noqa: BLE001 — invalid external decomposition is recoverable
            events.append(f"tasks_decompose_failed:{ms.id}")
            blocked_ms, blocked = self.store.finalize_milestone_decomposition(
                project_id,
                ms.id,
                [],
                claim_id,
                blocked=True,
            )
            if blocked and project is not None:
                ms.status = blocked_ms.status if blocked_ms is not None else "blocked"
                self._block_project(project, ms.id, events, reason="decompose_failed")
            self._audit(
                project_id,
                "project.decompose_failed",
                {"milestone_id": ms.id, "error": _error_text(exc)},
            )
            return
        if not committed or saved_ms is None:
            events.append(f"milestone_stale_tasks_ignored:{ms.id}")
            return
        ms.task_ids = list(saved_ms.task_ids)
        ms.status = saved_ms.status
        events.append(f"tasks_created:{ms.id}:{len(new_tasks)}")

    def _run_frontier(self, project: Project, ms: Milestone, events: list[str]) -> None:
        tasks = self.store.tasks_for_milestone(ms.id)
        for ready_task in ready_tasks(tasks):
            assigned_role = ROLE_FOR_TASK.get(
                ready_task.type,
                ready_task.assigned_role or "engineer",
            )
            claim = self.store.claim_task(
                ready_task.id,
                assigned_role=assigned_role,
            )
            if claim is None:
                # Another worker or an operator changed the row after this
                # frontier snapshot. Re-read before skipping so this tick never
                # executes from stale pending/ready state.
                self.store.get_task(ready_task.id)
                events.append(f"task_stale_claim_ignored:{ready_task.id}")
                continue
            task, claim_id = claim

            # Operator reassignment wins; otherwise pick a concrete group member
            # or fallback role for this execution. Assignment happens after the
            # atomic claim so an injected assigner is also called only once.
            try:
                task.assigned_agent = task.assigned_agent or self._assign(task)
            except Exception as exc:  # noqa: BLE001 — assignment is an injected hook
                task.output = f"assignment error: {_error_text(exc)}"
                if task.attempts >= MAX_TASK_ATTEMPTS:
                    task.status = "failed"
                    event = f"task_failed_assignment:{task.id}"
                else:
                    task.status = "pending"
                    event = f"task_assignment_error_retry:{task.id}"
                self._commit_task_claim(task, claim_id, event, events)
                continue
            try:
                context_tasks = [task if item.id == task.id else item for item in tasks]
                context = self._context(project, ms, context_tasks)
                # 项目模式 × 集群/蜂群：任务节点声明了 team_mode（swarm/cluster）
                # 且注入了 run_task_team 时，把它交给团队执行器（蜂群 fan-out /
                # 集群角色流水线），否则退回单 agent 执行。这样项目 DAG 里可以
                # 混排「单点任务」和「团队任务」。
                if task.team_mode in ("swarm", "cluster") and self._run_task_team is not None:
                    task.output = self._run_task_team(task, context)
                else:
                    task.output = self._execute(task, context)
            except Exception as exc:  # noqa: BLE001 — one task failing must not kill the loop
                task.output = f"error: {type(exc).__name__}: {exc}"
                if task.attempts >= MAX_TASK_ATTEMPTS:
                    task.status = "failed"
                    event = f"task_failed:{task.id}"
                else:
                    task.status = "pending"
                    event = f"task_error_retry:{task.id}"
                self._commit_task_claim(task, claim_id, event, events)
                continue
            try:
                verdict = self._qa(task, ms)
            except Exception as exc:  # noqa: BLE001 — QA is an injected hook
                task.qa_verdict = {
                    "approved": False,
                    "reason": f"qa error: {_error_text(exc)}",
                }
                if task.attempts >= MAX_TASK_ATTEMPTS:
                    task.status = "failed"
                    event = f"task_failed_qa_error:{task.id}"
                else:
                    task.status = "pending"
                    event = f"task_qa_error_retry:{task.id}"
                self._commit_task_claim(task, claim_id, event, events)
                continue
            task.qa_verdict = verdict
            if verdict.get("approved"):
                task.status = "done"
                event = f"task_done:{task.id}"
            elif task.attempts >= MAX_TASK_ATTEMPTS:
                task.status = "failed"
                event = f"task_failed_qa:{task.id}"
            else:
                task.status = "pending"  # QA rejected → retry next tick
                event = f"task_rejected:{task.id}"
            self._commit_task_claim(task, claim_id, event, events)

    def _commit_task_claim(
        self,
        task: Task,
        claim_id: str,
        event: str,
        events: list[str],
    ) -> bool:
        _current, committed = self.store.finalize_task_claim(task, claim_id)
        if committed:
            events.append(event)
        else:
            events.append(f"task_stale_result_ignored:{task.id}")
        return committed

    def _gate_milestone(self, project: Project, ms: Milestone, events: list[str]) -> None:
        tasks = self.store.tasks_for_milestone(ms.id)
        if not tasks or not all(t.status == "done" for t in tasks):
            if any(t.status == "failed" for t in tasks):
                ms.status = "blocked"
                saved_ms = self.store.save_milestone(project.id, ms)
                if saved_ms.status != "blocked":
                    events.append(f"milestone_stale_block_ignored:{ms.id}")
                    return
                events.append(f"milestone_blocked:{ms.id}")
                self._block_project(project, ms.id, events, reason="task_failed")
            elif tasks and not any(t.status == "running" for t in tasks) and not ready_tasks(tasks):
                ms.status = "blocked"
                saved_ms = self.store.save_milestone(project.id, ms)
                if saved_ms.status != "blocked":
                    events.append(f"milestone_stale_block_ignored:{ms.id}")
                    return
                events.append(f"milestone_blocked_dag:{ms.id}")
                self._block_project(project, ms.id, events, reason="task_dag_blocked")
            return
        try:
            gate = self._gate(ms, tasks)
        except Exception as exc:  # noqa: BLE001 — milestone gate is an injected hook
            ms.status = "blocked"
            saved_ms = self.store.save_milestone(project.id, ms)
            if saved_ms.status != "blocked":
                events.append(f"milestone_stale_gate_error_ignored:{ms.id}")
                return
            events.append(f"milestone_gate_error:{ms.id}")
            self._block_project(project, ms.id, events, reason="gate_error")
            self._audit(
                project.id,
                "project.gate_failed",
                {"milestone_id": ms.id, "error": _error_text(exc)},
            )
            return
        if gate.get("met"):
            ms.status = "done"
            saved_ms = self.store.save_milestone(project.id, ms)
            if saved_ms.status != "done":
                events.append(f"milestone_stale_done_ignored:{ms.id}")
                return
            project.current_ms = None
            saved_project = self.store.save_project(project)
            if saved_project.current_ms is not None and saved_project.current_ms != "":
                events.append(f"project_stale_milestone_done_ignored:{project.id}")
                return
            events.append(f"milestone_done:{ms.id}")
        else:
            ms.status = "blocked"
            saved_ms = self.store.save_milestone(project.id, ms)
            if saved_ms.status != "blocked":
                events.append(f"milestone_stale_gate_failed_ignored:{ms.id}")
                return
            events.append(f"milestone_gate_failed:{ms.id}")
            self._block_project(project, ms.id, events, reason="gate_failed")

    def _block_project(
        self,
        project: Project,
        milestone_id: str | None,
        events: list[str],
        *,
        reason: str,
    ) -> None:
        project.status = "blocked"
        if milestone_id:
            project.current_ms = milestone_id
        saved_project = self.store.save_project(project)
        if saved_project.status != "blocked":
            events.append(f"project_stale_block_ignored:{reason}")
            return
        events.append(f"project_blocked:{reason}")

    def _with_downstream_tasks(self, tasks: list[Task], task_ids: set[str]) -> set[str]:
        out = set(task_ids)
        changed = True
        while changed:
            changed = False
            for task in tasks:
                if task.id in out:
                    continue
                if any(dep in out for dep in task.depends_on):
                    out.add(task.id)
                    changed = True
        return out

    def _reset_task_for_rerun(
        self,
        task: Task,
        *,
        reset_attempts: bool,
        clear_outputs: bool,
    ) -> None:
        task.status = "pending"
        if reset_attempts:
            task.attempts = 0
        if clear_outputs:
            task.output = None
            task.qa_verdict = None
        self.store.save_task(task, allow_terminal_rewrite=True)

    def _audit(self, project_id: str, kind: str, payload: dict) -> None:
        with suppress(Exception):
            self.store.append_event(project_id, kind=kind, payload=payload)

    def _context(self, project: Project, ms: Milestone, tasks: list[Task]) -> dict[str, Any]:
        try:
            thread_id = self.store.thread_for_project(project.id) or ""
        except (AttributeError, TypeError, ValueError):
            thread_id = ""
        context = {
            "project_id": project.id,
            "project_goal": project.goal,
            "owner_id": project.owner_id,
            "tenant_id": project.tenant_id,
            "thread_id": thread_id,
            "milestone_goal": ms.goal,
            "milestone_spec": ms.spec,
            "success_criteria": ms.success_criteria,
            "done_outputs": {t.id: t.output for t in tasks if t.status == "done"},
        }
        if self._resolve_thread_context is not None:
            resolved = self._resolve_thread_context(thread_id)
            if not isinstance(resolved, dict):
                raise TypeError("thread context resolver must return a mapping")
            workspace_path = resolved.get("workspace_path")
            runtime_metadata = resolved.get("runtime_session_metadata")
            if isinstance(workspace_path, str) and workspace_path:
                context["workspace_path"] = workspace_path
            if isinstance(runtime_metadata, dict):
                context["runtime_session_metadata"] = dict(runtime_metadata)
        return context
