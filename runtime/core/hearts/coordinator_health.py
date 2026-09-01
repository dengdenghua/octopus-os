from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .coordinator import Coordinator, Lease

_LOG = logging.getLogger("echo.hearts.health")


@dataclass
class LeaseInfo:
    scope: str
    lease: Lease
    acquired_at: float
    last_renew_at: float
    renew_count: int = 0
    renew_failures: int = 0


@dataclass
class CoordinatorHealthReport:
    healthy: bool
    coordinator_type: str
    holder_id: str
    active_leases: int
    lease_details: list[dict[str, Any]] = field(default_factory=list)
    connectivity_ok: bool = True
    last_connectivity_check: float = 0.0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "healthy": self.healthy,
            "coordinator_type": self.coordinator_type,
            "holder_id": self.holder_id,
            "active_leases": self.active_leases,
            "lease_details": self.lease_details,
            "connectivity_ok": self.connectivity_ok,
            "last_connectivity_check": self.last_connectivity_check,
            "errors": self.errors,
        }


class LeaseGuardian:
    def __init__(
        self,
        coordinator: Coordinator,
        *,
        renew_interval_seconds: float = 10.0,
        renew_ratio: float = 0.5,
        max_renew_failures: int = 5,
    ) -> None:
        self.coordinator = coordinator
        self.renew_interval = renew_interval_seconds
        self.renew_ratio = renew_ratio
        self.max_renew_failures = max_renew_failures
        self._watches: dict[str, LeaseInfo] = {}
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def watch(self, scope: str, lease: Lease) -> None:
        with self._lock:
            self._watches[scope] = LeaseInfo(
                scope=scope,
                lease=lease,
                acquired_at=time.time(),
                last_renew_at=time.time(),
            )

    def unwatch(self, scope: str) -> LeaseInfo | None:
        with self._lock:
            return self._watches.pop(scope, None)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._guardian_loop,
            name="lease-guardian",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def watched_scopes(self) -> list[str]:
        with self._lock:
            return list(self._watches.keys())

    def _guardian_loop(self) -> None:
        while not self._stop_event.wait(self.renew_interval):
            self._renew_all()

    def _renew_all(self) -> None:
        with self._lock:
            scopes = list(self._watches.keys())
        now = time.time()
        for scope in scopes:
            with self._lock:
                info = self._watches.get(scope)
            if info is None:
                continue
            ttl_remaining = info.lease.expires_at - now
            original_ttl = info.lease.expires_at - info.lease.acquired_at
            if original_ttl <= 0:
                continue
            if ttl_remaining > original_ttl * self.renew_ratio:
                continue
            try:
                new_lease = self.coordinator.renew_lease(
                    info.lease,
                    ttl=original_ttl,
                )
                if new_lease is not None:
                    with self._lock:
                        if scope in self._watches:
                            self._watches[scope].lease = new_lease
                            self._watches[scope].last_renew_at = now
                            self._watches[scope].renew_count += 1
                else:
                    with self._lock:
                        if scope in self._watches:
                            self._watches[scope].renew_failures += 1
                            if self._watches[scope].renew_failures >= self.max_renew_failures:
                                _LOG.error(
                                    "lease guardian: scope %s lost after %d renew failures",
                                    scope,
                                    self.max_renew_failures,
                                )
                                del self._watches[scope]
            except Exception as exc:
                _LOG.warning("lease guardian renew failed for %s: %s", scope, exc)
                with self._lock:
                    if scope in self._watches:
                        self._watches[scope].renew_failures += 1


class CoordinatorHealth:
    def __init__(
        self,
        coordinator: Coordinator,
        *,
        guardian: LeaseGuardian | None = None,
        warning_threshold_seconds: float = 10.0,
    ) -> None:
        self.coordinator = coordinator
        self.guardian = guardian
        self.warning_threshold = warning_threshold_seconds

    def check(self) -> CoordinatorHealthReport:
        errors: list[str] = []
        lease_details: list[dict[str, Any]] = []
        now = time.time()
        connectivity_ok = True

        coord_type = type(self.coordinator).__name__
        holder_id = self.coordinator.holder_id

        try:
            if hasattr(self.coordinator, "client"):
                client = self.coordinator.client
                if hasattr(client, "ping"):
                    client.ping()
        except Exception as exc:
            connectivity_ok = False
            errors.append(f"connectivity check failed: {exc}")

        watched: dict[str, LeaseInfo] = {}
        if self.guardian is not None:
            with self.guardian._lock:
                watched = dict(self.guardian._watches)

        for scope, info in watched.items():
            remaining = info.lease.expires_at - now
            detail: dict[str, Any] = {
                "scope": scope,
                "ttl_remaining_seconds": round(remaining, 1),
                "renew_count": info.renew_count,
                "renew_failures": info.renew_failures,
                "status": "ok",
            }
            if remaining <= 0:
                detail["status"] = "expired"
                errors.append(f"lease expired: {scope}")
            elif remaining < self.warning_threshold:
                detail["status"] = "warning"
                errors.append(f"lease expiring soon: {scope} ({remaining:.1f}s remaining)")
            if info.renew_failures > 0:
                detail["status"] = "degraded"
            lease_details.append(detail)

        healthy = connectivity_ok and not any(d["status"] in ("expired",) for d in lease_details)

        return CoordinatorHealthReport(
            healthy=healthy,
            coordinator_type=coord_type,
            holder_id=holder_id,
            active_leases=len(watched),
            lease_details=lease_details,
            connectivity_ok=connectivity_ok,
            last_connectivity_check=now,
            errors=errors,
        )
