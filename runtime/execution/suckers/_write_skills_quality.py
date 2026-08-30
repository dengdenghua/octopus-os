"""Code quality skills for write_skills · extracted from write_skills.py.

Contains the shared ``_run_quality_cmd`` runner plus the test / lint / format
skills and the path-normalization helper.
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path
from typing import Any

from ._write_skills_common import (
    _ensure_sandbox,
    _error_with_execution_policy,
    _execution_policy_from_result,
)

_QUALITY_TIMEOUT_S = 60.0
_QUALITY_OUTPUT_CAP = 200_000


def _run_quality_cmd(
    command: list[str],
    cwd: str | Path,
    *,
    timeout_s: float = _QUALITY_TIMEOUT_S,
    sandbox_dir: str | None = None,
) -> dict[str, Any]:
    """Shared runner for code quality tools."""
    if not cwd:
        return {"error": "missing cwd"}
    resolved, err = _ensure_sandbox(cwd, sandbox_dir)
    if err:
        return {"error": err}
    if not resolved.is_dir():
        return {"error": f"cwd not a directory: {resolved}"}

    from runtime.platform.process.streaming import stream_run

    r = stream_run(
        command,
        cwd=str(resolved),
        timeout=timeout_s,
        output_cap_bytes=_QUALITY_OUTPUT_CAP,
        sandbox_dir=sandbox_dir,
        sandbox_required=True,
    )
    if "error" in r and "exit_code" not in r:
        msg = r["error"]
        if "FileNotFoundError" in msg or "not found" in msg.lower():
            return _error_with_execution_policy(f"command not found: {command[0]}", r)
        return _error_with_execution_policy(f"exec failed: {msg}", r)
    if r.get("timed_out"):
        return {
            "error": f"timeout after {timeout_s}s",
            "timed_out": True,
            "execution_policy": _execution_policy_from_result(r),
        }
    return {
        "exit_code": r["exit_code"],
        "stdout": r["stdout"],
        "stderr": r["stderr"],
        "success": r["exit_code"] == 0,
        "sandbox_backend": r.get("sandbox_backend", "direct"),
        "sandbox_hard": bool(r.get("sandbox_hard")),
        "execution_policy": _execution_policy_from_result(r),
    }


def _run_tests(
    cwd: str = "",
    *,
    command: str = "",
    paths: str | list[str] | None = None,
    sandbox_dir: str | None = None,
    **_kw: Any,
) -> dict[str, Any]:
    """Run project tests. Auto-detects runner if command not specified."""
    if not cwd:
        return {"error": "missing cwd"}
    resolved, err = _ensure_sandbox(cwd, sandbox_dir)
    if err:
        return {"error": err}

    if command:
        argv = shlex.split(command)
    else:
        p = Path(str(resolved))
        if (p / "pytest.ini").exists() or (p / "pyproject.toml").exists():
            # A sandboxed check must not need to write pytest's cache outside
            # the selected project.  Disabling the cache also keeps fixture
            # evaluations free from unrelated ``.pytest_cache`` diffs.
            argv = [
                sys.executable,
                "-m",
                "pytest",
                "-p",
                "no:cacheprovider",
                "--tb=short",
                "-q",
            ]
        elif (p / "package.json").exists():
            argv = (
                ["npx", "vitest", "--run"]
                if (p / "vitest.config.ts").exists() or (p / "vitest.config.js").exists()
                else ["npm", "test"]
            )
        else:
            return {"error": "cannot auto-detect test runner; pass command explicitly"}

    normalized_paths, path_error = _normalize_quality_paths(paths, resolved)
    if path_error:
        return {"error": path_error}
    argv.extend(normalized_paths)

    return _run_quality_cmd(argv, resolved, sandbox_dir=sandbox_dir)


def _normalize_quality_paths(
    paths: str | list[str] | None,
    cwd: Path,
) -> tuple[list[str], str | None]:
    """Normalize model-supplied quality paths without widening project scope.

    Tool callers commonly send a single path as a JSON string even when the
    generated schema previously described an array.  Iterating that string
    character-by-character can accidentally pass ``/`` to Ruff and make it
    scan the entire filesystem, so strings must be treated as one path.
    """

    if paths is None:
        return [], None
    if isinstance(paths, str):
        # Preserve a real filename containing spaces. Otherwise accept the
        # common model/CLI shape ``"a.py tests/test_a.py"`` as two paths.
        literal = paths.strip()
        if literal and (cwd / literal).exists():
            candidates = [literal]
        else:
            try:
                candidates = shlex.split(literal)
            except ValueError as exc:
                return [], f"invalid paths: {exc}"
    else:
        candidates = paths
    if not isinstance(candidates, list):
        return [], "paths must be a string or list of strings"

    normalized: list[str] = []
    root = cwd.resolve()
    for raw_path in candidates:
        if not isinstance(raw_path, str) or not raw_path.strip():
            return [], "paths must contain non-empty strings"
        path = Path(raw_path)
        if raw_path.startswith("-") or path.is_absolute() or ".." in path.parts:
            return [], f"invalid path: {raw_path}"
        try:
            (root / path).resolve().relative_to(root)
        except ValueError:
            return [], f"path escapes cwd: {raw_path}"
        normalized.append(raw_path)
    return normalized, None


def _lint_check(
    cwd: str = "",
    *,
    command: str = "",
    fix: bool = False,
    paths: str | list[str] | None = None,
    sandbox_dir: str | None = None,
    **_kw: Any,
) -> dict[str, Any]:
    """Run linter. Auto-detects ruff/eslint if command not specified."""
    if not cwd:
        return {"error": "missing cwd"}
    resolved, err = _ensure_sandbox(cwd, sandbox_dir)
    if err:
        return {"error": err}

    if command:
        argv = shlex.split(command)
    else:
        p = Path(str(resolved))
        if (p / "ruff.toml").exists() or (p / "pyproject.toml").exists():
            # Ruff otherwise discovers/creates a cache relative to an outer
            # project root.  That path may intentionally be read-only in a
            # workspace sandbox, so linting should be cache-independent.
            argv = [
                sys.executable,
                "-m",
                "ruff",
                "check",
                "--no-cache",
                "--output-format=concise",
            ]
            # On a normal check, return Ruff's exact safe-fix diff so an
            # agent does not have to guess import grouping or formatting.
            # Callers may opt into applying Ruff's safe fixes explicitly.
            argv.append("--fix" if fix else "--diff")
        elif (
            (p / ".eslintrc.js").exists()
            or (p / ".eslintrc.json").exists()
            or (p / "eslint.config.js").exists()
        ):
            argv = ["npx", "eslint", "--format=compact"]
            if fix:
                argv.append("--fix")
        else:
            return {"error": "cannot auto-detect linter; pass command explicitly"}

    normalized_paths, path_error = _normalize_quality_paths(paths, resolved)
    if path_error:
        return {"error": path_error}
    argv.extend(normalized_paths)

    return _run_quality_cmd(argv, resolved, sandbox_dir=sandbox_dir)


def _format_code(
    cwd: str = "",
    *,
    command: str = "",
    check_only: bool = True,
    paths: str | list[str] | None = None,
    sandbox_dir: str | None = None,
    **_kw: Any,
) -> dict[str, Any]:
    """Run formatter. Auto-detects ruff format/prettier if command not specified."""
    if not cwd:
        return {"error": "missing cwd"}
    resolved, err = _ensure_sandbox(cwd, sandbox_dir)
    if err:
        return {"error": err}

    if command:
        argv = shlex.split(command)
    else:
        p = Path(str(resolved))
        if (p / "ruff.toml").exists() or (p / "pyproject.toml").exists():
            argv = [sys.executable, "-m", "ruff", "format", "--no-cache"]
            if check_only:
                argv.append("--check")
        elif (
            (p / ".prettierrc").exists()
            or (p / ".prettierrc.json").exists()
            or (p / "prettier.config.js").exists()
        ):
            argv = ["npx", "prettier"]
            argv.append("--check" if check_only else "--write")
            argv.append(".")
        else:
            return {"error": "cannot auto-detect formatter; pass command explicitly"}

    normalized_paths, path_error = _normalize_quality_paths(paths, resolved)
    if path_error:
        return {"error": path_error}
    argv.extend(normalized_paths)

    return _run_quality_cmd(argv, resolved, sandbox_dir=sandbox_dir)
