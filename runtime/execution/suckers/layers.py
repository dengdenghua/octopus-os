from __future__ import annotations

from typing import Any

from runtime.platform.models import SkillId

# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════
#
#
# ═══════════════════════════════════════════════════════════


ATOMIC_SKILL_NAMES: frozenset[str] = frozenset(
    {
        "list_cwd",
        "read_file",
        "file_stats",
        "count_words",
        "hash_text",
        "use_chatgpt_connector",
        # file discovery/search · read-only project inspection helpers.
        "glob_files",
        "grep_text",
        "tree",
        "read_file_range",
        # memory · per-agent state writes · scoped to the agent's own core dir
        "remember",
        "recall",
        "note_user",
        "diary_write",
        "update_soul",
        "list_soul_history",
        "revert_soul",
        "recall_scores",
        "analyze_soul_impact",
        "auto_regression_check",  # atomic: reverts SOUL only w/ ≥5 samples drop
        "list_learned_skills",  # read-only enumeration · safe atomic
        # code intelligence · read-only AST queries · safe atomic
        "code_analyze",
        "code_search",
        "code_find_symbol",
        "code_dependency_graph",
        "get_available_voices",
        "get_data_source_desc",
        # knowledge graph · on-demand read-only query
        "kg_query",
        # mode · protocol-level transition out of plan mode · every agent
        # needs it or plan mode becomes a trap (no way to exit).
        "exit_plan_mode",
        # agent-meta · task plan surfacing to the user (▢/⏳/☑ panel).
        # Must be atomic so every agent implicitly exposes it · leaving
        # this out kills the "live progress checklist" UX on any agent
        # that didn't explicitly declare it in ``allowed_skills``.
        "todo_read",
        "todo_write",
        "search_skills",
        "query_skill",
        "execute_skill",
        "search_capabilities",
        "query_capability",
        "use_capability",
        # interactive ask-user-question · structured multiple-choice
        # decision card. Atomic so every agent can prompt the user
        # without explicit allowlisting.
        "ask_user_question",
        # blackboard · turn-scoped shared state for parallel sub-agents.
        # In-process dict, no I/O · trivially atomic. Always-on so every
        # agent (lead OR sub-agent) can use bb_read/bb_write to exchange
        # findings within the same turn.
        "bb_read",
        "bb_write",
        "bb_keys",
        # jobs · in-memory registry reads/writes, no I/O. ``job_output``
        # (wait may block) and ``call_agent_background`` (spawns a worker
        # thread) stay non-atomic.
        "job_list",
        "job_kill",
        # NOTE: ``call_agent`` used to live here. That was wrong:
        # subagents are isolated Agent/Task dispatches, not per-step
        # atomic skills. Team-chat delegates via ``[ROUTE TO: <id>]``;
        # programmatic callers use runtime.execution.subagents.
    }
)


# Ephemeral sub-agents must never be granted long-term memory / SOUL skills.
# A sub-agent runs without a propagated Session (current_session() is None in
# the dispatch thread), so calling them raises RuntimeError; and even if a
# Session were bound, a sub-agent must not mutate the parent agent's durable
# memory. Used both to strip these skills from an ephemeral role's advertised
# tool list and as a hard gate in the sub-agent tool executor.
EPHEMERAL_MEMORY_SKILLS: frozenset[str] = frozenset(
    {
        "remember",
        "recall",
        "note_user",
        "diary_write",
        "update_soul",
        "list_soul_history",
        "revert_soul",
        "recall_scores",
        "analyze_soul_impact",
        "auto_regression_check",
        "deep_reflect",
        "deep_evolve",
    }
)


def is_atomic(skill_name: str | SkillId) -> bool:
    return str(skill_name) in ATOMIC_SKILL_NAMES


def as_skill_ids() -> list[SkillId]:
    return [SkillId(n) for n in sorted(ATOMIC_SKILL_NAMES)]


_BLACKBOARD_SKILLS = frozenset({"bb_read", "bb_write", "bb_keys"})


# ── read-only tool surface (verifier / voter roles) ─────────────────────
# An ALLOW-list, not a deny-list, and deliberately so: a deny-list silently
# leaks every write skill added after it was written, whereas an omission here
# only costs a verifier one tool. Membership rule: the skill observes state and
# leaves nothing behind — no filesystem write, no shell, no git mutation, no
# outbound send, and no agent-memory/SOUL mutation (``remember`` / ``diary_write``
# / ``update_soul`` are atomic but they still persist).
#
# ``exec_shell`` is absent on purpose. A judge that can run the test suite would
# be stronger, but shell is the write path — verification that needs to EXECUTE
# belongs on the produce side (``verdict_repair``'s producer, a graph node),
# not on a ballot.
READ_ONLY_SKILL_NAMES: frozenset[str] = frozenset(
    {
        # filesystem · read + discovery
        "read_file",
        "read_file_range",
        "list_cwd",
        "file_stats",
        "count_words",
        "hash_text",
        "glob_files",
        "grep_text",
        "tree",
        # code intelligence · read-only AST / index queries
        "code_analyze",
        "code_search",
        "code_find_symbol",
        "code_dependency_graph",
        "kg_query",
        # language server · pure queries
        "lsp_diagnostics",
        "lsp_hover",
        "lsp_references",
        "lsp_definitions",
        # git · inspection only (no add/commit/checkout/push)
        "git_diff",
        "git_log",
        "git_status",
        # external evidence
        "fetch_url",
        "web_search",
        "web_extract",
        # shared state · READ side only. bb_write is excluded even though the
        # blackboard is an in-memory turn dict: a voter that publishes to the
        # board can steer its fellow voters, which is the one thing an
        # independent panel must not be able to do.
        "bb_read",
        "bb_keys",
        "todo_read",
        # catalog introspection. execute_skill is safe to include because it
        # rejects side-effecting skills itself (``is_side_effecting`` fails
        # closed on unknown affinity).
        "search_skills",
        "query_skill",
        "execute_skill",
        "search_capabilities",
        "query_capability",
        # agent memory · recall side only
        "recall",
        "recall_scores",
        "list_soul_history",
        "list_learned_skills",
    }
)


def is_read_only_skill(skill_name: str | SkillId) -> bool:
    return str(skill_name) in READ_ONLY_SKILL_NAMES


def select_tool_specs(
    allowlist: tuple[str, ...],
    all_specs: list[Any],
    *,
    read_only: bool = False,
) -> list[Any]:
    """Pick which tool specs an ephemeral sub-agent may use (by ``spec.name``).

    * Non-empty allowlist → exactly the named, registered skills, plus the
      always-on blackboard skills (``bb_*``) so parallel siblings can share
      state even if a role forgot to list them.
    * Empty allowlist → the atomic-safe inheritance set
      (``ATOMIC_SKILL_NAMES``), NOT the full catalog. An empty allowlist must
      not silently grant ``exec_shell`` / write / patch skills the role never
      asked for. ``bb_*`` are themselves atomic, so collaboration is preserved.

    ``read_only`` intersects the result with :data:`READ_ONLY_SKILL_NAMES`. It
    is applied LAST, after the allowlist and after the ``bb_*`` top-up, so it
    cannot be widened by a role definition or by a dynamic grant — a verifier
    stays read-only no matter what the caller asked to hand it.
    """
    if allowlist:
        spec_set = set(allowlist)
        by_name = {s.name: s for s in all_specs}
        tool_specs = [by_name[name] for name in allowlist if name in by_name]
        for s in all_specs:
            if s.name in _BLACKBOARD_SKILLS and s.name not in spec_set:
                tool_specs.append(s)
    else:
        tool_specs = [s for s in all_specs if is_atomic(s.name)]
    if read_only:
        return [s for s in tool_specs if is_read_only_skill(s.name)]
    return tool_specs
