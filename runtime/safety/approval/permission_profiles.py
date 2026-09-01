"""Built-in permission profile catalog (codex-style, minimal).

Codex ships three sandbox profiles — read_only / workspace_write /
danger_full_access — plus user-defined Toml profiles. Echo already has
a rule engine (data/permissions.json) + the network-access tiers, so we
only add the three built-in profiles as READY-MADE rule sets:

- ``read_only``: only reads/lists/compute allowed; everything else denies.
- ``workspace_write`` (default): the existing data/permissions.json rules
  (dangerous tools still ask) — behavior is byte-identical to today.
- ``full_access``: everything allowed (auto-approve).

A profile is selected via the ``profile`` field in data/permissions.json.
Users never edit rule sets by hand for these — they pick one of three;
advanced per-rule editing stays in the existing rules array (explicitly
NOT exposed as a Toml-editing surface, per "用户不会一个个配").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from runtime.safety.approval.approval_gate import ApprovalRule

PermissionProfileName = Literal["read_only", "workspace_write", "full_access"]

DEFAULT_PROFILE: PermissionProfileName = "workspace_write"

_ALLOW_READ_RULES: tuple[ApprovalRule, ...] = (
    ApprovalRule(effect="allow", tool="read_*", reason="profile read_only: pure read"),
    ApprovalRule(effect="allow", tool="list_*", reason="profile read_only: pure list"),
    ApprovalRule(effect="allow", tool="glob_*", reason="profile read_only: pure glob"),
    ApprovalRule(effect="allow", tool="grep_*", reason="profile read_only: pure grep"),
    ApprovalRule(effect="allow", tool="file_stats", reason="profile read_only: metadata"),
    ApprovalRule(effect="allow", tool="count_words", reason="profile read_only: compute"),
    ApprovalRule(effect="allow", tool="hash_text", reason="profile read_only: compute"),
    ApprovalRule(effect="deny", tool="*", reason="profile read_only: default deny"),
)

_FULL_ACCESS_RULES: tuple[ApprovalRule, ...] = (
    ApprovalRule(effect="allow", tool="*", reason="profile full_access: everything"),
)


@dataclass(frozen=True, slots=True)
class PermissionProfile:
    name: PermissionProfileName
    rules: tuple[ApprovalRule, ...] = field(default_factory=tuple)
    auto_approve: bool = False


_BUILT_IN_PROFILES: dict[PermissionProfileName, PermissionProfile] = {
    "read_only": PermissionProfile(name="read_only", rules=_ALLOW_READ_RULES),
    "workspace_write": PermissionProfile(name="workspace_write", rules=()),
    "full_access": PermissionProfile(
        name="full_access", rules=_FULL_ACCESS_RULES, auto_approve=True
    ),
}


def normalize_profile_name(value: object) -> PermissionProfileName:
    if isinstance(value, str) and value in _BUILT_IN_PROFILES:
        return value  # type: ignore[return-value]
    return DEFAULT_PROFILE


def built_in_profile(name: PermissionProfileName) -> PermissionProfile:
    return _BUILT_IN_PROFILES[name]


def resolve_profile(
    requested: object,
    existing_rules: tuple[ApprovalRule, ...],
) -> tuple[PermissionProfileName, tuple[ApprovalRule, ...], bool]:
    """Decide the effective profile + rule set.

    ``requested`` is the ``profile`` field from data/permissions.json.
    - explicit profile read_only / full_access → its built-in rules win.
    - workspace_write or absent → the existing rules array (today's
      behavior), auto_approve=False.
    """
    name = normalize_profile_name(requested)
    profile = _BUILT_IN_PROFILES[name]
    if name == "workspace_write":
        return name, existing_rules, False
    return name, profile.rules, profile.auto_approve
