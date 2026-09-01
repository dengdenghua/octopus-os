"""Tests for the distributed checkpoint mirror (P3 cross-machine durability).

Covers:

* In-memory KV double — verifies put / get / list_tasks / forget
  semantics and the JSON serialisation contract.
* Final-answer auto-purge — last-write-wins payload + index removal.
* Fail-soft — a misbehaving client never raises out of any method.
* env-var build path returns None when the URL is empty / unset.
* react_loop integration — when ``ECHO_CHECKPOINT_MIRROR_URL`` is
  unset, the loop's ``_checkpoint_mirror()`` returns None (off).
"""

from __future__ import annotations

import json

import pytest
from runtime.core.cerebrum.checkpoint_mirror import (
    CHECKPOINT_KEY_PREFIX,
    TASKS_INDEX_KEY,
    CheckpointMirror,
    build_checkpoint_mirror_from_url,
)


class _FakeRedis:
    """Minimal in-memory KV store with the surface CheckpointMirror needs."""

    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}

    # set / get
    def set(self, key: str, value: str) -> None:
        self.kv[key] = value

    def get(self, key: str):
        return self.kv.get(key)

    # set membership
    def sadd(self, key: str, *members: str) -> int:
        s = self.sets.setdefault(key, set())
        before = len(s)
        s.update(members)
        return len(s) - before

    def srem(self, key: str, *members: str) -> int:
        s = self.sets.get(key)
        if not s:
            return 0
        removed = 0
        for m in members:
            if m in s:
                s.remove(m)
                removed += 1
        return removed

    def smembers(self, key: str):
        return list(self.sets.get(key, set()))

    def delete(self, *keys: str) -> int:
        n = 0
        for k in keys:
            if k in self.kv:
                del self.kv[k]
                n += 1
        return n


# ══════════════════════════════════════════════════════════════════
# put / get / list_tasks
# ══════════════════════════════════════════════════════════════════


class TestPutGet:
    def test_put_returns_true(self) -> None:
        m = CheckpointMirror(_FakeRedis())
        assert m.put("task-1", {"iteration_completed": 5}) is True

    def test_put_writes_value_and_indexes_task(self) -> None:
        backend = _FakeRedis()
        m = CheckpointMirror(backend)
        m.put("task-abc", {"iteration_completed": 3, "phase": "verify"})
        assert CHECKPOINT_KEY_PREFIX + "task-abc" in backend.kv
        assert "task-abc" in backend.sets[TASKS_INDEX_KEY]

    def test_get_round_trips(self) -> None:
        m = CheckpointMirror(_FakeRedis())
        payload = {"iteration_completed": 7, "task_id": "x"}
        m.put("x", payload)
        assert m.get("x") == payload

    def test_get_missing_returns_none(self) -> None:
        assert CheckpointMirror(_FakeRedis()).get("nope") is None

    def test_payload_is_valid_json(self) -> None:
        backend = _FakeRedis()
        m = CheckpointMirror(backend)
        m.put("t", {"a": 1, "b": [1, 2, 3]})
        raw = backend.kv[CHECKPOINT_KEY_PREFIX + "t"]
        assert json.loads(raw) == {"a": 1, "b": [1, 2, 3]}

    def test_blank_task_id_rejected(self) -> None:
        m = CheckpointMirror(_FakeRedis())
        assert m.put("", {"x": 1}) is False
        assert m.get("") is None


class TestListTasks:
    def test_empty_initially(self) -> None:
        assert CheckpointMirror(_FakeRedis()).list_tasks() == []

    def test_returns_all_active(self) -> None:
        m = CheckpointMirror(_FakeRedis())
        m.put("task-a", {"iteration_completed": 1})
        m.put("task-b", {"iteration_completed": 2})
        assert m.list_tasks() == ["task-a", "task-b"]

    def test_byte_members_decoded(self) -> None:
        backend = _FakeRedis()
        # Simulate what real redis returns (bytes).
        backend.sets[TASKS_INDEX_KEY] = {b"task-z"}
        m = CheckpointMirror(backend)
        assert m.list_tasks() == ["task-z"]


class TestFinalAnswerPurge:
    def test_final_removes_from_index(self) -> None:
        backend = _FakeRedis()
        m = CheckpointMirror(backend)
        m.put("done", {"iteration_completed": 10, "has_final_answer": False})
        assert "done" in backend.sets[TASKS_INDEX_KEY]
        m.put("done", {"iteration_completed": 11, "has_final_answer": True})
        # Payload still there (history), but index is clean.
        assert "done" not in backend.sets[TASKS_INDEX_KEY]
        assert CHECKPOINT_KEY_PREFIX + "done" in backend.kv

    def test_final_does_not_break_other_tasks(self) -> None:
        backend = _FakeRedis()
        m = CheckpointMirror(backend)
        m.put("alive", {"iteration_completed": 5})
        m.put("done", {"iteration_completed": 10, "has_final_answer": True})
        assert m.list_tasks() == ["alive"]


class TestForget:
    def test_forget_removes_payload_and_index(self) -> None:
        backend = _FakeRedis()
        m = CheckpointMirror(backend)
        m.put("gone", {"iteration_completed": 3})
        m.forget("gone")
        assert m.get("gone") is None
        assert "gone" not in backend.sets.get(TASKS_INDEX_KEY, set())

    def test_forget_blank_id_returns_false(self) -> None:
        assert CheckpointMirror(_FakeRedis()).forget("") is False


# ══════════════════════════════════════════════════════════════════
# Fail-soft
# ══════════════════════════════════════════════════════════════════


class _BoomRedis:
    def set(self, *a, **kw):
        raise RuntimeError("redis down")

    def get(self, *a, **kw):
        raise RuntimeError("redis down")

    def sadd(self, *a, **kw):
        raise RuntimeError("redis down")

    def srem(self, *a, **kw):
        raise RuntimeError("redis down")

    def smembers(self, *a, **kw):
        raise RuntimeError("redis down")

    def delete(self, *a, **kw):
        raise RuntimeError("redis down")


class TestFailSoft:
    def test_put_returns_false_when_client_raises(self) -> None:
        m = CheckpointMirror(_BoomRedis())
        assert m.put("x", {"i": 1}) is False

    def test_get_returns_none_when_client_raises(self) -> None:
        assert CheckpointMirror(_BoomRedis()).get("x") is None

    def test_list_returns_empty_when_client_raises(self) -> None:
        assert CheckpointMirror(_BoomRedis()).list_tasks() == []

    def test_forget_returns_false_when_client_raises(self) -> None:
        assert CheckpointMirror(_BoomRedis()).forget("x") is False

    def test_corrupted_get_payload_returns_none(self) -> None:
        backend = _FakeRedis()
        backend.kv[CHECKPOINT_KEY_PREFIX + "bad"] = "this is not valid json {{"
        assert CheckpointMirror(backend).get("bad") is None


# ══════════════════════════════════════════════════════════════════
# build_checkpoint_mirror_from_url — env-driven build
# ══════════════════════════════════════════════════════════════════


class TestBuildFromUrl:
    def test_blank_url_returns_none(self) -> None:
        assert build_checkpoint_mirror_from_url("") is None
        assert build_checkpoint_mirror_from_url("   ") is None

    def test_no_redis_package_returns_none(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Force the import inside build_checkpoint_mirror_from_url to fail.
        import sys

        original = sys.modules.get("redis")
        sys.modules["redis"] = None  # type: ignore[assignment]
        try:
            assert build_checkpoint_mirror_from_url("redis://localhost:6379/0") is None
        finally:
            if original is not None:
                sys.modules["redis"] = original
            else:
                sys.modules.pop("redis", None)


# ══════════════════════════════════════════════════════════════════
# react_loop integration — singleton off when env var unset
# ══════════════════════════════════════════════════════════════════


class TestReactLoopIntegration:
    def test_singleton_off_by_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("ECHO_CHECKPOINT_MIRROR_URL", raising=False)
        from runtime.core.cerebrum.react_loop import (
            _checkpoint_mirror,
            _reset_checkpoint_mirror_for_tests,
        )

        _reset_checkpoint_mirror_for_tests()
        assert _checkpoint_mirror() is None

    def test_singleton_off_for_blank_url(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ECHO_CHECKPOINT_MIRROR_URL", "   ")
        from runtime.core.cerebrum.react_loop import (
            _checkpoint_mirror,
            _reset_checkpoint_mirror_for_tests,
        )

        _reset_checkpoint_mirror_for_tests()
        assert _checkpoint_mirror() is None

    def test_mirror_call_swallows_when_off(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("ECHO_CHECKPOINT_MIRROR_URL", raising=False)
        from runtime.core.cerebrum.react_loop import (
            _mirror_checkpoint,
            _reset_checkpoint_mirror_for_tests,
        )

        _reset_checkpoint_mirror_for_tests()
        # Must not raise even when no mirror is configured.
        _mirror_checkpoint("task-x", {"iteration_completed": 1})
