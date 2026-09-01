from __future__ import annotations

import contextlib
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from runtime.core.nerves.hooks import HookManager

from runtime.adapters.instrumentation import trace_stage
from runtime.execution.misc.file_write_leases import (
    FileWriteLeaseConflict,
    WorkspaceContentDriftConflict,
    acquire_file_write_lease,
    record_file_write_snapshot,
    verify_file_unchanged_since_read,
)
from runtime.execution.suckers import SkillRegistry
from runtime.execution.tool_engine._executor_fileops import (
    _emit_file_op_from_step,
    _extract_path,
    _try_read_pre_content,
)
from runtime.execution.tool_engine._executor_helpers import (
    _READ_BEFORE_WRITE_TOOLS,
    ReadBeforeWriteRequired,
    StepExecutionError,
    _call_handler_with_transient_retry,
    _check_capability_permission,
    _check_task_capability_permission,
    _current_execution_policy_context,
    _declared_write_scope_violation,
    _emit_skill_metrics,
    _extract_token_usage,
    _file_write_lease_owner,
    _file_write_lease_target,
    _hash_output,
    _make_reject_step,
    _mark_task_waiting_approval,
    _notify_budget_warnings,
    _prepare_scoped_args,
    _read_before_write_violation,
    _record_session_budget,
    _record_successful_read,
    _resolve_workspace_for_diagnostics,
    _restore_trusted_browser_loopback_access,
    _validate_output,
)
from runtime.execution.tool_engine.effect_receipts import (
    EffectResolution,
    ToolEffectReceiptIndex,
    build_server_effect_receipt,
    indeterminate_step,
    is_side_effecting,
)
from runtime.execution.tool_engine.effect_store import EffectStore, SQLiteEffectStore
from runtime.execution.tool_engine.skill_gate import (
    antigen_for,
    file_safety_target,
    use_trust_engine,
)
from runtime.execution.tool_engine.tool_protocol import output_signals_error
from runtime.memory.journal import InMemoryJournal, Journal
from runtime.platform.models import (
    ArmId,
    Budget,
    CostEntry,
    ExecutionResult,
    ExecutionStatus,
    InsufficientBudget,
    SkillId,
    Step,
    TaskId,
    ToolCall,
)
from runtime.platform.process.utils import safe_repr as _safe_repr
from runtime.safety.auth import (
    TrustEngine,
    check_file_write,
    strip_model_controlled_overrides,
)
from runtime.safety.governance import (
    GovernanceOutcome,
    build_execution_instruction,
    evaluate_execution_policy,
)
from runtime.safety.validation.prompt_injection import (
    is_untrusted_tool,
    mark_injection_taint,
    scan_for_injection,
)

_AUTOMATION_SKILL_GROUPS = frozenset({"browser", "browser_act", "computer"})


def _runtime_automation_gate(skill_id: str) -> tuple[bool, str | None]:
    """Re-check mutable automation opt-outs at dispatch time.

    Registration-time filtering keeps disabled tools out of new prompts. This
    second gate closes the restart window for already-built registries and
    already-running turns: switching automation off takes effect on the very
    next tool call, even though switching it back on still requires rebuilding
    the tool catalog.
    """

    from runtime.execution.all_skills import skill_group

    group = skill_group(skill_id)
    if group not in _AUTOMATION_SKILL_GROUPS:
        return False, group
    from runtime.platform.runtime_policy.capabilities import load as load_capabilities

    return group in load_capabilities().disabled_skill_groups(), group


def _canonical_execution_provenance(
    skill: Any,
    *,
    handler_executed: bool,
    receipt_rewrite_source: str | None = None,
) -> tuple[bool, str]:
    """Classify the exact Skill handler captured for this execution.

    Registry names and metadata are replaceable. Object identity of the
    canonical built-in handler is the sealed fact a same-name plugin cannot
    forge, and using the already-captured ``skill`` closes the lookup/call
    race.  Handler identity alone is insufficient, however: a pre hook can
    bypass the handler and a post hook can replace the receipt later consumed
    by environment-gap classification.  Both cases fail closed.
    """

    if not handler_executed:
        return False, receipt_rewrite_source or "handler_not_executed"
    if receipt_rewrite_source is not None:
        return False, receipt_rewrite_source

    tool_name = str(getattr(skill, "skill_id", "") or getattr(skill, "name", ""))
    if tool_name not in {"exec_shell", "format_code", "lint_check", "run_tests"}:
        return False, "registered_noncanonical"

    from runtime.execution.suckers._write_skills_exec import _exec_shell
    from runtime.execution.suckers._write_skills_quality import (
        _format_code,
        _lint_check,
        _run_tests,
    )

    canonical_handlers = {
        "exec_shell": _exec_shell,
        "format_code": _format_code,
        "lint_check": _lint_check,
        "run_tests": _run_tests,
    }
    expected = canonical_handlers.get(tool_name)
    if expected is not None and getattr(skill, "handler", None) is expected:
        return True, "canonical_builtin"
    return False, "registered_noncanonical"


__all__ = [
    "ReadBeforeWriteRequired",
    "StepExecutionError",
    "ToolExecutor",
]


class ToolExecutor:
    """Skill-step executor with read-before-write + diff/rollback wiring.

    ╔════════════════════════════════════════════════════════════════════╗
    ║ executor.py · navigation map.                                      ║
    ║                                                                    ║
    ║   §1 helpers (_validate_output, retry classifier)  ~L54            ║
    ║   §2 ToolExecutor (the main class, ~775 lines)     ~L105           ║
    ║   §3 token usage extraction                        ~L880           ║
    ║   §4 read-before-write enforcement                 ~L920           ║
    ║   §5 file-write lease tracking                     ~L950           ║
    ║   §6 path canonicalisation + workspace resolve     ~L996           ║
    ║   §7 reject-step builders + capability check       ~L1043          ║
    ║   §8 output hashing + path extraction              ~L1076          ║
    ║   §9 diff computation + rollback payload           ~L1151          ║
    ║   §10 file_op event emission                       ~L1261          ║
    ╚════════════════════════════════════════════════════════════════════╝
    """

    def __init__(
        self,
        registry: SkillRegistry,
        immunity: TrustEngine,
        journal: Journal | None = None,
        *,
        hooks: HookManager | None = None,
        budget_tracker: Any = None,
        effect_store_path: str | Path | None = None,
        effect_store: EffectStore | None = None,
    ) -> None:
        if effect_store_path is not None and effect_store is not None:
            raise ValueError("effect_store_path and effect_store are mutually exclusive")
        self.registry = registry
        self.immunity = immunity
        self.journal = journal if journal is not None else InMemoryJournal()
        self._effect_store_override = effect_store
        self._effect_store_path = (
            Path(effect_store_path).expanduser().resolve(strict=False)
            if effect_store_path is not None
            else None
        )
        self._effect_receipts = self._build_effect_receipts()
        self._effect_receipts_journal = self.journal
        self.hooks = hooks  # Implementation note.
        # Session-level cumulative budget tracker (Round 18 primitive).
        # When provided, every successful step's cost is recorded into
        # the per-session ledger keyed off ``current_session().session_id``.
        # ``None`` (default) preserves prior behaviour — no session-level
        # tracking, only per-task ``Budget`` accounting.
        self._budget_tracker = budget_tracker

    def _build_effect_receipts(self) -> ToolEffectReceiptIndex | None:
        if not (
            hasattr(self.journal, "read_all") and hasattr(self.journal, "write_tool_effect_intent")
        ):
            return None
        store_path = self._effect_store_path
        if store_path is None:
            journal_path = getattr(self.journal, "_path", None)
            if isinstance(journal_path, Path):
                store_path = journal_path.with_suffix(journal_path.suffix + ".effects.sqlite3")
        store = self._effect_store_override
        if store is None and store_path is not None:
            store = SQLiteEffectStore(store_path)
        return ToolEffectReceiptIndex(self.journal, store=store)

    def configure_effect_store(self, path: str | Path) -> None:
        """Attach the process-shared receipt plane used by server workers."""

        if self._effect_store_override is not None:
            return
        self._effect_store_path = Path(path).expanduser().resolve(strict=False)
        self._effect_receipts = self._build_effect_receipts()
        self._effect_receipts_journal = self.journal

    @property
    def effect_store(self) -> EffectStore | None:
        receipts = self._current_effect_receipts()
        return receipts.store if receipts is not None else None

    def _current_effect_receipts(self) -> ToolEffectReceiptIndex | None:
        """Keep the receipt index aligned when a test/runtime swaps journals."""

        if self._effect_receipts_journal is not self.journal:
            self._effect_receipts_journal = self.journal
            self._effect_receipts = self._build_effect_receipts()
        return self._effect_receipts

    def execute_step(
        self,
        step_id: int,
        node_id: str,
        sucker_id: SkillId,
        args: dict[str, Any],
        *,
        caller: str,
        task_id: TaskId,
        arm_id: ArmId,
        budget: Budget,
        predicted_cost: CostEntry | None = None,
        actor: str | None = None,
        trusted_browser_loopback: bool = False,
    ) -> Step:
        with trace_stage(
            "beak.execute_step",
            stage="execute",
            task_id=str(task_id),
            arm_id=arm_id,
        ) as span:
            span.set_attribute("echo.sucker_id", sucker_id)
            span.set_attribute("echo.node_id", node_id)
            if actor:
                span.set_attribute("echo.actor", actor)

            # Drop model-controllable privilege-escalation flags before the
            # args reach the ToolCall record, the journal, or
            # ``handler(**args)``. The published tool schema hides
            # ``allow_sensitive`` but is ``additionalProperties: True``, so a
            # model — or an indirect prompt injection in tool output — could
            # otherwise smuggle ``allow_sensitive`` / ``allow_private`` in to
            # defeat the sensitive-file / SSRF guards. Trusted internal callers
            # set these by invoking handlers directly, never via execute_step.
            args, _stripped_overrides = strip_model_controlled_overrides(args)
            args = _restore_trusted_browser_loopback_access(
                sucker_id,
                args,
                trusted_runtime_grant=trusted_browser_loopback,
            )
            if _stripped_overrides:
                span.set_attribute(
                    "echo.args.stripped_overrides",
                    ",".join(_stripped_overrides),
                )

            skill = self.registry.get(sucker_id)
            if not self.registry.is_enabled(sucker_id):
                call = ToolCall(
                    caller=caller,
                    sucker_id=sucker_id,
                    args=args,
                    predicted_cost=predicted_cost,
                )
                step = _make_reject_step(
                    step_id,
                    node_id,
                    call,
                    "failed",
                    f"skill disabled: {sucker_id}",
                )
                self.journal.write_step(task_id, arm_id, step, actor=actor)
                span.set_attribute("echo.skill.disabled", True)
                return step

            runtime_disabled, disabled_group = _runtime_automation_gate(str(sucker_id))
            if runtime_disabled:
                call = ToolCall(
                    caller=caller,
                    sucker_id=sucker_id,
                    args=args,
                    predicted_cost=predicted_cost,
                )
                step = _make_reject_step(
                    step_id,
                    node_id,
                    call,
                    "failed",
                    f"automation capability disabled: {disabled_group}",
                )
                self.journal.write_step(task_id, arm_id, step, actor=actor)
                span.set_attribute("echo.skill.disabled", True)
                span.set_attribute(
                    "echo.skill.disabled_group",
                    disabled_group or "",
                )
                return step

            # Auto-fill predicted_cost from adaptive immunity baseline when
            # the caller doesn't supply one. This activates the pre-execute
            # anomaly detection path in TrustEngine.check() — without it,
            # compute_risk() sees zero predictions and stays in cold-start.
            if predicted_cost is None and self.immunity.adaptive is not None:
                predicted_cost = self.immunity.adaptive.predict(str(sucker_id))

            call = ToolCall(
                caller=caller,
                sucker_id=sucker_id,
                args=args,
                predicted_cost=predicted_cost,
            )
            sig = antigen_for(skill)

            def _reject_step(
                status: str,
                reason: str | None,
                *,
                immune_reason: str | None = None,
                span_attrs: dict[str, Any] | None = None,
                waiting: tuple[str, dict[str, Any]] | None = None,
            ) -> Step:
                """Shared deny epilogue for the pre-dispatch gates.

                Order is part of the contract: task metadata first (the
                approval panel reads it), then the immune journal line,
                then span attributes, then the persisted reject step.
                ``immune_reason=None`` skips the immune write for gates
                that are not immunity verdicts (e.g. injection taint).
                """
                if waiting is not None:
                    _mark_task_waiting_approval(
                        str(sucker_id),
                        waiting[0],
                        metadata_patch=waiting[1],
                    )
                if immune_reason is not None:
                    self.journal.write_immune(
                        verdict="reject",
                        signature=sig,
                        task_id=task_id,
                        arm_id=arm_id,
                        actor=actor,
                        reason=immune_reason,
                    )
                    span.set_attribute("echo.immunity.verdict", "reject")
                for attr_key, attr_value in (span_attrs or {}).items():
                    span.set_attribute(attr_key, attr_value)
                protocol_tags: list[str] = []
                if waiting is not None and bool(waiting[1].get("approval_required")):
                    protocol_tags.extend(("waiting_user", "approval_required"))
                step = _make_reject_step(
                    step_id,
                    node_id,
                    call,
                    status,
                    reason,
                    protocol_tags=protocol_tags,
                )
                self.journal.write_step(task_id, arm_id, step, actor=actor)
                return step

            # A workflow preset is trusted, per-turn policy metadata.  Enforce
            # audit read-only at the executor chokepoint so both native tool
            # use and text-protocol ReAct calls receive the same hard gate.
            # The advertised catalog is narrowed too, but this check is the
            # authoritative defence against forged/stale tool calls.
            from runtime.execution.misc.skill_policy import (
                audit_read_only_tool_denial,
            )
            from runtime.platform.process.session import current_session

            _policy_session = current_session()
            _policy_context = getattr(_policy_session, "metadata", None) or {}
            _audit_denial = audit_read_only_tool_denial(
                sucker_id,
                args,
                context=_policy_context,
            )
            if _audit_denial is not None:
                return _reject_step(
                    "failed",
                    _audit_denial,
                    span_attrs={
                        "echo.audit_read_only.blocked": True,
                        "echo.audit_read_only.tool": str(sucker_id),
                    },
                )

            governance = evaluate_execution_policy(
                build_execution_instruction(
                    instruction_id=f"{task_id}:{arm_id}:{step_id}:{sucker_id}",
                    tool_name=str(sucker_id),
                    caller=caller,
                    args=args,
                    rewritten_fields=tuple(_stripped_overrides),
                ),
                context=_current_execution_policy_context(),
                task_capability=_check_task_capability_permission(sucker_id),
                capability=_check_capability_permission(skill),
            )
            governance_payload = governance.to_dict()
            span.set_attribute("echo.governance.outcome", governance.outcome.value)
            span.set_attribute("echo.governance.gate", governance.gate)
            span.set_attribute("echo.governance.risk", governance.instruction.risk.level)
            span.set_attribute("echo.governance.taint", governance.instruction.taint)

            if not governance.may_execute:
                reject_reason = governance.reason
                if governance.gate == "injection_taint":
                    reject_reason = f"injection_taint_block: {reject_reason}"

                span_attrs: dict[str, Any] = {
                    "echo.governance.blocked": True,
                    "echo.governance.reason": governance.reason,
                }
                waiting: tuple[str, dict[str, Any]] | None = None
                immune_reason: str | None = None

                if governance.gate == "task_capability":
                    immune_reason = governance.reason
                    span_attrs["echo.task_capability.blocked"] = governance.reason
                    waiting = (
                        governance.reason,
                        {
                            "approval_required": False,
                            "approval_denied": True,
                            "approval_action": "capability_denied",
                            "capability_denied": True,
                            "capability_denial_reason": governance.reason,
                            "governance_decision": governance_payload,
                        },
                    )
                elif governance.gate == "approval":
                    immune_reason = governance.reason
                    span_attrs["echo.executor_approval.blocked"] = True
                    span_attrs["echo.executor_approval.action"] = governance.approval_action
                    waiting = (
                        governance.reason,
                        {
                            "approval_required": governance.requires_approval,
                            "approval_denied": governance.outcome is GovernanceOutcome.DENY,
                            "approval_action": governance.approval_action,
                            "executor_approval": governance_payload,
                            "governance_decision": governance_payload,
                        },
                    )
                elif governance.gate == "capability":
                    immune_reason = governance.reason
                    span_attrs["echo.capability.blocked"] = governance.reason
                elif governance.gate == "injection_taint":
                    span_attrs["echo.injection.blocked"] = governance.reason

                return _reject_step(
                    "immune_reject",
                    reject_reason,
                    immune_reason=immune_reason,
                    span_attrs=span_attrs,
                    waiting=waiting,
                )

            report = self.immunity.check(call, sig)
            self.journal.write_immune(
                verdict=report.verdict,
                signature=sig,
                task_id=task_id,
                arm_id=arm_id,
                actor=actor,
                reason=report.reason,
            )
            span.set_attribute("echo.immunity.verdict", report.verdict)

            if report.verdict == "reject":
                step = _make_reject_step(step_id, node_id, call, "immune_reject")
                self.journal.write_step(task_id, arm_id, step, actor=actor)
                # Notify monitoring · immune system blocked a call.
                # Best-effort · cannot rail the reject path.
                try:
                    from runtime.platform.process.session import (
                        current_session as _cs_im,
                    )
                    from runtime.safety.hooks.runner import (
                        dispatch_notification,
                    )

                    dispatch_notification(
                        kind="immune_reject",
                        details={
                            "task_id": str(task_id),
                            "arm_id": str(arm_id),
                            "sucker_id": str(sucker_id),
                            "trusted_source": skill.trusted_source,
                            "reason": report.reason or "",
                        },
                        session=_cs_im(),
                    )
                except (TypeError, ValueError, AttributeError):  # noqa: BLE001
                    pass
                return step

            est = predicted_cost or CostEntry(tokens_in=100, tokens_out=100, usd=0.001)
            try:
                reservation = budget.reserve(est)
            except InsufficientBudget as e:
                step = _make_reject_step(step_id, node_id, call, "circuit_broken", str(e))
                self.journal.write_step(task_id, arm_id, step, actor=actor)
                self.journal.write_budget(
                    "budget_squirt",
                    task_id=task_id,
                    actor=actor,
                    reason=str(e),
                )
                # Community hook notification · circuit-breaker fired.
                # Monitoring / alerting subscribers pick this up to
                # ring pages without having to tail the journal.
                try:
                    from runtime.platform.process.session import (
                        current_session as _cs_sq,
                    )
                    from runtime.safety.hooks.runner import (
                        dispatch_notification,
                    )

                    dispatch_notification(
                        kind="budget_squirt",
                        details={
                            "task_id": str(task_id),
                            "arm_id": str(arm_id),
                            "sucker_id": str(sucker_id),
                            "reason": str(e),
                            "usd_spent": budget.usd_spent,
                            "tokens_spent": budget.tokens_spent,
                        },
                        session=_cs_sq(),
                    )
                except (TypeError, ValueError, AttributeError):  # noqa: BLE001
                    pass
                return step

            pre_result: ExecutionResult | None = None
            _handler_executed = False
            _receipt_rewrite_source: str | None = None
            if self.hooks is not None:
                from runtime.core.nerves.hooks import HookContext, HookError

                pre_ctx = HookContext(
                    phase="pre",
                    task_id=task_id,
                    arm_id=arm_id,
                    sucker_id=sucker_id,
                    node_id=node_id,
                    step_id=step_id,
                    call=call,
                    result=None,
                )
                try:
                    pre_out = self.hooks.run_pre(pre_ctx)
                    if pre_out is not None and pre_out.replace_with is not None:
                        pre_result = pre_out.replace_with
                        _receipt_rewrite_source = "legacy_pre_hook_replaced"
                except HookError as he:
                    budget.commit(reservation, CostEntry())
                    self.journal.write_budget("budget_commit", task_id=task_id, actor=actor)
                    step = _make_reject_step(
                        step_id,
                        node_id,
                        call,
                        "failed",
                        reason=f"hook_block: {he.reason}",
                    )
                    self.journal.write_step(task_id, arm_id, step, actor=actor)
                    span.set_attribute("echo.hook.blocked", he.reason)
                    return step

            pre_content: str | None = None
            pre_exists = False
            if "file" in (skill.affinity or []):
                _pre_path = _extract_path(args, None)
                if _pre_path:
                    with contextlib.suppress(OSError, ValueError):
                        pre_exists = Path(_pre_path).is_file()
                pre_content = _try_read_pre_content(_pre_path)
            t0 = time.monotonic()

            # 6a. PUBLIC PreToolUse hook · pre/post tool lifecycle
            # event · community handlers can cancel or rewrite args here.
            # Runs BEFORE legacy self.hooks pre_result branch so a
            # community hook can still override · returns None if no
            # handler registered (cheap no-op).
            hook_cancel: str | None = None
            try:
                from runtime.platform.process.session import current_session as _cs
                from runtime.safety.hooks.runner import dispatch_pre_tool

                pre_decision = dispatch_pre_tool(
                    sucker_id=str(sucker_id),
                    args=args,
                    caller=caller,
                    session=_cs(),
                )
                if pre_decision.cancelled:
                    hook_cancel = pre_decision.reason or "pre_tool_hook_cancelled"
                elif pre_decision.modified_args is not None:
                    args = pre_decision.modified_args
            except (TypeError, ValueError, RuntimeError):  # noqa: BLE001 — pre-tool hook best-effort
                pass

            if hook_cancel is not None:
                budget.commit(reservation, CostEntry())
                self.journal.write_budget(
                    "budget_commit",
                    task_id=task_id,
                    actor=actor,
                )
                step = _make_reject_step(
                    step_id,
                    node_id,
                    call,
                    "failed",
                    reason=f"hook_cancel: {hook_cancel}",
                )
                self.journal.write_step(task_id, arm_id, step, actor=actor)
                span.set_attribute("echo.hook.cancelled", hook_cancel)
                return step

            _effect_resolution: EffectResolution | None = None
            _effect_receipt_index = self._current_effect_receipts()
            _lease_target: Path | None = None
            if pre_result is not None:
                output = pre_result.output
                status = pre_result.status
                error_type = pre_result.error_type
                stderr_tags = list(pre_result.stderr_tags) + ["pre_hook_replaced"]
            else:
                try:
                    # Session injection + workspace-scope defaulting and
                    # enforcement. Raises PermissionError inside this try
                    # so scope escapes map to the same except branch as
                    # every other file-safety denial.
                    args = _prepare_scoped_args(skill, sucker_id, args)
                    _write_scope_reason = _declared_write_scope_violation(
                        skill,
                        sucker_id,
                        args,
                    )
                    if _write_scope_reason is not None:
                        raise PermissionError(_write_scope_reason)
                    _read_guard_reason = _read_before_write_violation(
                        str(sucker_id),
                        args,
                    )
                    if _read_guard_reason is not None:
                        raise ReadBeforeWriteRequired(_read_guard_reason)
                    # Credential-file denylist. Write scope decides
                    # *where* a skill may write; this decides *what it may
                    # never name* — .env, id_rsa, ~/.ssh/*, /etc/shadow,
                    # etc. — regardless of scope. A sandbox-scoped write to
                    # a `.env` is in-scope yet still a credential write, so
                    # this layer is complementary, not redundant.
                    _fs_target = file_safety_target(skill, args)
                    if _fs_target is not None:
                        _fs_verdict = check_file_write(_fs_target)
                        if not _fs_verdict.allow:
                            raise PermissionError(
                                f"write skill {sucker_id!r} blocked by "
                                f"file-safety: {_fs_verdict.reason}"
                            )
                    _effect_side = is_side_effecting(skill.affinity)
                    if caller == "react_loop" and _effect_receipt_index is not None:
                        _effect_resolution = _effect_receipt_index.begin(
                            task_id=task_id,
                            step_id=step_id,
                            sucker_id=sucker_id,
                            args=args,
                            side_effecting=_effect_side,
                        )
                        if _effect_resolution.kind != "execute":
                            budget.commit(reservation, CostEntry())
                            self.journal.write_budget(
                                "budget_commit",
                                task_id=task_id,
                                actor=actor,
                                cost=CostEntry(),
                            )
                            if _effect_resolution.kind == "replay":
                                assert _effect_resolution.step is not None
                                replayed = _effect_resolution.step
                                self.journal.write_step(task_id, arm_id, replayed, actor=actor)
                                span.set_attribute("echo.effect.replayed", True)
                                return replayed
                            uncertain = indeterminate_step(
                                step_id=step_id,
                                node_id=node_id,
                                call=call,
                                effect_key=_effect_resolution.key,
                                fencing_token=_effect_resolution.fencing_token,
                                reason=_effect_resolution.reason,
                            )
                            self.journal.write_step(task_id, arm_id, uncertain, actor=actor)
                            span.set_attribute("echo.effect.indeterminate", True)
                            return uncertain
                    _lease_target = _file_write_lease_target(skill, args)
                    if _lease_target is not None:
                        from runtime.platform.process.session import current_session

                        _write_session = current_session()
                        verify_file_unchanged_since_read(
                            _write_session,
                            _lease_target,
                        )
                        acquire_file_write_lease(
                            _write_session,
                            _lease_target,
                            owner=_file_write_lease_owner(
                                actor=actor,
                                arm_id=arm_id,
                                caller=caller,
                            ),
                        )
                    if _effect_resolution is not None:
                        assert _effect_receipt_index is not None
                        intent_event = self.journal.write_tool_effect_intent(
                            task_id,
                            arm_id,
                            effect_key=_effect_resolution.key,
                            call_id=str(call.call_id),
                            step_id=step_id,
                            node_id=node_id,
                            sucker_id=str(sucker_id),
                            args_fingerprint=_effect_resolution.args_fingerprint,
                            side_effecting=_effect_side,
                            actor=actor,
                        )
                        _effect_receipt_index.mark_intent(
                            intent_event,
                            _effect_resolution,
                        )

                    # Bind the runtime TrustEngine as the ambient engine for
                    # the handler call. A meta-skill (use_capability / forged
                    # composite) dispatches to an inner handler DIRECTLY,
                    # bypassing this execute_step; binding here lets that
                    # nested dispatch run the SAME immunity policy via
                    # skill_gate.gate_inner_dispatch.
                    def _invoke_captured_handler(**handler_args: Any) -> Any:
                        nonlocal _handler_executed
                        _handler_executed = True
                        return skill.handler(**handler_args)

                    with use_trust_engine(self.immunity):
                        output, retry_tags = _call_handler_with_transient_retry(
                            _invoke_captured_handler,
                            args,
                            allow_retry=(caller != "react_loop" or not _effect_side),
                            timeout_s=getattr(skill, "timeout_s", None),
                        )
                    status: ExecutionStatus = "success"
                    error_type: str | None = None
                    stderr_tags: list[str] = retry_tags
                    # Taint the turn (chokepoint) if this tool's output is
                    # external/untrusted and carries injection markers, so a
                    # LATER risky tool on ANY path is gated. Setting it here
                    # (not only in react_loop) covers the agentic-fallback
                    # and subagent paths that never reach react_loop's
                    # observation-wrap sites.
                    if is_untrusted_tool(
                        str(sucker_id),
                        list(skill.affinity or []),
                        args if isinstance(args, dict) else None,
                    ):
                        _inj_scan = scan_for_injection(str(output))
                        if _inj_scan.flagged:
                            mark_injection_taint(_inj_scan.severity)
                    # ── Structured output validation ──────
                    _output_schema = getattr(skill, "output_schema", None)
                    if (
                        _output_schema
                        and isinstance(_output_schema, dict)
                        and isinstance(output, dict)
                        and status == "success"
                    ):
                        try:
                            _validate_output(output, _output_schema)
                        except ValueError as ve:
                            stderr_tags.append("schema_mismatch")
                            span.set_attribute(
                                "echo.output_schema.error",
                                str(ve),
                            )
                except ReadBeforeWriteRequired as e:
                    output = {"error": str(e)}
                    status = "failed"
                    error_type = "read_before_write_required"
                    stderr_tags = [error_type, str(e)]
                except FileWriteLeaseConflict as e:
                    output = {"error": str(e)}
                    status = "failed"
                    error_type = "FileWriteLeaseConflict"
                    stderr_tags = ["file_write_lease_conflict", str(e)]
                except WorkspaceContentDriftConflict as e:
                    output = {"error": str(e)}
                    status = "failed"
                    error_type = "WorkspaceContentDriftConflict"
                    stderr_tags = ["workspace_content_drift", str(e)]
                except TimeoutError:
                    output = None
                    status = "timeout"
                    error_type = "timeout"
                    stderr_tags = []
                except PermissionError as e:
                    # Write-scope escapes and file-safety denials both
                    # surface here. Preserve the reason in output/tags so
                    # the block is attributable (the catch-all below would
                    # drop the message, keeping only the type name).
                    output = {"error": str(e)}
                    status = "failed"
                    error_type = "PermissionError"
                    stderr_tags = ["permission_denied", str(e)]
                except Exception as e:  # noqa: BLE001 - we want to capture arbitrary handler errors
                    output = None
                    status = "failed"
                    error_type = type(e).__name__
                    stderr_tags = [error_type]

            # Cooperative handlers commonly observe the ambient cancellation
            # token and return ``{"error": token.reason}`` instead of raising.
            # Cancellation is a lifecycle terminal, not a semantic tool error;
            # canonicalize it before the generic structured-error check so the
            # persisted StepEvent, UI receipt, and trajectory agree.  Preserve
            # already-specific policy/timeout failures by only rewriting a
            # successful return or the explicit cancellation exception types.
            from runtime.safety.approval.cancellation import (  # noqa: PLC0415
                current_cancellation_token,
            )

            _ambient_execution_cancelled = current_cancellation_token().is_cancelled
            if (status == "success" and _ambient_execution_cancelled) or error_type in {
                "OperationCancelled",
                "CancelledError",
            }:
                status = "failed"
                error_type = "cancelled"
                if "cancelled" not in stderr_tags:
                    stderr_tags.append("cancelled")
                span.set_attribute("echo.execution.cancelled", True)

            # Some handlers report a semantic failure as a normal return
            # value (for example ``{"ok": False}``, a non-zero
            # ``exit_code``, or ``{"status": "failed"}``). Canonicalize
            # that signal at the executor chokepoint so every caller and the
            # persisted StepEvent agree that the step failed. The explicit
            # success guard is important: policy denials, cancellations,
            # timeouts, hook failures, and raised handler errors already carry
            # a more specific terminal status and must never be relabelled.
            if status == "success" and output_signals_error(output):
                status = "failed"
                error_type = "semantic_error"
                stderr_tags.append("semantic_error")
                span.set_attribute("echo.execution.semantic_error", True)

            # Only a handler result that remains canonically successful may
            # update read-before-write state or establish a write snapshot.
            # A pre-hook replacement never executed the handler and therefore
            # must not create either side effect.
            if _handler_executed and status == "success":
                if _lease_target is not None and not (
                    isinstance(output, dict) and output.get("error")
                ):
                    record_file_write_snapshot(
                        _write_session,
                        _lease_target,
                    )
                _record_successful_read(str(sucker_id), args, output)

            latency_ms = (time.monotonic() - t0) * 1000

            #
            # Real tokens take priority over the ``est`` placeholder when
            # the skill output surfaces them. Convention: an LLM-coupled
            # skill returns a dict with ``"cost": {input_tokens, output_tokens}``
            # (e.g. ``deep_evolve``) OR ``"meta": {input_tokens, output_tokens}``
            # (e.g. ``deep_reflect`` / ``learn_skill_from_text``). If
            # neither key is present (atomic skills · tool_use handlers
            # that don't call LLMs) we fall back to the ``est`` values,
            # which for atomic skills are close to zero anyway.
            _real_in, _real_out = _extract_token_usage(output)
            if _real_in > 0 or _real_out > 0:
                actual_cost = CostEntry(
                    tokens_in=_real_in,
                    tokens_out=_real_out,
                    usd=est.usd,  # usd estimation still from predicted_cost
                    latency_ms=latency_ms,
                )
            else:
                actual_cost = CostEntry(
                    tokens_in=est.tokens_in,
                    tokens_out=est.tokens_out,
                    usd=est.usd,
                    latency_ms=latency_ms,
                )
            budget.commit(reservation, actual_cost)
            # Feed the adaptive-immunity baseline (protocols/immunity.md
            # Execute-time learning loop). No-op unless the adaptive tier
            # is enabled; builds the per-sucker latency/token baseline
            # from observed cost. Best-effort — must never break a
            # successful tool result.
            learn = getattr(self.immunity, "learn", None)
            if callable(learn):
                with contextlib.suppress(Exception):
                    learn(
                        call,
                        latency_ms=latency_ms,
                        tokens=float(actual_cost.tokens_in + actual_cost.tokens_out),
                    )
            self.journal.write_budget(
                "budget_commit",
                task_id=task_id,
                actor=actor,
                cost=actual_cost,
            )

            # Notify community hooks when the budget crosses 80% / 95%
            # utilization · at most once per level per Budget. Best-
            # effort · dispatch exceptions can't rail the commit path.
            _notify_budget_warnings(budget, task_id, arm_id)

            result = ExecutionResult(
                call_id=call.call_id,
                status=status,
                output=_safe_repr(output),
                output_hash=_hash_output(output),
                error_type=error_type,
                stderr_tags=stderr_tags,
                cost=actual_cost,
            )

            # 8a. PUBLIC PostToolUse hook · community handlers can
            # rewrite the output (e.g. scrub secrets before it reaches
            # the planner). Cancel is not meaningful post-hoc · any
            # side effect already happened · we honor modified_output
            # only.
            try:
                from runtime.platform.process.session import current_session as _cs2
                from runtime.safety.hooks.runner import dispatch_post_tool

                post_decision = dispatch_post_tool(
                    sucker_id=str(sucker_id),
                    args=args,
                    output=output,
                    success=(status == "success"),
                    session=_cs2(),
                )
                if post_decision.modified_output is not None:
                    _receipt_rewrite_source = "public_post_tool_rewritten"
                    # re-hash · keep ExecutionResult self-consistent
                    result = ExecutionResult(
                        call_id=call.call_id,
                        status=status,
                        output=_safe_repr(post_decision.modified_output),
                        output_hash=_hash_output(post_decision.modified_output),
                        error_type=error_type,
                        stderr_tags=stderr_tags + ["post_hook_rewrote"],
                        cost=actual_cost,
                    )
            except (TypeError, ValueError, RuntimeError):  # noqa: BLE001
                pass

            # 8b. PUBLIC PostToolUseFailure hook · fires when the tool did
            # not succeed. Notification-type: the failure already happened;
            # handlers get a chance to observe (metrics, alerts) before the
            # error propagates to the planner.
            if status != "success":
                try:
                    from runtime.platform.process.session import current_session as _cs3
                    from runtime.safety.hooks.runner import dispatch_post_tool_failure

                    dispatch_post_tool_failure(
                        sucker_id=str(sucker_id),
                        args=args,
                        error=(result.error_type if isinstance(result.error_type, str) else ""),
                        session=_cs3(),
                    )
                except (TypeError, ValueError, RuntimeError):  # noqa: BLE001
                    pass

            # 8c. Post-write diagnostics · auto-trigger ruff/eslint and
            # attach a targeted regression matrix after successful writes.
            # Output is appended to ``result.output`` (frozen model ·
            # re-built via ``model_copy``) so the model sees lint feedback
            # and knows which verification commands fit the changed file.
            # Best-effort · hook helpers never raise into the executor.
            if status == "success" and str(sucker_id) in _READ_BEFORE_WRITE_TOOLS | {
                "append_text_file",
            }:
                try:
                    from runtime.safety.hooks.tool_edge_hooks import (
                        post_write_diagnostics,
                        post_write_regression_matrix,
                    )

                    workspace_path = _resolve_workspace_for_diagnostics(args)
                    if workspace_path is not None:
                        diag = post_write_diagnostics(
                            str(sucker_id),
                            args,
                            output
                            if isinstance(output, dict)
                            else {"path": _extract_path(args, output)},
                            workspace_path=workspace_path,
                        )
                        matrix = post_write_regression_matrix(
                            str(sucker_id),
                            args,
                            output
                            if isinstance(output, dict)
                            else {"path": _extract_path(args, output)},
                            workspace_path=workspace_path,
                        )
                        if diag:
                            new_output_text = (
                                (result.output or "")
                                + ("\n\n" if result.output else "")
                                + "[post-write diagnostics]\n"
                                + diag
                            )
                            result = result.model_copy(
                                update={
                                    "output": new_output_text,
                                    "stderr_tags": list(result.stderr_tags) + ["post_diagnostics"],
                                }
                            )
                        if matrix:
                            new_output_text = (
                                (result.output or "")
                                + ("\n\n" if result.output else "")
                                + "[regression matrix]\n"
                                + matrix
                            )
                            result = result.model_copy(
                                update={
                                    "output": new_output_text,
                                    "stderr_tags": list(result.stderr_tags) + ["regression_matrix"],
                                }
                            )
                except (TypeError, ValueError, RuntimeError):  # noqa: BLE001
                    pass

            if self.hooks is not None:
                from runtime.core.nerves.hooks import HookContext

                post_ctx = HookContext(
                    phase="post",
                    task_id=task_id,
                    arm_id=arm_id,
                    sucker_id=sucker_id,
                    node_id=node_id,
                    step_id=step_id,
                    call=call,
                    result=result,
                )
                post_out = self.hooks.run_post(post_ctx)
                if post_out is not None and post_out.replace_with is not None:
                    result = post_out.replace_with
                    _receipt_rewrite_source = "legacy_post_hook_replaced"
            # Stamp provenance only after all post hooks have finished, but
            # derive it from the exact Skill object captured before dispatch.
            # This survives a concurrent registry replacement without either
            # trusting the replacement or accidentally executing it.
            _trusted_execution, _execution_source = _canonical_execution_provenance(
                skill,
                handler_executed=_handler_executed,
                receipt_rewrite_source=_receipt_rewrite_source,
            )
            result = result.model_copy(
                update={
                    "trusted_execution": _trusted_execution,
                    "execution_source": _execution_source,
                    "effect_receipt": build_server_effect_receipt(
                        skill=skill,
                        call_id=call.call_id,
                        handler_executed=_handler_executed,
                        result_status=str(result.status),
                        resolution=_effect_resolution,
                        receipt_rewrite_source=_receipt_rewrite_source,
                    ),
                }
            )
            step = Step(
                step_id=step_id,
                node_id=node_id,
                action=call,
                result=result,
                immune_verdict=report.verdict,
            )
            # Commit the cross-process receipt as soon as the complete Step
            # exists.  The journal append and secondary diagnostics below can
            # be replayed; the external side effect cannot.  Persisting the
            # receipt first closes the dangerous handler-return → journal
            # window where a process crash used to leave the result unknown.
            if _effect_resolution is not None and _effect_receipt_index is not None:
                _effect_receipt_index.finish(_effect_resolution, step)

            # Beak-level metrics. Increment counters + record latency
            # so the /metrics endpoint reflects skill-execution shape
            # without requiring a separate emitter on every call site.
            # Best-effort: a metrics-registry import failure must NOT
            # break execution.
            _emit_skill_metrics(sucker_id, status, latency_ms, error_type)

            # Session-level cumulative budget tracking. Records the
            # actual cost of this step against the active session's
            # ledger so cross-task / cross-arm aggregates are visible
            # at /api/budget/sessions and warning callbacks fire at
            # 80% / 95% of the configured ceiling.
            #
            # ``Session.thread_id`` is the canonical per-chat key.
            # Fall back to ``turn_id`` so anonymous / legacy flows
            # still bucket coherently.
            #
            # Best-effort: any failure (no active session, no tracker
            # configured, BudgetExceeded raised by ceiling enforcement)
            # propagates only when ``BudgetExceeded`` was specifically
            # raised; otherwise we swallow so a misconfigured ledger
            # can't break execution.
            _record_session_budget(self._budget_tracker, actual_cost)

            affinity = set(skill.affinity or [])
            if (
                status == "success"
                and "file" in affinity
                and affinity
                & {
                    "write",
                    "edit",
                    "delete",
                    "dangerous",
                }
            ):
                with contextlib.suppress(Exception):
                    _emit_file_op_from_step(
                        journal=self.journal,
                        skill=skill,
                        args=args,
                        output=output,
                        task_id=task_id,
                        arm_id=arm_id,
                        actor=actor,
                        pre_content=pre_content,
                        pre_exists=pre_exists,
                    )
                    if isinstance(output, dict) and output.get("diff_preview"):
                        result = result.model_copy(
                            update={
                                "output": _safe_repr(output),
                                "output_hash": _hash_output(output),
                            }
                        )
                        step = step.model_copy(update={"result": result})

            self.journal.write_step(task_id, arm_id, step, actor=actor)

            span.set_attribute("echo.execution.status", status)
            span.set_attribute("echo.execution.latency_ms", latency_ms)
            return step
