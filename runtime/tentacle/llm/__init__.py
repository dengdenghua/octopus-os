"""轻量 LLM 客户端 —— Python 端参考实现.

与 Echo Mobile 端的 `LightweightLlmClient.kt` 完全对称：

- **零外部依赖**：用 ``urllib.request``，不需要 ``requests`` / ``httpx``
- **OpenAI 兼容**：DeepSeek / Qwen / GLM / Ollama / vLLM 全部可对接
- **同步阻塞**：母体侧 Worker 直接用，不强制 async
- **可注入 transport**：测试时换成 FakeTransport 即可

设计哲学见 :ref:`ADR-008 <adr-008>` —— 方案 F：纯执行器 + 轻量 LLM + SKILL.md 单一源。
本模块只解决"如何与 LLM 对话"，不解决"如何决策"（那是 :mod:`react_loop` 的事）。
"""

from .chat_types import (
    ChatMessage,
    FinishReason,
    LlmResponse,
    SkillSpec,
    TaskOutcome,
    TaskResult,
    TokenUsage,
    ToolCall,
    ToolCallResult,
    ToolMessage,
)
from .lightweight_client import (
    FakeTransport,
    LightweightLlmClient,
    LlmConfig,
    Transport,
    UrllibTransport,
)
from .react_loop import LightweightReAct, ReActCallbacks
from .skill_manifest import SkillManifestLoader

__all__ = [
    "ChatMessage",
    "FakeTransport",
    "FinishReason",
    "LlmConfig",
    "LightweightLlmClient",
    "LightweightReAct",
    "LlmResponse",
    "ReActCallbacks",
    "SkillManifestLoader",
    "SkillSpec",
    "TaskOutcome",
    "TaskResult",
    "TokenUsage",
    "ToolCall",
    "ToolCallResult",
    "ToolMessage",
    "Transport",
    "UrllibTransport",
]
