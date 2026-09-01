"""Operator-declared capability flags from ``custom_models.json``.

This is the layer-neutral source of truth used by both the core context
budgeting path and sensing adapters.
"""

from __future__ import annotations

import json
from typing import Any

from runtime.platform.models.custom_model_selection import (
    LONG_CONTEXT_PROFILE,
    resolve_custom_model_selection,
    selections_for_entry,
)


def read_custom_models() -> dict[str, Any] | None:
    try:
        from runtime.platform.process.paths import app_paths

        path = app_paths().custom_models_path
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, ImportError, TypeError):
        return None


def entry_matches_model(entry: Any, model: str) -> bool:
    if not isinstance(entry, dict):
        return False
    target = (model or "").strip()
    if not target:
        return False
    entry_id = str(entry.get("id") or "").strip()
    if entry_id and any(
        selection.selection_id == target for selection in selections_for_entry(entry_id, entry)
    ):
        return True
    target = target.removesuffix("::1m")
    candidates = {
        str(value).strip()
        for value in (
            entry.get("id"),
            entry.get("name"),
            entry.get("model"),
            entry.get("display_name"),
        )
        if isinstance(value, str) and value.strip()
    }
    raw_models = entry.get("models")
    if isinstance(raw_models, list):
        candidates.update(
            str(value).strip() for value in raw_models if isinstance(value, str) and value.strip()
        )
    return target in candidates


def custom_model_entry_for(model: str) -> dict[str, Any] | None:
    data = read_custom_models()
    if not isinstance(data, dict):
        return None
    selection = resolve_custom_model_selection(data, model)
    if selection is not None:
        return selection.entry
    for entry in data.values():
        if entry_matches_model(entry, model):
            return entry
    return None


def model_supports_tool_use(model: str) -> bool:
    """Return whether the operator permits native function calling."""

    entry = custom_model_entry_for(model)
    return not (isinstance(entry, dict) and entry.get("supports_tool_use") is False)


def model_omits_sampling_parameters(model: str) -> bool:
    """Return whether sampling knobs must be omitted for this model."""

    entry = custom_model_entry_for(model)
    return bool(entry.get("omit_sampling_parameters")) if isinstance(entry, dict) else False


def model_is_openai_compat_endpoint(model: str) -> bool:
    """Whether ``model`` maps to an operator-added OpenAI-compatible endpoint.

    Operator endpoints are the ones declared in ``custom_models.json`` that
    talk the OpenAI chat/responses wire format (``provider: "openai"`` plus a
    custom ``base_url``). Reasoning models across this family (Kimi, DeepSeek,
    Qwen, GLM, Moonshot, …) all honour the extended-thinking envelope and
    silently ignore it when unsupported — so it is safe to probe.
    """
    entry = custom_model_entry_for(model)
    if not isinstance(entry, dict):
        return False
    provider = str(entry.get("provider", "")).lower()
    return provider in ("openai", "") and bool(entry.get("base_url"))


def model_supports_vision(model: str) -> bool | None:
    """Operator-declared vision capability for a model id.

    Mirrors ``model_supports_tool_use`` / ``custom_model_supports_thinking``:
    the ``supports_vision`` field on the ``custom_models.json`` entry wins
    when declared. Unlike the tool-use helper (which defaults True), this
    returns ``None`` when undeclared so the caller can distinguish "known
    non-vision" from "don't know" — the runtime vision guard feeds images
    to unknown models and recovers on a 4xx, but never to a model that is
    explicitly marked ``supports_vision: false``.
    """
    entry = custom_model_entry_for(model)
    if isinstance(entry, dict):
        declared = entry.get("supports_vision")
        if isinstance(declared, bool):
            return declared
    return None


def custom_model_supports_thinking(model: str) -> bool:
    """Operator-declared thinking capability for a custom model.

    This is the single source of truth for the thinking channel on custom
    endpoints, applied uniformly by the cerebrum loop, gateway, and router:

      - ``supports_thinking: true``  -> always on
      - ``supports_thinking: false`` -> always off (opt-out for strict models
        that reject the thinking envelope with a hard 400)
      - absent                       -> forward-compat probe: OpenAI-compatible
        endpoints default to ON, because the upstream ignores the param when
        it does not think. New models therefore "just work" with no code or
        config change — the failure mode the allowlist approach kept hitting.
        Non-compat / first-party models stay conservative (off) and fall
        through to the built-in name allowlist instead.

    Strict models that break on the probe can be pinned off with
    ``supports_thinking: false`` (or ``unsupported_request_fields: ["thinking"]``).
    """
    entry = custom_model_entry_for(model)
    if isinstance(entry, dict):
        declared = entry.get("supports_thinking")
        if declared is True:
            return True
        if declared is False:
            return False
        # absent -> forward-compat default for OpenAI-compatible endpoints
        return model_is_openai_compat_endpoint(model)
    return False


def custom_model_default_reasoning_effort(model: str) -> str | None:
    """Operator-declared default reasoning effort for a custom model.

    ``default_reasoning_effort`` on the ``custom_models.json`` entry:

      - ``"off"`` | ``"high"`` | ``"max"`` — injected when the caller does
        not specify an effort (DeepSeek-native vocabulary).
      - ``"none"`` — disable injection for this model, even the built-in
        name-pattern default.
      - absent — leave to the built-in name patterns.

    Returns ``None`` when absent or malformed, so callers fall through to
    the built-in default rather than dropping the request.
    """

    entry = custom_model_entry_for(model)
    if not isinstance(entry, dict):
        return None
    value = entry.get("default_reasoning_effort")
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in {"off", "high", "max", "none"}:
        return normalized
    return None


def model_context_window(model: str) -> int | None:
    """Return the input window for a custom model.

    An operator's ``context_window`` wins. When the entry does not declare one
    (or declares something out of range), fall back to the bundled models.dev
    snapshot instead of a flat 256k guess: a relay's ``deepseek-v4-flash``
    really has a 1M window, and guessing 128k-256k made context budgeting
    truncate work that would have fit.
    """

    data = read_custom_models()
    selection = resolve_custom_model_selection(data, model) if isinstance(data, dict) else None
    entry = selection.entry if selection is not None else custom_model_entry_for(model)
    if not isinstance(entry, dict):
        return None
    if (
        selection is not None and selection.context_profile == LONG_CONTEXT_PROFILE
    ) or model.strip().endswith("::1m"):
        return 1_000_000
    capability_model = selection.model if selection is not None else model
    raw_context_window = entry.get("context_window")
    if raw_context_window is None:
        return _upstream_context_window(capability_model)
    try:
        value = int(raw_context_window)
    except (TypeError, ValueError):
        return _upstream_context_window(capability_model)
    if 8_192 <= value <= 2_000_000:
        return value
    return _upstream_context_window(capability_model) or 256_000


def _upstream_context_window(model: str) -> int | None:
    """models.dev's window for ``model``, clamped to the range we accept."""
    from runtime.platform.models.model_capabilities import known_model_context_window

    value = known_model_context_window(model)
    if value is None:
        return None
    return value if 8_192 <= value <= 2_000_000 else None


__all__ = [
    "custom_model_entry_for",
    "custom_model_default_reasoning_effort",
    "custom_model_supports_thinking",
    "entry_matches_model",
    "model_context_window",
    "model_omits_sampling_parameters",
    "model_supports_tool_use",
    "model_supports_vision",
    "read_custom_models",
]
