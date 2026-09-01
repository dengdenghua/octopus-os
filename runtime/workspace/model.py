"""Workspace data model: Workspace + WorkspaceMember dataclasses.

Pure dataclasses with dict round-trips so the store can be SQLite/JSON-backed
and the engine stays I/O-free. Mirrors the pattern in
``runtime/projectos/model.py``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

MountType = Literal["local", "smb", "nfs", "webdav", "sftp", "s3"]
MemberRole = Literal["owner", "editor", "reviewer", "viewer"]

VALID_MOUNT_TYPES = frozenset({"local", "smb", "nfs", "webdav", "sftp", "s3"})
VALID_MEMBER_ROLES = frozenset({"owner", "editor", "reviewer", "viewer"})


@dataclass
class Workspace:
    """A first-class workspace entity: a mount + owner + members.

    ``mount_options`` carries mount-specific config (including credentials).
    Sensitive fields inside it are encrypted at rest by ``crypto.encrypt_options``
    before the row is written; reads decrypt on the fly via
    ``crypto.decrypt_options`` so callers always see the original dict.
    """

    id: str
    name: str
    mount_type: MountType
    mount_target: str
    mount_options: dict[str, Any] = field(default_factory=dict)
    owner_id: str = ""
    tenant_id: str = ""
    created_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Workspace:
        mount_type = raw.get("mount_type")
        if mount_type not in VALID_MOUNT_TYPES:
            mount_type = "local"
        return cls(
            id=str(raw["id"]),
            name=str(raw.get("name") or raw["id"]),
            mount_type=mount_type,
            mount_target=str(raw.get("mount_target") or ""),
            mount_options=dict(raw.get("mount_options") or {}),
            owner_id=str(raw.get("owner_id") or ""),
            tenant_id=str(raw.get("tenant_id") or ""),
            created_at=float(raw.get("created_at") or 0.0),
        )


@dataclass
class WorkspaceMember:
    """Membership row: a user/agent attached to a workspace with a role."""

    workspace_id: str
    member_id: str
    role: MemberRole
    added_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> WorkspaceMember:
        role = raw.get("role")
        if role not in VALID_MEMBER_ROLES:
            role = "viewer"
        return cls(
            workspace_id=str(raw["workspace_id"]),
            member_id=str(raw["member_id"]),
            role=role,
            added_at=float(raw.get("added_at") or 0.0),
        )
