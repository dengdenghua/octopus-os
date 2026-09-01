from __future__ import annotations

# ╔════════════════════════════════════════════════════════════════════════╗
# ║ delegation_skills.py · delegation / orchestration skill catalog.        ║
# ║                                                                        ║
# ║   The handler implementations live in ``_delegation_skills_*.py``       ║
# ║   submodules and are re-imported here so the public API surface (what   ║
# ║   tests and other modules import) is unchanged.  This file keeps only   ║
# ║   the ``register_delegation_skills`` entrypoint and the re-exports.     ║
# ║                                                                        ║
# ║   §1 shared helpers        → _delegation_skills_common                 ║
# ║   §2 _call_agent           → _delegation_skills_agent                  ║
# ║   §3 parallel fan-out      → _delegation_skills_parallel               ║
# ║   §4 vote gate             → _delegation_skills_vote                   ║
# ║   §5 orchestration loop    → _delegation_skills_orchestration          ║
# ║   §6 judge panels          → _delegation_skills_judge                  ║
# ║   §7 pipeline              → _delegation_skills_pipeline               ║
# ║   §8 declarative DAG       → _delegation_skills_graph                  ║
# ╚════════════════════════════════════════════════════════════════════════╝
#
# The names tests monkeypatch at this module level (``_allowed_agent_ids`` /
# ``_check_absolute_cap`` / ``_record_delegation``) are kept visible here so a
# monkeypatch is still observed at call time by the submodules that resolve
# them lazily via ``delegation_skills``.
from ._delegation_skills_agent import (
    _call_agent,
)
from ._delegation_skills_common import (
    _DYNAMIC_SKILL_PACKS,
    _VOTE_MAX,
    _allowed_agent_ids,
    _build_ballot_prompt,
    _bump_and_check,
    _coerce_name_list,
    _coerce_timeout_s,
    _coerce_vote_choices,
    _dedupe_names,
    _delegation_budget_exhausted_message,
    _derive_error_type,
    _display_name_for_agent_id,
    _emit_orchestration_progress,
    _empty_parallel_result,
    _extract_verdict,
    _format_role_catalog,
    _is_transient_error,
    _normalize_verdict,
    _parallel_route_decision,
    _resolve_custom_agent_id,
    _resolve_session_and_turn,
    _role_defaults_to_cheap,
    _route_context_risk_level,
    _should_auto_retry,
    _skill_context_from_spec,
    _tally_votes,
    _vote_note,
    _wrap_prompt_with_role_label,
    orchestration_progress_scope,
    workflow_settlement_scope,
)
from ._delegation_skills_graph import (
    _MAX_GRAPH_NODES,
    _coerce_graph_nodes,
    _plan_layers,
    _resolve_node_prompt,
    _run_agent_graph,
)
from ._delegation_skills_judge import (
    _run_tournament,
    _run_verdict_repair,
)
from ._delegation_skills_orchestration import (
    _ORCH_MAX_FINDINGS_PER_WORKER,
    _ORCH_MAX_FINDINGS_TOTAL,
    _ORCH_MAX_SPAWNS_CEILING,
    _coerce_roles,
    _dedupe_findings,
    _finder_prompt,
    _finder_spec,
    _findings_from_success,
    _norm_finding,
    _resolve_max_spawns,
    _run_orchestration,
    _split_findings,
    _synthesis_prompt,
    _synthesis_spec,
)
from ._delegation_skills_parallel import (
    _build_parallel_envelope,
    _call_agent_parallel,
    _coerce_parallel_specs,
)
from ._delegation_skills_pipeline import (
    _run_pipeline,
)
from ._delegation_skills_vote import (
    _call_agent_vote,
)
from .delegation_budget import (
    check_absolute_cap as _check_absolute_cap,
)
from .delegation_budget import (
    compute_fingerprint as _compute_fingerprint,
)
from .delegation_budget import (
    record_delegation as _record_delegation,
)
from .registry import Skill, SkillRegistry
from .testing import SkillExpect, SkillTestCase


def register_delegation_skills(registry: SkillRegistry) -> int:
    """Register `call_agent` for sub-agent delegation. Returns count.

    The description is generated each time so a re-register reflects
    the current advertised role catalog. App boot sequence:
    ``register_all`` first (registers with builtin advertisements),
    then ``set_subagent_registry`` + ``register_delegation_skills``
    again (to refresh, though user defs aren't advertised here ·
    they remain callable).
    """
    role_table = _format_role_catalog()
    description = (
        "Spawn an isolated specialist subagent for one focused "
        "subtask. The subagent runs in its own context window with "
        "its own tools, runs ONE turn, returns a single summary "
        "string back to you. This tool exists and works · the "
        "guidance below is about WHEN to use it, not whether you "
        "have it.\n"
        "\n"
        "Frequency: this is an EXCEPTION tool in normal chat/code turns, "
        "not a default reflex. In explicit `swarm` mode it may be used as "
        "part of a dynamic orchestration plan when the task is large enough. "
        "Most tasks lead handles itself. Hard cap is 1 call per "
        "turn — if you need delegation again on the same turn, the "
        "second call returns an error · do that work yourself.\n"
        "\n"
        "Use it when ALL three are true:\n"
        "  1. The subtask falls inside an advertised specialist "
        "role.\n"
        "  2. The lead's general competence is genuinely "
        "insufficient — you've thought about it for at least one "
        "turn first.\n"
        "  3. You haven't already used `call_agent` this turn.\n"
        "\n"
        "Skip delegation (do it yourself with normal tools) for:\n"
        "  - Anything you can finish in ≤ 2 more turns.\n"
        "  - Code work touching ≤ 3 files (read / edit / debug).\n"
        "  - Web research / fact lookup → use `web_search`.\n"
        "  - 'I don't know how to start' reflex → just start.\n"
        "  - Asking the user a clarifying question → reply directly.\n"
        "\n"
        "When the user asks for MULTIPLE delegations in one turn: "
        "pick the single most-specialist task and delegate that, "
        "then handle the rest yourself in the same turn. Tell the "
        "user what you split. Don't refuse the whole request — "
        "delegate one, do the others.\n"
        "\n"
        "Args: {agent_id: string (one of the names below), "
        "prompt: string (focused brief, written like a user message), "
        "skills?/tools?: list of concrete skill names, "
        "skill_pack(s)?: one or more of research/web/browser/files/code/"
        "review/write/memory/shell, plugins?: plugin or package hints, "
        "output_schema?: a JSON Schema object — pass it when you need the "
        "subagent's answer as a validated JSON object you can parse "
        "(returned under `parsed`), e.g. "
        '{"type":"object","properties":{"verdict":{"type":"string"}},'
        '"required":["verdict"]}}. '
        "Use skill packs when the subtask needs tools beyond the role's "
        "default allowlist.\n"
        "\n"
        "Advertised specialist roles:\n"
        f"{role_table}"
    )
    registry.register(
        Skill(
            name="call_agent",
            description=description,
            affinity=["delegation", "subagent", "task", "last-resort"],
            cost_profile="high",  # spawns a separate LLM turn
            trusted_source="skill://public/call_agent",
            handler=_call_agent,
            tests=[
                SkillTestCase(
                    name="missing_agent_id_returns_error",
                    tier="golden",
                    args={"agent_id": "", "prompt": "hi"},
                    expect=SkillExpect(
                        schema_keys=[
                            "agent_id",
                            "output",
                            "success",
                            "error",
                        ]
                    ),
                    custom_predicate=lambda r: (
                        isinstance(r, dict)
                        and r.get("success") is False
                        and "agent_id is required" in (r.get("error") or "")
                    ),
                ),
            ],
        ),
        replace=True,
    )

    # ── parallel variant ─────────────────────────────────────
    parallel_description = (
        "Spawn N sub-agents CONCURRENTLY (Kimi-style swarm) for tasks "
        "that decompose cleanly into independent sub-tasks each "
        "needing a specialist. All siblings + lead share a turn-scoped "
        "blackboard (bb_read / bb_write / bb_keys) so they can exchange "
        "partial findings.\n"
        "\n"
        "Availability: this is a real parallel delegation tool in ordinary "
        "Agent/ReAct mode as well as explicit swarm mode. In Agent mode, use "
        "it when multiple independent lanes would materially improve speed "
        "or quality; the lead remains responsible for synthesis and final "
        "delivery.\n"
        "\n"
        "Use it when:\n"
        "  - The task is genuinely parallel (research lanes, file areas, "
        "vendor comparisons, review lanes), AND\n"
        "  - Each sub-task fits an advertised specialist role, AND\n"
        "  - The lead can integrate the returned summaries into a final "
        "deliverable in the same turn.\n"
        "\n"
        "Skip if:\n"
        "  - Sub-tasks are sequential (each depends on previous output) — "
        "use serial reasoning instead.\n"
        "  - You'd just be parallelizing trivial work — overhead > gain.\n"
        "  - A single web_search/read_file pass is enough.\n"
        "\n"
        "Swarm workflow hint: update todo_write first, assign independent "
        "roles dynamically, ask workers to bb_write compact findings under "
        "clear keys, then bb_keys/bb_read, synthesize, verify/cross-check, "
        "and produce the final answer or file deliverable. Do NOT stop after "
        "the parallel call with only raw worker logs.\n"
        "\n"
        "Honesty contract: if the returned envelope has ``partial=True`` or "
        "``failed > 0``, your final answer MUST include a brief run-status "
        'line such as "Subagent status: 3/5 succeeded; 2 failed" and MUST '
        "not say that all lanes completed. Name the failed lanes from "
        "``failures[].task_label`` / ``failures[].agent_id`` and either "
        "disclose the gap or explicitly describe how you filled it yourself.\n"
        "\n"
        "Budget (2026-06 smart-budget): Each spec counts independently. "
        "First-time failures are FREE (you can fix and retry). Repeat "
        "failures (same agent + same prompt) count. Absolute cap: 5 "
        "delegations/turn. Wall-clock = max(per-agent time), not sum.\n"
        "\n"
        "Args: {specs: list[{agent_id: string, prompt: string, "
        "skills?/tools?: list of concrete skill names, "
        "skill_pack(s)?: research/web/browser/files/code/review/write/"
        "memory/shell, plugins?: plugin/package hints, "
        "isolate?: bool}], "
        "timeout_s?: int (per-agent, default 900)}.\n"
        "Use the canonical keys `agent_id` and `prompt`. Legacy `role` and "
        "`goal` are accepted for compatibility, but do not prefer them in new calls.\n"
        "\n"
        "`isolate: true` runs that ONE spec inside its own git worktree, so "
        "its writes cannot collide with a sibling's. Set it on every lane that "
        "EDITS FILES when more than one lane writes — parallel writes to a "
        "shared checkout corrupt each other. Read-only lanes should leave it "
        "off (a worktree costs a checkout). The lane's `diff` and `branch` come "
        "back in its result and nothing is auto-merged: reconciling parallel "
        "edits is yours to do. You cannot choose the directory — confinement is "
        "created on the trusted side.\n"
        "\n"
        "Returns: {ok, successes:[{role, agent_id, output, files_touched, "
        "diff?, branch?, isolated?}], "
        "failures:[{role, agent_id, error, error_type}], partial, total, "
        "success_count, notes:[...], plus legacy keys "
        "(results, count, succeeded, failed, outputs)}. ``ok=False`` only "
        "when zero workers succeed — partial failures still return ok=True "
        "with the surviving outputs and a [partial-degradation] note in "
        "``notes`` so you can synthesise from what came back.\n"
        "\n"
        "Available specialist roles (same as call_agent):\n"
        f"{role_table}"
    )
    registry.register(
        Skill(
            name="call_agent_parallel",
            description=parallel_description,
            affinity=["delegation", "subagent", "parallel", "swarm"],
            cost_profile="high",  # spawns N separate LLM turns
            trusted_source="skill://public/call_agent_parallel",
            handler=_call_agent_parallel,
            tests=[
                SkillTestCase(
                    name="empty_specs_returns_error",
                    tier="golden",
                    args={"specs": []},
                    expect=SkillExpect(
                        schema_keys=[
                            "ok",
                            "successes",
                            "failures",
                            "partial",
                            "total",
                            "success_count",
                            "notes",
                            "results",
                            "count",
                            "outputs",
                            "error",
                        ]
                    ),
                    custom_predicate=lambda r: (
                        isinstance(r, dict)
                        and r.get("count") == 0
                        and r.get("ok") is False
                        and r.get("partial") is False
                        and r.get("successes") == []
                    ),
                ),
            ],
        ),
        replace=True,
    )

    # ── consensus / vote gate ────────────────────────────────
    vote_description = (
        "Verification gate — spawn N INDEPENDENT voters on the SAME "
        "question and tally a MAJORITY verdict (with confidence + dissent). "
        "This is the adversarial-verify / judge-panel pattern: confirm or "
        "refute a claim with independent judgment instead of trusting one "
        "agent.\n"
        "\n"
        "Use it for decisions that matter: 'is this bug real?', 'does this "
        "patch actually fix it?', 'is this answer correct?', 'which option is "
        "safer: A or B?'. A natural pairing is call_agent_parallel (gather "
        "candidate answers) → call_agent_vote (adjudicate them).\n"
        "\n"
        "Each voter runs in its own context and must emit `VERDICT: <choice>` "
        "then `REASON: <one line>`. Unparseable replies count as abstentions; "
        "a split returns verdict=null with tie=true (no decision — you break "
        "it or escalate).\n"
        "\n"
        "Args: {question: string (the claim / question to judge), n?: int "
        "2-5 voters (default 3 · odd avoids ties), choices?: list[string] a "
        'fixed ballot e.g. ["yes","no"] (omit for a free-form verdict), '
        "agent_id?: voter role (default reviewer), timeout_s?: int}.\n"
        "\n"
        "Returns: {ok, verdict (null on tie / no-verdict), confidence (0-1), "
        "unanimous, tie, tie_between, tally:{choice:count}, "
        "votes:[{verdict, reason, codename, abstained}], votes_cast, "
        "abstentions, note, honesty_warning}.\n"
        "\n"
        "Budget: each voter is one delegation against the 5/turn cap, so a "
        "3-voter vote uses 3. Reserve it for decisions worth the spend."
    )
    registry.register(
        Skill(
            name="call_agent_vote",
            description=vote_description,
            affinity=["delegation", "verify", "vote", "consensus", "judge"],
            cost_profile="high",  # spawns N separate LLM turns
            trusted_source="skill://public/call_agent_vote",
            handler=_call_agent_vote,
            tests=[
                SkillTestCase(
                    name="missing_question_returns_error",
                    tier="golden",
                    args={"question": ""},
                    expect=SkillExpect(
                        schema_keys=[
                            "ok",
                            "verdict",
                            "tally",
                            "votes",
                            "note",
                        ]
                    ),
                    custom_predicate=lambda r: (
                        isinstance(r, dict)
                        and r.get("ok") is False
                        and "required" in (r.get("error") or "")
                    ),
                ),
            ],
        ),
        replace=True,
    )

    # ── deterministic orchestration loop ─────────────────────
    orchestrate_description = (
        "Run a DETERMINISTIC multi-round discovery loop end-to-end in one "
        "call: fan out N workers per round, split their findings (one per "
        "line), de-duplicate against everything seen so far, and loop until "
        "no new findings arrive (or the round budget runs out) — optionally "
        "vote-verifying each finding. The control flow (looping, dedup, "
        "stop-when-dry) is code, not the model, so nothing is skipped.\n"
        "\n"
        "Use it for EXHAUSTIVE discovery where one pass isn't enough: 'find "
        "all the edge cases', 'enumerate every place X happens', 'gather "
        "everything known about Y'. This is the loop the flat per-turn cap "
        "normally forbids — it runs inside a bounded spawn budget instead.\n"
        "\n"
        "Args: {goal: string (what to discover), n?: int 1-6 workers/round "
        "(default 3), rounds?: int 1-5 (default 2), patience?: int 0-3 dry "
        "rounds tolerated before stopping (default 1), verify?: bool "
        "(default false — vote-verify each finding), synthesize?: bool "
        "(default false — fold the confirmed findings into ONE coherent "
        "answer via a final synthesizer; returned in `synthesis`), choices?: "
        "[keep,drop]-style ballot for verify, max_spawns?: int total spawn "
        "budget (auto-sized, capped at 48), agent_id?: worker role, or a LIST "
        "of roles rotated across workers for diverse lenses, e.g. "
        "[researcher, explorer, critic] (default researcher)}.\n"
        "\n"
        "Returns: {ok, goal, collected:[...], confirmed:[...] (== collected "
        "unless verify), count, rounds_run, fresh_per_round:[...], verified, "
        "unverified, synthesis (str, '' unless synthesize), shared (bool — "
        "findings published to the turn blackboard), inherited (int — findings "
        "seeded from siblings' blackboard entries), stopped_reason "
        "(rounds|dry|budget|cap), budget_used, max_spawns, failures (list of "
        "{role, error, error_type} — de-duplicated sub-agent failure summaries, "
        "empty when every worker succeeded), failure_count (int — raw total of "
        "failed sub-agent runs), note (str — a warning when any sub-agent "
        "failed; '' otherwise)}.\n"
        "\n"
        "Coordination: findings are auto-shared on the turn's blackboard, so a "
        "sibling agent or a later orchestration on the same goal builds on "
        "them instead of re-discovering — enforced by the harness, no manual "
        "bb_write needed.\n"
        "\n"
        "Budget: one orchestration costs ONE against the 5/turn delegation "
        "cap; its internal fan-outs/votes draw from the bounded spawn budget, "
        "not the turn cap. Reserve it for genuinely multi-round work."
    )
    registry.register(
        Skill(
            name="run_orchestration",
            description=orchestrate_description,
            affinity=["delegation", "orchestration", "swarm", "discovery", "loop"],
            cost_profile="high",  # spawns many LLM turns under a budget
            trusted_source="skill://public/run_orchestration",
            handler=_run_orchestration,
            tests=[
                SkillTestCase(
                    name="missing_goal_returns_error",
                    tier="golden",
                    args={"goal": ""},
                    expect=SkillExpect(
                        schema_keys=[
                            "ok",
                            "collected",
                            "confirmed",
                            "count",
                        ]
                    ),
                    custom_predicate=lambda r: (
                        isinstance(r, dict)
                        and r.get("ok") is False
                        and "required" in (r.get("error") or "")
                    ),
                ),
            ],
        ),
        replace=True,
    )

    # ── verdict-gated repair loop ─────────────────────────────────
    verdict_repair_description = (
        "Run a task through a CLOSED quality loop in one call: a worker "
        "produces a result, an independent panel VOTES pass/fail on it, and a "
        "rejection drives a corrected re-attempt that is FED the reviewers' "
        "critique — produce → judge → rewrite → re-judge, bounded. The loop "
        "(stop-on-pass, bound, feeding the critique forward) is deterministic "
        "code, not the model, so a FAIL actually triggers a fix instead of just "
        "shipping.\n"
        "\n"
        "Use it when correctness matters more than speed and a single attempt "
        "isn't trustworthy: 'write this function AND make sure it's right', "
        "'draft the migration and have it reviewed until it passes', 'answer "
        "this, but verify before returning'.\n"
        "\n"
        "Args: {task: string (what to accomplish), agent_id?: worker role "
        "(default general), judge_n?: int 2-5 voters on the gate (default 3), "
        "max_repairs?: int 0-4 corrective re-attempts (default 2), choices?: "
        "[accept,reject]-style ballot where the FIRST label means accept "
        "(default [pass,fail])}.\n"
        "\n"
        "Returns: {ok, task, passed (bool — did it clear the gate), repaired "
        "(bool — only reached pass AFTER a repair), output (the accepted/best "
        "result), attempts, verdict, confidence, rounds:[{attempt, passed, "
        "verdict, critique}], max_spawns}.\n"
        "\n"
        "Budget: one call costs ONE against the 5/turn delegation cap; its "
        "internal produce/judge fan-outs draw from a bounded spawn budget."
    )
    registry.register(
        Skill(
            name="verdict_repair",
            description=verdict_repair_description,
            affinity=["delegation", "verify", "repair", "critique", "quality", "judge"],
            cost_profile="high",  # spawns producers + voters across rounds
            trusted_source="skill://public/verdict_repair",
            handler=_run_verdict_repair,
            tests=[
                SkillTestCase(
                    name="missing_task_returns_error",
                    tier="golden",
                    args={"task": ""},
                    expect=SkillExpect(
                        schema_keys=[
                            "ok",
                            "passed",
                            "output",
                            "attempts",
                            "rounds",
                        ]
                    ),
                    custom_predicate=lambda r: (
                        isinstance(r, dict)
                        and r.get("ok") is False
                        and "required" in (r.get("error") or "")
                    ),
                ),
            ],
        ),
        replace=True,
    )

    # ── tournament: best-of-N over isolated worktree candidates ───
    tournament_description = (
        "Attempt the SAME goal N independent times, each in its OWN isolated "
        "git worktree (no collisions), then have an independent panel VOTE on "
        "which result is best — best-of-N with a judge. Builds on echo's "
        "worktree isolation (the candidates) plus call_agent_vote (the judge); "
        "selection is deterministic code. NEVER auto-applies: the winning diff "
        "is returned for you to review and apply (reconciling parallel edits is "
        "a human call).\n"
        "\n"
        "Use it when one attempt is unreliable and the solution space is wide, "
        "so divergent tries help: 'implement X — try a few approaches and keep "
        "the best', 'refactor this several ways and pick the cleanest'. Costly "
        "(N full coding sub-agents + voters) — reserve it for genuinely "
        "high-value or ambiguous work.\n"
        "\n"
        "Args: {goal: string (what each candidate should accomplish), n?: int "
        "2-5 candidates (default 3), agent_id?: worker role (default "
        "worktree_writer), judge_n?: int 2-5 voters (default 3), repo_root?: "
        "path (default cwd — must be a git repo)}.\n"
        "\n"
        "Returns: {ok, goal, decided_by (judge|only_candidate|judge_abstained|"
        "none), candidate_count, viable_count, winner:{id, files, branch, "
        "diff}, candidates:[{id, ok, viable, files, error}], note}. Apply the "
        "winner's diff yourself — nothing is merged automatically."
    )
    registry.register(
        Skill(
            name="tournament",
            description=tournament_description,
            affinity=["delegation", "worktree", "tournament", "best_of", "judge", "candidates"],
            cost_profile="high",  # N coding sub-agents in worktrees + voters
            trusted_source="skill://public/tournament",
            handler=_run_tournament,
            tests=[
                SkillTestCase(
                    name="missing_goal_returns_error",
                    tier="golden",
                    args={"goal": ""},
                    expect=SkillExpect(
                        schema_keys=[
                            "ok",
                            "winner",
                            "candidates",
                            "decided_by",
                        ]
                    ),
                    custom_predicate=lambda r: (
                        isinstance(r, dict)
                        and r.get("ok") is False
                        and "required" in (r.get("error") or "")
                    ),
                ),
            ],
        ),
        replace=True,
    )

    # ── pipeline (item-chain, no inter-stage barrier) ─────────────
    pipeline_description = (
        "Process a list of items through ORDERED STAGES without waiting for "
        "all items to complete each stage before advancing — item A can be in "
        "stage 3 while item B is still in stage 1.\n"
        "\n"
        "Use it when:\n"
        "  - You have N independent items (files, URLs, tasks) that each need "
        "the SAME sequence of specialist passes (find → review → fix, or "
        "extract → classify → summarise).\n"
        "  - Stages are sequential PER ITEM (each stage needs the previous "
        "output for that item), but items themselves are independent.\n"
        "  - You want true pipeline throughput: wall-clock = max(slowest item "
        "chain), not sum of slowest-per-stage.\n"
        "\n"
        "Stage prompt templates use:\n"
        "  {item}   — the original item value\n"
        "  {prev}   — previous stage's output for this item (empty for stage 0)\n"
        "  {stage0_output}, {stage1_output}, ... — any earlier stage's output\n"
        "\n"
        "A failed stage halts that item's chain; remaining stages for that item "
        "are skipped. Other items continue.\n"
        "\n"
        "Args: {items: list[str|dict] (up to 16), "
        "stages: list[{prompt_template: str, agent_id?: str}] (up to 4 stages), "
        "default_agent_id?: str (used when stage omits agent_id, default researcher), "
        "timeout_s?: int (per-subagent, default 900)}.\n"
        "\n"
        "Returns: {ok, results:[{item, stages:[{stage, agent_id, output, ok}], "
        "final_output, ok}], success_count, failure_count, total, stages_run}.\n"
        "\n"
        "Budget: one pipeline costs ONE against the 5/turn delegation cap; "
        "item × stage spawn budget is managed internally."
    )
    registry.register(
        Skill(
            name="run_pipeline",
            description=pipeline_description,
            affinity=["delegation", "pipeline", "stages", "multi-step", "batch"],
            cost_profile="high",
            trusted_source="skill://public/run_pipeline",
            handler=_run_pipeline,
            tests=[
                SkillTestCase(
                    name="empty_items_returns_error",
                    tier="golden",
                    args={"items": [], "stages": [{"prompt_template": "review {item}"}]},
                    expect=SkillExpect(
                        schema_keys=[
                            "ok",
                            "results",
                            "success_count",
                            "failure_count",
                            "total",
                        ]
                    ),
                    custom_predicate=lambda r: (
                        isinstance(r, dict)
                        and r.get("ok") is False
                        and "required" in (r.get("error") or "")
                    ),
                ),
                SkillTestCase(
                    name="empty_stages_returns_error",
                    tier="golden",
                    args={"items": ["foo"], "stages": []},
                    expect=SkillExpect(
                        schema_keys=[
                            "ok",
                            "results",
                            "success_count",
                            "failure_count",
                            "total",
                        ]
                    ),
                    custom_predicate=lambda r: (
                        isinstance(r, dict)
                        and r.get("ok") is False
                        and "required" in (r.get("error") or "")
                    ),
                ),
            ],
        ),
        replace=True,
    )
    # ── declarative DAG (fan-in resolved server-side) ─────────────
    graph_description = (
        "Run a DAG of subagents where one lane CONSUMES another lane's output. "
        "Declare each node's `depends_on`; independent nodes run concurrently "
        "and the runtime derives the execution order.\n"
        "\n"
        "Use it when call_agent_parallel is not enough because a later step "
        "needs an earlier step's result:\n"
        "  - two investigations in parallel, then one node that reconciles both;\n"
        "  - find → verify → write-up, where each stage reads the last;\n"
        "  - a diamond: split into lanes, then fold back into one answer.\n"
        "\n"
        "A node prompt may reference an upstream output:\n"
        "  {node_id.output}        — that node's full reply text\n"
        "  {node_id.output.field}  — a field, when that node used output_schema\n"
        "The substitution happens SERVER-SIDE, so upstream text never passes "
        "through your context — that is the point of using a graph instead of "
        "running lanes yourself and pasting results into the next prompt.\n"
        "\n"
        "Prefer call_agent_parallel when lanes are independent (cheaper, one "
        "layer). Prefer run_pipeline when many ITEMS each need the same stage "
        "chain. Use this when the TOPOLOGY itself has a join.\n"
        "\n"
        f"Args: {{nodes: list[{{id: str, prompt: str, depends_on?: list[str], "
        f"agent_id?: str, output_schema?: object, isolate?: bool}}] "
        f"(up to {_MAX_GRAPH_NODES}), "
        "default_agent_id?: str (default researcher), "
        "timeout_s?: int (per-subagent, default 900), "
        "resume_token?: str (from a previous run's return)}.\n"
        "\n"
        "`isolate: true` gives that node its own git worktree — set it on nodes "
        "that EDIT FILES so concurrent writers in the same layer cannot collide. "
        "The node's diff comes back for review; nothing is auto-merged.\n"
        "\n"
        "Returns: {ok, nodes:{id:{id, agent_id, output, ok, error?, skipped?}}, "
        "order:[[id,...],...] (execution layers), terminal:[str] (leaf outputs), "
        "layers_run, success_count, failure_count, total}.\n"
        "\n"
        "Cycles and unknown depends_on are rejected before any spawn. A node "
        "whose upstream failed is skipped and reported, not run with an "
        "unresolved placeholder.\n"
        "\n"
        "Budget: one graph costs ONE against the 5/turn delegation cap; "
        "per-node spawns are charged against the internal spawn budget."
        "\n\n"
        "Resume: pass the returned `resume_token` back to re-run the same "
        "graph without respawning completed nodes - unchanged nodes replay "
        "their recorded result (0 spawns), a node whose prompt/role/schema "
        "changed respawns, and failed or empty nodes always re-run. The "
        "token is in-memory only (not valid across process restarts) and an "
        "unknown token is an error, not a cold run."
    )
    registry.register(
        Skill(
            name="call_agent_graph",
            description=graph_description,
            affinity=["delegation", "graph", "dag", "fan-in", "depends_on", "multi-agent"],
            cost_profile="high",
            trusted_source="skill://public/call_agent_graph",
            handler=_run_agent_graph,
            tests=[
                SkillTestCase(
                    name="missing_nodes_returns_error",
                    tier="golden",
                    args={},
                    expect=SkillExpect(
                        schema_keys=["ok", "nodes", "success_count", "failure_count"]
                    ),
                    custom_predicate=lambda r: (
                        isinstance(r, dict)
                        and r.get("ok") is False
                        and "required" in (r.get("error") or "")
                    ),
                ),
                SkillTestCase(
                    name="cycle_is_rejected_before_spawning",
                    tier="golden",
                    args={
                        "nodes": [
                            {"id": "a", "prompt": "x", "depends_on": ["b"]},
                            {"id": "b", "prompt": "y", "depends_on": ["a"]},
                        ]
                    },
                    expect=SkillExpect(
                        schema_keys=["ok", "nodes", "success_count", "failure_count"]
                    ),
                    custom_predicate=lambda r: (
                        isinstance(r, dict)
                        and r.get("ok") is False
                        and "cycle" in (r.get("error") or "")
                    ),
                ),
            ],
        ),
        replace=True,
    )
    # 8 delegation/orchestration skills registered above (call_agent,
    # call_agent_parallel, call_agent_vote, run_orchestration,
    # verdict_repair, tournament, run_pipeline, call_agent_graph).
    # External CLI auto-detection is intentionally not exposed here.
    return 8


__all__ = [
    # entrypoint
    "register_delegation_skills",
    # shared helpers
    "_DYNAMIC_SKILL_PACKS",
    "_VOTE_MAX",
    "_allowed_agent_ids",
    "_bump_and_check",
    "_build_ballot_prompt",
    "_coerce_name_list",
    "_coerce_timeout_s",
    "_coerce_vote_choices",
    "_dedupe_names",
    "_delegation_budget_exhausted_message",
    "_derive_error_type",
    "_display_name_for_agent_id",
    "_emit_orchestration_progress",
    "_empty_parallel_result",
    "_extract_verdict",
    "_format_role_catalog",
    "_is_transient_error",
    "_normalize_verdict",
    "_should_auto_retry",
    "_parallel_route_decision",
    "_resolve_custom_agent_id",
    "_resolve_session_and_turn",
    "_role_defaults_to_cheap",
    "_route_context_risk_level",
    "_skill_context_from_spec",
    "_tally_votes",
    "_vote_note",
    "_wrap_prompt_with_role_label",
    "orchestration_progress_scope",
    "workflow_settlement_scope",
    # single-agent delegation
    "_call_agent",
    # parallel fan-out
    "_build_parallel_envelope",
    "_call_agent_parallel",
    "_coerce_parallel_specs",
    # vote gate
    "_call_agent_vote",
    # orchestration loop
    "_ORCH_MAX_FINDINGS_PER_WORKER",
    "_ORCH_MAX_FINDINGS_TOTAL",
    "_ORCH_MAX_SPAWNS_CEILING",
    "_coerce_roles",
    "_dedupe_findings",
    "_finder_prompt",
    "_finder_spec",
    "_findings_from_success",
    "_norm_finding",
    "_resolve_max_spawns",
    "_run_orchestration",
    "_split_findings",
    "_synthesis_prompt",
    "_synthesis_spec",
    # judge panels
    "_run_tournament",
    "_run_verdict_repair",
    # pipeline
    "_run_pipeline",
    # declarative DAG (server-side fan-in)
    "_MAX_GRAPH_NODES",
    "_coerce_graph_nodes",
    "_plan_layers",
    "_resolve_node_prompt",
    "_run_agent_graph",
    # delegation-budget aliases kept visible for monkeypatch / lazy import
    "register_call_agent_parallel",
    "_check_absolute_cap",
    "_compute_fingerprint",
    "_record_delegation",
]


def register_call_agent_parallel(
    registry: SkillRegistry,
    max_spawns: int = 5,
    depth: int = 1,
) -> int:
    """Register `call_agent_parallel` for hierarchical sub-delegation.

    This is used when ephemeral sub-agents are allowed to spawn their own
    sub-agents (recursive orchestration). The tool is registered with depth
    and budget constraints to prevent infinite recursion.

    Parameters
    ----------
    registry :
        SkillRegistry to register into (typically a cloned registry for
        the sub-agent).
    max_spawns :
        Maximum number of parallel spawns this sub-agent can create.
    depth :
        Delegation depth (0=planner, 1=ephemeral, 2=ephemeral's child).
        Used for context propagation.

    Returns
    -------
    int
        Number of skills registered (always 1 for now).
    """
    from runtime.execution.suckers._delegation_skills_parallel import (
        _call_agent_parallel,
    )

    role_table = _format_role_catalog()
    description = (
        f"Spawn up to {max_spawns} sub-agents CONCURRENTLY for tasks "
        "that decompose cleanly into independent sub-tasks. Use when:\n"
        "  - The task is genuinely parallel (independent dimensions/modules)\n"
        "  - Each sub-task fits a specialist role\n"
        "  - You can integrate the results into a final deliverable\n"
        "\n"
        f"You are at delegation depth {depth}. Budget and spawns are "
        "inherited from your parent.\n"
        "\n"
        "Args: {nodes: list of {agent_id, prompt, ...}, context}\n"
        "\n"
        "Advertised specialist roles:\n"
        f"{role_table}"
    )

    registry.register(
        Skill(
            name="call_agent_parallel",
            description=description,
            affinity=["delegation", "parallel", "orchestration"],
            cost_profile="high",
            trusted_source="skill://public/call_agent_parallel",
            handler=_call_agent_parallel,
        ),
        replace=True,
    )

    return 1
