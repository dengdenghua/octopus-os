"""VLM（视觉语言模型）视觉理解模块.

提供屏幕截图分析、操作建议、结果验证等能力，
支持所有 OpenAI 兼容的视觉模型（GPT-4o, Qwen-VL, GLM-4V, DeepSeek-VL 等）。

核心组件：

- :class:`VlmClient` — VLM 视觉理解客户端（零外部依赖，裸 urllib）
- :class:`VlmConfig` — VLM 配置（含预设工厂方法）
- :class:`ScreenAnalysis` — 屏幕分析结果
- :class:`SuggestedAction` — VLM 建议的操作
- :class:`VisionReAct` — 带视觉理解的 ReAct 循环
- :class:`VlmTriggerPolicy` — VLM 触发策略配置

用法::

    from runtime.tentacle.mobile.vlm import VlmClient, VlmConfig

    client = VlmClient(VlmConfig.qwen_vl("sk-xxx"))
    result = client.analyze_screenshot(
        screenshot_base64="...",
        task="找到登录按钮并点击",
    )
    for action in result.suggested_actions:
        print(f"{action.action} → {action.target}")
"""

from .client import (
    FakeVlmTransport,
    ScreenAnalysis,
    SuggestedAction,
    UrllibVlmTransport,
    VlmClient,
    VlmConfig,
    VlmTransport,
    VlmUsage,
)
from .react_with_vision import (
    ScreenInfoGetter,
    ScreenshotGetter,
    VisionReAct,
    VlmTriggerPolicy,
)

__all__ = [
    "FakeVlmTransport",
    "ScreenAnalysis",
    "ScreenInfoGetter",
    "ScreenshotGetter",
    "SuggestedAction",
    "UrllibVlmTransport",
    "VlmClient",
    "VlmConfig",
    "VlmTriggerPolicy",
    "VlmTransport",
    "VlmUsage",
    "VisionReAct",
]
