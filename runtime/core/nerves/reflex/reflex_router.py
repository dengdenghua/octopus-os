from __future__ import annotations

import hashlib
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from runtime.adapters.instrumentation import trace_stage
from runtime.platform.models import ParsedIntent

# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


class ReflexMatch(BaseModel):
    model_config = ConfigDict(frozen=True)

    rule_id: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    response: Any
    latency_ms: float = 0.0
    kind: Literal["regex", "deterministic", "cache", "slm"] = "regex"


class ReflexMiss(BaseModel):
    model_config = ConfigDict(frozen=True)

    tried_rules: list[str] = Field(default_factory=list)
    reason: str = ""


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


class Reflex(ABC):
    rule_id: str = ""
    # "slm" 是预留枚举,目前**没有** Reflex 子类发射它(仅 regex/deterministic/cache
    # 三种)。真正的 SLM 小模型走的是另一条旁路 —— tiers.py 的 SLMTier(ReplyTier 体系),
    # 不经 Reflex。保留此字面量以备将来的 Reflex 级 SLM 匹配器。
    kind: Literal["regex", "deterministic", "cache", "slm"] = "regex"
    priority: int = 0  # Implementation note.

    @abstractmethod
    def try_match(self, intent: ParsedIntent) -> ReflexMatch | None: ...


# ═══════════════════════════════════════════════════════════
# RegexMatcher
# ═══════════════════════════════════════════════════════════


class RegexMatcher(Reflex):
    kind: Literal["regex"] = "regex"

    def __init__(
        self,
        rule_id: str,
        pattern: str,
        response: Any = None,
        handler: Callable[[re.Match[str], ParsedIntent], Any] | None = None,
        priority: int = 0,
        case_insensitive: bool = True,
    ) -> None:
        self.rule_id = rule_id
        flags = re.IGNORECASE if case_insensitive else 0
        self._regex = re.compile(pattern, flags)
        self._response = response
        self._handler = handler
        self.priority = priority

    def try_match(self, intent: ParsedIntent) -> ReflexMatch | None:
        t0 = time.monotonic()
        m = self._regex.search(intent.normalized_goal)
        if m is None:
            return None
        if self._handler is not None:
            response = self._handler(m, intent)
        elif self._response is not None:
            response = self._response
        else:
            response = {"match": m.group(0), "groups": list(m.groups())}
        return ReflexMatch(
            rule_id=self.rule_id,
            response=response,
            latency_ms=(time.monotonic() - t0) * 1000,
            kind="regex",
        )


# ═══════════════════════════════════════════════════════════
# DeterministicMatcher
# ═══════════════════════════════════════════════════════════


class DeterministicMatcher(Reflex):
    kind: Literal["deterministic"] = "deterministic"

    def __init__(
        self,
        rule_id: str,
        intent_type: str,
        response: Any,
        keywords: list[str] | None = None,
        priority: int = 0,
    ) -> None:
        self.rule_id = rule_id
        self._intent_type = intent_type
        self._response = response
        self._keywords = [k.lower() for k in (keywords or [])]
        self.priority = priority

    def try_match(self, intent: ParsedIntent) -> ReflexMatch | None:
        t0 = time.monotonic()
        if intent.intent_type != self._intent_type:
            return None
        if self._keywords:
            goal = intent.normalized_goal.lower()
            if not any(k in goal for k in self._keywords):
                return None
        return ReflexMatch(
            rule_id=self.rule_id,
            response=self._response,
            latency_ms=(time.monotonic() - t0) * 1000,
            kind="deterministic",
        )


# ═══════════════════════════════════════════════════════════
# CacheMatcher
# ═══════════════════════════════════════════════════════════


@dataclass
class CacheHit:
    key: str
    value: Any
    ts: float = field(default_factory=time.time)


class CacheMatcher(Reflex):
    kind: Literal["cache"] = "cache"

    def __init__(
        self,
        rule_id: str = "cache",
        ttl_seconds: int = 3600,
        max_entries: int = 10_000,
        priority: int = 5,
    ) -> None:
        self.rule_id = rule_id
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.priority = priority
        self._store: dict[str, CacheHit] = {}

    def put(self, intent: ParsedIntent, response: Any) -> None:
        key = self.key_for(intent)
        if len(self._store) >= self.max_entries:
            first_key = next(iter(self._store))
            del self._store[first_key]
        self._store[key] = CacheHit(key=key, value=response)

    def try_match(self, intent: ParsedIntent) -> ReflexMatch | None:
        t0 = time.monotonic()
        key = self.key_for(intent)
        hit = self._store.get(key)
        if hit is None:
            return None
        if time.time() - hit.ts > self.ttl_seconds:
            del self._store[key]
            return None
        return ReflexMatch(
            rule_id=self.rule_id,
            response=hit.value,
            latency_ms=(time.monotonic() - t0) * 1000,
            kind="cache",
            confidence=0.95,
        )

    @staticmethod
    def key_for(intent: ParsedIntent) -> str:
        parts = [
            intent.intent_type,
            " ".join(intent.normalized_goal.lower().split()),
            str(intent.user_context.get("locale", "")),
        ]
        return hashlib.blake2b("|".join(parts).encode("utf-8"), digest_size=12).hexdigest()

    def size(self) -> int:
        return len(self._store)

    def clear(self) -> None:
        self._store.clear()


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


class ReflexRouter:
    FORCE_DELIBERATIVE_TYPES = {"plan", "refactor", "debug", "design"}

    def __init__(
        self,
        reflexes: Iterable[Reflex],
        default_threshold: float = 0.85,
    ) -> None:
        self._reflexes = sorted(reflexes, key=lambda r: -r.priority)
        self.default_threshold = default_threshold
        self.hit_count = 0
        self.try_count = 0
        self._hit_count_by_rule: dict[str, int] = {}
        self._try_count_by_rule: dict[str, int] = {}
        # When the router itself was constructed · used to compute
        # ``stale_for_hours`` so a rule that's never been hit since
        # router-init can still be flagged as not-yet-exercised.
        self._created_at: float = time.time()
        # rule_id → unix timestamp of its most recent hit. Populated
        # in try_match() when a rule fires. Drives the 24h-stale
        # detection in stats_by_rule and the panel's stale badge.
        self._last_hit_at_by_rule: dict[str, float] = {}

    def try_match(self, intent: ParsedIntent) -> ReflexMatch | ReflexMiss:
        self.try_count += 1
        tried: list[str] = []

        with trace_stage("spinal_cord.try_reflex") as span:
            if self._force_deliberative(intent):
                span.set_attribute("echo.reflex.force_deliberative", True)
                return ReflexMiss(tried_rules=[], reason="force_deliberative")

            # Resolve actor/now ONCE per try_match call so all gates
            # see consistent inputs (a rule that just barely makes the
            # window can't flip mid-loop). Both reads are cheap and
            # gating is opt-in · the per-rule getattr is the only cost
            # for non-gated rules.
            _gate_actor: str | None = None
            try:
                from runtime.platform.process.session import current_session as _cs

                _sess = _cs()
                _gate_actor = getattr(_sess, "actor", None) if _sess else None
            except Exception:  # noqa: BLE001
                pass
            import datetime as _dt

            _gate_now = _dt.datetime.now()

            for reflex in self._reflexes:
                # Gating · check per-rule predicates BEFORE we even
                # call try_match. This is what enables gray release /
                # time-of-day / rollout-percentage rules. Skipped
                # rules don't increment the per-rule try counter
                # (they wouldn't have matched anyway, so the metric
                # would be misleading).
                gating = getattr(reflex, "_gating_spec", None)
                if gating is not None and not gating.is_active(
                    actor=_gate_actor,
                    now=_gate_now,
                ):
                    continue
                self._try_count_by_rule[reflex.rule_id] = (
                    self._try_count_by_rule.get(reflex.rule_id, 0) + 1
                )
                tried.append(reflex.rule_id)
                match = reflex.try_match(intent)
                if match is None:
                    continue
                if match.confidence < self.default_threshold:
                    continue
                self.hit_count += 1
                self._hit_count_by_rule[match.rule_id] = (
                    self._hit_count_by_rule.get(match.rule_id, 0) + 1
                )
                # Coverage tracking · last_hit_at lets the admin UI
                # show "this rule hasn't fired in 3 days" so dead
                # regexes get pruned. Stored as unix seconds.
                self._last_hit_at_by_rule[match.rule_id] = time.time()
                span.set_attribute("echo.reflex.rule_id", match.rule_id)
                span.set_attribute("echo.reflex.kind", match.kind)
                span.set_attribute("echo.reflex.confidence", match.confidence)
                return match

            span.set_attribute("echo.reflex.hit", False)
            return ReflexMiss(
                tried_rules=tried, reason="no_matcher_returned_high_enough_confidence"
            )

    @staticmethod
    def _force_deliberative(intent: ParsedIntent) -> bool:
        if intent.flags.get("deep"):
            return True
        return intent.intent_type in ReflexRouter.FORCE_DELIBERATIVE_TYPES

    @property
    def hit_rate(self) -> float:
        if self.try_count == 0:
            return 0.0
        return self.hit_count / self.try_count

    def stats_by_rule(self) -> dict[str, dict[str, int | float]]:
        out: dict[str, dict[str, int | float]] = {}
        now = time.time()
        for rule_id, tries in self._try_count_by_rule.items():
            hits = self._hit_count_by_rule.get(rule_id, 0)
            last_hit = self._last_hit_at_by_rule.get(rule_id)
            entry: dict[str, int | float] = {
                "tries": tries,
                "hits": hits,
                "hit_rate": hits / tries if tries > 0 else 0.0,
            }
            if last_hit is not None:
                entry["last_hit_at"] = last_hit
                entry["stale_for_hours"] = (now - last_hit) / 3600.0
            out[rule_id] = entry
        return out

    def coverage_summary(
        self,
        *,
        stale_hours: float = 24.0,
    ) -> dict[str, Any]:
        """Identify rules that look unused · feeds the admin UI's
        "rules to prune" callout.

        A rule counts as STALE when:
          * it has at least one ``try`` (so we know it's been seen by
            real traffic, not just sitting unused since boot)
          * its last_hit_at is more than ``stale_hours`` ago, OR it
            has zero hits ever

        Brand-new rules (no tries yet) count as ``unexercised`` rather
        than stale · they might just be waiting for the first match.
        """
        now = time.time()
        stale: list[str] = []
        unexercised: list[str] = []
        rule_ids = {r.rule_id for r in self._reflexes}
        for rid in rule_ids:
            tries = self._try_count_by_rule.get(rid, 0)
            self._hit_count_by_rule.get(rid, 0)
            if tries == 0:
                unexercised.append(rid)
                continue
            last_hit = self._last_hit_at_by_rule.get(rid)
            if last_hit is None:
                stale.append(rid)
                continue
            if (now - last_hit) / 3600.0 > stale_hours:
                stale.append(rid)
        return {
            "stale_threshold_hours": stale_hours,
            "stale": sorted(stale),
            "unexercised": sorted(unexercised),
            "total_rules": len(rule_ids),
        }

    # ─── hot-reload support ──────────────────────────────────

    def replace_reflexes(
        self,
        reflexes: Iterable[Reflex],
        *,
        reset_stats: bool = False,
    ) -> int:
        """Swap the matcher list in-place · returns the new count.

        Used by ``POST /api/reflex/reload`` so existing references to
        this router (held by the realtime and OpenAI gateways via
        closure capture) keep working after a config-file
        edit · no need to re-wire the whole app.

        ``reset_stats`` is opt-in · by default the hit-rate counters
        survive a reload so operators can compare before / after
        without losing baseline data.
        """
        # Same sort key as __init__ · keeps priority semantics.
        self._reflexes = sorted(reflexes, key=lambda r: -r.priority)
        if reset_stats:
            self.hit_count = 0
            self.try_count = 0
            self._hit_count_by_rule.clear()
            self._try_count_by_rule.clear()
        return len(self._reflexes)

    def list_rules(self) -> list[dict[str, Any]]:
        """Return per-rule descriptors · feeds the admin UI's "current
        reflex rules" panel. Includes rule_id, kind (regex/cache/...),
        priority, and any cheap-to-extract metadata.
        """
        now = time.time()
        out: list[dict[str, Any]] = []
        for r in self._reflexes:
            entry: dict[str, Any] = {
                "rule_id": r.rule_id,
                "kind": r.kind,
                "priority": r.priority,
            }
            # Coverage timestamps · let the admin UI show "last fired
            # 2h ago" / "never fired" so dead rules can be spotted.
            last_hit = self._last_hit_at_by_rule.get(r.rule_id)
            if last_hit is not None:
                entry["last_hit_at"] = last_hit
                entry["stale_for_hours"] = (now - last_hit) / 3600.0
            # Surface the regex pattern when available · helps operators
            # confirm a hot-reload picked up the new pattern.
            pattern = getattr(r, "_regex", None)
            if pattern is not None and hasattr(pattern, "pattern"):
                entry["pattern"] = pattern.pattern
            intent_type = getattr(r, "_intent_type", None)
            if intent_type:
                entry["intent_type"] = intent_type
            ttl = getattr(r, "_ttl_seconds", None)
            if ttl:
                entry["ttl_seconds"] = ttl
            # Surface configured variants + per-variant hit counts so
            # the admin UI can render an A/B comparison column. None
            # when the rule has no variants.
            variants = getattr(r, "_variants", None)
            if variants:
                hits = getattr(r, "_variant_hits", {}) or {}
                entry["variants"] = [
                    {
                        "variant_id": vid,
                        "weight": w,
                        "hits": hits.get(vid, 0),
                        "preview": (
                            (resp.get("reply") if isinstance(resp, dict) else str(resp)) or ""
                        )[:60],
                    }
                    for (w, resp, vid) in variants
                ]
                # Per-actor pinning · {actor_id: variant_id} when set
                # in YAML's ``per_actor:`` block. Lets the admin UI
                # show "alice→polite, bob→casual" alongside variants.
                pinned = getattr(r, "_per_actor_variant", None)
                if pinned:
                    entry["per_actor"] = dict(pinned)
            # Surface action presence (don't dump cmd/url for security)
            spec = getattr(r, "_action_spec", None)
            if spec is not None:
                entry["actions"] = [
                    k for k in ("webhook", "mqtt", "exec") if getattr(spec, k, None) is not None
                ]
            # Surface gating · sanitised describe() avoids leaking
            # sensitive actor lists if any. Lets the admin UI render
            # a "🌙 22:00–06:00" or "10% rollout" badge so operators
            # can see at a glance which rules are gated.
            gating = getattr(r, "_gating_spec", None)
            if gating is not None:
                entry["enabled_when"] = gating.describe()
            out.append(entry)
        return out
