"""Standalone FastAPI service for the shared paper-trading QuoteHub.

This module is deliberately a market-data plane, not a second Echo app.  It
loads one server-owned read-only platform credential, maintains one merged
upstream WebSocket subscription, and exposes only health plus quote APIs.

Authentication is intentionally absent here.  The deployment's Nginx
``auth_request`` gate owns tenant authentication before a request reaches the
loopback-only service.
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import re
import threading
import time
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from datetime import time as datetime_time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from .live import HAS_CRYPTO, PlatformClient
from .live_push import HAS_WEBSOCKETS, LivePushClient
from .quote_hub import (
    CallbackQuoteSourceAdapter,
    PollingQuoteSource,
    QuoteHub,
    normalize_quote,
)
from .upstream_url import secure_upstream_origin

API_PREFIX = "/api/plugins/paper-trading/quotes"
_CODE_PATTERN = re.compile(r"\d{6}")
_SHANGHAI = ZoneInfo("Asia/Shanghai")


def _bounded_int(
    environment: Mapping[str, str],
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = str(environment.get(name, "")).strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _bounded_float(
    environment: Mapping[str, str],
    name: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    raw = str(environment.get(name, "")).strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum:g} and {maximum:g}")
    return value


def _load_secret_file(value: str) -> dict[str, str]:
    """Load one explicit service-owned JSON secret in strict file mode."""

    if not value:
        return {}
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("QUOTE_HUB_SECRET_FILE must be an absolute path")
    try:
        stat = path.stat()
    except OSError as exc:
        raise ValueError(f"QUOTE_HUB_SECRET_FILE is not readable: {path}") from exc
    if not path.is_file():
        raise ValueError(f"QUOTE_HUB_SECRET_FILE is not a regular file: {path}")
    if os.name != "nt" and stat.st_mode & 0o077:
        raise ValueError("QUOTE_HUB_SECRET_FILE must not be accessible by group or others")
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("QUOTE_HUB_SECRET_FILE must contain valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("QUOTE_HUB_SECRET_FILE must contain a JSON object")
    return {str(key): str(value) for key, value in parsed.items() if value is not None}


@dataclass(frozen=True)
class QuoteServiceSettings:
    """Validated standalone-service settings.

    The platform phone/password are accepted only from the explicitly selected
    secret file or the dedicated ``QUOTE_HUB_PLATFORM_*`` environment values.
    They are passed directly to :class:`PlatformClient`; the standalone service
    never falls back to the interactive plugin's home-directory credential.
    """

    upstream_url: str = ""
    phone: str = ""
    password: str = field(default="", repr=False)
    secret_file: str = ""
    state_dir: str = "/var/lib/echo/quote-hub"
    upstream_timeout: float = 12.0
    rest_interval: float = 3.0
    stale_after: float = 12.0
    health_check_interval: float = 1.0
    failure_threshold: int = 3
    recovery_threshold: int = 2
    primary_recovery_seconds: float = 120.0
    queue_size: int = 50
    max_clients: int = 50
    max_codes_per_client: int = 100
    max_union_codes: int = 500
    sse_keepalive: float = 15.0
    sse_max_lifetime: float = 600.0
    readiness_probe_timeout: float = 3.0
    readiness_cache_seconds: float = 30.0

    @classmethod
    def from_env(cls, environment: Mapping[str, str] | None = None) -> QuoteServiceSettings:
        env = os.environ if environment is None else environment
        secret_file = str(env.get("QUOTE_HUB_SECRET_FILE", "")).strip()
        secret = _load_secret_file(secret_file)

        def value(
            env_name: str,
            secret_name: str,
            default: str = "",
            *,
            strip: bool = True,
        ) -> str:
            selected = str(env.get(env_name) or secret.get(secret_name) or default)
            return selected.strip() if strip else selected

        max_codes = _bounded_int(env, "QUOTE_HUB_MAX_CODES_PER_CLIENT", 100, 1, 500)
        max_union = _bounded_int(env, "QUOTE_HUB_MAX_UNION_CODES", 500, 1, 5000)
        if max_union < max_codes:
            raise ValueError(
                "QUOTE_HUB_MAX_UNION_CODES must be at least QUOTE_HUB_MAX_CODES_PER_CLIENT"
            )
        return cls(
            upstream_url=value("QUOTE_HUB_UPSTREAM_URL", "upstream_url"),
            phone=value("QUOTE_HUB_PLATFORM_PHONE", "phone"),
            password=value("QUOTE_HUB_PLATFORM_PASSWORD", "password", strip=False),
            secret_file=secret_file,
            state_dir=value("QUOTE_HUB_STATE_DIR", "state_dir", "/var/lib/echo/quote-hub"),
            upstream_timeout=_bounded_float(env, "QUOTE_HUB_UPSTREAM_TIMEOUT", 12.0, 1.0, 60.0),
            rest_interval=_bounded_float(env, "QUOTE_HUB_REST_INTERVAL", 3.0, 1.0, 30.0),
            stale_after=_bounded_float(env, "QUOTE_HUB_STALE_AFTER", 12.0, 3.0, 300.0),
            health_check_interval=_bounded_float(
                env, "QUOTE_HUB_HEALTH_CHECK_INTERVAL", 1.0, 0.2, 30.0
            ),
            failure_threshold=_bounded_int(env, "QUOTE_HUB_FAILURE_THRESHOLD", 3, 1, 20),
            recovery_threshold=_bounded_int(env, "QUOTE_HUB_RECOVERY_THRESHOLD", 2, 1, 20),
            primary_recovery_seconds=_bounded_float(
                env, "QUOTE_HUB_PRIMARY_RECOVERY_SECONDS", 120.0, 0.0, 3600.0
            ),
            queue_size=_bounded_int(env, "QUOTE_HUB_QUEUE_SIZE", 50, 2, 500),
            max_clients=_bounded_int(env, "QUOTE_HUB_MAX_CLIENTS", 50, 1, 1000),
            max_codes_per_client=max_codes,
            max_union_codes=max_union,
            sse_keepalive=_bounded_float(env, "QUOTE_HUB_SSE_KEEPALIVE", 15.0, 3.0, 60.0),
            sse_max_lifetime=_bounded_float(env, "QUOTE_HUB_SSE_MAX_LIFETIME", 600.0, 60.0, 840.0),
            readiness_probe_timeout=_bounded_float(
                env, "QUOTE_HUB_READINESS_PROBE_TIMEOUT", 3.0, 1.0, 10.0
            ),
            readiness_cache_seconds=_bounded_float(
                env, "QUOTE_HUB_READINESS_CACHE_SECONDS", 30.0, 2.0, 300.0
            ),
        )

    def configuration_issues(self) -> list[str]:
        issues: list[str] = []
        if not secure_upstream_origin(self.upstream_url):
            issues.append("missing trusted HTTPS upstream")
        if not self.phone or not self.password:
            issues.append("missing platform credential")
        if not HAS_CRYPTO:
            issues.append("cryptography dependency unavailable")
        if not HAS_WEBSOCKETS:
            issues.append("websockets dependency unavailable")
        return issues

    @property
    def configured(self) -> bool:
        return not self.configuration_issues()


class _ManagedPushEventClient:
    """Start the lazy WebSocket worker when QuoteHub gains its first code."""

    def __init__(self, push: LivePushClient) -> None:
        self._push = push

    def subscribe(self, event: str, params: list[str], callback: Any) -> None:
        # Register before starting the worker.  This guarantees the first
        # connection sees the desired subscription after its Socket.IO
        # namespace handshake, rather than racing an early cross-thread frame.
        self._push.subscribe(event, params, callback)
        if not self._push.start():
            self._push.unsubscribe(event, callback)
            raise RuntimeError("platform WebSocket source is unavailable")

    def unsubscribe(self, event: str, callback: Any) -> None:
        self._push.unsubscribe(event, callback)


def _is_a_share_session(now: datetime | None = None) -> bool:
    current = now or datetime.now(_SHANGHAI)
    if current.weekday() >= 5:
        return False
    current_time = current.time().replace(tzinfo=None)
    # Include the opening call auction.  Explicit transport errors are reported
    # regardless of this window; the calendar gate only controls silence-based
    # stale detection.
    return datetime_time(9, 15) <= current_time <= datetime_time(11, 30) or datetime_time(
        13, 0
    ) <= current_time <= datetime_time(15, 0)


class QuoteService:
    """Own the standalone client's, push worker's and hub's lifecycles."""

    def __init__(
        self,
        settings: QuoteServiceSettings,
        hub: QuoteHub,
        *,
        push: LivePushClient | None = None,
        upstream_probe: Callable[[], Any] | None = None,
        snapshot_fetcher: Callable[[list[str]], Any] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.settings = settings
        self.hub = hub
        self.push = push
        self._upstream_probe = upstream_probe
        self._snapshot_fetcher = snapshot_fetcher
        self._clock = clock
        self._probe_lock = threading.Lock()
        self._probe_checked_at = 0.0
        self._probe_ok = False
        self._snapshot_lock = threading.Lock()
        self._rest_snapshot_cache: dict[str, dict[str, Any]] = {}
        self._rest_snapshot_cached_at: dict[str, float] = {}

    @property
    def configured(self) -> bool:
        return self.settings.configured

    @property
    def ready(self) -> bool:
        # This is the last observed readiness.  /readyz refreshes an aged probe
        # before returning; passive /health and /status calls never create
        # credentialed network traffic just because the cache TTL elapsed.
        return self.configured and self._probe_ok and bool(self._probe_checked_at)

    def probe_upstream(self, *, force: bool = False) -> bool:
        """Run one bounded real quote probe and cache its readiness result.

        A failed result is cached for at most two seconds so deployment retries
        can recover promptly, while a healthy origin is not hit by every local
        readiness check.  The production probe uses a dedicated client with its
        own short network timeout.
        """

        if not self.configured or self._upstream_probe is None:
            return False
        with self._probe_lock:
            now = self._clock()
            cache_for = (
                self.settings.readiness_cache_seconds
                if self._probe_ok
                else min(2.0, self.settings.readiness_cache_seconds)
            )
            if not force and self._probe_checked_at and now - self._probe_checked_at < cache_for:
                return self._probe_ok
            try:
                result = self._upstream_probe()
                ok = isinstance(result, list) and any(isinstance(row, Mapping) for row in result)
            except Exception:  # noqa: BLE001 - probe details stay out of the public response
                ok = False
            self._probe_checked_at = self._clock()
            self._probe_ok = ok
            return ok

    def snapshot(self, codes: list[str]) -> dict[str, Any]:
        """Return cached WS quotes, filling misses through one coalesced REST call."""

        hub_snapshot = self.hub.snapshot(codes)
        by_code = {
            str(quote.get("code") or ""): dict(quote)
            for quote in hub_snapshot.get("quotes", [])
            if isinstance(quote, Mapping) and quote.get("code")
        }
        wanted = set(codes)
        if (
            wanted
            and wanted.issubset(by_code)
            and not any(by_code[code].get("stale") for code in wanted)
        ):
            return hub_snapshot
        if not wanted:
            return hub_snapshot

        with self._snapshot_lock:
            now = self._clock()
            for code in wanted:
                cached = self._rest_snapshot_cache.get(code)
                cached_at = self._rest_snapshot_cached_at.get(code, 0.0)
                if cached is not None and now - cached_at <= self.settings.rest_interval:
                    by_code[code] = dict(cached)

            missing = {
                code for code in wanted if code not in by_code or bool(by_code[code].get("stale"))
            }
            fetch_error: Exception | None = None
            if missing and self._snapshot_fetcher is not None:
                try:
                    rows = self._snapshot_fetcher(sorted(missing))
                    if not isinstance(rows, list):
                        rows = []
                    received_epoch = self._clock()
                    fetched: dict[str, dict[str, Any]] = {}
                    for row in rows:
                        if not isinstance(row, Mapping):
                            continue
                        quote = normalize_quote(
                            row,
                            source="platform_rest",
                            received_epoch=received_epoch,
                            seq=0,
                        )
                        code = str(quote.get("code") or "")
                        if code not in wanted and "." not in code:
                            code = next(
                                (
                                    wanted_code
                                    for wanted_code in wanted
                                    if wanted_code.startswith(f"{code}.")
                                ),
                                code,
                            )
                            quote["code"] = code
                        if code in wanted:
                            fetched[code] = quote
                    if fetched:
                        self._rest_snapshot_cache.update(fetched)
                        self._rest_snapshot_cached_at.update(dict.fromkeys(fetched, received_epoch))
                        by_code.update(fetched)
                except Exception as exc:  # noqa: BLE001 - route returns safe degradation
                    fetch_error = exc

        selected = [by_code[code] for code in codes if code in by_code]
        if not selected and fetch_error is not None:
            raise RuntimeError("quote upstream snapshot is unavailable") from fetch_error
        if not selected:
            raise RuntimeError("quote upstream returned no requested quotes")
        received_at = max(str(quote.get("received_at") or "") for quote in selected)
        sequence = max(int(quote.get("seq") or 0) for quote in selected)
        sources = {str(quote.get("source") or "") for quote in selected}
        return {
            "type": "snapshot",
            "seq": sequence,
            "source": sources.pop() if len(sources) == 1 else "mixed",
            "received_at": received_at,
            "quotes": selected,
        }

    def public_status(self) -> dict[str, Any]:
        status = dict(self.hub.status())
        status.pop("subscribers", None)
        status.pop("subscribed_codes", None)
        status.pop("ref_counts", None)
        for source in status.get("sources", {}).values():
            if isinstance(source, dict):
                source.pop("last_error", None)
                source.pop("subscribed_codes", None)
        status["enabled"] = self.configured
        status["upstream_ready"] = self.ready
        status["source_labels"] = {
            "platform_ws": "平台直连",
            "platform_rest": "平台快照备用",
        }
        return status

    def health(self) -> dict[str, Any]:
        push_status = self.push.status() if self.push is not None else {}
        return {
            "ok": True,
            "service": "paper-trading-quote-hub",
            "configured": self.configured,
            "ready": self.ready,
            "configuration_issues": self.settings.configuration_issues(),
            "upstream_probe": {
                "checked_at": datetime.fromtimestamp(self._probe_checked_at, tz=UTC).isoformat(
                    timespec="seconds"
                )
                if self._probe_checked_at
                else "",
                "ok": self._probe_ok,
            },
            "upstream": {
                "websocket_running": bool(push_status.get("running")),
                "websocket_connected": bool(push_status.get("connected")),
                "active_source": self.hub.active_source,
            },
        }

    def close(self) -> None:
        self.hub.close()
        if self.push is not None:
            self.push.stop()


def create_quote_service(settings: QuoteServiceSettings | None = None) -> QuoteService:
    selected = settings or QuoteServiceSettings.from_env()
    client = PlatformClient(
        selected.upstream_url,
        phone=selected.phone,
        password=selected.password,
        state_dir=selected.state_dir,
        timeout=selected.upstream_timeout,
    )
    # PlatformClient supports legacy PAPER_TRADING_* env values for the desktop
    # plugin.  Keep this service isolated to its dedicated settings even when a
    # host happens to export those legacy variables too.
    client.phone = selected.phone
    client.password = selected.password
    hub_holder: dict[str, QuoteHub] = {}

    def push_state(state: str, error: str) -> None:
        hub = hub_holder.get("hub")
        if hub is not None and state == "failure":
            # A start failure can be reported synchronously while QuoteHub is
            # reconciling subscriptions.  Report on a separate daemon to avoid
            # recursively taking its non-reentrant upstream-sync lock.
            def report() -> None:
                if hub.status()["subscribed_code_count"]:
                    hub.report_failure(
                        "platform_ws", error or "platform WebSocket transport failed"
                    )

            threading.Thread(
                target=report,
                name="quote-hub-ws-state",
                daemon=True,
            ).start()

    push = LivePushClient(
        client,
        host=selected.upstream_url,
        auto_start=False,
        state_callback=push_state,
    )
    sources = {
        "platform_ws": CallbackQuoteSourceAdapter(_ManagedPushEventClient(push)),
        "platform_rest": PollingQuoteSource(
            client.fetch_real_quotes,
            interval=selected.rest_interval,
        ),
    }
    hub = QuoteHub(
        sources,
        primary="platform_ws",
        failure_threshold=selected.failure_threshold,
        recovery_threshold=selected.recovery_threshold,
        stale_after=selected.stale_after,
        health_check_interval=selected.health_check_interval,
        subscriber_queue_size=selected.queue_size,
        max_subscribers=selected.max_clients,
        max_codes_per_subscriber=selected.max_codes_per_client,
        max_union_codes=selected.max_union_codes,
        primary_recovery_seconds=selected.primary_recovery_seconds,
        health_check_enabled=_is_a_share_session,
    )
    hub_holder["hub"] = hub
    probe_client = PlatformClient(
        selected.upstream_url,
        phone=selected.phone,
        password=selected.password,
        state_dir=str(Path(selected.state_dir) / "probe"),
        timeout=selected.readiness_probe_timeout,
    )
    probe_client.phone = selected.phone
    probe_client.password = selected.password
    return QuoteService(
        selected,
        hub,
        push=push,
        upstream_probe=lambda: probe_client.fetch_real_quotes(["600000.sh"]),
        snapshot_fetcher=client.fetch_real_quotes,
    )


def _quote_code(value: str) -> str:
    clean = str(value or "").strip().lower()
    if not clean:
        return ""
    if "." in clean:
        code, suffix = clean.rsplit(".", 1)
        return (
            f"{code}.{suffix}"
            if _CODE_PATTERN.fullmatch(code) and suffix in {"sh", "sz", "bj"}
            else ""
        )
    if not _CODE_PATTERN.fullmatch(clean):
        return ""
    if clean.startswith(("4", "8", "92")):
        suffix = "bj"
    elif clean.startswith(("5", "6", "9")):
        suffix = "sh"
    else:
        suffix = "sz"
    return f"{clean}.{suffix}"


def _quote_codes(value: str, *, limit: int, required: bool = False) -> list[str]:
    raw = [part.strip() for part in str(value or "").split(",") if part.strip()]
    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw:
        code = _quote_code(item)
        if not code:
            raise ValueError(f"invalid A-share quote code: {item}")
        if code not in seen:
            normalized.append(code)
            seen.add(code)
    if required and not normalized:
        raise ValueError("at least one quote code is required")
    if len(normalized) > limit:
        raise ValueError(f"at most {limit} quote codes are allowed per connection")
    return normalized


def _sse_frame(event: str, payload: dict[str, Any]) -> str:
    sequence = int(payload.get("seq") or 0)
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"id: {sequence}\nevent: {event}\ndata: {body}\n\n"


def create_app(
    settings: QuoteServiceSettings | None = None,
    *,
    service: QuoteService | None = None,
) -> FastAPI:
    if settings is not None and service is not None:
        raise ValueError("pass settings or service, not both")
    quote_service = service or create_quote_service(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        quote_service.hub.start()
        try:
            yield
        finally:
            quote_service.close()

    application = FastAPI(
        title="Paper Trading QuoteHub",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    application.state.quote_service = quote_service

    @application.get("/health")
    @application.get("/livez", include_in_schema=False)
    def health() -> dict[str, Any]:
        return quote_service.health()

    @application.get("/readyz", include_in_schema=False)
    def readiness() -> dict[str, Any]:
        if not quote_service.probe_upstream():
            health_payload = quote_service.health()
            raise HTTPException(status_code=503, detail=health_payload)
        return quote_service.health()

    @application.get(f"{API_PREFIX}/status")
    def quote_status() -> dict[str, Any]:
        # Observability must not start a credentialed connection.
        return quote_service.public_status()

    @application.get(f"{API_PREFIX}/snapshot")
    def quote_snapshot(codes: str = "") -> dict[str, Any]:
        try:
            selected = _quote_codes(
                codes,
                limit=quote_service.settings.max_codes_per_client,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        # An omitted code list returns an empty snapshot instead of another
        # tenant's merged cache.  Callers must explicitly name what they read.
        try:
            snapshot = quote_service.snapshot(selected)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {
            "ok": True,
            **snapshot,
            "status": quote_service.public_status(),
        }

    @application.get(f"{API_PREFIX}/stream")
    def quote_stream(request: Request, codes: str = "") -> StreamingResponse:
        if not quote_service.configured:
            raise HTTPException(status_code=503, detail="quote upstream is not configured")
        try:
            selected = _quote_codes(
                codes,
                limit=quote_service.settings.max_codes_per_client,
                required=True,
            )
            subscription = quote_service.hub.subscribe(
                selected,
                queue_size=quote_service.settings.queue_size,
                replay=False,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        quote_service.hub.start()

        async def generate() -> AsyncIterator[str]:
            deadline = time.monotonic() + quote_service.settings.sse_max_lifetime
            try:
                yield "retry: 3000\n\n"
                try:
                    initial_snapshot = await asyncio.to_thread(quote_service.snapshot, selected)
                except RuntimeError:
                    initial_snapshot = quote_service.hub.snapshot(selected)
                yield _sse_frame("snapshot", initial_snapshot)
                yield _sse_frame("status", quote_service.public_status())
                while True:
                    if await request.is_disconnected():
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        yield _sse_frame(
                            "reauth",
                            {
                                "type": "reauth",
                                "seq": quote_service.hub.status()["sequence"],
                                "reason": "stream lifetime reached; reconnect with fresh authorization",
                            },
                        )
                        break
                    try:
                        item = await asyncio.to_thread(
                            subscription.get,
                            min(quote_service.settings.sse_keepalive, remaining),
                        )
                    except queue.Empty:
                        if time.monotonic() >= deadline:
                            yield _sse_frame(
                                "reauth",
                                {
                                    "type": "reauth",
                                    "seq": quote_service.hub.status()["sequence"],
                                    "reason": "stream lifetime reached; reconnect with fresh authorization",
                                },
                            )
                            break
                        yield ": keepalive\n\n"
                        yield _sse_frame("status", quote_service.public_status())
                        continue
                    kind = str(item.get("type") or "quotes")
                    if kind == "closed":
                        break
                    if kind == "source_changed":
                        yield _sse_frame("status", quote_service.public_status())
                        if item.get("quotes"):
                            yield _sse_frame("quote", item)
                    elif kind == "snapshot":
                        yield _sse_frame("snapshot", item)
                    else:
                        yield _sse_frame("quote", item)
            finally:
                subscription.close()

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    return application


app = create_app()


__all__ = [
    "API_PREFIX",
    "QuoteService",
    "QuoteServiceSettings",
    "app",
    "create_app",
    "create_quote_service",
]
