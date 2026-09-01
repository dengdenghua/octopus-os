"""Runtime capability permission switches.

High-level SKILL.md workflows belong in the skill browser. Low-level
runtime tools such as browser control, desktop control, file writes, git,
and shell execution are capabilities; they should be governed through a
small permission surface and checked before execution.
"""

from __future__ import annotations

from threading import RLock
from typing import Any

from runtime.execution.all_skills import skill_group, skills_in_group

CONTROLLED_CAPABILITY_GROUPS: tuple[str, ...] = (
    "builtin",
    "web",
    "browser",
    "computer",
    "fs_write",
    "git",
    "shell",
    "memory",
)

_DEFAULT_ENABLED: dict[str, bool] = {
    "builtin": True,
    "web": True,
    "browser": True,
    "computer": True,
    "fs_write": True,
    "git": True,
    "shell": True,
    "memory": True,
}

_lock = RLock()
_overrides: dict[str, bool] = {}


def permission_group_for_skill(skill_id: str) -> str | None:
    group = skill_group(skill_id)
    if group in CONTROLLED_CAPABILITY_GROUPS:
        return group
    return None


def is_capability_group_enabled(group: str) -> bool:
    if group not in CONTROLLED_CAPABILITY_GROUPS:
        return True
    with _lock:
        return _overrides.get(group, _DEFAULT_ENABLED.get(group, True))


def set_capability_group_enabled(group: str, enabled: bool) -> bool:
    if group not in CONTROLLED_CAPABILITY_GROUPS:
        raise KeyError(group)
    with _lock:
        _overrides[group] = bool(enabled)
        return _overrides[group]


def reset_capability_permissions() -> None:
    with _lock:
        _overrides.clear()


def is_skill_allowed(skill_id: str) -> tuple[bool, str | None]:
    group = permission_group_for_skill(skill_id)
    if group is None:
        return True, None
    if is_capability_group_enabled(group):
        return True, None
    return False, f"capability group disabled: {group}"


def list_capability_permissions(
    *,
    registered_skill_names: set[str] | None = None,
) -> list[dict[str, Any]]:
    registered = registered_skill_names or set()
    out: list[dict[str, Any]] = []
    for group in CONTROLLED_CAPABILITY_GROUPS:
        known = sorted(skills_in_group(group))
        available = [name for name in known if name in registered]
        out.append(
            {
                "id": group,
                "enabled": is_capability_group_enabled(group),
                "available": bool(available),
                "skill_names": available or known,
            }
        )
    return out


__all__ = [
    "CONTROLLED_CAPABILITY_GROUPS",
    "permission_group_for_skill",
    "is_capability_group_enabled",
    "set_capability_group_enabled",
    "reset_capability_permissions",
    "is_skill_allowed",
    "list_capability_permissions",
]
