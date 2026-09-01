"""``_run_orchestration`` · deterministic multi-round discovery loop.

Extracted from delegation_skills.py. This module holds the orchestration
machinery: the spawn-budget resolver, the finding split/dedupe helpers, the
finder/synthesis prompts, role-roster coercion, and the ``_run_orchestration``
handler itself. The names tests monkeypatch at the ``delegation_skills`` module
level (``_call_agent_parallel`` / ``_call_agent_vote`` / ``_check_absolute_cap``
/ ``_record_delegation``) are resolved lazily via ``delegation_skills`` so a
monkeypatch is still observed at call time — the same pattern used by
``_delegation_skills_agent`` / ``_write_skills_background``.
"""

from __future__ import annotations

import contextlib
import re
from typing import Any

from ._delegation_skills_common import (
    _DEFAULT_SUBAGENT_TIMEOUT_S,
    _coerce_vote_choices,
    _emit_orchestration_progress,
)
from .delegation_budget import (
    compute_fingerprint as _compute_fingerprint,
)
from .delegation_budget import (
    orchestration_budget_scope as _orchestration_budget_scope,
)

# ── build the spawn budget ────────────────────────────────
# Hard ceiling on total spawns per orchestration, for every path. Raised from the
# old conservative 48 so a deep verify+synth run (~n*rounds*voters) can use its
# full natural spawn count instead of being throttled mid-fan-out.
#
# NOTE ON THE TRUST BOUNDARY: this ceiling also bounds a model-declared explicit
# ``max_spawns``, so the model can now reach 256 without an operator budget. That
# is deliberate. What it does NOT change: spawns are still metered per-run by the
# budget scope, and the depth/recursion guards are enforced elsewhere — this is a
# width cap, not the fork-bomb defence.
_ORCH_MAX_SPAWNS_CEILING = 256
# Ceiling for the OPT-IN budget-driven path (a trusted token budget set by the
# bus/operator). Kept as its own name so the two paths can diverge again without
# touching call sites; equal to the default ceiling today.
_ORCH_MAX_SPAWNS_BUDGET_CEILING = 256
_ORCH_VERIFY_VOTERS = 3
_ORCH_MAX_FINDINGS_PER_WORKER = 50
_ORCH_MAX_FINDINGS_TOTAL = 200


def _resolve_max_spawns(
    max_spawns: int | str | None,
    *,
    n: int,
    rounds: int,
    verify: bool,
    synthesize: bool,
    token_budget: int | float | None = None,
) -> int:
    """Resolve an orchestration's total spawn budget.

    Precedence:
      1. explicit ``max_spawns`` (clamped to ``_ORCH_MAX_SPAWNS_CEILING``);
      2. opt-in ``token_budget`` (TRUSTED only) → scale to budget up to
         ``_ORCH_MAX_SPAWNS_BUDGET_CEILING``;
      3. default → ``n*rounds`` estimate, clamped to the same ceiling.

    Pure function so the budget policy is unit-testable without spawning agents.
    """
    if max_spawns is not None:
        try:
            explicit = int(max_spawns)
        except (TypeError, ValueError):
            explicit = n * rounds
        return max(n, min(_ORCH_MAX_SPAWNS_CEILING, explicit))
    if token_budget is not None:
        from runtime.execution.suckers.delegation_budget import (
            max_spawns_for_token_budget,
        )

        return max(
            n,
            max_spawns_for_token_budget(token_budget, ceiling=_ORCH_MAX_SPAWNS_BUDGET_CEILING),
        )
    verify_cost = n * rounds * _ORCH_VERIFY_VOTERS if verify else 0
    synth_cost = 1 if synthesize else 0
    planned = n * rounds + verify_cost + synth_cost
    return min(_ORCH_MAX_SPAWNS_CEILING, max(n, planned))


_NULL_FINDING_TOKENS = frozenset(
    {
        "none",
        "n/a",
        "na",
        "nothing",
        "no new findings",
        "no findings",
        "(none)",
    }
)
# A LEADING list marker only: a bullet, or an enumerator like "1." / "2)" /
# "(3)" / "4、" followed by a space. Deliberately NOT a bare digit run, so a
# finding whose content starts with a number ("3 retries observed") is kept
# intact rather than mangled.
_LIST_MARKER = re.compile(r"^\s*(?:[-*•·]|\(?\d{1,3}[.)）、])\s+")
# Markdown noise a real model emits around a list (observed live): horizontal
# rules (--- *** ===), ATX headings (## Foo), and lines that are entirely one
# bold/emphasis span used as a section label (**Critical issues**). A finding
# that merely CONTAINS bold ("**Always** validate") is kept — only whole-line
# emphasis is dropped.
_NOISE_LINE = re.compile(
    r"^(?:[-*=_~#]{3,}|#{1,6}\s.*|\*\*[^*]+\*\*|__[^_]+__|\*[^*]+\*)$",
)


def _split_findings(
    output: str,
    *,
    max_items: int = _ORCH_MAX_FINDINGS_PER_WORKER,
) -> list[str]:
    """One finding per line; strip a leading list marker, drop null markers and
    markdown noise (rules / headings / section labels), and cap how many lines a
    single worker can contribute (runaway guard)."""
    out: list[str] = []
    for line in (output or "").splitlines():
        s = _LIST_MARKER.sub("", line.strip()).strip()
        if not s or s.lower() in _NULL_FINDING_TOKENS or _NOISE_LINE.match(s):
            continue
        out.append(s)
        if len(out) >= max_items:
            break
    return out


def _norm_finding(s: str) -> str:
    return " ".join((s or "").lower().split())


def _dedupe_findings(items: list[str], seen_norms: set[str]) -> list[str]:
    """Return the items whose normalised form hasn't been seen; updates seen."""
    fresh: list[str] = []
    for it in items:
        key = _norm_finding(it)
        if key and key not in seen_norms:
            seen_norms.add(key)
            fresh.append(it)
    return fresh


# Finders return a JSON array of atomic findings; call_subagent validates it
# (and re-asks once on mismatch). Structured output replaces the brittle
# one-finding-per-line text parsing — the array survives multi-line findings,
# prose wrappers and markdown that _split_findings would mangle or drop.
_FINDER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "findings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["findings"],
}


def _findings_from_success(s: dict[str, Any]) -> list[str]:
    """Extract one worker's findings, preferring the schema-validated array and
    falling back to legacy one-per-line parsing when no parsed object is present
    (schema disabled, or it failed after the retry). The per-worker cap and
    null-marker filter apply on both paths."""
    parsed = s.get("parsed")
    if isinstance(parsed, dict) and isinstance(parsed.get("findings"), list):
        out: list[str] = []
        for raw in parsed["findings"]:
            txt = str(raw).strip()
            if not txt or txt.lower() in _NULL_FINDING_TOKENS:
                continue
            out.append(txt)
            if len(out) >= _ORCH_MAX_FINDINGS_PER_WORKER:
                break
        return out
    return _split_findings(str(s.get("output") or ""))


# Per-role lens directives. ``_coerce_roles`` promises that a role LIST buys
# "diverse lenses [that] surface more than N copies of one role", but every
# worker used to receive a byte-identical ``_finder_prompt`` — the only thing
# that differed was ``agent_id``, i.e. which role template the ephemeral runner
# loaded. Same goal + same instructions + same already-seen list makes N
# near-duplicate searches, which is exactly what the dedupe pass then throws
# away. Giving each role an explicit angle is what makes the fan-out pay.
_ROLE_LENS: dict[str, str] = {
    "researcher": "Search broadly for facts, prior art and documented behaviour.",
    "explorer": "Map structure and entry points; find where things live and how they connect.",
    "reviewer": "Judge quality and correctness of what exists; look for defects and omissions.",
    "debugger": "Hunt concrete failure modes: what breaks, under which inputs or states.",
    "architect": "Assess boundaries, coupling and design fit; find structural problems.",
    "security-review": "Look for unsafe handling: authz, injection, secrets, unchecked input.",
    "planner": "Identify missing steps, ordering constraints and unstated prerequisites.",
    "designer": "Examine the surface a user or caller touches; find friction and gaps.",
    "implementer": "Look for what is stubbed, partial, or wired but unreachable.",
    "synthesizer": "Look for cross-cutting themes that only appear when parts are compared.",
}


def _lens_for(role: str) -> str | None:
    """Look up a role's lens tolerantly.

    ``_coerce_roles`` preserves the caller's spelling verbatim, and a model
    writing ``agent_id=["Researcher", "Security_Review"]`` is routine. The role
    itself still resolves downstream (``_resolve_custom_agent_id`` casefolds),
    so an exact-match lookup here would drop the lens while keeping the role —
    a silent degradation back to N identical prompts, which is the very thing
    the lens exists to prevent. Normalise case and ``_``/``-`` the same way the
    resolver does.
    """
    key = str(role or "").strip().lower().replace("_", "-")
    return _ROLE_LENS.get(key)


def _finder_prompt(goal: str, seen: list[str], role: str = "") -> str:
    lens = _lens_for(role)
    base = f"You are one worker in a parallel discovery pass.\n\nGOAL:\n{goal}\n\n"
    if lens:
        base += (
            f"YOUR LENS ({role}): {lens}\n"
            "Other workers cover other lenses in parallel — do not try to cover "
            "everything yourself. Report what YOUR lens surfaces; a narrow, "
            "specific set beats a broad restatement of the goal.\n\n"
        )
    base += (
        "Report your findings as JSON: a `findings` array where each element is "
        "ONE atomic finding (a short string). No preamble or commentary — just "
        'the findings. If you have nothing, return {"findings": []}.'
    )
    if seen:
        shown = "\n".join(f"- {s}" for s in seen[:40])
        base += (
            "\n\nAlready found — do NOT repeat these; include only genuinely "
            "NEW ones (or an empty array if you have nothing new):\n"
            f"{shown}"
        )
    return base


def _finder_spec(role: str, goal: str, collected: list[str]) -> dict[str, Any]:
    """One discovery worker's spec.

    ``cheap`` is left unset on purpose: ``_call_agent_parallel`` then applies
    ``_role_defaults_to_cheap``, so a role roster of researcher/explorer routes
    to the cheap model while architect/implementer keep the primary one. The
    roster is the model-tier control, which is why passing a heterogeneous list
    is worth more than the role label alone.
    """
    return {
        "agent_id": role,
        "prompt": _finder_prompt(goal, collected, role),
        "output_schema": _FINDER_SCHEMA,
    }


def _synthesis_prompt(goal: str, findings: list[str]) -> str:
    """Prompt for the single synthesizer that closes an orchestration: turn the
    confirmed findings into one coherent answer. The closing step my own
    fan-out harness always has — without it ``run_orchestration`` hands back a
    bag of findings and makes the caller synthesize."""
    numbered = "\n".join(f"{i + 1}. {f}" for i, f in enumerate(findings))
    return (
        "You are the synthesizer at the end of a parallel discovery + "
        "verification pass. Combine the CONFIRMED FINDINGS below into ONE "
        "coherent, non-redundant answer to the GOAL. Merge overlaps, order by "
        "importance, and add nothing that is not supported by a finding.\n\n"
        f"GOAL:\n{goal}\n\n"
        f"CONFIRMED FINDINGS:\n{numbered}\n\n"
        "Return the synthesized answer as plain text — no preamble."
    )


def _synthesis_spec(goal: str, confirmed: list[str]) -> dict[str, Any]:
    """The closing synthesizer's spec.

    Pinned to the ``synthesizer`` role rather than ``roles[0]``. With the
    default roster that was ``researcher``, which ``_role_defaults_to_cheap``
    routes to the CHEAP model — the single most reasoning-heavy step of an
    orchestration ran on the weakest model available. ``synthesizer`` is in
    ``_NON_CHEAP_ROLES``, so this both loads a fit-for-purpose role template and
    keeps the primary model.

    No ``output_schema``: the synthesizer returns prose, and the finder schema
    would reject it.
    """
    return {
        "agent_id": "synthesizer",
        "prompt": _synthesis_prompt(goal, confirmed),
    }


def _coerce_roles(agent_id: Any) -> list[str]:
    """Normalise ``agent_id`` into a worker-role roster (deduped, order kept).
    A LIST gives heterogeneous lenses (e.g. researcher + explorer + critic)
    that are rotated across the per-round workers — diverse lenses surface more
    than N copies of one role (the multi-modal-sweep principle)."""
    raw = agent_id if isinstance(agent_id, (list, tuple)) else [agent_id]
    roles: list[str] = []
    for entry in raw:
        if entry is None:
            continue
        role = str(entry).strip()
        if role and role not in roles:
            roles.append(role)
    return roles or ["researcher"]


def _summarise_failures(failures: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Distil the envelope's ``failures`` lane into concise, de-duplicated
    summaries so the orchestration can honestly report *why* its sub-agents
    failed — instead of silently swallowing them (which is exactly what made a
    total sub-agent crash read as "nothing found / dry")."""
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for f in failures:
        if not isinstance(f, dict):
            continue
        role = str(f.get("role") or f.get("agent_id") or "?").strip() or "?"
        error = str(f.get("error") or f.get("error_type") or "unknown failure").strip()
        key = (role, error)
        if key in seen:
            continue
        seen.add(key)
        summary: dict[str, str] = {"role": role, "error": error}
        if f.get("error_type"):
            summary["error_type"] = str(f["error_type"])
        out.append(summary)
    return out


def _run_orchestration(
    goal: str = "",
    *,
    agent_id: str | list[str] = "researcher",
    n: int | str = 3,
    rounds: int | str = 2,
    patience: int | str = 1,
    verify: bool = False,
    synthesize: bool = False,
    choices: Any = None,
    max_spawns: int | str | None = None,
    timeout_s: int | str = _DEFAULT_SUBAGENT_TIMEOUT_S,
    context: dict[str, Any] | None = None,
    session: Any = None,
    **_kw: Any,
) -> dict[str, Any]:
    """Run a deterministic discovery loop: fan out ``n`` workers per round,
    split + dedupe their findings, loop up to ``rounds`` (stopping early after
    ``patience`` dry rounds), optionally vote-verify each finding, and
    optionally synthesize the confirmed findings into one coherent answer. The
    whole run is bounded by a spawn budget so it can't run away.
    """
    # Resolve the monkeypatch-visible names lazily via the delegation_skills
    # module so tests patching ``delegation_skills._call_agent_parallel`` /
    # ``_call_agent_vote`` / ``_check_absolute_cap`` / ``_record_delegation`` /
    # ``_resolve_session_and_turn`` observe them here.
    from runtime.execution.suckers.delegation_skills import (
        _call_agent_parallel,
        _call_agent_vote,
        _check_absolute_cap,
        _record_delegation,
        _resolve_session_and_turn,
    )

    goal = str(goal or _kw.get("prompt") or _kw.get("task") or _kw.get("query") or "").strip()
    if not goal:
        return {
            "ok": False,
            "error": "goal is required",
            "collected": [],
            "confirmed": [],
            "count": 0,
        }

    def _clamp(value: Any, lo: int, hi: int, default: int) -> int:
        try:
            return max(lo, min(hi, int(value)))
        except (TypeError, ValueError):
            return default

    n = _clamp(n, 1, 6, 3)
    rounds = _clamp(rounds, 1, 5, 2)
    patience = _clamp(patience, 0, 3, 1)
    # LLM callers pass booleans as strings ("true"/"false") just as often as
    # real bools; normalise so ``synthesize="false"`` doesn't read truthy.
    synthesize = str(synthesize).strip().lower() not in (
        "",
        "0",
        "false",
        "no",
        "off",
        "none",
    )
    # Opt-in budget-driven depth, from TRUSTED sources only — never the model's
    # call args (or a model could raise its own cap):
    #   1. per-turn: ``session.metadata["orchestration_token_budget"]`` (set by
    #      the bus / audit.ultracode path);
    #   2. deployment-wide: the ``ECHO_ORCH_TOKEN_BUDGET`` operator env.
    # Per-turn wins; absent both, the conservative n*rounds / 48 default holds.
    _orch_token_budget: Any = None
    try:
        _sess_meta = getattr(session, "metadata", None)
        if isinstance(_sess_meta, dict):
            _orch_token_budget = _sess_meta.get("orchestration_token_budget")
    except (AttributeError, TypeError):
        _orch_token_budget = None
    if _orch_token_budget is None:
        from runtime.execution.suckers.delegation_budget import (
            operator_orchestration_token_budget,
        )

        _orch_token_budget = operator_orchestration_token_budget()
    max_spawns = _resolve_max_spawns(
        max_spawns,
        n=n,
        rounds=rounds,
        verify=verify,
        synthesize=synthesize,
        token_budget=_orch_token_budget,
    )
    roles = _coerce_roles(agent_id)

    # Outer gate: an orchestration costs ONE against the per-turn cap so the
    # model can't spawn unboundedly by launching many orchestrations.
    parent_sess, turn_id = _resolve_session_and_turn()
    if session is None:
        session = parent_sess
    _, within = _check_absolute_cap(turn_id)
    if not within:
        return {
            "ok": False,
            "error": (
                "delegation budget exhausted for this turn — do the rest "
                "yourself, don't launch another orchestration."
            ),
            "collected": [],
            "confirmed": [],
            "count": 0,
        }
    fingerprint = _compute_fingerprint("run_orchestration", goal)
    _record_delegation(turn_id, fingerprint, succeeded=True)

    ballot = _coerce_vote_choices(choices) or ["keep", "drop"]
    seen_norms: set[str] = set()
    collected: list[str] = []
    per_round: list[int] = []
    dry = 0
    stopped = "rounds"
    # Every sub-agent failure is accumulated (not just successes) so the return
    # can honestly report a total crash instead of masking it as "dry".
    subagent_failures: list[dict[str, Any]] = []

    # Blackboard coordination: publish the evolving findings to the turn's
    # shared blackboard so sibling agents — and a later orchestration on the
    # same goal — build on them instead of re-discovering. Stigmergic
    # coordination the harness ENFORCES (the orchestrator reads/publishes),
    # not something the model must remember to do. No turn / no board (unit
    # tests) → a no-op; best-effort throughout, never breaks the run.
    from runtime.memory.runtime_state.blackboard import get_blackboard

    board = get_blackboard(turn_id)
    bb_key = f"orchestration.findings.{fingerprint}"
    inherited = 0
    if board is not None:
        try:
            prior = board.read(bb_key, None)
            if isinstance(prior, list) and prior:
                seed = [str(x) for x in prior if str(x).strip()]
                seed = seed[:_ORCH_MAX_FINDINGS_TOTAL]
                _dedupe_findings(seed, seen_norms)  # seed the dedup set
                collected.extend(seed)
                inherited = len(seed)
        except Exception:  # noqa: BLE001 — sharing must never break the run
            pass

    def _publish(findings: list[str]) -> None:
        if board is None:
            return
        # Sharing must never break the run.
        with contextlib.suppress(Exception):
            board.write(bb_key, list(findings), writer="run_orchestration")

    _emit_orchestration_progress(
        f"[orchestration] start · roles={roles} n={n} rounds={rounds} "
        f"max_spawns={max_spawns} verify={verify} synthesize={synthesize}"
    )
    with _orchestration_budget_scope(int(max_spawns)) as budget:
        for _ in range(int(rounds)):
            if not budget.has_room():
                stopped = "budget"
                break
            env = _call_agent_parallel(
                specs=[_finder_spec(roles[i % len(roles)], goal, collected) for i in range(n)],
                timeout_s=timeout_s,
                context=context,
                session=session,
            )
            items: list[str] = []
            for s in env.get("successes", []):
                items.extend(_findings_from_success(s))
            subagent_failures.extend(env.get("failures", []))
            fresh = _dedupe_findings(items, seen_norms)
            per_round.append(len(fresh))
            _emit_orchestration_progress(
                f"[orchestration] round {len(per_round)}/{rounds}: "
                f"+{len(fresh)} fresh (total {len(collected) + len(fresh)})"
            )
            if not fresh:
                dry += 1
                if dry > patience:
                    stopped = "dry"
                    break
                continue
            dry = 0
            collected.extend(fresh)
            _publish(collected)  # share this round's progress on the blackboard
            if len(collected) >= _ORCH_MAX_FINDINGS_TOTAL:
                del collected[_ORCH_MAX_FINDINGS_TOTAL:]
                stopped = "cap"
                break

        confirmed = list(collected)
        synthesis = ""
        verified = False
        unverified = 0
        if verify and collected:
            verified = True
            voters = _ORCH_VERIFY_VOTERS
            # Deterministic budget split: verify as many findings as the
            # remaining spawn budget affords (each vote spends ``voters``); the
            # rest are kept but flagged unverified. WHICH findings get verified
            # is deterministic (the first ``affordable``); only the verdicts
            # vary with the model — as verification inherently must.
            affordable = max(0, budget.remaining() // voters)
            to_verify = collected[:affordable]
            unverified = len(collected) - len(to_verify)

            def _verdict_for(finding: str) -> str | None:
                try:
                    vote = _call_agent_vote(
                        question=f"Is this finding correct and worth keeping?\n\n{finding}",
                        n=voters,
                        choices=ballot,
                        timeout_s=timeout_s,
                        context=context,
                        session=session,
                    )
                    return vote.get("verdict")
                except Exception:  # noqa: BLE001 — a failed vote keeps the finding
                    return None

            verdicts: list[str | None] = [None] * len(to_verify)
            if to_verify:
                import concurrent.futures as _cf
                import contextvars as _ctxvars

                pool_workers = max(1, min(4, len(to_verify)))
                with _cf.ThreadPoolExecutor(
                    max_workers=pool_workers,
                    thread_name_prefix="orch-verify",
                ) as pool:
                    # copy_context per task so each pool thread sees the ambient
                    # orchestration budget (a ContextVar doesn't auto-propagate
                    # into pool workers); the votes then charge the same envelope.
                    futures = {
                        pool.submit(_ctxvars.copy_context().run, _verdict_for, f): idx
                        for idx, f in enumerate(to_verify)
                    }
                    for fut in _cf.as_completed(futures):
                        verdicts[futures[fut]] = fut.result()

            # Drop only on an explicit majority "drop"; ties/no-verdict keep.
            kept = [f for f, v in zip(to_verify, verdicts, strict=True) if v != ballot[-1]]
            confirmed = kept + collected[len(to_verify) :]
            _emit_orchestration_progress(
                f"[orchestration] verify: kept {len(kept)}/{len(to_verify)} "
                f"voted · {unverified} unverified"
            )

        # Closing synthesis: one spawn folds the confirmed findings into a
        # single coherent answer (the stage that makes the harness return a
        # usable result, not a bag of findings). Budget-gated like everything
        # else; a failed/empty synthesis leaves ``confirmed`` untouched.
        if synthesize and confirmed and budget.has_room():
            synth_env = _call_agent_parallel(
                specs=[_synthesis_spec(goal, confirmed)],
                timeout_s=timeout_s,
                context=context,
                session=session,
            )
            synth_succ = synth_env.get("successes", [])
            subagent_failures.extend(synth_env.get("failures", []))
            if synth_succ:
                synthesis = str(synth_succ[0].get("output") or "").strip()
        if synthesize and synthesis:
            _emit_orchestration_progress(f"[orchestration] synthesis: {len(synthesis)} chars")
        # Publish the verified set so the shared pool reflects confirmed (not
        # just collected) findings for the rest of the turn.
        _publish(confirmed)
        budget_used = budget.used
        _emit_orchestration_progress(
            f"[orchestration] done · {len(confirmed)} confirmed · spawns {budget_used}/{max_spawns}"
        )

    failure_count = len(subagent_failures)
    failure_summaries = _summarise_failures(subagent_failures)
    note = ""
    if failure_count:
        reasons = "; ".join(f"{s['role']}: {s['error']}" for s in failure_summaries)
        note = (
            f"SUBAGENT FAILURES: {failure_count} delegated sub-agent run(s) failed "
            f"({reasons}). Do not present these as a completed multi-agent pass — "
            "the delegated finders failed (round cap / connection / model cannot "
            "converge), not that nothing was found."
        )

    return {
        "ok": True,
        "goal": goal[:240],
        "collected": collected,
        "confirmed": confirmed,
        "count": len(confirmed),
        "rounds_run": len(per_round),
        "fresh_per_round": per_round,
        "verified": verified,
        "unverified": unverified,
        "synthesis": synthesis,
        "shared": board is not None,
        "inherited": inherited,
        "stopped_reason": stopped,
        "budget_used": budget_used,
        "max_spawns": int(max_spawns),
        "failures": failure_summaries,
        "failure_count": failure_count,
        "note": note,
    }
