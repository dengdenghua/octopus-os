from __future__ import annotations

import os
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest

from benchmarks.eval_harness import Trajectory, run_case, run_suite_by_case
from benchmarks.fixed_suite_fixtures import (
    FIXTURE_SPECS,
    FixtureSpec,
    _trajectory_requirement,
    prepare_coding_fixture_suite,
    prepare_fixture_suite,
)
from benchmarks.fixture_grading import PythonTestFixture, python_test_runner_provenance
from benchmarks.trusted_verifier_controller import UnsafeLocalWorkerLauncher
from benchmarks.verifier_sandbox import FixtureInfrastructureError
from benchmarks.verifiers import verify_path_boundary

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_fixture_spec_registry_is_single_read_only_binding() -> None:
    assert FIXTURE_SPECS["coding.path-boundary"] == FixtureSpec(
        fixture_name="coding.path-boundary",
        verifier_name="verify_path_boundary.py",
    )
    with pytest.raises(TypeError):
        FIXTURE_SPECS["new.case"] = FixtureSpec("new.case", "verify.py")  # type: ignore[index]


def test_coding_fixture_provides_isolated_repeatable_test_command(tmp_path) -> None:
    prepared = prepare_fixture_suite(
        repo_root=REPO_ROOT,
        runs_root=tmp_path / "runs",
        preserve_runs=True,
        case_ids={"coding.path-boundary"},
    )
    fixture = prepared.fixtures["coding.path-boundary"]
    fixture.setup()
    first_workspace = fixture.workspace()
    try:
        tests_dir = first_workspace / "tests"
        (tests_dir / "test_runner_smoke.py").write_text(
            "def test_runner_smoke():\n    assert True\n",
            encoding="utf-8",
        )
        runner = (
            first_workspace
            / ".echo-eval"
            / ("run-tests.cmd" if os.name == "nt" else "run-tests")
        )
        provenance = python_test_runner_provenance()
        assert provenance["ownership"] == "evaluator"
        assert provenance["interpreter_path"] == os.path.abspath(sys.executable)
        assert provenance["runner_path"] == runner.relative_to(first_workspace).as_posix()
        assert len(provenance["runner_sha256"]) == 64
        assert sha256(runner.read_bytes()).hexdigest() == provenance["runner_sha256"]
        command_env = None
        if os.name != "nt":
            empty_path = tmp_path / "path-without-python"
            empty_path.mkdir()
            command_env = {**os.environ, "PATH": str(empty_path)}
        completed = subprocess.run(
            [str(runner)],
            cwd=first_workspace,
            env=command_env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert "1 passed" in completed.stdout
        assert not list(first_workspace.rglob("__pycache__"))
        assert not (first_workspace / ".pytest_cache").exists()
        assert {path.name for path in runner.parent.iterdir()} == {runner.name}
    finally:
        fixture.teardown()

    fixture.setup()
    try:
        assert fixture.workspace() != first_workspace
        assert not (fixture.workspace() / "tests" / "test_runner_smoke.py").exists()
    finally:
        fixture.teardown()
    assert not (REPO_ROOT / "benchmarks/fixtures/coding.path-boundary/.echo-eval").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX launcher quoting test")
def test_coding_fixture_runner_handles_interpreter_path_with_spaces(tmp_path) -> None:
    interpreter_parent = tmp_path / "interpreter with spaces"
    interpreter_parent.symlink_to(Path(sys.prefix), target_is_directory=True)
    fixture = PythonTestFixture(
        template=REPO_ROOT / "benchmarks/fixtures/coding.path-boundary",
        runs_root=tmp_path / "runs",
        python_executable=str(interpreter_parent / "bin/python"),
    )

    fixture.setup()
    try:
        (fixture.workspace() / "tests" / "test_space.py").write_text(
            "def test_space():\n    assert True\n",
            encoding="utf-8",
        )
        runner = fixture.workspace() / ".echo-eval/run-tests"
        completed = subprocess.run(
            [str(runner)],
            cwd=fixture.workspace(),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
    finally:
        fixture.teardown()


def test_coding_fixture_preflight_does_not_run_business_tests(tmp_path) -> None:
    template = tmp_path / "template"
    tests_dir = template / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_intentionally_red.py").write_text(
        "raise RuntimeError('business tests must not run during preflight')\n",
        encoding="utf-8",
    )
    fixture = PythonTestFixture(template=template, runs_root=tmp_path / "runs")

    fixture.setup()
    try:
        runner = (
            fixture.workspace()
            / ".echo-eval"
            / ("run-tests.cmd" if os.name == "nt" else "run-tests")
        )
        completed = subprocess.run(
            [str(runner)],
            cwd=fixture.workspace(),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert completed.returncode != 0
        assert "business tests must not run during preflight" in (
            completed.stdout + completed.stderr
        )
    finally:
        fixture.teardown()


@pytest.mark.parametrize("tamper", ["modify", "delete"])
def test_coding_fixture_runner_tampering_is_infrastructure_failure(tmp_path, tamper) -> None:
    prepared = prepare_fixture_suite(
        repo_root=REPO_ROOT,
        runs_root=tmp_path / "runs",
        case_ids={"coding.path-boundary"},
    )
    case = prepared.cases[0]

    def runner(_prompt):
        runner_path = (
            prepared.workspace(case.id)
            / ".echo-eval"
            / ("run-tests.cmd" if os.name == "nt" else "run-tests")
        )
        if tamper == "modify":
            runner_path.write_text("tampered\n", encoding="utf-8")
        else:
            runner_path.unlink()
        yield {"kind": "text_delta", "delta": "done"}

    result = run_case(case, runner=runner, k=1)

    assert result.passes == 0
    assert result.trajectories[0].failure_category == "infrastructure"
    assert "test runner" in result.verdicts[0].reason


def test_hidden_verifier_isolation_unavailable_is_infrastructure_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from benchmarks import fixture_grading

    prepared = prepare_fixture_suite(
        repo_root=REPO_ROOT,
        runs_root=tmp_path / "runs",
        case_ids={"coding.path-boundary"},
    )
    case = prepared.cases[0]

    def unavailable(**_kwargs):
        raise FixtureInfrastructureError("hardened verifier isolation unavailable")

    monkeypatch.setattr(fixture_grading, "run_hidden_verifier", unavailable)
    result = run_case(
        case,
        runner=lambda _prompt: iter([{"kind": "text_delta", "delta": "done"}]),
        k=1,
    )

    assert result.passes == 0
    assert result.trajectories[0].failure_category == "infrastructure"
    assert "hardened verifier isolation unavailable" in result.verdicts[0].reason


def test_coding_prompts_expose_test_directory_and_repeatable_command(tmp_path) -> None:
    prepared = prepare_coding_fixture_suite(
        repo_root=REPO_ROOT,
        runs_root=tmp_path / "runs",
    )

    assert len(prepared.cases) == 2
    for case in prepared.cases:
        assert "tests/" in case.prompt
        assert "./.echo-eval/run-tests" in case.prompt
        assert "Do not assume bare python, pytest, or ruff" in case.prompt


def test_path_boundary_grader_requires_tests_directory(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "tests").mkdir(parents=True)
    (workspace / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8"
    )
    (workspace / "file_service.py").write_text(
        """\
from pathlib import Path
from urllib.parse import unquote

class PathBoundaryError(ValueError):
    pass

class FileService:
    def __init__(self, root):
        self.root = Path(root)

    def read_text(self, user_path):
        decoded = user_path
        for _ in range(4):
            updated = unquote(decoded)
            if updated == decoded:
                break
            decoded = updated
        root = self.root.resolve()
        candidate = (root / decoded).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise PathBoundaryError("escape") from exc
        return candidate.read_text(encoding="utf-8")
""",
        encoding="utf-8",
    )
    launcher = UnsafeLocalWorkerLauncher()
    result = verify_path_boundary._run(workspace, launcher=launcher)
    assert result["passed"] is False
    assert "focused regression tests" in str(result["reason"])

    (workspace / "tests" / "test_file_service.py").write_text(
        "def test_regression(): assert True\n",
        encoding="utf-8",
    )
    assert verify_path_boundary._run(workspace, launcher=launcher)["passed"] is True


def test_coding_fixed_suite_is_infrastructure_invalid_without_hardened_runner(
    tmp_path,
) -> None:
    prepared = prepare_coding_fixture_suite(
        repo_root=REPO_ROOT,
        runs_root=tmp_path / "runs",
    )

    report = run_suite_by_case(
        prepared.cases,
        runner_factory=lambda _case: (
            lambda _prompt: iter([{"kind": "text_delta", "delta": "no changes"}])
        ),
        k=1,
    )

    assert {case.id for case in prepared.cases} == {
        "coding.concurrent-cache",
        "coding.path-boundary",
    }
    assert report.aggregate_pass_pow_k == 0.0
    assert all(result.verdicts[0].reason for result in report.cases)
    assert all(
        result.trajectories[0].failure_category == "infrastructure" for result in report.cases
    )
    assert all(
        "permission diagnostics only" in result.verdicts[0].reason for result in report.cases
    )
    assert all(not any(path.iterdir()) for path in (tmp_path / "runs").iterdir())


def test_all_implemented_fixed_fixtures_fail_closed_on_starters(tmp_path) -> None:
    prepared = prepare_fixture_suite(
        repo_root=REPO_ROOT,
        runs_root=tmp_path / "all-runs",
    )

    report = run_suite_by_case(
        prepared.cases,
        runner_factory=lambda _case: (
            lambda _prompt: iter([{"kind": "text_delta", "delta": "no changes"}])
        ),
        k=1,
    )

    assert len(prepared.cases) == 14
    assert report.aggregate_pass_pow_k == 0.0
    assert all(result.passes == 0 for result in report.cases)
    assert all(
        result.trajectories[0].failure_category == "infrastructure" for result in report.cases
    )


def test_sensitive_cases_require_behavioral_trajectory_evidence() -> None:
    parallel = Trajectory(trial_id="parallel", case_id="multiagent.parallel-evidence")
    assert _trajectory_requirement("multiagent.parallel-evidence", parallel)
    for _ in range(3):
        parallel.append("tool_start", tool_name="subagent")
    # The outcome grader, rather than an Echo-specific bb_* tool name,
    # validates the shared workspace handoff for cross-client comparisons.
    assert _trajectory_requirement("multiagent.parallel-evidence", parallel) is None

    denied = Trajectory(trial_id="denied", case_id="security.denied-destructive-action")
    assert _trajectory_requirement("security.denied-destructive-action", denied)
    denied.append(
        "approval_request",
        method="item/commandExecution/requestApproval",
    )
    assert _trajectory_requirement("security.denied-destructive-action", denied) is None

    resume = Trajectory(trial_id="resume", case_id="memory.context-reset-resume")
    resume.append("phase_start", phase_index=1)
    assert _trajectory_requirement("memory.context-reset-resume", resume)
    resume.append("phase_start", phase_index=2)
    assert _trajectory_requirement("memory.context-reset-resume", resume) is None


def test_browser_cases_require_real_ui_tool_trajectories() -> None:
    crud = Trajectory(trial_id="crud", case_id="browser.dynamic-crud")
    assert _trajectory_requirement("browser.dynamic-crud", crud)
    crud.append("tool_start", tool_name="browser_navigate")
    # Select controls can be changed through click semantics; two explicit
    # type actions plus four clicks are still a real UI trajectory.
    for _ in range(2):
        crud.append("tool_start", tool_name="browser_type")
    for _ in range(4):
        crud.append("tool_start", tool_name="browser_click")
    crud.append("tool_start", tool_name="browser_get")
    assert _trajectory_requirement("browser.dynamic-crud", crud) is None

    editor = Trajectory(trial_id="editor", case_id="browser.rich-editor-upload")
    assert _trajectory_requirement("browser.rich-editor-upload", editor)
    editor.append("tool_start", tool_name="browser_navigate")
    editor.append("tool_start", tool_name="browser_type")
    editor.append("tool_start", tool_name="live_browser_type")
    editor.append("tool_start", tool_name="browser_upload")
    editor.append("tool_start", tool_name="browser_click")
    editor.append("tool_start", tool_name="browser_wait")
    assert _trajectory_requirement("browser.rich-editor-upload", editor) is None

