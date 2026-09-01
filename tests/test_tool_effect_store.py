from __future__ import annotations

import multiprocessing
import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from runtime.execution.suckers import Skill, SkillRegistry
from runtime.execution.tool_engine import ToolExecutor
from runtime.execution.tool_engine.effect_receipts import (
    EffectLeaseLost,
    ToolEffectReceiptIndex,
)
from runtime.execution.tool_engine.effect_store import SQLiteEffectStore
from runtime.execution.tool_engine.redis_effect_store import RedisEffectStore
from runtime.memory.journal import InMemoryJournal, JSONLJournal, StepEvent
from runtime.platform.models import (
    ArmId,
    Budget,
    BudgetLimits,
    ExecutionResult,
    SkillId,
    Step,
    TaskId,
    ToolCall,
)
from runtime.safety.auth import TrustEngine


def _executor_with_shared_store(
    store_path: str | Path,
    handler,
    *,
    lease_ttl_s: float = 0.3,
    wait_timeout_s: float = 4.0,
    journal: InMemoryJournal | None = None,
) -> ToolExecutor:
    return _executor_with_backend(
        SQLiteEffectStore(store_path),
        handler,
        lease_ttl_s=lease_ttl_s,
        wait_timeout_s=wait_timeout_s,
        journal=journal,
    )


def _executor_with_backend(
    store,
    handler,
    *,
    lease_ttl_s: float = 0.3,
    wait_timeout_s: float = 4.0,
    journal: InMemoryJournal | None = None,
) -> ToolExecutor:
    journal = journal if journal is not None else InMemoryJournal()
    registry = SkillRegistry()
    registry.register(
        Skill(
            name="shared_effect_tool",
            description="cross-process effect test",
            affinity=["write"],
            trusted_source="skill://public/shared-effect-tool",
            handler=handler,
        )
    )
    executor = ToolExecutor(
        registry=registry,
        immunity=TrustEngine(trusted_sources=["skill://public/*"]),
        journal=journal,
        effect_store=store,
    )
    executor._effect_receipts = ToolEffectReceiptIndex(  # noqa: SLF001
        journal,
        store=store,
        lease_ttl_s=lease_ttl_s,
        wait_timeout_s=wait_timeout_s,
        poll_interval_s=0.02,
    )
    executor._effect_receipts_journal = journal  # noqa: SLF001
    return executor


class _FailingStepJournal(InMemoryJournal):
    def write(self, event) -> None:
        if isinstance(event, StepEvent):
            raise OSError("simulated process loss before journal append")
        super().write(event)


class _FakeRedisScript:
    def __init__(self, source: str, client: _FakeRedis) -> None:
        self.source = source
        self.client = client

    def __call__(self, *, keys: list[str], args: list[Any]) -> int:
        with self.client.lock:
            if "echo_effect_authorize_retry_v1" in self.source:
                if self.client.get(keys[0]) is not None:
                    return 0
                raw = self.client.get(keys[1])
                if raw is None:
                    return 0
                import json

                receipt = json.loads(raw)
                if receipt.get("state") != "indeterminate":
                    return 0
                if int(receipt.get("fencing_token") or 0) != int(args[0]):
                    return 0
                receipt["state"] = "retry_authorized"
                receipt["holder_id"] = args[1]
                receipt["reason"] = args[2]
                receipt["updated_at"] = float(args[3])
                self.client.set(keys[1], json.dumps(receipt))
                return 1
            if "echo_effect_repair_committed_v1" in self.source:
                if self.client.get(keys[0]) is not None:
                    return 0
                self.client.set(keys[1], args[0])
                return 1
            expected = args[0]
            expected_bytes = expected.encode() if isinstance(expected, str) else expected
            if self.client.get(keys[0]) != expected_bytes:
                return 0
            if "echo_effect_fenced_set_retain_v1" in self.source:
                self.client.set(keys[1], args[1])
                self.client.pexpire(keys[0], int(args[2]))
                return 1
            if "echo_effect_fenced_set_release_v1" in self.source:
                self.client.set(keys[1], args[1])
                self.client.delete(keys[0])
                return 1
            if "echo_effect_fenced_delete_release_v1" in self.source:
                self.client.delete(keys[1])
                self.client.delete(keys[0])
                return 1
            if "PEXPIRE" in self.source:
                return self.client.pexpire(keys[0], int(args[1]))
            if "DEL" in self.source:
                return self.client.delete(keys[0])
            return 0


class _FakeRedis:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.values: dict[str, bytes] = {}
        self.expires: dict[str, float] = {}

    def _expire(self, key: str) -> None:
        expires_at = self.expires.get(key)
        if expires_at is not None and time.time() >= expires_at:
            self.values.pop(key, None)
            self.expires.pop(key, None)

    def get(self, key: str):
        with self.lock:
            self._expire(key)
            return self.values.get(key)

    def set(
        self,
        key: str,
        value,
        *,
        nx: bool = False,
        px: int | None = None,
    ) -> bool:
        with self.lock:
            self._expire(key)
            if nx and key in self.values:
                return False
            self.values[key] = value.encode() if isinstance(value, str) else value
            if px is not None:
                self.expires[key] = time.time() + px / 1000
            return True

    def delete(self, key: str) -> int:
        with self.lock:
            existed = key in self.values
            self.values.pop(key, None)
            self.expires.pop(key, None)
            return int(existed)

    def incr(self, key: str) -> int:
        with self.lock:
            self._expire(key)
            value = int(self.values.get(key, b"0")) + 1
            self.values[key] = str(value).encode()
            return value

    def pexpire(self, key: str, ttl_ms: int) -> int:
        with self.lock:
            self._expire(key)
            if key not in self.values:
                return 0
            self.expires[key] = time.time() + ttl_ms / 1000
            return 1

    def pttl(self, key: str) -> int:
        with self.lock:
            self._expire(key)
            if key not in self.values:
                return -2
            expires_at = self.expires.get(key)
            if expires_at is None:
                return -1
            return max(-2, int((expires_at - time.time()) * 1000))

    def register_script(self, source: str) -> _FakeRedisScript:
        return _FakeRedisScript(source, self)

    def scan_iter(self, *, match: str, count: int = 100):
        del count
        import fnmatch

        with self.lock:
            keys = list(self.values)
        for key in keys:
            self._expire(key)
            if fnmatch.fnmatch(key, match) and key in self.values:
                yield key.encode()

    def ping(self) -> bool:
        return True


def _run_shared(executor: ToolExecutor, task_id: TaskId):
    return executor.execute_step(
        step_id=1,
        node_id="react_n1",
        sucker_id=SkillId("shared_effect_tool"),
        args={"value": "one"},
        caller="react_loop",
        task_id=task_id,
        arm_id=ArmId("react_arm"),
        budget=Budget(
            task_id=task_id,
            limits=BudgetLimits(tokens=10_000, usd=1.0),
        ),
    )


def _sample_step(output: str = "old result") -> Step:
    action = ToolCall(
        caller="react_loop",
        sucker_id=SkillId("shared_effect_tool"),
        args={"value": "one"},
    )
    return Step(
        step_id=1,
        node_id="react_n1",
        action=action,
        result=ExecutionResult(
            call_id=action.call_id,
            status="success",
            output=output,
        ),
    )


def _competing_worker(
    store_path: str,
    effect_path: str,
    task_id: str,
    gate,
    queue,
) -> None:
    def _handler(value: str):
        with Path(effect_path).open("a", encoding="utf-8") as stream:
            stream.write(f"{os.getpid()}:{value}\n")
            stream.flush()
            os.fsync(stream.fileno())
        # Longer than the lease: the heartbeat must retain ownership.
        time.sleep(0.8)
        return {"value": value, "owner": os.getpid()}

    executor = _executor_with_shared_store(store_path, _handler)
    gate.wait(timeout=3)
    step = _run_shared(executor, TaskId(UUID(task_id)))
    queue.put(
        {
            "success": step.success,
            "replayed": "durable_effect_replay" in step.result.stderr_tags,
            "output": step.result.output,
        }
    )


def _crashing_worker(
    store_path: str,
    effect_path: str,
    task_id: str,
    entered,
) -> None:
    def _handler(value: str):
        with Path(effect_path).open("a", encoding="utf-8") as stream:
            stream.write(f"crashed:{value}\n")
            stream.flush()
            os.fsync(stream.fileno())
        entered.set()
        os._exit(23)

    executor = _executor_with_shared_store(
        store_path,
        _handler,
        lease_ttl_s=0.2,
        wait_timeout_s=1.0,
    )
    _run_shared(executor, TaskId(UUID(task_id)))


def test_expired_unstarted_claim_can_be_safely_taken_over(tmp_path: Path) -> None:
    store = SQLiteEffectStore(tmp_path / "effects.sqlite3")
    first = store.claim(
        effect_key="effect:test",
        task_id="task",
        step_id=1,
        sucker_id="tool",
        args_fingerprint="args",
        side_effecting=True,
        holder_id="worker-a",
        lease_ttl_s=0.05,
        observed_durable_intent=False,
    )
    assert first.kind == "execute"

    time.sleep(0.08)
    takeover = store.claim(
        effect_key="effect:test",
        task_id="task",
        step_id=1,
        sucker_id="tool",
        args_fingerprint="args",
        side_effecting=True,
        holder_id="worker-b",
        lease_ttl_s=1,
        observed_durable_intent=False,
    )

    assert takeover.kind == "execute"
    assert takeover.fencing_token > first.fencing_token


def test_sqlite_store_recovers_after_state_directory_is_recreated(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    store_path = state_root / "effects.sqlite3"
    store = SQLiteEffectStore(store_path)
    initial = store.claim(
        effect_key="effect:before-reset",
        task_id="task",
        step_id=1,
        sucker_id="tool",
        args_fingerprint="args",
        side_effecting=False,
        holder_id="worker-a",
        lease_ttl_s=1,
        observed_durable_intent=False,
    )
    assert initial.kind == "execute"
    assert len(store.list_receipts()) == 1

    # A long-lived server may outlive a workspace/state reset. The existing
    # store object must recover without requiring a process restart.
    shutil.rmtree(state_root)

    assert store.list_receipts() == []
    after_reset = store.claim(
        effect_key="effect:after-reset",
        task_id="task",
        step_id=2,
        sucker_id="tool",
        args_fingerprint="args",
        side_effecting=False,
        holder_id="worker-b",
        lease_ttl_s=1,
        observed_durable_intent=False,
    )
    assert after_reset.kind == "execute"
    assert store_path.is_file()
    assert [receipt.effect_key for receipt in store.list_receipts()] == ["effect:after-reset"]


def test_sqlite_store_migrates_result_summary_for_existing_receipts(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "effects.sqlite3"
    with sqlite3.connect(store_path) as conn:
        conn.execute(
            """
            CREATE TABLE tool_effect_receipts (
                effect_key TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                step_id INTEGER NOT NULL,
                sucker_id TEXT NOT NULL,
                args_fingerprint TEXT NOT NULL,
                side_effecting INTEGER NOT NULL,
                state TEXT NOT NULL,
                holder_id TEXT NOT NULL DEFAULT '',
                fencing_token INTEGER NOT NULL DEFAULT 0,
                lease_expires_at REAL NOT NULL DEFAULT 0,
                call_id TEXT NOT NULL DEFAULT '',
                step_json TEXT,
                reason TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO tool_effect_receipts(
                effect_key, task_id, step_id, sucker_id, args_fingerprint,
                side_effecting, state, step_json, created_at, updated_at
            ) VALUES(?, '', 0, '', '', 0, 'committed', ?, 1, 1)
            """,
            ("effect:legacy", _sample_step().model_dump_json()),
        )

    migrated = SQLiteEffectStore(store_path)

    [receipt] = migrated.list_receipts()
    assert receipt.effect_key == "effect:legacy"
    assert receipt.has_result is True


def test_sqlite_receipt_listing_skips_large_results_and_uses_sort_indexes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SQLiteEffectStore(tmp_path / "effects.sqlite3")
    store.record_committed(
        effect_key="effect:large-result",
        step=_sample_step("x" * 100_000),
    )
    real_connect = store._connect  # noqa: SLF001
    traced_queries: list[str] = []

    def _constrained_connect() -> sqlite3.Connection:
        conn = real_connect()
        conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1_024)
        conn.set_trace_callback(traced_queries.append)
        return conn

    monkeypatch.setattr(store, "_connect", _constrained_connect)

    [receipt] = store.list_receipts(limit=1)
    assert receipt.effect_key == "effect:large-result"
    assert receipt.has_result is True
    unfiltered_query = next(
        query for query in traced_queries if query.lstrip().startswith("SELECT")
    )

    traced_queries.clear()
    [filtered_receipt] = store.list_receipts(state="committed", limit=1)
    assert filtered_receipt.effect_key == "effect:large-result"
    filtered_query = next(query for query in traced_queries if query.lstrip().startswith("SELECT"))

    conn = real_connect()
    try:
        unfiltered_plan = conn.execute(f"EXPLAIN QUERY PLAN {unfiltered_query}").fetchall()
        filtered_plan = conn.execute(f"EXPLAIN QUERY PLAN {filtered_query}").fetchall()
    finally:
        conn.close()
    unfiltered_details = [str(row[3]) for row in unfiltered_plan]
    filtered_details = [str(row[3]) for row in filtered_plan]
    assert any(
        "idx_tool_effect_receipts_priority_updated" in detail for detail in unfiltered_details
    )
    assert any("idx_tool_effect_receipts_state" in detail for detail in filtered_details)
    assert all("USE TEMP B-TREE" not in detail for detail in unfiltered_details)
    assert all("USE TEMP B-TREE" not in detail for detail in filtered_details)


def test_sqlite_journal_repair_cannot_overwrite_live_takeover(tmp_path: Path) -> None:
    store = SQLiteEffectStore(tmp_path / "effects.sqlite3")
    first = store.claim(
        effect_key="effect:test",
        task_id="task",
        step_id=1,
        sucker_id="tool",
        args_fingerprint="args",
        side_effecting=False,
        holder_id="worker-a",
        lease_ttl_s=0.05,
        observed_durable_intent=False,
    )
    assert first.kind == "execute"
    time.sleep(0.08)
    takeover = store.claim(
        effect_key="effect:test",
        task_id="task",
        step_id=1,
        sucker_id="tool",
        args_fingerprint="args",
        side_effecting=False,
        holder_id="worker-b",
        lease_ttl_s=1,
        observed_durable_intent=False,
    )
    assert takeover.kind == "execute"

    store.record_committed(effect_key="effect:test", step=_sample_step())
    observer = store.claim(
        effect_key="effect:test",
        task_id="task",
        step_id=1,
        sucker_id="tool",
        args_fingerprint="args",
        side_effecting=False,
        holder_id="worker-c",
        lease_ttl_s=1,
        observed_durable_intent=False,
    )

    assert observer.kind == "busy"
    assert observer.step is None


def test_expired_started_side_effect_is_never_taken_over(tmp_path: Path) -> None:
    store = SQLiteEffectStore(tmp_path / "effects.sqlite3")
    first = store.claim(
        effect_key="effect:test",
        task_id="task",
        step_id=1,
        sucker_id="tool",
        args_fingerprint="args",
        side_effecting=True,
        holder_id="worker-a",
        lease_ttl_s=0.05,
        observed_durable_intent=False,
    )
    assert store.mark_started(
        effect_key="effect:test",
        holder_id="worker-a",
        fencing_token=first.fencing_token,
        call_id="call-a",
        lease_ttl_s=0.05,
    )

    time.sleep(0.08)
    takeover = store.claim(
        effect_key="effect:test",
        task_id="task",
        step_id=1,
        sucker_id="tool",
        args_fingerprint="args",
        side_effecting=True,
        holder_id="worker-b",
        lease_ttl_s=1,
        observed_durable_intent=False,
    )

    assert takeover.kind == "indeterminate"


def test_two_processes_execute_one_side_effect_and_share_result(tmp_path: Path) -> None:
    ctx = multiprocessing.get_context("spawn")
    store_path = str(tmp_path / "effects.sqlite3")
    effect_path = str(tmp_path / "effect.log")
    task_id = str(uuid4())
    gate = ctx.Event()
    queue = ctx.Queue()
    workers = [
        ctx.Process(
            target=_competing_worker,
            args=(store_path, effect_path, task_id, gate, queue),
        )
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    gate.set()
    for worker in workers:
        worker.join(timeout=8)

    assert all(worker.exitcode == 0 for worker in workers)
    results = [queue.get(timeout=1) for _ in workers]
    assert all(result["success"] for result in results)
    assert sum(result["replayed"] for result in results) == 1
    assert len((tmp_path / "effect.log").read_text(encoding="utf-8").splitlines()) == 1


def test_process_crash_after_handler_entry_fails_closed_without_repeating(
    tmp_path: Path,
) -> None:
    ctx = multiprocessing.get_context("spawn")
    store_path = str(tmp_path / "effects.sqlite3")
    effect_path = str(tmp_path / "effect.log")
    task_id = str(uuid4())
    entered = ctx.Event()
    worker = ctx.Process(
        target=_crashing_worker,
        args=(store_path, effect_path, task_id, entered),
    )
    worker.start()
    assert entered.wait(timeout=4)
    worker.join(timeout=4)
    assert worker.exitcode == 23
    time.sleep(0.25)

    calls = 0

    def _must_not_run(value: str):
        nonlocal calls
        calls += 1
        return value

    executor = _executor_with_shared_store(
        store_path,
        _must_not_run,
        lease_ttl_s=0.2,
        wait_timeout_s=1.0,
    )
    step = _run_shared(executor, TaskId(UUID(task_id)))

    assert step.success is False
    assert step.result.error_type == "indeterminate_side_effect"
    assert step.result.output["retry_safe"] is False
    assert calls == 0
    assert (tmp_path / "effect.log").read_text(encoding="utf-8").splitlines() == ["crashed:one"]


def test_committed_receipt_survives_failure_before_journal_step_append(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "effects.sqlite3"
    task_id = TaskId(uuid4())
    calls = 0

    def _handler(value: str):
        nonlocal calls
        calls += 1
        return {"value": value, "calls": calls}

    first = _executor_with_shared_store(
        store_path,
        _handler,
        journal=_FailingStepJournal(),
    )
    with pytest.raises(OSError, match="before journal append"):
        _run_shared(first, task_id)

    resumed = _executor_with_shared_store(store_path, _handler)
    step = _run_shared(resumed, task_id)

    assert step.success is True
    assert calls == 1
    assert "durable_effect_replay" in step.result.stderr_tags


def test_redis_backed_hosts_execute_once_and_share_result() -> None:
    client = _FakeRedis()
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def _handler(value: str):
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(timeout=2)
        return {"value": value, "calls": calls}

    first = _executor_with_backend(
        RedisEffectStore(client, key_prefix="test:effects:"),
        _handler,
    )
    second = _executor_with_backend(
        RedisEffectStore(client, key_prefix="test:effects:"),
        _handler,
    )
    task_id = TaskId(uuid4())
    results = []
    owner = threading.Thread(target=lambda: results.append(_run_shared(first, task_id)))
    duplicate = threading.Thread(target=lambda: results.append(_run_shared(second, task_id)))
    owner.start()
    assert started.wait(timeout=1)
    duplicate.start()
    time.sleep(0.1)
    release.set()
    owner.join(timeout=3)
    duplicate.join(timeout=3)

    assert len(results) == 2
    assert calls == 1
    assert all(step.success for step in results)
    assert sum("durable_effect_replay" in step.result.stderr_tags for step in results) == 1


def test_redis_fencing_rejects_stale_holder_after_takeover() -> None:
    client = _FakeRedis()
    first = RedisEffectStore(client, key_prefix="test:effects:")
    second = RedisEffectStore(client, key_prefix="test:effects:")
    claim_a = first.claim(
        effect_key="effect:read",
        task_id="task",
        step_id=1,
        sucker_id="read_tool",
        args_fingerprint="args",
        side_effecting=False,
        holder_id="host-a",
        lease_ttl_s=0.05,
        observed_durable_intent=False,
    )
    assert claim_a.kind == "execute"
    assert first.mark_started(
        effect_key="effect:read",
        holder_id="host-a",
        fencing_token=claim_a.fencing_token,
        call_id="call-a",
        lease_ttl_s=0.05,
    )
    time.sleep(0.08)
    claim_b = second.claim(
        effect_key="effect:read",
        task_id="task",
        step_id=1,
        sucker_id="read_tool",
        args_fingerprint="args",
        side_effecting=False,
        holder_id="host-b",
        lease_ttl_s=1,
        observed_durable_intent=False,
    )

    assert claim_b.kind == "execute"
    assert claim_b.fencing_token > claim_a.fencing_token
    assert (
        first.renew(
            effect_key="effect:read",
            holder_id="host-a",
            fencing_token=claim_a.fencing_token,
            lease_ttl_s=1,
        )
        is False
    )
    first.record_committed(effect_key="effect:read", step=_sample_step())
    receipt = second._read_receipt("effect:read")  # noqa: SLF001
    assert receipt is not None
    assert receipt["state"] == "claimed"
    assert receipt["holder_id"] == "host-b"


def test_stale_executor_cannot_publish_result_after_redis_takeover() -> None:
    client = _FakeRedis()
    first_store = RedisEffectStore(client, key_prefix="test:effects:")
    second_store = RedisEffectStore(client, key_prefix="test:effects:")
    journal = InMemoryJournal()
    first = ToolEffectReceiptIndex(
        journal,
        store=first_store,
        holder_id="host-a",
        lease_ttl_s=0.05,
        wait_timeout_s=0.1,
    )
    resolution = first.begin(
        task_id="task",
        step_id=1,
        sucker_id="shared_effect_tool",
        args={"value": "one"},
        side_effecting=False,
    )
    assert resolution.kind == "execute"
    time.sleep(0.18)
    takeover = second_store.claim(
        effect_key=resolution.key,
        task_id="task",
        step_id=1,
        sucker_id="shared_effect_tool",
        args_fingerprint=resolution.args_fingerprint,
        side_effecting=False,
        holder_id="host-b",
        lease_ttl_s=1,
        observed_durable_intent=False,
    )
    assert takeover.kind == "execute"

    with pytest.raises(EffectLeaseLost, match="before result commit"):
        first.finish(resolution, _sample_step())

    receipt = second_store._read_receipt(resolution.key)  # noqa: SLF001
    assert receipt is not None
    assert receipt["state"] == "claimed"
    assert receipt["holder_id"] == "host-b"


def test_redis_started_side_effect_fails_closed_after_host_loss() -> None:
    client = _FakeRedis()
    first = RedisEffectStore(client, key_prefix="test:effects:")
    second = RedisEffectStore(client, key_prefix="test:effects:")
    claim = first.claim(
        effect_key="effect:write",
        task_id="task",
        step_id=1,
        sucker_id="write_tool",
        args_fingerprint="args",
        side_effecting=True,
        holder_id="host-a",
        lease_ttl_s=0.05,
        observed_durable_intent=False,
    )
    assert first.mark_started(
        effect_key="effect:write",
        holder_id="host-a",
        fencing_token=claim.fencing_token,
        call_id="call-a",
        lease_ttl_s=0.05,
    )
    time.sleep(0.08)

    recovered = second.claim(
        effect_key="effect:write",
        task_id="task",
        step_id=1,
        sucker_id="write_tool",
        args_fingerprint="args",
        side_effecting=True,
        holder_id="host-b",
        lease_ttl_s=1,
        observed_durable_intent=False,
    )

    assert recovered.kind == "indeterminate"


@pytest.mark.parametrize("backend", ["sqlite", "redis"])
def test_operator_can_authorize_one_fenced_retry(
    backend: str,
    tmp_path: Path,
) -> None:
    store = (
        SQLiteEffectStore(tmp_path / "effects.sqlite3")
        if backend == "sqlite"
        else RedisEffectStore(_FakeRedis(), key_prefix="test:effects:")
    )
    claim = store.claim(
        effect_key="effect:payment",
        task_id="task",
        step_id=1,
        sucker_id="payment_tool",
        args_fingerprint="args",
        side_effecting=True,
        holder_id="host-a",
        lease_ttl_s=1,
        observed_durable_intent=False,
    )
    assert store.mark_started(
        effect_key="effect:payment",
        holder_id="host-a",
        fencing_token=claim.fencing_token,
        call_id="call-a",
        lease_ttl_s=1,
    )
    store.finish_failed(
        effect_key="effect:payment",
        holder_id="host-a",
        fencing_token=claim.fencing_token,
        side_effecting=True,
        reason="payment provider timed out after request submission",
    )

    receipts = store.list_receipts(state="indeterminate")
    assert len(receipts) == 1
    assert receipts[0].effect_key == "effect:payment"
    assert (
        store.authorize_retry(
            effect_key="effect:payment",
            expected_fencing_token=claim.fencing_token + 1,
            actor="operator",
            reason="provider confirms no payment was created",
        )
        is False
    )
    assert store.authorize_retry(
        effect_key="effect:payment",
        expected_fencing_token=claim.fencing_token,
        actor="operator",
        reason="provider confirms no payment was created",
    )
    assert (
        store.authorize_retry(
            effect_key="effect:payment",
            expected_fencing_token=claim.fencing_token,
            actor="operator",
            reason="duplicate authorization must not be accepted",
        )
        is False
    )
    authorized = store.list_receipts(state="retry_authorized")
    assert len(authorized) == 1
    assert authorized[0].holder_id == "operator"

    retry_store = (
        RedisEffectStore(store.client, key_prefix="test:effects:")
        if isinstance(store, RedisEffectStore)
        else store
    )
    retry = retry_store.claim(
        effect_key="effect:payment",
        task_id="task",
        step_id=1,
        sucker_id="payment_tool",
        args_fingerprint="args",
        side_effecting=True,
        holder_id="host-b",
        lease_ttl_s=1,
        observed_durable_intent=True,
    )
    assert retry.kind == "execute"
    assert retry.fencing_token > claim.fencing_token


def test_tool_effect_reconciliation_api_is_admin_fenced_and_audited(
    tmp_path: Path,
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from runtime.sensing.gateway.observability_router import (
        create_observability_router,
    )

    class _Identity:
        def __init__(
            self,
            actor_id: str,
            roles: tuple[str, ...],
            *,
            scopes: tuple[str, ...] = (),
        ) -> None:
            self.actor_id = actor_id
            self.roles = roles
            self.metadata = {
                "tenant_id": f"tenant-{actor_id}",
                "scopes": list(scopes),
            }

    class _Identities:
        def verify_api_key(self, token: str):
            if token == "admin-token":
                return _Identity(
                    "admin-user",
                    ("admin",),
                    scopes=("global:admin",),
                )
            if token == "user-token":
                return _Identity("regular-user", ("user",))
            return None

    store = SQLiteEffectStore(tmp_path / "effects.sqlite3")
    claim = store.claim(
        effect_key="effect:payment",
        task_id="task",
        step_id=1,
        sucker_id="payment_tool",
        args_fingerprint="args",
        side_effecting=True,
        holder_id="host-a",
        lease_ttl_s=1,
        observed_durable_intent=False,
    )
    store.finish_failed(
        effect_key="effect:payment",
        holder_id="host-a",
        fencing_token=claim.fencing_token,
        side_effecting=True,
        reason="unknown provider outcome",
    )
    journal = JSONLJournal(tmp_path / "events.jsonl")
    app = FastAPI()
    app.include_router(
        create_observability_router(
            journal=journal,
            registry=SkillRegistry(),
            effect_store=store,
            identity_store=_Identities(),
            require_auth=True,
        )
    )
    client = TestClient(app)

    assert client.get("/api/tool-effects").status_code == 401
    forbidden_list = client.get(
        "/api/tool-effects?state=indeterminate",
        headers={"Authorization": "Bearer user-token"},
    )
    assert forbidden_list.status_code == 403
    admin_listed = client.get(
        "/api/tool-effects?state=indeterminate",
        headers={"Authorization": "Bearer admin-token"},
    )
    assert admin_listed.status_code == 403
    admin_listed = client.get(
        "/api/tool-effects?state=indeterminate&cross_tenant=true",
        headers={"Authorization": "Bearer admin-token"},
    )
    assert admin_listed.status_code == 200
    assert admin_listed.json()["global_control_plane"] is True
    assert admin_listed.json()["state_counts"]["indeterminate"] == 1
    assert admin_listed.json()["can_authorize_retry"] is True
    body = {
        "confirm": "AUTHORIZE RETRY",
        "fencing_token": claim.fencing_token,
        "reason": "provider dashboard confirms no payment was created",
    }
    forbidden = client.post(
        "/api/tool-effects/effect:payment/authorize-retry",
        headers={"Authorization": "Bearer user-token"},
        json=body,
    )
    assert forbidden.status_code == 403
    stale = client.post(
        "/api/tool-effects/effect:payment/authorize-retry?cross_tenant=true",
        headers={"Authorization": "Bearer admin-token"},
        json={**body, "fencing_token": claim.fencing_token + 1},
    )
    assert stale.status_code == 409
    resolved = client.post(
        "/api/tool-effects/effect:payment/authorize-retry?cross_tenant=true",
        headers={"Authorization": "Bearer admin-token"},
        json=body,
    )
    assert resolved.status_code == 200
    assert resolved.json()["global_control_plane"] is True
    assert resolved.json()["state"] == "retry_authorized"
    events = journal.read_by_type("tool_effect_reconciliation")
    assert len(events) == 1
    assert events[0].actor == "admin-user"
    assert events[0].tenant_id == "tenant-admin-user"
    assert events[0].owner_actor_id == "admin-user"


def test_distributed_readiness_rejects_local_only_store(tmp_path: Path) -> None:
    from runtime.platform.observability.health import HealthRegistry, effect_store_check

    registry = HealthRegistry(parallel=False)
    registry.register(
        effect_store_check(
            SQLiteEffectStore(tmp_path / "effects.sqlite3"),
            require_distributed=True,
        )
    )

    result = registry.probe(kind="readiness")
    assert result["status"] == "fail"
    assert result["checks"][0]["metadata"]["shared_across_hosts"] is False


def test_tool_effect_config_requires_real_distributed_backend() -> None:
    from runtime.platform.config import ToolEffectsConfig

    with pytest.raises(ValueError, match="requires backend=redis"):
        ToolEffectsConfig(
            backend="auto",
            require_distributed=True,
        )
    config = ToolEffectsConfig(
        backend="redis",
        redis_url="redis://redis:6379/0",
        require_distributed=True,
    )
    assert config.backend == "redis"


def test_server_preserves_explicit_redis_store_and_reports_readiness(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path))
    client = _FakeRedis()
    store = RedisEffectStore(client, key_prefix="test:effects:")
    monkeypatch.setattr(
        RedisEffectStore,
        "from_url",
        classmethod(lambda cls, url, **kwargs: store),
    )

    from runtime.platform.config import AgentConfig, ToolEffectsConfig, build_from_config
    from runtime.platform.ui import create_app

    stack = build_from_config(
        AgentConfig(
            enable_web_skills=False,
            tool_effects=ToolEffectsConfig(
                backend="redis",
                redis_url="redis://redis:6379/0",
                require_distributed=True,
            ),
        )
    )
    app = create_app(
        journal=stack.journal,
        registry=stack.registry,
        stack=stack,
        tentacle_enabled=False,
    )

    assert stack.executor.effect_store is store
    readiness = app.state.health_registry.probe(kind="readiness")
    effect_status = next(item for item in readiness["checks"] if item["name"] == "tool_effects")
    assert effect_status["status"] == "pass"
    assert effect_status["metadata"] == {
        "backend": "redis",
        "shared_across_hosts": True,
    }


def test_server_wires_shared_store_even_when_main_journal_is_in_memory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path))

    from runtime.platform.config import AgentConfig, build_from_config
    from runtime.platform.ui import create_app

    stack = build_from_config(AgentConfig(enable_web_skills=False))
    assert isinstance(stack.journal, InMemoryJournal)

    create_app(
        journal=stack.journal,
        registry=stack.registry,
        stack=stack,
        tentacle_enabled=False,
    )

    assert (
        stack.executor._effect_store_path
        == (  # noqa: SLF001
            tmp_path / "tool_effects.sqlite3"
        ).resolve()
    )

