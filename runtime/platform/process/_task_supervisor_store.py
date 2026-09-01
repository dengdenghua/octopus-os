from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from runtime.platform.io import atomic_write_json, read_json_with_backup
from runtime.platform.io.atomic import _cross_process_lock
from runtime.platform.process._task_supervisor_analysis import (
    build_task_recovery_queue,
    build_task_runs_overview,
)
from runtime.platform.process._task_supervisor_models import TaskRunRecord, _now_iso
from runtime.platform.process._task_supervisor_payload import _normalize_payload

# ``list`` is shadowed inside TaskSupervisorStore by its public ``list()``
# method, so bare ``list[...]`` annotations in that class read as the method
# and can't be resolved by mypy (which then can't type-check the file). These
# aliases capture the builtin here at module scope, where it isn't shadowed.
_TaskRecordList = list[TaskRunRecord]
_TaskDictList = list[dict[str, Any]]


class TaskSupervisorStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def upsert(self, record: TaskRunRecord) -> TaskRunRecord:
        def _mutate(
            existing: TaskRunRecord | None,
            next_lease_token: Callable[[], int],
        ) -> TaskRunRecord:
            del existing, next_lease_token
            return record

        return self.upsert_mutate(record.task_id, _mutate)

    def upsert_mutate(
        self,
        task_id: str,
        mutator: Callable[[TaskRunRecord | None, Callable[[], int]], TaskRunRecord],
    ) -> TaskRunRecord:
        with self._write_lock():
            payload = self._read_payload()
            tasks = self._read_tasks_from_payload(payload)
            now = _now_iso()
            existing: TaskRunRecord | None = None
            next_tasks: _TaskRecordList = []
            for task in tasks:
                if task.task_id != task_id:
                    next_tasks.append(task)
                    continue
                existing = task

            def _next_lease_token() -> int:
                token = max(0, int(payload.get("leaseCounter") or 0)) + 1
                payload["leaseCounter"] = token
                return token

            candidate = mutator(existing, _next_lease_token)
            updated = candidate.model_copy(
                update={
                    "created_at": existing.created_at
                    if existing is not None
                    else candidate.created_at,
                    "updated_at": now,
                },
                deep=True,
            )
            next_tasks.append(updated)
            payload["tasks"] = self._dump_tasks(next_tasks)
            payload["lastUpdated"] = now
            self._write_payload(payload)
            return updated

    def get(self, task_id: str) -> TaskRunRecord | None:
        with self._lock:
            for task in self._read_tasks():
                if task.task_id == task_id:
                    return task
        return None

    def list(
        self,
        *,
        status: str | None = None,
        kind: str | None = None,
        owner_id: str | None = None,
        thread_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
        include_unowned: bool = True,
    ) -> _TaskRecordList:
        return self.list_page(
            status=status,
            kind=kind,
            owner_id=owner_id,
            thread_id=thread_id,
            limit=limit,
            offset=offset,
            include_unowned=include_unowned,
        )["items"]

    def list_page(
        self,
        *,
        status: str | None = None,
        kind: str | None = None,
        owner_id: str | None = None,
        thread_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
        include_unowned: bool = True,
    ) -> dict[str, Any]:
        clean_limit = max(1, int(limit or 100))
        clean_offset = max(0, int(offset or 0))
        with self._lock:
            tasks = self._filtered_tasks(
                status=status,
                kind=kind,
                owner_id=owner_id,
                thread_id=thread_id,
                include_unowned=include_unowned,
            )
            return {
                "items": tasks[clean_offset : clean_offset + clean_limit],
                "total": len(tasks),
                "limit": clean_limit,
                "offset": clean_offset,
            }

    def count(
        self,
        *,
        status: str | None = None,
        kind: str | None = None,
        owner_id: str | None = None,
        thread_id: str | None = None,
        include_unowned: bool = True,
    ) -> int:
        with self._lock:
            return len(
                self._filtered_tasks(
                    status=status,
                    kind=kind,
                    owner_id=owner_id,
                    thread_id=thread_id,
                    include_unowned=include_unowned,
                )
            )

    def _filtered_tasks(
        self,
        *,
        status: str | None = None,
        kind: str | None = None,
        owner_id: str | None = None,
        thread_id: str | None = None,
        include_unowned: bool = True,
    ) -> _TaskRecordList:
        tasks = self._read_tasks()
        if status:
            tasks = [task for task in tasks if task.status.value == str(status)]
        if kind:
            tasks = [task for task in tasks if task.kind == str(kind)]
        if owner_id is not None:
            tasks = [
                task
                for task in tasks
                if task.owner_id == owner_id or (include_unowned and task.owner_id in (None, ""))
            ]
        if thread_id is not None:
            tasks = [task for task in tasks if task.thread_id == thread_id]
        tasks.sort(key=lambda task: (task.created_at, task.task_id), reverse=True)
        return tasks

    def overview(self) -> dict[str, Any]:
        with self._lock:
            tasks = self._read_tasks()
        return build_task_runs_overview(tasks)

    def recovery_queue(
        self,
        *,
        status: str | None = None,
        kind: str | None = None,
        owner_id: str | None = None,
        thread_id: str | None = None,
        include_monitor: bool = False,
        limit: int = 100,
        include_unowned: bool = True,
    ) -> dict[str, Any]:
        with self._lock:
            tasks = self._filtered_tasks(
                status=status,
                kind=kind,
                owner_id=owner_id,
                thread_id=thread_id,
                include_unowned=include_unowned,
            )
        return build_task_recovery_queue(
            tasks,
            include_monitor=include_monitor,
            limit=limit,
        )

    def mutate(
        self,
        task_id: str,
        mutator: Callable[[TaskRunRecord], TaskRunRecord],
    ) -> TaskRunRecord:
        with self._write_lock():
            payload = self._read_payload()
            tasks = self._read_tasks_from_payload(payload)
            updated: TaskRunRecord | None = None
            next_tasks: _TaskRecordList = []
            for task in tasks:
                if task.task_id != task_id:
                    next_tasks.append(task)
                    continue
                candidate = mutator(task)
                updated = candidate.model_copy(update={"updated_at": _now_iso()}, deep=True)
                next_tasks.append(updated)
            if updated is None:
                raise KeyError(task_id)
            payload["tasks"] = self._dump_tasks(next_tasks)
            payload["lastUpdated"] = updated.updated_at
            self._write_payload(payload)
            return updated

    def next_lease_token(self) -> int:
        with self._write_lock():
            payload = self._read_payload()
            token = max(0, int(payload.get("leaseCounter") or 0)) + 1
            payload["leaseCounter"] = token
            payload["lastUpdated"] = _now_iso()
            self._write_payload(payload)
            return token

    def _read_payload(self) -> dict[str, Any]:
        return _normalize_payload(read_json_with_backup(self.path, default=None))

    def _write_payload(self, payload: dict[str, Any]) -> None:
        atomic_write_json(self.path, _normalize_payload(payload))

    def _write_lock(self) -> Any:
        target = self.path.parent / f"{self.path.name}.rw"
        return _StoreWriteLock(self._lock, target)

    def _read_tasks(self) -> _TaskRecordList:
        return self._read_tasks_from_payload(self._read_payload())

    @staticmethod
    def _read_tasks_from_payload(payload: dict[str, Any]) -> _TaskRecordList:
        tasks: _TaskRecordList = []
        for item in payload.get("tasks") or []:
            if not isinstance(item, dict):
                continue
            try:
                tasks.append(TaskRunRecord.model_validate(item))
            except Exception:
                continue
        return tasks

    @staticmethod
    def _dump_tasks(tasks: _TaskRecordList) -> _TaskDictList:
        return [cast(dict[str, Any], task.model_dump(mode="json")) for task in tasks]


class _StoreWriteLock:
    def __init__(self, thread_lock: threading.RLock, target: Path) -> None:
        self._thread_lock = thread_lock
        self._target = target
        self._process_lock: Any = None

    def __enter__(self) -> None:
        self._thread_lock.__enter__()
        self._process_lock = _cross_process_lock(self._target)
        self._process_lock.__enter__()

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        try:
            if self._process_lock is not None:
                self._process_lock.__exit__(exc_type, exc, tb)
        finally:
            self._thread_lock.__exit__(exc_type, exc, tb)
