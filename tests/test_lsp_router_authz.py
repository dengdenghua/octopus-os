"""Authorization regressions for the host-capable LSP HTTP bridge."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.memory.threads import ThreadStateStore
from runtime.safety.auth import Identity, IdentityStore
from runtime.sensing.gateway.lsp_router import create_lsp_router
from runtime.sensing.gateway.thread_workspace import ensure_managed_thread_workspace


class _Registry:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def has(self, _name: str) -> bool:
        return True

    def get(self, name: str) -> Any:
        def _handler(**kwargs: Any) -> dict[str, Any]:
            self.calls.append({"name": name, **kwargs})
            return {"ok": True}

        return SimpleNamespace(handler=_handler)


def _secured_lsp_client(
    tmp_path: Path,
) -> tuple[TestClient, _Registry, Path, dict[str, str]]:
    identities = IdentityStore()
    for actor in ("alice", "bob"):
        identities.add(
            Identity(actor_id=actor, metadata={"tenant_id": "tenant-a"}),
            api_key_plaintext=f"sk-{actor}",
        )
    store = ThreadStateStore()
    workspace_root = tmp_path / "managed"
    managed = ensure_managed_thread_workspace(
        workspace_root,
        thread_id="alice-thread",
        actor_id="alice",
        tenant_id="tenant-a",
        store=store,
    )
    registry = _Registry()
    app = FastAPI()
    app.include_router(
        create_lsp_router(
            registry,
            thread_store=store,
            workspace_root=workspace_root,
            identity_store=identities,
            require_auth=True,
        )
    )
    return (
        TestClient(app),
        registry,
        managed,
        {
            "alice": "Bearer sk-alice",
            "bob": "Bearer sk-bob",
        },
    )


@pytest.mark.parametrize(
    ("endpoint", "extra"),
    [
        ("definition", {"symbol": "target"}),
        ("references", {"symbol": "target"}),
        ("diagnostics", {}),
    ],
)
def test_authenticated_lsp_uses_authoritative_thread_workspace(
    tmp_path: Path,
    endpoint: str,
    extra: dict[str, str],
) -> None:
    client, registry, managed, tokens = _secured_lsp_client(tmp_path)
    source = managed / "source.py"
    source.write_text("target = 1\n", encoding="utf-8")

    response = client.post(
        f"/api/lsp/{endpoint}",
        headers={"Authorization": tokens["alice"]},
        json={
            "thread_id": "alice-thread",
            "workspace": "/",
            "path": str(source),
            **extra,
        },
    )

    assert response.status_code == 200, response.json()
    assert registry.calls[-1]["sandbox_dir"] == str(managed)
    assert registry.calls[-1]["path"] == str(source.resolve())


def test_authenticated_lsp_blocks_foreign_thread_and_host_path(tmp_path: Path) -> None:
    client, registry, managed, tokens = _secured_lsp_client(tmp_path)
    source = managed / "source.py"
    source.write_text("target = 1\n", encoding="utf-8")

    foreign = client.post(
        "/api/lsp/diagnostics",
        headers={"Authorization": tokens["bob"]},
        json={"thread_id": "alice-thread", "workspace": str(managed), "path": str(source)},
    )
    assert foreign.status_code == 404

    escaped = client.post(
        "/api/lsp/diagnostics",
        headers={"Authorization": tokens["alice"]},
        json={"thread_id": "alice-thread", "workspace": "/", "path": "/etc/hosts"},
    )
    assert escaped.status_code == 403
    assert registry.calls == []


def test_authenticated_lsp_supports_exact_owned_workspace_compat_payload(
    tmp_path: Path,
) -> None:
    client, registry, managed, tokens = _secured_lsp_client(tmp_path)
    source = managed / "source.py"
    source.write_text("target = 1\n", encoding="utf-8")

    response = client.post(
        "/api/lsp/diagnostics",
        headers={"Authorization": tokens["alice"]},
        json={"workspace": str(managed), "path": str(source)},
    )
    assert response.status_code == 200, response.json()
    assert registry.calls[-1]["sandbox_dir"] == str(managed)

    unscoped = client.post(
        "/api/lsp/diagnostics",
        headers={"Authorization": tokens["alice"]},
        json={"path": str(source)},
    )
    assert unscoped.status_code == 403


def test_local_lsp_keeps_client_selected_workspace(tmp_path: Path) -> None:
    registry = _Registry()
    app = FastAPI()
    app.include_router(create_lsp_router(registry))
    client = TestClient(app)
    source = tmp_path / "source.py"
    source.write_text("target = 1\n", encoding="utf-8")

    response = client.post(
        "/api/lsp/diagnostics",
        json={"workspace": str(tmp_path), "path": str(source)},
    )
    assert response.status_code == 200, response.json()
    assert registry.calls[-1]["sandbox_dir"] == str(tmp_path)
    assert registry.calls[-1]["path"] == str(source)

