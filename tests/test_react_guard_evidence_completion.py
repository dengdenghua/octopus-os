"""Completion guards for evidence-backed project inspection answers."""

from __future__ import annotations

from runtime.core.cerebrum.react_guards import (
    _code_mode_inspection_answer_fragment_guard,
    _code_mode_missing_inspection_tool_guard,
    _explicit_source_paths,
    _incomplete_final_answer_guard,
)
from runtime.core.cerebrum.react_loop import _final_answer_needs_pre_emit_guard
from runtime.core.cerebrum.react_types import ReActStep


def test_explicit_source_paths_preserve_named_project_files() -> None:
    goal = (
        "比较 runtime/protocol/items.py 与 frontend/src/core/realtime/items.ts:42，并核对 README.md"
    )

    assert _explicit_source_paths(goal) == [
        "runtime/protocol/items.py",
        "frontend/src/core/realtime/items.ts",
        "readme.md",
    ]


def test_named_file_guard_reports_every_uncovered_file() -> None:
    message = _code_mode_missing_inspection_tool_guard(
        [],
        "两个定义一致。",
        goal=("比较 runtime/protocol/items.py 与 frontend/src/core/realtime/items.ts"),
        file_tools_visible=True,
        grounded_source_paths=frozenset({"runtime/protocol/items.py:100"}),
    )

    assert message is not None
    assert "frontend/src/core/realtime/items.ts" in message
    assert "runtime/protocol/items.py" not in message


def test_named_file_guard_accepts_exact_source_grounding() -> None:
    assert (
        _code_mode_missing_inspection_tool_guard(
            [],
            "两个定义一致。",
            goal=("比较 runtime/protocol/items.py 与 frontend/src/core/realtime/items.ts"),
            file_tools_visible=True,
            grounded_source_paths=frozenset(
                {
                    "runtime/protocol/items.py:108:1",
                    "frontend/src/core/realtime/items.ts:44",
                }
            ),
        )
        is None
    )


def test_named_file_guard_accepts_successful_reads_for_each_file() -> None:
    steps = [
        ReActStep(
            iteration=1,
            action='read_file({"path":"runtime/protocol/items.py"})',
            observation="class AgentMessageItem",
        ),
        ReActStep(
            iteration=2,
            action='read_file({"path":"frontend/src/core/realtime/items.ts"})',
            observation="export interface AgentMessageItem",
        ),
    ]

    assert (
        _code_mode_missing_inspection_tool_guard(
            steps,
            "两个定义一致。",
            goal=("比较 runtime/protocol/items.py 与 frontend/src/core/realtime/items.ts"),
            file_tools_visible=True,
        )
        is None
    )


def test_named_file_guard_accepts_successful_writes_for_named_file() -> None:
    # Regression: "Create a file hello.py ..." is classified as a
    # project-inspection goal because it names hello.py; the model's own
    # successful write is real grounding, so a redundant read_file must
    # not be demanded — previously the loop rejected the final answer
    # three times and died with guard_impasse.
    steps = [
        ReActStep(
            iteration=1,
            action='write_text_file({"path":"hello.py","content":"print(42)\\n"})',
            observation="(real tool execution succeeded) write_text_file",
            action_results=[{"ok": True}],
        ),
        ReActStep(
            iteration=2,
            action='exec_shell({"command":"python3 hello.py"})',
            observation="(real tool execution succeeded) exec_shell",
            action_results=[{"ok": True}],
        ),
    ]

    assert (
        _code_mode_missing_inspection_tool_guard(
            steps,
            "文件已创建并运行,输出 42。",
            goal="Create a file hello.py that prints 42, then run it and report the output.",
            file_tools_visible=True,
        )
        is None
    )


def test_named_file_guard_still_rejects_unwritten_read_target() -> None:
    steps = [
        ReActStep(
            iteration=1,
            action='write_text_file({"path":"other.py","content":"x"})',
            observation="(real tool execution succeeded) write_text_file",
            action_results=[{"ok": True}],
        ),
    ]

    message = _code_mode_missing_inspection_tool_guard(
        steps,
        "hello.py 没看过,先不答。",
        goal=("比较 hello.py 与 other.py"),
        file_tools_visible=True,
    )

    assert message is not None
    assert "hello.py" in message
    assert "other.py" not in message


def test_incomplete_final_answer_rejects_future_action_not_result() -> None:
    message = _incomplete_final_answer_guard("我先 grep 确认这三个字段在两端的具体定义。")

    assert message is not None
    assert "not a completed answer" in message


def test_incomplete_final_answer_rejects_continuation_promise() -> None:
    # Regression (thread tj1qarRWyf8H5zzT6dR_-u, trn_d1cd69902f864d67 /
    # trn_23c29c3f25ef4e68): after "我接下来会核对…" was rejected, the model
    # rephrased to "我继续核对…确认它们是否也跟随大涨" — 继/接 continuation
    # prefixes were missing from preparatory_start/future_action, so the
    # rephrased announce was delivered as a completed turn with zero tool calls.
    message = _incomplete_final_answer_guard(
        "我继续核对广义健康板块（健康保险、数字健康、远程医疗、健身）"
        "在2026年8月20日的行情，确认它们是否也跟随医药大涨。"
    )

    assert message is not None
    assert "not a completed answer" in message


def test_incomplete_final_answer_rejects_bare_continuation_verb_start() -> None:
    # A turn-final answer that opens with a bare 继续推进/接着核实 plan is a
    # placeholder, not a delivery — same shape as the 我继续 regression above.
    message = _incomplete_final_answer_guard(
        "继续推进当前任务，先核对已完成内容，确保下一步操作有明确依据。"
    )

    assert message is not None
    assert "not a completed answer" in message


def test_incomplete_final_answer_accepts_delivered_answer_that_mentions_continuation() -> None:
    # A concrete conclusion that merely mentions continuing later must pass:
    # the guard targets plan-only candidates, not finished reports that
    # honestly state what remains.
    assert (
        _incomplete_final_answer_guard(
            "结论：A方案成本更低。综合三轮报价数据，A方案总价 12.4 万，B 方案 15.1 万，"
            "差异 2.7 万主要来自许可费；后续扩容仍可继续评估。"
        )
        is None
    )


def test_incomplete_final_answer_rejects_negated_conclusion_signal() -> None:
    message = _incomplete_final_answer_guard(
        "我先 grep 确认具体定义，然后整理差异；现在还没有给出结论。"
    )

    assert message is not None
    assert "not a completed answer" in message


def test_incomplete_final_answer_rejects_failed_read_and_future_retry() -> None:
    message = _incomplete_final_answer_guard(
        "两次 `read_file` 都因为路径不对而失败。我先把项目目录探清楚再读。"
    )

    assert message is not None
    assert "not a completed answer" in message


def test_incomplete_final_answer_accepts_concrete_conclusion() -> None:
    assert (
        _incomplete_final_answer_guard(
            "结论：两端都定义 phaseId、parentItemId 和 progressSequence，字段命名一致。"
        )
        is None
    )


def test_incomplete_final_answer_accepts_short_concrete_reply() -> None:
    assert _incomplete_final_answer_guard("已思考") is None


def test_incomplete_final_answer_rejects_verify_promise_with_no_result() -> None:
    # Regression: kimi-k3 emitted "我会先核实…然后…做出分析" as its terminal
    # answer. The verb 核实 was missing from the evidence-action keyword list,
    # so the guard let a pure preparatory promise pass and the turn completed
    # without any tool call or concrete finding.
    message = _incomplete_final_answer_guard(
        "我会先核实项目当前的开发流程与代码规范配置，"
        "明确团队在协作与质量控制上的具体约束，"
        "然后基于这些事实对项目的工程成熟度做出深度分析。"
    )

    assert message is not None
    assert "not a completed answer" in message


def test_incomplete_final_answer_rejects_start_work_promise_with_supported_conclusion() -> None:
    # Regression (trn_514bd9600295430b): "我这就开始…逐项过一遍…用具体数据支撑
    # 结论" announced upcoming work and only promised (not delivered) a
    # conclusion, yet the bare word 结论 satisfied result_signal and the turn
    # completed without any real analysis. 开始/过一遍/支撑结论 must be caught.
    message = _incomplete_final_answer_guard(
        "我这就开始深度分析。先把项目的核心代码结构、模块关系、测试覆盖和工程"
        "质量逐项过一遍，用具体数据支撑结论。"
    )

    assert message is not None
    assert "not a completed answer" in message


def test_incomplete_final_answer_rejects_moqing_plan_promise() -> None:
    # Regression (thread tAUhAq-cjtzfSOmxq-JGu5): deepseek-v4-flash opened a
    # realtime audit with "我来分析这个项目，先并行摸清仓库结构…确定审计的重点
    # 范围。" — a pure plan statement, zero tool execution. The verb 摸清 was
    # missing from the evidence-action keyword list, so the first "查看" phrasing
    # got rejected, the model rephrased with 摸清, and the turn silently completed
    # with a plan instead of a result. 摸清 family must be caught like 探清.
    message = _incomplete_final_answer_guard(
        "我来分析这个项目，先并行摸清仓库结构、当前分支改动和近期提交，确定审计的重点范围。"
    )

    assert message is not None
    assert "not a completed answer" in message


def test_incomplete_final_answer_accepts_delivered_moqing_conclusion() -> None:
    # A completed audit that reports it *did* map the repo (past tense) must keep
    # passing — neither preparatory_start nor future_action matches 摸清了, so the
    # verb alone must not trigger the guard.
    assert (
        _incomplete_final_answer_guard(
            "结论：我摸清了仓库结构，共 40 个包，核心逻辑在 runtime/core。"
        )
        is None
    )


def test_incomplete_final_answer_rejects_collect_plan_promise() -> None:
    # Regression (same thread as the 摸清 leak): a follow-up turn reopened with
    # "直接进入实质分析，先并行收集仓库结构、Git 工作区和最近提交的数据。" —
    # again a plan-statement with zero execution. 收集/拉取/采集/搜集 were missing
    # from the evidence-action verb list, so the future-intent + action shape
    # passed the guard. A delivered report that *did* collect ("我收集了全部配置，
    # 结论是…") must still pass.
    assert (
        _incomplete_final_answer_guard(
            "直接进入实质分析，先并行收集仓库结构、Git 工作区和最近提交的数据。"
        )
        is not None
    )
    assert _incomplete_final_answer_guard("我收集了全部配置，结论是配置统一。") is None


def test_incomplete_final_answer_rejects_deferred_conclusion_announcement() -> None:
    message = _incomplete_final_answer_guard("我先核对两端定义，之后再给出结论。")

    assert message is not None
    assert "not a completed answer" in message


def test_incomplete_final_answer_rejects_look_scan_announcement() -> None:
    # Regression (thread tPO8mDlhtQev_grzsY1etH): announce-then-stop turns phrased
    # as "我先实际看一下前端代码再下结论" / "我来看看这个仓库的代码再点评" slipped
    # through. 看一下/看看/扫一遍 (future-intent 看/扫) were not evidence-action
    # verbs, and 下结论 ("draw a conclusion") was not a deferred-conclusion marker,
    # so the bare 结论 scored as a delivered result and the turn completed.
    assert (
        _incomplete_final_answer_guard(
            "我先实际看一下前端代码再下结论——扫一遍 frontend/ 的结构、样式体系和关键页面，"
            "给出有依据的评价。"
        )
        is not None
    )
    assert _incomplete_final_answer_guard("我来看看这个仓库的代码再点评。") is not None
    assert _incomplete_final_answer_guard("我先看下代码再给结论。") is not None


def test_incomplete_final_answer_accepts_past_tense_look_report() -> None:
    # The intent forms 看一下/看看 are future actions; the past-tense 看了/看过 must
    # keep passing as a delivered report even though the same 看 character appears.
    assert (
        _incomplete_final_answer_guard("我先看了代码，发现这个项目状态管理用了 Zustand，整体清晰。")
        is None
    )
    assert (
        _incomplete_final_answer_guard("我看过代码后，发现整体结构清晰，因此评价为良好。") is None
    )


def test_incomplete_final_answer_accepts_delivered_conclusion_still() -> None:
    # A real delivered conclusion must keep passing even though it contains
    # the verb 过一遍/开始 and the word 结论.
    assert (
        _incomplete_final_answer_guard("结论：我把两端定义过了一遍，开始、字段命名与类型完全一致。")
        is None
    )


def test_incomplete_final_answer_accepts_long_report_with_roadmap_opening() -> None:
    report = (
        "我将先检查当前项目，再给出结论。\n\n"
        "## 审计结论\n"
        "1. 核心运行链路已经完成统一收敛，工具执行结果会进入同一条时间线。\n"
        "2. 中断和等待状态不会再被最终答复误标为完成。\n"
        "3. 回归测试已覆盖状态同步、协议清洗和终态交付。\n\n"
        "因此本轮没有发现阻断性问题，剩余风险是旧历史记录需要刷新。"
    )
    assert _incomplete_final_answer_guard(report) is None


def test_inspection_answer_fragment_guard_rejects_bare_source_line() -> None:
    message = _code_mode_inspection_answer_fragment_guard(
        'str = ""',
        goal="只读核对 runtime/core/cerebrum/react_loop.py，说明 public_update 如何进入公开时间线",
        file_tools_visible=True,
    )

    assert message is not None
    assert "bare source-code fragment" in message


def test_inspection_answer_fragment_guard_accepts_concrete_conclusion() -> None:
    assert (
        _code_mode_inspection_answer_fragment_guard(
            "结论：两个 realtime 标志会让每轮 public_update 进入公开时间线。",
            goal=(
                "只读核对 runtime/core/cerebrum/react_loop.py，"
                "说明 public_update 如何进入公开时间线"
            ),
            file_tools_visible=True,
        )
        is None
    )


def test_inspection_answer_fragment_guard_is_scoped_to_project_inspection() -> None:
    assert (
        _code_mode_inspection_answer_fragment_guard(
            'str = ""',
            goal="解释 Python 类型注解语法",
            file_tools_visible=True,
        )
        is None
    )


def test_pre_emit_buffers_preparatory_chat_but_not_conclusion() -> None:
    assert _final_answer_needs_pre_emit_guard(
        "我先搜索官方资料，当前还没有给出结论。",
        is_code_mode=False,
    )
    assert not _final_answer_needs_pre_emit_guard(
        "结论：两个实现都保留了因果顺序，但本项目还额外暴露了阶段标识。",
        is_code_mode=False,
    )


def test_pre_emit_does_not_buffer_report_with_code_fence() -> None:
    """Regression: a report that quotes a markdown code fence must still
    stream. Previously `` ``` `` in the body forced full buffering, so any
    report containing code rendered all at once instead of typewriter-style."""
    fence = "```"
    report = (
        "## 调研结论\n\n"
        "两个实现都保留了因果顺序。涉及代码：\n\n"
        f"{fence}python\nprint(1)\n{fence}\n\n"
        "综上，建议采用方案 A。"
    )
    assert not _final_answer_needs_pre_emit_guard(report, is_code_mode=False)
    # code mode alone must not force buffering either — in the realtime
    # workbench a mounted workspace makes ``is_code`` true even for a
    # personal-space report, so treating it as a hard gate made every
    # final report render wholesale.
    assert not _final_answer_needs_pre_emit_guard(report, is_code_mode=True)


def test_pre_emit_still_buffers_executable_risk() -> None:
    """Only genuinely dangerous executable content forces buffering."""
    assert _final_answer_needs_pre_emit_guard(
        "执行 exec(open('/etc/passwd').read()) 来查看。",
        is_code_mode=True,
    )
    assert _final_answer_needs_pre_emit_guard(
        "通过 subprocess.run 删除该目录。",
        is_code_mode=False,
    )


def test_pre_emit_ignores_dynamic_exec_inside_fenced_code_block() -> None:
    """Regression: a code deliverable that quotes eval/exec inside a markdown
    fence must stream instead of buffering the whole report. The display-only
    token is not a runtime call the agent is about to run; the terminal guard
    still vets the full text."""
    fence = "```"
    report = (
        "## 实现方案\n\n"
        "核心逻辑如下：\n\n"
        f"{fence}python\n"
        "data = eval(f'{{x}}')\n"
        "exec('print(1)')\n"
        f"{fence}\n\n"
        "综上，建议采用该方案。"
    )
    assert not _final_answer_needs_pre_emit_guard(report, is_code_mode=True)
    # The same dynamic-exec call in prose (outside any fence) still buffers.
    assert _final_answer_needs_pre_emit_guard(
        "我准备执行 exec('print(1)') 来验证。",
        is_code_mode=True,
    )


def test_incomplete_final_answer_rejects_wo_lai_announcement() -> None:
    """Regression: ``我来`` announces an action exactly as ``我将`` does.

    Thread teD7hPf9dkGOExwO0dIiBE burned three consecutive turns on
    "我来查看黑板…" / "我先查看…" / "我将读取…" -- each emitted zero tool
    calls and each was recorded as a clean completion, so the user asked
    "为什么失败" three times and got another announcement every time.
    ``我来`` was simply missing from the preparatory-verb list.
    """
    message = _incomplete_final_answer_guard(
        "我来查看黑板上的实际键列表，确认刚才并行调研哪些子任务写回了结果、哪些缺失，以此定位失败环节。"
    )

    assert message is not None
    assert "not a completed answer" in message


def test_incomplete_final_answer_rejects_result_as_inspection_target() -> None:
    """A result word naming what will be looked at is not a delivered result.

    "确认哪些子任务写回了结果" made 结果 the object of a pending inspection,
    but the bare keyword scored it as a delivered conclusion and cancelled
    the guard.
    """
    message = _incomplete_final_answer_guard(
        "我先查看黑板与任务清单的当前状态，确认哪些并行调研子任务有结果、哪些缺失，再做下一步处理。"
    )

    assert message is not None
    assert "not a completed answer" in message


def test_incomplete_final_answer_rejects_apology_then_another_promise() -> None:
    message = _incomplete_final_answer_guard(
        "抱歉，刚才绕了圈子。我现在直接查黑板上并行调研的实际结果和任务清单，给你一个明确的失败定位。"
    )

    assert message is not None


def test_incomplete_final_answer_still_accepts_delivered_failure_analysis() -> None:
    """The fix must not block the answer the user actually wanted."""
    assert (
        _incomplete_final_answer_guard(
            "3 个方向成功，2 个失败。失败原因是 SSL 断连（competitive）"
            "和超出 25 轮上限（tech），与研究方向本身无关。"
        )
        is None
    )


def test_incomplete_final_answer_rejects_bare_intention_verbs() -> None:
    """Future-intent bare verbs (查/找/读/搜/分析) are plan statements, not
    delivered answers. Regression: the realtime thread txhjBkLKtmrjdfdJp0FQhN
    opened with "我来帮你查这三组数据…" which slipped through because bare 查
    was not an evidence-action verb, so the generic opener streamed to the user
    as the answer before any real work."""
    assert (
        _incomplete_final_answer_guard(
            "我来帮你查这三组数据：智能床垫全球体量、传统床/床品全球体量、"
            "温度影响睡眠的科学依据，最后汇总成带来源的清单。"
        )
        is not None
    )
    assert _incomplete_final_answer_guard("我来找一下相关文档再回答。") is not None
    assert _incomplete_final_answer_guard("我来读一下配置文件，然后再给结论。") is not None
    assert _incomplete_final_answer_guard("我先搜一下官方资料。") is not None
    assert _incomplete_final_answer_guard("我需要找一下资料再回答。") is not None


def test_incomplete_final_answer_accepts_past_tense_bare_intention_verbs() -> None:
    """Completed, past-tense statements with the same characters must keep
    passing — only the future-intent prefix triggers the guard."""
    assert (
        _incomplete_final_answer_guard("我查了资料，结论是智能床垫市场规模 17.6 亿美元。") is None
    )
    assert _incomplete_final_answer_guard("我找到了答案：两者的定义完全一致。") is None
    assert _incomplete_final_answer_guard("我分析了数据，得出三点结论。") is None


def test_incomplete_final_answer_rejects_promised_synthesis() -> None:
    """Regression (thread t0Wn5Zhvh3VUFwoAR2uP4M): "四个方向都收齐了，马上
    综合。" and "抱歉刚才掉线了，马上把4位成员的成果综合出来。" were each
    delivered as a *completed* turn — a promise to synthesize with zero actual
    output. A short, body-less promise-to-produce must be rejected like the
    read/search announce prefixes."""
    assert _incomplete_final_answer_guard("四个方向都收齐了，马上综合。") is not None
    assert (
        _incomplete_final_answer_guard("抱歉刚才掉线了，马上把4位成员的成果综合出来。") is not None
    )
    assert (
        _incomplete_final_answer_guard(
            "证据已足够成稿：市场、政策、成果、玩家、融资、风险六个维度都有可引用来源。"
            "更新任务状态后输出完整报告。"
        )
        is not None
    )
    assert _incomplete_final_answer_guard("马上综合给出：") is not None


def test_incomplete_final_answer_accepts_delivered_synthesis_tail() -> None:
    """A promise that actually carries a delivered body/numbers must keep
    passing — the guard targets body-less promises, not finished outputs."""
    assert (
        _incomplete_final_answer_guard(
            "马上把结论给你：A 方案 12.4 万，B 方案 15.1 万，差异 2.7 万。"
        )
        is None
    )
    assert (
        _incomplete_final_answer_guard(
            "综合如下：市场 1578 亿美元，政策中美加码，产业英伟达-礼来已布局。"
        )
        is None
    )
    assert (
        _incomplete_final_answer_guard(
            "结论：A方案成本更低。综合三轮报价数据，A方案总价 12.4 万，B 方案 15.1 万，"
            "差异 2.7 万主要来自许可费；后续扩容仍可继续评估。"
        )
        is None
    )

