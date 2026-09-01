from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

from runtime.adapters.scheduler import BackgroundRunner
from runtime.safety.budget_breaker import CircuitBreaker

from .coordinator import Coordinator, LeaderGuard, Lease


@dataclass(frozen=True)
class HeartsSnapshot:
    systemic: dict[str, Any]
    branchial: dict[str, dict[str, Any]]
    healthy: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "systemic": self.systemic,
            "branchial": self.branchial,
            "healthy": self.healthy,
        }


class Hearts(AbstractContextManager["Hearts"]):
    def __init__(
        self,
        *,
        systemic: BackgroundRunner | None = None,
        branchial: dict[str, CircuitBreaker] | None = None,
        coordinator: Coordinator | None = None,
    ) -> None:
        self.systemic: BackgroundRunner = systemic or BackgroundRunner()
        self.branchial: dict[str, CircuitBreaker] = dict(branchial or {})
        self.coordinator: Coordinator | None = coordinator
        self._held_leases: dict[str, Lease] = {}

    def register_branchial(self, name: str, breaker: CircuitBreaker) -> None:
        if not name:
            raise ValueError("channel name must be non-empty")
        if name in self.branchial:
            raise ValueError(f"duplicate branchial channel: {name!r}")
        self.branchial[name] = breaker

    def dispatch_io(self, channel: str) -> CircuitBreaker:
        try:
            return self.branchial[channel]
        except KeyError as e:
            raise KeyError(
                f"no branchial channel named {channel!r} (registered: {sorted(self.branchial)})"
            ) from e

    def channels(self) -> list[str]:
        return sorted(self.branchial)

    def start(self) -> None:
        self.systemic.start()

    def stop(self, timeout: float = 5.0) -> None:
        if self.systemic.is_running:
            self.systemic.stop(timeout=timeout)

    @property
    def is_running(self) -> bool:
        return self.systemic.is_running

    def __enter__(self) -> Hearts:
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    def snapshot(self) -> HeartsSnapshot:
        systemic: dict[str, Any] = {
            "state": self.systemic.state,
            "is_running": self.systemic.is_running,
            "tasks": {
                name: {
                    "success_count": st.success_count,
                    "error_count": st.error_count,
                    "last_run_ts": st.last_run_ts,
                    "last_duration_s": st.last_duration_s,
                    "last_error": st.last_error,
                }
                for name, st in self.systemic.stats().items()
            },
        }
        branchial = {name: breaker.snapshot() for name, breaker in self.branchial.items()}
        return HeartsSnapshot(
            systemic=systemic,
            branchial=branchial,
            healthy=self._compute_healthy(systemic, branchial),
        )

    def healthy(self) -> bool:
        if not self.systemic.is_running:
            return False
        return all(b.state != "open" for b in self.branchial.values())

    # ─── HA · leader election ────────────────────

    def acquire_leadership(
        self,
        scope: str = "systemic",
        *,
        ttl: float = 30.0,
    ) -> LeaderGuard:
        if self.coordinator is None:
            return _AlwaysLeaderGuard()
        return LeaderGuard(self.coordinator, scope=scope, ttl=ttl)

    def is_leader(self, scope: str = "systemic") -> bool:
        if self.coordinator is None:
            return True
        current = self.coordinator.current_lease(scope)
        if current is None:
            return False
        return current.holder_id == self.coordinator.holder_id

    @staticmethod
    def _compute_healthy(
        systemic: dict[str, Any],
        branchial: dict[str, dict[str, Any]],
    ) -> bool:
        if not systemic.get("is_running"):
            return False
        return all(b.get("state") != "open" for b in branchial.values())


class _AlwaysLeaderGuard:
    is_leader: bool = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None
