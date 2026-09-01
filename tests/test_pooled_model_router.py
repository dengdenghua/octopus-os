from __future__ import annotations

from collections.abc import Callable

import pytest

from runtime.platform.models import CostEntry
from runtime.sensing.model_router.models import ModelResponse, ModelStreamEvent
from runtime.sensing.model_router.pooled_router import (
    PooledModelRouter,
    _is_transient_network_error,
)


class _RecordingPool:
    def __init__(self, keys: list[str]) -> None:
        self._keys = keys
        self._index = 0
        self.acquired: list[str] = []
        self.exhausted: list[str] = []
        self.usage: list[tuple[str, float]] = []

    def acquire(self) -> str:
        key = self._keys[self._index]
        self._index += 1
        self.acquired.append(key)
        return key

    def report_exhausted(self, key: str) -> None:
        self.exhausted.append(key)

    def report_usage(self, key: str, cost_usd: float = 0.0) -> None:
        self.usage.append((key, cost_usd))


class _SequenceRouter:
    def __init__(self, outcomes: list[Exception | ModelResponse]) -> None:
        self._outcomes = outcomes
        self.calls = 0

    def call(self, request: object) -> ModelResponse:
        del request
        outcome = self._outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _SequenceStreamRouter(_SequenceRouter):
    def __init__(
        self,
        stream_outcomes: list[Exception | list[ModelStreamEvent]],
    ) -> None:
        super().__init__([])
        self._stream_outcomes = stream_outcomes

    def call_stream(self, request: object):
        del request
        outcome = self._stream_outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        yield from outcome


def _response(text: str = "ok") -> ModelResponse:
    return ModelResponse(text=text, model="test", cost=CostEntry(usd=0.01))


def _factory(routers: dict[str, _SequenceRouter]) -> Callable[[str], _SequenceRouter]:
    return lambda key: routers[key]


def test_transient_failure_retries_same_key_with_backoff(monkeypatch) -> None:
    pool = _RecordingPool(["key-1", "key-2"])
    first = _SequenceRouter([ConnectionError("connection reset"), _response()])
    sleeps: list[float] = []
    monkeypatch.setattr(
        "runtime.sensing.model_router.pooled_router.time.sleep",
        sleeps.append,
    )
    router = PooledModelRouter(
        pool,  # type: ignore[arg-type]
        _factory({"key-1": first}),  # type: ignore[arg-type]
        max_retries=3,
        retry_base_delay=0.1,
    )

    result = router.call(object())  # type: ignore[arg-type]

    assert result.text == "ok"
    assert pool.acquired == ["key-1"]
    assert pool.exhausted == []
    assert pool.usage == [("key-1", 0.01)]
    assert first.calls == 2
    assert sleeps == [0.1]


def test_rate_limit_exhausts_key_and_rotates_to_next_key() -> None:
    class RateLimitError(Exception):
        pass

    pool = _RecordingPool(["key-1", "key-2"])
    first = _SequenceRouter([RateLimitError("HTTP 429")])
    second = _SequenceRouter([_response("fallback")])
    router = PooledModelRouter(
        pool,  # type: ignore[arg-type]
        _factory({"key-1": first, "key-2": second}),  # type: ignore[arg-type]
        max_retries=3,
        retry_base_delay=0,
    )

    result = router.call(object())  # type: ignore[arg-type]

    assert result.text == "fallback"
    assert pool.acquired == ["key-1", "key-2"]
    assert pool.exhausted == ["key-1"]
    assert pool.usage == [("key-2", 0.01)]


def test_transient_retry_budget_is_bounded_and_keeps_key(monkeypatch) -> None:
    pool = _RecordingPool(["key-1", "key-2"])
    failure = ConnectionError("server disconnected")
    first = _SequenceRouter([failure, failure, failure])
    sleeps: list[float] = []
    monkeypatch.setattr(
        "runtime.sensing.model_router.pooled_router.time.sleep",
        sleeps.append,
    )
    router = PooledModelRouter(
        pool,  # type: ignore[arg-type]
        _factory({"key-1": first}),  # type: ignore[arg-type]
        max_retries=3,
        retry_base_delay=0.05,
    )

    with pytest.raises(ConnectionError, match="server disconnected"):
        router.call(object())  # type: ignore[arg-type]

    assert pool.acquired == ["key-1"]
    assert first.calls == 3
    assert sleeps == [0.05, 0.1]


def test_permanent_certificate_error_is_not_retried() -> None:
    error = RuntimeError("SSL certificate verify failed")
    assert _is_transient_network_error(error) is False

    pool = _RecordingPool(["key-1", "key-2"])
    first = _SequenceRouter([error])
    router = PooledModelRouter(
        pool,  # type: ignore[arg-type]
        _factory({"key-1": first}),  # type: ignore[arg-type]
        max_retries=3,
        retry_base_delay=0,
    )

    with pytest.raises(RuntimeError, match="certificate verify failed"):
        router.call(object())  # type: ignore[arg-type]

    assert pool.acquired == ["key-1"]
    assert first.calls == 1


def test_stream_transient_before_first_event_retries_same_key(monkeypatch) -> None:
    pool = _RecordingPool(["key-1", "key-2"])
    done = ModelStreamEvent(type="done", final=_response("streamed"))
    first = _SequenceStreamRouter([ConnectionError("connection reset"), [done]])
    sleeps: list[float] = []
    monkeypatch.setattr(
        "runtime.sensing.model_router.pooled_router.time.sleep",
        sleeps.append,
    )
    router = PooledModelRouter(
        pool,  # type: ignore[arg-type]
        _factory({"key-1": first}),  # type: ignore[arg-type]
        max_retries=3,
        retry_base_delay=0.1,
    )

    events = list(router.call_stream(object()))  # type: ignore[arg-type]

    assert events == [done]
    assert pool.acquired == ["key-1"]
    assert pool.exhausted == []
    assert pool.usage == [("key-1", 0.01)]
    assert first.calls == 2
    assert sleeps == [0.1]


def test_stream_rate_limit_before_first_event_rotates_key() -> None:
    class RateLimitError(Exception):
        pass

    pool = _RecordingPool(["key-1", "key-2"])
    first = _SequenceStreamRouter([RateLimitError("HTTP 429")])
    done = ModelStreamEvent(type="done", final=_response("fallback stream"))
    second = _SequenceStreamRouter([[done]])
    router = PooledModelRouter(
        pool,  # type: ignore[arg-type]
        _factory({"key-1": first, "key-2": second}),  # type: ignore[arg-type]
        max_retries=3,
        retry_base_delay=0,
    )

    events = list(router.call_stream(object()))  # type: ignore[arg-type]

    assert events == [done]
    assert pool.acquired == ["key-1", "key-2"]
    assert pool.exhausted == ["key-1"]
    assert pool.usage == [("key-2", 0.01)]


def test_stream_failure_after_visible_delta_is_not_replayed() -> None:
    pool = _RecordingPool(["key-1", "key-2"])

    class _PartialFailureRouter(_SequenceRouter):
        def call_stream(self, request: object):
            del request
            self.calls += 1
            yield ModelStreamEvent(type="text_delta", delta="visible")
            raise ConnectionError("server disconnected")

    first = _PartialFailureRouter([])
    router = PooledModelRouter(
        pool,  # type: ignore[arg-type]
        _factory({"key-1": first}),  # type: ignore[arg-type]
        max_retries=3,
        retry_base_delay=0,
    )
    stream = router.call_stream(object())  # type: ignore[arg-type]

    assert next(stream).delta == "visible"
    with pytest.raises(ConnectionError, match="server disconnected"):
        next(stream)

    assert pool.acquired == ["key-1"]
    assert pool.exhausted == []
    assert pool.usage == [("key-1", 0.0)]
    assert first.calls == 1


def test_closing_partial_stream_records_one_provider_call() -> None:
    pool = _RecordingPool(["key-1"])

    class _OpenStreamRouter(_SequenceRouter):
        def call_stream(self, request: object):
            del request
            self.calls += 1
            yield ModelStreamEvent(type="text_delta", delta="first")
            yield ModelStreamEvent(type="text_delta", delta="second")

    first = _OpenStreamRouter([])
    router = PooledModelRouter(
        pool,  # type: ignore[arg-type]
        _factory({"key-1": first}),  # type: ignore[arg-type]
    )
    stream = router.call_stream(object())  # type: ignore[arg-type]

    assert next(stream).delta == "first"
    stream.close()

    assert pool.usage == [("key-1", 0.0)]
    assert first.calls == 1

