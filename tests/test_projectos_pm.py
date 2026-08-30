"""PM read-model: milestone health, burndown, risks/next-actions, retro.

Covers the "real project management" layer on top of Project OS: derived
health (on_track/at_risk/overdue/blocked/completed), progress & estimates,
the PM console (risks/blockers/next actions/assignments), and the retro that
a finished project produces.
"""

from __future__ import annotations

from datetime import UTC, datetime

from runtime.memory.cowork import service
from runtime.memory.cowork.group_store import GroupStore
from runtime.projectos.cowork_bridge import full_project_state
from runtime.projectos.model import Milestone, Project, Task
from runtime.projectos.pm import (
    build_pm_report,
    build_retro,
    derive_milestone_pm,
)
from runtime.projectos.store import ProjectStore

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def _task(**kw) -> Task:
    base = dict(id="T1", milestone_id="M1", type="code", goal="g")
    base.update(kw)
    return Task(**base)


# ── 模型 & 存储往返 ───────────────────────────────────────────
def test_task_roundtrips_pm_fields() -> None:
    t = Task(
        id="T1",
        milestone_id="M1",
        type="code",
        goal="g",
        priority="P0",
        estimate=2.5,
        due_at="2026-08-22",
        acceptance_criteria=["it works", "tests pass"],
        team_mode="cluster",
    )
    restored = Task.from_dict(t.to_dict())
    assert restored.priority == "P0"
    assert restored.estimate == 2.5
    assert restored.due_at == "2026-08-22"
    assert restored.acceptance_criteria == ["it works", "tests pass"]
    assert restored.team_mode == "cluster"
    # sanitized
    bad = Task.from_dict(
        {"id": "T2", "milestone_id": "M1", "type": "code", "goal": "g", "priority": "P9"}
    )
    assert bad.priority == "P2"


def test_milestone_project_roundtrip_pm_fields() -> None:
    ms = Milestone(
        id="M1",
        name="plan",
        goal="g",
        priority="P1",
        planned_start="2026-08-18",
        due_at="2026-08-25",
    )
    restored = Milestone.from_dict(ms.to_dict())
    assert restored.priority == "P1"
    assert restored.planned_start == "2026-08-18"
    assert restored.due_at == "2026-08-25"

    p = Project(id="P1", name="x", goal="g", owner="pm-1", created_at="2026-08-20")
    assert Project.from_dict(p.to_dict()).owner == "pm-1"


def test_store_normalize_preserves_pm_fields(tmp_path) -> None:
    s = ProjectStore(base_dir=tmp_path)
    s.save_project(Project(id="P1", name="x", goal="g", owner="pm-1"))
    s.save_milestone(
        "P1",
        Milestone(id="M1", name="m", goal="g", priority="P1", due_at="2026-08-25"),
    )
    s.save_task(
        Task(
            id="T1",
            milestone_id="M1",
            type="research",
            goal="g",
            priority="P0",
            estimate=1.5,
            due_at="2026-08-21",
            acceptance_criteria=["a"],
        )
    )
    t = s.get_task("T1")
    assert t.priority == "P0"
    assert t.estimate == 1.5
    assert t.due_at == "2026-08-21"
    assert t.acceptance_criteria == ["a"]
    m = s.get_milestone("M1")
    assert m.priority == "P1"
    assert m.due_at == "2026-08-25"


# ── 里程碑健康度 ─────────────────────────────────────────────
def test_health_completed_and_overdue() -> None:
    done_ms = Milestone(id="M1", name="m", goal="g", status="done", due_at="2026-08-10")
    assert derive_milestone_pm(done_ms, [], now=NOW)["health"] == "completed"

    ms = Milestone(id="M1", name="m", goal="g", due_at="2026-08-19")  # already passed
    tasks = [_task(id="T1", status="pending", due_at="2026-08-19")]
    pm = derive_milestone_pm(ms, tasks, now=NOW)
    assert pm["health"] == "overdue"
    assert len(pm["overdue_tasks"]) == 1


def test_health_at_risk_and_on_track() -> None:
    # remaining estimate (3d) > days left (2d) → at_risk
    ms = Milestone(id="M1", name="m", goal="g", planned_start="2026-08-18", due_at="2026-08-22")
    tasks = [_task(id="T1", status="pending", estimate=3.0)]
    assert derive_milestone_pm(ms, tasks, now=NOW)["health"] == "at_risk"

    # plenty of time → on_track
    ms2 = Milestone(id="M2", name="m", goal="g", due_at="2026-09-30")
    assert derive_milestone_pm(ms2, tasks, now=NOW)["health"] == "on_track"


def test_health_blocked() -> None:
    ms = Milestone(id="M1", name="m", goal="g", status="blocked")
    pm = derive_milestone_pm(ms, [_task()], now=NOW)
    assert pm["health"] == "blocked"
    assert pm["failed"] == 0


def test_progress_and_estimate_derivation() -> None:
    ms = Milestone(id="M1", name="m", goal="g", due_at="2026-09-30")
    tasks = [
        _task(id="T1", status="done", estimate=2.0),
        _task(id="T2", status="pending", estimate=1.0),
        _task(id="T3", status="running", estimate=1.0),
    ]
    pm = derive_milestone_pm(ms, tasks, now=NOW)
    assert pm["done"] == 1
    assert pm["total"] == 3
    assert pm["progress"] == round(1 / 3, 3)
    assert pm["remaining_estimate"] == 2.0


# ── PM 驾驶舱 ────────────────────────────────────────────────
def _store_with_project(tmp_path) -> ProjectStore:
    s = ProjectStore(base_dir=tmp_path)
    s.save_project(Project(id="P1", name="x", goal="g", status="running", started_at="2026-08-18"))
    s.save_milestone(
        "P1",
        Milestone(id="M1", name="build", goal="g", status="in_progress", due_at="2026-08-25"),
    )
    s.save_milestone(
        "P1", Milestone(id="M2", name="ship", goal="g", due_at="2026-09-01", dependencies=["M1"])
    )
    s.save_task(
        Task(
            id="T1",
            milestone_id="M1",
            type="code",
            goal="write feature",
            status="done",
            priority="P0",
            estimate=2.0,
            assigned_agent="alice",
        )
    )
    s.save_task(
        Task(
            id="T2",
            milestone_id="M1",
            type="code",
            goal="fix bug",
            status="pending",
            priority="P1",
            estimate=1.0,
            due_at="2026-08-19",  # overdue
            assigned_agent="bob",
        )
    )
    s.save_task(
        Task(
            id="T3",
            milestone_id="M2",
            type="code",
            goal="release",
            status="pending",
            priority="P2",
            estimate=0.5,
            depends_on=["T1"],
        )
    )
    return s


def test_pm_report_progress_burndown_risks_actions(tmp_path) -> None:
    s = _store_with_project(tmp_path)
    report = build_pm_report(s, "P1", now=NOW)
    assert report is not None
    # progress: 1 done / 3 total
    assert report["overall_progress"] == round(1 / 3, 3)
    assert report["done_tasks"] == 1
    assert report["total_tasks"] == 3
    # burndown rows per milestone
    assert len(report["burndown"]) == 2
    # risks: overdue task T2
    assert any(r.get("type") == "task" and r.get("task_id") == "T2" for r in report["risks"])
    # blockers empty (no blocked milestone), overdue lists T2
    assert report["blockers"] == []
    assert any(m["name"] == "build" and m["overdue_tasks"] for m in report["milestones"])
    # next actions: runnable = T2 (ready) + T3 (M2, deps met? T1 done → T3 ready)
    task_ids = {a["task_id"] for a in report["next_actions"]}
    assert "T2" in task_ids
    # assignments map
    assert "alice" in report["assignments"] and "bob" in report["assignments"]


def test_pm_report_blocked_milestone_lists_blocker(tmp_path) -> None:
    s = _store_with_project(tmp_path)
    ms = s.get_milestone("M1")
    ms.status = "blocked"
    s.save_milestone("P1", ms)
    report = build_pm_report(s, "P1", now=NOW)
    assert report["blockers"] == ["build"]
    assert any(r.get("health") == "blocked" for r in report["risks"])


def test_retro_on_done_project(tmp_path) -> None:
    s = ProjectStore(base_dir=tmp_path)
    s.save_project(
        Project(
            id="P1",
            name="x",
            goal="g",
            status="done",
            started_at="2026-08-18T09:00:00+00:00",
            finished_at="2026-08-20T18:00:00+00:00",
        )
    )
    s.save_milestone("P1", Milestone(id="M1", name="build", goal="g", status="done"))
    s.save_task(
        Task(
            id="T1",
            milestone_id="M1",
            type="code",
            goal="a",
            status="done",
            attempts=1,
            estimate=1.0,
        )
    )
    s.save_task(
        Task(
            id="T2",
            milestone_id="M1",
            type="code",
            goal="b",
            status="failed",
            attempts=2,
            estimate=0.5,
        )
    )
    retro = build_retro(s, "P1")
    assert retro is not None
    assert retro["done_tasks"] == 1
    assert retro["failed_tasks"] == 1
    assert retro["attempts_total"] == 3
    assert retro["duration_days"] == 2
    assert any("QA" in r for r in retro["recommendations"])


def test_full_project_state_includes_pm_and_retro(tmp_path) -> None:
    gs = GroupStore(base_dir=tmp_path)
    service.invite_member(gs, "t", actor="u", target_id="alice", kind="agent")
    store = ProjectStore(base_dir=tmp_path)
    store.save_project(Project(id="P1", name="x", goal="g", status="done"))
    store.save_milestone("P1", Milestone(id="M1", name="m", goal="g", status="done"))
    store.save_task(Task(id="T1", milestone_id="M1", type="code", goal="g", status="done"))
    state = full_project_state(store, "P1")
    assert state["pm"] is not None
    assert state["retro"] is not None
    assert state["pm"]["overall_progress"] == 1.0


def test_ready_tasks_orders_by_priority_then_due_then_estimate() -> None:
    """PM 优先级真正驱动执行顺序：P0 先于 P1，相同优先级按截止日期、估时升序。"""
    from runtime.projectos.model import ready_tasks

    t_p0_late = Task(
        id="p0-late",
        milestone_id="M1",
        type="code",
        goal="urgent but later due",
        priority="P0",
        due_at="2026-09-01",
        estimate=5.0,
    )
    t_p0_early = Task(
        id="p0-early",
        milestone_id="M1",
        type="code",
        goal="urgent and due soon",
        priority="P0",
        due_at="2026-08-21",
        estimate=1.0,
    )
    t_p1 = Task(
        id="p1",
        milestone_id="M1",
        type="code",
        goal="normal",
        priority="P1",
        due_at="2026-08-25",
        estimate=0.5,
    )
    t_p3 = Task(
        id="p3",
        milestone_id="M1",
        type="research",
        goal="low",
        priority="P3",
    )
    ordered = ready_tasks([t_p3, t_p1, t_p0_late, t_p0_early])
    assert [t.id for t in ordered] == ["p0-early", "p0-late", "p1", "p3"]


def test_ready_tasks_respects_dag_dependencies() -> None:
    """依赖未完成的任务不进入就绪前沿，即使它优先级最高。"""
    from runtime.projectos.model import ready_tasks

    done_dep = Task(id="dep", milestone_id="M1", type="research", goal="r", status="done")
    ready_high = Task(
        id="blocked",
        milestone_id="M1",
        type="code",
        goal="c",
        priority="P0",
        depends_on=["dep"],
        status="pending",
    )
    waiting_low = Task(
        id="waiting",
        milestone_id="M1",
        type="code",
        goal="w",
        priority="P0",
        depends_on=["not-done"],
        status="pending",
    )
    standalone = Task(id="free", milestone_id="M1", type="code", goal="f", priority="P2")
    ordered = ready_tasks([ready_high, waiting_low, standalone, done_dep])
    # 依赖已就绪的 P0 排在 P2 前；依赖未完成的即使 P0 也不进入前沿
    assert [t.id for t in ordered] == ["blocked", "free"]

