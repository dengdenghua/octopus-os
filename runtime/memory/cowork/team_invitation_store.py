"""Durable, tenant-scoped invitations for human Team Room members."""

from __future__ import annotations

import secrets
import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar
from uuid import uuid4

from ._team_invitation_support import (
    _INVITE_COLUMNS,
    _JOIN_REQUEST_COLUMNS,
    _SCHEMA,
    InvitationError,
    InvitationExhausted,
    InvitationExpired,
    InvitationNotFound,
    InvitationRevoked,
    JoinRequestConflict,
    JoinRequestNotFound,
    _as_utc,
    _default_db_path,
    _from_row,
    _hash_token,
    _join_request_from_row,
    _join_request_has_reservation,
    _optional_note,
    _raise_unusable,
    _required_text,
    _reservation_by_id,
    _reservation_row,
    _reserved_payload,
    _status,
    _utc_now,
    _without_secret,
)
from ._team_invitation_support import (
    _parse_time as _parse_time,
)

_T = TypeVar("_T")


class TeamInvitationStore:
    def __init__(
        self,
        db_path: Path | str | None = None,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._db = Path(db_path) if db_path is not None else _default_db_path()
        self._db.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._lock = threading.RLock()
        with self._lock, self._connect() as conn:
            conn.executescript(_SCHEMA)

    @property
    def db_path(self) -> Path:
        return self._db

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    @staticmethod
    def _refresh_join_request_states(conn: sqlite3.Connection, now: datetime) -> None:
        """Persist terminal states inherited from invitation expiry/capacity."""

        timestamp = now.isoformat()
        conn.execute(
            "UPDATE team_join_requests SET status = 'expired', updated_at = ? "
            "WHERE status = 'pending' AND expires_at <= ? AND NOT EXISTS ("
            "SELECT 1 FROM team_invitation_reservations r "
            "WHERE r.invite_id = team_join_requests.invite_id "
            "AND r.actor_id = team_join_requests.actor_id)",
            (timestamp, timestamp),
        )
        conn.execute(
            "UPDATE team_join_requests SET status = 'cancelled', updated_at = ? "
            "WHERE status = 'pending' AND invite_id IN ("
            "SELECT invite_id FROM team_invitations "
            "WHERE revoked_at IS NOT NULL OR use_count >= max_uses"
            ") AND NOT EXISTS (SELECT 1 FROM team_invitation_reservations r "
            "WHERE r.invite_id = team_join_requests.invite_id "
            "AND r.actor_id = team_join_requests.actor_id)",
            (timestamp,),
        )

    @staticmethod
    def _request_row(
        conn: sqlite3.Connection,
        request_id: str,
        *,
        tenant_id: str,
        room_id: str,
    ) -> sqlite3.Row | None:
        return conn.execute(
            f"SELECT {_JOIN_REQUEST_COLUMNS} FROM team_join_requests "  # noqa: S608
            "WHERE request_id = ? AND tenant_id = ? AND room_id = ?",
            (request_id, tenant_id, room_id),
        ).fetchone()

    @staticmethod
    def _invitation_row_for_request(
        conn: sqlite3.Connection,
        request_row: sqlite3.Row,
    ) -> sqlite3.Row | None:
        return conn.execute(
            f"SELECT {_INVITE_COLUMNS} FROM team_invitations "  # noqa: S608
            "WHERE invite_id = ? AND tenant_id = ? AND room_id = ?",
            (
                request_row["invite_id"],
                request_row["tenant_id"],
                request_row["room_id"],
            ),
        ).fetchone()

    @staticmethod
    def _reserve_use(
        conn: sqlite3.Connection,
        invitation: dict[str, Any],
        *,
        actor_id: str,
        participant_id: str | None,
        audit_request_id: str,
        join_request_id: str | None,
        decided_by: str | None,
        membership_already_applied: bool,
        now: datetime,
    ) -> tuple[dict[str, Any], sqlite3.Row]:
        row = _reservation_row(
            conn,
            invite_id=str(invitation["id"]),
            actor_id=actor_id,
        )
        if row is not None:
            reserved_participant_id = str(row["participant_id"] or "") or None
            if participant_id is not None and reserved_participant_id != participant_id:
                raise JoinRequestConflict("reserved membership identity changed")
            if not membership_already_applied and invitation["status"] in {"expired", "revoked"}:
                _raise_unusable(invitation)
            return invitation, row
        _raise_unusable(invitation)
        next_use = int(invitation["use_count"]) + 1
        cursor = conn.execute(
            "UPDATE team_invitations SET use_count = ?, last_used_at = ? "
            "WHERE invite_id = ? AND revoked_at IS NULL AND use_count = ? "
            "AND use_count < max_uses",
            (
                next_use,
                now.isoformat(),
                invitation["id"],
                invitation["use_count"],
            ),
        )
        if cursor.rowcount != 1:
            raise InvitationExhausted("invitation could not be reserved")
        conn.execute(
            "INSERT INTO team_invitation_reservations("
            "reservation_id, invite_id, tenant_id, room_id, actor_id, use_number, "
            "participant_id, audit_request_id, join_request_id, decided_by, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"reservation-{uuid4().hex}",
                invitation["id"],
                invitation["tenant_id"],
                invitation["room_id"],
                actor_id,
                next_use,
                participant_id,
                audit_request_id,
                join_request_id,
                decided_by,
                now.isoformat(),
            ),
        )
        invitation = {
            **invitation,
            "use_count": next_use,
            "last_used_at": now.isoformat(),
            "remaining_uses": max(0, int(invitation["max_uses"]) - next_use),
        }
        invitation["status"] = _status(invitation, now)
        row = _reservation_row(
            conn,
            invite_id=str(invitation["id"]),
            actor_id=actor_id,
        )
        if row is None:  # pragma: no cover - insert/select invariant
            raise RuntimeError("invitation reservation did not return a row")
        return invitation, row

    def has_consumption_reservation(self, *, invite_id: str, actor_id: str) -> bool:
        invite_id = _required_text(invite_id, label="invite_id")
        actor_id = _required_text(actor_id, label="actor_id")
        with self._lock, self._connect() as conn:
            return _reservation_row(conn, invite_id=invite_id, actor_id=actor_id) is not None

    def _finalize_reservation(
        self,
        reservation_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any] | None, bool]:
        now = _as_utc(self._clock())
        with self._lock, self._connect() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                reservation = _reservation_by_id(conn, reservation_id)
                if reservation is None:
                    raise RuntimeError("invitation reservation not found")
                changed = reservation["finalized_at"] is None
                if changed:
                    conn.execute(
                        "INSERT INTO team_invitation_uses("
                        "invite_id, use_number, tenant_id, room_id, actor_id, used_at, request_id"
                        ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            reservation["invite_id"],
                            reservation["use_number"],
                            reservation["tenant_id"],
                            reservation["room_id"],
                            reservation["actor_id"],
                            reservation["created_at"],
                            reservation["audit_request_id"],
                        ),
                    )
                    timestamp = now.isoformat()
                    if reservation["join_request_id"] is not None:
                        cursor = conn.execute(
                            "UPDATE team_join_requests SET status = 'approved', updated_at = ?, "
                            "decided_at = ?, decided_by = ?, decision_reason = '', "
                            "participant_id = ? WHERE request_id = ? AND status = 'pending'",
                            (
                                timestamp,
                                timestamp,
                                reservation["decided_by"],
                                reservation["participant_id"],
                                reservation["join_request_id"],
                            ),
                        )
                        if cursor.rowcount != 1:
                            raise JoinRequestConflict("join request approval was superseded")
                    elif reservation["participant_id"] is not None:
                        conn.execute(
                            "UPDATE team_join_requests SET status = 'approved', updated_at = ?, "
                            "decided_at = ?, decided_by = ?, decision_reason = ?, "
                            "participant_id = ? WHERE invite_id = ? AND actor_id = ? "
                            "AND status = 'pending'",
                            (
                                timestamp,
                                timestamp,
                                reservation["actor_id"],
                                "joined after direct-join policy",
                                reservation["participant_id"],
                                reservation["invite_id"],
                                reservation["actor_id"],
                            ),
                        )
                    conn.execute(
                        "UPDATE team_invitation_reservations SET finalized_at = ? "
                        "WHERE reservation_id = ? AND finalized_at IS NULL",
                        (timestamp, reservation_id),
                    )
                    conn.execute(
                        "UPDATE team_join_requests SET status = 'cancelled', updated_at = ?, "
                        "decision_reason = ? WHERE invite_id = ? AND status = 'pending' "
                        "AND invite_id IN (SELECT invite_id FROM team_invitations "
                        "WHERE use_count >= max_uses) "
                        "AND NOT EXISTS (SELECT 1 FROM team_invitation_reservations r "
                        "WHERE r.invite_id = team_join_requests.invite_id "
                        "AND r.actor_id = team_join_requests.actor_id)",
                        (
                            timestamp,
                            "invitation exhausted",
                            reservation["invite_id"],
                        ),
                    )
                invite_row = conn.execute(
                    f"SELECT {_INVITE_COLUMNS} FROM team_invitations WHERE invite_id = ?",  # noqa: S608
                    (reservation["invite_id"],),
                ).fetchone()
                request_row = (
                    self._request_row(
                        conn,
                        str(reservation["join_request_id"]),
                        tenant_id=str(reservation["tenant_id"]),
                        room_id=str(reservation["room_id"]),
                    )
                    if reservation["join_request_id"] is not None
                    else None
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        if invite_row is None:  # pragma: no cover - foreign-key invariant
            raise RuntimeError("reserved invitation not found")
        invitation = _without_secret(_from_row(invite_row, now=now))
        application = _join_request_from_row(request_row) if request_row is not None else None
        return invitation, application, changed

    def create(
        self,
        *,
        tenant_id: str,
        room_id: str,
        role: str,
        created_by: str,
        expires_in_seconds: int,
        max_uses: int,
    ) -> tuple[dict[str, Any], str]:
        tenant_id = _required_text(tenant_id, label="tenant_id")
        room_id = _required_text(room_id, label="room_id")
        created_by = _required_text(created_by, label="created_by")
        if role not in {"member", "viewer"}:
            raise ValueError("role must be member or viewer")
        if not 1 <= int(expires_in_seconds) <= 30 * 24 * 60 * 60:
            raise ValueError("expires_in_seconds must be between 1 second and 30 days")
        if not 1 <= int(max_uses) <= 1000:
            raise ValueError("max_uses must be between 1 and 1000")

        now = _as_utc(self._clock())
        token = secrets.token_urlsafe(32)
        values = (
            f"invite-{uuid4().hex}",
            tenant_id,
            room_id,
            _hash_token(token),
            role,
            created_by,
            now.isoformat(),
            (now + timedelta(seconds=int(expires_in_seconds))).isoformat(),
            int(max_uses),
        )
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO team_invitations("
                "invite_id, tenant_id, room_id, token_hash, role, created_by, "
                "created_at, expires_at, max_uses"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                values,
            )
            row = conn.execute(
                f"SELECT {_INVITE_COLUMNS} FROM team_invitations WHERE invite_id = ?",  # noqa: S608
                (values[0],),
            ).fetchone()
        if row is None:  # pragma: no cover - INSERT + same-transaction SELECT invariant
            raise RuntimeError("invitation insert did not return a row")
        return _without_secret(_from_row(row, now=now)), token

    def find_by_token(
        self,
        token: str,
        *,
        tenant_id: str,
    ) -> dict[str, Any] | None:
        tenant_id = _required_text(tenant_id, label="tenant_id")
        token_hash = _hash_token(_required_text(token, label="invite token"))
        now = _as_utc(self._clock())
        with self._lock, self._connect() as conn:
            row = conn.execute(
                f"SELECT {_INVITE_COLUMNS} FROM team_invitations "  # noqa: S608
                "WHERE token_hash = ? AND tenant_id = ?",
                (token_hash, tenant_id),
            ).fetchone()
        return _without_secret(_from_row(row, now=now)) if row is not None else None

    def require_usable_token(
        self,
        token: str,
        *,
        tenant_id: str,
    ) -> dict[str, Any]:
        invitation = self.find_by_token(token, tenant_id=tenant_id)
        if invitation is None:
            raise InvitationNotFound("invitation not found")
        _raise_unusable(invitation)
        return invitation

    def list_for_room(self, *, tenant_id: str, room_id: str) -> list[dict[str, Any]]:
        tenant_id = _required_text(tenant_id, label="tenant_id")
        room_id = _required_text(room_id, label="room_id")
        now = _as_utc(self._clock())
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_INVITE_COLUMNS} FROM team_invitations "  # noqa: S608
                "WHERE tenant_id = ? AND room_id = ? ORDER BY created_at DESC",
                (tenant_id, room_id),
            ).fetchall()
        return [_without_secret(_from_row(row, now=now)) for row in rows]

    def revoke(
        self,
        invite_id: str,
        *,
        tenant_id: str,
        room_id: str,
        revoked_by: str,
    ) -> dict[str, Any] | None:
        invite_id = _required_text(invite_id, label="invite_id")
        tenant_id = _required_text(tenant_id, label="tenant_id")
        room_id = _required_text(room_id, label="room_id")
        revoked_by = _required_text(revoked_by, label="revoked_by")
        now = _as_utc(self._clock())
        with self._lock, self._connect() as conn:
            row = conn.execute(
                f"SELECT {_INVITE_COLUMNS} FROM team_invitations "  # noqa: S608
                "WHERE invite_id = ? AND tenant_id = ? AND room_id = ?",
                (invite_id, tenant_id, room_id),
            ).fetchone()
            if row is None:
                return None
            if row["revoked_at"] is None:
                conn.execute(
                    "UPDATE team_invitations SET revoked_at = ?, revoked_by = ? "
                    "WHERE invite_id = ? AND revoked_at IS NULL",
                    (now.isoformat(), revoked_by, invite_id),
                )
                conn.execute(
                    "UPDATE team_join_requests SET status = 'cancelled', updated_at = ?, "
                    "decided_at = ?, decided_by = ?, decision_reason = ? "
                    "WHERE invite_id = ? AND status = 'pending' AND NOT EXISTS ("
                    "SELECT 1 FROM team_invitation_reservations r "
                    "WHERE r.invite_id = team_join_requests.invite_id "
                    "AND r.actor_id = team_join_requests.actor_id)",
                    (
                        now.isoformat(),
                        now.isoformat(),
                        revoked_by,
                        "invitation revoked",
                        invite_id,
                    ),
                )
                row = conn.execute(
                    f"SELECT {_INVITE_COLUMNS} FROM team_invitations "  # noqa: S608
                    "WHERE invite_id = ?",
                    (invite_id,),
                ).fetchone()
        return _without_secret(_from_row(row, now=now)) if row is not None else None

    def revoke_room(self, *, tenant_id: str, room_id: str, revoked_by: str) -> int:
        tenant_id = _required_text(tenant_id, label="tenant_id")
        room_id = _required_text(room_id, label="room_id")
        revoked_by = _required_text(revoked_by, label="revoked_by")
        now = _as_utc(self._clock()).isoformat()
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "UPDATE team_invitations SET revoked_at = ?, revoked_by = ? "
                "WHERE tenant_id = ? AND room_id = ? AND revoked_at IS NULL",
                (now, revoked_by, tenant_id, room_id),
            )
            conn.execute(
                "UPDATE team_join_requests SET status = 'cancelled', updated_at = ?, "
                "decided_at = ?, decided_by = ?, decision_reason = ? "
                "WHERE tenant_id = ? AND room_id = ? AND status = 'pending' "
                "AND NOT EXISTS (SELECT 1 FROM team_invitation_reservations r "
                "WHERE r.invite_id = team_join_requests.invite_id "
                "AND r.actor_id = team_join_requests.actor_id)",
                (now, now, revoked_by, "team room revoked", tenant_id, room_id),
            )
        return int(cursor.rowcount)

    def consume_with(
        self,
        token: str,
        *,
        tenant_id: str,
        room_id: str,
        actor_id: str,
        request_id: str,
        apply: Callable[[dict[str, Any]], _T],
        participant_id: str | None = None,
        membership_already_applied: bool = False,
    ) -> tuple[dict[str, Any], _T | None]:
        tenant_id = _required_text(tenant_id, label="tenant_id")
        room_id = _required_text(room_id, label="room_id")
        actor_id = _required_text(actor_id, label="actor_id")
        request_id = _required_text(request_id, label="request_id")
        resolved_participant_id = (
            _required_text(participant_id, label="participant_id")
            if participant_id is not None
            else None
        )
        token_hash = _hash_token(_required_text(token, label="invite token"))
        now = _as_utc(self._clock())
        with self._lock, self._connect() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    f"SELECT {_INVITE_COLUMNS} FROM team_invitations "  # noqa: S608
                    "WHERE token_hash = ? AND tenant_id = ? AND room_id = ?",
                    (token_hash, tenant_id, room_id),
                ).fetchone()
                if row is None:
                    raise InvitationNotFound("invitation not found")
                current = _from_row(row, now=now)
                current, reservation = self._reserve_use(
                    conn,
                    current,
                    actor_id=actor_id,
                    participant_id=resolved_participant_id,
                    audit_request_id=request_id,
                    join_request_id=None,
                    decided_by=None,
                    membership_already_applied=membership_already_applied,
                    now=now,
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        consumed = _reserved_payload(current, reservation)
        if reservation["finalized_at"] is not None:
            return consumed, None
        result = apply(consumed)
        finalized, _application, _changed = self._finalize_reservation(
            str(reservation["reservation_id"])
        )
        return {**consumed, **finalized}, result

    def create_join_request(
        self,
        token: str,
        *,
        tenant_id: str,
        room_id: str,
        actor_id: str,
        display_name: str,
    ) -> tuple[dict[str, Any], dict[str, Any], bool]:
        """Create one durable, replay-safe application per invitation and actor."""

        tenant_id = _required_text(tenant_id, label="tenant_id")
        room_id = _required_text(room_id, label="room_id")
        actor_id = _required_text(actor_id, label="actor_id")
        display_name = _required_text(display_name, label="display_name")
        token_hash = _hash_token(_required_text(token, label="invite token"))
        now = _as_utc(self._clock())
        with self._lock, self._connect() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                self._refresh_join_request_states(conn, now)
                invite_row = conn.execute(
                    f"SELECT {_INVITE_COLUMNS} FROM team_invitations "  # noqa: S608
                    "WHERE token_hash = ? AND tenant_id = ? AND room_id = ?",
                    (token_hash, tenant_id, room_id),
                ).fetchone()
                if invite_row is None:
                    raise InvitationNotFound("invitation not found")
                invitation = _from_row(invite_row, now=now)
                existing = conn.execute(
                    f"SELECT {_JOIN_REQUEST_COLUMNS} FROM team_join_requests "  # noqa: S608
                    "WHERE invite_id = ? AND actor_id = ?",
                    (invitation["id"], actor_id),
                ).fetchone()
                if existing is not None:
                    conn.commit()
                    return (
                        _without_secret(invitation),
                        _join_request_from_row(existing),
                        False,
                    )
                _raise_unusable(invitation)
                request_id = f"join-{uuid4().hex}"
                timestamp = now.isoformat()
                conn.execute(
                    "INSERT INTO team_join_requests("
                    "request_id, invite_id, tenant_id, room_id, actor_id, display_name, "
                    "role, status, created_at, updated_at, expires_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)",
                    (
                        request_id,
                        invitation["id"],
                        tenant_id,
                        room_id,
                        actor_id,
                        display_name,
                        invitation["role"],
                        timestamp,
                        timestamp,
                        invitation["expires_at"],
                    ),
                )
                request_row = self._request_row(
                    conn,
                    request_id,
                    tenant_id=tenant_id,
                    room_id=room_id,
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        if request_row is None:  # pragma: no cover - insert/select invariant
            raise RuntimeError("join request insert did not return a row")
        return _without_secret(invitation), _join_request_from_row(request_row), True

    def join_request_for_actor_token(
        self,
        token: str,
        *,
        tenant_id: str,
        actor_id: str,
    ) -> dict[str, Any] | None:
        tenant_id = _required_text(tenant_id, label="tenant_id")
        actor_id = _required_text(actor_id, label="actor_id")
        token_hash = _hash_token(_required_text(token, label="invite token"))
        now = _as_utc(self._clock())
        with self._lock, self._connect() as conn:
            self._refresh_join_request_states(conn, now)
            row = conn.execute(
                f"SELECT r.{_JOIN_REQUEST_COLUMNS.replace(', ', ', r.')} "  # noqa: S608
                "FROM team_join_requests r JOIN team_invitations i "
                "ON i.invite_id = r.invite_id "
                "WHERE i.token_hash = ? AND r.tenant_id = ? AND r.actor_id = ?",
                (token_hash, tenant_id, actor_id),
            ).fetchone()
        return _join_request_from_row(row) if row is not None else None

    def get_join_request(
        self,
        request_id: str,
        *,
        tenant_id: str,
        room_id: str,
    ) -> dict[str, Any] | None:
        request_id = _required_text(request_id, label="request_id")
        tenant_id = _required_text(tenant_id, label="tenant_id")
        room_id = _required_text(room_id, label="room_id")
        now = _as_utc(self._clock())
        with self._lock, self._connect() as conn:
            self._refresh_join_request_states(conn, now)
            row = self._request_row(
                conn,
                request_id,
                tenant_id=tenant_id,
                room_id=room_id,
            )
        return _join_request_from_row(row) if row is not None else None

    def list_join_requests(
        self,
        *,
        tenant_id: str,
        room_id: str,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        tenant_id = _required_text(tenant_id, label="tenant_id")
        room_id = _required_text(room_id, label="room_id")
        normalized_status = str(status or "").strip().lower()
        allowed = {"pending", "approved", "rejected", "withdrawn", "expired", "cancelled"}
        if normalized_status and normalized_status not in allowed:
            raise ValueError("invalid join request status")
        now = _as_utc(self._clock())
        with self._lock, self._connect() as conn:
            self._refresh_join_request_states(conn, now)
            sql = (
                f"SELECT {_JOIN_REQUEST_COLUMNS} FROM team_join_requests "  # noqa: S608
                "WHERE tenant_id = ? AND room_id = ?"
            )
            params: tuple[Any, ...] = (tenant_id, room_id)
            if normalized_status:
                sql += " AND status = ?"
                params = (*params, normalized_status)
            sql += " ORDER BY created_at DESC, request_id DESC"
            rows = conn.execute(sql, params).fetchall()
        return [_join_request_from_row(row) for row in rows]

    def withdraw_join_request(
        self,
        token: str,
        *,
        tenant_id: str,
        actor_id: str,
    ) -> dict[str, Any]:
        tenant_id = _required_text(tenant_id, label="tenant_id")
        actor_id = _required_text(actor_id, label="actor_id")
        token_hash = _hash_token(_required_text(token, label="invite token"))
        now = _as_utc(self._clock())
        with self._lock, self._connect() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                self._refresh_join_request_states(conn, now)
                row = conn.execute(
                    f"SELECT r.{_JOIN_REQUEST_COLUMNS.replace(', ', ', r.')} "  # noqa: S608
                    "FROM team_join_requests r JOIN team_invitations i "
                    "ON i.invite_id = r.invite_id "
                    "WHERE i.token_hash = ? AND r.tenant_id = ? AND r.actor_id = ?",
                    (token_hash, tenant_id, actor_id),
                ).fetchone()
                if row is None:
                    raise JoinRequestNotFound("join request not found")
                application = _join_request_from_row(row)
                if application["status"] == "withdrawn":
                    conn.commit()
                    return application
                if application["status"] != "pending":
                    raise JoinRequestConflict(f"join request is already {application['status']}")
                if _join_request_has_reservation(conn, application):
                    raise JoinRequestConflict("join request approval is in progress")
                timestamp = now.isoformat()
                conn.execute(
                    "UPDATE team_join_requests SET status = 'withdrawn', updated_at = ?, "
                    "decided_at = ?, decided_by = ?, decision_reason = ? "
                    "WHERE request_id = ? AND status = 'pending'",
                    (
                        timestamp,
                        timestamp,
                        actor_id,
                        "withdrawn by applicant",
                        application["id"],
                    ),
                )
                updated = self._request_row(
                    conn,
                    application["id"],
                    tenant_id=tenant_id,
                    room_id=application["room_id"],
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        if updated is None:  # pragma: no cover - update/select invariant
            raise RuntimeError("join request withdrawal did not return a row")
        return _join_request_from_row(updated)

    def reject_join_request(
        self,
        request_id: str,
        *,
        tenant_id: str,
        room_id: str,
        decided_by: str,
        reason: str = "",
    ) -> tuple[dict[str, Any], bool]:
        request_id = _required_text(request_id, label="request_id")
        tenant_id = _required_text(tenant_id, label="tenant_id")
        room_id = _required_text(room_id, label="room_id")
        decided_by = _required_text(decided_by, label="decided_by")
        reason = _optional_note(reason, label="decision reason")
        now = _as_utc(self._clock())
        with self._lock, self._connect() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                self._refresh_join_request_states(conn, now)
                row = self._request_row(
                    conn,
                    request_id,
                    tenant_id=tenant_id,
                    room_id=room_id,
                )
                if row is None:
                    raise JoinRequestNotFound("join request not found")
                application = _join_request_from_row(row)
                if application["status"] == "rejected":
                    conn.commit()
                    return application, False
                if application["status"] != "pending":
                    raise JoinRequestConflict(f"join request is already {application['status']}")
                if _join_request_has_reservation(conn, application):
                    raise JoinRequestConflict("join request approval is in progress")
                timestamp = now.isoformat()
                conn.execute(
                    "UPDATE team_join_requests SET status = 'rejected', updated_at = ?, "
                    "decided_at = ?, decided_by = ?, decision_reason = ? "
                    "WHERE request_id = ? AND status = 'pending'",
                    (timestamp, timestamp, decided_by, reason, request_id),
                )
                updated = self._request_row(
                    conn,
                    request_id,
                    tenant_id=tenant_id,
                    room_id=room_id,
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        if updated is None:  # pragma: no cover - update/select invariant
            raise RuntimeError("join request rejection did not return a row")
        return _join_request_from_row(updated), True

    def approve_existing_membership(
        self,
        request_id: str,
        *,
        tenant_id: str,
        room_id: str,
        decided_by: str,
        participant_id: str,
    ) -> tuple[dict[str, Any], bool]:
        """Close a pending request when the actor already joined another way."""

        request_id = _required_text(request_id, label="request_id")
        tenant_id = _required_text(tenant_id, label="tenant_id")
        room_id = _required_text(room_id, label="room_id")
        decided_by = _required_text(decided_by, label="decided_by")
        participant_id = _required_text(participant_id, label="participant_id")
        now = _as_utc(self._clock())
        with self._lock, self._connect() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                self._refresh_join_request_states(conn, now)
                row = self._request_row(
                    conn,
                    request_id,
                    tenant_id=tenant_id,
                    room_id=room_id,
                )
                if row is None:
                    raise JoinRequestNotFound("join request not found")
                application = _join_request_from_row(row)
                if application["status"] == "approved":
                    conn.commit()
                    return application, False
                if application["status"] != "pending":
                    raise JoinRequestConflict(f"join request is already {application['status']}")
                if _join_request_has_reservation(conn, application):
                    raise JoinRequestConflict("join request approval is in progress")
                timestamp = now.isoformat()
                conn.execute(
                    "UPDATE team_join_requests SET status = 'approved', updated_at = ?, "
                    "decided_at = ?, decided_by = ?, decision_reason = ?, participant_id = ? "
                    "WHERE request_id = ? AND status = 'pending'",
                    (
                        timestamp,
                        timestamp,
                        decided_by,
                        "actor already held active membership",
                        participant_id,
                        request_id,
                    ),
                )
                updated = self._request_row(
                    conn,
                    request_id,
                    tenant_id=tenant_id,
                    room_id=room_id,
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        if updated is None:  # pragma: no cover - update/select invariant
            raise RuntimeError("join request approval did not return a row")
        return _join_request_from_row(updated), True

    def approve_join_request_with(
        self,
        request_id: str,
        *,
        tenant_id: str,
        room_id: str,
        decided_by: str,
        participant_id: str,
        audit_request_id: str,
        apply: Callable[[dict[str, Any], dict[str, Any]], _T],
        membership_already_applied: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any], _T | None, bool]:
        request_id = _required_text(request_id, label="request_id")
        tenant_id = _required_text(tenant_id, label="tenant_id")
        room_id = _required_text(room_id, label="room_id")
        decided_by = _required_text(decided_by, label="decided_by")
        participant_id = _required_text(participant_id, label="participant_id")
        audit_request_id = _required_text(audit_request_id, label="audit_request_id")
        now = _as_utc(self._clock())
        with self._lock, self._connect() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                self._refresh_join_request_states(conn, now)
                request_row = self._request_row(
                    conn,
                    request_id,
                    tenant_id=tenant_id,
                    room_id=room_id,
                )
                if request_row is None:
                    raise JoinRequestNotFound("join request not found")
                application = _join_request_from_row(request_row)
                invite_row = self._invitation_row_for_request(conn, request_row)
                if invite_row is None:
                    raise InvitationNotFound("invitation not found")
                invitation = _from_row(invite_row, now=now)
                if application["status"] == "approved":
                    conn.commit()
                    return (
                        _without_secret(invitation),
                        application,
                        None,
                        False,
                    )
                reservation = _reservation_row(
                    conn,
                    invite_id=str(invitation["id"]),
                    actor_id=str(application["actor_id"]),
                )
                if application["status"] != "pending" and reservation is None:
                    raise JoinRequestConflict(f"join request is already {application['status']}")
                invitation, reservation = self._reserve_use(
                    conn,
                    invitation,
                    actor_id=str(application["actor_id"]),
                    participant_id=participant_id,
                    audit_request_id=audit_request_id,
                    join_request_id=request_id,
                    decided_by=decided_by,
                    membership_already_applied=membership_already_applied,
                    now=now,
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        consumed = _reserved_payload(invitation, reservation)
        if reservation["join_request_id"] != request_id:
            raise JoinRequestConflict("actor already reserved this invitation")
        if reservation["finalized_at"] is not None:
            finalized, approved, _changed = self._finalize_reservation(
                str(reservation["reservation_id"])
            )
            if approved is None:  # pragma: no cover - reservation invariant
                raise RuntimeError("approved reservation has no join request")
            return {**consumed, **finalized}, approved, None, False
        pending_approval = {
            **application,
            "status": "approved",
            "decided_by": str(reservation["decided_by"]),
            "participant_id": str(reservation["participant_id"]),
        }
        result = apply(consumed, pending_approval)
        finalized, approved, changed = self._finalize_reservation(
            str(reservation["reservation_id"])
        )
        if approved is None:  # pragma: no cover - reservation invariant
            raise RuntimeError("approved reservation has no join request")
        return {**consumed, **finalized}, approved, result, changed

    def acceptances(self, *, invite_id: str) -> list[dict[str, Any]]:
        """Administrative audit records; never contains the bearer token."""

        invite_id = _required_text(invite_id, label="invite_id")
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT use_number, tenant_id, room_id, actor_id, used_at, request_id "
                "FROM team_invitation_uses WHERE invite_id = ? ORDER BY use_number",
                (invite_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def clear(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM team_join_requests")
            conn.execute("DELETE FROM team_invitation_uses")
            conn.execute("DELETE FROM team_invitation_reservations")
            conn.execute("DELETE FROM team_invitations")


__all__ = [
    "InvitationError",
    "InvitationExhausted",
    "InvitationExpired",
    "InvitationNotFound",
    "InvitationRevoked",
    "JoinRequestConflict",
    "JoinRequestNotFound",
    "TeamInvitationStore",
]
