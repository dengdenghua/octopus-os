from __future__ import annotations

import json
import logging
from typing import Any

from runtime.platform.models.custom_model_selection import (
    resolve_custom_model_selection,
)

_DEFAULT_MAX_TOKENS = 131072


def _resolve_custom_model_router(
    model_name: str,
    router: Any,
) -> tuple[Any, str]:
    """Resolve a user-facing model alias through custom_models.json.

    Looks up ``model_name`` in two ways:

    1. As an entry id (``mimo2.5`` → the mimo entry). When the entry
       has a ``models`` list, the first item is used as the upstream
       model name.
    2. As a variant inside any entry's ``models`` list
       (``mimo-v2.5-flash`` → mimo entry, upstream=``mimo-v2.5-flash``).
       This branch lets the picker show concrete variant rows the
       user can pick directly while still routing through the entry's
       ``base_url`` / ``api_key``.

    Both branches build the appropriate provider router (Anthropic /
    Gemini / OpenAI-compat) using the entry's credentials. Returns
    ``(router, model_name)`` unchanged when no entry matches.
    """
    try:
        from runtime.platform.process.paths import app_paths as _app_paths

        _custom_path = _app_paths().custom_models_path
        if not _custom_path.exists():
            return router, model_name
        _custom_data = json.loads(_custom_path.read_text(encoding="utf-8"))
        if not isinstance(_custom_data, dict):
            return router, model_name
        _selection = resolve_custom_model_selection(_custom_data, model_name)
        _custom_entry = _selection.entry if _selection is not None else None
        _upstream_model = _selection.model if _selection is not None else ""
        _legacy_name = model_name.removesuffix("::1m")
        if _custom_entry is None:
            _custom_entry = _custom_data.get(_legacy_name)
        # Variant lookup: walk every entry's models list and pick the
        # entry whose models[] contains the given name. The variant
        # itself becomes the upstream model name (no slot remap).
        _variant_match = _selection is not None
        if not isinstance(_custom_entry, dict):
            for _entry_id, _entry in _custom_data.items():
                if not isinstance(_entry, dict):
                    continue
                _models = _entry.get("models")
                if isinstance(_models, list) and any(
                    isinstance(_m, str) and _m.strip() == _legacy_name for _m in _models
                ):
                    _custom_entry = _entry
                    _variant_match = True
                    _upstream_model = _legacy_name
                    break
        if not isinstance(_custom_entry, dict):
            return router, model_name
        _provider = str(_custom_entry.get("provider") or "openai").lower()
        _base_url = str(_custom_entry.get("base_url") or "")
        _api_key = str(_custom_entry.get("api_key") or "")
        # When the caller passed a variant name, that exact name IS
        # the upstream model. Otherwise pick the first model in the
        # entry's models[] list (legacy "entry-as-alias" behavior).
        if not _variant_match:
            _raw_models = _custom_entry.get("models")
            if isinstance(_raw_models, list):
                for _m in _raw_models:
                    if isinstance(_m, str) and _m.strip():
                        _upstream_model = _m.strip()
                        break
            if not _upstream_model:
                _legacy = _custom_entry.get("model")
                if isinstance(_legacy, str) and _legacy.strip():
                    _upstream_model = _legacy.strip()
            if not _upstream_model:
                _upstream_model = model_name
        _headers = (
            _custom_entry.get("default_headers")
            if isinstance(_custom_entry.get("default_headers"), dict)
            else None
        )
        if _provider in ("anthropic", "claude"):
            from runtime.sensing.model_router.anthropic_router import AnthropicModelRouter

            new_router: Any = AnthropicModelRouter(
                api_key=_api_key,
                default_model=_upstream_model,
                base_url=(_base_url or None),
            )
        elif _provider in ("gemini", "google"):
            from runtime.sensing.model_router.gemini_router import GeminiModelRouter

            new_router = GeminiModelRouter(
                api_key=_api_key,
                default_model=_upstream_model,
                base_url=(_base_url or "https://generativelanguage.googleapis.com/v1beta"),
                extra_headers=_headers,
            )
        elif _provider in ("openai", "openai-compatible", "openai_compat"):
            from runtime.sensing.model_router.openai_router import OpenAIModelRouter

            new_router = OpenAIModelRouter(
                base_url=_base_url,
                api_key=_api_key,
                default_model=_upstream_model,
                extra_headers=_headers,
                custom_model_entry=_custom_entry,
            )
        else:
            return router, model_name
        resolved_model = _upstream_model
        return new_router, resolved_model
    except Exception as _e:  # noqa: BLE001
        _logger = logging.getLogger(__name__)
        _logger.debug("custom model resolution failed for %s: %s", model_name, _e)
        return router, model_name


def _custom_model_entry(model_name: str) -> dict[str, Any] | None:
    try:
        from runtime.platform.process.paths import app_paths as _app_paths

        _custom_path = _app_paths().custom_models_path
        if not _custom_path.exists():
            return None
        _custom_data = json.loads(_custom_path.read_text(encoding="utf-8"))
        if not isinstance(_custom_data, dict):
            return None
        selection = resolve_custom_model_selection(_custom_data, model_name)
        if selection is not None:
            return selection.entry
        entry = _custom_data.get(model_name.removesuffix("::1m"))
        return entry if isinstance(entry, dict) else None
    except Exception as _e:  # noqa: BLE001
        logging.getLogger(__name__).debug(
            "custom model metadata read failed for %s: %s",
            model_name,
            _e,
        )
        return None


def _entry_matches_model(entry: dict[str, Any], model_name: str) -> bool:
    needle = str(model_name or "").strip()
    if not needle:
        return False
    for key in ("id", "name", "model", "model_performance"):
        if str(entry.get(key) or "").strip() == needle:
            return True
    raw_models = entry.get("models")
    if isinstance(raw_models, list):
        return any(isinstance(item, str) and item.strip() == needle for item in raw_models)
    return False


def _custom_model_entry_for(model_name: str, resolved_model: str) -> dict[str, Any] | None:
    entry = _custom_model_entry(model_name) or _custom_model_entry(resolved_model)
    if entry is not None:
        return entry
    try:
        from runtime.platform.process.paths import app_paths as _app_paths

        custom_path = _app_paths().custom_models_path
        if not custom_path.exists():
            return None
        custom_data = json.loads(custom_path.read_text(encoding="utf-8"))
        if not isinstance(custom_data, dict):
            return None
        for candidate in custom_data.values():
            if not isinstance(candidate, dict):
                continue
            if _entry_matches_model(candidate, model_name) or _entry_matches_model(
                candidate,
                resolved_model,
            ):
                return candidate
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).debug(
            "custom model reverse lookup failed for %s/%s: %s",
            model_name,
            resolved_model,
            exc,
        )
    return None


def _model_runtime_options(model_name: str, resolved_model: str) -> tuple[bool, int | None]:
    entry = _custom_model_entry_for(model_name, resolved_model)
    supports_thinking = (
        bool(entry.get("supports_thinking"))
        if entry is not None and "supports_thinking" in entry
        else _model_supports_thinking(resolved_model)
    )
    provider = str(entry.get("provider") or "").lower() if entry else ""
    if entry is not None and provider in ("openai", "openai-compatible", "openai_compat", ""):
        return supports_thinking, None
    return supports_thinking, _DEFAULT_MAX_TOKENS


# Moved to platform.models.llm (model-capability knowledge belongs with
# the model types). Re-exported here for the gateway's existing callers.
# Moved to adapters.web_auth (shared by adapter + gateway routers; the
# adapter integrations shouldn't depend upward on the gateway just to
# authenticate). Re-exported here for the gateway's existing callers.
from runtime.adapters.web_auth import _resolve_actor  # noqa: E402,F401
from runtime.platform.models.llm import (  # noqa: E402
    model_supports_thinking as _model_supports_thinking,
)
