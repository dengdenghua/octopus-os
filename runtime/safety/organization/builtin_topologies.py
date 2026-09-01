"""Built-in team topologies seeded on first runtime boot.

The forge ships with an empty registry by design — users grow their own
recipes through the evolver. But "empty" is a poor first-run experience:
no multi-agent dispatch can happen until *some* topology exists. This
module supplies four production-tested team recipes that cover the
common task families (research, code-review, refactor, debug) and
seeds them into the registry only when it's still pristine.

The data model keeps a single ``AgentSpec`` per ``Role``. When a
recipe semantically wants three parallel reviewers (security /
performance / style), we collapse the parallel fan-out into a single
slot whose ``system_addendum`` instructs the agent to enumerate all
perspectives in one pass. This is a pragmatic mapping — the structural
contract (roles → coordination protocol) stays honest, and the
extra parallelism is recovered later when the runner gains a
``parallel`` protocol branch.

Roles available in the enum (``Role``):
  PLANNER · GENERATOR · EVALUATOR · CRITIC · RESEARCHER · SYNTHESIZER

Sequential order in ``team_runner._SEQUENTIAL_ORDER``:
  PLANNER → RESEARCHER → GENERATOR → CRITIC → SYNTHESIZER → EVALUATOR
"""

from __future__ import annotations

from .topology import (
    AgentSpec,
    CoordinationProtocol,
    Role,
    TeamTopology,
)

# Generic fallback agent — every shipped agent profile understands the
# core toolset (read_file, web_search, exec_shell, etc.). The ``general``
# agent is preset and always present; ``coder`` is preset and present in
# code-tilted profiles. Users can swap these later via ``swap_agent``.
# Built-in ephemeral role names (defined in
# ``runtime.execution.suckers.ephemeral_agents.BUILTIN_ROLES``).
# Topology specs reference these so ``call_subagent(agent_id=...)``
# dispatches through the ephemeral runner that's actually wired up
# in app.py — instead of falling through to the never-configured
# generic ``_RUNNER`` (which silently produced "(no output)" rows).
_PLANNER = "planner"
_ARCHITECT = "architect"
_DESIGNER = "designer"
_RESEARCHER = "researcher"
_REVIEWER = "reviewer"
_EXPLORER = "explorer"
_DEBUGGER = "debugger"
_ARBITER = "arbiter"
_SYNTHESIZER = "synthesizer"
_IMPLEMENTER = "implementer"

# Legacy aliases — kept temporarily so any remaining `_GENERAL`/`_CODER`
# references compile during the transition. Both are now mapped to
# real ephemeral roles upstream so dispatch always succeeds.
_GENERAL = _RESEARCHER  # default research-tilted catch-all
_CODER = _IMPLEMENTER  # default code-tilted catch-all


# ── 1. research_swarm_v1 ────────────────────────────────────────

_research_swarm = TeamTopology(
    name="research_swarm_v1",
    protocol=CoordinationProtocol.PARALLEL,
    task_bucket="research-report",
    max_iterations=1,
    quality_threshold=0.6,
    agents={
        Role.PLANNER: AgentSpec(
            agent_id=_PLANNER,
            system_addendum=(
                "你是研究编排者(architect)。读取用户目标后,把它拆成 3 个互不重叠"
                "的子问题,覆盖事实/对比/趋势三个维度。把拆解结果通过 "
                "bb_write('plan', ...) 写入黑板,内容用编号列表呈现(编号 1/2/3),"
                "每条 ≤80 字。完成后立即结束本轮,不要尝试自己回答。"
            ),
        ),
        Role.RESEARCHER: AgentSpec(
            agent_id=_RESEARCHER,
            parallel_replicas=3,
            system_addendum=(
                "你是并行研究员副本 {replica_index}/{replica_count}(researcher "
                "pool)。从黑板 bb_read('plan') 读取子问题列表,只负责第 "
                "{replica_index} 个子问题(不要调研其他编号)。用 web_search + "
                "fetch_url + web_fetch 深入调研这一项,至少引用 2 个来源。"
                "把发现通过 bb_write('result_{replica_index}', ...) 写入黑板,"
                "保持原文链接,不要预先合成。若任务说明证据包已在当前工作区,"
                "先 list_cwd 并读取这些本地文件,不要改为上网搜索。"
            ),
        ),
        Role.CRITIC: AgentSpec(
            agent_id=_ARBITER,
            system_addendum=(
                "你是事实核查员(fact_checker)。读取所有 bb_read('result_*'),"
                "挑选 2-3 条关键事实声明,用 web_search 抽查;若发现可疑、"
                "过时、或来源单一的论断,在输出末尾追加 NOTES 段落标注。"
                "若全部稳妥,直接说明'未发现需要更正的事实'。"
            ),
        ),
        Role.SYNTHESIZER: AgentSpec(
            agent_id=_SYNTHESIZER,
            system_addendum=(
                "你是研究综合者(synthesizer)。读取所有 bb_read('result_*') 和 "
                "事实核查员的批注,按用户原始要求产出完整最终报告。若 critic "
                "指出缺项、截断、部分完成或给出'需补全内容',必须把这些缺项"
                "当作硬性交付要求补齐,不得直接转述一份部分完成的底稿。\n\n"
                "若用户要求创建具体文件或结构化产物,必须用工作区文件工具按精确"
                "路径和字段落盘,再读回确认；此时以用户给出的文件契约为最高优先级,"
                "不要套用下面的通用报告结构。对本地证据任务先 list_cwd,只读取实际"
                "列出的文件；一旦证据齐全就立即写产物,不要猜测其他文件名或重复探查。\n\n"
                '长任务/调研报告的默认结构:正文第一行必须原样输出"长任务回归开始";以用户要求的开头起笔;一、市场假设;'
                "二、用户画像;三、竞品格局;四、技术风险;五、商业化路径;"
                "六、90天行动计划;最后用 5 条 bullet 总结。保持段落紧凑,"
                "但必须交付完整正文;若上游已经有完整报告,可以直接复用或合并,"
                "不得压缩成摘要或只输出关键发现。优先确保结尾的 90 天计划和 5 条总结"
                "完整出现。对引用保留原始 URL。"
            ),
        ),
    },
    metadata={
        "description": (
            "研究 / 市场调研 / 行业报告 / 竞品分析。架构师拆题,研究员并行调研,"
            "事实核查员抽查,综合者产出报告。"
        ),
        "builtin": True,
        "version": "3",
    },
)


# ── 2. code_review_team_v1 ──────────────────────────────────────

_code_review_team = TeamTopology(
    name="code_review_team_v1",
    protocol=CoordinationProtocol.SEQUENTIAL,
    task_bucket="code-review",
    max_iterations=1,
    quality_threshold=0.6,
    agents={
        Role.PLANNER: AgentSpec(
            agent_id=_ARCHITECT,
            system_addendum=(
                "你是评审架构师(review architect)。读取 diff/PR 文本,识别变更"
                "范围和潜在风险面;把评审拆成三个视角清单:安全(security)、"
                "性能(performance)、风格与可维护性(style)。把视角清单写入黑板"
                "并简述每条视角应优先关注哪些文件/函数。"
            ),
        ),
        Role.RESEARCHER: AgentSpec(
            agent_id=_REVIEWER,
            system_addendum=(
                "你是并行评审员组(reviewer pool):security / performance / style "
                "三个视角同时进行。用 read_file、grep、code_search 阅读相关代码,"
                "对每个视角分别给出 finding(文件:行:严重度:描述:建议),"
                "至少覆盖架构师列出的全部关注点。不要去除原文引用。"
            ),
        ),
        Role.CRITIC: AgentSpec(
            agent_id=_ARBITER,
            system_addendum=(
                "你是评审复核者(review critic)。审视上游评审员的 finding 列表,"
                "标记重复、误报或严重度判错的条目,按需补充被遗漏的常见问题"
                "(空指针、未处理异常、并发竞态、SQL 注入等)。"
            ),
        ),
        Role.SYNTHESIZER: AgentSpec(
            agent_id=_SYNTHESIZER,
            system_addendum=(
                "你是评审整合者(synthesizer)。把所有 finding 按 PR 评审格式输出:"
                "## Summary(2-3 句总结)→ ## Issues by severity"
                "(blocker/major/minor/nit 分组)→ ## Suggested fixes"
                "(每个 blocker/major 给出可落地的修改建议或 patch sketch)。"
            ),
        ),
    },
    metadata={
        "description": (
            "代码评审 / code review / 安全审查。架构师拆视角,评审员并行扫描"
            "安全、性能、风格,复核者去重补漏,整合者输出 PR 风格评审。"
        ),
        "builtin": True,
        "version": "1",
    },
)


# ── 3. refactor_pair_v1 ─────────────────────────────────────────

_refactor_pair = TeamTopology(
    name="refactor_pair_v1",
    protocol=CoordinationProtocol.SEQUENTIAL,
    task_bucket="code-refactor",
    max_iterations=1,
    quality_threshold=0.6,
    agents={
        Role.PLANNER: AgentSpec(
            agent_id=_DESIGNER,
            system_addendum=(
                "你是重构设计师(designer),处于 planning_mode。仅使用只读工具:"
                "read_file、list_cwd、grep、code_search。读取相关代码后,产出:"
                "(1) 当前结构概要;(2) 重构方案(分步骤,标注每步影响的文件);"
                "(3) 验收标准(单测命令、行为不变性断言)。最后用 todo_write "
                "把方案写成可执行 todo 列表。**严禁修改任何文件**,设计完成即结束。"
            ),
        ),
        Role.GENERATOR: AgentSpec(
            agent_id=_IMPLEMENTER,
            system_addendum=(
                "你是重构实施者(implementer)。读取设计师写的 plan 与 todo,"
                "按顺序执行:edit_file 或 multi_edit_file 落地改动,每完成一步用 "
                "exec_shell 跑相关单测/lint 验证;若验证失败,先修复再继续下一步,"
                "不要回到设计阶段。最终输出已修改文件清单与验证日志摘要。"
            ),
        ),
    },
    metadata={
        "description": (
            "重构 / refactor / 多文件改造。设计师在 planning_mode 下出方案与验收"
            "标准,实施者顺序执行并验证每一步。"
        ),
        "builtin": True,
        "version": "1",
    },
)


# ── 4. debug_team_v1 ────────────────────────────────────────────

_debug_team = TeamTopology(
    name="debug_team_v1",
    protocol=CoordinationProtocol.SEQUENTIAL,
    task_bucket="debug",
    max_iterations=1,
    quality_threshold=0.6,
    agents={
        Role.RESEARCHER: AgentSpec(
            agent_id=_EXPLORER,
            system_addendum=(
                "你是问题复现员(reproducer)。读取用户提供的错误信息/堆栈/复现"
                "步骤,用 read_file 与 exec_shell 在最小条件下尝试复现 bug;"
                "记录复现步骤、实际日志、与预期行为的偏差。**不要修改源码**,"
                "复现成功(或确认无法复现)即停止。避免重复执行相同的命令/"
                "读取;连续两轮无新信息就立即收尾,输出当前结论。"
            ),
        ),
        Role.GENERATOR: AgentSpec(
            agent_id=_DEBUGGER,
            system_addendum=(
                "你是根因假说员(hypothesizer)。基于复现员的输出,提出不超过 3 "
                "条候选根因假说,每条包含:(a) 受影响代码位置;(b) 触发条件;"
                "(c) 预期可观测信号。按可能性从高到低排序,不要直接修复。"
            ),
        ),
        Role.CRITIC: AgentSpec(
            agent_id=_REVIEWER,
            system_addendum=(
                "你是假说验证员(verifier)。逐一测试假说员给出的每一条:用 "
                "read_file/grep 检查代码、用 exec_shell 跑断言或加临时日志,"
                "对每条假说给出 confirmed / refuted / inconclusive 之一,并附"
                "证据片段。验证完成立即停止,不要写最终修复方案。"
            ),
        ),
        Role.SYNTHESIZER: AgentSpec(
            agent_id=_SYNTHESIZER,
            system_addendum=(
                "你是诊断综合者(synthesizer)。把复现+假说+验证的结果整合为:"
                "## Root cause(被确认的那条假说,或剩余分歧)→ ## Evidence"
                "(关键证据引用)→ ## Suggested fix(具体改哪个文件、改成什么、"
                "为什么)→ ## Test to add(防回归)。结构化、可执行。"
            ),
        ),
    },
    metadata={
        "description": (
            "调试 / debug / 排查。复现员先现场重现,假说员提 ≤3 候选根因,"
            "验证员逐一测试,综合者写诊断与修复建议。"
        ),
        "builtin": True,
        "version": "1",
    },
)


BUILTIN_TOPOLOGIES: list[TeamTopology] = [
    _research_swarm,
    _code_review_team,
    _refactor_pair,
    _debug_team,
]


def seed_builtin_topologies(registry: dict[str, TeamTopology]) -> int:
    """Add built-in topologies to ``registry`` if they aren't there yet.

    Idempotent: a topology is only added when its fingerprint isn't
    already a key in the registry. The registry dict is mutated in
    place; the count of newly inserted topologies is returned.
    """
    added = 0
    for topology in BUILTIN_TOPOLOGIES:
        fp = topology.fingerprint
        if fp not in registry:
            registry[fp] = topology
            added += 1
    return added


def upgrade_present_builtin_topologies(registry: dict[str, TeamTopology]) -> int:
    """Replace stale built-ins that are already present in a registry.

    User deletion remains respected: a missing built-in is not re-added to a
    non-empty registry.  Only entries explicitly marked ``builtin`` with the
    same stable name and an older version are migrated.
    """
    upgraded = 0
    for current in BUILTIN_TOPOLOGIES:
        current_version = int(current.metadata.get("version") or 0)
        stale = [
            fp
            for fp, candidate in registry.items()
            if candidate.name == current.name
            and candidate.metadata.get("builtin") is True
            and int(candidate.metadata.get("version") or 0) < current_version
        ]
        if not stale:
            continue
        for fp in stale:
            registry.pop(fp, None)
        registry[current.fingerprint] = current
        upgraded += 1
    return upgraded


__all__ = [
    "BUILTIN_TOPOLOGIES",
    "seed_builtin_topologies",
    "upgrade_present_builtin_topologies",
]
