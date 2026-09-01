"""Tests for the cross-platform :class:`SandboxRunner`.

The runner has to behave the same on Windows and POSIX, so each test
uses ``sys.executable`` (always available) and feeds Python a one-liner
via ``-c``. That dodges shell-builtin differences (``echo``, ``ls``,
``true``) without losing test coverage.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from runtime.safety.sandboxing.sandbox import (
    DirectBackend,
    SandboxPolicy,
    SandboxRunner,
    SandboxViolation,
)


def _python(*code_chunks: str) -> list[str]:
    """Run a tiny Python program via the test interpreter."""
    return [sys.executable, "-c", "; ".join(code_chunks)]


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


class TestRun:
    def test_basic_stdout(self, workspace: Path) -> None:
        runner = SandboxRunner(SandboxPolicy(workspace=workspace, timeout_s=10.0))
        result = runner.run(_python("print('hi')"))
        assert result.exit_code == 0
        assert "hi" in result.stdout
        assert result.timed_out is False

    def test_non_zero_exit(self, workspace: Path) -> None:
        runner = SandboxRunner(SandboxPolicy(workspace=workspace, timeout_s=10.0))
        result = runner.run(_python("import sys; sys.exit(2)"))
        assert result.exit_code == 2

    def test_stderr_captured(self, workspace: Path) -> None:
        runner = SandboxRunner(SandboxPolicy(workspace=workspace, timeout_s=10.0))
        result = runner.run(_python("import sys; sys.stderr.write('boom'); sys.stderr.flush()"))
        assert "boom" in result.stderr

    def test_stdin_text(self, workspace: Path) -> None:
        runner = SandboxRunner(SandboxPolicy(workspace=workspace, timeout_s=10.0))
        result = runner.run(
            _python("import sys; print(sys.stdin.read().strip().upper())"),
            stdin_text="hello",
        )
        assert "HELLO" in result.stdout

    def test_streaming_callback_receives_chunks(self, workspace: Path) -> None:
        chunks: list[str] = []
        runner = SandboxRunner(SandboxPolicy(workspace=workspace, timeout_s=10.0))
        runner.run(
            _python("for i in range(3):\n    print('line' + str(i), flush=True)\n"),
            on_output=chunks.append,
        )
        assert any("line" in c for c in chunks)


class TestEnvironmentScrub:
    def test_secret_env_blocked_by_default(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_API_KEY", "TOPSECRET")
        runner = SandboxRunner(SandboxPolicy(workspace=workspace, timeout_s=10.0))
        result = runner.run(_python("import os; print(os.environ.get('FAKE_API_KEY', 'MISSING'))"))
        assert "MISSING" in result.stdout
        assert "TOPSECRET" not in result.stdout

    def test_extra_env_passed_through(self, workspace: Path) -> None:
        runner = SandboxRunner(
            SandboxPolicy(
                workspace=workspace,
                timeout_s=10.0,
                extra_env={"OCT_INJECT": "yes"},
            )
        )
        result = runner.run(_python("import os; print(os.environ.get('OCT_INJECT', 'no'))"))
        assert "yes" in result.stdout

    def test_no_network_sets_proxy_short_circuit(self, workspace: Path) -> None:
        runner = SandboxRunner(SandboxPolicy(workspace=workspace, timeout_s=10.0))
        result = runner.run(
            _python("import os; print(os.environ.get('no_proxy'), os.environ.get('http_proxy'))")
        )
        assert "*" in result.stdout
        assert "127.0.0.1:1" in result.stdout


class TestCwdIsolation:
    def test_default_cwd_is_workspace(self, workspace: Path) -> None:
        runner = SandboxRunner(SandboxPolicy(workspace=workspace, timeout_s=10.0))
        result = runner.run(_python("import os; print(os.getcwd())"))
        # Comparing via Path normalises Windows drive-letter casing.
        assert Path(result.stdout.strip()).resolve() == workspace.resolve()

    def test_subdir_cwd_allowed(self, workspace: Path) -> None:
        sub = workspace / "sub"
        sub.mkdir()
        runner = SandboxRunner(SandboxPolicy(workspace=workspace, timeout_s=10.0))
        result = runner.run(
            _python("import os; print(os.getcwd())"),
            cwd=sub,
        )
        assert Path(result.stdout.strip()).resolve() == sub.resolve()

    def test_outside_cwd_rejected(self, workspace: Path, tmp_path: Path) -> None:
        outside = tmp_path.parent
        runner = SandboxRunner(SandboxPolicy(workspace=workspace, timeout_s=10.0))
        with pytest.raises(SandboxViolation):
            runner.run(_python("print('x')"), cwd=outside)

    def test_nonexistent_cwd_rejected(self, workspace: Path) -> None:
        runner = SandboxRunner(SandboxPolicy(workspace=workspace, timeout_s=10.0))
        with pytest.raises(SandboxViolation):
            runner.run(_python("print('x')"), cwd=workspace / "missing")


class TestLimits:
    def test_timeout_kills_process(self, workspace: Path) -> None:
        runner = SandboxRunner(SandboxPolicy(workspace=workspace, timeout_s=0.5))
        result = runner.run(_python("import time; time.sleep(10)"))
        assert result.timed_out is True
        assert result.killed is True

    def test_output_cap_truncates(self, workspace: Path) -> None:
        runner = SandboxRunner(
            SandboxPolicy(workspace=workspace, timeout_s=10.0, max_output_bytes=64)
        )
        # Each line is ~10 chars; produce more than the cap.
        result = runner.run(_python("for i in range(200):\n    print('xxxxxxxx', flush=True)\n"))
        assert result.truncated is True
        assert len(result.stdout) <= 64

    def test_empty_command_rejected(self, workspace: Path) -> None:
        runner = SandboxRunner(SandboxPolicy(workspace=workspace, timeout_s=10.0))
        with pytest.raises(SandboxViolation):
            runner.run([])

    def test_missing_executable_rejected(self, workspace: Path) -> None:
        runner = SandboxRunner(SandboxPolicy(workspace=workspace, timeout_s=10.0))
        with pytest.raises(SandboxViolation):
            runner.run(["this-binary-definitely-does-not-exist-xyz"])


class TestBackendSeam:
    def test_direct_backend_passes_through(self, workspace: Path) -> None:
        runner = SandboxRunner(
            SandboxPolicy(workspace=workspace, timeout_s=10.0),
            backend=DirectBackend(),
        )
        result = runner.run(_python("print('seam')"))
        assert "seam" in result.stdout

    def test_custom_backend_can_inject_args(self, workspace: Path) -> None:
        captured: dict[str, list[str]] = {}

        class TaggingBackend:
            def transform(self, argv, env, cwd, policy):  # type: ignore[no-untyped-def]
                captured["argv"] = list(argv)
                # Don't actually rewrap — we just assert the seam fires.
                return argv, env, cwd

        runner = SandboxRunner(
            SandboxPolicy(workspace=workspace, timeout_s=10.0),
            backend=TaggingBackend(),
        )
        runner.run(_python("print('via backend')"))
        assert captured["argv"][0] == sys.executable
