from __future__ import annotations

import json
from pathlib import Path

from runtime.cli import main


def test_root_prompt_runs_code_session(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setenv("ECHO_HOME", str(tmp_path / "home"))

    rc = main(
        [
            "--no-color",
            "--mock-response",
            "Final Answer: rooted",
            "--print",
            "say rooted from root",
        ]
    )

    assert rc == 0
    assert capsys.readouterr().out.strip() == "rooted"
    sessions = list((tmp_path / "home" / "sessions").glob("*.json"))
    assert len(sessions) == 1
    data = json.loads(sessions[0].read_text(encoding="utf-8"))
    assert data["messages"][0]["content"] == "say rooted from root"


def test_pyproject_exposes_echo_coding_binary() -> None:
    import tomllib

    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert data["project"]["scripts"]["echo"] == "runtime.cli:main"


def test_code_print_saves_session(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setenv("ECHO_HOME", str(tmp_path / "home"))

    rc = main(
        [
            "--no-color",
            "code",
            "say hi",
            "--mock-response",
            "Final Answer: hi",
            "--print",
            "--max-iterations",
            "3",
        ]
    )

    assert rc == 0
    assert capsys.readouterr().out.strip() == "hi"
    sessions = list((tmp_path / "home" / "sessions").glob("*.json"))
    assert len(sessions) == 1
    data = json.loads(sessions[0].read_text(encoding="utf-8"))
    assert data["messages"][-1]["content"] == "hi"


def test_code_continue_reuses_latest_session(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setenv("ECHO_HOME", str(tmp_path / "home"))

    first = main(
        [
            "--no-color",
            "code",
            "first",
            "--mock-response",
            "Final Answer: one",
            "--print",
            "--max-iterations",
            "3",
        ]
    )
    assert first == 0
    capsys.readouterr()

    second = main(
        [
            "--no-color",
            "code",
            "second",
            "--continue",
            "--mock-response",
            "Final Answer: two",
            "--output-format",
            "json",
            "--max-iterations",
            "3",
        ]
    )

    assert second == 0
    payload = json.loads(capsys.readouterr().out)
    session_path = Path(payload["session_path"])
    data = json.loads(session_path.read_text(encoding="utf-8"))
    assert payload["final_answer"] == "two"
    assert payload["continue_command"] == "echo-agent code --continue"
    assert payload["resume_command"].startswith("echo-agent code --resume ")
    assert [m["content"] for m in data["messages"]] == ["first", "one", "second", "two"]


def test_code_list_sessions_json(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setenv("ECHO_HOME", str(tmp_path / "home"))
    assert (
        main(
            [
                "--no-color",
                "code",
                "task",
                "--mock-response",
                "Final Answer: done",
                "--print",
            ]
        )
        == 0
    )
    capsys.readouterr()

    rc = main(["--no-color", "code", "--list-sessions", "--output-format", "json"])

    assert rc == 0
    sessions = json.loads(capsys.readouterr().out)
    assert len(sessions) == 1
    assert sessions[0]["permission_mode"] == "default"


def test_code_model_can_come_from_env(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setenv("ECHO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ECHO_MODEL", "mock/env")

    rc = main(
        [
            "--no-color",
            "code",
            "task",
            "--mock-response",
            "Final Answer: env model",
            "--output-format",
            "json",
        ]
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["model"] == "mock/env"
