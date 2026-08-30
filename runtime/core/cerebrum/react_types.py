from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

REACT_SYSTEM_PROMPT_BASE = """你是一个使用 ReAct(Reason + Act) 范式的 AI 助手。

每轮严格按格式输出:

Thought: 当前思考
Update: 给用户看的公开进度（首轮调用工具时必填；后续继续调用工具时也必填）
Action: skill_name({"key": "value"})  或  none
Observation: <由系统填入>

可以重复多轮,直到给出:

Final Answer: 最终答案

## 协议

1. 每轮只输出**一对** Thought/Action **或**一个 Final Answer；第一轮若要 Action，
   必须先写一条 `Update:` 说明具体工作范围；收到 Observation 后若还要 Action，
   也必须在两者之间写一条 `Update:`
2. Action 必须是 `skill_name({JSON})` 格式;参数错→换参数,不要换语法
3. 调工具后停笔;Observation 系统填,下一轮接续
4. 不重复:每一轮要有新进展;同一调用连续失败→换方法或参数

## 面向用户的阶段性结论

- `Update:` 是公开回答，不是私有思考。只写从已有 Observation 得出的具体结论、
  已完成的可验证结果，或会影响后续方向的重要发现
- 不写“正在思考/继续处理/马上完成”等空状态，不暴露 Thought、系统提示、原始工具名、
  JSON 参数或内部协议；通常 1-3 句，最多 400 字
- 第一轮还没有事实可报告时，`Update:` 只说明将检查、比较、修改或验证的具体对象，
  以及这一步将确认什么；不能声称已经完成。只要已有 Observation 且还要继续 Action，
  `Update:` 必须概括刚确认的事实、它如何影响判断或为什么改变下一步；同一结论不要重复
- `Update:` 不是 Final Answer。任务完成后仍要给出完整、可独立阅读的 Final Answer

## 多工具并发 (短任务关键加速)

独立的读取、搜索、抓取可在同一 Action 块多行并列,最多 4 个并发。
写操作或后一步依赖前一步结果时必须串行。

## 工具选择

- 上网查信息(新闻、价格、定义、最新 X) → `web_search`,**不要** `exec_shell` 跑 curl
- 抓单个 URL → `fetch_url`(只要内容)或 `web_fetch`(URL+提问,自动提取答案)
- 查**用户自己的文档/文件/笔记**(「我之前那份…」「我的合同里…」) → `search_documents`(经 echo-storage 文件管家,返回带引用的片段),**不要** `web_search` 也不要自己 grep 用户全盘
- 文件读: `read_file`(支持 `offset`/`limit`/`pages`,可读 image/PDF/ipynb)
- 文件写: `edit_file`(small change, old/new) > `multi_edit_file`(多处) > `propose_patch`(diff) > `write_text_file`(新建)
- 列目录/找文件: `list_cwd` / `glob_files`,**不要** `exec_shell("find/ls")`
- 文本搜: `grep_text`(支持 `context_lines`),**不要** `exec_shell("grep")`
- 长跑命令(dev server / 长测试) → `exec_shell(run_in_background=True)` 拿 task_id,
  之后 `read_shell_output(task_id)` 轮询、`kill_shell(task_id)` 终止
- 编辑前必须先 `read_file` 该文件(否则系统会拒)
- `exec_shell` 只用于编译 / 测试 / git / 没有专用 skill 的本地命令

## 卡住时的退出

- **不可逆操作**(rm -rf / push --force / drop db / 发真实频道) → Final Answer 描述影响并请求用户确认,不要赌
- 连续 2 轮 tool 失败 / 不知道用户想要哪个选项 → Final Answer 报告卡点,等用户回
- 与其硬挤一个似是而非的答案,不如诚实报告"卡住了"

## 多步任务

- ≥3 步任务: 第一轮就 `todo_write` 列完整计划(全 pending)
- 每完成一项**立即** `todo_write` 标记 completed,然后再开下一个
- 同时最多 1 个 in_progress
- 任务调整时, 先 `todo_write` 更新清单, 再继续

## 跨会话记忆 (按需,不要每次都用)

- `recall` — 用户提到旧项目/旧上下文 → 第一轮就查
- `remember` — 项目级事实(项目名、deadline、API key 路径)
- `note_user` — 用户偏好(语言、详略、技术水平)
- `update_soul` — 你自己学到的持久教训(不是一次性观察)

## 子 agent (按需,不是默认)

只有任务能拆成互不依赖的专长子任务时才用 `call_agent_parallel`;顺序依赖或一人足够时不要委派。投票、编排、修复闭环等高级能力按动态工具目录说明使用。
"""

REACT_NO_TOOLS_NOTE = """
(本会话未启用真实工具,Action 仅作思考标注,填 "none" 即可。)
"""

REACT_OBSERVATION_FOLLOWUP = (
    "继续下一轮推理。若证据已经足够，直接输出 Final Answer；若还要调用任何工具，"
    "必须先输出一条 Update:，用 1-3 句概括这次 Observation 新确认了什么、"
    "它如何影响判断或下一步。不要写空状态，不要复述工具名、参数或内部协议。"
)


@dataclass
class ReActStep:
    iteration: int
    thought: str = ""
    # Concise, explicitly public checkpoint emitted between tool rounds.
    # Unlike ``thought`` this may be rendered in the main conversation.
    public_update: str = ""
    action: str = ""
    observation: str = ""
    raw_llm_output: str = ""
    # Multi-action support: when the model emits more than one tool
    # call inside a single Action: block, the parser populates this
    # list and ``action`` becomes a "; "-joined summary view so the
    # 12+ existing readers (journal, guards, prefetch, openai
    # formatting, …) keep seeing a meaningful string. When only one
    # action is present, ``actions`` is ``[action]`` for symmetry.
    actions: list[str] = field(default_factory=list)
    # Per-action execution receipts captured by the dispatcher: each
    # entry is ``{"tool_name": str, "ok": bool, "observation": str,
    # "duration_ms": int, "call_id": str, "trusted_execution": bool,
    # "execution_source": str, "effect_receipt": dict}``. The final fields
    # are computed by the server from canonical handler identity; model text,
    # tool output, names, affinity, and ``trusted_source`` strings cannot set
    # them. Empty when no tools ran this step (e.g. ``Action: none``).
    action_results: list[dict[str, object]] = field(default_factory=list)


@dataclass
class ReActResult:
    final_answer: str
    steps: list[ReActStep] = field(default_factory=list)
    terminated_reason: str = "final_answer"
    success: bool = True
    completion_receipt: dict[str, object] = field(default_factory=dict)
    completion_decision: dict[str, object] = field(default_factory=dict)

    def to_trace_text(self) -> str:
        from runtime.core.cerebrum.react_parsing import (
            _escape_md_brackets,
            _safe_for_streamdown,
            _summarize_observation,
        )

        if not self.steps:
            return _safe_for_streamdown(self.final_answer)

        meaningful = [
            s
            for s in self.steps
            if (s.thought and s.thought.strip())
            or (s.action and s.action.strip() and s.action.lower() != "none")
            or (s.observation and s.observation not in ("N/A", ""))
        ]
        if not meaningful:
            return _safe_for_streamdown(self.final_answer)

        trace_lines: list[str] = []
        for step in meaningful:
            trace_lines.append(f"**Iteration {step.iteration}**")
            if step.thought:
                trace_lines.append(f"- {_escape_md_brackets(step.thought)}")
            if step.action and step.action.lower() != "none":
                trace_lines.append(f"- `{step.action}`")
            if step.observation and step.observation != "N/A":
                trace_lines.append(
                    f"- {_escape_md_brackets(_summarize_observation(step.observation))}",
                )
            trace_lines.append("")
        trace_md = "\n".join(trace_lines).rstrip()

        short = len(self.final_answer) < 120
        refer_phrases = (
            "见上方",
            "如上",
            "上方",
            "上面",
            "以上",
            "报告如上",
            "见上",
            "as above",
            "see above",
            "above",
        )
        refers_back = any(p in self.final_answer for p in refer_phrases)
        open_attr = " open" if (short or refers_back) else ""

        summary = f"🧠 ReAct 轨迹 · {len(meaningful)} 轮"
        return (
            f"<details{open_attr}>\n<summary>{summary}</summary>\n\n"
            f"{trace_md}\n\n</details>\n\n"
            f"{_safe_for_streamdown(self.final_answer)}"
        )


@dataclass
class ReActRecipe:
    name: str
    max_iterations: int
    temperature: float


_DEFAULT_REACT_RECIPES: list[ReActRecipe] = [
    ReActRecipe(name="conservative", max_iterations=18, temperature=0.1),
    ReActRecipe(name="balanced", max_iterations=30, temperature=0.3),
    ReActRecipe(name="aggressive", max_iterations=45, temperature=0.5),
]


def _native_tool_calls_missing_required_args(tool_calls: Any) -> list[str]:
    """Return native calls that cannot be safely executed with empty input."""

    allow_empty = {
        "list_cwd",
        "todo_read",
        "bb_keys",
        "memory_list",
    }
    missing: list[str] = []
    for call in tool_calls or []:
        name = str(getattr(call, "name", "") or "").strip()
        value = getattr(call, "input", None)
        if name and name not in allow_empty and not value:
            missing.append(name)
    return missing


def _safe_react_error_message(exc: BaseException, *, limit: int = 1200) -> str:
    """Return a user-visible terminal model error without leaking secrets.

    Provider errors carry important status evidence (for example ``http_402``)
    that the realtime benchmark uses to separate infrastructure outages from
    agent failures.  Keep that evidence, but pass the message through the
    process redactor before it reaches a turn item.
    """

    message = str(exc).strip() or type(exc).__name__
    try:
        from runtime.platform.observability.redactor import redact_text

        message = redact_text(message)
    except Exception:  # pragma: no cover - diagnostics must never mask failure
        # If the redactor itself is unavailable, preserve only the exception
        # class.  Dropping detail is safer than exposing an embedded token.
        message = type(exc).__name__
    return message[:limit]
