from __future__ import annotations

import functools
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, TypeVar

try:
    from opentelemetry import trace  # type: ignore[import-untyped]
    from opentelemetry.trace import Status, StatusCode  # type: ignore[import-untyped]

    OTEL_AVAILABLE = True
except ImportError:  # pragma: no cover - tested via test_tracing_noop path
    OTEL_AVAILABLE = False

    class _NoopSpan:
        def set_attribute(self, *a: Any, **kw: Any) -> None: ...
        def set_status(self, *a: Any, **kw: Any) -> None: ...
        def record_exception(self, *a: Any, **kw: Any) -> None: ...
        def end(self) -> None: ...
        def __enter__(self) -> _NoopSpan:
            return self

        def __exit__(self, *a: Any) -> None: ...

    class _NoopTracer:
        def start_as_current_span(self, name: str, **kw: Any) -> _NoopSpan:
            return _NoopSpan()

        def start_span(self, name: str, **kw: Any) -> _NoopSpan:
            return _NoopSpan()

    class _NoopStatusCode:
        OK = "OK"
        ERROR = "ERROR"

    class _NoopStatus:
        def __init__(self, code: str, description: str = "") -> None:
            self.code = code

    Status = _NoopStatus  # type: ignore[assignment,misc]
    StatusCode = _NoopStatusCode  # type: ignore[assignment,misc]


ECHO_ATTR_STAGE = "echo.stage"  # Implementation note.
ECHO_ATTR_TASK_ID = "echo.task_id"
ECHO_ATTR_ARM = "echo.arm_id"
ECHO_ATTR_SUCKER = "echo.sucker_id"
ECHO_ATTR_RECIPE = "echo.recipe_id"
ECHO_ATTR_GENOME = "echo.genome_version"
ECHO_ATTR_COST_USD = "echo.cost_usd"
ECHO_ATTR_COST_TOKENS = "echo.cost_tokens"

GEN_AI_ATTR_SYSTEM = "gen_ai.system"
GEN_AI_ATTR_MODEL = "gen_ai.request.model"
GEN_AI_ATTR_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_ATTR_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_ATTR_FINISH_REASONS = "gen_ai.response.finish_reasons"


def get_tracer(name: str = "runtime") -> Any:
    if OTEL_AVAILABLE:
        return trace.get_tracer(name)
    return _NoopTracer()  # type: ignore[name-defined]


_TRACING_CONFIGURED = False


def maybe_setup_tracing(*, service_name: str = "echo-agent") -> bool:
    """Install a global OTel TracerProvider so the ~65 instrumentation
    points actually export — but only when explicitly configured.

    Without this, ``trace.get_tracer()`` returns a tracer bound to the
    default NoOp provider: spans are created and immediately discarded,
    so every ``trace_stage`` / ``traced`` call is invisible. The app
    never configured a provider, leaving the whole tracing surface a
    silent no-op even with the ``[tracing]`` extra installed.

    Opt-in and side-effect-free by default:
      * returns False when the OTel SDK isn't installed;
      * returns False unless an exporter is requested via
        ``OTEL_EXPORTER_OTLP_ENDPOINT`` (OTLP) or
        ``ECHO_OTEL_CONSOLE=1`` (stdout, for local debugging);
      * never raises — a tracing misconfig must not stop serve.

    Idempotent. Returns True iff a provider was installed.
    """
    global _TRACING_CONFIGURED
    if _TRACING_CONFIGURED or not OTEL_AVAILABLE:
        return False
    import os

    otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    want_console = os.environ.get("ECHO_OTEL_CONSOLE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if not otlp_endpoint and not want_console:
        return False
    try:
        from opentelemetry import trace as _trace  # type: ignore[import-untyped]
        from opentelemetry.sdk.resources import Resource  # type: ignore[import-untyped]
        from opentelemetry.sdk.trace import TracerProvider  # type: ignore[import-untyped]
        from opentelemetry.sdk.trace.export import (  # type: ignore[import-untyped]
            BatchSpanProcessor,
            ConsoleSpanExporter,
        )

        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        if otlp_endpoint:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # type: ignore[import-untyped]
                OTLPSpanExporter,
            )

            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        if want_console:
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        _trace.set_tracer_provider(provider)
        _TRACING_CONFIGURED = True
        return True
    except Exception:  # noqa: BLE001 — tracing setup must never break startup
        return False


@contextmanager
def _span_scope(name: str) -> Iterator[Any]:
    """Create a span without attaching it to the global current context.

    Starlette can run sync generators and worker callbacks in different
    contextvars contexts. ``start_as_current_span`` attaches a token that must
    be detached from the same context; when that invariant breaks,
    OpenTelemetry logs noisy "Failed to detach context" tracebacks. A plain
    span still records attributes/status/events while avoiding attach/detach.
    """
    span = get_tracer().start_span(name)
    try:
        yield span
    finally:
        span.end()


F = TypeVar("F", bound=Callable[..., Any])


def traced(
    span_name: str | None = None,
    stage: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> Callable[[F], F]:
    static_attrs = dict(attributes or {})

    def decorator(fn: F) -> F:
        name = span_name or f"{fn.__module__}.{fn.__qualname__}"

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with _span_scope(name) as span:
                if stage:
                    span.set_attribute(ECHO_ATTR_STAGE, stage)
                for k, v in static_attrs.items():
                    span.set_attribute(k, v)
                try:
                    result = fn(*args, **kwargs)
                    span.set_status(Status(StatusCode.OK))
                    return result
                except Exception as e:
                    span.record_exception(e)
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    raise

        return wrapper  # type: ignore[return-value]

    return decorator


@contextmanager
def trace_stage(
    name: str,
    stage: str | None = None,
    task_id: str | None = None,
    arm_id: str | None = None,
    **extra: Any,
) -> Iterator[Any]:
    with _span_scope(name) as span:
        if stage:
            span.set_attribute(ECHO_ATTR_STAGE, stage)
        if task_id:
            span.set_attribute(ECHO_ATTR_TASK_ID, task_id)
        if arm_id:
            span.set_attribute(ECHO_ATTR_ARM, arm_id)
        for k, v in extra.items():
            span.set_attribute(k, v)
        try:
            yield span
            span.set_status(Status(StatusCode.OK))
        except Exception as e:
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, str(e)))
            raise


def record_gen_ai_cost(
    span: Any,
    *,
    system: str,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    usd: float = 0.0,
    finish_reasons: list[str] | None = None,
) -> None:
    span.set_attribute(GEN_AI_ATTR_SYSTEM, system)
    span.set_attribute(GEN_AI_ATTR_MODEL, model)
    span.set_attribute(GEN_AI_ATTR_INPUT_TOKENS, input_tokens)
    span.set_attribute(GEN_AI_ATTR_OUTPUT_TOKENS, output_tokens)
    if finish_reasons:
        span.set_attribute(GEN_AI_ATTR_FINISH_REASONS, finish_reasons)
    span.set_attribute(ECHO_ATTR_COST_USD, float(usd))
    span.set_attribute(ECHO_ATTR_COST_TOKENS, int(input_tokens + output_tokens))
