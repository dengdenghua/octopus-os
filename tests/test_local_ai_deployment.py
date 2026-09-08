from __future__ import annotations

import hashlib
import json
import zipfile

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.platform.ui.cookbook_router import create_cookbook_router
from runtime.sensing.model_router import hwfit
from runtime.sensing.model_router import local_ai_deployment as deploy
from runtime.sensing.model_router import managed_ollama as runtime


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "root", lambda: tmp_path / "local-ai")
    monkeypatch.setattr(runtime, "_lease", None)
    monkeypatch.setattr(runtime, "_process", None)
    monkeypatch.setattr(deploy, "_active", None)
    monkeypatch.setattr(deploy, "_plans", {})
    monkeypatch.setattr(hwfit, "_pull_state", {})
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    deploy._stopping.clear()
    yield
    runtime.shutdown()


def fake_resources(monkeypatch):
    monkeypatch.setattr(runtime, "asset", lambda: ("fixture.zip", 123, "digest"))
    monkeypatch.setattr(runtime, "available_disk", lambda: 100 * runtime.GIB)
    monkeypatch.setattr(
        hwfit,
        "recommend",
        lambda *args: [
            hwfit.Recommendation(
                "fixture:1b", "Fixture", 1, "q4_K_M", 2, True, "fits", None, False, 1, "fixture"
            )
        ],
    )


def test_plan_is_read_only_and_has_storage_and_expiry(monkeypatch):
    fake_resources(monkeypatch)
    result = deploy.plan("fixture:1b")
    assert result["runtime_download_bytes"] == 123
    assert result["required_disk_bytes"] > result["model_estimate_bytes"] * 2
    assert not runtime.root().exists()
    assert result["endpoint"] == runtime.BASE
    assert deploy.plan("auto")["tag"] == "fixture:1b"


@pytest.mark.parametrize("case", ["disk", "model", "override"])
def test_plan_rejects_incompatible_conditions(monkeypatch, case):
    fake_resources(monkeypatch)
    if case == "disk":
        monkeypatch.setattr(runtime, "available_disk", lambda: 1)
    if case == "override":
        monkeypatch.setenv("OLLAMA_BASE_URL", "https://external.example")
    with pytest.raises(ValueError):
        deploy.plan("missing:1b" if case == "model" else "fixture:1b")


def test_expiry_disk_race_and_existing_job_stop_start(monkeypatch):
    fake_resources(monkeypatch)
    p = deploy.plan("fixture:1b")
    deploy._plans[p["plan_id"]]["expires_at"] = 0
    with pytest.raises(ValueError, match="过期"):
        deploy.start(p["plan_id"])
    p = deploy.plan("fixture:1b")
    monkeypatch.setattr(runtime, "available_disk", lambda: 0)
    with pytest.raises(ValueError, match="空间"):
        deploy.start(p["plan_id"])
    monkeypatch.setattr(runtime, "available_disk", lambda: 100 * runtime.GIB)
    hwfit._pull_state["other"] = "verifying"
    with pytest.raises(ValueError, match="正在准备"):
        deploy.start(p["plan_id"])


def test_thread_failure_clears_busy_reservation(monkeypatch):
    fake_resources(monkeypatch)
    p = deploy.plan("fixture:1b")

    def fail(*args, **kwargs):
        raise RuntimeError("cannot spawn")

    monkeypatch.setattr(deploy.threading.Thread, "start", fail)
    with pytest.raises(ValueError, match="无法启动"):
        deploy.start(p["plan_id"])
    assert deploy.status()["stage"] == "error"
    assert hwfit._pull_state["fixture:1b"].startswith("error:")


@pytest.mark.parametrize("failure", [None, "install", "pull", "verify"])
def test_worker_only_enables_after_success_and_persists_failures(monkeypatch, failure):
    runtime.root().mkdir()
    deploy._active = {"tag": "fixture:1b", "stage": "preparing"}
    calls = []

    def step(name):
        def run(*args, **kwargs):
            calls.append(name)
            if name == failure:
                raise ValueError("fixture failure")
            return {"status": "ok"}

        return run

    monkeypatch.setattr(runtime, "install", step("install"))
    monkeypatch.setattr(runtime, "start", step("start"))
    monkeypatch.setattr(hwfit, "pull_model", step("pull"))
    monkeypatch.setattr(deploy, "verify_model", step("verify"))
    deploy._worker({"tag": "fixture:1b"})
    assert runtime.enabled() is (failure is None)
    assert deploy.status()["stage"] == ("error" if failure else "ready")
    assert (
        json.loads((runtime.root() / "deployment.json").read_text())["stage"]
        == deploy.status()["stage"]
    )
    if failure is None:
        assert calls == ["install", "start", "pull", "verify"]


def test_interrupted_job_is_not_reported_as_running_forever():
    runtime.root().mkdir()
    (runtime.root() / "deployment.json").write_text('{"stage":"pulling"}', encoding="utf-8")
    assert deploy.status()["stage"] == "error"


def test_startup_never_installs_or_downloads(monkeypatch):
    starts = []
    monkeypatch.setattr(runtime, "start", lambda: starts.append(True))
    deploy.resume_runtime()
    assert starts == []
    runtime.root().mkdir()
    runtime.enable()
    deploy.resume_runtime()
    assert starts == [True]


@pytest.mark.parametrize("name", ["../escape", "/escape", "C:/escape"])
def test_archive_rejects_escape_paths(tmp_path, name):
    archive = tmp_path / "fixture.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr(name, b"malicious")
    folder = tmp_path / "unpacked"
    folder.mkdir()
    with pytest.raises(ValueError):
        runtime._extract(archive, folder)


def test_target_rejects_windows_separator_before_extraction(tmp_path):
    with pytest.raises(ValueError):
        runtime._target(tmp_path, "folder\\escape")


def test_archive_rejects_symlinks_and_size_bombs(tmp_path, monkeypatch):
    archive = tmp_path / "fixture.zip"
    entry = zipfile.ZipInfo("link")
    entry.external_attr = 0o120777 << 16
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr(entry, "other")
    with pytest.raises(ValueError, match="符号链接"):
        runtime._extract(archive, tmp_path / "out")
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("big", b"1234")
    monkeypatch.setattr(runtime, "MAX_UNPACKED", 1)
    with pytest.raises(ValueError, match="大小"):
        runtime._extract(archive, tmp_path / "out")


def test_download_verifies_pinned_digest_and_official_redirect(tmp_path, monkeypatch):
    real_client = httpx.Client
    seen = []

    def response(request):
        seen.append(request.url.host)
        if request.url.host == "github.com":
            return httpx.Response(
                302, headers={"location": "https://release-assets.githubusercontent.com/file"}
            )
        return httpx.Response(200, content=b"fixture")

    monkeypatch.setattr(
        runtime.httpx,
        "Client",
        lambda **kw: real_client(transport=httpx.MockTransport(response), **kw),
    )
    runtime._download(
        tmp_path / "archive",
        ("fixture.zip", 7, hashlib.sha256(b"fixture").hexdigest()),
        lambda *a: None,
    )
    assert seen == ["github.com", "release-assets.githubusercontent.com"]
    with pytest.raises(ValueError, match="校验"):
        runtime._download(tmp_path / "bad", ("fixture.zip", 7, "wrong"), lambda *a: None)


def test_download_does_not_follow_arbitrary_redirect(tmp_path, monkeypatch):
    real_client = httpx.Client
    calls = []

    def response(request):
        calls.append(request.url.host)
        return httpx.Response(302, headers={"location": "https://unexpected.example/file"})

    monkeypatch.setattr(
        runtime.httpx,
        "Client",
        lambda **kw: real_client(transport=httpx.MockTransport(response), **kw),
    )
    with pytest.raises(ValueError, match="允许列表"):
        runtime._download(tmp_path / "archive", ("fixture.zip", 7, "wrong"), lambda *a: None)
    assert calls == ["github.com"]


def test_deployment_endpoints_require_authentication():
    app = FastAPI()
    app.include_router(create_cookbook_router(require_auth=True))
    with TestClient(app) as client:
        for path in ("plan", "start"):
            assert client.post(
                f"/api/cookbook/deployment/{path}", json={"tag": "fixture:1b", "plan_id": "x"}
            ).status_code in (401, 403)
        assert client.get("/api/cookbook/deployment").status_code in (401, 403)


def test_successful_zip_install_is_atomic_and_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime.platform, "system", lambda: "Windows")
    monkeypatch.setattr(runtime, "asset", lambda: ("fixture.zip", 1, "unused"))

    def download(path, info, progress):
        with zipfile.ZipFile(path, "w") as package:
            package.writestr("ollama.exe", b"fixture executable")

    monkeypatch.setattr(runtime, "_download", download)
    runtime.install(lambda *args: None)
    assert runtime.executable().read_bytes() == b"fixture executable"
    monkeypatch.setattr(runtime, "_download", lambda *args: pytest.fail("downloaded twice"))
    runtime.install(lambda *args: None)


def test_managed_start_uses_private_environment_and_hidden_process(monkeypatch):
    from runtime.sensing.model_router import local_model_setup

    runtime.executable().parent.mkdir(parents=True)
    runtime.executable().write_bytes(b"fixture")
    captured = {}

    class Child:
        def poll(self):
            return None

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def spawn(args, **kwargs):
        captured.update(args=args, **kwargs)
        return Child()

    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-secret")
    monkeypatch.setenv("OLLAMA_HOST", "0.0.0.0:11434")
    monkeypatch.setattr(runtime.subprocess, "Popen", spawn)
    monkeypatch.setattr(local_model_setup, "client", lambda **kwargs: Connection())
    monkeypatch.setattr(local_model_setup, "model_catalog", lambda connection: [])
    runtime.start()
    assert captured["args"][-1] == "serve"
    env = captured["env"]
    assert env["OLLAMA_HOST"] == "127.0.0.1:11435"
    assert env["OLLAMA_NO_CLOUD"] == "1"
    assert env["OLLAMA_CONTEXT_LENGTH"] == "4096"
    assert "HTTPS_PROXY" not in env and "OPENAI_API_KEY" not in env
    assert captured["creationflags"] == getattr(runtime.subprocess, "CREATE_NO_WINDOW", 0)
    runtime._process = None  # synthetic child has no OS process to shut down
