from __future__ import annotations

from runtime.sensing.model_router.openai_compat_providers import (
    apply_custom_openai_compat_profile,
    audit_openai_compat_profile_catalog,
    describe_openai_compat_profile,
    extract_openai_compat_reasoning,
    extract_openai_compat_usage,
    known_openai_compat_profiles,
    normalize_openai_compat_payload,
    parse_tool_call_arguments,
    plan_openai_compat_retries,
    probe_openai_compat_request_contract,
    resolve_openai_compat_profile,
    retry_payloads_after_openai_compat_error,
    sample_openai_compat_profile_probe,
    split_inline_reasoning,
)
from runtime.sensing.model_router.openai_compat_smoke_matrix import (
    openai_compat_smoke_provider_ids,
    openai_compat_smoke_providers,
    openai_compat_smoke_readiness,
)


def test_domestic_provider_profile_detection_by_base_url() -> None:
    cases = {
        "https://api.deepseek.com/v1": "deepseek",
        "https://api.kimi.com/coding/v1": "kimi_coding",
        "https://api.moonshot.cn/v1": "kimi",
        "https://dashscope.aliyuncs.com/compatible-mode/v1": "qwen",
        "https://open.bigmodel.cn/api/paas/v4": "glm",
        "https://ark.cn-beijing.volces.com/api/v3": "doubao",
        "https://api.minimaxi.com/v1": "minimax",
        "https://api.baichuan-ai.com/v1": "baichuan",
        "https://api.lingyiwanwu.com/v1": "yi",
        "https://api.stepfun.com/v1": "stepfun",
        "https://api.siliconflow.cn/v1": "siliconflow",
        "https://api.hunyuan.cloud.tencent.com/v1": "hunyuan",
        "https://qianfan.baidubce.com/v2": "qianfan",
    }

    for base_url, expected in cases.items():
        assert resolve_openai_compat_profile(base_url).id == expected


def test_domestic_provider_profile_detection_by_model_name() -> None:
    assert resolve_openai_compat_profile("", "qwen-max-latest").id == "qwen"
    assert resolve_openai_compat_profile("", "deepseek-reasoner").id == "deepseek"
    assert resolve_openai_compat_profile("", "doubao-pro-256k").id == "doubao"
    assert resolve_openai_compat_profile("", "MiniMax-M2").id == "minimax"
    assert resolve_openai_compat_profile("", "glm-4.6").id == "glm"
    assert resolve_openai_compat_profile("", "K2.7 Code").id == "kimi_coding"
    assert resolve_openai_compat_profile("", "kimi-k2.7-code").id == "kimi_coding"
    assert resolve_openai_compat_profile("", "kimi-k2-0711-preview").id == "kimi"


def test_profile_catalog_includes_main_domestic_providers() -> None:
    ids = {profile.id for profile in known_openai_compat_profiles()}
    assert {
        "deepseek",
        "kimi",
        "qwen",
        "glm",
        "doubao",
        "minimax",
        "hunyuan",
        "baichuan",
        "yi",
        "stepfun",
        "siliconflow",
    }.issubset(ids)


def test_live_smoke_matrix_covers_every_builtin_domestic_profile() -> None:
    profile_ids = {profile.id for profile in known_openai_compat_profiles()}
    smoke_ids = set(openai_compat_smoke_provider_ids())

    assert profile_ids <= smoke_ids
    assert len(openai_compat_smoke_providers()) == len(smoke_ids)


def test_live_smoke_readiness_is_secret_safe_and_env_driven(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ECHO_LIVE_MODEL_SMOKE", "1")
    monkeypatch.setenv("ECHO_LIVE_MODEL_TOOL_SMOKE", "1")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-secret")
    monkeypatch.setenv("DEEPSEEK_SMOKE_MODEL", "deepseek-chat-custom")

    readiness = openai_compat_smoke_readiness()

    assert "sk-deepseek-secret" not in repr(readiness)
    assert readiness["chat_smoke_enabled"] is True
    assert readiness["tool_smoke_enabled"] is True
    assert readiness["provider_count"] == len(openai_compat_smoke_providers())
    assert readiness["configured_provider_count"] >= 1
    by_id = {row["id"]: row for row in readiness["providers"]}
    deepseek = by_id["deepseek"]
    assert deepseek["configured_api_key_env"] == "DEEPSEEK_API_KEY"
    assert deepseek["model"] == "deepseek-chat-custom"
    assert deepseek["chat_smoke_runnable"] is True
    assert deepseek["tool_smoke_runnable"] is True


def test_profile_catalog_audit_verifies_smoke_and_resolver_probes() -> None:
    audit = audit_openai_compat_profile_catalog()

    assert audit["catalog_ready"] is True
    assert audit["missing_required_profile_ids"] == []
    assert audit["missing_smoke_provider_ids"] == []
    assert audit["orphan_smoke_provider_ids"] == []
    assert audit["resolver_mismatches"] == []
    assert audit["model_alias_mismatches"] == [
        {
            "profile_id": "siliconflow",
            "base_url": "https://api.siliconflow.cn/v1",
            "model": "deepseek-ai/DeepSeek-V3",
            "model_resolves_to": "deepseek",
            "reason": "model_id_looks_like_upstream_model_on_aggregator",
        }
    ]
    assert audit["request_contract_mismatches"] == []
    assert len(audit["request_contract_probes"]) == len(known_openai_compat_profiles())
    assert all(item["contract_ready"] for item in audit["request_contract_probes"])
    assert len(audit["sample_probes"]) == len(known_openai_compat_profiles())


def test_every_builtin_profile_probe_resolves_to_its_profile() -> None:
    for profile in known_openai_compat_profiles():
        probe = sample_openai_compat_profile_probe(profile)

        assert probe.smoke_provider_configured is True
        assert probe.base_url_resolves_to == profile.id
        if profile.id != "siliconflow":
            assert probe.model_resolves_to == profile.id
        else:
            assert probe.model_resolves_to == "deepseek"


def test_profile_summary_exposes_policy_for_diagnostics() -> None:
    profile = resolve_openai_compat_profile("https://api.kimi.com/coding/v1")
    summary = describe_openai_compat_profile(profile)

    assert summary["id"] == "kimi_coding"
    assert summary["display_name"] == "Kimi Coding"
    assert 60 <= summary["compat_score"] < 100
    assert "drop_sampling_parameters" in summary["normalization_hints"]
    assert "retry_without_tool_choice" in summary["normalization_hints"]
    assert any("coding endpoint" in note for note in summary["notes"])


def test_custom_entry_can_override_compat_profile_and_field_policy() -> None:
    base = resolve_openai_compat_profile("https://proxy.example/v1", "custom-code")
    profile = apply_custom_openai_compat_profile(
        {
            "compat_profile": "kimi_coding",
            "thinking_request_style": "minimax_adaptive",
            "drop_tool_choice": True,
            "strict_tool_schema": True,
            "max_temperature": 0.2,
            "unsupported_request_fields": ["parallel_tool_calls"],
        },
        base_profile=base,
    )

    assert profile.id == "kimi_coding"
    assert profile.thinking_request_style == "minimax_adaptive"
    assert profile.omit_sampling_parameters is True
    assert profile.drop_tool_choice is True
    assert profile.strict_tool_schema is True
    assert profile.max_temperature == 0.2
    assert profile.unsupported_request_fields == ("parallel_tool_calls",)


def test_legacy_false_omit_sampling_does_not_disable_detected_profile() -> None:
    base = resolve_openai_compat_profile("https://api.kimi.com/coding/v1", "K2.7-Code")
    profile = apply_custom_openai_compat_profile(
        {"omit_sampling_parameters": False},
        base_profile=base,
    )

    assert profile.id == "kimi_coding"
    assert profile.omit_sampling_parameters is True


def test_explicit_compat_profile_can_disable_sampling_omission() -> None:
    base = resolve_openai_compat_profile("https://api.kimi.com/coding/v1", "K2.7-Code")
    profile = apply_custom_openai_compat_profile(
        {
            "compat_profile": "kimi_coding",
            "omit_sampling_parameters": False,
        },
        base_profile=base,
    )

    assert profile.id == "kimi_coding"
    assert profile.omit_sampling_parameters is False


def test_kimi_coding_payload_omits_sampling_and_thinking_extensions() -> None:
    profile = resolve_openai_compat_profile("https://api.kimi.com/coding/v1")
    payload = normalize_openai_compat_payload(
        {
            "model": "kimi-k2.7-code",
            "messages": [],
            "temperature": 0.7,
            "top_p": 0.9,
            "max_tokens": 128,
            "reasoning_effort": "high",
            "thinking": {"type": "enabled"},
        },
        profile=profile,
    )

    assert payload["model"] == "kimi-k2.7-code"
    assert payload["max_tokens"] == 128
    assert "temperature" not in payload
    assert "top_p" not in payload
    assert "reasoning_effort" not in payload
    assert "thinking" not in payload


def test_strict_domestic_profile_normalizes_tool_schema_up_front() -> None:
    profile = resolve_openai_compat_profile(
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    payload = normalize_openai_compat_payload(
        {
            "model": "qwen-plus",
            "messages": [],
            "parallel_tool_calls": True,
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "read",
                        "parameters": {
                            "$schema": "https://json-schema.org/draft/2020-12/schema",
                            "title": "Read file input",
                            "type": "object",
                            "properties": {
                                "path": {
                                    "type": "string",
                                    "default": "README.md",
                                    "examples": ["README.md"],
                                    "format": "uri-reference",
                                    "additionalProperties": False,
                                },
                            },
                            "additionalProperties": True,
                        },
                    },
                },
            ],
        },
        profile=profile,
    )

    params = payload["tools"][0]["function"]["parameters"]
    assert profile.strict_tool_schema is True
    assert "parallel_tool_calls" not in payload
    assert "$schema" not in params
    assert "title" not in params
    assert "additionalProperties" not in params
    assert "additionalProperties" not in params["properties"]["path"]
    assert "default" not in params["properties"]["path"]
    assert "examples" not in params["properties"]["path"]
    assert "format" not in params["properties"]["path"]
    assert params["properties"]["path"]["type"] == "string"


def test_kimi_k2_general_model_keeps_sampling_on_plain_proxy() -> None:
    profile = resolve_openai_compat_profile(
        "https://proxy.example/v1",
        "kimi-k2-0711-preview",
    )
    payload = normalize_openai_compat_payload(
        {
            "model": "kimi-k2-0711-preview",
            "messages": [],
            "temperature": 1.7,
            "top_p": 0.9,
        },
        profile=profile,
    )

    assert profile.id == "kimi"
    assert payload["temperature"] == 1.0
    assert payload["top_p"] == 0.9


def test_kimi_general_clamps_temperature_but_keeps_tools() -> None:
    profile = resolve_openai_compat_profile("https://api.moonshot.cn/v1")
    payload = normalize_openai_compat_payload(
        {
            "model": "moonshot-v1-128k",
            "messages": [],
            "temperature": 1.7,
            "tools": [{"type": "function", "function": {"name": "read"}}],
            "tool_choice": "auto",
        },
        profile=profile,
    )

    assert payload["temperature"] == 1.0
    assert payload["tools"]
    assert payload["tool_choice"] == "auto"


def test_minimax_thinking_uses_adaptive_payload() -> None:
    profile = resolve_openai_compat_profile("https://api.minimaxi.com/v1")
    payload = normalize_openai_compat_payload(
        {
            "model": "MiniMax-M2",
            "messages": [],
            "reasoning_effort": "high",
            "thinking": {"type": "enabled"},
        },
        profile=profile,
    )

    assert "reasoning_effort" not in payload
    assert payload["thinking"] == {"type": "adaptive"}


def test_request_contract_probe_exposes_provider_specific_degradations() -> None:
    kimi = resolve_openai_compat_profile("https://api.kimi.com/coding/v1")
    kimi_contract = probe_openai_compat_request_contract(kimi, "K2.7-Code")

    assert kimi_contract["schema"] == "echo.openai_compat_request_contract_probe.v1"
    assert kimi_contract["contract_ready"] is True
    assert kimi_contract["risk_level"] == "high"
    assert {
        "temperature",
        "top_p",
        "presence_penalty",
        "frequency_penalty",
        "reasoning_effort",
        "thinking",
        "parallel_tool_calls",
    }.issubset(set(kimi_contract["removed_fields"]))
    assert "sampling_parameters_removed" in kimi_contract["risk_reasons"]
    assert "parallel_tool_calls_removed" in kimi_contract["risk_reasons"]
    capability_status = {
        item["capability"]: item["status"] for item in kimi_contract["capability_matrix"]
    }
    assert capability_status["chat_completion"] == "pass"
    assert capability_status["tool_calling"] == "warn"
    assert capability_status["fallback_retries"] == "pass"

    qwen = resolve_openai_compat_profile(
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    qwen_contract = probe_openai_compat_request_contract(qwen, "qwen-plus")
    assert qwen_contract["contract_ready"] is True
    assert "tools" in qwen_contract["changed_fields"]
    assert "tool_schema_normalized" in qwen_contract["risk_reasons"]
    assert "parallel_tool_calls" in qwen_contract["removed_fields"]


def test_retry_payloads_drop_incompatible_fields_incrementally() -> None:
    profile = resolve_openai_compat_profile("https://dashscope.aliyuncs.com/compatible-mode/v1")
    payload = {
        "model": "qwen-plus",
        "messages": [],
        "temperature": 0.3,
        "max_tokens": 8,
        "tool_choice": "auto",
        "reasoning_effort": "high",
        "thinking": {"type": "enabled"},
    }

    variants = retry_payloads_after_openai_compat_error(
        payload,
        status_code=400,
        body='{"error":{"message":"unsupported max_completion_tokens or tool_choice"}}',
        profile=profile,
    )

    assert variants[0]["model"] == payload["model"]
    assert "reasoning_effort" not in variants[0]
    assert "thinking" not in variants[0]
    assert any("tool_choice" not in variant for variant in variants)
    assert any("max_completion_tokens" in variant for variant in variants)


def test_retry_plan_reports_reason_and_payload_delta() -> None:
    profile = resolve_openai_compat_profile("https://dashscope.aliyuncs.com/compatible-mode/v1")
    plan = plan_openai_compat_retries(
        {
            "model": "qwen-plus",
            "messages": [],
            "max_tokens": 8,
            "tool_choice": "auto",
            "reasoning_effort": "high",
            "thinking": {"type": "enabled"},
        },
        status_code=400,
        body="unsupported max_completion_tokens or tool_choice",
        profile=profile,
    )

    assert [item.reason for item in plan][:3] == [
        "drop_thinking_fields",
        "drop_tool_choice",
        "rename_max_tokens",
    ]
    assert plan[0].removed_fields == ("reasoning_effort", "thinking")
    assert plan[1].removed_fields == ("tool_choice",)
    assert plan[2].removed_fields == ("max_tokens",)
    assert plan[2].added_fields == ("max_completion_tokens",)
    assert plan[-1].reason == "combined_compatibility_fallback"


def test_retry_plan_drops_sampling_on_generic_strict_validation_error() -> None:
    profile = resolve_openai_compat_profile("https://dashscope.aliyuncs.com/compatible-mode/v1")
    plan = plan_openai_compat_retries(
        {
            "model": "qwen-plus",
            "messages": [],
            "temperature": 0.7,
            "top_p": 0.8,
        },
        status_code=400,
        body="extra inputs are not permitted: temperature",
        profile=profile,
    )

    assert plan
    assert plan[0].reason == "drop_sampling_parameters"
    assert plan[0].removed_fields == ("temperature", "top_p")


def test_retry_plan_renames_completion_tokens_back_to_max_tokens() -> None:
    profile = resolve_openai_compat_profile("https://plain-proxy.example/v1")
    plan = plan_openai_compat_retries(
        {
            "model": "proxy-model",
            "messages": [],
            "max_completion_tokens": 16,
        },
        status_code=400,
        body="unsupported parameter: max_completion_tokens; use max_tokens",
        profile=profile,
    )

    assert plan[0].reason == "rename_max_completion_tokens"
    assert plan[0].removed_fields == ("max_completion_tokens",)
    assert plan[0].added_fields == ("max_tokens",)
    assert plan[0].payload["max_tokens"] == 16


def test_retry_plan_drops_named_optional_fields_on_generic_proxy() -> None:
    profile = resolve_openai_compat_profile("https://plain-proxy.example/v1")
    plan = plan_openai_compat_retries(
        {
            "model": "proxy-model",
            "messages": [],
            "parallel_tool_calls": True,
            "response_format": {"type": "json_object"},
            "stream_options": {"include_usage": True},
            "temperature": 0.2,
        },
        status_code=400,
        body=(
            "unsupported parameter: parallel_tool_calls; "
            "unknown field response_format and stream_options"
        ),
        profile=profile,
    )

    assert plan[0].reason == (
        "drop_unsupported_fields:parallel_tool_calls,response_format,stream_options"
    )
    assert plan[0].removed_fields == (
        "parallel_tool_calls",
        "response_format",
        "stream_options",
    )
    assert "temperature" in plan[0].payload


def test_retry_plan_drops_optional_fields_on_unnamed_strict_validation_error() -> None:
    profile = resolve_openai_compat_profile("https://plain-proxy.example/v1")
    plan = plan_openai_compat_retries(
        {
            "model": "proxy-model",
            "messages": [],
            "parallel_tool_calls": True,
            "response_format": {"type": "json_object"},
            "stream_options": {"include_usage": True},
            "temperature": 0.2,
        },
        status_code=400,
        body="extra inputs are not permitted",
        profile=profile,
    )

    assert plan[0].reason == (
        "drop_unsupported_fields:parallel_tool_calls,response_format,stream_options"
    )
    assert plan[0].removed_fields == (
        "parallel_tool_calls",
        "response_format",
        "stream_options",
    )
    assert "temperature" in plan[0].payload


def test_retry_plan_drops_tool_payload_when_provider_rejects_tool_calling() -> None:
    profile = resolve_openai_compat_profile("https://plain-proxy.example/v1")
    plan = plan_openai_compat_retries(
        {
            "model": "proxy-model",
            "messages": [],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "read",
                        "parameters": {"type": "object"},
                    },
                },
            ],
            "tool_choice": "auto",
            "parallel_tool_calls": True,
        },
        status_code=400,
        body="tools are not supported by this model",
        profile=profile,
    )

    drop_tools = next(item for item in plan if item.reason == "drop_tools")
    assert drop_tools.removed_fields == (
        "parallel_tool_calls",
        "tool_choice",
        "tools",
    )
    assert drop_tools.payload == {
        "model": "proxy-model",
        "messages": [],
    }


def test_retry_plan_adds_combined_fallback_for_multi_field_rejections() -> None:
    profile = resolve_openai_compat_profile("https://api.kimi.com/coding/v1")
    plan = plan_openai_compat_retries(
        {
            "model": "kimi-k2.7-code",
            "messages": [],
            "temperature": 0.7,
            "top_p": 0.9,
            "max_tokens": 8,
            "tool_choice": "auto",
            "reasoning_effort": "high",
            "thinking": {"type": "enabled"},
        },
        status_code=400,
        body=("unsupported reasoning_effort, tool_choice, temperature and max_completion_tokens"),
        profile=profile,
    )

    combined = next(item for item in plan if item.reason == "combined_compatibility_fallback")
    assert combined.removed_fields == (
        "max_tokens",
        "reasoning_effort",
        "temperature",
        "thinking",
        "tool_choice",
        "top_p",
    )
    assert combined.added_fields == ("max_completion_tokens",)
    assert combined.payload == {
        "model": "kimi-k2.7-code",
        "messages": [],
        "max_completion_tokens": 8,
    }


def test_tool_schema_retry_strips_additional_properties() -> None:
    profile = resolve_openai_compat_profile("https://api.hunyuan.cloud.tencent.com/v1")
    variants = retry_payloads_after_openai_compat_error(
        {
            "model": "hunyuan-large",
            "messages": [],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "read",
                        "parameters": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "additionalProperties": True,
                        },
                    },
                },
            ],
        },
        status_code=400,
        body="tool schema additionalProperties is not supported",
        profile=profile,
    )

    strict = variants[-1]["tools"][0]["function"]["parameters"]
    assert "additionalProperties" not in strict
    assert strict["properties"]["path"]["type"] == "string"


def test_extract_reasoning_from_common_domestic_response_shapes() -> None:
    assert extract_openai_compat_reasoning({"reasoning_content": "deepseek"}) == "deepseek"
    assert extract_openai_compat_reasoning({"reasoning": "glm"}) == "glm"
    assert extract_openai_compat_reasoning({"thinking": "minimax"}) == "minimax"
    assert (
        extract_openai_compat_reasoning(
            {
                "reasoning_details": [{"text": "step 1"}, {"content": "step 2"}],
            }
        )
        == "step 1\nstep 2"
    )


def test_extract_usage_from_nonstandard_usage_keys() -> None:
    assert extract_openai_compat_usage(
        {
            "choices": [{"usage": {"input_tokens": "12", "output_tokens": 7}}],
        }
    ) == (12, 7)


def test_parse_tool_call_arguments_accepts_json_and_python_repr() -> None:
    assert parse_tool_call_arguments('{"path":"README.md"}') == {"path": "README.md"}
    assert parse_tool_call_arguments("{'path': 'README.md'}") == {"path": "README.md"}
    assert parse_tool_call_arguments({"path": "README.md"}) == {"path": "README.md"}
    assert parse_tool_call_arguments("not-json") == {}


def test_parse_tool_call_arguments_recovers_kimi_xml_parameters() -> None:
    xml = (
        '<parameter name="url" string="true">http://127.0.0.1:8001/</parameter>'
        '<parameter name="allow_private" boolean="true">true</parameter>'
    )
    assert parse_tool_call_arguments(xml) == {
        "url": "http://127.0.0.1:8001/",
        "allow_private": True,
    }
    assert parse_tool_call_arguments({"browser_navigate": xml}) == {
        "url": "http://127.0.0.1:8001/",
        "allow_private": True,
    }


def test_split_inline_reasoning_extracts_think_blocks() -> None:
    assert split_inline_reasoning("<think>\nweigh it\n</think>\n4") == ("4", "weigh it")
    assert split_inline_reasoning("a<think>x</think>b<think>y</think>c") == ("abc", "x\ny")


def test_split_inline_reasoning_leaves_ordinary_text_alone() -> None:
    # The overwhelmingly common case: no tag, nothing changed.
    assert split_inline_reasoning("a < b and c > d") == ("a < b and c > d", "")


def test_split_inline_reasoning_treats_an_unclosed_tag_as_reasoning() -> None:
    # A generation truncated mid-thought never reached an answer.
    assert split_inline_reasoning("<think>cut off here") == ("", "cut off here")


# ═══════════════════════════════════════════════════════════
# DeepSeek native thinking (V4): reasoning_effort off|high|max
# ═══════════════════════════════════════════════════════════


def test_deepseek_profile_uses_native_thinking_style() -> None:
    profile = resolve_openai_compat_profile("https://api.deepseek.com/v1", "deepseek-v4-flash")
    assert profile.id == "deepseek"
    assert profile.thinking_request_style == "deepseek"


def test_deepseek_native_effort_off_disables_thinking() -> None:
    profile = resolve_openai_compat_profile("https://api.deepseek.com/v1", "deepseek-v4-flash")
    normalized = normalize_openai_compat_payload(
        {"model": "deepseek-v4-flash", "reasoning_effort": "off"},
        profile=profile,
    )
    assert normalized["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in normalized


def test_deepseek_native_effort_high_and_max_kept() -> None:
    profile = resolve_openai_compat_profile("https://api.deepseek.com/v1", "deepseek-v4-flash")
    for effort in ("high", "max"):
        normalized = normalize_openai_compat_payload(
            {"model": "deepseek-v4-flash", "reasoning_effort": effort},
            profile=profile,
        )
        assert normalized["thinking"] == {"type": "enabled"}
        assert normalized["reasoning_effort"] == effort


def test_deepseek_maps_openai_efforts_into_native_vocab() -> None:
    profile = resolve_openai_compat_profile("https://api.deepseek.com/v1", "deepseek-v4-flash")
    for incoming, expected in (
        ("minimal", "high"),
        ("low", "high"),
        ("medium", "high"),
        ("high", "high"),
        ("xhigh", "max"),
        ("max", "max"),
    ):
        normalized = normalize_openai_compat_payload(
            {"model": "deepseek-v4-flash", "reasoning_effort": incoming},
            profile=profile,
        )
        assert normalized["reasoning_effort"] == expected, incoming
        assert normalized["thinking"] == {"type": "enabled"}


def test_deepseek_thinking_disabled_alone_stays_disabled() -> None:
    profile = resolve_openai_compat_profile("https://api.deepseek.com/v1", "deepseek-v4-flash")
    normalized = normalize_openai_compat_payload(
        {"model": "deepseek-v4-flash", "thinking": {"type": "disabled"}},
        profile=profile,
    )
    assert normalized["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in normalized


def test_deepseek_unknown_effort_drops_effort_keeps_explicit_thinking() -> None:
    profile = resolve_openai_compat_profile("https://api.deepseek.com/v1", "deepseek-v4-flash")
    normalized = normalize_openai_compat_payload(
        {
            "model": "deepseek-v4-flash",
            "reasoning_effort": "turbo",
            "thinking": {"type": "enabled"},
        },
        profile=profile,
    )
    assert "reasoning_effort" not in normalized
    assert normalized["thinking"] == {"type": "enabled"}


def test_deepseek_style_not_applied_to_other_profiles() -> None:
    generic = resolve_openai_compat_profile("https://proxy.example/v1", "some-model")
    assert generic.thinking_request_style == "openai"
    normalized = normalize_openai_compat_payload(
        {"model": "some-model", "reasoning_effort": "off"},
        profile=generic,
    )
    assert normalized["reasoning_effort"] == "off"  # OpenAI style passes through


def test_custom_entry_can_select_deepseek_style() -> None:
    base = resolve_openai_compat_profile("https://proxy.example/v1", "custom-model")
    profile = apply_custom_openai_compat_profile(
        {"thinking_request_style": "deepseek"},
        base_profile=base,
    )
    assert profile.thinking_request_style == "deepseek"


def test_deepseek_profile_keeps_full_compat_score() -> None:
    profile = resolve_openai_compat_profile("https://api.deepseek.com/v1", "deepseek-v4-flash")
    summary = describe_openai_compat_profile(profile)
    # 94 == generic OpenAI score: native thinking no longer deducts the
    # non-openai-style penalty (that would have made it 88), and the
    # remaining 6 points are the shared defensive-retry strategy every
    # profile carries, not a capability gap.
    assert summary["compat_score"] == 94
    assert "thinking:deepseek" in summary["normalization_hints"]


def test_supported_efforts_follow_provider_capability_set() -> None:
    from runtime.sensing.model_router.openai_compat_providers import (
        apply_custom_openai_compat_profile,
        effective_supported_efforts,
        resolve_openai_compat_profile,
    )

    # DeepSeek only distinguishes off/high/max on the wire — the picker
    # vocabulary collapses that to off/high/xhigh, dropping the low/medium
    # tiers it would silently promote.
    deepseek = resolve_openai_compat_profile(
        "https://api.deepseek.com/v1",
        "deepseek-v4-flash",
    )
    assert effective_supported_efforts(deepseek) == ("off", "high", "xhigh")

    # MiniMax's thinking is fixed adaptive — no meaningful tier control.
    minimax = resolve_openai_compat_profile(
        "https://api.minimaxi.com/v1",
        "minimax-m2",
    )
    assert effective_supported_efforts(minimax) == ()

    # A generic OpenAI-style profile keeps the full default set (None).
    generic = resolve_openai_compat_profile("https://proxy.example/v1", "mimo-pro")
    assert effective_supported_efforts(generic) is None

    # A custom entry that switches to DeepSeek style inherits its tiers.
    custom = apply_custom_openai_compat_profile(
        {"thinking_request_style": "deepseek"},
        base_profile=generic,
    )
    assert effective_supported_efforts(custom) == ("off", "high", "xhigh")

