"""Durable Echo-owned background operations for Hub lifecycle changes."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import re
import sqlite3
import stat
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from appliance.app_registry.docker_client import (
    DockerConflict,
    DockerControlDenied,
    DockerUnavailable,
)
from appliance.audit import ApplianceAudit, AuditIntegrityError
from appliance.hub.progress import hub_progress, validate_hub_progress

HUB_OPERATIONS_FILENAME = "hub-operations.sqlite3"
HUB_OPERATION_SCHEMA = "echo.hub.operation.v1"
HUB_OPERATIONS_SCHEMA = "echo.hub.operations.v1"

_OPERATION_ID = re.compile(r"^[0-9a-f]{32}$")
_ACTIVE_STATUSES = ("queued", "running")
_FINAL_STATUSES = ("succeeded", "failed", "interrupted")
_MAX_RESULT_BYTES = 128 * 1024

HubOperationAction = Literal[
    "install",
    "update",
    "uninstall",
    "start",
    "stop",
    "restart",
]

_log = logging.getLogger(__name__)


class HubOperationConflict(RuntimeError):
    """Raised when an app already has an active lifecycle operation."""


class HubOperationUnavailable(RuntimeError):
    """Raised when the bounded operation queue cannot accept more work."""


class HubOperationCredentialsUnavailable(RuntimeError):
    """Raised when one-time operation credentials cannot be claimed."""


class HubOperationExecutor(Protocol):
    def install_hub_app(
        self, app_id: str, *, plan_id: str, catalog_digest: str
    ) -> dict[str, Any]: ...

    def update_hub_app(
        self, app_id: str, *, plan_id: str, catalog_digest: str
    ) -> dict[str, Any]: ...

    def uninstall_hub_app(
        self, app_id: str, *, plan_id: str, catalog_digest: str
    ) -> dict[str, Any]: ...

    def start_hub_app(
        self, app_id: str, *, plan_id: str, catalog_digest: str
    ) -> dict[str, Any]: ...

    def stop_hub_app(self, app_id: str, *, plan_id: str, catalog_digest: str) -> dict[str, Any]: ...

    def restart_hub_app(
        self, app_id: str, *, plan_id: str, catalog_digest: str
    ) -> dict[str, Any]: ...


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class _StoredOperation:
    operation_id: str
    action: HubOperationAction
    app_id: str
    plan_id: str
    catalog_digest: str
    actor: str
    intent_id: str | None
    status: str


class HubOperationStore:
    """Small private SQLite ledger owned by Echo, not by Agent."""

    def __init__(self, data_dir: str | Path, *, encryption_secret: str) -> None:
        if not encryption_secret:
            raise ValueError("Hub operation encryption secret is required")
        root = Path(data_dir)
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / HUB_OPERATIONS_FILENAME
        self._assert_safe_path()
        self._key = hashlib.sha256(
            b"echo-os/hub-operation-result/v1\0" + encryption_secret.encode("utf-8")
        ).digest()
        self._initialize()
        self.interrupt_active()

    def _assert_safe_path(self) -> None:
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise RuntimeError("Hub operation store path is unsafe")

    def _connect(self) -> sqlite3.Connection:
        self._assert_safe_path()
        connection = sqlite3.connect(str(self.path), timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS hub_operations (
                    operation_id TEXT PRIMARY KEY,
                    action TEXT NOT NULL CHECK (
                        action IN ('install', 'update', 'uninstall', 'start', 'stop', 'restart')
                    ),
                    app_id TEXT NOT NULL,
                    plan_id TEXT NOT NULL,
                    catalog_digest TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    intent_id TEXT,
                    status TEXT NOT NULL CHECK (
                        status IN ('queued', 'running', 'succeeded', 'failed', 'interrupted')
                    ),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    recovery_action TEXT,
                    warning_code TEXT,
                    warning_message TEXT,
                    result_ciphertext BLOB,
                    credentials_claimed_at TEXT,
                    progress_stage TEXT NOT NULL DEFAULT 'queued',
                    progress_step TEXT NOT NULL DEFAULT 'waiting',
                    progress_completed INTEGER,
                    progress_total INTEGER,
                    progress_unit TEXT,
                    progress_item INTEGER,
                    progress_items INTEGER,
                    progress_sequence INTEGER NOT NULL DEFAULT 0
                );
                CREATE UNIQUE INDEX IF NOT EXISTS hub_operations_one_active_app
                    ON hub_operations(app_id)
                    WHERE status IN ('queued', 'running');
                CREATE INDEX IF NOT EXISTS hub_operations_recent
                    ON hub_operations(created_at DESC);
                """
            )
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(hub_operations)").fetchall()
            }
            migrations = {
                "credentials_claimed_at": "TEXT",
                "progress_stage": "TEXT NOT NULL DEFAULT 'queued'",
                "progress_step": "TEXT NOT NULL DEFAULT 'waiting'",
                "progress_completed": "INTEGER",
                "progress_total": "INTEGER",
                "progress_unit": "TEXT",
                "progress_item": "INTEGER",
                "progress_items": "INTEGER",
                "progress_sequence": "INTEGER NOT NULL DEFAULT 0",
            }
            for column, definition in migrations.items():
                if column not in columns:
                    connection.execute(
                        f"ALTER TABLE hub_operations ADD COLUMN {column} {definition}"
                    )
            table_sql_row = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'hub_operations'"
            ).fetchone()
            table_sql = str(table_sql_row[0] if table_sql_row else "")
            if "'restart'" not in table_sql:
                connection.execute("DROP INDEX IF EXISTS hub_operations_one_active_app")
                connection.execute("DROP INDEX IF EXISTS hub_operations_recent")
                connection.execute("ALTER TABLE hub_operations RENAME TO hub_operations_legacy")
                connection.executescript(
                    """
                    CREATE TABLE hub_operations (
                        operation_id TEXT PRIMARY KEY,
                        action TEXT NOT NULL CHECK (
                            action IN (
                                'install', 'update', 'uninstall', 'start', 'stop', 'restart'
                            )
                        ),
                        app_id TEXT NOT NULL,
                        plan_id TEXT NOT NULL,
                        catalog_digest TEXT NOT NULL,
                        actor TEXT NOT NULL,
                        intent_id TEXT,
                        status TEXT NOT NULL CHECK (
                            status IN ('queued', 'running', 'succeeded', 'failed', 'interrupted')
                        ),
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        started_at TEXT,
                        finished_at TEXT,
                        error_code TEXT,
                        error_message TEXT,
                        recovery_action TEXT,
                        warning_code TEXT,
                        warning_message TEXT,
                        result_ciphertext BLOB,
                        credentials_claimed_at TEXT,
                        progress_stage TEXT NOT NULL DEFAULT 'queued',
                        progress_step TEXT NOT NULL DEFAULT 'waiting',
                        progress_completed INTEGER,
                        progress_total INTEGER,
                        progress_unit TEXT,
                        progress_item INTEGER,
                        progress_items INTEGER,
                        progress_sequence INTEGER NOT NULL DEFAULT 0
                    );
                    INSERT INTO hub_operations SELECT * FROM hub_operations_legacy;
                    DROP TABLE hub_operations_legacy;
                    CREATE UNIQUE INDEX hub_operations_one_active_app
                        ON hub_operations(app_id)
                        WHERE status IN ('queued', 'running');
                    CREATE INDEX hub_operations_recent
                        ON hub_operations(created_at DESC);
                    """
                )
        os.chmod(self.path, 0o600)

    def interrupt_active(self) -> int:
        now = _now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE hub_operations
                SET status = 'interrupted', updated_at = ?, finished_at = ?,
                    error_code = 'RUNTIME_RESTARTED',
                    error_message = 'Echo restarted before the operation reported a final state',
                    recovery_action = 'Refresh the app state and create a new plan before retrying',
                    progress_stage = 'interrupted', progress_step = 'runtime-restarted',
                    progress_completed = NULL, progress_total = NULL, progress_unit = NULL,
                    progress_item = NULL, progress_items = NULL,
                    progress_sequence = progress_sequence + 1
                WHERE status IN ('queued', 'running')
                """,
                (now, now),
            )
            return cursor.rowcount

    def create(
        self,
        *,
        action: HubOperationAction,
        app_id: str,
        plan_id: str,
        catalog_digest: str,
        actor: str,
        intent_id: str | None,
    ) -> dict[str, Any]:
        operation_id = os.urandom(16).hex()
        now = _now()
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO hub_operations (
                        operation_id, action, app_id, plan_id, catalog_digest,
                        actor, intent_id, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)
                    """,
                    (
                        operation_id,
                        action,
                        app_id,
                        plan_id,
                        catalog_digest,
                        actor[:128],
                        intent_id[:128] if intent_id else None,
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise HubOperationConflict("This app already has an active Hub operation") from exc
        return self.get(operation_id)

    def claim(self, operation_id: str) -> _StoredOperation | None:
        now = _now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE hub_operations
                SET status = 'running', started_at = ?, updated_at = ?,
                    progress_stage = 'validating', progress_step = 'checking-plan',
                    progress_sequence = progress_sequence + 1
                WHERE operation_id = ? AND status = 'queued'
                """,
                (now, now, operation_id),
            )
            if cursor.rowcount != 1:
                return None
            row = connection.execute(
                """
                SELECT operation_id, action, app_id, plan_id, catalog_digest,
                       actor, intent_id, status
                FROM hub_operations WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
        if row is None:
            return None
        return _StoredOperation(**dict(row))

    def update_progress(self, operation_id: str, progress: dict[str, Any]) -> None:
        normalized = validate_hub_progress(progress)
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE hub_operations
                SET updated_at = ?, progress_stage = ?, progress_step = ?,
                    progress_completed = ?, progress_total = ?, progress_unit = ?,
                    progress_item = ?, progress_items = ?,
                    progress_sequence = progress_sequence + 1
                WHERE operation_id = ? AND status = 'running'
                """,
                (
                    now,
                    normalized["stage"],
                    normalized["step"],
                    normalized["completed"],
                    normalized["total"],
                    normalized["unit"],
                    normalized["item"],
                    normalized["items"],
                    operation_id,
                ),
            )

    def succeed(
        self,
        operation_id: str,
        result: dict[str, Any],
        *,
        warning_code: str | None = None,
        warning_message: str | None = None,
    ) -> None:
        ciphertext = self._encrypt_result(operation_id, result)
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE hub_operations
                SET status = 'succeeded', updated_at = ?, finished_at = ?,
                    warning_code = ?, warning_message = ?, result_ciphertext = ?,
                    progress_stage = 'completed', progress_step = 'finished',
                    progress_completed = NULL, progress_total = NULL, progress_unit = NULL,
                    progress_item = NULL, progress_items = NULL,
                    progress_sequence = progress_sequence + 1
                WHERE operation_id = ? AND status = 'running'
                """,
                (now, now, warning_code, warning_message, ciphertext, operation_id),
            )

    def fail(
        self,
        operation_id: str,
        *,
        error_code: str,
        error_message: str,
        recovery_action: str,
    ) -> None:
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE hub_operations
                SET status = 'failed', updated_at = ?, finished_at = ?,
                    error_code = ?, error_message = ?, recovery_action = ?,
                    progress_stage = 'failed', progress_step = 'operation-failed',
                    progress_completed = NULL, progress_total = NULL, progress_unit = NULL,
                    progress_item = NULL, progress_items = NULL,
                    progress_sequence = progress_sequence + 1
                WHERE operation_id = ? AND status IN ('queued', 'running')
                """,
                (
                    now,
                    now,
                    error_code[:64],
                    error_message[:512],
                    recovery_action[:512],
                    operation_id,
                ),
            )

    def get(self, operation_id: str) -> dict[str, Any]:
        if _OPERATION_ID.fullmatch(operation_id) is None:
            raise KeyError(operation_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM hub_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        if row is None:
            raise KeyError(operation_id)
        return self._public(dict(row))

    def claim_credentials(self, operation_id: str) -> dict[str, Any]:
        if _OPERATION_ID.fullmatch(operation_id) is None:
            raise KeyError(operation_id)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM hub_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if row is None:
                raise KeyError(operation_id)
            values = dict(row)
            if values["status"] != "succeeded" or values["credentials_claimed_at"]:
                raise HubOperationCredentialsUnavailable("One-time Hub credentials are unavailable")
            result = self._decrypt_result(values)
            credentials = result.get("revealedSecrets") if result else None
            if not isinstance(credentials, dict) or not credentials:
                raise HubOperationCredentialsUnavailable("One-time Hub credentials are unavailable")
            if any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in credentials.items()
            ):
                raise HubOperationCredentialsUnavailable("One-time Hub credentials are unavailable")
            redacted = dict(result)
            redacted.pop("revealedSecrets", None)
            now = _now()
            connection.execute(
                """
                UPDATE hub_operations
                SET result_ciphertext = ?, credentials_claimed_at = ?, updated_at = ?
                WHERE operation_id = ? AND credentials_claimed_at IS NULL
                """,
                (self._encrypt_result(operation_id, redacted), now, now, operation_id),
            )
        return {
            "schema": "echo.hub.operation-credentials.v1",
            "operationId": operation_id,
            "credentials": credentials,
        }

    def list(self, *, app_id: str | None = None, limit: int = 20) -> dict[str, Any]:
        bounded_limit = max(1, min(limit, 50))
        with self._connect() as connection:
            if app_id is None:
                rows = connection.execute(
                    "SELECT * FROM hub_operations ORDER BY created_at DESC LIMIT ?",
                    (bounded_limit,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM hub_operations WHERE app_id = ?
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (app_id, bounded_limit),
                ).fetchall()
        operations = [self._public(dict(row)) for row in rows]
        return {
            "schema": HUB_OPERATIONS_SCHEMA,
            "operations": operations,
            "total": len(operations),
        }

    def _public(self, row: dict[str, Any]) -> dict[str, Any]:
        result = self._decrypt_result(row)
        credentials_available = False
        if result is not None:
            secrets = result.pop("revealedSecrets", None)
            credentials_available = (
                isinstance(secrets, dict) and bool(secrets) and not row["credentials_claimed_at"]
            )
        return {
            "schema": HUB_OPERATION_SCHEMA,
            "operationId": row["operation_id"],
            "operation": row["action"],
            "appId": row["app_id"],
            "planId": row["plan_id"],
            "catalogDigest": row["catalog_digest"],
            "status": row["status"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "startedAt": row["started_at"],
            "finishedAt": row["finished_at"],
            "error": (
                {
                    "code": row["error_code"],
                    "message": row["error_message"],
                    "recoveryAction": row["recovery_action"],
                }
                if row["error_code"]
                else None
            ),
            "warning": (
                {"code": row["warning_code"], "message": row["warning_message"]}
                if row["warning_code"]
                else None
            ),
            "progress": {
                **hub_progress(
                    row["progress_stage"],
                    row["progress_step"],
                    completed=row["progress_completed"],
                    total=row["progress_total"],
                    unit=row["progress_unit"],
                    item=row["progress_item"],
                    items=row["progress_items"],
                ),
                "sequence": row["progress_sequence"],
            },
            "credentialsAvailable": credentials_available,
            "result": result,
        }

    def _encrypt_result(self, operation_id: str, result: dict[str, Any]) -> bytes:
        encoded = json.dumps(result, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if len(encoded) > _MAX_RESULT_BYTES:
            raise ValueError("Hub operation result is too large")
        nonce = os.urandom(12)
        return nonce + AESGCM(self._key).encrypt(
            nonce,
            encoded,
            f"{HUB_OPERATION_SCHEMA}:{operation_id}".encode("ascii"),
        )

    def _decrypt_result(self, row: dict[str, Any]) -> dict[str, Any] | None:
        encrypted = row["result_ciphertext"]
        if not encrypted:
            return None
        try:
            nonce, ciphertext = bytes(encrypted[:12]), bytes(encrypted[12:])
            decoded = AESGCM(self._key).decrypt(
                nonce,
                ciphertext,
                f"{HUB_OPERATION_SCHEMA}:{row['operation_id']}".encode("ascii"),
            )
            candidate = json.loads(decoded)
            return candidate if isinstance(candidate, dict) else None
        except (ValueError, TypeError, json.JSONDecodeError):
            return None


class HubOperationService:
    """Runs approved lifecycle operations on a small daemon worker pool."""

    def __init__(
        self,
        store: HubOperationStore,
        *,
        executor: HubOperationExecutor,
        audit: ApplianceAudit | None,
        workers: int = 2,
        queue_size: int = 32,
    ) -> None:
        self.store = store
        self.executor = executor
        self.audit = audit
        self._queue: queue.Queue[str | None] = queue.Queue(maxsize=queue_size)
        self._threads = [
            threading.Thread(
                target=self._worker,
                name=f"echo-hub-operation-{index + 1}",
                daemon=True,
            )
            for index in range(max(1, min(workers, 4)))
        ]
        for thread in self._threads:
            thread.start()

    def submit(
        self,
        *,
        action: HubOperationAction,
        app_id: str,
        plan_id: str,
        catalog_digest: str,
        actor: str,
        intent_id: str | None,
    ) -> dict[str, Any]:
        operation = self.store.create(
            action=action,
            app_id=app_id,
            plan_id=plan_id,
            catalog_digest=catalog_digest,
            actor=actor,
            intent_id=intent_id,
        )
        try:
            self._queue.put_nowait(operation["operationId"])
        except queue.Full as exc:
            self.store.fail(
                operation["operationId"],
                error_code="OPERATION_QUEUE_FULL",
                error_message="The Hub operation queue is full",
                recovery_action="Wait for an active operation to finish, then create a new plan",
            )
            raise HubOperationUnavailable("Hub operation queue is full") from exc
        return operation

    def get(self, operation_id: str) -> dict[str, Any]:
        return self.store.get(operation_id)

    def claim_credentials(self, operation_id: str) -> dict[str, Any]:
        return self.store.claim_credentials(operation_id)

    def list(self, *, app_id: str | None = None, limit: int = 20) -> dict[str, Any]:
        return self.store.list(app_id=app_id, limit=limit)

    def shutdown(self) -> None:
        for _thread in self._threads:
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                break

    def _worker(self) -> None:
        while True:
            operation_id = self._queue.get()
            try:
                if operation_id is None:
                    return
                operation = self.store.claim(operation_id)
                if operation is not None:
                    self._execute(operation)
            finally:
                self._queue.task_done()

    def _execute(self, operation: _StoredOperation) -> None:
        method = getattr(self.executor, f"{operation.action}_hub_app")
        streaming_method = getattr(
            self.executor,
            f"{operation.action}_hub_app_with_progress",
            None,
        )
        try:
            if callable(streaming_method):
                result = streaming_method(
                    operation.app_id,
                    plan_id=operation.plan_id,
                    catalog_digest=operation.catalog_digest,
                    progress=lambda event: self.store.update_progress(
                        operation.operation_id, event
                    ),
                )
            else:
                result = method(
                    operation.app_id,
                    plan_id=operation.plan_id,
                    catalog_digest=operation.catalog_digest,
                )
        except DockerControlDenied:
            self._record_failure(
                operation,
                code="CONTROL_DENIED",
                message="The privileged installer denied this operation",
                recovery="Refresh the app state and review a new plan",
                audit_reason="installer denied",
            )
            return
        except DockerConflict:
            self._record_failure(
                operation,
                code="STATE_CHANGED",
                message="The device state changed after this plan was approved",
                recovery="Refresh the app state and review a new plan",
                audit_reason="installer plan conflict",
            )
            return
        except DockerUnavailable:
            self._record_failure(
                operation,
                code="RUNTIME_UNAVAILABLE",
                message="The app runtime became unavailable",
                recovery="Restore the app service, then create a new plan",
                audit_reason="installer unavailable",
            )
            return
        except Exception:
            _log.exception("Hub %s operation failed for %s", operation.action, operation.app_id)
            self._record_failure(
                operation,
                code="OPERATION_FAILED",
                message="The Hub operation did not complete",
                recovery="Refresh the app state before deciding whether to retry",
                audit_reason="unexpected installer failure",
            )
            return

        warning_code = None
        warning_message = None
        try:
            self._audit(operation, "succeeded", self._success_metadata(operation, result))
        except (OSError, AuditIntegrityError):
            _log.exception("Hub operation succeeded but its final audit record failed")
            warning_code = "AUDIT_UNAVAILABLE"
            warning_message = "The operation succeeded but its final audit record is unavailable"
        self.store.succeed(
            operation.operation_id,
            result,
            warning_code=warning_code,
            warning_message=warning_message,
        )

    def _record_failure(
        self,
        operation: _StoredOperation,
        *,
        code: str,
        message: str,
        recovery: str,
        audit_reason: str,
    ) -> None:
        try:
            self._audit(
                operation,
                "failed",
                {"appId": operation.app_id, "reason": audit_reason},
            )
        except (OSError, AuditIntegrityError):
            _log.exception("Hub operation failure audit record is unavailable")
        self.store.fail(
            operation.operation_id,
            error_code=code,
            error_message=message,
            recovery_action=recovery,
        )

    def _audit(
        self,
        operation: _StoredOperation,
        outcome: str,
        metadata: dict[str, Any],
    ) -> None:
        if self.audit is None:
            return
        payload = dict(metadata)
        if operation.intent_id:
            payload["intentId"] = operation.intent_id
        self.audit.record(
            actor=operation.actor,
            action=f"hub.app.{operation.action}",
            target=operation.plan_id,
            outcome=outcome,
            metadata=payload,
        )

    @staticmethod
    def _success_metadata(operation: _StoredOperation, result: dict[str, Any]) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "appId": operation.app_id,
            "containerId": result.get("containerId"),
            "catalogDigest": operation.catalog_digest,
        }
        if operation.action == "update":
            metadata["previousContainerId"] = result.get("previousContainerId")
            metadata["dataVolumesRetained"] = True
        elif operation.action == "uninstall":
            metadata["dataVolumesRetained"] = True
        elif operation.action in {"start", "stop", "restart"}:
            metadata["serviceCount"] = result.get("serviceCount")
            metadata["dataVolumesRetained"] = True
        return metadata


__all__ = [
    "HUB_OPERATION_SCHEMA",
    "HUB_OPERATIONS_FILENAME",
    "HUB_OPERATIONS_SCHEMA",
    "HubOperationConflict",
    "HubOperationCredentialsUnavailable",
    "HubOperationService",
    "HubOperationStore",
    "HubOperationUnavailable",
]
