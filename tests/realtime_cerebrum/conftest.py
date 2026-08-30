"""Shared fixtures for realtime cerebrum tests.

The autouse ``_patch_react_loop`` fixture replaces the real
``stream_react_loop`` with a deterministic fake that reads its
script from ``_helpers._SCRIPT``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    FastAPI = None  # type: ignore[assignment]
    TestClient = None  # type: ignore[assignment]

from tests.realtime_cerebrum import _helpers

pytestmark = pytest.mark.skipif(
    FastAPI is None, reason="fastapi required for realtime gateway tests"
)


@pytest.fixture(autouse=True)
def _patch_react_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the real stream_react_loop with a deterministic fake.

    The fake reads its script from a module-level list so individual
    tests can stage the event sequence they want. This is the cleanest
    seam the runtime exposes — substituting the planner keeps LLM,
    tool, and sandbox machinery out of the test.
    """
    import runtime.core.cerebrum.react_loop as rl

    def fake_stream(*args: Any, **kwargs: Any) -> Iterator[dict[str, Any]]:
        from runtime.platform.process.session import current_session

        _helpers._LAST_STREAM_ARGS.clear()
        _helpers._LAST_STREAM_ARGS.update({"args": args, "kwargs": kwargs})
        _helpers._LAST_STREAM_KWARGS.clear()
        _helpers._LAST_STREAM_KWARGS.update(kwargs)
        active_session = current_session()
        _helpers._LAST_SESSION.clear()
        _helpers._LAST_SESSION.update(
            {
                "thread_id": active_session.thread_id if active_session else None,
                "metadata": dict(active_session.metadata) if active_session else {},
            }
        )
        approval_provider = kwargs.get("approval_provider")
        # When _SCRIPT_POP_ONCE is True, consume the script on first call
        # so loop-back (auto-verification) doesn't replay events and
        # double-emit file changes.
        if _helpers._SCRIPT_POP_ONCE:
            script = _helpers._SCRIPT[:]
            _helpers._SCRIPT.clear()
        else:
            script = _helpers._SCRIPT[:]
        for event in script:
            if event.get("__approve__"):
                # Translate into an approval round-trip using the
                # injected provider (mirrors what the real loop does).
                from runtime.safety.approval.approval_gate import ApprovalRequest

                decision = approval_provider.request(
                    ApprovalRequest(
                        thread_id=kwargs.get("thread_id", ""),
                        tool_name=event.get("tool_name", "x"),
                        tool_call_id=event.get("tool_call_id", "c1"),
                    )
                )
                yield {
                    "type": "tool_end",
                    "tool_name": event.get("tool_name", "x"),
                    "tool_call_id": event.get("tool_call_id", "c1"),
                    "iteration": 1,
                    "status": "success" if decision.approved else "rejected",
                    "output_preview": decision.reason or ("ok" if decision.approved else "denied"),
                    "duration_ms": 1,
                }
                continue
            yield event

    monkeypatch.setattr(rl, "stream_react_loop", fake_stream)


@pytest.fixture()
def gateway(tmp_path: Path) -> Any:
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime
    from runtime.sensing.gateway.realtime_gateway import RealtimeGateway

    runtime = CerebrumRuntime(
        stack=object(),  # unused by the fake loop
        agent=object(),
        logs_root=str(tmp_path / "threads"),
    )
    gateway = RealtimeGateway(runtime=runtime, approval_timeout=5.0)
    app = FastAPI()
    app.include_router(gateway.router)
    with TestClient(app) as client:
        yield client, tmp_path / "threads"

