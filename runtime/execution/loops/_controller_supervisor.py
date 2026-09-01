from __future__ import annotations

import logging

from runtime.execution.loops.models import LoopRun, LoopRunStatus
from runtime.platform.process.task_supervisor import (
    LostTaskLease,
    TaskCapabilityManifest,
    TaskLeaseConflict,
    TaskRunStatus,
)

_LOG = logging.getLogger("runtime.execution.loops.controller")


class LoopControllerSupervisorMixin:
    def _task_capabilities_for_run(self, run: LoopRun) -> TaskCapabilityManifest:
        return TaskCapabilityManifest(
            source="loop_policy",
            workspace_paths=[run.workspace_path] if run.workspace_path else [],
            groups={
                "builtin": True,
                "web": True,
                "browser": True,
                "computer": True,
                "fs_write": True,
                "git": True,
                "shell": True,
                "memory": True,
            },
        )

    def _supervisor_start(self, run: LoopRun) -> bool:
        if self.task_supervisor is None:
            return True
        try:
            self.task_supervisor.start_task(
                task_id=run.run_id,
                kind="loop",
                owner_id=run.owner_id,
                thread_id=run.thread_id or run.run_id,
                parent_task_id=run.parent_run_id,
                origin_task_id=run.origin_run_id,
                resume_checkpoint_id=run.resume_checkpoint_id,
                title=run.goal,
                goal=run.goal,
                mode=run.mode.value,
                workspace_path=run.workspace_path,
                capabilities=self._task_capabilities_for_run(run),
                status=self._supervisor_status(run.status),
                metadata={
                    "policy": run.policy.model_dump(mode="json"),
                    "source": "loop_controller",
                },
            )
            return True
        except TaskLeaseConflict as exc:
            _LOG.info("loop task %s is already leased by %s", run.run_id, exc.holder_id)
            return False
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("loop task supervisor start failed for %s: %s", run.run_id, exc)
            return True

    def _supervisor_transition(
        self,
        run: LoopRun,
        status: TaskRunStatus | None = None,
        *,
        checkpoint_id: str | int | None = None,
    ) -> bool:
        if self.task_supervisor is None:
            return True
        try:
            self.task_supervisor.transition(
                run.run_id,
                status or self._supervisor_status(run.status),
                reason=run.cancel_reason or run.last_error,
                checkpoint_id=checkpoint_id,
                metadata_patch={
                    "attempt_count": len(run.attempts),
                    "last_loop_status": run.status.value,
                },
            )
            return True
        except KeyError:
            if not self._supervisor_start(run):
                return False
            try:
                self.task_supervisor.transition(
                    run.run_id,
                    status or self._supervisor_status(run.status),
                    reason=run.cancel_reason or run.last_error,
                    checkpoint_id=checkpoint_id,
                    metadata_patch={
                        "attempt_count": len(run.attempts),
                        "last_loop_status": run.status.value,
                    },
                )
                return True
            except (LostTaskLease, TaskLeaseConflict) as exc:
                _LOG.info("loop task supervisor lease lost for %s: %s", run.run_id, exc)
                return False
            except Exception as exc:  # noqa: BLE001
                _LOG.warning(
                    "loop task supervisor retry transition failed for %s: %s",
                    run.run_id,
                    exc,
                )
                return True
        except LostTaskLease as exc:
            _LOG.info("loop task supervisor lease lost for %s: %s", run.run_id, exc)
            return False
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("loop task supervisor transition failed for %s: %s", run.run_id, exc)
            return True

    def _supervisor_heartbeat(self, run_id: str) -> bool:
        if self.task_supervisor is None:
            return True
        try:
            self.task_supervisor.heartbeat(run_id)
            return True
        except KeyError:
            return True
        except LostTaskLease as exc:
            _LOG.info("loop task supervisor lease lost for %s: %s", run_id, exc)
            return False
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("loop task supervisor heartbeat failed for %s: %s", run_id, exc)
            return True

    def _latest_run(self, run_id: str) -> LoopRun:
        latest = self.store.get(run_id)
        if latest is None:
            raise KeyError(run_id)
        return latest

    @staticmethod
    def _supervisor_status(status: LoopRunStatus) -> TaskRunStatus:
        return {
            LoopRunStatus.PENDING: TaskRunStatus.PENDING,
            LoopRunStatus.RUNNING: TaskRunStatus.RUNNING,
            LoopRunStatus.VERIFYING: TaskRunStatus.VERIFYING,
            LoopRunStatus.REPAIRING: TaskRunStatus.REPAIRING,
            LoopRunStatus.COMPLETED: TaskRunStatus.COMPLETED,
            LoopRunStatus.FAILED: TaskRunStatus.FAILED,
            LoopRunStatus.CANCELLED: TaskRunStatus.CANCELLED,
            LoopRunStatus.INTERRUPTED: TaskRunStatus.DISCONNECTED,
        }[status]
