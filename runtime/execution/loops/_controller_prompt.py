from __future__ import annotations

from typing import Any

from runtime.execution.loops._controller_helpers import (
    _attempt_exception_feedback,
    _attempt_exception_repairable,
    _verifier_feedback,
)
from runtime.execution.loops.learning import (
    build_loop_repair_candidate_spec,
    build_loop_run_review,
)
from runtime.execution.loops.models import LoopRun, LoopRunStatus
from runtime.execution.loops.recovery import build_loop_run_resume_prompt
from runtime.safety.auth.scope import TenantScope, tenant_scoped_path
from runtime.safety.evolution.candidate_registry import (
    CandidateRegistry,
    CandidateRegistryError,
)


class LoopControllerPromptMixin:
    def _build_attempt_prompt(self, run: LoopRun) -> str:
        if not run.attempts:
            resume_prompt = self._build_resume_prompt(run)
            return resume_prompt or run.goal
        latest = run.attempts[-1]
        repair = _verifier_feedback(latest.verifier_result)
        if (
            not repair
            and latest.verifier_result is None
            and latest.status == "failed"
            and str(latest.error or "").strip()
        ):
            terminated_reason = str(latest.terminated_reason or "").strip()
            category = (
                terminated_reason.removeprefix("exception:")
                if terminated_reason.startswith("exception:")
                else "runner_indeterminate_effect_blocker"
            )
            if _attempt_exception_repairable(category):
                repair = _attempt_exception_feedback(latest.error, category=category)
        if not repair:
            return run.goal
        return f"{run.goal}\n\n{repair}"

    def _build_resume_prompt(self, run: LoopRun) -> str:
        if not run.resume_checkpoint_id or not run.parent_run_id:
            return ""
        source = self.store.get(run.parent_run_id)
        if source is None:
            return ""
        if source.status not in {
            LoopRunStatus.FAILED,
            LoopRunStatus.CANCELLED,
            LoopRunStatus.INTERRUPTED,
        }:
            return ""
        return build_loop_run_resume_prompt(
            source,
            goal=run.goal,
            checkpoint_id=run.resume_checkpoint_id,
        )

    def _finalize_learning(self, run: LoopRun) -> LoopRun:
        if not self._supervisor_heartbeat(run.run_id):
            return self._latest_run(run.run_id)
        existing = self._existing_terminal_review(run)
        if existing is not None:
            checkpoint_id = self._review_trace_checkpoint_id(existing.last_review)
            if not self._supervisor_transition(existing, checkpoint_id=checkpoint_id):
                return self._latest_run(run.run_id)
            return existing
        review = build_loop_run_review(run)
        trace_checkpoint_id = self._record_trace_terminal_artifacts(run)
        if trace_checkpoint_id is not None:
            review = self._link_trace_checkpoint(review, trace_checkpoint_id)
        if not self._supervisor_transition(run, checkpoint_id=trace_checkpoint_id):
            return self._latest_run(run.run_id)
        queue_result: dict[str, Any] | None = None
        if self.review_queue is not None and (
            review.get("learning_candidates") or review.get("backlog_candidates")
        ):
            scope = None
            if run.tenant_id and run.owner_id:
                scope = TenantScope(tenant_id=run.tenant_id, actor_id=run.owner_id)
            queue_result = self.review_queue.add_from_task_run_review(review, scope=scope)
        evolution_candidate_result = self._record_typed_repair_candidate(run)
        return self.store.mutate(
            run.run_id,
            lambda current, review=review, queue_result=queue_result, evolution_candidate_result=evolution_candidate_result: (
                current.model_copy(
                    update={
                        "last_review": review,
                        "last_review_queue_result": queue_result,
                        "last_evolution_candidate_result": evolution_candidate_result,
                    }
                )
            ),
        )

    def _record_typed_repair_candidate(self, run: LoopRun) -> dict[str, Any] | None:
        spec = build_loop_repair_candidate_spec(run)
        registry_base = getattr(self, "candidate_registry_path", None)
        if spec is None or registry_base is None:
            return None
        if bool(run.tenant_id) != bool(run.owner_id):
            return {
                "status": "not_recorded",
                "reason": "candidate tenant and owner provenance must be complete",
                "automatic_activation": False,
            }
        scope = (
            TenantScope(tenant_id=run.tenant_id, actor_id=run.owner_id)
            if run.tenant_id and run.owner_id
            else None
        )
        registry_path = tenant_scoped_path(registry_base, scope)
        registry = CandidateRegistry(registry_path, tenant_scope=scope)
        try:
            candidate = registry.propose(
                **spec,
                tenant_id=scope.tenant_id if scope is not None else None,
                owner_actor_id=scope.actor_id if scope is not None else None,
            )
            evidence_run_ids = [
                str(item)
                for item in candidate.metadata.get("evidence_run_ids", [])
                if str(item).strip()
            ]
            if run.run_id not in evidence_run_ids:
                summary = run.attempts[-1].effect_summary
                evidence_run_ids.append(run.run_id)
                candidate = registry.record_evidence(
                    candidate.candidate_id,
                    hard_gate_results={
                        "server_owned_effect_receipts": True,
                        "local_effects_only": True,
                        "independent_verifier_passed": True,
                        "repair_tool_confinement": True,
                        # An independently replayed fixture is intentionally
                        # still required before an operator can validate this
                        # candidate and advance it toward Shadow.
                        "independent_replay": False,
                    },
                    metric_vector={
                        "verified_source_runs": float(len(evidence_run_ids)),
                        "latest_attempt_count": float(len(run.attempts)),
                        "latest_workspace_write_count": float(
                            int(summary.get("workspace_write_effect_count") or 0)
                        ),
                    },
                    metadata={
                        "evidence_run_ids": evidence_run_ids[-20:],
                        "awaiting_gates": ["independent_replay"],
                        "next_stage": "independent_replay",
                    },
                )
        except (CandidateRegistryError, OSError, TypeError, ValueError) as exc:
            return {
                "status": "not_recorded",
                "reason": f"{type(exc).__name__}: candidate registry unavailable",
                "automatic_activation": False,
            }
        return {
            "candidate_id": candidate.candidate_id,
            "status": candidate.status.value,
            "gene_type": candidate.gene_type.value,
            "scope": candidate.scope,
            "automatic_activation": False,
            "hard_gate_passed": candidate.hard_gate_passed,
            "awaiting_gates": list(candidate.metadata.get("awaiting_gates") or []),
            "next_stage": str(candidate.metadata.get("next_stage") or "independent_replay"),
        }

    def _existing_terminal_review(self, run: LoopRun) -> LoopRun | None:
        current = self.store.get(run.run_id)
        if current is None:
            raise KeyError(run.run_id)
        review = current.last_review if isinstance(current.last_review, dict) else None
        if review is None:
            return None
        if str(review.get("status") or "") != run.status.value:
            return None
        if current.status != run.status:
            return None
        return current

    @staticmethod
    def _review_trace_checkpoint_id(review: dict[str, Any] | None) -> int | None:
        if not isinstance(review, dict):
            return None
        summary = review.get("summary") if isinstance(review.get("summary"), dict) else {}
        checkpoint_id = summary.get("trace_checkpoint_id")
        if checkpoint_id is None:
            resume = review.get("resume") if isinstance(review.get("resume"), dict) else {}
            latest = (
                resume.get("latest_checkpoint")
                if isinstance(resume.get("latest_checkpoint"), dict)
                else {}
            )
            checkpoint_id = latest.get("trace_checkpoint_id")
        try:
            return int(checkpoint_id) if checkpoint_id is not None else None
        except (TypeError, ValueError):
            return None
