"""Tests for the SWE-bench adapter."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from benchmarks.swebench_adapter import (
    InstanceResult,
    SwebenchInstance,
    SwebenchPrediction,
    extract_patch,
    load_existing_predictions,
    write_predictions,
    write_summary,
)

# ── SwebenchInstance ────────────────────────────────────────


def test_instance_from_dataset_row_basic() -> None:
    row = {
        "instance_id": "django__django-12345",
        "repo": "django/django",
        "base_commit": "abc123def",
        "problem_statement": "URL resolver crashes on reserved keywords",
        "hints_text": "",
        "FAIL_TO_PASS": "[]",
        "PASS_TO_PASS": "[]",
        "test_patch": "",
        "version": "3.2",
    }
    instance = SwebenchInstance.from_dataset_row(row)
    assert instance.instance_id == "django__django-12345"
    assert instance.repo == "django/django"
    assert instance.base_commit == "abc123def"
    assert instance.problem_statement == "URL resolver crashes on reserved keywords"
    assert instance.fail_to_pass == []
    assert instance.pass_to_pass == []


def test_instance_from_dataset_row_with_list_fields() -> None:
    row = {
        "instance_id": "sympy__sympy-20590",
        "repo": "sympy/sympy",
        "base_commit": "def456",
        "problem_statement": "sympify raises wrong error",
        "FAIL_TO_PASS": ["test_a", "test_b"],
        "PASS_TO_PASS": ["test_c"],
    }
    instance = SwebenchInstance.from_dataset_row(row)
    assert instance.fail_to_pass == ["test_a", "test_b"]
    assert instance.pass_to_pass == ["test_c"]


def test_instance_repo_slug() -> None:
    instance = SwebenchInstance(
        instance_id="x",
        repo="owner/repo-name",
        base_commit="abc",
        problem_statement="",
    )
    assert instance.repo_slug == "owner__repo-name"


# ── SwebenchPrediction ──────────────────────────────────────


def test_prediction_to_jsonl() -> None:
    pred = SwebenchPrediction(
        instance_id="django__django-12345",
        model="echo-v0.2.0",
        prediction="diff --git a/foo.py b/foo.py\n+fixed",
    )
    line = pred.to_jsonl()
    parsed = json.loads(line)
    assert parsed["instance_id"] == "django__django-12345"
    assert parsed["model"] == "echo-v0.2.0"
    assert "diff --git" in parsed["prediction"]


def test_prediction_to_jsonl_unicode() -> None:
    pred = SwebenchPrediction(
        instance_id="x",
        model="m",
        prediction="# 修复: 中文注释\n+pass",
    )
    parsed = json.loads(pred.to_jsonl())
    assert "修复" in parsed["prediction"]


# ── write_predictions ───────────────────────────────────────


def test_write_predictions_skips_errors(tmp_path: Path) -> None:
    results = [
        InstanceResult(
            instance_id="ok-1",
            prediction=SwebenchPrediction("ok-1", "model", "diff --git a/x b/x\n+x"),
        ),
        InstanceResult(
            instance_id="fail-1",
            error="timeout",
        ),
        InstanceResult(
            instance_id="ok-2",
            prediction=SwebenchPrediction("ok-2", "model", "diff --git a/y b/y\n+y"),
        ),
    ]
    output = tmp_path / "predictions.jsonl"
    count = write_predictions(results, output, model_name="model")
    assert count == 2
    lines = output.read_text().strip().splitlines()
    assert len(lines) == 2
    ids = [json.loads(line)["instance_id"] for line in lines]
    assert "ok-1" in ids
    assert "ok-2" in ids


def test_write_predictions_creates_parent_dirs(tmp_path: Path) -> None:
    output = tmp_path / "nested" / "deep" / "predictions.jsonl"
    write_predictions([], output, model_name="m")
    assert output.exists()


# ── load_existing_predictions ───────────────────────────────


def test_load_existing_predictions_from_file(tmp_path: Path) -> None:
    path = tmp_path / "existing.jsonl"
    path.write_text(
        json.dumps({"instance_id": "a", "model": "m", "prediction": "x"})
        + "\n"
        + json.dumps({"instance_id": "b", "model": "m", "prediction": "y"})
        + "\n"
    )
    ids = load_existing_predictions(path)
    assert ids == {"a", "b"}


def test_load_existing_predictions_missing_file(tmp_path: Path) -> None:
    ids = load_existing_predictions(tmp_path / "nonexistent.jsonl")
    assert ids == set()


def test_load_existing_predictions_skips_bad_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text(
        json.dumps({"instance_id": "good", "prediction": "x"})
        + "\n"
        + "not json at all\n"
        + json.dumps({"instance_id": "also_good", "prediction": "y"})
        + "\n"
    )
    ids = load_existing_predictions(path)
    assert ids == {"good", "also_good"}


# ── write_summary ───────────────────────────────────────────


def test_write_summary_calculates_stats(tmp_path: Path) -> None:
    results = [
        InstanceResult(
            instance_id="ok-1",
            prediction=SwebenchPrediction("ok-1", "m", "line1\nline2"),
            duration_seconds=30.0,
            patch_lines=2,
        ),
        InstanceResult(
            instance_id="fail-1",
            error="timeout after 1800s",
            duration_seconds=1800.0,
        ),
    ]
    output = tmp_path / "predictions.jsonl"
    write_summary(results, output, dataset="test", model="m")

    summary_path = output.with_suffix(".summary.json")
    assert summary_path.exists()
    summary = json.loads(summary_path.read_text())
    assert summary["total_instances"] == 2
    assert summary["predictions_generated"] == 1
    assert summary["failed"] == 1
    assert summary["total_duration_seconds"] == 1830.0
    assert summary["total_patch_lines"] == 2
    assert "timeout after 1800s" in summary["errors_by_type"]


# ── extract_patch ───────────────────────────────────────────


def test_extract_patch_returns_unified_diff(tmp_path: Path) -> None:
    """Test that extract_patch produces a git diff from workspace changes."""
    workspace = tmp_path / "repo"
    workspace.mkdir()
    # Init git repo
    import subprocess

    subprocess.run(["git", "init", "--quiet", str(workspace)], check=True)
    subprocess.run(
        ["git", "-C", str(workspace), "config", "user.email", "test@test.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(workspace), "config", "user.name", "Test"],
        check=True,
    )
    # Create initial file and commit
    (workspace / "foo.py").write_text("def foo():\n    pass\n")
    subprocess.run(["git", "-C", str(workspace), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(workspace), "commit", "-m", "init", "--quiet"],
        check=True,
    )
    # Make a change
    (workspace / "foo.py").write_text("def foo():\n    return True\n")

    patch = extract_patch(workspace)
    assert "diff --git" in patch
    assert "return True" in patch
    assert "pass" in patch


def test_extract_patch_empty_when_no_changes(tmp_path: Path) -> None:
    import subprocess

    workspace = tmp_path / "repo"
    workspace.mkdir()
    subprocess.run(["git", "init", "--quiet", str(workspace)], check=True)
    subprocess.run(
        ["git", "-C", str(workspace), "config", "user.email", "t@t.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(workspace), "config", "user.name", "T"],
        check=True,
    )
    (workspace / "file.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(workspace), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(workspace), "commit", "-m", "init", "--quiet"],
        check=True,
    )

    patch = extract_patch(workspace)
    assert patch.strip() == ""


# ── process_instance (mocked) ───────────────────────────────


def test_process_instance_success(tmp_path: Path) -> None:
    """Test process_instance with mocked agent invocation."""
    from benchmarks import swebench_adapter

    instance = SwebenchInstance(
        instance_id="test__test-1",
        repo="test/test",
        base_commit="abc",
        problem_statement="Fix the bug",
    )

    def fake_prepare(instance, workspace_root, *, repos_cache=None):
        d = tmp_path / "ws"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def fake_run(instance, workspace, **kwargs):
        return {
            "patch": "diff --git a/x b/x\n+x",
            "success": True,
            "error": None,
            "event_count": 5,
        }

    with (
        patch.object(swebench_adapter, "prepare_workspace", fake_prepare),
        patch.object(swebench_adapter, "run_echo_agent", fake_run),
    ):
        result = swebench_adapter.process_instance(
            instance,
            tmp_path,
        )

    assert result.prediction is not None
    assert result.prediction.instance_id == "test__test-1"
    assert "diff --git" in result.prediction.prediction
    assert result.error is None
    assert result.event_count == 5


def test_process_instance_empty_patch_returns_error(tmp_path: Path) -> None:
    from benchmarks import swebench_adapter

    instance = SwebenchInstance(
        instance_id="test__test-2",
        repo="test/test",
        base_commit="abc",
        problem_statement="Fix the bug",
    )

    def fake_prepare(instance, workspace_root, *, repos_cache=None):
        d = tmp_path / "ws2"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def fake_run(instance, workspace, **kwargs):
        return {"patch": "", "success": False, "error": "agent failed", "event_count": 0}

    with (
        patch.object(swebench_adapter, "prepare_workspace", fake_prepare),
        patch.object(swebench_adapter, "run_echo_agent", fake_run),
    ):
        result = swebench_adapter.process_instance(instance, tmp_path)

    assert result.prediction is None
    assert result.error == "agent failed"


def test_process_instance_exception_caught(tmp_path: Path) -> None:
    from benchmarks import swebench_adapter

    instance = SwebenchInstance(
        instance_id="test__test-3",
        repo="test/test",
        base_commit="abc",
        problem_statement="Fix the bug",
    )

    def fake_prepare(instance, workspace_root, *, repos_cache=None):
        raise RuntimeError("git clone failed")

    with patch.object(swebench_adapter, "prepare_workspace", fake_prepare):
        result = swebench_adapter.process_instance(instance, tmp_path)

    assert result.prediction is None
    assert "git clone failed" in result.error
    assert "RuntimeError" in result.error

