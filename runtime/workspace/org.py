"""Organization data model: Organization / Department / Channel + unified members.

This is the enterprise-space (阶段一) foundation on top of the single-user
``Workspace`` model. It turns the existing single-machine identity into a real
organization tree:

- **Organization** — the top-level tenant (企业空间). Owns departments and
  channels. Mirrors the acceptance criterion "创建组织，加入成员，挂载 Agent".
- **Department** — an optional level of the org tree (``parent_id`` chains a
  nested hierarchy). A channel may live directly under an org or under a
  department.
- **Channel** — an enterprise collaboration space (频道/群聊). A channel is a
  first-class citizen with its own member ACL, decoupled from the cowork
  ``thread_id`` it may later be bridged to.
- **OrgMember / ChannelMember** — the *unified member model*: Human and Agent
  share the same identity row (``kind`` disambiguates, matching the roadmap
  "同一身份体系可查 Human 与 Agent"). Agent bindings will additionally carry
  tool permissions, data scope and budget in a later stage.

Pure dataclasses with dict round-trips so the store can be SQLite-backed and
the engine stays I/O-free — same pattern as ``runtime/workspace/model.py``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

MemberKind = Literal["human", "agent"]
OrgRole = Literal["owner", "admin", "member", "viewer"]
ChannelRole = Literal["owner", "admin", "member", "viewer"]
ChannelKind = Literal["channel", "group"]

VALID_MEMBER_KINDS = frozenset({"human", "agent"})
VALID_ORG_ROLES = frozenset({"owner", "admin", "member", "viewer"})
VALID_CHANNEL_ROLES = frozenset({"owner", "admin", "member", "viewer"})
VALID_CHANNEL_KINDS = frozenset({"channel", "group"})

# Roles that can administer (membership / ACL) an org or channel.
_ORG_ADMIN_ROLES = frozenset({"owner", "admin"})
_CHANNEL_ADMIN_ROLES = frozenset({"owner", "admin"})


@dataclass
class Organization:
    """A top-level enterprise tenant (企业空间)."""

    id: str
    name: str
    owner_id: str = ""
    created_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Organization:
        return cls(
            id=str(raw["id"]),
            name=str(raw.get("name") or raw["id"]),
            owner_id=str(raw.get("owner_id") or ""),
            created_at=float(raw.get("created_at") or 0.0),
        )


@dataclass
class Department:
    """A (possibly nested) node of the organization tree."""

    id: str
    org_id: str
    name: str
    parent_id: str | None = None
    created_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Department:
        parent = raw.get("parent_id")
        return cls(
            id=str(raw["id"]),
            org_id=str(raw["org_id"]),
            name=str(raw.get("name") or raw["id"]),
            parent_id=str(parent) if parent else None,
            created_at=float(raw.get("created_at") or 0.0),
        )


@dataclass
class OrgMember:
    """A unified (Human or Agent) member of an organization."""

    org_id: str
    member_id: str
    kind: MemberKind = "agent"
    role: OrgRole = "member"
    display_name: str = ""
    added_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> OrgMember:
        return cls(
            org_id=str(raw["org_id"]),
            member_id=str(raw["member_id"]),
            kind="human" if raw.get("kind") == "human" else "agent",
            role=_coerce_role(raw.get("role"), VALID_ORG_ROLES, "member"),
            display_name=str(raw.get("display_name") or ""),
            added_at=float(raw.get("added_at") or 0.0),
        )


@dataclass
class Channel:
    """An enterprise collaboration space (频道/群聊) owned by an org."""

    id: str
    org_id: str
    name: str
    kind: ChannelKind = "channel"
    department_id: str | None = None
    created_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Channel:
        dept = raw.get("department_id")
        return cls(
            id=str(raw["id"]),
            org_id=str(raw["org_id"]),
            name=str(raw.get("name") or raw["id"]),
            kind=_coerce_kind(raw.get("kind")),
            department_id=str(dept) if dept else None,
            created_at=float(raw.get("created_at") or 0.0),
        )


@dataclass
class ChannelMember:
    """Channel ACL row: which member may see/act in a channel."""

    channel_id: str
    member_id: str
    role: ChannelRole = "member"
    added_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ChannelMember:
        return cls(
            channel_id=str(raw["channel_id"]),
            member_id=str(raw["member_id"]),
            role=_coerce_role(raw.get("role"), VALID_CHANNEL_ROLES, "member"),
            added_at=float(raw.get("added_at") or 0.0),
        )


def _coerce_role(raw: Any, valid: frozenset[str], default: str) -> str:
    value = raw
    if isinstance(value, str) and value in valid:
        return value
    return default


def _coerce_kind(raw: Any) -> ChannelKind:
    if isinstance(raw, str) and raw in VALID_CHANNEL_KINDS:
        return raw  # type: ignore[return-value]
    return "channel"


def role_has_channel_admin(role: str) -> bool:
    """Whether ``role`` may administer a channel (own channel ACL)."""
    return role in _CHANNEL_ADMIN_ROLES


def role_has_org_admin(role: str) -> bool:
    """Whether ``role`` may administer an org (membership / departments)."""
    return role in _ORG_ADMIN_ROLES


__all__ = [
    "VALID_CHANNEL_KINDS",
    "VALID_CHANNEL_ROLES",
    "VALID_MEMBER_KINDS",
    "VALID_ORG_ROLES",
    "Channel",
    "ChannelKind",
    "ChannelMember",
    "ChannelRole",
    "Department",
    "MemberKind",
    "Organization",
    "OrgMember",
    "OrgRole",
    "role_has_channel_admin",
    "role_has_org_admin",
]
