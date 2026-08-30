"""E2E tests for the core execution path (tasks 10-12).

Reuses the scripted-fake-model pattern from ``tests/test_react_loop.py``
(``_FakeResponse`` / ``_ScriptedRouter`` / ``_FakePlanner`` / ``_FakeStack``)
to drive the full ``intent → planner → run_react_loop → final`` chain with no
real model call. The fake planner only needs a ``planner_model`` attribute
(matching ``_FakePlanner``) because ``run_react_loop`` / ``stream_react_loop``
read that single attribute off the stack's planner.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from runtime.core.cerebrum.react_loop import ReActResult, run_react_loop, stream_react_loop
from runtime.execution.suckers import Skill, SkillRegistry
from runtime.execution.tool_engine import ToolExecutor
from runtime.platform.models import ParsedIntent
from runtime.safety.auth import TrustEngine

# ────────────────────────────────────────────────────────────────────────
# Task 10 · scripted fake-model provider + full-chain plumbing
# ────────────────────────────────────────────────────────────────────────


@dataclass
class _FakeResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: str = "stop"


class _ScriptedRouter:
    """Return ``scripts[i]`` on the i-th call — scripted thought/action/final."""

    def __init__(self, scripts: list[str]) -> None:
        self.scripts = list(scripts)
        self.calls = 0

    def call(self, req: Any) -> _FakeResponse:  # noqa: ARG002
        if self.calls >= len(self.scripts):
            raise RuntimeError("router exhausted")
        text = self.scripts[self.calls]
        self.calls += 1
        return _FakeResponse(text=text)

    def call_stream(self, req: Any):
        from runtime.sensing.model_router.models import CostEntry, ModelResponse, ModelStreamEvent

        resp = self.call(req)
        if resp.text:
            yield ModelStreamEvent(type="text_delta", delta=resp.text)
        yield ModelStreamEvent(
            type="done",
            final=ModelResponse(
                text=resp.text,
                model="test-model",
                input_tokens=resp.input_tokens,
                output_tokens=resp.output_tokens,
                finish_reason=resp.finish_reason,
                cost=CostEntry(),
            ),
        )


class _TransientFailureRouter(_ScriptedRouter):
    """Call 1 makes progress, call 2 raises ConnectionError, call 3 recovers.

    Mirrors the multi-round rescue pattern in ``test_react_loop.py``: the
    error fires on the second call (after a step exists) so the loop can
    retry instead of bailing out on the very first LLM call.
    """

    def __init__(self) -> None:
        super().__init__([])

    def call(self, req: Any) -> _FakeResponse:  # noqa: ARG002
        self.calls += 1
        if self.calls == 1:
            return _FakeResponse("Thought: inspect\nAction: none\nObservation: N/A")
        if self.calls == 2:
            raise ConnectionError("temporary upstream disconnect")
        return _FakeResponse("Final Answer: recovered")


class _FakePlanner:
    """Minimal planner — ``run_react_loop`` only reads ``planner_model``."""

    def __init__(self, router: _ScriptedRouter | None) -> None:
        self.router = router
        self.planner_model = "test-model"


class _FakeStack:
    def __init__(self, router: _ScriptedRouter | None) -> None:
        self.planner = _FakePlanner(router)


def _intent(goal: str = "你好") -> ParsedIntent:
    return ParsedIntent(
        raw=goal,
        intent_type="task",
        normalized_goal=goal,
        user_context={},
    )


def _build_registry_with_skills() -> SkillRegistry:
    reg = SkillRegistry()

    def _echo(text: str = "") -> dict:
        return {"echoed": text}

    def _exec_shell(command: str = "", **_kwargs: Any) -> dict:
        return {
            "argv": command.split(),
            "exit_code": 1 if "fail" in command else 0,
            "stdout": "1 failed" if "fail" in command else "ok",
            "stderr": "",
            "stdout_truncated": False,
            "stderr_truncated": False,
        }

    def _write_text_file(
        path: str = "",
        content: str = "",
        *,
        sandbox_dir: str | None = None,
        overwrite: bool = False,
    ) -> dict:
        from runtime.execution.suckers.write_skills import _write_text_file as real_write

        return real_write(
            path=path,
            content=content,
            sandbox_dir=sandbox_dir,
            overwrite=overwrite,
        )

    def _todo_write(todos: list[dict] | None = None) -> dict:
        return {"todos": todos or []}

    reg.register(
        Skill(
            name="echo",
            description="Echo back input text.",
            trusted_source="builtin://echo",
            handler=_echo,
        ),
        verify_tests=False,
    )
    reg.register(
        Skill(
            name="exec_shell",
            description="Run a shell command.",
            trusted_source="builtin://exec_shell",
            handler=_exec_shell,
            affinity=["verify"],
        ),
        verify_tests=False,
    )
    reg.register(
        Skill(
            name="todo_write",
            description="Record a todo checklist.",
            trusted_source="builtin://todo_write",
            handler=_todo_write,
        ),
        verify_tests=False,
    )
    reg.register(
        Skill(
            name="write_text_file",
            description="Write a generated text artifact.",
            trusted_source="builtin://write_text_file",
            handler=_write_text_file,
            affinity=["write", "file"],
        ),
        verify_tests=False,
    )
    return reg


def _build_stack_with_executor(router: _ScriptedRouter | None) -> _FakeStack:
    stack = _FakeStack(router)
    stack.executor = ToolExecutor(
        registry=_build_registry_with_skills(),
        immunity=TrustEngine(
            trusted_sources=["builtin://*"],
            unknown_policy="allow",
        ),
    )
    return stack


def _run_core_path(
    router: _ScriptedRouter | None,
    goal: str,
    *,
    max_iterations: int = 8,
    **kwargs: Any,
) -> ReActResult:
    """Full chain: intent → planner(_FakePlanner) → run_react_loop → final."""
    stack = _build_stack_with_executor(router)
    return run_react_loop(stack, _intent(goal), agent=None, max_iterations=max_iterations, **kwargs)


def _drain(gen: Any) -> tuple[list[dict], Any]:
    events: list[dict] = []
    try:
        while True:
            events.append(next(gen))
    except StopIteration as stop:
        return events, stop.value


# ────────────────────────────────────────────────────────────────────────
# Task 11 · core-path E2E
# ────────────────────────────────────────────────────────────────────────


def test_e2e_single_turn_direct_answer() -> None:
    """11.1 — one round, direct Final Answer, no tool turns."""
    router = _ScriptedRouter(["Final Answer: 你好,我在。"])
    result = _run_core_path(router, "你好")

    assert isinstance(result, ReActResult)
    assert result.success
    assert result.final_answer == "你好,我在。"
    assert result.terminated_reason == "final_answer"
    assert result.completion_receipt["ready"] is True
    assert router.calls == 1
    # No tool action was emitted in the single step.
    assert len(result.steps) == 1
    assert result.steps[0].action in ("", "none")


def test_e2e_multi_turn_tool_call_observation_flows_into_final() -> None:
    """11.2 — think+action(echo) then final; tool result lands in observation."""
    router = _ScriptedRouter(
        [
            'Thought: 调用 echo 工具\nAction: echo({"text": "hello"})\n',
            "Final Answer: 工具已执行,echo 返回 hello",
        ]
    )
    result = _run_core_path(router, "echo hello")

    assert result is not None and result.success
    assert router.calls == 2
    assert len(result.steps) == 2
    tool_step = result.steps[0]
    assert tool_step.action.startswith("echo(")
    assert "hello" in tool_step.observation
    assert "real tool execution succeeded" in tool_step.observation
    assert result.final_answer == "工具已执行,echo 返回 hello"


def test_e2e_verification_failure_is_retried_until_green(tmp_path: Any) -> None:
    """11.3 — first verification is red, model fixes and reverifies green."""
    target = tmp_path / "cache.py"
    target.write_text("value = 0\n", encoding="utf-8")
    router = _ScriptedRouter(
        [
            (
                "Thought: plan\n"
                'Action: todo_write({"todos": [{"title": "implement cache", '
                '"status": "in_progress"}]})'
            ),
            (
                "Thought: write\n"
                f'Action: write_text_file({{"path": "{target.as_posix()}", '
                '"content": "value = 1\\n", "overwrite": true})'
            ),
            'Thought: verify\nAction: exec_shell({"command": "python -m pytest fail"})\n',
            'Thought: fixed\nAction: exec_shell({"command": "python -m pytest tests"})\n',
            (
                "Thought: finish checklist\n"
                'Action: todo_write({"todos": [{"title": "implement cache", '
                '"status": "completed"}]})'
            ),
            "Final Answer: 已修复,测试全部通过",
            "Final Answer: 已修复,测试全部通过",
            "Final Answer: 已修复,测试全部通过",
        ]
    )
    stack = _build_stack_with_executor(router)
    intent = _intent("implement and verify cache.py")
    intent.user_context.update({"mode": "code", "auto_approve": True})

    result = run_react_loop(stack, intent, agent=None, max_iterations=10)

    assert result is not None and result.success
    assert target.read_text(encoding="utf-8") == "value = 1\n"
    shell_steps = [s for s in result.steps if s.action.startswith("exec_shell(")]
    assert len(shell_steps) == 2
    assert "fail" in shell_steps[0].action
    assert "1 failed" in shell_steps[0].observation
    assert "1 failed" not in shell_steps[1].observation
    assert result.final_answer == "已修复,测试全部通过"


def test_e2e_model_error_is_rescued_to_success() -> None:
    """11.4 — a transient model error after progress is rescued to success."""
    router = _TransientFailureRouter()
    result = _run_core_path(router, "Continue a multi-step analysis", max_iterations=4)

    assert isinstance(result, ReActResult)
    assert result.success
    assert result.final_answer == "recovered"
    assert result.terminated_reason == "final_answer"
    assert router.calls == 3


# ────────────────────────────────────────────────────────────────────────
# Task 12 · realtime streaming E2E
# ────────────────────────────────────────────────────────────────────────


def test_e2e_stream_event_sequence_along_core_path() -> None:
    """12.1 — thought → tool → delta → final event sequence."""
    stack = _build_stack_with_executor(
        _ScriptedRouter(
            [
                'Thought: 调用 echo\nAction: echo({"text": "hi"})\n',
                "Final Answer: done",
            ]
        )
    )
    events, result = _drain(stream_react_loop(stack, _intent("hi"), agent=None, max_iterations=3))

    assert result is not None and result.success
    types = [e["type"] for e in events]
    # A thinking event (the thought text) precedes the tool lifecycle.
    assert "thinking_delta" in types
    thinking_idx = types.index("thinking_delta")
    tool_start_idx = types.index("tool_start")
    assert thinking_idx < tool_start_idx
    # A text delta carrying the final answer arrives after the tool ended.
    text_deltas = [e for e in events if e["type"] == "text_delta"]
    assert text_deltas
    assert "".join(e["delta"] for e in text_deltas) == "done"
    # The terminal event is react_completed, and it follows the tool end.
    assert "react_completed" in types
    assert types.index("tool_end") < types.index("react_completed")


def test_e2e_stream_single_turn_emits_thought_and_final_without_tool() -> None:
    """12.2 — direct answer: thought + text delta + done, no tool events."""
    stack = _build_stack_with_executor(_ScriptedRouter(["Final Answer: 直接作答"]))
    events, result = _drain(stream_react_loop(stack, _intent("你好"), agent=None, max_iterations=3))

    assert result is not None and result.success
    assert not [e for e in events if e["type"] == "tool_start"]
    assert "".join(e["delta"] for e in events if e["type"] == "text_delta") == "直接作答"
    assert "react_completed" in [e["type"] for e in events]

