"""ChatTypes —— 消息 / 工具 / 响应 / 任务结果.

与 Kotlin 端 ``ChatTypes.kt`` 对称。所有数据结构都用 ``@dataclass(slots=True)``
+ 不可变字段，便于在 ReAct 循环里安全传递。

设计要点：

- **OpenAI 兼容字段**：``role`` / ``content`` / ``tool_call_id`` 命名刻意贴近
  OpenAI Chat Completions API，方便直接序列化。
- **可序列化**：每个类型都带 ``to_dict()``，能被 ``json.dumps`` 直接消化。
- **可表达任务终止** ``TaskResult`` 是 sealed-style 枚举值，区分 DONE / MAX_STEPS
  / CANCELLED / STUCK。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

# ── 消息 ───────────────────────────────────────────────────────


@dataclass(slots=True)
class ChatMessage:
    """对话消息（OpenAI 风格）.

    字段对齐 OpenAI Chat Completions::

        {"role": "user", "content": "..."}

    ``name`` 可选（多角色场景，例如不同 agent 分工）。
    """

    role: str  # "system" | "user" | "assistant" | "tool"
    content: str
    name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.name is not None:
            d["name"] = self.name
        return d

    @classmethod
    def system(cls, content: str) -> ChatMessage:
        return cls(role="system", content=content)

    @classmethod
    def user(cls, content: str) -> ChatMessage:
        return cls(role="user", content=content)

    @classmethod
    def assistant(cls, content: str, tool_calls: list[ToolCall] | None = None) -> ChatMessage:
        msg = cls(role="assistant", content=content)
        msg._tool_calls = tool_calls or []
        return msg

    # 内部字段：assistant 消息携带的 tool_calls（OpenAI 风格）
    _tool_calls: list[ToolCall] = field(default_factory=list, repr=False)

    def tool_calls(self) -> list[ToolCall]:
        return list(self._tool_calls)


@dataclass(slots=True)
class ToolMessage:
    """工具执行结果（送回 LLM 的 role=tool 消息）.

    OpenAI 协议：每条 tool 消息必须带 ``tool_call_id``，对应 assistant 的 tool_calls.id。
    """

    tool_call_id: str
    content: str

    def to_message(self) -> ChatMessage:
        return ChatMessage(role="tool", content=self.content, name=self.tool_call_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": self.tool_call_id,
            "content": self.content,
        }


# ── 工具调用 ───────────────────────────────────────────────────


@dataclass(slots=True)
class ToolCall:
    """LLM 输出的工具调用请求.

    与 :class:`runtime.tentacle.base.ToolCall` 命名一致，但字段刻意简化：

    - ``id`` = OpenAI 的 tool_calls[].id
    - ``name`` = 工具名（如 ``android.tap``）
    - ``arguments`` = 解析后的 dict（LLM 返回的是 JSON 字符串）
    """

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": self.arguments,  # 已解析
            },
        }


@dataclass(slots=True)
class ToolCallResult:
    """一次工具调用的结果（ReAct 内部用）.

    与 :class:`runtime.tentacle.base.ToolResult` 不同：本类型面向 LLM，
    不含 duration_ms / 错误码等内部统计。
    """

    tool_call_id: str
    name: str
    success: bool
    content: str  # 喂回 LLM 的字符串（已摘要）
    raw: Any = None  # 原始数据（供后续处理，例如截图 bytes）

    def to_tool_message(self) -> ToolMessage:
        # 失败也照实返回，让 LLM 自己决定下一步
        prefix = "[OK]" if self.success else "[ERR]"
        return ToolMessage(
            tool_call_id=self.tool_call_id,
            content=f"{prefix} {self.name}: {self.content}",
        )


# ── LLM 响应 ───────────────────────────────────────────────────


class FinishReason(StrEnum):
    """LLM 响应结束原因."""

    STOP = "stop"  # 正常文本结束
    TOOL_CALLS = "tool_calls"  # 调用了工具
    LENGTH = "length"  # 触达 max_tokens
    CONTENT_FILTER = "content_filter"
    CANCELLED = "cancelled"  # 客户端取消
    ERROR = "error"


@dataclass(slots=True)
class TokenUsage:
    """token 用量（与 OpenAI usage 字段对齐）."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass(slots=True)
class LlmResponse:
    """LLM 一次完整响应.

    字段顺序遵循 ReAct 循环关心度：

    1. ``finish_reason`` —— 决定是否进入下一轮
    2. ``content`` / ``tool_calls`` —— 决策 / 动作
    3. ``usage`` —— 预算治理用
    """

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: FinishReason = FinishReason.STOP
    usage: TokenUsage = field(default_factory=TokenUsage)
    model: str = ""
    latency_ms: int = 0

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "finish_reason": self.finish_reason.value,
            "usage": {
                "prompt_tokens": self.usage.prompt_tokens,
                "completion_tokens": self.usage.completion_tokens,
                "total_tokens": self.usage.total_tokens,
            },
            "model": self.model,
            "latency_ms": self.latency_ms,
        }


# ── 工具规格（SKILL.md 解析结果） ──────────────────────────────


@dataclass(slots=True)
class SkillSpec:
    """单个技能的元信息（SKILL.md frontmatter 解析结果）.

    字段名刻意贴近 OpenAI tools 格式::

        {
            "type": "function",
            "function": {
                "name": "android.tap",
                "description": "点击屏幕指定坐标",
                "parameters": { ... JSON Schema ... }
            }
        }
    """

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)
    timeout_ms: int = 15_000
    risk: str = "low"  # "low" | "medium" | "high"
    requires_screen: bool = True
    body: str = ""  # SKILL.md 正文（仅供人类阅读，不送 LLM）

    def to_openai_tool(self) -> dict[str, Any]:
        """转成 OpenAI ``tools`` 数组的一项."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters or {"type": "object", "properties": {}},
            },
        }


# ── 任务结果（终止原因） ───────────────────────────────────────


class TaskOutcome(StrEnum):
    """任务终止原因（与 Kotlin 端 ``TaskResult`` sealed class 对应）."""

    DONE = "done"  # LLM 主动 finish
    MAX_STEPS = "max_steps"  # 触达步数上限
    CANCELLED = "cancelled"  # 用户取消
    STUCK = "stuck"  # 死循环检测触发
    ERROR = "error"  # 不可恢复错误


@dataclass(slots=True)
class TaskResult:
    """ReAct 循环的最终结果."""

    outcome: TaskOutcome
    final_message: str = ""
    steps: int = 0
    total_tokens: int = 0
    error: str | None = None

    @property
    def is_success(self) -> bool:
        return self.outcome == TaskOutcome.DONE

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "final_message": self.final_message,
            "steps": self.steps,
            "total_tokens": self.total_tokens,
            "error": self.error,
        }
