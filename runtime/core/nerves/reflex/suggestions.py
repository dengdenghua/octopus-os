from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
from typing import Any

from runtime.platform.process.service_provider import get_provider

# Cap to bound memory · 1000 distinct unmatched prompts is plenty for
# a household-scale deployment, and even at 200 bytes each it's only
# ~200 KB. Bigger deployments should swap this for a persistent store.
MAX_TRACKED = 1000


class SuggestionTracker:
    """Records unmatched prompts + emits ranked rule suggestions.

    Thread-safe via a single RLock · the rate of incoming requests is
    capped by the reflex hot-path's own latency, so contention is fine.
    """

    def __init__(self, *, max_tracked: int = MAX_TRACKED) -> None:
        self._max = max_tracked
        self._lock = threading.RLock()
        # OrderedDict so we can move-to-end on each access for LRU eviction.
        # Value is a small dict: {count, first_seen, last_seen, examples}
        self._counts: OrderedDict[str, dict[str, Any]] = OrderedDict()

    @staticmethod
    def _normalize(prompt: str) -> str:
        """Bucket prompts that differ only in whitespace / case · this
        is the cheapest grouping that doesn't require embeddings.
        For Chinese, case is a no-op but trimming still matters."""
        return prompt.strip().lower()

    def record_miss(self, prompt: str) -> None:
        """Note that ``prompt`` reached the planner without a reflex
        hit. Cheap (O(1) amortized) · safe to call from the hot path.
        """
        if not isinstance(prompt, str) or not prompt.strip():
            return
        # Don't track prompts longer than 200 chars · those are
        # unlikely to be repeat queries we'd want to reflex.
        if len(prompt) > 200:
            return
        key = self._normalize(prompt)
        if not key:
            return
        with self._lock:
            now = time.time()
            entry = self._counts.get(key)
            if entry is None:
                # Evict oldest if we're at the cap.
                if len(self._counts) >= self._max:
                    self._counts.popitem(last=False)
                entry = {
                    "count": 0,
                    "first_seen": now,
                    "last_seen": now,
                    "example": prompt,  # keep one verbatim sample for display
                }
                self._counts[key] = entry
            entry["count"] = int(entry.get("count", 0)) + 1
            entry["last_seen"] = now
            # Refresh LRU position so frequently-seen prompts survive.
            self._counts.move_to_end(key)

    def suggestions(
        self,
        *,
        min_count: int = 3,
        limit: int = 20,
        cluster: bool = False,
        similarity: float = 0.6,
    ) -> list[dict[str, Any]]:
        """Return prompts seen ≥ ``min_count`` times, ranked by count
        descending · ties broken by recency. Each entry includes a
        pre-built ``suggested_yaml`` block so the operator can paste
        it straight into ``data/reflex_rules.yaml``.

        ``cluster=True`` enables Jaccard-bigram clustering · prompts
        with character-bigram overlap ≥ ``similarity`` collapse into
        a single suggestion whose ``count`` is the sum of all
        members. The yaml suggestion uses an ``(a|b|c)`` alternation
        regex over all member prompts so one rule covers them all.
        Members are returned in ``aliases`` for inspection.

        Without ``cluster``, behaviour is unchanged from the v1 API
        (one entry per distinct normalized prompt).
        """
        with self._lock:
            items = [
                (k, dict(v)) for k, v in self._counts.items() if int(v.get("count", 0)) >= min_count
            ]
        items.sort(
            key=lambda kv: (-kv[1]["count"], -kv[1]["last_seen"]),
        )
        if cluster:
            return _cluster_suggestions(items, similarity=similarity, limit=limit)
        out: list[dict[str, Any]] = []
        for key, e in items[:limit]:
            out.append(
                {
                    "prompt": e["example"],
                    "normalized": key,
                    "count": e["count"],
                    "first_seen": e["first_seen"],
                    "last_seen": e["last_seen"],
                    "suggested_yaml": _build_suggested_yaml(e["example"]),
                }
            )
        return out

    def reset(self) -> int:
        """Clear all tracked prompts · returns the number dropped.

        Useful after the operator adds the suggested rules to the
        yaml + reloads · rerunning the workload then rebuilds a
        fresh "what's still missing" list.
        """
        with self._lock:
            n = len(self._counts)
            self._counts.clear()
            return n

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "tracked": len(self._counts),
                "capacity": self._max,
                "total_misses": sum(int(e.get("count", 0)) for e in self._counts.values()),
            }


def _bigrams(text: str) -> set[str]:
    """Character bigrams · cheapest similarity feature that works
    for both Chinese (CJK has no spaces) and ASCII. Empty / 1-char
    strings get a synthetic single token so similarity stays defined.
    """
    s = text.strip().lower()
    if len(s) < 2:
        return {s} if s else set()
    return {s[i : i + 2] for i in range(len(s) - 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    """Standard Jaccard · |A∩B| / |A∪B| · 0..1."""
    if not a and not b:
        return 1.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _cluster_suggestions(
    items: list[tuple[str, dict[str, Any]]],
    *,
    similarity: float,
    limit: int,
) -> list[dict[str, Any]]:
    """Single-linkage clustering on bigram-Jaccard similarity ·
    O(N²) which is fine since N is bounded by MAX_TRACKED.

    For each input prompt:
      1. compute its bigram set
      2. assign to first existing cluster whose representative
         (highest-count member) has Jaccard ≥ similarity
      3. else start a new cluster with this prompt as rep

    Returns clusters ranked by total count (sum of member counts),
    each with the merged-alternation regex suggestion baked in.
    """
    clusters: list[dict[str, Any]] = []
    # Pre-compute bigrams for the rep of each cluster · avoids
    # recomputing on every comparison.
    for _key, entry in items:
        prompt = entry["example"]
        bg = _bigrams(prompt)
        attached = False
        for c in clusters:
            if _jaccard(c["_rep_bigrams"], bg) >= similarity:
                c["members"].append(prompt)
                c["count"] += int(entry["count"])
                c["last_seen"] = max(c["last_seen"], entry["last_seen"])
                c["first_seen"] = min(c["first_seen"], entry["first_seen"])
                attached = True
                break
        if not attached:
            clusters.append(
                {
                    "rep": prompt,
                    "_rep_bigrams": bg,
                    "members": [prompt],
                    "count": int(entry["count"]),
                    "first_seen": entry["first_seen"],
                    "last_seen": entry["last_seen"],
                }
            )
    # Sort by total count desc · ties by recency.
    clusters.sort(key=lambda c: (-c["count"], -c["last_seen"]))
    out: list[dict[str, Any]] = []
    for c in clusters[:limit]:
        members = sorted(set(c["members"]))
        out.append(
            {
                "prompt": c["rep"],
                "normalized": c["rep"].strip().lower(),
                "count": c["count"],
                "first_seen": c["first_seen"],
                "last_seen": c["last_seen"],
                "aliases": members,
                "cluster_size": len(members),
                "suggested_yaml": _build_clustered_yaml(c["rep"], members),
            }
        )
    return out


def _build_clustered_yaml(rep: str, members: list[str]) -> str:
    """Build a yaml snippet that covers every member with one
    regex · uses ``^(a|b|c)$`` alternation over the escaped
    member literals. When the cluster has only one member this
    degenerates to the same shape as ``_build_suggested_yaml``.
    """
    if len(members) <= 1:
        return _build_suggested_yaml(rep)
    rid_raw = re.sub(r"[^\w\u4e00-\u9fff]+", "_", rep.strip())
    rid = rid_raw[:24].strip("_").lower()
    if not rid or rid.isdigit():
        import hashlib

        rid = (
            "auto_"
            + hashlib.sha1(
                rep.encode("utf-8"),
                usedforsecurity=False,
            ).hexdigest()[:8]
        )
    alts = "|".join(re.escape(m.strip()) for m in members)
    return (
        f"  - id: {rid}\n"
        f"    type: regex\n"
        f"    pattern: '^({alts})$'\n"
        f'    reply: "TODO · 写一个对该 prompt 的标准回复"\n'
        f"    priority: 20\n"
        f"    # auto-clustered from {len(members)} similar prompts:\n"
        + "".join(f"    #   {m!r}\n" for m in members)
    )


def _build_suggested_yaml(prompt: str) -> str:
    """Build a copy-paste yaml block suggesting a regex rule for the
    given prompt. Pattern uses ``re.escape`` so user-typed special
    chars don't accidentally become regex meta. The rule_id is a
    safe slug derived from the first chars of the prompt.

    For Chinese prompts (which the original ``[^a-z0-9]+`` slug
    regex stripped to empty), we use a Unicode-aware character class
    that keeps CJK ideographs · the resulting rule_id is human-
    recognizable in the panel even when the prompt has no ASCII.
    Failing all of that, fall back to a stable hash-based id so we
    never emit ``id: ``.
    """
    # \w in Python re with the default UNICODE flag matches CJK.
    raw = re.sub(r"[^\w\u4e00-\u9fff]+", "_", prompt.strip())
    rid = raw[:24].strip("_").lower()
    if not rid or rid.isdigit():
        # Pure-numeric or empty slug · fall back to a hash for stability.
        import hashlib

        rid = (
            "auto_"
            + hashlib.sha1(
                prompt.encode("utf-8"),
                usedforsecurity=False,
            ).hexdigest()[:8]
        )
    pattern = re.escape(prompt.strip())
    # Use single-quoted YAML for the pattern so backslashes survive
    # round-tripping. Reply is a TODO so the operator picks the right
    # answer · we don't want to invent one for them.
    return (
        f"  - id: {rid}\n"
        f"    type: regex\n"
        f"    pattern: '^{pattern}$'\n"
        f'    reply: "TODO · 写一个对该 prompt 的标准回复"\n'
        f"    priority: 20\n"
    )


def get_default_tracker() -> SuggestionTracker:
    """Lazy-init the process-wide tracker · safe to call repeatedly."""
    provider = get_provider()
    tracker = provider.get("suggestion_tracker")
    if tracker is None:
        tracker = SuggestionTracker()
        provider.register_instance("suggestion_tracker", tracker)
    return tracker


__all__ = [
    "SuggestionTracker",
    "get_default_tracker",
]
