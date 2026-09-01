from __future__ import annotations

import logging
from typing import Any

from runtime.execution.loops._controller_helpers import _TRACE_AGENT_ID
from runtime.execution.loops.models import LoopRun, LoopRunStatus
from runtime.execution.loops.recovery import build_loop_run_checkpoint

_LOG = logging.getLogger("runtime.execution.loops.controller")


class LoopControllerTraceMixin:
    def _record_trace_run_started(self, run: LoopRun) -> None:
        if self.trace_store is None or not run.started_at:
            return
        try:
            existing = self.trace_store.events(
                task_id=run.run_id,
                event_type="TASK_RUN_STARTED",
                limit=1,
            )
            if existing:
                return
            self.trace_store.record_task_run_started(
                task_id=run.run_id,
                thread_id=run.thread_id or run.run_id,
                turn_id=run.run_id,
                agent_id=_TRACE_AGENT_ID,
                title=run.goal,
                goal=run.goal,
                mode=run.mode.value,
                metadata={
                    "workspace_path": run.workspace_path,
                    "parent_run_id": run.parent_run_id,
                    "origin_run_id": run.origin_run_id,
                    "resume_checkpoint_id": run.resume_checkpoint_id,
                },
                ts=run.started_at,
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("loop trace start record failed for %s: %s", run.run_id, exc)

    def _record_trace_terminal_artifacts(self, run: LoopRun) -> int | None:
        if self.trace_store is None:
            return None
        try:
            checkpoint = build_loop_run_checkpoint(run)
            existing_checkpoint_id = self._matching_trace_checkpoint_id(run, checkpoint=checkpoint)
            if existing_checkpoint_id is not None:
                self._ensure_trace_terminal_event(
                    run,
                    checkpoint=checkpoint,
                    checkpoint_id=existing_checkpoint_id,
                )
                return existing_checkpoint_id
            checkpoint_id = self.trace_store.record_checkpoint(
                task_id=run.run_id,
                checkpoint_type=str(checkpoint.get("checkpoint_type") or "loop_run"),
                state=checkpoint.get("state") if isinstance(checkpoint.get("state"), dict) else {},
                thread_id=run.thread_id or run.run_id,
                turn_id=run.run_id,
                agent_id=_TRACE_AGENT_ID,
                iteration=int(checkpoint.get("iteration") or 0),
                summary=str(checkpoint.get("summary") or ""),
                ts=str(checkpoint.get("ts") or "") or None,
            )
            self._ensure_trace_terminal_event(
                run,
                checkpoint=checkpoint,
                checkpoint_id=checkpoint_id,
            )
            return checkpoint_id
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("loop trace terminal record failed for %s: %s", run.run_id, exc)
            return None

    def _matching_trace_checkpoint_id(
        self,
        run: LoopRun,
        *,
        checkpoint: dict[str, Any],
    ) -> int | None:
        if self.trace_store is None:
            return None
        existing = self.trace_store.latest_checkpoint(
            task_id=run.run_id,
            checkpoint_type=str(checkpoint.get("checkpoint_type") or "loop_run"),
        )
        if existing is None:
            return None
        checkpoint_id = existing.get("id")
        if self._terminal_trace_event(run, checkpoint_id=checkpoint_id) is not None:
            try:
                return int(checkpoint_id)
            except (TypeError, ValueError):
                return None
        if int(existing.get("iteration") or 0) != int(checkpoint.get("iteration") or 0):
            return None
        if str(existing.get("summary") or "") != str(checkpoint.get("summary") or ""):
            return None
        state = existing.get("state") if isinstance(existing.get("state"), dict) else {}
        if str(state.get("current_phase") or "") != run.status.value:
            return None
        try:
            return int(checkpoint_id)
        except (TypeError, ValueError):
            return None

    def _ensure_trace_terminal_event(
        self,
        run: LoopRun,
        *,
        checkpoint: dict[str, Any],
        checkpoint_id: int,
    ) -> None:
        if self._terminal_trace_event(run, checkpoint_id=checkpoint_id) is not None:
            return
        if self.trace_store is None:
            return
        self.trace_store.record_task_run_finished(
            task_id=run.run_id,
            status=self._trace_task_status(run.status),
            thread_id=run.thread_id or run.run_id,
            turn_id=run.run_id,
            agent_id=_TRACE_AGENT_ID,
            summary=str(checkpoint.get("summary") or ""),
            reason=run.cancel_reason or run.last_error,
            metadata={
                "checkpoint_id": checkpoint_id,
                "checkpoint_type": str(checkpoint.get("checkpoint_type") or "loop_run"),
                "workspace_path": run.workspace_path,
                "parent_run_id": run.parent_run_id,
                "origin_run_id": run.origin_run_id,
                "resume_checkpoint_id": run.resume_checkpoint_id,
            },
            ts=run.completed_at,
        )

    def _terminal_trace_event(
        self,
        run: LoopRun,
        *,
        checkpoint_id: Any,
    ) -> dict[str, Any] | None:
        if self.trace_store is None:
            return None
        event_type = {
            "completed": "TASK_RUN_COMPLETED",
            "failed": "TASK_RUN_FAILED",
            "cancelled": "TASK_RUN_CANCELLED",
        }.get(self._trace_task_status(run.status), "TASK_RUN_FINISHED")
        for event in self.trace_store.events(
            task_id=run.run_id,
            event_type=event_type,
            limit=20,
        ):
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
            if str(metadata.get("checkpoint_id") or "") == str(checkpoint_id or ""):
                return event
        return None

    @staticmethod
    def _trace_task_status(status: LoopRunStatus) -> str:
        return {
            LoopRunStatus.COMPLETED: "completed",
            LoopRunStatus.FAILED: "failed",
            LoopRunStatus.CANCELLED: "cancelled",
        }.get(status, "unknown")

    @staticmethod
    def _link_trace_checkpoint(review: dict[str, Any], trace_checkpoint_id: int) -> dict[str, Any]:
        payload = dict(review)
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        payload["summary"] = {**summary, "trace_checkpoint_id": trace_checkpoint_id}
        resume = payload.get("resume") if isinstance(payload.get("resume"), dict) else {}
        latest_checkpoint = (
            resume.get("latest_checkpoint")
            if isinstance(resume.get("latest_checkpoint"), dict)
            else {}
        )
        if latest_checkpoint:
            payload["resume"] = {
                **resume,
                "latest_checkpoint": {
                    **latest_checkpoint,
                    "trace_checkpoint_id": trace_checkpoint_id,
                },
            }
        return payload
