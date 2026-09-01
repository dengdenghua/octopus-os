"""
deep_evolution · LLM-judged self-evaluation (B2) + self-evolution (B3).

Two tiers built on top of the zero-cost ``turn_scoring`` heuristic:

    B1 (free, already in turn_scoring.py)
        Heuristic per-turn scoring + simple before/after delta on
        SOUL hash change. Catches "lesson clearly hurt latency" or
        "lesson clearly broke tool use" but can't say WHY.

    B2 · deep_reflect (cheap, ~2-3¢ per call)
        Cheap-LLM judge reads the last N scored turns + their
        trajectories + current SOUL, scores each turn 0-100,
        names dominant failure mode, suggests ONE concrete
        action: "add lesson X" / "revert last update" / "no
        action needed". Returns structured JSON. Single LLM
        round, no autonomous loop.

    B3 · deep_evolve (expensive, ~10-30¢ per run, manual trigger)
        MiniMax-style autonomous loop:
            1. analyze recent failure trajectories
            2. LLM proposes K candidate SOUL changes
            3. LLM-as-judge scores each candidate's predicted
               impact against held-out trajectories
            4. pick winner (and dry-run preview OR register candidate)
            5. iterate up to ``max_rounds``

        Gated by ``dry_run`` (default True): the agent / user sees
        the full proposed plan + reasoning WITHOUT mutation. Set
        ``dry_run=False`` to register a typed candidate. The candidate
        must then pass structured shadow review and staged canary before
        deployment; the model loop no longer edits SOUL.md directly.

Why these live here (not in suckers/)
-------------------------------------

These functions are LLM-coupled (they need a router) and not safe
to register at module import time without a router being wired up.
The wiring happens in ``app.py`` like the ephemeral runner — see
``set_evolve_router`` below. The skills layer (``memory_skills``)
exposes them as ``deep_reflect`` / ``deep_evolve`` skills with a
clean error if the router isn't wired.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from runtime.memory.learning.turn_scoring import is_safe_agent_id
from runtime.platform.budget.usage_pricing import price
from runtime.platform.llm_infra.llm_caller import LLMCaller
from runtime.platform.process.paths import app_paths
from runtime.platform.process.service_provider import get_provider
from runtime.safety.auth.scope import TenantScope, tenant_scoped_path
from runtime.safety.evolution.candidate_registry import (
    CandidateRegistry,
    CandidateRegistryError,
    CandidateStatus,
    EvolutionCandidate,
)
from runtime.safety.recovery.evolution_constraints import EvolutionConstraintValidator

_LOG = logging.getLogger("echo.deep_evolution")

_EVOLVE_CALLER = LLMCaller("evolve_router", "evolve_default_model")


def _agent_soul_path(agent_id: str) -> Path:
    if not is_safe_agent_id(agent_id):
        raise ValueError("unsafe agent id for deep evolution")
    # Agent presets are deployment assets. ``resources_root`` (via the
    # loader) also works in packaged/container deployments where cwd/project
    # data and bundled agents live in different roots.
    from runtime.execution.agents.loader import default_agents_root

    return default_agents_root() / agent_id / "agent-core" / "SOUL.md"


def set_evolve_router(router: Any, *, default_model: str | None = None) -> None:
    """Bootstrap hook · install the router used for deep_reflect /
    deep_evolve. Pass ``None`` to disable."""
    provider = get_provider()
    provider.register_instance("evolve_router", router)
    provider.register_instance("evolve_default_model", default_model)


def _llm_call_json(
    *,
    system: str,
    user: str,
    model: str | None = None,
    max_tokens: int = 800,
    temperature: float = 0.3,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Backwards-compatible JSON-envelope call used by adjacent modules.

    Delegates to ``_EVOLVE_CALLER.call_json``. Kept at module scope so
    callers (and tests) can monkey-patch this symbol to stub out the
    LLM round-trip without touching the caller instance.
    """
    return _EVOLVE_CALLER.call_json(
        system=system,
        user=user,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
    )


def _build_holdout_runner(*, model: str | None = None):
    """Return a ``(soul_text, prompt) -> reply`` callable backed by
    the evolve router. Used by the pre-flight holdout gate.

    Tests patch this symbol to inject a fake runner without touching
    any LLM. The returned callable uses ``LLMCaller.call`` (text mode,
    not JSON) and feeds the SOUL as a system prompt envelope.
    """

    def _runner(soul_text: str, prompt: str) -> str:
        system = "<your-soul>\n" + (soul_text or "") + "\n</your-soul>"
        text, _meta = _EVOLVE_CALLER.call(
            system=system,
            user=prompt,
            model=model,
            max_tokens=512,
            temperature=0.2,
        )
        return text or ""

    return _runner


def _record_deep_evolve_candidate(
    *,
    agent_id: str,
    candidate: dict[str, Any],
    judgment: dict[str, Any],
    holdout_passed: bool,
    source_failures: list[str],
    registry_path: Any = None,
    scope: TenantScope | None = None,
) -> EvolutionCandidate:
    if not is_safe_agent_id(agent_id):
        raise ValueError("unsafe agent id for deep evolution candidate")
    lesson = str(candidate.get("lesson") or "").strip()
    kind = str(candidate.get("kind") or "").strip()
    safety_results = EvolutionConstraintValidator().validate_prompt(lesson or kind)
    safety_passed = bool(safety_results) and all(item.passed for item in safety_results)
    if scope is not None and scope.allow_cross_tenant:
        raise ValueError("cross-tenant deep evolution cannot materialize a candidate")
    base_registry_path = registry_path or app_paths().evolution_candidates_path
    effective_registry_path = (
        tenant_scoped_path(base_registry_path, scope) if scope is not None else base_registry_path
    )
    registry = CandidateRegistry(effective_registry_path, tenant_scope=scope)
    patch = (
        {"op": "append_lesson", "value": lesson, "tag": candidate.get("tag")}
        if kind == "add_lesson"
        else {"op": "revert", "steps_back": int(candidate.get("revert_steps") or 1)}
    )
    evolution_candidate = registry.propose(
        gene_type="role",
        scope=f"agent.{agent_id}.soul",
        patch=patch,
        proposer="deep_evolve",
        lineage_id=f"deep_evolve:{agent_id}",
        role_id=agent_id,
        task_domain="self_evolution",
        risk_level=str(candidate.get("risk") or "medium"),
        source_failures=source_failures,
        metadata={
            "deep_evolve_candidate_id": candidate.get("id"),
            "predicted_impact": candidate.get("predicted_impact"),
            "judgment": judgment,
        },
    )
    gates = {
        "independent_judgment": judgment.get("verdict") == "apply",
        "safety_constraints": safety_passed,
        "sealed_holdout": bool(holdout_passed),
        # Runtime rollout deliberately supports append/replace overlays only.
        # A historical revert changes durable files and remains a manual,
        # explicitly approved recovery action.
        "runtime_patch_supported": kind == "add_lesson" and bool(lesson),
    }
    delta = judgment.get("predicted_avg_score_delta")
    metrics = {
        "predicted_avg_score_delta": float(delta) if isinstance(delta, (int, float)) else 0.0
    }
    evolution_candidate = registry.record_evidence(
        evolution_candidate.candidate_id,
        hard_gate_results=gates,
        metric_vector=metrics,
        metadata={
            "awaiting_gates": [name for name, passed in gates.items() if not passed],
            "next_stage": "structured_shadow" if all(gates.values()) else "validation",
        },
    )
    if evolution_candidate.status == CandidateStatus.PROPOSED and all(gates.values()):
        evolution_candidate = registry.transition(
            evolution_candidate.candidate_id,
            CandidateStatus.VALIDATED,
            metadata={"next_stage": "structured_shadow"},
        )
    return evolution_candidate


# ═══════════════════════════════════════════════════════════
# B2 · deep_reflect
# ═══════════════════════════════════════════════════════════

_REFLECT_SYSTEM = """You are a meta-evaluator for an AI agent. You read its
recent turns and judge quality. Be terse and concrete. Output ONLY a
JSON envelope, no prose outside the fenced block.

Schema:
```json
{
  "overall_score": 0-100,
  "trend": "improving" | "stable" | "regressing",
  "dominant_failure_mode": "<short tag>" | null,
  "lesson_quality": "helpful" | "neutral" | "harmful" | "no_recent_lesson",
  "action": "add_lesson" | "revert_last" | "no_action",
  "action_detail": "<one-line concrete instruction>",
  "rationale": "<2-3 sentences why>"
}
```
"""


def deep_reflect(
    *,
    agent_id: str,
    window: int = 20,
    model: str | None = None,
    scope: TenantScope | None = None,
) -> dict[str, Any]:
    """LLM-judged review of recent turns. Cheap (single LLM call).

    Reads:
        - last ``window`` per-turn scores from ``.scores.jsonl``
        - current SOUL.md content
        - the heuristic ``analyze_soul_impact`` verdict for context

    Returns a structured envelope (same shape as REFLECT schema)
    plus a ``meta`` dict with cost info + raw model output for
    audit. On router-not-wired returns ``{ok: False, error}``.
    """
    if not is_safe_agent_id(agent_id):
        return {"ok": False, "error": "unsafe_agent_id"}
    if get_provider().get("evolve_router") is None:
        return {
            "ok": False,
            "error": "deep_reflect router not configured · call set_evolve_router(router) at boot",
        }
    from runtime.memory.learning.turn_scoring import (
        analyze_soul_impact as _analyze,
    )
    from runtime.memory.learning.turn_scoring import (
        read_recent_scores,
    )

    scores = read_recent_scores(agent_id, limit=window, scope=scope)
    if not scores:
        return {
            "ok": True,
            "verdict": {
                "overall_score": None,
                "trend": "stable",
                "dominant_failure_mode": None,
                "lesson_quality": "no_recent_lesson",
                "action": "no_action",
                "action_detail": "no scored turns yet · run a few first then re-evaluate",
                "rationale": "insufficient data",
            },
            "scores_count": 0,
            "meta": {"input_tokens": 0, "output_tokens": 0, "model": None},
        }

    # Read SOUL.md (best-effort)
    soul_path = _agent_soul_path(agent_id)
    soul_content = soul_path.read_text(encoding="utf-8") if soul_path.exists() else "(no SOUL.md)"

    heuristic = _analyze(agent_id, window=window, scope=scope)

    # Compose the user message · concise table of recent scores +
    # current SOUL + heuristic snapshot.
    score_rows = "\n".join(
        f"  - {s.ts} · score={s.score} · reason={s.reason} · "
        f"rounds={s.rounds} · soul_hash={s.soul_hash}"
        for s in scores
    )
    user_msg = (
        f"AGENT: {agent_id}\n"
        f"WINDOW: last {len(scores)} scored turns\n\n"
        f"### Recent scores (newest first)\n{score_rows}\n\n"
        f"### Heuristic verdict (free pre-analysis)\n"
        f"{json.dumps(heuristic, ensure_ascii=False, indent=2)}\n\n"
        f"### Current SOUL.md ({len(soul_content)} chars)\n"
        f"```markdown\n{soul_content[:3000]}\n```\n\n"
        f"Now produce your JSON envelope."
    )

    parsed, meta = _EVOLVE_CALLER.call_json(
        system=_REFLECT_SYSTEM,
        user=user_msg,
        model=model,
        max_tokens=800,
        temperature=0.2,
    )
    if parsed is None:
        return {
            "ok": False,
            "error": meta.get("error") or meta.get("parse_error") or "LLM call failed",
            "meta": meta,
        }
    return {
        "ok": True,
        "verdict": parsed,
        "scores_count": len(scores),
        "heuristic": heuristic,
        "meta": meta,
    }


# ═══════════════════════════════════════════════════════════
# B3 · deep_evolve
# ═══════════════════════════════════════════════════════════

_PROPOSE_SYSTEM = """You are a self-improvement proposer for an AI agent.
You read recent turns + current SOUL and propose K candidate
changes that might improve future performance. Output ONLY a JSON
envelope, no prose outside the fenced block.

Schema:
```json
{
  "candidates": [
    {
      "id": "c1",
      "kind": "add_lesson" | "revert",
      "lesson": "<imperative one-liner>" | null,
      "tag": "<short category>" | null,
      "revert_steps": <int> | null,
      "predicted_impact": "<one sentence>",
      "risk": "low" | "medium" | "high"
    }
  ]
}
```
Only ``kind == 'add_lesson'`` uses ``lesson`` + ``tag``;
only ``kind == 'revert'`` uses ``revert_steps``. Generate exactly K
candidates. Prefer LOW risk · favor concrete tool/workflow lessons
over vague personality changes."""

_JUDGE_SYSTEM = """You are an impact judge. You read recent agent turns
and a candidate self-improvement, then predict the impact on those
SAME turns if the candidate had been active. Output ONLY a JSON envelope.

Schema:
```json
{
  "candidate_id": "<id>",
  "predicted_avg_score_delta": -1.0 .. +1.0,
  "predicted_failure_mode_change": "<short>" | "none",
  "would_help_count": <int>,
  "would_hurt_count": <int>,
  "confidence": "low" | "medium" | "high",
  "verdict": "apply" | "skip" | "needs_more_data"
}
```
Be conservative · "needs_more_data" is fine when uncertain."""


def deep_evolve(
    *,
    agent_id: str,
    window: int = 20,
    candidates_per_round: int = 3,
    max_rounds: int = 1,
    dry_run: bool = True,
    model: str | None = None,
    legacy_direct_apply: bool = False,
    candidate_registry_path: Any = None,
    scope: TenantScope | None = None,
) -> dict[str, Any]:
    """MiniMax-style autonomous self-improvement loop. EXPENSIVE.

    Each round:
        1. Read recent scores + SOUL + heuristic context.
        2. Propose K=``candidates_per_round`` candidate changes.
        3. Judge each candidate against the same recent turns.
        4. Pick the highest-confidence ``apply`` verdict.
        5. If ``dry_run=False``, materialize a typed candidate for
           structured shadow review and candidate-scoped canary.

    Returns full audit trail · including all proposals, judgments,
    and applied actions. On router-not-wired returns clean error.

    Token cost (rough): ``window * 50 + candidates_per_round *
    1500`` per round, so ``max_rounds=1`` ~ 5K tokens, ``max_rounds=5``
    ~25K tokens. Default 1 round to keep cost bounded.

    ``legacy_direct_apply`` remains in the signature only for source
    compatibility with older embedders. Direct SOUL mutation bypasses sealed
    validation, shadow review, canary receipts, and durable rollback lineage,
    so it is permanently fail-closed.
    """
    if legacy_direct_apply:
        return {
            "ok": False,
            "error": "legacy_direct_apply_disabled",
            "reason": "direct self-modification must use the governed candidate pipeline",
            "applied": [],
            "candidates": [],
            "dry_run": bool(dry_run),
        }
    if not is_safe_agent_id(agent_id):
        return {"ok": False, "error": "unsafe_agent_id"}
    if scope is not None and scope.allow_cross_tenant:
        return {
            "ok": False,
            "error": "cross_tenant_evolution_forbidden",
            "reason": "aggregate tenant evidence cannot authorize one candidate mutation",
        }
    if get_provider().get("evolve_router") is None:
        return {
            "ok": False,
            "error": "deep_evolve router not configured",
        }
    from runtime.memory.learning.turn_scoring import (
        analyze_soul_impact as _analyze,
    )
    from runtime.memory.learning.turn_scoring import (
        read_recent_scores,
    )

    soul_path = _agent_soul_path(agent_id)
    audit: list[dict[str, Any]] = []
    total_in = 0
    total_out = 0
    cost_model: str | None = None  # 实际所用模型,供按模型估算成本(非硬编码 Haiku)
    applied: list[dict[str, Any]] = []
    materialized_candidates: list[dict[str, Any]] = []

    for round_i in range(max(1, int(max_rounds))):
        scores = read_recent_scores(agent_id, limit=window, scope=scope)
        if not scores:
            audit.append(
                {
                    "round": round_i + 1,
                    "skipped": "no scored turns yet",
                }
            )
            break
        soul_content = (
            soul_path.read_text(encoding="utf-8") if soul_path.exists() else "(no SOUL.md)"
        )
        heuristic = _analyze(agent_id, window=window, scope=scope)

        score_rows = "\n".join(
            f"  - {s.ts} · score={s.score} · reason={s.reason} · "
            f"rounds={s.rounds} · soul_hash={s.soul_hash}"
            for s in scores
        )
        propose_user = (
            f"AGENT: {agent_id}\n"
            f"ROUND: {round_i + 1}/{max_rounds}\n"
            f"K = {candidates_per_round}\n\n"
            f"### Recent scores\n{score_rows}\n\n"
            f"### Heuristic verdict\n"
            f"{json.dumps(heuristic, ensure_ascii=False)}\n\n"
            f"### Current SOUL\n```markdown\n{soul_content[:2500]}\n```\n\n"
            f"Propose {candidates_per_round} candidate changes."
        )
        proposal, p_meta = _EVOLVE_CALLER.call_json(
            system=_PROPOSE_SYSTEM,
            user=propose_user,
            model=model,
            max_tokens=1500,
            temperature=0.5,
        )
        total_in += int(p_meta.get("input_tokens", 0) or 0)
        total_out += int(p_meta.get("output_tokens", 0) or 0)
        cost_model = cost_model or p_meta.get("model") or model
        if proposal is None:
            audit.append(
                {
                    "round": round_i + 1,
                    "stage": "propose",
                    "error": p_meta.get("error") or p_meta.get("parse_error"),
                }
            )
            break
        candidates = proposal.get("candidates", [])
        if not candidates:
            audit.append(
                {
                    "round": round_i + 1,
                    "stage": "propose",
                    "result": "no candidates produced",
                }
            )
            break

        # Judge each candidate.
        judgments: list[dict[str, Any]] = []
        for cand in candidates:
            judge_user = (
                f"AGENT: {agent_id}\n\n"
                f"### Recent turns\n{score_rows}\n\n"
                f"### Candidate\n"
                f"{json.dumps(cand, ensure_ascii=False, indent=2)}\n\n"
                f"Predict impact + verdict."
            )
            judgment, j_meta = _EVOLVE_CALLER.call_json(
                system=_JUDGE_SYSTEM,
                user=judge_user,
                model=model,
                max_tokens=400,
                temperature=0.1,
            )
            total_in += int(j_meta.get("input_tokens", 0) or 0)
            total_out += int(j_meta.get("output_tokens", 0) or 0)
            judgments.append(
                {
                    "candidate": cand,
                    "judgment": judgment or {"verdict": "judge_failed"},
                }
            )

        # Pick the best apply-verdict, prefer high confidence.
        winner = None
        for entry in judgments:
            v = (entry["judgment"] or {}).get("verdict")
            if v == "apply":
                conf = (entry["judgment"] or {}).get("confidence", "low")
                # Prefer higher-confidence applies; first-found at
                # 'high' wins, else any 'medium', else first 'apply'.
                if winner is None:
                    winner = entry
                else:
                    cur = (winner["judgment"] or {}).get("confidence", "low")
                    rank = {"high": 3, "medium": 2, "low": 1}
                    if rank.get(conf, 0) > rank.get(cur, 0):
                        winner = entry

        round_record: dict[str, Any] = {
            "round": round_i + 1,
            "candidates": candidates,
            "judgments": judgments,
            "winner": winner,
            "dry_run": dry_run,
        }

        if winner and not dry_run:
            cand = winner["candidate"]
            kind = cand.get("kind")
            holdout_passed = kind == "revert"
            # ── Pre-flight holdout gate ─────────────────────────
            # Only meaningful for ``add_lesson`` (which mutates the
            # SOUL by appending a line). ``revert`` walks the journal
            # back, so a holdout check on the proposed text would be
            # meaningless — let the post-apply canary watch it.
            if kind == "add_lesson":
                try:
                    from runtime.memory.learning.soul_holdout import (
                        GatePolicy,
                        evaluate_against_holdout,
                        load_holdout,
                    )
                    from runtime.memory.learning.soul_holdout import (
                        gate as _holdout_gate,
                    )

                    entries = load_holdout(agent_id, scope=scope)
                    if entries:
                        lesson_line = str(cand.get("lesson") or "").strip()
                        proposed_soul = (
                            (soul_content.rstrip() + "\n- " + lesson_line + "\n")
                            if lesson_line
                            else soul_content
                        )
                        runner = _build_holdout_runner(model=model)
                        old_result = evaluate_against_holdout(
                            agent_id,
                            soul_content,
                            runner,
                            entries,
                        )
                        new_result = evaluate_against_holdout(
                            agent_id,
                            proposed_soul,
                            runner,
                            entries,
                        )
                        allowed, reason = _holdout_gate(
                            new_result,
                            old_result,
                            GatePolicy(),
                        )
                        if not allowed:
                            round_record["applied"] = {
                                "ok": False,
                                "applied": False,
                                "error": "holdout_regression",
                                "reason": reason,
                                "old_pass_rate": old_result.pass_rate,
                                "new_pass_rate": new_result.pass_rate,
                                "regressions": [d for d in new_result.detail if d["score"] == 0],
                            }
                            audit.append(round_record)
                            continue
                        holdout_passed = True
                except (ImportError, OSError) as _exc:
                    _LOG.debug("holdout gate skipped: %s", _exc)
            try:
                judgment = dict(winner.get("judgment") or {})
                evolution_candidate = _record_deep_evolve_candidate(
                    agent_id=agent_id,
                    candidate=dict(cand),
                    judgment=judgment,
                    holdout_passed=holdout_passed,
                    source_failures=list(dict.fromkeys(str(score.reason) for score in scores)),
                    registry_path=candidate_registry_path,
                    scope=scope,
                )
                candidate_wire = evolution_candidate.to_wire()
                round_record["candidate"] = candidate_wire
                materialized_candidates.append(candidate_wire)
            except (CandidateRegistryError, OSError, TypeError, ValueError) as exc:
                round_record["applied"] = {
                    "ok": False,
                    "applied": False,
                    "error": f"candidate registration failed: {type(exc).__name__}: {exc}",
                }
                audit.append(round_record)
                continue

            round_record["applied"] = {
                "ok": True,
                "applied": False,
                "routed_to_candidate": True,
                "candidate_id": evolution_candidate.candidate_id,
                "candidate_status": evolution_candidate.status.value,
                "next_stage": evolution_candidate.metadata.get("next_stage"),
            }
            audit.append(round_record)
            continue

        audit.append(round_record)

    return {
        "ok": True,
        "rounds_run": len(audit),
        "audit": audit,
        "applied": applied,
        "candidates": materialized_candidates,
        "dry_run": dry_run,
        "cost": {
            "input_tokens": total_in,
            "output_tokens": total_out,
            # 按实际所用模型估算(单一定价真相源 usage_pricing.price);未知模型 → 0
            # 并由 model 字段标明,胜过旧的"无论什么模型都按 Haiku 价"硬编码。
            "model": cost_model,
            "approx_usd": round(price(cost_model, total_in, total_out), 4),
        },
    }


__all__ = [
    "set_evolve_router",
    "deep_reflect",
    "deep_evolve",
]
