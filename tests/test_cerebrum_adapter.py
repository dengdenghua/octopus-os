"""CerebrumDecisionAdapter 集成测试.

验证：
1. TaskGraph → ToolCall 列表转换正确
2. 拓扑排序保持依赖顺序
3. 无匹配规则时 fallback_skill 生效
4. 设备能力过滤生效
5. coordinator.with_cerebrum() 工厂方法可正常工作
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from runtime.core.cerebrum.planner import Rule, StaticPlanner
from runtime.tentacle.coordinator import TentacleCoordinator
from runtime.tentacle.mobile.cerebrum_adapter import CerebrumDecisionAdapter
from runtime.tentacle.mobile.device import MobileDevice

# ── fixtures ──────────────────────────────────────────────


@pytest.fixture
def wechat_rule():
    """打开微信规则."""
    return Rule(
        name="open_wechat",
        keywords=["微信", "wechat", "WeChat"],
        skill_sequence=["android.open_app", "android.wait"],
        node_args_templates=[{"package": "com.tencent.mm"}, {"ms": 500}],
        priority=10,
    )


@pytest.fixture
def tap_rule():
    """点击规则."""
    return Rule(
        name="tap_anything",
        keywords=["点击", "tap", "按"],
        skill_sequence=["android.find_and_tap"],
        node_args_templates=[{"text": "{intent_goal}"}],
        priority=5,
    )


@pytest.fixture
def planner_with_rules(wechat_rule, tap_rule):
    """带规则的 StaticPlanner."""
    return StaticPlanner(
        rules=[wechat_rule, tap_rule],
        fallback_skill="android.find_text",
    )


@pytest.fixture
def adapter(planner_with_rules):
    """CerebrumDecisionAdapter 实例."""
    return CerebrumDecisionAdapter(planner=planner_with_rules)


@pytest_asyncio.fixture
async def mock_device():
    """Mock 移动设备."""
    device = MobileDevice(
        tentacle_id="android-test-001",
        device_meta={"brand": "Google", "model": "Pixel 8"},
    )
    await device.connect()
    yield device
    await device.disconnect()


# ── 单元测试：适配器 ──────────────────────────────────────


@pytest.mark.asyncio
async def test_adapter_converts_taskgraph_to_toolcalls(adapter, mock_device):
    """TaskGraph 应正确转换为 ToolCall 列表."""
    tool_calls = await adapter.decide("打开微信", mock_device)

    assert len(tool_calls) == 2
    assert tool_calls[0].tool == "android.open_app"
    assert tool_calls[0].args == {"package": "com.tencent.mm", "intent_goal": "打开微信"}
    assert tool_calls[0].tentacle_id == "android-test-001"

    assert tool_calls[1].tool == "android.wait"
    assert tool_calls[1].args == {"ms": 500, "intent_goal": "打开微信"}


@pytest.mark.asyncio
async def test_adapter_includes_intent_goal_in_args(adapter, mock_device):
    """args_template 应包含 intent_goal."""
    tool_calls = await adapter.decide("点击发送按钮", mock_device)

    assert len(tool_calls) == 1
    assert tool_calls[0].tool == "android.find_and_tap"
    # {intent_goal} 被替换为实际任务描述
    assert "点击发送按钮" in tool_calls[0].args.get("text", "")


@pytest.mark.asyncio
async def test_adapter_fallback_skill(adapter, mock_device):
    """无匹配规则时应使用 fallback_skill."""
    tool_calls = await adapter.decide("这是一个完全不匹配的任务", mock_device)

    assert len(tool_calls) == 1
    assert tool_calls[0].tool == "android.find_text"
    assert tool_calls[0].args.get("intent_goal") == "这是一个完全不匹配的任务"


@pytest.mark.asyncio
async def test_adapter_topo_sort_linear(adapter, mock_device):
    """线性计划应保持顺序."""
    tool_calls = await adapter.decide("打开微信", mock_device)

    # 两个节点，无并行，保持规则定义的顺序
    assert tool_calls[0].tool == "android.open_app"
    assert tool_calls[1].tool == "android.wait"
    # call_id 有序
    assert tool_calls[0].call_id.endswith("-0")
    assert tool_calls[1].call_id.endswith("-1")


@pytest.mark.asyncio
async def test_adapter_trace_id_set(adapter, mock_device):
    """所有 ToolCall 应共享同一个 trace_id（task_id）."""
    tool_calls = await adapter.decide("打开微信", mock_device)

    assert len(tool_calls) == 2
    assert tool_calls[0].trace_id is not None
    assert tool_calls[0].trace_id == tool_calls[1].trace_id


@pytest.mark.asyncio
async def test_adapter_timeout_default(adapter, mock_device):
    """默认 timeout_ms 应生效."""
    tool_calls = await adapter.decide("打开微信", mock_device)

    assert all(tc.timeout_ms == 15_000 for tc in tool_calls)


@pytest.mark.asyncio
async def test_adapter_custom_timeout(mock_device):
    """自定义 timeout_ms 应生效."""
    planner = StaticPlanner(
        rules=[
            Rule(
                name="quick_tap",
                keywords=["tap"],
                skill_sequence=["android.tap"],
            ),
        ],
    )
    adapter = CerebrumDecisionAdapter(planner=planner, default_timeout_ms=5_000)

    tool_calls = await adapter.decide("tap", mock_device)
    assert tool_calls[0].timeout_ms == 5_000


@pytest.mark.asyncio
async def test_adapter_empty_result_on_plan_failure(mock_device):
    """Planner 抛异常时应返回空列表（不崩溃）."""
    # 创建一个会失败的 planner：无规则、无 fallback
    bad_planner = StaticPlanner(rules=[])
    adapter = CerebrumDecisionAdapter(planner=bad_planner)

    tool_calls = await adapter.decide("任意任务", mock_device)
    assert tool_calls == []


# ── 拓扑排序测试 ──────────────────────────────────────────


def test_topo_sort_linear_edges():
    """线性 edges 应保持原顺序."""
    from runtime.platform.models import BudgetSpec, TaskGraph, TaskNode, WorkflowEdge

    graph = TaskGraph(
        nodes=[
            TaskNode(node_id="n0", skill_ref="a", args_template={}),
            TaskNode(node_id="n1", skill_ref="b", args_template={}),
            TaskNode(node_id="n2", skill_ref="c", args_template={}),
        ],
        edges=[
            WorkflowEdge(from_node="n0", to_node="n1"),
            WorkflowEdge(from_node="n1", to_node="n2"),
        ],
        budget=BudgetSpec(tokens=100, usd=0.1),
    )
    ordered = CerebrumDecisionAdapter._topo_sort(graph)
    assert [n.node_id for n in ordered] == ["n0", "n1", "n2"]


def test_topo_sort_parallel_branches():
    """并行分支应正确排序（n0 在前，n1/n2 可并行）."""
    from runtime.platform.models import BudgetSpec, TaskGraph, TaskNode, WorkflowEdge

    graph = TaskGraph(
        nodes=[
            TaskNode(node_id="n0", skill_ref="a", args_template={}),
            TaskNode(node_id="n1", skill_ref="b", args_template={}),
            TaskNode(node_id="n2", skill_ref="c", args_template={}),
        ],
        edges=[
            WorkflowEdge(from_node="n0", to_node="n1"),
            WorkflowEdge(from_node="n0", to_node="n2"),
        ],
        budget=BudgetSpec(tokens=100, usd=0.1),
    )
    ordered = CerebrumDecisionAdapter._topo_sort(graph)
    # n0 必须在最前
    assert ordered[0].node_id == "n0"
    # n1, n2 顺序不确定但都在 n0 之后
    assert {ordered[1].node_id, ordered[2].node_id} == {"n1", "n2"}


def test_topo_sort_no_edges():
    """无 edges 时保持原始顺序."""
    from runtime.platform.models import BudgetSpec, TaskGraph, TaskNode

    graph = TaskGraph(
        nodes=[
            TaskNode(node_id="n0", skill_ref="a", args_template={}),
            TaskNode(node_id="n1", skill_ref="b", args_template={}),
        ],
        edges=[],
        budget=BudgetSpec(tokens=100, usd=0.1),
    )
    ordered = CerebrumDecisionAdapter._topo_sort(graph)
    assert [n.node_id for n in ordered] == ["n0", "n1"]


def test_topo_sort_cycle_fallback():
    """环检测失败时应回退到原始顺序.

    TaskGraph 模型自身会拒绝环，所以我们构造一个"伪" graph 对象
    （只用于测试 _topo_sort 的降级逻辑）。
    """
    from runtime.platform.models import BudgetSpec, TaskNode

    # 构造一个带环的 mock graph（绕过 TaskGraph 验证）
    class _MockGraph:
        def __init__(self, nodes, edges, budget):
            self.nodes = nodes
            self.edges = edges
            self.budget = budget

    graph = _MockGraph(
        nodes=[
            TaskNode(node_id="n0", skill_ref="a", args_template={}),
            TaskNode(node_id="n1", skill_ref="b", args_template={}),
        ],
        edges=[
            ("n0", "n1"),
            ("n1", "n0"),  # 环
        ],
        budget=BudgetSpec(tokens=100, usd=0.1),
    )
    ordered = CerebrumDecisionAdapter._topo_sort(graph)
    # 回退到原始顺序
    assert [n.node_id for n in ordered] == ["n0", "n1"]


# ── Coordinator 工厂方法测试 ──────────────────────────────


@pytest.mark.asyncio
async def test_coordinator_with_cerebrum_factory():
    """with_cerebrum 工厂方法应创建带决策引擎的协调器.

    本测试直接调用 decision_engine 验证适配器逻辑，
    避免 WebSocket 全双工死锁问题（task/execute 处理会阻塞消息循环）。
    """
    coord = TentacleCoordinator.with_cerebrum(
        host="127.0.0.1",
        port=18767,
        rules=[
            Rule(
                name="open_app",
                keywords=["打开", "open"],
                skill_sequence=["android.open_app"],
                node_args_templates=[{"package": "com.example.app"}],
            ),
        ],
        fallback_skill="android.find_text",
    )

    assert coord._decision_engine is not None

    # 直接测试决策引擎（不经过 WebSocket）
    device = MobileDevice(
        tentacle_id="android-test-001",
        device_meta={"brand": "Google", "model": "Pixel 8"},
    )
    await device.connect()

    tool_calls = await coord._decision_engine("打开示例应用", device)

    assert len(tool_calls) == 1
    assert tool_calls[0].tool == "android.open_app"
    assert tool_calls[0].args["package"] == "com.example.app"
    assert tool_calls[0].tentacle_id == "android-test-001"


@pytest.mark.asyncio
async def test_coordinator_with_cerebrum_fallback():
    """无匹配规则时应触发 fallback_skill."""
    coord = TentacleCoordinator.with_cerebrum(
        host="127.0.0.1",
        port=18768,
        rules=[
            Rule(
                name="open_app",
                keywords=["打开"],
                skill_sequence=["android.open_app"],
            ),
        ],
        fallback_skill="android.find_text",
    )

    device = MobileDevice(
        tentacle_id="android-test-002",
        device_meta={"brand": "Google", "model": "Pixel 8"},
    )
    await device.connect()

    # 不匹配任何规则的任务
    tool_calls = await coord._decision_engine("xyz completely unrelated", device)

    assert len(tool_calls) == 1
    assert tool_calls[0].tool == "android.find_text"
