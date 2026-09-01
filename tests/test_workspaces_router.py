from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from runtime.sensing.gateway.workspaces_router import create_workspaces_router


def _client(root: Path) -> TestClient:
    app = FastAPI()
    app.include_router(create_workspaces_router(workspace_root=root))
    return TestClient(app)


def test_workspace_info_creates_standard_layout(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.get("/api/workspaces/th-1")

    assert response.status_code == 200
    data = response.json()
    root = Path(data["root"])
    assert root == tmp_path.resolve() / "th-1"
    assert data["manifest"]["schema"] == "echo.workspace.v1"
    assert data["manifest"]["thread_id"] == "th-1"
    assert {entry["key"] for entry in data["dirs"]} == {
        "upload",
        "output",
        "stages",
        "final",
        "deploy",
        "skills",
    }
    assert all(entry["exists"] for entry in data["dirs"])
    assert (root / "output" / "stages").is_dir()
    assert (root / "workspace.json").is_file()


def test_thread_workspace_alias_matches_primary_route(tmp_path: Path) -> None:
    client = _client(tmp_path)

    primary = client.get("/api/workspaces/th-alias").json()
    alias = client.get("/api/threads/th-alias/workspace").json()

    assert alias["root"] == primary["root"]
    assert alias["paths"] == primary["paths"]
    assert alias["manifest"]["schema"] == "echo.workspace.v1"


def test_workspace_outputs_list_and_serve_final_files(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.get("/api/workspaces/th-out")
    final = tmp_path.resolve() / "th-out" / "output" / "final" / "report.md"
    final.write_bytes(b"# report\n")

    listing = client.get("/api/workspaces/th-out/outputs?area=final")

    assert listing.status_code == 200
    data = listing.json()
    assert data["area"] == "final"
    assert data["count"] == 1
    assert data["files"][0]["relative_path"] == "report.md"
    assert data["files"][0]["download_url"] == (
        "/api/workspaces/th-out/outputs/report.md?area=final"
    )

    content = client.get("/api/workspaces/th-out/outputs/report.md?area=final")
    assert content.status_code == 200
    assert content.content == b"# report\n"


def test_workspace_outputs_reject_path_traversal(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.get("/api/workspaces/th-out/outputs/%2E%2E%2Fworkspace.json")

    assert response.status_code == 400


def test_thread_outputs_alias_serves_deploy_area(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.get("/api/workspaces/th-deploy")
    deploy = tmp_path.resolve() / "th-deploy" / "deploy" / "index.html"
    deploy.write_text("<h1>ok</h1>", encoding="utf-8")

    listing = client.get("/api/threads/th-deploy/outputs?area=deploy")
    content = client.get("/api/threads/th-deploy/outputs/index.html?area=deploy")

    assert listing.status_code == 200
    assert listing.json()["files"][0]["relative_path"] == "index.html"
    assert content.status_code == 200
    assert content.text == "<h1>ok</h1>"
