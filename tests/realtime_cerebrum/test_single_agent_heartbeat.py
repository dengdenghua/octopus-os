"""Single-agent turns emit ``turn/heartbeat`` during idle stretches.

Team turns get a keepalive from the team runner; a solo ReAct turn had
none, so a slow model or a silently-running tool was indistinguishable
from a wedged connection on the frontend (the stream-vitals classifier
would flag it "slow"). The consumer loop now emits a heartbeat whenever
the event queue stays idle past ``_SINGLE_AGENT_HEARTBEAT_INTERVAL_S``.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import pytest

from tests.realtime_cerebrum._helpers import drive as _drive


def _install_slow_stream(
    monkeypatch: pytest.MonkeyPatch, *, stall_s: float, interval_s: float
) -> None:
    import runtime.core.cerebrum.react_loop as rl
    import runtime.sensing.gateway._realtime_react_stream_drive as drive_rs

    # Fire the keepalive almost immediately so the test stays sub-second.
    # Patch the constant where the consumer loop actually reads it (the
    # drive module holds its own imported binding).
    monkeypatch.setattr(drive_rs, "_SINGLE_AGENT_HEARTBEAT_INTERVAL_S", interval_s)

    def slow_stream(*_args: Any, **_kwargs: Any) -> Iterator[dict[str, Any]]:
        # Stall before producing any event — the model "thinking" with no
        # deltas. The consumer should keep the connection warm meanwhile.
        time.sleep(stall_s)
        yield {"type": "text_delta", "delta": "done"}
        yield {"type": "react_completed"}

    monkeypatch.setattr(rl, "stream_react_loop", slow_stream)


def test_solo_turn_emits_heartbeat_during_idle_stretch(
    gateway: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = gateway
    _install_slow_stream(monkeypatch, stall_s=0.3, interval_s=0.05)

    with client.websocket_connect("/api/realtime") as ws:
        result = _drive(
            ws,
            params={
                "threadId": "th_hb_solo",
                "input": [{"type": "text", "text": "think hard"}],
                "approvalPolicy": "never",
            },
        )

    heartbeats = [n for n in result["notifications"] if n.method == "turn/heartbeat"]
    assert heartbeats, "expected a turn/heartbeat during the idle stretch"

    hb = heartbeats[0].params
    assert hb["threadId"] == "th_hb_solo"
    assert hb.get("turnId"), "heartbeat must carry the turn id"
    assert isinstance(hb["elapsedS"], (int, float))
    assert hb["elapsedS"] >= 0

    # The turn still completes normally once the model resumes.
    assert any(n.method == "turn/completed" for n in result["notifications"])


def test_fast_turn_emits_no_heartbeat(
    gateway: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A turn that streams promptly (stall well under the interval) should
    # never pay for a keepalive — heartbeats are strictly an idle signal.
    client, _ = gateway
    _install_slow_stream(monkeypatch, stall_s=0.0, interval_s=5.0)

    with client.websocket_connect("/api/realtime") as ws:
        result = _drive(
            ws,
            params={
                "threadId": "th_hb_fast",
                "input": [{"type": "text", "text": "hi"}],
                "approvalPolicy": "never",
            },
        )

    heartbeats = [n for n in result["notifications"] if n.method == "turn/heartbeat"]
    assert not heartbeats, "a promptly-streaming turn should emit no heartbeat"
    assert any(n.method == "turn/completed" for n in result["notifications"])

