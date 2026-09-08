from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.sensing.gateway.workspaces_router import create_workspaces_router


def _client(root: Path) -> TestClient:
    app = FastAPI()
    app.include_router(create_workspaces_router(workspace_root=root))
    return TestClient(app)


def test_locate_resolves_the_same_output_without_reading_its_content(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.get("/api/workspaces/task")
    target = tmp_path / "task" / "output" / "final" / "report #1.txt"
    target.write_text("private content", encoding="utf-8")
    for prefix in ("threads", "workspaces"):
        response = client.get(f"/api/{prefix}/task/outputs/report%20%231.txt?area=final&locate=true")
        assert response.status_code == 200
        assert Path(response.json()["path"]) == target.resolve()
        assert response.json()["thread_id"] == "task"
        assert response.headers["cache-control"] == "no-store"
        assert "private content" not in response.text


def test_locate_rejects_missing_files_and_traversal(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.get("/api/threads/task/outputs/missing.txt?locate=true").status_code == 404
    assert client.get("/api/threads/task/outputs/%2E%2E%2Fworkspace.json?locate=true").status_code == 400


def test_locate_does_not_disclose_another_actors_path(tmp_path: Path, monkeypatch) -> None:
    from runtime.sensing.gateway import openai_gateway_router

    monkeypatch.setattr(openai_gateway_router, "_resolve_actor", lambda *args, **kwargs: "visitor")
    app = FastAPI()
    app.include_router(create_workspaces_router(
        workspace_root=tmp_path,
        thread_store={"task": {"metadata": {"owner_actor_id": "owner"}}},
    ))
    response = TestClient(app).get("/api/threads/task/outputs/report.txt?locate=true")
    assert response.status_code == 404
    assert str(tmp_path) not in response.text
    from runtime.platform.resource_identity import workspace_file_resource_id

    resource = workspace_file_resource_id("task", "output", "report.txt")
    resource_response = TestClient(app).get(f"/api/workspace-resources/{resource}")
    assert resource_response.status_code == 404
    assert str(tmp_path) not in resource_response.text


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
    assert data["files"][0]["resource_id"].startswith("workspace-file:v1:")
    assert data["files"][0]["download_url"] == (
        "/api/workspaces/th-out/outputs/report.md?area=final"
    )

    content = client.get("/api/workspaces/th-out/outputs/report.md?area=final")
    assert content.status_code == 200
    assert content.content == b"# report\n"


def test_locate_returns_the_same_stable_resource_identity(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.get("/api/workspaces/th-resource")
    target = tmp_path.resolve() / "th-resource" / "output" / "final" / "report.md"
    target.write_text("# report\n", encoding="utf-8")

    listing = client.get("/api/workspaces/th-resource/outputs?area=final").json()
    located = client.get(
        "/api/workspaces/th-resource/outputs/report.md?area=final&locate=true"
    ).json()

    assert located["resource_id"] == listing["files"][0]["resource_id"]
    resource_url = f"/api/workspace-resources/{located['resource_id']}"
    resolved = client.get(resource_url)
    assert resolved.status_code == 200
    assert resolved.text.replace("\r\n", "\n") == "# report\n"


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


def test_workspace_resource_identity_supports_optimistic_edit_and_restore(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    client.get("/api/workspaces/th-edit")
    target = tmp_path.resolve() / "th-edit" / "output" / "final" / "site.html"
    target.write_text("<h1>old</h1>", encoding="utf-8")
    from runtime.platform.resource_identity import workspace_file_resource_id

    resource = workspace_file_resource_id("th-edit", "final", "site.html")
    old_digest = hashlib.sha256(target.read_bytes()).hexdigest()
    edited = client.put(
        f"/api/workspace-resources/{resource}",
        json={"content": "<h1>new</h1>", "expected_sha256": old_digest},
    )
    assert edited.status_code == 200
    revision_id = edited.json()["revision_id"]
    assert target.read_text(encoding="utf-8") == "<h1>new</h1>"

    new_digest = hashlib.sha256(target.read_bytes()).hexdigest()
    restored = client.post(
        f"/api/workspace-resources/{resource}",
        json={"revision_id": revision_id, "expected_sha256": new_digest},
    )
    assert restored.status_code == 200
    assert target.read_text(encoding="utf-8") == "<h1>old</h1>"
