from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from runtime.sensing.gateway.apps_router import create_apps_router, discover_apps


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_apps_router_discovers_app_actions_from_plugin_pack(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins" / "research-console"
    write(
        plugin_dir / ".codex-plugin" / "plugin.json",
        json.dumps(
            {
                "name": "research-console",
                "version": "0.1.0",
                "description": "Research UI",
            }
        ),
    )
    write(
        plugin_dir / "echo-app.jsonc",
        """
{
  "schema_version": "1",
  "apps": {
    "research-console": {
      "title": "Research Console",
      "description": "Review briefs",
      "category": "research",
      "route": "/apps/research-console",
      "actions": [
        {
          "name": "open_brief",
          "description": "Open a saved brief",
          "input_schema": {"type": "object"},
        },
      ],
    },
  },
}
""",
    )

    apps = discover_apps([tmp_path / "plugins"])
    assert [app["id"] for app in apps] == ["research-console"]
    assert apps[0]["name"] == "Research Console"
    assert apps[0]["category"] == "research"
    assert apps[0]["route"] == "/apps/research-console"
    assert apps[0]["actions"][0]["name"] == "open_brief"

    app = FastAPI()
    app.include_router(create_apps_router(app_roots=[tmp_path / "plugins"]))
    client = TestClient(app)

    assert client.get("/api/apps").json()[0]["id"] == "research-console"
    assert client.get("/api/apps/research-console").json()["actions"][0]["name"] == "open_brief"
    assert client.get("/api/apps/missing").status_code == 404


def test_apps_router_skips_broken_plugin_pack(
    tmp_path: Path,
    monkeypatch,
) -> None:
    for name in ("good", "broken"):
        write(
            tmp_path / "plugins" / name / ".codex-plugin" / "plugin.json",
            json.dumps({"name": name, "version": "0.1.0"}),
        )
    write(
        tmp_path / "plugins" / "good" / "echo-app.jsonc",
        """
{
  "apps": {
    "good": {
      "title": "Good App",
      "description": "Survives a neighboring bad plugin",
      "route": "/apps/good",
    },
  },
}
""",
    )

    from runtime.sensing.gateway import apps_router

    original_scan = apps_router.scan_agent_pack

    def scan_or_boom(plugin_dir: Path):
        if plugin_dir.name == "broken":
            raise RuntimeError("bad plugin metadata")
        return original_scan(plugin_dir)

    monkeypatch.setattr(apps_router, "scan_agent_pack", scan_or_boom)

    app = FastAPI()
    app.include_router(create_apps_router(app_roots=[tmp_path / "plugins"]))
    client = TestClient(app)

    r = client.get("/api/apps")
    assert r.status_code == 200
    assert [item["id"] for item in r.json()] == ["good"]
