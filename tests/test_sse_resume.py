"""SSE resume: the journal feeds honour ``Last-Event-ID``.

The frontend SSE client sends ``Last-Event-ID`` on every reconnect. The
endpoints (``/api/stream``, ``/api/files/stream``, ``/api/preview/stream``)
are thin wrappers around ``_iter_sse_frames`` — the production generator
that emits ``id:`` per event and replays journal events strictly after the
cursor. TestClient blocks on a never-ending SSE body, so we drive that
generator directly with a real journal and the same queue wiring the
endpoints use.
"""

from __future__ import annotations

import queue

from runtime.memory.journal import (
    FileOpEvent,
    InMemoryJournal,
    PreviewRefreshEvent,
    SubToolStartEvent,
)
from runtime.sensing.gateway._observability_progress_stream import (
    _iter_sse_frames,
)


def _collect_frames(
    journal,
    cursor: str | None,
    *,
    event_name: str | None = None,
    event_filter=None,
    catch_up: int = 0,
    queued: list | None = None,
) -> list[str]:
    """Run the production generator until the ``: connected`` marker (the
    live loop then blocks on the queue — we stop before that)."""
    q: queue.Queue = queue.Queue(maxsize=500)
    for ev in queued or []:
        q.put(ev)
    frames: list[str] = []
    for frame in _iter_sse_frames(
        journal,
        q,
        cursor,
        event_name=event_name,
        event_filter=event_filter,
        catch_up=catch_up,
    ):
        if frame.startswith(": connected"):
            break
        frames.append(frame)
    return frames


def _ids(frames: list[str]) -> list[str]:
    out: list[str] = []
    for f in frames:
        for line in f.splitlines():
            if line.startswith("id: "):
                out.append(line[4:])
    return out


def _make_journal(events) -> InMemoryJournal:
    j = InMemoryJournal()
    for ev in events:
        j.write(ev)
    return j


def test_replays_events_strictly_after_cursor() -> None:
    first = SubToolStartEvent(
        role_id="planner",
        tool_call_id="call_1",
        tool_name="web_search",
        iteration=1,
        args_preview="",
        parent_tool_use_id=None,
    )
    second = SubToolStartEvent(
        role_id="planner",
        tool_call_id="call_2",
        tool_name="web_search",
        iteration=2,
        args_preview="",
        parent_tool_use_id=None,
    )
    journal = _make_journal([first, second])

    frames = _collect_frames(journal, str(first.event_id))
    assert _ids(frames) == [str(second.event_id)]


def test_unknown_cursor_replays_nothing() -> None:
    ev = SubToolStartEvent(
        role_id="planner",
        tool_call_id="call_1",
        tool_name="web_search",
        iteration=1,
        args_preview="",
        parent_tool_use_id=None,
    )
    journal = _make_journal([ev])

    frames = _collect_frames(
        journal,
        "00000000-0000-0000-0000-000000000000",
    )
    assert _ids(frames) == []


def test_every_frame_carries_id_before_data() -> None:
    ev = SubToolStartEvent(
        role_id="planner",
        tool_call_id="call_1",
        tool_name="web_search",
        iteration=1,
        args_preview="",
        parent_tool_use_id=None,
    )
    journal = _make_journal([ev])

    frames = _collect_frames(journal, None, queued=[ev])
    assert len(frames) == 1
    lines = frames[0].splitlines()
    assert lines[0] == f"id: {ev.event_id}"
    assert lines[1].startswith("data: ")


def test_queued_events_are_not_duplicated_by_replay() -> None:
    """Events that landed between subscribe and generator start are both
    in the queue and in the journal — the resume must emit them once."""
    first = SubToolStartEvent(
        role_id="planner",
        tool_call_id="call_1",
        tool_name="web_search",
        iteration=1,
        args_preview="",
        parent_tool_use_id=None,
    )
    second = SubToolStartEvent(
        role_id="planner",
        tool_call_id="call_2",
        tool_name="web_search",
        iteration=2,
        args_preview="",
        parent_tool_use_id=None,
    )
    journal = _make_journal([first, second])

    frames = _collect_frames(journal, str(first.event_id), queued=[second])
    assert _ids(frames) == [str(second.event_id)]


def test_files_stream_catch_up_tail_on_first_connect() -> None:
    evs = [FileOpEvent(path=f"/a{i}.txt", action="write", sucker_id="s1") for i in range(3)]
    journal = _make_journal(evs)

    frames = _collect_frames(
        journal,
        None,
        event_name="file_op",
        event_filter=lambda e: isinstance(e, FileOpEvent),
        catch_up=2,
    )
    assert _ids(frames) == [str(evs[1].event_id), str(evs[2].event_id)]


def test_files_stream_resume_filters_by_type() -> None:
    file_ev = FileOpEvent(path="/a.txt", action="write", sucker_id="s1")
    other = SubToolStartEvent(
        role_id="planner",
        tool_call_id="call_1",
        tool_name="web_search",
        iteration=1,
        args_preview="",
        parent_tool_use_id=None,
    )
    journal = _make_journal([file_ev, other])

    frames = _collect_frames(
        journal,
        str(file_ev.event_id),
        event_name="file_op",
        event_filter=lambda e: isinstance(e, FileOpEvent),
    )
    # Only file_op events replay; the sub_tool event after the cursor is
    # filtered out, and the feed blocks on live — no extra frames.
    assert frames == []


def test_preview_stream_emits_named_event_with_id() -> None:
    ev = PreviewRefreshEvent(target="/", trigger_path="/a.txt", reason="write")
    journal = _make_journal([ev])

    frames = _collect_frames(
        journal,
        None,
        event_name="preview_refresh",
        event_filter=lambda e: isinstance(e, PreviewRefreshEvent),
        catch_up=5,
        queued=[ev],
    )
    assert len(frames) == 1
    assert frames[0].startswith("event: preview_refresh\n")
    assert f"id: {ev.event_id}" in frames[0]

