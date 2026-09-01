"""System-prompt guidance + tool / capability / skill-catalog sections for
the PHASE 3 assembly.

Leaf of the prompt-assembly split. Core guidance (workspace rules, project
profile, code-mode steering, goal-mode, long-task, memory/templates, user
preferences, reporting cadence, tool-choice policy) and delegation guidance
(workflow preset, mode contract, codex composer, output style, thinking,
personal mode, agent auto-delegation, swarm, research) plus the tool sections
(capability activation, pinned plugin auto-load + mention-history side
effects, the skill catalog, todo protocol guidance, and the PLAN-FIRST /
CODEX PLAN lock blocks). Never imports ``react_loop``.
"""

from __future__ import annotations

import logging

from runtime.core.cerebrum._react_prompt_assembly_state import _AssemblyState
from runtime.core.cerebrum.react_browser_iteration import _ensure_browser_operation_skills
from runtime.core.cerebrum.react_context import (
    _build_code_agent_mode_prompt,
    _build_personal_agent_mode_prompt,
    _build_project_profile_prompt,
    _build_project_signals_prompt,
    _build_workflow_preset_prompt,
    _format_skill_catalog,
    _load_project_rules,
)
from runtime.core.cerebrum.react_native import STRICT_EXPLICIT_READ_TOOL_NAMES
from runtime.core.cerebrum.react_types import REACT_NO_TOOLS_NOTE
from runtime.core.cerebrum.todo_protocol import render_todo_protocol_guidance

_logger = logging.getLogger(__name__)

_DELEGATION_HYGIENE = (
    "- Ask workers for compact, evidence-backed findings and any files "
    "touched. After the observation returns, synthesize the outputs "
    "yourself, resolve conflicts, verify critical claims, and produce one "
    "integrated final result.\n"
    "- Never finish with raw worker logs or a partial plan. If workers fail "
    "partially, use the surviving outputs and state the residual risk.\n"
)


def _build_auto_delegation_guidance(state: _AssemblyState) -> str:
    """Delegation guidance for a non-swarm turn.

    Two variants, because the DEFAULT one actively contradicts the
    ``audit.deep`` preset. That preset's contract is "orchestrate by
    default, the bar is inverted" — while this block used to open with
    "Current mode is single-agent", tell the model that simple or sequential
    work should be done alone, and cap fan-out at "exactly one
    ``call_agent_parallel`` batch for the current turn". A live run confirmed
    the cost: with the preset text present and ``run_orchestration`` in the
    catalog, the model still ran 17 atomic tool calls and zero spawns. The
    nearest, most concrete instruction wins, and this block sits ~14 lines
    below the preset.

    The operational hygiene (synthesize yourself, never ship raw worker logs)
    is orthogonal to how wide to go, so both variants keep it.
    """
    preset = str(state.workflow_preset_value or "").strip().lower()
    # Backward compatibility
    if preset in ("audit.ultracode", "ultracode"):
        preset = "audit.deep"

    if preset == "audit.deep":
        return (
            "\n<agent-auto-delegation-guidance>\n"
            "This turn runs the deep audit workflow. You are the lead of a "
            "multi-agent run, NOT a single agent: the <workflow-preset> block "
            "above is authoritative on how wide to go, and nothing here "
            "narrows it.\n"
            "\n"
            "- Prefer `run_orchestration` as the primary driver; it fans out, "
            "dedupes, optionally votes, and loops until no new findings.\n"
            "- Fan out DURING the understanding phase, not after it. Workers "
            "carry their own tools and read files themselves, so you do not "
            "need to build context for them first. Allow yourself 1-2 "
            "locating calls (list a dir, glob a path) and then orchestrate. "
            "Reading the codebase yourself before delegating is the failure "
            "mode this block exists to prevent: it consumes the whole "
            "iteration budget and ships zero spawns.\n"
            "- `call_agent_parallel` is for one-off lanes you place yourself. "
            "Multiple batches across the turn are expected and correct — "
            "chain them by phase, orchestrating each phase rather than doing "
            "the early phases alone.\n"
            "- Pick roles from the actual lanes (researcher, explorer, "
            "debugger, reviewer, architect, security-review). Do not call "
            "serial `call_agent` for work that can run concurrently.\n"
            "- Doing the whole task yourself with atomic tools is the "
            "exception here, reserved for genuinely trivial work.\n"
            + _DELEGATION_HYGIENE
            + "</agent-auto-delegation-guidance>"
        )
    return (
        "\n<agent-auto-delegation-guidance>\n"
        "Current mode is single-agent Agent/ReAct. You remain the lead, "
        "but you may use real subagents when parallelism will materially "
        "improve speed or quality.\n"
        "\n"
        "Use `call_agent_parallel` proactively when the task has 2-4 "
        "independent work lanes: e.g. market research lanes, competitor "
        "comparison lanes, frontend/backend/test investigation lanes, "
        "or reproduce/read-code/review lanes. This tool spawns real "
        "specialist turns concurrently; it is not a display shortcut.\n"
        "\n"
        "Decision policy:\n"
        "- Simple or sequential work: do it yourself with atomic tools.\n"
        "- Large ambiguous work: first clarify if needed, then "
        "todo_write a visible plan before fan-out.\n"
        "- If using subagents, make exactly one `call_agent_parallel` "
        "batch for the current turn. Pick roles from the actual lanes "
        "(researcher, explorer, debugger, reviewer, architect, "
        "security-review). Do not call serial `call_agent`.\n"
        + _DELEGATION_HYGIENE
        + "</agent-auto-delegation-guidance>"
    )


# Explicit repair authorisation inside an audit turn. Deliberately requires an
# imperative verb of *action*, not a mere mention of problems: "有哪些问题需要
# 修复" is a question about repairs, not a mandate to perform them, and a false
# positive here would let an audit turn silently rewrite code. Matched against
# the user's own instruction only — never against tool output, which is
# untrusted and frequently contains words like "fix" in unrelated contexts.
_FIX_AUTHORIZATION_PATTERNS = (
    "继续修复",
    "开始修复",
    "直接修复",
    "去修复",
    "修复吧",
    "修一下",
    "修掉",
    "全修",
    "都修",
    "干活",
    "动手",
    "改吧",
    "开始改",
    "直接改",
    "去改",
    "开始优化",
    "帮我修",
    "让你修",
    "现在修",
)
_FIX_AUTHORIZATION_EN = (
    "go ahead and fix",
    "start fixing",
    "just fix",
    "fix it now",
    "fix them all",
    "apply the fix",
    "make the change",
)


def _fix_authorization_present(state: _AssemblyState) -> bool:
    """Whether the user's instruction authorises landing writes this turn.

    An explicit host-provided flag wins; otherwise the goal text is matched
    against imperative repair instructions. Returns False on any uncertainty
    so the read-only default holds — the cost of a missed authorisation is one
    clarifying round, while the cost of a false positive is an unrequested
    edit.
    """
    for source in (state.user_context, state.metadata):
        flag = source.get("fix_authorized") if isinstance(source, dict) else None
        if isinstance(flag, bool):
            return flag
    goal = str(getattr(state.intent, "normalized_goal", "") or "").strip()
    if not goal:
        return False
    lowered = goal.lower()
    if any(token in goal for token in _FIX_AUTHORIZATION_PATTERNS):
        return True
    return any(token in lowered for token in _FIX_AUTHORIZATION_EN)


def _assemble_core_guidance(state: _AssemblyState) -> None:
    """Approval gate + workspace / project / code-mode / cadence sections."""
    if state.approval_provider is not None:
        # Approval-gate etiquette only means anything when a gate exists to
        # be tripped. Keeping it out of REACT_SYSTEM_PROMPT_BASE stops every
        # plain-chat turn — which can never see an approval request — from
        # paying for it (the base prompt is charged on literally every turn;
        # see tests/test_system_prompt_size.py).
        state.system_parts.append(
            "\n- 如果任务明确要求通过**内置审批门**演示批准/拒绝,应发起一次对应高风险"
            "工具调用,让系统生成真实审批请求。收到拒绝后不得重试危险动作或再次询问同一"
            "确认;应把 `approval_denied` 等事实准确写入安全计划,完成仍可安全完成的收尾"
        )
    if isinstance(state.effective_wp, str) and state.effective_wp.strip():
        _effective_wp_text = state.effective_wp.strip()
        _workspace_label = (
            "个人隔离工作目录"
            if not (isinstance(state.wp, str) and state.wp.strip())
            else "当前工作目录"
        )
        state.system_parts.append(
            f"\n{_workspace_label}: {_effective_wp_text}\n"
            "所有文件操作（list_cwd / read_file / write 等）的相对路径都基于此目录。"
            "分析或编程时请从这个目录开始,不要使用其他目录。"
        )
        if isinstance(state.wp, str) and state.wp.strip():
            _rules = _load_project_rules(_effective_wp_text)
            if _rules:
                state.system_parts.append("\n<project-rules>\n" + _rules + "\n</project-rules>")
            _profile = _build_project_profile_prompt(
                _effective_wp_text,
                include_diagnostics=state.is_code_mode,
            )
            if _profile:
                state.system_parts.append(
                    "\n<project-profile>\n" + _profile + "\n</project-profile>"
                )
        if state.is_code_mode:
            state.system_parts.append(
                "\n<code-mode>\n"
                "**编程三阶段** (强制):\n"
                "1. **理解** (1-3 轮): `list_cwd` + `read_file` 摸清目录与关键文件;"
                "禁止写操作。Discovery 用 `list_cwd`/`read_file`/`grep_text`/`glob_files`,"
                "不要用 `exec_shell` 跑 find/ls/cat/grep。\n"
                "2. **执行** (2-N 轮): `todo_write` 列计划 → 小步改 (`edit_file`/`multi_edit_file`/"
                "`propose_patch`) → 相关、低风险文件可成组修改。完成一个可验证里程碑后"
                "批量更新 todo；不要在每个微小编辑之间重复清单往返。"
                "每个连贯改动批次完成后跑相应 lint/typecheck/test。\n"
                "3. **验证** (1-2 轮): 项目自带 lint/typecheck/test 跑过再 Final Answer。"
                "失败回阶段 2 修;不要 fake 验证通过。\n"
                "**第一轮 Thought 必须声明阶段**(理解/执行/验证)。\n"
                "**收工硬约束**: 仍有 pending/in_progress todo、改动未验证、"
                "或工具/权限/登录阻塞时, 不能给完成式 Final Answer;"
                "用 Final Answer 描述阻塞 + 列出未完成 todo + 已做过的验证。\n"
                "**进度 ≠ 收尾**: 阶段总结、下一步计划、'接下来还要读/改 X' 这类"
                "中间产出必须用 commentary/进度消息, 绝不能当作 Final Answer 提交;"
                "只有完成全部承诺的读取/修改/验证动作后才给完成式 Final Answer。\n"
                "**协议块走工具通道**: `Thought:` / `Action: name({args})` / "
                "`Observation:` 等 ReAct 协议块必须走工具调用通道, 绝不能写进 "
                "Final Answer 文本——否则工具不会执行、用户只看到协议原文。"
                "Final Answer 只能是给用户的最终答复, 不含任何 ReAct 协议块。\n"
                "</code-mode>"
            )
            state.system_parts.append(_build_code_agent_mode_prompt(state.agent_mode_value))
            _workflow_preset_prompt = _build_workflow_preset_prompt(state.workflow_preset_value)
            if _workflow_preset_prompt:
                state.system_parts.append(_workflow_preset_prompt)
            _signals_prompt = _build_project_signals_prompt(state.project_signals)
            if _signals_prompt:
                state.system_parts.append(_signals_prompt)
            if state.browser_regression_enabled:
                _preview_line = (
                    f"优先测试预览地址: {state.browser_regression_preview_url}\n"
                    if isinstance(state.browser_regression_preview_url, str)
                    and state.browser_regression_preview_url.strip()
                    else "如果当前任务产出了可预览页面，请先启动或定位预览地址。\n"
                )
                state.system_parts.append(
                    "\n<browser-regression-guidance>\n"
                    "用户已在代码模式开启 UI 回归。完成代码修改和静态验证后，如果改动涉及前端、HTML、样式、交互或可视输出，"
                    "必须补充浏览器回归检查。\n"
                    + _preview_line
                    + "这是代码模式的隔离预览，不依赖 Echo Electron 桌面桥。对该 localhost/127.0.0.1 地址，"
                    "直接使用 browser_navigate，再用 browser_state/browser_type/browser_click/browser_extract 检查；"
                    "不要自建第二个 HTTP 服务；只使用本段列出的隔离浏览器工具完成验证。\n"
                    + "浏览器回归应模拟真人操作：使用可见鼠标移动、点击、输入和滚动路径，检查关键交互、布局、控制台错误和明显视觉回归。"
                    "发现问题时回到执行阶段修复，再重新验证。\n"
                    "如果没有可测试 UI、缺少登录/权限或预览无法启动，请在 Final Answer 里明确说明阻塞原因和已完成的静态验证。\n"
                    "</browser-regression-guidance>"
                )
        # Personal build has a real writable sandbox, so it is code mode, but
        # it still needs the maker contract that distinguishes it from project
        # development. Personal research/general are non-code work styles and
        # arrive here too; the helper intentionally emits nothing for them.
        if state.work_mode.scope == "personal":
            _personal_mode_prompt = _build_personal_agent_mode_prompt(state.personal_mode_value)
            if _personal_mode_prompt:
                state.system_parts.append("\n" + _personal_mode_prompt)
        if state.is_goal_mode:
            state.system_parts.append(
                "\n<goal-mode-guidance>\n"
                "当前为 Codex 风格 Goal 模式: Goal 是跨轮次持续存在的 objective, "
                "不是把单次 ReAct 循环拉长到无限。\n"
                "本轮仍受 max_iterations 和预算约束; 到达边界时要留下可恢复状态, "
                "不要为了凑完成而扩大范围或重定义成功。\n"
                "开始执行前把 objective 拆成可审计 todo; 每次改动或验证后更新 todo。\n"
                "完成前必须做 completion audit: 从原始 objective 推导每个显式要求、"
                "交付物、命令、测试、验收条件, 并逐项用当前证据验证。\n"
                "只有证据证明全部要求满足、所有 todo completed、必要验证完成时, "
                "才能给完成式 Final Answer。\n"
                "如果证据不足或还有工作, Final Answer 只能报告进度、剩余项、"
                "下一个具体动作或阻塞原因; 不要声明完成。\n"
                "同一阻塞连续多轮确认前不要把目标视为 blocked; 可以请求用户输入, "
                "但要先保留恢复上下文。\n"
                "</goal-mode-guidance>"
            )
        # Long-task / large-context guidance — only relevant when the turn is
        # going to be more than a couple of rounds. Skipping short / chat turns
        # keeps the system prompt small for them and improves prompt cache hits
        # across turn types.
        if (
            state.todo_protocol_required
            or state.is_research_mode
            or state.is_swarm_mode
            or state.is_goal_mode
        ):
            state.system_parts.append(
                "\n<long-task>\n"
                "**深度**: 长任务可以显式配置更高 max_iter; 当前轮始终受传入的 "
                "max_iterations 约束。跑到第 10/20 轮会有 system 检查,"
                "实诚回答(还在推进/已经完成/工具连续失败); 答完了就停, 别凑轮数。\n"
                "**大项目**: 文件 >20 个时不要试图全读 — 维护"
                "「工作集」(直接相关 3-8 个文件), 已读过的不要在后续 Thought 复述。"
                "context 接近上限时优先保留: 当前正在改的文件 > 任务目标 > 历史推理。\n"
                "**进度**: 第一轮 todo_write 列完整计划 → 每个可验证里程碑批量更新 →"
                "Final Answer 前再同步一次准确状态 →"
                "完成里程碑在 Thought 给一句话总结。\n"
                "</long-task>"
            )

        # Memory + skill-template playbook — only inject when the user's
        # request looks like one we've seen before, otherwise the model is
        # just told about features it doesn't need this turn.
        if state.todo_protocol_required:
            state.system_parts.append(
                "\n<memory-and-templates>\n"
                "**模板复用** (低成本高回报): 看到「以后也按这格式 / 做成 X 那样」→"
                "先 `list_learned_skills()`(0 token), 命中就 `apply_skill(name, request)`,"
                "没命中再考虑 `learn_skill_from_text(name, sample, golden_samples=[...])`"
                "(framework 会用 golden_samples 校验模板才落盘)。\n"
                "**记忆四档**(按需,不要每次都用):\n"
                "  - `recall` — 用户提到旧上下文 → 第一轮就查\n"
                "  - `remember` — 项目级事实(项目名 / deadline / API key 路径)\n"
                "  - `note_user` — 用户偏好(语言 / 详略 / 技术水平)\n"
                "  - `update_soul` — 你自己的持久教训(不是一次性观察)\n"
                "</memory-and-templates>"
            )

        # User long-term preferences — persistent settings the user has
        # asked us to honor across turns (e.g. "always 4-space indent",
        # "no Co-Authored-By footer"). Injected before reporting-cadence
        # so cadence/tool guidance can't shadow user-stated defaults.
        try:
            from runtime.memory.users.user_preferences import (
                _load_user_preferences as _load_prefs,
            )

            _prefs = _load_prefs(state.user_context.get("actor") or state.metadata.get("actor"))
        except ImportError:
            _logger.debug("user_preferences module not available", exc_info=True)
            _prefs = {}
        except Exception:  # noqa: BLE001 - never break turn startup
            _logger.debug("user_preferences load failed", exc_info=True)
            _prefs = {}
        if _prefs:
            _pref_lines = [f"- {k}: {v}" for k, v in sorted(_prefs.items())]
            state.system_parts.append(
                "\n<user-preferences>\n"
                "用户的长期偏好（影响默认行为；用户在本轮另有要求时以本轮为准）:\n"
                + "\n".join(_pref_lines)
                + "\n</user-preferences>"
            )

        # Cadence + final-answer shape — applies to every mode that has
        # visible tool work (echo optimisation §27 + §30). Skipped for pure
        # chat where there's no work to report on.
        if state.todo_protocol_required:
            state.system_parts.append(
                "\n<reporting-cadence>\n"
                "**进度节奏**(避免闷头干 N 步再一次性 dump):\n"
                "- 每改 2-3 个文件、或每完成一个清单项, 在下一轮 Thought 里给\n"
                "  一句话进度("
                "本轮做了 X / 接下来 Y / 若 Z 不对请打断"
                ")\n"
                "- 不要积攒 5+ 步成果再统一汇报 — 用户看不到你做了什么就\n"
                "  无法 mid-course 纠偏\n"
                "- 单次 Thought 不超过 6 行;真要展开就拆成多轮\n"
                "</reporting-cadence>\n"
                "<final-answer-shape>\n"
                "**Final Answer 结构**(任务完成时;请求协助时另议):\n"
                "- 第 1 行: 一句话总结(做了什么 / 状态如何)\n"
                "- 改动: 列出修改/新建的文件路径(逐行,绝对或工作目录相对)\n"
                "- 验证: 跑过的命令 + 关键结果("
                "如 `pytest tests/foo.py -q` → 4 passed"
                ")\n"
                "- 未做(可选): 故意跳过的、需要后续做的\n"
                "调研/报告类任务输出报告本身, 但仍在结尾附改动 + 来源说明。\n"
                "</final-answer-shape>\n"
                "<tool-choice-policy>\n"
                "**工具选择硬约束**(优先级 / 危险性 / cwd):\n"
                "- 文件发现: 用 `list_cwd` / `glob_files`(若可用); **不要**\n"
                '  `exec_shell("find ...")` / `exec_shell("ls ...")`\n'
                "- 内容搜索: 用 `code_search` / `grep`(项目内置, 跨平台);\n"
                '  **不要** `exec_shell("grep -r ...")`\n'
                "- 文件读取: 用 `read_file` 带 `offset`/`limit`(超 2000 行\n"
                '  必带);**不要** `exec_shell("cat"/"head"/"tail")`\n'
                "- exec_shell 限定用途: 编译 / 测试 / 构建 / git / 跑特定\n"
                "  CLI(那种没专用 skill 的 ad-hoc 命令)\n"
                "- 长运行命令(dev server / watcher / docker compose / 长测试):\n"
                "  用 `exec_shell(run_in_background=True)` 或 `background_exec`, 然后用\n"
                "  `read_shell_output(task_id)` / `read_background_output(task_id)` 轮询;\n"
                "  结束时用 `kill_shell(task_id)` / `kill_background_exec(task_id)`\n"
                "- **危险命令预审**: 调 exec_shell 前在 Thought 里分类:\n"
                "  * destructive(`rm -rf` / drop database / `git push --force`\n"
                "    main / chmod 777 / sudo / docker rm -f / kubectl delete):\n"
                "    描述影响范围, 然后 Final Answer 请求用户确认;**不要**\n"
                "    赌默认 approval 会兜住\n"
                "  * mutating(普通 git commit / npm install / pytest -x):\n"
                "    继续\n"
                "  * read-only(`ls` / `git status` / `cat README`): 安静继续\n"
                "- **cwd 习惯**: 多个 exec_shell 调用之间 cwd 可能被工具重置;\n"
                "  显式用 `exec_shell(cwd=...)` 参数, **不要**在 command 字\n"
                "  符串里 `cd X && do Y`(`cd` 失败是 silent 的)\n"
                "- **Edit 失败时**: old_string 不唯一就 (a) 加上下文使其唯一,\n"
                "  或 (b) `replace_all=True`;不要把同一调用换个壳重发\n"
                "- **并行 tool_use**: 同一轮里 emit 的多个 tool_use blocks,\n"
                "  如果它们彼此**没有数据依赖**(典型: 多个 `read_file` 读\n"
                "  不同文件 / `Read(a) + Glob(...) + Bash(git status)`),\n"
                "  尽量在一个 assistant message 里一次性 emit,\n"
                "  框架会并发执行 → 单 turn 速度大幅加快。\n"
                "  反例: 第一个 `read_file` 的结果决定第二个 `edit_file` 的\n"
                "  参数 → 必须串行(分两轮 emit),不要塞一起。\n"
                "</tool-choice-policy>"
            )


def _assemble_delegation_guidance(state: _AssemblyState) -> None:
    """Workflow preset / mode contract / codex / output-style / thinking /
    personal-mode / agent-delegation / swarm / research sections."""
    if not state.is_code_mode:
        _workflow_preset_prompt = _build_workflow_preset_prompt(state.workflow_preset_value)
        if _workflow_preset_prompt:
            state.system_parts.append(_workflow_preset_prompt)
    if state.mode_contract_value:
        state.system_parts.append(
            "\n<mode-contract>\n" + state.mode_contract_value[:4000] + "\n</mode-contract>"
        )
    if state.work_mode.scope == "personal":
        _personal_instructions = str(
            state.user_context.get("personal_instructions")
            or state.metadata.get("personal_instructions")
            or ""
        ).strip()
        if _personal_instructions:
            state.system_parts.append(
                "\n<personal-space-custom-instructions>\n"
                + _personal_instructions[:2000]
                + "\n</personal-space-custom-instructions>"
            )
    # Audit / review turns: default to inspect-and-report. The task is to
    # surface findings, not to rewrite code silently. Edits are allowed but
    # must be explicitly stated and justified in the same turn.
    if (
        state.user_context.get("audit_mode")
        or state.metadata.get("audit_mode")
        or str(state.mode_value or "").strip().lower() == "audit"
        or str(state.agent_mode_value or "").strip().lower() == "audit"
    ):
        # Once the user has authorised repairs, the read-only clause is
        # *removed* rather than followed by an exception. Keeping both in the
        # prompt makes the model re-adjudicate "does the exception apply?"
        # every round — observed across trn_c2fbddce247b4164 /
        # trn_3348dff0b9e54a99, whose reasoning traces spend most of their
        # length on exactly that question and end with another plan instead of
        # an edit. A contract the model has to reason *about* is a contract it
        # can reason its way out of.
        if _fix_authorization_present(state):
            state.system_parts.append(
                "\n<audit-mode>\n"
                "本轮为审计模式下的已授权修复阶段。用户已明确要求动手修复，"
                "只读约束不再适用。\n"
                "直接落地写操作：先改代码，再运行验证，最后在回复中列出"
                "每一处改动（文件与行为）和验证结果。\n"
                "不要重新征询方向、不要复述计划代替执行、"
                "不要以“我将检查/我先看一下”作为本轮结论。\n"
                "</audit-mode>"
            )
        else:
            state.system_parts.append(
                "\n<audit-mode>\n"
                "当前为审计/审查模式。默认行为是只读检查并输出审计报告："
                "先逐项核对目标并给出证据与结论，最后汇总发现的问题和风险。\n"
                "不要在没有明确说明的情况下静默修改代码或配置；"
                "若审计中发现需要修复的问题，先在报告中指出，"
                "说明你准备如何修改、为何修改，征得确认后再执行写操作。\n\n"
                "完成审计后，直接输出报告并结束本轮对话。不要在完成后继续推理或等待。"
                "报告应包含：发现的问题、证据、严重度评级、影响面分析和修复优先级建议。\n"
                "</audit-mode>"
            )
    if state.is_plan_or_spec_composer:
        state.system_parts.append(
            "\n<codex-composer-mode>\n"
            "当前为 Codex 风格 "
            + (
                "Spec"
                if state.workflow_mode_value == "spec" or state.completion_policy_value == "spec"
                else "Plan"
            )
            + " 模式。默认产出计划/规格和验收口径,不要主动进入实现或写文件; "
            "可以读取必要上下文来提高计划/规格质量。不要把计划模式解释为"
            "先计划再自动执行；若用户明确要求继续执行,再按普通执行模式推进。"
            "若同时存在 code-mode 指令,本模式覆盖其中"
            "执行/写入阶段要求,仅保留代码理解、上下文读取和验收设计要求。\n"
            "</codex-composer-mode>"
        )
    try:
        from runtime.core.cerebrum.output_styles import render_output_style

        _output_style_value = (
            state.user_context.get("output_style") or state.metadata.get("output_style") or ""
        )
        _output_style_block = render_output_style(_output_style_value)
        if _output_style_block:
            # Volatile: user can switch per turn; would break cache prefix.
            state.volatile_parts.append(_output_style_block)
    except (ImportError, AttributeError):
        _logger.debug("output_styles overlay not available", exc_info=True)
    try:
        from runtime.core.cerebrum.thinking_mode import render_thinking_guidance

        _thinking_guidance = render_thinking_guidance(state.user_context.get("thinking_plan"))
    except (ImportError, AttributeError):
        _logger.debug("thinking_mode guidance not available", exc_info=True)
        _thinking_guidance = ""
    if _thinking_guidance:
        # Volatile: changes whenever the model picks a new thinking plan.
        state.volatile_parts.append(_thinking_guidance)
    state.system_parts.append(
        "\n<user-facing-process-language>\n"
        "Internal tool names are execution details, not product language. "
        "Use names like `call_agent_parallel`, `web_search`, `fetch_url`, "
        "`todo_write`, `bb_keys`, or `query_skill` only inside tool actions "
        "and private reasoning. In Final Answer and any user-facing prose, "
        "describe the work in human terms instead: call a teammate, search "
        "sources, read webpages, make a plan, or check team context. Do not "
        "show raw tool names unless the user explicitly asks for technical "
        "debug details.\n"
        "</user-facing-process-language>"
    )
    # User-facing posture: (1) don't stall on clarifying questions when the
    # request is actionable — proceed with a stated assumption; (2) never break
    # the fourth wall by explaining internal member/role/mode architecture to
    # the user in chat. Regression: thread t0Wn5Zhvh3VUFwoAR2uP4M asked "想调研
    # 哪个方向" twice mid-flight and later answered "我是 member（团队成员），你的
    # TL 是 general" — both are the assistant leaking its machinery instead of
    # being useful.
    state.system_parts.append(
        "\n<user-facing-posture>\n"
        "- 用户请求可直接执行时,先动手,不要停在追问/澄清上;需要做范围假设时,"
        "明确写出你的假设并直接推进,不要反复问用户。只有在任务确实无法凭合理假设"
        "继续时才提问,且只问一次。\n"
        "- 不要在聊天里向用户解释内部机制:不要说'我是 member/TL''这是 cowork 协作"
        "模式''我派出了 xx 位成员'这类架构性话语;不要用成员人设互相打趣或指责"
        "（如'你们都在摸鱼'）。你始终是直接帮用户解决问题的助手。\n"
        "- 提到团队成员时,只说你安排了哪些人手、他们各自负责什么、进展如何,"
        "并且这些安排必须对应真实的派发动作;不要编造不存在的成员或声称派发了"
        "实际没有发生的动作。\n"
        "</user-facing-posture>"
    )
    if not state.is_swarm_mode and state.mode_value not in {"chat", "flash", "inspiration"}:
        state.system_parts.append(_build_auto_delegation_guidance(state))
    if state.is_swarm_mode:
        state.system_parts.append(
            "\n<swarm-orchestration-guidance>\n"
            "Current mode is SWARM. Treat swarm as an adaptive long-task "
            "orchestration mode, not a fixed template.\n"
            "\n"
            "Decision policy:\n"
            "- If the user's request is simple or can be completed by the "
            "lead in one short pass, do NOT spawn subagents; answer or use "
            "the smallest necessary tool path.\n"
            "- If the task is large, long-running, research-heavy, or has "
            "independent work lanes, create/update a visible todo_write plan "
            "first. Use stage-like item names such as task analysis, parallel "
            "research/execution round N, synthesis, quality review, and "
            "delivery only when those stages are actually needed.\n"
            "- For durable research/report/build tasks, write or update "
            "`plan.md` before substantial execution when a workspace/file "
            "output is available.\n"
            "- Choose skills dynamically. For research/report work, prefer "
            "`deep-research-swarm` -> `report-writing` -> `docx` when the "
            "user explicitly asked for a file deliverable. When the user "
            "did not specify a format, default to a markdown report "
            "rendered directly in the chat reply (the UI renders it "
            "natively) and skip the `.docx` export. If a needed skill is "
            "missing, say which capability is missing and use the best "
            "available real tools.\n"
            "- Use `call_agent_parallel` only for independent subtasks. Pick "
            "the number and roles from the task itself; do not force a fixed "
            "headcount. Good roles include researcher, explorer, architect, "
            "reviewer, debugger, and security-review.\n"
            "- Ask parallel workers to write compact findings to blackboard "
            "keys with `bb_write`; after the batch, read them with `bb_keys` "
            "and `bb_read`, synthesize conflicts, and cross-check important "
            "claims before final delivery.\n"
            "- Never finish with only raw worker logs, a partial plan, or "
            "'still working' prose. Final Answer must include the integrated "
            "result and any created file paths. If blocked, update todo_write "
            "and ask for the specific missing input.\n"
            "</swarm-orchestration-guidance>"
        )
    if state.is_research_mode:
        if state.work_mode.scope == "personal":
            state.system_parts.append(
                "\n<personal-research-scope>\n"
                "This is a personal-space research turn, not a bound project. The "
                "isolated workspace contains only artifacts for this task; it is not "
                "evidence that files, reports, or directories mentioned in memory "
                "exist locally. Treat memory as a lead to verify, never as a file "
                "inventory. For market, industry, or competitor research, start with "
                "web evidence unless the user explicitly supplies a local file. Before "
                "reading any local path, confirm it exists with list_cwd or an observed "
                "search. If a path is outside the isolated workspace or is absent, do "
                "not repeatedly climb parent directories or retry variants of that path; "
                "switch to web evidence, use available facts, or state the exact missing "
                "input.\n"
                "</personal-research-scope>"
            )
        # Mode-aware skill chain: ``deep-research-swarm`` is reserved for swam
        # mode (TeamRunner with native tool_use). In single-agent / Agent mode
        # (the common case here when ``_is_research_mode`` is true but
        # ``_is_swarm_mode`` is false) we point the model at ``deep-research``
        # instead — the single-agent counterpart that returns the 7-phase
        # instruction document the parent ReAct loop drives via plain
        # ``web_search`` / ``fetch_url``.
        _research_skill = "deep-research-swarm" if state.is_swarm_mode else "deep-research"
        state.system_parts.append(
            "\n<research-skill-chain-guidance>\n"
            "This turn is a research/report task. Drive the work through "
            "the visible research-skill chain when the corresponding "
            "skills are available, otherwise fall back to atomic tools.\n"
            "Suggested workflow (skip steps the user did not ask for):\n"
            "1. Create or update a concrete `plan.md` for the task with "
            "`write_text_file` before substantial research begins.\n"
            f"2. Call `{_research_skill}` to load the research workflow, "
            "then follow it for evidence collection and cross-checking.\n"
            "3. **Default deliverable is the report rendered directly in "
            "the chat reply (markdown).** The chat UI renders headings, "
            "tables, and citations natively, so a long-form markdown "
            "answer is already the final product — do NOT auto-export to "
            ".docx / .pdf / any other file format unless the user "
            "explicitly asked for that format.\n"
            "4. Only when the user asks for a file deliverable: call "
            "`report-writing` and/or `docx` (or the appropriate format "
            "skill) to produce the file, then include the file path in "
            "the final answer alongside the chat-rendered summary.\n"
            "5. Do not finish with only 'still searching' / 'still "
            "writing' prose — the final answer must contain the actual "
            "report text.\n"
            "If one of the optional skills is not visible, state which "
            "capability is missing, then fall back to the best available "
            "tools without pretending the skill chain ran.\n"
            "</research-skill-chain-guidance>"
        )
        state.system_parts.append(
            "\n<research-final-guidance>\n"
            "当前任务具有调研/研究报告性质。工具搜索与浏览只是证据收集阶段，不能把过程模板当作最终回答。\n"
            "在给 Final Answer 前，必须输出用户可直接阅读的完整报告正文；"
            "报告至少包含：执行摘要、关键结论、分维度分析、对比表或清单、"
            "风险/不确定性、建议、来源说明。\n"
            "如果搜索轮次或预算接近上限，不要停在「正在整理/继续搜索」；"
            "应基于已有证据生成阶段性完整报告，并清楚标注仍需补证的点。\n"
            "</research-final-guidance>"
        )


def _assemble_tool_sections(state: _AssemblyState) -> None:
    """Capability activation, plugin side effects, skill catalog, plan lock."""
    if state.tools_active:
        assert state.executor is not None
        if state.browser_operation_mode:
            _ensure_browser_operation_skills(state.executor)
        try:
            from runtime.core.cerebrum.capability_router import (
                activate_capabilities,
            )

            _capability_activation = activate_capabilities(
                state.intent.normalized_goal,
                user_context=state.user_context,
                registry=state.executor.registry,
            )
            _capability_activation_prompt = _capability_activation.render_prompt()
        except (ImportError, AttributeError, TypeError, ValueError):
            _logger.debug(
                "capability activation prompt unavailable",
                exc_info=True,
            )
            _capability_activation_prompt = ""
            _capability_activation = None
        state.capability_activation = _capability_activation
        if _capability_activation_prompt:
            state.volatile_parts.append(_capability_activation_prompt)

        # Side effects of mention parsing:
        #   1. Auto-load pinned plugins so the model can use them this turn.
        #   2. Persist mention history for cross-thread autocomplete ranking.
        # Both are best-effort; failures don't block the turn.
        if _capability_activation is not None:
            _codex_handled_plugins: set[str] = set()
            try:
                if _capability_activation.pinned_plugins:
                    try:
                        from runtime.execution.suckers.codex_plugin_skills import (
                            load_codex_plugin_skills,
                        )

                        codex_report = load_codex_plugin_skills(
                            state.executor.registry,
                            _capability_activation.pinned_plugins,
                        )
                        _codex_handled_plugins.update(
                            plugin_id.lower() for plugin_id in codex_report.handled_plugin_ids
                        )
                        codex_obs = codex_report.render_observation()
                        if codex_obs:
                            state.volatile_parts.append(
                                f"<codex-plugin-injection>\n{codex_obs}\n</codex-plugin-injection>",
                            )
                    except (ImportError, AttributeError, TypeError, ValueError):
                        _logger.debug(
                            "codex plugin skill injection failed",
                            exc_info=True,
                        )

                    from runtime.core.cerebrum.plugin_auto_load import (
                        auto_load_pinned_plugins,
                    )

                    legacy_plugins = tuple(
                        plugin_id
                        for plugin_id in _capability_activation.pinned_plugins
                        if plugin_id.lower() not in _codex_handled_plugins
                    )
                    if legacy_plugins:
                        plugin_report = auto_load_pinned_plugins(legacy_plugins)
                        obs = plugin_report.render_observation()
                        if obs:
                            state.volatile_parts.append(
                                f"<plugin-activation>\n{obs}\n</plugin-activation>",
                            )
            except (ImportError, AttributeError, TypeError):
                _logger.debug(
                    "plugin auto-load failed",
                    exc_info=True,
                )

            try:
                import time as _time

                from runtime.memory.users.mention_history import (
                    get_mention_history_store,
                )

                actor = (
                    str(
                        state.user_context.get("user_id")
                        or state.user_context.get("actor")
                        or "anonymous"
                    )
                    if isinstance(state.user_context, dict)
                    else "anonymous"
                )
                store = get_mention_history_store()
                ts = _time.time()
                items: list[tuple[str, str]] = []
                for ident in _capability_activation.pinned_plugins:
                    items.append(("plugin", ident))
                for ident in _capability_activation.pinned_skills:
                    items.append(("skill", ident))
                for ident in _capability_activation.pinned_agents:
                    items.append(("agent", ident))
                for ident in _capability_activation.pinned_packs:
                    items.append(("pack", ident))
                if items:
                    store.record_batch(actor, items, ts=ts)
            except (ImportError, AttributeError, OSError, TypeError):
                _logger.debug(
                    "mention history record failed",
                    exc_info=True,
                )

        catalog = _format_skill_catalog(
            state.executor.registry,
            agent=state.agent,
            user_context=state.user_context,
            goal=state.intent.normalized_goal,
            include_names=(
                STRICT_EXPLICIT_READ_TOOL_NAMES if state.strict_explicit_reads else None
            ),
        )
        if catalog:
            state.file_inspection_tools_visible = "  - read_file:" in catalog
            state.todo_protocol_visible = "  - todo_write:" in catalog
            state.system_parts.append(catalog)
            if state.todo_protocol_visible:
                state.system_parts.append(
                    render_todo_protocol_guidance(
                        required=state.todo_protocol_required,
                        mode=state.todo_protocol_mode,
                    )
                )
    else:
        state.system_parts.append(REACT_NO_TOOLS_NOTE)
    if state.planning_mode and state.is_plan_or_spec_composer:
        state.system_parts.append(
            "CODEX PLAN/SPEC LOCK — This turn is a composer-applied "
            "Plan/Spec mode. Use tools only for read-only context gathering "
            "when necessary. Do not write files, run side-effecting commands, "
            "create artifacts, or continue into implementation by default. "
            "The Final Answer should be the requested plan/specification and "
            "acceptance criteria, not executed changes.",
        )
    elif state.planning_mode:
        # New semantics (2026-05-31): "plan first, then execute" — not
        # "plan only and stop". Long tasks benefit from a written plan before
        # tool work, but the user should NOT have to send a second turn to
        # actually run the plan. Old prompt forced the model to halt after
        # planning; updated prompt nudges it to write plan.md, then keep going
        # with real tool calls.
        state.system_parts.append(
            "PLAN-FIRST MODE — Before substantial tool work, write or "
            "update a brief ``plan.md`` (or todo_write entries) outlining "
            "the goal, the steps you'll take, and what the deliverable "
            "looks like. After the plan is recorded, **continue executing "
            "the plan in the same turn** using real tools (web_search, "
            "fetch_url, write_text_file, etc.). Do NOT stop after the "
            "plan — the user expects the work, not just an outline. The "
            "Final Answer must include the integrated result, not the "
            "plan alone.",
        )
