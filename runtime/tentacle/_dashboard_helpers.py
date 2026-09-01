"""Tentacle Dashboard 内部辅助函数."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from runtime.tentacle.mobile.vlm import VlmConfig


def _auto_detect_vlm_config() -> VlmConfig | None:
    """从环境变量自动检测 VLM 配置.

    检测优先级：
    1. VLM_API_KEY + VLM_BASE_URL + VLM_MODEL（通用环境变量）
    2. QWEN_API_KEY（阿里 Qwen-VL）
    3. OPENAI_API_KEY（OpenAI GPT-4o）
    4. DEEPSEEK_API_KEY（DeepSeek-VL）
    5. GLM_API_KEY（智谱 GLM-4V）
    """
    import os

    from runtime.tentacle.mobile.vlm import VlmConfig

    # 1. 通用 VLM 环境变量
    vlm_key = os.environ.get("VLM_API_KEY", "").strip()
    if vlm_key:
        return VlmConfig(
            base_url=os.environ.get("VLM_BASE_URL", "https://api.openai.com/v1"),
            api_key=vlm_key,
            model=os.environ.get("VLM_MODEL", "gpt-4o"),
        )

    # 2. Qwen-VL
    qwen_key = os.environ.get("QWEN_API_KEY", "").strip()
    if qwen_key:
        return VlmConfig.qwen_vl(qwen_key)

    # 3. OpenAI
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if openai_key:
        return VlmConfig.openai_vl(openai_key)

    # 4. DeepSeek
    deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if deepseek_key:
        return VlmConfig.deepseek_vl(deepseek_key)

    # 5. GLM
    glm_key = os.environ.get("GLM_API_KEY", "").strip()
    if glm_key:
        return VlmConfig.glm_vl(glm_key)

    return None
