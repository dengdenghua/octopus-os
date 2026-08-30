"""Pure helper functions for ``ParallelAgentOrchestrator``.

Extracted from ``orchestrator.py`` (2026-06) to keep that file under the
god-file threshold. These helpers are stateless — they don't read or
write orchestrator state, so they sit naturally outside the class.

Includes:
  * ``default_runner`` — stub subagent used when no real runner is wired
  * ``preview`` — truncating string formatter for log lines
  * ``build_plan`` — topological sort of tasks → BatchPlan with phases
  * ``initial_runtime_session_metadata`` — extract workspace metadata
  * ``authorize_dependency_file_handoffs`` — leases file writes between
    dependent tasks
  * Path utilities (``workspace_path_from_context``, ``resolve_write_path``,
    ``normalize_write_path``)
  * ``contract_for`` / ``deps_terminal_success``
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from runtime.execution.misc.file_write_leases import (
    authorize_file_write_handoff,
)

from .models import (
    BatchPhase,
    BatchPlan,
    WorkContract,
)


def journal_batch_lifecycle(batch_id: str, *, status: str, detail: str) -> None:
    """Best-effort parallel-batch lifecycle row (audit T-12).

    A ``running`` row at dispatch and a terminal row at close give the
    journal startup sweep (``sweep_interrupted_jobs``) something to fold:
    a batch left mid-flight by a crash/restart is marked interrupted on
    the next boot instead of vanishing silently. Never raises.
    """
    try:
        from runtime.memory.journal.activity import write_job_change

        write_job_change(
            job_id=batch_id,
            kind="parallel_batch",
            label=f"parallel batch {batch_id}",
            status=status,
            detail=detail,
        )
    except Exception:  # noqa: BLE001 — journaling is best-effort
        pass


def default_runner(
    description: str,
    *,
    subagent_name: str,
    context: Any = None,
    cancel_event: Any = None,
) -> str:
    """Stub subagent used when no real runner is wired."""
    for _ in range(3):
        if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
            return ""
        time.sleep(0.01)
    return f"[stub subagent={subagent_name}] received: {description[:120]}"


def preview(value: str | None, max_chars: int = 420) -> str | None:
    """Collapse whitespace + truncate a string for log/event display."""
    if not value:
        return None
    text = " ".join(value.split())
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def build_plan(
    *,
    batch_id: str,
    entries: dict[str, Any],
    max_concurrency: int,
) -> BatchPlan:
    """Topo-sort ``entries`` (dict of task_id → _TaskEntry) into phases.

    Each phase contains tasks whose dependencies are already satisfied.
    Returns a BatchPlan with phases + WorkContracts (one per task).
    """
    phases: list[BatchPhase] = []
    remaining = set(entries)
    completed: set[str] = set()
    phase_index = 0
    while remaining:
        ready = sorted(
            task_id
            for task_id in remaining
            if all(dep in completed or dep not in entries for dep in entries[task_id].depends_on)
        )
        if not ready:
            ready = sorted(remaining)
        phases.append(
            BatchPhase(
                phase_index=phase_index,
                task_ids=ready,
                parallel=len(ready) > 1,
            )
        )
        completed.update(ready)
        remaining.difference_update(ready)
        phase_index += 1

    all_task_ids = list(entries)
    contracts = [
        WorkContract(
            contract_id=entry.task_id,
            agent_id=entry.subagent_name,
            role=entry.subagent_name,
            task_ids=[entry.task_id],
            depends_on=list(entry.depends_on),
            owned_scope=[f"task:{entry.task_id}"],
            forbidden_scope=[
                f"task:{task_id}" for task_id in all_task_ids if task_id != entry.task_id
            ],
            write_paths=list(entry.write_paths),
            success_criteria=[
                f"Complete task {entry.task_id}",
                "Return a result or explicit error",
            ],
        )
        for entry in entries.values()
    ]
    return BatchPlan(
        batch_id=batch_id,
        strategy="parallel_agents",
        max_concurrency=max_concurrency,
        phases=phases,
        contracts=contracts,
    )


def initial_runtime_session_metadata(
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    """Extract runtime_session_metadata + workspace_path from context."""
    source = context if isinstance(context, dict) else {}
    existing = source.get("runtime_session_metadata")
    metadata = existing if isinstance(existing, dict) else {}
    workspace_path = source.get("workspace_path")
    if isinstance(workspace_path, str) and workspace_path.strip():
        metadata.setdefault("workspace_path", workspace_path)
    return metadata


def authorize_dependency_file_handoffs(
    batch: Any,
    entry: Any,
    context: dict[str, Any],
) -> None:
    """When a task writes paths that its dependency also wrote,
    authorize the lease handoff so the file_write_leases system
    doesn't reject the second write."""
    if not entry.write_paths or not entry.depends_on:
        return
    metadata = context.get("runtime_session_metadata")
    if not isinstance(metadata, dict):
        return
    session = SimpleNamespace(metadata=metadata)
    current_paths = {normalize_write_path(path) for path in entry.write_paths}
    current_paths.discard("")
    if not current_paths:
        return
    workspace = workspace_path_from_context(context, metadata)
    for dep_id in entry.depends_on:
        dep = batch.tasks.get(dep_id)
        if dep is None or not dep.write_paths:
            continue
        dep_paths = {normalize_write_path(path) for path in dep.write_paths}
        for rel_path in sorted(current_paths & dep_paths):
            authorize_file_write_handoff(
                session,
                resolve_write_path(rel_path, workspace),
                from_owner=dep.task_id,
                to_owner=entry.task_id,
            )


def workspace_path_from_context(
    context: dict[str, Any],
    metadata: dict[str, Any],
) -> Path | None:
    """Resolve the workspace root from context/metadata, if any."""
    raw = context.get("workspace_path") or metadata.get("workspace_path")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return Path(raw).expanduser().resolve(strict=False)
    except OSError:
        return Path(raw).expanduser()


def resolve_write_path(path: str, workspace: Path | None) -> Path:
    """Resolve a relative write path against the workspace root."""
    candidate = Path(path)
    if workspace is not None and not candidate.is_absolute():
        candidate = workspace / candidate
    return candidate


def normalize_write_path(path: str) -> str:
    """Normalize a write path for fingerprint comparison."""
    normalized = str(path or "").strip().replace("\\", "/").lstrip("./")
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    return normalized


def contract_for(plan: BatchPlan, task_id: str) -> WorkContract | None:
    """Find the WorkContract for a given task_id in the plan."""
    for contract in plan.contracts:
        if contract.contract_id == task_id:
            return contract
    return None


def deps_terminal_success(batch: Any, entry: Any) -> bool:
    """Return True iff every dependency of ``entry`` completed successfully."""
    for dep in entry.depends_on:
        dep_entry = batch.tasks.get(dep)
        if dep_entry is None:
            continue
        if dep_entry.status != "completed":
            return False
    return True
