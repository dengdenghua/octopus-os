"""C4 regression: resume-request confirm/consume are atomic (no TOCTOU).

The state transitions used unconditional ``WHERE id = ?`` UPDATEs (and confirm
did its lookup + update in separate lock regions), so two racing callers could
both confirm or both consume the same request → a resume could execute twice.
The UPDATEs are now compare-and-set (``WHERE ... AND status ...``) with a
rowcount check, so the loser of the race gets ``None`` and does not re-act.
"""

from __future__ import annotations

from runtime.memory.diagnostics.trace_store import AgentTraceStore


def _store(tmp_path) -> AgentTraceStore:
    return AgentTraceStore(tmp_path / "trace.db")


def test_consume_is_single_shot(tmp_path) -> None:
    store = _store(tmp_path)
    rid = store.record_resume_request(thread_id="t1", checkpoint_id=1, intent={"x": 1})

    first = store.consume_resume_request(rid)
    second = store.consume_resume_request(rid)

    assert first is not None
    assert first["status"] == "consumed"
    assert second is None  # already consumed → no double-resume


def test_consume_unknown_id_returns_none(tmp_path) -> None:
    store = _store(tmp_path)
    assert store.consume_resume_request(999_999) is None


def test_confirm_is_single_shot(tmp_path) -> None:
    store = _store(tmp_path)
    store.record_resume_request(thread_id="t1", checkpoint_id=7, intent={"x": 1})

    first = store.confirm_resume_request(thread_id="t1", checkpoint_id=7)
    second = store.confirm_resume_request(thread_id="t1", checkpoint_id=7)

    assert first is not None
    assert first["status"] == "confirmed"
    assert second is None  # no longer pending → loser gets None


def test_consume_after_confirm_still_single_shot(tmp_path) -> None:
    store = _store(tmp_path)
    rid = store.record_resume_request(thread_id="t1", checkpoint_id=3, intent={"x": 1})
    store.confirm_resume_request(thread_id="t1", checkpoint_id=3)

    first = store.consume_resume_request(rid)
    second = store.consume_resume_request(rid)

    assert first is not None
    assert first["status"] == "consumed"
    assert second is None

