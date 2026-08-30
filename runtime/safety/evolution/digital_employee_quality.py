from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from runtime.platform.process.paths import project_root as default_project_root


@dataclass(frozen=True)
class DigitalEmployeeCheck:
    id: str
    title: str
    paths: tuple[str, ...]
    required_terms: tuple[str, ...]
    weight: int = 1


CHECKS: tuple[DigitalEmployeeCheck, ...] = (
    DigitalEmployeeCheck(
        id="projectos_cowork_bridge",
        title="Cowork group to Project OS execution bridge",
        paths=(
            "runtime/projectos/cowork_bridge.py",
            "runtime/projectos/timeline.py",
            "runtime/sensing/gateway/projects_router.py",
            "tests/test_projectos_cowork.py",
            "tests/test_projects_router.py",
        ),
        required_terms=(
            "run_project_from_group",
            "echo.projectos.run_trace.v1",
            "echo.projectos.process_timeline.v1",
            "/api/projects/from-group/{thread_id}",
            "/api/projects/{project_id}/process-timeline",
            "project_for_thread",
            "project_process_timeline",
            "process_events_persisted",
            "available_actions",
            "action_specs",
        ),
    ),
    DigitalEmployeeCheck(
        id="realtime_project_mode",
        title="Realtime project-mode employee loop",
        paths=(
            "runtime/sensing/gateway/realtime_cerebrum.py",
            "runtime/sensing/gateway/_realtime_cerebrum_project_os.py",
            "runtime/projectos/cowork_bridge.py",
            "tests/test_realtime_cerebrum.py",
        ),
        required_terms=(
            "Project OS 已接管并运行项目",
            "Project OS 已继续推进项目",
            "echo.projectos.run_trace.v1",
            "echo.projectos.control_trace.v1",
            "project_os_max_ticks",
            "/project task",
        ),
    ),
    DigitalEmployeeCheck(
        id="team_task_timeline",
        title="Team task process timeline",
        paths=(
            "runtime/sensing/gateway/team_tasks_router.py",
            "tests/test_team_tasks_router.py",
            "frontend/src/core/team-tasks/api.ts",
            "frontend/src/core/team-tasks/hooks.ts",
            "frontend/src/components/workspace/collab/team-tasks-panel.tsx",
            "frontend/src/components/workspace/collab/team-tasks-panel.test.tsx",
        ),
        required_terms=(
            "/api/team-tasks/{task_id}/process-timeline",
            "echo.team_task_process_timeline.v1",
            "process_events_persisted",
            "team_role_start",
            "team_role_end",
            "run_done",
            "getTaskProcessTimeline",
            "useTeamTaskProcessTimeline",
            "流程证据",
        ),
    ),
    DigitalEmployeeCheck(
        id="project_control_recovery",
        title="Project task control and recovery",
        paths=(
            "runtime/projectos/engine.py",
            "runtime/projectos/cowork_bridge.py",
            "runtime/sensing/gateway/realtime_cerebrum.py",
            "runtime/sensing/gateway/_realtime_cerebrum_project_os.py",
            "tests/test_projects_router.py",
            "tests/test_realtime_cerebrum.py",
        ),
        required_terms=(
            "recover_and_run",
            "intervene_task",
            "task.intervention",
            "task.intervention_rejected",
            "realtime_command",
            "Project OS 任务控制命令未执行",
        ),
    ),
    DigitalEmployeeCheck(
        id="operator_visible_employee_actions",
        title="Operator-visible employee actions",
        paths=(
            "runtime/projectos/cowork_bridge.py",
            "runtime/projectos/timeline.py",
            "runtime/sensing/gateway/projects_router.py",
            "runtime/safety/evolution/agent_competitor_scorecard.py",
            "runtime/safety/evolution/_agent_competitor_scorecard_drilldown.py",
            "frontend/src/components/workspace/agent-operator-panel.tsx",
            "tests/test_evolution_modules.py",
        ),
        required_terms=(
            "project_process_timeline",
            "/api/projects/{project_id}/process-timeline",
            "team_task_process_timeline",
            "/api/team-tasks/{task_id}/process-timeline",
            "available_actions",
            "Queue scorecard gap",
            "scorecard_gap_backlog",
        ),
    ),
)


def compute_digital_employee_quality(
    *,
    root: str | Path | None = None,
) -> dict[str, Any]:
    base = Path(root) if root is not None else default_project_root(Path(__file__))
    checks = [_check_row(base, check) for check in CHECKS]
    total_weight = sum(int(row["weight"]) for row in checks)
    passed_weight = sum(int(row["weight"]) for row in checks if row["passed"])
    return {
        "schema": "echo.digital_employee_quality.v1",
        "score": round(passed_weight / max(1, total_weight), 3),
        "passed": sum(1 for row in checks if row["passed"]),
        "total": len(checks),
        "ready": all(row["passed"] for row in checks),
        "checks": checks,
        "next_actions": [str(row["next_action"]) for row in checks if not row["passed"]],
    }


def _check_row(base: Path, check: DigitalEmployeeCheck) -> dict[str, Any]:
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
        "next_action": f"Complete digital-employee quality check: {check.title}.",
    }


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


__all__ = [
    "CHECKS",
    "DigitalEmployeeCheck",
    "compute_digital_employee_quality",
]
