"""Health, metrics, and K8s probe routers for ``create_app``.

Extracted from ``app.py`` during the god-file reduction (§2.2 of the
navigation map). Mounts the health router, the optional /metrics
endpoint, and the liveness/readiness probe router.
"""

from __future__ import annotations

from typing import Any

from ._app_context import AppContext


def mount_health(
    ctx: AppContext,
    *,
    agent_registry: Any,
    channel_manager: Any,
    group_registry: Any,
    server_host: str | None,
    server_port: int | None,
    frontend_host: str | None,
    frontend_port: int | None,
    frontend_proxy_target: str | None,
) -> None:
    """Mount health + metrics + probe routers onto ctx.app."""
    app = ctx.app
    state = ctx.state
    stack = ctx.stack

    # Health and capability probes.
    from runtime.platform.ui.health_router import create_health_router

    app.include_router(
        create_health_router(
            state=state,
            agent_registry=agent_registry,
            channel_manager=channel_manager,
            group_registry=group_registry,
            server_host=server_host,
            server_port=server_port,
            frontend_host=frontend_host,
            frontend_port=frontend_port,
            frontend_proxy_target=frontend_proxy_target,
            identity_store=ctx.identity_store,
            require_auth=ctx.require_auth,
            jwt_secret=ctx.jwt_secret,
            jwt_issuer=ctx.jwt_issuer,
            jwt_audience=ctx.jwt_audience,
        )
    )

    # Prometheus /metrics scrape endpoint (Round 24 wiring).
    # The metrics registry is the process-wide shared singleton from
    # runtime.platform.observability.metrics so every emitter (Beak skill telemetry,
    # health probes, …) lands here automatically.
    try:
        from runtime.sensing.gateway.metrics_router import create_metrics_router

        app.include_router(create_metrics_router())
    except (
        ImportError,
        AttributeError,
        TypeError,
    ):  # best-effort · optional, proceed without /metrics
        # Metrics module is optional · proceed without /metrics rather
        # than refuse to boot.
        pass

    # K8s liveness / readiness probes (Round 24 wiring).
    # ``/livez`` and ``/readyz`` follow the K8s convention so a
    # standard StatefulSet manifest works out-of-the-box. We seed
    # the registry with a minimal "process is alive" liveness check
    # plus a journal-readability readiness check so the pod doesn't
    # advertise itself before genome storage is reachable.
    try:
        from runtime.platform.observability.health import (
            HealthCheck,
            HealthRegistry,
            create_probe_router,
            effect_store_check,
            journal_check,
        )
        from runtime.platform.observability.metrics import get_registry as _mreg

        _hreg = HealthRegistry(metrics_registry=_mreg())
        _hreg.register(
            HealthCheck(
                name="process",
                check=lambda: True,
                kind="liveness",
            )
        )
        if state.journal is not None:
            _hreg.register(journal_check(state.journal))
        if stack is not None and getattr(stack, "executor", None) is not None:
            _effect_store = stack.executor.effect_store
            if _effect_store is not None:
                _require_distributed = bool(
                    getattr(
                        getattr(stack.config, "tool_effects", None),
                        "require_distributed",
                        False,
                    )
                )
                _hreg.register(
                    effect_store_check(
                        _effect_store,
                        require_distributed=_require_distributed,
                    )
                )
        app.include_router(create_probe_router(_hreg))
        # Stash on app.state so test clients / operators can probe it
        # programmatically and so other routers can register their
        # own checks (e.g. redis_check at startup).
        app.state.health_registry = _hreg
    except (
        ImportError,
        AttributeError,
        TypeError,
        OSError,
    ):  # best-effort · liveness/readiness probes are optional
        pass
