from __future__ import annotations

import hashlib
import json
import multiprocessing
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime.memory.learning import turn_scoring
from runtime.memory.learning.deep_evolution import _record_deep_evolve_candidate
from runtime.memory.learning.turn_scoring import TurnScore
from runtime.platform.models import ParsedIntent
from runtime.platform.process.session import Session
from runtime.safety.auth.scope import TenantScope, tenant_scoped_path
from runtime.safety.evolution.drift_monitor import DriftConfig, DriftMonitor
from runtime.safety.evolution.fitness import FitnessConfig, compute_fitness, compute_l1
from runtime.safety.recovery.tenant_scope import (
    AUTHORITATIVE_SCOPE_CONTEXT_KEY,
    authoritative_scope_context,
    trusted_scope_from_session,
)

TENANT_A = TenantScope("tenant-a-sensitive", "alice-sensitive")
TENANT_B = TenantScope("tenant-b-sensitive", "bob-sensitive")
CROSS = TenantScope("ops", "admin", allow_cross_tenant=True)


def _multiprocess_score_writer(
    root: str,
    worker_id: int,
    count: int,
    tenant_id: str,
    actor_id: str,
) -> None:
    """Spawn-safe worker used to exercise the real file transaction."""

    from runtime.memory.learning import turn_scoring as child_scoring
    from runtime.safety.auth.scope import TenantScope as ChildTenantScope

    child_scoring._project_root = lambda: Path(root)  # type: ignore[assignment]
    scope = ChildTenantScope(tenant_id, actor_id)
    for index in range(count):
        path = child_scoring.record_turn_score(
            agent_id="coder",
            score=1.0,
            reason=f"worker-{worker_id}",
            turn_id=f"worker-{worker_id}-turn-{index}",
            scope=scope,
        )
        if path is None:
            raise RuntimeError("score append was not durable")


def _multiprocess_same_turn_writer(
    root: str,
    tenant_id: str,
    actor_id: str,
) -> None:
    from runtime.memory.learning import turn_scoring as child_scoring
    from runtime.safety.auth.scope import TenantScope as ChildTenantScope

    child_scoring._project_root = lambda: Path(root)  # type: ignore[assignment]
    path = child_scoring.record_turn_score(
        agent_id="coder",
        score=1.0,
        reason="same-turn",
        rounds=3,
        thread_id="thread-shared",
        turn_id="turn-shared",
        scope=ChildTenantScope(tenant_id, actor_id),
    )
    if path is None:
        raise RuntimeError("idempotent score append failed")


def _record(
    *,
    agent_id: str,
    score: float,
    reason: str,
    scope: TenantScope | None,
    turn_id: str,
) -> Path:
    path = turn_scoring.record_turn_score(
        agent_id=agent_id,
        score=score,
        reason=reason,
        turn_id=turn_id,
        scope=scope,
    )
    assert path is not None
    return path


def _scores(level: float, *, agent_id: str = "coder") -> list[TurnScore]:
    return [
        TurnScore(
            ts=f"2026-08-26T00:00:{index:02d}",
            agent_id=agent_id,
            score=level,
            reason="fixture",
            soul_hash="hash",
            turn_id=f"turn-{index}",
        )
        for index in range(10)
    ]


def test_configured_data_dir_owns_mutable_scores(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "runtime-data"
    monkeypatch.setenv("ECHO_DATA_DIR", str(data_dir))
    monkeypatch.delenv("ECHO_HOME", raising=False)
    monkeypatch.setattr(
        turn_scoring,
        "_project_root",
        lambda: (_ for _ in ()).throw(AssertionError("source root must not be used")),
    )

    path = _record(
        agent_id="coder",
        score=1.0,
        reason="packaged-runtime",
        scope=None,
        turn_id="configured-data-dir",
    )

    assert path == data_dir / "agents" / "coder" / "agent-core" / ".scores.jsonl"
    assert path.is_file()


def test_score_storage_is_exactly_partitioned_and_cross_read_is_explicit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(turn_scoring, "_project_root", lambda: tmp_path)

    legacy_path = _record(
        agent_id="coder",
        score=0.5,
        reason="legacy",
        scope=None,
        turn_id="legacy-turn",
    )
    path_a = _record(
        agent_id="coder",
        score=1.0,
        reason="tenant-a",
        scope=TENANT_A,
        turn_id="a-turn",
    )
    path_b = _record(
        agent_id="coder",
        score=0.0,
        reason="tenant-b",
        scope=TENANT_B,
        turn_id="b-turn",
    )

    assert legacy_path != path_a != path_b
    assert path_a.parent.parent.name == "tenants"
    assert path_b.parent.parent.name == "tenants"
    for sensitive in (
        TENANT_A.tenant_id,
        TENANT_A.actor_id,
        TENANT_B.tenant_id,
        TENANT_B.actor_id,
    ):
        assert sensitive not in str(path_a)
        assert sensitive not in str(path_b)

    assert [row.reason for row in turn_scoring.read_recent_scores("coder")] == ["legacy"]
    assert [row.reason for row in turn_scoring.read_recent_scores("coder", scope=TENANT_A)] == [
        "tenant-a"
    ]
    assert [row.reason for row in turn_scoring.read_recent_scores("coder", scope=TENANT_B)] == [
        "tenant-b"
    ]
    assert {row.reason for row in turn_scoring.read_recent_scores("coder", scope=CROSS)} == {
        "legacy",
        "tenant-a",
        "tenant-b",
    }

    row_a = json.loads(path_a.read_text(encoding="utf-8"))
    assert row_a["tenant_id"] == TENANT_A.tenant_id
    assert row_a["owner_actor_id"] == TENANT_A.actor_id
    assert (
        turn_scoring.record_turn_score(
            agent_id="coder",
            score=1,
            reason="forbidden-global-write",
            scope=CROSS,
        )
        is None
    )


def test_score_reader_rejects_partial_ownership_wrong_agent_and_path_traversal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(turn_scoring, "_project_root", lambda: tmp_path)
    path = tenant_scoped_path(
        tmp_path / "agents" / "coder" / "agent-core" / ".scores.jsonl",
        TENANT_A,
    )
    path.parent.mkdir(parents=True)
    rows = [
        {
            "ts": "2026-01-01T00:00:00",
            "agent_id": "coder",
            "score": 1,
            "reason": "partial",
            "soul_hash": "h",
            "tenant_id": TENANT_A.tenant_id,
        },
        {
            "ts": "2026-01-01T00:00:01",
            "agent_id": "researcher",
            "score": 1,
            "reason": "wrong-agent",
            "soul_hash": "h",
            "tenant_id": TENANT_A.tenant_id,
            "owner_actor_id": TENANT_A.actor_id,
        },
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    assert turn_scoring.read_recent_scores("coder", scope=TENANT_A) == []
    assert turn_scoring.read_recent_scores("../../escape", scope=TENANT_A) == []
    assert (
        turn_scoring.record_turn_score(
            agent_id="../../escape",
            score=1,
            reason="unsafe",
            scope=TENANT_A,
        )
        is None
    )
    assert not (tmp_path / "escape").exists()


def test_score_append_is_multiprocess_safe_and_restart_readable(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    workers = [
        context.Process(
            target=_multiprocess_score_writer,
            args=(
                str(tmp_path),
                worker_id,
                20,
                TENANT_A.tenant_id,
                TENANT_A.actor_id,
            ),
        )
        for worker_id in range(4)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30)
        assert worker.exitcode == 0

    original_root = turn_scoring._project_root
    turn_scoring._project_root = lambda: tmp_path  # type: ignore[assignment]
    try:
        rows = turn_scoring.read_recent_scores("coder", limit=100, scope=TENANT_A)
    finally:
        turn_scoring._project_root = original_root  # type: ignore[assignment]
    assert len(rows) == 80
    assert len({row.turn_id for row in rows}) == 80


def test_same_turn_is_exactly_once_across_processes(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    workers = [
        context.Process(
            target=_multiprocess_same_turn_writer,
            args=(str(tmp_path), TENANT_A.tenant_id, TENANT_A.actor_id),
        )
        for _ in range(6)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30)
        assert worker.exitcode == 0

    original_root = turn_scoring._project_root
    turn_scoring._project_root = lambda: tmp_path  # type: ignore[assignment]
    try:
        rows = turn_scoring.read_recent_scores("coder", limit=20, scope=TENANT_A)
    finally:
        turn_scoring._project_root = original_root  # type: ignore[assignment]
    assert [row.turn_id for row in rows] == ["turn-shared"]


def test_conflicting_same_turn_payload_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(turn_scoring, "_project_root", lambda: tmp_path)
    first = _record(
        agent_id="coder",
        score=1.0,
        reason="success",
        scope=TENANT_A,
        turn_id="turn-conflict",
    )
    assert (
        turn_scoring.record_turn_score(
            agent_id="coder",
            score=0.0,
            reason="no_reply",
            scope=TENANT_A,
            turn_id="turn-conflict",
        )
        is None
    )
    assert len(first.read_text(encoding="utf-8").splitlines()) == 1
    assert turn_scoring.read_recent_scores("coder", scope=TENANT_A)[0].score == 1.0


def test_same_turn_retry_ignores_recomputed_soul_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(turn_scoring, "_project_root", lambda: tmp_path)
    hashes = iter(("old-soul", "new-soul"))
    monkeypatch.setattr(turn_scoring, "_soul_hash", lambda _agent_id: next(hashes))
    first = _record(
        agent_id="coder",
        score=1.0,
        reason="success",
        scope=TENANT_A,
        turn_id="turn-soul-changed",
    )
    retry = turn_scoring.record_turn_score(
        agent_id="coder",
        score=1.0,
        reason="success",
        scope=TENANT_A,
        turn_id="turn-soul-changed",
    )
    assert retry == first
    assert len(first.read_text(encoding="utf-8").splitlines()) == 1
    assert turn_scoring.read_recent_scores("coder", scope=TENANT_A)[0].soul_hash == ("old-soul")


def test_score_append_fsyncs_file_and_parent_and_recovers_after_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(turn_scoring, "_project_root", lambda: tmp_path)
    original_fsync = turn_scoring.os.fsync
    fsync_targets: list[bool] = []

    def _observe_fsync(fd: int) -> None:
        fsync_targets.append(stat.S_ISDIR(turn_scoring.os.fstat(fd).st_mode))
        original_fsync(fd)

    monkeypatch.setattr(turn_scoring.os, "fsync", _observe_fsync)
    path = _record(
        agent_id="coder",
        score=1.0,
        reason="durable",
        scope=TENANT_A,
        turn_id="durable-turn",
    )
    assert False in fsync_targets  # file data
    assert True in fsync_targets  # newly-created directory entry

    failed = False

    def _fail_once(fd: int) -> None:
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("injected fsync failure")
        original_fsync(fd)

    monkeypatch.setattr(turn_scoring.os, "fsync", _fail_once)
    assert (
        turn_scoring.record_turn_score(
            agent_id="coder",
            score=0.5,
            reason="uncertain-after-fsync-error",
            scope=TENANT_A,
            turn_id="fsync-failed-turn",
        )
        is None
    )

    monkeypatch.setattr(turn_scoring.os, "fsync", original_fsync)
    _record(
        agent_id="coder",
        score=1.0,
        reason="after-restart",
        scope=TENANT_A,
        turn_id="after-restart-turn",
    )
    raw_lines = path.read_text(encoding="utf-8").splitlines()
    assert raw_lines
    assert all(isinstance(json.loads(raw), dict) for raw in raw_lines)
    assert turn_scoring.read_recent_scores("coder", scope=TENANT_A)[0].turn_id == (
        "after-restart-turn"
    )


def test_reader_skips_corrupt_rows_and_next_append_repairs_truncated_tail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(turn_scoring, "_project_root", lambda: tmp_path)
    path = _record(
        agent_id="coder",
        score=1.0,
        reason="before-corruption",
        scope=TENANT_A,
        turn_id="before",
    )
    with path.open("ab") as stream:
        stream.write(b'{"tenant_id":"truncated"')

    _record(
        agent_id="coder",
        score=1.0,
        reason="after-corruption",
        scope=TENANT_A,
        turn_id="after",
    )
    rows = turn_scoring.read_recent_scores("coder", scope=TENANT_A)
    assert [row.turn_id for row in rows] == ["after", "before"]
    assert path.read_bytes().endswith(b"\n")


def test_native_score_writer_uses_only_authoritative_scope_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.sensing.gateway import _tool_bridge_scoring as bridge_scoring

    captured: dict[str, object] = {}

    def _capture(**kwargs: object) -> Path:
        captured.update(kwargs)
        return Path("ignored")

    monkeypatch.setattr(turn_scoring, "record_turn_score", _capture)
    monkeypatch.setattr(bridge_scoring, "_auto_evolve_tick_safe", lambda *_a, **_kw: None)
    intent = ParsedIntent(
        raw="test",
        intent_type="task",
        normalized_goal="test",
        user_context={
            # Public identity-shaped keys are attacker-controlled and differ
            # deliberately from the private server marker.
            "tenant_id": "spoofed-tenant",
            "owner_actor_id": "spoofed-owner",
            AUTHORITATIVE_SCOPE_CONTEXT_KEY: authoritative_scope_context(TENANT_A),
        },
    )
    bridge_scoring._record_score_safe(
        agent=SimpleNamespace(agent_id="coder"),
        intent=intent,
        has_final_reply=True,
        tool_error_count=0,
        rounds_used=1,
        duration_ms=10,
    )

    assert captured["scope"] == TENANT_A
    assert captured["agent_id"] == "coder"


def test_incomplete_or_inconsistent_session_scope_fails_closed() -> None:
    with pytest.raises(ValueError, match="incomplete"):
        trusted_scope_from_session(Session(metadata={"tenant_id": "tenant-a"}))
    with pytest.raises(ValueError, match="does not match"):
        trusted_scope_from_session(
            Session(
                actor="mallory",
                metadata={"tenant_id": "tenant-a", "owner_actor_id": "alice"},
            )
        )


def test_l1_and_full_fitness_never_mix_same_agent_across_tenants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(turn_scoring, "_project_root", lambda: tmp_path)
    for index in range(5):
        _record(
            agent_id="coder",
            score=1.0,
            reason="a-good",
            scope=TENANT_A,
            turn_id=f"a-{index}",
        )
        _record(
            agent_id="coder",
            score=0.0,
            reason="b-bad",
            scope=TENANT_B,
            turn_id=f"b-{index}",
        )

    assert compute_l1("coder", scope=TENANT_A).score == 1.0
    assert compute_l1("coder", scope=TENANT_B).score == 0.0
    assert compute_l1("coder").score == 0.5  # no legacy rows → neutral prior

    config = FitnessConfig(promotion_audit_path=str(tmp_path / "promotion_audit.json"))
    report_a = compute_fitness("coder", config, publish_event=False, scope=TENANT_A)
    report_b = compute_fitness("coder", config, publish_event=False, scope=TENANT_B)
    report_cross = compute_fitness("coder", config, publish_event=False, scope=CROSS)
    assert (report_a.combined, report_a.scope_mode) == (1.0, "tenant")
    assert (report_b.combined, report_b.scope_mode) == (0.0, "tenant")
    assert (report_cross.combined, report_cross.scope_mode) == (0.5, "cross_tenant")


def test_fitness_and_drift_http_control_plane_uses_principal_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from runtime.safety.auth.identity import Identity, IdentityStore
    from runtime.sensing.gateway.evolution_router import create_evolution_router

    monkeypatch.setattr(turn_scoring, "_project_root", lambda: tmp_path)
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("ECHO_DRIFT_STATE_DIR", raising=False)
    for index in range(5):
        _record(
            agent_id="coder",
            score=1.0,
            reason="a-good",
            scope=TENANT_A,
            turn_id=f"api-a-{index}",
        )
        _record(
            agent_id="coder",
            score=0.0,
            reason="b-bad",
            scope=TENANT_B,
            turn_id=f"api-b-{index}",
        )

    identities = IdentityStore()
    identities.add(
        Identity(
            actor_id=TENANT_A.actor_id,
            roles=("operator",),
            metadata={"tenant_id": TENANT_A.tenant_id},
        ),
        api_key_plaintext="sk-tenant-a",
    )
    identities.add(
        Identity(
            actor_id=TENANT_B.actor_id,
            roles=("operator",),
            metadata={"tenant_id": TENANT_B.tenant_id},
        ),
        api_key_plaintext="sk-tenant-b",
    )
    identities.add(
        Identity(
            actor_id="global-admin",
            roles=("admin",),
            metadata={
                "tenant_id": "admin-tenant",
                "scopes": ["evolution:cross_tenant"],
            },
        ),
        api_key_plaintext="sk-global-admin",
    )
    app = fastapi.FastAPI()
    app.include_router(create_evolution_router(identity_store=identities, require_auth=True))
    client = TestClient(app)
    headers_a = {"Authorization": "Bearer sk-tenant-a"}
    headers_b = {"Authorization": "Bearer sk-tenant-b"}
    headers_admin = {"Authorization": "Bearer sk-global-admin"}

    assert client.get("/api/evolution/fitness/coder").status_code == 401
    fitness_a = client.get("/api/evolution/fitness/coder", headers=headers_a).json()
    fitness_b = client.get("/api/evolution/fitness/coder", headers=headers_b).json()
    assert (fitness_a["l1"]["score"], fitness_a["scope_mode"]) == (1.0, "tenant")
    assert (fitness_b["l1"]["score"], fitness_b["scope_mode"]) == (0.0, "tenant")
    assert (
        client.get(
            "/api/evolution/fitness/coder?cross_tenant=true",
            headers=headers_a,
        ).status_code
        == 403
    )
    fitness_cross = client.get(
        "/api/evolution/fitness/coder?cross_tenant=true",
        headers=headers_admin,
    ).json()
    assert (fitness_cross["l1"]["score"], fitness_cross["scope_mode"]) == (
        0.5,
        "cross_tenant",
    )

    drift_a = client.get("/api/evolution/drift/coder", headers=headers_a).json()
    drift_b = client.get("/api/evolution/drift/coder", headers=headers_b).json()
    assert drift_a["scope_mode"] == "tenant"
    assert drift_b["scope_mode"] == "tenant"
    state_files = list((tmp_path / "data" / "evolution_drift_state").rglob("*.json"))
    assert len(state_files) == 2
    assert len({path.parent.name for path in state_files}) == 2
    assert all(TENANT_A.tenant_id not in str(path) for path in state_files)
    assert all(TENANT_B.tenant_id not in str(path) for path in state_files)


def test_governance_penalty_uses_the_same_tenant_partition(
    tmp_path: Path,
) -> None:
    audit_base = tmp_path / "promotion_audit.json"
    path_a = tenant_scoped_path(audit_base, TENANT_A)
    path_b = tenant_scoped_path(audit_base, TENANT_B)
    path_a.parent.mkdir(parents=True)
    path_b.parent.mkdir(parents=True)
    failed = {
        "records": [
            {
                "agent_id": "coder",
                "status": "failed",
                "decision_context": {
                    "replay_gate": {"passed": False},
                    "override_replay_gate": True,
                },
            }
        ]
    }
    path_a.write_text(json.dumps(failed), encoding="utf-8")
    path_b.write_text(json.dumps({"records": []}), encoding="utf-8")

    config = FitnessConfig(promotion_audit_path=str(audit_base))
    report_a = compute_fitness("coder", config, publish_event=False, scope=TENANT_A)
    report_b = compute_fitness("coder", config, publish_event=False, scope=TENANT_B)
    assert report_a.governance is not None
    assert report_b.governance is not None
    assert report_a.governance.penalty > 0
    assert report_b.governance.penalty == 0


def test_drift_baseline_survives_restart_and_is_exact_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import runtime.memory.learning.turn_scoring as scoring

    state_dir = tmp_path / "drift-state"
    config = DriftConfig(score_drop_threshold=0.15, state_dir=str(state_dir))
    monkeypatch.setattr(scoring, "read_recent_scores", lambda *_a, **_kw: _scores(0.9))
    first = DriftMonitor("coder", config, scope=TENANT_A)
    monkeypatch.setattr(first, "_check_soul_drift", lambda _now: None)
    monkeypatch.setattr(first, "_check_genome_drift", lambda _now: None)
    assert first.check(publish_events=False).has_drift is False
    assert first._state_path is not None
    assert first._state_path.exists()
    assert TENANT_A.tenant_id not in str(first._state_path)
    assert TENANT_A.actor_id not in str(first._state_path)

    monkeypatch.setattr(scoring, "read_recent_scores", lambda *_a, **_kw: _scores(0.4))
    restarted = DriftMonitor("coder", config, scope=TENANT_A)
    monkeypatch.setattr(restarted, "_check_soul_drift", lambda _now: None)
    monkeypatch.setattr(restarted, "_check_genome_drift", lambda _now: None)
    report = restarted.check(publish_events=False)
    assert report.max_severity == "critical"
    assert [event.kind for event in report.events] == ["score_regression"]

    other = DriftMonitor("coder", config, scope=TENANT_B)
    assert other._state_path != restarted._state_path
    assert other._baseline_score is None


def test_score_hash_and_drift_use_configured_agents_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agents_root = tmp_path / "configured-agent-assets"
    soul_path = agents_root / "coder" / "agent-core" / "SOUL.md"
    soul_path.parent.mkdir(parents=True)
    soul_path.write_text("first soul\n", encoding="utf-8")
    monkeypatch.setenv("ECHO_AGENTS_ROOT", str(agents_root))
    monkeypatch.setattr(turn_scoring, "_project_root", lambda: tmp_path / "runtime-data")

    _record(
        agent_id="coder",
        score=1.0,
        reason="configured-root",
        scope=TENANT_A,
        turn_id="configured-root-turn",
    )
    recorded = turn_scoring.read_recent_scores("coder", scope=TENANT_A)[0]
    assert (
        recorded.soul_hash
        == hashlib.md5(  # noqa: S324 - compatibility fingerprint
            b"first soul\n",
            usedforsecurity=False,
        ).hexdigest()[:8]
    )

    config = DriftConfig(state_dir=str(tmp_path / "drift-state"))
    first = DriftMonitor("coder", config, scope=TENANT_A)
    assert first.check(publish_events=False).has_drift is False
    soul_path.write_text("second soul\n", encoding="utf-8")
    restarted = DriftMonitor("coder", config, scope=TENANT_A)
    report = restarted.check(publish_events=False)
    assert [event.kind for event in report.events] == ["soul_change"]


def test_drift_state_damage_and_wrong_provenance_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = DriftConfig(state_dir=str(tmp_path / "state"))
    seed = DriftMonitor("coder", config, scope=TENANT_A)
    assert seed._state_path is not None
    seed._state_path.parent.mkdir(parents=True, exist_ok=True)
    seed._state_path.write_text("{truncated", encoding="utf-8")

    damaged = DriftMonitor("coder", config, scope=TENANT_A)
    monkeypatch.setattr(damaged, "_check_score_drift", lambda _now: pytest.fail("must not read"))
    report = damaged.check(publish_events=False)
    assert report.max_severity == "critical"
    assert report.events[0].kind == "drift_state_integrity"
    assert seed._state_path.read_text(encoding="utf-8") == "{truncated"

    seed._state_path.write_text("{}", encoding="utf-8")
    empty_object = DriftMonitor("coder", config, scope=TENANT_A)
    assert empty_object.check(publish_events=False).max_severity == "critical"
    assert seed._state_path.read_text(encoding="utf-8") == "{}"

    state_b = DriftMonitor("coder", config, scope=TENANT_B)
    assert state_b._state_path is not None
    state_b._state_path.parent.mkdir(parents=True, exist_ok=True)
    state_b._state_path.write_text(
        json.dumps(
            {
                "schema": "echo.evolution.drift_state.v1",
                "agent_id": "coder",
                "scope_mode": "tenant",
                "tenant_id": TENANT_A.tenant_id,
                "owner_actor_id": TENANT_A.actor_id,
                "last_soul_hash": None,
                "last_genome_version": None,
                "baseline_score": 0.9,
            }
        ),
        encoding="utf-8",
    )
    wrong_owner = DriftMonitor("coder", config, scope=TENANT_B)
    assert wrong_owner.check(publish_events=False).max_severity == "critical"


def test_drift_monitor_rejects_unsafe_agent_before_any_io(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    with pytest.raises(ValueError, match="unsafe agent id"):
        DriftMonitor(
            "../../escape",
            DriftConfig(state_dir=str(state_dir)),
            scope=TENANT_A,
        )
    assert not state_dir.exists()


def test_cross_tenant_drift_view_never_persists_a_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import runtime.memory.learning.turn_scoring as scoring

    monkeypatch.setattr(scoring, "read_recent_scores", lambda *_a, **_kw: _scores(0.8))
    monitor = DriftMonitor(
        "coder",
        DriftConfig(state_dir=str(tmp_path / "state")),
        scope=CROSS,
    )
    monkeypatch.setattr(monitor, "_check_soul_drift", lambda _now: None)
    monkeypatch.setattr(monitor, "_check_genome_drift", lambda _now: None)
    monitor.check(publish_events=False)
    assert monitor._state_path is None
    assert not (tmp_path / "state").exists()


def test_deep_evolution_candidate_registry_is_tenant_partitioned(
    tmp_path: Path,
) -> None:
    base = tmp_path / "evolution_candidates.jsonl"
    candidate = _record_deep_evolve_candidate(
        agent_id="coder",
        candidate={
            "id": "c1",
            "kind": "add_lesson",
            "lesson": "Verify the final result before responding.",
            "tag": "verification",
            "risk": "low",
        },
        judgment={
            "verdict": "apply",
            "predicted_avg_score_delta": 0.2,
            "confidence": "high",
        },
        holdout_passed=True,
        source_failures=["no_reply"],
        registry_path=base,
        scope=TENANT_A,
    )
    scoped_path = tenant_scoped_path(base, TENANT_A)
    assert scoped_path.exists()
    assert not base.exists()
    assert candidate.tenant_id == TENANT_A.tenant_id
    assert candidate.owner_actor_id == TENANT_A.actor_id
    assert TENANT_A.tenant_id not in str(scoped_path)
    assert TENANT_A.actor_id not in str(scoped_path)

    with pytest.raises(ValueError, match="cross-tenant"):
        _record_deep_evolve_candidate(
            agent_id="coder",
            candidate={
                "id": "c2",
                "kind": "add_lesson",
                "lesson": "Keep scope exact.",
            },
            judgment={"verdict": "apply"},
            holdout_passed=True,
            source_failures=[],
            registry_path=base,
            scope=CROSS,
        )


def test_deep_reflect_and_evolve_read_only_the_supplied_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.memory.learning import deep_evolution

    captured: list[TenantScope | None] = []

    def _capture_scores(
        _agent_id: str,
        _limit: int = 50,
        *,
        scope: TenantScope | None = None,
        **_kwargs: object,
    ) -> list[TurnScore]:
        captured.append(scope)
        return []

    monkeypatch.setattr(turn_scoring, "read_recent_scores", _capture_scores)
    deep_evolution.set_evolve_router(object())
    try:
        reflected = deep_evolution.deep_reflect(agent_id="coder", scope=TENANT_A)
        evolved = deep_evolution.deep_evolve(agent_id="coder", scope=TENANT_B)
    finally:
        deep_evolution.set_evolve_router(None)

    assert reflected["scores_count"] == 0
    assert evolved["audit"][0]["skipped"] == "no scored turns yet"
    assert captured == [TENANT_A, TENANT_B]
