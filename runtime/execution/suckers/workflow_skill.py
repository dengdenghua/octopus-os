"""Model-facing ``workflow`` skill (dsh ``tool-workflow``).

Runs a model-authored Python orchestration script that fans out
subagents, and returns the script's final JSON value plus run identity.
The engine seam (``runtime.execution.workflow``) owns parsing, execution,
caps, cancellation and lifecycle events; this module owns the model-facing
schema, progress narration and result mapping.
"""

from __future__ import annotations

import logging
from typing import Any

from runtime.execution.workflow import (
    WorkflowEngine,
    WorkflowObserver,
)

from ._delegation_skills_common import _emit_orchestration_progress, _emit_workflow_settlement
from .registry import Skill, SkillRegistry

_log = logging.getLogger("runtime.execution.suckers.workflow")

_ENGINE: WorkflowEngine | None = None


def get_workflow_engine() -> WorkflowEngine:
    """The process-wide workflow engine (deployment knobs defaulted)."""
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = WorkflowEngine()
    return _ENGINE


def set_workflow_engine(engine: WorkflowEngine | None) -> None:
    """Inject a custom engine (tests / deployment wiring)."""
    global _ENGINE
    _ENGINE = engine


class _ProgressObserver(WorkflowObserver):
    """Narrate the run into the orchestration progress lane and the durable
    journal timeline (dsh settlement bridge: workflow lifecycle rows land in
    the conversation's journal even when no realtime client is attached)."""

    def __init__(self) -> None:
        from runtime.memory.journal.activity import capture_attribution

        # The engine may invoke observer callbacks from a worker thread;
        # journal context is task-local, so snapshot it at construction.
        self._journal_attribution = capture_attribution()

    def on_start(self, info: Any) -> None:
        _emit_orchestration_progress(f"[workflow] start {info.meta.name}")
        from runtime.memory.journal.activity import write_workflow_start

        write_workflow_start(
            run_id=info.run_id,
            name=getattr(info.meta, "name", "workflow"),
            description=getattr(info.meta, "description", ""),
            **self._journal_attribution,
        )

    def on_phase(self, info: Any, title: str) -> None:
        _emit_orchestration_progress(f"[workflow] phase {title}")
        from runtime.memory.journal.activity import write_workflow_progress

        write_workflow_progress(
            run_id=info.run_id,
            kind="phase",
            text=title,
            **self._journal_attribution,
        )

    def on_log(self, info: Any, message: str) -> None:
        _emit_orchestration_progress(f"[workflow] {message}")
        from runtime.memory.journal.activity import write_workflow_progress

        write_workflow_progress(
            run_id=info.run_id,
            kind="log",
            text=message,
            **self._journal_attribution,
        )

    def on_agent_start(self, info: Any, agent: Any) -> None:
        _emit_orchestration_progress(f"[workflow] agent {agent.seq} {agent.label} started")
        from runtime.memory.journal.activity import write_workflow_progress

        write_workflow_progress(
            run_id=info.run_id,
            kind="agent_start",
            agent_seq=agent.seq,
            agent_label=agent.label,
            **self._journal_attribution,
        )

    def on_agent_end(self, info: Any, agent: Any) -> None:
        _emit_orchestration_progress(f"[workflow] agent {agent.seq} {agent.label}: {agent.outcome}")
        from runtime.memory.journal.activity import write_workflow_progress

        write_workflow_progress(
            run_id=info.run_id,
            kind="agent_end",
            text=str(agent.outcome),
            agent_seq=agent.seq,
            agent_label=agent.label,
            **self._journal_attribution,
        )

    def on_end(self, info: Any, result: Any) -> None:
        _emit_orchestration_progress(
            f"[workflow] end {result.stop_reason} ({result.agents_started} agents)"
        )
        from runtime.memory.journal.activity import write_workflow_end

        write_workflow_end(
            run_id=info.run_id,
            stop_reason=result.stop_reason,
            agents_started=result.agents_started,
            error=result.error or "",
            **self._journal_attribution,
        )
        # Emit workflow completion notification (dsh ``settlement`` analog)
        _emit_workflow_settlement(
            {
                "workflowName": getattr(info.meta, "name", "workflow"),
                "workflowDescription": getattr(info.meta, "description", ""),
                "runId": info.run_id,
                "stopReason": result.stop_reason,
                "success": result.stop_reason == "completed",
                "agentsStarted": result.agents_started,
                "error": result.error,
            }
        )


async def _run_workflow(
    *,
    script: str,
    meta: dict[str, Any],
    args: dict[str, Any] | None = None,
    max_total_agents: int | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Execute one workflow script; always returns a plain dict."""
    try:
        run = get_workflow_engine().start(
            {
                "script": script,
                "meta": meta,
                **({"args": args} if args is not None else {}),
                **({"maxTotalAgents": max_total_agents} if max_total_agents is not None else {}),
            },
            observer=_ProgressObserver(),
        )
    except Exception as exc:  # noqa: BLE001 — sync validation failures surface as errors
        return {
            "success": False,
            "error": str(exc) or exc.__class__.__name__,
        }
    try:
        result = await run.result
        if result.stop_reason != "completed":
            return {
                "success": False,
                "runId": run.id,
                "agentsStarted": result.agents_started,
                "error": result.error or f"workflow run {result.stop_reason}",
            }
        return {
            "success": True,
            "runId": run.id,
            "agentsStarted": result.agents_started,
            "result": result.value,
        }
    finally:
        try:
            await run.dispose()
        except Exception:  # noqa: BLE001 — disposal is best-effort
            _log.warning("workflow run disposal failed", exc_info=True)


_WORKFLOW_DESCRIPTION = """\
Run a model-authored Python orchestration script that fans out subagents \
and returns the script's final JSON value.

The script executes in an isolated worker process against a SMALL \
restricted vocabulary — no imports, no file/network access, no \
introspection. Attribute access is allowed only on non-dunder names \
(e.g. `s.strip()`, `d.items()`); `x.__class__`-style dunder access, \
imports, classes and generators are rejected at parse time.

Available globals:
- `args` — the plain JSON `args` object (a dict; use `args["key"]`).
- `agent(prompt, opts?)` — run one isolated subagent and return its \
final text (or structured value when `opts["schema"]` is set). Options: \
`label` (display), `phase` (grouping), `schema` (JSON Schema object — \
only `{"type": "object", ...}` is supported; the child returns the \
validated value), `provider` (optional subagent role/agent id override), \
`model` (optional child model override). A child that fails for its own \
reasons returns `None` — filter it out.
- `phase(title)` — announce a progress phase.
- `log(message)` — narrate a progress line.
- `parallel([thunk, ...])` — run zero-argument thunks concurrently; an \
ordinary thunk failure becomes `None`, a fatal error (bad option, cap \
tripped, infrastructure fault) aborts the whole script.
- `pipeline(items, *stages)` — run per-item stage chains concurrently; \
an ordinary stage failure drops THAT item to `None`, fatals abort.

The script ends with `return <json-value>` (top-level `await` allowed). \
The return value must be plain JSON data. Do NOT assign a top-level \
`meta` variable — meta rides this call's `meta` parameter.

`meta` = `{"name": kebab-case-name, "description": one-line, \
"whenToUse"?, "phases"?}`. Phases are progress annotations only.

Caps: `max_total_agents` (per-run child ceiling, default engine-wide), \
concurrent agent calls bounded by the engine, per-call item cap for \
parallel/pipeline. Cancellation is cooperative at hook boundaries.

Returns `{runId, agentsStarted, result}`. Use when a task decomposes \
into an orchestrated fan-out (research lanes, parallel review, staged \
pipelines) that fixed `call_agent_parallel` envelopes cannot express.
"""


def register_workflow_skills(registry: SkillRegistry) -> int:
    """Register the ``workflow`` skill. Returns the count registered."""
    registry.register(
        Skill(
            name="workflow",
            description=_WORKFLOW_DESCRIPTION,
            summary=(
                "Run a model-authored orchestration script that fans out "
                "subagents (agent/phase/log/parallel/pipeline) and returns "
                "a JSON value."
            ),
            affinity=["delegation", "orchestration", "workflow", "subagent", "swarm"],
            cost_profile="high",
            trusted_source="skill://public/workflow",
            handler=_run_workflow,
        ),
        replace=True,
    )
    return 1


__all__ = [
    "get_workflow_engine",
    "register_workflow_skills",
    "set_workflow_engine",
]
