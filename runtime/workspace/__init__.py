"""Workspace: first-class mount + membership entity for Echo Agent.

Public surface:
    Workspace            — data model for a single workspace
    WorkspaceMember      — data model for a membership row
    WorkspaceStore       — SQLite-backed persistence
    encrypt_options      — encrypt sensitive fields in mount_options dict
    decrypt_options      — inverse of encrypt_options

Enterprise space (阶段一 org tree):
    Organization         — top-level tenant (企业空间)
    Department           — node of the org tree
    OrgMember            — unified Human/Agent member of an org
    Channel              — enterprise collaboration space (频道/群聊)
    ChannelMember        — channel ACL row
    OrgStore             — SQLite-backed persistence for the org tree

See ``model.py`` for workspace dataclasses, ``crypto.py`` for the at-rest
encryption scheme, ``store.py`` for the workspace SQLite store, and ``org.py``
/ ``org_store.py`` for the enterprise organization layer.
"""

from __future__ import annotations

from runtime.workspace.channel_bridge import (
    channel_history,
    grant_for_channel_role,
    link_channel_to_group,
    map_channel_role,
    send_channel_message,
    sync_channel_members_to_group,
)
from runtime.workspace.crypto import decrypt_options, encrypt_options
from runtime.workspace.model import (
    VALID_MEMBER_ROLES,
    VALID_MOUNT_TYPES,
    Workspace,
    WorkspaceMember,
)
from runtime.workspace.org import (
    VALID_CHANNEL_KINDS,
    VALID_CHANNEL_ROLES,
    VALID_MEMBER_KINDS,
    VALID_ORG_ROLES,
    Channel,
    ChannelMember,
    Department,
    Organization,
    OrgMember,
    role_has_channel_admin,
    role_has_org_admin,
)
from runtime.workspace.org_audit import (
    EVENT_TYPES,
    append_org_audit_event,
    export_org_audit_bundle,
    list_org_audit_events,
    verify_org_audit_chain,
)
from runtime.workspace.org_store import OrgStore
from runtime.workspace.store import WorkspaceStore

__all__ = [
    "VALID_CHANNEL_KINDS",
    "VALID_CHANNEL_ROLES",
    "VALID_MEMBER_KINDS",
    "VALID_MEMBER_ROLES",
    "VALID_MOUNT_TYPES",
    "VALID_ORG_ROLES",
    "Channel",
    "ChannelMember",
    "Department",
    "EVENT_TYPES",
    "OrgMember",
    "OrgStore",
    "Organization",
    "Workspace",
    "WorkspaceMember",
    "WorkspaceStore",
    "append_org_audit_event",
    "channel_history",
    "decrypt_options",
    "encrypt_options",
    "export_org_audit_bundle",
    "grant_for_channel_role",
    "link_channel_to_group",
    "list_org_audit_events",
    "map_channel_role",
    "role_has_channel_admin",
    "role_has_org_admin",
    "send_channel_message",
    "sync_channel_members_to_group",
    "verify_org_audit_chain",
]
