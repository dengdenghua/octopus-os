"""Approved document organization with durable, independently checked file facts.

The provider executes a bounded request synchronously. TaskSupervisor owns its
task lifecycle; this module stores exact plans and file receipts, not a queue.
"""

from __future__ import annotations

import copy
import hashlib
import os
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from appliance.agent_api.documents import (
    DocumentExtractionBudget,
    DocumentWorkerCleanupError,
    invoice_extraction_context,
)
from appliance.agent_api.file_tasks import FileOperationTask
from appliance.audit import AuditIntegrityError
from appliance.data_access import DataAccessDenied, DataAccessScope, DataAccessUnavailable
from appliance.files.manager import FileManager, InsufficientStorage, ShareQuotaExceeded
from appliance.files.organization_directories import ensure_parent_directories, pinned_directory
from appliance.files.organization_io import (
    OrganizationIOError,
    _parts,
    inspect_move,
    move_file,
    read_file_snapshot,
    snapshot_file,
)
from appliance.files.organization_plan import (
    _target_conflict,
    build_organization_record,
    seal_record,
    verify_record,
)
from appliance.files.organization_preview import OrganizationPreviewAdmission
from appliance.files.organization_store import OrganizationStore
from appliance.state_lock import StateLockError

_MAX_FILE_BYTES = 16 * 1024 * 1024


class OrganizationError(Exception):
    def __init__(self, status: int, error: str, message: str):
        super().__init__(message)
        self.status, self.error, self.message = status, error, message


@dataclass(frozen=True)
class OrganizationOriginal:
    filename: str
    data: bytes
    sha256: str


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).isoformat().replace("+00:00", "Z")


def _summarize(result: dict[str, Any], *, stopped: bool = False) -> None:
    rows = result["results"]
    result["counts"] = {
        key: sum(row["status"] == status for row in rows)
        for key, status in (
            ("moved", "moved"),
            ("conflicts", "conflict"),
            ("failed", "failed"),
            ("pending", "pending"),
            ("uncertain", "uncertain"),
            ("skipped", "skipped"),
        )
    }
    counts = result["counts"]
    result["finalizationPending"] = any(
        result.get(flag) is False
        for flag in (
            "receiptRecorded",
            "auditRecorded",
            "taskRecorded",
            "undoLinkRecorded",
        )
    )
    if counts["uncertain"] or result.get("receiptRecorded") is False:
        result.update(state="uncertain", executionComplete=False)
    elif stopped and counts["pending"]:
        result.update(state="cancelled", executionComplete=False)
    elif counts["conflicts"] or counts["failed"] or counts["pending"]:
        result.update(state="partial" if counts["moved"] else "failed", executionComplete=False)
    elif result["finalizationPending"]:
        result.update(state="partial", executionComplete=False)
    else:
        result.update(state="completed", executionComplete=True)


class FileOrganizationService:
    def __init__(
        self,
        manager: FileManager,
        state_dir: Path,
        *,
        data_access: Any = None,
        supervisor: Any = None,
        audit: Any = None,
        auth_required: bool = False,
        extraction_budget: DocumentExtractionBudget | None = None,
    ):
        self.manager = manager
        self.store = OrganizationStore(Path(state_dir) / "file-organizations")
        self.data_access, self.supervisor = data_access, supervisor
        self.audit, self.auth_required = audit, auth_required
        self.extraction_budget = extraction_budget or DocumentExtractionBudget.from_environment()
        self._preview = OrganizationPreviewAdmission(Path(state_dir))

    def shutdown(self) -> None:
        self._preview.shutdown()

    def _scope(self, actor: str) -> DataAccessScope:
        if not isinstance(actor, str) or not actor or len(actor) > 256:
            raise OrganizationError(403, "access_denied", "无法确认当前文件访问身份。")
        try:
            scope = (
                self.data_access.scope_for_actor(actor)
                if self.data_access is not None
                else DataAccessScope.unrestricted(actor)
            )
            if scope.actor != actor:
                raise DataAccessDenied("actor mismatch")
            return scope
        except DataAccessDenied as exc:
            raise OrganizationError(403, "access_denied", "当前账户无权访问此目录。") from exc
        except (DataAccessUnavailable, OSError) as exc:
            raise OrganizationError(
                503, "access_unavailable", "暂时无法核实当前目录权限。"
            ) from exc

    def _authorize(self, actor: str, record: dict[str, Any], *, entry=None) -> None:
        try:
            scope = self._scope(actor)
            scope.require_read_tree(record["plan"]["path"])
            if entry is not None:
                scope.require_write(entry["source"])
                scope.require_write(entry["target"])
        except DataAccessDenied as exc:
            raise OrganizationError(
                403, "access_denied", "目录或文件权限已变化，请重新检查。"
            ) from exc

    def _load(self, actor: str, plan_id: str) -> dict[str, Any]:
        try:
            record = self.store.load(plan_id)
        except FileNotFoundError as exc:
            raise OrganizationError(404, "plan_not_found", "找不到此整理计划。") from exc
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise OrganizationError(
                409, "receipt_unavailable", "无法安全读取整理计划记录。"
            ) from exc
        if record.get("owner") != actor:
            raise OrganizationError(403, "access_denied", "此整理计划不属于当前账户。")
        if not verify_record(record):
            raise OrganizationError(409, "plan_changed", "整理计划完整性检查失败。")
        self._authorize(actor, record)
        return record

    def create_plan(self, actor: str, path: str) -> dict[str, Any]:
        try:
            scope = self._scope(actor)
            scope.require_read_tree(path)
            with self._preview.acquire() as cancelled:
                deadline = time.monotonic() + self.extraction_budget.scan_seconds
                with invoice_extraction_context(
                    budget=self.extraction_budget, deadline=deadline, cancel=cancelled
                ):
                    record = build_organization_record(
                        manager=self.manager,
                        actor=actor,
                        scope=scope,
                        path=path,
                        deadline=deadline,
                        cancelled=cancelled,
                    )
            self._authorize(actor, record)
            with self.store.lease():
                self.store.save(record)
            return copy.deepcopy(record["plan"])
        except OrganizationError:
            raise
        except DataAccessDenied as exc:
            raise OrganizationError(403, "access_denied", "当前账户无权扫描此目录。") from exc
        except StateLockError as exc:
            raise OrganizationError(
                409, "operation_busy", "另一个整理操作正在处理，请稍后重试。"
            ) from exc
        except DocumentWorkerCleanupError as exc:
            raise OrganizationError(
                503, "extraction_unavailable", "文档解析资源无法安全释放，请检查服务状态后重试。"
            ) from exc
        except (OSError, ValueError) as exc:
            raise OrganizationError(
                400, "scan_unavailable", "无法安全扫描或保存此目录的计划。"
            ) from exc

    def get_plan(self, actor: str, plan_id: str) -> dict[str, Any]:
        record = self._load(actor, plan_id)
        plan = copy.deepcopy(record["plan"])
        if "result" in record:
            plan["result"] = self._readback(actor, self._reconcile(actor, record))
        return plan

    def list_plans(self, actor: str, path: str) -> dict[str, Any]:
        try:
            if path:
                parts = _parts(path)
                if any(part.casefold().startswith(".echo-") for part in parts):
                    raise ValueError("reserved directory")
            self._scope(actor).require_read_tree(path)
        except (ValueError, OSError) as exc:
            raise OrganizationError(
                403, "access_denied", "当前账户无权查看此目录的整理计划。"
            ) from exc
        plans, complete = [], True
        with self.store._directory(), os.scandir(self.store.directory) as entries:
            for index, entry in enumerate(entries):
                if index >= 500:
                    complete = False
                    break
                if re.fullmatch(r"[0-9a-f]{64}\.json", entry.name) is None:
                    continue
                try:
                    record = self._load(actor, entry.name[:-5])
                except OrganizationError:
                    continue
                plan = record["plan"]
                if plan["path"] != path:
                    continue
                public = {
                    key: copy.deepcopy(plan[key])
                    for key in (
                        "planId",
                        "path",
                        "direction",
                        "createdAt",
                        "expiresAt",
                        "ready",
                        "summary",
                    )
                }
                if "sourcePlanId" in plan:
                    public["sourcePlanId"] = plan["sourcePlanId"]
                public["state"] = record.get("result", {}).get("state")
                plans.append(public)
        try:
            self._scope(actor).require_read_tree(path)
        except DataAccessDenied as exc:
            raise OrganizationError(403, "access_denied", "目录权限已变化。") from exc
        plans.sort(key=lambda plan: plan["createdAt"], reverse=True)
        return {
            "schema": "echo.files.organize.plans.v1",
            "path": path,
            "plans": plans[:20],
            "complete": complete,
        }

    @staticmethod
    def _pending(plan_id: str, row: dict[str, Any]) -> str:
        return str(
            PurePosixPath(row["source"]).parent / f".echo-organize-{plan_id}-{row['entryId'][:16]}"
        )

    def _reconcile(self, actor: str, record: dict[str, Any]) -> dict[str, Any]:
        """Recover receipts only while no writer owns the provider lease.

        Inspecting never moves a file. A sole matching source or captured
        pending file becomes retryable; retry still requires a new approval.
        """
        if "result" not in record:
            return record
        try:
            with self.store.lease():
                current = self._load(actor, record["plan"]["planId"])
                result = current["result"]
                if result["state"] == "completed" and result.get("receiptRecorded") is not False:
                    return current
                changed = False
                with pinned_directory(
                    self.manager.root, current["plan"]["path"], expected=current["rootIdentity"]
                ):
                    for row in result["results"]:
                        if not row.get("attempted") or row["committed"] is True:
                            continue
                        observed = inspect_move(
                            self.manager.root,
                            row["source"],
                            row["target"],
                            expected=current["snapshots"][row["entryId"]],
                            pending=self._pending(current["plan"]["planId"], row),
                        )
                        if observed["status"] == "pending":
                            observed["status"] = (
                                "pending" if result["state"] == "cancelled" else "failed"
                            )
                        row.update(observed)
                        changed = True
                if changed or result["state"] == "running":
                    previous = result["state"]
                    result["receiptRecorded"] = True
                    _summarize(result, stopped=previous == "cancelled")
                    self._authorize(actor, current)
                    self.store.save(current)
                    if current["plan"]["direction"] == "undo":
                        self._record_undo(current)
                    return current
                return current
        except StateLockError:
            return record
        except (OSError, ValueError):
            return record

    def _matches(self, path: str, expected: dict[str, Any]) -> bool:
        try:
            return snapshot_file(self.manager.root, path, max_bytes=_MAX_FILE_BYTES) == expected
        except (OSError, ValueError):
            return False

    def _readback(self, actor: str, record: dict[str, Any]) -> dict[str, Any]:
        result = copy.deepcopy(record["result"])
        running = result["state"] == "running"
        if running:
            try:
                with self.store.lease():
                    running = False
            except StateLockError:
                pass
        try:
            with pinned_directory(
                self.manager.root,
                record["plan"]["path"],
                expected=record["rootIdentity"],
            ):
                for row in result["results"]:
                    row["actualPath"] = None
                    expected = record["snapshots"].get(row["entryId"])
                    if expected is None:
                        continue
                    if row["status"] == "moved" or row["committed"] is True:
                        target = (
                            row["source"]
                            if row["entryId"] in record.get("undoneBy", {})
                            else row["target"]
                        )
                        if target and self._matches(target, expected):
                            row["actualPath"] = target
                        else:
                            row.update(status="uncertain", reason="committed_file_changed")
                    elif self._matches(row["source"], expected):
                        row["actualPath"] = row["source"]
                    elif (
                        row.get("attempted")
                        and row["target"]
                        and self._matches(row["target"], expected)
                    ):
                        # Readback proves a matching target, but cannot prove a sole
                        # source/pending absence. Conditional IO reconciles on retry.
                        row["actualPath"] = row["target"]
                    if not running and row["status"] == "pending" and row.get("attempted"):
                        row.update(
                            status="uncertain", committed=None, reason="operation_interrupted"
                        )
        except (OSError, ValueError):
            for row in result["results"]:
                row["actualPath"] = None
                if row["committed"] is True or row.get("attempted"):
                    row.update(status="uncertain", reason="root_unavailable")
        self._authorize(actor, record)
        if running:
            # A currently held provider lease may still be publishing a file.
            # Return the persisted facts without asserting a terminal state.
            result.update(state="running", executionComplete=False)
        elif result["state"] == "running":
            _summarize(result)
            if result["state"] not in {"completed", "partial", "failed"}:
                result.update(state="uncertain", executionComplete=False)
        else:
            original_state = result["state"]
            _summarize(result, stopped=original_state == "cancelled")
        return result

    def get_result(self, actor: str, plan_id: str) -> dict[str, Any]:
        record = self._load(actor, plan_id)
        if "result" not in record:
            raise OrganizationError(404, "result_not_found", "此计划尚未开始执行。")
        return self._readback(actor, self._reconcile(actor, record))

    def read_original(self, actor: str, plan_id: str, entry_id: str) -> OrganizationOriginal:
        """Return verified bytes, never a path to be reopened by the HTTP server."""
        record = self._load(actor, plan_id)

        def unavailable() -> OrganizationError:
            return OrganizationError(
                409, "original_unavailable", "原件已变化或无法核实，请刷新整理结果后重新检查。"
            )

        if (
            not isinstance(entry_id, str)
            or re.fullmatch(r"(?:[0-9a-f]{24}|[0-9a-f]{64})", entry_id) is None
            or "result" not in record
        ):
            raise unavailable()
        planned = next(
            (entry for entry in record["plan"]["entries"] if entry["entryId"] == entry_id), None
        )
        expected = record["snapshots"].get(entry_id)
        if (
            planned is None
            or not isinstance(expected, dict)
            or type(expected.get("size")) is not int
            or not 0 <= expected["size"] <= _MAX_FILE_BYTES
        ):
            raise unavailable()
        try:
            with pinned_directory(
                self.manager.root, record["plan"]["path"], expected=record["rootIdentity"]
            ):
                # Inspect only the requested entry. Its permitted names come from
                # the immutable plan, not request parameters or an old UI result.
                observed_record = copy.deepcopy(record)
                observed_record["result"]["results"] = [
                    row for row in record["result"]["results"] if row["entryId"] == entry_id
                ]
                observed = self._readback(actor, observed_record)
                if len(observed["results"]) != 1:
                    raise unavailable()
                actual = observed["results"][0].get("actualPath")
                if not actual or actual not in {planned["source"], planned["target"]}:
                    raise unavailable()
                self._scope(actor).require_read(actual)
                data, snapshot = read_file_snapshot(
                    self.manager.root, actual, max_bytes=expected["size"]
                )
                if snapshot != expected:
                    raise unavailable()
                # POSIX directory pins keep descriptors alive, not every public
                # name fixed. Reject a replacement of the selected directory.
                with pinned_directory(
                    self.manager.root, record["plan"]["path"], expected=record["rootIdentity"]
                ):
                    self._authorize(actor, record)
                    self._scope(actor).require_read(actual)
                return OrganizationOriginal(PurePosixPath(actual).name, data, snapshot["sha256"])
        except DataAccessDenied as exc:
            raise OrganizationError(403, "access_denied", "当前账户无权读取此原件。") from exc
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise unavailable() from exc

    def _audit(self, actor: str, record: dict[str, Any], outcome: str) -> None:
        if self.audit is None:
            if self.auth_required:
                raise OrganizationError(503, "audit_unavailable", "操作审计暂不可用。")
            return
        try:
            self.audit.record(
                actor=actor,
                action=f"files.organize.{record['plan']['direction']}",
                target=record["plan"]["planId"],
                outcome=outcome,
                metadata={"intentId": record["taskId"], "provider": "echo-os.files"},
            )
        except (OSError, ValueError, AuditIntegrityError) as exc:
            raise OrganizationError(503, "audit_unavailable", "无法保存可信操作审计。") from exc

    @staticmethod
    def _initial_result(record: dict[str, Any]) -> dict[str, Any]:
        plan = record["plan"]
        result = {
            "schema": "echo.files.organize.result.v1",
            "planId": plan["planId"],
            "operationId": plan["planId"],
            "taskId": record["taskId"],
            "direction": plan["direction"],
            "state": "running",
            "executionComplete": False,
            "auditRecorded": False,
            "taskRecorded": False,
            "reviewCount": sum(
                item["status"] not in {"ready", "already_organized"} for item in plan["entries"]
            ),
            "results": [],
        }
        for entry in plan["entries"]:
            row = {
                "entryId": entry["entryId"],
                "source": entry["source"],
                "target": entry["target"],
                "status": "pending" if entry["status"] == "ready" else "skipped",
                "committed": False,
                "reason": entry["reason"],
                "recoveryPaths": [],
                "actualPath": None,
            }
            if entry["entryId"] in record["snapshots"]:
                row["sha256"] = record["snapshots"][entry["entryId"]]["sha256"]
            result["results"].append(row)
        _summarize(result)
        result.update(state="running", executionComplete=False)
        return result

    def _move(
        self, actor: str, record: dict[str, Any], entry: dict[str, Any], pending: str
    ) -> dict[str, Any]:
        self._authorize(actor, record, entry=entry)
        selected = record["plan"]["path"]

        def authorize_directory(relative: str) -> None:
            scope = self._scope(actor)
            if not selected or relative == selected or relative.startswith(selected + "/"):
                scope.require_write(relative)
            else:
                scope.require_list(relative)

        with (
            self.manager._upload_lock,
            pinned_directory(
                self.manager.root,
                selected,
                expected=record["rootIdentity"],
            ),
        ):
            source = self.manager.root.joinpath(*PurePosixPath(entry["source"]).parts)
            target = self.manager.root.joinpath(*PurePosixPath(entry["target"]).parts)
            self.manager._assert_no_active_uploads(source)
            self.manager._assert_no_active_uploads(target)
            expected = record["snapshots"][entry["entryId"]]
            self.manager._share_quota_reports(target, expected["size"], source=source)
            created = ensure_parent_directories(
                self.manager.root,
                entry["target"],
                authorize=authorize_directory,
            )
            self._authorize(actor, record, entry=entry)
            result = move_file(
                self.manager.root,
                entry["source"],
                entry["target"],
                expected=expected,
                pending=pending,
            )
            result["createdDirectories"] = created
            return result

    def apply(self, actor: str, plan_id: str) -> dict[str, Any]:
        try:
            with self.store.lease():
                return self._apply_locked(actor, plan_id)
        except StateLockError as exc:
            raise OrganizationError(
                409, "operation_busy", "另一个整理操作正在处理，请稍后核实结果。"
            ) from exc

    def _apply_locked(self, actor: str, plan_id: str) -> dict[str, Any]:
        record = self._load(actor, plan_id)
        plan = record["plan"]
        if not plan["ready"] or not plan["scanComplete"]:
            raise OrganizationError(409, "plan_not_ready", "此计划没有可安全执行的完整扫描结果。")
        if "result" not in record and time.time() > record["expiresAtEpoch"]:
            raise OrganizationError(409, "plan_expired", "此预览已过期，请重新生成计划。")
        result = record.setdefault("result", self._initial_result(record))
        candidates = {
            item["entryId"]: item for item in plan["entries"] if item["status"] == "ready"
        }
        retryable = [
            row
            for row in result["results"]
            if row["entryId"] in candidates and row["committed"] is not True
        ]
        if not retryable and not result.get("finalizationPending"):
            return self._readback(actor, record)
        if self.supervisor is None:
            raise OrganizationError(503, "tasks_unavailable", "任务状态服务暂不可用。")
        try:
            with pinned_directory(self.manager.root, plan["path"], expected=record["rootIdentity"]):
                pass
        except (OSError, ValueError) as exc:
            raise OrganizationError(
                409, "root_changed", "目录或挂载身份已变化，已停止操作。"
            ) from exc
        self._audit(actor, record, "attempted")
        task = FileOperationTask(self.supervisor, record)
        try:
            task.start()
        except (RuntimeError, ValueError, OSError) as exc:
            raise OrganizationError(
                409, "task_unavailable", "当前任务租约或状态不可用，请稍后核实。"
            ) from exc
        result.update(
            state="running",
            executionComplete=False,
            receiptRecorded=True,
            auditRecorded=False,
            taskRecorded=False,
        )
        record["startedAt"] = record.get("startedAt", _iso(time.time()))
        try:
            self.store.save(record)
        except (OSError, ValueError) as exc:
            result.update(state="failed", executionComplete=False, receiptRecorded=False)
            self._finish_task(task, result)
            raise OrganizationError(
                503, "receipt_unavailable", "无法保存执行前记录，未开始移动文件。"
            ) from exc
        stopped = False
        for row in retryable:
            try:
                if task.cancelled():
                    stopped = True
                    break
            except (OSError, ValueError, RuntimeError):
                result["taskRecorded"] = False
                break
            entry = candidates[row["entryId"]]
            pending = self._pending(plan_id, row)
            previously_attempted = row.get("attempted", False)
            row.update(
                status="pending",
                attempted=True,
                committed=None,
                reason=None,
                actualPath=None,
                recoveryPaths=[pending],
            )
            try:
                self.store.save(record)
            except (OSError, ValueError):
                row.update(
                    status="uncertain" if previously_attempted else "failed",
                    committed=None if previously_attempted else False,
                    reason="prepare_receipt_failed",
                )
                result["receiptRecorded"] = False
                break
            try:
                moved = self._move(actor, record, entry, pending)
            except (OrganizationError, DataAccessDenied):
                moved = {
                    "status": "uncertain" if previously_attempted else "conflict",
                    "committed": None if previously_attempted else False,
                    "reason": "access_changed",
                    "recoveryPaths": [pending] if previously_attempted else [],
                }
            except (ShareQuotaExceeded, InsufficientStorage):
                moved = {
                    "status": "uncertain" if previously_attempted else "conflict",
                    "committed": None if previously_attempted else False,
                    "reason": "quota_or_capacity_unavailable",
                    "recoveryPaths": [pending] if previously_attempted else [],
                }
            except (OSError, ValueError, RuntimeError) as exc:
                moved = {
                    "status": "uncertain",
                    "committed": None,
                    "reason": exc.reason
                    if isinstance(exc, OrganizationIOError)
                    else "file_operation_unverified",
                    "recoveryPaths": [entry["source"], pending, entry["target"]],
                }
            row.update(moved)
            if row["committed"] is True and self._matches(
                entry["target"], record["snapshots"][row["entryId"]]
            ):
                row["actualPath"] = entry["target"]
            elif row["status"] == "moved":
                row.update(status="uncertain", reason="committed_file_changed")
            _summarize(result)
            result.update(state="running", executionComplete=False)
            try:
                self.store.save(record)
            except (OSError, ValueError):
                result["receiptRecorded"] = False
                break
            try:
                task.progress(sum(item["status"] == "moved" for item in result["results"]))
            except (OSError, ValueError, RuntimeError):
                result["taskRecorded"] = False
                break
        _summarize(result, stopped=stopped)
        try:
            self.store.save(record)
        except (OSError, ValueError):
            result.update(receiptRecorded=False, state="uncertain", executionComplete=False)
        if plan["direction"] == "undo":
            self._record_undo(record)
        try:
            final_result = copy.deepcopy(result)
            final_result.update(auditRecorded=True, taskRecorded=True)
            _summarize(final_result, stopped=stopped)
            self._audit(actor, record, final_result["state"])
            if self.audit is not None:
                result["auditRecorded"] = True
            else:
                result.pop("auditRecorded", None)
        except OrganizationError:
            result.update(auditRecorded=False, state="uncertain", executionComplete=False)
            try:
                self.store.save(record)
            except (OSError, ValueError):
                result["receiptRecorded"] = False
        self._finish_task(task, result)
        _summarize(result, stopped=stopped)
        try:
            self.store.save(record)
        except (OSError, ValueError):
            result.update(receiptRecorded=False, state="uncertain", executionComplete=False)
        self._authorize(actor, record)
        return self._readback(actor, record)

    @staticmethod
    def _finish_task(task: FileOperationTask, result: dict[str, Any]) -> None:
        try:
            finalized = copy.deepcopy(result)
            finalized["taskRecorded"] = True
            _summarize(finalized, stopped=result["state"] == "cancelled")
            task.finish(finalized)
            result["taskRecorded"] = True
        except (OSError, ValueError, RuntimeError):
            result["taskRecorded"] = False

    def cancel(self, actor: str, plan_id: str) -> dict[str, Any]:
        record = self._load(actor, plan_id)
        if "result" not in record:
            raise OrganizationError(409, "not_started", "此计划尚未开始，无需取消。")
        try:
            FileOperationTask(self.supervisor, record).request_cancel(actor)
        except (OSError, ValueError, RuntimeError, KeyError) as exc:
            raise OrganizationError(
                409, "cancel_unavailable", "无法确认取消请求，请刷新执行结果。"
            ) from exc
        return self._readback(actor, record)

    def create_undo_plan(self, actor: str, plan_id: str) -> dict[str, Any]:
        try:
            with self.store.lease():
                original = self._load(actor, plan_id)
                if original["plan"]["direction"] != "apply":
                    raise OrganizationError(409, "undo_not_available", "此计划不是原始整理操作。")
                if "result" not in original:
                    raise OrganizationError(409, "not_started", "此计划尚未执行。")
                try:
                    with pinned_directory(
                        self.manager.root,
                        original["plan"]["path"],
                        expected=original["rootIdentity"],
                    ):
                        return self._create_undo_locked(actor, original)
                except (OSError, ValueError) as exc:
                    raise OrganizationError(
                        409, "root_changed", "目录或挂载身份已变化，无法生成撤销计划。"
                    ) from exc
        except StateLockError as exc:
            raise OrganizationError(
                409, "operation_busy", "文件操作仍在进行，请稍后创建撤销计划。"
            ) from exc

    def _create_undo_locked(self, actor: str, original: dict[str, Any]) -> dict[str, Any]:
        now = time.time()
        plan = copy.deepcopy(original["plan"])
        plan.update(
            direction="undo",
            sourcePlanId=plan["planId"],
            createdAt=_iso(now),
            expiresAt=_iso(now + 900),
            entries=[],
            blockers=[],
            scanComplete=True,
        )
        plan["approval"] = {"action": "files.organize.undo", "target": ""}
        snapshots, sources = {}, {}
        originals = {item["entryId"]: item for item in original["plan"]["entries"]}
        for row in original["result"]["results"]:
            if row["committed"] is not True or row["entryId"] in original.get("undoneBy", {}):
                continue
            entry = copy.deepcopy(originals[row["entryId"]])
            entry.update(source=row["target"], target=row["source"], status="ready", reason=None)
            entry_id = hashlib.sha256(
                f"undo:{original['plan']['planId']}:{row['entryId']}".encode()
            ).hexdigest()
            entry["entryId"] = entry_id
            expected = original["snapshots"][row["entryId"]]
            if not self._matches(entry["source"], expected):
                entry.update(status="conflict", reason="committed_file_changed")
            elif (
                _target_conflict(
                    self.manager.root, entry["target"], source_device=expected["identity"]["dev"]
                )
                is not None
            ):
                entry.update(status="conflict", reason="original_path_occupied")
            else:
                try:
                    self._authorize(actor, original, entry=entry)
                except OrganizationError:
                    entry.update(status="conflict", reason="access_changed")
            plan["entries"].append(entry)
            snapshots[entry_id] = expected
            sources[entry_id] = row["entryId"]
        if not plan["entries"]:
            raise OrganizationError(409, "nothing_to_undo", "没有已确认且尚未撤销的文件移动。")
        plan["summary"] = {
            "scanned": len(plan["entries"]),
            "ready": sum(item["status"] == "ready" for item in plan["entries"]),
            "needsReview": 0,
            "alreadyOrganized": 0,
            "conflicts": sum(item["status"] == "conflict" for item in plan["entries"]),
            "unsupported": 0,
        }
        plan["ready"] = bool(plan["summary"]["ready"])
        record = seal_record(
            {
                "schema": "echo.files.organization-state.v1",
                "owner": actor,
                "plan": plan,
                "workspacePath": original["workspacePath"],
                "rootIdentity": original["rootIdentity"],
                "taskId": str(uuid4()),
                "snapshots": snapshots,
                "createdAtEpoch": now,
                "expiresAtEpoch": now + 900,
                "nonce": uuid4().hex,
                "undoEntries": sources,
            }
        )
        self._authorize(actor, record)
        try:
            self.store.save(record)
        except (OSError, ValueError) as exc:
            raise OrganizationError(503, "receipt_unavailable", "无法保存撤销计划。") from exc
        return copy.deepcopy(record["plan"])

    def _record_undo(self, record: dict[str, Any]) -> None:
        try:
            original = self.store.load(record["plan"]["sourcePlanId"])
            if original["owner"] != record["owner"] or not verify_record(original):
                raise ValueError("original plan changed")
            for row in record["result"]["results"]:
                if row["committed"] is True:
                    original.setdefault("undoneBy", {})[record["undoEntries"][row["entryId"]]] = (
                        record["plan"]["planId"]
                    )
            self.store.save(original)
        except (OSError, ValueError, KeyError):
            record["result"].update(
                undoLinkRecorded=False, state="uncertain", executionComplete=False
            )


__all__ = ["FileOrganizationService", "OrganizationError"]
