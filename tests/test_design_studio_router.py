from __future__ import annotations

import json
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.platform.process.state import MemoryBackend, StateStore
from runtime.sensing.gateway import design_studio_router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(design_studio_router.create_design_studio_router())
    return TestClient(app)


def test_comfyui_rejects_non_local_service(monkeypatch) -> None:
    monkeypatch.setenv("ECHO_COMFYUI_URL", "https://example.com")
    response = _client().get("/api/design/comfyui/status")
    assert response.status_code == 200
    assert response.json()["state"] == "invalid_config"


def test_comfyui_dependencies_inventory_is_read_only(monkeypatch, tmp_path) -> None:
    (tmp_path / "models" / "checkpoints").mkdir(parents=True)
    (tmp_path / "models" / "loras").mkdir(parents=True)
    (tmp_path / "models" / "checkpoints" / "base.safetensors").write_bytes(b"model")
    (tmp_path / "models" / "loras" / "style.ckpt").write_bytes(b"lora")
    (tmp_path / "models" / "loras" / "notes.txt").write_text("ignore")
    (tmp_path / "custom_nodes" / "ComfyUI-Manager").mkdir(parents=True)
    (tmp_path / "custom_nodes" / "_disabled").mkdir(parents=True)
    monkeypatch.setattr(design_studio_router, "_comfyui_home", lambda: tmp_path)

    response = _client().get("/api/design/comfyui/dependencies")
    assert response.status_code == 200
    payload = response.json()
    assert payload["detected"] is True
    assert payload["total_models"] == 2
    assert payload["model_counts"]["checkpoints"] == 1
    assert payload["model_counts"]["loras"] == 1
    assert payload["custom_nodes"] == ["ComfyUI-Manager"]
    assert payload["managed"] is False


def test_comfyui_lifecycle_routes_delegate_to_owned_supervisor(monkeypatch) -> None:
    class OfflineClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url):
            raise design_studio_router.httpx.ConnectError("offline")

    monkeypatch.setattr(design_studio_router.httpx, "AsyncClient", OfflineClient)
    monkeypatch.setattr(design_studio_router, "start_comfyui", lambda: "started")
    monkeypatch.setattr(design_studio_router, "stop_comfyui", lambda: "stopped")
    monkeypatch.setattr(
        design_studio_router,
        "comfyui_process_status",
        lambda: {"owned": True, "running": True, "pid": 42},
    )
    client = _client()
    started = client.post("/api/design/comfyui/start")
    assert started.status_code == 200
    assert started.json()["state"] == "started"
    assert started.json()["process"]["pid"] == 42

    stopped = client.post("/api/design/comfyui/stop")
    assert stopped.status_code == 200
    assert stopped.json()["state"] == "stopped"


def test_comfyui_managed_install_update_and_cancel_routes(monkeypatch) -> None:
    states = {
        "install": "started",
        "update": "started",
        "cancel": "cancelled",
    }
    status = {
        "managed": True,
        "installed": False,
        "job": {"running": False},
    }
    monkeypatch.setattr(design_studio_router, "manager_status", lambda: status)
    monkeypatch.setattr(
        design_studio_router,
        "start_manager_job",
        lambda action: states[action],
    )
    monkeypatch.setattr(design_studio_router, "cancel_manager_job", lambda: states["cancel"])
    monkeypatch.setattr(
        design_studio_router,
        "comfyui_process_status",
        lambda: {"running": False},
    )
    client = _client()

    assert client.post("/api/design/comfyui/install").json()["state"] == "started"
    assert client.post("/api/design/comfyui/update").json()["state"] == "started"
    assert client.post("/api/design/comfyui/manager/cancel").json()["state"] == "cancelled"
    assert client.get("/api/design/comfyui/manager").json()["managed"] is True


def test_comfyui_custom_node_registry_and_recoverable_actions(monkeypatch) -> None:
    class RegistryResponse:
        status_code = 200

        def __init__(self, node_id):
            self.node_id = node_id

        def json(self):
            return {
                "id": self.node_id,
                "name": "KJ Nodes",
                "description": "Utility nodes",
                "downloads": 42,
                "github_stars": 7,
                "repository": "https://github.com/example/nodes",
                "publisher": {"name": "Example"},
                "latest_version": {
                    "version": "1.2.3",
                    "dependencies": ["numpy"],
                },
            }

    class RegistryClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url):
            return RegistryResponse(url.rsplit("/", 1)[-1])

    calls: list[tuple] = []
    monkeypatch.setattr(design_studio_router.httpx, "AsyncClient", RegistryClient)
    monkeypatch.setattr(
        design_studio_router,
        "_comfyui_dependencies",
        lambda: {"custom_nodes": ["comfyui-kjnodes"]},
    )
    monkeypatch.setattr(
        design_studio_router,
        "comfyui_process_status",
        lambda: {"running": False},
    )
    monkeypatch.setattr(
        design_studio_router,
        "start_manager_job",
        lambda action, node_id=None: calls.append((action, node_id)) or "started",
    )
    monkeypatch.setattr(design_studio_router, "manager_status", lambda: {})
    monkeypatch.setattr(
        design_studio_router,
        "uninstall_managed_node",
        lambda node_id: calls.append(("uninstall", node_id)) or "uninstalled",
    )
    monkeypatch.setattr(
        design_studio_router,
        "rollback_managed_node",
        lambda node_id, backup_id=None: (
            calls.append(("rollback", node_id, backup_id)) or "restored"
        ),
    )
    monkeypatch.setattr(
        design_studio_router,
        "list_node_backups",
        lambda node_id: [{"id": "backup-1", "node_id": node_id}],
    )
    client = _client()

    registry = client.get(
        "/api/design/comfyui/custom-nodes/registry",
        params={"query": "comfyui-kjnodes"},
    )
    assert registry.status_code == 200
    assert registry.json()["items"][0]["installed"] is True
    assert registry.json()["items"][0]["version"] == "1.2.3"

    body = {"node_id": "comfyui-kjnodes"}
    assert client.post("/api/design/comfyui/custom-nodes/install", json=body).json()["ok"] is True
    assert client.post("/api/design/comfyui/custom-nodes/update", json=body).json()["ok"] is True
    assert client.delete("/api/design/comfyui/custom-nodes/comfyui-kjnodes").json()["ok"] is True
    assert (
        client.post(
            "/api/design/comfyui/custom-nodes/comfyui-kjnodes/rollback",
            json={"backup_id": "backup-1"},
        ).json()["ok"]
        is True
    )
    assert calls == [
        ("node_install", "comfyui-kjnodes"),
        ("node_update", "comfyui-kjnodes"),
        ("uninstall", "comfyui-kjnodes"),
        ("rollback", "comfyui-kjnodes", "backup-1"),
    ]


def test_comfyui_custom_node_changes_require_stopped_service(monkeypatch) -> None:
    monkeypatch.setattr(
        design_studio_router,
        "comfyui_process_status",
        lambda: {"running": True},
    )
    response = _client().post(
        "/api/design/comfyui/custom-nodes/install",
        json={"node_id": "comfyui-kjnodes"},
    )
    assert response.status_code == 409


def test_comfyui_model_download_inventory_remove_and_restore(monkeypatch) -> None:
    calls: list[tuple] = []
    monkeypatch.setattr(
        design_studio_router,
        "comfyui_process_status",
        lambda: {"running": False},
    )
    monkeypatch.setattr(
        design_studio_router,
        "list_managed_models",
        lambda: [
            {
                "id": "checkpoints:model.safetensors",
                "group": "checkpoints",
                "name": "model.safetensors",
                "size_bytes": 1024,
            }
        ],
    )
    monkeypatch.setattr(
        design_studio_router,
        "list_model_backups",
        lambda: [{"id": "checkpoints:backup-model.safetensors"}],
    )
    monkeypatch.setattr(
        design_studio_router,
        "start_manager_job",
        lambda action, node_id=None, **kwargs: calls.append((action, node_id, kwargs)) or "started",
    )
    monkeypatch.setattr(design_studio_router, "manager_status", lambda: {})
    monkeypatch.setattr(
        design_studio_router,
        "remove_managed_model",
        lambda group, name: calls.append(("remove", group, name)) or "removed",
    )
    monkeypatch.setattr(
        design_studio_router,
        "restore_managed_model",
        lambda backup_id: calls.append(("restore", backup_id)) or "restored",
    )
    client = _client()

    inventory = client.get("/api/design/comfyui/models")
    assert inventory.status_code == 200
    assert inventory.json()["items"][0]["name"] == "model.safetensors"
    downloaded = client.post(
        "/api/design/comfyui/models/download",
        json={
            "url": "https://huggingface.co/owner/repo/model.safetensors",
            "group": "checkpoints",
        },
    )
    assert downloaded.json()["ok"] is True
    assert (
        client.post(
            "/api/design/comfyui/models/remove",
            json={"group": "checkpoints", "name": "model.safetensors"},
        ).json()["ok"]
        is True
    )
    assert (
        client.post(
            "/api/design/comfyui/models/restore",
            json={"backup_id": "checkpoints:backup-model.safetensors"},
        ).json()["ok"]
        is True
    )
    assert calls == [
        (
            "model_download",
            None,
            {
                "model_url": "https://huggingface.co/owner/repo/model.safetensors",
                "model_group": "checkpoints",
            },
        ),
        ("remove", "checkpoints", "model.safetensors"),
        ("restore", "checkpoints:backup-model.safetensors"),
    ]


def test_comfyui_workflow_import_and_list(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(design_studio_router, "_workflow_dir", lambda: tmp_path)
    client = _client()

    imported = client.post(
        "/api/design/comfyui/workflows/import",
        json={"name": "角色分镜", "workflow": {"1": {"class_type": "CheckpointLoaderSimple"}}},
    )
    assert imported.status_code == 200
    assert imported.json()["ok"] is True

    listed = client.get("/api/design/comfyui/workflows")
    assert listed.status_code == 200
    items = listed.json()["items"]
    imported_item = next(item for item in items if item["id"] == "角色分镜")
    assert imported_item["name"] == "角色分镜"
    assert imported_item["source"] == "user"

    detail = client.get("/api/design/comfyui/workflows/text-to-image")
    assert detail.status_code == 200
    assert detail.json()["source"] == "bundled"
    assert detail.json()["workflow"]["5"]["class_type"] == "KSampler"


def test_comfyui_workflow_diagnostics_finds_node_input_and_model_conflicts(
    monkeypatch, tmp_path
) -> None:
    workflows = tmp_path / "workflows"
    home = tmp_path / "comfyui"
    workflows.mkdir()
    (home / "models" / "checkpoints").mkdir(parents=True)
    monkeypatch.setattr(design_studio_router, "_workflow_dir", lambda: workflows)
    monkeypatch.setattr(design_studio_router, "_comfyui_home", lambda: home)

    class NodeCatalogResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "CheckpointLoaderSimple": {
                    "input": {"required": {"ckpt_name": [["installed.safetensors"], {}]}}
                },
                "KnownNode": {
                    "input": {
                        "required": {
                            "strength": ["FLOAT", {}],
                            "mode": [["fast", "quality"], {}],
                        }
                    }
                },
            }

    class NodeCatalogClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url):
            return NodeCatalogResponse()

    monkeypatch.setattr(design_studio_router.httpx, "AsyncClient", NodeCatalogClient)
    client = _client()
    imported = client.post(
        "/api/design/comfyui/workflows/import",
        json={
            "name": "conflicted",
            "workflow": {
                "1": {
                    "class_type": "CheckpointLoaderSimple",
                    "inputs": {"ckpt_name": "missing.safetensors"},
                },
                "2": {"class_type": "KnownNode", "inputs": {"mode": "turbo"}},
                "3": {"class_type": "MissingCustomNode", "inputs": {}},
            },
        },
    )
    assert imported.status_code == 200
    diagnosed = client.get("/api/design/comfyui/workflows/conflicted/diagnostics")
    assert diagnosed.status_code == 200
    payload = diagnosed.json()
    kinds = {item["kind"] for item in payload["issues"]}
    assert {
        "missing_model",
        "missing_required_input",
        "invalid_enum_value",
        "missing_node_type",
    } <= kinds
    assert payload["compatible"] is False
    assert payload["fullyChecked"] is True
    assert payload["counts"]["errors"] >= 4


def test_comfyui_workflow_save_persists_ui_and_rejects_stale_revision(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(design_studio_router, "_workflow_dir", lambda: tmp_path)
    client = _client()
    body = {
        "name": "产品图工作流",
        "workflow": {
            "1": {
                "class_type": "KSampler",
                "inputs": {"seed": 42, "steps": 24},
            }
        },
        "ui": {"positions": {"1": {"x": 160, "y": 220}}},
        "expected_revision": 0,
    }

    saved = client.put("/api/design/comfyui/workflows/product-shot", json=body)
    assert saved.status_code == 200
    assert saved.json()["revision"] == 1

    loaded = client.get("/api/design/comfyui/workflows/product-shot")
    assert loaded.status_code == 200
    assert loaded.json()["source"] == "user"
    assert loaded.json()["ui"]["positions"]["1"] == {"x": 160, "y": 220}
    assert loaded.json()["workflow"]["1"]["inputs"]["seed"] == 42

    stale = client.put("/api/design/comfyui/workflows/product-shot", json=body)
    assert stale.status_code == 409
    assert stale.json()["detail"] == {
        "code": "WORKFLOW_REVISION_CONFLICT",
        "revision": 1,
    }


def test_comfyui_object_info_returns_compact_local_node_specs(monkeypatch) -> None:
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "KSampler": {
                    "display_name": "KSampler 采样器",
                    "category": "sampling",
                    "input": {
                        "required": {
                            "seed": ["INT", {"default": 0}],
                            "sampler_name": [["euler", "dpmpp_2m"]],
                        },
                        "optional": {"control_after_generate": ["BOOLEAN"]},
                    },
                }
            }

    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url):
            assert url == "http://127.0.0.1:8188/object_info"
            return FakeResponse()

    monkeypatch.setattr(design_studio_router.httpx, "AsyncClient", FakeAsyncClient)
    response = _client().get("/api/design/comfyui/object-info")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["class_type"] == "KSampler"
    assert item["title"] == "KSampler 采样器"
    assert item["inputs"][0]["default"] == 0
    assert item["inputs"][2]["optional"] is True


def test_comfyui_queue_accepts_workflow_id_and_exposes_outputs(monkeypatch, tmp_path) -> None:
    workflow = {
        "name": "测试工作流",
        "workflow": {"1": {"class_type": "KSampler", "inputs": {}}},
    }
    (tmp_path / "workflow-one.json").write_text(json.dumps(workflow), encoding="utf-8")
    monkeypatch.setattr(design_studio_router, "_workflow_dir", lambda: tmp_path)

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, json):
            assert json["prompt"]["1"]["class_type"] == "KSampler"
            return FakeResponse({"prompt_id": "prompt-1"})

        async def get(self, _url):
            return FakeResponse(
                {
                    "prompt-1": {
                        "status": {"completed": True},
                        "outputs": {
                            "9": {
                                "images": [
                                    {
                                        "filename": "result.png",
                                        "subfolder": "final",
                                        "type": "output",
                                    }
                                ]
                            }
                        },
                    }
                }
            )

    monkeypatch.setattr(design_studio_router.httpx, "AsyncClient", FakeAsyncClient)
    client = _client()
    queued = client.post("/api/design/comfyui/queue", json={"workflow_id": "workflow-one"})
    assert queued.status_code == 200
    assert queued.json()["prompt_id"] == "prompt-1"

    result = client.get("/api/design/comfyui/history/prompt-1")
    assert result.status_code == 200
    assert result.json()["state"] == "completed"
    assert result.json()["outputs"][0]["filename"] == "result.png"
    assert "filename=result.png" in result.json()["outputs"][0]["url"]


def test_project_canvas_is_revisioned_and_persistent(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(design_studio_router, "_canvas_dir", lambda: tmp_path)
    client = _client()

    empty = client.get("/api/design/projects/project-1/canvas")
    assert empty.status_code == 200
    assert empty.json()["revision"] == 0
    assert empty.json()["document"] is None

    saved = client.put(
        "/api/design/projects/project-1/canvas",
        json={
            "expected_revision": 0,
            "document": {"title": "发布会画布", "nodes": [], "edges": []},
        },
    )
    assert saved.status_code == 200
    assert saved.json()["revision"] == 1

    loaded = client.get("/api/design/projects/project-1/canvas")
    assert loaded.json()["document"]["title"] == "发布会画布"

    conflict = client.put(
        "/api/design/projects/project-1/canvas",
        json={"expected_revision": 0, "document": {"title": "旧版本"}},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "CANVAS_REVISION_CONFLICT"


def test_project_canvas_rejects_unsafe_project_id(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(design_studio_router, "_canvas_dir", lambda: tmp_path)
    response = _client().get("/api/design/projects/..%2Fprivate/canvas")
    assert response.status_code == 404


def test_project_canvas_presence_heartbeats_lists_leaves_and_expires(monkeypatch, tmp_path) -> None:
    clock = [100.0]
    monkeypatch.setattr(design_studio_router, "_canvas_dir", lambda: tmp_path)
    monkeypatch.setattr(design_studio_router, "_presence_now", lambda: clock[0])
    design_studio_router._CANVAS_PRESENCE.clear()
    client = _client()
    base = "/api/design/projects/project-1/presence"

    first = client.post(
        base,
        json={
            "client_id": "client-alpha",
            "display_name": "Alice",
            "x": 120,
            "y": 240,
            "section": "canvas",
        },
    )
    assert first.status_code == 200
    assert first.json()["items"][0]["display_name"] == "Alice"
    assert first.json()["items"][0]["color"].startswith("#")

    second = client.post(
        base,
        json={
            "client_id": "client-bravo",
            "display_name": "Bob",
            "x": -10,
            "y": 25,
            "section": "assets",
        },
    )
    assert second.status_code == 200
    assert len(second.json()["items"]) == 2

    left = client.delete(f"{base}/client-alpha")
    assert left.status_code == 200
    assert left.json()["left"] is True
    assert [item["display_name"] for item in client.get(base).json()["items"]] == ["Bob"]

    clock[0] += 9
    assert client.get(base).json()["items"] == []
    design_studio_router._CANVAS_PRESENCE.clear()


def test_project_canvas_presence_rejects_invalid_client(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(design_studio_router, "_canvas_dir", lambda: tmp_path)
    response = _client().post(
        "/api/design/projects/project-1/presence",
        json={"client_id": "../bad", "display_name": "Mallory"},
    )
    assert response.status_code == 422


def test_design_asset_library_persists_metadata_and_content(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(design_studio_router, "_asset_library_dir", lambda: tmp_path)
    client = _client()

    created = client.post(
        "/api/design/assets",
        data={
            "name": "  主角设定  ",
            "category": "角色",
            "description": "保持服装、发型与配色一致",
            "tags": "主角, 赛博朋克，主角",
        },
        files={"file": ("hero concept.png", b"image-bytes", "image/png")},
    )
    assert created.status_code == 200
    item = created.json()["item"]
    assert item["name"] == "主角设定"
    assert item["category"] == "角色"
    assert item["tags"] == ["主角", "赛博朋克"]
    assert item["kind"] == "image"

    listing = client.get("/api/design/assets")
    assert listing.status_code == 200
    assert listing.json()["items"] == [item]

    content = client.get(item["url"])
    assert content.status_code == 200
    assert content.content == b"image-bytes"


def test_design_asset_library_rejects_unknown_category(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(design_studio_router, "_asset_library_dir", lambda: tmp_path)
    response = _client().post(
        "/api/design/assets",
        data={"name": "asset", "category": "系统文件"},
        files={"file": ("asset.txt", b"payload", "text/plain")},
    )
    assert response.status_code == 422


def test_design_asset_library_isolates_persona_creation_rooms(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(design_studio_router, "_asset_library_dir", lambda: tmp_path)
    client = _client()

    for persona_id, name in (("luna", "月影角色"), ("kane", "工程角色")):
        response = client.post(
            "/api/design/assets",
            data={"name": name, "category": "角色", "persona_id": persona_id},
            files={"file": (f"{persona_id}.png", persona_id.encode(), "image/png")},
        )
        assert response.status_code == 200

    luna = client.get("/api/design/assets?persona_id=luna").json()["items"]
    kane = client.get("/api/design/assets?persona_id=kane").json()["items"]
    assert [item["name"] for item in luna] == ["月影角色"]
    assert [item["name"] for item in kane] == ["工程角色"]
    assert "persona_id=luna" in luna[0]["url"]
    assert client.get(luna[0]["url"]).content == b"luna"


def test_design_asset_pack_imports_manifest_and_files_atomically(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(design_studio_router, "_asset_library_dir", lambda: tmp_path)
    archive = BytesIO()
    with ZipFile(archive, "w", ZIP_DEFLATED) as bundle:
        bundle.writestr(
            "manifest.json",
            json.dumps(
                {
                    "version": 1,
                    "assets": [
                        {
                            "path": "files/hero.png",
                            "name": "林乔角色设定",
                            "category": "角色",
                            "description": "灰白通勤外套",
                            "tags": ["主角", "白港", "主角"],
                        },
                        {
                            "path": "files/train.mp4",
                            "name": "白港列车",
                            "category": "场景",
                        },
                    ],
                },
                ensure_ascii=False,
            ),
        )
        bundle.writestr("files/hero.png", b"hero-image")
        bundle.writestr("files/train.mp4", b"train-video")
    response = _client().post(
        "/api/design/assets/import-pack",
        files={"file": ("echo-assets.zip", archive.getvalue(), "application/zip")},
    )
    assert response.status_code == 200
    assert response.json()["total"] == 2
    hero = next(item for item in response.json()["items"] if item["category"] == "角色")
    assert hero["tags"] == ["主角", "白港"]
    assert hero["source_pack"] == "echo-assets.zip"
    assert _client().get(hero["url"]).content == b"hero-image"
    listing = _client().get("/api/design/assets").json()
    assert listing["total"] == 2


def test_design_asset_pack_rejects_unsafe_or_incomplete_archives(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(design_studio_router, "_asset_library_dir", lambda: tmp_path)
    archive = BytesIO()
    with ZipFile(archive, "w", ZIP_DEFLATED) as bundle:
        bundle.writestr(
            "manifest.json",
            json.dumps(
                {
                    "assets": [
                        {"path": "files/hero.png", "name": "角色", "category": "角色"},
                        {"path": "files/missing.png", "name": "缺失", "category": "角色"},
                    ]
                }
            ),
        )
        bundle.writestr("files/hero.png", b"hero")
    response = _client().post(
        "/api/design/assets/import-pack",
        files={"file": ("broken.zip", archive.getvalue(), "application/zip")},
    )
    assert response.status_code == 422
    assert _client().get("/api/design/assets").json()["items"] == []

    unsafe = BytesIO()
    with ZipFile(unsafe, "w", ZIP_DEFLATED) as bundle:
        bundle.writestr("manifest.json", json.dumps({"assets": []}))
        bundle.writestr("../escape.txt", b"escape")
    response = _client().post(
        "/api/design/assets/import-pack",
        files={"file": ("unsafe.zip", unsafe.getvalue(), "application/zip")},
    )
    assert response.status_code == 422
    assert not (tmp_path.parent / "escape.txt").exists()


def test_creative_skill_preview_only_returns_safe_text_files(monkeypatch, tmp_path) -> None:
    skill = tmp_path / "creative-preview"
    references = skill / "references"
    references.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Preview\n", encoding="utf-8")
    (references / "guide.md").write_text("safe guide", encoding="utf-8")
    (references / "binary.png").write_bytes(b"not-previewed")
    monkeypatch.setattr(design_studio_router, "_creative_skill_dir", lambda _id: skill)

    response = _client().get("/api/design/skills/creative-preview/files")
    assert response.status_code == 200
    assert response.json()["items"] == [
        {"path": "SKILL.md", "content": "# Preview\n"},
        {"path": "references/guide.md", "content": "safe guide"},
    ]


def test_creative_skill_preview_rejects_unsafe_name() -> None:
    response = _client().get("/api/design/skills/not-creative/files")
    assert response.status_code == 404


def test_plugin_node_state_is_scoped_revisioned_and_deletable() -> None:
    app = FastAPI()
    store = StateStore(backend=MemoryBackend())
    app.include_router(
        design_studio_router.create_design_studio_router(plugin_node_state_store=store)
    )
    client = TestClient(app)
    base = "/api/design/projects/project-1/plugin-nodes/node-1/state"

    empty = client.get(base, params={"plugin_id": "comfyui-bridge"})
    assert empty.status_code == 200
    assert empty.json()["items"] == {}

    created = client.put(
        f"{base}/workflow",
        json={
            "plugin_id": "comfyui-bridge",
            "expected_revision": 0,
            "value": {"nodes": {"1": {"class_type": "KSampler"}}},
        },
    )
    assert created.status_code == 200
    assert created.json()["revision"] == 1

    other_plugin = client.get(base, params={"plugin_id": "clip-studio"})
    assert other_plugin.json()["items"] == {}
    loaded = client.get(base, params={"plugin_id": "comfyui-bridge"})
    assert loaded.json()["revisions"]["workflow"] == 1

    conflict = client.put(
        f"{base}/workflow",
        json={
            "plugin_id": "comfyui-bridge",
            "expected_revision": 0,
            "value": {"nodes": {}},
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "PLUGIN_NODE_STATE_REVISION_CONFLICT"

    deleted = client.delete(
        f"{base}/workflow",
        params={
            "plugin_id": "comfyui-bridge",
            "expected_revision": 1,
        },
    )
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True


def test_plugin_node_state_enforces_value_and_identifier_limits(monkeypatch) -> None:
    app = FastAPI()
    app.include_router(
        design_studio_router.create_design_studio_router(
            plugin_node_state_store=StateStore(backend=MemoryBackend())
        )
    )
    client = TestClient(app)
    base = "/api/design/projects/project-1/plugin-nodes/node-1/state"

    invalid = client.get(base, params={"plugin_id": "../escape"})
    assert invalid.status_code == 422
    monkeypatch.setattr(design_studio_router, "_MAX_PLUGIN_NODE_VALUE_BYTES", 8)
    oversized = client.put(
        f"{base}/data",
        json={
            "plugin_id": "clip-studio",
            "expected_revision": 0,
            "value": "too-large",
        },
    )
    assert oversized.status_code == 413


def test_project_asset_upload_publishes_durable_artifact_and_serves_bytes(
    monkeypatch, tmp_path
) -> None:
    events: list[dict] = []

    class FakeProjectStore:
        def get_project(self, project_id):
            return object() if project_id == "project-1" else None

        def append_event(self, project_id, *, kind, payload):
            events.append({"project_id": project_id, "kind": kind, "payload": payload})
            return {"id": "event-1"}

    monkeypatch.setattr(
        design_studio_router,
        "_project_asset_dir",
        lambda project_id: tmp_path / project_id,
    )
    app = FastAPI()
    app.include_router(
        design_studio_router.create_design_studio_router(project_store=FakeProjectStore())
    )
    client = TestClient(app)
    uploaded = client.post(
        "/api/design/projects/project-1/assets",
        files={"files": ("shot.png", b"\x89PNG-test", "image/png")},
    )
    assert uploaded.status_code == 200
    artifact = uploaded.json()["items"][0]
    assert artifact["kind"] == "image"
    assert artifact["name"] == "shot.png"
    assert events[0]["kind"] == "project.artifact_published"
    assert events[0]["payload"]["artifact"]["id"] == artifact["id"]

    served = client.get(artifact["url"])
    assert served.status_code == 200
    assert served.content == b"\x89PNG-test"

