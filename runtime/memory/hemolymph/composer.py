# ruff: noqa: E402 — module-level imports below are intentionally late

from __future__ import annotations

import contextlib
import hashlib
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from typing import Any

from runtime.adapters.instrumentation import trace_stage
from runtime.core.hearts.gill_pump import GillCache
from runtime.execution.suckers import SkillRegistry
from runtime.memory.journal import Journal, TrajectoryEvent
from runtime.platform.models import (
    DEFAULT_QUOTAS,
    ArmId,
    ContextPacket,
    ContextSegment,
    ParsedIntent,
    QuotaAllocation,
    TaskGraph,
)
from runtime.safety.auth.scope import TenantScope
from runtime.safety.recovery.tenant_scope import read_learning_events

# ── Compose telemetry ring buffer ─────────────────────────
# Each ``compose()`` call records a small snapshot here so the
# observability panel can render a live meter of how each bucket
# (system / suckers / memory / history) was filled on the last N
# composes. Deque is bounded · oldest dropped when full · no
# persistence. Locked for thread safety (composes can happen from
# the SSE pump thread + planner thread concurrently).
_RECENT_COMPOSES_MAX: int = 50
_RECENT_COMPOSES: deque[dict[str, Any]] = deque(maxlen=_RECENT_COMPOSES_MAX)
_RECENT_COMPOSES_LOCK = threading.Lock()


def _record_compose_snapshot(
    *,
    budget_tokens: int,
    quotas: QuotaAllocation,
    segments: list[ContextSegment],
    recipe_id: str | None,
    task_type: str | None,
) -> None:
    """Stash a compact view of one ``compose()`` call for the UI."""
    by_bucket: dict[str, int] = {}
    for s in segments:
        by_bucket[s.bucket] = by_bucket.get(s.bucket, 0) + s.tokens_estimated
    total_used = sum(by_bucket.values())
    alloc = quotas.as_tokens(budget_tokens)
    snapshot = {
        "ts": time.time(),
        "budget_tokens": budget_tokens,
        "tokens_used": total_used,
        "utilization": (total_used / budget_tokens if budget_tokens > 0 else 0.0),
        "segment_count": len(segments),
        "by_bucket": {
            bucket: {
                "used": by_bucket.get(bucket, 0),
                "alloc": alloc.get(bucket, 0),
            }
            for bucket in ("system", "suckers", "memory", "history")
        },
        "recipe_id": recipe_id,
        "task_type": task_type,
    }
    with _RECENT_COMPOSES_LOCK:
        _RECENT_COMPOSES.append(snapshot)


def get_recent_compose_snapshots(limit: int = 50) -> list[dict[str, Any]]:
    """Return up to ``limit`` most-recent compose snapshots, newest last.

    Observability panel calls this on a heartbeat interval to render
    the per-bucket utilization bars + a sparkline of recent totals.
    """
    with _RECENT_COMPOSES_LOCK:
        if limit >= len(_RECENT_COMPOSES):
            return list(_RECENT_COMPOSES)
        # deque doesn't slice · tail N via islice
        from itertools import islice

        start = len(_RECENT_COMPOSES) - limit
        return list(islice(_RECENT_COMPOSES, start, None))


# Skills that exist for backward-compatible programmatic paths but should not
# be advertised to the planner as ordinary one-step actions.
_HIDDEN_BY_DEFAULT_SKILLS: frozenset[str] = frozenset(
    {
        "call_agent",
    }
)

# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 3)


# ═══════════════════════════════════════════════════════════
# Skill relevance scoring · surface task-relevant skills first so a
# focused task (e.g. "做个网页") reliably gets `create-website` instead of
# losing it to a registration-ordered, token-truncated flat dump.
# ═══════════════════════════════════════════════════════════

import re

_SKILL_WORD_RE = re.compile(r"[a-z0-9]{3,}")
_CJK_RUN_RE = re.compile(r"[一-鿿]{2,}")


def _cjk_bigrams(text: str) -> set[str]:
    """Adjacent-CJK-char bigrams within each CJK run — a cheap cross-lingual
    overlap signal (Chinese tasks don't tokenize on whitespace)."""
    out: set[str] = set()
    for run in _CJK_RUN_RE.findall(text):
        for i in range(len(run) - 1):
            out.add(run[i : i + 2])
    return out


def score_skill_relevance(query: str, name: str, affinity: list[str], description: str) -> int:
    """Lexical relevance of a skill to the task. Zero infra: English word
    overlap + name/affinity substring hits + CJK bigram overlap. Higher = more
    relevant. Deterministic; ties are broken by the caller (stable order)."""
    q = (query or "").lower()
    if not q.strip():
        return 0
    doc = f"{name} {' '.join(affinity)} {description or ''}".lower()
    score = 0
    # English word overlap (task ∩ skill doc).
    q_words = set(_SKILL_WORD_RE.findall(q))
    score += 2 * len(q_words & set(_SKILL_WORD_RE.findall(doc)))
    # Name / affinity term appearing directly in the task — strong signal,
    # catches CJK affinity keywords too.
    for term in (name, *affinity):
        t = term.lower().strip()
        if len(t) >= 2 and t in q:
            score += 4
    # CJK bigram overlap (Chinese task vs Chinese description/keywords).
    q_bi = _cjk_bigrams(q)
    if q_bi:
        score += len(q_bi & _cjk_bigrams(doc))
    return score


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


from runtime.platform.prompts.budget import DEFAULT_BUDGET

_COMPRESS_ORDER: tuple[str, ...] = DEFAULT_BUDGET.compress_order


def _compress_to_budget(
    segments: list[ContextSegment],
    total_budget: int,
) -> list[ContextSegment]:
    total = sum(s.tokens_estimated for s in segments)
    if total <= total_budget:
        return segments

    by_bucket: dict[str, list[ContextSegment]] = {}
    for s in segments:
        by_bucket.setdefault(s.bucket, []).append(s)

    overflow = total - total_budget
    for bucket_name in _COMPRESS_ORDER:
        if overflow <= 0:
            break
        if bucket_name not in by_bucket:
            continue
        bucket_segs = by_bucket[bucket_name]
        while bucket_segs and overflow > 0:
            popped = bucket_segs.pop()
            overflow -= popped.tokens_estimated

    result: list[ContextSegment] = []
    for bucket_name in ("system", "suckers", "memory", "history"):
        result.extend(by_bucket.get(bucket_name, []))
    return result


# ═══════════════════════════════════════════════════════════
# ContextEngine — pluggable compression strategy
# ═══════════════════════════════════════════════════════════


class ContextEngine(ABC):
    """Abstract base for pluggable context-compression strategies.

    The context-engine pattern keeps compression strategies
    interchangeable so the planner can swap them without touching
    call sites: each engine satisfies the same interface, and the
    composer picks one at startup based on the deployment profile.

    Subclasses implement ``compress`` to reduce a list of
    ``ContextSegment`` objects to fit within ``budget_tokens``.
    The default engine (``TruncationContextEngine``) replicates the
    existing bucket-drop behaviour; richer engines can summarise,
    embed-and-retrieve, or apply LLM-based compression.

    Usage::

        class MyEngine(ContextEngine):
            def compress(self, segments, budget_tokens):
                # custom logic
                return segments[:10]

        composer = ContextComposer(registry, engine=MyEngine())
    """

    @abstractmethod
    def compress(
        self,
        segments: list[ContextSegment],
        budget_tokens: int,
    ) -> list[ContextSegment]:
        """Reduce ``segments`` so their total token estimate fits within
        ``budget_tokens``.

        Parameters
        ----------
        segments:
            Ordered list of segments as produced by the composer's
            bucket-fill phase. Segments are ordered
            system → suckers → memory → history.
        budget_tokens:
            Hard ceiling. The returned list's total
            ``tokens_estimated`` SHOULD be ≤ this value.

        Returns
        -------
        list[ContextSegment]
            A (possibly shorter / truncated) list of segments.
        """


class TruncationContextEngine(ContextEngine):
    """Default engine — drops whole segments from the lowest-priority
    buckets first until the total fits within the budget.

    This replicates the original ``_compress_to_budget`` behaviour so
    existing deployments are unaffected when no custom engine is
    supplied.
    """

    def compress(
        self,
        segments: list[ContextSegment],
        budget_tokens: int,
    ) -> list[ContextSegment]:
        return _compress_to_budget(segments, budget_tokens)


# ═══════════════════════════════════════════════════════════
# ContextComposer
# ═══════════════════════════════════════════════════════════


class ContextComposer:
    def __init__(
        self,
        registry: SkillRegistry,
        journal: Journal | None = None,
        quotas: QuotaAllocation = DEFAULT_QUOTAS,
        engine: ContextEngine | None = None,
        gill_cache: GillCache | None = None,
        gill_max_age_s: float = 2.0,
    ) -> None:
        self.registry = registry
        self.journal = journal
        self.quotas = quotas
        # Pluggable compression strategy. Falls back to the default
        # TruncationContextEngine when none is supplied so existing
        # callers are unaffected.
        self.engine: ContextEngine = engine or TruncationContextEngine()
        self.gill_cache = gill_cache
        self.gill_max_age_s = max(0.1, float(gill_max_age_s))

    def compose(
        self,
        task_info: TaskGraph | ParsedIntent,
        *,
        system_prompt: str = "",
        budget_tokens: int = 20_000,
        relevant_skills: list[str] | None = None,
        arm_id: ArmId | None = None,
        history_cutoff_n: int = 5,
        recipe_id: str | None = None,
        task_type: str | None = None,
        scope: TenantScope | None = None,
    ) -> ContextPacket:
        with trace_stage(
            "hemolymph.compose",
            arm_id=arm_id or "",
        ) as span:
            span.set_attribute("echo.compose.budget_tokens", budget_tokens)

            segments: list[ContextSegment] = []
            alloc = self.quotas.as_tokens(budget_tokens)

            # ─── system bucket ────────────────────
            if system_prompt:
                segments.append(
                    ContextSegment(
                        bucket="system",
                        content=system_prompt,
                        tokens_estimated=estimate_tokens(system_prompt),
                        source_refs=["system_prompt"],
                    )
                )

            task_blurb = self._render_task(task_info)
            if task_blurb:
                segments.append(
                    ContextSegment(
                        bucket="system",
                        content=task_blurb,
                        tokens_estimated=estimate_tokens(task_blurb),
                        source_refs=["task_info"],
                    )
                )

            # ─── suckers bucket · progressive disclosure ─
            skill_blurb = self._render_skills(
                relevant_skills,
                budget_for_bucket=alloc["suckers"],
                task_query=self._skill_query(task_info),
            )
            if skill_blurb:
                segments.append(
                    ContextSegment(
                        bucket="suckers",
                        content=skill_blurb,
                        tokens_estimated=estimate_tokens(skill_blurb),
                        source_refs=["skill_registry"],
                    )
                )

            # ─── memory bucket ───────────────────
            if self.journal is not None:
                memory_key = self.memory_cache_key(
                    n=history_cutoff_n,
                    arm_id=arm_id,
                    budget_for_bucket=alloc["memory"],
                    scope=scope,
                )
                cached_memory = (
                    self.gill_cache.get_memory(
                        memory_key,
                        max_age_s=self.gill_max_age_s,
                    )
                    if self.gill_cache is not None
                    else []
                )
                if cached_memory:
                    segments.extend(cached_memory)
                    span.set_attribute("echo.compose.gill_memory_hit", True)
                else:
                    memory_segments = self.prefetch_memory_segments(
                        n=history_cutoff_n,
                        arm_id=arm_id,
                        budget_for_bucket=alloc["memory"],
                        scope=scope,
                    )
                    segments.extend(memory_segments)
                    if self.gill_cache is not None:
                        self.gill_cache.set_memory(memory_segments, memory_key)
                    span.set_attribute("echo.compose.gill_memory_hit", False)

            final_segments = self.engine.compress(segments, budget_tokens)

            span.set_attribute("echo.compose.segment_count", len(final_segments))
            span.set_attribute(
                "echo.compose.tokens_used",
                sum(s.tokens_estimated for s in final_segments),
            )

            # Feed the observability panel's ring buffer. Best-effort ·
            # a bad entry here must not break a compose. Try/except to
            # keep the invariant "compose never raises because of UI
            # telemetry."
            with contextlib.suppress(Exception):
                _record_compose_snapshot(
                    budget_tokens=budget_tokens,
                    quotas=self.quotas,
                    segments=final_segments,
                    recipe_id=recipe_id,
                    task_type=task_type,
                )

            return ContextPacket(
                total_budget_tokens=budget_tokens,
                quotas=self.quotas,
                segments=final_segments,
                recipe_id=recipe_id,
                task_type=task_type,
            )

    @staticmethod
    def memory_cache_key(
        *,
        n: int,
        arm_id: ArmId | None,
        budget_for_bucket: int,
        scope: TenantScope | None = None,
    ) -> str:
        """Stable identity for a recent-trajectory retrieval window."""
        if scope is None:
            scope_key = "legacy"
        elif scope.allow_cross_tenant:
            scope_key = "cross-tenant"
        else:
            ownership = f"{scope.tenant_id}\x00{scope.actor_id}".encode()
            scope_key = hashlib.sha256(ownership).hexdigest()[:20]
        return f"recent-trajectories:{scope_key}:{arm_id or '*'}:{n}:{budget_for_bucket}"

    def prefetch_memory_segments(
        self,
        *,
        n: int = 5,
        arm_id: ArmId | None = None,
        budget_for_bucket: int,
        scope: TenantScope | None = None,
    ) -> list[ContextSegment]:
        """Render memory in the same shape consumed by ``compose``.

        A GillHeartPump can call this method ahead of the next turn and store
        the result under ``memory_cache_key``. The normal synchronous path
        uses it as a safe fallback on a miss or stale entry.
        """
        if self.journal is None:
            return []
        return [
            ContextSegment(
                bucket="memory",
                content=blurb,
                tokens_estimated=estimate_tokens(blurb),
                source_refs=refs,
            )
            for blurb, refs in self._render_recent_trajectories(
                n=n,
                arm_id=arm_id,
                budget_for_bucket=budget_for_bucket,
                scope=scope,
            )
        ]

    @staticmethod
    def _render_task(task_info: Any) -> str:
        if isinstance(task_info, ParsedIntent):
            return f"TASK intent={task_info.intent_type} goal={task_info.normalized_goal!r}"
        if isinstance(task_info, TaskGraph):
            steps = " → ".join(f"{n.node_id}:{n.skill_ref}" for n in task_info.nodes)
            return f"TASK task_type={task_info.task_type} plan=[{steps}]"
        return ""

    @staticmethod
    def _skill_query(task_info: Any) -> str:
        """The text used to rank skills by relevance — the user's goal for an
        intent, or the plan shape for a graph."""
        if isinstance(task_info, ParsedIntent):
            return task_info.normalized_goal or ""
        if isinstance(task_info, TaskGraph):
            refs = " ".join(n.skill_ref for n in task_info.nodes)
            return f"{task_info.task_type or ''} {refs}".strip()
        return ""

    def _skill_relevance(self, name: str, task_query: str) -> int:
        try:
            skill = self.registry.get(name)
        except (KeyError, LookupError):
            return 0
        return score_skill_relevance(
            task_query,
            name,
            list(getattr(skill, "affinity", []) or []),
            skill.description or "",
        )

    def _render_skills(
        self,
        relevant_skills: list[str] | None,
        budget_for_bucket: int,
        task_query: str = "",
    ) -> str:
        if relevant_skills is None or "*" in relevant_skills:
            names = [n for n in self.registry.all_names() if n not in _HIDDEN_BY_DEFAULT_SKILLS]
        else:
            names = [n for n in relevant_skills if self.registry.has(n)]

        if not names:
            return ""

        # Rank by task relevance so the skills that actually fit the task are
        # surfaced first — and therefore never the ones dropped when the bucket
        # budget truncates. Ties keep the original (registry) order via the
        # index tie-breaker. No query (e.g. some warm-up calls) → original order.
        if task_query.strip():
            ranked = sorted(
                enumerate(names),
                key=lambda it: (
                    -self._skill_relevance(it[1], task_query),
                    it[0],
                ),
            )
            names = [n for _, n in ranked]

        lines: list[str] = ["AVAILABLE SKILLS (name · one-liner):"]
        used = estimate_tokens(lines[0])

        for name in names:
            skill = self.registry.get(name)
            line = f"  - {name} · {skill.description or '(no description)'}"
            cost = estimate_tokens(line)
            if used + cost > budget_for_bucket:
                lines.append(f"  ... ({len(names) - (len(lines) - 1)} more truncated)")
                break
            lines.append(line)
            used += cost

        return "\n".join(lines)

    def _render_recent_trajectories(
        self,
        *,
        n: int,
        arm_id: ArmId | None,
        budget_for_bucket: int,
        scope: TenantScope | None = None,
    ) -> list[tuple[str, list[str]]]:
        assert self.journal is not None
        # Context recall is a learning/serving boundary.  No scope means old
        # ownership-free rows only; authenticated callers must pass the
        # server-resolved tenant+owner scope explicitly.
        events = read_learning_events(self.journal, "trajectory", scope=scope)
        grouped: dict[object, list[tuple[int, TrajectoryEvent]]] = {}
        for idx, event in enumerate(events):
            if not isinstance(event, TrajectoryEvent):
                continue
            grouped.setdefault(event.trajectory.task_id, []).append((idx, event))

        deduped: list[TrajectoryEvent] = []
        for bucket in grouped.values():
            # Pick the LAST-WRITTEN swarm aggregate · critical for resume /
            # retry paths that reuse ``task_id``. The earlier
            # ``next(...)``-first behavior could surface a stale failed
            # aggregate to the planner instead of the successful one
            # that followed it. We use append index rather than event.ts
            # because in-memory tests and fast retries can share the same
            # timestamp tick.
            swarm_events = [item for item in bucket if item[1].trajectory.strategy_id == "swarm"]
            if swarm_events:
                deduped.append(max(swarm_events, key=lambda item: item[0])[1])
            else:
                deduped.extend(event for _, event in bucket)

        recent = [
            e
            for e in reversed(deduped)
            if isinstance(e, TrajectoryEvent) and (arm_id is None or e.trajectory.arm_id == arm_id)
        ][:n]

        blurbs: list[tuple[str, list[str]]] = []
        used = 0
        for e in recent:
            t = e.trajectory
            outcome_label = (
                "yes"
                if t.outcome.success and not t.outcome.degraded
                else "degraded"
                if t.outcome.degraded
                else "no"
            )
            summary = (
                f"past trajectory: task={t.task_id} arm={t.arm_id} "
                f"steps={t.step_count} "
                f"ok={outcome_label}"
            )
            cost = estimate_tokens(summary)
            if used + cost > budget_for_bucket:
                break
            blurbs.append((summary, [f"trajectory:{t.trajectory_id}"]))
            used += cost
        return blurbs
