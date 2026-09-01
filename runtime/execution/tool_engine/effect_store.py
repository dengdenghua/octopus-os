"""Transactional cross-process coordination for tool side effects.

The append-only execution journal explains what happened, but it is not a
compare-and-swap primitive.  Two server workers can both read "no receipt"
before either appends an intent.  This store closes that race with SQLite's
``BEGIN IMMEDIATE`` transaction and a fenced, expiring claim.

The state machine deliberately distinguishes ``claimed`` from ``started``:

* an expired ``claimed`` row is safe to take over because no durable intent
  says the handler was entered;
* an expired side-effecting ``started`` row becomes ``indeterminate`` and is
  never executed again automatically;
* ``committed`` rows carry the exact structured Step for zero-cost replay.

SQLite is used only as the coordination/receipt plane.  The journal remains
the audit source of truth and can repair a receipt if a process dies between
the journal Step append and the SQLite commit.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from runtime.platform.models import Step

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tool_effect_receipts (
    effect_key       TEXT PRIMARY KEY,
    task_id          TEXT NOT NULL,
    step_id          INTEGER NOT NULL,
    sucker_id        TEXT NOT NULL,
    args_fingerprint TEXT NOT NULL,
    side_effecting   INTEGER NOT NULL,
    state            TEXT NOT NULL,
    holder_id        TEXT NOT NULL DEFAULT '',
    fencing_token    INTEGER NOT NULL DEFAULT 0,
    lease_expires_at REAL NOT NULL DEFAULT 0,
    call_id          TEXT NOT NULL DEFAULT '',
    step_json        TEXT,
    has_result       INTEGER NOT NULL DEFAULT 0,
    reason           TEXT NOT NULL DEFAULT '',
    created_at       REAL NOT NULL,
    updated_at       REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tool_effect_receipts_state
    ON tool_effect_receipts(state, updated_at);

CREATE INDEX IF NOT EXISTS idx_tool_effect_receipts_priority_updated
    ON tool_effect_receipts(
        CASE state
            WHEN 'indeterminate' THEN 0
            WHEN 'started' THEN 1
            WHEN 'claimed' THEN 2
            WHEN 'retry_authorized' THEN 3
            ELSE 4
        END,
        updated_at DESC
    );
"""

_RECEIPT_SUMMARY_COLUMNS = """
    effect_key, task_id, step_id, sucker_id, side_effecting, state,
    holder_id, fencing_token, lease_expires_at, call_id, reason,
    updated_at, has_result
"""

_RECEIPT_PRIORITY_ORDER = """
    CASE state
        WHEN 'indeterminate' THEN 0
        WHEN 'started' THEN 1
        WHEN 'claimed' THEN 2
        WHEN 'retry_authorized' THEN 3
        ELSE 4
    END,
    updated_at DESC
"""


@dataclass(frozen=True)
class StoreDecision:
    kind: Literal["execute", "busy", "replay", "indeterminate"]
    fencing_token: int = 0
    lease_expires_at: float = 0.0
    step: Step | None = None
    reason: str = ""


@dataclass(frozen=True)
class EffectReceipt:
    """Operator-safe view of one durable tool-effect receipt."""

    effect_key: str
    task_id: str
    step_id: int
    sucker_id: str
    side_effecting: bool
    state: str
    holder_id: str
    fencing_token: int
    lease_expires_at: float
    call_id: str
    reason: str
    updated_at: float
    has_result: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "effect_key": self.effect_key,
            "task_id": self.task_id,
            "step_id": self.step_id,
            "sucker_id": self.sucker_id,
            "side_effecting": self.side_effecting,
            "state": self.state,
            "holder_id": self.holder_id,
            "fencing_token": self.fencing_token,
            "lease_expires_at": self.lease_expires_at,
            "call_id": self.call_id,
            "reason": self.reason,
            "updated_at": self.updated_at,
            "has_result": self.has_result,
        }


@runtime_checkable
class EffectStore(Protocol):
    """Shared contract for local and cluster receipt planes."""

    backend_name: str
    shared_across_hosts: bool

    def claim(
        self,
        *,
        effect_key: str,
        task_id: str,
        step_id: int,
        sucker_id: str,
        args_fingerprint: str,
        side_effecting: bool,
        holder_id: str,
        lease_ttl_s: float,
        observed_durable_intent: bool,
    ) -> StoreDecision: ...

    def mark_started(
        self,
        *,
        effect_key: str,
        holder_id: str,
        fencing_token: int,
        call_id: str,
        lease_ttl_s: float,
    ) -> bool: ...

    def renew(
        self,
        *,
        effect_key: str,
        holder_id: str,
        fencing_token: int,
        lease_ttl_s: float,
    ) -> bool: ...

    def commit(
        self,
        *,
        effect_key: str,
        holder_id: str,
        fencing_token: int,
        step: Step,
    ) -> bool: ...

    def record_committed(self, *, effect_key: str, step: Step) -> None: ...

    def finish_failed(
        self,
        *,
        effect_key: str,
        holder_id: str,
        fencing_token: int,
        side_effecting: bool,
        reason: str,
    ) -> None: ...

    def release_unstarted(
        self,
        *,
        effect_key: str,
        holder_id: str,
        fencing_token: int,
    ) -> None: ...

    def ping(self) -> bool: ...

    def list_receipts(
        self,
        *,
        state: str | None = None,
        limit: int = 100,
    ) -> list[EffectReceipt]: ...

    def authorize_retry(
        self,
        *,
        effect_key: str,
        expected_fencing_token: int,
        actor: str,
        reason: str,
    ) -> bool: ...


class SQLiteEffectStore:
    """A fork-safe SQLite receipt store.

    Connections are intentionally short-lived.  A process may be forked after
    the executor is constructed; keeping one inherited SQLite connection would
    make that unsafe.  WAL permits waiters to read committed results while the
    owner is alive, and ``synchronous=FULL`` makes the pre-handler claim a real
    crash boundary rather than a best-effort cache write.
    """

    backend_name = "sqlite"
    shared_across_hosts = False

    def __init__(self, path: str | Path, *, busy_timeout_s: float = 5.0) -> None:
        self.path = Path(path).expanduser().resolve(strict=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._busy_timeout_ms = max(1, int(float(busy_timeout_s) * 1000))
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        # The state directory can be replaced while a long-lived server still
        # owns this store instance (for example after a workspace reset).  A
        # new SQLite file is otherwise created without our schema and every
        # subsequent receipt query fails until the process restarts.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(self.path),
            timeout=self._busy_timeout_ms / 1000,
            isolation_level=None,
        )
        try:
            conn.row_factory = sqlite3.Row
            conn.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
            table_exists = conn.execute(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type = 'table' AND name = 'tool_effect_receipts'
                """
            ).fetchone()
            if table_exists is None:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=FULL")
                conn.executescript(_SCHEMA)
            return conn
        except Exception:
            conn.close()
            raise

    def _initialize(self) -> None:
        deadline = time.monotonic() + (self._busy_timeout_ms / 1000)
        while True:
            try:
                with contextlib.closing(self._connect()) as conn:
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("PRAGMA synchronous=FULL")
                    conn.executescript(_SCHEMA)
                    self._migrate_receipt_summary(conn)
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)
        with contextlib.suppress(OSError):
            os.chmod(self.path, 0o600)

    @staticmethod
    def _migrate_receipt_summary(conn: sqlite3.Connection) -> None:
        """Add the small result-presence summary to pre-existing stores."""

        conn.execute("BEGIN IMMEDIATE")
        try:
            columns = {
                str(row["name"]) for row in conn.execute("PRAGMA table_info(tool_effect_receipts)")
            }
            if "has_result" not in columns:
                conn.execute(
                    """
                    ALTER TABLE tool_effect_receipts
                    ADD COLUMN has_result INTEGER NOT NULL DEFAULT 0
                    """
                )
                conn.execute(
                    """
                    UPDATE tool_effect_receipts
                    SET has_result = 1
                    WHERE step_json IS NOT NULL AND step_json <> ''
                    """
                )
            conn.execute("COMMIT")
        except Exception:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            raise

    def ping(self) -> bool:
        try:
            with contextlib.closing(self._connect()) as conn:
                return conn.execute("SELECT 1").fetchone()[0] == 1
        except sqlite3.Error:
            return False

    def list_receipts(
        self,
        *,
        state: str | None = None,
        limit: int = 100,
    ) -> list[EffectReceipt]:
        safe_limit = max(1, min(int(limit), 500))
        query = f"SELECT {_RECEIPT_SUMMARY_COLUMNS} FROM tool_effect_receipts"
        params: tuple[object, ...]
        if state is not None:
            query += " WHERE state = ? ORDER BY updated_at DESC LIMIT ?"
            params = (state, safe_limit)
        else:
            query += f" ORDER BY {_RECEIPT_PRIORITY_ORDER} LIMIT ?"
            params = (safe_limit,)
        with contextlib.closing(self._connect()) as conn:
            rows = conn.execute(query, params).fetchall()
        return [_receipt_from_sqlite_row(row) for row in rows]

    def authorize_retry(
        self,
        *,
        effect_key: str,
        expected_fencing_token: int,
        actor: str,
        reason: str,
    ) -> bool:
        """Allow one explicitly reviewed retry of an indeterminate effect."""

        now = time.time()
        with contextlib.closing(self._connect()) as conn:
            cursor = conn.execute(
                """
                UPDATE tool_effect_receipts
                SET state = 'retry_authorized', holder_id = ?, reason = ?,
                    lease_expires_at = 0, updated_at = ?
                WHERE effect_key = ? AND state = 'indeterminate'
                  AND fencing_token = ? AND lease_expires_at <= ?
                """,
                (
                    actor,
                    reason,
                    now,
                    effect_key,
                    expected_fencing_token,
                    now,
                ),
            )
            return cursor.rowcount == 1

    def claim(
        self,
        *,
        effect_key: str,
        task_id: str,
        step_id: int,
        sucker_id: str,
        args_fingerprint: str,
        side_effecting: bool,
        holder_id: str,
        lease_ttl_s: float,
        observed_durable_intent: bool,
    ) -> StoreDecision:
        now = time.time()
        expires_at = now + max(0.05, float(lease_ttl_s))
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM tool_effect_receipts WHERE effect_key = ?",
                (effect_key,),
            ).fetchone()
            if row is None:
                if observed_durable_intent and side_effecting:
                    reason = _dangling_intent_reason()
                    conn.execute(
                        """
                        INSERT INTO tool_effect_receipts(
                            effect_key, task_id, step_id, sucker_id,
                            args_fingerprint, side_effecting, state, reason,
                            created_at, updated_at
                        ) VALUES(?, ?, ?, ?, ?, 1, 'indeterminate', ?, ?, ?)
                        """,
                        (
                            effect_key,
                            task_id,
                            step_id,
                            sucker_id,
                            args_fingerprint,
                            reason,
                            now,
                            now,
                        ),
                    )
                    conn.execute("COMMIT")
                    return StoreDecision("indeterminate", reason=reason)
                conn.execute(
                    """
                    INSERT INTO tool_effect_receipts(
                        effect_key, task_id, step_id, sucker_id,
                        args_fingerprint, side_effecting, state, holder_id,
                        fencing_token, lease_expires_at, created_at, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, 'claimed', ?, 1, ?, ?, ?)
                    """,
                    (
                        effect_key,
                        task_id,
                        step_id,
                        sucker_id,
                        args_fingerprint,
                        int(side_effecting),
                        holder_id,
                        expires_at,
                        now,
                        now,
                    ),
                )
                conn.execute("COMMIT")
                return StoreDecision("execute", 1, expires_at)

            state = str(row["state"])
            token = int(row["fencing_token"])
            if state == "committed":
                step = _decode_step(row["step_json"])
                if step is not None:
                    conn.execute("COMMIT")
                    return StoreDecision("replay", token, step=step)
                reason = "committed tool receipt is missing a valid structured result"
                self._mark_indeterminate_tx(conn, effect_key, reason, now)
                conn.execute("COMMIT")
                return StoreDecision("indeterminate", token, reason=reason)
            if state == "indeterminate":
                conn.execute("COMMIT")
                return StoreDecision(
                    "indeterminate",
                    token,
                    reason=str(row["reason"] or _dangling_intent_reason()),
                )

            lease_expires = float(row["lease_expires_at"] or 0.0)
            owner = str(row["holder_id"] or "")
            live = lease_expires > now
            if live and owner != holder_id:
                conn.execute("COMMIT")
                return StoreDecision(
                    "busy",
                    token,
                    lease_expires,
                    reason="another process owns the live tool-effect lease",
                )
            if live and owner == holder_id:
                conn.execute("COMMIT")
                return StoreDecision("execute", token, lease_expires)

            prior_side_effecting = bool(row["side_effecting"])
            retry_authorized = state == "retry_authorized"
            unsafe_started = (
                not retry_authorized
                and state == "started"
                and (side_effecting or prior_side_effecting)
            )
            unsafe_intent = (
                not retry_authorized
                and observed_durable_intent
                and (side_effecting or prior_side_effecting)
            )
            if unsafe_started or unsafe_intent:
                reason = _dangling_intent_reason()
                self._mark_indeterminate_tx(conn, effect_key, reason, now)
                conn.execute("COMMIT")
                return StoreDecision("indeterminate", token, reason=reason)

            next_token = token + 1
            conn.execute(
                """
                UPDATE tool_effect_receipts
                SET task_id = ?, step_id = ?, sucker_id = ?,
                    args_fingerprint = ?, side_effecting = ?, state = 'claimed',
                    holder_id = ?, fencing_token = ?, lease_expires_at = ?,
                    call_id = '', step_json = NULL, has_result = 0,
                    reason = '', updated_at = ?
                WHERE effect_key = ?
                """,
                (
                    task_id,
                    step_id,
                    sucker_id,
                    args_fingerprint,
                    int(side_effecting),
                    holder_id,
                    next_token,
                    expires_at,
                    now,
                    effect_key,
                ),
            )
            conn.execute("COMMIT")
            return StoreDecision("execute", next_token, expires_at)
        except Exception:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def mark_started(
        self,
        *,
        effect_key: str,
        holder_id: str,
        fencing_token: int,
        call_id: str,
        lease_ttl_s: float,
    ) -> bool:
        now = time.time()
        with contextlib.closing(self._connect()) as conn:
            cursor = conn.execute(
                """
                UPDATE tool_effect_receipts
                SET state = 'started', call_id = ?, lease_expires_at = ?, updated_at = ?
                WHERE effect_key = ? AND holder_id = ? AND fencing_token = ?
                  AND state IN ('claimed', 'started')
                """,
                (
                    call_id,
                    now + max(0.05, float(lease_ttl_s)),
                    now,
                    effect_key,
                    holder_id,
                    fencing_token,
                ),
            )
            return cursor.rowcount == 1

    def renew(
        self,
        *,
        effect_key: str,
        holder_id: str,
        fencing_token: int,
        lease_ttl_s: float,
    ) -> bool:
        now = time.time()
        with contextlib.closing(self._connect()) as conn:
            cursor = conn.execute(
                """
                UPDATE tool_effect_receipts
                SET lease_expires_at = ?, updated_at = ?
                WHERE effect_key = ? AND holder_id = ? AND fencing_token = ?
                  AND state IN ('claimed', 'started')
                """,
                (
                    now + max(0.05, float(lease_ttl_s)),
                    now,
                    effect_key,
                    holder_id,
                    fencing_token,
                ),
            )
            return cursor.rowcount == 1

    def commit(
        self,
        *,
        effect_key: str,
        holder_id: str,
        fencing_token: int,
        step: Step,
    ) -> bool:
        now = time.time()
        with contextlib.closing(self._connect()) as conn:
            cursor = conn.execute(
                """
                UPDATE tool_effect_receipts
                SET state = 'committed', step_json = ?, has_result = 1,
                    lease_expires_at = 0, reason = '', updated_at = ?
                WHERE effect_key = ? AND holder_id = ? AND fencing_token = ?
                  AND state IN ('claimed', 'started')
                """,
                (
                    step.model_dump_json(),
                    now,
                    effect_key,
                    holder_id,
                    fencing_token,
                ),
            )
            return cursor.rowcount == 1

    def record_committed(self, *, effect_key: str, step: Step) -> None:
        """Repair/seed a receipt from the durable journal's Step event."""

        now = time.time()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute(
                """
                SELECT state, lease_expires_at
                FROM tool_effect_receipts WHERE effect_key = ?
                """,
                (effect_key,),
            ).fetchone()
            if (
                current is not None
                and str(current["state"]) in {"claimed", "started"}
                and float(current["lease_expires_at"] or 0.0) > now
            ):
                conn.execute("COMMIT")
                return
            conn.execute(
                """
                INSERT INTO tool_effect_receipts(
                    effect_key, task_id, step_id, sucker_id,
                    args_fingerprint, side_effecting, state, fencing_token,
                    step_json, has_result, created_at, updated_at
                ) VALUES(?, '', 0, '', '', 0, 'committed', 0, ?, 1, ?, ?)
                ON CONFLICT(effect_key) DO UPDATE SET
                    state = 'committed', step_json = excluded.step_json,
                    has_result = 1, lease_expires_at = 0, reason = '',
                    updated_at = excluded.updated_at
                """,
                (effect_key, step.model_dump_json(), now, now),
            )
            conn.execute("COMMIT")
        except Exception:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def finish_failed(
        self,
        *,
        effect_key: str,
        holder_id: str,
        fencing_token: int,
        side_effecting: bool,
        reason: str,
    ) -> None:
        now = time.time()
        with contextlib.closing(self._connect()) as conn:
            if side_effecting:
                conn.execute(
                    """
                    UPDATE tool_effect_receipts
                    SET state = 'indeterminate', lease_expires_at = 0,
                        reason = ?, updated_at = ?
                    WHERE effect_key = ? AND holder_id = ? AND fencing_token = ?
                    """,
                    (reason, now, effect_key, holder_id, fencing_token),
                )
            else:
                conn.execute(
                    """
                    DELETE FROM tool_effect_receipts
                    WHERE effect_key = ? AND holder_id = ? AND fencing_token = ?
                    """,
                    (effect_key, holder_id, fencing_token),
                )

    def release_unstarted(
        self,
        *,
        effect_key: str,
        holder_id: str,
        fencing_token: int,
    ) -> None:
        with contextlib.closing(self._connect()) as conn:
            conn.execute(
                """
                DELETE FROM tool_effect_receipts
                WHERE effect_key = ? AND holder_id = ? AND fencing_token = ?
                  AND state = 'claimed'
                """,
                (effect_key, holder_id, fencing_token),
            )

    @staticmethod
    def _mark_indeterminate_tx(
        conn: sqlite3.Connection,
        effect_key: str,
        reason: str,
        now: float,
    ) -> None:
        conn.execute(
            """
            UPDATE tool_effect_receipts
            SET state = 'indeterminate', lease_expires_at = 0,
                reason = ?, updated_at = ?
            WHERE effect_key = ?
            """,
            (reason, now, effect_key),
        )


def _decode_step(raw: object) -> Step | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return Step.model_validate_json(raw)
    except (TypeError, ValueError):
        return None


def _receipt_from_sqlite_row(row: sqlite3.Row) -> EffectReceipt:
    return EffectReceipt(
        effect_key=str(row["effect_key"]),
        task_id=str(row["task_id"]),
        step_id=int(row["step_id"]),
        sucker_id=str(row["sucker_id"]),
        side_effecting=bool(row["side_effecting"]),
        state=str(row["state"]),
        holder_id=str(row["holder_id"] or ""),
        fencing_token=int(row["fencing_token"]),
        lease_expires_at=float(row["lease_expires_at"] or 0.0),
        call_id=str(row["call_id"] or ""),
        reason=str(row["reason"] or ""),
        updated_at=float(row["updated_at"] or 0.0),
        has_result=bool(row["has_result"]),
    )


def _dangling_intent_reason() -> str:
    return (
        "a previous process entered this side-effecting tool but did not durably record its result"
    )


__all__ = ["EffectReceipt", "EffectStore", "SQLiteEffectStore", "StoreDecision"]
