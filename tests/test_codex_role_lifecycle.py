"""Background roles must terminate promptly and retain cancellation outcomes."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from runtime.execution.codex_backend import role_runner as mod
from runtime.execution.codex_backend.types import Notification, RequestTimeoutError


@pytest.fixture
def runner(monkeypatch):
    session = SimpleNamespace(
        start=AsyncMock(),
        close=AsyncMock(),
        interrupt=AsyncMock(),
        next_notification=AsyncMock(side_effect=AssertionError("polled after terminal event")),
    )

    @asynccontextmanager
    async def lifecycle(*args, **kwargs):
        try:
            yield SimpleNamespace(session=session)
        finally:
            await session.close()

    monkeypatch.setattr(mod, "codex_execution_lifecycle", lifecycle)
    monkeypatch.setattr(mod, "build_codex_role_request", lambda *a, **kw: (object(), None, None))
    return session


@pytest.mark.asyncio
async def test_fatal_error_finishes_without_waiting_for_another_notification(runner):
    runner.next_notification.side_effect = [
        Notification("error", {"message": "provider rejected request", "willRetry": False}),
        AssertionError("fatal error must end the role immediately"),
    ]
    published = []
    result = await mod.run_agent_role(None, None, "work", event_callback=published.append)
    assert result.success is False
    assert result.status == "failed"
    assert result.events[-1]["type"] == "react_error"
    assert result.events[-1]["message"] == "provider rejected request"
    assert published == list(result.events)
    runner.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_retryable_error_does_not_end_the_role(runner):
    runner.next_notification.side_effect = [
        Notification("error", {"willRetry": True}),
        Notification("turn/completed", {"turn": {"status": "completed"}}),
    ]
    result = await mod.run_agent_role(None, None, "work")
    assert result.success is True
    assert result.status == "completed"
    assert result.events[0]["type"] == "commentary_delta"
    runner.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_stop_survives_interrupt_transport_failure(runner):
    runner.interrupt.side_effect = RequestTimeoutError("interrupt acknowledgement lost")
    published = []
    result = await mod.run_agent_role(
        None, None, "work", is_interrupted=lambda: True, event_callback=published.append
    )
    assert result.success is False
    assert result.status == "interrupted"
    assert published[-1]["type"] == "react_cancelled"
    assert result.events == tuple(published)
    runner.next_notification.assert_not_awaited()
    runner.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_timeout_interrupts_and_publishes_a_terminal_event(runner, monkeypatch):
    monkeypatch.setattr(mod, "_timeout_s", lambda context: 0)
    published = []
    result = await mod.run_agent_role(None, None, "work", event_callback=published.append)
    assert result.success is False
    assert result.status == "timeout"
    assert published[-1]["type"] == "react_cancelled"
    assert published[-1]["reason"] == "codex_timeout"
    assert result.events == tuple(published)
    runner.interrupt.assert_awaited_once()
    runner.close.assert_awaited_once()
