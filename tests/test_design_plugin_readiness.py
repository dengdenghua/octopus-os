"""Real plugin assembly and image-only use with optional video dependencies absent."""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from runtime.execution.suckers.registry import SkillRegistry
from runtime.platform.plugins.bundled.clip_studio import readiness
from runtime.platform.plugins.plugin_hub import PluginHub
from runtime.platform.process.service_bus import ServiceBus

ROOT = Path(__file__).resolve().parents[1]
BUNDLED = ROOT / "runtime/platform/plugins/bundled"
PLUGINS = ["clip_studio", "comfyui_bridge", "director_stage"]


def _hub(tmp_path, monkeypatch, registry=None):
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))
    app = FastAPI()
    registry = registry if registry is not None else SkillRegistry()
    bus = ServiceBus()
    hub = PluginHub(
        plugin_dir=tmp_path / "plugins",
        bundled_plugin_dir=BUNDLED,
        fastapi_app=app,
        skill_registry=registry,
        service_bus=bus,
        activation_root=tmp_path / "activation",
        data_root=tmp_path / "workbench-data",
    )
    return hub, registry, bus, TestClient(app)


@pytest.fixture
def no_av(monkeypatch):
    """Force the same absent dependency even on CI machines that installed av."""
    find_spec = importlib.util.find_spec
    import_module = importlib.import_module

    def absent_spec(name, *args, **kwargs):
        return None if name == "av" else find_spec(name, *args, **kwargs)

    def absent_import(name, *args, **kwargs):
        if name == "av":
            raise ModuleNotFoundError("injected private dependency path", name="av")
        return import_module(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", absent_spec)
    monkeypatch.setattr(importlib, "import_module", absent_import)


@pytest.mark.parametrize("name", PLUGINS)
def test_declared_surfaces_are_registered_and_unloaded(name, tmp_path, monkeypatch, caplog, no_av):
    hub, registry, bus, client = _hub(tmp_path, monkeypatch)
    plugin = hub.load(name)
    assert plugin is not None
    provided = yaml.safe_load((BUNDLED / name / "plugin.yaml").read_text(encoding="utf-8"))[
        "provides"
    ]
    assert set(provided) == {f"{name}.api", f"{name}.skills"}
    assert {cap.name for cap in plugin.capabilities} == set(provided)
    for service in provided:
        # ServiceBus resolves ModulePlugin, while callable skills live in SkillRegistry.
        assert bus.require(service) is plugin
    assert plugin.ctx.skill_registry is registry
    names = [skill for skill in registry.all_names() if skill.startswith(name + ".")]
    assert names
    assert all(callable(registry.get(skill).handler) for skill in names)
    assert client.get(f"/api/plugins/{name.replace('_', '-')}/health").status_code == 200
    if name == "clip_studio":
        assert registry.get(name + ".project_get").handler(project_id="assembly")["ok"]
    elif name == "director_stage":
        assert registry.get(name + ".scene_get").handler(scene_id="assembly")["ok"]
    else:
        # Dependency inventory is local and does not contact or start ComfyUI.
        assert isinstance(registry.get(name + ".dependencies").handler(), dict)
    assert "manifest.provides mismatch" not in caplog.text

    assert hub.unload(name)
    assert all(not bus.has(service) for service in provided)
    assert all(not registry.has(skill) for skill in names)
    assert client.get(f"/api/plugins/{name.replace('_', '-')}/health").status_code == 404


@pytest.mark.parametrize("name", PLUGINS)
def test_failed_skill_registration_rolls_back_instead_of_claiming_loaded(
    name, tmp_path, monkeypatch, no_av
):
    class RejectSecondSkill(SkillRegistry):
        def register(self, skill, **kwargs):
            if self.all_names():
                raise RuntimeError("injected skill registration failure")
            return super().register(skill, **kwargs)

    hub, registry, bus, client = _hub(tmp_path, monkeypatch, RejectSecondSkill())
    assert hub.load(name) is None
    assert registry.all_names() == []
    assert not bus.has(name + ".api")
    assert not bus.has(name + ".skills")
    assert client.get(f"/api/plugins/{name.replace('_', '-')}/health").status_code == 404


def test_readiness_separates_missing_media_features_from_project_and_images(
    tmp_path, monkeypatch, no_av
):
    hub, _, _, client = _hub(tmp_path, monkeypatch)
    assert hub.load("clip_studio")
    health = client.get("/api/plugins/clip-studio/health").json()
    assert health["state"] == health["readiness"]["state"] == "partial"
    capabilities = health["readiness"]["capabilities"]
    for feature in ("project_editing", "image_snapshot"):
        assert capabilities[feature]["available"] is True
    for feature in ("video_snapshot", "video_export", "audio_analysis"):
        assert capabilities[feature]["available"] is False
        assert "av" in capabilities[feature]["missingDependencies"]


def test_dependency_presence_never_imports_or_claims_verified_codecs(monkeypatch):
    monkeypatch.setattr(importlib.util, "find_spec", lambda _name: object())

    def unexpected_import(*_args, **_kwargs):
        raise AssertionError("readiness must not import optional modules")

    monkeypatch.setattr(importlib, "import_module", unexpected_import)
    status = readiness.media_readiness()
    assert status["state"] == "dependencies_present"
    for feature in ("video_snapshot", "video_export", "audio_analysis"):
        assert status["capabilities"][feature]["state"] == "dependencies_present"
        assert status["capabilities"][feature]["runtimeVerified"] is False


def test_image_and_project_skills_work_without_video_stack(tmp_path, monkeypatch, no_av):
    hub, registry, _, client = _hub(tmp_path, monkeypatch)
    assert hub.load("clip_studio")
    image = tmp_path / "source.png"
    Image.new("RGB", (160, 90), (30, 100, 170)).save(image)
    edited = registry.get("clip_studio.project_edit").handler(
        project_id="image-only",
        operations=[
            {"type": "import_media", "path": str(image), "durationSec": 2},
            {"type": "add_text", "text": "caption", "atSec": 1, "durationSec": 0.5},
        ],
    )
    assert edited["ok"] is True
    project = registry.get("clip_studio.project_get").handler(project_id="image-only", view="full")
    assert project["textClips"][0]["text"] == "caption"
    response = client.post(
        "/api/plugins/clip-studio/projects/image-only/snapshot",
        json={"times": [0.25], "maxDim": 160},
    )
    assert response.status_code == 200
    with Image.open(response.json()["frames"][0]["path"]) as rendered:
        assert rendered.size == (160, 90)
        assert rendered.getpixel((80, 45)) == (30, 100, 170)
    assert (
        registry.get("clip_studio.project_history").handler(project_id="image-only", action="undo")[
            "stepsTaken"
        ]
        == 1
    )
    assert client.get("/api/plugins/clip-studio/projects/image-only").json()["counts"]["clips"] == 0


@pytest.mark.parametrize("feature", ["video_snapshot", "video_export", "audio_analysis"])
@pytest.mark.parametrize("load_failure", [False, True])
def test_unavailable_video_features_return_controlled_http_and_skill_errors(
    feature, load_failure, tmp_path, monkeypatch, no_av
):
    hub, registry, _, client = _hub(tmp_path, monkeypatch)
    assert hub.load("clip_studio")
    media = tmp_path / "private-source.mp4"
    media.write_bytes(b"dependency check must happen before media decoding")
    result = registry.get("clip_studio.project_edit").handler(
        project_id="video",
        operations=[{"type": "import_media", "path": str(media), "durationSec": 2}],
    )
    assert result["ok"]
    clip_id = result["results"][0]["clipId"]
    if load_failure:
        original = importlib.import_module

        def broken_dll(name, *args, **kwargs):
            if name == "av":
                raise OSError("private DLL path: C:/private/native/codec.dll")
            return original(name, *args, **kwargs)

        monkeypatch.setattr(importlib, "import_module", broken_dll)
    if feature == "video_snapshot":
        action, payload = "snapshot", {"times": [0.25]}
    elif feature == "video_export":
        action, payload = "export", {}
    else:
        action, payload = (
            "edit",
            {
                "operations": [
                    {"type": "add_text", "text": "must roll back", "atSec": 0},
                    {"type": "cut_silences", "clipId": clip_id},
                ]
            },
        )
    response = client.post(f"/api/plugins/clip-studio/projects/video/{action}", json=payload)
    assert response.status_code == 503, response.text
    detail = response.json()["detail"]
    expected_code = "dependency_load_failed" if load_failure else "optional_dependency_unavailable"
    assert detail["code"] == expected_code
    assert detail["capability"] == feature
    assert detail["unavailableDependencies"] == ["av"]
    assert "private" not in response.text and str(tmp_path) not in response.text
    skill_result = registry.get("clip_studio.project_" + action).handler(
        project_id="video", **payload
    )
    assert skill_result["ok"] is False
    assert skill_result["code"] == expected_code
    assert "private" not in json.dumps(skill_result)
    if feature == "audio_analysis":
        assert detail["rolledBack"] is True
        assert detail["applied"] == 0
        project = client.get("/api/plugins/clip-studio/projects/video?view=full").json()
        assert project["textClips"] == []


def test_image_to_video_transition_does_not_hide_missing_codec(tmp_path, monkeypatch, no_av):
    hub, registry, _, client = _hub(tmp_path, monkeypatch)
    assert hub.load("clip_studio")
    image = tmp_path / "transition-source.png"
    Image.new("RGB", (160, 90), "red").save(image)
    video = tmp_path / "transition-target.mp4"
    video.write_bytes(b"codec check precedes decoding")
    edit = registry.get("clip_studio.project_edit").handler
    result = edit(
        project_id="transition",
        operations=[
            {"type": "import_media", "path": str(image), "durationSec": 2, "atSec": 0},
            {"type": "import_media", "path": str(video), "durationSec": 2, "atSec": 2},
        ],
    )
    assert result["ok"]
    assert edit(
        project_id="transition",
        operations=[
            {
                "type": "add_transition",
                "clipId": result["results"][0]["clipId"],
                "transitionType": "crossfade",
                "durationSec": 0.5,
            }
        ],
    )["ok"]
    response = client.post(
        "/api/plugins/clip-studio/projects/transition/snapshot", json={"times": [1.75]}
    )
    assert response.status_code == 503
    assert response.json()["detail"]["capability"] == "video_snapshot"


def test_fresh_process_import_and_real_image_render_never_attempt_av(tmp_path):
    # A clean interpreter catches eager imports hidden by pytest's module cache.
    script = textwrap.dedent(
        """
        import importlib.abc, json, os, sys
        from pathlib import Path
        class RejectAv(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == 'av' or fullname.startswith('av.'):
                    raise AssertionError('unexpected eager video dependency import')
        sys.meta_path.insert(0, RejectAv())
        from PIL import Image
        from runtime.platform.plugins.bundled.clip_studio import ClipStudioPlugin
        from runtime.platform.plugins.plugin_base import ModuleContext
        from runtime.execution.suckers.registry import SkillRegistry
        registry = SkillRegistry()
        plugin = ClipStudioPlugin()
        plugin.on_load(ModuleContext(plugin_name='clip_studio', plugin_dir='.',
                                    manifest=None, skill_registry=registry))
        image = Path(os.environ['ECHO_DATA_DIR']) / 'real-image.png'
        Image.new('RGB', (160, 90), (30, 100, 170)).save(image)
        result = registry.get('clip_studio.project_edit').handler(project_id='fresh', operations=[
            {'type':'import_media', 'path':str(image), 'durationSec':2}])
        assert result['ok'], result
        result = registry.get('clip_studio.project_snapshot').handler(
            project_id='fresh', times=[0.25], max_dim=160)
        assert result['ok'], result
        with Image.open(result['frames'][0]['path']) as rendered:
            assert rendered.getpixel((80,45)) == (30,100,170)
        assert 'av' not in sys.modules
        assert 'runtime.platform.plugins.bundled.clip_studio.video_export' not in sys.modules
        print(json.dumps({'skills':len(registry.all_names()), 'imageRendered':True, 'avImported':False}))
        """
    )
    env = {**os.environ, "ECHO_DATA_DIR": str(tmp_path), "PYTHONUTF8": "1"}
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=25,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"skills": 7, "imageRendered": True, "avImported": False}
