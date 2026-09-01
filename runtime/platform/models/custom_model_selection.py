"""Stable, opaque row identifiers for operator-configured model routes.

The browser must be able to distinguish all three dimensions of a picker row:
the custom endpoint entry, its concrete upstream model, and its context profile.
None of those dimensions can safely be inferred from the legacy ``model`` or
``entry_id`` fields because both may be shared by several rows.

``selection_id`` is therefore a versioned digest of those non-secret routing
coordinates.  It is deterministic across restarts, contains no credentials or
base URL, and is resolved only against the current custom-model catalog.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

SELECTION_ID_PREFIX = "echo-custom-model:v1:"
DEFAULT_CONTEXT_PROFILE = "default"
LONG_CONTEXT_PROFILE = "1m"


@dataclass(frozen=True)
class CustomModelSelection:
    selection_id: str
    entry_id: str
    model: str
    context_profile: str
    entry: dict[str, Any]


def custom_model_selection_id(
    entry_id: str,
    model: str,
    context_profile: str = DEFAULT_CONTEXT_PROFILE,
) -> str:
    """Return the stable opaque id for one advertised custom-model row."""

    coordinates = json.dumps(
        ["echo.custom-model-selection.v1", entry_id, model, context_profile],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(coordinates).hexdigest()[:24]
    return f"{SELECTION_ID_PREFIX}{digest}"


def custom_model_upstreams(entry: dict[str, Any], entry_id: str) -> list[str]:
    """Resolve modern and legacy upstream fields without changing their order."""

    raw_models = entry.get("models")
    if isinstance(raw_models, list) and raw_models:
        upstreams = [str(item).strip() for item in raw_models if str(item or "").strip()]
        if upstreams:
            return upstreams

    legacy: list[str] = []
    primary = entry.get("model")
    if isinstance(primary, str) and primary.strip():
        legacy.append(primary.strip())
    performance = entry.get("model_performance")
    if isinstance(performance, str) and performance.strip() and performance.strip() not in legacy:
        legacy.append(performance.strip())
    return legacy or [entry_id]


def custom_model_1m_enabled(entry: dict[str, Any], upstreams: list[str]) -> bool:
    explicit = entry.get("enable_1m_context")
    if isinstance(explicit, bool):
        return explicit
    probe = " ".join(upstreams).lower()
    return any(marker in probe for marker in ("glm-5.2", "deepseek-v4-flash", "deepseek-v4-pro"))


def selections_for_entry(
    entry_id: str,
    entry: dict[str, Any],
) -> Iterator[CustomModelSelection]:
    """Yield every selectable variant/profile coordinate for one endpoint."""

    resolved_entry_id = str(entry.get("id") or entry_id).strip()
    if not resolved_entry_id:
        return
    upstreams = custom_model_upstreams(entry, resolved_entry_id)
    profiles = [DEFAULT_CONTEXT_PROFILE]
    if custom_model_1m_enabled(entry, upstreams):
        profiles.append(LONG_CONTEXT_PROFILE)
    for model in upstreams:
        for profile in profiles:
            yield CustomModelSelection(
                selection_id=custom_model_selection_id(
                    resolved_entry_id,
                    model,
                    profile,
                ),
                entry_id=resolved_entry_id,
                model=model,
                context_profile=profile,
                entry=entry,
            )


def iter_custom_model_selections(
    custom_models: dict[str, Any],
) -> Iterator[CustomModelSelection]:
    for raw_entry_id, entry in custom_models.items():
        if not isinstance(entry, dict):
            continue
        yield from selections_for_entry(str(raw_entry_id), entry)


def resolve_custom_model_selection(
    custom_models: dict[str, Any],
    selection_id: str,
) -> CustomModelSelection | None:
    """Resolve an advertised id; arbitrary or stale digests fail closed."""

    candidate = str(selection_id or "").strip()
    if not candidate.startswith(SELECTION_ID_PREFIX):
        return None
    for selection in iter_custom_model_selections(custom_models):
        if selection.selection_id == candidate:
            return selection
    return None


__all__ = [
    "CustomModelSelection",
    "DEFAULT_CONTEXT_PROFILE",
    "LONG_CONTEXT_PROFILE",
    "SELECTION_ID_PREFIX",
    "custom_model_1m_enabled",
    "custom_model_selection_id",
    "custom_model_upstreams",
    "iter_custom_model_selections",
    "resolve_custom_model_selection",
    "selections_for_entry",
]
