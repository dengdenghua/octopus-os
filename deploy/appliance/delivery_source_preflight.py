#!/usr/bin/env python3
"""Fail-closed release-source readiness check for Echo OS delivery workflows."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess  # nosec B404
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

# Commands below use fixed argv arrays and never invoke a command shell.

SCHEMA_VERSION = 1
MAX_COMMAND_OUTPUT = 1024 * 1024
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
BRANCH_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
GITHUB_COMPONENT_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
REQUIRED_WORKFLOWS = (
    ".github/workflows/ci.yml",
    ".github/workflows/os-image.yml",
    ".github/workflows/ab-update-smoke.yml",
    ".github/workflows/desktop-session-smoke.yml",
    ".github/workflows/omv-real-x86.yml",
    ".github/workflows/appliance-release.yml",
    ".github/workflows/delivery-release-candidate.yml",
)
PREFLIGHT_CHECK_CODES = (
    "repository_layout",
    "git_repository",
    "delivery_branch",
    "source_revision",
    "worktree_clean",
    "required_workflows_tracked",
    "tracking_ref",
    "cached_os_remote",
    "os_origin_identity",
    "embedded_agent_source",
    "github_auth",
    "online_os_remote",
    "online_embedded_agent",
)


def _run_command(
    argv: list[str],
    *,
    cwd: Path,
    timeout: int,
    environment_overrides: dict[str, str] | None = None,
) -> tuple[bool, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GH_PROMPT_DISABLED": "1",
        }
    )
    if environment_overrides is not None:
        environment.update(environment_overrides)
    try:
        # Every dynamic argv field is constrained before it reaches this boundary.
        completed = subprocess.run(  # nosec B603
            argv,
            cwd=cwd,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, ""
    output = completed.stdout
    if completed.returncode != 0 or len(output.encode("utf-8", "replace")) > MAX_COMMAND_OUTPUT:
        return False, ""
    return True, output.strip()


def _run_github(argv: list[str], *, cwd: Path, timeout: int) -> tuple[bool, str]:
    return _run_command(["gh", *argv], cwd=cwd, timeout=timeout)


def _git(root: Path, *args: str, timeout: int) -> tuple[bool, str]:
    return _run_command(["git", *args], cwd=root, timeout=timeout)


def _github_repository(value: str, *, expected_host: str) -> str | None:
    repository: str
    if value.startswith("git@"):
        prefix = f"git@{expected_host}:"
        if not value.startswith(prefix):
            return None
        repository = value[len(prefix) :]
    else:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname != expected_host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
            or parsed.query
            or parsed.fragment
        ):
            return None
        repository = parsed.path.lstrip("/")
    if repository.endswith(".git"):
        repository = repository[:-4]
    fields = repository.split("/")
    if (
        len(fields) != 2
        or any(GITHUB_COMPONENT_PATTERN.fullmatch(field) is None for field in fields)
        or any(field in {".", ".."} for field in fields)
    ):
        return None
    return repository


def inspect_delivery_source(
    repository_root: Path,
    *,
    expected_branch: str = "os-main",
    remote: str = "origin",
    github_host: str = "github.com",
    offline: bool = False,
    timeout: int = 20,
) -> dict[str, Any]:
    if BRANCH_PATTERN.fullmatch(expected_branch) is None:
        raise ValueError("expected branch is invalid")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", remote):
        raise ValueError("remote name is invalid")
    if github_host != "github.com":
        raise ValueError("only the reviewed github.com delivery host is supported")
    if not 1 <= timeout <= 120:
        raise ValueError("timeout must be between 1 and 120 seconds")

    root = repository_root.expanduser().resolve()
    checks: list[dict[str, str]] = []

    def record(code: str, passed: bool, detail: str) -> None:
        checks.append(
            {
                "code": code,
                "status": "passed" if passed else "failed",
                "detail": detail,
            }
        )

    required_paths = ("pyproject.toml", "runtime/__init__.py", *REQUIRED_WORKFLOWS)
    layout_ok = all(
        (root / relative).is_file() and not (root / relative).is_symlink()
        for relative in required_paths
    )
    record(
        "repository_layout",
        layout_ok,
        "Required release-source files are regular files"
        if layout_ok
        else "Required release-source files are missing or unsafe",
    )

    top_ok, top = _git(root, "rev-parse", "--show-toplevel", timeout=timeout)
    git_repository_ok = top_ok and Path(top).resolve() == root
    record(
        "git_repository",
        git_repository_ok,
        "Repository root matches Git metadata"
        if git_repository_ok
        else "Path is not the exact Git repository root",
    )

    branch = ""
    head = ""
    os_repository: str | None = None
    agent_source: dict[str, str] | None = None
    if git_repository_ok:
        branch_ok, branch = _git(root, "branch", "--show-current", timeout=timeout)
        delivery_branch_ok = branch_ok and branch == expected_branch
        record(
            "delivery_branch",
            delivery_branch_ok,
            f"Current branch is {expected_branch}"
            if delivery_branch_ok
            else "Current branch is not the reviewed delivery branch",
        )

        head_ok, head = _git(root, "rev-parse", "HEAD", timeout=timeout)
        source_revision_ok = head_ok and SHA_PATTERN.fullmatch(head) is not None
        record(
            "source_revision",
            source_revision_ok,
            "OS source revision is one full Git commit"
            if source_revision_ok
            else "OS source revision is unavailable or malformed",
        )

        status_ok, status = _git(
            root,
            "status",
            "--porcelain=v1",
            "--untracked-files=normal",
            timeout=timeout,
        )
        changed_count = len(status.splitlines()) if status_ok and status else 0
        worktree_ok = status_ok and changed_count == 0
        if not status_ok:
            worktree_detail = "Working tree status could not be read within the safety bound"
        elif worktree_ok:
            worktree_detail = "Working tree is clean"
        else:
            worktree_detail = f"Working tree has {changed_count} changed or untracked entries"
        record(
            "worktree_clean",
            worktree_ok,
            worktree_detail,
        )

        tracked_ok, tracked = _git(
            root,
            "ls-files",
            "--stage",
            "--",
            *REQUIRED_WORKFLOWS,
            timeout=timeout,
        )
        tracked_workflows: set[str] = set()
        tracked_modes: dict[str, str] = {}
        if tracked_ok:
            for line in tracked.splitlines():
                try:
                    metadata, path = line.split("\t", 1)
                    mode, object_id, stage = metadata.split()
                except ValueError:
                    tracked_workflows.clear()
                    break
                if SHA_PATTERN.fullmatch(object_id) is None or stage != "0":
                    tracked_workflows.clear()
                    break
                tracked_workflows.add(path)
                tracked_modes[path] = mode
        workflows_ok = (
            tracked_ok
            and tracked_workflows == set(REQUIRED_WORKFLOWS)
            and all(tracked_modes.get(path) == "100644" for path in REQUIRED_WORKFLOWS)
        )
        record(
            "required_workflows_tracked",
            workflows_ok,
            "All required delivery workflows are tracked as regular source files"
            if workflows_ok
            else "One or more required delivery workflows are untracked or unsafe",
        )

        upstream_ok, upstream = _git(
            root,
            "rev-parse",
            "--abbrev-ref",
            "--symbolic-full-name",
            "@{upstream}",
            timeout=timeout,
        )
        expected_upstream = f"{remote}/{expected_branch}"
        tracking_ok = upstream_ok and upstream == expected_upstream
        record(
            "tracking_ref",
            tracking_ok,
            f"Delivery branch tracks {expected_upstream}"
            if tracking_ok
            else "Delivery branch does not track the expected remote branch",
        )

        cached_ok, cached_commit = _git(
            root,
            "rev-parse",
            f"refs/remotes/{remote}/{expected_branch}",
            timeout=timeout,
        )
        cached_match = (
            cached_ok
            and SHA_PATTERN.fullmatch(cached_commit) is not None
            and bool(head)
            and cached_commit == head
        )
        record(
            "cached_os_remote",
            cached_match,
            "OS HEAD matches the cached remote delivery ref"
            if cached_match
            else "OS HEAD does not match the cached remote delivery ref",
        )

        origin_ok, origin_url = _git(root, "remote", "get-url", remote, timeout=timeout)
        os_repository = (
            _github_repository(origin_url, expected_host=github_host) if origin_ok else None
        )
        record(
            "os_origin_identity",
            os_repository is not None,
            "OS origin is a credential-free GitHub repository"
            if os_repository is not None
            else "OS origin is missing, credential-bearing, or outside github.com",
        )

    agent_source = (
        {"repository": os_repository, "commit": head}
        if os_repository is not None and SHA_PATTERN.fullmatch(head or "") is not None
        else None
    )
    record(
        "embedded_agent_source",
        agent_source is not None,
        "Agent runtime is embedded in the same immutable Echo OS revision"
        if agent_source is not None
        else "Unified Echo source identity is unavailable",
    )

    if offline:
        for code, detail in (
            ("github_auth", "GitHub authentication was not checked in offline mode"),
            ("online_os_remote", "OS remote identity was not checked in offline mode"),
            (
                "online_embedded_agent",
                "Embedded Agent reachability was not checked in offline mode",
            ),
        ):
            checks.append({"code": code, "status": "skipped", "detail": detail})
    else:
        auth_ok, _ = _run_github(
            ["auth", "status", "--hostname", github_host],
            cwd=root,
            timeout=timeout,
        )
        record(
            "github_auth",
            auth_ok,
            "GitHub CLI authentication is usable"
            if auth_ok
            else "GitHub CLI authentication is unavailable or invalid",
        )

        os_remote_ok = False
        if auth_ok and os_repository is not None and head:
            ref_ok, remote_head = _run_github(
                [
                    "api",
                    f"repos/{os_repository}/git/ref/heads/{expected_branch}",
                    "--jq",
                    ".object.sha",
                ],
                cwd=root,
                timeout=timeout,
            )
            os_remote_ok = ref_ok and remote_head == head
        record(
            "online_os_remote",
            os_remote_ok,
            "OS HEAD exactly matches the live GitHub delivery branch"
            if os_remote_ok
            else "OS HEAD is not proven at the live GitHub delivery branch",
        )

        agent_remote_ok = os_remote_ok and agent_source is not None
        record(
            "online_embedded_agent",
            agent_remote_ok,
            "Embedded Agent runtime matches the live Echo OS revision"
            if agent_remote_ok
            else "Embedded Agent runtime is not proven at the live Echo OS revision",
        )

    emitted_codes = tuple(check["code"] for check in checks)
    if emitted_codes != PREFLIGHT_CHECK_CODES:
        raise RuntimeError("delivery source preflight check contract drifted")
    failed = [check["code"] for check in checks if check["status"] == "failed"]
    blockers = list(failed)
    if offline:
        blockers.append("online_verification_required")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "echo.delivery-source-preflight",
        "mode": "offline" if offline else "online",
        "ready": not blockers,
        "expectedBranch": expected_branch,
        "branch": branch or None,
        "sourceRevision": head or None,
        "osRepository": os_repository,
        "agentSource": agent_source,
        "requiredWorkflows": list(REQUIRED_WORKFLOWS),
        "checks": checks,
        "blockers": blockers,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument("--expected-branch", default="os-main")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--compact", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = inspect_delivery_source(
            args.repository_root,
            expected_branch=args.expected_branch,
            remote=args.remote,
            offline=args.offline,
            timeout=args.timeout,
        )
    except ValueError as exc:
        print(f"Echo delivery source preflight failed: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=None if args.compact else 2,
            sort_keys=True,
        )
    )
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
