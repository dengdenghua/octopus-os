"""VisionReAct —— 带视觉理解的 ReAct 循环.

在标准 LightweightReAct 基础上增加 VLM 兜底：

1. 优先使用 Skill + 无障碍树（精确、快速）
2. 当无障碍树无法提供足够信息时，调用 VLM 分析截图
3. VLM 返回建议操作 → 转换为 ToolCall 执行

触发 VLM 的条件：
- get_screen_info 返回空树或节点数 < 3
- 连续 2 次工具调用失败
- 用户明确要求"看屏幕"
- Skill 执行后结果验证失败

用法::

    from runtime.tentacle.mobile.vlm import VlmClient, VlmConfig, VisionReAct

    vlm = VlmClient(VlmConfig.qwen_vl("sk-xxx"))
    react = VisionReAct(
        client=llm_client,
        executor=executor,
        vlm_client=vlm,
    )
    result = react.run(
        user_task="打开微信发消息",
        skills=mobile_skills,
        screenshot_getter=get_screenshot_base64,
    )
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ...llm.chat_types import (
    ChatMessage,
    FinishReason,
    SkillSpec,
    TaskOutcome,
    TaskResult,
    ToolCall,
    ToolCallResult,
)
from ...llm.lightweight_client import LightweightLlmClient
from ...llm.react_loop import ReActCallbacks
from .client import ScreenAnalysis, SuggestedAction, VlmClient

logger = logging.getLogger(__name__)


# ── 截图获取器类型 ──────────────────────────────────────────────

# 截图获取器：返回 base64 编码的截图字符串
ScreenshotGetter = Callable[[], str]

# 无障碍树获取器：返回 dict 或 None
ScreenInfoGetter = Callable[[], dict[str, Any] | None]


# ── VLM 触发策略 ──────────────────────────────────────────────


@dataclass(slots=True)
class VlmTriggerPolicy:
    """VLM 触发策略配置.

    控制何时调用 VLM 分析截图，避免每步都调浪费 token。
    """

    min_tree_nodes: int = 3  # 无障碍树节点数低于此值时触发 VLM
    consecutive_failures: int = 2  # 连续工具调用失败次数达到此值时触发 VLM
    verify_after_skill: bool = True  # Skill 执行后是否验证结果
    vlm_call_interval: int = 2  # 每隔多少步才允许调用 VLM（0 = 不限制）
    enable_auto_trigger: bool = True  # 是否自动触发 VLM（关闭则仅手动触发）


# ── 带视觉的 ReAct 循环 ──────────────────────────────────────


class VisionReAct:
    """带视觉理解的 ReAct 循环.

    在标准 ReAct 循环基础上增加 VLM 兜底：
    优先使用 Skill + 无障碍树（精确、快速），
    当信息不足时自动调用 VLM 分析截图。

    用法::

        vlm = VlmClient(VlmConfig.qwen_vl("sk-xxx"))
        react = VisionReAct(
            client=llm_client,
            executor=executor,
            vlm_client=vlm,
        )
        result = react.run(
            user_task="打开微信发消息",
            skills=mobile_skills,
            screenshot_getter=get_screenshot_base64,
        )
    """

    def __init__(
        self,
        client: LightweightLlmClient,
        executor: Any,  # ToolExecutor
        vlm_client: VlmClient,
        *,
        max_steps: int = 30,
        stuck_window: int = 4,
        compress_trigger_ratio: float = 0.7,
        compress_target_ratio: float = 0.5,
        max_tool_result_chars: int = 1500,
        system_prompt: str | None = None,
        callbacks: ReActCallbacks | None = None,
        trigger_policy: VlmTriggerPolicy | None = None,
    ) -> None:
        self.client = client
        self.executor = executor
        self.vlm_client = vlm_client
        self.max_steps = max_steps
        self.stuck_window = stuck_window
        self.compress_trigger_ratio = compress_trigger_ratio
        self.compress_target_ratio = compress_target_ratio
        self.max_tool_result_chars = max_tool_result_chars
        self.system_prompt = system_prompt or VISION_SYSTEM_PROMPT
        self.callbacks = callbacks or ReActCallbacks()
        self.trigger_policy = trigger_policy or VlmTriggerPolicy()

        self._cancelled = False
        self._total_tokens = 0
        self._consecutive_failures = 0
        self._vlm_call_count = 0
        self._last_vlm_step = -100  # 上次调用 VLM 的步数
        self._recent_window: deque[frozenset[str]] = deque(maxlen=stuck_window * 2)

        # 截图/无障碍树获取器（由 run() 传入）
        self._screenshot_getter: ScreenshotGetter | None = None
        self._screen_info_getter: ScreenInfoGetter | None = None

    # ── 公共入口 ────────────────────────────────────────────

    def run(
        self,
        user_task: str,
        skills: list[SkillSpec],
        *,
        screenshot_getter: ScreenshotGetter | None = None,
        screen_info_getter: ScreenInfoGetter | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> TaskResult:
        """同步执行带视觉理解的 ReAct 循环.

        Args:
            user_task: 用户原始任务文本
            skills: 可用工具列表
            screenshot_getter: 截图获取器（返回 base64 字符串）
            screen_info_getter: 无障碍树获取器（返回 dict 或 None）
            cancel_check: 每次 step 前调用，返回 True 立刻终止
        """
        self._cancelled = False
        self._total_tokens = 0
        self._consecutive_failures = 0
        self._vlm_call_count = 0
        self._last_vlm_step = -100
        self._recent_window.clear()
        self._screenshot_getter = screenshot_getter
        self._screen_info_getter = screen_info_getter

        messages: list[ChatMessage] = [
            ChatMessage.system(self.system_prompt),
            ChatMessage.user(user_task),
        ]
        steps = 0
        final = ""

        try:
            for step in range(self.max_steps):
                steps = step + 1
                if cancel_check and cancel_check():
                    self._cancelled = True
                    return self._finish(
                        TaskOutcome.CANCELLED,
                        "cancelled by cancel_check",
                        steps,
                    )
                if self._cancelled:
                    return self._finish(
                        TaskOutcome.CANCELLED,
                        "cancelled by external signal",
                        steps,
                    )
                self._emit(self.callbacks.on_step_start, step)

                # 1. 检查是否需要 VLM 兜底
                vlm_analysis = self._maybe_vlm_analyze(step, user_task, messages)
                if vlm_analysis is not None:
                    # 将 VLM 分析结果注入消息
                    vlm_msg = self._format_vlm_analysis(vlm_analysis)
                    messages.append(ChatMessage.user(vlm_msg))

                # 2. 调 LLM
                resp = self.client.chat(messages, skills=skills)
                self._total_tokens += resp.usage.total_tokens
                self._emit(self.callbacks.on_llm_response, resp)

                # 3. 决策
                if not resp.has_tool_calls:
                    final = resp.content
                    if resp.finish_reason == FinishReason.ERROR:
                        return self._finish(TaskOutcome.ERROR, final, steps, error=final)
                    return self._finish(TaskOutcome.DONE, final, steps)

                # 4. 把 assistant 消息（含 tool_calls）塞回历史
                messages.append(ChatMessage.assistant(resp.content, tool_calls=resp.tool_calls))

                # 5. 死循环检测
                if self._is_stuck(resp.tool_calls):
                    self._emit(self.callbacks.on_stuck, [tc.name for tc in resp.tool_calls])
                    return self._finish(
                        TaskOutcome.STUCK,
                        f"Stuck: same tools repeated {self.stuck_window}x",
                        steps,
                    )

                # 6. 执行工具
                for tc in resp.tool_calls:
                    self._emit(self.callbacks.on_tool_call, tc)
                    try:
                        result = self.executor.execute(tc)
                    except Exception as e:  # noqa: BLE001
                        logger.exception("Tool %s crashed", tc.name)
                        result = ToolCallResult(
                            tool_call_id=tc.id,
                            name=tc.name,
                            success=False,
                            content=f"executor crashed: {e!r}",
                        )

                    # 更新连续失败计数
                    if result.success:
                        self._consecutive_failures = 0
                    else:
                        self._consecutive_failures += 1

                    # 摘要化喂回 LLM
                    summarized = self._summarize(result)
                    messages.append(summarized.to_tool_message().to_message())
                    self._emit(self.callbacks.on_tool_result, result)

                # 7. 三级压缩
                self._maybe_compress(messages)
                self._emit(self.callbacks.on_step_end, step)

            # 循环结束 —— 步数耗尽
            return self._finish(
                TaskOutcome.MAX_STEPS,
                f"Reached max_steps={self.max_steps}",
                steps,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("VisionReAct crashed")
            return self._finish(TaskOutcome.ERROR, str(e), steps, error=str(e))

    def cancel(self) -> None:
        """外部取消信号（线程安全）."""
        self._cancelled = True

    # ── VLM 分析 ────────────────────────────────────────────

    def _maybe_vlm_analyze(
        self,
        step: int,
        task: str,
        messages: list[ChatMessage],
    ) -> ScreenAnalysis | None:
        """判断是否需要 VLM 分析，如需要则执行.

        触发条件（满足任一即触发）：
        1. 无障碍树节点数 < min_tree_nodes
        2. 连续失败次数 >= consecutive_failures
        3. 用户消息包含"看屏幕"等关键词
        4. 调用间隔满足 vlm_call_interval
        """
        if not self.trigger_policy.enable_auto_trigger:
            return None

        # 检查调用间隔
        if (
            self.trigger_policy.vlm_call_interval > 0
            and step - self._last_vlm_step < self.trigger_policy.vlm_call_interval
        ):
            return None

        should_trigger = False

        # 条件 1：无障碍树信息不足
        if self._screen_info_getter is not None:
            try:
                screen_info = self._screen_info_getter()
                if screen_info is None:
                    should_trigger = True
                    logger.info("VLM 触发：无障碍树为空 (step=%d)", step)
                elif isinstance(screen_info, dict):
                    node_count = self._count_tree_nodes(screen_info)
                    if node_count < self.trigger_policy.min_tree_nodes:
                        should_trigger = True
                        logger.info(
                            "VLM 触发：无障碍树节点不足 (%d < %d, step=%d)",
                            node_count,
                            self.trigger_policy.min_tree_nodes,
                            step,
                        )
            except Exception as e:
                logger.warning("获取无障碍树失败: %s", e)
                should_trigger = True

        # 条件 2：连续失败
        if self._consecutive_failures >= self.trigger_policy.consecutive_failures:
            should_trigger = True
            logger.info(
                "VLM 触发：连续 %d 次工具调用失败 (step=%d)",
                self._consecutive_failures,
                step,
            )

        # 条件 3：用户要求"看屏幕"
        if messages and "看屏幕" in (messages[1].content if len(messages) > 1 else ""):
            should_trigger = True
            logger.info("VLM 触发：用户要求看屏幕 (step=%d)", step)

        if not should_trigger:
            return None

        # 执行 VLM 分析
        return self._do_vlm_analyze(step, task)

    def _do_vlm_analyze(self, step: int, task: str) -> ScreenAnalysis | None:
        """执行 VLM 分析截图."""
        if self._screenshot_getter is None:
            logger.warning("VLM 触发但未提供 screenshot_getter，跳过")
            return None

        try:
            screenshot_b64 = self._screenshot_getter()
            if not screenshot_b64:
                logger.warning("VLM 触发但截图为空，跳过")
                return None

            # 获取可选的无障碍树信息
            screen_info = None
            if self._screen_info_getter is not None:
                with contextlib.suppress(Exception):  # best-effort; fail-open
                    screen_info = self._screen_info_getter()

            logger.info("调用 VLM 分析截图 (step=%d)...", step)
            analysis = self.vlm_client.analyze_screenshot(
                screenshot_base64=screenshot_b64,
                task=task,
                screen_info=screen_info,
            )
            self._vlm_call_count += 1
            self._last_vlm_step = step

            logger.info(
                "VLM 分析完成: description=%s actions=%d app=%s",
                analysis.description[:50],
                len(analysis.suggested_actions),
                analysis.current_app,
            )
            return analysis

        except Exception as e:
            logger.warning("VLM 分析失败: %s", e)
            return None

    @staticmethod
    def _format_vlm_analysis(analysis: ScreenAnalysis) -> str:
        """将 VLM 分析结果格式化为可注入 ReAct 消息的文本."""
        parts = [
            "[VLM 视觉分析]",
            f"屏幕描述：{analysis.description}",
        ]
        if analysis.current_app:
            parts.append(f"当前应用：{analysis.current_app}")
        if analysis.screen_state:
            parts.append(f"屏幕状态：{analysis.screen_state}")
        if analysis.suggested_actions:
            parts.append("建议操作：")
            for i, action in enumerate(analysis.suggested_actions, 1):
                coord_str = f" 坐标{action.coordinates}" if action.coordinates else ""
                text_str = f" 输入'{action.text}'" if action.text else ""
                parts.append(
                    f"  {i}. {action.action} → {action.target}{coord_str}{text_str}"
                    f" (置信度: {action.confidence:.0%})"
                )
        return "\n".join(parts)

    # ── SuggestedAction → ToolCall 转换 ─────────────────────

    @staticmethod
    def suggested_action_to_tool_calls(
        actions: list[SuggestedAction],
        tentacle_id: str,
        platform: str = "android",
    ) -> list[ToolCall]:
        """将 VLM 建议的操作转换为 Tentacle ToolCall 列表.

        映射关系：
        - tap → {platform}.tap (x, y)
        - swipe → {platform}.swipe (direction)
        - type → {platform}.input_text (text)
        - scroll → {platform}.swipe (direction)
        - long_press → {platform}.long_press (x, y)

        Args:
            actions: VLM 建议的操作列表
            tentacle_id: 目标设备 ID
            platform: 设备平台前缀（``ios`` / ``android``），默认 ``android``

        Returns:
            转换后的 ToolCall 列表
        """
        from ...base import ToolCall as BaseToolCall

        tool_calls: list[ToolCall] = []
        for i, action in enumerate(actions):
            call_id = f"vlm-{int(time.time() * 1000)}-{i}"
            tool_name = ""
            args: dict[str, Any] = {}

            if action.action == "tap" and action.coordinates:
                tool_name = f"{platform}.tap"
                args = {"x": action.coordinates[0], "y": action.coordinates[1]}
            elif action.action == "long_press" and action.coordinates:
                tool_name = f"{platform}.long_press"
                args = {"x": action.coordinates[0], "y": action.coordinates[1]}
            elif action.action == "type" and action.text:
                tool_name = f"{platform}.input_text"
                args = {"text": action.text}
                # 如果有坐标，先点击目标位置
                if action.coordinates:
                    tap_call = BaseToolCall(
                        call_id=f"{call_id}-tap",
                        tentacle_id=tentacle_id,
                        tool=f"{platform}.tap",
                        args={"x": action.coordinates[0], "y": action.coordinates[1]},
                    )
                    # 注意：这里返回的是 llm.chat_types.ToolCall，需要转换
                    tool_calls.append(
                        ToolCall(
                            id=tap_call.call_id,
                            name=tap_call.tool,
                            arguments=tap_call.args,
                        )
                    )
            elif action.action in ("swipe", "scroll"):
                tool_name = f"{platform}.swipe"
                # 默认向上滑动（最常见）
                args = {"direction": "up"}
            else:
                # 未知操作类型，跳过
                logger.warning("VLM 建议的未知操作类型: %s", action.action)
                continue

            if tool_name:
                tool_calls.append(
                    ToolCall(
                        id=call_id,
                        name=tool_name,
                        arguments=args,
                    )
                )

        return tool_calls

    # ── 内部工具（与 LightweightReAct 对称） ─────────────────

    def _finish(
        self,
        outcome: TaskOutcome,
        message: str,
        steps: int,
        *,
        error: str | None = None,
    ) -> TaskResult:
        result = TaskResult(
            outcome=outcome,
            final_message=message,
            steps=steps,
            total_tokens=self._total_tokens,
            error=error,
        )
        self._emit(self.callbacks.on_finish, result)
        return result

    def _is_stuck(self, tool_calls: list[ToolCall]) -> bool:
        """4 轮滑动窗口：连续 N 轮调用同一组工具就停."""
        if not tool_calls:
            return False
        fingerprints = [_tool_fingerprint(tc) for tc in tool_calls]
        self._recent_window.append(frozenset(fingerprints))
        if len(self._recent_window) < self.stuck_window:
            return False
        recent = list(self._recent_window)[-self.stuck_window :]
        return all(r == recent[0] for r in recent)

    def _maybe_compress(self, messages: list[ChatMessage]) -> None:
        """三级压缩：保留 system + 最近若干轮，折叠中间历史."""
        total_chars = sum(len(m.content) for m in messages)
        estimated_budget = 200_000 * 4
        if total_chars < estimated_budget * self.compress_trigger_ratio:
            return
        if len(messages) < 6:
            return
        keep_recent = 4
        head = messages[0]
        tail = messages[-(keep_recent):]
        middle = messages[1:-keep_recent]
        summary = self._summarize_middle(middle)
        new_messages = [head, ChatMessage.assistant(f"[历史摘要] {summary}")] + tail
        old_len = len(messages)
        self._emit(self.callbacks.on_compress, old_len, len(new_messages))
        messages.clear()
        messages.extend(new_messages)

    @staticmethod
    def _summarize_middle(middle: list[ChatMessage]) -> str:
        """极简摘要：把中间消息的 content 拼起来再截断."""
        parts: list[str] = []
        for m in middle:
            snippet = m.content[:200].replace("\n", " ")
            parts.append(f"[{m.role}] {snippet}")
        joined = " | ".join(parts)
        return joined[:1500] + ("..." if len(joined) > 1500 else "")

    def _summarize(self, result: ToolCallResult) -> ToolCallResult:
        """截断喂回 LLM 的 content（节省 token）."""
        if len(result.content) <= self.max_tool_result_chars:
            return result
        truncated = result.content[: self.max_tool_result_chars]
        return ToolCallResult(
            tool_call_id=result.tool_call_id,
            name=result.name,
            success=result.success,
            content=truncated + f"\n... [truncated, full={len(result.content)} chars]",
            raw=result.raw,
        )

    def _emit(self, fn: Any | None, *args: Any) -> None:
        """安全触发回调."""
        if fn is None:
            return
        try:
            fn(*args)
        except Exception:  # noqa: BLE001
            logger.exception("Callback failed")

    @staticmethod
    def _count_tree_nodes(tree: dict[str, Any]) -> int:
        """递归计算无障碍树节点数."""
        count = 1
        children = tree.get("children", [])
        if isinstance(children, list):
            for child in children:
                if isinstance(child, dict):
                    count += VisionReAct._count_tree_nodes(child)
        return count


# ── 默认 system prompt ────────────────────────────────────────

VISION_SYSTEM_PROMPT = """\
你是 Echo Mobile 的执行 Agent，运行在 Android 设备触手之上。
你同时拥有无障碍树和视觉理解能力。

工作原则：
1. **先观察，再行动**：先用 take_screenshot / get_screen_info 看清当前界面
2. **VLM 辅助**：当无障碍树信息不足时，VLM 会提供视觉分析结果，请参考其建议
3. **小步快跑**：每步只做 1-2 个动作，做完再截屏确认
4. **路径稳健**：找不到目标就滚动；遇到弹窗先 dismiss
5. **诚实汇报**：用 finish({ok: true/false, summary: "..."}) 收尾，不要胡编

可用工具见 tools 列表。每次响应要么调工具，要么调 finish 收尾。
"""


# ── 内部工具函数 ──────────────────────────────────────────────


def _tool_fingerprint(tc: ToolCall) -> str:
    """给一个 tool_call 算指纹：name + 排序后 args keys + args hash."""
    import hashlib

    key_part = tc.name
    arg_keys = ",".join(sorted(tc.arguments.keys()))
    args_hash = hashlib.md5(
        json.dumps(tc.arguments, sort_keys=True, ensure_ascii=False).encode("utf-8"),
        usedforsecurity=False,
    ).hexdigest()[:8]
    return f"{key_part}|{arg_keys}|{args_hash}"
