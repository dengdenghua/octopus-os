"""Operator-declared capability flags from ``custom_models.json``.

The OpenAI-compatible router consults these to decide, per model id,
whether to send ``tools``, sampling knobs, or thinking parameters.
Kept apart from the router so the flag semantics stay reusable and the
router module stays under the god-file threshold.
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
    """Return False when ``custom_models.json`` (or per-model env
    overrides) marks this model id as not supporting native
    function calling.

    Default is True — most OpenAI-compatible endpoints honor
    ``tools``. We only flip to False when the operator has
    explicitly declared incompatibility, so we don't accidentally
    disable working providers.
    """
    entry = custom_model_entry_for(model)
    return not (isinstance(entry, dict) and entry.get("supports_tool_use") is False)


def model_omits_sampling_parameters(model: str) -> bool:
    """Return True for strict OpenAI-compatible coding endpoints.

    Some coding-model gateways reject sampling knobs entirely (or
    require their undocumented defaults). Operators can declare
    ``omit_sampling_parameters=true`` in ``custom_models.json`` so
    Echo sends only model/messages/max_tokens/tool fields.
    """
    entry = custom_model_entry_for(model)
    return bool(entry.get("omit_sampling_parameters")) if isinstance(entry, dict) else False


def custom_model_supports_thinking(model: str) -> bool:
    entry = custom_model_entry_for(model)
    return bool(entry.get("supports_thinking")) if isinstance(entry, dict) else False


def model_context_window(model: str) -> int | None:
    """Return the operator-declared input window for a custom model."""
    data = read_custom_models()
    selection = resolve_custom_model_selection(data, model) if isinstance(data, dict) else None
    entry = selection.entry if selection is not None else custom_model_entry_for(model)
    if not isinstance(entry, dict):
        return None
    if (
        selection is not None and selection.context_profile == LONG_CONTEXT_PROFILE
    ) or model.strip().endswith("::1m"):
        return 1_000_000
    raw_context_window = entry.get("context_window")
    if raw_context_window is None:
        return None
    try:
        value = int(raw_context_window)
    except (TypeError, ValueError):
        return None
    return value if 8_192 <= value <= 2_000_000 else 256_000
