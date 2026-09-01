"""OrgStore · SQLite-backed persistence for Organization / Department / Channel.

Four tables (schema matches the enterprise-space 阶段一 model in ``org.py``):

  - ``organizations``: PK id, name, owner_id, created_at
  - ``departments``:   PK id, org_id, parent_id (nullable root), name, created_at
  - ``org_members``:   composite PK (org_id, member_id), kind (human/agent),
                       role, display_name, added_at
  - ``channels``:      PK id, org_id, department_id (optional), name, kind,
                       created_at
  - ``channel_members``: composite PK (channel_id, member_id), role, added_at

Design notes:

- **Unified member model**: ``org_members.kind`` carries whether a row is a
  Human or an Agent, so the same identity table serves both — matching the
  roadmap "同一身份体系可查 Human 与 Agent".
- **ACL is membership**: a channel is only visible to members listed in
  ``channel_members``. ``list_channels_for_user`` is the single access-check
  entry point (非成员不可见频道内容). Org admins can also see every channel in
  their org (they administer the ACL).
- **Cascading deletes** run inside one transaction so partial state never leaks
  (delete org → its departments/channels/members; delete channel → its ACL).

Mirrors the SQLite style of ``runtime/workspace/store.py``: ``sqlite3`` stdlib
+ ``threading.Lock`` write serialization + ``PRAGMA journal_mode=WAL``.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

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
    role_has_org_admin,
)

_LOG = logging.getLogger("echo.workspace.org_store")

_EXISTS_QUERIES = {
    ("organizations", "id"): "SELECT 1 FROM organizations WHERE id=?",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS organizations (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    owner_id   TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS departments (
    id         TEXT PRIMARY KEY,
    org_id     TEXT NOT NULL,
    parent_id  TEXT,
    name       TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS org_members (
    org_id       TEXT NOT NULL,
    member_id    TEXT NOT NULL,
    kind         TEXT NOT NULL,
    role         TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    added_at     REAL NOT NULL,
    PRIMARY KEY (org_id, member_id)
);
CREATE TABLE IF NOT EXISTS channels (
    id            TEXT PRIMARY KEY,
    org_id        TEXT NOT NULL,
    department_id TEXT,
    name          TEXT NOT NULL,
    kind          TEXT NOT NULL,
    created_at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS channel_members (
    channel_id TEXT NOT NULL,
    member_id  TEXT NOT NULL,
    role       TEXT NOT NULL,
    added_at   REAL NOT NULL,
    PRIMARY KEY (channel_id, member_id)
);
CREATE INDEX IF NOT EXISTS idx_departments_org ON departments(org_id);
CREATE INDEX IF NOT EXISTS idx_departments_parent ON departments(parent_id);
CREATE INDEX IF NOT EXISTS idx_org_members_member ON org_members(member_id);
CREATE INDEX IF NOT EXISTS idx_channels_org ON channels(org_id);
CREATE INDEX IF NOT EXISTS idx_channel_members_member ON channel_members(member_id);
"""


def _default_db_path() -> Path:
    """``<data>/org.db`` — same ``app_paths`` data dir other stores use."""
    from runtime.platform.process.paths import app_paths

    return app_paths().data_dir / "org.db"


def _org_from_row(row: tuple[Any, ...]) -> Organization:
    return Organization(
        id=str(row[0]),
        name=str(row[1]),
        owner_id=str(row[2]),
        created_at=float(row[3]),
    )


def _dept_from_row(row: tuple[Any, ...]) -> Department:
    parent = row[2]
    return Department(
        id=str(row[0]),
        org_id=str(row[1]),
        parent_id=str(parent) if parent else None,
        name=str(row[3]),
        created_at=float(row[4]),
    )


def _org_member_from_row(row: tuple[Any, ...]) -> OrgMember:
    return OrgMember(
        org_id=str(row[0]),
        member_id=str(row[1]),
        kind="human" if str(row[2]) == "human" else "agent",
        role=str(row[3]) if str(row[3]) in VALID_ORG_ROLES else "member",
        display_name=str(row[4]),
        added_at=float(row[5]),
    )


def _channel_from_row(row: tuple[Any, ...]) -> Channel:
    dept = row[2]
    return Channel(
        id=str(row[0]),
        org_id=str(row[1]),
        department_id=str(dept) if dept else None,
        name=str(row[3]),
        kind=str(row[4]) if str(row[4]) in VALID_CHANNEL_KINDS else "channel",
        created_at=float(row[5]),
    )


def _channel_member_from_row(row: tuple[Any, ...]) -> ChannelMember:
    return ChannelMember(
        channel_id=str(row[0]),
        member_id=str(row[1]),
        role=str(row[2]) if str(row[2]) in VALID_CHANNEL_ROLES else "member",
        added_at=float(row[3]),
    )


class OrgStore:
    """SQLite-backed persistence for the enterprise organization tree.

    The constructor accepts a full DB file path (``db_path``) so tests can point
    at a tmpdir. When ``db_path`` is None the default ``<data>/org.db`` is used.
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        path = Path(db_path) if db_path else _default_db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = path
        self._lock = threading.Lock()
        self._ensure_schema()

    @property
    def db_path(self) -> Path:
        return self._db

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db), timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _ensure_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _require_exists(
        self, conn: sqlite3.Connection, table: str, pk_col: str, value: str
    ) -> None:
        query = _EXISTS_QUERIES.get((table, pk_col))
        if query is None:
            raise ValueError(f"unsupported existence check: {table}.{pk_col}")
        row = conn.execute(query, (value,)).fetchone()
        if not row:
            raise ValueError(f"{table} {value!r} does not exist")

    # ── organizations ────────────────────────────────────────────────────────

    def create_organization(
        self,
        *,
        name: str,
        owner_id: str,
        organization_id: str | None = None,
        created_at: float | None = None,
    ) -> Organization:
        """Insert a new organization. The owner is auto-added as
        ``org_members`` with role=``owner`` so membership queries work
        immediately, mirroring ``WorkspaceStore.create_workspace``."""
        if not name.strip():
            raise ValueError("name is required")
        if not owner_id:
            raise ValueError("owner_id is required")
        org = Organization(
            id=organization_id or str(uuid4()),
            name=name.strip(),
            owner_id=owner_id,
            created_at=float(created_at if created_at is not None else time.time()),
        )
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO organizations(id, name, owner_id, created_at) VALUES (?, ?, ?, ?)",
                (org.id, org.name, org.owner_id, org.created_at),
            )
        self.add_org_member(
            org.id, org.owner_id, kind="human", role="owner", added_at=org.created_at
        )
        return org

    def get_organization(self, organization_id: str) -> Organization | None:
        if not organization_id:
            return None
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT id, name, owner_id, created_at FROM organizations WHERE id=?",
                (organization_id,),
            ).fetchone()
        return _org_from_row(row) if row else None

    def list_organizations(self) -> list[Organization]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT id, name, owner_id, created_at FROM organizations ORDER BY created_at, id"
            ).fetchall()
        return [_org_from_row(r) for r in rows]

    def list_organizations_for_user(self, member_id: str) -> list[Organization]:
        """Orgs ``member_id`` belongs to (any role)."""
        if not member_id:
            return []
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT o.id, o.name, o.owner_id, o.created_at "
                "FROM organizations o "
                "INNER JOIN org_members m ON m.org_id = o.id "
                "WHERE m.member_id = ? "
                "ORDER BY o.created_at, o.id",
                (member_id,),
            ).fetchall()
        return [_org_from_row(r) for r in rows]

    def delete_organization(self, organization_id: str) -> bool:
        """Remove an org and all its departments/channels/members in one
        transaction. Returns True if it existed and was removed (idempotent)."""
        if not organization_id:
            return False
        with self._lock, self._connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM organizations WHERE id=?", (organization_id,)
            ).fetchone()
            if not exists:
                return False
            channel_ids = [
                r[0]
                for r in conn.execute(
                    "SELECT id FROM channels WHERE org_id=?", (organization_id,)
                ).fetchall()
            ]
            dept_ids = [
                r[0]
                for r in conn.execute(
                    "SELECT id FROM departments WHERE org_id=?", (organization_id,)
                ).fetchall()
            ]
            for cid in channel_ids:
                conn.execute("DELETE FROM channel_members WHERE channel_id=?", (cid,))
            for did in dept_ids:
                conn.execute("DELETE FROM channels WHERE department_id=?", (did,))
            conn.execute("DELETE FROM channels WHERE org_id=?", (organization_id,))
            conn.execute("DELETE FROM departments WHERE org_id=?", (organization_id,))
            conn.execute("DELETE FROM org_members WHERE org_id=?", (organization_id,))
            conn.execute("DELETE FROM organizations WHERE id=?", (organization_id,))
        return True

    # ── org members (unified Human + Agent) ─────────────────────────────────

    def add_org_member(
        self,
        org_id: str,
        member_id: str,
        *,
        kind: str = "agent",
        role: str = "member",
        display_name: str = "",
        added_at: float | None = None,
    ) -> OrgMember:
        """Add or upsert an org membership. ``kind`` is ``human`` or ``agent``
        (the unified member model). Upserting updates the role in place."""
        if kind not in VALID_MEMBER_KINDS:
            raise ValueError(f"invalid kind {kind!r}; expected human or agent")
        if role not in VALID_ORG_ROLES:
            raise ValueError(f"invalid role {role!r}; expected one of {sorted(VALID_ORG_ROLES)}")
        if not member_id:
            raise ValueError("member_id is required")
        member = OrgMember(
            org_id=org_id,
            member_id=member_id,
            kind=kind,  # type: ignore[arg-type]
            role=role,  # type: ignore[arg-type]
            display_name=display_name,
            added_at=float(added_at if added_at is not None else time.time()),
        )
        with self._lock, self._connect() as conn:
            self._require_exists(conn, "organizations", "id", org_id)
            conn.execute(
                "INSERT INTO org_members(org_id, member_id, kind, role, display_name, added_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(org_id, member_id) DO UPDATE SET "
                "kind=excluded.kind, role=excluded.role, "
                "display_name=excluded.display_name",
                (
                    member.org_id,
                    member.member_id,
                    member.kind,
                    member.role,
                    member.display_name,
                    member.added_at,
                ),
            )
        return member

    def remove_org_member(self, org_id: str, member_id: str) -> bool:
        """Remove an org membership. Returns True if a row was deleted."""
        if not org_id or not member_id:
            return False
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM org_members WHERE org_id=? AND member_id=?",
                (org_id, member_id),
            )
            return cur.rowcount > 0

    def list_org_members(self, org_id: str) -> list[OrgMember]:
        if not org_id:
            return []
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT org_id, member_id, kind, role, display_name, added_at "
                "FROM org_members WHERE org_id=? ORDER BY added_at, member_id",
                (org_id,),
            ).fetchall()
        return [_org_member_from_row(r) for r in rows]

    def get_org_member_role(self, org_id: str, member_id: str) -> str | None:
        """Return the org role for ``member_id``, or None if not a member."""
        if not org_id or not member_id:
            return None
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT role FROM org_members WHERE org_id=? AND member_id=?",
                (org_id, member_id),
            ).fetchone()
        return str(row[0]) if row else None

    # ── departments ──────────────────────────────────────────────────────────

    def create_department(
        self,
        *,
        org_id: str,
        name: str,
        parent_id: str | None = None,
        department_id: str | None = None,
        created_at: float | None = None,
    ) -> Department:
        """Create a department under an org (optionally nested under another
        department of the same org)."""
        if not name.strip():
            raise ValueError("name is required")
        dept = Department(
            id=department_id or str(uuid4()),
            org_id=org_id,
            name=name.strip(),
            parent_id=parent_id,
            created_at=float(created_at if created_at is not None else time.time()),
        )
        with self._lock, self._connect() as conn:
            self._require_exists(conn, "organizations", "id", org_id)
            if dept.parent_id:
                parent = conn.execute(
                    "SELECT 1 FROM departments WHERE id=? AND org_id=?",
                    (dept.parent_id, org_id),
                ).fetchone()
                if not parent:
                    raise ValueError(f"parent department {dept.parent_id!r} not in org {org_id!r}")
            conn.execute(
                "INSERT INTO departments(id, org_id, parent_id, name, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (dept.id, dept.org_id, dept.parent_id, dept.name, dept.created_at),
            )
        return dept

    def get_department(self, department_id: str) -> Department | None:
        if not department_id:
            return None
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT id, org_id, parent_id, name, created_at FROM departments WHERE id=?",
                (department_id,),
            ).fetchone()
        return _dept_from_row(row) if row else None

    def list_departments(self, org_id: str) -> list[Department]:
        if not org_id:
            return []
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT id, org_id, parent_id, name, created_at "
                "FROM departments WHERE org_id=? ORDER BY created_at, id",
                (org_id,),
            ).fetchall()
        return [_dept_from_row(r) for r in rows]

    def delete_department(self, department_id: str) -> bool:
        """Remove a department and any channels attached to it. Returns True if
        a row was deleted. (Nested children are not auto-removed to avoid
        surprising data loss; callers should re-parent first.)"""
        if not department_id:
            return False
        with self._lock, self._connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM departments WHERE id=?", (department_id,)
            ).fetchone()
            if not exists:
                return False
            channels = [
                r[0]
                for r in conn.execute(
                    "SELECT id FROM channels WHERE department_id=?", (department_id,)
                ).fetchall()
            ]
            for cid in channels:
                conn.execute("DELETE FROM channel_members WHERE channel_id=?", (cid,))
            conn.execute("DELETE FROM channels WHERE department_id=?", (department_id,))
            conn.execute("DELETE FROM departments WHERE id=?", (department_id,))
        return True

    # ── channels ─────────────────────────────────────────────────────────────

    def create_channel(
        self,
        *,
        org_id: str,
        name: str,
        kind: str = "channel",
        department_id: str | None = None,
        channel_id: str | None = None,
        created_at: float | None = None,
    ) -> Channel:
        """Create a channel under an org (optionally under a department)."""
        if kind not in VALID_CHANNEL_KINDS:
            raise ValueError(f"invalid kind {kind!r}; expected channel or group")
        if not name.strip():
            raise ValueError("name is required")
        channel = Channel(
            id=channel_id or str(uuid4()),
            org_id=org_id,
            name=name.strip(),
            kind=kind,  # type: ignore[arg-type]
            department_id=department_id,
            created_at=float(created_at if created_at is not None else time.time()),
        )
        with self._lock, self._connect() as conn:
            self._require_exists(conn, "organizations", "id", org_id)
            if channel.department_id:
                dept = conn.execute(
                    "SELECT 1 FROM departments WHERE id=? AND org_id=?",
                    (channel.department_id, org_id),
                ).fetchone()
                if not dept:
                    raise ValueError(f"department {channel.department_id!r} not in org {org_id!r}")
            conn.execute(
                "INSERT INTO channels(id, org_id, department_id, name, kind, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    channel.id,
                    channel.org_id,
                    channel.department_id,
                    channel.name,
                    channel.kind,
                    channel.created_at,
                ),
            )
        return channel

    def get_channel(self, channel_id: str) -> Channel | None:
        if not channel_id:
            return None
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT id, org_id, department_id, name, kind, created_at FROM channels WHERE id=?",
                (channel_id,),
            ).fetchone()
        return _channel_from_row(row) if row else None

    def list_channels(self, org_id: str) -> list[Channel]:
        if not org_id:
            return []
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT id, org_id, department_id, name, kind, created_at "
                "FROM channels WHERE org_id=? ORDER BY created_at, id",
                (org_id,),
            ).fetchall()
        return [_channel_from_row(r) for r in rows]

    def delete_channel(self, channel_id: str) -> bool:
        """Remove a channel and its ACL rows in one transaction."""
        if not channel_id:
            return False
        with self._lock, self._connect() as conn:
            exists = conn.execute("SELECT 1 FROM channels WHERE id=?", (channel_id,)).fetchone()
            if not exists:
                return False
            conn.execute("DELETE FROM channel_members WHERE channel_id=?", (channel_id,))
            conn.execute("DELETE FROM channels WHERE id=?", (channel_id,))
        return True

    # ── channel ACL ──────────────────────────────────────────────────────────

    def add_channel_member(
        self,
        channel_id: str,
        member_id: str,
        *,
        role: str = "member",
        added_at: float | None = None,
        require_org_member: bool = True,
    ) -> ChannelMember:
        """Add or upsert a channel ACL row. By default the member must already
        belong to the channel's org (a channel is only reachable by org members)
        unless ``require_org_member=False`` for a direct grant."""
        if role not in VALID_CHANNEL_ROLES:
            raise ValueError(
                f"invalid role {role!r}; expected one of {sorted(VALID_CHANNEL_ROLES)}"
            )
        if not member_id:
            raise ValueError("member_id is required")
        member = ChannelMember(
            channel_id=channel_id,
            member_id=member_id,
            role=role,  # type: ignore[arg-type]
            added_at=float(added_at if added_at is not None else time.time()),
        )
        with self._lock, self._connect() as conn:
            channel = conn.execute(
                "SELECT org_id FROM channels WHERE id=?", (channel_id,)
            ).fetchone()
            if not channel:
                raise ValueError(f"channel {channel_id!r} does not exist")
            if require_org_member:
                org = conn.execute(
                    "SELECT 1 FROM org_members WHERE org_id=? AND member_id=?",
                    (channel[0], member_id),
                ).fetchone()
                if not org:
                    raise ValueError(f"member {member_id!r} is not a member of org {channel[0]!r}")
            conn.execute(
                "INSERT INTO channel_members(channel_id, member_id, role, added_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(channel_id, member_id) DO UPDATE SET role=excluded.role",
                (member.channel_id, member.member_id, member.role, member.added_at),
            )
        return member

    def remove_channel_member(self, channel_id: str, member_id: str) -> bool:
        """Remove a channel ACL row. Returns True if a row was deleted."""
        if not channel_id or not member_id:
            return False
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM channel_members WHERE channel_id=? AND member_id=?",
                (channel_id, member_id),
            )
            return cur.rowcount > 0

    def list_channel_members(self, channel_id: str) -> list[ChannelMember]:
        if not channel_id:
            return []
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT channel_id, member_id, role, added_at "
                "FROM channel_members WHERE channel_id=? ORDER BY added_at, member_id",
                (channel_id,),
            ).fetchall()
        return [_channel_member_from_row(r) for r in rows]

    def get_channel_member_role(self, channel_id: str, member_id: str) -> str | None:
        """Return the channel role for ``member_id``, or None if not a member."""
        if not channel_id or not member_id:
            return None
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT role FROM channel_members WHERE channel_id=? AND member_id=?",
                (channel_id, member_id),
            ).fetchone()
        return str(row[0]) if row else None

    def can_access_channel(self, channel_id: str, member_id: str) -> bool:
        """The single ACL check: a member may see a channel only if they are in
        its ACL (or an org admin, who administers the ACL)."""
        if not channel_id or not member_id:
            return False
        with self._lock, self._connect() as conn:
            channel = conn.execute(
                "SELECT org_id FROM channels WHERE id=?", (channel_id,)
            ).fetchone()
            if not channel:
                return False
            acl = conn.execute(
                "SELECT role FROM channel_members WHERE channel_id=? AND member_id=?",
                (channel_id, member_id),
            ).fetchone()
            if acl:
                return True
            org_role = conn.execute(
                "SELECT role FROM org_members WHERE org_id=? AND member_id=?",
                (channel[0], member_id),
            ).fetchone()
            return bool(org_role and role_has_org_admin(str(org_role[0])))

    def list_channels_for_user(self, member_id: str) -> list[Channel]:
        """Channels the member can see (in its ACL, or any channel in an org
        they admin). This is the access-filtered channel listing."""
        if not member_id:
            return []
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT c.id, c.org_id, c.department_id, c.name, c.kind, c.created_at "
                "FROM channels c "
                "WHERE c.id IN ("
                "  SELECT channel_id FROM channel_members WHERE member_id=?"
                ") "
                "OR c.org_id IN ("
                "  SELECT org_id FROM org_members "
                "  WHERE member_id=? AND role IN ('owner','admin')"
                ") "
                "ORDER BY c.created_at, c.id",
                (member_id, member_id),
            ).fetchall()
        return [_channel_from_row(r) for r in rows]


__all__ = ["OrgStore"]
