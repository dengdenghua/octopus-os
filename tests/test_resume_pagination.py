"""thread/resume pagination.

Without params the response is byte-compatible with the old full
replay (plus additive totalTurns/hasMore fields). With ``limit`` the
newest window is returned; ``beforeTurnId`` pages backwards.
"""

from pathlib import Path

from runtime.memory.threads.event_log import EventLog
from runtime.protocol import Turn, TurnParams, TurnStatus


def _turn(i: int) -> Turn:
    return Turn(
        id=f"turn-{i}",
        threadId="th-1",
        status=TurnStatus.COMPLETED,
        params=TurnParams(threadId="th-1", input=[{"type": "text", "text": str(i)}]),
    )


def _turns(n: int) -> list[Turn]:
    return [_turn(i) for i in range(n)]


class TestPaginateTurns:
    def test_no_limit_returns_everything(self):
        turns = _turns(5)
        window, has_more = EventLog.paginate_turns(turns)
        assert window == turns
        assert has_more is False

    def test_limit_keeps_newest_window(self):
        turns = _turns(10)
        window, has_more = EventLog.paginate_turns(turns, limit=3)
        assert [t.id for t in window] == ["turn-7", "turn-8", "turn-9"]
        assert has_more is True

    def test_limit_larger_than_list_is_full_list(self):
        turns = _turns(3)
        window, has_more = EventLog.paginate_turns(turns, limit=10)
        assert window == turns
        assert has_more is False

    def test_cursor_pages_backwards(self):
        turns = _turns(10)
        first, _ = EventLog.paginate_turns(turns, limit=3)
        older, has_more = EventLog.paginate_turns(turns, limit=3, before_turn_id=first[0].id)
        assert [t.id for t in older] == ["turn-4", "turn-5", "turn-6"]
        assert has_more is True

    def test_cursor_reaches_the_beginning(self):
        turns = _turns(4)
        older, has_more = EventLog.paginate_turns(turns, limit=10, before_turn_id="turn-2")
        assert [t.id for t in older] == ["turn-0", "turn-1"]
        assert has_more is False

    def test_unknown_cursor_falls_back_to_full_list(self):
        turns = _turns(4)
        window, has_more = EventLog.paginate_turns(turns, limit=2, before_turn_id="nope")
        assert [t.id for t in window] == ["turn-2", "turn-3"]
        assert has_more is True

    def test_zero_or_negative_limit_means_unlimited(self):
        turns = _turns(4)
        for limit in (0, -1):
            window, has_more = EventLog.paginate_turns(turns, limit=limit)
            assert window == turns
            assert has_more is False


class TestEchoResumePagination:
    def _log_with_turns(self, tmp_path: Path, n: int) -> EventLog:
        log = EventLog(tmp_path / "th-1.jsonl")
        log.thread_started("th-1")
        for i in range(n):
            t = _turn(i)
            log.turn_started("th-1", t)
            log.turn_completed("th-1", t.id, TurnStatus.COMPLETED)
        return log

    def test_resume_default_is_full_and_flagged(self, tmp_path: Path) -> None:
        import asyncio

        from runtime.sensing.gateway.realtime_echo import EchoRuntime

        self._log_with_turns(tmp_path, 5)
        rt = EchoRuntime(logs_root=tmp_path)

        class _Emitter:
            actor_id = None

        out = asyncio.run(rt.handle_request("thread/resume", {"threadId": "th-1"}, _Emitter()))
        assert len(out["turns"]) == 5
        assert out["totalTurns"] == 5
        assert out["hasMore"] is False

    def test_resume_with_limit_and_cursor(self, tmp_path: Path) -> None:
        import asyncio

        from runtime.sensing.gateway.realtime_echo import EchoRuntime

        self._log_with_turns(tmp_path, 7)
        rt = EchoRuntime(logs_root=tmp_path)

        class _Emitter:
            actor_id = None

        first = asyncio.run(
            rt.handle_request("thread/resume", {"threadId": "th-1", "limit": 2}, _Emitter())
        )
        assert [t["id"] for t in first["turns"]] == ["turn-5", "turn-6"]
        assert first["totalTurns"] == 7
        assert first["hasMore"] is True

        older = asyncio.run(
            rt.handle_request(
                "thread/resume",
                {
                    "threadId": "th-1",
                    "limit": 2,
                    "beforeTurnId": first["turns"][0]["id"],
                },
                _Emitter(),
            )
        )
        assert [t["id"] for t in older["turns"]] == ["turn-3", "turn-4"]
        assert older["hasMore"] is True
