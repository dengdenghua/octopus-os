"""Tests for the LLM summariser and its wiring into CerebrumRuntime.

Two layers:

1. Pure summariser — feed it a stub ``ModelRouter`` that records the
   request and returns canned text. Verifies the prompt shape,
   transcript rendering, fallback on empty / error.
2. Runtime integration — drive ``CerebrumRuntime`` with ``compaction_policy``
   + a stub router; assert that after enough turns a ``turn_compacted``
   event lands in the log with LLM-provided text.
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
    FastAPI = None
    TestClient = None

from runtime.memory.threads.compaction import (
    CompactionPolicy,
    _default_summariser,
)
from runtime.memory.threads.event_log import EventLog
from runtime.memory.threads.llm_summariser import (
    LlmSummariserConfig,
    make_llm_summariser,
)
from runtime.protocol import (
    JsonRpcRequest,
    JsonRpcResponse,
    Notification,
    decode_message,
    encode_message,
)
from runtime.protocol.items import (
    AgentMessageItem,
    ErrorItem,
    McpToolCallItem,
    PlanItem,
    TodoEntry,
    TodoListItem,
    Turn,
    TurnStatus,
    UserMessageItem,
)
from runtime.sensing.model_router.models import (
    CostEntry,
    ModelRequest,
    ModelResponse,
    ModelRouter,
)

pytestmark = pytest.mark.skipif(
    FastAPI is None, reason="fastapi required for realtime integration tests"
)


# ── Test stubs ────────────────────────────────────────────────


class _RecordingRouter(ModelRouter):
    def __init__(self, reply_text: str = "• condensed summary") -> None:
        self.calls: list[ModelRequest] = []
        self.reply_text = reply_text
        self.next_error: Exception | None = None

    def call(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        if self.next_error is not None:
            raise self.next_error
        return ModelResponse(
            text=self.reply_text,
            input_tokens=50,
            output_tokens=20,
            cost=CostEntry(),
            model=request.model,
            provider="mock",
        )


def _turn(
    idx: int,
    thread_id: str = "th",
    user: str = "",
    agent: str = "",
) -> Turn:
    items: list = []
    if user:
        items.append(UserMessageItem(text=user))
    if agent:
        items.append(AgentMessageItem(text=agent))
    return Turn(
        id=f"trn_{idx:04d}",
        threadId=thread_id,
        status=TurnStatus.COMPLETED,
        items=items,
    )


# ── Pure summariser ───────────────────────────────────────────


class TestSummariserPure:
    def test_returns_llm_output(self) -> None:
        router = _RecordingRouter("• first · added X\n• second · ran Y")
        summariser = make_llm_summariser(router)
        text = summariser([_turn(i, user=f"ask {i}", agent=f"ans {i}") for i in range(3)])
        assert text == "• first · added X\n• second · ran Y"
        assert len(router.calls) == 1

    def test_prompt_includes_user_and_agent_text(self) -> None:
        router = _RecordingRouter()
        summariser = make_llm_summariser(router)
        summariser([_turn(0, user="please refactor auth", agent="did it")])
        req = router.calls[0]
        # The first message is the system prompt; the user message is
        # the rendered transcript.
        user_msg = next(m for m in req.messages if m.role == "user")
        assert "please refactor auth" in user_msg.content
        assert "did it" in user_msg.content

    def test_fallback_on_router_error(self) -> None:
        router = _RecordingRouter()
        router.next_error = RuntimeError("upstream 500")
        summariser = make_llm_summariser(router, fallback=_default_summariser)
        text = summariser([_turn(i, user=f"u{i}", agent=f"a{i}") for i in range(3)])
        # Falls back to mechanical — includes the turn counts header.
        assert "[turn 1/3" in text

    def test_empty_response_uses_fallback(self) -> None:
        router = _RecordingRouter(reply_text="")
        summariser = make_llm_summariser(router, fallback=_default_summariser)
        text = summariser([_turn(0, user="x", agent="y")])
        assert "[turn 1/1" in text

    def test_config_is_passed_to_router(self) -> None:
        router = _RecordingRouter()
        summariser = make_llm_summariser(
            router,
            config=LlmSummariserConfig(
                model="test-model",
                max_tokens=42,
                temperature=0.7,
            ),
        )
        summariser([_turn(0, user="u", agent="a")])
        req = router.calls[0]
        assert req.model == "test-model"
        assert req.max_tokens == 42
        assert req.temperature == 0.7

    def test_transcript_renders_operational_item_types(self) -> None:
        router = _RecordingRouter()
        summariser = make_llm_summariser(router)
        turn = Turn(
            threadId="th",
            status=TurnStatus.COMPLETED,
            items=[
                UserMessageItem(text="use mcp"),
                McpToolCallItem(server="fs", tool="read", result={"path": "a.txt"}),
                PlanItem(text="inspect then edit"),
                TodoListItem(plan=[TodoEntry(title="patch", status="completed")]),
                ErrorItem(message="boom"),
            ],
        )
        summariser([turn])
        user_msg = next(m for m in router.calls[0].messages if m.role == "user")
        assert "mcp:fs/read" in user_msg.content
        assert "inspect then edit" in user_msg.content
        assert "completed: patch" in user_msg.content
        assert "boom" in user_msg.content

    def test_transcript_budget_respected(self) -> None:
        router = _RecordingRouter()
        summariser = make_llm_summariser(
            router,
            config=LlmSummariserConfig(transcript_char_budget=200),
        )
        turns = [_turn(i, user="x" * 300, agent="y" * 300) for i in range(5)]
        summariser(turns)
        user_msg = next(m for m in router.calls[0].messages if m.role == "user")
        assert len(user_msg.content) <= 400  # budget + last-section slack


# ── Runtime integration ───────────────────────────────────────


_SCRIPT: list[dict[str, Any]] = []


def _set_script(events: list[dict[str, Any]]) -> None:
    _SCRIPT.clear()
    _SCRIPT.extend(events)


@pytest.fixture(autouse=True)
def _patch_react_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    import runtime.core.cerebrum.react_loop as rl

    def fake_stream(*args: Any, **kwargs: Any) -> Iterator[dict[str, Any]]:
        yield from _SCRIPT[:]

    monkeypatch.setattr(rl, "stream_react_loop", fake_stream)


@pytest.fixture()
def compaction_gateway(tmp_path: Path):
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime
    from runtime.sensing.gateway.realtime_gateway import RealtimeGateway

    router = _RecordingRouter(reply_text="• compacted by llm")
    policy = CompactionPolicy(trigger_at=3, keep_recent=1)
    runtime = CerebrumRuntime(
        stack=object(),
        agent=object(),
        logs_root=str(tmp_path / "threads"),
        compaction_policy=policy,
        summary_router=router,
    )
    gateway = RealtimeGateway(runtime=runtime, approval_timeout=5.0)
    app = FastAPI()
    app.include_router(gateway.router)
    with TestClient(app) as client:
        yield client, tmp_path / "threads", router


def _drive_simple(ws: Any, params: dict[str, Any]) -> None:
    ws.send_text(encode_message(JsonRpcRequest(id=1, method="turn/start", params=params)))
    while True:
        msg = decode_message(ws.receive_text())
        if isinstance(msg, JsonRpcRequest):
            # Shouldn't happen in these scripts — reply to unblock.
            ws.send_text(encode_message(JsonRpcResponse(id=msg.id, result={"action": "decline"})))
            continue
        if isinstance(msg, Notification):
            continue
        if isinstance(msg, JsonRpcResponse) and msg.id == 1:
            return


def test_compaction_fires_after_threshold_and_uses_llm(
    compaction_gateway: Any,
) -> None:
    client, logs_root, router = compaction_gateway
    _set_script(
        [
            {"type": "text_delta", "delta": "ok"},
            {"type": "react_completed"},
        ]
    )
    # 3 turns on the same thread → policy trigger_at=3 → compaction fires
    # on the 3rd completion.
    for i in range(3):
        with client.websocket_connect("/api/realtime") as ws:
            _drive_simple(
                ws,
                {
                    "threadId": "th-llm",
                    "input": [{"type": "text", "text": f"turn {i}"}],
                    "approvalPolicy": "never",
                },
            )

    # LLM summariser was called once.
    assert len(router.calls) >= 1
    # The log has a turn_compacted event with the LLM's text.
    log = EventLog(logs_root / "th-llm.jsonl")
    compacted = [e for e in log.iter_events() if e.event == "turn_compacted"]
    assert len(compacted) == 1
    summary = compacted[0].payload["summaryTurn"]
    # The summary item carries the LLM's canned reply.
    assert "compacted by llm" in summary["items"][0]["text"]


def test_no_compaction_without_policy(tmp_path: Path) -> None:
    """Sanity: when ``compaction_policy`` is None, no event is ever
    emitted even after many turns."""
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime
    from runtime.sensing.gateway.realtime_gateway import RealtimeGateway

    runtime = CerebrumRuntime(
        stack=object(),
        agent=object(),
        logs_root=str(tmp_path / "threads"),
    )
    gateway = RealtimeGateway(runtime=runtime, approval_timeout=5.0)
    app = FastAPI()
    app.include_router(gateway.router)
    with TestClient(app) as client:
        _set_script(
            [
                {"type": "text_delta", "delta": "ok"},
                {"type": "react_completed"},
            ]
        )
        for i in range(5):
            with client.websocket_connect("/api/realtime") as ws:
                _drive_simple(
                    ws,
                    {
                        "threadId": "th-nocomp",
                        "input": [{"type": "text", "text": f"t{i}"}],
                        "approvalPolicy": "never",
                    },
                )

    log = EventLog(tmp_path / "threads" / "th-nocomp.jsonl")
    compacted = [e for e in log.iter_events() if e.event == "turn_compacted"]
    assert compacted == []
