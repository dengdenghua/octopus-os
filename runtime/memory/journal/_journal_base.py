from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal, cast

from runtime.platform.models import (
    AntigenSignature,
    ArmId,
    CostEntry,
    ImmuneVerdict,
    Step,
    TaskId,
    Trajectory,
)
from runtime.safety.auth.scope import TenantScope

from ._journal_models import (
    AssistantChunkEvent,
    BudgetBreakerResetEvent,
    BudgetEvent,
    CurriculumGoalDecisionEvent,
    FileOpEvent,
    GoalChangeEvent,
    ImmuneEvent,
    JournalEvent,
    JournalEventType,
    McpProposalDecisionEvent,
    NodeStartedEvent,
    PreviewRefreshEvent,
    ProtocolDriftDecisionEvent,
    ReactCheckpointEvent,
    ReflexHitEvent,
    SkillProposalDecisionEvent,
    StepEvent,
    TaskCheckpointEvent,
    TaskPausedEvent,
    TaskResumedEvent,
    TaskStartedEvent,
    TokenUsageEvent,
    ToolEffectIntentEvent,
    ToolEffectReconciliationEvent,
    TrajectoryEvent,
    UserMessageEvent,
)
from .journal_context import (
    current_agent_id,
    current_conversation_id,
    current_owner_actor_id,
    current_tenant_id,
)


class Journal:
    def _apply_context(self, event: JournalEvent) -> JournalEvent:
        updates: dict[str, Any] = {}
        if event.tenant_id is None and current_tenant_id() is not None:
            updates["tenant_id"] = current_tenant_id()
        if event.owner_actor_id is None and current_owner_actor_id() is not None:
            updates["owner_actor_id"] = current_owner_actor_id()
        return event.model_copy(update=updates) if updates else event

    def write(self, event: JournalEvent) -> None:
        raise NotImplementedError

    def canonicalize_event(self, event: JournalEvent) -> JournalEvent:
        """Return the exact event representation this backend will persist.

        The base contract applies server-owned journal context. File-backed
        implementations may additionally apply storage transformations such
        as secret redaction. Streaming wrappers use the returned value so live
        subscribers observe the same representation as durable readers.
        """

        return self._apply_context(event)

    def write_canonical(self, event: JournalEvent) -> JournalEvent:
        """Write one event and return the representation accepted by storage.

        This combined hook prevents streaming wrappers from reconstructing a
        committed event with a post-write read (which can race rotation or
        replay an older row). Backends with non-trivial serialization should
        override it so canonicalization happens exactly once.
        """

        canonical = self.canonicalize_event(event)
        self.write(canonical)
        return canonical

    def write_trajectory_once(self, event: TrajectoryEvent) -> bool:
        """Atomically append a trajectory when its durable key is absent.

        Implementations must treat ``(task_id, strategy_id, tenant_id,
        owner_actor_id)`` as the idempotency key and cover the absence check
        plus append with the same storage transaction.  Returning ``False``
        means that key was already present.  Backends which cannot provide
        that guarantee deliberately fail closed instead of falling back to a
        racy ``read_by_task`` followed by ``write``.
        """

        del event
        raise NotImplementedError("journal backend has no atomic trajectory append")

    def canonicalize_trajectory_event(self, event: TrajectoryEvent) -> TrajectoryEvent:
        """Return the exact event representation this backend will persist.

        Streaming wrappers use this hook so subscribers see the same
        server-owned scope as durable storage. File-backed journals may also
        apply their configured redactor here.
        """

        return cast(TrajectoryEvent, self.canonicalize_event(event))

    def write_trajectory_once_canonical(
        self,
        event: TrajectoryEvent,
    ) -> tuple[bool, TrajectoryEvent]:
        """Atomically write and return the backend's canonical event.

        The default implementation preserves compatibility for third-party
        journals. Concrete backends can override it to avoid applying a
        non-idempotent canonicalizer twice.
        """

        canonical = self.canonicalize_trajectory_event(event)
        return self.write_trajectory_once(canonical), canonical

    def read_all(self, *, scope: TenantScope | None = None) -> list[JournalEvent]:
        raise NotImplementedError

    def subscribe(self, callback: Callable[[JournalEvent], None]) -> Callable[[], None]:
        """Default no-op pub/sub. Returns an unsubscribe callable.

        The base Journal is append-only and has no live subscribers —
        callers that need real-time event fan-out must wrap it in
        ``StreamingJournal`` (runtime/sensing/gateway/streaming_journal.py),
        which overrides this method to broadcast ``write`` events to
        registered callbacks.

        Returning a no-op unsubscribe here (rather than raising
        ``AttributeError``) lets consumers like ``TaskProgressTracker``
        be constructed against any Journal subclass without coupling
        to the streaming wrapper — they simply won't receive live
        events until wrapped in ``StreamingJournal``.
        """
        del callback  # unused — base journal has no fan-out path
        return lambda: None

    def _visible(self, event: JournalEvent, scope: TenantScope | None) -> bool:
        if scope is None or scope.allow_cross_tenant:
            return True
        return bool(
            event.tenant_id
            and event.owner_actor_id
            and event.tenant_id == scope.tenant_id
            and event.owner_actor_id == scope.actor_id
        )

    def read_by_task(
        self, task_id: TaskId, *, scope: TenantScope | None = None
    ) -> list[JournalEvent]:
        return [e for e in self.read_all(scope=scope) if e.task_id == task_id]

    def read_by_type(
        self, event_type: JournalEventType, *, scope: TenantScope | None = None
    ) -> list[JournalEvent]:
        return [e for e in self.read_all(scope=scope) if e.event_type == event_type]

    def read_since(self, ts: datetime) -> list[JournalEvent]:
        return [e for e in self.read_all() if e.ts >= ts]

    def read_by_actor(self, actor: str) -> list[JournalEvent]:
        return [e for e in self.read_all() if e.actor == actor]

    def read_by_agent(self, agent_id: str) -> list[JournalEvent]:
        return [e for e in self.read_all() if e.agent_id == agent_id]

    def read_by_session(self, session_id: str) -> list[JournalEvent]:
        """Events for one sub-agent session (audit P-04).

        The base implementation filters the full log; a backend that keeps a
        per-session index can override this to read only the session's rows.
        """
        return [e for e in self.read_all() if str(getattr(e, "session_id", "") or "") == session_id]

    def file_transaction_summary(
        self,
        *,
        task_id: TaskId | str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        from runtime.memory.runtime_state.file_transactions import summarize_file_ops

        events = self.read_by_type("file_op")
        if task_id is not None:
            events = [e for e in events if str(e.task_id or "") == str(task_id)]
        if actor is not None:
            events = [e for e in events if e.actor == actor]
        return summarize_file_ops(events).to_dict()

    def read_by_conversation(
        self,
        conversation_id: str,
    ) -> list[JournalEvent]:
        return [e for e in self.read_all() if e.conversation_id == conversation_id]

    def list_conversations(
        self,
        *,
        agent_id: str | None = None,
    ) -> list[str]:
        seen: dict[str, datetime] = {}
        for e in self.read_all():
            cid = e.conversation_id
            if cid is None:
                continue
            if agent_id is not None and e.agent_id != agent_id:
                continue
            if cid not in seen or e.ts < seen[cid]:
                seen[cid] = e.ts
        return [cid for cid, _ts in sorted(seen.items(), key=lambda kv: kv[1])]

    def export_trajectories(
        self,
        *,
        task_id: TaskId | None = None,
        format: str = "sharegpt",  # noqa: A002
    ) -> list[dict[str, Any]]:
        """Export execution trajectories as training data.

        Converts ``StepEvent`` + ``TrajectoryEvent`` sequences into
        a format suitable for LLM fine-tuning.

        Parameters
        ----------
        task_id:
            If given, export only events for this task. Otherwise
            export all tasks.
        format:
            ``"sharegpt"`` (default) — ShareGPT-compatible JSONL
            conversations format commonly used by chat-model
            fine-tuning pipelines::

                {
                  "conversations": [
                    {"from": "human", "value": "<goal>"},
                    {"from": "gpt",   "value": "<thought>"},
                    {"from": "tool",  "value": "<tool_call>"},
                    {"from": "tool_result", "value": "<result>"},
                    ...
                  ],
                  "task_id": "...",
                  "outcome": "pass|fail|pass_degraded|unknown",
                  "total_cost_tokens": 0,
                }

            ``"raw"`` — list of dicts with all event fields.

        Returns
        -------
        list[dict]
            One entry per task (ShareGPT) or one entry per event
            (raw).
        """
        events = self.read_all()
        if task_id is not None:
            events = [e for e in events if e.task_id == task_id]

        if format == "raw":
            import json as _json

            return [_json.loads(e.model_dump_json()) for e in events]

        # ── ShareGPT format ──────────────────────────────────────
        # Group events by task_id, then build a conversation thread
        # from the step sequence.
        from collections import defaultdict

        by_task: dict[str, list[JournalEvent]] = defaultdict(list)
        for e in events:
            key = str(e.task_id) if e.task_id else "__unbound__"
            by_task[key].append(e)

        records: list[dict[str, Any]] = []
        for tid, task_events in by_task.items():
            task_events.sort(key=lambda e: e.ts)
            conversations: list[dict[str, str]] = []
            outcome = "unknown"
            total_tokens = 0

            for ev in task_events:
                if isinstance(ev, TaskStartedEvent):
                    goal = getattr(ev, "goal", None) or ""
                    if goal:
                        conversations.append(
                            {"from": "human", "value": goal},
                        )
                elif isinstance(ev, StepEvent):
                    step = getattr(ev, "step", None)
                    if step is None:
                        continue
                    action = getattr(step, "action", None)
                    result = getattr(step, "result", None)
                    if action is not None:
                        import json as _json

                        try:
                            args_str = _json.dumps(
                                getattr(action, "args", {}),
                                ensure_ascii=False,
                            )
                        except (TypeError, ValueError):
                            args_str = str(getattr(action, "args", ""))
                        conversations.append(
                            {
                                "from": "tool",
                                "value": (f"{getattr(action, 'sucker_id', '?')}({args_str})"),
                            }
                        )
                    if result is not None:
                        try:
                            import json as _json

                            out_str = _json.dumps(
                                getattr(result, "output", ""),
                                ensure_ascii=False,
                            )
                        except (TypeError, ValueError):
                            out_str = str(getattr(result, "output", ""))
                        conversations.append(
                            {
                                "from": "tool_result",
                                "value": out_str[:2000],
                            }
                        )
                        cost = getattr(result, "cost", None)
                        if cost is not None:
                            total_tokens += (
                                (getattr(cost, "tokens_in", 0) or 0)
                                + (getattr(cost, "tokens_out", 0) or 0)
                                # legacy field name kept for compat
                                + (getattr(cost, "tokens", 0) or 0)
                            )
                elif isinstance(ev, TrajectoryEvent):
                    traj = getattr(ev, "trajectory", None)
                    if traj is not None:
                        traj_outcome = getattr(traj, "outcome", None)
                        if traj_outcome is not None:
                            # ``TrajectoryOutcome`` exposes ``success`` /
                            # ``degraded`` — there is no ``status`` field,
                            # so derive the label from the real booleans.
                            if getattr(traj_outcome, "success", False):
                                outcome = (
                                    "pass_degraded"
                                    if getattr(traj_outcome, "degraded", False)
                                    else "pass"
                                )
                            else:
                                outcome = "fail"

            if conversations:
                records.append(
                    {
                        "conversations": conversations,
                        "task_id": tid,
                        "outcome": outcome,
                        "total_cost_tokens": total_tokens,
                    }
                )

        return records

    def write_step(
        self,
        task_id: TaskId,
        arm_id: ArmId,
        step: Step,
        *,
        actor: str | None = None,
    ) -> None:
        self.write(
            StepEvent(
                task_id=task_id,
                arm_id=arm_id,
                actor=actor,
                step=step,
                agent_id=current_agent_id(),
                conversation_id=current_conversation_id(),
            )
        )

    def write_trajectory(
        self,
        trajectory: Trajectory,
        *,
        actor: str | None = None,
    ) -> None:
        self.write(
            TrajectoryEvent(
                task_id=trajectory.task_id,
                arm_id=trajectory.arm_id,
                actor=actor,
                trajectory=trajectory,
                agent_id=current_agent_id(),
                conversation_id=current_conversation_id(),
            )
        )

    def write_task_started(
        self,
        task_id: TaskId,
        *,
        arm_id: ArmId | None = None,
        actor: str | None = None,
        total_nodes: int = 0,
        strategy: str = "",
        task_type: str = "",
        recipe_hash: str | None = None,
    ) -> None:
        self.write(
            TaskStartedEvent(
                task_id=task_id,
                arm_id=arm_id,
                actor=actor,
                total_nodes=total_nodes,
                strategy=strategy,
                task_type=task_type,
                recipe_hash=recipe_hash,
                agent_id=current_agent_id(),
                conversation_id=current_conversation_id(),
            )
        )

    def write_node_started(
        self,
        task_id: TaskId,
        arm_id: ArmId,
        *,
        actor: str | None = None,
        node_id: str,
        skill_ref: str,
        node_index: int,
    ) -> None:
        self.write(
            NodeStartedEvent(
                task_id=task_id,
                arm_id=arm_id,
                actor=actor,
                node_id=node_id,
                skill_ref=skill_ref,
                node_index=node_index,
                agent_id=current_agent_id(),
                conversation_id=current_conversation_id(),
            )
        )

    def write_checkpoint(
        self,
        task_id: TaskId,
        *,
        arm_id: ArmId | None = None,
        actor: str | None = None,
        nodes_completed: int,
        total_nodes: int,
        tokens_spent: int = 0,
        usd_spent: float = 0.0,
    ) -> None:
        self.write(
            TaskCheckpointEvent(
                task_id=task_id,
                arm_id=arm_id,
                actor=actor,
                nodes_completed=nodes_completed,
                total_nodes=total_nodes,
                tokens_spent=tokens_spent,
                usd_spent=usd_spent,
                agent_id=current_agent_id(),
                conversation_id=current_conversation_id(),
            )
        )

    def write_react_checkpoint(
        self,
        task_id: TaskId,
        *,
        arm_id: ArmId | None = None,
        actor: str | None = None,
        iteration_completed: int,
        max_iterations: int,
        messages_snapshot: list[dict[str, Any]],
        steps_snapshot: list[dict[str, Any]],
        has_final_answer: bool = False,
        final_answer: str = "",
        working_set_snapshot: list[dict[str, Any]] | None = None,
        progress_summary: str = "",
        current_phase: str = "",
    ) -> None:
        self.write(
            ReactCheckpointEvent(
                task_id=task_id,
                arm_id=arm_id,
                actor=actor,
                iteration_completed=iteration_completed,
                max_iterations=max_iterations,
                messages_snapshot=messages_snapshot,
                steps_snapshot=steps_snapshot,
                has_final_answer=has_final_answer,
                final_answer=final_answer,
                working_set_snapshot=working_set_snapshot or [],
                progress_summary=progress_summary,
                current_phase=current_phase,
                agent_id=current_agent_id(),
                conversation_id=current_conversation_id(),
            )
        )

    def write_tool_effect_intent(
        self,
        task_id: TaskId,
        arm_id: ArmId,
        *,
        effect_key: str,
        call_id: str,
        step_id: int,
        node_id: str,
        sucker_id: str,
        args_fingerprint: str,
        side_effecting: bool,
        actor: str | None = None,
    ) -> ToolEffectIntentEvent:
        event = ToolEffectIntentEvent(
            task_id=task_id,
            arm_id=arm_id,
            actor=actor,
            effect_key=effect_key,
            call_id=call_id,
            step_id=step_id,
            node_id=node_id,
            sucker_id=sucker_id,
            args_fingerprint=args_fingerprint,
            side_effecting=side_effecting,
            agent_id=current_agent_id(),
            conversation_id=current_conversation_id(),
        )
        self.write(event)
        return event

    def write_tool_effect_reconciliation(
        self,
        *,
        effect_key: str,
        fencing_token: int,
        action: Literal["authorize_retry"],
        reason: str,
        actor: str,
    ) -> ToolEffectReconciliationEvent:
        event = ToolEffectReconciliationEvent(
            actor=actor,
            effect_key=effect_key,
            fencing_token=fencing_token,
            action=action,
            reason=reason,
            agent_id=current_agent_id(),
            conversation_id=current_conversation_id(),
        )
        self.write(event)
        return event

    def write_task_paused(
        self,
        task_id: str,
        *,
        reason: str = "user_request",
        requested_by: str = "",
        iteration: int = 0,
    ) -> None:
        self.write(
            TaskPausedEvent(
                task_id=task_id,
                reason=reason,
                requested_by=requested_by,
                iteration=iteration,
                agent_id=current_agent_id(),
                conversation_id=current_conversation_id(),
            )
        )

    def write_task_resumed(
        self,
        task_id: str,
        *,
        resumed_by: str = "",
        extra_tokens: int = 0,
        extra_usd: float = 0.0,
        extra_iterations: int = 0,
    ) -> None:
        self.write(
            TaskResumedEvent(
                task_id=task_id,
                resumed_by=resumed_by,
                extra_tokens=extra_tokens,
                extra_usd=extra_usd,
                extra_iterations=extra_iterations,
                agent_id=current_agent_id(),
                conversation_id=current_conversation_id(),
            )
        )

    def write_token_usage(
        self,
        task_id: str,
        *,
        iteration: int,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float = 0.0,
        model: str = "",
        session_id: str = "",
    ) -> None:
        if input_tokens == 0 and output_tokens == 0:
            return  # Implementation note.
        self.write(
            TokenUsageEvent(
                task_id=task_id,
                session_id=session_id,
                iteration=iteration,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
                model=model,
                agent_id=current_agent_id(),
                conversation_id=current_conversation_id(),
            )
        )

    def write_file_op(
        self,
        *,
        path: str,
        action: str = "write",
        sucker_id: str = "",
        task_id: TaskId | None = None,
        arm_id: ArmId | None = None,
        actor: str | None = None,
        old_size: int | None = None,
        new_size: int | None = None,
        diff: str | None = None,
        rollback: dict[str, Any] | None = None,
    ) -> None:
        delta = 0
        if old_size is not None and new_size is not None:
            delta = new_size - old_size
        elif new_size is not None and action in ("create", "write"):
            delta = new_size
        elif old_size is not None and action == "delete":
            delta = -old_size
        self.write(
            FileOpEvent(
                task_id=task_id,
                arm_id=arm_id,
                actor=actor,
                path=path,
                action=action,  # type: ignore[arg-type]
                old_size=old_size,
                new_size=new_size,
                bytes_delta=delta,
                sucker_id=sucker_id,
                diff=diff,
                rollback=rollback,
                agent_id=current_agent_id(),
                conversation_id=current_conversation_id(),
            )
        )

    def write_preview_refresh(
        self,
        *,
        target: str = "",
        trigger_path: str = "",
        reason: str = "",
        task_id: TaskId | None = None,
        arm_id: ArmId | None = None,
        actor: str | None = None,
    ) -> None:
        self.write(
            PreviewRefreshEvent(
                task_id=task_id,
                arm_id=arm_id,
                actor=actor,
                target=target,
                trigger_path=trigger_path,
                reason=reason,
                agent_id=current_agent_id(),
                conversation_id=current_conversation_id(),
            )
        )

    def write_reflex_hit(
        self,
        *,
        task_id: TaskId | None = None,
        arm_id: ArmId | None = None,
        actor: str | None = None,
        rule_id: str,
        kind: str,
        latency_ms: float,
        intent_goal: str,
        response: Any = None,  # noqa: F821
    ) -> None:
        self.write(
            ReflexHitEvent(
                task_id=task_id,
                arm_id=arm_id,
                actor=actor,
                rule_id=rule_id,
                kind=kind,
                latency_ms=latency_ms,
                intent_goal=intent_goal,
                response=response,
            )
        )

    def write_immune(
        self,
        verdict: ImmuneVerdict,
        signature: AntigenSignature,
        task_id: TaskId | None = None,
        arm_id: ArmId | None = None,
        actor: str | None = None,
        reason: str = "",
    ) -> None:
        self.write(
            ImmuneEvent(
                task_id=task_id,
                arm_id=arm_id,
                actor=actor,
                verdict=verdict,
                signature=signature,
                reason=reason,
            )
        )

    def write_budget(
        self,
        event_type: Literal["budget_squirt", "budget_commit"],
        task_id: TaskId,
        *,
        actor: str | None = None,
        reason: str = "",
        cost: CostEntry | None = None,
    ) -> None:
        self.write(
            BudgetEvent(
                event_type=event_type,
                task_id=task_id,
                actor=actor,
                reason=reason,
                cost=cost or CostEntry(),
            )
        )

    def write_budget_breaker_reset(
        self,
        *,
        component: str,
        reason: str = "",
        actor: str | None = None,
    ) -> None:
        self.write(
            BudgetBreakerResetEvent(
                actor=actor,
                component=component,
                reason=reason,
                agent_id=current_agent_id(),
                conversation_id=current_conversation_id(),
            )
        )

    def write_goal_change(self, change: dict[str, Any]) -> None:
        """Append one CAS-guarded goal mutation (dsh ``goal/change``)."""
        self.write(
            GoalChangeEvent(
                change=change,
                agent_id=current_agent_id(),
                conversation_id=current_conversation_id(),
            )
        )

    def write_assistant_chunk(
        self,
        *,
        iteration: int,
        delta: str,
        kind: str = "text-delta",
        task_id: TaskId | None = None,
        index: int | None = None,
        call_id: str = "",
        name: str = "",
    ) -> None:
        """Append one streamed parent-reply chunk (dsh ``assistant/chunk``).

        ``iteration`` is the react-loop turn number; ``kind`` mirrors
        dsh's ``StreamChunk`` lane (``"text-delta"`` today,
        ``"reasoning-delta"`` / ``"tool-call-delta"`` when the producer
        streams those lanes). ``index`` / ``call_id`` / ``name`` carry
        the optional call identity for ``kind == "tool-call-delta"``
        fragments and default to unset otherwise.
        """
        self.write(
            AssistantChunkEvent(
                task_id=task_id,
                iteration=int(iteration),
                kind=kind,
                delta=delta,
                index=index,
                call_id=call_id,
                name=name,
                agent_id=current_agent_id(),
                conversation_id=current_conversation_id(),
            )
        )

    def write_user_message(
        self,
        text: str,
        *,
        goal_source: dict[str, Any] | None = None,
        session_id: str = "",
    ) -> None:
        """Append one human message (dsh ``user/message``).

        ``goal_source`` (optional dsh ``GoalMessageSource``) lets the goal
        fold count this message as the next admitted continuation round.
        ``session_id`` (optional) correlates the message to a durable
        sub-agent session; the goal fold ignores source-less rows either way.
        """
        self.write(
            UserMessageEvent(
                text=text,
                goal_source=goal_source,
                session_id=session_id,
                agent_id=current_agent_id(),
                conversation_id=current_conversation_id(),
            )
        )

    def write_skill_proposal_decision(
        self,
        *,
        proposal_name: str,
        decision: str,
        candidate_id: str = "",
        proposal_kind: str = "skill_forge",
        reason: str = "",
        details: dict[str, Any] | None = None,
        actor: str | None = None,
    ) -> None:
        self.write(
            SkillProposalDecisionEvent(
                actor=actor,
                proposal_kind=proposal_kind,
                proposal_name=proposal_name,
                candidate_id=candidate_id,
                decision=decision,
                reason=reason,
                details=details or {},
                agent_id=current_agent_id(),
                conversation_id=current_conversation_id(),
            )
        )

    def write_curriculum_goal_decision(
        self,
        *,
        goal_id: int,
        cluster_key: str,
        status: str,
        covered_by: str | None = None,
        reason: str = "",
        details: dict[str, Any] | None = None,
        actor: str | None = None,
    ) -> None:
        self.write(
            CurriculumGoalDecisionEvent(
                actor=actor,
                goal_id=goal_id,
                cluster_key=cluster_key,
                status=status,
                covered_by=covered_by,
                reason=reason,
                details=details or {},
                agent_id=current_agent_id(),
                conversation_id=current_conversation_id(),
            )
        )

    def write_mcp_proposal_decision(
        self,
        *,
        server_name: str,
        status: str,
        reason: str = "",
        details: dict[str, Any] | None = None,
        actor: str | None = None,
    ) -> None:
        self.write(
            McpProposalDecisionEvent(
                actor=actor,
                server_name=server_name,
                status=status,
                reason=reason,
                details=details or {},
                agent_id=current_agent_id(),
                conversation_id=current_conversation_id(),
            )
        )

    def write_protocol_drift_decision(
        self,
        *,
        drift_id: int,
        protocol_id: str,
        status: str,
        reason: str = "",
        details: dict[str, Any] | None = None,
        actor: str | None = None,
    ) -> None:
        self.write(
            ProtocolDriftDecisionEvent(
                actor=actor,
                drift_id=drift_id,
                protocol_id=protocol_id,
                status=status,
                reason=reason,
                details=details or {},
                agent_id=current_agent_id(),
                conversation_id=current_conversation_id(),
            )
        )
