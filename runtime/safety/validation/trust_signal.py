"""Trust signal — bridges P1 guard telemetry into P0 constitution decisions.

Background
----------
The constitution gate (``gate.check_outbound``) decides allow/rewrite/
block on every outbound message. Its decisions are based on STATIC
patterns: regex secrets, regex PII, an optional LLM judge. None of
those reflect *how trustworthy this project's agent has been lately*.

After P1 landed, we have a different signal: per-guard precision data
(what fraction of the agent's caught Final Answers were genuinely
buggy vs. false-positive). An agent that's been sloppy this week —
getting blocked by real security guards, not noise — should face a
STRICTER constitution. An agent that's been clean — guards rarely fire
or fire only on genuine catches — has earned looser handling.

This module computes that trust score. It does NOT change gate
behaviour today. It exposes a number the gate (or an operator
dashboard, or the prompt evolver) can read and act on. The
``ConstitutionProfile`` selection is a separate decision that downstream
code may wire later.

Score semantics
---------------
``compute_guard_trust_score()`` returns a float in [0.0, 1.0]:

* 1.0 — perfect track record. Few hits OR all judged true_positive.
* 0.5 — neutral / unknown. No data yet, or data evenly split.
* 0.0 — every recent security-class hit was a real catch. Agent is
        actively trying to ship bad output and the guards keep saving
        it. Tighten profile, raise human-gate thresholds.

Why security-class?
~~~~~~~~~~~~~~~~~~~
A high false-positive rate on ``magic-number guard`` (code-smell)
tells us the detector is noisy. It doesn't tell us the agent is
unsafe. A high TRUE-positive rate on ``secret-leak guard`` (security)
tells us the agent IS unsafe — the only reason precision is high is
because the agent keeps trying to leak secrets. Security is the
right category to derive trust from.

Defaults
--------
``compute_guard_trust_score(digest)`` reads from any digest dict
shaped like ``GuardTelemetry.digest()``. Pass ``None`` (or call the
module-level helper that pulls the singleton sink) to get a
neutral 0.5 when telemetry is unavailable.
"""

from __future__ import annotations

import logging
from typing import Any

_LOG = logging.getLogger("runtime.safety.validation.trust_signal")

NEUTRAL_SCORE = 0.5
HIGH_TRUST_FLOOR = 0.85
LOW_TRUST_CEILING = 0.20


def compute_guard_trust_score(
    digest: dict[str, Any] | None,
    *,
    category: str = "security",
    min_judged_for_signal: int = 5,
) -> float:
    """Compute trust score from a GuardTelemetry digest.

    Score is derived from the chosen ``category``'s guard precision
    data (default: ``security``). Higher TP rate → LOWER trust (agent
    keeps tripping real catches); lower TP rate / no hits → higher
    trust.

    When fewer than ``min_judged_for_signal`` security hits have been
    judged, returns ``NEUTRAL_SCORE`` (0.5) — not enough data to
    tighten or relax safely.
    """
    if not isinstance(digest, dict):
        return NEUTRAL_SCORE
    if int(digest.get("total_hits") or 0) <= 0:
        return 1.0  # No hits at all — clean slate.

    label_precision = digest.get("label_precision") or {}
    by_label = digest.get("by_label") or {}
    if not isinstance(label_precision, dict) or not isinstance(by_label, dict):
        return NEUTRAL_SCORE

    # Aggregate TP/FP across all labels in the chosen category.
    category_labels = _labels_in_category(digest, category)
    if not category_labels:
        return NEUTRAL_SCORE  # No security guards fired — neutral, not perfect

    tp = 0
    fp = 0
    judged = 0
    for label in category_labels:
        stats = label_precision.get(label) or {}
        tp += int(stats.get("tp") or 0)
        fp += int(stats.get("fp") or 0)
        judged += int(stats.get("judged") or 0)

    if judged < min_judged_for_signal:
        return NEUTRAL_SCORE

    graded = tp + fp
    if graded == 0:
        return NEUTRAL_SCORE

    # High TP rate = agent keeps doing bad things → low trust
    # Low  TP rate = agent fine, guards noisy → high trust
    tp_rate = tp / graded
    return round(1.0 - tp_rate, 4)


def _labels_in_category(
    digest: dict[str, Any],
    category: str,
) -> list[str]:
    """Return labels in the digest belonging to ``category``.

    The digest itself doesn't carry per-label category — that lives
    in the GuardSpec registry. We mirror the registry mapping here
    by importing lazily; if the import fails (circular / unavailable),
    fall back to label-name heuristics.
    """
    try:
        from runtime.core.cerebrum.react_guards import GUARD_REGISTRY

        return [spec.label for spec in GUARD_REGISTRY if spec.category == category]
    except Exception:  # noqa: BLE001 — fall back to heuristic
        _LOG.debug("GUARD_REGISTRY unavailable; using heuristic labels")
        if category == "security":
            return [
                "secret-leak guard",
                "destructive-call guard",
                "dynamic-exec guard",
                "shell-injection guard",
                "unsafe-deser guard",
            ]
        return []


def classify_trust_score(score: float) -> str:
    """Bucket a numeric score into a coarse label for downstream use.

    * ``"trusted"``   — score >= HIGH_TRUST_FLOOR (loosen profile OK)
    * ``"suspect"``   — score <= LOW_TRUST_CEILING (tighten profile)
    * ``"neutral"``   — between (no clear signal)
    """
    if score >= HIGH_TRUST_FLOOR:
        return "trusted"
    if score <= LOW_TRUST_CEILING:
        return "suspect"
    return "neutral"


def fetch_current_trust_score(
    *,
    category: str = "security",
    min_judged_for_signal: int = 5,
) -> float:
    """Read the singleton GuardTelemetry sink and compute current trust.

    Defaults to NEUTRAL_SCORE on any failure — telemetry path missing,
    YAML broken, etc. Designed to be safe to call from the gate hot
    path even when telemetry isn't fully wired.
    """
    try:
        from runtime.safety.evolution.guard_telemetry import GuardTelemetry

        digest = GuardTelemetry().digest()
    except Exception as exc:  # noqa: BLE001 — fail neutral
        _LOG.debug("trust signal fetch failed: %s — returning neutral", exc)
        return NEUTRAL_SCORE
    return compute_guard_trust_score(
        digest,
        category=category,
        min_judged_for_signal=min_judged_for_signal,
    )


def render_trust_summary(
    digest: dict[str, Any] | None,
    *,
    category: str = "security",
) -> str:
    """One-line summary for logs / dashboard / CLI."""
    if not isinstance(digest, dict) or int(digest.get("total_hits") or 0) <= 0:
        return "guard trust: no data — neutral 0.50"
    score = compute_guard_trust_score(digest, category=category)
    bucket = classify_trust_score(score)
    return (
        f"guard trust: {score:.2f} ({bucket}) "
        f"based on {category} hits across "
        f"{int(digest.get('total_hits', 0))} total"
    )


__all__ = [
    "HIGH_TRUST_FLOOR",
    "LOW_TRUST_CEILING",
    "NEUTRAL_SCORE",
    "classify_trust_score",
    "compute_guard_trust_score",
    "fetch_current_trust_score",
    "render_trust_summary",
]
