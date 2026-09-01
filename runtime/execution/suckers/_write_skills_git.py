"""Git core skills for write_skills · extracted from write_skills.py.

Contains the shared ``_run_git`` runner plus the read-only / local write git
skills (status / diff / log / add / commit / branch).
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from ._write_skills_common import (
    _ensure_sandbox,
    _error_with_execution_policy,
    _execution_policy_from_result,
)

_GIT_READ_TIMEOUT_S = 15.0
_GIT_WRITE_TIMEOUT_S = 30.0
_GIT_OUTPUT_CAP = 200_000

# A hook that shells out to ``pnpm exec`` / ``pnpm install`` is the classic
# no-TTY abort: the sandbox corepack pnpm wants to purge a node_modules a
# different pnpm major installed, and with no TTY it cannot ask for the
# confirmation — ``ERR_PNPM_ABORTED_REMOVE_MODULES_DIR_NO_TTY``.
_GIT_HOOK_PNPM_RISK_RE = re.compile(r"\bpnpm\s+(exec|install|add|rebuild|dlx)\b")


def _node_bin_dirs() -> list[str]:
    """Locate a usable ``node`` binary's bin dir for git hook subprocesses.

    A repo's husky/commitlint hooks invoke ``node`` from PATH; when the
    server process was launched without a node dir on PATH (common on
    macOS, where node lives under ``~/.local/node/bin`` or ``~/node``),
    hooks die with ``node: not found`` and every ``git commit`` fails.
    This returns candidate dirs derived from ``which node`` plus the
    conventional install locations, so hooks resolve ``node`` even when
    the server PATH omits it.
    """
    seen: list[str] = []
    candidates: list[str] = []
    which_node = shutil.which("node")
    if which_node:
        candidates.append(str(Path(which_node).resolve().parent))
    home = Path.home()
    for rel in (
        ".local/node/bin",
        ".node/bin",
        "node/bin",
        ".local/bin",
        ".volta/bin",
        ".nvm/versions/node/current/bin",
    ):
        candidates.append(str(home / rel))
    for cand in candidates:
        if (Path(cand) / "node").is_file() and cand not in seen:
            seen.append(cand)
    return seen


def _git_env_with_node(base_env: dict[str, str] | None = None) -> dict[str, str]:
    """Return ``base_env`` (or os.environ) with node bin dirs on PATH."""
    src = dict(base_env) if base_env is not None else dict(os.environ)
    existing_path = src.get("PATH") or ""
    extra = [d for d in _node_bin_dirs() if d and d not in existing_path.split(os.pathsep)]
    if not extra:
        return src
    src["PATH"] = (
        os.pathsep.join([*extra, existing_path]) if existing_path else os.pathsep.join(extra)
    )
    return src


def _run_git(
    repo_dir: str | Path,
    argv: list[str],
    *,
    timeout_s: float,
    sandbox_dir: str | None = None,
    allow_network: bool = False,
) -> dict[str, Any]:
    if not repo_dir:
        return {"error": "missing repo_dir"}
    resolved, err = _ensure_sandbox(repo_dir, sandbox_dir)
    if err:
        return {"error": err}
    if not resolved.is_dir():
        return {"error": f"repo_dir not a directory: {resolved}"}

    from runtime.platform.process.streaming import stream_run

    full_argv = ["git", "-C", str(resolved), *argv]
    r = stream_run(
        full_argv,
        timeout=timeout_s,
        output_cap_bytes=_GIT_OUTPUT_CAP,
        env=_git_env_with_node(),
        sandbox_dir=sandbox_dir,
        allow_network=allow_network,
        sandbox_required=True,
    )
    if "error" in r and "exit_code" not in r:
        msg = r["error"]
        if "FileNotFoundError" in msg or "No such file" in msg or "not found" in msg.lower():
            return _error_with_execution_policy("git_not_found_on_path", r)
        return _error_with_execution_policy(f"git_exec_failed: {msg}", r)
    if r.get("timed_out"):
        return {
            "error": f"git timeout after {timeout_s}s",
            "timed_out": True,
            "execution_policy": _execution_policy_from_result(r),
        }
    return {
        "exit_code": r["exit_code"],
        "stdout": r["stdout"],
        "stderr": r["stderr"],
        "stdout_truncated": r["stdout_truncated"],
        "resolved_repo": str(resolved),
        "sandbox_backend": r.get("sandbox_backend", "direct"),
        "sandbox_hard": bool(r.get("sandbox_hard")),
        "execution_policy": _execution_policy_from_result(r),
    }


def _git_status(
    repo_dir: str = "",
    *,
    sandbox_dir: str | None = None,
    **_kw: Any,
) -> dict[str, Any]:
    r = _run_git(
        repo_dir,
        ["status", "--porcelain=v1", "--branch"],
        timeout_s=_GIT_READ_TIMEOUT_S,
        sandbox_dir=sandbox_dir,
    )
    if "error" in r:
        return r
    if r["exit_code"] != 0:
        return {"error": "git_status_failed", **r}

    branch = ""
    files: list[dict[str, str]] = []
    for line in r["stdout"].splitlines():
        if line.startswith("## "):
            branch = line[3:].split("...")[0]
            continue
        if len(line) < 3:
            continue
        code = line[:2]
        path = line[3:]
        files.append({"status": code.strip() or code, "path": path})
    return {
        "branch": branch,
        "files": files,
        "clean": not files,
    }


def _git_diff(
    repo_dir: str = "",
    *,
    path: str | None = None,
    staged: bool = False,
    sandbox_dir: str | None = None,
    **_kw: Any,
) -> dict[str, Any]:
    argv = ["diff"]
    if staged:
        argv.append("--staged")
    if path:
        if path.startswith("-"):
            return {"error": "invalid path (leading '-')"}
        argv.extend(["--", path])
    r = _run_git(
        repo_dir,
        argv,
        timeout_s=_GIT_READ_TIMEOUT_S,
        sandbox_dir=sandbox_dir,
    )
    if "error" in r:
        return r
    if r["exit_code"] != 0:
        return {"error": "git_diff_failed", **r}
    return {
        "diff": r["stdout"],
        "truncated": r["stdout_truncated"],
        "staged": staged,
    }


def _git_log(
    repo_dir: str = "",
    *,
    limit: int = 10,
    path: str | None = None,
    sandbox_dir: str | None = None,
    **_kw: Any,
) -> dict[str, Any]:
    if limit <= 0 or limit > 500:
        return {"error": f"limit out of range: {limit}"}
    fmt = "%H%x1f%an%x1f%aI%x1f%s"
    argv = ["log", f"-n{limit}", f"--pretty=format:{fmt}"]
    if path:
        if path.startswith("-"):
            return {"error": "invalid path (leading '-')"}
        argv.extend(["--", path])
    r = _run_git(
        repo_dir,
        argv,
        timeout_s=_GIT_READ_TIMEOUT_S,
        sandbox_dir=sandbox_dir,
    )
    if "error" in r:
        return r
    if r["exit_code"] != 0:
        return {"error": "git_log_failed", **r}

    commits: list[dict[str, str]] = []
    for line in r["stdout"].splitlines():
        parts = line.split("\x1f")
        if len(parts) != 4:
            continue
        sha, author, date, subject = parts
        commits.append(
            {
                "sha": sha,
                "author": author,
                "date": date,
                "subject": subject,
            }
        )
    return {"commits": commits}


def _git_add(
    repo_dir: str = "",
    paths: list[str] | None = None,
    *,
    sandbox_dir: str | None = None,
    **_kw: Any,
) -> dict[str, Any]:
    if not paths:
        return {"error": "paths must be a non-empty list"}
    if not isinstance(paths, list):
        return {"error": f"paths must be list (got {type(paths).__name__})"}
    safe_paths: list[str] = []
    for p in paths:
        if not isinstance(p, str) or not p:
            return {"error": "each path must be a non-empty string"}
        if p.startswith("-"):
            return {"error": f"flag-like path rejected: {p}"}
        if p in (".", "*") or ".." in Path(p).parts:
            return {"error": f"overly broad or traversal path: {p}"}
        safe_paths.append(p)

    r = _run_git(
        repo_dir,
        ["add", "--", *safe_paths],
        timeout_s=_GIT_WRITE_TIMEOUT_S,
        sandbox_dir=sandbox_dir,
    )
    if "error" in r:
        return r
    if r["exit_code"] != 0:
        return {"error": "git_add_failed", **r}
    return {"added": safe_paths, "exit_code": 0}


def _pnpm_major(pm: str) -> str | None:
    """Extract the major version from a ``pnpm@X.Y.Z`` packageManager spec."""
    m = re.search(r"pnpm@(\d+)", pm)
    return m.group(1) if m else None


def _package_manager_drift(repo_root: Path) -> dict[str, Any] | None:
    """Detect a pnpm major mismatch between package.json and node_modules.

    The classic failure is the sandbox corepack pnpm (a newer major) refusing
    to operate on a node_modules installed by the repo's pinned pnpm:
    ``ERR_PNPM_ABORTED_REMOVE_MODULES_DIR_NO_TTY``. ``node_modules/.modules.yaml``
    records the pnpm that installed the tree; ``package.json`` pins the
    intended one. A major mismatch is the drift that makes any ``pnpm`` call
    inside a git hook abort in a no-TTY sandbox.
    """
    package_json = repo_root / "package.json"
    modules_yaml = repo_root / "node_modules" / ".modules.yaml"
    if not package_json.is_file() or not modules_yaml.is_file():
        return None
    try:
        package_manager = json.loads(package_json.read_text(encoding="utf-8")).get(
            "packageManager", ""
        )
    except (OSError, ValueError):
        return None
    if not isinstance(package_manager, str) or "pnpm@" not in package_manager:
        return None
    installed = ""
    try:
        for line in modules_yaml.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if stripped.startswith("packageManager:"):
                installed = stripped.split(":", 1)[1].strip()
                break
    except OSError:
        return None
    if not installed or "pnpm@" not in installed:
        return None
    pinned_major = _pnpm_major(package_manager)
    installed_major = _pnpm_major(installed)
    if pinned_major and installed_major and pinned_major != installed_major:
        return {
            "detail": (
                f"package.json 固定 pnpm {pinned_major}.x，但 node_modules 由 "
                f"pnpm {installed_major}.x 安装——major 不一致时 pnpm 会要求交互清理"
                "目录并在无 TTY 环境直接中止"
            ),
            "pinned": package_manager,
            "installed": installed,
        }
    return None


def _git_commit_precheck(
    repo_dir: str | Path,
    sandbox_dir: str | None,
) -> dict[str, Any]:
    """Inspect the repo for conditions known to abort a commit in a no-TTY
    agent sandbox.

    Checks the repo's git hooks (``.husky/*``) for ``pnpm exec`` /
    ``pnpm install`` calls, and for pnpm major drift between
    ``package.json`` and the installed ``node_modules``. Returns
    ``{"blocked": bool, "risks": [...], "readable": str}``; ``_git_commit``
    uses it to enrich a failed commit with a human reason instead of the raw
    pnpm stderr the user cannot decode.
    """
    resolved, err = _ensure_sandbox(repo_dir, sandbox_dir)
    if err or not resolved.is_dir():
        return {"blocked": False, "risks": [], "readable": ""}

    risks: list[dict[str, Any]] = []
    hooks_dir = resolved / ".husky"
    if hooks_dir.is_dir():
        for hook in sorted(hooks_dir.glob("*")):
            if not hook.is_file() or hook.name.startswith("."):
                continue
            try:
                text = hook.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if _GIT_HOOK_PNPM_RISK_RE.search(text):
                risks.append(
                    {
                        "hook": hook.name,
                        "risk": "pnpm_exec_in_hook",
                        "detail": (
                            f".husky/{hook.name} 直接调用 pnpm exec/install，在无 TTY "
                            "沙箱可能触发 node_modules 清理确认而中止"
                        ),
                    }
                )

    drift = _package_manager_drift(resolved)
    if drift is not None:
        risks.append({"risk": "package_manager_drift", **drift})

    blocked = bool(risks)
    readable = ""
    if blocked:
        readable = (
            "提交前预检发现 git 钩子可能被环境阻塞："
            + "；".join(risk["detail"] for risk in risks)
            + "。若提交失败，可让钩子直接调用 node_modules/.bin 下的可执行文件、"
            "在钩子或命令环境里 export CI=true，或设置 .npmrc confirmModulesPurge=false。"
        )
    return {"blocked": blocked, "risks": risks, "readable": readable}


def _git_commit(
    repo_dir: str = "",
    message: str = "",
    *,
    author: str | None = None,
    sandbox_dir: str | None = None,
    **_kw: Any,
) -> dict[str, Any]:
    if not message.strip():
        return {"error": "commit message must be non-empty"}
    if len(message.encode("utf-8")) > 10_000:
        return {"error": "commit message too large"}

    argv = ["commit", "-m", message]
    if author:
        if "<" not in author or ">" not in author:
            return {"error": "author must be 'Name <email>' format"}
        argv.extend(["--author", author])

    precheck = _git_commit_precheck(repo_dir, sandbox_dir)

    r = _run_git(
        repo_dir,
        argv,
        timeout_s=_GIT_WRITE_TIMEOUT_S,
        sandbox_dir=sandbox_dir,
    )
    if "error" in r:
        return r
    if r["exit_code"] != 0:
        result = {"error": "git_commit_failed", "precheck": precheck, **r}
        # Enrich the cryptic failure with the precheck's human reason when a
        # hook / package-manager risk was present — the commit died for the
        # reason we can already name.
        if precheck["blocked"]:
            result["error"] = "git_commit_precheck_blocked"
            result["readable"] = precheck["readable"]
        return result

    head = _run_git(
        repo_dir,
        ["rev-parse", "HEAD"],
        timeout_s=_GIT_READ_TIMEOUT_S,
        sandbox_dir=sandbox_dir,
    )
    sha = head.get("stdout", "").strip() if "error" not in head else ""
    result = {"sha": sha, "stdout": r["stdout"], "stderr": r["stderr"]}
    if precheck["blocked"]:
        # Commit went through despite the risk (e.g. CI=true already set) —
        # surface the flag so the agent knows the hook may abort next time.
        result["precheck"] = precheck
    return result


def _git_branch(
    repo_dir: str = "",
    *,
    create: str | None = None,
    from_ref: str | None = None,
    sandbox_dir: str | None = None,
    **_kw: Any,
) -> dict[str, Any]:
    if create:
        if create.startswith("-") or " " in create:
            return {"error": f"invalid branch name: {create!r}"}
        argv = ["branch", create]
        if from_ref:
            if from_ref.startswith("-"):
                return {"error": f"invalid ref: {from_ref!r}"}
            argv.append(from_ref)
        r = _run_git(
            repo_dir,
            argv,
            timeout_s=_GIT_WRITE_TIMEOUT_S,
            sandbox_dir=sandbox_dir,
        )
        if "error" in r:
            return r
        if r["exit_code"] != 0:
            return {"error": "git_branch_create_failed", **r}
        return {"created": create, "from_ref": from_ref}

    r = _run_git(
        repo_dir,
        ["branch", "--list"],
        timeout_s=_GIT_READ_TIMEOUT_S,
        sandbox_dir=sandbox_dir,
    )
    if "error" in r:
        return r
    if r["exit_code"] != 0:
        return {"error": "git_branch_list_failed", **r}
    branches: list[dict[str, Any]] = []
    for line in r["stdout"].splitlines():
        line = line.rstrip()
        if not line:
            continue
        current = line.startswith("*")
        name = line[2:] if len(line) > 2 else line
        branches.append({"name": name.strip(), "current": current})
    return {"branches": branches}
