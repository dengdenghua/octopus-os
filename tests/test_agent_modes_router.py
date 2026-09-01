from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.safety.auth import Identity, IdentityStore
from runtime.sensing.gateway.agent_modes_router import create_agent_modes_router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(create_agent_modes_router())
    return TestClient(app)


def test_list_agent_modes_exposes_builder_coder_architect() -> None:
    res = _client().get("/api/agent-modes")

    assert res.status_code == 200
    names = {item["name"] for item in res.json()["modes"]}
    assert {"builder", "coder", "architect"}.issubset(names)


def test_detect_empty_workspace_recommends_builder(tmp_path: Path) -> None:
    res = _client().get(
        "/api/agent-modes/detect",
        params={"workspace_path": str(tmp_path)},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["recommended_mode"] == "builder"
    assert body["signals"]["exists"] is True


def test_detect_existing_project_recommends_coder(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts":{"typecheck":"tsc --noEmit","test":"vitest","build":"vite build"}}',
        encoding="utf-8",
    )
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: 9.0", encoding="utf-8")
    (tmp_path / "src").mkdir()

    res = _client().get(
        "/api/agent-modes/detect",
        params={"workspace_path": str(tmp_path)},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["recommended_mode"] == "coder"
    assert "package.json" in body["signals"]["manifests"]
    assert "pnpm-lock.yaml" in body["signals"]["lock_files"]
    commands = body["signals"]["commands"]
    assert {
        "kind": "typecheck",
        "command": "pnpm run typecheck",
        "source": "package.json scripts.typecheck",
    } in commands
    assert {
        "kind": "test",
        "command": "pnpm run test",
        "source": "package.json scripts.test",
    } in commands


def test_detect_python_project_surfaces_local_verification_commands(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[build-system]
requires = ["setuptools"]

[tool.ruff]
line-length = 100

[tool.mypy]
python_version = "3.11"
""",
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir()

    res = _client().get(
        "/api/agent-modes/detect",
        params={"workspace_path": str(tmp_path)},
    )

    assert res.status_code == 200
    commands = res.json()["signals"]["commands"]
    assert {
        "kind": "lint",
        "command": "ruff check .",
        "source": "pyproject.toml/requirements",
    } in commands
    assert {
        "kind": "typecheck",
        "command": "mypy .",
        "source": "pyproject.toml/requirements",
    } in commands
    assert any(
        item["kind"] == "test" and item["command"] == "python -m pytest" for item in commands
    )


def test_detect_rejects_relative_workspace_path() -> None:
    res = _client().get(
        "/api/agent-modes/detect",
        params={"workspace_path": "relative/path"},
    )

    assert res.status_code == 400


def test_authenticated_loopback_user_can_detect_their_selected_project(
    tmp_path: Path,
) -> None:
    identities = IdentityStore()
    identities.add(Identity(actor_id="alice"), api_key_plaintext="sk-alice")
    app = FastAPI()
    app.include_router(
        create_agent_modes_router(
            identity_store=identities,
            require_auth=True,
            allow_local_workspace_access=True,
        )
    )
    client = TestClient(app)

    response = client.get(
        "/api/agent-modes/detect",
        headers={"Authorization": "Bearer sk-alice"},
        params={"workspace_path": str(tmp_path)},
    )

    assert response.status_code == 200
    assert response.json()["signals"]["workspace_path"] == str(tmp_path)


def test_authenticated_shared_user_still_needs_operator_for_host_path_scan(
    tmp_path: Path,
) -> None:
    identities = IdentityStore()
    identities.add(Identity(actor_id="alice"), api_key_plaintext="sk-alice")
    app = FastAPI()
    app.include_router(
        create_agent_modes_router(
            identity_store=identities,
            require_auth=True,
            allow_local_workspace_access=False,
        )
    )
    client = TestClient(app)

    response = client.get(
        "/api/agent-modes/detect",
        headers={"Authorization": "Bearer sk-alice"},
        params={"workspace_path": str(tmp_path)},
    )

    assert response.status_code == 403


@pytest.mark.parametrize(
    "mode",
    ["develop", "audit", "uxui", "builder", "coder", "architect"],
)
def test_set_current_mode_accepts_task_strategies_and_legacy_project_kinds(
    mode: str,
) -> None:
    res = _client().put(
        "/api/agent-modes/current",
        json={"mode": mode, "session_id": "session-1"},
    )

    assert res.status_code == 200
    assert res.json() == {"ok": True, "mode": mode, "session_id": "session-1"}


def test_set_current_mode_rejects_unknown_mode_with_supported_names() -> None:
    res = _client().put(
        "/api/agent-modes/current",
        json={"mode": "magic", "session_id": "session-1"},
    )

    assert res.status_code == 400
    assert "develop/audit/uxui" in res.json()["detail"]
    assert "builder/coder/architect" in res.json()["detail"]

