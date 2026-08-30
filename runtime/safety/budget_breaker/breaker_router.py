from __future__ import annotations

from collections.abc import Iterator

from runtime.adapters.instrumentation import trace_stage
from runtime.platform.models.llm import (
    ModelRequest,
    ModelResponse,
    ModelRouter,
    ModelStreamEvent,
)

from .breaker import CircuitBreaker, CircuitOpen


class BreakerModelRouter(ModelRouter):
    def __init__(self, *, inner: ModelRouter, breaker: CircuitBreaker) -> None:
        self.inner = inner
        self.breaker = breaker

    def call(self, request: ModelRequest) -> ModelResponse:
        with trace_stage("ink.breaker.call") as span:
            self._check_or_trace_rejection(span)

            try:
                response = self.inner.call(request)
            except Exception as exc:  # noqa: BLE001 — every inner failure must count toward the breaker
                self.breaker.record(success=False)
                span.set_attribute("echo.breaker.inner_error", type(exc).__name__)
                span.set_attribute("echo.breaker.state_after", self.breaker.state)
                raise

            cost_usd = response.cost.usd if response.cost else 0.0
            self.breaker.record(success=True, cost_usd=cost_usd)
            span.set_attribute("echo.breaker.state_after", self.breaker.state)
            return response

    def call_stream(
        self,
        request: ModelRequest,
    ) -> Iterator[ModelStreamEvent]:
        with trace_stage("ink.breaker.call_stream") as span:
            self._check_or_trace_rejection(span)

            final_response: ModelResponse | None = None
            saw_done = False
            try:
                for event in self.inner.call_stream(request):
                    if event.type == "done":
                        saw_done = True
                        if event.final is not None:
                            final_response = event.final
                    yield event
            except GeneratorExit:
                if saw_done:
                    self._record_stream_success(span, final_response)
                else:
                    self.breaker.record(success=False)
                    span.set_attribute("echo.breaker.stream_abandoned", True)
                    span.set_attribute("echo.breaker.state_after", self.breaker.state)
                raise
            except Exception as exc:  # noqa: BLE001 — every stream failure must count toward the breaker
                self.breaker.record(success=False)
                span.set_attribute("echo.breaker.inner_error", type(exc).__name__)
                span.set_attribute("echo.breaker.state_after", self.breaker.state)
                raise

            self._record_stream_success(span, final_response)

    def _check_or_trace_rejection(self, span: object) -> str:
        state_before = self.breaker.state
        span.set_attribute("echo.breaker.state_before_check", state_before)
        try:
            state = self.breaker.check()
        except CircuitOpen as exc:
            span.set_attribute("echo.breaker.state_on_entry", state_before)
            span.set_attribute("echo.breaker.state_after", self.breaker.state)
            span.set_attribute("echo.breaker.rejected", True)
            span.set_attribute("echo.breaker.reject_reason", exc.reason)
            raise
        span.set_attribute("echo.breaker.state_on_entry", state)
        return state

    def _record_stream_success(self, span: object, final_response: ModelResponse | None) -> None:
        cost_usd = final_response.cost.usd if final_response and final_response.cost else 0.0
        self.breaker.record(success=True, cost_usd=cost_usd)
        span.set_attribute("echo.breaker.state_after", self.breaker.state)
