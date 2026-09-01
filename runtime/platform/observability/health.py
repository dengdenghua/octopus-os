"""Health Check Framework — K8s-ready liveness & readiness probes.

The existing ``/api/health`` endpoint is a dashboard summary: always
returns 200 as long as FastAPI is up. K8s readiness probes need
something sharper:

* **Liveness** — am I alive? Fails only on deadlock / GC pause / OOM.
* **Readiness** — am I ready to serve traffic? Fails while DB /
  Redis / downstream LLM providers are unhealthy.

A pod that's alive-but-not-ready is drained from the Service load
balancer by K8s; dead pods are restarted. Separating the two means
restarts happen only when restart is the correct remedy.

Design
------
``HealthCheck`` is the unit of work: name, timeout, and an async or
sync callable that returns ``HealthStatus`` (pass/warn/fail).

``HealthRegistry`` aggregates multiple checks into one probe result.
Checks run in parallel with per-check timeouts so one slow check
can't stall the probe.

FastAPI adapter exposes two endpoints:

* ``GET /livez`` — 200 iff every **liveness** check passes.
* ``GET /readyz`` — 200 iff every **readiness** check passes.

Either responds with::

    {
      "status": "pass" | "warn" | "fail",
      "checks": [{"name": "...", "status": "...", "detail": "..."}],
      "duration_ms": 12
    }

so humans can hit them directly for diagnostics.

Built-in checks
---------------
* ``memory_check`` — gauge on RSS if ``psutil`` is installed; skipped
  otherwise so the framework stays zero-dep.
* ``disk_check`` — free space on the data dir.
* ``journal_check`` — verify the journal is writeable (readiness).
* ``redis_check`` — ping a Redis coordinator if configured (readiness).
* ``llm_router_check`` — 1-token canary to the default LLM router
  (readiness only, with 10s timeout; disabled by default because
  paid probes are expensive).

All checks are opt-in via ``registry.register(check)``; nothing is
forced on the agent.

Prometheus integration
----------------------
Each check updates a gauge in the Round-19 metrics registry::

    echo_health_check_status{name="redis", kind="readiness"} 1 | 0

so Grafana dashboards can alert on specific checks without parsing
the JSON body.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

_LOG = logging.getLogger("echo.platform.health")

HealthKind = Literal["liveness", "readiness"]
HealthStatusValue = Literal["pass", "warn", "fail"]


@dataclass(frozen=True)
class HealthStatus:
    """Result of one health check."""

    name: str
    status: HealthStatusValue
    detail: str = ""
    duration_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in ("pass", "warn")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "duration_ms": round(self.duration_ms, 2),
            "metadata": dict(self.metadata),
        }


@dataclass
class HealthCheck:
    """A registered check.

    Parameters
    ----------
    name:
        Short identifier, e.g. ``"redis"``, ``"disk"``.
    check:
        Callable ``() -> HealthStatus`` or ``() -> bool``. A bool
        is coerced: True → pass, False → fail.
    kind:
        ``"liveness"`` or ``"readiness"``. Affects which probe runs
        this check.
    timeout_seconds:
        If the check hasn't returned within this many seconds, the
        check is marked failed with ``detail="timeout"``. The default
        is 2s — any check that's slower than that shouldn't be on
        the probe path.
    critical:
        When True, a failing check forces the entire probe to fail.
        When False, failing is treated as ``warn`` in the aggregate
        — useful for soft dependencies.
    """

    name: str
    check: Callable[[], HealthStatus | bool]
    kind: HealthKind = "readiness"
    timeout_seconds: float = 2.0
    critical: bool = True


# ── Registry ─────────────────────────────────────────────────


class HealthRegistry:
    """Aggregator for health checks.

    Parameters
    ----------
    metrics_registry:
        Optional Round-19 ``MetricsRegistry`` instance. When supplied,
        every probe run writes per-check gauges to it.
    parallel:
        Run checks concurrently (default True). Disable for tests
        that want deterministic ordering.
    """

    def __init__(
        self,
        *,
        metrics_registry: Any | None = None,
        parallel: bool = True,
    ) -> None:
        self._checks: dict[str, HealthCheck] = {}
        self._lock = threading.Lock()
        self._parallel = parallel
        self._metrics = metrics_registry
        self._gauge: Any | None = None
        if metrics_registry is not None:
            try:
                self._gauge = metrics_registry.gauge(
                    "echo_health_check_status",
                    "Health check result (1=pass, 0.5=warn, 0=fail)",
                    labels=["name", "kind"],
                )
            except (OSError, ImportError, AttributeError):
                self._gauge = None

    # ── registration ────────────────────────────────────────

    def register(self, check: HealthCheck) -> None:
        """Register a new check (replacing any prior one of the same name)."""
        with self._lock:
            self._checks[check.name] = check

    def unregister(self, name: str) -> bool:
        with self._lock:
            return self._checks.pop(name, None) is not None

    def names(self, *, kind: HealthKind | None = None) -> list[str]:
        with self._lock:
            if kind is None:
                return sorted(self._checks.keys())
            return sorted(n for n, c in self._checks.items() if c.kind == kind)

    # ── probe ───────────────────────────────────────────────

    def probe(
        self,
        *,
        kind: HealthKind = "readiness",
    ) -> dict[str, Any]:
        """Run every check of ``kind`` and return the aggregate result.

        Aggregate status:
        * ``"pass"``  — every check passed
        * ``"warn"``  — at least one non-critical check failed OR
                         a check returned warn
        * ``"fail"``  — any critical check failed or timed out
        """
        started = time.time()
        with self._lock:
            selected = [c for c in self._checks.values() if c.kind == kind]

        results: list[HealthStatus] = []
        if self._parallel and selected:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=max(1, len(selected)),
                thread_name_prefix="health",
            ) as pool:
                futures = {pool.submit(self._run_check, c): c for c in selected}
                for fut in concurrent.futures.as_completed(futures):
                    results.append(fut.result())
        else:
            for c in selected:
                results.append(self._run_check(c))

        # Aggregate.
        aggregate: HealthStatusValue = "pass"
        check_by_name = {c.name: c for c in selected}
        for r in results:
            c = check_by_name.get(r.name)
            critical = c.critical if c is not None else True
            if r.status == "fail":
                aggregate = "fail" if critical else _max_status(aggregate, "warn")
            elif r.status == "warn":
                aggregate = _max_status(aggregate, "warn")

        # Update Prometheus gauges.
        if self._gauge is not None:
            for r in results:
                val = {"pass": 1.0, "warn": 0.5, "fail": 0.0}[r.status]
                with contextlib.suppress(Exception):
                    self._gauge.set(
                        val,
                        labels={"name": r.name, "kind": kind},
                    )

        duration_ms = (time.time() - started) * 1000
        return {
            "status": aggregate,
            "kind": kind,
            "checks": [r.to_dict() for r in sorted(results, key=lambda x: x.name)],
            "duration_ms": round(duration_ms, 2),
        }

    # ── internal ────────────────────────────────────────────

    def _run_check(self, check: HealthCheck) -> HealthStatus:
        started = time.time()

        def _execute() -> HealthStatus:
            raw = check.check()
            if isinstance(raw, bool):
                return HealthStatus(
                    name=check.name,
                    status="pass" if raw else "fail",
                    detail="" if raw else "check returned False",
                )
            if isinstance(raw, HealthStatus):
                return raw
            return HealthStatus(
                name=check.name,
                status="fail",
                detail=f"check returned unexpected type: {type(raw).__name__}",
            )

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                fut = pool.submit(_execute)
                try:
                    result = fut.result(timeout=check.timeout_seconds)
                except concurrent.futures.TimeoutError:
                    duration = (time.time() - started) * 1000
                    return HealthStatus(
                        name=check.name,
                        status="fail",
                        detail=f"timed out after {check.timeout_seconds}s",
                        duration_ms=duration,
                    )
        except Exception as exc:  # noqa: BLE001 — health check must coerce every failure mode
            duration = (time.time() - started) * 1000
            return HealthStatus(
                name=check.name,
                status="fail",
                detail=f"{type(exc).__name__}: {exc}",
                duration_ms=duration,
            )

        duration = (time.time() - started) * 1000
        # Ensure duration_ms is on the returned status.
        if result.duration_ms == 0.0:
            result = HealthStatus(
                name=result.name,
                status=result.status,
                detail=result.detail,
                duration_ms=duration,
                metadata=result.metadata,
            )
        return result


def _max_status(a: HealthStatusValue, b: HealthStatusValue) -> HealthStatusValue:
    rank = {"pass": 0, "warn": 1, "fail": 2}
    return a if rank[a] >= rank[b] else b


# ── Built-in checks ─────────────────────────────────────────


def disk_check(
    path: str,
    *,
    min_free_mb: int = 500,
    name: str = "disk",
) -> HealthCheck:
    """Pass if ``path`` has at least ``min_free_mb`` of free space."""
    import shutil

    def _check() -> HealthStatus:
        try:
            usage = shutil.disk_usage(path)
            free_mb = usage.free / (1024 * 1024)
            if free_mb < min_free_mb:
                return HealthStatus(
                    name=name,
                    status="fail",
                    detail=f"only {free_mb:.0f}MB free (min {min_free_mb}MB)",
                    metadata={"free_mb": round(free_mb, 2), "path": path},
                )
            return HealthStatus(
                name=name,
                status="pass",
                metadata={"free_mb": round(free_mb, 2), "path": path},
            )
        except (OSError, TypeError, ValueError) as exc:
            return HealthStatus(
                name=name,
                status="fail",
                detail=f"{type(exc).__name__}: {exc}",
            )

    return HealthCheck(name=name, check=_check, kind="readiness")


def redis_check(client: Any, *, name: str = "redis") -> HealthCheck:
    """Pass if ``client.ping()`` returns a truthy response."""

    def _check() -> HealthStatus:
        try:
            ok = client.ping()
            if ok:
                return HealthStatus(name=name, status="pass")
            return HealthStatus(
                name=name,
                status="fail",
                detail="ping returned falsy",
            )
        except (OSError, ImportError, TypeError, ValueError) as exc:
            return HealthStatus(
                name=name,
                status="fail",
                detail=f"{type(exc).__name__}: {exc}",
            )

    return HealthCheck(name=name, check=_check, kind="readiness")


def effect_store_check(
    store: Any,
    *,
    require_distributed: bool = False,
    name: str = "tool_effects",
) -> HealthCheck:
    """Verify the durable receipt plane and its deployment scope."""

    def _check() -> HealthStatus:
        backend = str(getattr(store, "backend_name", type(store).__name__))
        shared = bool(getattr(store, "shared_across_hosts", False))
        metadata = {"backend": backend, "shared_across_hosts": shared}
        if require_distributed and not shared:
            return HealthStatus(
                name=name,
                status="fail",
                detail="cluster mode requires a cross-host tool-effect backend",
                metadata=metadata,
            )
        try:
            ok = bool(store.ping())
        except Exception as exc:  # noqa: BLE001 - readiness must report arbitrary backend failures
            return HealthStatus(
                name=name,
                status="fail",
                detail=f"{type(exc).__name__}: {exc}",
                metadata=metadata,
            )
        return HealthStatus(
            name=name,
            status="pass" if ok else "fail",
            detail="" if ok else "receipt backend ping returned falsy",
            metadata=metadata,
        )

    return HealthCheck(name=name, check=_check, kind="readiness", critical=True)


def journal_check(journal: Any, *, name: str = "journal") -> HealthCheck:
    """Pass if the journal's ``read_all()`` succeeds without raising."""

    def _check() -> HealthStatus:
        try:
            journal.read_all()
            return HealthStatus(name=name, status="pass")
        except (OSError, ImportError, TypeError, ValueError) as exc:
            return HealthStatus(
                name=name,
                status="fail",
                detail=f"{type(exc).__name__}: {exc}",
            )

    return HealthCheck(name=name, check=_check, kind="readiness")


def memory_check(
    *,
    max_rss_mb: int = 2048,
    name: str = "memory",
) -> HealthCheck:
    """Pass if the process RSS is below ``max_rss_mb``.

    Requires ``psutil``. When absent, the check returns ``warn`` with
    a note so operators know to install it instead of silently
    skipping.
    """

    def _check() -> HealthStatus:
        try:
            import psutil  # type: ignore[import-not-found]
        except ImportError:
            return HealthStatus(
                name=name,
                status="warn",
                detail="psutil not installed; memory check skipped",
            )
        try:
            rss_mb = psutil.Process().memory_info().rss / (1024 * 1024)
            if rss_mb > max_rss_mb:
                return HealthStatus(
                    name=name,
                    status="fail",
                    detail=f"RSS {rss_mb:.0f}MB > limit {max_rss_mb}MB",
                    metadata={"rss_mb": round(rss_mb, 2)},
                )
            return HealthStatus(
                name=name,
                status="pass",
                metadata={"rss_mb": round(rss_mb, 2)},
            )
        except (OSError, ImportError, TypeError, ValueError) as exc:
            return HealthStatus(
                name=name,
                status="fail",
                detail=f"{type(exc).__name__}: {exc}",
            )

    return HealthCheck(name=name, check=_check, kind="liveness")


# ── FastAPI adapter ─────────────────────────────────────────


def create_probe_router(registry: HealthRegistry) -> Any:
    """Return a FastAPI ``APIRouter`` exposing ``/livez`` and ``/readyz``.

    Lazy-imports FastAPI so the module stays usable in environments
    without the web stack.
    """
    try:
        from fastapi import APIRouter
        from fastapi.responses import JSONResponse, Response
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "fastapi not installed; install `echo-os[serve]`",
        ) from exc

    import json

    router = APIRouter(tags=["health"])

    def _respond(result: dict[str, Any]) -> Response:
        status_code = 200 if result["status"] != "fail" else 503
        return Response(
            content=json.dumps(result, ensure_ascii=False),
            media_type="application/json",
            status_code=status_code,
        )

    # response_class=JSONResponse tells FastAPI the return is a
    # pre-built Response object — skip Pydantic response-model
    # schema generation (which trips a forward-ref error on the
    # bare ``Response`` annotation under pydantic v2).
    @router.get("/livez", response_class=JSONResponse)
    def livez():
        return _respond(registry.probe(kind="liveness"))

    @router.get("/readyz", response_class=JSONResponse)
    def readyz():
        return _respond(registry.probe(kind="readiness"))

    return router


__all__ = [
    "HealthCheck",
    "HealthKind",
    "HealthRegistry",
    "HealthStatus",
    "HealthStatusValue",
    "create_probe_router",
    "disk_check",
    "effect_store_check",
    "journal_check",
    "memory_check",
    "redis_check",
]
