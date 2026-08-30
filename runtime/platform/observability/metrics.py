"""Metrics Registry — zero-dependency Prometheus-compatible counters.

What this is
------------
A tiny in-process metrics registry with three instrument types:

* ``Counter``    — monotonically-increasing value (requests_total, ...)
* ``Gauge``      — arbitrary value that goes up and down (queue_depth, ...)
* ``Histogram``  — observation-bucket sampler (latency_seconds, ...)

All three support **labels** so one metric name can be sliced by
dimension (``status="200"``, ``provider="anthropic"``, ...).

What this isn't
---------------
Not a replacement for OpenTelemetry — the project still uses OTel
for traces. This is a lightweight counter/histogram path for things
that are cheap to count but expensive to emit as spans: skill call
rates, retry counts, rate-limit hits, cache hit ratios.

Prometheus text format
----------------------
``registry.render_prometheus()`` returns a plain-text
Prometheus-style payload suitable for a ``/metrics`` HTTP endpoint::

    # HELP echo_skill_calls_total Total skill invocations
    # TYPE echo_skill_calls_total counter
    echo_skill_calls_total{skill="read_file"} 42
    echo_skill_calls_total{skill="web_search"} 7

    # HELP echo_llm_latency_seconds LLM call latency
    # TYPE echo_llm_latency_seconds histogram
    echo_llm_latency_seconds_bucket{provider="anthropic",le="0.1"} 3
    echo_llm_latency_seconds_bucket{provider="anthropic",le="0.5"} 8
    ...
    echo_llm_latency_seconds_sum{provider="anthropic"} 5.43
    echo_llm_latency_seconds_count{provider="anthropic"} 12

Usage
-----

    from runtime.platform.observability.metrics import MetricsRegistry, get_registry

    reg = get_registry()  # global shared registry
    calls = reg.counter(
        "echo_skill_calls_total", "Total skill invocations",
        labels=["skill"],
    )
    calls.inc(labels={"skill": "read_file"})

    lat = reg.histogram(
        "echo_llm_latency_seconds", "LLM latency",
        labels=["provider"],
    )
    lat.observe(0.42, labels={"provider": "anthropic"})

    # Expose from an HTTP handler:
    @router.get("/metrics")
    def metrics(): return reg.render_prometheus()

Thread safety
-------------
Every instrument holds its own lock. The registry's metric dict is
guarded by an RLock. For typical 10k-req/sec workloads the
contention is negligible because each counter/histogram is its own
hot spot.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from typing import Any

# Default histogram buckets — covers typical LLM / HTTP latencies.
DEFAULT_BUCKETS: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
    60.0,
    math.inf,
)


def _labels_key(labels: dict[str, str] | None) -> tuple[tuple[str, str], ...]:
    """Stable hashable key for a label set (sorted by name)."""
    if not labels:
        return ()
    return tuple(sorted((str(k), str(v)) for k, v in labels.items()))


def _format_labels(labels: dict[str, str] | tuple[tuple[str, str], ...]) -> str:
    """Render a label set as ``{k="v",k2="v2"}`` (Prometheus format)."""
    if isinstance(labels, tuple):
        pairs = labels
    else:
        pairs = tuple(sorted((str(k), str(v)) for k, v in labels.items()))
    if not pairs:
        return ""
    inner = ",".join(f'{k}="{_escape(v)}"' for k, v in pairs)
    return "{" + inner + "}"


def _escape(value: str) -> str:
    """Escape label values per Prometheus text format rules."""
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


# ─── Counter ──────────────────────────────────────────────────


class Counter:
    """Monotonically-increasing numeric value."""

    def __init__(
        self,
        name: str,
        help_text: str,
        labels: list[str] | None = None,
    ) -> None:
        self.name = name
        self.help_text = help_text
        self.label_names = list(labels or [])
        self._values: dict[tuple[tuple[str, str], ...], float] = {}
        self._lock = threading.Lock()

    def inc(
        self,
        amount: float = 1.0,
        *,
        labels: dict[str, str] | None = None,
    ) -> None:
        if amount < 0:
            raise ValueError("Counter can only increase (amount must be >= 0)")
        key = _labels_key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def value(self, labels: dict[str, str] | None = None) -> float:
        with self._lock:
            return self._values.get(_labels_key(labels), 0.0)

    def render(self) -> list[str]:
        out = [
            f"# HELP {self.name} {self.help_text}",
            f"# TYPE {self.name} counter",
        ]
        with self._lock:
            snapshot = dict(self._values)
        for label_key, val in sorted(snapshot.items()):
            out.append(f"{self.name}{_format_labels(label_key)} {_fmt_num(val)}")
        return out


# ─── Gauge ────────────────────────────────────────────────────


class Gauge:
    """Value that can go up and down."""

    def __init__(
        self,
        name: str,
        help_text: str,
        labels: list[str] | None = None,
    ) -> None:
        self.name = name
        self.help_text = help_text
        self.label_names = list(labels or [])
        self._values: dict[tuple[tuple[str, str], ...], float] = {}
        self._lock = threading.Lock()

    def set(
        self,
        value: float,
        *,
        labels: dict[str, str] | None = None,
    ) -> None:
        key = _labels_key(labels)
        with self._lock:
            self._values[key] = float(value)

    def inc(
        self,
        amount: float = 1.0,
        *,
        labels: dict[str, str] | None = None,
    ) -> None:
        key = _labels_key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def dec(
        self,
        amount: float = 1.0,
        *,
        labels: dict[str, str] | None = None,
    ) -> None:
        self.inc(-amount, labels=labels)

    def value(self, labels: dict[str, str] | None = None) -> float:
        with self._lock:
            return self._values.get(_labels_key(labels), 0.0)

    def render(self) -> list[str]:
        out = [
            f"# HELP {self.name} {self.help_text}",
            f"# TYPE {self.name} gauge",
        ]
        with self._lock:
            snapshot = dict(self._values)
        for label_key, val in sorted(snapshot.items()):
            out.append(f"{self.name}{_format_labels(label_key)} {_fmt_num(val)}")
        return out


# ─── Histogram ────────────────────────────────────────────────


@dataclass
class _HistogramSeries:
    """One labeled series inside a histogram."""

    buckets: list[float]  # counts, parallel to bucket bounds
    sum: float = 0.0
    count: int = 0


class Histogram:
    """Observation-bucket sampler with sum and count."""

    def __init__(
        self,
        name: str,
        help_text: str,
        labels: list[str] | None = None,
        buckets: tuple[float, ...] = DEFAULT_BUCKETS,
    ) -> None:
        self.name = name
        self.help_text = help_text
        self.label_names = list(labels or [])
        # Ensure +inf is present at the end.
        bs = list(buckets)
        if not bs or bs[-1] != math.inf:
            bs = list(bs) + [math.inf]
        self.bucket_bounds: list[float] = bs
        self._series: dict[tuple[tuple[str, str], ...], _HistogramSeries] = {}
        self._lock = threading.Lock()

    def observe(
        self,
        value: float,
        *,
        labels: dict[str, str] | None = None,
    ) -> None:
        key = _labels_key(labels)
        with self._lock:
            series = self._series.get(key)
            if series is None:
                series = _HistogramSeries(
                    buckets=[0] * len(self.bucket_bounds),
                )
                self._series[key] = series
            # Cumulative histogram: increment every bucket where
            # bound >= value.
            for i, bound in enumerate(self.bucket_bounds):
                if value <= bound:
                    series.buckets[i] += 1
            series.sum += value
            series.count += 1

    def snapshot(
        self,
        labels: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        key = _labels_key(labels)
        with self._lock:
            series = self._series.get(key)
            if series is None:
                return {
                    "count": 0,
                    "sum": 0.0,
                    "buckets": {b: 0 for b in self.bucket_bounds},
                }
            return {
                "count": series.count,
                "sum": series.sum,
                "buckets": dict(zip(self.bucket_bounds, series.buckets, strict=True)),
            }

    def render(self) -> list[str]:
        out = [
            f"# HELP {self.name} {self.help_text}",
            f"# TYPE {self.name} histogram",
        ]
        with self._lock:
            snapshot = {k: (s.buckets[:], s.sum, s.count) for k, s in self._series.items()}
        for label_key, (buckets, total, count) in sorted(snapshot.items()):
            for bound, c in zip(self.bucket_bounds, buckets, strict=True):
                # Merge le="..." into the label set for the bucket line.
                merged = dict(label_key) if label_key else {}
                merged["le"] = "+Inf" if bound == math.inf else _fmt_num(bound)
                out.append(f"{self.name}_bucket{_format_labels(merged)} {c}")
            out.append(f"{self.name}_sum{_format_labels(label_key)} {_fmt_num(total)}")
            out.append(f"{self.name}_count{_format_labels(label_key)} {count}")
        return out


def _fmt_num(x: float) -> str:
    """Format a number for Prometheus text output."""
    if x == math.inf:
        return "+Inf"
    if x == -math.inf:
        return "-Inf"
    if isinstance(x, int) or x.is_integer():
        return str(int(x))
    return repr(x)


# ─── Registry ────────────────────────────────────────────────


class MetricsRegistry:
    """Container for all metrics in a process.

    Prefer ``get_registry()`` for the shared singleton. Construct
    your own when you want isolation (e.g. tests).
    """

    def __init__(self) -> None:
        self._metrics: dict[str, Counter | Gauge | Histogram] = {}
        self._lock = threading.RLock()

    # ── instrument factories ────────────────────────────────

    def counter(
        self,
        name: str,
        help_text: str = "",
        labels: list[str] | None = None,
    ) -> Counter:
        return self._get_or_create(
            name,
            lambda: Counter(name, help_text, labels),
            Counter,
        )

    def gauge(
        self,
        name: str,
        help_text: str = "",
        labels: list[str] | None = None,
    ) -> Gauge:
        return self._get_or_create(
            name,
            lambda: Gauge(name, help_text, labels),
            Gauge,
        )

    def histogram(
        self,
        name: str,
        help_text: str = "",
        labels: list[str] | None = None,
        buckets: tuple[float, ...] = DEFAULT_BUCKETS,
    ) -> Histogram:
        return self._get_or_create(
            name,
            lambda: Histogram(name, help_text, labels, buckets),
            Histogram,
        )

    def get(self, name: str) -> Counter | Gauge | Histogram | None:
        with self._lock:
            return self._metrics.get(name)

    def clear(self) -> None:
        """Drop every metric. Primarily useful in tests."""
        with self._lock:
            self._metrics.clear()

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._metrics.keys())

    # ── Prometheus text export ──────────────────────────────

    def render_prometheus(self) -> str:
        """Return a Prometheus-compatible text payload."""
        lines: list[str] = []
        with self._lock:
            metrics = list(self._metrics.values())
        for m in sorted(metrics, key=lambda x: x.name):
            lines.extend(m.render())
            lines.append("")  # blank line between metrics
        return "\n".join(lines).rstrip() + "\n"

    # ── internals ───────────────────────────────────────────

    def _get_or_create(
        self,
        name: str,
        factory: Any,
        expected_type: type,
    ) -> Any:
        with self._lock:
            existing = self._metrics.get(name)
            if existing is not None:
                if not isinstance(existing, expected_type):
                    raise TypeError(
                        f"metric {name!r} already registered as "
                        f"{type(existing).__name__}, not {expected_type.__name__}"
                    )
                return existing
            instrument = factory()
            self._metrics[name] = instrument
            return instrument


# ─── Global registry ─────────────────────────────────────────

_GLOBAL_REGISTRY = MetricsRegistry()


def get_registry() -> MetricsRegistry:
    """Return the process-wide shared registry."""
    return _GLOBAL_REGISTRY


__all__ = [
    "DEFAULT_BUCKETS",
    "Counter",
    "Gauge",
    "Histogram",
    "MetricsRegistry",
    "get_registry",
]
