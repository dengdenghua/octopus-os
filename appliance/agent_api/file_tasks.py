"""Bind synchronous provider work to the existing Agent task lifecycle."""

from __future__ import annotations

import copy
from typing import Any

from appliance.agent_api.tasks import TaskLeaseConflict

_TERMINAL = {"completed", "failed", "cancelled", "disconnected"}


class FileOperationTask:
    def __init__(self, supervisor: Any, record: dict[str, Any]):
        self.supervisor = supervisor
        self.record = copy.deepcopy(record)
        self.task_id = record["taskId"]
        self._attempt: Any = None
        self._token: int | None = None
        self._finished: dict[str, Any] | None = None

    def _binding(self, current: Any) -> None:
        if current.owner_id != self.record["owner"]:
            raise PermissionError("file organization task owner changed")
        if (
            current.kind != "file_organization"
            or current.workspace_path != self.record["workspacePath"]
            or current.metadata.get("provider") != "echo-os.files"
            or current.metadata.get("plan_id") != self.record["plan"]["planId"]
        ):
            raise PermissionError("file organization task binding changed")

    def _current(self):
        if self._attempt is None:
            raise RuntimeError("file organization task attempt has not started")
        current = self._attempt.assert_current_holder(self.task_id)
        self._binding(current)
        return current

    def _new_attempt(self):
        return type(self.supervisor)(
            self.supervisor.store,
            holder_id=self.supervisor.holder_id,
            lease_ttl_seconds=self.supervisor.lease_ttl_seconds,
        )

    def _adopt_takeover(self, previous: Any) -> None:
        token = previous.lease.token
        attempt = self._new_attempt()
        observed = attempt.heartbeat(self.task_id)
        if observed.lease is None or observed.lease.token != token:
            raise TaskLeaseConflict(self.task_id, self.supervisor.holder_id)

        def consume(current):
            self._binding(current)
            lease = current.lease
            if (
                lease is None
                or lease.expired
                or lease.token != token
                or lease.holder_id != self.supervisor.holder_id
                or current.metadata.get("file_operation_takeover_token") != token
                or current.metadata.get("takeover_by") not in {self.record["owner"], "local:admin"}
            ):
                raise TaskLeaseConflict(self.task_id, self.supervisor.holder_id)
            return current.model_copy(
                update={
                    "metadata": {
                        **current.metadata,
                        "file_operation_takeover_token": None,
                        "cancel_requested": False,
                        "completed_steps": 0,
                        "moved_steps": 0,
                        "file_operation_state": "running",
                    }
                },
                deep=True,
            )

        self.supervisor.store.mutate(self.task_id, consume)
        self._attempt, self._token, self._finished = attempt, token, None

    def start(self) -> None:
        if self.supervisor is None:
            raise RuntimeError("file organization task authority is unavailable")
        previous = self.supervisor.store.get(self.task_id)
        if previous is not None:
            self._binding(previous)
            lease = previous.lease
            if str(previous.status) not in _TERMINAL and lease is not None and not lease.expired:
                if self._attempt is not None and lease.token == self._token:
                    self._current()
                    self._attempt.heartbeat(self.task_id)
                    return
                if (
                    lease.holder_id == self.supervisor.holder_id
                    and previous.metadata.get("file_operation_takeover_token") == lease.token
                    and previous.metadata.get("takeover_by")
                    in {self.record["owner"], "local:admin"}
                ):
                    self._adopt_takeover(previous)
                    return
                raise TaskLeaseConflict(self.task_id, lease.holder_id)
        plan = self.record["plan"]
        # Same authority/store/holder; only its lease-token memory is scoped to
        # this attempt, so an old worker cannot borrow a retry's newer token.
        self._attempt = self._new_attempt()
        current = self._attempt.start_task(
            task_id=self.task_id,
            kind="file_organization",
            owner_id=self.record["owner"],
            title="撤销文档整理" if plan["direction"] == "undo" else "按年月整理票据",
            goal="执行已批准的文件变更清单，保留逐项结果与恢复证据。",
            mode="file_organization",
            workspace_path=self.record["workspacePath"],
            metadata={
                "provider": "echo-os.files",
                "plan_id": plan["planId"],
                "cancel_requested": False,
                "completed_steps": 0,
                "total_steps": plan["summary"]["ready"],
                "file_operation_state": "running",
                "moved_steps": 0,
                "file_operation_takeover_token": None,
            },
        )
        self._token = current.lease.token
        self._finished = None

    def cancelled(self) -> bool:
        try:
            current = self._current()
        except (KeyError, RuntimeError, PermissionError):
            return True
        return bool(current.metadata.get("cancel_requested"))

    def progress(self, completed: int) -> None:
        current = self._current()
        total = self.record["plan"]["summary"]["ready"]
        if type(completed) is not int or not 0 <= completed <= total:
            raise ValueError("invalid file organization progress")
        if completed < current.metadata.get("completed_steps", 0):
            raise ValueError("file organization progress cannot go backwards")
        self._attempt.transition(
            self.task_id, "running", metadata_patch={"completed_steps": completed}
        )

    def finish(self, result: dict[str, Any]) -> None:
        if self._finished is not None:
            if self._finished == result:
                return
            raise RuntimeError("file organization attempt is already finished")
        current = self._current()
        state = result["state"]
        if state not in {"completed", "partial", "cancelled", "failed", "uncertain"}:
            raise ValueError("file organization result is not terminal")
        status = (
            "cancelled"
            if state == "cancelled"
            else "completed"
            if state == "completed" and result["executionComplete"] is True
            else "failed"
        )
        counts = result["counts"]
        if any(type(value) is not int or value < 0 for value in counts.values()):
            raise ValueError("invalid file organization result counts")
        if status == "completed" and any(
            counts.get(key, 0) for key in ("conflicts", "failed", "pending", "uncertain")
        ):
            raise ValueError("completed file organization result has unresolved entries")
        total = self.record["plan"]["summary"]["ready"]
        processed = min(total, sum(value for key, value in counts.items() if key != "pending"))
        self._attempt.transition(
            self.task_id,
            status,
            reason=state,
            metadata_patch={
                "completed_steps": max(current.metadata.get("completed_steps", 0), processed),
                "moved_steps": counts["moved"],
                "file_operation_state": state,
            },
        )
        self._finished = copy.deepcopy(result)

    def request_cancel(self, actor: str) -> None:
        if self.supervisor is None:
            raise RuntimeError("file organization task authority is unavailable")

        def update(current):
            if current.owner_id != actor:
                raise PermissionError("file organization task owner changed")
            self._binding(current)
            if str(current.status) in _TERMINAL:
                return current
            return current.model_copy(
                update={"metadata": {**current.metadata, "cancel_requested": True}}, deep=True
            )

        self.supervisor.store.mutate(self.task_id, update)


__all__ = ["FileOperationTask"]
