"""Isolated fixture lifecycle and deterministic outcome graders."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any

from benchmarks.eval_harness import Trajectory, Verdict
from benchmarks.verifier_sandbox import VerifierProcessResult, run_hidden_verifier

TrajectoryValidator = Callable[[Trajectory], str | None]


@dataclass
class IsolatedFixture:
    template: str | Path
    runs_root: str | Path
    preserve_runs: bool = False
    _current: Path | None = field(default=None, init=False, repr=False)

    def setup(self) -> None:
        if self._current is not None:
            raise RuntimeError("fixture trial is already active")
        template = Path(self.template).resolve()
        runs_root = Path(self.runs_root).resolve()
        if not template.is_dir():
            raise ValueError(f"fixture template does not exist: {template}")
        try:
            runs_root.relative_to(template)
        except ValueError:
            pass
        else:
            raise ValueError("runs_root must not be inside the fixture template")
        runs_root.mkdir(parents=True, exist_ok=True)
        destination = runs_root / f"trial-{uuid.uuid4().hex}"
        try:
            shutil.copytree(template, destination, symlinks=True)
        except Exception:
            shutil.rmtree(destination, ignore_errors=True)
            raise
        self._current = destination

    def workspace(self) -> Path:
        if self._current is None:
            raise RuntimeError("fixture trial is not active")
        return self._current

    def teardown(self) -> None:
        current = self._current
        self._current = None
        if current is not None and not self.preserve_runs:
            shutil.rmtree(current, ignore_errors=False)


@dataclass
class PythonTestFixture(IsolatedFixture):
    """Coding fixture with one harness-owned, repeatable pytest command.

    Model-driven shell commands must not depend on whichever ``python`` or
    ``pytest`` happens to appear first on the realtime server's ``PATH``.  The
    runner is created inside each disposable workspace, but invokes the exact
    interpreter that launched the benchmark harness.
    """

    python_executable: str = field(default_factory=lambda: os.path.abspath(sys.executable))
    _runner_provenance: dict[str, str] | None = field(default=None, init=False, repr=False)

    def setup(self) -> None:
        super().setup()
        try:
            provenance = python_test_runner_provenance(self.python_executable)
            _write_python_test_runner(
                self.workspace(),
                python_executable=self.python_executable,
                expected_sha256=str(provenance["runner_sha256"]),
            )
            _preflight_python_test_runner(self.workspace(), provenance)
            self._runner_provenance = provenance
        except Exception:
            super().teardown()
            raise

    def assert_runner_integrity(self) -> None:
        provenance = self._runner_provenance
        if provenance is None:
            raise RuntimeError("coding fixture test runner was not provisioned")
        runner = self.workspace() / provenance["runner_path"]
        if runner.parent.is_symlink() or not runner.is_file() or runner.is_symlink():
            raise RuntimeError("evaluator-owned coding fixture test runner is missing or unsafe")
        observed = sha256(runner.read_bytes()).hexdigest()
        if observed != provenance["runner_sha256"]:
            raise RuntimeError("evaluator-owned coding fixture test runner was modified")
        if os.name != "nt" and not os.access(runner, os.X_OK):
            raise RuntimeError("evaluator-owned coding fixture test runner is not executable")

    def teardown(self) -> None:
        self._runner_provenance = None
        super().teardown()


@dataclass
class LiveIsolatedFixture(IsolatedFixture):
    server_command: Sequence[str] = field(default_factory=tuple)
    startup_timeout_seconds: float = 10.0
    _server: subprocess.Popen[str] | None = field(default=None, init=False, repr=False)

    def setup(self) -> None:
        super().setup()
        workspace = self.workspace()
        command = [part.replace("{workspace}", str(workspace)) for part in self.server_command]
        if not command:
            super().teardown()
            raise ValueError("live fixture server_command is empty")
        self._server = subprocess.Popen(
            command,
            cwd=workspace,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        url_path = workspace / "EVAL_URL.txt"
        deadline = time.monotonic() + self.startup_timeout_seconds
        while time.monotonic() < deadline:
            if self._server.poll() is not None:
                break
            if url_path.exists() and url_path.read_text(encoding="utf-8").strip():
                return
            time.sleep(0.02)
        self._stop_server()
        super().teardown()
        raise RuntimeError("live fixture server failed to become ready")

    def url(self) -> str:
        return (self.workspace() / "EVAL_URL.txt").read_text(encoding="utf-8").strip()

    def teardown(self) -> None:
        self._stop_server()
        super().teardown()

    def _stop_server(self) -> None:
        process = self._server
        self._server = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def python_test_runner_provenance(python_executable: str | None = None) -> dict[str, str]:
    """Probe and describe the evaluator-owned coding-fixture test runner."""

    python = os.path.abspath(python_executable or sys.executable)
    if not Path(python).is_file():
        raise RuntimeError(f"coding fixture interpreter is unavailable: {python}")
    probe = (
        "import json, platform, pytest; "
        "print(json.dumps({'python_version': platform.python_version(), "
        "'pytest_version': pytest.__version__}, sort_keys=True))"
    )
    try:
        completed = subprocess.run(
            [python, "-I", "-c", probe],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"coding fixture interpreter preflight failed: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
        raise RuntimeError(f"coding fixture pytest preflight failed: {detail[-1000:]}")
    try:
        payload = json.loads(completed.stdout.strip())
        python_version = str(payload["python_version"])
        pytest_version = str(payload["pytest_version"])
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RuntimeError("coding fixture pytest preflight returned invalid metadata") from exc
    runner_path, content = _python_test_runner_content(python)
    return {
        "schema": "echo.fixture_test_runner.v1",
        "ownership": "evaluator",
        "interpreter_path": python,
        "python_version": python_version,
        "pytest_version": pytest_version,
        "runner_path": runner_path,
        "runner_sha256": sha256(content.encode("utf-8")).hexdigest(),
        "test_root": "tests",
    }


def _python_test_runner_content(python_executable: str) -> tuple[str, str]:
    if os.name == "nt":
        path = ".echo-eval/run-tests.cmd"
        content = (
            "@echo off\r\n"
            "set PYTHONDONTWRITEBYTECODE=1\r\n"
            "set PYTHONNOUSERSITE=1\r\n"
            "set PYTHONPATH=\r\n"
            "set PYTHONHOME=\r\n"
            'if "%~1"=="--echo-preflight" goto preflight\r\n'
            'if not "%~1"=="" goto unsupported\r\n'
            f'"{python_executable}" -m pytest -p no:cacheprovider --tb=short -q tests\r\n'
            "exit /b %ERRORLEVEL%\r\n"
            ":preflight\r\n"
            'if not "%~2"=="" goto unsupported\r\n'
            f'"{python_executable}" -I -c "import pytest; '
            "print('pytest ' + pytest.__version__)\"\r\n"
            "exit /b %ERRORLEVEL%\r\n"
            ":unsupported\r\n"
            "echo unsupported evaluator test-runner argument 1>&2\r\n"
            "exit /b 64\r\n"
        )
        return path, content
    path = ".echo-eval/run-tests"
    content = (
        "#!/bin/sh\n"
        "export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1\n"
        "unset PYTHONPATH PYTHONHOME\n"
        'if [ "$#" -eq 1 ] && [ "$1" = "--echo-preflight" ]; then\n'
        f"  exec {shlex.quote(python_executable)} -I -c "
        "'import pytest; print(\"pytest \" + pytest.__version__)'\n"
        "fi\n"
        'if [ "$#" -ne 0 ]; then\n'
        "  echo 'unsupported evaluator test-runner argument' >&2\n"
        "  exit 64\n"
        "fi\n"
        f"exec {shlex.quote(python_executable)} -m pytest "
        "-p no:cacheprovider --tb=short -q tests\n"
    )
    return path, content


def _write_python_test_runner(
    workspace: Path,
    *,
    python_executable: str,
    expected_sha256: str,
) -> None:
    workspace = workspace.resolve(strict=True)
    runner_dir = workspace / ".echo-eval"
    runner_dir.mkdir(mode=0o700)
    relative_path, content = _python_test_runner_content(python_executable)
    if sha256(content.encode("utf-8")).hexdigest() != expected_sha256:
        raise RuntimeError("coding fixture runner provenance changed after preflight")
    runner = workspace / relative_path
    fd, temporary_name = tempfile.mkstemp(prefix=".run-tests-", dir=runner_dir)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            temporary.chmod(0o755)
        os.replace(temporary, runner)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _preflight_python_test_runner(workspace: Path, provenance: dict[str, str]) -> None:
    runner = workspace / provenance["runner_path"]
    if os.name == "nt":
        comspec = os.environ.get("COMSPEC") or "cmd.exe"
        command = [comspec, "/d", "/s", "/c", f'""{runner}" --echo-preflight"']
    else:
        command = [str(runner), "--echo-preflight"]
    try:
        completed = subprocess.run(
            command,
            cwd=workspace,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"coding fixture test runner preflight failed: {exc}") from exc
    output = (completed.stdout + completed.stderr).strip()
    if completed.returncode != 0 or "pytest" not in output.lower():
        detail = output or f"exit {completed.returncode}"
        raise RuntimeError(f"coding fixture test runner preflight failed: {detail[-1000:]}")
    observed = sha256(runner.read_bytes()).hexdigest()
    if observed != provenance["runner_sha256"]:
        raise RuntimeError("coding fixture test runner changed during preflight")


@dataclass(frozen=True)
class SubprocessOutcomeGrader:
    fixture: IsolatedFixture
    command: Sequence[str]
    rubric: dict[str, Any]
    timeout_seconds: float = 120.0
    extra_env: dict[str, str] = field(default_factory=dict)
    trajectory_validator: TrajectoryValidator | None = None
    hidden_verifier_source: str | Path | None = None
    hidden_verifier_arguments: Sequence[str] = field(default_factory=tuple)
    hidden_verifier_sha256: str | None = None
    hidden_verifier_infrastructure_exit_codes: frozenset[int] = field(default_factory=frozenset)

    def __call__(self, _trajectory: Trajectory) -> Verdict:
        workspace = self.fixture.workspace()
        if isinstance(self.fixture, PythonTestFixture):
            self.fixture.assert_runner_integrity()
        completed: VerifierProcessResult | subprocess.CompletedProcess[str]
        if self.hidden_verifier_source is not None:
            if self.extra_env:
                raise RuntimeError(
                    "sandboxed hidden verifiers do not accept ambient environment overrides"
                )
            completed = run_hidden_verifier(
                verifier_source=self.hidden_verifier_source,
                argument_templates=self.hidden_verifier_arguments,
                workspace=workspace,
                timeout_seconds=self.timeout_seconds,
                infrastructure_exit_codes=self.hidden_verifier_infrastructure_exit_codes,
                expected_source_sha256=self.hidden_verifier_sha256,
            )
        else:
            command = [part.replace("{workspace}", str(workspace)) for part in self.command]
            completed = subprocess.run(
                command,
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
                env={**os.environ, "ECHO_BEHAVIORAL_EVAL": "1", **self.extra_env},
            )
        if completed.returncode != 0:
            reason = (
                completed.stderr.strip()
                or completed.stdout.strip()
                or (f"verifier exited {completed.returncode}")
            )
            return Verdict(
                passed=False,
                score=0.0,
                reason=reason[-4000:],
                rubric=self.rubric,
            )
        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        if not lines:
            return Verdict(
                passed=False,
                score=0.0,
                reason="verifier produced no JSON result",
                rubric=self.rubric,
            )
        try:
            payload = json.loads(lines[-1])
        except json.JSONDecodeError as exc:
            return Verdict(
                passed=False,
                score=0.0,
                reason=f"verifier result is not JSON: {exc}",
                rubric=self.rubric,
            )
        if not isinstance(payload, dict) or not isinstance(payload.get("passed"), bool):
            return Verdict(
                passed=False,
                score=0.0,
                reason="verifier JSON must contain boolean passed",
                rubric=self.rubric,
            )
        raw_score = payload.get("score", 1.0 if payload["passed"] else 0.0)
        score = float(raw_score) if isinstance(raw_score, int | float) else 0.0
        if not 0.0 <= score <= 1.0:
            return Verdict(
                passed=False,
                score=0.0,
                reason="verifier score must be between 0 and 1",
                rubric=self.rubric,
            )
        if payload["passed"] and self.trajectory_validator is not None:
            trajectory_error = self.trajectory_validator(_trajectory)
            if trajectory_error:
                return Verdict(
                    passed=False,
                    score=0.0,
                    reason=trajectory_error,
                    rubric={**self.rubric, "observed_checks": payload.get("checks") or []},
                )
        return Verdict(
            passed=payload["passed"],
            score=score,
            reason=str(payload.get("reason") or ""),
            rubric={**self.rubric, "observed_checks": payload.get("checks") or []},
        )


__all__ = [
    "IsolatedFixture",
    "LiveIsolatedFixture",
    "PythonTestFixture",
    "SubprocessOutcomeGrader",
    "TrajectoryValidator",
    "python_test_runner_provenance",
]


