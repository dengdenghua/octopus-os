from __future__ import annotations

import subprocess
import sys
import time

from runtime.execution.loops.models import (
    LoopAttempt,
    LoopRun,
    LoopRunStatus,
    VerifierFinding,
    VerifierResult,
)
from runtime.execution.loops.store import LoopRunStore
from runtime.execution.loops.verifiers import (
    _classify_finding,
    build_default_loop_verifier_registry,
)
from runtime.platform.io.atomic import _cross_process_lock


def test_loop_store_create_get_filter_and_mutate(tmp_path) -> None:
    store = LoopRunStore(tmp_path / "loop_runs.json")
    execution_policy = {
        "schema": "echo.execution_policy.v1",
        "sandbox_requested": True,
        "workspace": str(tmp_path),
        "cwd": str(tmp_path),
        "backend": "seatbelt",
        "hard": True,
        "allow_network": False,
        "env_mode": "allowlist",
        "process_group": True,
        "process_tree_kill": True,
        "timeout_s": 60,
    }
    alice = LoopRun(
        owner_id="alice",
        parent_run_id=" parent-1 ",
        origin_run_id=" origin-1 ",
        resume_checkpoint_id=" checkpoint-1 ",
        goal="fix auth flow",
        thread_id=" th-alice ",
    )
    bob = LoopRun(owner_id="bob", goal="refactor worker", thread_id="th-bob")

    store.create(alice)
    store.create(bob)

    updated = store.mutate(
        alice.run_id,
        lambda current: current.model_copy(
            update={
                "status": LoopRunStatus.VERIFYING,
                "attempts": [
                    LoopAttempt(
                        attempt_index=1,
                        prompt=current.goal,
                        success=False,
                        status="needs_verify",
                    )
                ],
                "last_verifier_result": VerifierResult(
                    profile="python_repo_patch",
                    kind="python",
                    passed=False,
                    findings=[
                        VerifierFinding(
                            name="syntax",
                            passed=False,
                            exit_code=1,
                            stderr="SyntaxError: bad indent",
                            execution_policy=execution_policy,
                        )
                    ],
                    summary="failed checks: syntax",
                ),
            }
        ),
    )

    fetched = store.get(alice.run_id)
    assert fetched is not None
    assert fetched.status == LoopRunStatus.VERIFYING
    assert fetched.last_verifier_result is not None
    assert fetched.last_verifier_result.findings[0].name == "syntax"
    assert fetched.last_verifier_result.findings[0].execution_policy == execution_policy
    assert fetched.parent_run_id == "parent-1"
    assert fetched.origin_run_id == "origin-1"
    assert fetched.resume_checkpoint_id == "checkpoint-1"
    assert fetched.thread_id == "th-alice"
    assert fetched.attempts[0].prompt == "fix auth flow"
    assert fetched.updated_at != alice.updated_at
    assert updated.updated_at == fetched.updated_at

    alice_only = store.list(owner_id="alice")
    assert [run.run_id for run in alice_only] == [alice.run_id]
    assert store.count(owner_id="alice") == 1
    assert store.count(status="verifying") == 1
    assert store.list(status="verifying")[0].run_id == alice.run_id


def test_loop_store_write_lock_serializes_cross_process_writers(tmp_path) -> None:
    path = tmp_path / "loop_runs.json"
    started = tmp_path / "child-started"
    done = tmp_path / "child-done"
    script = f"""
from pathlib import Path
from runtime.execution.loops.models import LoopRun
from runtime.execution.loops.store import LoopRunStore

Path({str(started)!r}).write_text("started", encoding="utf-8")
LoopRunStore({str(path)!r}).create(LoopRun(run_id="child-run", goal="child write"))
Path({str(done)!r}).write_text("done", encoding="utf-8")
"""

    with _cross_process_lock(path.parent / f"{path.name}.rw"):
        child = subprocess.Popen([sys.executable, "-c", script])
        deadline = time.monotonic() + 5
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert started.exists()
        time.sleep(0.2)
        assert done.exists() is False
        assert child.poll() is None

    child.wait(timeout=5)
    assert child.returncode == 0
    assert done.exists()
    assert LoopRunStore(path).get("child-run") is not None


def test_default_loop_verifier_registry_exposes_auto_and_legacy_profiles() -> None:
    registry = build_default_loop_verifier_registry()

    for profile in {
        "auto",
        "python_repo_patch",
        "python",
        "node",
        "node-ts",
        "rust",
        "go",
        "unknown",
    }:
        assert profile in registry._handlers


def test_loop_verifier_classifies_failures_for_repair_policy() -> None:
    assert (
        _classify_finding(
            name="typecheck",
            command="npx --no-install tsc --noEmit",
            exit_code=-3,
            stdout="",
            stderr="executable not found: npx",
        )
        == "environment_missing_tool"
    )
    assert (
        _classify_finding(
            name="typecheck",
            command="python -m mypy .",
            exit_code=1,
            stdout="",
            stderr="/usr/bin/python: No module named mypy",
        )
        == "environment_missing_dependency"
    )
    assert (
        _classify_finding(
            name="test",
            command="python -m pytest -q",
            exit_code=1,
            stdout="",
            stderr="AssertionError: expected 200 got 500",
        )
        == "test_failure"
    )
    assert (
        _classify_finding(
            name="package-json",
            command='python -c "parse package.json"',
            exit_code=2,
            stdout="",
            stderr="package.json: invalid JSON: Expecting property name",
        )
        == "project_manifest_error"
    )
    assert (
        _classify_finding(
            name="package-json",
            command='python -c "parse package.json"',
            exit_code=-1,
            stdout="",
            stderr="timeout",
        )
        == "verification_timeout"
    )
    assert (
        _classify_finding(
            name="slow",
            command="tool",
            exit_code=-5,
            stdout="partial output",
            stderr="cancelled",
        )
        == "verification_cancelled"
    )
    assert (
        _classify_finding(
            name="cwd",
            command="python -c cwd",
            exit_code=-4,
            stdout="",
            stderr="sandbox_violation: cwd escapes workspace",
        )
        == "verifier_sandbox_violation"
    )
    assert (
        _classify_finding(
            name="odd",
            command="tool",
            exit_code=-6,
            stdout="",
            stderr="verifier runner returned no exit_code",
        )
        == "verifier_internal_error"
    )


def test_loop_verifier_classification_prefers_execution_policy_result() -> None:
    def policy(status: str, **result):
        return {
            "schema": "echo.execution_policy.v1",
            "result": {
                "status": status,
                **result,
            },
        }

    assert (
        _classify_finding(
            name="typecheck",
            command="python -m mypy .",
            exit_code=1,
            stdout="",
            stderr="/usr/bin/python: No module named mypy",
            execution_policy=policy("timed_out", timed_out=True),
        )
        == "verification_timeout"
    )
    assert (
        _classify_finding(
            name="test",
            command="python -m pytest -q",
            exit_code=1,
            stdout="AssertionError",
            stderr="",
            execution_policy=policy("sandbox_violation", error_type="sandbox_violation"),
        )
        == "verifier_sandbox_violation"
    )
    assert (
        _classify_finding(
            name="lint",
            command="ruff check .",
            exit_code=1,
            stdout="F401 unused import",
            stderr="",
            execution_policy=policy("cancelled", cancelled=True),
        )
        == "verification_cancelled"
    )


def test_reconcile_interrupted_folds_active_runs_and_preserves_attempts(tmp_path) -> None:
    """Audit R-02: runs left ACTIVE by a crashed process become
    ``interrupted`` (resumable), terminal runs are untouched, attempts
    survive, and the sweep is idempotent."""
    store = LoopRunStore(tmp_path / "loop_runs.json")

    running = LoopRun(
        goal="was mid-flight",
        status=LoopRunStatus.RUNNING,
        attempts=[LoopAttempt(attempt_index=1, prompt="work", status="running")],
    )
    verifying = LoopRun(goal="in verify", status=LoopRunStatus.VERIFYING)
    pending = LoopRun(goal="never dispatched", status=LoopRunStatus.PENDING)
    done = LoopRun(goal="finished", status=LoopRunStatus.COMPLETED)
    failed = LoopRun(goal="already failed", status=LoopRunStatus.FAILED)
    for run in (running, verifying, pending, done, failed):
        store.create(run)

    affected = store.reconcile_interrupted()
    assert sorted(affected) == sorted([running.run_id, verifying.run_id, pending.run_id])

    assert store.get(running.run_id).status is LoopRunStatus.INTERRUPTED
    assert store.get(verifying.run_id).status is LoopRunStatus.INTERRUPTED
    assert store.get(pending.run_id).status is LoopRunStatus.INTERRUPTED
    assert store.get(done.run_id).status is LoopRunStatus.COMPLETED
    assert store.get(failed.run_id).status is LoopRunStatus.FAILED

    # Attempts are preserved so the run stays resumable.
    assert len(store.get(running.run_id).attempts) == 1
    # A reconciliation reason is recorded when the run had none.
    assert "restart" in store.get(verifying.run_id).last_error

    # Idempotent: a second sweep finds nothing active.
    assert store.reconcile_interrupted() == []


def test_reconcile_interrupted_on_empty_store(tmp_path) -> None:
    store = LoopRunStore(tmp_path / "loop_runs.json")
    assert store.reconcile_interrupted() == []


# ═══════════════════════════════════════════════════════════
# Audit T-13: retention (max-runs cap + TTL)
# ═══════════════════════════════════════════════════════════


def test_prune_enforces_max_runs_cap(tmp_path) -> None:
    store = LoopRunStore(tmp_path / "loop_runs.json")
    for i in range(30):
        store.create(
            LoopRun(
                owner_id="alice",
                goal=f"g{i}",
                thread_id="t",
                workspace_path=str(tmp_path),
                status=LoopRunStatus.COMPLETED,
            )
        )
    removed = store.prune(max_runs=10, ttl_seconds=0)
    assert removed == 20
    remaining = store.list(limit=1000)
    assert len(remaining) == 10
    # The newest 10 survive (created_at ordering).
    goals = sorted(r.goal for r in remaining)
    assert goals == [f"g{i}" for i in range(20, 30)]


def test_prune_enforces_ttl(tmp_path) -> None:
    from datetime import UTC, datetime, timedelta

    store = LoopRunStore(tmp_path / "loop_runs.json")
    old = LoopRun(
        owner_id="alice",
        goal="old",
        thread_id="t",
        workspace_path=str(tmp_path),
        status=LoopRunStatus.COMPLETED,
        created_at=(datetime.now(UTC) - timedelta(days=200)).isoformat(),
    )
    fresh = LoopRun(
        owner_id="alice",
        goal="fresh",
        thread_id="t",
        workspace_path=str(tmp_path),
        status=LoopRunStatus.COMPLETED,
    )
    store.create(old)
    store.create(fresh)
    removed = store.prune(max_runs=0, ttl_seconds=90 * 24 * 60 * 60)
    assert removed == 1
    remaining = store.list(limit=100)
    assert [r.goal for r in remaining] == ["fresh"]


def test_prune_is_idempotent_when_within_policy(tmp_path) -> None:
    store = LoopRunStore(tmp_path / "loop_runs.json")
    for i in range(5):
        store.create(
            LoopRun(
                owner_id="alice",
                goal=f"g{i}",
                thread_id="t",
                workspace_path=str(tmp_path),
                status=LoopRunStatus.COMPLETED,
            )
        )
    assert store.prune(max_runs=100, ttl_seconds=0) == 0
    assert len(store.list(limit=1000)) == 5


# ═══════════════════════════════════════════════════════════
# Audit T-16: dispatcher queue cap refuses when full
# ═══════════════════════════════════════════════════════════


def test_dispatcher_queue_cap_refuses_when_full(tmp_path) -> None:
    import threading
    import time

    from runtime.execution.loops.dispatcher import LoopRunDispatcher

    release = threading.Event()
    started: list[str] = []

    class _SlowController:
        def execute(self, run_id: str, cancellation_token=None):
            started.append(run_id)
            release.wait(10)
            return

    store = LoopRunStore(tmp_path / "loop_runs.json")
    d = LoopRunDispatcher(
        controller=_SlowController(),
        store=store,
        max_workers=1,
        max_queued=2,
    )
    try:
        assert d.submit("r1") is True
        assert d.submit("r2") is True  # queued
        assert d.submit("r3") is True  # queued (1 running + 2 queued = cap)
        assert d.submit("r4") is False  # queue full -> refused
        assert d.submit("r1") is True  # already accepted run is a no-op True
    finally:
        release.set()
        time.sleep(0.1)


def test_prune_with_default_policy_no_args(tmp_path) -> None:
    """prune() without explicit limits uses the class defaults (regression:
    bare class-attribute references in the method used to NameError)."""
    store = LoopRunStore(tmp_path / "loop_runs.json")
    run = LoopRun(owner_id="owner", goal="prune default policy", thread_id="th-prune")
    created = store.create(run)
    assert store.prune() >= 0
    # default TTL is 90 days — a fresh run stays
    assert store.get(created.run_id) is not None

