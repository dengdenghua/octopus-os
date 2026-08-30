"""Phase 0-A regression tests for commercial deployment guardrails."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from runtime.platform.ui.health_router import create_health_router  # noqa: E402
from runtime.safety.auth import Identity, IdentityStore  # noqa: E402
from runtime.sensing.gateway.agent_trace_router import (  # noqa: E402
    create_agent_trace_router,
)
from runtime.sensing.gateway.media_router import create_media_router  # noqa: E402


def _store() -> IdentityStore:
    store = IdentityStore()
    store.add(Identity(actor_id="alice"), api_key_plaintext="sk-alice")
    return store


def test_network_bind_guard_is_fail_closed() -> None:
    from runtime.cli_serve import _insecure_bind_error

    assert _insecure_bind_error(host="0.0.0.0", uds=None, require_auth=False)
    assert _insecure_bind_error(host="example.internal", uds=None, require_auth=False)
    assert _insecure_bind_error(host="127.0.0.1", uds=None, require_auth=False) is None
    assert _insecure_bind_error(host="0.0.0.0", uds=None, require_auth=True) is None
    assert _insecure_bind_error(host="0.0.0.0", uds="/tmp/echo.sock", require_auth=False) is None


def test_serve_refuses_network_bind_without_auth(tmp_path: Path, capsys) -> None:
    from runtime.cli import run_serve

    config = tmp_path / "config.yaml"
    config.write_text(
        "name: phase0a\nplanner:\n  type: static\n",
        encoding="utf-8",
    )

    assert (
        run_serve(
            config_path=config,
            host="0.0.0.0",
            port=8000,
            learn_interval_s=0,
            color=False,
        )
        == 2
    )
    assert "security error" in capsys.readouterr().err


def test_media_router_requires_auth_when_enabled() -> None:
    app = FastAPI()
    app.include_router(
        create_media_router(identity_store=_store(), require_auth=True),
        prefix="/media",
    )
    client = TestClient(app)

    assert client.post("/media/video/index", json={}).status_code == 401


def test_media_router_fails_closed_without_identity_store() -> None:
    app = FastAPI()
    app.include_router(create_media_router(require_auth=True), prefix="/media")
    assert TestClient(app).post("/media/video/index", json={}).status_code == 401


def test_agent_trace_router_requires_auth_when_enabled(tmp_path: Path) -> None:
    from runtime.memory.diagnostics.trace_store import AgentTraceStore

    app = FastAPI()
    app.include_router(
        create_agent_trace_router(
            store=AgentTraceStore(tmp_path / "trace.sqlite"),
            identity_store=_store(),
            require_auth=True,
        )
    )
    client = TestClient(app)

    assert client.get("/api/agent-trace/stats").status_code == 401


def test_capability_enable_requires_auth_when_enabled() -> None:
    app = FastAPI()
    state = SimpleNamespace(registry=[], journal=None)
    app.include_router(
        create_health_router(
            state=state,
            identity_store=_store(),
            require_auth=True,
        )
    )
    client = TestClient(app)

    assert client.post("/api/capabilities/enable", json={}).status_code == 401


def test_evolution_control_plane_requires_operator_in_shared_mode() -> None:
    from runtime.sensing.gateway.evolution_router import create_evolution_router

    identities = IdentityStore()
    identities.add(Identity(actor_id="alice"), api_key_plaintext="sk-alice")
    identities.add(
        Identity(actor_id="ops", roles=("operator",)),
        api_key_plaintext="sk-ops",
    )
    app = FastAPI()
    app.include_router(create_evolution_router(identity_store=identities, require_auth=True))
    client = TestClient(app)
    assert client.get("/api/evolution/codex-gap").status_code == 401
    assert (
        client.get(
            "/api/evolution/codex-gap", headers={"Authorization": "Bearer sk-alice"}
        ).status_code
        == 403
    )
    assert (
        client.get(
            "/api/evolution/codex-gap", headers={"Authorization": "Bearer sk-ops"}
        ).status_code
        == 200
    )

