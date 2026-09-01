from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from runtime.memory.threads import ThreadStateStore
from runtime.platform.ui.app import create_app


def test_factory_reset_endpoint_requires_confirmation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    app = create_app()
    client = TestClient(app)

    response = client.post("/api/system/factory-reset", json={"confirm": "RESET"})

    assert response.status_code == 400
    assert "RESET ECHO" in response.text


def test_factory_reset_endpoint_clears_runtime_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "threads.jsonl").write_text("thread", encoding="utf-8")
    (tmp_path / ".echo").mkdir()
    (tmp_path / ".echo" / "intelligence.json").write_text("{}", encoding="utf-8")
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "general.txt").write_text("keep", encoding="utf-8")

    app = create_app()
    client = TestClient(app)

    response = client.post(
        "/api/system/factory-reset",
        json={"confirm": "RESET ECHO", "clear_user_install_state": False},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert not (tmp_path / "data").exists()
    assert not (tmp_path / ".echo").exists()
    assert (tmp_path / "agents" / "general.txt").exists()


def _fake_stack() -> SimpleNamespace:
    return SimpleNamespace(
        journal=None,
        executor=SimpleNamespace(journal=None, registry=None),
        runtime=SimpleNamespace(journal=None),
        planner=SimpleNamespace(router=None, planner_model=None),
        config=SimpleNamespace(mcp_servers=None),
        is_llm_planner=False,
    )


def test_factory_reset_endpoint_clears_visible_threads_and_teams(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    app = create_app(stack=_fake_stack())
    client = TestClient(app)

    thread_response = client.post(
        "/api/threads",
        json={"values": {"title": "Old chat"}},
    )
    assert thread_response.status_code == 200
    team_response = client.post(
        "/api/teams",
        json={
            "name": "Old team",
            "members": [{"name": "general", "display_name": "General"}],
        },
    )
    assert team_response.status_code == 200
    assert client.get("/api/threads/search").json()["threads"]
    assert client.get("/api/teams").json()["teams"]

    response = client.post(
        "/api/system/factory-reset",
        json={"confirm": "RESET ECHO", "clear_user_install_state": False},
    )

    assert response.status_code == 200
    assert client.get("/api/threads/search").json()["threads"] == []
    assert client.get("/api/teams").json()["teams"] == []


def test_factory_reset_clears_thread_state_memory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    store = ThreadStateStore(per_agent_base=tmp_path)
    store.ensure_thread(
        "t-1",
        metadata={"agent": "general", "team_id": "family", "mode": "team"},
        values={"title": "hello"},
    )
    assert store.search(limit=10)
    store.clear()
    assert store.search(limit=10) == []
