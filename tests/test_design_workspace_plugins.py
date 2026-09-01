from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from runtime.execution.suckers.registry import SkillRegistry
from runtime.platform.plugins.bundled import director_stage
from runtime.platform.plugins.bundled.comfyui_bridge import ComfyUIBridgePlugin
from runtime.platform.plugins.bundled.director_stage import DirectorStagePlugin
from runtime.platform.plugins.plugin_base import ModuleContext
from runtime.platform.plugins.plugin_hub import PluginHub

ROOT = Path(__file__).resolve().parents[1]
BUNDLED = ROOT / "runtime/platform/plugins/bundled"


def _load(plugin, name: str, config: dict | None = None) -> TestClient:
    app = FastAPI()
    plugin.on_load(
        ModuleContext(
            plugin_name=name,
            plugin_dir=str(BUNDLED / name),
            manifest=None,
            fastapi_app=app,
            config=config or {},
        )
    )
    return TestClient(app)


def test_director_stage_contract_is_discoverable_and_served() -> None:
    client = _load(DirectorStagePlugin(), "director_stage")
    assert client.get("/api/plugins/director-stage/health").json()["ok"] is True
    schema = client.get("/api/plugins/director-stage/scene-schema").json()
    assert schema["coordinate_system"] == "right_handed_y_up"
    assert "camera_path" in schema["timeline_tracks"]
    assert "object_path" in schema["timeline_tracks"]
    assert "bench" in schema["prop_catalog"]
    assert "prop" in schema["entities"]
    assert len(schema["poses"]) == 19


def test_director_stage_persists_atomic_scene_edits(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(director_stage, "_scenes_dir", lambda: tmp_path)
    monkeypatch.setattr(
        director_stage, "_scene_snapshot_dir", lambda scene_id: tmp_path / "visual" / scene_id
    )
    client = _load(DirectorStagePlugin(), "director_stage")
    initial = client.get("/api/plugins/director-stage/scenes/film?view=full").json()
    camera_id = next(item["id"] for item in initial["entities"] if item["type"] == "camera")
    response = client.post(
        "/api/plugins/director-stage/scenes/film/edit",
        json={
            "operations": [
                {"type": "set_pose", "entityId": "character-1", "pose": "sword"},
                {
                    "type": "add_prop",
                    "assetId": "bench",
                    "position": [1.5, 0, 0],
                    "rotationY": 90,
                    "label": "候车长椅",
                },
                {
                    "type": "add_camera_path",
                    "cameraId": camera_id,
                    "points": [[0, 1.6, 5], [2, 2, 3]],
                    "durationSec": 8,
                },
            ]
        },
    )
    assert response.json()["applied"] == 3
    assert client.get("/api/plugins/director-stage/scenes/film/snapshot").status_code == 409
    buffer = BytesIO()
    Image.new("RGB", (320, 180), "#dce6f2").save(buffer, format="PNG")
    uploaded = client.put(
        "/api/plugins/director-stage/scenes/film/visual-snapshot",
        json={
            "data_url": "data:image/png;base64,"
            + base64.b64encode(buffer.getvalue()).decode("ascii"),
            "view": "director",
        },
    )
    assert uploaded.status_code == 200
    snapshot = client.get("/api/plugins/director-stage/scenes/film/snapshot").json()
    assert snapshot["visualEvidence"] is True
    assert Image.open(snapshot["frames"][0]["path"]).size == (320, 180)
    full_scene = client.get("/api/plugins/director-stage/scenes/film?view=full").json()
    assert full_scene["durationSec"] == 8
    assert full_scene["timeline"]["tracks"][0]["type"] == "camera_path"
    prop = next(item for item in full_scene["entities"] if item["type"] == "prop")
    assert prop["assetId"] == "bench"
    assert prop["name"] == "候车长椅"
    moved = client.post(
        "/api/plugins/director-stage/scenes/film/edit",
        json={
            "operations": [
                {
                    "type": "add_move_path",
                    "targetId": prop["id"],
                    "points": [[1.5, 0, 0], [1.5, 0, 2.5]],
                    "durationSec": 4,
                    "orient": "keep",
                },
                {"type": "rename", "id": prop["id"], "label": "移动长椅"},
                {
                    "type": "set_environment",
                    "skyColor": "#dde8f5",
                    "backgroundMode": "flat",
                    "backgroundImage": "data:image/png;base64,AAAA",
                    "backgroundImageName": "车站全景.png",
                    "horizontalRotation": 35,
                    "sphereRadius": 120,
                    "showRoleLabels": False,
                    "showGround": True,
                },
            ]
        },
    )
    assert moved.json()["applied"] == 3
    full_scene = client.get("/api/plugins/director-stage/scenes/film?view=full").json()
    assert any(track["type"] == "object_path" for track in full_scene["timeline"]["tracks"])
    assert next(item for item in full_scene["entities"] if item["id"] == prop["id"])["name"] == (
        "移动长椅"
    )
    assert full_scene["scene"]["skyColor"] == "#dde8f5"
    assert full_scene["scene"]["backgroundMode"] == "flat"
    assert full_scene["scene"]["backgroundImageName"] == "车站全景.png"
    assert full_scene["scene"]["horizontalRotation"] == 35
    assert full_scene["scene"]["sphereRadius"] == 120
    assert full_scene["scene"]["showRoleLabels"] is False
    assert client.get("/api/plugins/director-stage/scenes/film/diagnostics").json()["clean"] is True

    failed = client.post(
        "/api/plugins/director-stage/scenes/film/edit",
        json={"operations": [{"type": "set_pose", "entityId": "character-1", "pose": "unknown"}]},
    )
    assert failed.json()["rolledBack"] is True
    latest = client.get("/api/plugins/director-stage/scenes/film?view=entities").json()
    assert (
        next(item for item in latest["entities"] if item["id"] == "character-1")["pose"] == "sword"
    )


def test_director_stage_history_motion_and_camera_path_reads(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(director_stage, "_scenes_dir", lambda: tmp_path)
    client = _load(DirectorStagePlugin(), "director_stage")
    initial = client.get("/api/plugins/director-stage/scenes/story?view=full").json()
    camera_id = next(item["id"] for item in initial["entities"] if item["type"] == "camera")
    edited = client.post(
        "/api/plugins/director-stage/scenes/story/edit",
        json={
            "operations": [
                {
                    "type": "set_motion",
                    "label": "转身招手",
                    "source": "0ms pose stand\n500ms turn 90\n900ms pose wave",
                    "defaultMs": 900,
                },
                {
                    "type": "add_camera_path",
                    "cameraId": camera_id,
                    "points": [[0, 1.6, 5], [1, 1.8, 3], [0, 1.4, 2]],
                    "durationSec": 4,
                    "easing": "linear",
                    "source": "0s at 0 1.6 5\n4s at 0 1.4 2",
                },
            ]
        },
    ).json()
    motion_id = edited["results"][0]["motionId"]
    path_id = edited["results"][1]["trackId"]
    motion = client.get(f"/api/plugins/director-stage/scenes/story/motions/{motion_id}").json()
    assert motion["builtin"] is False
    assert motion["label"] == "转身招手"
    path = client.get(f"/api/plugins/director-stage/scenes/story/camera-paths/{path_id}").json()
    assert path["easing"] == "linear"
    assert len(path["points"]) == 3

    undone = client.post(
        "/api/plugins/director-stage/scenes/story/history",
        json={"action": "undo"},
    ).json()
    assert undone["stepsTaken"] == 1
    restored = client.get("/api/plugins/director-stage/scenes/story?view=full").json()
    assert restored["motions"] == []
    assert restored["timeline"]["tracks"] == []

    redone = client.post(
        "/api/plugins/director-stage/scenes/story/history",
        json={"action": "redo"},
    ).json()
    assert redone["stepsTaken"] == 1
    assert (
        client.get(f"/api/plugins/director-stage/scenes/story/motions/{motion_id}").status_code
        == 200
    )


def test_director_stage_generates_captures_and_compares_safe_models(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(director_stage, "_scenes_dir", lambda: tmp_path / "scenes")
    monkeypatch.setattr(
        director_stage,
        "_model_output_dir",
        lambda scene_id: tmp_path / "captures" / scene_id,
    )
    client = _load(DirectorStagePlugin(), "director_stage")
    generated = client.post(
        "/api/plugins/director-stage/scenes/product/models/generate",
        json={
            "label": "机器人样机",
            "parts": [
                {
                    "name": "body",
                    "shape": "box",
                    "size": [1.2, 1.4, 0.7],
                    "position": [0, 0.7, 0],
                    "color": "#4f70d9",
                },
                {
                    "name": "head",
                    "shape": "sphere",
                    "size": [0.7, 0.7, 0.7],
                    "position": [0, 1.75, 0],
                    "color": "#dce7ff",
                },
            ],
        },
    )
    assert generated.status_code == 200
    result = generated.json()["results"][0]
    model_id = result["modelId"]
    assert result["warnings"] == []
    assert result["bbox"]["min"][1] == 0

    scene = client.get("/api/plugins/director-stage/scenes/product?view=full").json()
    assert scene["counts"]["models"] == 1
    model = next(item for item in scene["entities"] if item["id"] == model_id)
    assert [part["name"] for part in model["parts"]] == ["body", "head"]

    captured = client.post(
        f"/api/plugins/director-stage/scenes/product/models/{model_id}/capture",
        json={"views": ["front", "iso"], "max_dim": 320},
    )
    assert captured.status_code == 200
    captures = captured.json()["captures"]
    assert [item["view"] for item in captures] == ["front", "iso"]
    for item in captures:
        image = Image.open(item["path"])
        assert image.size == (320, 320)
        assert image.getbbox()

    compared = client.post(
        f"/api/plugins/director-stage/scenes/product/models/{model_id}/compare",
        json={"reference_path": captures[0]["path"], "view": "front"},
    )
    assert compared.status_code == 200
    assert compared.json()["score"] >= 0.97
    assert Path(compared.json()["differencePath"]).is_file()

    replaced = client.post(
        "/api/plugins/director-stage/scenes/product/models/generate",
        json={
            "model_id": model_id,
            "label": "机器人样机 v2",
            "parts": [
                {
                    "name": "body",
                    "shape": "cylinder",
                    "size": [1, 1.6, 1],
                    "position": [0, 0.8, 0],
                    "color": "#486bd6",
                }
            ],
        },
    ).json()
    assert replaced["results"][0]["replaced"] is True
    assert client.get("/api/plugins/director-stage/scenes/product").json()["counts"]["models"] == 1


def test_comfyui_bridge_is_local_only_and_points_to_real_integration() -> None:
    local = _load(ComfyUIBridgePlugin(), "comfyui_bridge")
    assert local.get("/api/plugins/comfyui-bridge/health").json()["local_only"] is True
    caps = local.get("/api/plugins/comfyui-bridge/capabilities").json()
    assert caps["queue"] == "/api/design/comfyui/queue"
    assert caps["dependencies"] == "/api/design/comfyui/dependencies"
    assert caps["install"] == "/api/design/comfyui/install"
    assert caps["update"] == "/api/design/comfyui/update"
    assert caps["custom_node_registry"] == "/api/design/comfyui/custom-nodes/registry"
    assert caps["custom_node_rollback"].endswith("/{node_id}/rollback")
    assert caps["model_download"] == "/api/design/comfyui/models/download"
    assert caps["model_restore"] == "/api/design/comfyui/models/restore"
    assert caps["workflow_diagnostics"].endswith("/{workflow_id}/diagnostics")

    remote = _load(
        ComfyUIBridgePlugin(),
        "comfyui_bridge",
        {"base_url": "https://example.com"},
    )
    assert remote.get("/api/plugins/comfyui-bridge/health").json()["ok"] is False


def test_design_plugins_register_agent_callable_skills(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(director_stage, "_scenes_dir", lambda: tmp_path / "scenes")
    monkeypatch.setattr(
        "runtime.platform.plugins.bundled.comfyui_bridge.app_paths",
        lambda: SimpleNamespace(data_dir=tmp_path),
    )
    monkeypatch.setenv("ECHO_COMFYUI_HOME", str(tmp_path / "comfyui"))
    (tmp_path / "comfyui" / "models" / "checkpoints").mkdir(parents=True)
    (tmp_path / "comfyui" / "models" / "checkpoints" / "base.safetensors").write_bytes(b"model")
    registry = SkillRegistry()
    for plugin, name in (
        (DirectorStagePlugin(), "director_stage"),
        (ComfyUIBridgePlugin(), "comfyui_bridge"),
    ):
        plugin.on_load(
            ModuleContext(
                plugin_name=name,
                plugin_dir=str(BUNDLED / name),
                manifest=None,
                skill_registry=registry,
                config={"base_url": "http://127.0.0.1:8188"},
            )
        )
    assert {
        "director_stage.scene_get",
        "director_stage.scene_edit",
        "director_stage.scene_diagnostics",
        "director_stage.scene_history",
        "director_stage.scene_snapshot",
        "director_stage.motion_read",
        "director_stage.campath_read",
        "director_stage.model_generate",
        "director_stage.model_capture",
        "director_stage.model_compare",
        "comfyui_bridge.status",
        "comfyui_bridge.dependencies",
        "comfyui_bridge.manager_status",
        "comfyui_bridge.workflows",
        "comfyui_bridge.workflow_get",
        "comfyui_bridge.workflow_diagnostics",
        "comfyui_bridge.workflow_save",
        "comfyui_bridge.queue",
        "comfyui_bridge.result",
    } <= set(registry.all_names())
    dependencies = registry.get("comfyui_bridge.dependencies").handler()
    assert dependencies["detected"] is True
    assert dependencies["totalModels"] == 1
    edited = registry.get("director_stage.scene_edit").handler(
        scene_id="agent-scene",
        operations=[{"type": "set_pose", "entityId": "character-1", "pose": "wave"}],
    )
    assert edited["ok"] is True
    scene = registry.get("director_stage.scene_get").handler(
        scene_id="agent-scene", view="entities"
    )
    assert scene["entities"][0]["pose"] == "wave"
    workflows = registry.get("comfyui_bridge.workflows").handler()
    assert workflows["ok"] is True
    assert workflows["total"] >= 3
    loaded = registry.get("comfyui_bridge.workflow_get").handler(workflow_id="text-to-image")
    assert loaded["ok"] is True
    assert loaded["workflow"]["5"]["class_type"] == "KSampler"
    saved = registry.get("comfyui_bridge.workflow_save").handler(
        workflow_id="agent-flow",
        name="Agent 工作流",
        workflow={"1": {"class_type": "KSampler", "inputs": {"steps": 24}}},
        ui={"positions": {"1": {"x": 100, "y": 120}}},
        expected_revision=0,
    )
    assert saved["ok"] is True
    assert saved["revision"] == 1
    reloaded = registry.get("comfyui_bridge.workflow_get").handler(workflow_id="agent-flow")
    assert reloaded["ui"]["positions"]["1"] == {"x": 100, "y": 120}
    conflict = registry.get("comfyui_bridge.workflow_save").handler(
        workflow_id="agent-flow",
        name="旧版本",
        workflow={},
        expected_revision=0,
    )
    assert conflict["code"] == "WORKFLOW_REVISION_CONFLICT"


def test_comfyui_agent_can_queue_by_id_and_read_outputs(monkeypatch) -> None:
    registry = SkillRegistry()
    plugin = ComfyUIBridgePlugin()
    plugin.on_load(
        ModuleContext(
            plugin_name="comfyui_bridge",
            plugin_dir=str(BUNDLED / "comfyui_bridge"),
            manifest=None,
            skill_registry=registry,
            config={"base_url": "http://127.0.0.1:8188"},
        )
    )

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    def fake_post(_url, json, timeout):
        assert timeout == 15
        assert json["prompt"]["5"]["class_type"] == "KSampler"
        return FakeResponse({"prompt_id": "job-1"})

    def fake_get(url, timeout):
        assert timeout == 5
        assert url.endswith("/history/job-1")
        return FakeResponse(
            {
                "job-1": {
                    "status": {"completed": True},
                    "outputs": {
                        "9": {
                            "images": [{"filename": "done.png", "type": "output", "subfolder": ""}]
                        }
                    },
                }
            }
        )

    monkeypatch.setattr("runtime.platform.plugins.bundled.comfyui_bridge.httpx.post", fake_post)
    monkeypatch.setattr("runtime.platform.plugins.bundled.comfyui_bridge.httpx.get", fake_get)
    queued = registry.get("comfyui_bridge.queue").handler(workflow_id="text-to-image")
    assert queued == {"ok": True, "prompt_id": "job-1"}
    result = registry.get("comfyui_bridge.result").handler(prompt_id="job-1")
    assert result["state"] == "completed"
    assert result["outputs"][0]["filename"] == "done.png"


def test_plugin_hub_lists_all_design_workspace_plugins() -> None:
    hub = PluginHub(plugin_dir=ROOT / ".missing-plugins", bundled_plugin_dir=BUNDLED)
    discovered = {item["id"] for item in hub.discover()}
    assert {"clip_studio", "director_stage", "comfyui_bridge"} <= discovered

