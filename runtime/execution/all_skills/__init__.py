# ruff: noqa: E402 — module-level imports below are intentionally late
"""
runtime.execution.all_skills · unified skill catalog.

This is the **master registry** of every skill the runtime knows about.
It splits skills into two tiers:

1. **Atomic base** (``BASE_SKILL_IDS``) — pure read / compute, zero I/O
   side effects. Always exposed to every agent so the LLM never has to
   think about whether ``read_file`` is available.
2. **Specialized pool** — grouped by capability (web / fs_write / git /
   shell / computer / browser). Each agent declares which groups and/or
   individual skills to add on top of the base via its
   ``tool-registry.jsonc``.

The agent loader composes ``allowed_skills = base ∪ agent.private`` and
passes that set to the LLM system prompt, so the model only *sees* the
skills the agent is allowed to call.

Adding a new skill
------------------

1. Implement + register it in ``suckers/<group>_skills.py``.
2. Add an entry to ``_CATALOG`` below with ``group`` (which registrar
   owns it) and ``atomic`` (True only if it's side-effect-free).
3. If this is a new group, add its registrar to ``_GROUP_REGISTRARS``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from runtime.execution.suckers import SkillRegistry
from runtime.execution.suckers.agent_doc_skills import (
    register_agent_doc_skills as _register_agent_docs,
)
from runtime.execution.suckers.agent_meta_skills import (
    register_agent_meta_skills as _register_agent_meta,
)
from runtime.execution.suckers.ask_user_question import (
    register_ask_user_question_skill as _register_ask_user_question,
)
from runtime.execution.suckers.blackboard_skills import (
    register_blackboard_skills as _register_blackboard,
)
from runtime.execution.suckers.browser_act_skills import (
    register_browser_act_skills as _register_browser_act,
)
from runtime.execution.suckers.browser_skills import (
    register_browser_skills as _register_browser,
)
from runtime.execution.suckers.builtins import (
    register_builtins as _register_builtins,
)
from runtime.execution.suckers.code_intelligence_skills import (
    register_code_intelligence_skills as _register_code_intel,
)
from runtime.execution.suckers.computer_skills import (
    register_computer_skills as _register_computer,
)
from runtime.execution.suckers.crawler_skills import (
    register_crawler_skills as _register_crawler,
)
from runtime.execution.suckers.cron_skills import (
    register_cron_skills as _register_cron,
)
from runtime.execution.suckers.delegation_skills import (
    register_delegation_skills as _register_delegation,
)
from runtime.execution.suckers.fs_search_skills import (
    register_fs_search_skills as _register_fs_search,
)
from runtime.execution.suckers.jobs_skills import (
    register_jobs_skills as _register_jobs,
)
from runtime.execution.suckers.kimi_compat_skills import (
    register_kimi_compat_skills as _register_kimi_compat,
)
from runtime.execution.suckers.layers import ATOMIC_SKILL_NAMES
from runtime.execution.suckers.lsp_skills import (
    register_lsp_skills as _register_lsp,
)
from runtime.execution.suckers.market_skills import (
    register_market_skills as _register_market,
)
from runtime.execution.suckers.market_skills import (
    register_prompt_market_skills as _register_prompt_market,
)
from runtime.execution.suckers.memory_skills import (
    register_memory_skills as _register_memory,
)
from runtime.execution.suckers.plan_mode import (
    register_plan_mode_skill as _register_mode,
)
from runtime.execution.suckers.skill_library_skills import (
    register_skill_library_skills as _register_skill_library,
)
from runtime.execution.suckers.web_skills import (
    register_web_skills as _register_web,
)
from runtime.execution.suckers.workflow_skill import (
    register_workflow_skills as _register_workflow,
)
from runtime.execution.suckers.write_skills import (
    register_code_quality_skills as _register_code_quality,
)
from runtime.execution.suckers.write_skills import (
    register_exec_skill as _register_shell,
)
from runtime.execution.suckers.write_skills import (
    register_git_skills as _register_git,
)
from runtime.execution.suckers.write_skills import (
    register_write_skills as _register_fs_write,
)

_log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# Skill catalog · keep this in sync with the actual registrars
# ═══════════════════════════════════════════════════════════

_CATALOG: dict[str, dict[str, Any]] = {
    # ── atomic base (no side effects · always-on) ─────────────
    "list_cwd": {"group": "builtin", "atomic": True},
    "read_file": {"group": "builtin", "atomic": True},
    "file_stats": {"group": "builtin", "atomic": True},
    "count_words": {"group": "builtin", "atomic": True},
    "hash_text": {"group": "builtin", "atomic": True},
    "use_chatgpt_connector": {"group": "builtin", "atomic": True},
    # File-system discovery/search. These are read-only and are mentioned by
    # the builtin tool descriptions, so they must be callable anywhere the
    # model can see those hints.
    "glob_files": {"group": "fs_search", "atomic": True},
    "grep_text": {"group": "fs_search", "atomic": True},
    "tree": {"group": "fs_search", "atomic": True},
    "read_file_range": {"group": "fs_search", "atomic": True},
    # ── web (httpx) ──────────────────────────────────────────
    "fetch_url": {"group": "web", "atomic": False},
    "web_search": {"group": "web", "atomic": False},
    "web_fetch": {"group": "web", "atomic": False},
    "crawl_site": {"group": "crawler", "atomic": False},
    "platform_search": {"group": "web", "atomic": False},
    "platform_read": {"group": "web", "atomic": False},
    "platform_collect": {"group": "web", "atomic": False},
    "platform_monitor": {"group": "web", "atomic": False},
    "reach_doctor": {"group": "web", "atomic": False},
    "search_image_by_text": {"group": "kimi_compat", "atomic": False},
    "search_image_by_image": {"group": "kimi_compat", "atomic": False},
    "get_data_source_desc": {"group": "kimi_compat", "atomic": True},
    "get_data_source": {"group": "kimi_compat", "atomic": False},
    "deploy_website": {"group": "kimi_compat", "atomic": False},
    # ── code intelligence ────────────────────────────────────
    "code_analyze": {"group": "code_intel", "atomic": True},
    "code_search": {"group": "code_intel", "atomic": True},
    "code_edit_diff": {"group": "code_intel", "atomic": False},
    "code_find_symbol": {"group": "code_intel", "atomic": True},
    "code_dependency_graph": {"group": "code_intel", "atomic": True},
    # ── LSP code navigation (one external server per language) ─
    "lsp_definition": {"group": "lsp", "atomic": False},
    "lsp_references": {"group": "lsp", "atomic": False},
    "lsp_hover": {"group": "lsp", "atomic": False},
    "lsp_document_symbols": {"group": "lsp", "atomic": False},
    # Public agent documentation skills (Google Gemini + Addy Osmani).
    "frontend-ui-engineering": {"group": "agent_docs", "atomic": False},
    "api-and-interface-design": {"group": "agent_docs", "atomic": False},
    "browser-testing-with-devtools": {"group": "agent_docs", "atomic": False},
    "frontend-design": {"group": "agent_docs", "atomic": False},
    "react-best-practices": {"group": "agent_docs", "atomic": False},
    "typescript-best-practices": {"group": "agent_docs", "atomic": False},
    "code-quality": {"group": "agent_docs", "atomic": False},
    # ── filesystem write ─────────────────────────────────────
    "write_text_file": {"group": "fs_write", "atomic": False},
    "append_text_file": {"group": "fs_write", "atomic": False},
    "edit_text_file": {"group": "fs_write", "atomic": False},
    "edit_file": {"group": "fs_write", "atomic": False},
    "multi_edit_file": {"group": "fs_write", "atomic": False},
    # ── local code verification ───────────────────────────────
    "run_tests": {"group": "code_quality", "atomic": False},
    "lint_check": {"group": "code_quality", "atomic": False},
    "format_code": {"group": "code_quality", "atomic": False},
    # ── git ──────────────────────────────────────────────────
    "git_status": {"group": "git", "atomic": False},
    "git_diff": {"group": "git", "atomic": False},
    "git_log": {"group": "git", "atomic": False},
    "git_add": {"group": "git", "atomic": False},
    "git_commit": {"group": "git", "atomic": False},
    "git_branch": {"group": "git", "atomic": False},
    # ── shell ────────────────────────────────────────────────
    "exec_shell": {"group": "shell", "atomic": False},
    "ipython": {"group": "shell", "atomic": False},
    "background_exec": {"group": "shell", "atomic": False},
    "read_background_output": {"group": "shell", "atomic": False},
    "read_shell_output": {"group": "shell", "atomic": False},
    "kill_background_exec": {"group": "shell", "atomic": False},
    "kill_shell": {"group": "shell", "atomic": False},
    # ── desktop automation (pyautogui) ───────────────────────
    "screen_capture": {"group": "computer", "atomic": False},
    "screen_info": {"group": "computer", "atomic": False},
    "mouse_click": {"group": "computer", "atomic": False},
    "mouse_move": {"group": "computer", "atomic": False},
    "keyboard_type": {"group": "computer", "atomic": False},
    "keyboard_press": {"group": "computer", "atomic": False},
    "computer_observe": {"group": "computer", "atomic": False},
    "computer_plan_next": {"group": "computer", "atomic": False},
    "computer_preview_action": {"group": "computer", "atomic": False},
    "computer_execute_token": {"group": "computer", "atomic": False},
    "computer_uia_status": {"group": "computer", "atomic": False},
    "computer_uia_tree": {"group": "computer", "atomic": False},
    "computer_uia_find": {"group": "computer", "atomic": False},
    # ── browser automation (playwright headless) ─────────────
    "browser_get": {"group": "browser", "atomic": False},
    "browser_extract": {"group": "browser", "atomic": False},
    "browser_navigate": {"group": "browser", "atomic": False},
    "browser_click": {"group": "browser", "atomic": False},
    "browser_type": {"group": "browser", "atomic": False},
    "browser_upload": {"group": "browser", "atomic": False},
    "browser_scroll": {"group": "browser", "atomic": False},
    "browser_wait": {"group": "browser", "atomic": False},
    "browser_screenshot": {"group": "browser", "atomic": False},
    "browser_find": {"group": "browser", "atomic": False},
    "browser_state": {"group": "browser", "atomic": False},
    "screenshot_web_full_page": {"group": "kimi_compat", "atomic": False},
    # ── live browser bridge (Electron desktop's webview) ─────
    "live_browser_click": {"group": "browser_act", "atomic": False},
    "live_browser_type": {"group": "browser_act", "atomic": False},
    "live_browser_wait": {"group": "browser_act", "atomic": False},
    "live_browser_scroll": {"group": "browser_act", "atomic": False},
    "live_browser_navigate": {"group": "browser_act", "atomic": False},
    "live_browser_extract": {"group": "browser_act", "atomic": False},
    "live_browser_screenshot": {"group": "browser_act", "atomic": False},
    "live_browser_execute_js": {"group": "browser_act", "atomic": False},
    "live_browser_current_url": {"group": "browser_act", "atomic": False},
    "live_browser_find": {"group": "browser_act", "atomic": False},
    "live_browser_state": {"group": "browser_act", "atomic": False},
    # Kimi-compatible media / data / website helper tools.
    "generate_image": {"group": "kimi_compat", "atomic": False},
    "generate_video": {"group": "kimi_compat", "atomic": False},
    "generate_speech": {"group": "kimi_compat", "atomic": False},
    "get_available_voices": {"group": "kimi_compat", "atomic": True},
    "generate_sound_effects": {"group": "kimi_compat", "atomic": False},
    "website_version_manager": {"group": "kimi_compat", "atomic": False},
    "find_asset_bbox": {"group": "kimi_compat", "atomic": False},
    "crop_and_replicate_assets_in_image": {"group": "kimi_compat", "atomic": False},
    # ── memory (per-agent state files) ───────────────────────
    "remember": {"group": "memory", "atomic": True},
    "recall": {"group": "memory", "atomic": True},
    "note_user": {"group": "memory", "atomic": True},
    "diary_write": {"group": "memory", "atomic": True},
    "update_soul": {"group": "memory", "atomic": True},
    "list_soul_history": {"group": "memory", "atomic": True},
    "revert_soul": {"group": "memory", "atomic": True},
    "recall_scores": {"group": "memory", "atomic": True},
    "analyze_soul_impact": {"group": "memory", "atomic": True},
    "auto_regression_check": {"group": "memory", "atomic": True},
    "deep_reflect": {"group": "memory", "atomic": False},
    "deep_evolve": {"group": "memory", "atomic": False},
    # ── learned-skill library (Kimi-style document templates) ──
    # ``learn_skill_from_text`` extracts a reusable template from a
    # high-quality sample doc; ``apply_skill`` reuses it for a new
    # request; ``list_learned_skills`` enumerates the agent's
    # library. All non-atomic (LLM-coupled).
    "learn_skill_from_text": {"group": "skill_library", "atomic": False},
    "list_learned_skills": {"group": "skill_library", "atomic": True},
    "apply_skill": {"group": "skill_library", "atomic": False},
    # ── knowledge graph (on-demand query) ───────────────────
    "kg_query": {"group": "memory", "atomic": True},
    # ── mode protocol (plan-mode exit) ───────────────────────
    "exit_plan_mode": {"group": "mode", "atomic": True},
    # ── interactive ask-user-question (pause-and-ask atomic) ───
    "ask_user_question": {"group": "ask_user", "atomic": True},
    # ── agent meta (self-management · UI surfacing) ──────────
    # ``todo_write`` is the task-plan surface · call it from any
    # multi-step agent turn to drive the UI's live checklist.
    "todo_read": {"group": "agent_meta", "atomic": True},
    "todo_write": {"group": "agent_meta", "atomic": True},
    "search_skills": {"group": "agent_meta", "atomic": True},
    "query_skill": {"group": "agent_meta", "atomic": True},
    "execute_skill": {"group": "agent_meta", "atomic": True},
    "search_capabilities": {"group": "agent_meta", "atomic": True},
    "query_capability": {"group": "agent_meta", "atomic": True},
    "use_capability": {"group": "agent_meta", "atomic": True},
    # ── delegation (Claude-style sub-agent / Task tool) ─────
    # ``call_agent`` spawns a focused ephemeral role (researcher /
    # debugger / reviewer / ...). Backed by the existing
    # ``runtime.execution.subagents.call_subagent`` bridge + the
    # ephemeral role runner already wired up in app.py.
    "call_agent": {"group": "delegation", "atomic": False},
    # ``call_agent_parallel`` spawns N sub-agents concurrently, all
    # sharing the same blackboard so they can pass partial findings
    # to each other. Counts as 1 against the per-turn delegation
    # budget regardless of N.
    "call_agent_parallel": {"group": "delegation", "atomic": False},
    # ``workflow`` runs a model-authored orchestration script that fans out
    # subagents (agent/phase/log/parallel/pipeline) and returns a JSON value.
    "workflow": {"group": "workflow", "atomic": False},
    # ── jobs (dsh background tasks) ─────────────────────────
    # ``call_agent_background`` starts one subagent in the background and
    # returns a job id; ``job_list`` / ``job_output`` / ``job_kill`` are the
    # generic controls over the process-local job registry. The parent is
    # notified in-session when a job settles.
    "call_agent_background": {"group": "jobs", "atomic": False},
    "job_list": {"group": "jobs", "atomic": True},
    "job_output": {"group": "jobs", "atomic": False},
    "job_kill": {"group": "jobs", "atomic": True},
    # ── blackboard (turn-scoped shared state for multi-agent) ─
    # Substrate for parallel sub-agents to exchange findings.
    # Atomic-safe (no I/O outside in-process dict).
    "bb_read": {"group": "blackboard", "atomic": True},
    "bb_write": {"group": "blackboard", "atomic": True},
    "bb_keys": {"group": "blackboard", "atomic": True},
    # ── self-scheduling (mid-turn cron · same store as the UI) ──
    "schedule_task": {"group": "cron", "atomic": False},
    "list_scheduled_tasks": {"group": "cron", "atomic": False},
    "cancel_scheduled_task": {"group": "cron", "atomic": False},
}


def _add_file_backed_skill_catalog() -> None:
    """Add SKILL.md prompt packages that live beside this module.

    The literal catalog above owns system/automation skills. File-backed
    domain skills are discovered from ``all_skills/<name>/SKILL.md`` so
    imported skill packs do not require editing this Python file for every
    single prompt template.

    SKILL.md ``aliases:`` (optional list) mirror the same handler under
    additional trigger names — registered here in ``_CATALOG`` so
    ``ALL_SKILL_IDS`` includes both the canonical name and every alias.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent
    try:
        skill_dirs = sorted(p for p in root.iterdir() if (p / "SKILL.md").is_file())
    except OSError:
        return
    for skill_dir in skill_dirs:
        canonical = skill_dir.name
        _CATALOG.setdefault(canonical, {"group": "market", "atomic": False})

        # Cheap aliases parse — just look for a top-level ``aliases:`` list.
        # Falls back to no aliases on any error; the loader proper does
        # the real frontmatter parse.
        try:
            text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for alias in _peek_aliases(text):
            _CATALOG.setdefault(alias, {"group": "market", "atomic": False})


def _peek_aliases(text: str) -> list[str]:
    """Best-effort extraction of ``aliases:`` from SKILL.md frontmatter.

    We can't import ``runtime.execution.suckers.market_skills`` here
    (circular at module import time), so we do a small inline parse:
    only catches the simple ``aliases: [a, b]`` and ``aliases: a, b``
    forms. Anything more exotic falls through and is picked up by the
    real loader at registration time — _CATALOG entries for those will
    appear after ``register_all`` runs, not at import.
    """
    if not text.startswith("---"):
        return []
    end = text.find("\n---", 3)
    if end < 0:
        return []
    fm = text[3:end]
    for line in fm.splitlines():
        s = line.strip()
        if not s.lower().startswith("aliases:"):
            continue
        rest = s.split(":", 1)[1].strip()
        if rest.startswith("[") and rest.endswith("]"):
            inner = rest[1:-1]
            return [a.strip().strip('"').strip("'") for a in inner.split(",") if a.strip()]
        if rest:
            return [a.strip().strip('"').strip("'") for a in rest.split(",") if a.strip()]
    return []


_add_file_backed_skill_catalog()

ALL_SKILL_IDS: frozenset[str] = frozenset(_CATALOG)
# BASE_SKILL_IDS is the canonical atomic-safe set exposed to every agent.
# It must equal ATOMIC_SKILL_NAMES from suckers/layers.py — that module
# is what Arm.can_use() consults for the "any arm implicitly allows these"
# rule. Keeping them in sync is enforced by the assertion below.
BASE_SKILL_IDS: frozenset[str] = frozenset(sid for sid, meta in _CATALOG.items() if meta["atomic"])
assert BASE_SKILL_IDS == ATOMIC_SKILL_NAMES, (
    f"all_skills.BASE_SKILL_IDS drift from suckers.layers.ATOMIC_SKILL_NAMES: "
    f"catalog={sorted(BASE_SKILL_IDS)} vs layers={sorted(ATOMIC_SKILL_NAMES)}"
)


def skill_group(skill_id: str) -> str | None:
    """Return the group name for a skill, or None if unknown."""
    meta = _CATALOG.get(skill_id)
    return meta["group"] if meta else None


def skills_in_group(group: str) -> frozenset[str]:
    """All skill ids belonging to a group."""
    return frozenset(sid for sid, meta in _CATALOG.items() if meta["group"] == group)


# ═══════════════════════════════════════════════════════════
# Registrars · one per group
# ═══════════════════════════════════════════════════════════

_GROUP_REGISTRARS: dict[str, Callable[[SkillRegistry], Any]] = {
    "builtin": _register_builtins,
    "fs_search": _register_fs_search,
    "web": _register_web,
    "crawler": _register_crawler,
    "fs_write": _register_fs_write,
    "code_quality": _register_code_quality,
    "git": _register_git,
    "shell": _register_shell,
    "computer": lambda registry: _register_computer(registry, verify_tests=False),
    "browser": _register_browser,
    "browser_act": _register_browser_act,
    "memory": _register_memory,
    "mode": _register_mode,
    "agent_meta": _register_agent_meta,
    "ask_user": _register_ask_user_question,
    "delegation": _register_delegation,
    "workflow": _register_workflow,
    "jobs": _register_jobs,
    "blackboard": _register_blackboard,
    "kimi_compat": _register_kimi_compat,
    "skill_library": _register_skill_library,
    "code_intel": _register_code_intel,
    "lsp": _register_lsp,
    "agent_docs": _register_agent_docs,
    "cron": _register_cron,
    "market": lambda registry: _register_market(
        registry,
        respect_enabled_flag=False,
        skip_registered_directories=True,
    ),
}


# Local/offline mode keeps the complete coding and desktop surface while
# removing capabilities whose primary purpose is reaching external web
# content.  ``enable_web_skills=False`` must not collapse code mode to the
# five read-only builtins.
LOCAL_SKILL_GROUPS: frozenset[str] = frozenset(
    {
        "builtin",
        "fs_search",
        "fs_write",
        "code_quality",
        "git",
        "shell",
        "computer",
        "memory",
        "mode",
        "agent_meta",
        "ask_user",
        "delegation",
        "workflow",
        "jobs",
        "blackboard",
        "skill_library",
        "code_intel",
        "lsp",
        "agent_docs",
        "cron",
    }
)


# Groups excluded from ``LOCAL_SKILL_GROUPS`` — i.e., only registered when
# ``enable_web_skills=True``. The ReAct dispatcher uses this to distinguish
# "tool is known but disabled by config" from "tool is completely unknown",
# so the model gets actionable feedback instead of a generic "unregistered"
# message and the UI can surface a one-click enable prompt.
WEB_ONLY_GROUPS: frozenset[str] = frozenset(set(_GROUP_REGISTRARS) - set(LOCAL_SKILL_GROUPS))


def is_known_but_disabled_tool(name: str) -> tuple[bool, str | None]:
    """Check if a tool name belongs to a known but currently-disabled group.

    Returns ``(True, group_name)`` if the tool is recognized by the catalog
    but its group is excluded from local/offline mode (i.e., it would be
    registered under ``enable_web_skills=True``). Returns ``(False, None)``
    otherwise — either the tool is completely unknown, or its group is
    already registered and the failure lies elsewhere.

    Called from the ReAct dispatcher's unregistered-tool branch, so the
    caller already knows the tool is not in the registry; this function
    explains *why* — config-disabled vs. genuinely unknown.
    """
    group = skill_group(name)
    if group is None:
        return False, None
    if group in WEB_ONLY_GROUPS:
        return True, group
    return False, None


def _register_groups(registry: SkillRegistry, groups: set[str] | frozenset[str]) -> None:
    # Import the runtime-policy module directly.  ``runtime.platform`` also
    # contains a legacy ``capabilities`` package, so importing the attribute
    # from the parent package becomes order-dependent once that package has
    # been imported and may resolve to a module without ``load``.
    from runtime.platform.runtime_policy.capabilities import load as _load_capabilities

    disabled = _load_capabilities().disabled_skill_groups()
    for name, fn in _GROUP_REGISTRARS.items():
        if name not in groups:
            continue
        if name in disabled:
            _log.info(
                "skill group %r disabled by automation capability gate "
                "— skipping (edit data/capabilities.json or Settings → 自动化 to re-enable)",
                name,
            )
            continue
        try:
            fn(registry)
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "skill group %r failed to register (%s: %s) — skipping",
                name,
                type(exc).__name__,
                exc,
            )


def register_all(registry: SkillRegistry) -> None:
    # Native registrars win name collisions with prompt packs.  The external
    # registry catalog is then bootstrapped/loaded (or falls back to packaged
    # skills), and the legacy market group finally fills non-colliding offline
    # capabilities.  A completely missing distribution is a startup error;
    # register_prompt_market_skills deliberately does not fail silently.
    groups = set(_GROUP_REGISTRARS)
    groups.remove("market")
    _register_groups(registry, groups)
    _register_prompt_market(registry)
    _register_groups(registry, {"market"})


def register_local(registry: SkillRegistry) -> None:
    """Register the offline/local catalog, including full coding tools.

    This excludes web search, crawler, browser and remote Kimi/market
    groups, but retains filesystem writes, shell, git, test/lint/format,
    code intelligence and local desktop operation.
    """
    _register_groups(registry, LOCAL_SKILL_GROUPS)


def register_base(registry: SkillRegistry) -> None:
    """Register only the atomic base skills.

    Splits across THREE registrars:
        * ``_register_builtins``     — list_cwd/read_file/file_stats/count_words/hash_text
        * ``_register_fs_search``    — glob_files/grep_text/tree/read_file_range
        * ``_register_memory``       — remember/recall/note_user/diary_write
        * ``_register_mode``         — exit_plan_mode (protocol-level · plan→tier)

    Keep ``BASE_SKILL_IDS`` (catalog) · ``ATOMIC_SKILL_NAMES`` (layers)
    and these registrar calls in sync. The invariant assertion at
    module import will scream if they drift.
    """
    _register_builtins(registry)
    _register_fs_search(registry)
    _register_memory(registry)
    _register_mode(registry)


def register_subset(
    registry: SkillRegistry,
    skill_ids: set[str] | frozenset[str] | list[str],
) -> None:
    """Register the minimum set of groups needed to cover ``skill_ids``.

    Registration is coarse — a group is either fully registered or not
    at all (that's how the suckers registrars work). Asking for
    ``{"git_commit"}`` still registers the whole ``git`` group. The
    actual per-agent refinement happens at the *agent* layer via
    ``allowed_skills`` whitelisting; this function only ensures the
    skills EXIST in the registry so the agent CAN whitelist them.
    """
    wanted: set[str] = set(skill_ids)
    needed_groups: set[str] = set()
    for sid in wanted:
        g = skill_group(sid)
        if g is not None:
            needed_groups.add(g)
        else:
            _log.warning(
                "register_subset: unknown skill id %r — skipping",
                sid,
            )
    for g in needed_groups:
        fn = _GROUP_REGISTRARS[g]
        try:
            fn(registry)
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "skill group %r failed (%s: %s) — skipping",
                g,
                type(exc).__name__,
                exc,
            )


def register_group(registry: SkillRegistry, group: str) -> list[str]:
    """Register a single skill group into ``registry``.

    Used by the ``POST /api/capabilities/enable`` endpoint to hot-load
    a group that was excluded at startup by ``enable_web_skills=False``.
    Returns the list of skill names newly added (empty if the group was
    already registered, unknown, or failed to register).
    """
    import contextlib

    fn = _GROUP_REGISTRARS.get(group)
    if fn is None:
        return []
    before: set[str] = set()
    with contextlib.suppress(Exception):
        before = set(registry.all_names())
    try:
        fn(registry)
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "register_group: group %r failed (%s: %s)",
            group,
            type(exc).__name__,
            exc,
        )
        return []
    after: set[str] = set()
    with contextlib.suppress(Exception):
        after = set(registry.all_names())
    return sorted(after - before)


# ═══════════════════════════════════════════════════════════
# Skill kind classification · system / automation / domain
# ═══════════════════════════════════════════════════════════
#
# Three-way bucket used by /api/skills filtering and by the ReAct
# catalog to decide what a given agent should see:
#
#   * system     — internal/atomic groups (memory, builtin, bb, kg, git,
#                  shell, fs_write, web, code_intel, delegation, mode,
#                  agent_meta, skill_library). NOT user-toggleable;
#                  governed by per-agent arm whitelist.
#   * automation — high-risk groups (browser, computer). Governed by
#   * domain     — file-backed business skills (docx / pdf / gmail /
#                  shopify / ...). User-installable; visible to an agent
#                  only when skill.affinity ∩ agent.affinity is non-empty
#                  (fail-open when metadata missing).
#
# Keep these sets in sync with ``_GROUP_REGISTRARS`` keys above.
# meta_router re-exports these for the /api/skills wire payload; react_loop
# uses them to filter the ReAct catalog per-agent.

_SYSTEM_GROUPS: frozenset[str] = frozenset(
    {
        "builtin",
        "fs_search",
        "memory",
        "mode",
        "agent_meta",
        "delegation",
        "workflow",
        "jobs",
        "blackboard",
        "skill_library",
        "code_intel",
        "lsp",
        "fs_write",
        "git",
        "shell",
        "web",
        "kimi_compat",
        "cron",
    }
)

_AUTOMATION_GROUPS: frozenset[str] = frozenset(
    {
        "browser",
        "browser_act",
        "computer",
    }
)

import re as _re

_AUTOMATION_NAME_PATTERN = _re.compile(
    r"^(browser_|live_browser_|screen_|mouse_|keyboard_|computer_)"
)


def skill_kind(skill_id: str) -> str:
    """Classify a skill as ``"system"`` / ``"automation"`` / ``"domain"``.

    Lookup order:
      1. group in ``_AUTOMATION_GROUPS`` → automation
      2. group in ``_SYSTEM_GROUPS`` → system
      3. name matches automation prefix pattern → automation (catches
         skills like ``computer_use_loop`` that are registered outside
         ``_GROUP_REGISTRARS`` via VisionPlanner)
      4. otherwise → domain (covers file-backed SKILL.md catalog +
         anything else outside known system groups)
    """
    group = skill_group(skill_id)
    if group in _AUTOMATION_GROUPS:
        return "automation"
    if group in _SYSTEM_GROUPS:
        return "system"
    if _AUTOMATION_NAME_PATTERN.match(skill_id):
        return "automation"
    return "domain"


__all__ = [
    "ALL_SKILL_IDS",
    "BASE_SKILL_IDS",
    "skill_group",
    "skill_kind",
    "skills_in_group",
    "register_all",
    "register_base",
    "register_subset",
    "register_group",
    "WEB_ONLY_GROUPS",
    "is_known_but_disabled_tool",
]
