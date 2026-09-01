from __future__ import annotations

import fnmatch
import logging
from typing import Literal

from runtime.platform.models import AntigenSignature, ImmuneReport, ImmuneVerdict, ToolCall
from runtime.safety.auth.adaptive_immunity import AdaptiveImmunity
from runtime.safety.auth.attack_memory import AttackMemory

_LOG = logging.getLogger("echo.safety.trust")

UnknownPolicy = Literal["quarantine", "reject", "allow"]


class TrustEngine:
    def __init__(
        self,
        trusted_sources: list[str] | None = None,
        self_whitelist: list[str] | None = None,
        unknown_policy: UnknownPolicy = "quarantine",
        attack_memory: AttackMemory | None = None,
        adaptive: AdaptiveImmunity | None = None,
    ) -> None:
        # Default trust roster — keep these in sync with the
        # ``ImmunityConfig`` defaults in
        # ``runtime/platform/config/schema.py``. Same exact defaults
        # ensure the in-process construction (via _build_stack in cli)
        # matches the yaml-driven construction.
        #
        # SECURITY: ``mcp://*`` is intentionally REMOVED from defaults.
        # MCP servers must be explicitly whitelisted in config to be
        # trusted. This prevents malicious MCP servers from bypassing
        # trust checks out of the box.
        self.trusted_sources = (
            trusted_sources if trusted_sources is not None else ["skill://public/*"]
        )
        self.self_whitelist = (
            self_whitelist
            if self_whitelist is not None
            else [
                "cerebrum",
                "ganglia",
                "arms/*",
                "react_loop",
                "react_arm",
                "intel_collector/*",
            ]
        )
        self.unknown_policy = unknown_policy
        # Memory tier (protocols/immunity.md): known attackers are
        # rejected before the trusted_sources allow — a compromised
        # entity inside a trusted glob must not keep its free pass.
        self.attack_memory = attack_memory if attack_memory is not None else AttackMemory()
        # Adaptive tier (protocols/immunity.md §Adaptive). Disabled by
        # default (None) — opt in via config so deployments without a
        # cost-baseline history don't quarantine on noise.
        self.adaptive = adaptive

    def check(self, call: ToolCall, signature: AntigenSignature) -> ImmuneReport:
        if self._is_self(call):
            return ImmuneReport(
                verdict="allow",
                signature=signature,
                strategy_used="tolerance",
                reason="self-whitelisted caller",
            )

        pattern = self.attack_memory.match(signature.entity_id)
        if pattern is not None:
            return ImmuneReport(
                verdict="reject",
                signature=signature,
                strategy_used="memory",
                reason=(f"known attack pattern (hits={pattern.hit_count}): {pattern.reason}"),
            )

        # Adaptive tier — behavioural anomaly. Risk only TIGHTENS: a
        # high score quarantines even an otherwise-trusted source (a
        # trusted skill suddenly costing 10x its baseline is worth a
        # second look), but a low score never relaxes Innate/Memory.
        # Self callers already returned above (I2: no autoimmunity).
        if self.adaptive is not None:
            sucker_str = str(call.sucker_id)
            # Fast path: sucker already in quarantine from a prior observed anomaly.
            if self.adaptive.is_quarantined(sucker_str):
                return ImmuneReport(
                    verdict="quarantine",
                    signature=signature,
                    strategy_used="adaptive",
                    reason="quarantined: recent behavioural anomaly",
                )
            predicted = call.predicted_cost
            # Auto-fill predicted_cost from the sucker's baseline mean via
            # adaptive.predict() when the caller doesn't supply one. Mature
            # baselines get real pre-execute scoring; immature baselines
            # (cold-start, predict() returns None) fall through with 0.0
            # predicted cost — the adaptive tier still learns post-execute
            # via the learn() method and can quarantine on observed anomalies.
            if predicted is None:
                predicted = self.adaptive.predict(sucker_str)
            score = self.adaptive.compute_risk(
                sucker_str,
                predicted_latency_ms=predicted.latency_ms if predicted else 0.0,
                predicted_tokens=(
                    (predicted.tokens_in + predicted.tokens_out) if predicted else 0.0
                ),
            )
            if self.adaptive.is_anomalous(score):
                return ImmuneReport(
                    verdict="quarantine",
                    signature=signature,
                    strategy_used="adaptive",
                    risk=score,
                    reason=f"behavioural anomaly: {score.reason}",
                )

        if self._is_trusted(signature.entity_id):
            return ImmuneReport(
                verdict="allow",
                signature=signature,
                strategy_used="innate",
                reason="matched trusted_sources",
            )

        verdict: ImmuneVerdict
        if self.unknown_policy == "reject":
            verdict = "reject"
            reason = "unknown source, policy=reject"
            # Crystallization input: repeated hard rejections of the
            # same source become an antibody, so the source stays
            # rejected even if the policy is later relaxed.
            self.attack_memory.record_violation(
                signature.entity_id,
                reason="repeated innate rejections",
            )
        elif self.unknown_policy == "allow":
            verdict = "allow"
            reason = "unknown source, policy=allow (risky)"
        else:
            verdict = "quarantine"
            reason = "unknown source, policy=quarantine"

        return ImmuneReport(
            verdict=verdict,
            signature=signature,
            strategy_used="innate",
            reason=reason,
        )

    def learn(self, call: ToolCall, *, latency_ms: float, tokens: float) -> None:
        """Fold an executed call's observed cost into the adaptive
        baseline (protocols/immunity.md Execute-time learning loop).
        No-op when the adaptive tier is disabled.

        Audit-only behavioural-anomaly observability: BEFORE folding the
        observation in, score it against the sucker's existing baseline.
        A call far from this sucker's normal latency/token profile (a
        classic exfil/abuse/hang signature) is logged for telemetry — it
        is NEVER blocked here (the call already ran). This gives the
        adaptive tier a real, sound effect while its pre-execute quarantine
        path stays inert (that path needs a cost *prediction* the runtime
        doesn't supply yet — observed-vs-baseline is the sound substitute)."""
        # The check path exempts self-whitelisted orchestration callers from
        # adaptive immunity (I2: no autoimmunity). Learning must honor the
        # same boundary: otherwise internal eval/agent traffic can poison the
        # shared baseline and quarantine a skill for later external callers,
        # even though the originating self-call could never be blocked.
        if self.adaptive is None or self._is_self(call):
            return
        sucker = str(call.sucker_id)
        score = self.adaptive.score_observed(sucker, latency_ms=latency_ms, tokens=tokens)
        if self.adaptive.is_anomalous(score):
            _LOG.warning(
                "adaptive immunity · behavioural anomaly · sucker=%s "
                "composite=%.2f · %s · quarantining for %.0fs",
                sucker,
                score.composite,
                score.reason,
                self.adaptive.quarantine_ttl_s,
            )
            self.adaptive.quarantine(sucker)
        self.adaptive.learn(sucker, latency_ms=latency_ms, tokens=tokens)

    def _is_self(self, call: ToolCall) -> bool:
        caller = str(getattr(call, "caller", "") or "")
        return any(fnmatch.fnmatch(caller, pattern) for pattern in self.self_whitelist)

    def _is_trusted(self, entity_id: str) -> bool:
        return any(fnmatch.fnmatch(entity_id, pattern) for pattern in self.trusted_sources)
