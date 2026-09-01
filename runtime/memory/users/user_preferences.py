"""User long-term preferences loader for system-prompt injection.

Reads ``data/user_preferences.json`` (under ``app_paths().data_dir``) and
returns a merged ``{key: value}`` view for the requested actor.

Format::

    {
      "default": {"indent": "4 spaces"},
      "actor_id_x": {"language": "Chinese first"}
    }

Best-effort. Never raises. Returns ``{}`` on any error / missing file /
missing actor / malformed JSON.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from runtime.platform.process.paths import app_paths

__all__ = ["_load_user_preferences", "preferences_path"]


def preferences_path() -> Path:
    """Return the configured ``data/user_preferences.json`` path."""
    return app_paths().data_dir / "user_preferences.json"


def _load_user_preferences(
    actor: str | None,
    *,
    path: Path | None = None,
) -> dict[str, str]:
    """Read user preferences from the configured user store, or {}.

    Best-effort — never raises. Returns {} when no store / no actor /
    any error.

    The returned dict is the merged view of the ``default`` block plus
    the actor-specific block (actor wins on key conflict). Values are
    coerced to ``str`` for safe rendering into the system prompt.
    """
    try:
        target = path if path is not None else preferences_path()
        try:
            raw_text = Path(target).read_text(encoding="utf-8")
        except (FileNotFoundError, OSError, UnicodeDecodeError):
            return {}
        try:
            raw = json.loads(raw_text)
        except (TypeError, ValueError):
            return {}
        if not isinstance(raw, dict):
            return {}

        merged: dict[str, str] = {}
        default_block = raw.get("default")
        if isinstance(default_block, dict):
            merged.update(_coerce_pref_block(default_block))

        if actor:
            actor_key = str(actor).strip()
            if actor_key:
                actor_block = raw.get(actor_key)
                if isinstance(actor_block, dict):
                    merged.update(_coerce_pref_block(actor_block))

        return merged
    except Exception:  # noqa: BLE001 - best-effort, must not raise
        return {}


def _coerce_pref_block(block: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in block.items():
        if not isinstance(key, str):
            continue
        clean_key = key.strip()
        if not clean_key:
            continue
        if value is None:
            continue
        out[clean_key] = str(value)
    return out
