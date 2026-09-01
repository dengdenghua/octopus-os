"""
Ephemeral sub-agent roles · lightweight personas for one-shot
delegation tasks (``researcher`` / ``debugger`` / ``reviewer`` / …).

Two kinds of "sub-agent" coexist in Echo
-------------------------------------------

1. **Registered agents** (team mode): ``agents/<id>/`` directories
   with their own profile / memory / workspace. Long-lived personas.
   Called through the subagent bridge / team routing protocol. Budget
   lives on the parent task but the subagent has its own persistent
   identity.

2. **Ephemeral roles** (chat/code mode · THIS file): lightweight
   personas defined purely as ``(role_id, system_prompt,
   tool_allowlist)`` tuples. No FS footprint. Called with the same
   subagent bridge with a ``role_id`` that matches an entry in
   ``BUILTIN_ROLES``. The role:

   - **inherits** the caller's conversation history (so it knows
     what the user asked)
   - **inherits** the caller agent's three-tier memory (global /
     project / agent)
   - runs ONE LLM turn with a composed prompt
   - returns a string reply
   - is discarded · no journal identity of its own

   Equivalent to the built-in subagent pattern
   (general-purpose / Explore / Plan / code-reviewer / ...).

Why this is not a skill
-----------------------

Subagents are isolated agent turns with their own prompt/context boundary.
They are invoked through the subagent bridge, not through the SkillRegistry.

Pluggable runner
----------------

``_EPHEMERAL_RUNNER`` is a ``Callable[[EphemeralCall], str]`` set via
``set_ephemeral_role_runner(fn)`` at bootstrap. The default runner
returns a structured "not configured" response so the
ephemeral-role feature is a no-op on deployments that haven't
wired an LLM · consistent with how ``sub_agent._RUNNER`` behaves.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_log = logging.getLogger("runtime.execution.ephemeral")


# ═══════════════════════════════════════════════════════════
# Role definition
# ═══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class EphemeralRoleDef:
    """Everything needed to spin up an ephemeral sub-agent turn.

    Deliberately frozen + hashable so the catalog can live as a
    module-level dict without accidental mutation at runtime.
    """

    id: str
    display_name: str
    description: str
    system_prompt: str
    # Pull caller's recent conversation messages into the sub-agent's
    # context. Almost always True for "look at what we were talking
    # about and continue" roles (reviewer / debugger). False for
    # fresh-topic roles (researcher given an unrelated topic).
    share_context: bool = True
    # Pull caller agent's three-tier memory (global / project /
    # agent) into the prompt. True for roles that need project
    # awareness (architect, reviewer). False for roles that should
    # think in a vacuum.
    share_memory: bool = True
    # Optional narrow tool list · empty = inherit caller's.
    tool_allowlist: tuple[str, ...] = ()


# ═══════════════════════════════════════════════════════════
# Built-in role catalog
# ═══════════════════════════════════════════════════════════


BUILTIN_ROLES: dict[str, EphemeralRoleDef] = {
    "reviewer": EphemeralRoleDef(
        id="reviewer",
        display_name="Code Reviewer",
        description=(
            "Scans a code change for bugs, security holes, performance "
            "issues, and maintainability smells. Shares main agent's "
            "conversation context so it can review what was just "
            "discussed."
        ),
        system_prompt=(
            "You are a focused code reviewer. Given the main agent's "
            "conversation context and a target (diff / file / snippet), "
            "produce three sections:\n"
            "\n"
            "1. **Critical issues** — bugs, security, data-loss paths. "
            "Cite file:line where possible.\n"
            "2. **Suggestions** — perf, readability, idiomatic fixes.\n"
            "3. **Strengths** — what's done well.\n"
            "\n"
            "Be concise · do not restate the code · do not propose "
            "sweeping rewrites unless asked."
        ),
        share_context=True,
        share_memory=True,
        tool_allowlist=("read_file", "file_stats", "count_words", "grep_text"),
    ),
    "researcher": EphemeralRoleDef(
        id="researcher",
        display_name="Research Specialist",
        description=(
            "Deep-dives a single topic using web search + URL fetch. "
            "Does NOT inherit memory by default · researches in a "
            "vacuum so prior beliefs don't color the findings."
        ),
        system_prompt=(
            "You are a focused researcher. Given a topic, collect "
            "factual evidence from the web, synthesize, cite sources. "
            "Prefer primary sources. Flag conflicting claims. Keep "
            "the output a 200-400 word brief with ≤ 5 citations."
        ),
        share_context=True,
        share_memory=False,
        tool_allowlist=(
            "fetch_url",
            "web_search",
            "read_file",
            "list_cwd",
            "grep_text",
            "glob_files",
            "bb_write",
            "bb_read",
            "bb_keys",
        ),
    ),
    "debugger": EphemeralRoleDef(
        id="debugger",
        display_name="Debug Investigator",
        description=(
            "Traces the root cause of a reported bug · inherits the "
            "caller's conversation to understand the observed symptom."
        ),
        system_prompt=(
            "You are a systematic debugger. Given a symptom, work "
            "top-down:\n"
            "\n"
            "1. Restate the observed behavior in one sentence.\n"
            "2. List 2-3 hypotheses · most likely first.\n"
            "3. For each hypothesis: the one observation that would "
            "confirm or reject it.\n"
            "4. Recommend the next action.\n"
            "\n"
            "Never speculate without citing evidence (file:line / log "
            "line / stack frame)."
        ),
        share_context=True,
        share_memory=True,
        tool_allowlist=("read_file", "list_cwd", "exec_shell", "grep_text", "glob_files"),
    ),
    "architect": EphemeralRoleDef(
        id="architect",
        display_name="System Architect",
        description=(
            "Reasons about system design trade-offs · inherits both "
            "conversation and memory so it has full project context."
        ),
        system_prompt=(
            "You are an experienced software architect. Given a "
            "design question, produce:\n"
            "\n"
            "1. The core trade-off (in one sentence).\n"
            "2. Two viable options · each with one pro and one con.\n"
            "3. Your recommendation · with the criterion that tipped "
            "the decision.\n"
            "\n"
            "Do not write code. Avoid buzzwords. Reference concrete "
            "existing files / modules in this repo if they're "
            "relevant."
        ),
        share_context=True,
        share_memory=True,
        tool_allowlist=(),  # full read inherit
    ),
    "security-review": EphemeralRoleDef(
        id="security-review",
        display_name="Security Reviewer",
        description=(
            "Scans a change for security vulnerabilities · XSS / "
            "SQLi / auth bypass / secrets / path traversal / "
            "deserialization."
        ),
        system_prompt=(
            "You are a security reviewer. Given a code target, "
            "enumerate vulnerabilities found, each with:\n"
            "\n"
            "- Category (e.g. XSS / SQLi / auth / secret / path-traversal)\n"
            "- Location (file:line)\n"
            "- Severity (critical / high / medium / low)\n"
            "- Minimal reproduction or attack scenario\n"
            "- Recommended fix\n"
            "\n"
            "If you find no issues, say so plainly. Do not invent "
            "issues to fill the output."
        ),
        share_context=True,
        share_memory=True,
        tool_allowlist=("read_file", "file_stats", "grep_text"),
    ),
    "explorer": EphemeralRoleDef(
        id="explorer",
        display_name="Codebase Explorer",
        description=(
            "Fast navigation of the repo to answer structural "
            "questions like 'where is X defined?' · 'who calls Y?'"
        ),
        system_prompt=(
            "You are a codebase explorer. Given a structural "
            "question, use the file-system tools to locate the "
            "answer. Return concise findings as a list of "
            "file:line references · one per finding · NO prose "
            "beyond a one-sentence summary at the end. "
            "Stop as soon as you have the answer; do NOT repeat "
            "identical tool calls — if a tool returns nothing new, "
            "conclude with what you already have."
        ),
        share_context=True,
        share_memory=False,
        tool_allowlist=(
            "read_file",
            "list_cwd",
            "file_stats",
            "hash_text",
            "grep_text",
            "glob_files",
        ),
    ),
    # Invoked by the team-vote dispatcher to arbitrate N candidate
    # answers from a roster. Not meant to be called directly by users —
    # it's how the routing layer implements MAJORITY / synthesis without
    # needing a third "cowork" UI mode.
    "arbiter": EphemeralRoleDef(
        id="arbiter",
        display_name="Team Arbiter",
        description=(
            "Given a question and N candidate answers from different "
            "agents, pick the majority / best answer and explain why. "
            "Used by the team vote dispatcher · not a user-facing role."
        ),
        system_prompt=(
            "You are a team arbiter. You receive:\n"
            "  * the original question\n"
            "  * N candidate answers (each labelled with the proposing "
            "agent's id)\n"
            "\n"
            "Your job:\n"
            "1. **Consensus** — name the answer the majority converges "
            "on (or note 'no majority · split').\n"
            "2. **Winner** — one answer you judge best and WHY. Be "
            "specific about which candidate (cite the agent id).\n"
            "3. **Dissent** — note any strong minority view worth "
            "preserving · ignore noise.\n"
            "4. **Final** — a 2-4 line consolidated answer the user "
            "can actually read. This is what they take away.\n"
            "\n"
            "Rules:\n"
            "- Stay under 200 words total.\n"
            "- DO NOT rewrite candidates into your own answer if there's "
            "a clear winner · cite them.\n"
            "- DO NOT invent facts beyond what the candidates said."
        ),
        share_context=False,  # arbiter sees only the vote payload · clean slate
        share_memory=False,
        tool_allowlist=(),
    ),
    # ── Roles wired to built-in TeamTopology blueprints ──────
    # The four built-in topologies (research_swarm_v1 / code_review_team_v1 /
    # refactor_pair_v1 / debug_team_v1) reference these by name. Without
    # them dispatch falls through to the un-configured `_RUNNER` and the
    # role records "(no output)" — which is exactly the bug that landed
    # users on this fix.
    "planner": EphemeralRoleDef(
        id="planner",
        display_name="Task Planner",
        description=(
            "Decomposes a goal into concrete sub-tasks; does not "
            "execute them. Used as the first role in research / "
            "code-review / debug topologies."
        ),
        system_prompt=(
            "你是任务编排者。读取用户目标后,把它拆成 3-5 个互不重叠"
            "的子任务,每条 ≤ 80 字。把拆解结果通过 "
            "bb_write('plan', ...) 写入黑板,然后立即结束本轮——不要"
            "尝试自己回答任何子任务。\n\n"
            "如果话题适合并行调研, 子任务应覆盖事实/对比/趋势三个维度。"
        ),
        share_context=True,
        share_memory=False,
        tool_allowlist=("bb_write", "bb_read", "bb_keys", "todo_write"),
    ),
    "synthesizer": EphemeralRoleDef(
        id="synthesizer",
        display_name="Report Synthesizer",
        description=(
            "Reads sibling agents' findings off the blackboard and "
            "produces the final consolidated output."
        ),
        system_prompt=(
            "You are the synthesizer. Read everything siblings wrote "
            "on the blackboard via bb_keys() then bb_read(key) for each. "
            "Treat an injected Workspace task contract (TASK.md) as a hard "
            "delivery contract. When it names a file, write and read back the "
            "exact file before you return; never substitute chat prose for it. "
            "Produce the final answer in the format required by any "
            "topology-specific instructions or the caller's request. "
            "Do not invent a generic summary format when a more specific "
            "structure is provided. Do NOT redo their research; just "
            "consolidate and finish the report."
        ),
        share_context=True,
        share_memory=False,
        tool_allowlist=(
            "bb_read",
            "bb_keys",
            "read_file",
            "list_cwd",
            "glob_files",
            "write_text_file",
            "edit_file",
        ),
    ),
    "implementer": EphemeralRoleDef(
        id="implementer",
        display_name="Code Implementer",
        description=(
            "Executes a plan written by another role, applying file "
            "edits and verification. Used as the second role in "
            "refactor_pair_v1."
        ),
        system_prompt=(
            "You are the implementer. A plan has been written for "
            "you (read it via bb_read('plan') and todo_read). Apply "
            "the changes step-by-step using edit_file / "
            "multi_edit_file / propose_patch. Verify each step "
            "(lint / test) before continuing. Do NOT redesign — if "
            "the plan is wrong, escalate via Final Answer."
        ),
        share_context=True,
        share_memory=True,
        tool_allowlist=(
            "read_file",
            "list_cwd",
            "glob_files",
            "grep_text",
            "edit_file",
            "multi_edit_file",
            "write_text_file",
            "propose_patch",
            "exec_shell",
            "bb_read",
            "bb_keys",
            "todo_read",
            "todo_write",
        ),
    ),
    # Like implementer but with NO shell / network: every write must stay inside
    # the locked worktree, and shell would bypass the sandbox_dir confinement
    # (verified live — a shell-capable role escapes). All its write skills accept
    # sandbox_dir, so the ephemeral chokepoint can confine them. Used by
    # run_worktree_loop's subagent worker.
    "worktree_writer": EphemeralRoleDef(
        id="worktree_writer",
        display_name="Worktree Writer",
        description=(
            "Confined implementer with NO shell · for worktree-isolated runs "
            "where every write must stay inside the locked worktree."
        ),
        system_prompt=(
            "You are a confined implementer working inside an isolated git "
            "worktree. Apply the requested changes using write_text_file / "
            "edit_file / multi_edit_file ONLY — you have no shell. Use relative "
            "paths inside the workspace; never absolute paths outside it. "
            "Verify by reading files back. Report changes via Final Answer."
        ),
        share_context=True,
        share_memory=True,
        tool_allowlist=(
            "read_file",
            "list_cwd",
            "glob_files",
            "grep_text",
            "edit_file",
            "multi_edit_file",
            "write_text_file",
            "bb_read",
            "bb_keys",
            "todo_read",
            "todo_write",
        ),
    ),
    "designer": EphemeralRoleDef(
        id="designer",
        display_name="Refactor Designer",
        description=(
            "Plans a refactor without executing it. Reads the "
            "current code, writes a step-by-step plan + acceptance "
            "criteria via todo_write, then yields to the implementer."
        ),
        system_prompt=(
            "你是重构设计者。先用 list_cwd / read_file / grep_text "
            "读懂现有代码 (≥3 个相关文件), 然后输出:\n\n"
            "1. 重构目标(一句话)\n"
            "2. 影响范围(文件清单)\n"
            "3. 步骤(每步可独立验证)\n"
            "4. 验收标准(lint/test 必须通过的项目)\n\n"
            "用 todo_write 把步骤落进清单,用 bb_write('plan', ...) "
            "写黑板供 implementer 读取。**禁止任何写文件操作** —— "
            "你的输出就是一份计划。"
        ),
        share_context=True,
        share_memory=True,
        tool_allowlist=(
            "read_file",
            "list_cwd",
            "glob_files",
            "grep_text",
            "code_search",
            "bb_write",
            "bb_read",
            "todo_write",
        ),
    ),
    # Narrative Studio drives these roles with an explicit, bounded context
    # pack.  They deliberately inherit neither the caller conversation nor
    # durable memory: story facts must come from cited project context, not an
    # unrelated chat.  Read-only blackboard access is the entire tool surface;
    # every output remains a candidate until human governance commits it.
    "narrative-outline": EphemeralRoleDef(
        id="narrative-outline",
        display_name="Narrative Outliner",
        description="Turns a story objective and cited context pack into a scene-level candidate outline.",
        system_prompt=(
            "You are a professional long-form story outliner. Use only the "
            "facts and source references supplied in the task. Produce a "
            "candidate outline with: dramatic objective, POV, scene beats, "
            "turning point, emotional change, continuity dependencies, and "
            "foreshadowing setup/payoff. Mark unsupported necessities as "
            "[NEEDS DECISION]; never silently invent canon. Do not write prose "
            "and never claim that your output is canonical."
        ),
        share_context=False,
        share_memory=False,
        tool_allowlist=("bb_read", "bb_keys"),
    ),
    "narrative-draft": EphemeralRoleDef(
        id="narrative-draft",
        display_name="Narrative Drafter",
        description="Writes candidate prose from an approved outline and bounded story context.",
        system_prompt=(
            "You are a fiction drafter. Follow the supplied outline, POV, "
            "language, voice constraints, and cited story facts exactly. "
            "Prefer concrete action, sensory specificity, subtext, and causal "
            "scene progression. Do not add world facts that the context does "
            "not support; mark an unavoidable gap as [NEEDS DECISION]. Return "
            "candidate prose only, never a canon declaration."
        ),
        share_context=False,
        share_memory=False,
        tool_allowlist=("bb_read", "bb_keys"),
    ),
    "narrative-continuity": EphemeralRoleDef(
        id="narrative-continuity",
        display_name="Continuity Auditor",
        description="Checks a candidate draft against cited facts, state changes, chronology, and unresolved setup.",
        system_prompt=(
            "You are a strict narrative continuity auditor. Compare the draft "
            "with every supplied source and state record. Report each issue as "
            "severity (blocking/major/minor), exact draft evidence, conflicting "
            "source reference, and the smallest safe correction. Also check "
            "chronology, location, knowledge boundaries, motivation, inventory, "
            "and foreshadowing. If evidence is absent, say unknown. Do not "
            "rewrite the chapter and do not promote canon."
        ),
        share_context=False,
        share_memory=False,
        tool_allowlist=("bb_read", "bb_keys"),
    ),
    "narrative-style": EphemeralRoleDef(
        id="narrative-style",
        display_name="Style Critic",
        description="Evaluates voice, pacing, clarity, dialogue, and repetition without changing story facts.",
        system_prompt=(
            "You are a literary style critic. Evaluate the candidate against "
            "the requested audience, language, genre, and voice profile. "
            "Identify pacing drag, exposition, repetition, vague language, "
            "dialogue problems, tonal drift, and cliches. Quote only short "
            "diagnostic fragments and propose targeted edits. Preserve all "
            "story facts; do not make canon decisions."
        ),
        share_context=False,
        share_memory=False,
        tool_allowlist=("bb_read", "bb_keys"),
    ),
    "narrative-revision": EphemeralRoleDef(
        id="narrative-revision",
        display_name="Narrative Reviser",
        description="Produces a new candidate revision from the draft and structured review findings.",
        system_prompt=(
            "You are a senior fiction reviser. Apply the supplied continuity "
            "and style findings to the candidate draft while preserving POV, "
            "intent, supported facts, and source traceability. Resolve every "
            "blocking issue; if one cannot be resolved from evidence, leave a "
            "clear [NEEDS DECISION] marker. Return the revised candidate prose "
            "followed by a concise change log. Never claim canon status."
        ),
        share_context=False,
        share_memory=False,
        tool_allowlist=("bb_read", "bb_keys"),
    ),
    "narrative-editorial": EphemeralRoleDef(
        id="narrative-editorial",
        display_name="Editorial Judge",
        description="Scores the revised candidate and recommends approve, revise, or block for human review.",
        system_prompt=(
            "You are the final editorial judge, not the canon authority. Review "
            "the revised candidate and cited evidence. Return: recommendation "
            "(approve/revise/block), scores from 0-100 for coherence, "
            "continuity, character, pacing, prose, and originality, unresolved "
            "blocking items, and a short rationale. Approval only means ready "
            "for human governance; never commit or announce canon yourself."
        ),
        share_context=False,
        share_memory=False,
        tool_allowlist=("bb_read", "bb_keys"),
    ),
}


def get_ephemeral_role_ids() -> frozenset[str]:
    """Stable view for dispatch lookup."""
    return frozenset(BUILTIN_ROLES.keys())


def get_role_display(role_id: str) -> tuple[str, str] | None:
    """Return the built-in role's ``(display_name, description)``, or None.

    The role label a sub-agent spawns under is a free-form string the model
    invents (``researcher`` / ``critic`` / ``writer`` / …). Only the labels
    that resolve to a ``BUILTIN_ROLES`` entry have an authoritative
    display name and responsibility blurb — and those live here, co-located
    with the role's ``tool_allowlist``, so the nameplate (角色卡) and the
    tool permissions derive from the same source. Unknown labels return None
    so callers fall back to their own display mapping.
    """
    role = BUILTIN_ROLES.get((role_id or "").strip().lower())
    if role is None:
        return None
    return role.display_name, role.description


# ═══════════════════════════════════════════════════════════
# Runner abstraction
# ═══════════════════════════════════════════════════════════


@dataclass
class EphemeralCall:
    """Everything the runner needs to execute a role turn.

    The ``composed_system_prompt`` already has:
    - role's own ``system_prompt``
    - caller's conversation history (if ``role.share_context``)
    - caller agent's memory (if ``role.share_memory``)

    The runner just has to feed this to an LLM and return the text.
    """

    role: EphemeralRoleDef
    user_prompt: str
    composed_system_prompt: str
    caller_thread_id: str
    caller_agent_id: str
    context: dict[str, Any]


EphemeralRunner = Callable[[EphemeralCall], str]


def _coerce_tool_names(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        for sep in ("，", "、", ";", "\n", "\t"):
            raw = raw.replace(sep, ",")
        return [part.strip() for part in raw.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            out.extend(_coerce_tool_names(item))
        return out
    return [str(value).strip()] if str(value).strip() else []


def _dedupe_tool_names(names: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for name in names:
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


# What a read-only judge gets when its role declares no allowlist at all
# (``architect`` is one, and the voter rotation seats it). Enough to check a
# claim against the code; nothing that leaves a trace.
_READ_ONLY_DEFAULT_JUDGE_TOOLS: tuple[str, ...] = (
    "read_file",
    "read_file_range",
    "grep_text",
    "glob_files",
    "list_cwd",
    "file_stats",
    "tree",
    "git_diff",
    "code_search",
    "code_find_symbol",
    "bb_read",
    "bb_keys",
)


def _effective_tool_allowlist(
    role: EphemeralRoleDef,
    context: dict[str, Any] | None,
) -> list[str]:
    from runtime.execution.misc.skill_policy import resolve_context_tool_policy

    policy = resolve_context_tool_policy(
        role_allowlist=role.tool_allowlist,
        context=context,
    )
    if policy.allow_all:
        return []
    return list(policy.allowed)


def _effective_tool_policy(
    role: EphemeralRoleDef,
    context: dict[str, Any] | None,
):
    from runtime.execution.misc.skill_policy import resolve_context_tool_policy

    return resolve_context_tool_policy(
        role_allowlist=role.tool_allowlist,
        context=context,
    )


def _format_dynamic_skill_grant_note(
    context: dict[str, Any],
    allowlist: list[str],
) -> str:
    parts: list[str] = []
    packs = _coerce_tool_names(context.get("skill_pack_names"))
    plugins = _coerce_tool_names(context.get("plugin_grants"))
    direct = _coerce_tool_names(context.get("direct_skill_grants"))
    if packs:
        parts.append(f"Skill packs: {', '.join(packs)}.")
    if plugins:
        parts.append(f"Plugin grants requested: {', '.join(plugins)}.")
    if direct:
        parts.append(f"Direct skills: {', '.join(direct)}.")
    mode = str(context.get("tool_allowlist_mode") or "").strip().lower()
    if mode in {"all", "full", "inherit_all", "*"}:
        parts.append("Tool access: full available sub-agent tool catalog.")
    elif allowlist:
        parts.append(f"Effective tools: {', '.join(allowlist)}.")
    if not parts:
        return ""
    return (
        "The main agent granted these capabilities for this subtask. "
        "Use them only when they help complete the assignment; if a "
        "needed capability is absent, state the gap clearly.\n\n"
        + "\n".join(f"- {part}" for part in parts)
    )


def _null_ephemeral_runner(call: EphemeralCall) -> str:
    """Default runner · returns a "not configured" marker. Wiring a
    real LLM runner is bootstrap's responsibility (see
    ``set_ephemeral_role_runner``)."""
    raise RuntimeError(
        "ephemeral sub-agent runner not configured · "
        "call set_ephemeral_role_runner(fn) during bootstrap"
    )


_EPHEMERAL_RUNNER: EphemeralRunner = _null_ephemeral_runner


def set_ephemeral_role_runner(runner: EphemeralRunner | None) -> None:
    """Install the runner that executes one ephemeral role turn.

    Passing ``None`` resets to the null runner (raises on invocation).
    """
    global _EPHEMERAL_RUNNER
    _EPHEMERAL_RUNNER = runner or _null_ephemeral_runner


def get_ephemeral_role_runner() -> EphemeralRunner:
    return _EPHEMERAL_RUNNER


# ═══════════════════════════════════════════════════════════
# Context + memory composers · pure functions · no I/O beyond
# disk reads of known paths
# ═══════════════════════════════════════════════════════════


def _collect_caller_context(session: Any) -> list[dict[str, str]]:
    """Pull recent messages from the caller's thread · best-effort.

    Returns a list of ``{"type": "human|ai", "content": str}`` dicts.
    If the session or thread store isn't reachable, returns empty.

    Session is a slotted dataclass so we can't monkey-attach extras ·
    we read from ``session.metadata`` which is the documented
    extension surface. The thread_compat router stamps both
    ``recent_messages`` and ``thread_store`` onto metadata before
    dispatching the tool call.
    """
    if session is None:
        return []
    meta = getattr(session, "metadata", None) or {}
    if not isinstance(meta, dict):
        return []
    # Primary · caller already has the message list in hand · cheapest.
    msgs = meta.get("recent_messages")
    if isinstance(msgs, list):
        return [m for m in msgs if isinstance(m, dict)][-20:]

    # Fallback · look up the thread from a stored handle.
    thread_id = getattr(session, "thread_id", None)
    store = meta.get("thread_store")
    if thread_id and store is not None:
        try:
            thread = store.get(thread_id)
        except (TypeError, ValueError, KeyError):  # noqa: BLE001
            return []
        if thread:
            values = thread.get("values") if isinstance(thread, dict) else None
            if isinstance(values, dict):
                raw = values.get("messages") or []
                if isinstance(raw, list):
                    return [m for m in raw if isinstance(m, dict)][-20:]
    return []


def _collect_caller_memory(session: Any) -> str:
    """Pull caller agent's three-tier memory (global / project /
    agent) as a concatenated markdown block. Returns ``""`` if
    session has no agent or the memory paths don't resolve."""
    if session is None:
        return ""
    agent = getattr(session, "agent", None)
    if agent is None:
        return ""
    try:
        from runtime.execution.agents.loader import _memory_tier_paths
    except (ImportError, TypeError, ValueError, AttributeError):
        return ""
    agent_dir_name = getattr(agent, "agent_id", None)
    if not agent_dir_name:
        return ""
    from pathlib import Path

    # Walk up from this file to find repo root (same strategy as
    # other skills).
    repo = Path(__file__).resolve()
    for parent in repo.parents:
        if (parent / "agents").is_dir() and (parent / "runtime").is_dir():
            root = parent
            break
    else:
        return ""
    agent_dir = root / "agents" / agent_dir_name
    core = agent_dir / "agent-core"
    if not core.exists():
        return ""
    pieces: list[str] = []
    for tier, path in _memory_tier_paths(agent_dir, core):
        if path.is_file():
            try:
                txt = path.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeDecodeError):
                continue
            if txt:
                pieces.append(f"## Memory ({tier})\n\n{txt}")
    return "\n\n".join(pieces)


def _compose_system_prompt(
    role: EphemeralRoleDef,
    session: Any,
    context: dict[str, Any] | None = None,
) -> str:
    """Build the full system prompt injected into the ephemeral LLM
    turn. Structure:

        <role system_prompt>

        ## Topology-specific instructions (if context.system_addendum)
        <system_addendum>

        ## Granted Skills (if dynamic skill grant note present)

        ## Prior turns in this thread (if thread_id + role memory present)
        <last N subagent turns for this (thread, role) — preserves
         "dig deeper on that patent" continuity>

        ## Caller conversation (if share_context)
        <last N messages>

        ## Inherited memory (if share_memory)
        <three tiers>
    """
    parts: list[str] = [role.system_prompt]

    # Tool-use contract for multi-agent sub-agents. The single-agent react
    # loop injects ``_TOOL_USE_CONTRACT`` (react_prompt_assembly_sections.py);
    # this lane bypasses that assembly (react_stack is None → mini-loop), so
    # inject an equivalent "default to tools" instruction here. Gated on the
    # sub-agent actually holding tools (set by ``run_ephemeral_definition``) —
    # a tool-less role is never told to call tools it doesn't have.
    if (context or {}).get("_ephemeral_tools_available"):
        parts.append(
            "## Tool use contract\n\n"
            "You have tools available in this turn and are expected to use "
            "them. Any task that requires searching, reading, computing, "
            "verifying, retrieving, or acting on information MUST call the "
            "appropriate tool first and base your answer on its Observation. "
            "Do not end the turn with an announcement instead of an action — "
            'phrases like "I will check…", "I\'ll continue…", "Let me look '
            'into…", or "I\'ll proceed to…" are not answers. If the tools are '
            "available, use them before you respond."
        )
    addendum = (context or {}).get("system_addendum")
    if isinstance(addendum, str) and addendum.strip():
        parts.append(
            "## Topology-specific instructions\n\n"
            "These instructions override the generic role template when they "
            "conflict.\n\n"
            f"{addendum.strip()}"
        )

    # Hierarchical delegation guidance: when this sub-agent is allowed to spawn
    # its own sub-agents, inject role-specific orchestration guidance.
    delegation_guidance = (context or {}).get("delegation_guidance")
    if isinstance(delegation_guidance, str) and delegation_guidance.strip():
        parts.append(
            "## Hierarchical Orchestration\n\n"
            "You can delegate work to specialist sub-agents using `call_agent_parallel`. "
            "Use this capability to decompose your task into parallel dimensions.\n\n"
            f"{delegation_guidance.strip()}"
        )

    workspace_path = (context or {}).get("workspace_path")
    delivery_roles = {"generator", "implementer", "synthesizer"}
    if role.id in delivery_roles and isinstance(workspace_path, str) and workspace_path.strip():
        try:
            workspace_root = Path(workspace_path).expanduser().resolve()
            task_path = (workspace_root / "TASK.md").resolve()
            if task_path.parent == workspace_root and task_path.is_file():
                task_contract = task_path.read_text(encoding="utf-8")[:12_000].strip()
                if task_contract:
                    parts.append(
                        "## Workspace task contract (TASK.md)\n\n"
                        "This is part of the caller's task, not optional background. "
                        "Satisfy every machine-checkable deliverable before returning. "
                        "If it names an output file or exact path, use the workspace "
                        "file tools to write that file and read it back; a prose-only "
                        "answer does not satisfy the contract.\n\n"
                        f"{task_contract}"
                    )
        except (OSError, RuntimeError, UnicodeError):
            _log.debug("workspace TASK.md injection skipped", exc_info=True)
    grant_note = (context or {}).get("dynamic_skill_grant_note")
    if isinstance(grant_note, str) and grant_note.strip():
        parts.append("## Granted Skills\n\n" + grant_note.strip())

    # Inject per-role, per-thread subagent memory so multi-turn calls
    # (e.g., "researcher, dig deeper on that patent") can reference prior
    # outputs. Memory is keyed by (thread_id, role_id) and bounded to the
    # last MAX_TURNS_PER_KEY turns. Disabled when context explicitly says
    # ``share_history: false`` or when no thread_id is present.
    # Verifier context-starvation. Set by the trusted vote path only (the key
    # canonicalises under the ``subagentpolicy`` protected prefix, so a model
    # cannot set or clear it). It overrides the role definition's own
    # share_context / share_memory: an independent judge must not be able to
    # read the reasoning it was spawned to check, and picking a role whose
    # definition happens to share context must not silently re-open that.
    starved = bool((context or {}).get("subagent_policy_starve_context"))
    thread_id = (context or {}).get("thread_id") or getattr(session, "thread_id", None)
    share_history = (context or {}).get("share_history", True) and not starved
    if thread_id and share_history:
        from runtime.execution.subagents.memory import recent_turns_prompt

        history_prefix = recent_turns_prompt(str(thread_id), role.id)
        if history_prefix:
            parts.append(history_prefix.strip())

    if role.share_context and not starved:
        msgs = _collect_caller_context(session)
        if msgs:
            rendered_lines: list[str] = ["## Caller conversation"]
            for m in msgs:
                who = "User" if m.get("type") == "human" else "Main agent"
                content = m.get("content")
                if isinstance(content, list):
                    # Multi-block message content · flatten text
                    content = " ".join(
                        b.get("text", "")
                        for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                if not isinstance(content, str):
                    continue
                content = content.strip()
                if not content:
                    continue
                rendered_lines.append(f"**{who}**: {content[:1000]}")
            if len(rendered_lines) > 1:
                parts.append(
                    "\n\n".join(
                        [rendered_lines[0], "\n".join(rendered_lines[1:])],
                    )
                )

    if role.share_memory and not starved:
        mem = _collect_caller_memory(session)
        if mem:
            parts.append(mem)

    return "\n\n---\n\n".join(parts)


# ═══════════════════════════════════════════════════════════
# Public entry used by ``sub_agent._call_agent``
# ═══════════════════════════════════════════════════════════


def is_ephemeral_role(agent_id: str) -> bool:
    """True iff ``agent_id`` refers to an ephemeral role (vs a
    registered agent directory)."""
    return agent_id in BUILTIN_ROLES


def _emit_incomplete_to_bus(role_id: str, partial_text: str, rounds: int, reason: str) -> None:
    """Fire-and-forget mirror of an incomplete sub-agent outcome onto the
    typed event bus so the Workbench can render it as an incomplete tile.
    """
    try:
        from runtime.execution.subagents.event_bus import (
            EVT_SUB_INCOMPLETE,
            publish_subagent_event,
        )

        publish_subagent_event(
            EVT_SUB_INCOMPLETE,
            {
                "role": role_id,
                "reason": reason,
                "rounds": rounds,
                "partial_chars": len(partial_text),
            },
        )
    except Exception:  # noqa: BLE001 · telemetry loss never breaks the runner
        pass


def run_ephemeral_definition(
    role: EphemeralRoleDef,
    user_prompt: str,
    *,
    session: Any = None,
    context: dict[str, Any] | None = None,
    timeout_s: int = 300,
) -> dict[str, Any]:
    """Execute one isolated subagent role definition."""

    call_context: dict[str, Any] = dict(context or {})
    effective_tool_policy = _effective_tool_policy(role, call_context)
    # advertised list keeps the inherit semantics (an empty tuple means "atomic
    # inherit", which intentionally includes the memory/SOUL skills). Those skills
    # are blocked at the *execution* gate in ``_ephemeral_tool_exec`` instead of
    # here, so the legacy "full grant -> []" contract (which the tests assert)
    # stays intact and a sub-agent still can't mutate the parent's durable memory.
    effective_tool_allowlist = (
        [] if effective_tool_policy.allow_all else list(effective_tool_policy.allowed)
    )
    # Read-only verifier lane: narrow the ADVERTISED list too, so the granted-
    # skills note doesn't promise a tool the runner will filter out (an agent
    # told it has ``exec_shell`` and then handed no such tool wastes a round
    # discovering that). Enforcement stays in ``select_tool_specs`` — an empty
    # list here means "atomic inherit", which the runner still intersects, so
    # this cannot be the only gate.
    if bool(call_context.get("tool_allowlist_read_only")):
        from runtime.execution.suckers.layers import is_read_only_skill

        if effective_tool_policy.allow_all:
            # ``allow_all`` would otherwise hand a judge the entire catalog.
            effective_tool_allowlist = sorted(
                name for name in _READ_ONLY_DEFAULT_JUDGE_TOOLS if is_read_only_skill(name)
            )
        else:
            effective_tool_allowlist = [
                name for name in effective_tool_allowlist if is_read_only_skill(name)
            ]
    if effective_tool_policy.sources:
        call_context["skill_policy_sources"] = {
            source: list(names) for source, names in effective_tool_policy.sources.items()
        }
        call_context["skill_policy_reason_map"] = {
            name: list(sources) for name, sources in effective_tool_policy.reason_map.items()
        }
    grant_note = _format_dynamic_skill_grant_note(
        call_context,
        effective_tool_allowlist,
    )
    if grant_note:
        call_context["dynamic_skill_grant_note"] = grant_note
    # Feed tool availability into ``_compose_system_prompt`` so the tool-use
    # contract is injected only when the sub-agent actually holds tools.
    # (This lane bypasses the single-agent react prompt assembly, whose
    # _TOOL_USE_CONTRACT it mirrors — see _react_prompt_assembly_sections.py.)
    call_context["_ephemeral_tools_available"] = bool(
        effective_tool_policy.allow_all or effective_tool_allowlist
    )
    composed = _compose_system_prompt(role, session, context=call_context)
    call = EphemeralCall(
        role=role,
        user_prompt=user_prompt,
        composed_system_prompt=composed,
        caller_thread_id=getattr(session, "thread_id", "") or "",
        caller_agent_id=(getattr(getattr(session, "agent", None), "agent_id", "") or ""),
        context={
            **call_context,
            "timeout_s": timeout_s,
            "tool_allowlist": effective_tool_allowlist,
            "ephemeral": True,
        },
    )

    try:
        output = _EPHEMERAL_RUNNER(call)
    except Exception as exc:  # noqa: BLE001
        # EphemeralRoundCapExceeded carries partial_text — surface it as
        # a partial answer with explicit success=false so callers don't
        # silently accept "(exceeded round cap)" as a valid result.
        from runtime.execution.suckers.ephemeral_runner import (
            EphemeralConvergedIncomplete,
            EphemeralRoundCapExceeded,
        )

        if isinstance(exc, EphemeralConvergedIncomplete):
            _log.warning(
                "ephemeral converged early for role=%s · rounds=%d · partial_chars=%d",
                role.id,
                exc.rounds,
                len(exc.partial_text),
            )
            _emit_incomplete_to_bus(role.id, exc.partial_text, exc.rounds, "converged_early")
            return {
                "agent_id": role.id,
                "output": exc.partial_text,
                "success": False,
                "error": (
                    f"sub-agent {role.id!r} converged early after {exc.rounds} "
                    "rounds with no new progress. Partial output included."
                ),
                "ephemeral": True,
                "round_cap_exceeded": False,
                "converged_early": True,
                "rounds_completed": exc.rounds,
                "partial": True,
            }
        if isinstance(exc, EphemeralRoundCapExceeded):
            _log.warning(
                "ephemeral round cap hit for role=%s · rounds=%d · partial_chars=%d",
                role.id,
                exc.rounds,
                len(exc.partial_text),
            )
            _emit_incomplete_to_bus(role.id, exc.partial_text, exc.rounds, "round_cap")
            return {
                "agent_id": role.id,
                "output": exc.partial_text,
                "success": False,
                "error": (
                    f"sub-agent {role.id!r} exceeded round cap ({exc.rounds}) "
                    "without converging. Partial output included."
                ),
                "ephemeral": True,
                "round_cap_exceeded": True,
                "rounds_completed": exc.rounds,
                "partial": True,
            }
        _log.warning(
            "ephemeral runner failed for role=%s: %s: %s",
            role.id,
            type(exc).__name__,
            exc,
        )
        return {
            "agent_id": role.id,
            "output": "",
            "success": False,
            "error": f"{type(exc).__name__}: {exc}",
        }

    return {
        "agent_id": role.id,
        "output": str(output) if output is not None else "",
        "success": True,
        "error": None,
        "ephemeral": True,
    }


def run_ephemeral_role(
    role_id: str,
    user_prompt: str,
    *,
    session: Any = None,
    context: dict[str, Any] | None = None,
    timeout_s: int = 300,
) -> dict[str, Any]:
    """Execute one built-in ephemeral role turn."""
    role = BUILTIN_ROLES.get(role_id)
    if role is None:
        return {
            "agent_id": role_id,
            "output": "",
            "success": False,
            "error": f"unknown ephemeral role {role_id!r}",
        }
    return run_ephemeral_definition(
        role,
        user_prompt,
        session=session,
        context=context,
        timeout_s=timeout_s,
    )


__all__ = [
    "BUILTIN_ROLES",
    "EphemeralRoleDef",
    "EphemeralCall",
    "EphemeralRunner",
    "get_ephemeral_role_ids",
    "get_ephemeral_role_runner",
    "is_ephemeral_role",
    "run_ephemeral_definition",
    "run_ephemeral_role",
    "set_ephemeral_role_runner",
]
