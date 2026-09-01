"""
Pure helper functions for the config router.

Split out of config_router.py (pure structural refactor — no logic
changes). Imported back by config_router.py.

These are the stateless helpers that interpret a single custom-model
``entry`` dict (model-id / upstreams / context-window resolution,
wire-safe row emission, OpenAI-compat diagnostics) plus the built-in
compat-profile catalog. They touch no closure state and no router
state, so they can live at module level without changing behavior.
"""

from __future__ import annotations

from typing import Any

from runtime.platform.models.custom_model_selection import (
    custom_model_1m_enabled,
    custom_model_selection_id,
    custom_model_upstreams,
    selections_for_entry,
)


def _entry_model_id(entry: dict[str, Any]) -> str:
    raw = entry.get("id") or entry.get("name")
    return raw if isinstance(raw, str) else ""


def _entry_upstreams(entry: dict[str, Any], model_id: str) -> list[str]:
    # Read the open-ended ``models`` list. Falls back to legacy
    # ``model`` + optional ``model_performance`` for entries
    # persisted before the list refactor, so an in-place deploy
    # doesn't lose user config.
    return custom_model_upstreams(entry, model_id)


def _entry_context_window(entry: dict[str, Any], upstreams: list[str]) -> int:
    """Return the provider input window, distinct from output max_tokens."""
    raw = entry.get("context_window")
    try:
        explicit = int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        explicit = 0
    if 8_192 <= explicit <= 2_000_000:
        return explicit
    # No usable declaration: ask the bundled models.dev snapshot before
    # falling back to a flat guess. Context budgeting already resolves
    # the real window this way (custom_model_flags.model_context_window),
    # and a guess here made the UI report 256k for a model the router was
    # correctly treating as 1M.
    from runtime.platform.models.model_capabilities import known_model_context_window

    for candidate in (*upstreams, entry.get("id"), entry.get("name")):
        if not isinstance(candidate, str) or not candidate.strip():
            continue
        known = known_model_context_window(candidate)
        if known is not None and 8_192 <= known <= 2_000_000:
            return known
    return 256_000


def _entry_1m_enabled(entry: dict[str, Any], upstreams: list[str]) -> bool:
    return custom_model_1m_enabled(entry, upstreams)


def _entry_route_ids(entry: dict[str, Any], fallback_id: str = "") -> list[str]:
    model_id = _entry_model_id(entry) or fallback_id
    route_ids: list[str] = []
    for raw in [model_id, *_entry_upstreams(entry, model_id)]:
        route_id = str(raw or "").strip()
        if route_id and route_id not in route_ids:
            route_ids.append(route_id)
    if _entry_1m_enabled(entry, _entry_upstreams(entry, model_id)):
        route_ids.extend(f"{route_id}::1m" for route_id in list(route_ids))
    # Row-level ids are the unambiguous route. Keep every legacy alias above
    # for stored threads and API clients that have not adopted selection_id.
    for upstream in _entry_upstreams(entry, model_id):
        route_ids.append(custom_model_selection_id(model_id, upstream, "default"))
        if _entry_1m_enabled(entry, _entry_upstreams(entry, model_id)):
            route_ids.append(custom_model_selection_id(model_id, upstream, "1m"))
    return route_ids


def _entry_supported_efforts(entry: dict[str, Any], model: str) -> list[str] | None:
    """UI effort tiers a custom model's resolved profile accepts, or None.

    None → the full default set (off/low/medium/high/xhigh) — an ordinary
    OpenAI-style model. Empty list → no meaningful effort control (adaptive /
    unsupported thinking) and the picker hides it. Otherwise exactly the
    tiers the provider genuinely maps on the wire.
    """
    if str(entry.get("provider") or "openai").lower() not in {
        "openai",
        "openai-compatible",
        "openai_compat",
        "custom",
    }:
        return None
    from runtime.sensing.model_router.openai_compat_providers import (
        apply_custom_openai_compat_profile,
        effective_supported_efforts,
        resolve_openai_compat_profile,
    )

    base_url = str(entry.get("base_url") or "")
    base_profile = resolve_openai_compat_profile(base_url, model)
    profile = apply_custom_openai_compat_profile(entry, base_profile=base_profile)
    efforts = effective_supported_efforts(profile)
    return list(efforts) if efforts is not None else None


def _compat_diagnostic_for_entry(entry: dict[str, Any]) -> dict[str, Any]:
    provider = str(entry.get("provider") or "openai").lower()
    model_id = _entry_model_id(entry)
    base_url = str(entry.get("base_url") or "")
    upstreams = _entry_upstreams(entry, model_id)
    header_names = sorted(
        str(name) for name in (entry.get("default_headers") or {}) if str(name).strip()
    )
    if provider not in {"openai", "openai-compatible", "openai_compat", "custom"}:
        return {
            "id": model_id,
            "provider": provider,
            "applicable": False,
            "reason": "provider is not OpenAI-compatible",
            "upstreams": upstreams,
            "default_header_names": header_names,
        }

    from runtime.sensing.model_router.openai_compat_providers import (
        apply_custom_openai_compat_profile,
        describe_openai_compat_profile,
        probe_openai_compat_request_contract,
        resolve_openai_compat_profile,
    )

    rows: list[dict[str, Any]] = []
    for upstream in upstreams:
        base_profile = resolve_openai_compat_profile(base_url, upstream)
        profile = apply_custom_openai_compat_profile(
            entry,
            base_profile=base_profile,
        )
        profile_summary = describe_openai_compat_profile(profile)
        request_contract = probe_openai_compat_request_contract(
            profile,
            upstream,
        )
        rows.append(
            {
                "model": upstream,
                "profile": profile.id,
                "profile_display_name": profile.display_name,
                "profile_summary": profile_summary,
                "compat_score": profile_summary["compat_score"],
                "normalization_hints": profile_summary["normalization_hints"],
                "compatibility_notes": profile_summary["notes"],
                "thinking_request_style": profile.thinking_request_style,
                "supports_vision": profile.supports_vision,
                "omit_sampling_parameters": profile.omit_sampling_parameters,
                "drop_tool_choice": profile.drop_tool_choice,
                "strict_tool_schema": profile.strict_tool_schema,
                "max_temperature": profile.max_temperature,
                "unsupported_request_fields": list(
                    profile.unsupported_request_fields,
                ),
                "dry_run": True,
                "risk_level": request_contract["risk_level"],
                "risk_reasons": request_contract["risk_reasons"],
                "capability_matrix": request_contract["capability_matrix"],
                "request_contract": request_contract,
                "normalization": {
                    "removed_fields": request_contract["removed_fields"],
                    "added_fields": request_contract["added_fields"],
                    "changed_fields": request_contract["changed_fields"],
                    "normalized_fields": request_contract["normalized_fields"],
                    "payload": request_contract["normalized_payload"],
                },
                "fallback_retries": request_contract["fallback_retries"],
            }
        )
    return {
        "id": model_id,
        "provider": provider,
        "base_url": base_url,
        "applicable": True,
        "has_api_key": _entry_has_api_key(entry),
        "default_header_names": header_names,
        "upstreams": rows,
    }


def _default_header_names(entry: dict[str, Any]) -> list[str]:
    headers = entry.get("default_headers") or {}
    if not isinstance(headers, dict):
        return []
    return sorted(str(name) for name in headers if str(name).strip())


def _entry_has_api_key(entry: dict[str, Any]) -> bool:
    if entry.get("api_key") or entry.get("credential_configured") is True:
        return True
    if not entry.get("credential_ref"):
        return False
    from runtime.platform.models.model_provider_plugin import (
        model_provider_entry_has_key,
    )

    return model_provider_entry_has_key(entry)


def _custom_model_wire_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Return a custom-model row safe for browser/API responses.

    ``api_key`` and ``default_headers`` both contain secrets. The
    browser only needs presence + header names so users can see what is
    configured without receiving bearer tokens or route keys back over the
    wire.

    ``supports_vision`` is inferred from the compat profile when the
    profile explicitly declares support (True) or lack of support (False).
    This overrides any user-set value to prevent capability mismatches.
    """
    header_names = _default_header_names(entry)
    safe = {
        k: v
        for k, v in entry.items()
        if k
        not in {
            "api_key",
            "credential_ref",
            "credential_configured",
            "default_headers",
        }
    }
    model_id = _entry_model_id(entry)
    upstreams = _entry_upstreams(entry, model_id)
    safe["context_window"] = _entry_context_window(
        entry,
        upstreams,
    )
    safe["enable_1m_context"] = _entry_1m_enabled(entry, upstreams)
    safe["has_api_key"] = _entry_has_api_key(entry)
    safe["default_header_names"] = header_names
    safe["has_default_headers"] = bool(header_names)
    safe["selection_ids"] = [
        selection.selection_id for selection in selections_for_entry(model_id, entry)
    ]
    if upstreams:
        safe["reasoning_efforts"] = _entry_supported_efforts(entry, upstreams[0])

    # Override supports_vision from compat profile if explicitly declared
    from runtime.sensing.model_router.openai_compat_providers import (
        resolve_openai_compat_profile,
    )

    base_url = str(entry.get("base_url") or "")
    if upstreams and base_url:
        profile = resolve_openai_compat_profile(base_url, upstreams[0])
        # If profile explicitly declares vision support (True or False), use that
        if profile.supports_vision is not None:
            safe["supports_vision"] = profile.supports_vision

    return safe


def _builtin_openai_compat_catalog() -> list[dict[str, Any]]:
    from runtime.sensing.model_router.openai_compat_providers import (
        known_openai_compat_profiles,
        sample_openai_compat_profile_probe,
    )

    catalog: list[dict[str, Any]] = []
    for profile in known_openai_compat_profiles():
        probe = sample_openai_compat_profile_probe(profile)
        diagnostic = _compat_diagnostic_for_entry(
            {
                "id": profile.id,
                "name": profile.display_name,
                "provider": "openai",
                "base_url": probe.base_url,
                "models": [probe.model],
                "compat_profile": profile.id,
            }
        )
        diagnostic["built_in"] = True
        diagnostic["sample_base_url"] = probe.base_url
        diagnostic["sample_model"] = probe.model
        diagnostic["smoke_provider_configured"] = probe.smoke_provider_configured
        diagnostic["resolver_check"] = {
            "base_url_resolves_to": probe.base_url_resolves_to,
            "model_resolves_to": probe.model_resolves_to,
            "model_alias_mismatch": probe.model_resolves_to != profile.id,
            "passed": probe.base_url_resolves_to == profile.id,
        }
        catalog.append(diagnostic)
    return catalog


__all__ = [
    "_builtin_openai_compat_catalog",
    "_compat_diagnostic_for_entry",
    "_custom_model_wire_entry",
    "_default_header_names",
    "_entry_1m_enabled",
    "_entry_context_window",
    "_entry_model_id",
    "_entry_route_ids",
    "_entry_supported_efforts",
    "_entry_upstreams",
]
