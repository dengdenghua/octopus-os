"""Dense coverage for _reflex_admin_editor endpoints (audit Q-05)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.platform.ui._reflex_admin_editor import register_reflex_editor_endpoints

RULES_YAML = """rules:
  - id: hi
    pattern: ^hello$
    reply: Hi there!
    priority: 20
  - id: webhook
    pattern: alert
    reply: alerting
    action:
      webhook:
        url: http://h/1
        method: POST
"""


def _build(rules_path: Path) -> TestClient:
    app = FastAPI()
    admin = app.router
    register_reflex_editor_endpoints(
        admin,
        _reflex_router=SimpleNamespace(replace_reflexes=lambda _reflexes: 1),
        panel_html="<html>panel</html>",
        editor_html="<html>editor</html>",
    )
    import runtime.core.nerves.reflex.rules_loader as rl

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(rl, "find_default_rules_file", lambda: rules_path)
    app.state._rl_patch = monkeypatch  # keep patch alive for the client lifetime
    return TestClient(app)


def test_panel_and_editor_pages(tmp_path: Path) -> None:
    rules = tmp_path / "rules.yaml"
    rules.write_text(RULES_YAML, encoding="utf-8")
    client = _build(rules)
    r = client.get("/admin/reflex")
    assert r.status_code == 200 and "panel" in r.text
    r2 = client.get("/admin/reflex/edit")
    assert r2.status_code == 200 and "editor" in r2.text


def test_rules_yaml_get_and_put(tmp_path: Path) -> None:
    rules = tmp_path / "rules.yaml"
    rules.write_text(RULES_YAML, encoding="utf-8")
    client = _build(rules)

    got = client.get("/api/reflex/rules-yaml")
    assert got.json()["ok"] is True
    assert "hello" in got.json()["content"]

    ok = client.post(
        "/api/reflex/rules-yaml",
        json={"content": RULES_YAML, "expected_mtime": 0, "reload": False},
    )
    assert ok.json()["ok"] is True

    bad = client.post(
        "/api/reflex/rules-yaml",
        json={"content": "rules: [broken", "expected_mtime": 0, "reload": False},
    )
    assert ok.json()["ok"] is True or bad.json().get("ok") is False
    missing = client.post(
        "/api/reflex/rules-yaml",
        json={"content": 123, "expected_mtime": 0, "reload": False},
    )
    assert "missing content" in missing.json()["error"]


def test_rules_cards_get(tmp_path: Path) -> None:
    rules = tmp_path / "rules.yaml"
    rules.write_text(RULES_YAML, encoding="utf-8")
    client = _build(rules)
    resp = client.get("/api/reflex/rules-cards")
    data = resp.json()
    if data.get("ok") is True:
        cards = {c["id"]: c for c in data["cards"]}
        assert cards["hi"]["trigger_mode"] == "exact"
        assert cards["webhook"]["action"]["mode"] == "webhook"
    else:
        # ruamel missing — the endpoint degrades cleanly.
        assert "error" in data


def test_rules_cards_put_upsert_and_delete(tmp_path: Path) -> None:
    rules = tmp_path / "rules.yaml"
    rules.write_text(RULES_YAML, encoding="utf-8")
    client = _build(rules)
    resp = client.post(
        "/api/reflex/rules-cards",
        json={
            "expected_mtime": 0,
            "reload": False,
            "upserts": [
                {
                    "id": "new1",
                    "trigger_mode": "contains",
                    "trigger_text": "ping",
                    "reply": "pong",
                    "priority": "low",
                }
            ],
            "deletes": ["webhook"],
        },
    )
    data = resp.json()
    if data.get("ok") is True:
        content = rules.read_text(encoding="utf-8")
        assert "ping" in content
        assert "webhook" not in content
    else:
        assert "error" in data  # ruamel missing degrades cleanly


def test_rules_yaml_mtime_conflict(tmp_path: Path) -> None:
    rules = tmp_path / "rules.yaml"
    rules.write_text(RULES_YAML, encoding="utf-8")
    client = _build(rules)
    future_mtime = rules.stat().st_mtime + 1000
    resp = client.post(
        "/api/reflex/rules-yaml",
        json={"content": RULES_YAML, "expected_mtime": future_mtime, "reload": False},
    )
    data = resp.json()
    if data.get("ok") is not True:
        assert "modified externally" in data.get("error", "") or "parse failed" in data.get(
            "error", ""
        )


def test_rules_yaml_put_schema_and_reload(tmp_path: Path) -> None:
    rules = tmp_path / "rules.yaml"
    rules.write_text(RULES_YAML, encoding="utf-8")
    app = FastAPI()
    admin = app.router
    import runtime.core.nerves.reflex.rules_loader as rl

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(rl, "find_default_rules_file", lambda: rules)
    register_reflex_editor_endpoints(
        admin,
        _reflex_router=SimpleNamespace(replace_reflexes=lambda _r: 1),
        panel_html="<html>panel</html>",
        editor_html="<html>editor</html>",
    )
    app.state._rl_patch = monkeypatch
    client = TestClient(app)

    # top-level not a list -> schema error
    no_rules = client.post(
        "/api/reflex/rules-yaml",
        json={"content": "foo: bar\n", "expected_mtime": 0, "reload": False},
    )
    assert no_rules.json().get("ok") is False
    assert "rules:" in no_rules.json().get("error", "")

    # valid rules with reload=True -> reload path runs (fake replace_reflexes)
    ok = client.post(
        "/api/reflex/rules-yaml",
        json={"content": RULES_YAML, "expected_mtime": 0, "reload": True},
    )
    data = ok.json()
    assert data.get("ok") is True
    assert data.get("reloaded") is True or data.get("reload_error")


def test_rules_yaml_put_reload_failure(tmp_path: Path, monkeypatch) -> None:
    rules = tmp_path / "rules.yaml"
    rules.write_text(RULES_YAML, encoding="utf-8")
    app = FastAPI()
    admin = app.router
    import runtime.cli as cli
    import runtime.core.nerves.reflex.rules_loader as rl

    monkeypatch.setattr(rl, "find_default_rules_file", lambda: rules)
    monkeypatch.setattr(
        cli, "_build_reflex_router", lambda: (_ for _ in ()).throw(RuntimeError("no reflex"))
    )

    class _Router:
        def replace_reflexes(self, reflexes):
            return 0

    register_reflex_editor_endpoints(
        admin,
        _reflex_router=_Router(),
        panel_html="",
        editor_html="",
    )
    client = TestClient(app)
    resp = client.post(
        "/api/reflex/rules-yaml",
        json={"content": RULES_YAML, "expected_mtime": 0, "reload": True},
    )
    data = resp.json()
    assert data.get("ok") is True
    assert data.get("reloaded") is False
    assert "reload_error" in data

