"""Live-smoke metadata for OpenAI-compatible provider profiles.

The live smoke tests are opt-in because they spend provider quota, but the
matrix itself is checked in normal tests.  That keeps every built-in domestic
compat profile tied to an executable probe instead of becoming a docs-only
claim.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OpenAICompatSmokeProvider:
    id: str
    base_url: str
    api_key_env: tuple[str, ...]
    model_env: str
    default_model: str


_SMOKE_PROVIDERS: tuple[OpenAICompatSmokeProvider, ...] = (
    OpenAICompatSmokeProvider(
        id="opencode_zen",
        base_url="https://opencode.ai/zen/v1",
        api_key_env=("OPENCODE_ZEN_API_KEY",),
        model_env="OPENCODE_ZEN_SMOKE_MODEL",
        default_model="big-pickle",
    ),
    OpenAICompatSmokeProvider(
        id="deepseek",
        base_url="https://api.deepseek.com/v1",
        api_key_env=("DEEPSEEK_API_KEY",),
        model_env="DEEPSEEK_SMOKE_MODEL",
        default_model="deepseek-chat",
    ),
    OpenAICompatSmokeProvider(
        id="kimi",
        base_url="https://api.moonshot.cn/v1",
        api_key_env=("MOONSHOT_API_KEY", "KIMI_API_KEY"),
        model_env="KIMI_SMOKE_MODEL",
        default_model="moonshot-v1-8k",
    ),
    OpenAICompatSmokeProvider(
        id="kimi_coding",
        base_url="https://api.kimi.com/coding/v1",
        api_key_env=("KIMI_CODING_API_KEY", "KIMI_API_KEY"),
        model_env="KIMI_CODING_SMOKE_MODEL",
        default_model="K2.7-Code",
    ),
    OpenAICompatSmokeProvider(
        id="qwen",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key_env=("DASHSCOPE_API_KEY", "QWEN_API_KEY"),
        model_env="QWEN_SMOKE_MODEL",
        default_model="qwen-plus",
    ),
    OpenAICompatSmokeProvider(
        id="glm",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        api_key_env=("ZHIPU_API_KEY", "GLM_API_KEY"),
        model_env="GLM_SMOKE_MODEL",
        default_model="glm-4-flash",
    ),
    OpenAICompatSmokeProvider(
        id="doubao",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        api_key_env=("ARK_API_KEY", "DOUBAO_API_KEY", "VOLCENGINE_API_KEY"),
        model_env="DOUBAO_SMOKE_MODEL",
        default_model="doubao-pro-32k",
    ),
    OpenAICompatSmokeProvider(
        id="minimax",
        base_url="https://api.minimaxi.com/v1",
        api_key_env=("MINIMAX_API_KEY",),
        model_env="MINIMAX_SMOKE_MODEL",
        default_model="MiniMax-M2",
    ),
    OpenAICompatSmokeProvider(
        id="hunyuan",
        base_url="https://api.hunyuan.cloud.tencent.com/v1",
        api_key_env=("HUNYUAN_API_KEY", "TENCENT_HUNYUAN_API_KEY"),
        model_env="HUNYUAN_SMOKE_MODEL",
        default_model="hunyuan-large",
    ),
    OpenAICompatSmokeProvider(
        id="baichuan",
        base_url="https://api.baichuan-ai.com/v1",
        api_key_env=("BAICHUAN_API_KEY",),
        model_env="BAICHUAN_SMOKE_MODEL",
        default_model="Baichuan4",
    ),
    OpenAICompatSmokeProvider(
        id="yi",
        base_url="https://api.lingyiwanwu.com/v1",
        api_key_env=("YI_API_KEY", "LINGYIWANWU_API_KEY"),
        model_env="YI_SMOKE_MODEL",
        default_model="yi-lightning",
    ),
    OpenAICompatSmokeProvider(
        id="stepfun",
        base_url="https://api.stepfun.com/v1",
        api_key_env=("STEPFUN_API_KEY",),
        model_env="STEPFUN_SMOKE_MODEL",
        default_model="step-2-mini",
    ),
    OpenAICompatSmokeProvider(
        id="siliconflow",
        base_url="https://api.siliconflow.cn/v1",
        api_key_env=("SILICONFLOW_API_KEY",),
        model_env="SILICONFLOW_SMOKE_MODEL",
        default_model="deepseek-ai/DeepSeek-V3",
    ),
    OpenAICompatSmokeProvider(
        id="qianfan",
        base_url="https://qianfan.baidubce.com/v2",
        api_key_env=("QIANFAN_API_KEY", "BAIDU_QIANFAN_API_KEY"),
        model_env="QIANFAN_SMOKE_MODEL",
        default_model="ernie-4.5-turbo-128k",
    ),
)


def openai_compat_smoke_providers() -> tuple[OpenAICompatSmokeProvider, ...]:
    return _SMOKE_PROVIDERS


def openai_compat_smoke_provider_ids() -> tuple[str, ...]:
    return tuple(provider.id for provider in _SMOKE_PROVIDERS)


def openai_compat_smoke_readiness() -> dict[str, Any]:
    """Secret-safe local readiness for optional live provider smoke tests."""
    chat_enabled = os.environ.get("ECHO_LIVE_MODEL_SMOKE") == "1"
    tool_enabled = os.environ.get("ECHO_LIVE_MODEL_TOOL_SMOKE") == "1"
    providers: list[dict[str, Any]] = []
    configured = 0
    for provider in _SMOKE_PROVIDERS:
        key_env = _first_configured_env(provider.api_key_env)
        has_api_key = bool(key_env)
        if has_api_key:
            configured += 1
        model_override = os.environ.get(provider.model_env, "").strip()
        providers.append(
            {
                "id": provider.id,
                "base_url": provider.base_url,
                "api_key_env": list(provider.api_key_env),
                "configured_api_key_env": key_env,
                "has_api_key": has_api_key,
                "model_env": provider.model_env,
                "model": model_override or provider.default_model,
                "uses_default_model": not bool(model_override),
                "chat_smoke_runnable": chat_enabled and has_api_key,
                "tool_smoke_runnable": chat_enabled and tool_enabled and has_api_key,
            }
        )
    return {
        "schema": "echo.openai_compat_live_smoke_readiness.v1",
        "chat_smoke_enabled": chat_enabled,
        "tool_smoke_enabled": tool_enabled,
        "provider_count": len(_SMOKE_PROVIDERS),
        "configured_provider_count": configured,
        "missing_provider_count": len(_SMOKE_PROVIDERS) - configured,
        "runnable_chat_provider_count": sum(1 for row in providers if row["chat_smoke_runnable"]),
        "runnable_tool_provider_count": sum(1 for row in providers if row["tool_smoke_runnable"]),
        "providers": providers,
    }


def _first_configured_env(names: tuple[str, ...]) -> str:
    for name in names:
        if os.environ.get(name, "").strip():
            return name
    return ""


__all__ = [
    "OpenAICompatSmokeProvider",
    "openai_compat_smoke_provider_ids",
    "openai_compat_smoke_providers",
    "openai_compat_smoke_readiness",
]
