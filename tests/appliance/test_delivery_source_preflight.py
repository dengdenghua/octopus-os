from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "appliance" / "delivery_source_preflight.py"
SPEC = importlib.util.spec_from_file_location("echo_delivery_source_preflight", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
preflight = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = preflight
SPEC.loader.exec_module(preflight)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repository(tmp_path: Path, *, branch: str = "os-main") -> tuple[Path, str]:
    root = tmp_path / "echo-os"
    root.mkdir()
    _git(root, "init", "-q", "-b", branch)
    _git(root, "config", "user.name", "Echo Test")
    _git(root, "config", "user.email", "echo@example.test")
    (root / "pyproject.toml").write_text('[project]\nname = "echo-os"\n')
    runtime = root / "runtime" / "__init__.py"
    runtime.parent.mkdir(parents=True)
    runtime.write_text('"""Embedded Echo Agent runtime."""\n')
    for relative in preflight.REQUIRED_WORKFLOWS:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("name: Echo test\n")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "release source")
    head = _git(root, "rev-parse", "HEAD")
    _git(root, "remote", "add", "origin", "https://github.com/example/echo-os.git")
    _git(root, "update-ref", "refs/remotes/origin/os-main", head)
    _git(root, "config", f"branch.{branch}.remote", "origin")
    _git(root, "config", f"branch.{branch}.merge", "refs/heads/os-main")
    return root, head


def _online_github(head: str):
    def run(argv: list[str], *, cwd: Path, timeout: int) -> tuple[bool, str]:
        del cwd, timeout
        if argv == ["auth", "status", "--hostname", "github.com"]:
            return True, ""
        if argv[:2] == ["api", "repos/example/echo-os/git/ref/heads/os-main"]:
            return True, head
        raise AssertionError(f"unexpected GitHub command: {argv}")

    return run


def _check(report: dict[str, object], code: str) -> dict[str, str]:
    checks = report["checks"]
    assert isinstance(checks, list)
    return next(check for check in checks if check["code"] == code)


def test_online_preflight_proves_one_clean_pushed_echo_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, head = _repository(tmp_path)
    monkeypatch.setattr(preflight, "_run_github", _online_github(head))

    report = preflight.inspect_delivery_source(root)

    assert set(report) == {
        "schemaVersion",
        "kind",
        "mode",
        "ready",
        "expectedBranch",
        "branch",
        "sourceRevision",
        "osRepository",
        "agentSource",
        "requiredWorkflows",
        "checks",
        "blockers",
    }
    assert all(set(check) == {"code", "status", "detail"} for check in report["checks"])
    assert report["ready"] is True
    assert report["mode"] == "online"
    assert report["branch"] == "os-main"
    assert report["sourceRevision"] == head
    assert report["osRepository"] == "example/echo-os"
    assert report["agentSource"] == {
        "repository": "example/echo-os",
        "commit": head,
    }
    assert report["blockers"] == []
    assert all(check["status"] == "passed" for check in report["checks"])


def test_offline_preflight_never_claims_release_readiness(tmp_path: Path) -> None:
    root, _head = _repository(tmp_path)

    report = preflight.inspect_delivery_source(root, offline=True)

    assert report["ready"] is False
    assert report["blockers"] == ["online_verification_required"]
    assert _check(report, "github_auth")["status"] == "skipped"
    assert _check(report, "online_os_remote")["status"] == "skipped"
    assert _check(report, "online_embedded_agent")["status"] == "skipped"


def test_dirty_or_untracked_delivery_source_fails_closed(tmp_path: Path) -> None:
    root, _head = _repository(tmp_path)
    untracked_workflow = preflight.REQUIRED_WORKFLOWS[1]
    _git(root, "rm", "--cached", untracked_workflow)
    (root / "local-secret-name.txt").write_text("not reported\n")

    report = preflight.inspect_delivery_source(root, offline=True)

    assert report["ready"] is False
    assert _check(report, "worktree_clean")["status"] == "failed"
    assert "3 changed or untracked entries" in _check(report, "worktree_clean")["detail"]
    assert _check(report, "required_workflows_tracked")["status"] == "failed"
    serialized = json.dumps(report)
    assert "local-secret-name.txt" not in serialized


def test_branch_and_cached_remote_must_match_exactly(tmp_path: Path) -> None:
    root, _head = _repository(tmp_path, branch="feature")

    report = preflight.inspect_delivery_source(root, offline=True)

    assert _check(report, "delivery_branch")["status"] == "failed"
    assert report["branch"] == "feature"

    (root / "pyproject.toml").write_text('[project]\nname = "changed"\n')
    _git(root, "add", "pyproject.toml")
    _git(root, "commit", "-qm", "local only")
    report = preflight.inspect_delivery_source(
        root,
        expected_branch="feature",
        offline=True,
    )
    assert _check(report, "cached_os_remote")["status"] == "failed"


def test_online_checks_fail_when_auth_or_live_ref_is_not_proven(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, head = _repository(tmp_path)

    def unavailable(argv: list[str], *, cwd: Path, timeout: int) -> tuple[bool, str]:
        del argv, cwd, timeout
        return False, ""

    monkeypatch.setattr(preflight, "_run_github", unavailable)
    report = preflight.inspect_delivery_source(root)
    assert _check(report, "github_auth")["status"] == "failed"
    assert _check(report, "online_os_remote")["status"] == "failed"
    assert _check(report, "online_embedded_agent")["status"] == "failed"

    def wrong_os_ref(argv: list[str], *, cwd: Path, timeout: int) -> tuple[bool, str]:
        del cwd, timeout
        if argv[0] == "auth":
            return True, ""
        if "echo-os" in argv[1]:
            return True, "f" * 40
        raise AssertionError(f"unexpected GitHub command: {argv}")

    monkeypatch.setattr(preflight, "_run_github", wrong_os_ref)
    report = preflight.inspect_delivery_source(root)
    assert report["sourceRevision"] == head
    assert _check(report, "online_os_remote")["status"] == "failed"
    assert _check(report, "online_embedded_agent")["status"] == "failed"


def test_embedded_runtime_is_required_by_the_source_contract(tmp_path: Path) -> None:
    root, _head = _repository(tmp_path)
    (root / "runtime" / "__init__.py").unlink()

    report = preflight.inspect_delivery_source(root, offline=True)

    assert report["ready"] is False
    assert _check(report, "repository_layout")["status"] == "failed"


def test_cli_emits_machine_readable_fail_closed_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, _head = _repository(tmp_path)

    exit_code = preflight.main(["--repository-root", str(root), "--offline", "--compact"])

    report = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert report["kind"] == "echo.delivery-source-preflight"
    assert report["ready"] is False
    assert report["blockers"] == ["online_verification_required"]


def test_cli_returns_zero_only_after_online_source_identity_is_proven(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, head = _repository(tmp_path)
    monkeypatch.setattr(preflight, "_run_github", _online_github(head))

    exit_code = preflight.main(["--repository-root", str(root), "--compact"])

    report = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert report["ready"] is True
    assert report["blockers"] == []


def test_cli_rejects_unreviewed_branch_names_before_running_checks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, _head = _repository(tmp_path)

    exit_code = preflight.main(
        ["--repository-root", str(root), "--expected-branch", "feature/unsafe"]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert "expected branch is invalid" in captured.err
