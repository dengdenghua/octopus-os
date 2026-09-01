from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from runtime.adapters.instrumentation import trace_stage
from runtime.platform.models.llm import Message, ModelRequest, ModelRouter
from runtime.platform.prompts import get_prompt
from runtime.safety.auth.scope import TenantScope
from runtime.safety.recovery.tenant_scope import read_learning_events

from .prompt_optimizer import PromptVariant

_MUTATOR_SYSTEM_PROMPT: str = ""
_MERGE_SYSTEM_PROMPT: str = ""


def _load_mutator_prompts() -> None:
    global _MUTATOR_SYSTEM_PROMPT, _MERGE_SYSTEM_PROMPT
    _MUTATOR_SYSTEM_PROMPT = get_prompt("prompt_mutator")
    _MERGE_SYSTEM_PROMPT = get_prompt("prompt_merge")


_load_mutator_prompts()


_SUFFIX_EXTRACT_RE = re.compile(r"<suffix>(.*?)</suffix>", re.DOTALL | re.IGNORECASE)


@dataclass(frozen=True)
class MutationProposal:
    variant: PromptVariant
    source_hash: str  # Implementation note.
    reason: str = ""  # Implementation note.


class PromptMutator:
    def __init__(
        self,
        *,
        router: ModelRouter,
        model: str = "claude-haiku-4-5-20251001",
        max_suffix_chars: int = 400,
        temperature: float = 0.7,
        name_prefix: str = "mutated",
    ) -> None:
        self.router = router
        self.model = model
        self.max_suffix_chars = max_suffix_chars
        self.temperature = temperature
        self.name_prefix = name_prefix
        self._seen_hashes: set[str] = set()

    def propose(
        self,
        base: PromptVariant,
        journal: Any,
        *,
        recipe_id: str | None = None,
        max_samples: int = 5,
        initial_weight: float = 0.1,
        guard_digest: dict[str, Any] | None = None,
        scope: TenantScope | None = None,
    ) -> MutationProposal | None:
        with trace_stage("camouflage.prompt_mutator.propose") as span:
            span.set_attribute("echo.mutator.base_variant", base.name)

            losing_summaries = _extract_losing_samples(
                journal,
                max_samples,
                recipe_id=recipe_id,
                scope=scope,
            )
            if not losing_summaries:
                span.set_attribute("echo.mutator.skip", "no_failures_to_learn_from")
                return None

            user_prompt = _build_user_prompt(
                base,
                losing_summaries,
                guard_digest=guard_digest,
            )

            try:
                response = self.router.call(
                    ModelRequest(
                        model=self.model,
                        messages=[
                            Message(role="system", content=_MUTATOR_SYSTEM_PROMPT),
                            Message(role="user", content=user_prompt),
                        ],
                        max_tokens=600,
                        temperature=self.temperature,
                    )
                )
            except Exception as e:  # noqa: BLE001
                span.set_attribute("echo.mutator.error", f"{type(e).__name__}: {e}")
                return None

            new_suffix = _extract_suffix(response.text)
            if not new_suffix:
                span.set_attribute("echo.mutator.parse", "no_suffix_tag")
                return None
            if len(new_suffix) > self.max_suffix_chars:
                new_suffix = new_suffix[: self.max_suffix_chars].rstrip() + "…"

            suffix_hash = _hash(new_suffix)
            if suffix_hash in self._seen_hashes:
                span.set_attribute("echo.mutator.skip", "duplicate_suffix")
                return None
            if suffix_hash == _hash(base.system_prompt_suffix):
                span.set_attribute("echo.mutator.skip", "no_change_from_base")
                return None
            self._seen_hashes.add(suffix_hash)

            new_name = f"{self.name_prefix}_{suffix_hash[:6]}"
            reason_text = _extract_reason(response.text)
            new_variant = PromptVariant(
                name=new_name,
                system_prompt_suffix=new_suffix,
                weight=initial_weight,
                description=f"Mutated from {base.name!r}",
                parents=(base.name,),
                generation=base.generation + 1,
                origin="mutation",
                reason=reason_text,
            )
            span.set_attribute("echo.mutator.new_variant", new_name)
            span.set_attribute("echo.mutator.suffix_chars", len(new_suffix))
            return MutationProposal(
                variant=new_variant,
                source_hash=_hash(base.system_prompt_suffix),
                reason=reason_text,
            )

    def propose_merge(
        self,
        parent_a: PromptVariant,
        parent_b: PromptVariant,
        *,
        initial_weight: float = 0.15,
    ) -> MutationProposal | None:
        with trace_stage("camouflage.prompt_mutator.merge") as span:
            span.set_attribute("echo.mutator.parent_a", parent_a.name)
            span.set_attribute("echo.mutator.parent_b", parent_b.name)

            if parent_a.name == parent_b.name:
                span.set_attribute("echo.mutator.skip", "same_parent")
                return None
            a_suffix = parent_a.system_prompt_suffix
            b_suffix = parent_b.system_prompt_suffix
            if a_suffix == b_suffix:
                span.set_attribute("echo.mutator.skip", "parents_identical")
                return None

            user_prompt = _build_merge_prompt(parent_a, parent_b)
            try:
                response = self.router.call(
                    ModelRequest(
                        model=self.model,
                        messages=[
                            Message(role="system", content=_MERGE_SYSTEM_PROMPT),
                            Message(role="user", content=user_prompt),
                        ],
                        max_tokens=800,
                        temperature=self.temperature,
                    )
                )
            except Exception as e:  # noqa: BLE001
                span.set_attribute("echo.mutator.error", f"{type(e).__name__}: {e}")
                return None

            merged = _extract_suffix(response.text)
            if not merged:
                span.set_attribute("echo.mutator.parse", "no_suffix_tag")
                return None
            if len(merged) > self.max_suffix_chars:
                merged = merged[: self.max_suffix_chars].rstrip() + "…"

            merged_hash = _hash(merged)
            if merged_hash in self._seen_hashes:
                span.set_attribute("echo.mutator.skip", "duplicate_suffix")
                return None
            if merged_hash == _hash(a_suffix) or merged_hash == _hash(b_suffix):
                span.set_attribute("echo.mutator.skip", "same_as_parent")
                return None
            self._seen_hashes.add(merged_hash)

            new_name = f"{self.name_prefix}_x{merged_hash[:6]}"
            reason_text = _extract_reason(response.text)
            new_variant = PromptVariant(
                name=new_name,
                system_prompt_suffix=merged,
                weight=initial_weight,
                description=f"Merge of {parent_a.name!r} × {parent_b.name!r}",
                parents=(parent_a.name, parent_b.name),
                generation=max(parent_a.generation, parent_b.generation) + 1,
                origin="crossover",
                reason=reason_text,
            )
            span.set_attribute("echo.mutator.new_variant", new_name)
            span.set_attribute("echo.mutator.merged_chars", len(merged))
            return MutationProposal(
                variant=new_variant,
                source_hash=_hash(f"{a_suffix}|||{b_suffix}"),
                reason=reason_text,
            )


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


def _extract_losing_samples(
    journal: Any,
    max_samples: int,
    *,
    recipe_id: str | None = None,
    max_arg_chars: int = 80,
    scope: TenantScope | None = None,
) -> list[str]:
    # Prompt mutation is a learning boundary, not an operator inspection
    # surface.  An omitted scope deliberately means legacy-only; only an
    # explicit cross-tenant TenantScope may mine the shared journal.  This is
    # especially important here because compacted tool arguments are sent to
    # the mutator model below.
    traj_events = read_learning_events(journal, "trajectory", scope=scope)

    # Bucket failures in two passes:
    #   narrow = only this recipe's failures (targeted · mutator
    #            learns from mistakes specific to the base variant's
    #            planner config)
    #   broad  = any failure (fallback when narrow is empty · lets
    #            the mutator still produce proposals when no
    #            recipe-matched data exists yet: first-round
    #            bootstrap, or trajectories pre-dating recipe
    #            tagging)
    narrow_buckets: dict[object, list[Any]] = defaultdict(list)
    broad_buckets: dict[object, list[Any]] = defaultdict(list)
    for idx, event in enumerate(traj_events):
        outcome = event.trajectory.outcome
        if outcome.success and not outcome.degraded:
            continue
        broad_buckets[event.trajectory.task_id].append((idx, event))
        if recipe_id is not None and event.trajectory.recipe_id == recipe_id:
            narrow_buckets[event.trajectory.task_id].append((idx, event))

    # Prefer narrow · fall back to broad when narrow is empty.
    # Caught by test_cli_optimize.py::TestMutatorIntegration ·
    # before the fallback, seeds tagged with a non-matching
    # recipe_id were silently filtered and the mutator went home
    # empty-handed (mutator_returned_none in evolver output).
    failed_buckets = narrow_buckets if recipe_id is not None and narrow_buckets else broad_buckets

    failed: list[Any] = []
    for bucket in failed_buckets.values():
        swarm_entries = [item for item in bucket if item[1].trajectory.strategy_id == "swarm"]
        if swarm_entries:
            failed.append(max(swarm_entries, key=lambda item: item[0])[1])
        else:
            failed.extend(event for _, event in bucket)
    if not failed:
        return []

    summaries: list[str] = []
    for ev in failed[-max_samples:]:
        t = ev.trajectory
        cost = t.outcome.cost
        lines = [
            f"## task {str(t.task_id)[:8]} · {t.step_count} steps · "
            f"${cost.usd:.4f} · {cost.tokens} tok"
        ]
        for s in t.steps:
            args_str = _compact_args(s.action.args, max_arg_chars)
            status = s.result.status
            err = s.result.error_type or ""
            marker = "✓" if s.success else "✗"
            lines.append(
                f"  {marker} step[{s.step_id}] {s.action.sucker_id}({args_str}) "
                f"→ {status}{(' · ' + err) if err else ''}"
            )
        summaries.append("\n".join(lines))
    return summaries


def _compact_args(args: dict, max_chars: int) -> str:
    if not args:
        return ""
    parts: list[str] = []
    budget = max_chars
    for k, v in list(args.items())[:4]:  # Implementation note.
        v_str = str(v)
        if len(v_str) > 30:
            v_str = v_str[:27] + "..."
        part = f"{k}={v_str}"
        if budget - len(part) < 0:
            parts.append("...")
            break
        parts.append(part)
        budget -= len(part) + 2
    return ", ".join(parts)


def _render_guard_digest_for_prompt(digest: dict[str, Any]) -> str:
    """Convert a GuardTelemetry digest into a brief LLM-readable
    summary that names the agent's most-frequent guard violations.

    Returns an empty string when the digest has no hits or no tuning
    candidates — caller must check for empty before splicing.
    """
    if not isinstance(digest, dict):
        return ""
    total = int(digest.get("total_hits") or 0)
    if total <= 0:
        return ""
    candidates = digest.get("tuning_candidates") or []
    by_category = digest.get("by_category") or {}
    dominant = digest.get("dominant_category") or ""

    lines = [
        f"Recent guard-firing patterns ({total} blocked Final Answers):",
    ]
    if dominant:
        lines.append(
            f"  Dominant failure category: {dominant} (most-blocked area of agent behaviour)."
        )
    if by_category:
        cat_summary = ", ".join(f"{cat}={count}" for cat, count in list(by_category.items())[:5])
        lines.append(f"  By category: {cat_summary}")
    if candidates:
        threshold = int(digest.get("tuning_threshold") or 0)
        lines.append(
            f"  Top repeat offenders (>= {threshold} firings each) — "
            "agent keeps making these mistakes:"
        )
        for cand in candidates[:5]:
            label = cand.get("label", "")
            count = cand.get("count", 0)
            lines.append(f"    - {label}: {count} firings")
        lines.append(
            "  Your suffix should explicitly steer the agent away from "
            "these failure modes — be concrete about the behaviour to adopt."
        )
    return "\n".join(lines)


def _build_user_prompt(
    base: PromptVariant,
    samples: list[str],
    *,
    guard_digest: dict[str, Any] | None = None,
) -> str:
    samples_text = "\n\n".join(samples)
    base_text = base.system_prompt_suffix or "(empty)"
    digest_section = ""
    if guard_digest:
        rendered = _render_guard_digest_for_prompt(guard_digest)
        if rendered:
            digest_section = f"\n\n{rendered}\n"
    return (
        f"Current suffix for variant {base.name!r}:\n"
        f"<current>\n{base_text}\n</current>\n\n"
        f"Recent failed trajectories ({len(samples)}):\n\n{samples_text}\n"
        f"{digest_section}\n"
        "Propose an improved suffix targeting these specific failures."
    )


def _build_merge_prompt(a: PromptVariant, b: PromptVariant) -> str:
    a_text = a.system_prompt_suffix or "(empty)"
    b_text = b.system_prompt_suffix or "(empty)"
    return (
        f"Suffix A ({a.name!r}):\n<suffix_a>\n{a_text}\n</suffix_a>\n\n"
        f"Suffix B ({b.name!r}):\n<suffix_b>\n{b_text}\n</suffix_b>\n\n"
        "Synthesize a new suffix that combines the strengths of both."
    )


def _extract_suffix(text: str) -> str:
    m = _SUFFIX_EXTRACT_RE.search(text)
    if not m:
        return ""
    return m.group(1).strip()


_REASON_RE = re.compile(r"<reason>(.*?)</reason>", re.DOTALL | re.IGNORECASE)


def _extract_reason(text: str) -> str:
    m = _REASON_RE.search(text)
    if not m:
        return ""
    return m.group(1).strip()[:200]


def _hash(text: str) -> str:
    return hashlib.blake2b(text.encode("utf-8"), digest_size=8).hexdigest()
