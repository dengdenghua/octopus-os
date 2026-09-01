"""网状 Arm 互通 — Arm↔Arm 点对点消息（mailbox）。

阶段 1 验证 Worker 接入 signal_bus 后能收发 peer 消息，且不接入时行为不变
（向后兼容）。这是 architecture.md §"为什么比 Lead+Sub-agents 更进一步"
描述的"Arm₃ 直接告诉 Arm₇ 我已经抓住了"的最小实装。

阶段 2 验证 Arm 在 GraphRuntime 执行循环的 step 间隙能 poll mailbox，
即 on_step_callback 接线 — Arm 不必等整个图跑完才看到 peer 消息。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from runtime.execution.arms import Worker
from runtime.platform.models import (
    ArmAssignment,
    ArmId,
    Budget,
    BudgetLimits,
    BudgetSpec,
    ContextPacketRef,
    ExecutionResult,
    Step,
    TaskGraph,
    TaskId,
    TaskNode,
    ToolCall,
    Trajectory,
    TrajectoryOutcome,
)
from runtime.safety.chromatophores import SignalBus, SignalEvent


class _StubRuntime:
    """最小 GraphRuntime 桩 — Worker 构造需要它，但本测试不调用 handle()。"""


def _make_arm(arm_id: str, signal_bus: SignalBus | None = None) -> Worker:
    return Worker(
        arm_id=ArmId(arm_id),
        affinity=["test"],
        allowed_skills=[],
        runtime=_StubRuntime(),  # type: ignore[arg-type]
        signal_bus=signal_bus,
    )


def _make_step(i: int, caller: str) -> Step:
    action = ToolCall(caller=caller, sucker_id="test", args={})
    return Step(
        step_id=i,
        node_id=f"n{i}",
        action=action,
        result=ExecutionResult(call_id=action.call_id, status="success", output={"i": i}),
    )


def _make_assignment(arm_id: ArmId) -> ArmAssignment:
    graph = TaskGraph(
        nodes=[TaskNode(node_id="n0"), TaskNode(node_id="n1")],
        budget=BudgetSpec(tokens=10_000, usd=0.10),
        task_type="test",
    )
    return ArmAssignment(
        arm_id=arm_id,
        subgraph=graph,
        context_ref=ContextPacketRef(packet_id=uuid4(), budget_tokens=1_000),
        deadline=datetime.now(UTC) + timedelta(minutes=5),
    )


def _make_budget() -> Budget:
    return Budget(task_id=TaskId(uuid4()), limits=BudgetLimits(tokens=10_000, usd=1.0))


class _CallbackRuntime:
    """Mock GraphRuntime that invokes on_step_callback between steps.

    Records the callback it received and optionally performs an action
    (e.g. sending a peer message) between steps to test mid-run drain.
    """

    def __init__(self, n_steps: int = 3, between_steps=None) -> None:
        self.n_steps = n_steps
        self.between_steps = between_steps
        self.received_callback = None
        self.callback_count = 0

    def run(
        self,
        graph: TaskGraph,
        *,
        budget: Budget,
        caller: str,
        arm_id: ArmId,
        on_step_callback=None,
        **kw,
    ) -> Trajectory:
        self.received_callback = on_step_callback
        steps: list[Step] = []
        for i in range(self.n_steps):
            step = _make_step(i, caller)
            steps.append(step)
            if on_step_callback is not None:
                on_step_callback(step, i, self.n_steps)
                self.callback_count += 1
            if self.between_steps is not None and i < self.n_steps - 1:
                self.between_steps(i)
        return Trajectory(
            task_id=graph.task_id,
            arm_id=arm_id,
            strategy_id="default",
            recipe_id="",
            steps=steps,
            outcome=TrajectoryOutcome(success=True),
        )


class TestWorkerMailbox:
    """Worker 的 mailbox 机制。"""

    def test_no_signal_bus_is_isolated(self):
        # 向后兼容：不传 signal_bus 的 Worker 行为不变，send_to_arm 是 no-op。
        arm = _make_arm("arm1")
        assert arm._signal_bus is None
        assert arm.send_to_arm("arm2", {"hi": 1}) is None
        assert arm.drain_mailbox() == []

    def test_with_signal_bus_subscribes_to_own_mailbox(self):
        bus = SignalBus()
        arm = _make_arm("arm1", signal_bus=bus)
        # 构造时已订阅自己的 mailbox topic。
        assert arm._mailbox_sid is not None
        assert bus.subscriber_count() == 1

    def test_send_to_arm_delivers_to_target_mailbox(self):
        # 核心场景：Arm₃ 直接告诉 Arm₇ "我抓住了"。
        bus = SignalBus()
        arm3 = _make_arm("arm3", signal_bus=bus)
        arm7 = _make_arm("arm7", signal_bus=bus)

        arm3.send_to_arm("arm7", {"event": "grabbed", "resource": "file.txt"})

        # Arm₇ 的 mailbox 应收到消息。
        messages = arm7.drain_mailbox()
        assert len(messages) == 1
        event: SignalEvent = messages[0]
        assert event.topic == "arm.mailbox.arm7"
        assert event.payload["from"] == "arm3"
        assert event.payload["body"] == {"event": "grabbed", "resource": "file.txt"}
        assert event.publisher == "arm3"

    def test_message_not_delivered_to_unrelated_arm(self):
        # 点对点：发给 Arm₇ 的消息不被 Arm₃ 收到。
        bus = SignalBus()
        arm3 = _make_arm("arm3", signal_bus=bus)
        arm7 = _make_arm("arm7", signal_bus=bus)
        _arm9 = _make_arm("arm9", signal_bus=bus)

        arm3.send_to_arm("arm7", {"hi": 1})

        assert arm7.drain_mailbox() != []
        assert arm3.drain_mailbox() == []  # 发送方不收自己的消息

    def test_drain_mailbox_is_destructive(self):
        # drain 后再调返回空。
        bus = SignalBus()
        arm1 = _make_arm("arm1", signal_bus=bus)
        arm2 = _make_arm("arm2", signal_bus=bus)

        arm1.send_to_arm("arm2", {"n": 1})
        arm1.send_to_arm("arm2", {"n": 2})

        first = arm2.drain_mailbox()
        second = arm2.drain_mailbox()
        assert len(first) == 2
        assert second == []

    def test_multiple_arms_concurrent_publish_no_loss(self):
        # 多 Arm 并发 publish 不丢消息（queue.Queue 线程安全）。
        import threading

        bus = SignalBus()
        sender_a = _make_arm("a", signal_bus=bus)
        sender_b = _make_arm("b", signal_bus=bus)
        receiver = _make_arm("r", signal_bus=bus)

        N = 50  # noqa: N806

        def send_many(sender: Worker) -> None:
            for i in range(N):
                sender.send_to_arm("r", {"from": str(sender.arm_id), "i": i})

        t1 = threading.Thread(target=send_many, args=(sender_a,))
        t2 = threading.Thread(target=send_many, args=(sender_b,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        messages = receiver.drain_mailbox()
        assert len(messages) == 2 * N  # 无丢失

    def test_wildcard_subscriber_receives_all_mailbox_messages(self):
        # 编排器/观察者可用 arm.mailbox.* 监听全部 Arm 间消息。
        bus = SignalBus()
        seen: list[SignalEvent] = []
        bus.subscribe("arm.mailbox.*", lambda ev: seen.append(ev))

        arm1 = _make_arm("arm1", signal_bus=bus)
        arm2 = _make_arm("arm2", signal_bus=bus)

        arm1.send_to_arm("arm2", {"hi": 1})
        arm2.send_to_arm("arm1", {"yo": 2})

        assert len(seen) == 2
        assert {e.topic for e in seen} == {"arm.mailbox.arm2", "arm.mailbox.arm1"}


class TestWorkerStepCallback:
    """阶段 2 — on_step_callback 接线：Arm 在执行中 poll mailbox。"""

    def test_on_step_drains_mailbox_when_bus_present(self):
        # _on_step 在有 bus 时调 drain_mailbox，清空待处理消息。
        bus = SignalBus()
        arm = _make_arm("a", signal_bus=bus)
        peer = _make_arm("b", signal_bus=bus)

        peer.send_to_arm("a", {"hi": 1})
        assert len(arm.drain_mailbox()) == 1  # 收到了

        # 再发一条，调 _on_step 后应被 drain
        peer.send_to_arm("a", {"hi": 2})
        arm._on_step(_make_step(0, "test"), 0, 1)
        assert arm.drain_mailbox() == []  # 已被 _on_step 清空

    def test_on_step_noop_without_bus(self):
        # 无 bus 时 _on_step 是 no-op，不报错。
        arm = _make_arm("a")  # 无 signal_bus
        arm._on_step(_make_step(0, "test"), 0, 1)  # 不应 raise

    def test_handle_passes_on_step_callback_to_runtime(self):
        # handle() 应把 self._on_step 传给 runtime.run()。
        bus = SignalBus()
        arm = _make_arm("a", signal_bus=bus)
        rt = _CallbackRuntime(n_steps=2)
        arm.runtime = rt  # type: ignore[assignment]

        arm.handle(_make_assignment(arm.arm_id), _make_budget())

        assert rt.received_callback is not None
        assert rt.received_callback == arm._on_step
        assert rt.callback_count == 2  # 每步都触发了

    def test_handle_without_bus_still_passes_callback(self):
        # 即使无 bus，handle() 也传 callback（_on_step 内部 guard 为 no-op）。
        arm = _make_arm("a")  # 无 bus
        rt = _CallbackRuntime(n_steps=2)
        arm.runtime = rt  # type: ignore[assignment]

        arm.handle(_make_assignment(arm.arm_id), _make_budget())

        assert rt.received_callback is not None
        assert rt.callback_count == 2

    def test_arm_drains_mailbox_mid_run(self):
        # 端到端：Arm 在 GraphRuntime 执行的 step 间隙 drain peer 消息。
        # 时序：
        #   step0 → callback(drain, 空) → between(0): peer 发消息
        #   step1 → callback(drain, 收到消息) → between(1): peer 发消息
        #   step2 → callback(drain, 收到消息)
        bus = SignalBus()
        arm_a = _make_arm("a", signal_bus=bus)
        arm_b = _make_arm("b", signal_bus=bus)

        # 包装 drain_mailbox 记录每次 drain 的内容
        drained_per_step: list[list[SignalEvent]] = []
        original_drain = arm_a.drain_mailbox

        def recording_drain() -> list[SignalEvent]:
            msgs = original_drain()
            drained_per_step.append(msgs)
            return msgs

        arm_a.drain_mailbox = recording_drain  # type: ignore[method-assign]

        def send_between(step_idx: int) -> None:
            arm_b.send_to_arm("a", {"mid_run": True, "after_step": step_idx})

        rt = _CallbackRuntime(n_steps=3, between_steps=send_between)
        arm_a.runtime = rt  # type: ignore[assignment]

        result = arm_a.handle(_make_assignment(arm_a.arm_id), _make_budget())

        # 执行成功
        assert result.status == "success"
        # callback 被调用 3 次
        assert rt.callback_count == 3
        # drain 被调用 3 次（每次 callback 一次）
        assert len(drained_per_step) == 3
        # step0 后的 drain：消息还没发（between_steps 在 callback 之后才发）
        assert drained_per_step[0] == []
        # step1 后的 drain：收到 between_steps(0) 发的消息
        assert len(drained_per_step[1]) == 1
        assert drained_per_step[1][0].payload["body"]["after_step"] == 0
        # step2 后的 drain：收到 between_steps(1) 发的消息
        assert len(drained_per_step[2]) == 1
        assert drained_per_step[2][0].payload["body"]["after_step"] == 1

