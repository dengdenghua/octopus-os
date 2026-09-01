"""Provider profile data and data-layer accessors for OpenAI-compatible gateways.

Extracted from ``openai_compat_providers.py`` to keep the provider catalog
(immutable dataclasses, the ``_PROFILES`` registry, and the pure
data-only accessors that query them) separate from the request/retry
logic.  ``openai_compat_providers.py`` re-exports every public name so
existing import sites continue to work unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ThinkingRequestStyle = Literal["openai", "none", "minimax_adaptive", "deepseek"]


@dataclass(frozen=True)
class OpenAICompatProviderProfile:
    id: str
    display_name: str
    base_url_markers: tuple[str, ...] = ()
    model_markers: tuple[str, ...] = ()
    thinking_request_style: ThinkingRequestStyle = "none"
    # UI-vocabulary effort tiers this provider genuinely accepts on the wire.
    # None → the full default set (off/low/medium/high/xhigh). Empty tuple →
    # no meaningful tier control (adaptive/none), so the picker hides it.
    # Otherwise only these tiers are offered, killing fake granularity where
    # a provider collapses many OpenAI-style efforts onto one wire value.
    supported_efforts: tuple[str, ...] | None = None
    supports_vision: bool | None = None  # None = unknown, True = supports, False = no vision
    omit_sampling_parameters: bool = False
    drop_tool_choice: bool = False
    strict_tool_schema: bool = False
    max_temperature: float | None = None
    unsupported_request_fields: tuple[str, ...] = field(default_factory=tuple)
    retry_without_tool_choice: bool = True
    retry_without_sampling: bool = True
    retry_max_tokens_as_completion_tokens: bool = True
    compatibility_notes: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class OpenAICompatRetryPayload:
    payload: dict[str, Any]
    reason: str
    removed_fields: tuple[str, ...] = ()
    added_fields: tuple[str, ...] = ()
    changed_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class OpenAICompatProfileProbe:
    profile_id: str
    base_url: str
    model: str
    smoke_provider_configured: bool
    base_url_resolves_to: str
    model_resolves_to: str


GENERIC_OPENAI_PROFILE = OpenAICompatProviderProfile(
    id="openai_compat",
    display_name="OpenAI-compatible",
    thinking_request_style="openai",
)

REQUIRED_DOMESTIC_PROFILE_IDS: tuple[str, ...] = (
    "kimi_coding",
    "kimi",
    "deepseek",
    "qwen",
    "glm",
    "doubao",
    "minimax",
    "hunyuan",
    "baichuan",
    "yi",
    "stepfun",
    "siliconflow",
    "qianfan",
)


_PROFILES: tuple[OpenAICompatProviderProfile, ...] = (
    OpenAICompatProviderProfile(
        id="opencode_zen",
        display_name="OpenCode Zen",
        base_url_markers=("opencode.ai/zen/v1",),
        model_markers=("big-pickle",),
        supported_efforts=(),
        supports_vision=False,
        strict_tool_schema=True,
        compatibility_notes=(
            "OpenAI-compatible Zen gateway with model-specific capabilities",
            "free-tier model availability is discovered during plugin connection",
            "tool schemas are normalized before dispatch",
        ),
    ),
    OpenAICompatProviderProfile(
        id="kimi_coding",
        display_name="Kimi Coding",
        base_url_markers=(
            "api.kimi.com/coding",
            "api.moonshot.ai/coding",
            "api.moonshot.cn/coding",
            "/coding/v1",
        ),
        model_markers=(
            "kimi-code",
            "kimi-for-coding",
            "kimi-coding",
            "k2-code",
            "k2 code",
            "k2.7-code",
            "k2.7 code",
            "k2.7code",
            "k2.7_code",
        ),
        omit_sampling_parameters=True,
        unsupported_request_fields=("parallel_tool_calls",),
        compatibility_notes=(
            "coding endpoint rejects sampling knobs",
            "drops OpenAI reasoning/thinking extensions",
        ),
    ),
    OpenAICompatProviderProfile(
        id="deepseek",
        display_name="DeepSeek",
        base_url_markers=("api.deepseek.com",),
        model_markers=("deepseek-", "deepseek/", "deepseek_"),
        thinking_request_style="deepseek",
        supported_efforts=("off", "high", "xhigh"),
        supports_vision=False,  # DeepSeek does not support vision/image input
        compatibility_notes=(
            "native V4 thinking: reasoning_effort off|high|max, thinking:{type:disabled} to turn off",
            "reasoning text arrives as reasoning_content",
            "some deployments prefer max_completion_tokens on retry",
            "no vision support",
        ),
    ),
    OpenAICompatProviderProfile(
        id="kimi",
        display_name="Kimi / Moonshot",
        base_url_markers=(
            "api.moonshot.cn",
            "api.moonshot.ai",
            "platform.moonshot",
            "api.kimi.com",
        ),
        model_markers=("kimi", "moonshot"),
        max_temperature=1.0,
        compatibility_notes=("temperature is clamped to 1.0",),
    ),
    OpenAICompatProviderProfile(
        id="qwen",
        display_name="Alibaba Cloud Qwen / DashScope",
        base_url_markers=("dashscope.aliyuncs.com", "bailian.aliyuncs.com"),
        model_markers=("qwen", "qwq", "qvq", "tongyi"),
        strict_tool_schema=True,
        unsupported_request_fields=("parallel_tool_calls",),
        compatibility_notes=(
            "DashScope-compatible mode may reject OpenAI-only fields",
            "max_tokens can be retried as max_completion_tokens",
            "tool schemas are normalized for stricter compatible-mode validation",
        ),
    ),
    OpenAICompatProviderProfile(
        id="glm",
        display_name="Zhipu / Z.AI GLM",
        base_url_markers=("open.bigmodel.cn", "api.z.ai"),
        model_markers=("glm-", "chatglm", "zai/", "z.ai/"),
        strict_tool_schema=True,
        unsupported_request_fields=("parallel_tool_calls",),
        compatibility_notes=(
            "GLM reasoning may arrive as reasoning",
            "legacy function_call responses are accepted",
            "parallel_tool_calls is removed for OpenAI-compatible strict mode",
        ),
    ),
    OpenAICompatProviderProfile(
        id="doubao",
        display_name="Volcano Engine Doubao / Ark",
        base_url_markers=("ark.cn-beijing.volces.com", "volces.com/api/v3"),
        model_markers=("doubao",),
        strict_tool_schema=True,
        unsupported_request_fields=("parallel_tool_calls",),
        compatibility_notes=(
            "Ark OpenAI-compatible endpoint uses strict request validation",
            "tool schemas are normalized before the first request",
        ),
    ),
    OpenAICompatProviderProfile(
        id="minimax",
        display_name="MiniMax",
        base_url_markers=("api.minimaxi.com", "api.minimax.io", "api.minimax.chat"),
        model_markers=("minimax", "abab"),
        thinking_request_style="minimax_adaptive",
        supported_efforts=(),
        strict_tool_schema=True,
        unsupported_request_fields=("parallel_tool_calls",),
        compatibility_notes=(
            "thinking requests are translated to MiniMax adaptive style",
            "parallel tool-call hints are removed for stricter gateways",
        ),
    ),
    OpenAICompatProviderProfile(
        id="hunyuan",
        display_name="Tencent Hunyuan",
        base_url_markers=("api.hunyuan.cloud.tencent.com",),
        model_markers=("hunyuan",),
        strict_tool_schema=True,
        unsupported_request_fields=("parallel_tool_calls",),
        compatibility_notes=("tool schemas may require additionalProperties stripping",),
    ),
    OpenAICompatProviderProfile(
        id="baichuan",
        display_name="Baichuan",
        base_url_markers=("api.baichuan-ai.com", "platform.baichuan-ai.com"),
        model_markers=("baichuan",),
        strict_tool_schema=True,
        unsupported_request_fields=("parallel_tool_calls",),
        compatibility_notes=("falls back by removing strict OpenAI-only fields",),
    ),
    OpenAICompatProviderProfile(
        id="yi",
        display_name="01.AI Yi",
        base_url_markers=("api.lingyiwanwu.com", "platform.01.ai"),
        model_markers=("yi-", "yi_", "yi/"),
        strict_tool_schema=True,
        unsupported_request_fields=("parallel_tool_calls",),
        compatibility_notes=("falls back by removing strict OpenAI-only fields",),
    ),
    OpenAICompatProviderProfile(
        id="stepfun",
        display_name="StepFun",
        base_url_markers=("api.stepfun.ai", "api.stepfun.com"),
        model_markers=("step-", "stepfun"),
        strict_tool_schema=True,
        unsupported_request_fields=("parallel_tool_calls",),
        compatibility_notes=("falls back by removing strict OpenAI-only fields",),
    ),
    OpenAICompatProviderProfile(
        id="siliconflow",
        display_name="SiliconFlow",
        base_url_markers=("api.siliconflow.cn", "api.siliconflow.com"),
        model_markers=("siliconflow/",),
        strict_tool_schema=True,
        unsupported_request_fields=("parallel_tool_calls",),
        compatibility_notes=("proxy-hosted models vary; diagnostics surface normalized payloads",),
    ),
    OpenAICompatProviderProfile(
        id="qianfan",
        display_name="Baidu Qianfan",
        base_url_markers=("qianfan.baidubce.com",),
        model_markers=("ernie", "wenxin", "qianfan"),
        strict_tool_schema=True,
        unsupported_request_fields=("parallel_tool_calls",),
        compatibility_notes=("falls back by removing strict OpenAI-only fields",),
    ),
)


def known_openai_compat_profiles() -> tuple[OpenAICompatProviderProfile, ...]:
    return _PROFILES


def effective_supported_efforts(
    profile: OpenAICompatProviderProfile,
) -> tuple[str, ...] | None:
    """UI effort tiers a provider genuinely accepts, in picker vocabulary.

    None → the full default set (off/low/medium/high/xhigh) — a plain
    OpenAI-style profile that passes the effort through verbatim. Empty
    tuple → no meaningful effort control (thinking is adaptive/fixed or
    unsupported), so the picker hides the control entirely. Otherwise only
    the listed tiers are offered.
    """
    if profile.supported_efforts is not None:
        return profile.supported_efforts
    # A custom entry that overrides thinking_request_style without an
    # explicit supported_efforts still infers the right capability set.
    if profile.thinking_request_style == "deepseek":
        return ("off", "high", "xhigh")
    if profile.thinking_request_style == "openai":
        return None
    return ()


def openai_compat_profile_ids() -> tuple[str, ...]:
    return tuple(profile.id for profile in (GENERIC_OPENAI_PROFILE, *_PROFILES))


def resolve_openai_compat_profile(
    base_url: str,
    model: str | None = None,
) -> OpenAICompatProviderProfile:
    base = (base_url or "").strip().lower()
    model_probe = (model or "").strip().lower()

    for profile in _PROFILES:
        if profile.base_url_markers and any(marker in base for marker in profile.base_url_markers):
            return profile
    for profile in _PROFILES:
        if profile.model_markers and any(marker in model_probe for marker in profile.model_markers):
            return profile
    return GENERIC_OPENAI_PROFILE


def describe_openai_compat_profile(
    profile: OpenAICompatProviderProfile,
) -> dict[str, Any]:
    """Machine-readable summary for UI/API compatibility diagnostics."""
    normalization_hints: list[str] = []
    supported_efforts = effective_supported_efforts(profile)
    if supported_efforts is not None:
        normalization_hints.append(f"efforts:{','.join(supported_efforts)}")
    if profile.thinking_request_style != "openai":
        normalization_hints.append(f"thinking:{profile.thinking_request_style}")
    if profile.omit_sampling_parameters:
        normalization_hints.append("drop_sampling_parameters")
    if profile.drop_tool_choice:
        normalization_hints.append("drop_tool_choice")
    if profile.strict_tool_schema:
        normalization_hints.append("strict_tool_schema")
    if profile.max_temperature is not None:
        normalization_hints.append(f"max_temperature:{profile.max_temperature:g}")
    for field_name in profile.unsupported_request_fields:
        normalization_hints.append(f"drop:{field_name}")
    if profile.retry_without_tool_choice:
        normalization_hints.append("retry_without_tool_choice")
    if profile.retry_without_sampling:
        normalization_hints.append("retry_without_sampling")
    if profile.retry_max_tokens_as_completion_tokens:
        normalization_hints.append("retry_max_tokens_as_completion_tokens")
    score = _compatibility_score(profile)
    return {
        "id": profile.id,
        "display_name": profile.display_name,
        "compat_score": score,
        "supported_efforts": list(supported_efforts) if supported_efforts is not None else None,
        "normalization_hints": normalization_hints,
        "notes": list(profile.compatibility_notes),
    }


def _compatibility_score(profile: OpenAICompatProviderProfile) -> int:
    score = 100
    if profile.thinking_request_style in ("none", "minimax_adaptive"):
        score -= 6
    if profile.omit_sampling_parameters:
        score -= 10
    if profile.drop_tool_choice:
        score -= 8
    if profile.strict_tool_schema:
        score -= 3
    if profile.max_temperature is not None:
        score -= 3
    score -= min(12, len(profile.unsupported_request_fields) * 3)
    if profile.retry_without_tool_choice:
        score -= 2
    if profile.retry_without_sampling:
        score -= 2
    if profile.retry_max_tokens_as_completion_tokens:
        score -= 2
    return max(60, score)


def _profile_by_id(value: Any) -> OpenAICompatProviderProfile | None:
    if not isinstance(value, str) or not value.strip():
        return None
    target = value.strip().lower().replace("-", "_")
    if target == GENERIC_OPENAI_PROFILE.id:
        return GENERIC_OPENAI_PROFILE
    for profile in _PROFILES:
        if profile.id == target:
            return profile
    return None


def _sample_model_from_profile_markers(profile: OpenAICompatProviderProfile) -> str:
    markers = tuple(profile.model_markers or ())
    marker = str(markers[0] if markers else profile.id).rstrip("-_/ ")
    return marker or profile.id


def _sample_base_url_from_profile_markers(profile: OpenAICompatProviderProfile) -> str:
    markers = tuple(profile.base_url_markers or ())
    marker = str(markers[0] if markers else "example.com/v1")
    if marker.startswith("http://") or marker.startswith("https://"):
        return marker.rstrip("/")
    if marker.startswith("/"):
        return f"https://api.example.com{marker}".rstrip("/")
    return f"https://{marker.rstrip('/')}"
