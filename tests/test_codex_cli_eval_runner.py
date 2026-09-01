from __future__ import annotations

import json
import subprocess

from benchmarks import codex_cli_runner


def test_codex_cli_runner_uses_ephemeral_json_without_shell(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        stdout = "\n".join(
            [
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"type": "agent_message", "text": "done"},
                    }
                ),
                json.dumps(
                    {
                        "type": "item.started",
                        "item": {"type": "collab_agent_tool_call", "agent": "worker-1"},
                    }
                ),
                json.dumps(
                    {
                        "type": "item.started",
                        "item": {"type": "command_execution", "command": "pwd"},
                    }
                ),
            ]
        )
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(codex_cli_runner.subprocess, "run", fake_run)
    runner = codex_cli_runner.CodexCliTrialRunner(
        executable="/opt/codex",
        workspace=tmp_path,
        model="gpt-test",
        ignore_user_config=True,
    )

    events = list(runner("do work"))

    command = captured["command"]
    assert isinstance(command, list)
    assert command[:2] == ["/opt/codex", "exec"]
    assert "--json" in command
    assert "--ephemeral" in command
    assert "--ignore-user-config" in command
    assert command[-1] == "-"
    assert captured["input"] == "do work"
    assert "shell" not in captured
    assert {"kind": "text_delta", "delta": "done"} in events
    assert any(event["kind"] == "tool_start" for event in events)
    assert any(
        event["kind"] == "tool_start" and event["tool_name"] == "subagent" for event in events
    )


def test_codex_cli_runner_records_nonzero_exit(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        codex_cli_runner.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            7,
            stdout="",
            stderr="provider failed",
        ),
    )

    events = list(
        codex_cli_runner.CodexCliTrialRunner(
            executable="codex",
            workspace=tmp_path,
        )("work")
    )

    assert events == [
        {
            "kind": "error",
            "error": {"returncode": 7, "stderr": "provider failed"},
        }
    ]


def test_codex_cli_runner_classifies_stream_disconnect_as_infrastructure(
    monkeypatch,
    tmp_path,
) -> None:
    stdout = json.dumps(
        {
            "type": "error",
            "message": (
                "Reconnecting... 5/5 (stream disconnected before completion: tls handshake eof)"
            ),
        }
    )
    monkeypatch.setattr(
        codex_cli_runner.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            0,
            stdout=stdout,
            stderr="",
        ),
    )

    events = list(
        codex_cli_runner.CodexCliTrialRunner(
            executable="codex",
            workspace=tmp_path,
        )("work")
    )

    assert events == [
        {
            "kind": "infrastructure_error",
            "error": {
                "type": "error",
                "message": (
                    "Reconnecting... 5/5 (stream disconnected before completion: tls handshake eof)"
                ),
            },
        }
    ]


def test_codex_cli_runner_classifies_provider_nonzero_exit_as_infrastructure(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        codex_cli_runner.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr="provider unavailable: HTTP 503",
        ),
    )

    events = list(
        codex_cli_runner.CodexCliTrialRunner(
            executable="codex",
            workspace=tmp_path,
        )("work")
    )

    assert events == [
        {
            "kind": "infrastructure_error",
            "error": {
                "returncode": 1,
                "stderr": "provider unavailable: HTTP 503",
                "type": "infrastructure",
            },
        }
    ]


def test_codex_cli_runner_classifies_http_402_as_infrastructure(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        codex_cli_runner.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr="HTTP 402 payment required: insufficient balance",
        ),
    )

    events = list(
        codex_cli_runner.CodexCliTrialRunner(
            executable="codex",
            workspace=tmp_path,
        )("work")
    )

    assert events[0]["kind"] == "infrastructure_error"
    assert events[0]["error"]["type"] == "infrastructure"

