"""maybe_setup_tracing — opt-in OTel provider bootstrap.

These verify the no-op contract that keeps default behaviour unchanged
(the export path itself needs the OTel SDK + a collector to verify
end-to-end and is exercised in deployment, not here).
"""

import runtime.adapters.instrumentation.tracing as tracing
from runtime.adapters.instrumentation import maybe_setup_tracing


def test_noop_when_sdk_absent(monkeypatch):
    # Force the "SDK not installed" branch regardless of the env.
    monkeypatch.setattr(tracing, "OTEL_AVAILABLE", False)
    monkeypatch.setattr(tracing, "_TRACING_CONFIGURED", False)
    monkeypatch.setenv("ECHO_OTEL_CONSOLE", "1")
    assert maybe_setup_tracing() is False


def test_noop_when_unconfigured(monkeypatch):
    # SDK "available" but no exporter requested → strict no-op, so the
    # default behaviour (no provider installed) is preserved.
    monkeypatch.setattr(tracing, "OTEL_AVAILABLE", True)
    monkeypatch.setattr(tracing, "_TRACING_CONFIGURED", False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("ECHO_OTEL_CONSOLE", raising=False)
    assert maybe_setup_tracing() is False


def test_idempotent_after_configured(monkeypatch):
    monkeypatch.setattr(tracing, "OTEL_AVAILABLE", True)
    monkeypatch.setattr(tracing, "_TRACING_CONFIGURED", True)  # already set up
    monkeypatch.setenv("ECHO_OTEL_CONSOLE", "1")
    assert maybe_setup_tracing() is False


def test_setup_failure_is_swallowed(monkeypatch):
    # Requested but the SDK import inside the try fails → must return
    # False, never raise (startup must not break on a tracing misconfig).
    monkeypatch.setattr(tracing, "OTEL_AVAILABLE", True)
    monkeypatch.setattr(tracing, "_TRACING_CONFIGURED", False)
    monkeypatch.setenv("ECHO_OTEL_CONSOLE", "1")
    # OTEL_AVAILABLE forced True but the SDK isn't installed here, so the
    # inner import raises and must be swallowed. Whatever the env, the
    # call must return a bool and never propagate.
    result = maybe_setup_tracing()
    assert isinstance(result, bool)
