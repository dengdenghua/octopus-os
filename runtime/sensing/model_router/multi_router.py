from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from runtime.adapters.instrumentation import trace_stage

from .models import ModelRequest, ModelResponse, ModelRouter, ModelStreamEvent

_LOG = logging.getLogger("echo.eyes.multi_router")

# Cap the in-memory dispatch log so a long-lived router can't grow it without
# bound (one record per call). It's a rolling debug/observability buffer —
# only the most recent dispatches matter; tests read dispatch_log[-1].
_DISPATCH_LOG_MAX = 256


class EmptyModelStreamError(RuntimeError):
    """Provider ended a streaming request without producing any event."""


def _is_transient_error(exc: BaseException) -> bool:
    """Heuristic: an error worth retrying on the same provider before
    giving up and trying the next one in the fallback chain.

    Matches by error-class name and message substrings so it works
    whether the anthropic / openai SDK is installed (real exception
    classes) or a duck-typed test client is in use.
    """
    name = type(exc).__name__
    msg = str(exc)
    # Name-based: any vendor SDK's RateLimit / Timeout / Connection.
    if any(
        needle in name
        for needle in (
            "RateLimit",
            "Timeout",
            "Connection",
            "APIConnection",
            "EmptyModelStream",
        )
    ):
        return True
    # Transport/protocol errors are normally safe to retry.  Do not match a
    # generic SSLError here: certificate verification/configuration failures
    # are permanent and retrying them only adds latency.
    if any(
        needle in name
        for needle in (
            "RemoteProtocol",
            "ReadError",
            "ProtocolError",
            "SSLEOF",
        )
    ):
        return True
    # 429 / 5xx surfaced via message string (proxies, mirrors, …).
    if any(code in msg for code in (" 429", " 500", " 502", " 503", " 504")):
        return True
    # SSL EOF / connection reset — common with overloaded LLM endpoints.
    msg_lower = msg.lower()
    return any(
        marker in msg_lower
        for marker in (
            "unexpected_eof",
            "eof occurred in violation of protocol",
            "connection reset",
            "connection aborted",
            "server disconnected",
            "remote protocol",
            "remoteprotocol",
        )
    )


@dataclass
class RouteAttempt:
    role: str  # "primary" | "strong" | "fallback[i]"
    model: str  # Implementation note.
    success: bool
    error: str | None = None
    response_provider: str | None = None


@dataclass
class DispatchRecord:
    prefer_strength: str
    attempts: list[RouteAttempt] = field(default_factory=list)

    @property
    def final_role(self) -> str | None:
        for a in self.attempts:
            if a.success:
                return a.role
        return None


class MultiModelRouter(ModelRouter):
    def __init__(
        self,
        *,
        primary: ModelRouter,
        strong: ModelRouter | None = None,
        fallbacks: list[ModelRouter] | None = None,
        retry_attempts: int = 3,
        retry_base_delay: float = 0.5,
    ) -> None:
        self.primary = primary
        self.strong = strong
        self.fallbacks = list(fallbacks or [])
        self.dispatch_log: deque[DispatchRecord] = deque(maxlen=_DISPATCH_LOG_MAX)
        # Retry config — same provider gets up to ``retry_attempts``
        # tries on a transient failure before falling back to the
        # next provider in the chain. Set to 1 to disable.
        self._retry_attempts = max(1, retry_attempts)
        self._retry_base_delay = retry_base_delay

    def call(self, request: ModelRequest) -> ModelResponse:
        with trace_stage("eyes.multi_router.call") as span:
            span.set_attribute("echo.multi.prefer", request.prefer_strength)

            chain = self._build_chain(request.prefer_strength)
            record = DispatchRecord(prefer_strength=request.prefer_strength)
            last_error: Exception | None = None

            for role, router in chain:
                sub_request = _rewrite_model_for(request, router)
                try:
                    response = self._call_with_retry(router, sub_request, role)
                except Exception as e:  # noqa: BLE001
                    record.attempts.append(
                        RouteAttempt(
                            role=role,
                            model=sub_request.model,
                            success=False,
                            error=f"{type(e).__name__}: {e}",
                        )
                    )
                    last_error = e
                    continue

                record.attempts.append(
                    RouteAttempt(
                        role=role,
                        model=sub_request.model,
                        success=True,
                        response_provider=response.provider or None,
                    )
                )
                self.dispatch_log.append(record)
                span.set_attribute("echo.multi.final_role", role)
                span.set_attribute("echo.multi.attempts", len(record.attempts))
                return response

            self.dispatch_log.append(record)
            span.set_attribute("echo.multi.failed", True)
            span.set_attribute("echo.multi.attempts", len(record.attempts))
            if last_error is not None:
                raise last_error
            raise RuntimeError("MultiModelRouter: no routes configured")

    def call_stream(self, request: ModelRequest) -> Iterator[ModelStreamEvent]:
        """Stream from the preferred provider with safe failover semantics.

        A provider may be retried or replaced only before its first visible
        event.  Once any delta/tool call is yielded, replaying on another route
        could duplicate user-visible text or execute a tool twice, so the
        original failure is surfaced unchanged.
        """

        with trace_stage("eyes.multi_router.call_stream") as span:
            span.set_attribute("echo.multi.prefer", request.prefer_strength)
            chain = self._build_chain(request.prefer_strength)
            record = DispatchRecord(prefer_strength=request.prefer_strength)
            last_error: Exception | None = None

            for role, router in chain:
                sub_request = _rewrite_model_for(request, router)
                route_error: Exception | None = None
                response_provider: str | None = None
                route_succeeded = False

                for retry_index in range(self._retry_attempts):
                    yielded_any = False
                    try:
                        for event in router.call_stream(sub_request):
                            yielded_any = True
                            if event.type == "done" and event.final is not None:
                                response_provider = event.final.provider or None
                            yield event
                        if not yielded_any:
                            raise EmptyModelStreamError(f"{role} returned no streaming events")
                        route_succeeded = True
                        break
                    except GeneratorExit:
                        record.attempts.append(
                            RouteAttempt(
                                role=role,
                                model=sub_request.model,
                                success=False,
                                error="GeneratorExit: stream closed by consumer",
                            )
                        )
                        self.dispatch_log.append(record)
                        span.set_attribute("echo.multi.cancelled", True)
                        span.set_attribute("echo.multi.attempts", len(record.attempts))
                        raise
                    except Exception as exc:  # noqa: BLE001
                        route_error = exc
                        last_error = exc
                        if yielded_any:
                            record.attempts.append(
                                RouteAttempt(
                                    role=role,
                                    model=sub_request.model,
                                    success=False,
                                    error=f"{type(exc).__name__}: {exc}",
                                )
                            )
                            self.dispatch_log.append(record)
                            span.set_attribute("echo.multi.partial_stream_failed", True)
                            span.set_attribute("echo.multi.attempts", len(record.attempts))
                            raise
                        is_last_retry = retry_index >= self._retry_attempts - 1
                        if is_last_retry or not _is_transient_error(exc):
                            break
                        delay = _stream_retry_delay(
                            retry_index,
                            base_delay=self._retry_base_delay,
                        )
                        _LOG.info(
                            "multi_router stream retry · role=%s model=%s "
                            "attempt=%d exc=%s sleep=%.3fs",
                            role,
                            sub_request.model,
                            retry_index + 1,
                            type(exc).__name__,
                            delay,
                        )
                        if delay > 0:
                            time.sleep(delay)

                if route_succeeded:
                    record.attempts.append(
                        RouteAttempt(
                            role=role,
                            model=sub_request.model,
                            success=True,
                            response_provider=response_provider,
                        )
                    )
                    self.dispatch_log.append(record)
                    span.set_attribute("echo.multi.final_role", role)
                    span.set_attribute("echo.multi.attempts", len(record.attempts))
                    return

                if route_error is not None:
                    record.attempts.append(
                        RouteAttempt(
                            role=role,
                            model=sub_request.model,
                            success=False,
                            error=f"{type(route_error).__name__}: {route_error}",
                        )
                    )

            self.dispatch_log.append(record)
            span.set_attribute("echo.multi.failed", True)
            span.set_attribute("echo.multi.attempts", len(record.attempts))
            if last_error is not None:
                raise last_error
            raise RuntimeError("MultiModelRouter: no routes configured")

    def _call_with_retry(
        self,
        router: ModelRouter,
        request: ModelRequest,
        role: str,
    ) -> ModelResponse:
        """Retry transient errors on a single provider before failing
        over to the next one in the chain.

        Uses ``RetryPolicy`` so backoff + jitter is consistent with
        every other retryable boundary in the system.
        """
        if self._retry_attempts <= 1:
            return router.call(request)

        from runtime.platform.runtime_policy.retry import RetryPolicy, retry_call

        policy = RetryPolicy(
            on=(),  # use retry_if predicate instead
            retry_if=_is_transient_error,
            attempts=self._retry_attempts,
            base_delay=self._retry_base_delay,
            jitter=0.25,
        )

        def _on_retry(idx: int, exc: BaseException, delay: float) -> None:
            _LOG.info(
                "multi_router retry · role=%s model=%s attempt=%d exc=%s sleep=%.3fs",
                role,
                request.model,
                idx + 1,
                type(exc).__name__,
                delay,
            )

        return retry_call(
            router.call,
            request,
            policy=policy,
            on_retry=_on_retry,
        )

    # ─── internals ───────────────────────────────────

    def _build_chain(self, prefer: str) -> list[tuple[str, ModelRouter]]:
        chain: list[tuple[str, ModelRouter]] = []

        if prefer == "strong" and self.strong is not None:
            chain.append(("strong", self.strong))
            chain.append(("primary", self.primary))
        else:
            chain.append(("primary", self.primary))

        for i, fb in enumerate(self.fallbacks):
            chain.append((f"fallback[{i}]", fb))

        seen: set[int] = set()
        deduped: list[tuple[str, ModelRouter]] = []
        for role, router in chain:
            if id(router) in seen:
                continue
            seen.add(id(router))
            deduped.append((role, router))
        return deduped


def _rewrite_model_for(request: ModelRequest, router: ModelRouter) -> ModelRequest:
    default_model: Any = getattr(router, "default_model", None)
    if default_model and isinstance(default_model, str) and default_model != request.model:
        return request.model_copy(update={"model": default_model})
    return request


def _stream_retry_delay(attempt_index: int, *, base_delay: float) -> float:
    from runtime.platform.runtime_policy.retry import RetryPolicy

    return RetryPolicy(base_delay=base_delay, jitter=0.25).compute_delay(attempt_index)
