"""Project OS: model DAG, store round-trips, and the milestone-driven engine."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock

import pytest

import runtime.projectos.store as project_store_module
from runtime.projectos.engine import (
    HARD_MAX_RUN_TICKS,
    ProjectEngine,
    normalize_run_ticks,
    stub_generate_milestones,
)
from runtime.projectos.model import Milestone, Project, Task, ready_tasks
from runtime.projectos.store import (
    ProjectAlreadyBoundError,
    ProjectBindingActiveError,
    ProjectBindingMigrationRequiredError,
    ProjectClaimActiveError,
    ProjectDeletedError,
    ProjectDeleteInProgressError,
    ProjectStore,
    ProjectThreadBoundError,
    ProjectThreadDeletingError,
)
from runtime.projectos.timeline import project_process_timeline


# ── model ────────────────────────────────────────────────────────────────────
def test_stub_decompose_research_node_is_swarm() -> None:
    """The fallback decomposer flags the research node as a swarm task so a
    roster-aware engine brainstorms it across the group."""
    from runtime.projectos.engine import stub_decompose_tasks

    tasks = stub_decompose_tasks(Milestone(id="MS", name="m", goal="g"))
    research = next(t for t in tasks if t.type == "research")
    assert research.team_mode == "swarm"


def test_engine_dispatches_team_mode_tasks_to_run_task_team(tmp_path) -> None:
    """swarm/cluster tasks go to the injected run_task_team; single stays on
    execute_task — the project × cluster/swarm seam."""
    from runtime.projectos.engine import stub_decompose_tasks

    store = ProjectStore(base_dir=tmp_path)
    eng = ProjectEngine(
        store,
        generate_milestones=stub_generate_milestones,
        decompose_tasks=stub_decompose_tasks,
        execute_task=lambda task, ctx: "single-exec",
        run_task_team=lambda task, ctx: f"team-{task.team_mode}",
    )
    p = eng.plan("t", "g")
    eng.run(p.id, max_ticks=50)
    tasks = store.tasks_for_milestone(p.milestone_ids[0])
    by_type = {t.type: t for t in tasks}
    # research node is swarm → team hook; code node is single → execute.
    assert by_type["research"].output == "team-swarm"
    assert by_type["code"].output == "single-exec"


def test_ready_tasks_respects_dag() -> None:
    t1 = Task(id="T1", milestone_id="M", type="design", goal="a")
    t2 = Task(id="T2", milestone_id="M", type="code", goal="b", depends_on=["T1"])
    # T2 blocked until T1 done
    assert [t.id for t in ready_tasks([t1, t2])] == ["T1"]
    t1.status = "done"
    assert [t.id for t in ready_tasks([t1, t2])] == ["T2"]


def test_roundtrips() -> None:
    p = Project(
        id="P1",
        name="x",
        goal="g",
        milestone_ids=["M1"],
        execution_thread_id="thread-primary",
    )
    assert Project.from_dict(p.to_dict()).milestone_ids == ["M1"]
    assert Project.from_dict(p.to_dict()).execution_thread_id == "thread-primary"
    m = Milestone(
        id="M1",
        name="n",
        goal="g",
        spec={"power": "<5W"},
        success_criteria=["works"],
        dependencies=["M0"],
    )
    assert Milestone.from_dict(m.to_dict()).spec == {"power": "<5W"}
    t = Task(id="T1", milestone_id="M1", type="research", goal="g", depends_on=["T0"])
    assert Task.from_dict(t.to_dict()).depends_on == ["T0"]
    # team_mode (single/swarm/cluster) round-trips and is sanitized.
    tw = Task(id="T2", milestone_id="M1", type="research", goal="g", team_mode="swarm")
    assert Task.from_dict(tw.to_dict()).team_mode == "swarm"
    tc = Task(id="T3", milestone_id="M1", type="code", goal="g", team_mode="cluster")
    assert Task.from_dict(tc.to_dict()).team_mode == "cluster"
    assert (
        Task.from_dict(
            {"id": "T4", "milestone_id": "M1", "type": "code", "goal": "g", "team_mode": "banana"}
        ).team_mode
        == "single"
    )


# ── store ────────────────────────────────────────────────────────────────────
def test_store_roundtrip(tmp_path) -> None:
    s = ProjectStore(base_dir=tmp_path)
    s.save_project(Project(id="P1", name="x", goal="g"))
    s.save_milestone("P1", Milestone(id="M1", name="m", goal="g"))
    s.save_task(Task(id="T1", milestone_id="M1", type="code", goal="g"))
    assert s.get_project("P1").goal == "g"
    assert [m.id for m in s.milestones_for("P1")] == ["M1"]
    assert [t.id for t in s.tasks_for_milestone("M1")] == ["T1"]


def test_project_delete_lease_is_durable_idempotent_and_irreversible(tmp_path) -> None:
    first = ProjectStore(base_dir=tmp_path)
    second = ProjectStore(base_dir=tmp_path)
    first.save_project(Project(id="P-delete-lease", name="delete", goal="g"))
    first.bind_thread("thread-delete-lease", "P-delete-lease")

    initial = first.begin_project_delete(
        "P-delete-lease",
        event_kind="project.delete_projection_pending",
    )
    resumed = second.begin_project_delete(
        "P-delete-lease",
        event_kind="project.delete_projection_pending",
    )

    assert initial.resumed is False
    assert resumed.resumed is True
    assert resumed.token == initial.token
    assert [event["kind"] for event in first.events_for_project("P-delete-lease")].count(
        "project.delete_projection_pending"
    ) == 1
    with pytest.raises(ProjectDeleteInProgressError):
        second.append_event("P-delete-lease", kind="late.write", payload={})
    with pytest.raises(ProjectDeleteInProgressError):
        second.unbind_thread("thread-delete-lease")
    with (
        sqlite3.connect(str(tmp_path / "projectos.db")) as conn,
        pytest.raises(sqlite3.IntegrityError, match="delete in progress"),
    ):
        conn.execute("DELETE FROM thread_projects WHERE thread_id='thread-delete-lease'")
    assert first.project_for_thread("thread-delete-lease").id == "P-delete-lease"

    assert not hasattr(first, "cancel_project_delete")
    with pytest.raises(ProjectDeleteInProgressError):
        second.append_event("P-delete-lease", kind="write.still_fenced", payload={})


def test_finalized_delete_tombstone_rejects_all_stale_source_writers(tmp_path) -> None:
    first = ProjectStore(base_dir=tmp_path)
    stale = ProjectStore(base_dir=tmp_path)
    project = Project(id="P-final-delete", name="delete", goal="g")
    milestone = Milestone(id="M-final-delete", name="m", goal="g", status="in_progress")
    task = Task(
        id="T-final-delete",
        milestone_id=milestone.id,
        type="code",
        goal="g",
    )
    first.save_project(project)
    first.save_milestone(project.id, milestone)
    first.save_task(task)
    stale_project = stale.get_project(project.id)
    stale_milestone = stale.get_milestone(milestone.id)
    stale_task = stale.get_task(task.id)
    assert stale_project is not None
    assert stale_milestone is not None
    assert stale_task is not None
    lease = first.begin_project_delete(
        project.id,
        event_kind="project.delete_projection_pending",
    )
    ready = Barrier(2)

    def finalize() -> bool:
        ready.wait(timeout=5)
        return first.finalize_project_delete(project.id, lease.token)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(finalize)
        ready.wait(timeout=5)
        assert future.result(timeout=5) is True

    stale_project.name = "revived"
    stale_milestone.name = "revived"
    stale_task.goal = "revived"
    for write in (
        lambda: stale.save_project(stale_project),
        lambda: stale.save_milestone(project.id, stale_milestone),
        lambda: stale.save_task(stale_task),
        lambda: stale.append_event(project.id, kind="late.write", payload={}),
        lambda: stale.bind_thread("thread-revive", project.id),
        lambda: stale.claim_task(task.id),
        lambda: stale.claim_milestone_decomposition(milestone.id),
        lambda: ProjectEngine(
            stale,
            generate_milestones=stub_generate_milestones,
            decompose_tasks=lambda _milestone: [],
        ).plan("revived", "g", project_id=project.id),
    ):
        with pytest.raises(ProjectDeletedError):
            write()

    with sqlite3.connect(str(tmp_path / "projectos.db")) as conn:
        assert conn.execute(
            "SELECT token FROM project_delete_tombstones WHERE project_id=?",
            (project.id,),
        ).fetchone() == (lease.token,)
        for statement, params in (
            (
                "INSERT INTO projects(id, doc) VALUES (?, '{}')",
                (project.id,),
            ),
            (
                "INSERT INTO milestones(id, project_id, doc) VALUES (?, ?, '{}')",
                (milestone.id, project.id),
            ),
            (
                "INSERT INTO tasks(id, milestone_id, doc) VALUES (?, ?, '{}')",
                (task.id, milestone.id),
            ),
            (
                "INSERT INTO project_events(id, project_id, kind, payload, created_at) "
                "VALUES ('EV-revive', ?, 'late.raw', '{}', 0)",
                (project.id,),
            ),
        ):
            with pytest.raises(sqlite3.IntegrityError, match="delete in progress"):
                conn.execute(statement, params)


def test_new_plan_namespaces_default_milestones_around_delete_tombstones(tmp_path) -> None:
    store = ProjectStore(base_dir=tmp_path)
    engine = ProjectEngine(
        store,
        generate_milestones=stub_generate_milestones,
        decompose_tasks=lambda _milestone: [],
    )
    first = engine.plan("first", "g", project_id="P-first")
    assert store.delete_project(first.id) is True

    second = engine.plan("second", "g", project_id="P-second")

    assert set(first.milestone_ids).isdisjoint(second.milestone_ids)
    assert store.get_project(second.id) is not None


def test_store_binds_thread_to_project(tmp_path) -> None:
    s = ProjectStore(base_dir=tmp_path)
    s.save_project(Project(id="P1", name="x", goal="g"))
    s.bind_thread("thread-1", "P1")
    assert s.project_for_thread("thread-1").id == "P1"
    assert s.project_for_thread("missing") is None


def test_thread_delete_reservation_is_durable_and_permanently_fences_binding(tmp_path) -> None:
    deleting = ProjectStore(base_dir=tmp_path)
    binding = ProjectStore(base_dir=tmp_path)
    deleting.save_project(Project(id="P-thread-delete", name="delete", goal="g"))

    lease = deleting.begin_thread_delete("thread-delete-reserved")
    resumed = binding.begin_thread_delete("thread-delete-reserved")

    assert lease.resumed is False
    assert lease.finalized is False
    assert resumed.resumed is True
    assert resumed.token == lease.token
    with pytest.raises(ProjectThreadDeletingError):
        binding.bind_thread("thread-delete-reserved", "P-thread-delete")
    with (
        sqlite3.connect(str(tmp_path / "projectos.db")) as conn,
        pytest.raises(sqlite3.IntegrityError, match="thread delete in progress"),
    ):
        conn.execute(
            "INSERT INTO thread_projects(thread_id, project_id) VALUES (?, ?)",
            ("thread-delete-reserved", "P-thread-delete"),
        )

    assert deleting.finalize_thread_delete("thread-delete-reserved", lease.token) is True
    finalized = binding.begin_thread_delete("thread-delete-reserved")
    assert finalized.finalized is True
    assert finalized.token == lease.token
    with pytest.raises(ProjectThreadDeletingError):
        binding.bind_thread("thread-delete-reserved", "P-thread-delete")


def test_bound_thread_refuses_delete_reservation_without_mutation(tmp_path) -> None:
    store = ProjectStore(base_dir=tmp_path)
    store.save_project(Project(id="P-thread-bound", name="bound", goal="g"))
    store.bind_thread("thread-bound-delete", "P-thread-bound")

    with pytest.raises(ProjectThreadBoundError) as raised:
        store.begin_thread_delete("thread-bound-delete")

    assert raised.value.project.id == "P-thread-bound"
    assert store.project_for_thread("thread-bound-delete").id == "P-thread-bound"


def test_thread_bind_and_delete_reservation_atomically_choose_one_winner(tmp_path) -> None:
    binding = ProjectStore(base_dir=tmp_path)
    deleting = ProjectStore(base_dir=tmp_path)
    binding.save_project(Project(id="P-thread-race", name="race", goal="g"))
    ready = Barrier(2)

    def bind() -> str:
        ready.wait(timeout=5)
        try:
            binding.bind_thread("thread-bind-delete-race", "P-thread-race")
        except ProjectThreadDeletingError:
            return "delete"
        return "bind"

    def delete() -> str:
        ready.wait(timeout=5)
        try:
            deleting.begin_thread_delete("thread-bind-delete-race")
        except ProjectThreadBoundError:
            return "bind"
        return "delete"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = [pool.submit(bind), pool.submit(delete)]
        winners = [future.result(timeout=5) for future in outcomes]

    assert winners[0] == winners[1]
    if winners[0] == "bind":
        assert binding.project_for_thread("thread-bind-delete-race").id == "P-thread-race"
    else:
        assert binding.project_for_thread("thread-bind-delete-race") is None
        with pytest.raises(ProjectThreadDeletingError):
            binding.bind_thread("thread-bind-delete-race", "P-thread-race")


@pytest.mark.parametrize("bind_once", [False, True])
def test_store_refuses_a_second_thread_for_the_same_project(tmp_path, bind_once: bool) -> None:
    store = ProjectStore(base_dir=tmp_path)
    store.save_project(Project(id="P1", name="one", goal="g"))

    store.bind_thread("thread-a", "P1")

    with pytest.raises(ProjectAlreadyBoundError) as raised:
        if bind_once:
            store.bind_thread_if_absent("thread-b", "P1")
        else:
            store.bind_thread("thread-b", "P1")

    assert raised.value.canonical_thread_id == "thread-a"
    assert raised.value.requested_thread_id == "thread-b"
    assert store.thread_for_project("P1") == "thread-a"
    assert store.get_project("P1").execution_thread_id == "thread-a"
    assert store.project_for_thread("thread-b") is None


def test_two_stores_atomically_choose_one_thread_for_a_project(tmp_path) -> None:
    first = ProjectStore(base_dir=tmp_path)
    second = ProjectStore(base_dir=tmp_path)
    first.save_project(Project(id="P1", name="one", goal="g"))
    ready = Barrier(2)

    def bind(store: ProjectStore, thread_id: str) -> tuple[str, str]:
        ready.wait(timeout=5)
        try:
            store.bind_thread(thread_id, "P1")
        except ProjectAlreadyBoundError as exc:
            return "lost", exc.canonical_thread_id
        return "won", thread_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda args: bind(*args),
                ((first, "thread-a"), (second, "thread-b")),
            )
        )

    winner = next(thread_id for outcome, thread_id in results if outcome == "won")
    assert [outcome for outcome, _thread_id in results].count("won") == 1
    assert [outcome for outcome, _thread_id in results].count("lost") == 1
    assert first.thread_project_map() == {winner: "P1"}
    assert first.get_project("P1").execution_thread_id == winner


def test_store_inactive_rebind_clears_the_old_projects_last_execution_thread(tmp_path) -> None:
    first = ProjectStore(base_dir=tmp_path)
    second = ProjectStore(base_dir=tmp_path)
    first.save_project(Project(id="P1", name="one", goal="g"))
    first.save_project(Project(id="P2", name="two", goal="g"))
    first.bind_thread("thread-move", "P1")

    second.bind_thread("thread-move", "P2")

    assert first.project_for_thread("thread-move").id == "P2"
    assert first.get_project("P1").execution_thread_id == ""
    assert first.get_project("P2").execution_thread_id == "thread-move"


@pytest.mark.parametrize("status", ["running", "blocked"])
def test_store_refuses_to_rebind_any_thread_from_an_active_or_blocked_project(
    tmp_path,
    status: str,
) -> None:
    first = ProjectStore(base_dir=tmp_path)
    second = ProjectStore(base_dir=tmp_path)
    initial_status = "running" if status == "running" else "planning"
    first.save_project(Project(id="P-old", name="old", goal="g", status=initial_status))
    first.save_project(Project(id="P-new", name="new", goal="g"))
    first.bind_thread("thread-move", "P-old")
    if status == "running":
        assert first.start_project_if_bound("P-old", "thread-move") is not None
    else:
        blocked = first.get_project("P-old")
        assert blocked is not None
        blocked.status = "blocked"
        first.save_project(blocked)

    with pytest.raises(ProjectBindingActiveError):
        second.bind_thread("thread-move", "P-new")

    assert first.project_for_thread("thread-move").id == "P-old"
    old = first.get_project("P-old")
    target = first.get_project("P-new")
    assert old is not None and old.execution_thread_id == "thread-move"
    assert target is not None and target.execution_thread_id == ""


def test_binding_generation_is_durable_across_tombstones_and_legacy_rows(tmp_path) -> None:
    store = ProjectStore(base_dir=tmp_path)
    store.save_project(Project(id="P1", name="one", goal="g"))
    store.save_project(Project(id="P2", name="two", goal="g"))

    _first, first_generation = store.bind_thread_versioned("thread-versioned", "P1")
    canonical, inserted, retry_generation = store.bind_thread_if_absent_versioned(
        "thread-versioned",
        "P1",
    )
    assert canonical.id == "P1"
    assert inserted is False
    assert retry_generation == first_generation == 1

    _detached, tombstone_generation = store.unbind_thread_versioned(
        "thread-versioned",
        expected_project_id="P1",
    )
    assert tombstone_generation == 2
    assert store.binding_snapshot("thread-versioned") == (None, 2)
    _second, second_generation = store.bind_thread_versioned("thread-versioned", "P2")
    assert second_generation == 3
    assert store.delete_project("P2") is True
    assert store.binding_snapshot("thread-versioned") == (None, 4)

    # Simulate a pre-generation database row. Reopening migrates it to the
    # generation-zero baseline, and the first transition advances it to one.
    store.save_project(Project(id="P-legacy", name="legacy", goal="g"))
    with sqlite3.connect(str(tmp_path / "projectos.db")) as conn:
        conn.execute(
            "INSERT INTO thread_projects(thread_id, project_id) VALUES (?, ?)",
            ("thread-legacy", "P-legacy"),
        )
        conn.execute(
            "DELETE FROM thread_project_generations WHERE thread_id=?",
            ("thread-legacy",),
        )
    reopened = ProjectStore(base_dir=tmp_path)
    legacy, legacy_generation = reopened.binding_snapshot("thread-legacy")
    assert legacy is not None and legacy.id == "P-legacy"
    assert legacy_generation == 0
    _legacy, legacy_tombstone = reopened.unbind_thread_versioned(
        "thread-legacy",
        expected_project_id="P-legacy",
    )
    assert legacy_tombstone == 1


def test_store_refuses_to_open_legacy_multi_binding_without_cross_store_migration(
    tmp_path,
) -> None:
    store = ProjectStore(base_dir=tmp_path)
    store.save_project(Project(id="P-legacy-multi", name="legacy", goal="g"))
    store.bind_thread("thread-canonical", "P-legacy-multi")
    with sqlite3.connect(str(tmp_path / "projectos.db")) as conn:
        conn.execute("DROP INDEX idx_thread_projects_single_project")
        conn.execute(
            "INSERT INTO thread_projects(thread_id, project_id) VALUES (?, ?)",
            ("thread-extra", "P-legacy-multi"),
        )

    with pytest.raises(ProjectBindingMigrationRequiredError) as raised:
        ProjectStore(base_dir=tmp_path)

    assert raised.value.duplicates == {"P-legacy-multi": ("thread-canonical", "thread-extra")}
    with sqlite3.connect(str(tmp_path / "projectos.db")) as conn:
        assert conn.execute(
            "SELECT thread_id FROM thread_projects WHERE project_id=? ORDER BY thread_id",
            ("P-legacy-multi",),
        ).fetchall() == [("thread-canonical",), ("thread-extra",)]


def test_blocked_target_without_started_at_requires_explicit_recovery(tmp_path) -> None:
    store = ProjectStore(base_dir=tmp_path)
    blocked = Project(id="P-blocked-target", name="blocked", goal="g", status="blocked")
    store.save_project(blocked)

    with pytest.raises(ProjectBindingActiveError):
        store.bind_thread("thread-new", blocked.id)
    with pytest.raises(ProjectBindingActiveError):
        store.bind_thread_if_absent("thread-new", blocked.id)

    assert store.binding_snapshot("thread-new") == (None, 0)
    current = store.get_project(blocked.id)
    assert current is not None and current.execution_thread_id == ""


def test_binding_restore_never_rewinds_a_new_active_execution_thread(tmp_path) -> None:
    store = ProjectStore(base_dir=tmp_path)
    store.save_project(Project(id="P1", name="one", goal="g", status="running"))
    store.bind_thread("thread-old", "P1")
    detached, _generation = store.unbind_thread_versioned(
        "thread-old",
        expected_project_id="P1",
    )
    assert detached is not None
    store.bind_thread("thread-new", "P1")
    started = store.start_project_if_bound("P1", "thread-new")
    assert started is not None and started.started_at

    restored = store.restore_thread_bindings(
        "P1",
        ["thread-old"],
        original_execution_thread_id="thread-old",
    )

    assert restored.execution_restored is False
    assert store.project_for_thread("thread-old") is None
    assert store.project_for_thread("thread-new").id == "P1"
    assert restored.conflict_project_ids == {"thread-old": "P1"}
    current = store.get_project("P1")
    assert current is not None
    assert current.status == "running"
    assert current.execution_thread_id == "thread-new"
    assert current.started_at == started.started_at
    events = store.events_for_project("P1")
    assert events[-1]["kind"] == "project.binding_restore_conflict"
    assert events[-1]["payload"]["active_execution_preserved"] is True


@pytest.mark.parametrize("bind_once", [False, True])
def test_store_refuses_to_move_a_started_targets_execution_boundary(
    tmp_path,
    bind_once: bool,
) -> None:
    first = ProjectStore(base_dir=tmp_path)
    second = ProjectStore(base_dir=tmp_path)
    first.save_project(Project(id="P-active", name="active", goal="g", status="running"))
    first.bind_thread("thread-execution", "P-active")
    assert first.start_project_if_bound("P-active", "thread-execution") is not None

    with pytest.raises(ProjectAlreadyBoundError):
        if bind_once:
            second.bind_thread_if_absent("thread-incoming", "P-active")
        else:
            second.bind_thread("thread-incoming", "P-active")

    active = first.get_project("P-active")
    assert active is not None and active.execution_thread_id == "thread-execution"
    assert first.project_for_thread("thread-execution").id == "P-active"
    assert first.project_for_thread("thread-incoming") is None


def test_store_delete_project_cascades_owned_rows_and_bindings(tmp_path) -> None:
    s = ProjectStore(base_dir=tmp_path)
    s.save_project(Project(id="P1", name="x", goal="g"))
    s.save_milestone("P1", Milestone(id="M1", name="m", goal="g"))
    s.save_task(Task(id="T1", milestone_id="M1", type="code", goal="g"))
    s.append_event("P1", kind="project.created", payload={})
    s.bind_thread("thread-1", "P1")

    assert s.thread_project_map() == {"thread-1": "P1"}
    assert s.delete_project("P1") is True
    assert s.delete_project("P1") is False
    assert s.get_project("P1") is None
    assert s.get_milestone("M1") is None
    assert s.get_task("T1") is None
    assert s.events_for_project("P1") == []
    assert s.thread_project_map() == {}


def test_store_project_events_roundtrip_and_limit(tmp_path) -> None:
    s = ProjectStore(base_dir=tmp_path)
    s.save_project(Project(id="P1", name="x", goal="g"))
    s.save_project(Project(id="P2", name="other", goal="g"))
    s.append_event("P1", kind="project.recover", payload={"n": 1}, created_at=1.0)
    s.append_event("P1", kind="task.intervention", payload={"n": 2}, created_at=2.0)
    s.append_event("P2", kind="task.intervention", payload={"n": 3}, created_at=3.0)

    events = s.events_for_project("P1")
    assert [event["kind"] for event in events] == ["project.recover", "task.intervention"]
    assert [event["payload"]["n"] for event in events] == [1, 2]
    assert [event["payload"]["n"] for event in s.events_for_project("P1", limit=1)] == [2]


def test_store_does_not_reuse_terminal_milestone_for_another_project(tmp_path) -> None:
    s = ProjectStore(base_dir=tmp_path)
    s.save_project(Project(id="P1", name="one", goal="g"))
    s.save_project(Project(id="P2", name="two", goal="g"))
    s.save_milestone(
        "P1",
        Milestone(id="MS1", name="done", goal="done", status="done"),
    )

    with pytest.raises(ValueError, match="another project"):
        s.save_milestone("P2", Milestone(id="MS1", name="new", goal="new"))

    assert s.milestones_for("P2") == []


def test_store_projects_latest_artifact_event_by_id(tmp_path) -> None:
    s = ProjectStore(base_dir=tmp_path)
    s.save_project(Project(id="P1", name="x", goal="g"))
    s.append_event(
        "P1",
        event_id="EV-old",
        kind="project.artifact_published",
        payload={"artifact": {"id": "ART-1", "title": "Old title", "path": "old.md"}},
        created_at=1.0,
    )
    s.append_event(
        "P1",
        event_id="EV-new",
        kind="project.artifact_published",
        payload={
            "artifact": {
                "id": "ART-1",
                "name": "Current title",
                "kind": "document",
                "path": "current.md",
                "summary": "Current published version",
            }
        },
        created_at=2.0,
    )
    s.append_event(
        "P1",
        event_id="EV-legacy",
        kind="project.artifact_published",
        payload={"artifact": {"title": "Legacy artifact", "url": "https://example.test/a"}},
        created_at=0.5,
    )

    assert s.artifacts_for_project("P1") == [
        {
            "id": "ART-1",
            "name": "Current title",
            "kind": "document",
            "path": "current.md",
            "summary": "Current published version",
        },
        {
            "id": "EV-legacy",
            "name": "Legacy artifact",
            "url": "https://example.test/a",
        },
    ]


def test_store_projects_latest_durable_decision_event_by_id(tmp_path) -> None:
    s = ProjectStore(base_dir=tmp_path)
    s.save_project(Project(id="P1", name="x", goal="g"))
    s.append_event(
        "P1",
        event_id="EV-decision-old",
        kind="project.decision_recorded",
        payload={
            "decision_id": "DEC-1",
            "decision": "Use option A",
            "actor": "alice",
        },
        created_at=1.0,
    )
    s.append_event(
        "P1",
        event_id="EV-decision-new",
        kind="project.decision_recorded",
        payload={
            "decision": {
                "id": "DEC-1",
                "title": "Adopt option B",
                "decision": "Use option B",
                "summary": "Lower operational risk",
                "milestone_id": "MS2",
            },
            "actor": "alice",
            "source_message": {"source_message_id": "chat-2"},
        },
        created_at=2.0,
    )
    s.append_event(
        "P1",
        event_id="EV-decision-legacy",
        kind="project.decision_recorded",
        payload={
            "decision": "Release on Friday",
            "rationale": "Support coverage is highest",
            "actor": "bob",
        },
        created_at=0.5,
    )

    assert s.decisions_for_project("P1") == [
        {
            "id": "DEC-1",
            "title": "Adopt option B",
            "summary": "Lower operational risk",
            "decision": "Use option B",
            "actor": "alice",
            "created_at": "1970-01-01T00:00:02+00:00",
            "source_message_id": "chat-2",
            "milestone_id": "MS2",
        },
        {
            "id": "EV-decision-legacy",
            "title": "Release on Friday",
            "summary": "Support coverage is highest",
            "decision": "Release on Friday",
            "actor": "bob",
            "created_at": "1970-01-01T00:00:00.500000+00:00",
        },
    ]


def test_store_rejects_unsafe_ids_and_oversized_payloads(tmp_path) -> None:
    s = ProjectStore(base_dir=tmp_path)
    with pytest.raises(ValueError, match="project_id"):
        s.save_project(Project(id="../bad", name="x", goal="g"))

    s.save_project(Project(id="P1", name="x", goal="g"))

    with pytest.raises(ValueError, match="thread_id"):
        s.bind_thread("../bad", "P1")
    with pytest.raises(ValueError, match="event kind"):
        s.append_event("P1", kind="../bad", payload={})
    with pytest.raises(ValueError, match="task_id"):
        s.save_task(Task(id="../bad", milestone_id="MS1", type="code", goal="g"))
    with pytest.raises(ValueError, match="task output"):
        s.save_task(
            Task(
                id="T-big",
                milestone_id="MS1",
                type="code",
                goal="g",
                output="x" * (1024 * 1024 + 1),
            )
        )


def test_store_skips_corrupt_rows_instead_of_crashing(tmp_path) -> None:
    s = ProjectStore(base_dir=tmp_path)
    with s._lock, s._conn() as conn:  # noqa: SLF001
        conn.execute("INSERT INTO projects(id, doc) VALUES (?, ?)", ("P-bad", "{not-json"))
        conn.execute(
            "INSERT INTO milestones(id, project_id, doc) VALUES (?, ?, ?)",
            ("MS-bad", "P-bad", "{not-json"),
        )
        conn.execute(
            "INSERT INTO tasks(id, milestone_id, doc) VALUES (?, ?, ?)",
            ("T-bad", "MS-bad", "{not-json"),
        )

    assert s.get_project("P-bad") is None
    assert s.get_milestone("MS-bad") is None
    assert s.get_task("T-bad") is None
    assert s.list_projects() == []
    assert s.milestones_for("P-bad") == []
    assert s.tasks_for_milestone("MS-bad") == []


def test_store_task_terminal_status_is_immutable_by_default(tmp_path) -> None:
    s = ProjectStore(base_dir=tmp_path)
    s.save_project(Project(id="P1", name="p", goal="g"))
    s.save_milestone("P1", Milestone(id="M1", name="m", goal="g"))
    original = Task(id="T1", milestone_id="M1", type="code", goal="g")
    original.status = "done"
    original.output = "accepted"
    s.save_task(original)

    stale_failure = Task(id="T1", milestone_id="M1", type="code", goal="g")
    stale_failure.status = "failed"
    stale_failure.output = "late failure"
    returned = s.save_task(stale_failure)

    assert returned.status == "done"
    assert returned.output == "accepted"
    stored = s.get_task("T1")
    assert stored.status == "done"
    assert stored.output == "accepted"


def test_store_task_terminal_status_can_be_reopened_explicitly(tmp_path) -> None:
    s = ProjectStore(base_dir=tmp_path)
    s.save_project(Project(id="P1", name="p", goal="g"))
    s.save_milestone("P1", Milestone(id="M1", name="m", goal="g"))
    original = Task(id="T1", milestone_id="M1", type="code", goal="g")
    original.status = "failed"
    original.output = "bad"
    original.attempts = 2
    s.save_task(original)

    recovered = Task(id="T1", milestone_id="M1", type="code", goal="g")
    recovered.status = "pending"
    recovered.output = None
    recovered.attempts = 0
    returned = s.save_task(recovered, allow_terminal_rewrite=True)

    assert returned.status == "pending"
    assert returned.output is None
    assert returned.attempts == 0
    assert s.get_task("T1").status == "pending"


def test_active_task_claim_cannot_be_revoked_by_operator_save(tmp_path) -> None:
    first = ProjectStore(base_dir=tmp_path)
    second = ProjectStore(base_dir=tmp_path)
    first.save_project(Project(id="P1", name="x", goal="g"))
    first.save_milestone(
        "P1",
        Milestone(id="M1", name="m", goal="g", status="in_progress"),
    )
    first.save_task(Task(id="T1", milestone_id="M1", type="code", goal="g"))

    claimed_first = first.claim_task("T1")
    assert claimed_first is not None
    first_task, first_claim_id = claimed_first

    reset = second.get_task("T1")
    assert reset is not None
    reset.status = "pending"
    with pytest.raises(ProjectClaimActiveError):
        second.save_task(reset, allow_terminal_rewrite=True)
    assert second.claim_task("T1") is None

    first_task.status = "done"
    first_task.output = "only worker"
    with pytest.raises(ProjectClaimActiveError):
        first.save_task(first_task)
    current, committed = first.finalize_task_claim(first_task, first_claim_id)
    assert committed is True
    assert current.status == "done"
    assert current.output == "only worker"


def test_save_task_locks_before_reading_and_cannot_revoke_a_new_claim(
    tmp_path, monkeypatch
) -> None:
    saving_store = ProjectStore(base_dir=tmp_path)
    claiming_store = ProjectStore(base_dir=tmp_path)
    saving_store.save_project(Project(id="P1", name="x", goal="g"))
    saving_store.save_milestone(
        "P1",
        Milestone(id="M1", name="m", goal="g", status="in_progress"),
    )
    saving_store.save_task(Task(id="T1", milestone_id="M1", type="code", goal="g"))
    stale = saving_store.get_task("T1")
    assert stale is not None
    stale.goal = "stale writer"

    read_started = Event()
    release_save = Event()
    original_parser = project_store_module._task_from_doc  # noqa: SLF001

    def block_after_read(raw):
        read_started.set()
        assert release_save.wait(timeout=5)
        return original_parser(raw)

    monkeypatch.setattr(project_store_module, "_task_from_doc", block_after_read)
    claim_started = Event()
    original_claim = claiming_store.claim_task

    def mark_claim_started(*args, **kwargs):
        claim_started.set()
        return original_claim(*args, **kwargs)

    monkeypatch.setattr(claiming_store, "claim_task", mark_claim_started)
    with ThreadPoolExecutor(max_workers=2) as pool:
        save_future = pool.submit(saving_store.save_task, stale)
        assert read_started.wait(timeout=5)
        with (
            sqlite3.connect(str(saving_store._db), timeout=0) as probe,  # noqa: SLF001
            pytest.raises(sqlite3.OperationalError, match="locked"),
        ):
            probe.execute("BEGIN IMMEDIATE")
        claim_future = pool.submit(claiming_store.claim_task, "T1")
        assert claim_started.wait(timeout=5)
        release_save.set()
        save_future.result(timeout=5)
        claimed = claim_future.result(timeout=5)

    assert claimed is not None
    task, claim_id = claimed
    with sqlite3.connect(str(saving_store._db)) as conn:  # noqa: SLF001
        assert conn.execute("SELECT claim_id FROM task_claims WHERE task_id='T1'").fetchone() == (
            claim_id,
        )
    assert saving_store.claim_task("T1") is None
    task.status = "done"
    task.output = "only winner"
    current, committed = claiming_store.finalize_task_claim(task, claim_id)
    assert committed is True
    assert current is not None and current.output == "only winner"


def test_save_milestone_locks_before_reading_and_preserves_decompose_claim(
    tmp_path, monkeypatch
) -> None:
    saving_store = ProjectStore(base_dir=tmp_path)
    claiming_store = ProjectStore(base_dir=tmp_path)
    saving_store.save_project(Project(id="P1", name="x", goal="g"))
    saving_store.save_milestone(
        "P1",
        Milestone(id="M1", name="m", goal="g", status="active"),
    )
    stale = saving_store.get_milestone("M1")
    assert stale is not None
    stale.name = "stale writer"

    read_started = Event()
    release_save = Event()
    original_parser = project_store_module._milestone_from_doc  # noqa: SLF001

    def block_after_read(raw):
        read_started.set()
        assert release_save.wait(timeout=5)
        return original_parser(raw)

    monkeypatch.setattr(project_store_module, "_milestone_from_doc", block_after_read)
    claim_started = Event()
    original_claim = claiming_store.claim_milestone_decomposition

    def mark_claim_started(*args, **kwargs):
        claim_started.set()
        return original_claim(*args, **kwargs)

    monkeypatch.setattr(
        claiming_store,
        "claim_milestone_decomposition",
        mark_claim_started,
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        save_future = pool.submit(saving_store.save_milestone, "P1", stale)
        assert read_started.wait(timeout=5)
        with (
            sqlite3.connect(str(saving_store._db), timeout=0) as probe,  # noqa: SLF001
            pytest.raises(sqlite3.OperationalError, match="locked"),
        ):
            probe.execute("BEGIN IMMEDIATE")
        claim_future = pool.submit(claiming_store.claim_milestone_decomposition, "M1")
        assert claim_started.wait(timeout=5)
        release_save.set()
        save_future.result(timeout=5)
        claimed = claim_future.result(timeout=5)

    assert claimed is not None
    _milestone, claim_id = claimed
    with sqlite3.connect(str(saving_store._db)) as conn:  # noqa: SLF001
        assert conn.execute(
            "SELECT claim_id FROM milestone_claims WHERE milestone_id='M1'"
        ).fetchone() == (claim_id,)
    assert saving_store.claim_milestone_decomposition("M1") is None
    current, committed = claiming_store.finalize_milestone_decomposition(
        "P1",
        "M1",
        [Task(id="T1", milestone_id="M1", type="code", goal="winner")],
        claim_id,
    )
    assert committed is True
    assert current is not None and current.task_ids == ["T1"]


def test_stale_milestone_save_preserves_atomic_message_action_task(tmp_path) -> None:
    action_store = ProjectStore(base_dir=tmp_path)
    stale_store = ProjectStore(base_dir=tmp_path)
    action_store.save_project(
        Project(
            id="P1",
            name="x",
            goal="g",
            milestone_ids=["M1"],
            current_ms="M1",
            status="running",
        )
    )
    action_store.save_milestone(
        "P1",
        Milestone(id="M1", name="m", goal="g", status="in_progress"),
    )
    _project, generation = action_store.bind_thread_versioned("thread-1", "P1")
    stale = stale_store.get_milestone("M1")
    assert stale is not None and stale.task_ids == []
    stale_save_ready = Event()
    release_stale_save = Event()

    def save_stale_snapshot() -> Milestone:
        stale_save_ready.set()
        assert release_stale_save.wait(timeout=5)
        return stale_store.save_milestone("P1", stale)

    with ThreadPoolExecutor(max_workers=1) as pool:
        save_future = pool.submit(save_stale_snapshot)
        assert stale_save_ready.wait(timeout=5)
        event, task, created = action_store.commit_message_action(
            "P1",
            event_id="EV-message-action",
            kind="project.task_created_from_message",
            payload={"source": "message"},
            expected_thread_id="thread-1",
            expected_binding_generation=generation,
            task=Task(id="T-new", milestone_id="M1", type="code", goal="new task"),
        )
        assert created is True and event["id"] == "EV-message-action"
        assert task is not None and task.id == "T-new"
        release_stale_save.set()
        saved = save_future.result(timeout=5)

    assert saved.task_ids == ["T-new"]
    assert stale_store.get_milestone("M1").task_ids == ["T-new"]
    assert stale_store.get_task("T-new") is not None


def test_stale_claim_blocks_restart_until_explicit_recovery(tmp_path, monkeypatch) -> None:
    first = ProjectStore(base_dir=tmp_path)
    first.save_project(
        Project(
            id="P1",
            name="x",
            goal="g",
            milestone_ids=["M1"],
            current_ms="M1",
            status="running",
        )
    )
    first.save_milestone(
        "P1",
        Milestone(
            id="M1",
            name="m",
            goal="g",
            status="in_progress",
            task_ids=["T1"],
        ),
    )
    first.save_task(Task(id="T1", milestone_id="M1", type="code", goal="g"))
    claimed = first.claim_task("T1")
    assert claimed is not None
    old_task, old_claim_id = claimed
    with first._lock, first._conn() as conn:  # noqa: SLF001 - simulate an old crashed worker
        conn.execute("UPDATE task_claims SET claimed_at=0 WHERE task_id='T1'")

    restarted_store = ProjectStore(base_dir=tmp_path)
    execute_calls = 0

    def execute(task: Task, context: dict) -> str:
        nonlocal execute_calls
        execute_calls += 1
        return "recovered delivery"

    restarted = ProjectEngine(
        restarted_store,
        generate_milestones=_stub_milestones,
        decompose_tasks=_stub_decompose,
        execute_task=execute,
        task_claim_timeout_seconds=1,
    )
    first_tick = restarted.tick("P1")

    assert execute_calls == 0
    assert first_tick["project_status"] == "blocked"
    assert "task_claim_orphaned:T1" in first_tick["events"]
    assert restarted_store.get_task("T1").status == "blocked"
    assert restarted_store.get_milestone("M1").status == "blocked"
    audit = restarted_store.events_for_project("P1")
    orphan_audit = next(event for event in audit if event["kind"] == "task.claim_orphaned")
    assert orphan_audit["payload"]["task_id"] == "T1"
    assert orphan_audit["payload"]["recovery_required"] is True
    assert old_claim_id not in str(orphan_audit["payload"])

    old_task.status = "done"
    old_task.output = "late crashed worker"
    current, committed = first.finalize_task_claim(old_task, old_claim_id)
    assert committed is False
    assert current is not None and current.status == "blocked"

    recovered = restarted.recover("P1")
    assert recovered["project_status"] == "running"
    assert "task_recovered:T1" in recovered["events"]
    new_claim_ids: list[str] = []
    original_claim = restarted_store.claim_task

    def capture_claim(*args, **kwargs):
        result = original_claim(*args, **kwargs)
        if result is not None:
            new_claim_ids.append(result[1])
        return result

    monkeypatch.setattr(restarted_store, "claim_task", capture_claim)
    restarted.tick("P1")

    assert execute_calls == 1
    assert new_claim_ids and new_claim_ids[0] != old_claim_id
    assert restarted_store.get_task("T1").status == "done"
    _current, committed = first.finalize_task_claim(old_task, old_claim_id)
    assert committed is False
    assert {event["kind"] for event in restarted_store.events_for_project("P1")} >= {
        "task.claim_orphaned",
        "project.recover",
    }


def test_stale_project_save_cannot_erase_execution_boundary(tmp_path) -> None:
    first = ProjectStore(base_dir=tmp_path)
    second = ProjectStore(base_dir=tmp_path)
    first.save_project(Project(id="P1", name="x", goal="g", status="running"))
    first.bind_thread("thread-1", "P1")
    stale = second.get_project("P1")
    assert stale is not None and stale.started_at == ""

    started = first.start_project_if_bound("P1", "thread-1")
    assert started is not None and started.started_at
    second.save_project(stale)

    current = first.get_project("P1")
    assert current is not None
    assert current.started_at == started.started_at
    assert current.execution_thread_id == "thread-1"
    with pytest.raises(ProjectBindingActiveError):
        first.unbind_thread(
            "thread-1",
            expected_project_id="P1",
            reject_active=True,
        )


def test_stale_writers_cannot_reopen_orphan_block_at_any_level(tmp_path) -> None:
    first = ProjectStore(base_dir=tmp_path)
    second = ProjectStore(base_dir=tmp_path)
    first.save_project(
        Project(
            id="P1",
            name="x",
            goal="g",
            milestone_ids=["M1"],
            current_ms="M1",
            status="running",
        )
    )
    first.save_milestone(
        "P1",
        Milestone(
            id="M1",
            name="m",
            goal="g",
            status="in_progress",
            task_ids=["T1"],
        ),
    )
    first.save_task(Task(id="T1", milestone_id="M1", type="code", goal="g", status="ready"))
    stale_project = second.get_project("P1")
    stale_milestone = second.get_milestone("M1")
    stale_task = second.get_task("T1")
    assert stale_project is not None
    assert stale_milestone is not None
    assert stale_task is not None

    claimed = first.claim_task("T1")
    assert claimed is not None
    with first._lock, first._conn() as conn:  # noqa: SLF001
        conn.execute("UPDATE task_claims SET claimed_at=0 WHERE task_id='T1'")
    assert [task.id for task in first.orphan_stale_task_claims("P1", stale_before=1)] == ["T1"]

    second.save_project(stale_project)
    second.save_milestone("P1", stale_milestone)
    second.save_task(stale_task)

    assert first.get_project("P1").status == "blocked"
    assert first.get_milestone("M1").status == "blocked"
    assert first.get_task("T1").status == "blocked"


def test_terminal_transition_winning_rejects_concurrent_task_add(tmp_path) -> None:
    terminal_store = ProjectStore(base_dir=tmp_path)
    adding_store = ProjectStore(base_dir=tmp_path)
    terminal_store.save_project(
        Project(
            id="P1",
            name="x",
            goal="g",
            milestone_ids=["M1"],
            current_ms="M1",
            status="running",
        )
    )
    terminal_store.save_milestone(
        "P1",
        Milestone(id="M1", name="m", goal="g", status="in_progress"),
    )
    rendezvous = Barrier(2)
    terminal_saved = Event()

    def finish_project() -> None:
        rendezvous.wait(timeout=5)
        try:
            milestone = terminal_store.get_milestone("M1")
            project = terminal_store.get_project("P1")
            assert milestone is not None and project is not None
            milestone.status = "done"
            terminal_store.save_milestone("P1", milestone)
            project.status = "done"
            project.current_ms = None
            terminal_store.save_project(project)
        finally:
            terminal_saved.set()

    def add_after_terminal() -> tuple[Task, bool]:
        rendezvous.wait(timeout=5)
        assert terminal_saved.wait(timeout=5)
        return adding_store.add_task_to_milestone(
            "P1",
            Task(id="T-late", milestone_id="M1", type="code", goal="late"),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        terminal_future = pool.submit(finish_project)
        add_future = pool.submit(add_after_terminal)
        terminal_future.result(timeout=5)
        with pytest.raises(ValueError, match="terminal project"):
            add_future.result(timeout=5)

    assert terminal_store.tasks_for_milestone("M1") == []
    assert terminal_store.get_project("P1").status == "done"
    assert terminal_store.get_milestone("M1").status == "done"


def test_add_winning_gate_interleave_cannot_complete_milestone(tmp_path, monkeypatch) -> None:
    adding_store = ProjectStore(base_dir=tmp_path)
    gating_store = ProjectStore(base_dir=tmp_path)
    adding_store.save_project(
        Project(
            id="P1",
            name="x",
            goal="g",
            milestone_ids=["M1"],
            current_ms="M1",
            status="running",
        )
    )
    adding_store.save_milestone(
        "P1",
        Milestone(
            id="M1",
            name="m",
            goal="g",
            status="in_progress",
            task_ids=["T0"],
        ),
    )
    adding_store.save_task(
        Task(
            id="T0",
            milestone_id="M1",
            type="code",
            goal="done",
            status="done",
            output="done",
        )
    )
    project = gating_store.get_project("P1")
    milestone = gating_store.get_milestone("M1")
    assert project is not None and milestone is not None

    add_read = Event()
    release_add = Event()
    gate_save_started = Event()
    parser_lock = Lock()
    parser_blocked = False
    original_parser = project_store_module._milestone_from_doc  # noqa: SLF001

    def block_first_milestone_parse(raw):
        nonlocal parser_blocked
        with parser_lock:
            should_block = not parser_blocked
            parser_blocked = True
        if should_block:
            add_read.set()
            assert release_add.wait(timeout=5)
        return original_parser(raw)

    monkeypatch.setattr(
        project_store_module,
        "_milestone_from_doc",
        block_first_milestone_parse,
    )
    original_save_milestone = gating_store.save_milestone

    def mark_gate_save(*args, **kwargs):
        gate_save_started.set()
        return original_save_milestone(*args, **kwargs)

    monkeypatch.setattr(gating_store, "save_milestone", mark_gate_save)
    engine = ProjectEngine(
        gating_store,
        generate_milestones=_stub_milestones,
        decompose_tasks=_stub_decompose,
    )
    rendezvous = Barrier(2)

    def add_first() -> tuple[Task, bool]:
        rendezvous.wait(timeout=5)
        return adding_store.add_task_to_milestone(
            "P1",
            Task(id="T-late", milestone_id="M1", type="code", goal="late"),
        )

    def gate_after_add_started() -> list[str]:
        rendezvous.wait(timeout=5)
        assert add_read.wait(timeout=5)
        events: list[str] = []
        engine._gate_milestone(project, milestone, events)  # noqa: SLF001
        return events

    with ThreadPoolExecutor(max_workers=2) as pool:
        add_future = pool.submit(add_first)
        gate_future = pool.submit(gate_after_add_started)
        assert add_read.wait(timeout=5)
        assert gate_save_started.wait(timeout=5)
        release_add.set()
        _task, created = add_future.result(timeout=5)
        events = gate_future.result(timeout=5)

    assert created is True
    assert "milestone_done:M1" not in events
    assert "milestone_stale_done_ignored:M1" in events
    assert adding_store.get_project("P1").current_ms == "M1"
    assert adding_store.get_milestone("M1").status == "in_progress"
    assert adding_store.get_task("T-late").status == "pending"


def test_concurrent_task_adds_preserve_every_milestone_task_id(tmp_path, monkeypatch) -> None:
    first = ProjectStore(base_dir=tmp_path)
    second = ProjectStore(base_dir=tmp_path)
    first.save_project(Project(id="P1", name="x", goal="g", status="running"))
    first.save_milestone(
        "P1",
        Milestone(id="M1", name="m", goal="g", status="in_progress"),
    )
    parse_started = Event()
    release_first = Event()
    parser_lock = Lock()
    parser_blocked = False
    original_parser = project_store_module._milestone_from_doc  # noqa: SLF001

    def block_first_milestone_parse(raw):
        nonlocal parser_blocked
        with parser_lock:
            should_block = not parser_blocked
            parser_blocked = True
        if should_block:
            parse_started.set()
            assert release_first.wait(timeout=5)
        return original_parser(raw)

    monkeypatch.setattr(
        project_store_module,
        "_milestone_from_doc",
        block_first_milestone_parse,
    )
    rendezvous = Barrier(2)

    def add(store: ProjectStore, task_id: str) -> tuple[Task, bool]:
        rendezvous.wait(timeout=5)
        return store.add_task_to_milestone(
            "P1",
            Task(id=task_id, milestone_id="M1", type="code", goal=task_id),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(add, first, "T1")
        second_future = pool.submit(add, second, "T2")
        assert parse_started.wait(timeout=5)
        try:
            with (
                sqlite3.connect(str(first._db), timeout=0) as probe,  # noqa: SLF001
                pytest.raises(sqlite3.OperationalError, match="locked"),
            ):
                probe.execute("BEGIN IMMEDIATE")
        finally:
            release_first.set()
        results = [first_future.result(timeout=5), second_future.result(timeout=5)]

    assert all(created for _task, created in results)
    assert {task.id for task in first.tasks_for_milestone("M1")} == {"T1", "T2"}
    milestone = first.get_milestone("M1")
    assert milestone is not None and set(milestone.task_ids) == {"T1", "T2"}


@pytest.mark.parametrize("status", ["blocked", "done", "failed"])
def test_tick_never_executes_tasks_for_non_runnable_project(tmp_path, status) -> None:
    store = ProjectStore(base_dir=tmp_path)
    store.save_project(
        Project(
            id="P1",
            name="x",
            goal="g",
            milestone_ids=["M1"],
            current_ms="M1",
            status=status,
        )
    )
    store.save_milestone(
        "P1",
        Milestone(
            id="M1",
            name="m",
            goal="g",
            status="in_progress",
            task_ids=["T1"],
        ),
    )
    store.save_task(Task(id="T1", milestone_id="M1", type="code", goal="g", status="ready"))
    execute_calls = 0

    def execute(task: Task, context: dict) -> str:
        nonlocal execute_calls
        execute_calls += 1
        return "must not run"

    engine = ProjectEngine(
        store,
        generate_milestones=_stub_milestones,
        decompose_tasks=_stub_decompose,
        execute_task=execute,
    )
    tick = engine.tick("P1")

    assert execute_calls == 0
    assert tick == {
        "events": [f"project_not_runnable:{status}"],
        "project_status": status,
        "current_ms": "M1",
    }
    assert store.get_task("T1").status == "ready"


def test_store_project_terminal_status_is_immutable_by_default(tmp_path) -> None:
    s = ProjectStore(base_dir=tmp_path)
    original = Project(id="P1", name="x", goal="g", status="done")
    s.save_project(original)

    stale = Project(id="P1", name="x", goal="g", status="blocked", current_ms="MS1")
    returned = s.save_project(stale)

    assert returned.status == "done"
    assert returned.current_ms is None
    assert s.get_project("P1").status == "done"


def test_store_project_terminal_status_can_be_reopened_explicitly(tmp_path) -> None:
    s = ProjectStore(base_dir=tmp_path)
    original = Project(id="P1", name="x", goal="g", status="done")
    s.save_project(original)

    reopened = Project(id="P1", name="x", goal="g", status="running", current_ms="MS1")
    returned = s.save_project(reopened, allow_terminal_rewrite=True)

    assert returned.status == "running"
    assert returned.current_ms == "MS1"
    assert s.get_project("P1").status == "running"


def test_store_milestone_terminal_status_is_immutable_by_default(tmp_path) -> None:
    s = ProjectStore(base_dir=tmp_path)
    s.save_project(Project(id="P1", name="p", goal="g"))
    original = Milestone(id="MS1", name="m", goal="g", status="done")
    s.save_milestone("P1", original)

    stale = Milestone(id="MS1", name="m", goal="g", status="blocked")
    returned = s.save_milestone("P1", stale)

    assert returned.status == "done"
    assert s.get_milestone("MS1").status == "done"


def test_store_milestone_terminal_status_can_be_reopened_explicitly(tmp_path) -> None:
    s = ProjectStore(base_dir=tmp_path)
    s.save_project(Project(id="P1", name="p", goal="g"))
    original = Milestone(id="MS1", name="m", goal="g", status="done")
    s.save_milestone("P1", original)

    reopened = Milestone(id="MS1", name="m", goal="g", status="in_progress")
    returned = s.save_milestone("P1", reopened, allow_terminal_rewrite=True)

    assert returned.status == "in_progress"
    assert s.get_milestone("MS1").status == "in_progress"


# ── engine ───────────────────────────────────────────────────────────────────
def _stub_milestones(goal: str) -> list[Milestone]:
    return [
        Milestone(id="MS1", name="research", goal="scope it"),
        Milestone(id="MS2", name="build", goal="build it", dependencies=["MS1"]),
        Milestone(id="MS3", name="verify", goal="verify it", dependencies=["MS2"]),
    ]


def _stub_decompose(ms: Milestone) -> list[Task]:
    # two tasks with a dependency, to exercise the DAG within a milestone
    return [
        Task(id=f"{ms.id}-T1", milestone_id=ms.id, type="research", goal=f"{ms.goal} part1"),
        Task(
            id=f"{ms.id}-T2",
            milestone_id=ms.id,
            type="code",
            goal=f"{ms.goal} part2",
            depends_on=[f"{ms.id}-T1"],
        ),
    ]


def _engine(tmp_path, **hooks) -> ProjectEngine:
    generate_milestones = hooks.pop("generate_milestones", _stub_milestones)
    decompose_tasks = hooks.pop("decompose_tasks", _stub_decompose)
    return ProjectEngine(
        ProjectStore(base_dir=tmp_path),
        generate_milestones=generate_milestones,
        decompose_tasks=decompose_tasks,
        **hooks,
    )


def test_plan_generates_milestones(tmp_path) -> None:
    eng = _engine(tmp_path)
    p = eng.plan("sleep sys", "make a smart sleep system")
    assert p.status == "running"
    assert [m.id for m in eng.store.milestones_for(p.id)] == ["MS1", "MS2", "MS3"]


def test_plan_falls_back_when_milestone_generation_fails(tmp_path) -> None:
    def broken_generate(goal: str) -> list[Milestone]:
        raise RuntimeError(f"planner unavailable for {goal}")

    eng = _engine(tmp_path, generate_milestones=broken_generate)

    p = eng.plan("fallback", "ship despite planner outage")

    assert p.status == "running"
    assert [m.id for m in eng.store.milestones_for(p.id)] == ["MS1", "MS2", "MS3"]


def test_consecutive_plans_resolve_global_milestone_ids_and_dependencies(tmp_path) -> None:
    eng = _engine(tmp_path)

    first = eng.plan("first", "ship the first project")
    second = eng.plan("second", "ship the second project")

    assert first.milestone_ids == ["MS1", "MS2", "MS3"]
    assert second.milestone_ids == [
        f"{second.id}:MS1",
        f"{second.id}:MS2",
        f"{second.id}:MS3",
    ]
    second_milestones = {item.id: item for item in eng.store.milestones_for(second.id)}
    assert second_milestones[second.milestone_ids[1]].dependencies == [second.milestone_ids[0]]
    assert second_milestones[second.milestone_ids[2]].dependencies == [second.milestone_ids[1]]


@pytest.mark.parametrize(
    "trigger_sql",
    [
        """
        CREATE TRIGGER fail_project_plan_milestone
        BEFORE INSERT ON milestones WHEN NEW.id = 'MS2'
        BEGIN SELECT RAISE(ABORT, 'milestone write failed'); END;
        """,
        """
        CREATE TRIGGER fail_project_plan_event
        BEFORE INSERT ON project_events WHEN NEW.kind = 'project.planned'
        BEGIN SELECT RAISE(ABORT, 'event write failed'); END;
        """,
    ],
)
def test_plan_rolls_back_every_row_when_persistence_fails(tmp_path, trigger_sql) -> None:
    store = ProjectStore(base_dir=tmp_path)
    with sqlite3.connect(str(tmp_path / "projectos.db")) as conn:
        conn.executescript(trigger_sql)
    eng = ProjectEngine(
        store,
        generate_milestones=stub_generate_milestones,
        decompose_tasks=_stub_decompose,
    )

    with pytest.raises(sqlite3.DatabaseError):
        eng.plan("atomic", "leave no partial project")

    assert store.list_projects() == []
    with sqlite3.connect(str(tmp_path / "projectos.db")) as conn:
        assert conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM milestones").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM project_events").fetchone()[0] == 0


def test_full_run_drives_project_to_done(tmp_path) -> None:
    eng = _engine(tmp_path)
    p = eng.plan("sleep sys", "make a smart sleep system")
    result = eng.run(p.id, max_ticks=50)
    assert result["final_status"] == "done"
    # every milestone reached done, in dependency order
    mss = {m.id: m.status for m in eng.store.milestones_for(p.id)}
    assert mss == {"MS1": "done", "MS2": "done", "MS3": "done"}
    # all tasks done
    for ms_id in ("MS1", "MS2", "MS3"):
        assert all(t.status == "done" for t in eng.store.tasks_for_milestone(ms_id))


def test_task_execution_context_carries_project_identity_and_thread(tmp_path) -> None:
    captured: list[dict] = []
    workspace = tmp_path / "managed-workspace"

    def one_milestone(goal: str) -> list[Milestone]:
        return [Milestone(id="MS-context", name="build", goal=goal)]

    def one_task(ms: Milestone) -> list[Task]:
        return [
            Task(
                id="T-context",
                milestone_id=ms.id,
                type="code",
                goal="ship it",
            )
        ]

    def capture_context(task: Task, context: dict) -> str:
        captured.append(context)
        return f"done:{task.id}"

    engine = ProjectEngine(
        ProjectStore(base_dir=tmp_path),
        generate_milestones=one_milestone,
        decompose_tasks=one_task,
        execute_task=capture_context,
        owner_id="alice",
        tenant_id="acme",
        resolve_thread_context=lambda thread_id: {
            "workspace_path": str(workspace),
            "runtime_session_metadata": {
                "workspace_path": str(workspace),
                "resolved_thread_id": thread_id,
            },
        },
    )
    project = engine.plan("context", "preserve dispatch identity")
    engine.store.bind_thread("thread-context", project.id)

    result = engine.run(project.id, max_ticks=10)

    assert result["final_status"] == "done"
    assert len(captured) == 1
    assert captured[0]["project_id"] == project.id
    assert captured[0]["owner_id"] == "alice"
    assert captured[0]["tenant_id"] == "acme"
    assert captured[0]["thread_id"] == "thread-context"
    assert captured[0]["workspace_path"] == str(workspace)
    assert captured[0]["runtime_session_metadata"] == {
        "workspace_path": str(workspace),
        "resolved_thread_id": "thread-context",
    }


def test_project_process_timeline_persists_plan_run_and_state(tmp_path) -> None:
    eng = _engine(tmp_path)
    project = eng.plan("timeline", "ship a durable employee loop")
    eng.run(project.id, max_ticks=20)

    timeline = project_process_timeline(eng.store, project.id)

    assert timeline is not None
    assert timeline["schema"] == "echo.projectos.process_timeline.v1"
    assert timeline["project_id"] == project.id
    assert timeline["overview"]["status"] == "done"
    assert timeline["overview"]["milestone_count"] == 3
    assert timeline["overview"]["task_count"] == 6
    assert timeline["overview"]["done_task_count"] == 6
    assert timeline["safety"]["raw_task_outputs_included"] is False
    assert timeline["safety"]["process_events_persisted"] is True
    kinds = {node["kind"] for node in timeline["timeline"]}
    lanes = {node["lane"] for node in timeline["timeline"]}
    assert {"project.planned", "project.run", "milestone_state", "task_state"} <= kinds
    assert {"project", "milestone", "task"} <= lanes
    run_node = next(node for node in timeline["timeline"] if node["kind"] == "project.run")
    assert run_node["data"]["history"]["omitted"] is True


def test_run_tick_budget_is_bounded_even_for_internal_callers(tmp_path, monkeypatch) -> None:
    eng = _engine(tmp_path)
    project = eng.plan("long", "keep running")
    calls = {"n": 0}

    def fake_tick(project_id: str) -> dict:
        calls["n"] += 1
        return {"events": [], "project_status": "running", "current_ms": "MS1"}

    monkeypatch.setattr(eng, "tick", fake_tick)

    result = eng.run(project.id, max_ticks=10_000)

    assert result["ticks"] == HARD_MAX_RUN_TICKS
    assert calls["n"] == HARD_MAX_RUN_TICKS


def test_normalize_run_ticks_has_safe_floor_and_default() -> None:
    assert normalize_run_ticks(0) == 1
    assert normalize_run_ticks(-10) == 1
    assert normalize_run_ticks(None) == 50


def test_dependent_milestone_waits(tmp_path) -> None:
    # MS2 must not start before MS1 is done — one tick only activates MS1.
    eng = _engine(tmp_path)
    p = eng.plan("x", "g")
    eng.tick(p.id)  # activates MS1 + creates its tasks
    assert eng.store.get_milestone("MS1").status == "in_progress"
    assert eng.store.get_milestone("MS2").status == "pending"  # still waiting on MS1
    assert eng.store.tasks_for_milestone("MS2") == []


def test_qa_rejection_retries_then_passes(tmp_path) -> None:
    calls = {"n": 0}

    def flaky_qa(task: Task, ms: Milestone) -> dict:
        # reject the very first QA, approve everything after
        calls["n"] += 1
        return {"approved": calls["n"] > 1, "reason": "flaky"}

    eng = _engine(tmp_path, qa_task=flaky_qa)
    p = eng.plan("x", "g")
    result = eng.run(p.id, max_ticks=50)
    assert result["final_status"] == "done"  # retry recovered the rejected task


def test_task_execution_error_retries_then_passes(tmp_path) -> None:
    calls: dict[str, int] = {}

    def flaky_execute(task: Task, context: dict) -> str:
        calls[task.id] = calls.get(task.id, 0) + 1
        if task.id == "MS1-T1" and calls[task.id] == 1:
            raise RuntimeError("transient tool failure")
        return f"ok:{task.id}"

    eng = _engine(tmp_path, execute_task=flaky_execute)
    p = eng.plan("x", "g")
    result = eng.run(p.id, max_ticks=50)

    assert result["final_status"] == "done"
    assert calls["MS1-T1"] == 2
    assert eng.store.get_task("MS1-T1").attempts == 2
    events = [event for tick in result["history"] for event in tick["events"]]
    assert "task_error_retry:MS1-T1" in events


def test_task_execution_error_blocks_project_after_retry_cap(tmp_path) -> None:
    def failing_execute(task: Task, context: dict) -> str:
        if task.id == "MS1-T1":
            raise RuntimeError("persistent tool failure")
        return f"ok:{task.id}"

    eng = _engine(tmp_path, execute_task=failing_execute)
    p = eng.plan("x", "g")
    result = eng.run(p.id, max_ticks=20)

    assert result["final_status"] == "blocked"
    assert eng.store.get_project(p.id).status == "blocked"
    assert eng.store.get_project(p.id).current_ms == "MS1"
    assert eng.store.get_milestone("MS1").status == "blocked"
    assert eng.store.get_task("MS1-T1").status == "failed"
    assert eng.store.get_task("MS1-T1").attempts == 2
    events = [event for tick in result["history"] for event in tick["events"]]
    assert "task_error_retry:MS1-T1" in events
    assert "task_failed:MS1-T1" in events
    assert "project_blocked:task_failed" in events


def test_two_engines_atomically_claim_one_ready_task(tmp_path, monkeypatch) -> None:
    first_store = ProjectStore(base_dir=tmp_path)
    second_store = ProjectStore(base_dir=tmp_path)
    first_store.save_project(
        Project(
            id="P1",
            name="x",
            goal="g",
            milestone_ids=["M1"],
            current_ms="M1",
            status="running",
        )
    )
    first_store.save_milestone(
        "P1",
        Milestone(
            id="M1",
            name="m",
            goal="g",
            status="in_progress",
            task_ids=["T1"],
        ),
    )
    first_store.save_task(Task(id="T1", milestone_id="M1", type="code", goal="g"))

    claim_barrier = Barrier(2)
    for store in (first_store, second_store):
        original_claim = store.claim_task

        def synchronized_claim(*args, _claim=original_claim, **kwargs):
            claim_barrier.wait(timeout=5)
            return _claim(*args, **kwargs)

        monkeypatch.setattr(store, "claim_task", synchronized_claim)

    execute_calls = 0
    execute_lock = Lock()

    def execute(task: Task, context: dict) -> str:
        nonlocal execute_calls
        with execute_lock:
            execute_calls += 1
        return "one delivery"

    engines = [
        ProjectEngine(
            store,
            generate_milestones=_stub_milestones,
            decompose_tasks=_stub_decompose,
            execute_task=execute,
        )
        for store in (first_store, second_store)
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        ticks = list(pool.map(lambda engine: engine.tick("P1"), engines))

    assert execute_calls == 1
    stored = first_store.get_task("T1")
    assert stored is not None
    assert stored.status == "done"
    assert stored.output == "one delivery"
    assert stored.attempts == 1
    assert sum("task_stale_claim_ignored:T1" in tick["events"] for tick in ticks) == 1


def test_two_engines_publish_only_one_canonical_decomposition(tmp_path, monkeypatch) -> None:
    first_store = ProjectStore(base_dir=tmp_path)
    second_store = ProjectStore(base_dir=tmp_path)
    first_store.save_project(
        Project(
            id="P1",
            name="x",
            goal="g",
            milestone_ids=["M1"],
            current_ms="M1",
            status="running",
        )
    )
    first_store.save_milestone(
        "P1",
        Milestone(id="M1", name="m", goal="g", status="active"),
    )

    claim_barrier = Barrier(2)
    for store in (first_store, second_store):
        original_claim = store.claim_milestone_decomposition

        def synchronized_claim(*args, _claim=original_claim, **kwargs):
            claim_barrier.wait(timeout=5)
            return _claim(*args, **kwargs)

        monkeypatch.setattr(store, "claim_milestone_decomposition", synchronized_claim)

    decompose_calls = 0
    execute_calls = 0
    calls_lock = Lock()

    def decompose(ms: Milestone) -> list[Task]:
        nonlocal decompose_calls
        with calls_lock:
            decompose_calls += 1
            suffix = "A" if decompose_calls == 1 else "B"
        return [Task(id=f"T-{suffix}", milestone_id=ms.id, type="code", goal="g")]

    def execute(task: Task, context: dict) -> str:
        nonlocal execute_calls
        with calls_lock:
            execute_calls += 1
        return "canonical delivery"

    engines = [
        ProjectEngine(
            store,
            generate_milestones=_stub_milestones,
            decompose_tasks=decompose,
            execute_task=execute,
        )
        for store in (first_store, second_store)
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        ticks = list(pool.map(lambda engine: engine.tick("P1"), engines))

    assert decompose_calls == 1
    assert execute_calls == 1
    tasks = first_store.tasks_for_milestone("M1")
    assert [(task.id, task.status, task.output) for task in tasks] == [
        ("T-A", "done", "canonical delivery")
    ]
    milestone = first_store.get_milestone("M1")
    assert milestone is not None and milestone.task_ids == ["T-A"]
    assert sum("milestone_decompose_claim_ignored:M1" in tick["events"] for tick in ticks) == 1


def test_task_claim_rechecks_blocked_parent_after_stale_tick_snapshot(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine_store = ProjectStore(base_dir=tmp_path)
    blocker = ProjectStore(base_dir=tmp_path)
    engine_store.save_project(
        Project(
            id="P1",
            name="x",
            goal="g",
            milestone_ids=["M1"],
            current_ms="M1",
            status="running",
        )
    )
    engine_store.save_milestone(
        "P1",
        Milestone(id="M1", name="m", goal="g", status="in_progress", task_ids=["T1"]),
    )
    engine_store.save_task(Task(id="T1", milestone_id="M1", type="code", goal="g"))
    claim_ready = Barrier(2)
    parent_blocked = Event()
    real_claim = engine_store.claim_task

    def claim_after_parent_block(*args, **kwargs):
        claim_ready.wait(timeout=5)
        assert parent_blocked.wait(timeout=5)
        return real_claim(*args, **kwargs)

    monkeypatch.setattr(engine_store, "claim_task", claim_after_parent_block)
    execute_calls = 0

    def execute(_task: Task, _context: dict) -> str:
        nonlocal execute_calls
        execute_calls += 1
        return "must not execute"

    engine = ProjectEngine(
        engine_store,
        generate_milestones=_stub_milestones,
        decompose_tasks=_stub_decompose,
        execute_task=execute,
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        tick_future = pool.submit(engine.tick, "P1")
        claim_ready.wait(timeout=5)
        blocked = blocker.get_project("P1")
        assert blocked is not None
        blocked.status = "blocked"
        blocker.save_project(blocked)
        parent_blocked.set()
        tick = tick_future.result(timeout=5)

    assert execute_calls == 0
    assert "task_stale_claim_ignored:T1" in tick["events"]
    assert engine_store.get_task("T1").status == "pending"
    assert engine_store.get_project("P1").status == "blocked"


def test_decomposition_claim_rechecks_blocked_parent_after_stale_tick_snapshot(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine_store = ProjectStore(base_dir=tmp_path)
    blocker = ProjectStore(base_dir=tmp_path)
    engine_store.save_project(
        Project(
            id="P1",
            name="x",
            goal="g",
            milestone_ids=["M1"],
            current_ms="M1",
            status="running",
        )
    )
    engine_store.save_milestone(
        "P1",
        Milestone(id="M1", name="m", goal="g", status="active"),
    )
    claim_ready = Barrier(2)
    parent_blocked = Event()
    real_claim = engine_store.claim_milestone_decomposition

    def claim_after_parent_block(*args, **kwargs):
        claim_ready.wait(timeout=5)
        assert parent_blocked.wait(timeout=5)
        return real_claim(*args, **kwargs)

    monkeypatch.setattr(engine_store, "claim_milestone_decomposition", claim_after_parent_block)
    decompose_calls = 0

    def decompose(ms: Milestone) -> list[Task]:
        nonlocal decompose_calls
        decompose_calls += 1
        return [Task(id="T1", milestone_id=ms.id, type="code", goal="g")]

    engine = ProjectEngine(
        engine_store,
        generate_milestones=_stub_milestones,
        decompose_tasks=decompose,
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        tick_future = pool.submit(engine.tick, "P1")
        claim_ready.wait(timeout=5)
        blocked = blocker.get_project("P1")
        assert blocked is not None
        blocked.status = "blocked"
        blocker.save_project(blocked)
        parent_blocked.set()
        tick = tick_future.result(timeout=5)

    assert decompose_calls == 0
    assert "milestone_decompose_claim_ignored:M1" in tick["events"]
    assert engine_store.tasks_for_milestone("M1") == []
    assert engine_store.get_project("P1").status == "blocked"


def test_stale_decomposition_claim_requires_explicit_recovery(tmp_path, monkeypatch) -> None:
    crashed_store = ProjectStore(base_dir=tmp_path)
    crashed_store.save_project(
        Project(
            id="P1",
            name="x",
            goal="g",
            milestone_ids=["M1"],
            current_ms="M1",
            status="running",
        )
    )
    crashed_store.save_milestone(
        "P1",
        Milestone(id="M1", name="m", goal="g", status="active"),
    )
    claimed = crashed_store.claim_milestone_decomposition("M1")
    assert claimed is not None
    _milestone, old_claim_id = claimed
    with crashed_store._lock, crashed_store._conn() as conn:  # noqa: SLF001
        conn.execute("UPDATE milestone_claims SET claimed_at=0 WHERE milestone_id='M1'")

    restarted_store = ProjectStore(base_dir=tmp_path)
    decompose_calls = 0

    def decompose(ms: Milestone) -> list[Task]:
        nonlocal decompose_calls
        decompose_calls += 1
        return [Task(id="T1", milestone_id=ms.id, type="code", goal="g")]

    restarted = ProjectEngine(
        restarted_store,
        generate_milestones=_stub_milestones,
        decompose_tasks=decompose,
        task_claim_timeout_seconds=1,
    )
    first_tick = restarted.tick("P1")

    assert decompose_calls == 0
    assert first_tick["project_status"] == "blocked"
    assert "milestone_decomposition_claim_orphaned:M1" in first_tick["events"]
    assert restarted_store.get_milestone("M1").status == "blocked"
    audit = restarted_store.events_for_project("P1")
    assert any(event["kind"] == "milestone.decomposition_claim_orphaned" for event in audit)

    current, committed = crashed_store.finalize_milestone_decomposition(
        "P1",
        "M1",
        [Task(id="T-old", milestone_id="M1", type="code", goal="late")],
        old_claim_id,
    )
    assert committed is False
    assert current is not None and current.status == "blocked"

    recovered = restarted.recover("P1")
    assert recovered["project_status"] == "running"
    new_claim_ids: list[str] = []
    original_claim = restarted_store.claim_milestone_decomposition

    def capture_claim(*args, **kwargs):
        result = original_claim(*args, **kwargs)
        if result is not None:
            new_claim_ids.append(result[1])
        return result

    monkeypatch.setattr(restarted_store, "claim_milestone_decomposition", capture_claim)
    restarted.tick("P1")

    assert decompose_calls == 1
    assert new_claim_ids and new_claim_ids[0] != old_claim_id
    assert restarted_store.get_task("T1").status == "done"
    _current, committed = crashed_store.finalize_milestone_decomposition(
        "P1",
        "M1",
        [Task(id="T-old", milestone_id="M1", type="code", goal="late")],
        old_claim_id,
    )
    assert committed is False


def _bound_ready_project(path) -> ProjectStore:
    store = ProjectStore(base_dir=path)
    store.save_project(
        Project(
            id="P-bound",
            name="bound",
            goal="g",
            milestone_ids=["M-bound"],
            current_ms="M-bound",
            status="running",
        )
    )
    store.save_milestone(
        "P-bound",
        Milestone(
            id="M-bound",
            name="m",
            goal="g",
            status="in_progress",
            task_ids=["T-bound"],
        ),
    )
    store.save_task(Task(id="T-bound", milestone_id="M-bound", type="code", goal="g"))
    store.bind_thread("thread-bound", "P-bound")
    return store


def test_detach_winning_execution_boundary_prevents_side_effects(tmp_path, monkeypatch) -> None:
    detaching_store = _bound_ready_project(tmp_path)
    engine_store = ProjectStore(base_dir=tmp_path)
    rendezvous = Barrier(2)
    detached = Event()
    original_start = engine_store.start_project_if_bound

    def start_after_detach(*args, **kwargs):
        rendezvous.wait(timeout=5)
        assert detached.wait(timeout=5)
        return original_start(*args, **kwargs)

    monkeypatch.setattr(engine_store, "start_project_if_bound", start_after_detach)
    execute_calls = 0

    def execute(task: Task, context: dict) -> str:
        nonlocal execute_calls
        execute_calls += 1
        return "must not run"

    engine = ProjectEngine(
        engine_store,
        generate_milestones=_stub_milestones,
        decompose_tasks=_stub_decompose,
        execute_task=execute,
        required_execution_thread_id="thread-bound",
    )

    def detach_first():
        rendezvous.wait(timeout=5)
        try:
            return detaching_store.unbind_thread(
                "thread-bound",
                expected_project_id="P-bound",
                reject_active=True,
            )
        finally:
            detached.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        tick_future = pool.submit(engine.tick, "P-bound")
        detach_future = pool.submit(detach_first)
        detached_project = detach_future.result(timeout=5)
        tick = tick_future.result(timeout=5)

    assert detached_project is not None
    assert execute_calls == 0
    assert tick["project_status"] == "blocked"
    assert "project_execution_binding_lost:thread-bound" in tick["events"]
    assert detaching_store.project_for_thread("thread-bound") is None


def test_execution_boundary_winning_rejects_nonforce_detach(tmp_path, monkeypatch) -> None:
    detaching_store = _bound_ready_project(tmp_path)
    engine_store = ProjectStore(base_dir=tmp_path)
    rendezvous = Barrier(2)
    started = Event()
    original_start = engine_store.start_project_if_bound

    def start_before_detach(*args, **kwargs):
        rendezvous.wait(timeout=5)
        result = original_start(*args, **kwargs)
        started.set()
        return result

    monkeypatch.setattr(engine_store, "start_project_if_bound", start_before_detach)
    execute_calls = 0

    def execute(task: Task, context: dict) -> str:
        nonlocal execute_calls
        execute_calls += 1
        return "one delivery"

    engine = ProjectEngine(
        engine_store,
        generate_milestones=_stub_milestones,
        decompose_tasks=_stub_decompose,
        execute_task=execute,
        required_execution_thread_id="thread-bound",
    )

    def detach_after_start():
        rendezvous.wait(timeout=5)
        assert started.wait(timeout=5)
        return detaching_store.unbind_thread(
            "thread-bound",
            expected_project_id="P-bound",
            reject_active=True,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        tick_future = pool.submit(engine.tick, "P-bound")
        detach_future = pool.submit(detach_after_start)
        with pytest.raises(ProjectBindingActiveError):
            detach_future.result(timeout=5)
        tick = tick_future.result(timeout=5)

    assert execute_calls == 1
    assert "task_done:T-bound" in tick["events"]
    assert detaching_store.project_for_thread("thread-bound") is not None
    assert detaching_store.get_project("P-bound").started_at


def test_delete_if_unbound_preserves_a_binding_that_wins_the_gap(tmp_path) -> None:
    binding_store = ProjectStore(base_dir=tmp_path)
    deleting_store = ProjectStore(base_dir=tmp_path)
    binding_store.save_project(Project(id="P-delete", name="delete", goal="g"))
    assert deleting_store.thread_for_project("P-delete") is None
    rendezvous = Barrier(2)
    bound = Event()

    def bind_in_gap():
        rendezvous.wait(timeout=5)
        try:
            return binding_store.bind_thread_if_absent("thread-new", "P-delete")
        finally:
            bound.set()

    def delete_after_gap_check():
        rendezvous.wait(timeout=5)
        assert bound.wait(timeout=5)
        return deleting_store.delete_project_if_unbound("P-delete")

    with ThreadPoolExecutor(max_workers=2) as pool:
        bind_future = pool.submit(bind_in_gap)
        delete_future = pool.submit(delete_after_gap_check)
        canonical, inserted = bind_future.result(timeout=5)
        deleted = delete_future.result(timeout=5)

    assert inserted is True
    assert canonical.id == "P-delete"
    assert deleted is False
    assert deleting_store.get_project("P-delete") is not None
    assert deleting_store.project_for_thread("thread-new").id == "P-delete"


def test_stale_running_claim_does_not_execute_terminal_task(tmp_path, monkeypatch) -> None:
    eng = _engine(tmp_path)
    p = eng.plan("x", "g")
    eng.tick(p.id)
    calls = {"execute": 0}
    original_claim_task = eng.store.claim_task

    def stale_claim(task_id: str, **kwargs):
        if task_id == "MS1-T2":
            return None
        return original_claim_task(task_id, **kwargs)

    def execute(task: Task, context: dict) -> str:
        calls["execute"] += 1
        return "should not run"

    monkeypatch.setattr(eng.store, "claim_task", stale_claim)
    eng._execute = execute

    tick = eng.tick(p.id)

    assert "task_stale_claim_ignored:MS1-T2" in tick["events"]
    assert calls["execute"] == 0


def test_recover_reopens_blocked_project_and_reruns_task(tmp_path) -> None:
    fail = {"enabled": True}

    def maybe_failing_execute(task: Task, context: dict) -> str:
        if task.id == "MS1-T1" and fail["enabled"]:
            raise RuntimeError("persistent tool failure")
        return f"ok:{task.id}"

    eng = _engine(tmp_path, execute_task=maybe_failing_execute)
    p = eng.plan("x", "g")
    blocked = eng.run(p.id, max_ticks=20)
    assert blocked["final_status"] == "blocked"

    fail["enabled"] = False
    recovered = eng.recover(p.id)
    assert recovered["project_status"] == "running"
    assert "project_recovered" in recovered["events"]
    assert "task_recovered:MS1-T1" in recovered["events"]
    audit = eng.store.events_for_project(p.id)
    assert audit[-1]["kind"] == "project.recover"
    assert audit[-1]["payload"]["events"] == recovered["events"]
    assert eng.store.get_task("MS1-T1").status == "pending"
    assert eng.store.get_task("MS1-T1").attempts == 0

    done = eng.run(p.id, max_ticks=20)
    assert done["final_status"] == "done"
    assert eng.store.get_project(p.id).status == "done"
    assert eng.store.get_task("MS1-T1").status == "done"


def test_recover_explicit_task_resets_downstream_dependants(tmp_path) -> None:
    eng = _engine(tmp_path)
    p = eng.plan("x", "g")
    eng.tick(p.id)
    t1 = eng.store.get_task("MS1-T1")
    t2 = eng.store.get_task("MS1-T2")
    t1.status = "failed"
    t1.output = "bad upstream"
    t1.attempts = 2
    t2.status = "done"
    t2.output = "stale downstream"
    eng.store.save_task(t1, allow_terminal_rewrite=True)
    eng.store.save_task(t2, allow_terminal_rewrite=True)
    ms = eng.store.get_milestone("MS1")
    ms.status = "blocked"
    eng.store.save_milestone(p.id, ms)
    p.status = "blocked"
    p.current_ms = "MS1"
    eng.store.save_project(p)

    recovered = eng.recover(p.id, task_ids=["MS1-T1"])

    assert recovered["project_status"] == "running"
    assert "task_recovered:MS1-T1" in recovered["events"]
    assert "task_recovered:MS1-T2" in recovered["events"]
    assert eng.store.get_task("MS1-T1").status == "pending"
    assert eng.store.get_task("MS1-T2").status == "pending"
    assert eng.store.get_task("MS1-T2").output is None


def test_intervene_reassign_resets_blocked_task_and_reopens_project(tmp_path) -> None:
    eng = _engine(tmp_path)
    p = eng.plan("x", "g")
    eng.tick(p.id)
    task = eng.store.get_task("MS1-T1")
    task.status = "failed"
    task.output = "bad"
    task.assigned_agent = "old-agent"
    task.attempts = 2
    eng.store.save_task(task)
    ms = eng.store.get_milestone("MS1")
    ms.status = "blocked"
    eng.store.save_milestone(p.id, ms)
    p.status = "blocked"
    p.current_ms = "MS1"
    eng.store.save_project(p)

    result = eng.intervene_task(
        p.id,
        "MS1-T1",
        action="reassign",
        assigned_agent="new-agent",
    )

    updated = eng.store.get_task("MS1-T1")
    assert result["project_status"] == "running"
    assert "task_reassigned:MS1-T1" in result["events"]
    assert "project_recovered" in result["events"]
    audit = eng.store.events_for_project(p.id)
    assert audit[-1]["kind"] == "task.intervention"
    assert audit[-1]["payload"]["action"] == "reassign"
    assert audit[-1]["payload"]["assigned_agent"] == "new-agent"
    assert updated.status == "pending"
    assert updated.assigned_agent == "new-agent"
    assert updated.attempts == 0
    assert updated.output is None

    done = eng.run(p.id, max_ticks=20)
    assert done["final_status"] == "done"
    assert eng.store.get_task("MS1-T1").assigned_agent == "new-agent"


def test_intervene_complete_and_skip_allow_milestone_to_finish(tmp_path) -> None:
    eng = _engine(tmp_path)
    p = eng.plan("x", "g")
    eng.tick(p.id)

    completed = eng.intervene_task(
        p.id,
        "MS1-T1",
        action="complete",
        output="operator accepted research",
        reason="manual review passed",
    )
    assert "task_completed_by_operator:MS1-T1" in completed["events"]

    skipped = eng.intervene_task(
        p.id,
        "MS1-T2",
        action="skip",
        reason="implementation not needed",
    )
    assert "task_skipped:MS1-T2" in skipped["events"]

    tick = eng.tick(p.id)
    assert "milestone_done:MS1" in tick["events"]
    assert eng.store.get_milestone("MS1").status == "done"
    assert eng.store.get_task("MS1-T2").output["skipped"] is True


def test_intervene_reset_cascades_to_downstream_dependants(tmp_path) -> None:
    eng = _engine(tmp_path)
    p = eng.plan("x", "g")
    eng.tick(p.id)
    t1 = eng.store.get_task("MS1-T1")
    t2 = eng.store.get_task("MS1-T2")
    t1.status = "done"
    t1.output = "old upstream"
    t2.status = "done"
    t2.output = "old downstream"
    eng.store.save_task(t1)
    eng.store.save_task(t2)

    result = eng.intervene_task(p.id, "MS1-T1", action="reset")

    assert "task_reset:MS1-T1" in result["events"]
    assert "task_reset:MS1-T2" in result["events"]
    assert eng.store.get_task("MS1-T1").status == "pending"
    assert eng.store.get_task("MS1-T2").status == "pending"
    assert eng.store.get_task("MS1-T2").output is None


def test_milestone_gate_blocks_when_criteria_unmet(tmp_path) -> None:
    def strict_gate(ms: Milestone, tasks: list[Task]) -> dict:
        return {"met": ms.id != "MS1", "reason": "MS1 forced-fail"}

    eng = _engine(tmp_path, gate_milestone=strict_gate)
    p = eng.plan("x", "g")
    result = eng.run(p.id, max_ticks=20)
    assert result["final_status"] == "blocked"
    assert eng.store.get_project(p.id).status == "blocked"
    assert eng.store.get_project(p.id).current_ms == "MS1"
    assert eng.store.get_milestone("MS1").status == "blocked"
    events = [event for tick in result["history"] for event in tick["events"]]
    assert "project_blocked:gate_failed" in events


def test_decompose_exception_blocks_project_instead_of_crashing_tick(tmp_path) -> None:
    def broken_decompose(ms: Milestone) -> list[Task]:
        raise RuntimeError(f"cannot decompose {ms.id}")

    eng = _engine(tmp_path, decompose_tasks=broken_decompose)
    p = eng.plan("x", "g")

    tick = eng.tick(p.id)

    assert "tasks_decompose_failed:MS1" in tick["events"]
    assert "project_blocked:decompose_failed" in tick["events"]
    assert tick["project_status"] == "blocked"
    assert eng.store.get_project(p.id).status == "blocked"
    assert eng.store.get_milestone("MS1").status == "blocked"
    audit = eng.store.events_for_project(p.id)
    assert audit[-1]["kind"] == "project.decompose_failed"
    assert "RuntimeError" in audit[-1]["payload"]["error"]


def test_empty_decompose_blocks_project_instead_of_spinning(tmp_path) -> None:
    eng = _engine(tmp_path, decompose_tasks=lambda _ms: [])
    p = eng.plan("x", "g")

    result = eng.run(p.id, max_ticks=5)

    assert result["final_status"] == "blocked"
    events = [event for tick in result["history"] for event in tick["events"]]
    assert "tasks_decompose_empty:MS1" in events
    assert "project_blocked:decompose_empty" in events
    assert eng.store.tasks_for_milestone("MS1") == []


def test_unreachable_task_dependency_blocks_project_instead_of_spinning(tmp_path) -> None:
    def bad_dag(ms: Milestone) -> list[Task]:
        return [
            Task(
                id=f"{ms.id}-T1",
                milestone_id=ms.id,
                type="code",
                goal="blocked forever",
                depends_on=["missing-task"],
            )
        ]

    eng = _engine(tmp_path, decompose_tasks=bad_dag)
    p = eng.plan("x", "g")

    result = eng.run(p.id, max_ticks=5)

    assert result["final_status"] == "blocked"
    events = [event for tick in result["history"] for event in tick["events"]]
    assert "milestone_blocked_dag:MS1" in events
    assert "project_blocked:task_dag_blocked" in events
    assert eng.store.get_project(p.id).current_ms == "MS1"


def test_assigner_exception_retries_then_blocks_project(tmp_path) -> None:
    def broken_assign(task: Task) -> str:
        raise RuntimeError(f"no assignee for {task.id}")

    eng = _engine(tmp_path, assign_agent=broken_assign)
    p = eng.plan("x", "g")

    result = eng.run(p.id, max_ticks=10)

    assert result["final_status"] == "blocked"
    task = eng.store.get_task("MS1-T1")
    assert task.status == "failed"
    assert task.attempts == 2
    assert "assignment error: RuntimeError" in task.output
    events = [event for tick in result["history"] for event in tick["events"]]
    assert "task_assignment_error_retry:MS1-T1" in events
    assert "task_failed_assignment:MS1-T1" in events


def test_qa_exception_retries_then_blocks_project(tmp_path) -> None:
    def broken_qa(task: Task, ms: Milestone) -> dict:
        raise RuntimeError(f"qa unavailable for {task.id}")

    eng = _engine(tmp_path, qa_task=broken_qa)
    p = eng.plan("x", "g")

    result = eng.run(p.id, max_ticks=10)

    assert result["final_status"] == "blocked"
    task = eng.store.get_task("MS1-T1")
    assert task.status == "failed"
    assert task.attempts == 2
    assert "qa error: RuntimeError" in task.qa_verdict["reason"]
    events = [event for tick in result["history"] for event in tick["events"]]
    assert "task_qa_error_retry:MS1-T1" in events
    assert "task_failed_qa_error:MS1-T1" in events


def test_gate_exception_blocks_project_instead_of_crashing_tick(tmp_path) -> None:
    def broken_gate(ms: Milestone, tasks: list[Task]) -> dict:
        raise RuntimeError(f"gate unavailable for {ms.id}")

    eng = _engine(tmp_path, gate_milestone=broken_gate)
    p = eng.plan("x", "g")

    result = eng.run(p.id, max_ticks=10)

    assert result["final_status"] == "blocked"
    assert eng.store.get_milestone("MS1").status == "blocked"
    events = [event for tick in result["history"] for event in tick["events"]]
    assert "milestone_gate_error:MS1" in events
    assert "project_blocked:gate_error" in events
    audit = eng.store.events_for_project(p.id)
    gate_failed = next(event for event in audit if event["kind"] == "project.gate_failed")
    assert "RuntimeError" in gate_failed["payload"]["error"]


def test_stale_project_block_does_not_downgrade_done_project(tmp_path) -> None:
    eng = _engine(tmp_path)
    p = eng.plan("x", "g")
    p.status = "done"
    p.current_ms = None
    eng.store.save_project(p)
    stale = Project(id=p.id, name=p.name, goal=p.goal, status="running", current_ms="MS1")
    events: list[str] = []

    eng._block_project(stale, "MS1", events, reason="late_failure")

    stored = eng.store.get_project(p.id)
    assert stored.status == "done"
    assert stored.current_ms is None
    assert "project_blocked:late_failure" not in events
    assert "project_stale_block_ignored:late_failure" in events


def test_stale_done_tick_reports_stored_terminal_project_status(tmp_path) -> None:
    eng = _engine(tmp_path)
    p = eng.plan("x", "g")
    for ms in eng.store.milestones_for(p.id):
        ms.status = "done"
        eng.store.save_milestone(p.id, ms)
    failed = Project(
        id=p.id,
        name=p.name,
        goal=p.goal,
        milestone_ids=p.milestone_ids,
        status="failed",
        current_ms="MS2",
    )
    eng.store.save_project(failed, allow_terminal_rewrite=True)

    tick = eng.tick(p.id)

    assert tick["project_status"] == "failed"
    assert tick["current_ms"] == "MS2"
    assert tick["events"] == ["project_not_runnable:failed"]
    assert eng.store.get_project(p.id).status == "failed"


def test_stale_gate_failure_does_not_downgrade_done_milestone(tmp_path) -> None:
    def strict_gate(ms: Milestone, tasks: list[Task]) -> dict:
        return {"met": False, "reason": "late fail"}

    eng = _engine(tmp_path, gate_milestone=strict_gate)
    p = eng.plan("x", "g")
    ms = eng.store.milestones_for(p.id)[0]
    ms.status = "done"
    eng.store.save_milestone(p.id, ms)
    t1 = Task(id="MS1-T1", milestone_id="MS1", type="research", goal="a")
    t1.status = "done"
    t2 = Task(id="MS1-T2", milestone_id="MS1", type="code", goal="b")
    t2.status = "done"
    eng.store.save_task(t1)
    eng.store.save_task(t2)
    stale_ms = Milestone(id="MS1", name="research", goal="scope it", status="in_progress")
    events: list[str] = []

    eng._gate_milestone(p, stale_ms, events)

    assert eng.store.get_milestone("MS1").status == "done"
    assert "milestone_gate_failed:MS1" not in events
    assert "milestone_stale_gate_failed_ignored:MS1" in events

