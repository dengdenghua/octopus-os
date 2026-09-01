"""Provider profiles for OpenAI-compatible chat-completion gateways.

The OpenAI-compatible label hides a few practical differences across
domestic model providers: some reject OpenAI-only thinking fields, some
prefer stricter sampling payloads, and several expose reasoning text
through provider-specific response keys.  This module keeps those rules
data-driven so ``OpenAIModelRouter`` can stay a normal chat-completions
transport instead of accumulating provider-specific branches.

The immutable provider catalog (dataclasses, the ``_PROFILES`` registry,
and pure data-only accessors) lives in ``_providers_data`` and the
response-parsing helpers (reasoning/usage extraction, tool-call argument
decoding) live in ``_response_parsers``.  Both are re-exported here so
existing import sites continue to work unchanged.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Any

from ._providers_data import (
    GENERIC_OPENAI_PROFILE,
    REQUIRED_DOMESTIC_PROFILE_IDS,
    OpenAICompatProfileProbe,
    OpenAICompatProviderProfile,
    OpenAICompatRetryPayload,
    _compatibility_score,
    _profile_by_id,
    _sample_base_url_from_profile_markers,
    _sample_model_from_profile_markers,
    describe_openai_compat_profile,
    effective_supported_efforts,
    known_openai_compat_profiles,
    openai_compat_profile_ids,
    resolve_openai_compat_profile,
)
from ._response_parsers import (
    InlineReasoningSplitter,
    extract_openai_compat_reasoning,
    extract_openai_compat_usage,
    parse_tool_call_arguments,
    split_inline_reasoning,
)

_OPTIONAL_REQUEST_FIELD_FALLBACKS = (
    "parallel_tool_calls",
    "response_format",
    "stream_options",
    "logprobs",
    "top_logprobs",
)

_TOOL_REQUEST_FIELDS = ("tools", "tool_choice", "parallel_tool_calls")

_STRICT_SCHEMA_DROPPED_KEYS = frozenset(
    {
        "$anchor",
        "$comment",
        "$id",
        "$schema",
        "default",
        "deprecated",
        "discriminator",
        "example",
        "examples",
        "externalDocs",
        "format",
        "nullable",
        "readOnly",
        "title",
        "writeOnly",
        "xml",
    }
)


def sample_openai_compat_profile_probe(
    profile: OpenAICompatProviderProfile,
) -> OpenAICompatProfileProbe:
    smoke = _smoke_provider_by_id().get(profile.id)
    base_url = (
        smoke.base_url if smoke is not None else _sample_base_url_from_profile_markers(profile)
    )
    model = (
        smoke.default_model if smoke is not None else _sample_model_from_profile_markers(profile)
    )
    return OpenAICompatProfileProbe(
        profile_id=profile.id,
        base_url=base_url,
        model=model,
        smoke_provider_configured=smoke is not None,
        base_url_resolves_to=resolve_openai_compat_profile(base_url).id,
        model_resolves_to=resolve_openai_compat_profile("", model).id,
    )


def audit_openai_compat_profile_catalog(
    required_profile_ids: tuple[str, ...] = REQUIRED_DOMESTIC_PROFILE_IDS,
) -> dict[str, Any]:
    profiles = list(known_openai_compat_profiles())
    profile_ids = [profile.id for profile in profiles]
    smoke_provider_ids = list(_smoke_provider_by_id())
    probes = [sample_openai_compat_profile_probe(profile) for profile in profiles]
    resolver_mismatches = [
        {
            "profile_id": probe.profile_id,
            "base_url": probe.base_url,
            "model": probe.model,
            "base_url_resolves_to": probe.base_url_resolves_to,
            "model_resolves_to": probe.model_resolves_to,
        }
        for probe in probes
        if probe.base_url_resolves_to != probe.profile_id
    ]
    model_alias_mismatches = [
        {
            "profile_id": probe.profile_id,
            "base_url": probe.base_url,
            "model": probe.model,
            "model_resolves_to": probe.model_resolves_to,
            "reason": "model_id_looks_like_upstream_model_on_aggregator",
        }
        for probe in probes
        if probe.model_resolves_to != probe.profile_id
    ]
    missing_required = [
        profile_id for profile_id in required_profile_ids if profile_id not in profile_ids
    ]
    missing_smoke = [
        profile_id for profile_id in profile_ids if profile_id not in smoke_provider_ids
    ]
    orphan_smoke = [
        provider_id for provider_id in smoke_provider_ids if provider_id not in profile_ids
    ]
    smoke_mismatches = [
        {
            "profile_id": probe.profile_id,
            "base_url": probe.base_url,
            "model": probe.model,
            "base_url_resolves_to": probe.base_url_resolves_to,
            "model_resolves_to": probe.model_resolves_to,
        }
        for probe in probes
        if probe.smoke_provider_configured and probe.base_url_resolves_to != probe.profile_id
    ]
    contract_probes = [
        probe_openai_compat_request_contract(
            _profile_by_id(probe.profile_id) or GENERIC_OPENAI_PROFILE,
            probe.model,
        )
        for probe in probes
    ]
    contract_mismatches = [
        {
            "profile_id": probe["profile_id"],
            "model": probe["model"],
            "risk_level": probe["risk_level"],
            "reason": "core_request_contract_changed",
        }
        for probe in contract_probes
        if not probe["contract_ready"]
    ]
    catalog_ready = (
        not missing_required
        and not missing_smoke
        and not orphan_smoke
        and not resolver_mismatches
        and not contract_mismatches
    )
    return {
        "schema": "echo.openai_compat_profile_audit.v1",
        "catalog_ready": catalog_ready,
        "profile_count": len(profile_ids),
        "profile_ids": profile_ids,
        "required_profile_ids": list(required_profile_ids),
        "missing_required_profile_ids": missing_required,
        "smoke_provider_ids": smoke_provider_ids,
        "missing_smoke_provider_ids": missing_smoke,
        "orphan_smoke_provider_ids": orphan_smoke,
        "resolver_mismatches": resolver_mismatches,
        "model_alias_mismatches": model_alias_mismatches,
        "smoke_resolver_mismatches": smoke_mismatches,
        "request_contract_mismatches": contract_mismatches,
        "request_contract_probes": contract_probes,
        "sample_probes": [
            {
                "profile_id": probe.profile_id,
                "base_url": probe.base_url,
                "model": probe.model,
                "smoke_provider_configured": probe.smoke_provider_configured,
                "base_url_resolves_to": probe.base_url_resolves_to,
                "model_resolves_to": probe.model_resolves_to,
            }
            for probe in probes
        ],
    }


def sample_openai_compat_contract_payload(model: str) -> dict[str, Any]:
    """Representative dry-run request covering common compat edge-fields."""
    return {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "temperature": 0.7,
        "top_p": 0.9,
        "presence_penalty": 0.0,
        "frequency_penalty": 0.0,
        "max_tokens": 8,
        "stream": True,
        "stream_options": {"include_usage": True},
        "response_format": {"type": "json_object"},
        "reasoning_effort": "high",
        "thinking": {"type": "enabled"},
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "diagnostic_ping",
                    "description": "No-op compatibility probe.",
                    "parameters": {
                        "$schema": "https://json-schema.org/draft/2020-12/schema",
                        "title": "Diagnostic ping input",
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
        "tool_choice": "auto",
        "parallel_tool_calls": True,
    }


def probe_openai_compat_request_contract(
    profile: OpenAICompatProviderProfile,
    model: str,
) -> dict[str, Any]:
    """Dry-run request contract probe for a provider/profile pair.

    This never calls the provider. It normalizes a representative request,
    plans retries for a representative strict-validation error, and reports
    which capabilities are preserved, normalized, or likely degraded.
    """
    original = sample_openai_compat_contract_payload(model)
    normalized = normalize_openai_compat_payload(original, profile=profile)
    removed, added, changed = _payload_delta(original, normalized)
    retry_plan = plan_openai_compat_retries(
        normalized,
        status_code=400,
        body=(
            "unsupported reasoning_effort thinking tool_choice "
            "temperature top_p max_completion_tokens stream_options "
            "response_format additionalProperties extra inputs are not "
            "permitted unsupported parameter"
        ),
        profile=profile,
    )
    summary = _request_contract_summary(
        normalized=normalized,
        removed_fields=removed,
        changed_fields=changed,
        retry_plan=retry_plan,
        compat_score=_compatibility_score(profile),
    )
    core_fields_ready = {"model", "messages"}.issubset(normalized)
    return {
        "schema": "echo.openai_compat_request_contract_probe.v1",
        "profile_id": profile.id,
        "model": model,
        "dry_run": True,
        "contract_ready": bool(core_fields_ready),
        "risk_level": summary["risk_level"],
        "risk_reasons": summary["risk_reasons"],
        "capability_matrix": summary["capability_matrix"],
        "original_fields": sorted(original),
        "normalized_fields": sorted(normalized),
        "removed_fields": list(removed),
        "added_fields": list(added),
        "changed_fields": list(changed),
        "normalized_payload": normalized,
        "fallback_retries": [
            {
                "reason": item.reason,
                "removed_fields": list(item.removed_fields),
                "added_fields": list(item.added_fields),
                "changed_fields": list(item.changed_fields),
                "payload_fields": sorted(item.payload),
            }
            for item in retry_plan
        ],
    }


def apply_custom_openai_compat_profile(
    entry: dict[str, Any] | None,
    *,
    base_profile: OpenAICompatProviderProfile,
) -> OpenAICompatProviderProfile:
    if not isinstance(entry, dict):
        return base_profile

    profile = _profile_by_id(entry.get("compat_profile")) or base_profile
    updates: dict[str, Any] = {}

    thinking_style = entry.get("thinking_request_style")
    if thinking_style in ("openai", "none", "minimax_adaptive", "deepseek"):
        updates["thinking_request_style"] = thinking_style

    for field_name in (
        "drop_tool_choice",
        "strict_tool_schema",
        "retry_without_tool_choice",
        "retry_without_sampling",
        "retry_max_tokens_as_completion_tokens",
    ):
        value = entry.get(field_name)
        if value is not None:
            updates[field_name] = bool(value)

    omit_sampling = entry.get("omit_sampling_parameters")
    if omit_sampling is not None and (
        omit_sampling is True or _has_explicit_compat_override(entry)
    ):
        updates["omit_sampling_parameters"] = bool(omit_sampling)

    max_temperature = _coerce_float(entry.get("max_temperature"))
    if max_temperature is not None:
        updates["max_temperature"] = max_temperature

    unsupported_fields = _coerce_string_tuple(entry.get("unsupported_request_fields"))
    if unsupported_fields is not None:
        updates["unsupported_request_fields"] = unsupported_fields

    if not updates:
        return profile
    return replace(profile, **updates)


def normalize_openai_compat_payload(
    payload: dict[str, Any],
    *,
    profile: OpenAICompatProviderProfile,
) -> dict[str, Any]:
    normalized = dict(payload)

    for field_name in profile.unsupported_request_fields:
        normalized.pop(field_name, None)

    _normalize_thinking_fields(normalized, profile)

    if profile.omit_sampling_parameters:
        _remove_sampling_parameters(normalized)
    elif profile.max_temperature is not None and "temperature" in normalized:
        value = normalized.get("temperature")
        if isinstance(value, int | float) and value > profile.max_temperature:
            normalized["temperature"] = profile.max_temperature

    if profile.drop_tool_choice:
        normalized.pop("tool_choice", None)

    if profile.strict_tool_schema and "tools" in normalized:
        normalized["tools"] = _strict_tools(normalized.get("tools"))

    return normalized


def retry_payloads_after_openai_compat_error(
    payload: dict[str, Any],
    *,
    status_code: int,
    body: str = "",
    profile: OpenAICompatProviderProfile = GENERIC_OPENAI_PROFILE,
) -> list[dict[str, Any]]:
    return [
        item.payload
        for item in plan_openai_compat_retries(
            payload,
            status_code=status_code,
            body=body,
            profile=profile,
        )
    ]


def plan_openai_compat_retries(
    payload: dict[str, Any],
    *,
    status_code: int,
    body: str = "",
    profile: OpenAICompatProviderProfile = GENERIC_OPENAI_PROFILE,
) -> list[OpenAICompatRetryPayload]:
    if status_code not in (400, 422):
        return []

    variants: list[OpenAICompatRetryPayload] = []
    seen: set[str] = {_payload_fingerprint(payload)}
    cascade = dict(payload)
    cascade_reasons: list[str] = []

    def add(reason: str, candidate: dict[str, Any]) -> None:
        fp = _payload_fingerprint(candidate)
        if fp in seen:
            return
        seen.add(fp)
        removed, added, changed = _payload_delta(payload, candidate)
        variants.append(
            OpenAICompatRetryPayload(
                payload=candidate,
                reason=reason,
                removed_fields=removed,
                added_fields=added,
                changed_fields=changed,
            )
        )

    lower = (body or "").lower()
    strict_validation = _mentions_any(
        lower,
        (
            "unsupported parameter",
            "unsupported field",
            "unsupported request",
            "unrecognized field",
            "unknown field",
            "unknown parameter",
            "extra inputs are not permitted",
            "extra_forbidden",
            "invalid parameter",
        ),
    )

    if "reasoning_effort" in payload or "thinking" in payload:
        candidate = dict(payload)
        candidate.pop("reasoning_effort", None)
        candidate.pop("thinking", None)
        add("drop_thinking_fields", candidate)
        cascade.pop("reasoning_effort", None)
        cascade.pop("thinking", None)
        cascade_reasons.append("drop_thinking_fields")

    optional_fields = _mentioned_payload_fields(
        lower,
        payload,
        _OPTIONAL_REQUEST_FIELD_FALLBACKS,
    )
    if strict_validation and not optional_fields:
        optional_fields = tuple(
            field_name for field_name in _OPTIONAL_REQUEST_FIELD_FALLBACKS if field_name in payload
        )
    if optional_fields:
        candidate = dict(payload)
        for field_name in optional_fields:
            candidate.pop(field_name, None)
            cascade.pop(field_name, None)
        add(f"drop_unsupported_fields:{','.join(optional_fields)}", candidate)
        cascade_reasons.append("drop_unsupported_fields")

    if profile.retry_without_tool_choice and "tool_choice" in payload:
        candidate = dict(payload)
        candidate.pop("tool_choice", None)
        add("drop_tool_choice", candidate)
        cascade.pop("tool_choice", None)
        cascade_reasons.append("drop_tool_choice")

    if (
        profile.retry_without_sampling
        and _payload_has_sampling(payload)
        and (
            _mentions_any(
                lower, ("temperature", "top_p", "sampling", "presence_penalty", "frequency_penalty")
            )
            or strict_validation
            or profile.omit_sampling_parameters
        )
    ):
        candidate = dict(payload)
        _remove_sampling_parameters(candidate)
        add("drop_sampling_parameters", candidate)
        _remove_sampling_parameters(cascade)
        cascade_reasons.append("drop_sampling_parameters")

    if (
        profile.retry_max_tokens_as_completion_tokens
        and "max_tokens" in payload
        and "max_completion_tokens" not in payload
        and _mentions_any(lower, ("max_completion_tokens", "max_tokens", "max token"))
    ):
        candidate = dict(payload)
        candidate["max_completion_tokens"] = candidate.pop("max_tokens")
        add("rename_max_tokens", candidate)
        if "max_tokens" in cascade and "max_completion_tokens" not in cascade:
            cascade["max_completion_tokens"] = cascade.pop("max_tokens")
        cascade_reasons.append("rename_max_tokens")

    if (
        profile.retry_max_tokens_as_completion_tokens
        and "max_completion_tokens" in payload
        and "max_tokens" not in payload
        and _mentions_any(lower, ("max_completion_tokens", "max_tokens", "max token"))
    ):
        candidate = dict(payload)
        candidate["max_tokens"] = candidate.pop("max_completion_tokens")
        add("rename_max_completion_tokens", candidate)
        if "max_completion_tokens" in cascade and "max_tokens" not in cascade:
            cascade["max_tokens"] = cascade.pop("max_completion_tokens")
        cascade_reasons.append("rename_max_completion_tokens")

    if "tools" in payload and _mentions_any(
        lower,
        ("additionalproperties", "additional properties", "tool schema", "parameters"),
    ):
        candidate = dict(payload)
        candidate["tools"] = _strict_tools(candidate.get("tools"))
        add("strict_tool_schema", candidate)
        cascade["tools"] = _strict_tools(cascade.get("tools"))
        cascade_reasons.append("strict_tool_schema")

    if "tools" in payload and _mentions_tool_use_unsupported(lower):
        candidate = dict(payload)
        for field_name in _TOOL_REQUEST_FIELDS:
            candidate.pop(field_name, None)
            cascade.pop(field_name, None)
        add("drop_tools", candidate)
        cascade_reasons.append("drop_tools")

    if len(cascade_reasons) > 1:
        add("combined_compatibility_fallback", cascade)

    return variants


def _request_contract_summary(
    *,
    normalized: dict[str, Any],
    removed_fields: tuple[str, ...],
    changed_fields: tuple[str, ...],
    retry_plan: list[OpenAICompatRetryPayload],
    compat_score: int,
) -> dict[str, Any]:
    retry_removed: set[str] = set()
    retry_changed: set[str] = set()
    retry_reasons: list[str] = []
    for retry in retry_plan:
        retry_reasons.append(str(retry.reason or ""))
        retry_removed.update(str(v) for v in retry.removed_fields or ())
        retry_changed.update(str(v) for v in retry.changed_fields or ())

    removed = set(removed_fields)
    changed = set(changed_fields)
    matrix = [
        _compat_capability_row(
            "chat_completion",
            "pass" if {"model", "messages"}.issubset(normalized) else "warn",
            [
                "model preserved" if "model" in normalized else "model missing",
                ("messages preserved" if "messages" in normalized else "messages missing"),
            ],
            ["dry_run_request_shape_only"],
        ),
        _compat_capability_row(
            "streaming",
            "warn" if "stream_options" in retry_removed else "unverified",
            [
                (
                    "stream flag preserved"
                    if normalized.get("stream") is True
                    else "stream flag not preserved"
                ),
                (
                    "stream_options preserved"
                    if "stream_options" in normalized
                    else "stream_options absent"
                ),
            ],
            [
                "dry_run_does_not_open_stream",
                *(
                    ["strict fallback may drop stream_options"]
                    if "stream_options" in retry_removed
                    else []
                ),
            ],
        ),
        _compat_capability_row(
            "tool_calling",
            "warn" if {"tools", "tool_choice"} & (removed | retry_removed) else "pass",
            [
                "tools preserved" if "tools" in normalized else "tools removed",
                ("tool_choice preserved" if "tool_choice" in normalized else "tool_choice absent"),
            ],
            [
                *(["parallel_tool_calls removed"] if "parallel_tool_calls" in removed else []),
                *(["tool schema normalized"] if "tools" in changed else []),
                *(["fallback may drop tool_choice"] if "tool_choice" in retry_removed else []),
                *(["fallback may drop tools"] if "tools" in retry_removed else []),
            ],
        ),
        _compat_capability_row(
            "structured_output",
            "warn" if "response_format" in retry_removed else "unverified",
            [
                (
                    "response_format preserved"
                    if "response_format" in normalized
                    else "response_format absent"
                ),
            ],
            [
                "dry_run_does_not_validate_response_schema",
                *(
                    ["strict fallback may drop response_format"]
                    if "response_format" in retry_removed
                    else []
                ),
            ],
        ),
        _compat_capability_row(
            "reasoning_request",
            "warn"
            if {"reasoning_effort", "thinking"}
            & (removed | changed | retry_removed | retry_changed)
            else "pass",
            [
                (
                    "reasoning_effort preserved"
                    if "reasoning_effort" in normalized
                    else "reasoning_effort absent"
                ),
                "thinking preserved" if "thinking" in normalized else "thinking absent",
            ],
            [
                *(
                    ["reasoning request normalized"]
                    if {"reasoning_effort", "thinking"} & (removed | changed)
                    else []
                ),
                *(
                    ["fallback may drop reasoning fields"]
                    if {"reasoning_effort", "thinking"} & retry_removed
                    else []
                ),
            ],
        ),
        _compat_capability_row(
            "usage_accounting",
            "warn" if "stream_options" in retry_removed else "unverified",
            [
                (
                    "stream usage requested"
                    if normalized.get("stream_options", {}).get("include_usage") is True
                    else "stream usage not requested"
                ),
            ],
            [
                "response_usage_shape_not_called_in_dry_run",
                *(
                    ["strict fallback may drop stream usage"]
                    if "stream_options" in retry_removed
                    else []
                ),
            ],
        ),
        _compat_capability_row(
            "fallback_retries",
            "pass" if retry_plan else "unverified",
            [
                f"{len(retry_plan)} retry variants planned",
                *retry_reasons[:4],
            ],
            ["dry_run_representative_400"],
        ),
    ]

    risk_reasons: list[str] = []
    risk_points = 0

    def add_risk(reason: str, points: int = 1) -> None:
        nonlocal risk_points
        if reason not in risk_reasons:
            risk_reasons.append(reason)
        risk_points += points

    if compat_score < 80:
        add_risk(f"compat_score:{compat_score}", 2 if compat_score < 70 else 1)
    if {"reasoning_effort", "thinking"} & (removed | changed):
        add_risk("reasoning_request_normalized", 1)
    if {"temperature", "top_p", "presence_penalty", "frequency_penalty"} & removed:
        add_risk("sampling_parameters_removed", 1)
    if "parallel_tool_calls" in removed:
        add_risk("parallel_tool_calls_removed", 1)
    if "tool_choice" in removed:
        add_risk("tool_calling_control_removed", 2)
    if "tools" in changed:
        add_risk("tool_schema_normalized", 1)
    if {"tool_choice", "tools"} & retry_removed:
        add_risk("tool_calling_fallback_degrades_control", 1)
    if {"response_format", "stream_options"} & retry_removed:
        add_risk("strict_provider_may_drop_optional_features", 0)
    if {"model", "messages"} & removed:
        add_risk("core_request_field_removed", 3)
    if "tools" in removed:
        add_risk("tool_calling_removed", 2)

    if risk_points >= 5:
        risk_level = "high"
    elif risk_points >= 2:
        risk_level = "medium"
    else:
        risk_level = "low"

    return {
        "risk_level": risk_level,
        "risk_reasons": risk_reasons,
        "capability_matrix": matrix,
    }


def _compat_capability_row(
    capability: str,
    status: str,
    evidence: list[str],
    notes: list[str],
) -> dict[str, Any]:
    return {
        "capability": capability,
        "status": status,
        "evidence": [item for item in evidence if item],
        "notes": [item for item in notes if item],
    }


def _smoke_provider_by_id() -> dict[str, Any]:
    try:
        from runtime.sensing.model_router.openai_compat_smoke_matrix import (
            openai_compat_smoke_providers,
        )
    except Exception:  # pragma: no cover - optional module import guard
        return {}
    return {provider.id: provider for provider in openai_compat_smoke_providers()}


def _normalize_thinking_fields(
    payload: dict[str, Any],
    profile: OpenAICompatProviderProfile,
) -> None:
    if "reasoning_effort" not in payload and "thinking" not in payload:
        return
    if profile.thinking_request_style == "openai":
        return
    if profile.thinking_request_style == "deepseek":
        _normalize_deepseek_thinking(payload)
        return
    if profile.thinking_request_style == "minimax_adaptive":
        payload.pop("reasoning_effort", None)
        payload["thinking"] = {"type": "adaptive"}
        return
    payload.pop("reasoning_effort", None)
    payload.pop("thinking", None)


_DEEPSEEK_EFFORT_VOCAB = frozenset({"off", "high", "max"})
"""DeepSeek's native reasoning_effort vocabulary (V4): off | high | max."""

_DEEPSEEK_EFFORT_FALLBACKS = {
    "minimal": "high",
    "low": "high",
    "medium": "high",
    "high": "high",
    "xhigh": "max",
    "max": "max",
}
"""OpenAI-style efforts map into the DeepSeek vocabulary; only high/max keep
thinking enabled, anything below high is promoted rather than dropped."""


def _normalize_deepseek_thinking(payload: dict[str, Any]) -> None:
    """DeepSeek-native thinking normalization (V4 wire contract).

    * ``reasoning_effort: off`` → ``thinking: {type: disabled}`` and the
      effort field is dropped (DeepSeek rejects ``off`` as an effort value).
    * ``reasoning_effort: high|max`` (or an OpenAI-style effort mapped into
      that vocabulary) → ``thinking: {type: enabled}`` with the effort kept.
    * ``thinking: {type: disabled}`` alone → stays disabled.
    * ``thinking: {type: enabled}`` alone → stays enabled without an effort.
    """

    thinking = payload.get("thinking")
    effort_raw = payload.get("reasoning_effort")
    effort = str(effort_raw or "").strip().lower() if effort_raw is not None else None

    if effort == "off":
        payload["thinking"] = {"type": "disabled"}
        payload.pop("reasoning_effort", None)
        return

    if effort in _DEEPSEEK_EFFORT_VOCAB:
        payload["thinking"] = {"type": "enabled"}
        payload["reasoning_effort"] = effort
        return

    if effort in _DEEPSEEK_EFFORT_FALLBACKS:
        payload["thinking"] = {"type": "enabled"}
        payload["reasoning_effort"] = _DEEPSEEK_EFFORT_FALLBACKS[effort]
        return

    if isinstance(thinking, dict) and thinking.get("type") in ("enabled", "disabled"):
        payload["thinking"] = {"type": thinking["type"]}
        # An effort outside the native vocabulary is invalid on the wire;
        # the explicit thinking flag wins and the effort is dropped.
        payload.pop("reasoning_effort", None)
        return

    # Unknown effort/thinking shape: drop both rather than risk a 400.
    payload.pop("reasoning_effort", None)
    payload.pop("thinking", None)


def _remove_sampling_parameters(payload: dict[str, Any]) -> None:
    for key in (
        "temperature",
        "top_p",
        "presence_penalty",
        "frequency_penalty",
        "logit_bias",
        "seed",
    ):
        payload.pop(key, None)


def _payload_has_sampling(payload: dict[str, Any]) -> bool:
    return any(
        key in payload
        for key in (
            "temperature",
            "top_p",
            "presence_penalty",
            "frequency_penalty",
            "logit_bias",
            "seed",
        )
    )


def _payload_fingerprint(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)


def _payload_delta(
    original: dict[str, Any],
    candidate: dict[str, Any],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    original_keys = set(original)
    candidate_keys = set(candidate)
    removed = tuple(sorted(original_keys - candidate_keys))
    added = tuple(sorted(candidate_keys - original_keys))
    changed = tuple(
        sorted(
            key for key in original_keys & candidate_keys if original.get(key) != candidate.get(key)
        )
    )
    return removed, added, changed


def _coerce_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_string_tuple(value: Any) -> tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",")]
    elif isinstance(value, list):
        items = [str(item).strip() for item in value]
    else:
        return None
    return tuple(item for item in items if item)


def _has_explicit_compat_override(entry: dict[str, Any]) -> bool:
    if _profile_by_id(entry.get("compat_profile")) is not None:
        return True
    return any(
        entry.get(field_name) is not None
        for field_name in (
            "thinking_request_style",
            "drop_tool_choice",
            "strict_tool_schema",
            "retry_without_tool_choice",
            "retry_without_sampling",
            "retry_max_tokens_as_completion_tokens",
            "max_temperature",
            "unsupported_request_fields",
        )
    )


def _mentions_any(haystack: str, needles: tuple[str, ...]) -> bool:
    return any(needle in haystack for needle in needles)


def _mentioned_payload_fields(
    haystack: str,
    payload: dict[str, Any],
    field_names: tuple[str, ...],
) -> tuple[str, ...]:
    return tuple(
        field_name
        for field_name in field_names
        if field_name in payload and _mentions_field(haystack, field_name)
    )


def _mentions_field(haystack: str, field_name: str) -> bool:
    needle = re.escape(field_name)
    return re.search(rf"(^|[^a-z0-9_]){needle}([^a-z0-9_]|$)", haystack) is not None


def _mentions_tool_use_unsupported(haystack: str) -> bool:
    return _mentions_any(
        haystack,
        (
            "tools is not supported",
            "tools are not supported",
            "tool calls are not supported",
            "tool calling is not supported",
            "function calling is not supported",
            "function calls are not supported",
            "unsupported parameter: tools",
            "unsupported field: tools",
            "unknown field: tools",
            "unrecognized field: tools",
        ),
    )


def _strict_tools(value: Any) -> Any:
    if not isinstance(value, list):
        return value
    tools: list[Any] = []
    for tool in value:
        if not isinstance(tool, dict):
            tools.append(tool)
            continue
        copied = dict(tool)
        fn = copied.get("function")
        if isinstance(fn, dict):
            fn_copy = dict(fn)
            name = fn_copy.get("name", "")
            if "-" in name:
                continue
            params = fn_copy.get("parameters")
            if isinstance(params, dict):
                fn_copy["parameters"] = _normalize_strict_json_schema(params)
            copied["function"] = fn_copy
        tools.append(copied)
    return tools


def _normalize_strict_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "additionalProperties" or key in _STRICT_SCHEMA_DROPPED_KEYS:
            continue
        if isinstance(value, dict):
            out[key] = _normalize_strict_json_schema(value)
        elif isinstance(value, list):
            out[key] = [
                _normalize_strict_json_schema(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            out[key] = value
    return out


__all__ = [
    "GENERIC_OPENAI_PROFILE",
    "OpenAICompatProviderProfile",
    "OpenAICompatProfileProbe",
    "OpenAICompatRetryPayload",
    "REQUIRED_DOMESTIC_PROFILE_IDS",
    "InlineReasoningSplitter",
    "apply_custom_openai_compat_profile",
    "audit_openai_compat_profile_catalog",
    "describe_openai_compat_profile",
    "effective_supported_efforts",
    "extract_openai_compat_reasoning",
    "extract_openai_compat_usage",
    "known_openai_compat_profiles",
    "normalize_openai_compat_payload",
    "openai_compat_profile_ids",
    "parse_tool_call_arguments",
    "plan_openai_compat_retries",
    "resolve_openai_compat_profile",
    "retry_payloads_after_openai_compat_error",
    "sample_openai_compat_profile_probe",
    "split_inline_reasoning",
]
