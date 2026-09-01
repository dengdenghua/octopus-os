from __future__ import annotations

import hashlib
import json
import threading

from runtime.core.cerebrum.react_types import ReActResult
from runtime.execution.loops._controller_helpers import (
    _PRODUCT_LOOP_MODES,
    _loop_mode_contract,
    _now_iso,
    _truncate_text,
)
from runtime.execution.loops.models import (
    LoopMode,
    LoopRun,
    LoopRunStatus,
    VerifierResult,
)
from runtime.platform.process.session import Session, session_scope
from runtime.platform.process.task_supervisor import TaskCapabilityManifest
from runtime.safety.approval.cancellation import (
    CancellationSource,
    CancellationToken,
    scoped_cancellation,
)

_REPAIR_ATTEMPT_ALLOWED_SKILL_IDS: tuple[str, ...] = (
    # Local, read-only workspace inspection.
    "list_cwd",
    "read_file",
    "read_file_range",
    "file_stats",
    "count_words",
    "hash_text",
    "glob_files",
    "grep_text",
    "tree",
    # One exact, unique replacement in an existing workspace file.
    "edit_file",
    # Turn-local progress bookkeeping only.
    "todo_read",
    "todo_write",
)

_SEALED_EFFECT_CLASSES = frozenset(
    {"none", "read_only", "workspace_write", "local_state", "external_or_unknown"}
)
_SEALED_EFFECT_STATES = frozenset(
    {"not_executed", "committed", "failed", "indeterminate", "replayed"}
)


def _sealed_effect_coordinates(receipt: dict[str, object]) -> tuple[str, str] | None:
    proof = receipt.get("effect_receipt")
    if not isinstance(proof, dict):
        return None
    if proof.get("schema") != "echo.tool.effect_receipt.v1":
        return None
    if proof.get("sealed") is not True or proof.get("emitted_by") != "tool_executor":
        return None
    effect_class = str(proof.get("effect_class") or "")
    state = str(proof.get("state") or "")
    if effect_class not in _SEALED_EFFECT_CLASSES or state not in _SEALED_EFFECT_STATES:
        return None
    proof_tool = str(proof.get("tool_name") or "")
    receipt_tool = str(receipt.get("tool_name") or "")
    if not proof_tool or proof_tool != receipt_tool:
        return None
    return effect_class, state


def _react_result_effect_summary(
    result: ReActResult | None,
    *,
    runtime_owned: bool,
) -> dict[str, object]:
    """Persist counts and a digest, never raw actions, args, or observations."""

    if result is None:
        return {
            "schema": "echo.loop.attempt_effect_summary.v2",
            "emitted_by": "react_runtime" if runtime_owned else "legacy_runner",
            "complete": False,
            "sealed": False,
            "total_tool_count": 0,
            "read_only_effect_count": 0,
            "workspace_write_effect_count": 0,
            "local_state_effect_count": 0,
            "external_effect_count": 0,
            "indeterminate_effect_count": 0,
            "unsealed_receipt_count": 0,
            "unknown_effect_count": 1,
        }
    receipts: list[dict[str, object]] = []
    for step in result.steps:
        receipts.extend(dict(item) for item in step.action_results if isinstance(item, dict))
    fingerprint_rows: list[tuple[str, bool, str, str, str]] = []
    failed_count = 0
    handler_not_executed_count = 0
    trusted_count = 0
    read_only_effect_count = 0
    workspace_write_effect_count = 0
    local_state_effect_count = 0
    external_effect_count = 0
    indeterminate_effect_count = 0
    unsealed_receipt_count = 0
    unknown_effect_count = 0
    for receipt in receipts:
        ok = receipt.get("ok") is True
        source = str(receipt.get("execution_source") or "unknown")
        tool_name = str(receipt.get("tool_name") or "unknown")
        coordinates = _sealed_effect_coordinates(receipt) if runtime_owned else None
        effect_class, effect_state = coordinates or ("unknown", "unknown")
        fingerprint_rows.append((tool_name, ok, source, effect_class, effect_state))
        if not ok:
            failed_count += 1
        if source == "handler_not_executed":
            handler_not_executed_count += 1
        if coordinates is None:
            unsealed_receipt_count += 1
            unknown_effect_count += 1
        else:
            if effect_class == "read_only":
                read_only_effect_count += 1
            elif effect_class == "workspace_write":
                workspace_write_effect_count += 1
            elif effect_class == "local_state":
                local_state_effect_count += 1
            elif effect_class == "external_or_unknown":
                external_effect_count += 1
            elif effect_class != "none":
                unknown_effect_count += 1
            if effect_state == "indeterminate":
                indeterminate_effect_count += 1
        if receipt.get("trusted_execution") is True:
            trusted_count += 1
    digest = hashlib.sha256(
        json.dumps(fingerprint_rows, ensure_ascii=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "schema": "echo.loop.attempt_effect_summary.v2",
        "emitted_by": "react_runtime" if runtime_owned else "legacy_runner",
        "complete": bool(runtime_owned and unsealed_receipt_count == 0),
        "sealed": bool(runtime_owned and unsealed_receipt_count == 0),
        "total_tool_count": len(receipts),
        "failed_effect_count": failed_count,
        "handler_not_executed_count": handler_not_executed_count,
        "trusted_receipt_count": trusted_count,
        "read_only_effect_count": read_only_effect_count,
        "workspace_write_effect_count": workspace_write_effect_count,
        "local_state_effect_count": local_state_effect_count,
        "external_effect_count": external_effect_count,
        "indeterminate_effect_count": indeterminate_effect_count,
        "unsealed_receipt_count": unsealed_receipt_count,
        "unknown_effect_count": unknown_effect_count,
        "effect_fingerprint": digest,
    }


class LoopControllerAttemptMixin:
    def _run_attempt(
        self,
        run: LoopRun,
        prompt: str,
        workspace_path: str,
        attempt_index: int = 1,
        cancellation_token: CancellationToken | None = None,
    ) -> ReActResult | None:
        if self.stack is None:
            raise RuntimeError("loop controller stack is not available")
        from runtime.core.cerebrum.react_loop import run_react_loop
        from runtime.platform.models import ParsedIntent

        runner = self.react_runner or run_react_loop
        thread_id = run.thread_id or run.run_id
        user_context = {
            "objective": run.goal,
            "workspace_path": workspace_path,
            "mode": run.mode.value,
            "workflow_mode": run.mode.value if run.mode in _PRODUCT_LOOP_MODES else "",
            "goal_mode": run.policy.goal_mode or run.mode == LoopMode.GOAL,
            "completion_policy": (
                "goal"
                if run.policy.goal_mode or run.mode == LoopMode.GOAL
                else run.mode.value
                if run.mode in {LoopMode.PLAN, LoopMode.SPEC}
                else ""
            ),
            "mode_preset": (
                f"{run.mode.value}.mode" if run.mode in _PRODUCT_LOOP_MODES else run.mode.value
            ),
            "workflow_preset": (
                f"{run.mode.value}.mode" if run.mode in _PRODUCT_LOOP_MODES else ""
            ),
            "mode_contract": _loop_mode_contract(run.mode),
            "budget_auto_pause": run.policy.budget_auto_pause,
            "max_tokens_budget": run.policy.max_tokens_budget,
            "max_usd_budget": run.policy.max_usd_budget,
            "auto_approve": run.policy.auto_approve,
            "thread_id": thread_id,
            "sandbox_mode": run.policy.sandbox_mode,
            "permission_mode": run.policy.permission_mode,
            "execution_environment": run.policy.execution_environment,
        }
        intent = ParsedIntent(
            raw=prompt,
            intent_type="task",
            normalized_goal=prompt,
            user_context=user_context,
        )
        metadata = dict(user_context)
        if self.task_supervisor is not None:
            manifest = self.task_supervisor.task_capabilities(run.run_id)
            if manifest is not None:
                metadata["task_id"] = run.run_id
                metadata["task_capability_manifest"] = manifest.model_dump(mode="json")
            metadata["task_supervisor_store_path"] = str(self.task_supervisor.store.path)
            metadata["task_supervisor_holder_id"] = self.task_supervisor.holder_id
            metadata["task_supervisor_lease_ttl_seconds"] = self.task_supervisor.lease_ttl_seconds
            metadata["enforce_executor_approval"] = True
        if attempt_index > 1:
            # Automatic repair attempts run inside the same workspace but on
            # a much smaller server-enforced tool surface.  The exact skill
            # allowlist is authoritative even for tools whose capability
            # group is unknown (including dynamically loaded plugins).
            repair_manifest = TaskCapabilityManifest(
                source="loop_repair_attempt",
                workspace_paths=[workspace_path] if workspace_path else [],
                allowed_skill_ids=list(_REPAIR_ATTEMPT_ALLOWED_SKILL_IDS),
                groups={
                    "builtin": True,
                    "web": False,
                    "browser": False,
                    "computer": False,
                    "fs_write": True,
                    "git": False,
                    "shell": False,
                    "memory": False,
                },
            )
            metadata["task_capability_manifest"] = repair_manifest.model_dump(mode="json")
        # One ReAct attempt can outlive the default supervisor lease TTL.
        # Heartbeat from a separate daemon thread while the attempt is in
        # flight; otherwise another worker can take over the same run and
        # workspace even though this attempt is still executing.
        attempt_source = CancellationSource()
        parent_token = cancellation_token or CancellationToken.none()
        unlink_parent = parent_token.on_cancelled(
            lambda reason: attempt_source.cancel(reason=reason or "parent cancelled"),
        )
        heartbeat_stop = threading.Event()
        heartbeat_interval = 0.0
        if self.task_supervisor is not None:
            heartbeat_interval = max(
                0.1,
                min(self.task_supervisor.lease_ttl_seconds / 3.0, 30.0),
            )

        def _heartbeat_loop() -> None:
            while heartbeat_interval > 0 and not heartbeat_stop.wait(heartbeat_interval):
                if not self._supervisor_heartbeat(run.run_id):
                    # The attempt is no longer authoritative. Cooperative
                    # model/tool code must stop before the replacement worker
                    # can touch the workspace.
                    attempt_source.cancel(reason="task supervisor lease lost")
                    return

        heartbeat_thread: threading.Thread | None = None
        if heartbeat_interval > 0:
            heartbeat_thread = threading.Thread(
                target=_heartbeat_loop,
                name=f"loop-lease-heartbeat-{run.run_id[:12]}",
                daemon=True,
            )
            heartbeat_thread.start()
        try:
            with (
                session_scope(
                    Session(
                        actor=run.owner_id,
                        thread_id=thread_id,
                        metadata=metadata,
                    )
                ),
                scoped_cancellation(attempt_source.token),
            ):
                runner_kwargs = {
                    "stack": self.stack,
                    "intent": intent,
                    "agent": None,
                    "model": run.policy.model,
                    "max_iterations": run.policy.max_iterations,
                    "thread_id": thread_id,
                }
                if self.react_runner is None:
                    from runtime.core.cerebrum.react_step_evaluator import (
                        build_runtime_step_evaluator,
                    )

                    runner_kwargs.update(
                        {
                            "max_tokens_budget": run.policy.max_tokens_budget,
                            "max_usd_budget": run.policy.max_usd_budget,
                            "step_evaluator": build_runtime_step_evaluator(),
                        }
                    )
                return runner(
                    **runner_kwargs,
                )
        finally:
            heartbeat_stop.set()
            if heartbeat_thread is not None:
                heartbeat_thread.join(timeout=max(1.0, heartbeat_interval + 0.5))
            unlink_parent()

    def _check_for_cancellation(
        self,
        run_id: str,
        *,
        cancellation_token: CancellationToken | None = None,
        latest_result: ReActResult | None = None,
    ) -> LoopRun | None:
        run = self.store.get(run_id)
        if run is None:
            raise KeyError(run_id)
        reason = self._cancellation_reason(run, cancellation_token=cancellation_token)
        if latest_result is not None and str(latest_result.terminated_reason or "") == "cancelled":
            reason = reason or str(latest_result.terminated_reason or "").strip() or "cancelled"
        if not reason:
            return None
        return self._cancel_run(run_id, reason)

    @staticmethod
    def _cancellation_reason(
        run: LoopRun,
        *,
        cancellation_token: CancellationToken | None = None,
    ) -> str:
        if cancellation_token is not None and cancellation_token.is_cancelled:
            return str(cancellation_token.reason or "").strip() or "cancelled"
        if run.cancel_requested_at:
            return str(run.cancel_reason or "").strip() or "cancelled"
        return ""

    def _cancel_run(self, run_id: str, reason: str) -> LoopRun:
        cancel_reason = str(reason or "").strip() or "cancelled"
        run = self.store.get(run_id)
        if run is None:
            raise KeyError(run_id)
        if run.status == LoopRunStatus.CANCELLED:
            return run
        if not self._supervisor_heartbeat(run_id):
            return run
        run = self.store.mutate(
            run_id,
            lambda current, cancel_reason=cancel_reason: current.model_copy(
                update={
                    "status": LoopRunStatus.CANCELLED,
                    "completed_at": current.completed_at or _now_iso(),
                    "cancel_requested_at": current.cancel_requested_at or _now_iso(),
                    "cancel_reason": cancel_reason,
                    "last_error": cancel_reason,
                }
            ),
        )
        return self._finalize_learning(run)

    def _record_attempt_exception(
        self,
        run_id: str,
        attempt_index: int,
        error_text: str,
        *,
        category: str,
        effect_summary: dict[str, object],
    ) -> LoopRun:
        return self.store.mutate(
            run_id,
            lambda current: current.model_copy(
                update={
                    "attempts": [
                        attempt.model_copy(
                            update={
                                "completed_at": _now_iso(),
                                "status": "failed",
                                "error": error_text,
                                "success": False,
                                "terminated_reason": f"exception:{category}",
                                "effect_summary": effect_summary,
                            }
                        )
                        if attempt.attempt_index == attempt_index
                        else attempt
                        for attempt in current.attempts
                    ],
                }
            ),
        )

    def _record_attempt_result(
        self,
        run_id: str,
        attempt_index: int,
        react_result: ReActResult | None,
    ) -> LoopRun:
        final_answer = react_result.final_answer if react_result is not None else ""
        success = react_result.success if react_result is not None else False
        terminated_reason = (
            react_result.terminated_reason if react_result is not None else "runner_returned_none"
        )
        completion_receipt = react_result.completion_receipt if react_result is not None else {}
        completion_decision = react_result.completion_decision if react_result is not None else {}
        effect_summary = _react_result_effect_summary(
            react_result,
            runtime_owned=self.react_runner is None,
        )
        return self.store.mutate(
            run_id,
            lambda current: current.model_copy(
                update={
                    "attempts": [
                        attempt.model_copy(
                            update={
                                "completed_at": _now_iso(),
                                "status": (
                                    "cancelled"
                                    if terminated_reason == "cancelled"
                                    else "completed"
                                    if success
                                    else "needs_verify"
                                ),
                                "success": success,
                                "terminated_reason": terminated_reason,
                                "final_answer": _truncate_text(final_answer),
                                "completion_receipt": completion_receipt,
                                "completion_decision": completion_decision,
                                "effect_summary": effect_summary,
                            }
                        )
                        if attempt.attempt_index == attempt_index
                        else attempt
                        for attempt in current.attempts
                    ],
                }
            ),
        )

    def _record_verifier_result(
        self,
        run_id: str,
        attempt_index: int,
        verifier_result: VerifierResult,
    ) -> LoopRun:
        return self.store.mutate(
            run_id,
            lambda current: current.model_copy(
                update={
                    "last_verifier_result": verifier_result,
                    "attempts": [
                        attempt.model_copy(update={"verifier_result": verifier_result})
                        if attempt.attempt_index == attempt_index
                        else attempt
                        for attempt in current.attempts
                    ],
                }
            ),
        )
