"""File Lease — persistent, per-workspace file lock with TTL.

Prevents two members from silently overwriting each other's edits on the
same file. A lease is **persistent** (stored in SQLite, survives process
restarts) and **time-bounded** (auto-expires after ``ttl_seconds`` so a
crashed holder doesn't lock the file forever).

Relationship to ``runtime.platform.io.atomic._cross_process_lock``:

  - ``_cross_process_lock`` is **instantaneous** — it serializes the
    few-milliseconds rename at the end of an atomic write. It does NOT
    prevent two writers who open the file 10 minutes apart from
    clobbering each other.
  - ``LeaseStore`` is **persistent** — it answers "who is allowed to
    edit this file right now?" for the human-scale duration of an edit.

Typical use::

    store = LeaseStore()
    try:
        lease = store.acquire(workspace_id, "src/main.py", holder_id="alice")
    except LeaseConflictError as exc:
        # tell user the file is locked by exc.lease.holder_id
        raise
    try:
        # ... user edits ...
        atomic_write_bytes(path, new_bytes)  # uses _cross_process_lock internally
    finally:
        store.release(lease.lease_id)

The background cleanup thread reclaims expired rows so the table stays
small even if holders crash without calling ``release``.

Thread safety: a single ``threading.Lock`` serialises all SQLite access
within the process, and a fresh ``sqlite3.Connection`` is created per
operation (never shared across threads).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from runtime.safety.auth.scope import TenantScope

_LOG = logging.getLogger("echo.platform.io.lease")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS file_leases (
    lease_id     TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    file_path    TEXT NOT NULL,
    holder_id    TEXT NOT NULL,
    acquired_at  REAL NOT NULL,
    expires_at   REAL NOT NULL,
    kind         TEXT NOT NULL,
    tenant_id    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_lease_workspace_path ON file_leases(workspace_id, file_path);
CREATE INDEX IF NOT EXISTS idx_lease_holder ON file_leases(holder_id);
"""

_LEASE_COLUMNS = (
    "lease_id, workspace_id, file_path, holder_id, acquired_at, expires_at, kind, tenant_id"
)


@dataclass
class FileLease:
    """One acquired lease on a file within a workspace."""

    lease_id: str
    workspace_id: str
    file_path: str
    holder_id: str
    acquired_at: float
    expires_at: float
    kind: str = "exclusive"
    tenant_id: str = ""

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.expires_at - time.time())


class LeaseConflictError(Exception):
    """File is already locked by another holder."""

    def __init__(self, lease: FileLease) -> None:
        self.lease = lease
        remaining = max(0, int(lease.expires_at - time.time()))
        super().__init__(
            f"File '{lease.file_path}' is locked by {lease.holder_id}, {remaining}s remaining"
        )


class LeaseNotFoundError(Exception):
    """Lease does not exist or has expired."""


def _row_to_lease(row: sqlite3.Row | tuple) -> FileLease:
    vals = tuple(row)
    lease_id, workspace_id, file_path, holder_id, acquired_at, expires_at, kind, *rest = vals
    return FileLease(
        lease_id=str(lease_id),
        workspace_id=str(workspace_id),
        file_path=str(file_path),
        holder_id=str(holder_id),
        acquired_at=float(acquired_at),
        expires_at=float(expires_at),
        kind=str(kind),
        tenant_id=str(rest[0]) if rest else "",
    )


class LeaseStore:
    """SQLite-backed file lease store with TTL-based expiry."""

    def __init__(
        self,
        db_path: Path | str = "data/file_leases.db",
        *,
        scope: TenantScope | None = None,
    ) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._scope = scope
        self._cleanup_thread: threading.Thread | None = None
        self._cleanup_stop = threading.Event()
        self._ensure_schema()

    def with_scope(self, scope: TenantScope | None) -> LeaseStore:
        view = object.__new__(LeaseStore)
        view._db_path = self._db_path
        view._lock = self._lock
        view._scope = scope
        view._cleanup_thread = None
        view._cleanup_stop = threading.Event()
        return view

    def _effective_scope(self, scope: TenantScope | None) -> TenantScope | None:
        return self._scope if scope is None else scope

    @staticmethod
    def _tenant_allowed(lease: FileLease, scope: TenantScope | None) -> bool:
        return bool(
            scope is None
            or scope.allow_cross_tenant
            or (scope.is_legacy and lease.tenant_id.startswith("legacy:"))
            or (bool(lease.tenant_id) and lease.tenant_id == scope.tenant_id)
        )

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path), timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)
        return conn

    def _ensure_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(_SCHEMA)
            columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(file_leases)").fetchall()
            }
            if "tenant_id" not in columns:
                conn.execute(
                    "ALTER TABLE file_leases ADD COLUMN tenant_id TEXT NOT NULL DEFAULT ''"
                )

    # ── acquire / renew / release ────────────────────────────────────────────

    def acquire(
        self,
        workspace_id: str,
        file_path: str,
        holder_id: str,
        ttl_seconds: int = 1800,
        kind: str = "exclusive",
        scope: TenantScope | None = None,
    ) -> FileLease:
        """Acquire a lease.

        If an unexpired **exclusive** lease on the same file is held by
        another holder, raises ``LeaseConflictError``. If held by the
        same holder, the existing lease is renewed in place (same
        ``lease_id``). Shared leases never conflict — multiple holders
        may hold a shared lease on the same file concurrently.
        """
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0")
        if kind not in ("exclusive", "shared"):
            raise ValueError("kind must be 'exclusive' or 'shared'")
        now = time.time()
        expires_at = now + ttl_seconds
        effective = self._effective_scope(scope)
        tenant_id = (
            effective.tenant_id
            if effective is not None and not effective.allow_cross_tenant
            else ""
        )
        with self._lock, self._connect() as conn:
            if kind == "exclusive":
                query = (
                    f"SELECT {_LEASE_COLUMNS} FROM file_leases "  # nosec B608 — _LEASE_COLUMNS is a constant; values use ?
                    "WHERE workspace_id = ? AND file_path = ? AND kind = 'exclusive' "
                    "AND expires_at > ?"
                )
                query_args: list[object] = [workspace_id, file_path, now]
                # A tenant-scoped view must resolve conflicts against the
                # same tenant only.  Selecting one global row and filtering
                # it in Python is incorrect when another tenant acquired the
                # same workspace/file earlier: the same-tenant lease could be
                # missed and a duplicate row inserted.
                if effective is not None and not effective.allow_cross_tenant:
                    query += " AND tenant_id = ?"
                    query_args.append(effective.tenant_id)
                query += " ORDER BY acquired_at LIMIT 1"
                row = conn.execute(query, tuple(query_args)).fetchone()
                if row is not None:
                    existing = _row_to_lease(row)
                    if existing.holder_id != holder_id:
                        raise LeaseConflictError(existing)
                    # Same holder — renew in place; keep lease_id + acquired_at.
                    conn.execute(
                        "UPDATE file_leases SET expires_at = ? WHERE lease_id = ?",
                        (expires_at, existing.lease_id),
                    )
                    return FileLease(
                        lease_id=existing.lease_id,
                        workspace_id=existing.workspace_id,
                        file_path=existing.file_path,
                        holder_id=existing.holder_id,
                        acquired_at=existing.acquired_at,
                        expires_at=expires_at,
                        kind=existing.kind,
                        tenant_id=existing.tenant_id,
                    )
            lease = FileLease(
                lease_id=uuid.uuid4().hex,
                workspace_id=workspace_id,
                file_path=file_path,
                holder_id=holder_id,
                acquired_at=now,
                expires_at=expires_at,
                kind=kind,
                tenant_id=tenant_id,
            )
            conn.execute(
                "INSERT INTO file_leases "
                "(lease_id, workspace_id, file_path, holder_id, "
                "acquired_at, expires_at, kind, tenant_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    lease.lease_id,
                    lease.workspace_id,
                    lease.file_path,
                    lease.holder_id,
                    lease.acquired_at,
                    lease.expires_at,
                    lease.kind,
                    lease.tenant_id,
                ),
            )
            return lease

    def renew(
        self,
        lease_id: str,
        ttl_seconds: int = 1800,
        *,
        scope: TenantScope | None = None,
    ) -> FileLease:
        """Renew a lease, extending ``expires_at``.

        ``acquired_at`` and ``lease_id`` are preserved. Raises
        ``LeaseNotFoundError`` if the lease does not exist or has
        already expired (the expired row is purged as part of the call).
        """
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0")
        now = time.time()
        expires_at = now + ttl_seconds
        with self._lock, self._connect() as conn:
            row = conn.execute(
                f"SELECT {_LEASE_COLUMNS} FROM file_leases WHERE lease_id = ?",  # nosec B608 — _LEASE_COLUMNS is a constant; values use ?
                (lease_id,),
            ).fetchone()
            if row is None:
                raise LeaseNotFoundError(f"lease {lease_id!r} not found")
            existing = _row_to_lease(row)
            if not self._tenant_allowed(existing, self._effective_scope(scope)):
                raise LeaseNotFoundError(f"lease {lease_id!r} not found")
            if existing.expires_at <= now:
                conn.execute("DELETE FROM file_leases WHERE lease_id = ?", (lease_id,))
                raise LeaseNotFoundError(f"lease {lease_id!r} expired")
            conn.execute(
                "UPDATE file_leases SET expires_at = ? WHERE lease_id = ?",
                (expires_at, lease_id),
            )
            return FileLease(
                lease_id=existing.lease_id,
                workspace_id=existing.workspace_id,
                file_path=existing.file_path,
                holder_id=existing.holder_id,
                acquired_at=existing.acquired_at,
                expires_at=expires_at,
                kind=existing.kind,
                tenant_id=existing.tenant_id,
            )

    def release(self, lease_id: str, *, scope: TenantScope | None = None) -> bool:
        """Release a lease. Returns ``True`` if a row was deleted."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                f"SELECT {_LEASE_COLUMNS} FROM file_leases WHERE lease_id = ?",  # nosec B608 — _LEASE_COLUMNS is a module constant; value uses a placeholder
                (lease_id,),
            ).fetchone()
            if row is None or not self._tenant_allowed(
                _row_to_lease(row), self._effective_scope(scope)
            ):
                return False
            cur = conn.execute("DELETE FROM file_leases WHERE lease_id = ?", (lease_id,))
            return cur.rowcount > 0

    # ── queries ──────────────────────────────────────────────────────────────

    def get_by_path(
        self,
        workspace_id: str,
        file_path: str,
        *,
        scope: TenantScope | None = None,
    ) -> FileLease | None:
        """Return one active lease for the file, or ``None``.

        For exclusive leases there is at most one. For shared leases
        several may exist; the earliest-acquired is returned.
        """
        now = time.time()
        with self._lock, self._connect() as conn:
            row = conn.execute(
                f"SELECT {_LEASE_COLUMNS} FROM file_leases "  # nosec B608 — _LEASE_COLUMNS is a constant; values use ?
                "WHERE workspace_id = ? AND file_path = ? AND expires_at > ? "
                "ORDER BY acquired_at LIMIT 1",
                (workspace_id, file_path, now),
            ).fetchone()
        lease = _row_to_lease(row) if row is not None else None
        return (
            lease
            if lease is not None and self._tenant_allowed(lease, self._effective_scope(scope))
            else None
        )

    def get_by_holder(self, holder_id: str, *, scope: TenantScope | None = None) -> list[FileLease]:
        """Return all active leases held by ``holder_id``."""
        now = time.time()
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_LEASE_COLUMNS} FROM file_leases "  # nosec B608 — _LEASE_COLUMNS is a constant; values use ?
                "WHERE holder_id = ? AND expires_at > ? "
                "ORDER BY acquired_at",
                (holder_id, now),
            ).fetchall()
        effective = self._effective_scope(scope)
        return [
            lease
            for lease in (_row_to_lease(r) for r in rows)
            if self._tenant_allowed(lease, effective)
        ]

    def list_active(
        self,
        workspace_id: str | None = None,
        *,
        scope: TenantScope | None = None,
    ) -> list[FileLease]:
        """List active leases, optionally filtered by workspace."""
        now = time.time()
        with self._lock, self._connect() as conn:
            if workspace_id is None:
                rows = conn.execute(
                    f"SELECT {_LEASE_COLUMNS} FROM file_leases "  # nosec B608 — _LEASE_COLUMNS is a constant; values use ?
                    "WHERE expires_at > ? ORDER BY acquired_at",
                    (now,),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT {_LEASE_COLUMNS} FROM file_leases "  # nosec B608 — _LEASE_COLUMNS is a constant; values use ?
                    "WHERE workspace_id = ? AND expires_at > ? "
                    "ORDER BY acquired_at",
                    (workspace_id, now),
                ).fetchall()
        effective = self._effective_scope(scope)
        return [
            lease
            for lease in (_row_to_lease(r) for r in rows)
            if self._tenant_allowed(lease, effective)
        ]

    # ── maintenance ──────────────────────────────────────────────────────────

    def cleanup_expired(self) -> int:
        """Delete expired leases. Returns the number of rows removed."""
        now = time.time()
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM file_leases WHERE expires_at <= ?", (now,))
            return cur.rowcount

    def start_cleanup_thread(self, interval_seconds: int = 60) -> None:
        """Start a daemon thread that periodically purges expired leases.

        Idempotent: calling again while the thread is running is a
        no-op. Use ``stop_cleanup_thread`` to stop it.
        """
        if self._cleanup_thread is not None and self._cleanup_thread.is_alive():
            return
        self._cleanup_stop.clear()
        thread = threading.Thread(
            target=self._cleanup_loop,
            name="file-lease-cleanup",
            daemon=True,
            kwargs={"interval_seconds": interval_seconds},
        )
        self._cleanup_thread = thread
        thread.start()

    def stop_cleanup_thread(self, timeout: float = 5.0) -> None:
        """Signal the cleanup thread to stop and wait briefly."""
        thread = self._cleanup_thread
        if thread is None:
            return
        self._cleanup_stop.set()
        thread.join(timeout=timeout)
        self._cleanup_thread = None

    def _cleanup_loop(self, interval_seconds: int) -> None:
        # ``Event.wait`` returns True when set → exit. False on timeout
        # → run one cleanup iteration and loop.
        while not self._cleanup_stop.wait(interval_seconds):
            try:
                removed = self.cleanup_expired()
                if removed:
                    _LOG.debug("lease cleanup removed %d expired lease(s)", removed)
            except Exception:  # noqa: BLE001 — background cleanup must not die
                _LOG.warning("lease cleanup iteration failed", exc_info=True)
