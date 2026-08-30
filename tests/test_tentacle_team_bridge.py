"""Team-task → device dispatch via the in-process tentacle bridge (stubbed)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from runtime.tentacle.team_bridge import (
    device_id_from_ref,
    get_active_coordinator,
    run_device_task,
    set_active_coordinator,
)


@dataclass
class _ToolResult:
    success: bool
    data: str = ""
    error_message: str = ""


@dataclass
class _Call:
    tool: str


class _Device:
    def __init__(self, *, online: bool = True, fail_at: int | None = None) -> None:
        self.is_online = online
        self._fail_at = fail_at
        self.calls: list[_Call] = []

    async def execute(self, call: _Call) -> _ToolResult:
        self.calls.append(call)
        idx = len(self.calls) - 1
        if self._fail_at is not None and idx >= self._fail_at:
            return _ToolResult(success=False, error_message="boom")
        return _ToolResult(success=True, data=f"did {call.tool}")


class _Coordinator:
    def __init__(self, device: _Device | None, engine: Any) -> None:
        self._device = device
        self._decision_engine = engine

        class _Pool:
            def get(self, _id: str) -> _Device | None:
                return device

        self.pool = _Pool()


def _engine(calls: list[_Call]):
    async def _e(_task: str, _device: _Device) -> list[_Call]:
        return calls

    return _e


def test_device_id_from_ref() -> None:
    assert device_id_from_ref("mobile_abc123") == "abc123"
    assert device_id_from_ref("plain") == "plain"


def test_active_coordinator_accessor() -> None:
    set_active_coordinator(None)
    assert get_active_coordinator() is None
    sentinel = object()
    set_active_coordinator(sentinel)
    assert get_active_coordinator() is sentinel
    set_active_coordinator(None)


def test_run_device_task_success() -> None:
    dev = _Device()
    coord = _Coordinator(dev, _engine([_Call("open_app"), _Call("tap")]))
    rec = asyncio.run(run_device_task(coord, "d1", "打开微信"))
    assert rec["ok"] is True
    assert "open_app" in rec["output"] and "tap" in rec["output"]
    assert len(dev.calls) == 2


def test_run_device_task_stops_on_failure() -> None:
    dev = _Device(fail_at=1)  # second call fails
    coord = _Coordinator(dev, _engine([_Call("a"), _Call("b"), _Call("c")]))
    rec = asyncio.run(run_device_task(coord, "d1", "x"))
    assert rec["ok"] is False
    assert len(dev.calls) == 2  # stopped after the failing one, never ran c


def test_run_device_task_offline() -> None:
    coord = _Coordinator(_Device(online=False), _engine([_Call("a")]))
    rec = asyncio.run(run_device_task(coord, "d1", "x"))
    assert rec["ok"] is False and "offline" in rec["error"]


def test_run_device_task_unknown_device() -> None:
    coord = _Coordinator(None, _engine([_Call("a")]))
    rec = asyncio.run(run_device_task(coord, "ghost", "x"))
    assert rec["ok"] is False and "not connected" in rec["error"]


def test_run_device_task_no_engine() -> None:
    coord = _Coordinator(_Device(), None)
    rec = asyncio.run(run_device_task(coord, "d1", "x"))
    assert rec["ok"] is False and "decision engine" in rec["error"]


def test_run_device_task_empty_plan() -> None:
    coord = _Coordinator(_Device(), _engine([]))  # planner returns nothing
    rec = asyncio.run(run_device_task(coord, "d1", "x"))
    assert rec["ok"] is False and "no device actions" in rec["error"]


def test_mobile_artifacts_shape() -> None:
    from runtime.sensing.gateway.team_tasks_router import _mobile_artifacts

    arts = _mobile_artifacts(
        [
            {"tentacle_id": "d1", "ok": True, "output": "✓ open_app: ok"},
            {"tentacle_id": "d2", "ok": False, "error": "offline"},
        ]
    )
    assert len(arts) == 2
    by = {a["device_id"]: a for a in arts}
    assert by["d1"]["type"] == "mobile_run" and by["d1"]["agent_id"] == "mobile_d1"
    assert by["d1"]["content"] == "✓ open_app: ok"
    assert by["d2"]["content"] == "offline" and by["d2"]["ok"] is False

