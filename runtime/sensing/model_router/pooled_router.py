from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import Any

from .credential_pool import AllKeysExhausted, CredentialPool
from .models import (
    ModelRequest,
    ModelResponse,
    ModelRouter,
    ModelStreamEvent,
)


class PooledModelRouter(ModelRouter):
    def __init__(
        self,
        pool: CredentialPool,
        router_factory: Callable[[str], ModelRouter],
        max_retries: int = 3,
        retry_base_delay: float = 0.25,
    ) -> None:
        self._pool = pool
        self._factory = router_factory
        self._max_retries = max(1, int(max_retries))
        self._retry_base_delay = max(0.0, float(retry_base_delay))
        self._routers: dict[str, ModelRouter] = {}

    @property
    def pool(self) -> CredentialPool:
        return self._pool

    @property
    def default_model(self) -> Any:
        with self._pool._lock:
            if self._routers:
                first = next(iter(self._routers.values()))
                return getattr(first, "default_model", None)
        return None

    def call(self, request: ModelRequest) -> ModelResponse:
        last_exc: Exception | None = None
        key: str | None = None
        for attempt in range(self._max_retries):
            if key is None:
                try:
                    key = self._pool.acquire()
                except AllKeysExhausted as exc:
                    raise AllKeysExhausted(f"all keys exhausted after {attempt} attempts") from exc

            router = self._get_router(key)
            try:
                resp = router.call(request)
                self._pool.report_usage(
                    key,
                    cost_usd=resp.cost.usd,
                )
                return resp
            except Exception as exc:
                last_exc = exc
                if _is_rate_limit_or_auth(exc):
                    self._pool.report_exhausted(key)
                    key = None
                    continue
                if _is_transient_network_error(exc) and attempt < self._max_retries - 1:
                    if self._retry_base_delay:
                        time.sleep(self._retry_base_delay * (2**attempt))
                    continue
                raise

        raise last_exc or RuntimeError("unexpected pool exhaustion loop")

    def call_stream(self, request: ModelRequest) -> Iterator[ModelStreamEvent]:
        last_exc: Exception | None = None
        key: str | None = None
        for attempt in range(self._max_retries):
            if key is None:
                try:
                    key = self._pool.acquire()
                except AllKeysExhausted as exc:
                    raise AllKeysExhausted(
                        f"all keys exhausted after {attempt} streaming attempts"
                    ) from exc

            router = self._get_router(key)
            total_usd = 0.0
            yielded_any = False
            try:
                for event in router.call_stream(request):
                    yielded_any = True
                    if event.type == "done" and event.final is not None:
                        total_usd = event.final.cost.usd
                    yield event
                self._pool.report_usage(key, cost_usd=total_usd)
                return
            except GeneratorExit:
                if yielded_any:
                    self._pool.report_usage(key, cost_usd=total_usd)
                raise
            except Exception as exc:
                last_exc = exc
                if yielded_any:
                    # Replaying after a visible delta could duplicate prose or,
                    # worse, execute the same tool call twice.  Record the
                    # partial provider call and surface the failure unchanged.
                    self._pool.report_usage(key, cost_usd=total_usd)
                    if _is_rate_limit_or_auth(exc):
                        self._pool.report_exhausted(key)
                    raise
                if _is_rate_limit_or_auth(exc):
                    self._pool.report_exhausted(key)
                    key = None
                    continue
                if _is_transient_network_error(exc) and attempt < self._max_retries - 1:
                    if self._retry_base_delay:
                        time.sleep(self._retry_base_delay * (2**attempt))
                    continue
                raise

        raise last_exc or RuntimeError("unexpected streaming pool exhaustion loop")

    def _get_router(self, key: str) -> ModelRouter:
        if key not in self._routers:
            self._routers[key] = self._factory(key)
        return self._routers[key]


def _is_rate_limit_or_auth(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    return any(
        token in name or token in msg
        for token in (
            "ratelimit",
            "rate_limit",
            "429",
            "quota",
            "exceeded",
            "unauthorized",
            "forbidden",
            "auth",
        )
    )


def _is_transient_network_error(exc: Exception) -> bool:
    """SSL EOF, connection reset, protocol errors — worth retrying on
    the same key (the key is fine, the network/upstream isn't)."""
    name = type(exc).__name__
    msg_lower = str(exc).lower()
    if any(
        needle in name
        for needle in (
            "RemoteProtocol",
            "ReadError",
            "ProtocolError",
            "SSLEOF",
            "ConnectionError",
            "Timeout",
        )
    ):
        return True
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
