"""P1 regression: the complexity-routed model must reach the react loop.

Routing computes ``validated.model`` and the reflection fast path already
forwarded it, but the main react path dropped it: ``_drive_react`` (both the
``CerebrumRuntime`` method and the module function) had no ``model`` parameter,
so ``stream_react_loop`` fell back to ``planner.planner_model`` and smart
routing silently no-op'd on the primary interactive path. The model is now
threaded turn_lifecycle → method → module → ``stream_react_loop``.
"""

from __future__ import annotations

import asyncio
import inspect

import runtime.sensing.gateway.realtime_cerebrum as rc
from runtime.sensing.gateway.realtime_react_stream import (
    _drive_react as _module_drive_react,
)


def test_module_and_method_drive_react_accept_model() -> None:
    assert "model" in inspect.signature(_module_drive_react).parameters
    assert "model" in inspect.signature(rc.CerebrumRuntime._drive_react).parameters


def test_method_forwards_routed_model_to_module(monkeypatch) -> None:
    captured: dict = {}

    async def _stub(runtime, turn, log, emitter, intent, provider, agent, *, model=None):
        captured["model"] = model

    monkeypatch.setattr(rc, "_drive_react", _stub)
    asyncio.run(
        rc.CerebrumRuntime._drive_react(
            object(),  # self — untouched before delegation
            None,  # turn
            None,  # log
            None,  # emitter
            None,  # intent
            None,  # provider
            None,  # agent
            model="routed-model-x",
        )
    )

    assert captured["model"] == "routed-model-x"

