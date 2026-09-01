"""Thread-safe, source-agnostic real-time quote distribution core.

``QuoteHub`` deliberately knows nothing about credentials, WebSockets, HTTP or
MCP.  An upstream only needs to expose the small callback-shaped
``QuotePushSource`` protocol.  ``CallbackQuoteSourceAdapter`` translates that
small protocol to ``LivePushClient``'s event-based callback API without making
this module import or otherwise depend on the platform client.

The hub owns four jobs:

* merge all downstream symbol subscriptions into one upstream subscription;
* normalize vendor payloads into one stable quote schema;
* fan changes out through bounded per-subscriber queues; and
* fail over between ordered sources without allowing stale quotes to look live.

It uses only the Python standard library and is safe to call from upstream
callback threads, REST request threads and SSE response workers concurrently.
"""

from __future__ import annotations

import queue
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

QuoteCallback = Callable[[str, Any], None]


@runtime_checkable
class QuotePushSource(Protocol):
    """Minimal protocol implemented by a push source.

    A polling/MCP provider can implement this directly.  The callback retains
    LivePush's convenient ``callback(event, data)`` shape while symbol updates
    and transport-specific event names stay outside the hub.
    """

    def subscribe(self, codes: Sequence[str], callback: QuoteCallback) -> None:
        """Add ``codes`` to the set delivered to ``callback``."""

        ...

    def unsubscribe(self, codes: Sequence[str], callback: QuoteCallback) -> None:
        """Remove ``codes`` from the set delivered to ``callback``."""

        ...


class CallbackQuoteSourceAdapter:
    """Adapt an event-based push client (including ``LivePushClient``).

    The wrapped object is intentionally typed as ``Any``: structural checks
    happen at this narrow boundary and importing the concrete platform client
    would defeat the protocol separation.
    """

    def __init__(self, client: Any, *, event: str = "kLineRealTime") -> None:
        self._client = client
        self._event = event
        self._lock = threading.Lock()
        self._codes: dict[QuoteCallback, set[str]] = {}

    def subscribe(self, codes: Sequence[str], callback: QuoteCallback) -> None:
        with self._lock:
            merged = set(self._codes.get(callback, set()))
            merged.update(codes)
            self._client.subscribe(self._event, sorted(merged), callback)
            self._codes[callback] = merged

    def unsubscribe(self, codes: Sequence[str], callback: QuoteCallback) -> None:
        with self._lock:
            remaining = set(self._codes.get(callback, set()))
            remaining.difference_update(codes)
            if remaining:
                # LivePush's subscribe replaces the event parameter list.
                self._client.subscribe(self._event, sorted(remaining), callback)
                self._codes[callback] = remaining
            else:
                self._client.unsubscribe(self._event, callback)
                self._codes.pop(callback, None)


class PollingQuoteSource:
    """Wrap a batch snapshot callable as a push-shaped fallback source.

    The worker exists only while at least one callback owns codes.  All codes
    are fetched once per interval as one union, so downstream user count never
    multiplies upstream requests.
    """

    def __init__(
        self,
        fetcher: Callable[[list[str]], Any],
        *,
        interval: float = 3.0,
        event: str = "kLineRealTime",
    ) -> None:
        if interval <= 0:
            raise ValueError("poll interval must be positive")
        self._fetcher = fetcher
        self._interval = float(interval)
        self._event = event
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._codes: dict[QuoteCallback, set[str]] = {}
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def subscribe(self, codes: Sequence[str], callback: QuoteCallback) -> None:
        started = False
        with self._lock:
            merged = set(self._codes.get(callback, set()))
            merged.update(codes)
            self._codes[callback] = merged
            if not self.running:
                self._thread = threading.Thread(
                    target=self._run,
                    name="paper-trading-quote-poller",
                    daemon=True,
                )
                self._thread.start()
                started = True
        if not started:
            self._wake.set()

    def unsubscribe(self, codes: Sequence[str], callback: QuoteCallback) -> None:
        with self._lock:
            remaining = set(self._codes.get(callback, set()))
            remaining.difference_update(codes)
            if remaining:
                self._codes[callback] = remaining
            else:
                self._codes.pop(callback, None)
        self._wake.set()

    def _run(self) -> None:
        while True:
            with self._lock:
                callbacks = {
                    callback: frozenset(codes) for callback, codes in self._codes.items() if codes
                }
                union = sorted({code for codes in callbacks.values() for code in codes})
                if not callbacks:
                    self._thread = None
                    return
            try:
                payload = {"data": self._fetcher(union)}
            except Exception as exc:  # noqa: BLE001 - provider isolation boundary
                payload = {"data": [], "error": str(exc)[:300]}
            for callback in callbacks:
                try:
                    callback(self._event, payload)
                except Exception:
                    # A consumer callback cannot stop the shared poller.
                    continue
            self._wake.wait(self._interval)
            self._wake.clear()


QUOTE_FIELDS: tuple[str, ...] = (
    "code",
    "name",
    "market",
    "exchange",
    "state",
    "price",
    "change_pct",
    "change",
    "open",
    "high",
    "low",
    "prev_close",
    "volume",
    "amount",
    "turnover",
    "amplitude",
    "pe",
    "pb",
    "bids",
    "asks",
    "source",
    "source_ts",
    "received_at",
    "seq",
    "stale",
)


def _utc_iso(epoch: float) -> str:
    return (
        datetime.fromtimestamp(epoch, tz=UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _source_timestamp(value: Any) -> str:
    """Return a JSON-safe source timestamp without inventing vendor time."""

    if value in (None, ""):
        return ""
    if isinstance(value, datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return dt.isoformat(timespec="milliseconds")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        epoch = float(value)
        if epoch > 10_000_000_000:  # common millisecond Unix timestamp
            epoch /= 1000.0
        try:
            return _utc_iso(epoch)
        except (OverflowError, OSError, ValueError):
            return str(value)
    return str(value)


def _canonical_code(value: Any, exchange: Any = "") -> str:
    code = str(value or "").strip()
    if not code:
        return ""
    if "." in code:
        base, suffix = code.rsplit(".", 1)
        return f"{base.strip()}.{suffix.strip().lower()}"
    exchange_text = str(exchange or "").strip().lower()
    suffixes = {
        "sh": "sh",
        "sse": "sh",
        "xshg": "sh",
        "sz": "sz",
        "sze": "sz",
        "szse": "sz",
        "xshe": "sz",
        "bj": "bj",
        "bse": "bj",
        "xbje": "bj",
    }
    suffix = suffixes.get(exchange_text, "")
    return f"{code}.{suffix}" if suffix else code


def _base_code(code: str) -> str:
    return code.rsplit(".", 1)[0]


def _pick(raw: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in raw and raw[name] is not None:
            return raw[name]
    return default


def _extract_quote_rows(payload: Any) -> list[Mapping[str, Any]]:
    """Accept LivePush, normalized, or adapter payload shapes."""

    if isinstance(payload, (list, tuple)):
        return [row for row in payload if isinstance(row, Mapping)]
    if not isinstance(payload, Mapping):
        return []

    nested = payload.get("data")
    if isinstance(nested, (list, tuple)):
        return [row for row in nested if isinstance(row, Mapping)]
    if isinstance(nested, Mapping):
        rows = _extract_quote_rows(nested)
        if rows:
            return rows

    # A quote response wrapper often also has ``code``; require a quote-like
    # field before treating the mapping itself as one row.
    if any(
        key in payload
        for key in (
            "stockCode",
            "symbol",
            "price",
            "currentPrice",
            "last",
            "lastPrice",
        )
    ):
        return [payload]
    return []


def normalize_quote(
    raw: Mapping[str, Any],
    *,
    source: str,
    received_epoch: float,
    seq: int,
) -> dict[str, Any]:
    """Normalize one vendor row into the stable public quote schema."""

    exchange = _pick(raw, "exchange", "exchangeType", "exchange_code", default="")
    code = _canonical_code(
        _pick(raw, "code", "stockCode", "symbol", "securityCode", default=""), exchange
    )
    source_ts = _pick(
        raw,
        "source_ts",
        "ts",
        "lastUpdateDate",
        "updateTime",
        "timestamp",
        "time",
        default="",
    )
    result: dict[str, Any] = {
        "code": code,
        "name": _pick(raw, "name", "stockName", "securityName", default=""),
        "market": _pick(raw, "market", "marketType", default=""),
        "exchange": exchange,
        "state": _pick(raw, "state", "stockState", "status", default=""),
        "price": _pick(raw, "price", "currentPrice", "last", "lastPrice"),
        "change_pct": _pick(raw, "change_pct", "stockIncrease", "pctChange", "changePct"),
        "change": _pick(raw, "change", "stockRiseFall", "priceChange"),
        "open": _pick(raw, "open", "openPrice"),
        "high": _pick(raw, "high", "highPrice"),
        "low": _pick(raw, "low", "lowPrice"),
        "prev_close": _pick(raw, "prev_close", "yClose", "preClose", "previousClose"),
        "volume": _pick(raw, "volume", "vol"),
        "amount": _pick(raw, "amount", "turnoverAmount"),
        "turnover": _pick(raw, "turnover", "exchangeRate", "turnoverRate"),
        "amplitude": _pick(raw, "amplitude"),
        "pe": _pick(raw, "pe", "peRatio"),
        "pb": _pick(raw, "pb", "pbRatio"),
        "bids": list(_pick(raw, "bids", "tenGearBuy", default=[]) or []),
        "asks": list(_pick(raw, "asks", "tenGearSell", default=[]) or []),
        "source": source,
        "source_ts": _source_timestamp(source_ts),
        "received_at": _utc_iso(received_epoch),
        "seq": seq,
        "stale": False,
    }
    # Keep the public contract exact and deterministic.
    return {field_name: result[field_name] for field_name in QUOTE_FIELDS}


@dataclass
class _SourceHealth:
    name: str
    source: QuotePushSource
    priority: int
    state: str = "standby"
    consecutive_failures: int = 0
    recovery_successes: int = 0
    recovery_started_epoch: float = 0.0
    total_failures: int = 0
    total_successes: int = 0
    last_success_epoch: float = 0.0
    last_failure_epoch: float = 0.0
    last_failure_check_epoch: float = 0.0
    last_error: str = ""
    attached_codes: tuple[str, ...] | None = None
    callback: QuoteCallback | None = None


class QuoteSubscription:
    """One downstream consumer with a bounded, latest-wins queue."""

    def __init__(
        self,
        subscriber_id: str,
        codes: Iterable[str],
        *,
        max_queue: int,
        on_close: Callable[[str], None],
    ) -> None:
        self.id = subscriber_id
        self._codes = frozenset(codes)
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=max_queue)
        self._on_close = on_close
        self._lock = threading.Lock()
        self._closed = False
        self._dropped = 0

    @property
    def codes(self) -> frozenset[str]:
        with self._lock:
            return self._codes

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    @property
    def dropped(self) -> int:
        with self._lock:
            return self._dropped

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    @property
    def max_queue(self) -> int:
        return self._queue.maxsize

    def get(self, timeout: float | None = None) -> dict[str, Any]:
        """Read the next REST/SSE-ready event."""

        if timeout is None:
            return self._queue.get()
        return self._queue.get(timeout=timeout)

    def get_nowait(self) -> dict[str, Any]:
        return self._queue.get_nowait()

    def drain(self, limit: int | None = None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        while limit is None or len(items) < limit:
            try:
                items.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return items

    def close(self) -> None:
        """Idempotently remove this subscription from its hub."""

        self._on_close(self.id)

    def _set_codes(self, codes: Iterable[str]) -> None:
        with self._lock:
            self._codes = frozenset(codes)

    def _offer(self, event: dict[str, Any]) -> None:
        with self._lock:
            if self._closed:
                return
        try:
            self._queue.put_nowait(event)
            return
        except queue.Full:
            pass
        # Quotes are state, not an audit log: retain the newest event and drop
        # the oldest one for a slow client.  The counter remains observable.
        with suppress(queue.Empty):  # another consumer may have drained it
            self._queue.get_nowait()
        with self._lock:
            self._dropped += 1
            if self._closed:
                return
        try:
            self._queue.put_nowait(event)
        except queue.Full:  # concurrent producer won the freed slot
            with self._lock:
                self._dropped += 1

    def _mark_closed(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        # Wake a blocking SSE worker.  This event follows the same JSON-ready
        # shape as other hub control events.
        try:
            self._queue.put_nowait({"type": "closed", "subscriber_id": self.id})
        except queue.Full:
            with suppress(queue.Empty):
                self._queue.get_nowait()
            with suppress(queue.Full):
                self._queue.put_nowait({"type": "closed", "subscriber_id": self.id})


class QuoteHub:
    """Merge, normalize, fail over and fan out real-time quote streams."""

    def __init__(
        self,
        sources: Mapping[str, QuotePushSource],
        *,
        primary: str | None = None,
        failure_threshold: int = 3,
        recovery_threshold: int = 3,
        stale_after: float = 12.0,
        health_check_interval: float = 1.0,
        subscriber_queue_size: int = 50,
        max_subscribers: int = 50,
        max_codes_per_subscriber: int = 100,
        max_union_codes: int = 1000,
        primary_recovery_seconds: float = 0.0,
        health_check_enabled: Callable[[], bool] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not sources:
            raise ValueError("QuoteHub requires at least one source")
        if failure_threshold < 1 or recovery_threshold < 1:
            raise ValueError("health thresholds must be positive")
        if stale_after <= 0 or health_check_interval <= 0:
            raise ValueError("health timing values must be positive")
        if subscriber_queue_size < 1:
            raise ValueError("subscriber_queue_size must be positive")
        if max_subscribers < 1 or max_codes_per_subscriber < 1 or max_union_codes < 1:
            raise ValueError("subscription limits must be positive")
        if max_union_codes < max_codes_per_subscriber:
            raise ValueError("max_union_codes must cover one subscriber")
        if primary_recovery_seconds < 0:
            raise ValueError("primary_recovery_seconds must not be negative")

        ordered_sources = list(sources.items())
        primary_name = primary or ordered_sources[0][0]
        if primary_name not in sources:
            raise ValueError(f"unknown primary source: {primary_name}")
        ordered_sources.sort(key=lambda item: 0 if item[0] == primary_name else 1)

        self._clock = clock
        self._failure_threshold = failure_threshold
        self._recovery_threshold = recovery_threshold
        self._stale_after = float(stale_after)
        self._health_check_interval = float(health_check_interval)
        self._subscriber_queue_size = subscriber_queue_size
        self._max_subscribers = int(max_subscribers)
        self._max_codes_per_subscriber = int(max_codes_per_subscriber)
        self._max_union_codes = int(max_union_codes)
        self._primary_recovery_seconds = float(primary_recovery_seconds)
        self._health_check_enabled = health_check_enabled or (lambda: True)
        self._lock = threading.RLock()
        self._sync_lock = threading.Lock()
        self._stop = threading.Event()
        self._watchdog: threading.Thread | None = None
        self._primary = primary_name
        self._priority = [name for name, _source in ordered_sources]
        self._active = primary_name
        self._active_since = self._clock()
        self._sequence = 0
        self._switch_count = 0
        self._last_switch_epoch = 0.0
        self._last_switch_reason = ""
        self._quotes: dict[str, dict[str, Any]] = {}
        self._quote_received_epoch: dict[str, float] = {}
        self._subscriptions: dict[str, QuoteSubscription] = {}
        self._ref_counts: dict[str, int] = {}
        self._sources: dict[str, _SourceHealth] = {}

        for priority, (name, source) in enumerate(ordered_sources):
            callback: QuoteCallback = self._make_source_callback(name)
            self._sources[name] = _SourceHealth(
                name=name,
                source=source,
                priority=priority,
                state="healthy" if name == primary_name else "standby",
                callback=callback,
            )

    @property
    def running(self) -> bool:
        thread = self._watchdog
        return thread is not None and thread.is_alive()

    @property
    def active_source(self) -> str:
        with self._lock:
            return self._active

    def start(self) -> None:
        """Start the lightweight stale-feed watchdog; idempotent."""

        with self._lock:
            if self.running:
                return
            self._stop.clear()
            self._watchdog = threading.Thread(
                target=self._watchdog_loop,
                name="paper-trading-quote-hub",
                daemon=True,
            )
            self._watchdog.start()
        self._sync_upstreams()

    def stop(self, *, detach: bool = False) -> None:
        """Stop health checks; optionally detach every upstream callback."""

        self._stop.set()
        thread = self._watchdog
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(1.0, self._health_check_interval * 2))
        with self._lock:
            if self._watchdog is thread and (thread is None or not thread.is_alive()):
                self._watchdog = None
            if detach:
                subscriptions = list(self._subscriptions.values())
                self._subscriptions.clear()
                self._ref_counts.clear()
            else:
                subscriptions = []
        for subscription in subscriptions:
            subscription._mark_closed()
        if detach:
            self._sync_upstreams()

    def close(self) -> None:
        """Stop the watchdog and detach all downstream/upstream subscriptions."""

        self.stop(detach=True)

    # ------------------------------------------------------------------
    # Downstream API (REST/SSE consumers)

    def subscribe(
        self,
        codes: Iterable[str],
        *,
        subscriber_id: str | None = None,
        queue_size: int | None = None,
        replay: bool = True,
    ) -> QuoteSubscription:
        """Create or replace one filtered downstream subscription.

        Reusing ``subscriber_id`` updates its code set atomically instead of
        increasing reference counts twice (useful during an SSE reconnect).
        """

        normalized = self._normalize_codes(codes)
        if not normalized:
            raise ValueError("at least one quote code is required")
        if len(normalized) > self._max_codes_per_subscriber:
            raise ValueError(
                f"at most {self._max_codes_per_subscriber} quote codes are allowed per subscriber"
            )
        sid = subscriber_id or uuid.uuid4().hex
        max_queue = self._subscriber_queue_size if queue_size is None else queue_size
        if max_queue < 1:
            raise ValueError("queue_size must be positive")

        with self._lock:
            had_upstream_codes = bool(self._ref_counts)
            existing = self._subscriptions.get(sid)
            if existing is None and len(self._subscriptions) >= self._max_subscribers:
                raise RuntimeError("quote subscriber capacity reached")
            prospective_codes = set(self._ref_counts)
            if existing is not None:
                for code in existing.codes:
                    if self._ref_counts.get(code, 0) <= 1:
                        prospective_codes.discard(code)
            prospective_codes.update(normalized)
            if len(prospective_codes) > self._max_union_codes:
                raise RuntimeError("quote union code capacity reached")
            if existing is None:
                subscription = QuoteSubscription(
                    sid,
                    normalized,
                    max_queue=max_queue,
                    on_close=self.unsubscribe,
                )
                self._subscriptions[sid] = subscription
                for code in normalized:
                    self._ref_counts[code] = self._ref_counts.get(code, 0) + 1
            else:
                subscription = existing
                old_codes = existing.codes
                for code in old_codes - normalized:
                    self._decrement_ref_locked(code)
                for code in normalized - old_codes:
                    self._ref_counts[code] = self._ref_counts.get(code, 0) + 1
                existing._set_codes(normalized)
            if not had_upstream_codes and self._ref_counts:
                # A hub may sit idle for hours; the silence timer starts only
                # when the first real downstream code is requested.
                self._active_since = self._clock()
                self._sources[self._active].last_failure_check_epoch = 0.0
            replay_event = self._snapshot_event_locked(normalized) if replay else None

        self._sync_upstreams()
        if replay_event is not None and replay_event["quotes"]:
            subscription._offer(replay_event)
        return subscription

    def unsubscribe(self, subscription: str | QuoteSubscription) -> bool:
        """Remove one subscriber and decrement every merged code reference."""

        sid = subscription.id if isinstance(subscription, QuoteSubscription) else str(subscription)
        with self._lock:
            existing = self._subscriptions.pop(sid, None)
            if existing is None:
                return False
            for code in existing.codes:
                self._decrement_ref_locked(code)
        existing._mark_closed()
        self._sync_upstreams()
        return True

    def snapshot(self, codes: Iterable[str] | None = None) -> dict[str, Any]:
        """Return a JSON-ready latest snapshot for REST or SSE replay."""

        normalized = self._normalize_codes(codes) if codes is not None else None
        with self._lock:
            return self._snapshot_event_locked(normalized)

    def status(self) -> dict[str, Any]:
        """Return a JSON-ready operational and health summary."""

        now = self._clock()
        with self._lock:
            stale_quotes = sum(
                1 for code, quote in self._quotes.items() if self._is_stale_locked(code, quote, now)
            )
            active_health = self._sources[self._active]
            age_ms = (
                max(0, int((now - active_health.last_success_epoch) * 1000))
                if active_health.last_success_epoch
                else None
            )
            degraded = (
                self._active != self._primary
                or active_health.state not in {"healthy", "standby"}
                or bool(stale_quotes)
            )
            state = (
                "idle"
                if not self._ref_counts
                else "stale"
                if stale_quotes and active_health.last_success_epoch
                else "fallback"
                if self._active != self._primary
                else "degraded"
                if degraded
                else "live"
            )
            return {
                "running": self.running,
                "primary_source": self._primary,
                "active_source": self._active,
                "sequence": self._sequence,
                "quote_count": len(self._quotes),
                "stale_quote_count": stale_quotes,
                "state": state,
                "degraded": degraded,
                "age_ms": age_ms,
                "subscriber_count": len(self._subscriptions),
                "subscribed_code_count": len(self._ref_counts),
                "subscribed_codes": sorted(self._ref_counts),
                "ref_counts": dict(sorted(self._ref_counts.items())),
                "switch_count": self._switch_count,
                "last_switch_at": _utc_iso(self._last_switch_epoch)
                if self._last_switch_epoch
                else "",
                "last_switch_reason": self._last_switch_reason,
                "stale_after_seconds": self._stale_after,
                "limits": {
                    "max_subscribers": self._max_subscribers,
                    "max_codes_per_subscriber": self._max_codes_per_subscriber,
                    "max_union_codes": self._max_union_codes,
                    "subscriber_queue_size": self._subscriber_queue_size,
                },
                "sources": {
                    name: {
                        "priority": record.priority,
                        "active": name == self._active,
                        "state": record.state,
                        "attached": record.attached_codes is not None,
                        "subscribed_codes": list(record.attached_codes or ()),
                        "consecutive_failures": record.consecutive_failures,
                        "recovery_successes": record.recovery_successes,
                        "total_failures": record.total_failures,
                        "total_successes": record.total_successes,
                        "last_success_at": _utc_iso(record.last_success_epoch)
                        if record.last_success_epoch
                        else "",
                        "last_failure_at": _utc_iso(record.last_failure_epoch)
                        if record.last_failure_epoch
                        else "",
                        "last_error": record.last_error,
                    }
                    for name, record in self._sources.items()
                },
                "subscribers": {
                    sid: {
                        "codes": sorted(subscription.codes),
                        "pending": subscription.pending,
                        "max_queue": subscription.max_queue,
                        "dropped": subscription.dropped,
                    }
                    for sid, subscription in self._subscriptions.items()
                },
            }

    # ------------------------------------------------------------------
    # Upstream callback / health API

    def ingest(self, event: str, data: Any, *, source: str) -> int:
        """Accept a ``LivePushClient``-style callback payload.

        Healthy inactive-source packets are recovery probes.  They advance the
        stable-recovery counter, but never overwrite or fan out the active
        source until the state machine has switched back.
        """

        if source not in self._sources:
            raise KeyError(f"unknown quote source: {source}")
        rows = _extract_quote_rows(data)
        if not rows:
            error = data.get("error") if isinstance(data, Mapping) else ""
            self.report_failure(source, str(error or "empty quote payload"))
            return 0

        self.report_success(source)
        received_epoch = self._clock()
        with self._lock:
            if source != self._active:
                return 0
            normalized: list[dict[str, Any]] = []
            for row in rows:
                self._sequence += 1
                quote = normalize_quote(
                    row,
                    source=source,
                    received_epoch=received_epoch,
                    seq=self._sequence,
                )
                code = quote["code"]
                if not code:
                    continue
                self._quotes[code] = quote
                self._quote_received_epoch[code] = received_epoch
                normalized.append(dict(quote))
            deliveries = self._deliveries_locked(normalized, received_epoch)

        for subscription, delivery in deliveries:
            subscription._offer(delivery)
        return len(normalized)

    def report_failure(self, source: str, error: str = "") -> bool:
        """Record one consecutive source failure and fail over when open.

        Returns ``True`` when this call changed the active source.
        """

        now = self._clock()
        with self._lock:
            record = self._require_source_locked(source)
            record.consecutive_failures += 1
            record.recovery_successes = 0
            record.recovery_started_epoch = 0.0
            record.total_failures += 1
            record.last_failure_epoch = now
            record.last_error = str(error)[:300]
            record.state = (
                "open" if record.consecutive_failures >= self._failure_threshold else "degraded"
            )
            changed = False
            deliveries: list[tuple[QuoteSubscription, dict[str, Any]]] = []
            if source == self._active and record.state == "open":
                next_source = self._next_available_source_locked(source)
                if next_source is not None:
                    deliveries = self._switch_locked(
                        next_source,
                        reason=f"{source} failed {record.consecutive_failures} times",
                        now=now,
                    )
                    changed = True

        self._sync_upstreams()
        for subscription, delivery in deliveries:
            subscription._offer(delivery)
        return changed

    def report_success(self, source: str) -> bool:
        """Record health and stably fail back to a recovered higher source."""

        now = self._clock()
        with self._lock:
            record = self._require_source_locked(source)
            was_unhealthy = record.state in {"open", "degraded", "recovering", "standby"}
            record.total_successes += 1
            record.last_success_epoch = now
            record.last_error = ""
            record.consecutive_failures = 0
            if was_unhealthy:
                if not record.recovery_started_epoch:
                    record.recovery_started_epoch = now
                record.recovery_successes += 1
                record.state = (
                    "healthy"
                    if record.recovery_successes >= self._recovery_threshold
                    else "recovering"
                )
            else:
                if not record.recovery_started_epoch:
                    record.recovery_started_epoch = now
                record.recovery_successes = self._recovery_threshold
                record.state = "healthy"

            changed = False
            deliveries: list[tuple[QuoteSubscription, dict[str, Any]]] = []
            active_priority = self._sources[self._active].priority
            stable_for = now - record.recovery_started_epoch
            if (
                record.priority < active_priority
                and record.state == "healthy"
                and stable_for >= self._primary_recovery_seconds
            ):
                deliveries = self._switch_locked(
                    source,
                    reason=f"{source} recovered stably",
                    now=now,
                )
                changed = True

        self._sync_upstreams()
        for subscription, delivery in deliveries:
            subscription._offer(delivery)
        return changed

    def check_health(self, now: float | None = None) -> dict[str, Any]:
        """Evaluate active-feed silence and advance the failure breaker.

        Calling this method is sufficient for embedding environments that
        already have a scheduler.  ``start()`` runs the same check in a daemon
        watchdog for stand-alone use.
        """

        checked_at = self._clock() if now is None else float(now)
        source_to_fail: str | None = None
        retry_unattached = False
        try:
            health_enabled = bool(self._health_check_enabled())
        except Exception:  # noqa: BLE001 - a calendar failure must not trip a source
            health_enabled = False
        with self._lock:
            if self._ref_counts and health_enabled:
                record = self._sources[self._active]
                freshness_epoch = max(record.last_success_epoch, self._active_since)
                stale = checked_at - freshness_epoch > self._stale_after
                due = checked_at - record.last_failure_check_epoch >= self._health_check_interval
                if stale and due:
                    record.last_failure_check_epoch = checked_at
                    source_to_fail = self._active
                retry_after = max(
                    self._health_check_interval,
                    min(self._stale_after, 10.0),
                )
                retry_unattached = any(
                    source.attached_codes is None
                    and source.last_failure_epoch > 0
                    and checked_at - source.last_failure_epoch >= retry_after
                    for name, source in self._sources.items()
                    if name in self._desired_source_names_locked()
                )
        if source_to_fail is not None:
            self.report_failure(
                source_to_fail,
                f"no quote update for more than {self._stale_after:g}s",
            )
        elif retry_unattached:
            # A source that failed during the subscribe handshake has no
            # callback through which it can announce recovery. Retry at a
            # bounded cadence so a healthy fallback does not make failback
            # impossible or hammer the failed provider every watchdog tick.
            self._sync_upstreams()
        return self.status()

    # ------------------------------------------------------------------
    # Internal helpers

    def _make_source_callback(self, source: str) -> QuoteCallback:
        def _callback(event: str, data: Any) -> None:
            self.ingest(event, data, source=source)

        return _callback

    def _normalize_codes(self, codes: Iterable[str] | None) -> frozenset[str]:
        if codes is None:
            return frozenset()
        normalized = {_canonical_code(code) for code in codes}
        normalized.discard("")
        return frozenset(normalized)

    def _decrement_ref_locked(self, code: str) -> None:
        count = self._ref_counts.get(code, 0) - 1
        if count > 0:
            self._ref_counts[code] = count
        else:
            self._ref_counts.pop(code, None)

    def _require_source_locked(self, source: str) -> _SourceHealth:
        try:
            return self._sources[source]
        except KeyError as exc:
            raise KeyError(f"unknown quote source: {source}") from exc

    def _is_stale_locked(
        self, code: str, quote: Mapping[str, Any], now: float | None = None
    ) -> bool:
        checked_at = self._clock() if now is None else now
        received = self._quote_received_epoch.get(code, 0.0)
        return (
            quote.get("source") != self._active
            or not received
            or checked_at - received > self._stale_after
        )

    def _matching_quote_locked(self, requested: str) -> tuple[str, dict[str, Any]] | None:
        quote = self._quotes.get(requested)
        if quote is not None:
            return requested, quote
        if "." not in requested:
            matches = [
                (code, candidate)
                for code, candidate in self._quotes.items()
                if _base_code(code) == requested
            ]
            if len(matches) == 1:
                return matches[0]
        return None

    def _snapshot_event_locked(self, codes: frozenset[str] | None) -> dict[str, Any]:
        now = self._clock()
        if codes is None:
            selected = list(self._quotes.items())
        else:
            selected = []
            seen: set[str] = set()
            for requested in sorted(codes):
                match = self._matching_quote_locked(requested)
                if match is not None and match[0] not in seen:
                    selected.append(match)
                    seen.add(match[0])
        quotes: list[dict[str, Any]] = []
        for code, quote in sorted(selected, key=lambda item: item[0]):
            copied = dict(quote)
            copied["stale"] = self._is_stale_locked(code, quote, now)
            quotes.append(copied)
        return {
            "type": "snapshot",
            "seq": self._sequence,
            "source": self._active,
            "received_at": _utc_iso(now),
            "quotes": quotes,
        }

    @staticmethod
    def _subscription_matches(codes: frozenset[str], quote_code: str) -> bool:
        if quote_code in codes:
            return True
        base = _base_code(quote_code)
        return base in codes

    def _deliveries_locked(
        self, quotes: Sequence[dict[str, Any]], received_epoch: float
    ) -> list[tuple[QuoteSubscription, dict[str, Any]]]:
        deliveries: list[tuple[QuoteSubscription, dict[str, Any]]] = []
        for subscription in self._subscriptions.values():
            codes = subscription.codes
            selected = [
                quote for quote in quotes if self._subscription_matches(codes, quote["code"])
            ]
            if not selected:
                continue
            deliveries.append(
                (
                    subscription,
                    {
                        "type": "quotes",
                        "seq": selected[-1]["seq"],
                        "source": self._active,
                        "received_at": _utc_iso(received_epoch),
                        "quotes": [dict(quote) for quote in selected],
                    },
                )
            )
        return deliveries

    def _stale_deliveries_locked(
        self, *, reason: str, now: float
    ) -> list[tuple[QuoteSubscription, dict[str, Any]]]:
        deliveries: list[tuple[QuoteSubscription, dict[str, Any]]] = []
        for subscription in self._subscriptions.values():
            selected: list[dict[str, Any]] = []
            for code, quote in self._quotes.items():
                if self._subscription_matches(subscription.codes, code):
                    copied = dict(quote)
                    copied["stale"] = True
                    selected.append(copied)
            deliveries.append(
                (
                    subscription,
                    {
                        "type": "source_changed",
                        "seq": self._sequence,
                        "source": self._active,
                        "received_at": _utc_iso(now),
                        "reason": reason,
                        "quotes": selected,
                    },
                )
            )
        return deliveries

    def _next_available_source_locked(self, failed_source: str) -> str | None:
        failed_priority = self._sources[failed_source].priority
        after = self._priority[failed_priority + 1 :]
        before = self._priority[:failed_priority]
        for name in after:
            if self._sources[name].state != "open":
                return name
        # A higher-priority source may only reclaim traffic after completing
        # the stable recovery threshold; "recovering" is not enough.
        for name in before:
            if self._sources[name].state == "healthy":
                return name
        return None

    def _switch_locked(
        self, new_source: str, *, reason: str, now: float
    ) -> list[tuple[QuoteSubscription, dict[str, Any]]]:
        if new_source == self._active:
            return []
        self._active = new_source
        self._active_since = now
        self._switch_count += 1
        self._last_switch_epoch = now
        self._last_switch_reason = reason
        target = self._sources[new_source]
        if target.state in {"standby", "degraded"}:
            target.state = "recovering"
            target.recovery_successes = 0
        # Every cached value belongs to the previous source until refreshed.
        return self._stale_deliveries_locked(reason=reason, now=now)

    def _desired_source_names_locked(self) -> set[str]:
        if not self._ref_counts:
            return set()
        active_priority = self._sources[self._active].priority
        # Keep higher-priority failed sources attached so their successful
        # callbacks can prove stable recovery.  Lower-priority sources detach
        # after a failback to avoid needless upstream load.
        return set(self._priority[: active_priority + 1])

    def _sync_upstreams(self) -> None:
        """Reconcile external subscriptions without holding the hub lock."""

        with self._sync_lock:
            # A concurrent subscriber may change the desired union while an
            # external call is in flight, so reconcile until one pass is clean.
            for _attempt in range(4):
                with self._lock:
                    desired_names = self._desired_source_names_locked()
                    desired_codes = tuple(sorted(self._ref_counts))
                    actions: list[tuple[str, _SourceHealth, tuple[str, ...]]] = []
                    for name, record in self._sources.items():
                        if name in desired_names:
                            current = set(record.attached_codes or ())
                            desired = set(desired_codes)
                            removed = tuple(sorted(current - desired))
                            added = tuple(sorted(desired - current))
                            if removed:
                                actions.append(("unsubscribe", record, removed))
                            if added:
                                actions.append(("subscribe", record, added))
                        elif record.attached_codes is not None:
                            actions.append(("unsubscribe", record, record.attached_codes))
                if not actions:
                    return

                failed = False
                switched = False
                deliveries: list[tuple[QuoteSubscription, dict[str, Any]]] = []
                for action, record, codes in actions:
                    callback = record.callback
                    if callback is None:  # defensive; constructor always sets it
                        continue
                    try:
                        if action == "subscribe":
                            record.source.subscribe(codes, callback)
                        else:
                            record.source.unsubscribe(codes, callback)
                    except Exception as exc:  # noqa: BLE001 - source isolation boundary
                        with self._lock:
                            current = self._sources[record.name]
                            current.last_error = f"subscription sync failed: {exc}"[:300]
                            if action == "subscribe":
                                now = self._clock()
                                current.consecutive_failures += 1
                                current.recovery_successes = 0
                                current.recovery_started_epoch = 0.0
                                current.total_failures += 1
                                current.last_failure_epoch = now
                                current.state = (
                                    "open"
                                    if current.consecutive_failures >= self._failure_threshold
                                    else "degraded"
                                )
                                if current.name == self._active and current.state == "open":
                                    next_source = self._next_available_source_locked(current.name)
                                    if next_source is not None:
                                        deliveries.extend(
                                            self._switch_locked(
                                                next_source,
                                                reason=(
                                                    f"{current.name} subscription failed "
                                                    f"{current.consecutive_failures} times"
                                                ),
                                                now=now,
                                            )
                                        )
                                        switched = True
                        failed = True
                        continue
                    with self._lock:
                        current = self._sources[record.name]
                        attached = set(current.attached_codes or ())
                        if action == "subscribe":
                            attached.update(codes)
                        else:
                            attached.difference_update(codes)
                        current.attached_codes = tuple(sorted(attached)) if attached else None
                for subscription, delivery in deliveries:
                    subscription._offer(delivery)
                # A source switch changes the desired source set; make one
                # more reconciliation pass so the fallback attaches now.
                if failed and not switched:
                    return

    def _watchdog_loop(self) -> None:
        while not self._stop.wait(self._health_check_interval):
            self.check_health()


__all__ = [
    "CallbackQuoteSourceAdapter",
    "PollingQuoteSource",
    "QUOTE_FIELDS",
    "QuoteHub",
    "QuotePushSource",
    "QuoteSubscription",
    "normalize_quote",
]
