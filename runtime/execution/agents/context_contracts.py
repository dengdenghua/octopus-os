"""Engine-neutral workspace context and agent mode contract builders."""

from __future__ import annotations

from pathlib import Path
from typing import Any

_CODE_CONTEXT_README_NAMES = ("README.md", "readme.md", "TASK.md")
_CODE_CONTEXT_STYLE_SUFFIXES = (
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".go",
    ".rs",
    ".html",
    ".css",
)
_CODE_CONTEXT_SKIP_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "coverage",
    ".next",
    "target",
}


def _build_code_context_prelude(workspace_path: str, goal: str = "") -> str:
    root = Path(workspace_path).expanduser()
    if not root.is_dir():
        return ""

    parts: list[str] = ["[startup-code-context]"]

    readme = _find_code_context_readme(root)
    if readme is not None:
        readme_text = _read_code_context_file(readme, max_chars=2000)
        if readme_text:
            readme_rel = readme.relative_to(root).as_posix()
            parts.append(f'Observation: read_file("{readme_rel}")')
            parts.append(f"Path: {readme.relative_to(root).as_posix()}")
            parts.append(readme_text)

    style_file = _find_code_context_style_file(root)
    if style_file is not None and style_file != readme:
        style_text = _read_code_context_file(style_file, max_chars=1500)
        if style_text:
            style_rel = style_file.relative_to(root).as_posix()
            parts.append(f'Observation: read_file("{style_rel}")')
            parts.append(f"Path: {style_file.relative_to(root).as_posix()}")
            parts.append(style_text)

    acceptance = _task_acceptance_context(goal, "\n".join(parts))
    if acceptance:
        parts.append(acceptance)

    if len(parts) == 1:
        return ""
    return "\n\n".join(parts)


def _task_acceptance_context(goal: str, observed_context: str) -> str:
    """Add bounded, task-derived acceptance checks for common high-risk work.

    This is deliberately phrased as verification guidance rather than a
    solution. It makes security and cross-cutting maintenance obligations
    stable across model providers without changing the user's requested API.
    """

    goal_text = str(goal or "").lower()
    context_text = observed_context.lower()
    checks: list[str] = []
    path_boundary_task = any(
        term in goal_text
        for term in ("path-boundary", "path boundary", "traversal", "symlink escape")
    )
    if path_boundary_task and any(
        term in context_text for term in ("unquote", "url decode", "pathboundaryerror")
    ):
        checks.append(
            "Security path-boundary acceptance: test plain, encoded, and repeatedly/double-encoded "
            "traversal; normalize separators; resolve symlinks; prove containment in the canonical "
            "root; raise the public boundary exception for every rejected input; preserve valid "
            "nested reads; and add focused regression tests for these cases."
        )
    crosscutting_change = any(
        term in goal_text for term in ("cross-cutting", "cross cutting", "rename")
    ) and any(term in goal_text for term in ("config", "configuration", "setting", "option"))
    if crosscutting_change:
        checks.append(
            "Cross-cutting configuration acceptance: search runtime consumers, schemas, CLI flags, "
            "documentation, examples/sample configs, and tests; preserve the documented legacy "
            "alias or migration path; then rerun a repository-wide search for stale names."
        )
    concurrent_cache_task = (
        "cache" in goal_text
        and any(term in goal_text for term in ("concurrent", "simultaneous", "并发"))
        and any(term in goal_text for term in ("ttl", "expire", "过期"))
    )
    if concurrent_cache_task:
        checks.append(
            "Concurrent cache acceptance: implement single-flight behavior per key so all simultaneous "
            "misses share exactly one loader result; never hold unrelated keys behind that load; wake "
            "all waiters on success or failure; do not cache exceptions; use a monotonic TTL clock; "
            "and add a barrier-based regression proving one loader call under real thread contention. "
            "Read the existing cache implementation and focused tests first, use one per-key pending "
            "state/condition instead of ad-hoc retry loops, then run the smallest targeted test and lint. "
            "Choose leader versus follower exactly once while holding the map lock; only the creator of "
            "the pending entry may call the loader, and followers must wait outside that lock. Never call "
            "a helper that re-acquires the same non-reentrant lock while its caller still holds it. A shared "
            "pending Event/result/exception entry is the simplest auditable shape. "
            "For failure fan-out tests, hold the first loader in-flight with an Event until followers "
            "have joined; a barrier only before get_or_load does not prove those callers became waiters, "
            "so do not assert scheduler-dependent exception counts. "
            "If the starter still calls the loader directly or the tests directory has no focused "
            "cache test, make the first mutations cache.py and tests/test_cache.py before invoking "
            "test tooling. Use the registered run_tests/lint_check tools; do not install dependencies, "
            "probe unrelated system Python environments, or substitute shell redirection for file tools. "
            "The only permitted product diffs are cache.py and tests/test_cache.py: do not modify "
            "pyproject.toml or add tests/__init__.py, conftest.py, helper scripts, or packaging metadata. "
            "If run_tests times out or fails, inspect its tail and repair cache.py/tests directly before "
            "running it again; do not create alternate test-runner scripts. "
            "When lint_check reports fixable import/format diagnostics, inspect its returned diff or call "
            "lint_check with fix=true instead of guessing edits or probing for a system ruff executable. "
            "Once those checks pass, stop and report the result instead of adding duplicate scripts or "
            "running unrelated broad suites."
        )
    if not checks:
        return ""
    return "[task-acceptance-contract]\n" + "\n".join(f"- {check}" for check in checks)


def _build_code_agent_mode_prompt(agent_mode: str | None) -> str:
    """Mode-specific operating contract for Agent page project/code turns."""
    mode = (agent_mode or "coder").strip().lower()
    aliases = {
        "develop": "coder",
        "build": "builder",
        "builder": "builder",
        "new": "builder",
        "code": "coder",
        "coder": "coder",
        "debugger": "coder",
        "architect": "architect",
        "architecture": "architect",
        "audit": "audit",
        "review": "audit",
        "uxui": "uxui",
        "ux/ui": "uxui",
    }
    canonical = aliases.get(mode, "coder")
    if canonical == "audit":
        body = (
            "当前项目子模式: audit / 审计。\n"
            "- 默认只读检查并报告,且由执行策略强制不修改文件;需要修复时先把当前任务"
            "切换到 develop。\n"
            "- 每条发现必须带严重度、可定位证据(文件与行)、影响和建议修复顺序。\n"
            "- 重要结论至少做一次交叉核对;没有证据的猜测标为待确认。"
        )
    elif canonical == "uxui":
        body = (
            "当前项目子模式: uxui / 体验与界面。\n"
            "- 先观察真实页面、关键状态和响应式布局,再修改代码;不要只凭源码猜视觉结果。\n"
            "- 关注遮挡、跳变、密度、层级、文案、键盘操作和可访问性。\n"
            "- 修改后必须通过浏览器重新走查受影响路径并保留可复核的页面证据。"
        )
    elif canonical == "builder":
        body = (
            "当前项目子模式: builder / 构建者。\n"
            "- 适合从零搭建项目、补脚手架、初始化配置、生成可运行最小闭环。\n"
            "- 先确认目标产物、运行入口和验收命令;优先创建最小可运行版本。\n"
            "- 不要过早引入大型框架或复杂抽象;每完成一个可运行切片就验证。"
        )
    elif canonical == "architect":
        body = (
            "当前项目子模式: architect / 架构师。\n"
            "- 适合跨模块设计、迁移方案、安全边界、接口契约和技术债治理。\n"
            "- 默认先读现有结构与约束,给出设计取舍;涉及大范围修改前先分阶段执行。\n"
            "- 优先保持兼容性和可回滚性;避免一次性重写核心路径。"
        )
    else:
        body = (
            "当前项目子模式: coder / 编码者。\n"
            "- 适合修 bug、加功能、写测试、重构局部代码。\n"
            "- 优先定位最小相关文件,做小步修改,每个修改点配套验证。\n"
            "- 交付时说明改了哪里、跑了什么验证、还有什么残余风险。"
        )
    return f"<code-agent-mode>\n{body}\n</code-agent-mode>"


def _build_workflow_preset_prompt(workflow_preset: str | None) -> str:
    """Operating contract for an intensity workflow preset (e.g. audit.ultracode).

    Every user-facing project preset carries an explicit operating contract;
    ``audit.ultracode`` additionally triggers deterministic multi-agent
    orchestration in the ReAct runtime.

    Spawn CEILING is deliberately NOT set here — it stays governed by the operator
    orchestration budget (``ECHO_ORCH_TOKEN_BUDGET`` and the runtime ceiling in
    ``_delegation_skills_orchestration``). This prompt only steers WHAT to do and
    how WIDE to ask, never how many agents are permitted, so a client picking this
    preset cannot escalate its own spawn budget.

    The orchestration trigger is deliberately DEFAULT-ON with an inverted bar
    ("is this trivial enough to skip fan-out?"), not conditional on the model
    first noticing parallelisable sub-problems. The older conditional phrasing
    ("when independent sub-problems exist, fan out") left the judgement call to a
    model that reliably decided its current task did not qualify, so the preset
    read as deep-thinking guidance and produced single-agent runs. The directive
    also names concrete widths, because ``run_orchestration`` defaults to n=3 /
    rounds=2 (6 spawns) and an unparameterised call silently stays narrow no
    matter how high the operator ceiling is.

    Still defensive about skill availability: if ``run_orchestration`` is gated
    out for this agent, fall back to a manual multi-pass review rather than
    calling a tool that isn't there.
    """
    preset = (workflow_preset or "").strip().lower()
    # Backward compatibility aliases
    if preset == "codex.plan":
        preset = "plan.mode"
    elif preset == "codex.spec":
        preset = "spec.mode"
    elif preset == "codex.goal":
        preset = "goal.mode"
    elif preset == "ultracode" or preset == "audit.ultracode":
        preset = "audit.deep"

    if preset == "plan.mode":
        body = (
            "当前工作流: plan.mode / Plan 模式。\n"
            "- 可以读取上下文、搜索资料、检查代码结构并提出少量澄清问题。\n"
            "- 默认不要写文件、改代码、执行实现性改动或启动长任务;用户明确要求执行时才切换。\n"
            "- 输出可执行计划,至少包含目标理解、约束/风险、步骤、验收标准和需要确认的点。"
        )
    elif preset == "spec.mode":
        body = (
            "当前工作流: spec.mode / Spec 模式。\n"
            "- 目标是沉淀规格,不是马上实现。默认不要改代码或写入项目文件。\n"
            "- 输出目标、非目标、用户故事/流程、接口或数据契约、边界条件、验收标准和开放问题。\n"
            "- 如果现有代码会影响规格,先读相关文件再写规格;不要凭空假设接口。"
        )
    elif preset == "goal.mode":
        body = (
            "当前工作流: goal.mode / Goal 模式。\n"
            "- 围绕 objective 持续推进,但单轮仍受 max_iterations、token 和成本预算约束。\n"
            "- 开始前拆成可审计 todo;每次推进后更新状态,保留可恢复上下文。\n"
            "- 完成前做 completion audit: 逐项核对原始目标、交付物、测试/验收和当前证据。"
        )
    elif preset == "audit.deep":
        body = (
            "当前工作流: audit.deep / 深度只读审计。\n"
            "- 这是强制只读工作流:即使消息中同时要求修复,也只能给出证据和修复建议;"
            "要修改文件必须先切换到 develop。\n"
            "- 以最详尽、最正确的答案为目标,不要因为 token 成本就提前收手;"
            "质量优先于速度。token 成本不是约束条件。\n"
            "- **默认就要编排,不要等到发现可并行子问题才编排。** 每个实质性任务都先用 "
            "`run_orchestration` 发起多代理编排;只有纯对话轮次和琐碎的机械检查才允许"
            "独自完成。判断标准是反向的:不是「这值得扇出吗」,而是"
            "「这琐碎到不配扇出吗」。\n"
            "- 扇出要**开够宽度**。默认参数(n=3, rounds=2)只有 6 个 spawn,对深度任务偏窄:"
            "显式传 `n`(单次编排上限 6,深度任务就按 5-6 开)、需要多轮深挖时传 `rounds`"
            "(上限 5),并开 `verify`(投票核验)和 `synthesize`(综合)。单次编排装不下的"
            "工作量,用**多次串联编排**覆盖,而不是传一个会被夹掉的大 n。扇出上限由部署"
            "预算约束,你负责把宽度提到任务真实需要的量级,但不自行抬高 spawn 上限。\n"
            "- **第一次编排就发生在理解阶段,不是等你自己读完代码之后。** 子代理自带工具、"
            "会自己读文件,不需要你先把上下文喂给它们。允许的独自动作只有 1-2 次定位性调用"
            "(列目录 / glob 确认路径),然后立刻 `run_orchestration`;把「先自己通读一遍再扇出」"
            "当成禁止项——那条路会把整轮预算耗在你一个人读文件上,最后一个子代理都没派出去。\n"
            "- 多阶段工作(理解→设计→审查→验证)按阶段**串联多次编排**:每个阶段都是一次编排,"
            "读完结果再决定下一阶段,而不是一次编排包办全部,也不是前面几个阶段自己干。"
            "你始终在环里,但你的角色是派活和综合,不是代替子代理去读。\n"
            "- 质量模式,按任务形态挑用:并行分片求全覆盖;独立视角互不干扰再交叉核验;"
            "对抗性验证(专门派人推翻已有结论);完整性批判(专门派人找漏掉了什么);"
            "循环到榨干(重复扇出直到不再有新发现)。\n"
            "- 给出结论前做对抗性自检:找反例、复核关键断言与证据(文件:行),"
            "核验未通过的标注为存疑,而不是满足于首版答案。\n"
            "- 若 `run_orchestration` 技能被网关裁掉了,退化为按模块自行分轮交叉推进,"
            "不要去调一个不存在的工具。"
        )
    elif preset == "develop.iterate":
        body = (
            "当前工作流: develop.iterate / 迭代开发。\n"
            "- 先定位最小相关面,再小步实现;每个连贯改动批次后立即运行最小有效验证。\n"
            "- 保持现有接口与风格;涉及迁移或兼容性时先确认回滚路径。\n"
            "- 完成时给出修改文件、验证结果和尚存风险,不能用计划代替实际交付。"
        )
    elif preset == "audit.review":
        body = (
            "当前工作流: audit.review / 标准审计。\n"
            "- 默认只读且由执行策略强制,先形成证据化发现;即使消息中要求修复也不要修改项目,"
            "必须先切换到 develop。\n"
            "- 按严重度排序,每条包含文件/行、触发条件、影响与修复建议。\n"
            "- 对高严重度发现做复核;无法复现或证据不足时明确标为待确认。"
        )
    elif preset == "uxui.regression":
        body = (
            "当前工作流: uxui.regression / 视觉与交互回归。\n"
            "- 修改前记录真实页面状态和关键交互;修改后重新检查同一路径。\n"
            "- 至少覆盖默认视口、窄屏、键盘可达性以及加载/空/错误等受影响状态。\n"
            "- 页面证据不可用时如实说明缺口,不要仅凭代码声称视觉回归通过。"
        )
    else:
        return ""
    return f"<workflow-preset>\n{body}\n</workflow-preset>"


def _build_personal_agent_mode_prompt(personal_mode: str | None) -> str:
    """Operating contract for a PERSONAL-space work mode (no bound user project).

    The code/project modes (:func:`_build_code_agent_mode_prompt`) only apply once
    a workspace directory is bound. Personal space is the agent's own
    conversational/work space — it still has a sandbox to write in, so it can carry
    its own modes. Only "build" carries steering here; "general" is the default
    (no contract) and "research" is handled upstream by the existing deep-research
    reasoning mode, not by this prompt.
    """
    mode = (personal_mode or "").strip().lower()
    if mode not in {"build", "builder", "make", "maker"}:
        return ""
    body = (
        "当前空间: 个人工作空间(未绑定用户项目目录),你有自己的沙箱工作目录可写。\n"
        "构建模式 / maker:\n"
        "- 主动产出可运行的成果,而不是只给方案:需要时在工作目录里创建文件、写代码、跑起来验证。\n"
        "- 每完成一个可运行切片就自测一次;优先最小可运行版本,不要堆到最后才验证。\n"
        "- 收工用 Final Answer 说明:产出了什么、怎么运行或获取(关键文件 / 命令 / 导出方式)、残余风险。"
    )
    return f"<personal-agent-mode>\n{body}\n</personal-agent-mode>"


def _build_project_signals_prompt(project_signals: Any) -> str:
    if not isinstance(project_signals, dict):
        return ""
    signals = project_signals.get("signals")
    if not isinstance(signals, dict):
        signals = project_signals

    def _list(key: str, limit: int = 8) -> list[str]:
        value = signals.get(key)
        if not isinstance(value, list):
            return []
        return [str(item) for item in value[:limit] if isinstance(item, str) and item.strip()]

    def _commands(limit: int = 8) -> list[str]:
        value = signals.get("commands")
        if not isinstance(value, list):
            return []
        formatted: list[str] = []
        for item in value[:limit]:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "").strip()
            command = str(item.get("command") or "").strip()
            source = str(item.get("source") or "").strip()
            if not kind or not command:
                continue
            suffix = f" ({source[:80]})" if source else ""
            formatted.append(f"[{kind}] {command}{suffix}")
        return formatted

    lines: list[str] = []
    recommended = project_signals.get("recommended_mode")
    if isinstance(recommended, str) and recommended.strip():
        confidence = project_signals.get("confidence")
        suffix = (
            f" ({round(float(confidence) * 100)}%)" if isinstance(confidence, (int, float)) else ""
        )
        lines.append(f"- 推荐子模式: {recommended.strip()}{suffix}")
    reason = project_signals.get("reason")
    if isinstance(reason, str) and reason.strip():
        lines.append(f"- 检测依据: {reason.strip()[:240]}")

    file_count = signals.get("file_count")
    if isinstance(file_count, int):
        lines.append(f"- 文件数量: {file_count}")
    git_commits = signals.get("git_commits")
    if isinstance(git_commits, int) and git_commits > 0:
        lines.append(f"- Git 提交数: {git_commits}")
    if signals.get("has_readme") is True:
        lines.append("- README: 已发现")

    manifests = _list("manifests")
    if manifests:
        lines.append("- 项目清单/技术栈信号: " + ", ".join(manifests))
    lock_files = _list("lock_files")
    if lock_files:
        lines.append("- 锁文件/包管理器信号: " + ", ".join(lock_files))
    structure_dirs = _list("structure_dirs", limit=12)
    if structure_dirs:
        lines.append("- 关键目录: " + ", ".join(structure_dirs))
    commands = _commands()
    if commands:
        lines.append("- 候选验证命令: " + "; ".join(commands))

    if not lines:
        return ""
    if commands:
        lines.append(
            "- 验证建议: 修改后优先从候选命令里选择最相关的一条执行;"
            "如果候选命令不适用,说明原因并选择更窄的验证。"
        )
    else:
        lines.append(
            "- 验证建议: 优先根据上述清单和锁文件选择项目自带 lint/typecheck/test/build 命令;"
            "不确定时先读取 package/pyproject/README 等清单文件再执行。"
        )
    return "<project-signals>\n" + "\n".join(lines) + "\n</project-signals>"


def _find_code_context_readme(root: Path) -> Path | None:
    for name in _CODE_CONTEXT_README_NAMES:
        candidate = root / name
        if candidate.is_file():
            return candidate
    try:
        for candidate in sorted(root.iterdir(), key=lambda p: p.name.lower()):
            if candidate.is_file() and candidate.name.lower() == "readme.md":
                return candidate
    except OSError:
        return None
    return None


def _find_code_context_style_file(root: Path) -> Path | None:
    def _candidate_depth(path: Path) -> int:
        return len(path.relative_to(root).parts)

    candidates: list[Path] = []
    try:
        for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
            if child.is_file() and child.suffix.lower() in _CODE_CONTEXT_STYLE_SUFFIXES:
                if child.name.lower() != "readme.md":
                    candidates.append(child)
            elif child.is_dir():
                if child.name in _CODE_CONTEXT_SKIP_DIR_NAMES:
                    continue
                try:
                    for grand in sorted(child.iterdir(), key=lambda p: p.name.lower()):
                        if grand.is_file() and grand.suffix.lower() in _CODE_CONTEXT_STYLE_SUFFIXES:  # noqa: SIM102
                            if grand.name.lower() != "readme.md":
                                candidates.append(grand)
                                break
                except OSError:
                    continue
    except OSError:
        return None
    if not candidates:
        return None
    candidates.sort(key=lambda p: (_candidate_depth(p), p.as_posix().lower()))
    return candidates[0]


def _read_code_context_file(path: Path, *, max_chars: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    text = text.strip()
    if not text:
        return ""
    if len(text) > max_chars:
        text = text[:max_chars] + "\n...(truncated)"
    return text
