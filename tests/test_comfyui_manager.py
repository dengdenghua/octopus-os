from __future__ import annotations

import json
from types import SimpleNamespace

from runtime.sensing.gateway import comfyui_manager


def test_worker_commands_are_fixed_to_managed_stable_install(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(comfyui_manager, "managed_root", lambda: tmp_path)

    install = comfyui_manager._worker_command("install")
    update = comfyui_manager._worker_command("update")

    assert install == [
        str(tmp_path / "venv" / "bin" / "comfy"),
        f"--workspace={tmp_path / 'workspace'}",
        "--skip-prompt",
        "install",
        "--version",
        "latest",
        "--skip-manager",
        "--cpu",
    ]
    assert update[-5:] == ["--skip-prompt", "update", "comfy", "--version", "latest"]
    assert all("model" not in item for item in install)
    assert comfyui_manager._worker_command("node_install", "comfyui-kjnodes")[-3:] == [
        "node",
        "registry-install",
        "comfyui-kjnodes",
    ]
    assert comfyui_manager._worker_command(
        "model_download",
        model_url="https://huggingface.co/owner/repo/resolve/main/model.safetensors",
        model_group="checkpoints",
    )[-6:] == [
        "model",
        "download",
        "--url",
        "https://huggingface.co/owner/repo/resolve/main/model.safetensors",
        "--relative-path",
        "checkpoints",
    ]


def test_worker_records_completed_install_without_downloading_models(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(comfyui_manager, "managed_root", lambda: tmp_path)
    calls: list[list[str]] = []

    def fake_run(argv, **_kwargs):
        command = [str(item) for item in argv]
        calls.append(command)
        if "install" in command:
            home = tmp_path / "workspace" / "ComfyUI"
            home.mkdir(parents=True, exist_ok=True)
            (home / "main.py").write_text("# comfy", encoding="utf-8")
        return SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(comfyui_manager.subprocess, "run", fake_run)
    assert comfyui_manager._run_worker("install") == 0

    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["state"] == "completed"
    assert state["phase"] == "ready"
    assert any("comfy-cli==1.17.0" in part for call in calls for part in call)
    assert not (tmp_path / "workspace" / "ComfyUI" / "models" / "checkpoints").exists()


def test_worker_fails_if_cli_does_not_create_main_file(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(comfyui_manager, "managed_root", lambda: tmp_path)
    monkeypatch.setattr(
        comfyui_manager.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="", returncode=0),
    )

    assert comfyui_manager._run_worker("install") == 1
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["state"] == "failed"
    assert "without creating" in state["error"]


def test_status_reports_installed_version_and_bounded_log(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(comfyui_manager, "managed_root", lambda: tmp_path)
    monkeypatch.setattr(comfyui_manager, "_PROCESS", None)
    home = tmp_path / "workspace" / "ComfyUI"
    home.mkdir(parents=True)
    (home / "main.py").write_text("# comfy", encoding="utf-8")
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "install.log").write_text(
        "\n".join(f"line-{index}" for index in range(90)), encoding="utf-8"
    )

    status = comfyui_manager.manager_status()

    assert status["installed"] is True
    assert status["runtime"]["comfy_cli_version"] == "1.17.0"
    assert len(status["log_tail"]) == 60
    assert status["log_tail"][0] == "line-30"


def test_custom_node_uninstall_is_recoverable_and_rollback_restores(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(comfyui_manager, "managed_root", lambda: tmp_path)
    node = tmp_path / "workspace" / "ComfyUI" / "custom_nodes" / "comfyui-kjnodes"
    node.mkdir(parents=True)
    (node / "marker.py").write_text("VALUE = 1", encoding="utf-8")

    assert comfyui_manager.uninstall_managed_node("comfyui-kjnodes") == "uninstalled"
    assert not node.exists()
    backups = comfyui_manager.list_node_backups("comfyui-kjnodes")
    assert len(backups) == 1

    assert comfyui_manager.rollback_managed_node("comfyui-kjnodes") == "restored"
    assert (node / "marker.py").read_text(encoding="utf-8") == "VALUE = 1"
    assert comfyui_manager.list_node_backups("comfyui-kjnodes") == []


def test_failed_custom_node_update_restores_previous_source(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(comfyui_manager, "managed_root", lambda: tmp_path)
    python = tmp_path / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    (python.parent / "comfy").write_text("", encoding="utf-8")
    home = tmp_path / "workspace" / "ComfyUI"
    home.mkdir(parents=True)
    (home / "main.py").write_text("# comfy", encoding="utf-8")
    node = home / "custom_nodes" / "comfyui-kjnodes"
    node.mkdir(parents=True)
    (node / "marker.py").write_text("old", encoding="utf-8")

    def fake_run(argv, **_kwargs):
        if "registry-install" in [str(item) for item in argv]:
            raise comfyui_manager.subprocess.CalledProcessError(1, argv)
        return SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(comfyui_manager.subprocess, "run", fake_run)

    assert comfyui_manager._run_worker("node_update", "comfyui-kjnodes") == 1
    assert (node / "marker.py").read_text(encoding="utf-8") == "old"


def test_model_sources_are_allowlisted_and_secret_queries_rejected(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(comfyui_manager, "managed_root", lambda: tmp_path)
    home = tmp_path / "workspace" / "ComfyUI"
    home.mkdir(parents=True)
    (home / "main.py").write_text("# comfy", encoding="utf-8")

    assert (
        comfyui_manager.start_manager_job(
            "model_download",
            model_url="http://127.0.0.1/private.safetensors",
            model_group="checkpoints",
        )
        == "invalid_model_source"
    )
    assert (
        comfyui_manager.start_manager_job(
            "model_download",
            model_url="https://huggingface.co/a/b?token=secret",
            model_group="checkpoints",
        )
        == "invalid_model_source"
    )
    assert (
        comfyui_manager.start_manager_job(
            "model_download",
            model_url="https://huggingface.co/a/b/model.safetensors",
            model_group="../escape",
        )
        == "invalid_model_source"
    )


def test_model_inventory_remove_and_restore_are_recoverable(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(comfyui_manager, "managed_root", lambda: tmp_path)
    model = (
        tmp_path / "workspace" / "ComfyUI" / "models" / "checkpoints" / "brand-model.safetensors"
    )
    model.parent.mkdir(parents=True)
    model.write_bytes(b"weights")

    listed = comfyui_manager.list_managed_models()
    assert listed[0]["id"] == "checkpoints:brand-model.safetensors"
    assert listed[0]["size_bytes"] == 7
    assert (
        comfyui_manager.remove_managed_model("checkpoints", "../outside.safetensors")
        == "invalid_model_path"
    )
    assert (
        comfyui_manager.remove_managed_model("checkpoints", "brand-model.safetensors") == "removed"
    )
    backups = comfyui_manager.list_model_backups()
    assert len(backups) == 1
    assert comfyui_manager.restore_managed_model(backups[0]["id"]) == "restored"
    assert model.read_bytes() == b"weights"

