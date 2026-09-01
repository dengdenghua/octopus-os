"""LightweightReAct —— 极简 ReAct 循环.

与 Echo Mobile 端 ``LightweightReAct.kt`` 对称：

- **三级上下文压缩**：防止长任务爆 context window
- **4 轮滑动窗口死循环检测**：相同 tool_call 反复出现就停
- **工具结果摘要化**：截断 / 摘要喂回 LLM，节省 token
- **默认 30 步上限 + cancel hook**：可控终止

ReAct 循环::

    ┌─→ LLM.chat(messages, skills)
    │       │
    │       ├─ finish_reason=stop → DONE
    │       └─ finish_reason=tool_calls
    │              │
    │              ↓
    │       for tc in tool_calls:
    │           result = execute_tool(tc)   # ← 注入
    │           messages.append(tool_msg(result))
    │              │
    │              ↓
    │       compress_if_needed(messages)     # 三级压缩
    │       stuck_check(messages)            # 4 轮滑动窗口
    │              │
    └──────────────┘

为什么是"极简"？

- 不用 LangChain / LangGraph —— 多一层抽象，多一处 token 浪费
- 不做 plan-and-execute —— 简单循环覆盖 90% 用例
- 不维护 vector store —— 长期记忆由母体的 Memory Organ 负责
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from .chat_types import (
    ChatMessage,
    FinishReason,
    LlmResponse,
    SkillSpec,
    TaskOutcome,
    TaskResult,
    ToolCall,
    ToolCallResult,
)
from .lightweight_client import LightweightLlmClient

logger = logging.getLogger(__name__)


# ── 工具执行器（可注入） ──────────────────────────────────────


class ToolExecutor(Protocol):
    """执行单个工具调用并返回结果.

    生产实现：``Tentacle.execute(call)`` + 转 ``ToolCallResult``
    测试实现：``FakeExecutor`` 直接返回预置数据
    """

    def execute(self, tool_call: ToolCall) -> ToolCallResult: ...


class AsyncToolExecutor(Protocol):
    """async 版本 —— 母体 Worker 通常是 asyncio."""

    async def execute(self, tool_call: ToolCall) -> ToolCallResult: ...


# ── 回调（观测点） ────────────────────────────────────────────


@dataclass(slots=True)
class ReActCallbacks:
    """ReAct 循环的钩子.

    每个回调都是可选的，不提供就是 no-op。母体的 TraceStore / Ink 预算治理
    都可以挂上来。
    """

    on_step_start: Callable[[int], None] | None = None
    on_llm_response: Callable[[LlmResponse], None] | None = None
    on_tool_call: Callable[[ToolCall], None] | None = None
    on_tool_result: Callable[[ToolCallResult], None] | None = None
    on_step_end: Callable[[int], None] | None = None
    on_compress: Callable[[int, int], None] | None = None  # (old_len, new_len)
    on_stuck: Callable[[list[str]], None] | None = None  # (recent_tool_names)
    on_finish: Callable[[TaskResult], None] | None = None


# ── 循环主类 ──────────────────────────────────────────────────


class LightweightReAct:
    """同步版 ReAct 循环.

    用法::

        client = LightweightLlmClient(LlmConfig.deepSeek())
        executor = TentacleExecutor(pool)  # 你的 Tentacle 适配器
        react = LightweightReAct(client, executor)

        result = react.run(
            user_task="打开微信，给文件传输助手发 hello",
            skills=mobile_skills,
        )
        if result.is_success:
            print(result.final_message)
    """

    def __init__(
        self,
        client: LightweightLlmClient,
        executor: ToolExecutor,
        *,
        max_steps: int = 30,
        stuck_window: int = 4,
        compress_trigger_ratio: float = 0.7,
        compress_target_ratio: float = 0.5,
        max_tool_result_chars: int = 1500,
        system_prompt: str | None = None,
        callbacks: ReActCallbacks | None = None,
    ) -> None:
        self.client = client
        self.executor = executor
        self.max_steps = max_steps
        self.stuck_window = stuck_window
        self.compress_trigger_ratio = compress_trigger_ratio
        self.compress_target_ratio = compress_target_ratio
        self.max_tool_result_chars = max_tool_result_chars
        self.system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
        self.callbacks = callbacks or ReActCallbacks()

        self._cancelled = False
        self._total_tokens = 0

    # ── 公共入口 ────────────────────────────────────────────

    def run(
        self,
        user_task: str,
        skills: list[SkillSpec],
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> TaskResult:
        """同步执行 ReAct 循环直到终止.

        Args:
            user_task: 用户原始任务文本
            skills: 可用工具列表
            cancel_check: 每次 step 前调用，返回 True 立刻终止
        """
        self._cancelled = False
        self._total_tokens = 0
        messages: list[ChatMessage] = [
            ChatMessage.system(self.system_prompt),
            ChatMessage.user(user_task),
        ]
        steps = 0
        final = ""

        try:
            for step in range(self.max_steps):
                steps = step + 1
                if cancel_check and cancel_check():
                    self._cancelled = True
                    return self._finish(
                        TaskOutcome.CANCELLED,
                        "cancelled by cancel_check",
                        steps,
                    )
                if self._cancelled:
                    return self._finish(
                        TaskOutcome.CANCELLED,
                        "cancelled by external signal",
                        steps,
                    )
                self._emit(self.callbacks.on_step_start, step)

                # 1. 调 LLM
                resp = self.client.chat(messages, skills=skills)
                self._total_tokens += resp.usage.total_tokens
                self._emit(self.callbacks.on_llm_response, resp)

                # 2. 决策
                if not resp.has_tool_calls:
                    final = resp.content
                    if resp.finish_reason == FinishReason.ERROR:
                        return self._finish(TaskOutcome.ERROR, final, steps, error=final)
                    return self._finish(TaskOutcome.DONE, final, steps)

                # 3. 把 assistant 消息（含 tool_calls）塞回历史
                messages.append(ChatMessage.assistant(resp.content, tool_calls=resp.tool_calls))

                # 4. 死循环检测
                if self._is_stuck(resp.tool_calls):
                    self._emit(self.callbacks.on_stuck, [tc.name for tc in resp.tool_calls])
                    return self._finish(
                        TaskOutcome.STUCK,
                        f"Stuck: same tools repeated {self.stuck_window}x",
                        steps,
                    )

                # 5. 执行工具
                for tc in resp.tool_calls:
                    self._emit(self.callbacks.on_tool_call, tc)
                    try:
                        result = self.executor.execute(tc)
                    except Exception as e:  # noqa: BLE001
                        logger.exception("Tool %s crashed", tc.name)
                        result = ToolCallResult(
                            tool_call_id=tc.id,
                            name=tc.name,
                            success=False,
                            content=f"executor crashed: {e!r}",
                        )
                    # 摘要化喂回 LLM
                    summarized = self._summarize(result)
                    messages.append(summarized.to_tool_message().to_message())
                    self._emit(self.callbacks.on_tool_result, result)

                # 6. 三级压缩
                self._maybe_compress(messages)
                self._emit(self.callbacks.on_step_end, step)

            # 循环结束 —— 步数耗尽
            return self._finish(
                TaskOutcome.MAX_STEPS,
                f"Reached max_steps={self.max_steps}",
                steps,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("ReAct crashed")
            return self._finish(TaskOutcome.ERROR, str(e), steps, error=str(e))

    def cancel(self) -> None:
        """外部取消信号（线程安全）."""
        self._cancelled = True

    # ── 内部工具 ────────────────────────────────────────────

    def _finish(
        self,
        outcome: TaskOutcome,
        message: str,
        steps: int,
        *,
        error: str | None = None,
    ) -> TaskResult:
        result = TaskResult(
            outcome=outcome,
            final_message=message,
            steps=steps,
            total_tokens=self._total_tokens,
            error=error,
        )
        self._emit(self.callbacks.on_finish, result)
        return result

    def _is_stuck(self, tool_calls: list[ToolCall]) -> bool:
        """4 轮滑动窗口：连续 N 轮调用同一组工具就停.

        用 ``(name + sorted_args_keys + args_hash)`` 当指纹，避免
        真的"换参数重试"被误判。
        """
        if not tool_calls:
            return False
        fingerprints = [_tool_fingerprint(tc) for tc in tool_calls]
        self._recent_window.append(frozenset(fingerprints))
        if len(self._recent_window) < self.stuck_window:
            return False
        # 取最近 N 个窗口
        recent = list(self._recent_window)[-self.stuck_window :]
        return all(r == recent[0] for r in recent)

    @property
    def _recent_window(self) -> deque[frozenset[str]]:
        # 懒初始化，避免 __init__ 复杂化
        if not hasattr(self, "_rw"):
            self._rw: deque[frozenset[str]] = deque(maxlen=self.stuck_window * 2)
        return self._rw

    def _maybe_compress(self, messages: list[ChatMessage]) -> None:
        """三级压缩：保留 system + 最近若干轮，折叠中间历史.

        触发条件：消息字符总数 / 估算 context 比例 ≥ compress_trigger_ratio
        目标比例：折叠后 ≤ compress_target_ratio
        """
        total_chars = sum(len(m.content) for m in messages)
        # 估算 context window（粗略：4 字符 ≈ 1 token；200k context = 800k chars）
        estimated_budget = 200_000 * 4
        if total_chars < estimated_budget * self.compress_trigger_ratio:
            return
        # 至少保留：system prompt + 最后 4 条消息
        if len(messages) < 6:
            return
        keep_recent = 4
        head = messages[0]  # system
        tail = messages[-(keep_recent):]  # 最近
        middle = messages[1:-keep_recent]
        # 折叠中间：用一条 assistant 消息总结
        summary = self._summarize_middle(middle)
        new_messages = [head, ChatMessage.assistant(f"[历史摘要] {summary}")] + tail
        old_len = len(messages)
        self._emit(self.callbacks.on_compress, old_len, len(new_messages))
        messages.clear()
        messages.extend(new_messages)

    def _summarize_middle(self, middle: list[ChatMessage]) -> str:
        """极简摘要：把中间消息的 content 拼起来再截断（不调 LLM，省 token）.

        如果以后要更智能，可在此调一次 LLM。
        """
        parts: list[str] = []
        for m in middle:
            snippet = m.content[:200].replace("\n", " ")
            parts.append(f"[{m.role}] {snippet}")
        joined = " | ".join(parts)
        return joined[:1500] + ("..." if len(joined) > 1500 else "")

    def _summarize(self, result: ToolCallResult) -> ToolCallResult:
        """截断喂回 LLM 的 content（节省 token）."""
        if len(result.content) <= self.max_tool_result_chars:
            return result
        truncated = result.content[: self.max_tool_result_chars]
        return ToolCallResult(
            tool_call_id=result.tool_call_id,
            name=result.name,
            success=result.success,
            content=truncated + f"\n... [truncated, full={len(result.content)} chars]",
            raw=result.raw,
        )

    def _emit(self, fn: Callable[..., Any] | None, *args: Any) -> None:
        if fn is None:
            return
        try:
            fn(*args)
        except Exception:  # noqa: BLE001
            logger.exception("Callback failed")


# ── 默认 system prompt ────────────────────────────────────────

DEFAULT_SYSTEM_PROMPT = """\
你是 Echo Mobile 的执行 Agent，运行在 Android 设备触手之上。

工作原则：
1. **先观察，再行动**：先用 take_screenshot / get_screen_info 看清当前界面
2. **小步快跑**：每步只做 1-2 个动作，做完再截屏确认
3. **路径稳健**：找不到目标就滚动；遇到弹窗先 dismiss
4. **诚实汇报**：用 finish({ok: true/false, summary: "..."}) 收尾，不要胡编

可用工具见 tools 列表。每次响应要么调工具，要么调 finish 收尾。
"""


# ── 内部工具函数 ──────────────────────────────────────────────


def _tool_fingerprint(tc: ToolCall) -> str:
    """给一个 tool_call 算指纹：name + 排序后 args keys + args hash.

    设计目标：

    - 同名同参 → 同指纹（真死循环）
    - 同名不同参 → 不同指纹（合理探索）
    - 不同名 → 不同指纹
    """
    key_part = tc.name
    arg_keys = ",".join(sorted(tc.arguments.keys()))
    args_hash = hashlib.md5(
        json.dumps(tc.arguments, sort_keys=True, ensure_ascii=False).encode("utf-8"),
        usedforsecurity=False,
    ).hexdigest()[:8]
    return f"{key_part}|{arg_keys}|{args_hash}"
