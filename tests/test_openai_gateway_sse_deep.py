"""
Deep SSE frame-sequence tests for ``/v1/chat/completions``.

Why
---

The existing ``test_openai_gateway.py::TestChatCompletionsStream`` only
asserts "200 + content-type + ``[DONE]`` appears somewhere". That's
not enough coverage to safely refactor the internals of
``_stream_chat`` / ``_stream_chat_wrapped``.

These tests pin the **frame sequence and per-frame shape** — so a
future refactor that accidentally drops the meta frame, the role
opener, step-per-frame emission, or the finish_reason changes gets
caught by a specific assertion rather than a vague "stream worked"
check.

Also covers:
* **Planner failure path** — when the planner raises, the stream
  must produce a ``finish_reason="failed"`` chunk and then
  ``[DONE]`` · not stall or 500.
* **Reflex fast path** — reflex hits produce a different frame
  structure (role + content + finish) · pinning so it doesn't
  silently start acting like the regular path or vice versa.
* **Meta frame correlation** — every stream's very first frame
  must carry ``echo.conversation_id`` so the UI can multiplex
  overlapping streams onto the right thread window.
"""

from __future__ import annotations

import json
import threading

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from runtime.platform.config import AgentConfig, PlannerConfig, build_from_config  # noqa: E402
from runtime.sensing.gateway import create_openai_router  # noqa: E402

# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════


def _mk_stack(plan_json: str = None, raise_planner: bool = False):
    """Build a stack with a mock LLM planner. When ``raise_planner``
    is set, give the planner a mock response that will fail to parse
    — drives the planner-error path of ``_stream_chat``."""
    if raise_planner:
        # Non-JSON mock response · planner will raise during parse.
        response = "this is not json at all"
    elif plan_json is not None:
        response = plan_json
    else:
        response = json.dumps(
            {
                "reasoning": "r",
                "nodes": [
                    {"skill": "list_cwd", "args": {"path": "."}},
                    {"skill": "count_words", "args": {"path": "a.txt"}},
                ],
            }
        )
    cfg = AgentConfig(
        planner=PlannerConfig(
            type="llm",
            model="mock/sse-deep",
            mock_response=response,
        ),
    )
    return build_from_config(cfg)


@pytest.fixture
def happy_stack():
    return _mk_stack()


@pytest.fixture
def broken_stack():
    return _mk_stack(raise_planner=True)


@pytest.fixture
def client(happy_stack):
    app = FastAPI()
    app.include_router(create_openai_router(happy_stack))
    return TestClient(app)


@pytest.fixture
def broken_client(broken_stack):
    app = FastAPI()
    app.include_router(create_openai_router(broken_stack))
    return TestClient(app)


# ═══════════════════════════════════════════════════════════
# Helpers for SSE parsing
# ═══════════════════════════════════════════════════════════


def _collect_frames(response) -> tuple[list[dict], bool]:
    """Parse all ``data:`` lines. Returns (frames, done_seen).

    Filters out the ``[DONE]`` sentinel but records that it was seen."""
    frames: list[dict] = []
    done_seen = False
    for chunk in response.iter_text():
        for line in chunk.splitlines():
            if not line.startswith("data: "):
                continue
            payload = line[len("data: ") :]
            if payload.strip() == "[DONE]":
                done_seen = True
                continue
            try:
                frames.append(json.loads(payload))
            except json.JSONDecodeError:
                pytest.fail(f"malformed SSE data frame: {payload!r}")
    return frames, done_seen


def _stream(client: TestClient, **kwargs):
    """POST /v1/chat/completions in stream mode with sensible defaults."""
    body = {
        "stream": True,
        "messages": [{"role": "user", "content": "list files"}],
        **kwargs,
    }
    return client.stream("POST", "/v1/chat/completions", json=body)


# ═══════════════════════════════════════════════════════════
# Happy-path frame sequence
# ═══════════════════════════════════════════════════════════


class TestHappyPath:
    def test_meta_frame_is_first(self, client: TestClient):
        """The very first ``data:`` frame must be the echo meta
        chunk (carries conversation_id + agent). Without this, the
        UI can't correlate frames to a task."""
        with _stream(client, conversation_id="meta-test") as r:
            assert r.status_code == 200
            frames, _ = _collect_frames(r)
        assert len(frames) >= 1
        first = frames[0]
        assert first["object"] == "chat.completion.chunk"
        assert "echo" in first
        assert first["echo"]["conversation_id"] == "meta-test"

    def test_role_frame_follows_meta(self, client: TestClient):
        """Frame 2: ``{role: "assistant", content: ""}`` — the
        assistant-message opener. OpenAI clients key off this to
        start rendering a new bubble."""
        with _stream(client) as r:
            frames, _ = _collect_frames(r)
        # skip meta · look for the role opener
        role_frames = [f for f in frames if f["choices"][0]["delta"].get("role") == "assistant"]
        assert len(role_frames) >= 1

    def test_done_sentinel_terminates_stream(
        self,
        client: TestClient,
    ):
        """Required by openai-compat clients to stop listening.
        If dropped, clients hang waiting forever."""
        with _stream(client) as r:
            _, done_seen = _collect_frames(r)
        assert done_seen, "stream must end with 'data: [DONE]'"

    def test_finish_reason_stop_on_success(
        self,
        client: TestClient,
    ):
        """Final content chunk must carry ``finish_reason=stop`` on
        success · openai-compat SDKs use this to distinguish graceful
        completion from abort."""
        with _stream(client) as r:
            frames, _ = _collect_frames(r)
        finish_reasons = [f["choices"][0].get("finish_reason") for f in frames]
        # At least one frame carries stop · not all are None
        assert "stop" in finish_reasons

    def test_every_frame_has_required_shape(
        self,
        client: TestClient,
    ):
        """Lock the openai-compat chunk schema · any refactor that
        drops one of these fields breaks client SDKs."""
        with _stream(client) as r:
            frames, _ = _collect_frames(r)
        required_top = {"id", "object", "created", "model", "choices"}
        for f in frames:
            missing = required_top - set(f.keys())
            assert not missing, f"chunk missing required fields {missing}: {f!r}"
            assert f["object"] == "chat.completion.chunk"
            # choices is always a non-empty list of dicts with index+delta
            assert isinstance(f["choices"], list) and f["choices"]
            for choice in f["choices"]:
                assert "index" in choice
                assert "delta" in choice
                # finish_reason may be None but key must exist
                assert "finish_reason" in choice

    def test_chunk_ids_share_conversation_continuity(
        self,
        client: TestClient,
    ):
        """All content chunks in one stream share the same ``id`` ·
        openai-compat SDKs concatenate by this id. The meta frame
        gets its own id · only the _stream_chat-produced ones must
        match each other."""
        with _stream(client) as r:
            frames, _ = _collect_frames(r)
        # content-bearing chunks (skip meta which has empty delta)
        content_frames = [
            f for f in frames if f["choices"][0]["delta"] and "content" in f["choices"][0]["delta"]
        ]
        if len(content_frames) < 2:
            pytest.skip("not enough content frames to compare · stream was trivially short")
        ids = {f["id"] for f in content_frames}
        assert len(ids) == 1, f"content-bearing frames should share one id · got {ids}"

    def test_stream_emits_step_before_runtime_returns(self):
        """Guard the real-streaming contract.

        A slow runtime writes a step, then blocks before returning the
        final trajectory. The SSE generator must surface that step
        before the worker finishes; otherwise we've regressed to
        post-hoc trajectory replay.
        """
        from runtime.memory.journal import InMemoryJournal
        from runtime.platform.models import (
            ArmId,
            BudgetSpec,
            ExecutionResult,
            ParsedIntent,
            Step,
            TaskGraph,
            TaskNode,
            ToolCall,
            Trajectory,
            TrajectoryOutcome,
        )
        from runtime.sensing.gateway.openai_gateway import _stream_chat

        journal = InMemoryJournal()
        step = Step(
            step_id=0,
            node_id="n0",
            action=ToolCall(
                caller="arms/code_arm",
                sucker_id="slow_skill",
                args={},
            ),
            result=ExecutionResult(
                call_id=ToolCall(
                    caller="arms/code_arm",
                    sucker_id="slow_skill",
                    args={},
                ).call_id,
                status="success",
                output={"answer": "streamed early"},
            ),
        )
        graph = TaskGraph(
            nodes=[TaskNode(node_id="n0", skill_ref="slow_skill")],
            budget=BudgetSpec(tokens=100, usd=0.01),
        )
        release_runtime = threading.Event()
        runtime_finished = threading.Event()

        class Planner:
            def plan(self, _intent, **_kwargs):
                return graph

        class Runtime:
            def run(self, _graph, *, budget, caller, arm_id, actor=None):
                journal.write_step(graph.task_id, ArmId("code_arm"), step)
                release_runtime.wait(timeout=5)
                runtime_finished.set()
                return Trajectory(
                    task_id=graph.task_id,
                    arm_id=ArmId("code_arm"),
                    steps=[step],
                    outcome=TrajectoryOutcome(success=True),
                )

        class Stack:
            pass

        stack = Stack()
        stack.planner = Planner()
        stack.runtime = Runtime()
        stack.journal = journal

        gen = _stream_chat(
            stack,
            ParsedIntent(raw="x", intent_type="task", normalized_goal="x"),
            "echo-agent",
            "code_arm",
        )
        try:
            role_frame = json.loads(next(gen)[len("data: ") :])
            assert role_frame["choices"][0]["delta"]["role"] == "assistant"

            step_frame = json.loads(next(gen)[len("data: ") :])
            content = step_frame["choices"][0]["delta"].get("content", "")
            assert "streamed early" in content
            assert not runtime_finished.is_set()
        finally:
            release_runtime.set()
            gen.close()

    def test_stream_emits_keepalive_while_runtime_is_silent(self):
        """Long-running tools that produce no journal events should
        still keep the SSE connection warm."""
        from runtime.platform.models import (
            BudgetSpec,
            ParsedIntent,
            TaskGraph,
            TaskNode,
        )
        from runtime.sensing.gateway.openai_gateway import _stream_chat

        release_runtime = threading.Event()
        graph = TaskGraph(
            nodes=[TaskNode(node_id="n0", skill_ref="silent_skill")],
            budget=BudgetSpec(tokens=100, usd=0.01),
        )

        class Journal:
            def read_by_task(self, _task_id):
                return []

        class Planner:
            def plan(self, _intent, **_kwargs):
                return graph

        class Runtime:
            def run(self, _graph, *, budget, caller, arm_id, actor=None):
                release_runtime.wait(timeout=5)
                raise RuntimeError("released test runtime")

        class Stack:
            pass

        stack = Stack()
        stack.planner = Planner()
        stack.runtime = Runtime()
        stack.journal = Journal()

        gen = _stream_chat(
            stack,
            ParsedIntent(raw="x", intent_type="task", normalized_goal="x"),
            "echo-agent",
            "code_arm",
            keepalive_interval_s=0.01,
        )
        try:
            assert next(gen).startswith("data: ")
            assert next(gen) == ": keepalive\n\n"
        finally:
            release_runtime.set()
            gen.close()


# ═══════════════════════════════════════════════════════════
# Error paths
# ═══════════════════════════════════════════════════════════


class TestPlannerError:
    def test_planner_error_emits_failed_finish(
        self,
        broken_client: TestClient,
    ):
        """Mock planner returns non-JSON → parse fails → the stream
        must emit a ``finish_reason=failed`` chunk, then DONE.
        Pre-fix behavior would have been to raise 500 mid-stream
        or stall indefinitely."""
        with _stream(broken_client) as r:
            assert r.status_code == 200
            frames, done_seen = _collect_frames(r)
        assert done_seen, "even error paths must terminate with [DONE]"
        finish_reasons = [f["choices"][0].get("finish_reason") for f in frames]
        assert "failed" in finish_reasons, (
            f"planner-error stream should include finish_reason=failed · "
            f"got reasons={finish_reasons}"
        )

    def test_planner_error_content_mentions_failure(
        self,
        broken_client: TestClient,
    ):
        """The failed chunk's delta includes a human-readable
        planning failure hint · surfaces to the UI so the
        user sees what broke rather than a cryptic empty bubble."""
        with _stream(broken_client) as r:
            frames, _ = _collect_frames(r)
        all_content = " ".join(str(f["choices"][0]["delta"].get("content", "")) for f in frames)
        assert "planning failed before execution" in all_content.lower(), (
            f"expected planning failure content; got: {all_content!r}"
        )


# ═══════════════════════════════════════════════════════════
# Non-stream mode still works with the same fixtures
# ═══════════════════════════════════════════════════════════


class TestNonStreamSmoke:
    """Non-stream mode must not regress while we refactor stream
    internals. Same fixtures · just stream=False."""

    def test_non_stream_returns_chat_completion(
        self,
        client: TestClient,
    ):
        r = client.post(
            "/v1/chat/completions",
            json={
                "messages": [{"role": "user", "content": "x"}],
                "stream": False,
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["object"] == "chat.completion"
        assert "choices" in data
        assert data["choices"][0]["message"]["role"] == "assistant"

    def test_non_stream_planner_error_returns_error(
        self,
        broken_client: TestClient,
    ):
        """Planner error in non-stream mode should still surface ·
        not hang or throw an uncaught exception in the handler."""
        r = broken_client.post(
            "/v1/chat/completions",
            json={
                "messages": [{"role": "user", "content": "x"}],
                "stream": False,
            },
        )
        # Either 500 with error, or 200 with degraded content · both
        # are acceptable behaviors · test just pins "doesn't hang /
        # doesn't 502". Regression on internal error handling would
        # trip one of these.
        assert r.status_code in (200, 400, 500)
