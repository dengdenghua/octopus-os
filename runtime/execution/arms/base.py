from __future__ import annotations

import queue
from typing import Any

from runtime.adapters.instrumentation import trace_stage
from runtime.core.graph_runtime import GraphRuntime
from runtime.platform.models import (
    ArmAssignment,
    ArmId,
    ArmResult,
    ArmResultStatus,
    Budget,
    CostEntry,
    SkillId,
)
from runtime.safety.chromatophores import SignalBus, SignalEvent

# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


class Worker:
    def __init__(
        self,
        arm_id: ArmId,
        affinity: list[str],
        allowed_skills: list[SkillId],
        runtime: GraphRuntime,
        *,
        display_name: str = "",
        description: str = "",
        soul: str = "",
        icon: str = "",
        signal_bus: SignalBus | None = None,
        decision_cache: Any = None,
    ) -> None:
        self.arm_id: ArmId = arm_id
        self.affinity: list[str] = list(affinity)
        self.allowed_skills: list[SkillId] = list(allowed_skills)
        self.runtime: GraphRuntime = runtime
        self.display_name: str = display_name or str(arm_id)
        self.description: str = description
        self.soul: str = soul
        self.icon: str = icon
        self.decision_cache = decision_cache
        # ─── Mesh communication (网状 Arm 互通 阶段 1) ──────
        # When a signal_bus is injected, the Arm subscribes to its own
        # mailbox topic (``arm.mailbox.<arm_id>``) and can both receive
        # peer messages and send them via ``send_to_arm``. Without a
        # bus the Arm stays isolated (back-compat with existing callers
        # that don't pass signal_bus).
        self._signal_bus: SignalBus | None = signal_bus
        self._mailbox: queue.Queue[SignalEvent] = queue.Queue()
        self._mailbox_sid: int | None = None
        if signal_bus is not None:
            self._mailbox_sid = signal_bus.subscribe(
                f"arm.mailbox.{arm_id}", self._on_mailbox_message
            )

    # ─── Mesh communication ────────────────────────────────

    def _on_mailbox_message(self, event: SignalEvent) -> None:
        """SignalBus handler: enqueue peer message for later retrieval.

        Runs in the publisher's thread (SignalBus dispatches handlers
        synchronously), so it must stay non-blocking — just put on the
        queue. ``drain_mailbox`` is called by the Arm's own execution
        thread to process them.
        """
        self._mailbox.put_nowait(event)

    def drain_mailbox(self) -> list[SignalEvent]:
        """Pop all pending peer messages from the mailbox.

        Called by the Arm's execution thread (e.g. at the start of
        ``handle`` or between GraphRuntime steps) to process messages
        received from other Arms via ``send_to_arm``. Returns messages
        in arrival order; returns an empty list if none pending.
        """
        messages: list[SignalEvent] = []
        while True:
            try:
                messages.append(self._mailbox.get_nowait())
            except queue.Empty:
                break
        return messages

    def send_to_arm(self, target_arm_id: str, body: dict) -> Any:
        """Send a point-to-point message to a peer Arm's mailbox.

        No-op (returns None) when this Arm has no signal_bus injected
        — keeps callers simple in tests/presets that don't wire the bus.
        """
        if self._signal_bus is None:
            return None
        return self._signal_bus.send_to_arm(target_arm_id, body, from_arm_id=str(self.arm_id))

    def _on_step(self, step: Any, node_index: int, total_nodes: int) -> None:
        """GraphRuntime on_step_callback: drain mailbox between steps.

        Called by GraphRuntime after each node (sequential path) or
        after each layer (layered path) completes. Lets the Arm react
        to peer messages mid-run without breaking the graph loop.

        Only wired when a signal_bus is present — without a bus the
        mailbox is always empty, so we skip the drain to avoid the
        queue-empty exception churn on every step.
        """
        if self._signal_bus is None:
            return
        self.drain_mailbox()

    def can_use(self, skill_ref: Any) -> bool:
        from runtime.execution.suckers.layers import is_atomic

        if skill_ref is None:
            return True
        if is_atomic(skill_ref):
            return True
        allowed = {str(skill) for skill in self.allowed_skills}
        if "*" in allowed:
            return True
        return str(skill_ref) in allowed

    def can_handle(self, task: ArmAssignment) -> bool:
        for node in task.subgraph.nodes:
            if node.skill_ref is None:
                continue
            if not self.can_use(node.skill_ref):
                return False
        return True

    def handle(
        self,
        task: ArmAssignment,
        budget: Budget,
        *,
        seed_outputs: dict[str, Any] | None = None,
    ) -> ArmResult:
        with trace_stage(
            "arm.handle",
            arm_id=str(self.arm_id),
            task_id=str(task.subgraph.task_id),
        ) as span:
            span.set_attribute("echo.arm.affinity_count", len(self.affinity))
            span.set_attribute("echo.arm.subgraph_nodes", len(task.subgraph.nodes))

            if not self.can_handle(task):
                span.set_attribute("echo.arm.rejected", True)
                return ArmResult(
                    arm_id=self.arm_id,
                    task_id=task.subgraph.task_id,
                    status="failed",
                    reason="skill_ref not in allowed_skills",
                )

            try:
                trajectory = self.runtime.run(
                    task.subgraph,
                    budget=budget,
                    caller=f"arms/{self.arm_id}",
                    arm_id=self.arm_id,
                    on_step_callback=self._on_step,
                    # Seed with prior swarm-layer outputs so a node here can
                    # resolve cross-layer template refs (e.g. {n1.content}).
                    outputs_seed=seed_outputs,
                )
            except Exception as e:  # noqa: BLE001
                span.record_exception(e)
                return ArmResult(
                    arm_id=self.arm_id,
                    task_id=task.subgraph.task_id,
                    status="failed",
                    reason=f"{type(e).__name__}: {e}",
                )

            status = _status_from_trajectory(trajectory)
            total_cost = _sum_cost(trajectory.steps)

            span.set_attribute("echo.arm.status", status)
            span.set_attribute("echo.arm.steps_run", len(trajectory.steps))

            outputs: dict[str, object] = {}
            for step in trajectory.steps:
                if step.success:
                    outputs[step.node_id] = step.result.output

            reason = ""
            if status != "success":
                for step in trajectory.steps:
                    if not step.success:
                        reason = f"step {step.step_id} ({step.node_id}) {step.result.status}" + (
                            f": {step.result.error_type}" if step.result.error_type else ""
                        )
                        break
                if not reason and len(trajectory.steps) < len(task.subgraph.nodes):
                    reason = "pipeline halted before all nodes ran"

            return ArmResult(
                arm_id=self.arm_id,
                task_id=task.subgraph.task_id,
                status=status,
                outputs=outputs,
                trajectory_id=trajectory.trajectory_id,
                cost=total_cost,
                reason=reason,
                # Carry steps so SwarmRuntime can aggregate them into
                # a full-task Trajectory. Without this, a per_node
                # split produces N separate 1-step Trajectories, each
                # below SkillForge's ``step_count >= 2`` gate, and
                # multi-step patterns are never learned from parallel
                # swarm execution. See
                # tests/test_swarm_to_skill_forge_integration.py.
                steps=list(trajectory.steps),
            )

    # ─── Dunder ────────────────────────────────────────

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            f"Worker(arm_id={self.arm_id!r}, "
            f"affinity={self.affinity!r}, "
            f"skills={len(self.allowed_skills)})"
        )


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


class ArmPool:
    def __init__(self, arms: list[Worker]) -> None:
        self._arms: list[Worker] = list(arms)

    def add(self, arm: Worker) -> None:
        self._arms.append(arm)

    def all_arms(self) -> list[Worker]:
        return list(self._arms)

    def __len__(self) -> int:
        return len(self._arms)

    def __iter__(self):
        return iter(self._arms)

    def pick_for(self, task: ArmAssignment) -> Worker | None:
        needed = {node.skill_ref for node in task.subgraph.nodes if node.skill_ref is not None}

        candidates: list[tuple[int, int, int, int, Worker]] = []
        for idx, arm in enumerate(self._arms):
            if not arm.can_handle(task):
                continue
            allow = {str(skill) for skill in arm.allowed_skills}
            from runtime.execution.suckers.layers import is_atomic

            needed_str = {str(s) for s in needed}
            non_atomic_needed = {s for s in needed_str if not is_atomic(s)}
            coverage = len(non_atomic_needed) if "*" in allow else len(non_atomic_needed & allow)
            # Specialist preference: an arm that EXPLICITLY lists the needed
            # skills beats one that only handles them via the universal atomic
            # bypass (``can_use`` returns True for any atomic skill). Without
            # this every atomic-skill node funnels to the first arm (idx 0) and
            # the swarm never spreads across the registry-derived arms — a node
            # goes to its specialist arm instead, so a diverse layer fans out.
            explicit = len(needed_str) if "*" in allow else len(needed_str & allow)
            candidates.append((coverage, explicit, len(arm.affinity), -idx, arm))

        if not candidates:
            return None
        candidates.sort(key=lambda t: (t[0], t[1], t[2], t[3]), reverse=True)
        return candidates[0][4]

    def pick_for_intent(self, intent: Any) -> Worker | None:
        goal = ""
        intent_type = ""
        for attr in ("normalized_goal", "raw"):
            v = getattr(intent, attr, None)
            if isinstance(v, str) and v:
                goal = v.lower()
                break
        itype = getattr(intent, "intent_type", None)
        if isinstance(itype, str):
            intent_type = itype.lower()

        if not goal and not intent_type:
            return None

        haystack = f"{goal} {intent_type}".lower()

        scored: list[tuple[int, int, int, Worker]] = []
        for idx, arm in enumerate(self._arms):
            score = sum(
                1 for tag in arm.affinity if isinstance(tag, str) and tag.lower() in haystack
            )
            if score > 0:
                scored.append((score, len(arm.affinity), -idx, arm))

        if not scored:
            return None
        scored.sort(key=lambda t: (t[0], t[1], t[2]), reverse=True)
        return scored[0][3]


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


def _status_from_trajectory(trajectory) -> ArmResultStatus:
    if trajectory.outcome.success:
        return "success"
    statuses = [s.result.status for s in trajectory.steps]
    if "immune_reject" in statuses:
        return "immune_reject"
    if "circuit_broken" in statuses:
        return "circuit_broken"
    has_success = any(s.success for s in trajectory.steps)
    if has_success:
        return "partial"
    return "failed"


def _sum_cost(steps) -> CostEntry:
    total = CostEntry()
    for step in steps:
        total = total + step.result.cost
    return total
