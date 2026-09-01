"""Fleet broadcast — fan one task out to many devices (stubbed)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from runtime.tentacle.fleet import broadcast


@dataclass
class _ToolResult:
    success: bool
    data: str = ""
    error_message: str = ""


@dataclass
class _Call:
    tool: str


class _Device:
    def __init__(self, tentacle_id: str, *, online: bool = True, fail: bool = False) -> None:
        self.tentacle_id = tentacle_id
        self.is_online = online
        self._fail = fail
        self.calls: list[_Call] = []

    async def execute(self, call: _Call) -> _ToolResult:
        self.calls.append(call)
        if self._fail:
            return _ToolResult(success=False, error_message="boom")
        return _ToolResult(success=True, data=f"did {call.tool}")


class _Pool:
    def __init__(self, devices: list[_Device]) -> None:
        self._by_id = {d.tentacle_id: d for d in devices}

    def get(self, tid: str) -> _Device | None:
        return self._by_id.get(tid)

    def all_online(self) -> list[_Device]:
        return [d for d in self._by_id.values() if d.is_online]


class _Coordinator:
    def __init__(self, devices: list[_Device], engine: Any) -> None:
        self.pool = _Pool(devices)
        self._decision_engine = engine


def _engine(calls: list[_Call]):
    async def _e(_task: str, _device: _Device) -> list[_Call]:
        return calls

    return _e


def _run(coord, *args, **kw):
    return asyncio.run(broadcast(coord, *args, **kw))


def test_broadcast_all_online_when_ids_none() -> None:
    devs = [_Device("d1"), _Device("d2"), _Device("d3", online=False)]
    coord = _Coordinator(devs, _engine([_Call("open_app")]))
    res = _run(coord, "打开微信")  # ids=None → only online devices
    assert res["total"] == 2  # d3 offline is not targeted
    assert res["succeeded"] == 2 and res["failed"] == 0
    assert res["ok"] is True
    assert {r["tentacle_id"] for r in res["results"]} == {"d1", "d2"}


def test_broadcast_explicit_ids_preserve_order_and_dedup() -> None:
    devs = [_Device("d1"), _Device("d2")]
    coord = _Coordinator(devs, _engine([_Call("tap")]))
    res = _run(coord, "x", ["d2", "d1", "d2"])  # duplicate d2 dropped, order kept
    assert [r["tentacle_id"] for r in res["results"]] == ["d2", "d1"]
    assert res["total"] == 2


def test_broadcast_failure_is_isolated() -> None:
    devs = [_Device("ok1"), _Device("bad", fail=True), _Device("ok2")]
    coord = _Coordinator(devs, _engine([_Call("a")]))
    res = _run(coord, "x", ["ok1", "bad", "ok2"])
    assert res["total"] == 3 and res["succeeded"] == 2 and res["failed"] == 1
    assert res["ok"] is False  # any failure flips batch ok
    by = {r["tentacle_id"]: r for r in res["results"]}
    assert by["ok1"]["ok"] is True and by["ok2"]["ok"] is True
    assert by["bad"]["ok"] is False


def test_broadcast_unknown_and_offline_become_failed_records() -> None:
    devs = [_Device("d1"), _Device("off", online=False)]
    coord = _Coordinator(devs, _engine([_Call("a")]))
    res = _run(coord, "x", ["d1", "off", "ghost"])
    by = {r["tentacle_id"]: r for r in res["results"]}
    assert by["d1"]["ok"] is True
    assert by["off"]["ok"] is False and "offline" in by["off"]["error"]
    assert by["ghost"]["ok"] is False and "not connected" in by["ghost"]["error"]
    assert res["succeeded"] == 1 and res["failed"] == 2


def test_broadcast_no_targets() -> None:
    coord = _Coordinator([], _engine([_Call("a")]))
    res = _run(coord, "x")  # nothing online
    assert res["total"] == 0 and res["ok"] is False and res["results"] == []


def test_broadcast_respects_concurrency_cap() -> None:
    # Track peak concurrent executions; with cap=2 it must never exceed 2.
    state = {"cur": 0, "peak": 0}

    class _SlowDevice(_Device):
        async def execute(self, call: _Call) -> _ToolResult:
            state["cur"] += 1
            state["peak"] = max(state["peak"], state["cur"])
            await asyncio.sleep(0.02)
            state["cur"] -= 1
            return _ToolResult(success=True, data="ok")

    devs = [_SlowDevice(f"d{i}") for i in range(6)]
    coord = _Coordinator(devs, _engine([_Call("a")]))
    res = _run(coord, "x", max_concurrency=2)
    assert res["total"] == 6 and res["succeeded"] == 6
    assert state["peak"] <= 2


def test_broadcast_no_engine_all_fail_gracefully() -> None:
    devs = [_Device("d1"), _Device("d2")]
    coord = _Coordinator(devs, engine=None)  # misconfigured coordinator
    res = _run(coord, "x", ["d1", "d2"])
    assert res["failed"] == 2 and res["succeeded"] == 0
    assert all("decision engine" in r["error"] for r in res["results"])

