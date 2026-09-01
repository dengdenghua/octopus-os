from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from runtime.safety.sandboxing.container_sandbox import ContainerSandbox, SandboxConfig


def test_write_file_uses_docker_exec_stdin_without_shell(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def _run(cmd: list[str], **kwargs: Any) -> Any:
        calls.append({"cmd": cmd, "kwargs": kwargs})
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    sandbox = ContainerSandbox(
        thread_id="abc",
        workspace_path="/tmp/workspace",
        config=SandboxConfig(enabled=True),
        _container_id="container123",
    )
    monkeypatch.setattr("runtime.safety.sandboxing.container_sandbox.subprocess.run", _run)

    result = sandbox.write_file("nested/demo.txt", "hello $(uname)")

    assert result["exit_code"] == 0
    assert calls
    cmd = calls[0]["cmd"]
    assert cmd[:4] == ["docker", "exec", "-i", sandbox.container_name]
    assert "sh" not in cmd
    assert "-c" in cmd  # Python receives code via -c; no shell is involved.
    assert calls[0]["kwargs"]["input"] == "hello $(uname)"


def test_write_file_rejects_workspace_escape() -> None:
    sandbox = ContainerSandbox(
        thread_id="abc",
        workspace_path="/tmp/workspace",
        config=SandboxConfig(enabled=True),
        _container_id="container123",
    )

    result = sandbox.write_file("../outside.txt", "x")

    assert result["exit_code"] == -1
    assert "invalid sandbox path" in result["error"]


def test_container_name_is_safe_for_arbitrary_thread_id() -> None:
    sandbox = ContainerSandbox(
        thread_id="../thread with spaces;$()",
        workspace_path="/tmp/workspace",
    )

    assert sandbox.container_name.startswith("echo-sandbox-")
    suffix = sandbox.container_name.removeprefix("echo-sandbox-")
    assert len(suffix) == 16
    assert suffix.isalnum()
    assert suffix == suffix.lower()


def test_container_name_avoids_truncated_prefix_collisions() -> None:
    first = ContainerSandbox(
        thread_id="thread-prefix-that-used-to-collide-A",
        workspace_path="/tmp/workspace",
    )
    second = ContainerSandbox(
        thread_id="thread-prefix-that-used-to-collide-B",
        workspace_path="/tmp/workspace",
    )

    assert first.container_name != second.container_name

