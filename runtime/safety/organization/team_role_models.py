"""Persist per-role cheap/primary model tier overrides.

The team-role-models settings panel needs three tiny helpers:

* ``role_defaults()``  — the built-in per-role default tier
* ``load_overrides()`` — operator overrides currently on disk
* ``save_overrides()`` — normalize + persist overrides

Storage lives in ``data/team_role_models.json`` beside the other small
runtime-owned JSON settings files.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from runtime.platform.io import atomic_write_json, read_json_with_backup
from runtime.platform.process.paths import app_paths
from runtime.safety.organization.topology import Role

_OVERRIDE_TIERS = frozenset({"cheap", "primary"})
_DEFAULT_PRIMARY_ROLES = frozenset(
    {
        Role.PLANNER.value,
        Role.GENERATOR.value,
        Role.SYNTHESIZER.value,
    }
)
_ROLE_ORDER: tuple[str, ...] = (
    Role.PLANNER.value,
    Role.GENERATOR.value,
    Role.SYNTHESIZER.value,
    Role.RESEARCHER.value,
    Role.CRITIC.value,
    Role.EVALUATOR.value,
    "reviewer",
    "fact_checker",
    "verifier",
    "arbiter",
    "architect",
    "designer",
    "implementer",
    "security",
    "performance",
    "style",
    "reproducer",
    "hypothesizer",
    "debugger",
    "explorer",
)
_CONFIG: Path | None = None


def _store_path() -> Path:
    return _CONFIG or (app_paths().data_dir / "team_role_models.json")


def _normalize_overrides(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, str] = {}
    for role, tier in raw.items():
        role_name = str(role or "").strip()
        tier_name = str(tier or "").strip().lower()
        if role_name and tier_name in _OVERRIDE_TIERS:
            normalized[role_name] = tier_name
    return normalized


def role_defaults() -> dict[str, str]:
    return {
        role: ("primary" if role in _DEFAULT_PRIMARY_ROLES else "cheap") for role in _ROLE_ORDER
    }


def _load_env_overrides() -> dict[str, str]:
    raw = os.environ.get("ECHO_TEAM_ROLE_MODELS")
    if not raw:
        return {}
    try:
        parsed: Any = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return _normalize_overrides(parsed)


def load_overrides() -> dict[str, str]:
    env_overrides = _load_env_overrides()
    if env_overrides:
        return env_overrides
    raw = read_json_with_backup(_store_path(), default={})
    return _normalize_overrides(raw)


def save_overrides(overrides: dict[str, str]) -> dict[str, str]:
    normalized = _normalize_overrides(overrides)
    atomic_write_json(_store_path(), normalized, indent=2, ensure_ascii=False)
    return normalized


def role_uses_cheap(
    role: Role | str,
    *,
    overrides: dict[str, str] | None = None,
) -> bool:
    role_name = role.value if isinstance(role, Role) else str(role or "").strip()
    default_tier = role_defaults().get(role_name, "primary")
    selected = (overrides if overrides is not None else load_overrides()).get(role_name)
    tier = default_tier if selected in (None, "", "default") else str(selected).lower()
    if tier not in _OVERRIDE_TIERS:
        tier = default_tier
    return tier == "cheap"


__all__ = ["load_overrides", "role_defaults", "role_uses_cheap", "save_overrides"]
