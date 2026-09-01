"""CerebrumDecisionAdapter —— 将 Cerebrum 决策引擎输出转换为 ToolCall 列表.

把 Cerebrum 的 TaskGraph（包含 TaskNode 序列）适配为 TentacleCoordinator
所需的 ToolCall 列表，供手机端执行。

用法::

    adapter = CerebrumDecisionAdapter(planner=static_planner)
    tool_calls = await adapter.decide("打开微信并发送消息", device)
"""

from __future__ import annotations

import logging
from typing import Any

from runtime.core.cerebrum.planner import StaticPlanner
from runtime.platform.models import ParsedIntent, TaskGraph
from runtime.tentacle.base import ToolCall
from runtime.tentacle.mobile.device import MobileDevice

logger = logging.getLogger(__name__)


class CerebrumDecisionAdapter:
    """Cerebrum → Tentacle ToolCall 适配器.

    负责：
    1. 将用户任务描述包装为 ParsedIntent
    2. 调用 Cerebrum Planner 生成 TaskGraph
    3. 将 TaskGraph 的每个 TaskNode 转换为 ToolCall
    4. 注入 tentacle_id 和 call_id
    """

    def __init__(
        self,
        planner: StaticPlanner,
        *,
        intent_type: str = "task",
        default_timeout_ms: int = 15_000,
    ) -> None:
        self.planner = planner
        self.intent_type = intent_type
        self.default_timeout_ms = default_timeout_ms

    async def decide(self, task: str, device: MobileDevice) -> list[ToolCall]:
        """生成 ToolCall 列表.

        Args:
            task: 自然语言任务描述，如 "打开微信"
            device: 目标移动设备

        Returns:
            有序的 ToolCall 列表（已按 TaskGraph 拓扑排序）
        """
        # 1. 构造 ParsedIntent
        intent = ParsedIntent(
            raw=task,
            intent_type=self.intent_type,  # type: ignore[arg-type]
            normalized_goal=task,
            user_context={
                "tentacle_id": device.tentacle_id,
                "platform": device.platform,
                "capabilities": device.capabilities,
            },
        )

        # 2. 调用 Cerebrum 生成 TaskGraph
        try:
            graph: TaskGraph = self.planner.plan(
                intent,
                allowed_skills=device.capabilities,
            )
        except Exception as e:
            logger.warning(
                "Cerebrum plan failed for task=%r device=%s: %s",
                task,
                device.tentacle_id,
                e,
            )
            # 降级：返回空列表，coordinator 会返回友好提示
            return []

        logger.info(
            "Cerebrum planned task=%r strategy=%s nodes=%d edges=%d",
            task,
            graph.strategy,
            len(graph.nodes),
            len(graph.edges),
        )

        # 3. 拓扑排序（如果 edges 存在）
        ordered_nodes = self._topo_sort(graph)

        # 4. 转换为 ToolCall
        tool_calls: list[ToolCall] = []
        for i, node in enumerate(ordered_nodes):
            if node.skill_ref is None:
                continue

            call = ToolCall(
                call_id=f"{graph.task_id}-{i}",
                tentacle_id=device.tentacle_id,
                tool=str(node.skill_ref),
                args=dict(node.args_template),
                timeout_ms=self.default_timeout_ms,
                trace_id=str(graph.task_id),
            )
            tool_calls.append(call)

        return tool_calls

    @staticmethod
    def _topo_sort(graph: TaskGraph) -> list[Any]:
        """对 TaskGraph 节点进行拓扑排序.

        无 edges 时保持原顺序（线性计划）。
        支持 edge 为 WorkflowEdge 对象或 (from, to) 元组。
        """
        if not graph.edges:
            return list(graph.nodes)

        def _edge_endpoints(edge: Any) -> tuple[str, str]:
            if hasattr(edge, "from_node") and hasattr(edge, "to_node"):
                return (edge.from_node, edge.to_node)
            if isinstance(edge, (tuple, list)) and len(edge) >= 2:
                return (str(edge[0]), str(edge[1]))
            raise ValueError(f"Unknown edge type: {type(edge)}")

        node_ids = {n.node_id for n in graph.nodes}
        adj: dict[str, list[str]] = {nid: [] for nid in node_ids}
        in_degree: dict[str, int] = {nid: 0 for nid in node_ids}

        for edge in graph.edges:
            src, dst = _edge_endpoints(edge)
            adj[src].append(dst)
            in_degree[dst] = in_degree.get(dst, 0) + 1

        # Kahn's algorithm
        queue = [nid for nid, deg in in_degree.items() if deg == 0]
        ordered_ids: list[str] = []
        while queue:
            # 保持确定性：按 node_id 排序
            queue.sort()
            node_id = queue.pop(0)
            ordered_ids.append(node_id)
            for neighbor in adj[node_id]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        # 如果图有环，回退到原始顺序
        if len(ordered_ids) != len(graph.nodes):
            logger.warning("TaskGraph has cycle, falling back to original order")
            return list(graph.nodes)

        node_by_id = {n.node_id: n for n in graph.nodes}
        return [node_by_id[nid] for nid in ordered_ids]
