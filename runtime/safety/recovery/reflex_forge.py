"""ReflexForge · auto-generate reflex rules from successful turns.

Biomimetic alias: a specialised *Regeneration* path that grows the
SpinalCord reflex layer from observed (prompt, reply) pairs. Mirrors
``SkillForge`` but targets the CPU fast path instead of the skill
registry.

Data flow
---------
1. **Source**: ``FuzzyCacheTier`` stores (prompt, reply) pairs from
   successful LLM turns (wired in ``realtime_turn_outcome.py``).
2. **Propose**: cluster entries by bigram-Jaccard similarity; each
   cluster with ≥ ``min_hits`` members becomes a candidate regex
   rule whose ``reply`` is the cluster's most common reply.
3. **Shadow validate**: build a temporary ``RegexMatcher`` and verify
   it matches every member prompt without false-positiving on a set
   of negative samples.
4. **Promote**: append the rule to ``data/reflex_rules.yaml`` under a
   delimited auto-forged section, then trigger a hot-reload so the
   new rule is live without a restart.

Unlike ``SkillForge`` (which forges composite skills that still call
the model), ReflexForge produces pure-CPU regex rules — the cheapest
possible evolution path. The EvolutionRouter decides which path a
candidate takes: model-dependent → SkillForge, pure-text → ReflexForge.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from runtime.adapters.instrumentation import trace_stage

_LOG = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
# Data structures
# ═══════════════════════════════════════════════════════════


class ForgedReflexCandidate(BaseModel):
    """One proposed reflex rule derived from a prompt-reply cluster."""

    model_config = ConfigDict(frozen=True)

    candidate_id: str = Field(..., min_length=8)
    rule_id: str
    pattern: str
    reply: str
    source_prompts: list[str]
    source_reply_variants: list[str]
    sample_count: int
    reply_consistency: float  # fraction of cluster sharing the top reply
    status: str = "proposed"  # proposed | shadow_pass | shadow_fail | promoted | retired


class ReflexForgeResult(BaseModel):
    """Outcome of one ``ReflexForge.run()`` tick."""

    model_config = ConfigDict(frozen=True)

    candidates_total: int
    promoted: list[str]
    shadow_failed: list[str]
    retired: list[str]
    reports: dict[str, dict[str, Any]] = Field(default_factory=dict)


@dataclass
class ReflexForgeConfig:
    min_hits: int = 3
    similarity_threshold: float = 0.7
    reply_consistency_threshold: float = 0.6
    max_candidates_per_run: int = 10
    max_reply_length: int = 500
    rules_file: str = "data/reflex_rules.yaml"
    auto_reload: bool = True


# ═══════════════════════════════════════════════════════════
# Clustering helpers
# ═══════════════════════════════════════════════════════════


def _bigrams(text: str) -> set[str]:
    s = text.strip().lower()
    if len(s) < 2:
        return {s} if s else set()
    return {s[i : i + 2] for i in range(len(s) - 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    union = len(a | b)
    return (len(a & b) / union) if union else 0.0


def _slugify(prompt: str) -> str:
    """Derive a human-readable rule_id from a prompt sample."""
    raw = re.sub(r"[^\w\u4e00-\u9fff]+", "_", prompt.strip())
    rid = raw[:24].strip("_").lower()
    if not rid or rid.isdigit():
        rid = "auto_" + hashlib.sha1(prompt.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]
    return f"forged_{rid}"


def _pattern_for_cluster(members: list[str]) -> str:
    """Build a regex alternation pattern covering all members.

    For a single member this is just the escaped literal. For
    multiple members we use ``^(a|b|c)$`` alternation so one rule
    covers the whole cluster.
    """
    if len(members) <= 1:
        return f"^{re.escape(members[0].strip())}$"
    alts = "|".join(re.escape(m.strip()) for m in members)
    return f"^({alts})$"


# ═══════════════════════════════════════════════════════════
# ReflexForge
# ═══════════════════════════════════════════════════════════


class ReflexForge:
    """Forge reflex rules from FuzzyCacheTier (prompt, reply) pairs.

    Usage::

        forge = ReflexForge()
        result = forge.run()

    Or step-by-step::

        candidates = forge.propose()
        for c in candidates:
            passed, report = forge.shadow_validate(c)
            if passed:
                forge.promote(c)
    """

    def __init__(
        self,
        config: ReflexForgeConfig | None = None,
        *,
        fuzzy_cache: Any = None,
    ) -> None:
        self.config = config or ReflexForgeConfig()
        self._fuzzy_cache = fuzzy_cache

    def _get_fuzzy_cache(self) -> Any:
        if self._fuzzy_cache is not None:
            return self._fuzzy_cache
        from runtime.core.nerves.reflex.tiers import get_default_fuzzy_cache

        return get_default_fuzzy_cache()

    # ─── propose ────────────────────────────────────

    def propose(self) -> list[ForgedReflexCandidate]:
        with trace_stage("regeneration.reflex_forge.propose"):
            fc = self._get_fuzzy_cache()
            pairs = self._collect_pairs(fc)
            if not pairs:
                return []

            clusters = self._cluster_pairs(pairs)
            candidates: list[ForgedReflexCandidate] = []
            for cluster in clusters:
                if len(cluster) < self.config.min_hits:
                    continue
                cand = self._make_candidate(cluster)
                if cand is not None:
                    candidates.append(cand)

            candidates.sort(
                key=lambda c: -(c.sample_count * c.reply_consistency),
            )
            return candidates[: self.config.max_candidates_per_run]

    def _collect_pairs(self, fc: Any) -> list[dict[str, str]]:
        """Extract (prompt, reply) entries from the FuzzyCacheTier store."""
        pairs: list[dict[str, str]] = []
        try:
            with fc._lock:
                for entry in fc._store.values():
                    prompt = str(entry.get("prompt") or "").strip()
                    reply = str(entry.get("reply") or "").strip()
                    if prompt and reply and len(reply) <= self.config.max_reply_length:
                        pairs.append({"prompt": prompt, "reply": reply})
        except Exception as exc:  # noqa: BLE001
            _LOG.debug("reflex_forge: failed to read fuzzy cache: %s", exc)
        return pairs

    def _cluster_pairs(
        self,
        pairs: list[dict[str, str]],
    ) -> list[list[dict[str, str]]]:
        """Single-linkage clustering on bigram-Jaccard similarity of prompts."""
        clusters: list[dict[str, Any]] = []
        for pair in pairs:
            bg = _bigrams(pair["prompt"])
            attached = False
            for c in clusters:
                if _jaccard(c["_rep_bigrams"], bg) >= self.config.similarity_threshold:
                    c["members"].append(pair)
                    attached = True
                    break
            if not attached:
                clusters.append(
                    {
                        "_rep_bigrams": bg,
                        "members": [pair],
                    }
                )
        return [c["members"] for c in clusters]

    def _make_candidate(
        self,
        cluster: list[dict[str, str]],
    ) -> ForgedReflexCandidate | None:
        prompts = [p["prompt"] for p in cluster]
        replies = [p["reply"] for p in cluster]

        # Pick the most common reply as the rule's reply.
        reply_counts = Counter(replies)
        top_reply, top_count = reply_counts.most_common(1)[0]
        consistency = top_count / len(cluster)
        if consistency < self.config.reply_consistency_threshold:
            return None

        rep_prompt = cluster[0]["prompt"]
        rule_id = _slugify(rep_prompt)
        pattern = _pattern_for_cluster(prompts)
        candidate_id = hashlib.blake2b(
            pattern.encode("utf-8"),
            digest_size=8,
        ).hexdigest()

        return ForgedReflexCandidate(
            candidate_id=candidate_id,
            rule_id=rule_id,
            pattern=pattern,
            reply=top_reply,
            source_prompts=prompts,
            source_reply_variants=list(reply_counts.keys()),
            sample_count=len(cluster),
            reply_consistency=round(consistency, 3),
        )

    # ─── shadow validate ───────────────────────────

    def shadow_validate(
        self,
        candidate: ForgedReflexCandidate,
    ) -> tuple[bool, dict[str, Any]]:
        with trace_stage("regeneration.reflex_forge.shadow_validate"):
            from runtime.core.nerves.reflex.reflex_router import RegexMatcher
            from runtime.platform.models import ParsedIntent

            matcher = RegexMatcher(
                rule_id=candidate.rule_id,
                pattern=candidate.pattern,
                response={"reply": candidate.reply},
            )

            # Positive samples: every source prompt must match.
            positive_hits = 0
            for prompt in candidate.source_prompts:
                intent = ParsedIntent(
                    intent_type="chitchat",
                    raw=prompt,
                    normalized_goal=prompt,
                )
                match = matcher.try_match(intent)
                if match is not None:
                    positive_hits += 1

            positive_rate = positive_hits / max(1, len(candidate.source_prompts))

            # Negative samples: the pattern should NOT match dissimilar
            # prompts. We use a small built-in set of obviously-unrelated
            # prompts to catch over-broad alternations.
            negative_samples = [
                "hello",
                "what time is it",
                "help me debug this code",
                "写一个排序算法",
            ]
            false_positives = 0
            for neg in negative_samples:
                intent = ParsedIntent(
                    intent_type="chitchat",
                    raw=neg,
                    normalized_goal=neg,
                )
                match = matcher.try_match(intent)
                if match is not None:
                    false_positives += 1

            passed = positive_rate >= 1.0 and false_positives == 0
            report: dict[str, Any] = {
                "positive_hits": positive_hits,
                "positive_total": len(candidate.source_prompts),
                "positive_rate": round(positive_rate, 3),
                "false_positives": false_positives,
                "negative_total": len(negative_samples),
                "passed": passed,
            }
            return passed, report

    # ─── promote ────────────────────────────────────

    def promote(self, candidate: ForgedReflexCandidate) -> bool:
        """Append the rule to the YAML file and trigger a hot-reload.

        Returns True on success, False on failure.
        """
        with trace_stage("regeneration.reflex_forge.promote"):
            rules_path = Path(self.config.rules_file)
            try:
                self._append_rule_to_yaml(rules_path, candidate)
            except Exception as exc:  # noqa: BLE001
                _LOG.warning(
                    "reflex_forge: failed to append rule %s to %s: %s",
                    candidate.rule_id,
                    rules_path,
                    exc,
                )
                return False

            if self.config.auto_reload:
                self._trigger_reload()

            _LOG.info(
                "reflex_forge: promoted rule %s (pattern=%r, reply=%d chars)",
                candidate.rule_id,
                candidate.pattern[:60],
                len(candidate.reply),
            )
            return True

    def _append_rule_to_yaml(
        self,
        path: Path,
        candidate: ForgedReflexCandidate,
    ) -> None:
        """Append a forged rule to the YAML rules file.

        Creates the file with a minimal skeleton if it doesn't exist.
        Appends under a delimited ``auto-forged`` section so operators
        can distinguish forged rules from hand-written ones.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("rules: []\n", encoding="utf-8")

        text = path.read_text(encoding="utf-8")
        # Ensure the file has a ``rules:`` list. If it's empty or
        # missing the key, bootstrap it.
        if "rules:" not in text:
            text = "rules: []\n" + text

        # Build the YAML block for this rule.
        reply_escaped = candidate.reply.replace("'", "''")
        block = (
            f"  - id: {candidate.rule_id}\n"
            f"    type: regex\n"
            f"    pattern: '{candidate.pattern}'\n"
            f"    reply: '{reply_escaped}'\n"
            f"    priority: 20\n"
            f"    # auto-forged by ReflexForge · {candidate.sample_count} samples, "
            f"consistency={candidate.reply_consistency}\n"
        )

        # Append under a delimited section.
        marker = "# ─── auto-forged by ReflexForge ───"
        if marker not in text:
            text = text.rstrip() + f"\n{marker}\n"
        text = text.rstrip() + "\n" + block

        path.write_text(text, encoding="utf-8")

    def _trigger_reload(self) -> None:
        """Trigger a hot-reload of the reflex router.

        Best-effort · if the reload endpoint isn't available (e.g.
        running outside a server context), the rule is still in the
        YAML file and will be picked up on the next restart.
        """
        try:
            from runtime.cli import _build_reflex_router

            fresh = _build_reflex_router()
            from runtime.platform.process.service_provider import get_provider

            provider = get_provider()
            existing = provider.get("reflex_router")
            if existing is not None:
                existing.replace_reflexes(fresh._reflexes)
        except Exception as exc:  # noqa: BLE001
            _LOG.debug("reflex_forge: hot-reload skipped: %s", exc)

    # ─── run ───────────────────────────────────────

    def run(self) -> ReflexForgeResult:
        candidates = self.propose()
        promoted: list[str] = []
        shadow_failed: list[str] = []
        retired: list[str] = []
        reports: dict[str, dict[str, Any]] = {}

        for cand in candidates:
            # Skip if the rule already exists in the YAML.
            if self._rule_exists(cand.rule_id):
                retired.append(cand.rule_id)
                continue

            passed, report = self.shadow_validate(cand)
            reports[cand.rule_id] = report
            if not passed:
                shadow_failed.append(cand.rule_id)
                retired.append(cand.rule_id)
                continue

            if self.promote(cand):
                promoted.append(cand.rule_id)
            else:
                shadow_failed.append(cand.rule_id)
                retired.append(cand.rule_id)

        return ReflexForgeResult(
            candidates_total=len(candidates),
            promoted=promoted,
            shadow_failed=shadow_failed,
            retired=retired,
            reports=reports,
        )

    def _rule_exists(self, rule_id: str) -> bool:
        """Check if a rule_id already exists in the YAML file."""
        path = Path(self.config.rules_file)
        if not path.exists():
            return False
        try:
            text = path.read_text(encoding="utf-8")
            return f"id: {rule_id}" in text
        except Exception:  # noqa: BLE001
            return False


__all__ = [
    "ForgedReflexCandidate",
    "ReflexForge",
    "ReflexForgeConfig",
    "ReflexForgeResult",
]
