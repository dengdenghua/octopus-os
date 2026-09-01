from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from runtime.platform.process.streaming import stream_run
from runtime.safety.sandboxing import sandbox as sandbox_mod
from runtime.safety.sandboxing.sandbox import DirectBackend, SandboxViolation


def test_stream_run_reports_direct_sandbox_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECHO_PROCESS_SANDBOX", "soft")

    result = stream_run(
        [sys.executable, "-c", "from pathlib import Path; print(Path.cwd())"],
        timeout=10,
        sandbox_dir=str(tmp_path),
    )

    assert result["exit_code"] == 0
    assert Path(result["stdout"].strip()).resolve() == tmp_path.resolve()
    assert result["sandbox_backend"] == "direct"
    assert result["sandbox_hard"] is False
    policy = result["execution_policy"]
    assert policy["schema"] == "echo.execution_policy.v1"
    assert policy["sandbox_requested"] is True
    assert policy["workspace"] == str(tmp_path.resolve())
    assert policy["cwd"] == str(tmp_path.resolve())
    assert policy["backend"] == "direct"
    assert policy["hard"] is False
    assert policy["allow_network"] is False
    assert policy["env_mode"] == "allowlist"
    assert policy["process_tree_kill"] is True
    assert policy["result"]["status"] == "completed"
    assert policy["result"]["exit_code"] == 0
    assert policy["result"]["timed_out"] is False
    assert policy["result"]["cancelled"] is False
    assert policy["result"]["output_truncated"] is False


def test_stream_run_sandbox_scrubs_sensitive_env_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECHO_PROCESS_SANDBOX", "soft")

    result = stream_run(
        [
            sys.executable,
            "-c",
            (
                "import os; "
                "print(os.environ.get('OPENAI_API_KEY', 'MISSING')); "
                "print(os.environ.get('CUSTOM_TOKEN', 'MISSING')); "
                "print(os.environ.get('ECHO_EXPLICIT', 'MISSING'))"
            ),
        ],
        timeout=10,
        sandbox_dir=str(tmp_path),
        env={
            "OPENAI_API_KEY": "sk-secret",
            "CUSTOM_TOKEN": "token-secret",
            "ECHO_EXPLICIT": "kept",
        },
    )

    assert result["exit_code"] == 0
    assert "sk-secret" not in result["stdout"]
    assert "token-secret" not in result["stdout"]
    assert result["stdout"].splitlines() == ["MISSING", "MISSING", "kept"]
    assert result["execution_policy"]["env_mode"] == "allowlist"


def test_stream_run_sandbox_redirects_home_and_temp_inside_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECHO_PROCESS_SANDBOX", "soft")

    result = stream_run(
        [
            sys.executable,
            "-c",
            (
                "import json, os, pathlib, tempfile; "
                "home = pathlib.Path.home(); "
                "tmp = pathlib.Path(tempfile.gettempdir()); "
                "(home / 'home-marker.txt').write_text('home'); "
                "(tmp / 'tmp-marker.txt').write_text('tmp'); "
                "print(json.dumps({"
                "'HOME': os.environ.get('HOME'), "
                "'USERPROFILE': os.environ.get('USERPROFILE'), "
                "'TMPDIR': os.environ.get('TMPDIR'), "
                "'TMP': os.environ.get('TMP'), "
                "'TEMP': os.environ.get('TEMP'), "
                "'tempfile': str(tmp)"
                "}))"
            ),
        ],
        timeout=10,
        sandbox_dir=str(tmp_path),
    )

    assert result["exit_code"] == 0
    env = json.loads(result["stdout"])
    expected_home = tmp_path / ".echo-home"
    expected_tmp = tmp_path / ".echo-tmp"
    assert Path(env["HOME"]).resolve() == expected_home.resolve()
    assert Path(env["USERPROFILE"]).resolve() == expected_home.resolve()
    assert Path(env["TMPDIR"]).resolve() == expected_tmp.resolve()
    assert Path(env["TMP"]).resolve() == expected_tmp.resolve()
    assert Path(env["TEMP"]).resolve() == expected_tmp.resolve()
    assert Path(env["tempfile"]).resolve() == expected_tmp.resolve()
    assert (expected_home / "home-marker.txt").read_text(encoding="utf-8") == "home"
    assert (expected_tmp / "tmp-marker.txt").read_text(encoding="utf-8") == "tmp"
    assert result["execution_policy"]["env_mode"] == "allowlist"


def test_stream_run_strict_mode_rejects_without_hard_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECHO_PROCESS_SANDBOX", "strict")
    monkeypatch.setattr(sandbox_mod.BubblewrapBackend, "available", staticmethod(lambda: False))
    monkeypatch.setattr(sandbox_mod.SeatbeltBackend, "available", staticmethod(lambda: False))

    result = stream_run(
        [sys.executable, "-c", "print('should not run')"],
        timeout=10,
        sandbox_dir=str(tmp_path),
    )

    assert "error" in result
    assert "has no usable hard backend" in result["error"]
    assert result["execution_policy"]["schema"] == "echo.execution_policy.v1"
    assert result["execution_policy"]["sandbox_requested"] is True
    assert result["execution_policy"]["result"]["status"] == "sandbox_violation"
    assert result["execution_policy"]["result"]["error_type"] == "sandbox_violation"


def test_commercial_mode_rejects_unconfined_high_risk_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECHO_DEPLOYMENT_MODE", "commercial")

    result = stream_run(
        [sys.executable, "-c", "print('must not run')"],
        timeout=10,
        sandbox_required=True,
    )

    assert result["error"].startswith("sandbox_violation:")
    assert "workspace sandbox" in result["error"]
    assert result["execution_policy"]["sandbox_requested"] is False
    assert result["execution_policy"]["result"]["status"] == "sandbox_violation"


def test_commercial_mode_cannot_downgrade_to_soft_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECHO_DEPLOYMENT_MODE", "commercial")
    monkeypatch.setenv("ECHO_PROCESS_SANDBOX", "soft")
    monkeypatch.setattr(sandbox_mod.BubblewrapBackend, "available", staticmethod(lambda: False))
    monkeypatch.setattr(sandbox_mod.SeatbeltBackend, "available", staticmethod(lambda: False))

    result = stream_run(
        [sys.executable, "-c", "print('must not run')"],
        timeout=10,
        sandbox_dir=str(tmp_path),
        sandbox_required=True,
    )

    assert "has no usable hard backend" in result["error"]
    assert result["execution_policy"]["backend"] == "direct"
    assert result["execution_policy"]["result"]["status"] == "sandbox_violation"


def test_stream_run_uses_selected_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TaggingBackend(DirectBackend):
        def transform(self, argv, env, cwd, policy):  # type: ignore[no-untyped-def]
            return (
                [
                    sys.executable,
                    "-c",
                    "print('wrapped')",
                ],
                env,
                cwd,
            )

    monkeypatch.setattr(
        sandbox_mod,
        "resolved_process_backend",
        lambda: sandbox_mod.BackendChoice(TaggingBackend(), "tagged", hard=True),
    )

    result = stream_run(
        [sys.executable, "-c", "print('original')"],
        timeout=10,
        sandbox_dir=str(tmp_path),
    )

    assert result["exit_code"] == 0
    assert result["stdout"].strip() == "wrapped"
    assert result["sandbox_backend"] == "tagged"
    assert result["sandbox_hard"] is True
    assert result["execution_policy"]["backend"] == "tagged"
    assert result["execution_policy"]["hard"] is True


def test_stream_run_surfaces_backend_violation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RejectingBackend(DirectBackend):
        def transform(self, argv, env, cwd, policy):  # type: ignore[no-untyped-def]
            raise SandboxViolation("backend rejected")

    monkeypatch.setattr(
        sandbox_mod,
        "resolved_process_backend",
        lambda: sandbox_mod.BackendChoice(RejectingBackend(), "reject", hard=True),
    )

    result = stream_run(
        [sys.executable, "-c", "print('should not run')"],
        timeout=10,
        sandbox_dir=str(tmp_path),
    )

    assert result["error"] == "sandbox_violation: backend rejected"
    assert result["execution_policy"]["schema"] == "echo.execution_policy.v1"
    assert result["execution_policy"]["sandbox_requested"] is True
    assert result["execution_policy"]["result"]["status"] == "sandbox_violation"


def test_stream_run_timeout_kills_child_process_tree(tmp_path: Path) -> None:
    marker = tmp_path / "child-survived.txt"
    code = (
        "import subprocess, sys, time\n"
        "subprocess.Popen([\n"
        "    sys.executable,\n"
        "    '-c',\n"
        f"    \"import pathlib, time; time.sleep(1.0); pathlib.Path({str(marker)!r}).write_text('alive')\",\n"
        "])\n"
        "time.sleep(10)\n"
    )

    result = stream_run(
        [sys.executable, "-c", code],
        timeout=0.2,
        cwd=str(tmp_path),
    )

    assert result["timed_out"] is True
    assert result["killed"] is True
    assert result["execution_policy"]["result"]["status"] == "timed_out"
    assert result["execution_policy"]["result"]["timed_out"] is True
    assert result["execution_policy"]["result"]["killed"] is True
    time.sleep(1.2)
    assert not marker.exists()


def test_stream_run_execution_policy_records_output_truncation(tmp_path: Path) -> None:
    payload = "PAYLOAD_SHOULD_NOT_APPEAR"
    result = stream_run(
        [sys.executable, "-c", f"print({payload!r} * 20)"],
        timeout=10,
        cwd=str(tmp_path),
        output_cap_bytes=16,
    )

    assert result["exit_code"] == 0
    assert result["stdout_truncated"] is True
    policy_result = result["execution_policy"]["result"]
    assert policy_result["status"] == "completed"
    assert policy_result["stdout_truncated"] is True
    assert policy_result["output_truncated"] is True
    assert payload not in str(result["execution_policy"])


def test_stream_run_output_cap_is_measured_in_utf8_bytes(tmp_path: Path) -> None:
    result = stream_run(
        [sys.executable, "-c", "import sys; sys.stdout.write('界' * 10)"],
        timeout=10,
        cwd=str(tmp_path),
        output_cap_bytes=5,
    )

    assert result["exit_code"] == 0
    assert result["stdout_truncated"] is True
    assert len(result["stdout"].encode("utf-8")) <= 5
    assert result["stdout"] == "界"
    policy_result = result["execution_policy"]["result"]
    assert policy_result["stdout_truncated"] is True
    assert policy_result["output_truncated"] is True

