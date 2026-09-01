"""Session-reference resolver tests — dsh ``@dsh-session-reference`` port."""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.execution.subagents.sessions import SubagentSessionStore
from runtime.execution.tool_engine.session_reference import (
    MAX_REFERENCES,
    SessionReferenceError,
    SessionReferenceInput,
    SessionReferenceRecord,
    SessionReferenceResolver,
    candidate_rank,
    normalize_references,
    render_reference_prompt,
)
from runtime.safety.approval.cancellation import (
    CancellationSource,
    scoped_cancellation,
)


def _surface(session_id: str, body: str = "") -> list[dict]:
    return [
        {
            "type": "user/message",
            "data": {
                "source": {"kind": "user"},
                "content": [{"type": "text", "text": body or f"prompt-{session_id}"}],
            },
        },
        {
            "type": "assistant/message",
            "data": {
                "message": {"content": [{"type": "text", "text": f"answer-{session_id}"}]},
            },
        },
    ]


def _records() -> list[SessionReferenceRecord]:
    return [
        SessionReferenceRecord(session_id="abc123", label="Researcher", cwd="/repo"),
        SessionReferenceRecord(session_id="def456", label="Writer", cwd="/other"),
        SessionReferenceRecord(session_id="ghi789", label="Researcher two", cwd="/repo"),
        SessionReferenceRecord(session_id="target-id", label="Self", cwd="/repo"),
    ]


# ─── candidate ranking ─────────────────────────────────────────────────────


def test_candidate_rank_order() -> None:
    assert candidate_rank("/repo", "/repo") == 0
    assert candidate_rank(None, "/repo") == 1
    assert candidate_rank("/other", "/repo") == 2


def test_list_candidates_excludes_self_and_ranks_by_cwd() -> None:
    resolver = SessionReferenceResolver()
    out = resolver.list_candidates(target_id="target-id", sessions=_records(), target_cwd="/repo")
    ids = [c.session_id for c in out]
    # Same-cwd sessions rank before the different-cwd one; self excluded.
    assert "target-id" not in ids
    assert ids[0] == "abc123"
    assert ids[1] == "ghi789"
    assert ids[2] == "def456"


def test_list_candidates_query_filters() -> None:
    resolver = SessionReferenceResolver()
    out = resolver.list_candidates(
        target_id="target-id",
        sessions=_records(),
        query="writer",
    )
    assert [c.session_id for c in out] == ["def456"]


def test_list_candidates_query_matches_cwd() -> None:
    resolver = SessionReferenceResolver()
    out = resolver.list_candidates(
        target_id="target-id",
        sessions=_records(),
        query="repo",
    )
    ids = [c.session_id for c in out]
    assert "abc123" in ids
    assert "def456" not in ids


def test_list_candidates_limit() -> None:
    resolver = SessionReferenceResolver()
    out = resolver.list_candidates(target_id="x", sessions=_records(), limit=2)
    assert len(out) == 2


def test_list_candidates_invalid_limit() -> None:
    resolver = SessionReferenceResolver()
    with pytest.raises(SessionReferenceError):
        resolver.list_candidates(target_id="x", sessions=_records(), limit=0)


def test_list_candidates_cancelled_raises() -> None:
    resolver = SessionReferenceResolver()
    source = CancellationSource()
    source.cancel(reason="autocomplete torn down")
    with scoped_cancellation(source.token), pytest.raises(SessionReferenceError) as exc:
        resolver.list_candidates(target_id="x", sessions=_records())
    assert exc.value.code == "SESSION_REFERENCE_CANCELLED"
    assert "cancelled" in str(exc.value)


def test_list_candidates_uncancelled_unchanged() -> None:
    resolver = SessionReferenceResolver()
    source = CancellationSource()
    with scoped_cancellation(source.token):
        out = resolver.list_candidates(
            target_id="target-id",
            sessions=_records(),
            target_cwd="/repo",
        )
    assert [c.session_id for c in out] == ["abc123", "ghi789", "def456"]


# ─── normalize_references ──────────────────────────────────────────────────


def test_normalize_references_dedupes_and_caps() -> None:
    refs = [
        {"session_id": "a", "label": "A"},
        {"session_id": "b"},
        {"session_id": "a"},  # dup collapses
    ]
    out = normalize_references("self", refs, max_references=3)
    assert [r.session_id for r in out] == ["a", "b"]


def test_normalize_references_rejects_self() -> None:
    with pytest.raises(SessionReferenceError) as exc:
        normalize_references("self", [{"session_id": "self"}], max_references=3)
    assert exc.value.code == "SESSION_REFERENCE_SELF_REFERENCE"


def test_normalize_references_too_many() -> None:
    refs = [{"session_id": f"s{i}"} for i in range(4)]
    with pytest.raises(SessionReferenceError) as exc:
        normalize_references("self", refs, max_references=3)
    assert exc.value.code == "SESSION_REFERENCE_TOO_MANY"


def test_normalize_references_invalid() -> None:
    with pytest.raises(SessionReferenceError):
        normalize_references("self", [{"label": "no id"}], max_references=3)
    with pytest.raises(SessionReferenceError):
        normalize_references("self", ["not-an-object"], max_references=3)


# ─── prepare ───────────────────────────────────────────────────────────────


def test_prepare_no_references_returns_content_only() -> None:
    resolver = SessionReferenceResolver()
    result = resolver.prepare(
        target_id="t",
        content=[{"type": "text", "text": "hi"}],
        references=[],
        read_surface=lambda sid: _surface(sid),
    )
    assert result.additional_context is None


def test_prepare_renders_referenced_frame() -> None:
    resolver = SessionReferenceResolver()
    result = resolver.prepare(
        target_id="t",
        content=[{"type": "text", "text": "hi"}],
        references=[SessionReferenceInput(session_id="s1", label="Patents")],
        read_surface=lambda sid: _surface(sid),
    )
    assert result.content == [{"type": "text", "text": "hi"}]
    ctx = result.additional_context
    assert ctx is not None
    assert ctx["source"]["kind"] == "session-reference"
    assert ctx["source"]["form"] == "recall"
    assert ctx["source"]["version"] == 1
    assert ctx["source"]["references"][0]["sessionId"] == "s1"
    assert ctx["source"]["references"][0]["label"] == "Patents"
    text = ctx["content"][0]["text"]
    assert "## Referenced sessions" in text
    assert "<referenced-sessions>" in text
    assert "</referenced-sessions>" in text
    assert "answer-s1" in text


def test_prepare_escapes_tag_characters() -> None:
    resolver = SessionReferenceResolver()
    result = resolver.prepare(
        target_id="t",
        content=[],
        references=[SessionReferenceInput(session_id="s1")],
        read_surface=lambda sid: [
            {
                "type": "user/message",
                "data": {
                    "source": {"kind": "user"},
                    "content": [{"type": "text", "text": "<script>alert(1)</script>"}],
                },
            }
        ],
    )
    text = result.additional_context["content"][0]["text"]
    # No literal '<' survives in the serialized JSON (tag-safe).
    assert "<script>" not in text


def test_prepare_self_reference_rejected() -> None:
    resolver = SessionReferenceResolver()
    with pytest.raises(SessionReferenceError) as exc:
        resolver.prepare(
            target_id="me",
            content=[],
            references=[SessionReferenceInput(session_id="me")],
            read_surface=lambda sid: _surface(sid),
        )
    assert exc.value.code == "SESSION_REFERENCE_SELF_REFERENCE"


def test_prepare_budget_exceeded() -> None:
    resolver = SessionReferenceResolver(max_reference_bytes=16)
    with pytest.raises(SessionReferenceError) as exc:
        resolver.prepare(
            target_id="t",
            content=[],
            references=[SessionReferenceInput(session_id="s1")],
            read_surface=lambda sid: _surface(sid),
        )
    assert exc.value.code == "SESSION_REFERENCE_BUDGET_EXCEEDED"


def test_prepare_read_failure() -> None:
    resolver = SessionReferenceResolver()

    def _boom(sid: str) -> list[dict]:
        raise RuntimeError("store down")

    with pytest.raises(SessionReferenceError) as exc:
        resolver.prepare(
            target_id="t",
            content=[],
            references=[SessionReferenceInput(session_id="s1")],
            read_surface=_boom,
        )
    assert exc.value.code == "SESSION_REFERENCE_READ_FAILED"


def test_prepare_cancelled_before_reads_raises() -> None:
    resolver = SessionReferenceResolver()
    source = CancellationSource()
    source.cancel(reason="client disconnected")

    def _never_called(sid: str) -> list[dict]:
        raise AssertionError("read_surface must not run when cancelled")

    with scoped_cancellation(source.token), pytest.raises(SessionReferenceError) as exc:
        resolver.prepare(
            target_id="t",
            content=[],
            references=[SessionReferenceInput(session_id="s1")],
            read_surface=_never_called,
        )
    assert exc.value.code == "SESSION_REFERENCE_CANCELLED"


def test_prepare_cancelled_during_reads_raises() -> None:
    """A token tripped mid-loop must stop the remaining reads (sync
    equivalent of dsh ``settleWithCancellation`` racing the batch)."""
    resolver = SessionReferenceResolver()
    source = CancellationSource()
    calls: list[str] = []

    def _cancelling_surface(sid: str) -> list[dict]:
        calls.append(sid)
        if len(calls) == 1:
            source.cancel(reason="request aborted")
        return _surface(sid)

    with scoped_cancellation(source.token), pytest.raises(SessionReferenceError) as exc:
        resolver.prepare(
            target_id="t",
            content=[],
            references=[
                SessionReferenceInput(session_id="s1"),
                SessionReferenceInput(session_id="s2"),
            ],
            read_surface=_cancelling_surface,
        )
    assert exc.value.code == "SESSION_REFERENCE_CANCELLED"
    assert calls == ["s1"]


def test_prepare_cancelled_after_reads_raises() -> None:
    """The post-read assertion mirrors dsh's second ``assertNotCancelled``
    before rendering the frame."""
    resolver = SessionReferenceResolver()
    source = CancellationSource()

    def _surface_then_cancel(sid: str) -> list[dict]:
        result = _surface(sid)
        source.cancel(reason="turn aborted")
        return result

    with scoped_cancellation(source.token), pytest.raises(SessionReferenceError) as exc:
        resolver.prepare(
            target_id="t",
            content=[],
            references=[SessionReferenceInput(session_id="s1")],
            read_surface=_surface_then_cancel,
        )
    assert exc.value.code == "SESSION_REFERENCE_CANCELLED"


def test_invalid_config_rejected() -> None:
    with pytest.raises(SessionReferenceError):
        SessionReferenceResolver(max_references=0)
    with pytest.raises(SessionReferenceError):
        SessionReferenceResolver(max_references=MAX_REFERENCES + 1)
    with pytest.raises(SessionReferenceError):
        SessionReferenceResolver(candidate_limit=-1)


def test_render_reference_prompt_shape() -> None:
    from runtime.execution.tool_engine.session_projection import ReferencedSessionData

    data = ReferencedSessionData(
        session_id="s1",
        label="L",
        cwd=None,
        captured_through_seq=None,
        conversation=[{"role": "user", "text": "x"}],
    )
    text = render_reference_prompt([data])
    assert text.startswith("## Referenced sessions")
    assert text.endswith("</referenced-sessions>")


# ─── subagent store adapter ────────────────────────────────────────────────


def test_store_surface_events_and_candidates(tmp_path: Path) -> None:
    store = SubagentSessionStore(base_dir=tmp_path / "sessions")
    s1 = store.create(agent_id="researcher", thread_id="t1")
    s2 = store.create(agent_id="writer", thread_id="t2")
    s3 = store.create(agent_id="coder", thread_id="t1")  # same thread as s1
    store.append_turn(s1.session_id, prompt="p1", output="o1", success=True)

    events = store.surface_events(s1.session_id)
    assert events and events[0]["type"] == "user/message"
    assert events[1]["type"] == "assistant/message"
    assert store.surface_events("missing") == []

    # Candidates are scoped to the calling thread: same-thread sessions are
    # discoverable, cross-thread sessions stay private (cross-tenant IDOR
    # guard — a thread must not enumerate another thread's subagent sessions).
    candidates = store.list_reference_candidates(target_id="t1")
    ids = [c["sessionId"] for c in candidates]
    assert s1.session_id in ids
    assert s3.session_id in ids
    assert s2.session_id not in ids
    candidates_t2 = store.list_reference_candidates(target_id="t2")
    ids_t2 = [c["sessionId"] for c in candidates_t2]
    assert s2.session_id in ids_t2
    assert s1.session_id not in ids_t2


def test_extract_session_mentions() -> None:
    from runtime.execution.tool_engine.session_reference import (
        extract_session_mentions,
    )

    sid1 = "00112233445566778899aabbccddeeff"
    sid2 = "aabbccddeeff00112233445566778899"
    assert extract_session_mentions("") == []
    assert extract_session_mentions("no mentions here") == []
    assert extract_session_mentions("@session:nothex") == []
    assert extract_session_mentions(f"see @session:{sid1} and @subagent:{sid2}") == [sid1, sid2]
    # Dedupe keeps first-mention order.
    assert extract_session_mentions(f"a @session:{sid1} b @session:{sid1} c") == [sid1]
    # Non-matching (invalid length) tokens are ignored.
    assert extract_session_mentions(f"@session:{sid1}x") == []


def test_resolve_mentions_empty_prompt_and_no_mentions() -> None:
    resolver = SessionReferenceResolver()

    def read_surface(_sid: str) -> list[dict]:
        return []

    out = resolver.resolve_mentions("", target_id="target", read_surface=read_surface)
    assert out.content == ""
    assert out.additional_context is None
    out = resolver.resolve_mentions("plain prompt", target_id="target", read_surface=read_surface)
    assert out.content == "plain prompt"
    assert out.additional_context is None


def test_resolve_mentions_projects_and_strips() -> None:
    resolver = SessionReferenceResolver()
    sid = "00112233445566778899aabbccddeeff"

    def read_surface(session_id: str) -> list[dict]:
        return [
            {
                "type": "user/message",
                "data": {
                    "source": {"kind": "user"},
                    "content": [{"type": "text", "text": f"prompt-{session_id}"}],
                },
            }
        ]

    out = resolver.resolve_mentions(
        f"research @session:{sid} deeper",
        target_id="target",
        read_surface=read_surface,
    )
    assert out.content == "research deeper"
    assert out.additional_context is not None
    rendered = out.additional_context["content"][0]["text"]
    assert "<referenced-sessions>" in rendered
    assert sid in rendered
    assert "prompt-00112233445566778899aabbccddeeff" in rendered


def test_resolve_mentions_stale_and_self_skipped() -> None:
    resolver = SessionReferenceResolver()
    known = "00112233445566778899aabbccddeeff"
    stale = "ffffffffffffffffffffffffffffffff"
    record = SessionReferenceRecord(session_id=known, label="researcher")

    def read_surface(session_id: str) -> list[dict]:
        return [
            {
                "type": "user/message",
                "data": {
                    "source": {"kind": "user"},
                    "content": [{"type": "text", "text": f"prompt-{session_id}"}],
                },
            }
        ]

    target = "55aa55aa55aa55aa55aa55aa55aa55aa"
    # Self-reference (target) is skipped, stale is skipped, known resolves.
    out = resolver.resolve_mentions(
        f"@session:{target} @subagent:{stale} @session:{known}",
        target_id=target,
        read_surface=read_surface,
        sessions=[record],
    )
    assert out.content == ""
    assert out.additional_context is not None

    # All-stale → no context, prompt stripped.
    out = resolver.resolve_mentions(
        f"@session:{stale}",
        target_id="target",
        read_surface=read_surface,
        sessions=[record],
    )
    assert out.additional_context is None
    assert out.content == ""


def test_resolve_mentions_caps_at_max_references() -> None:
    resolver = SessionReferenceResolver(max_references=2)
    sid1 = "00112233445566778899aabbccddeeff"
    sid2 = "aabbccddeeff00112233445566778899"
    sid3 = "ffeeddccbbaa99887766554433221100"
    seen: list[str] = []

    def read_surface(session_id: str) -> list[dict]:
        seen.append(session_id)
        return [
            {
                "type": "user/message",
                "data": {
                    "source": {"kind": "user"},
                    "content": [{"type": "text", "text": f"prompt-{session_id}"}],
                },
            }
        ]

    out = resolver.resolve_mentions(
        f"@session:{sid1} @session:{sid2} @session:{sid3}",
        target_id="target",
        read_surface=read_surface,
    )
    assert out.additional_context is not None
    assert sid1 in seen and sid2 in seen and sid3 not in seen


def test_store_resolve_session_mentions(tmp_path: Path) -> None:
    store = SubagentSessionStore(base_dir=tmp_path / "sessions")
    s1 = store.create(agent_id="researcher", thread_id="t1")
    store.append_turn(s1.session_id, prompt="p1", output="o1", success=True)

    # Same-thread mention resolves with the referenced frame.
    out = store.resolve_session_mentions(
        f"use @session:{s1.session_id}",
        target_id="t1",
    )
    assert out.content == "use"
    assert out.additional_context is not None
    rendered = out.additional_context["content"][0]["text"]
    assert "<referenced-sessions>" in rendered

    # Cross-thread mention of s1 from thread t2 is treated as unknown: the
    # token is stripped but no frame is injected (thread-private sessions).
    out = store.resolve_session_mentions(
        f"use @session:{s1.session_id}",
        target_id="t2",
    )
    assert out.additional_context is None
    assert out.content == "use"

    # Stale mention against a store with no matching session → no context.
    stale = "ffffffffffffffffffffffffffffffff"
    out = store.resolve_session_mentions(f"@session:{stale}", target_id="t2")
    assert out.additional_context is None
    assert out.content == ""

