"""Registrar for memory_skills · extracted from memory_skills.py.

Contains only ``register_memory_skills``.  All handler functions and
path helpers remain in ``memory_skills`` (they must stay there because
tests patch ``memory_skills._PROJECT_ROOT`` etc. via monkeypatch and
expect the handlers to see the patched values at call time).

Import order: ``memory_skills`` defines all handlers first, THEN imports
``register_memory_skills`` from this submodule at the bottom of the file.
When this module is loaded, ``memory_skills`` is already in
``sys.modules`` with all handlers defined, so the imports below succeed.
"""

from __future__ import annotations

# Handlers are imported from the parent module.  This works because
# memory_skills.py imports register_memory_skills (below) AFTER all
# handler definitions, so by the time Python loads this submodule the
# names are already bound in the partially-initialised memory_skills
# module.
from .memory_skills import (
    _analyze_soul_impact,
    _auto_regression_check,
    _deep_evolve,
    _deep_reflect,
    _diary_write,
    _list_soul_history,
    _note_user,
    _recall,
    _recall_scores,
    _remember,
    _revert_soul,
    _update_soul,
)
from .registry import Skill, SkillRegistry
from .testing import SkillExpect, SkillTestCase


def _register(registry: SkillRegistry, skill: Skill) -> None:
    """Idempotent family registration.

    The same family may be loaded twice — once by the default skill
    assembly (``all_skills``) and once by the composition-layer ``memory_arm``
    plugin. Duplicate names are therefore replaced (last definition wins)
    instead of raising, so re-loading a block is always safe.
    """
    registry.register(skill, replace=True)


def register_memory_skills(registry: SkillRegistry) -> int:
    """Register remember / recall / note_user / diary_write."""

    _register(
        registry,
        Skill(
            name="remember",
            description=(
                "Save a durable fact to long-term memory. CALL THIS WHEN "
                "the user mentions something worth remembering across "
                "future conversations: project names, deadlines, decisions "
                "made, key file paths, environment quirks, recurring "
                "preferences. Args: {fact: string, tags?: list[string], "
                "scope?: 'agent'|'project'|'team'|'team_agent'|'global'}. "
                "Default scope is 'agent'. Use 'team' for shared Team "
                "decisions, 'team_agent' for this member inside the Team, "
                "and 'project' for repo/workspace conventions. "
                "Don't use for transient context — only things that would "
                "still matter next week."
            ),
            affinity=["memory", "agent_state"],
            cost_profile="low",
            trusted_source="skill://private/remember",
            handler=_remember,
            tests=[
                SkillTestCase(
                    name="no_session_raises",
                    tier="golden",
                    args={"fact": "ping"},
                    expect=SkillExpect(raises="RuntimeError"),
                ),
            ],
        ),
    )

    _register(
        registry,
        Skill(
            name="recall",
            description=(
                "Look up previously-saved facts from long-term memory. "
                "CALL THIS AT THE START OF A TURN when the user references "
                "a project, person, or context you might have notes on — "
                "or anytime you'd otherwise say 'I don't remember'. "
                "Args: {query?: string (substring filter), "
                "limit?: int (default 20, latest N if no query), "
                "scope?: 'all'|'agent'|'project'|'team'|'team_agent'|'global'}. "
                "Default scope is 'all' visible tiers. Returns matching "
                "memory lines prefixed with their scope."
            ),
            affinity=["memory", "agent_state"],
            cost_profile="low",
            trusted_source="skill://private/recall",
            handler=_recall,
            tests=[
                SkillTestCase(
                    name="no_session_raises",
                    tier="golden",
                    args={},
                    expect=SkillExpect(raises="RuntimeError"),
                ),
            ],
        ),
    )

    _register(
        registry,
        Skill(
            name="note_user",
            description=(
                "Record a user trait or preference (communication style, "
                "skill level, language preference, recurring patterns). "
                "CALL THIS WHEN you learn something about the user that "
                "should shape how you respond going forward — e.g. 'prefers "
                "Chinese', 'wants terse answers', 'is a senior engineer', "
                "'doesn't know Rust'. Args: {trait: string}. Distinct from "
                "`remember` — that's for facts about the world / project, "
                "this is for facts about the user."
            ),
            affinity=["memory", "user_profile"],
            cost_profile="low",
            trusted_source="skill://private/note_user",
            handler=_note_user,
            tests=[
                SkillTestCase(
                    name="no_session_raises",
                    tier="golden",
                    args={"trait": "likes concise answers"},
                    expect=SkillExpect(raises="RuntimeError"),
                ),
            ],
        ),
    )

    _register(
        registry,
        Skill(
            name="diary_write",
            description=(
                "Append a timestamped entry to today's diary file "
                "(`diary/YYYY-MM-DD.md`) for the current agent."
            ),
            affinity=["memory", "diary"],
            cost_profile="low",
            trusted_source="skill://private/diary_write",
            handler=_diary_write,
            tests=[
                SkillTestCase(
                    name="no_session_raises",
                    tier="golden",
                    args={"entry": "hello diary"},
                    expect=SkillExpect(raises="RuntimeError"),
                ),
            ],
        ),
    )

    _register(
        registry,
        Skill(
            name="update_soul",
            description=(
                "Append a SELF-learned lesson to your own SOUL.md. "
                "The lesson is auto-loaded into your system prompt on "
                "next session boot, so this is how you get smarter "
                "over time. Use ONLY for durable insights about your "
                "own work style — workarounds that actually worked, "
                "approaches that failed and why, quirks of a tool's "
                "args that bit you, multi-step patterns worth "
                "repeating. NOT for facts about the world (use "
                "`remember`) or about the user (use `note_user`).\n"
                "Every successful write is auto-snapshotted to "
                "`.soul_history/` so you can `revert_soul` if a "
                "lesson turns out to hurt later turns.\n"
                "Args: {lesson: imperative one-liner, tag?: short "
                "category like 'tooling' / 'workflow' / 'mistake'}."
            ),
            affinity=["memory", "self_evolution", "soul"],
            cost_profile="low",
            trusted_source="skill://private/update_soul",
            handler=_update_soul,
            tests=[
                SkillTestCase(
                    name="empty_lesson_returns_error",
                    tier="golden",
                    args={"lesson": ""},
                    expect=SkillExpect(schema_keys=["ok", "error"]),
                    custom_predicate=lambda r: isinstance(r, dict) and r.get("ok") is False,
                ),
            ],
        ),
    )

    _register(
        registry,
        Skill(
            name="list_soul_history",
            description=(
                "List the most recent SOUL.md snapshots (newest "
                "first). Each `update_soul` and `revert_soul` "
                "leaves a snapshot here. Use this to see what "
                "changed when before deciding to roll back. "
                "Args: {limit?: int, default 10}. Returns "
                "{ok, count, snapshots: [{filename, size_bytes, "
                "mtime_iso}, ...]}."
            ),
            affinity=["memory", "self_evolution", "soul"],
            cost_profile="low",
            trusted_source="skill://private/list_soul_history",
            handler=_list_soul_history,
            tests=[
                # Same pattern as the other memory skills: outside a
                # Session, the skill raises RuntimeError because
                # there's no agent dir to look in. This is the safe
                # fail-closed default.
                SkillTestCase(
                    name="no_session_raises",
                    tier="golden",
                    args={"limit": 5},
                    expect=SkillExpect(raises="RuntimeError"),
                ),
            ],
        ),
    )

    # ── self-evaluation skills (Phase B1) ─────────────────
    _register(
        registry,
        Skill(
            name="recall_scores",
            description=(
                "Return your most recent per-turn quality scores "
                "(0.0/0.5/1.0 each, with a `reason` tag and the "
                "`soul_hash` that was active at score time). Useful "
                "to see if you're trending well or poorly. The score "
                "is heuristic (computed from rounds / tool errors / "
                "interruption / token usage) — zero LLM cost. "
                "Args: {limit?: int, default 20}."
            ),
            affinity=["memory", "self_evolution", "scoring"],
            cost_profile="low",
            trusted_source="skill://private/recall_scores",
            handler=_recall_scores,
            tests=[
                SkillTestCase(
                    name="no_session_raises",
                    tier="golden",
                    args={"limit": 5},
                    expect=SkillExpect(raises="RuntimeError"),
                ),
            ],
        ),
    )

    _register(
        registry,
        Skill(
            name="deep_reflect",
            description=(
                "B2 self-evaluation · LLM-judged review of recent "
                "scored turns. Reads the last `window` turns + "
                "current SOUL, sends a single judging call to a "
                "cheap model (~2-3¢), returns structured envelope: "
                "{verdict: {overall_score 0-100, trend, "
                "dominant_failure_mode, lesson_quality, action: "
                "add_lesson|revert_last|no_action, action_detail, "
                "rationale}}. Use this when the heuristic "
                "`analyze_soul_impact` says 'inconclusive' but you "
                "want a real LLM opinion before acting. Args: "
                "{window?: int (default 20), model?: string}."
            ),
            affinity=["memory", "self_evolution", "scoring", "reflection"],
            cost_profile="mid",  # 1 cheap LLM call
            trusted_source="skill://private/deep_reflect",
            handler=_deep_reflect,
            tests=[
                SkillTestCase(
                    name="no_session_raises",
                    tier="golden",
                    args={"window": 5},
                    expect=SkillExpect(raises="RuntimeError"),
                ),
            ],
        ),
    )

    _register(
        registry,
        Skill(
            name="deep_evolve",
            description=(
                "B3 deep self-evolution · MiniMax-style autonomous "
                "loop. Each round: LLM proposes K candidate SOUL "
                "changes → judges each candidate's predicted impact "
                "→ picks winner → optionally applies. EXPENSIVE "
                "(~10-30¢ per run · uses haiku-tier model). Default "
                "`dry_run=True` returns the full plan without "
                "mutating SOUL — review the proposed changes first, "
                "then re-run with `dry_run=False` to actually apply.\n"
                "Args: {window?: int (default 20), "
                "candidates_per_round?: int (1-5, default 3), "
                "max_rounds?: int (1-10, default 1), "
                "dry_run?: bool (default True)}.\n"
                "Returns full audit trail: all proposals, "
                "judgments, applied actions, and total cost."
            ),
            affinity=["memory", "self_evolution", "deep", "miniMax"],
            cost_profile="high",  # multiple LLM calls
            trusted_source="skill://private/deep_evolve",
            handler=_deep_evolve,
            tests=[
                SkillTestCase(
                    name="no_session_raises",
                    tier="golden",
                    args={"window": 5, "max_rounds": 1, "dry_run": True},
                    expect=SkillExpect(raises="RuntimeError"),
                ),
            ],
        ),
    )

    _register(
        registry,
        Skill(
            name="analyze_soul_impact",
            description=(
                "Statistical check: did the most recent SOUL.md "
                "change help or hurt your turn quality? Compares "
                "the average score before vs after the lesson was "
                "added. Returns {verdict: improved/regressed/"
                "neutral/inconclusive/no_change/no_data, before_avg, "
                "after_avg, delta, suggestion}. Run this before "
                "considering a `revert_soul`. Zero LLM cost · all "
                "heuristic.\n"
                "Args: {window?: int (per side, default 20), "
                "drop_threshold?: float (default 0.2)}."
            ),
            affinity=["memory", "self_evolution", "scoring"],
            cost_profile="low",
            trusted_source="skill://private/analyze_soul_impact",
            handler=_analyze_soul_impact,
            tests=[
                SkillTestCase(
                    name="no_session_raises",
                    tier="golden",
                    args={"window": 10},
                    expect=SkillExpect(raises="RuntimeError"),
                ),
            ],
        ),
    )

    _register(
        registry,
        Skill(
            name="auto_regression_check",
            description=(
                "Anti-self-harm guard: if the most recent SOUL.md "
                "change is hurting your scores (after_n ≥ 5 samples · "
                "delta < -threshold), automatically roll back via "
                "`revert_soul`. This is the safety counterpart to "
                "`deep_evolve(dry_run=False)`: register a governed candidate, "
                "this path verifies it actually helped. Zero LLM cost "
                "· defaults dry_run=False so it really reverts when "
                "triggered. Returns {action: reverted/would_revert/"
                "no_action, analysis, revert_result}.\n"
                "Args: {window?: int (default 20), drop_threshold?: "
                "float (default 0.2), min_samples?: int (default 5), "
                "dry_run?: bool (default False · set True to only "
                "report without reverting)}."
            ),
            affinity=["memory", "self_evolution", "safety", "scoring"],
            cost_profile="low",
            trusted_source="skill://private/auto_regression_check",
            handler=_auto_regression_check,
            tests=[
                SkillTestCase(
                    name="no_session_raises",
                    tier="golden",
                    args={"window": 10, "dry_run": True},
                    expect=SkillExpect(raises="RuntimeError"),
                ),
            ],
        ),
    )

    _register(
        registry,
        Skill(
            name="revert_soul",
            description=(
                "Roll your SOUL.md back N snapshots. Use this when "
                "you notice a recently-added lesson is causing "
                "regressions (over-cautious behavior, conflicting "
                "guidance, wrong tool choice, etc). The CURRENT "
                "state is snapshotted first under reason "
                "'pre-revert-<reason>' so the rollback is itself "
                "reversible.\n"
                "Args: {steps_back?: int (default 1), "
                "reason?: string (why you're reverting · "
                "stored as the snapshot tag)}."
            ),
            affinity=["memory", "self_evolution", "soul"],
            cost_profile="low",
            trusted_source="skill://private/revert_soul",
            handler=_revert_soul,
            tests=[
                SkillTestCase(
                    name="no_session_raises",
                    tier="golden",
                    args={"steps_back": 1, "reason": "test"},
                    expect=SkillExpect(raises="RuntimeError"),
                ),
            ],
        ),
    )

    # ── count of the always-registered skills above ───────
    # remember / recall / note_user / diary_write /
    # update_soul / list_soul_history / revert_soul /
    # recall_scores / analyze_soul_impact /
    # auto_regression_check / deep_reflect / deep_evolve
    total = 12

    # KG query · on-demand knowledge graph lookup
    try:
        from runtime.execution.suckers.kg_skill import register_kg_skill

        register_kg_skill(registry)
    except Exception:  # noqa: BLE001
        pass  # KG module optional

    # Cross-thread history · lets the agent read PAST conversations.
    # `recall` reads saved MEMORY.md facts; these read real transcripts,
    # which is the only way around per-thread context isolation.
    try:
        from runtime.execution.suckers.history_skill import register_history_skill

        total += register_history_skill(registry)
    except Exception:  # noqa: BLE001
        pass  # thread store optional (pure in-memory / test stacks)

    return total


__all__ = ["register_memory_skills"]
