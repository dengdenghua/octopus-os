"""Local-brain readiness checklist for the setup wizard (injectable probes)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.sensing.gateway import local_brain as lb


def _status(**kw):
    base = dict(
        ollama_probe=lambda: None,
        storage_probe=lambda: None,
        embed_info=lambda: {"kind": "in_process", "model": "all-MiniLM-L6-v2"},
        index_db=Path("/nonexistent/code_index.db"),
    )
    base.update(kw)
    return lb.local_brain_status(**base)


def _item(status, item_id):
    return next(i for i in status["items"] if i["id"] == item_id)


def test_nothing_running_guides_to_first_steps() -> None:
    s = _status()
    assert s["ready"] is False
    assert s["core_ready"] is False
    assert all(not i["ok"] for i in s["items"])
    assert _item(s, "ollama")["action"]  # every not-ok item carries a next step
    assert "本地模型服务" in s["summary"]
    assert len(s["items"]) == 5


def test_core_ready_when_ollama_and_chat_model() -> None:
    s = _status(ollama_probe=lambda: {"models": [{"name": "qwen2.5:7b"}]})
    assert _item(s, "ollama")["ok"] is True
    assert _item(s, "chat")["ok"] is True
    assert _item(s, "embedding")["ok"] is False  # still in_process, not unified
    assert s["core_ready"] is True
    assert s["ready"] is False


def test_fully_ready(tmp_path: Path) -> None:
    db = tmp_path / "code_index.db"
    db.write_text("x")
    s = _status(
        ollama_probe=lambda: {"models": [{"name": "qwen2.5"}, {"name": "bge-m3"}]},
        storage_probe=lambda: {"role": "storage"},
        embed_info=lambda: {"kind": "remote", "model": "bge-m3"},
        index_db=db,
    )
    assert s["ready"] is True
    assert all(i["ok"] for i in s["items"])
    assert "全部就绪" in s["summary"]
    assert all(i["action"] == "" for i in s["items"])  # nothing left to do


def test_embedding_item_needs_remote_backend_and_a_pulled_model() -> None:
    # Ollama up + an embed model pulled, but the backend is still in_process →
    # not unified → not ok, and the action names the env vars to set.
    s = _status(
        ollama_probe=lambda: {"models": [{"name": "qwen2.5"}, {"name": "nomic-embed-text"}]},
        embed_info=lambda: {"kind": "in_process", "model": "all-MiniLM-L6-v2"},
    )
    assert _item(s, "embedding")["ok"] is False
    assert "ECHO_EMBED_URL" in _item(s, "embedding")["action"]


def test_storage_optional_does_not_block_core() -> None:
    s = _status(
        ollama_probe=lambda: {"models": [{"name": "qwen2.5"}]},
        storage_probe=lambda: None,  # storage down
    )
    assert _item(s, "storage")["ok"] is False
    assert s["core_ready"] is True  # storage isn't part of core readiness


def test_model_classification_helpers() -> None:
    assert lb._is_embed("bge-m3") is True
    assert lb._is_embed("nomic-embed-text") is True
    assert lb._is_embed("qwen2.5:7b") is False
    assert lb._model_names({"models": [{"name": "a"}, {"name": "b"}]}) == ["a", "b"]
    assert lb._model_names(None) == []


def test_router_exposes_status_route() -> None:
    from runtime.sensing.gateway.local_brain_router import create_local_brain_router

    router = create_local_brain_router()
    paths = {getattr(r, "path", None) for r in router.routes}
    assert "/api/local-brain/status" in paths
    assert "/api/local-brain/storage/start" in paths


def test_storage_start_returns_same_origin_proxy_without_storage_token(monkeypatch) -> None:
    from runtime.execution.suckers import storage_skills
    from runtime.sensing.gateway import storage_supervisor
    from runtime.sensing.gateway.local_brain_router import create_local_brain_router

    monkeypatch.setattr(
        storage_supervisor,
        "maybe_start_storage",
        lambda *, force=False: "already_running" if force else "disabled",
    )
    monkeypatch.setattr(storage_skills, "storage_alive", lambda **_kwargs: True)

    app = FastAPI()
    app.include_router(create_local_brain_router())
    client = TestClient(app, client=("127.0.0.1", 45678))
    response = client.post("/api/local-brain/storage/start")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "status": "already_running",
        "base_url": "/api/storage",
        "auth_token": None,
    }

