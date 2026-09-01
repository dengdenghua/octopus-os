"""Implementation note."""

from __future__ import annotations

import pytest
from runtime.adapters.instrumentation import (
    ECHO_ATTR_STAGE,
    ECHO_ATTR_TASK_ID,
    OTEL_AVAILABLE,
    get_tracer,
    record_gen_ai_cost,
    trace_stage,
    traced,
)


class TestNoopPath:
    """Implementation note."""

    def test_get_tracer_never_fails(self):
        t = get_tracer()
        assert t is not None

    def test_traced_decorator_passes_through(self):
        @traced(span_name="test.fn", stage="test")
        def add(a, b):
            return a + b

        assert add(2, 3) == 5

    def test_traced_decorator_propagates_exception(self):
        @traced(span_name="test.bad")
        def bad():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            bad()

    def test_trace_stage_context_manager(self):
        with trace_stage("test", stage="test") as span:
            span.set_attribute("custom", "value")

    def test_trace_stage_propagates_exception(self):
        with pytest.raises(RuntimeError), trace_stage("test"):
            raise RuntimeError("stage failed")

    def test_record_gen_ai_cost_noop(self):
        with trace_stage("llm.call") as span:
            record_gen_ai_cost(
                span,
                system="anthropic",
                model="claude-haiku-4-5-20251001",
                input_tokens=100,
                output_tokens=50,
                usd=0.001,
            )


@pytest.mark.skipif(not OTEL_AVAILABLE, reason="needs opentelemetry installed")
class TestOtelPath:
    """Implementation note."""

    @pytest.fixture(scope="class")
    def exporter_holder(self):
        """Implementation note."""
        from opentelemetry import trace as otel_trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
            InMemorySpanExporter,
        )

        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        otel_trace.set_tracer_provider(provider)
        return exporter

    @pytest.fixture
    def in_memory_exporter(self, exporter_holder):
        """Implementation note."""
        exporter_holder.clear()
        return exporter_holder

    def test_traced_emits_span(self, in_memory_exporter):
        @traced(span_name="unit.fn", stage="execute")
        def work():
            return 42

        work()
        spans = in_memory_exporter.get_finished_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.name == "unit.fn"
        assert s.attributes.get(ECHO_ATTR_STAGE) == "execute"

    def test_trace_stage_emits_with_attributes(self, in_memory_exporter):
        with trace_stage("test.stage", stage="classify", task_id="abc-123"):
            pass

        spans = in_memory_exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].attributes.get(ECHO_ATTR_STAGE) == "classify"
        assert spans[0].attributes.get(ECHO_ATTR_TASK_ID) == "abc-123"

    def test_exception_recorded_on_span(self, in_memory_exporter):
        @traced(span_name="unit.bad")
        def bad():
            raise ValueError("test error")

        with pytest.raises(ValueError):
            bad()

        spans = in_memory_exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].status.status_code.name == "ERROR"
        assert len(spans[0].events) >= 1  # exception event
