from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from runtime.platform.process.paths import project_root as default_project_root


@dataclass(frozen=True)
class AgentLoopCheck:
    id: str
    title: str
    paths: tuple[str, ...]
    required_terms: tuple[str, ...]
    weight: int = 1


CHECKS: tuple[AgentLoopCheck, ...] = (
    AgentLoopCheck(
        id="turn_routing_priority",
        title="Realtime turn routing priority",
        paths=(
            "runtime/sensing/gateway/realtime_turn_lifecycle.py",
            "runtime/sensing/gateway/realtime_cerebrum.py",
            "tests/test_realtime_cerebrum.py",
        ),
        required_terms=(
            "estimate_turn_complexity",
            "_drive_codex_app_server",
            "_drive_group_fanout",
            "_drive_swarm_mesh",
            "planningMode",
        ),
    ),
    AgentLoopCheck(
        id="observable_turn_contract",
        title="Observable turn completion contract",
        paths=(
            "runtime/sensing/gateway/realtime_turn_lifecycle.py",
            "runtime/sensing/gateway/realtime_turn_outcome.py",
            "tests/test_realtime_cerebrum.py",
        ),
        required_terms=(
            "_turn_has_observable_output",
            "_turn_has_failed_code_verification",
            "_turn_has_unverified_code_changes",
            "turn_failure",
            "turn_success",
        ),
    ),
    AgentLoopCheck(
        id="process_timeline",
        title="Task-run process timeline",
        paths=(
            "runtime/memory/runtime_state/process_timeline.py",
            "runtime/sensing/gateway/agent_trace_router.py",
            "runtime/sensing/gateway/_agent_trace_router_trace.py",
            "tests/test_process_timeline.py",
            "tests/test_agent_trace_router.py",
        ),
        required_terms=(
            "echo.process_timeline.v1",
            "build_task_run_process_timeline",
            "/api/agent-trace/task-runs/{task_id}/process-timeline",
            "test_trace_task_run_process_timeline_merges_review_and_ledger",
        ),
    ),
    AgentLoopCheck(
        id="interrupt_and_resume_safety",
        title="Interrupt, resume, and stale-run safety",
        paths=(
            "runtime/sensing/gateway/realtime_turn_lifecycle.py",
            "runtime/sensing/gateway/realtime_cerebrum.py",
            "tests/test_realtime_cerebrum.py",
        ),
        required_terms=(
            "turn/interrupt",
            "_active_turn_ids",
            "_record_task_run_finished",
            "test_turn_interrupt_kills_in_flight_subprocess",
            "test_stale_background_watchers_reaped_on_next_turn",
        ),
    ),
    AgentLoopCheck(
        id="operator_workbench_trace",
        title="Operator workbench trace state",
        paths=(
            "runtime/sensing/gateway/realtime_turn_lifecycle.py",
            "tests/test_realtime_cerebrum.py",
            "frontend/src/components/workspace/agent-operator-panel.tsx",
            "frontend/src/components/workspace/agent-operator/index.tsx",
            "frontend/src/components/workspace/agent-workbench-panel.tsx",
            "frontend/src/components/workspace/agent-workbench-snapshot.ts",
        ),
        required_terms=(
            "turn/plan/updated",
            "workspaceFocus",
            "workbenchSnapshot",
            "turn/metaSkill/hint",
            "fetchAgentCompetitorScorecard",
        ),
    ),
    AgentLoopCheck(
        id="mixed_mode_completion",
        title="Mixed browser and code completion evidence",
        paths=(
            "runtime/core/cerebrum/react_guards.py",
            "runtime/core/cerebrum/react_browser_guards.py",
            "tests/test_react_guard_browser.py",
        ),
        required_terms=(
            "mixed-mode completion guard",
            "_mixed_mode_completion_guard",
            "_has_successful_browser_action",
            "workspace code edit",
            "code verification command",
        ),
    ),
)


def compute_agent_loop_quality(
    *,
    root: str | Path | None = None,
) -> dict[str, Any]:
    base = Path(root) if root is not None else default_project_root(Path(__file__))
    checks = [_check_row(base, check) for check in CHECKS]
    total_weight = sum(int(row["weight"]) for row in checks)
    passed_weight = sum(int(row["weight"]) for row in checks if row["passed"])
    return {
        "schema": "echo.agent_loop_quality.v1",
        "score": round(passed_weight / max(1, total_weight), 3),
        "passed": sum(1 for row in checks if row["passed"]),
        "total": len(checks),
        "ready": all(row["passed"] for row in checks),
        "checks": checks,
        "next_actions": [str(row["next_action"]) for row in checks if not row["passed"]],
    }


def _check_row(base: Path, check: AgentLoopCheck) -> dict[str, Any]:
    paths = [{"path": path, "exists": (base / path).exists()} for path in check.paths]
    text = "\n".join(_read_text(base / row["path"]) for row in paths if row["exists"]).lower()
    missing_paths = [str(row["path"]) for row in paths if not row["exists"]]
    missing_terms = [term for term in check.required_terms if term.lower() not in text]
    return {
        "id": check.id,
        "title": check.title,
        "weight": check.weight,
        "passed": not missing_paths and not missing_terms,
        "paths": paths,
        "missing_paths": missing_paths,
        "required_terms": list(check.required_terms),
        "missing_terms": missing_terms,
        "next_action": f"Complete agent-loop quality check: {check.title}.",
    }


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


__all__ = [
    "AgentLoopCheck",
    "CHECKS",
    "compute_agent_loop_quality",
]
