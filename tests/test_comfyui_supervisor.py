from __future__ import annotations

from types import SimpleNamespace

from runtime.sensing.gateway import comfyui_supervisor


class _FakeProcess:
    pid = 4242

    def __init__(self) -> None:
        self.returncode = None

    def poll(self):
        return self.returncode


def test_resolve_comfyui_command_uses_detected_home(monkeypatch, tmp_path) -> None:
    (tmp_path / "main.py").write_text("# comfy", encoding="utf-8")
    python = tmp_path / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    monkeypatch.setenv("ECHO_COMFYUI_HOME", str(tmp_path))
    monkeypatch.setenv("ECHO_COMFYUI_URL", "http://127.0.0.1:8288")

    resolved = comfyui_supervisor.resolve_comfyui_command()
    assert resolved is not None
    argv, cwd = resolved
    assert cwd == tmp_path
    assert argv == [
        str(python),
        str(tmp_path / "main.py"),
        "--listen",
        "127.0.0.1",
        "--port",
        "8288",
    ]


def test_start_and_stop_only_manage_owned_process(monkeypatch, tmp_path) -> None:
    fake = _FakeProcess()
    launched: list[tuple[list[str], str]] = []
    monkeypatch.setattr(comfyui_supervisor, "_PROCESS", None)
    monkeypatch.setattr(
        comfyui_supervisor,
        "resolve_comfyui_command",
        lambda: (["python", "main.py"], tmp_path),
    )
    monkeypatch.setattr(
        comfyui_supervisor,
        "app_paths",
        lambda: SimpleNamespace(data_dir=tmp_path / "data"),
    )
    monkeypatch.setattr(comfyui_supervisor, "process_group_kwargs", lambda: {})
    monkeypatch.setattr(comfyui_supervisor.atexit, "register", lambda _callback: None)

    def fake_popen(argv, *, cwd, **_kwargs):
        launched.append((argv, cwd))
        return fake

    monkeypatch.setattr(comfyui_supervisor.subprocess, "Popen", fake_popen)
    assert comfyui_supervisor.start_comfyui() == "started"
    assert launched == [(["python", "main.py"], str(tmp_path))]
    assert comfyui_supervisor.process_status() == {
        "owned": True,
        "running": True,
        "pid": 4242,
    }

    terminated: list[_FakeProcess] = []
    monkeypatch.setattr(
        comfyui_supervisor,
        "terminate_process_tree",
        lambda process, **_kwargs: terminated.append(process) or True,
    )
    assert comfyui_supervisor.stop_comfyui() == "stopped"
    assert terminated == [fake]
    assert comfyui_supervisor.stop_comfyui() == "not_owned"

