"""Lossless chunk-run storage packing (dsh ``chunk-rows``).

Streamed reply fragments and sub-agent prose chunks pack runs of
consecutive same-shape events into ONE JSONL storage row; readers
expand rows back to the exact original events (same ids, timestamps,
deltas — token boundaries are data). Tests cover the pure codec, the
JSONL write/read integration, fail-loud decode, and the rollback knob.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from runtime.memory.journal import (
    AssistantChunkEvent,
    InMemoryJournal,
    JSONLJournal,
)
from runtime.memory.journal._chunk_rows import (
    MIN_RUN,
    classify_chunk,
    continues_chunk_run,
    expand_chunk_row,
    is_chunk_row,
    pack_chunk_row,
)
from runtime.memory.journal._journal_models import UserMessageEvent


def _chunk(delta: str, *, iteration: int = 1, kind: str = "text-delta") -> AssistantChunkEvent:
    return AssistantChunkEvent(
        event_id=uuid4(),
        iteration=iteration,
        kind=kind,
        delta=delta,
        task_id=None,
    )


def _entry(event: AssistantChunkEvent) -> dict:
    entry = classify_chunk(event)
    assert entry is not None
    return entry


class TestClassify:
    def test_assistant_chunk_is_packable(self) -> None:
        entry = classify_chunk(_chunk("hi"))
        assert entry is not None
        assert entry["event_type"] == "assistant/chunk"
        assert entry["delta"] == "hi"

    def test_sub_text_delta_is_packable(self) -> None:
        from runtime.memory.journal import SubTextDeltaEvent

        entry = classify_chunk(SubTextDeltaEvent(role_id="r", round=1, delta="chunk"))
        assert entry is not None
        assert entry["event_type"] == "sub_text_delta"
        assert entry["extra"] == {
            "role_id": "r",
            "round": 1,
            "parent_tool_use_id": None,
            "session_id": "",
        }

    def test_sub_text_delta_session_id_preserved_and_split(self) -> None:
        """Different sessions must never pack into the same row, and a
        packed row must round-trip ``session_id`` losslessly."""
        from runtime.memory.journal import SubTextDeltaEvent

        def _sd(delta: str, session_id: str) -> dict:
            entry = classify_chunk(
                SubTextDeltaEvent(
                    event_id=uuid4(),
                    role_id="r",
                    round=1,
                    delta=delta,
                    session_id=session_id,
                )
            )
            assert entry is not None
            return entry

        # Different session_id breaks the run — they must not merge.
        assert not continues_chunk_run(_sd("one", "abc123def456"), _sd("two", "ZZZ999other"))
        # Same session_id continues.
        assert continues_chunk_run(_sd("one", "abc123def456"), _sd("two", "abc123def456"))
        # Same-session run packs and expands losslessly (>= MIN_RUN=3).
        packed = pack_chunk_row(
            [
                _sd("one", "abc123def456"),
                _sd("two", "abc123def456"),
                _sd("three", "abc123def456"),
            ]
        )
        expanded = expand_chunk_row(packed)
        assert [m["delta"] for m in expanded] == ["one", "two", "three"]
        assert all(m["session_id"] == "abc123def456" for m in expanded)

    def test_non_chunk_event_is_verbatim(self) -> None:
        assert classify_chunk(UserMessageEvent(text="hi")) is None

    def test_empty_delta_is_verbatim(self) -> None:
        assert classify_chunk(_chunk("")) is None


class TestContinues:
    def test_same_envelope_increasing_ts_continues(self) -> None:
        a, b = _entry(_chunk("a")), _entry(_chunk("b"))
        assert continues_chunk_run(a, b)

    def test_different_iteration_breaks_run(self) -> None:
        a, b = _entry(_chunk("a", iteration=1)), _entry(_chunk("b", iteration=2))
        assert not continues_chunk_run(a, b)

    def test_different_kind_breaks_run(self) -> None:
        a, b = _entry(_chunk("a", kind="text-delta")), _entry(_chunk("b", kind="reasoning"))
        assert not continues_chunk_run(a, b)

    def test_older_timestamp_breaks_run(self) -> None:
        a, b = _entry(_chunk("b")), _entry(_chunk("a"))
        # b was created after a, so continuing from b with a's entry is stale.
        assert not continues_chunk_run(b, a)


class TestCodec:
    def test_pack_expand_is_byte_identical(self) -> None:
        events = [_chunk(f"c{i}") for i in range(MIN_RUN + 2)]
        row = pack_chunk_row([_entry(e) for e in events])
        assert is_chunk_row(row)
        assert row["count"] == len(events)

        expanded = expand_chunk_row(row)
        assert len(expanded) == len(events)
        for original, restored in zip(events, expanded, strict=True):
            assert json.loads(original.model_dump_json()) == restored

    def test_expand_fails_loud_on_malformed_row(self) -> None:
        events = [_chunk(f"c{i}") for i in range(MIN_RUN)]
        row = pack_chunk_row([_entry(e) for e in events])
        row["members"] = row["members"][:-1]  # count now lies
        with pytest.raises(ValueError):
            expand_chunk_row(row)

    def test_expand_rejects_unknown_packed_type(self) -> None:
        events = [_chunk(f"c{i}") for i in range(MIN_RUN)]
        row = pack_chunk_row([_entry(e) for e in events])
        row["event_type"] = "mystery/event"
        with pytest.raises(ValueError):
            expand_chunk_row(row)


class TestJSONLIntegration:
    def test_long_run_packs_to_one_line_and_reads_back(self, tmp_path: Path) -> None:
        journal = JSONLJournal(tmp_path / "j.jsonl")
        events = [_chunk(f"c{i}") for i in range(8)]
        for event in events:
            journal.write(event)
        journal.write(UserMessageEvent(text="flush"))

        lines = (tmp_path / "j.jsonl").read_text().strip().splitlines()
        assert len(lines) == 2
        assert "__chunk_row__" in lines[0]

        restored = journal.read_all()
        assert len(restored) == 9
        for original, event in zip(events, restored[: len(events)], strict=True):
            assert isinstance(event, AssistantChunkEvent)
            assert event.event_id == original.event_id
            assert event.delta == original.delta
            assert event.iteration == original.iteration
            assert event.ts == original.ts
        assert isinstance(restored[-1], UserMessageEvent)
        assert len(journal) == 9

    def test_fresh_reader_expands_rows(self, tmp_path: Path) -> None:
        path = tmp_path / "j.jsonl"
        journal = JSONLJournal(path)
        events = [_chunk(f"c{i}") for i in range(6)]
        for event in events:
            journal.write(event)
        journal.write(UserMessageEvent(text="flush"))

        fresh = JSONLJournal(path)
        restored = fresh.read_all()
        assert [e.event_id for e in restored[:6]] == [e.event_id for e in events]
        assert [e.delta for e in restored[:6]] == [f"c{i}" for i in range(6)]

    def test_short_run_stays_verbatim(self, tmp_path: Path) -> None:
        journal = JSONLJournal(tmp_path / "j.jsonl")
        for delta in ("a", "b"):
            journal.write(_chunk(delta))
        journal.write(UserMessageEvent(text="flush"))

        lines = (tmp_path / "j.jsonl").read_text().strip().splitlines()
        assert len(lines) == 3
        assert all("__chunk_row__" not in line for line in lines)
        assert len(journal.read_all()) == 3

    def test_run_break_flushes_and_resumes(self, tmp_path: Path) -> None:
        journal = JSONLJournal(tmp_path / "j.jsonl")
        for delta in ("a", "b", "c"):
            journal.write(_chunk(delta))
        journal.write(UserMessageEvent(text="break"))
        for delta in ("d", "e", "f"):
            journal.write(_chunk(delta))
        journal.write(UserMessageEvent(text="flush"))

        lines = (tmp_path / "j.jsonl").read_text().strip().splitlines()
        assert len(lines) == 4  # row + break + row + flush
        assert sum("__chunk_row__" in line for line in lines) == 2

        restored = journal.read_all()
        assert [e.delta for e in restored if e.event_type == "assistant/chunk"] == [
            "a",
            "b",
            "c",
            "d",
            "e",
            "f",
        ]

    def test_env_knob_disables_packing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ECHO_JOURNAL_CHUNK_PACKING", "0")
        journal = JSONLJournal(tmp_path / "j.jsonl")
        for delta in ("a", "b", "c"):
            journal.write(_chunk(delta))
        journal.write(UserMessageEvent(text="flush"))

        lines = (tmp_path / "j.jsonl").read_text().strip().splitlines()
        assert len(lines) == 4
        assert all("__chunk_row__" not in line for line in lines)
        assert len(journal.read_all()) == 4

    def test_packing_is_per_journal_state(self, tmp_path: Path) -> None:
        # Two journals over the same file must both expand each other's rows.
        path = tmp_path / "j.jsonl"
        a = JSONLJournal(path)
        b = JSONLJournal(path)
        for delta in ("a", "b", "c"):
            a.write(_chunk(delta))
        b.write(UserMessageEvent(text="flush"))
        assert len(a.read_all()) == 4
        assert len(b.read_all()) == 4

    def test_sub_text_delta_packs_into_rows(self, tmp_path: Path) -> None:
        from runtime.memory.journal import SubTextDeltaEvent

        journal = JSONLJournal(tmp_path / "j.jsonl")
        for delta in ("a", "b", "c", "d"):
            journal.write(SubTextDeltaEvent(role_id="r", round=1, delta=delta))
        journal.write(UserMessageEvent(text="flush"))

        lines = (tmp_path / "j.jsonl").read_text().strip().splitlines()
        assert len(lines) == 2
        restored = journal.read_all()
        assert [e.delta for e in restored[:4]] == ["a", "b", "c", "d"]
        assert all(isinstance(e, SubTextDeltaEvent) for e in restored[:4])


def test_inmemory_journal_never_packs() -> None:
    journal = InMemoryJournal()
    for delta in ("a", "b", "c"):
        journal.write(_chunk(delta))
    assert len(journal) == 3
    assert all(e.event_type == "assistant/chunk" for e in journal.read_all())


class TestReasoningLanePacking:
    def test_reasoning_delta_packs_into_rows(self, tmp_path: Path) -> None:
        journal = JSONLJournal(tmp_path / "j.jsonl")
        for delta in ("a", "b", "c", "d"):
            journal.write(_chunk(delta, kind="reasoning-delta"))
        journal.write(UserMessageEvent(text="flush"))

        lines = (tmp_path / "j.jsonl").read_text().strip().splitlines()
        assert len(lines) == 2
        restored = journal.read_all()
        assert [e.delta for e in restored[:4]] == ["a", "b", "c", "d"]
        assert all(e.kind == "reasoning-delta" for e in restored[:4])

    def test_mixed_kind_breaks_run(self, tmp_path: Path) -> None:
        journal = JSONLJournal(tmp_path / "j.jsonl")
        for delta in ("a", "b", "c"):
            journal.write(_chunk(delta, kind="reasoning-delta"))
        for delta in ("d", "e", "f"):
            journal.write(_chunk(delta, kind="text-delta"))
        journal.write(UserMessageEvent(text="flush"))

        lines = (tmp_path / "j.jsonl").read_text().strip().splitlines()
        assert len(lines) == 3  # reasoning row + text row + flush
        restored = journal.read_all()
        assert [e.kind for e in restored[:-1]] == [
            "reasoning-delta",
            "reasoning-delta",
            "reasoning-delta",
            "text-delta",
            "text-delta",
            "text-delta",
        ]

