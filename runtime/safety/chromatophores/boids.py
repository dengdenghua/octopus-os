from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from runtime.adapters.instrumentation import trace_stage
from runtime.platform.models import ArmId, new_id, now_utc

from .signal_bus import TOPIC_SUCKER_GRABBED, SignalBus

ClaimVerdict = Literal["win", "lose", "coexist"]


# ─── ResourceClaim ─────────────────────────────────────────


class ResourceClaim(BaseModel):
    model_config = ConfigDict(frozen=True)

    claim_id: UUID = Field(default_factory=new_id)
    arm_id: ArmId
    resource_uri: str = Field(..., min_length=1)
    priority: int = Field(default=50, ge=0, le=100)
    ttl_ms: int = Field(default=5000, ge=0)
    claimed_at: datetime = Field(default_factory=now_utc)

    def is_expired(self, ref: datetime | None = None) -> bool:
        ref = ref or now_utc()
        return (ref - self.claimed_at) >= timedelta(milliseconds=self.ttl_ms)

    def is_readonly(self) -> bool:
        uri = self.resource_uri.lower()
        return ":read" in uri or uri.startswith("readonly:")


# ─── BoidsArbitrator ───────────────────────────────────────


class BoidsArbitrator:
    def __init__(self, signal_bus: SignalBus | None = None) -> None:
        self._lock = threading.Lock()
        self._holders: dict[str, ResourceClaim] = {}
        self._ro_holders: dict[str, dict[ArmId, ResourceClaim]] = {}
        self._bus = signal_bus

    # ─── arbitrate ────────────────────────────────────────

    def arbitrate(self, claim: ResourceClaim) -> ClaimVerdict:
        with trace_stage(
            "chromatophores.boids.arbitrate",
            stage="arbitrate",
            arm_id=str(claim.arm_id),
            resource_uri=claim.resource_uri,
        ):
            verdict = self._arbitrate_locked(claim)

        if verdict == "win" and self._bus is not None:
            self._bus.publish(
                TOPIC_SUCKER_GRABBED,
                {
                    "resource_uri": claim.resource_uri,
                    "arm_id": str(claim.arm_id),
                    "priority": claim.priority,
                    "claim_id": str(claim.claim_id),
                },
                publisher=str(claim.arm_id),
            )
        return verdict

    def _arbitrate_locked(self, claim: ResourceClaim) -> ClaimVerdict:
        with self._lock:
            self._gc_expired_locked()
            uri = claim.resource_uri

            if claim.is_readonly():
                bucket = self._ro_holders.setdefault(uri, {})
                bucket[claim.arm_id] = claim
                return "coexist"

            current = self._holders.get(uri)
            if current is None:
                self._holders[uri] = claim
                return "win"

            if current.arm_id == claim.arm_id:
                self._holders[uri] = claim
                return "win"

            if claim.priority > current.priority:
                self._holders[uri] = claim
                return "win"
            if claim.priority < current.priority:
                return "lose"

            if claim.claimed_at < current.claimed_at:
                self._holders[uri] = claim
                return "win"
            return "lose"

    # ─── release / clear_expired ──────────────────────────

    def release(self, arm_id: ArmId, resource_uri: str) -> None:
        with self._lock:
            current = self._holders.get(resource_uri)
            if current is not None and current.arm_id == arm_id:
                del self._holders[resource_uri]

            ro_bucket = self._ro_holders.get(resource_uri)
            if ro_bucket is not None and arm_id in ro_bucket:
                del ro_bucket[arm_id]
                if not ro_bucket:
                    del self._ro_holders[resource_uri]

    def clear_expired(self) -> int:
        with self._lock:
            return self._gc_expired_locked()

    def _gc_expired_locked(self) -> int:
        now = now_utc()
        removed = 0
        expired_uris = [uri for uri, c in self._holders.items() if c.is_expired(now)]
        for uri in expired_uris:
            del self._holders[uri]
            removed += 1
        empty_uris: list[str] = []
        for uri, bucket in self._ro_holders.items():
            expired_arms = [a for a, c in bucket.items() if c.is_expired(now)]
            for a in expired_arms:
                del bucket[a]
                removed += 1
            if not bucket:
                empty_uris.append(uri)
        for uri in empty_uris:
            del self._ro_holders[uri]
        return removed

    def active_claims(self) -> list[ResourceClaim]:
        with self._lock:
            self._gc_expired_locked()
            result: list[ResourceClaim] = list(self._holders.values())
            for bucket in self._ro_holders.values():
                result.extend(bucket.values())
            return result

    # ─── Alignment (Boids 原则 2:同 affinity 同 tick 启动) ──────

    def alignment_groups(
        self,
        arms: list[tuple[ArmId, list[str]]],
    ) -> dict[str, list[ArmId]]:
        """Group arms by shared affinity for coordinated start (Alignment).

        Returns a mapping ``affinity -> [arm_id, ...]``. Arms sharing an
        affinity tag are expected to start in the same tick so their
        outputs converge predictably. Arms with no affinity land in the
        ``""`` bucket and start independently (no barrier).
        """
        groups: dict[str, list[ArmId]] = {}
        for arm_id, affinities in arms:
            if not affinities:
                groups.setdefault("", []).append(arm_id)
                continue
            primary = affinities[0]
            groups.setdefault(primary, []).append(arm_id)
        return groups

    # ─── Cohesion (Boids 原则 3:idle 腕靠拢最忙簇) ──────────

    def cohesion_rebalance(
        self,
        arm_load: dict[ArmId, int],
        idle_arms: list[ArmId],
        max_redirect: int = 1,
    ) -> dict[ArmId, ArmId]:
        """Suggest idle arms to migrate toward the busiest cluster (Cohesion).

        Given current per-arm pending load and a list of idle arms, returns a
        mapping ``idle_arm -> busiest_arm`` suggesting each idle arm redirect
        to help the busiest arm. Only ``max_redirect`` idle arms are redirected
        per call (avoid thundering herd). Returns empty dict when no load
        imbalance exists.

        This is advisory — the caller (SwarmRuntime) decides whether to act
        on the suggestion by reassigning pending assignments.
        """
        if not idle_arms or not arm_load:
            return {}
        # Find the busiest arm (highest pending load).
        busiest_arm = max(arm_load, key=lambda a: arm_load[a])
        busiest_load = arm_load[busiest_arm]
        if busiest_load <= 1:
            # Nobody is overloaded; cohesion has nothing to do.
            return {}
        # Redirect up to max_redirect idle arms toward the busiest.
        return {idle: busiest_arm for idle in idle_arms[:max_redirect]}
