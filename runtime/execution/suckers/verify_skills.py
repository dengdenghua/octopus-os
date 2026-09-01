"""Project verification · detect project type and run checks.

Scans a workspace root for marker files (package.json, pyproject.toml,
Cargo.toml, go.mod, etc.) and returns the appropriate verification
commands. The runner executes them sequentially and returns structured
results so the frontend can show pass/fail per check.

Security: every ``check`` carries an ``argv`` (list of strings) that is
executed with ``shell=False``. The legacy ``cmd`` string field is still
read for backwards compatibility but is logged as a warning — new
entries must supply ``argv``.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)


# Shared by every diagnostics path that injects check output into the
# model's context (react_execution._run_auto_diagnostics and
# react_context._collect_initial_diagnostics). A missing checker binary
# is an environment gap, not a code failure — surfacing it as one sends
# the model chasing phantom errors.
_DEPENDENCY_MISSING_MARKERS = (
    "no module named",
    "modulenotfounderror",
    "cannot find module",
    "module not found",
)

_TOOL_MISSING_MARKERS = (
    "command not found",
    "not recognized as an internal or external command",
    "could not determine executable to run",
    "npx: not found",
    "[winerror 2]",
    "executable not found",
    # A bare "enoent" is intentionally excluded — it also appears in
    # legitimate "ENOENT: no such file or directory" compile/type
    # errors, which are REAL failures.
)


def classify_environment_gap(output: str) -> str:
    lowered = (output or "").lower()
    if any(marker in lowered for marker in _DEPENDENCY_MISSING_MARKERS):
        return "environment_missing_dependency"
    if any(marker in lowered for marker in _TOOL_MISSING_MARKERS):
        return "environment_missing_tool"
    return ""


def output_indicates_missing_tool(output: str) -> bool:
    return bool(classify_environment_gap(output))


def _legacy_shell_argv(command: str) -> list[str]:
    if sys.platform == "win32":
        return [os.environ.get("COMSPEC") or "cmd.exe", "/C", command]
    return [shutil.which("sh") or "/bin/sh", "-c", command]


def _coerce_output_cap(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _truncate_utf8_bytes(value: Any, max_bytes: int) -> str:
    text = str(value or "")
    cap = _coerce_output_cap(max_bytes)
    if cap <= 0:
        return ""
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= cap:
        return text
    return encoded[:cap].decode("utf-8", errors="ignore")


@dataclass
class ProjectProfile:
    kind: str
    root: str
    checks: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class CheckResult:
    name: str
    command: str
    passed: bool
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    execution_policy: dict[str, Any] = field(default_factory=dict)


def _module_installed(name: str) -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _tsc_installed(root: Path) -> bool:
    bin_dir = root / "node_modules" / ".bin"
    return (bin_dir / "tsc").exists() or (bin_dir / "tsc.cmd").exists()


def _executable_available(name: str) -> bool:
    return shutil.which(name) is not None


def _package_json_check() -> dict[str, Any]:
    return {
        "name": "package-json",
        "argv": [
            sys.executable,
            "-c",
            (
                "import json, sys\n"
                "from pathlib import Path\n"
                "try:\n"
                "    data = json.loads(Path('package.json').read_text(encoding='utf-8'))\n"
                "except json.JSONDecodeError as exc:\n"
                "    print(\n"
                "        f'package.json: invalid JSON: {exc.msg} '\n"
                "        f'at line {exc.lineno} column {exc.colno}',\n"
                "        file=sys.stderr,\n"
                "    )\n"
                "    sys.exit(2)\n"
                "name = data.get('name') if isinstance(data, dict) else None\n"
                "scripts = data.get('scripts', {}) if isinstance(data, dict) else {}\n"
                "print(f\"package={name or '<unnamed>'} scripts={len(scripts) if isinstance(scripts, dict) else 0}\")\n"
            ),
        ],
        "display_cmd": 'python -c "parse package.json"',
        "fatal_on_failure": True,
    }


def _pyproject_toml_check() -> dict[str, Any]:
    return {
        "name": "pyproject",
        "argv": [
            sys.executable,
            "-c",
            (
                "import sys\n"
                "from pathlib import Path\n"
                "text = Path('pyproject.toml').read_text(encoding='utf-8')\n"
                "try:\n"
                "    import tomllib\n"
                "except ModuleNotFoundError:\n"
                "    print(f'pyproject.toml: {len(text.splitlines())} lines')\n"
                "else:\n"
                "    try:\n"
                "        tomllib.loads(text)\n"
                "    except tomllib.TOMLDecodeError as exc:\n"
                "        print(f'pyproject.toml: invalid TOML: {exc}', file=sys.stderr)\n"
                "        sys.exit(2)\n"
                "    print('pyproject.toml: valid TOML')\n"
            ),
        ],
        "display_cmd": 'python -c "parse pyproject.toml"',
        "fatal_on_failure": True,
    }


def _manifest_file_check(filename: str, *, name: str) -> dict[str, Any]:
    if filename.endswith(".toml"):
        inline = (
            "import sys\n"
            "from pathlib import Path\n"
            f"path = Path({filename!r})\n"
            "text = path.read_text(encoding='utf-8')\n"
            "try:\n"
            "    import tomllib\n"
            "except ModuleNotFoundError:\n"
            "    print(f'{path.name}: {len(text.splitlines())} lines')\n"
            "else:\n"
            "    try:\n"
            "        tomllib.loads(text)\n"
            "    except tomllib.TOMLDecodeError as exc:\n"
            "        print(f'{path.name}: invalid TOML: {exc}', file=sys.stderr)\n"
            "        sys.exit(2)\n"
            "    print(f'{path.name}: valid TOML')\n"
        )
    elif filename == "go.mod":
        inline = (
            "import sys\n"
            "from pathlib import Path\n"
            "path = Path('go.mod')\n"
            "lines = path.read_text(encoding='utf-8').splitlines()\n"
            "first = next((line.strip() for line in lines if line.strip() and not line.strip().startswith('//')), '')\n"
            "if not first.startswith('module '):\n"
            "    print('go.mod: missing module directive', file=sys.stderr)\n"
            "    sys.exit(2)\n"
            "print('go.mod: module ' + first[len('module '):].strip())\n"
        )
    else:
        inline = (
            "from pathlib import Path\n"
            f"path = Path({filename!r})\n"
            "text = path.read_text(encoding='utf-8')\n"
            "print(f'{path.name}: {len(text.splitlines())} lines')\n"
        )
    return {
        "name": name,
        "argv": [
            sys.executable,
            "-c",
            inline,
        ],
        "display_cmd": f'python -c "read {filename}"',
        "fatal_on_failure": True,
    }


def detect_project(workspace: str) -> ProjectProfile:
    root = Path(workspace)
    if not root.is_dir():
        return ProjectProfile(kind="unknown", root=workspace)

    checks: list[dict[str, Any]] = []

    if (root / "package.json").is_file():
        checks.append(_package_json_check())
        pkg = _read_json(root / "package.json")
        scripts = pkg.get("scripts", {}) if isinstance(pkg, dict) else {}
        has_ts = (root / "tsconfig.json").is_file()
        kind = "node-ts" if has_ts else "node"
        npm_available = _executable_available("npm")
        npx_available = _executable_available("npx")
        # Only offer typecheck when tsc is actually installed: a bare
        # ``npx tsc`` on a machine without the package may hit the
        # network to fetch it, and a missing binary would surface as a
        # bogus "typecheck failed" — these checks feed the agent's
        # auto-diagnostics, so a false failure sends it chasing
        # phantom errors.
        if has_ts and npx_available and _tsc_installed(root):
            checks.append(
                {
                    "name": "typecheck",
                    "argv": ["npx", "--no-install", "tsc", "--noEmit"],
                    "display_cmd": "npx --no-install tsc --noEmit",
                }
            )
        if npm_available and "lint" in scripts:
            checks.append(
                {
                    "name": "lint",
                    "argv": ["npm", "run", "lint"],
                    "display_cmd": "npm run lint",
                }
            )
        if npm_available and "test" in scripts:
            # --run is the vitest convention; if the project uses a different
            # runner the extra flag is simply ignored.
            checks.append(
                {
                    "name": "test",
                    "argv": ["npm", "test", "--", "--run"],
                    "display_cmd": "npm test -- --run",
                }
            )
        if npm_available and "build" in scripts:
            checks.append(
                {
                    "name": "build",
                    "argv": ["npm", "run", "build"],
                    "display_cmd": "npm run build",
                }
            )
        return ProjectProfile(kind=kind, root=workspace, checks=checks)

    if (root / "pyproject.toml").is_file() or (root / "setup.py").is_file():
        kind = "python"
        if (root / "pyproject.toml").is_file():
            checks.append(_pyproject_toml_check())
        # Same tool-presence gate as tsc above: without it, projects
        # that don't ship mypy (most of them) get "No module named
        # mypy" reported as a typecheck FAILURE after every file write.
        if (root / "pyproject.toml").is_file() and _module_installed("mypy"):
            checks.append(
                {
                    "name": "typecheck",
                    "argv": [sys.executable, "-m", "mypy", ".", "--ignore-missing-imports"],
                    "display_cmd": "python -m mypy . --ignore-missing-imports",
                }
            )
        if _any_exists(
            root, ["pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini"]
        ) and _module_installed("pytest"):
            checks.append(
                {
                    "name": "test",
                    "argv": [sys.executable, "-m", "pytest", "--tb=short", "-q"],
                    "display_cmd": "python -m pytest --tb=short -q",
                }
            )
        checks.append(
            {
                "name": "syntax",
                "argv": [
                    sys.executable,
                    "-c",
                    (
                        "from pathlib import Path\n"
                        "import py_compile, sys\n"
                        "files = sorted(p for p in Path('.').rglob('*.py') "
                        "if '.venv' not in p.parts and '__pycache__' not in p.parts)[:20]\n"
                        "failed = 0\n"
                        "for p in files:\n"
                        "    try:\n"
                        "        py_compile.compile(str(p), doraise=True)\n"
                        "    except Exception as exc:\n"
                        "        failed += 1\n"
                        "        print(f'{p}: {exc}')\n"
                        "print(f'checked {len(files)} python files')\n"
                        "sys.exit(1 if failed else 0)\n"
                    ),
                ],
                "display_cmd": 'python -c "compile up to 20 Python files"',
            }
        )
        return ProjectProfile(kind=kind, root=workspace, checks=checks)

    if (root / "Cargo.toml").is_file():
        checks.append(_manifest_file_check("Cargo.toml", name="cargo-manifest"))
        if not _executable_available("cargo"):
            return ProjectProfile(
                kind="rust",
                root=workspace,
                checks=checks,
            )
        checks.extend(
            [
                {"name": "check", "argv": ["cargo", "check"], "display_cmd": "cargo check"},
                {
                    "name": "test",
                    "argv": ["cargo", "test", "--no-run"],
                    "display_cmd": "cargo test --no-run",
                },
                {
                    "name": "clippy",
                    "argv": ["cargo", "clippy", "--", "-D", "warnings"],
                    "display_cmd": "cargo clippy -- -D warnings",
                },
            ]
        )
        return ProjectProfile(kind="rust", root=workspace, checks=checks)

    if (root / "go.mod").is_file():
        checks.append(_manifest_file_check("go.mod", name="go-manifest"))
        if not _executable_available("go"):
            return ProjectProfile(
                kind="go",
                root=workspace,
                checks=checks,
            )
        checks.extend(
            [
                {
                    "name": "build",
                    "argv": ["go", "build", "./..."],
                    "display_cmd": "go build ./...",
                },
                {"name": "vet", "argv": ["go", "vet", "./..."], "display_cmd": "go vet ./..."},
                {
                    "name": "test",
                    "argv": ["go", "test", "./...", "-count=1", "-short"],
                    "display_cmd": "go test ./... -count=1 -short",
                },
            ]
        )
        return ProjectProfile(kind="go", root=workspace, checks=checks)

    return ProjectProfile(
        kind="unknown",
        root=workspace,
        checks=[
            {
                "name": "file-count",
                "argv": [
                    sys.executable,
                    "-c",
                    (
                        "from pathlib import Path\n"
                        "root = Path('.')\n"
                        "count = sum(1 for p in root.rglob('*') "
                        "if p.is_file() and len(p.relative_to(root).parts) <= 3)\n"
                        "print(count)\n"
                    ),
                ],
                "display_cmd": 'python -c "count files up to depth 3"',
            },
        ],
    )


def run_checks(
    profile: ProjectProfile,
    *,
    timeout_per_check: float = 60.0,
    max_output: int = 8000,
    sandbox_dir: str | None = None,
) -> list[CheckResult]:
    from runtime.platform.process.streaming import stream_run

    effective_sandbox_dir = sandbox_dir or profile.root
    output_cap = _coerce_output_cap(max_output)
    results: list[CheckResult] = []
    for check in profile.checks:
        argv = check.get("argv")
        display = check.get("display_cmd") or check.get("cmd") or ""
        legacy_cmd = check.get("cmd")
        fatal_on_failure = bool(check.get("fatal_on_failure"))
        t0 = time.monotonic()
        if isinstance(argv, list) and argv:
            r = stream_run(
                argv,
                cwd=str(profile.root),
                timeout=timeout_per_check,
                output_cap_bytes=output_cap,
                sandbox_dir=effective_sandbox_dir,
                sandbox_required=True,
            )
        elif isinstance(legacy_cmd, str):
            _logger.warning(
                "verify_skills: legacy 'cmd' string check %r — switch to 'argv'",
                check.get("name"),
            )
            r = stream_run(
                _legacy_shell_argv(legacy_cmd),
                cwd=str(profile.root),
                timeout=timeout_per_check,
                output_cap_bytes=output_cap,
                sandbox_dir=effective_sandbox_dir,
                sandbox_required=True,
            )
        else:
            results.append(
                CheckResult(
                    name=check.get("name", "?"),
                    command=display,
                    passed=False,
                    exit_code=-2,
                    stdout="",
                    stderr="check is missing both 'argv' and 'cmd'",
                    duration_ms=0,
                )
            )
            continue
        duration = int((time.monotonic() - t0) * 1000)
        if not isinstance(r, dict):
            results.append(
                CheckResult(
                    name=check["name"],
                    command=display,
                    passed=False,
                    exit_code=-6,
                    stdout="",
                    stderr=f"verifier runner returned non-dict result: {type(r).__name__}",
                    duration_ms=duration,
                )
            )
            if fatal_on_failure:
                break
            continue
        stdout = _truncate_utf8_bytes(r.get("stdout") or "", output_cap)
        stderr = _truncate_utf8_bytes(r.get("stderr") or "", output_cap)
        execution_policy = (
            r.get("execution_policy") if isinstance(r.get("execution_policy"), dict) else {}
        )
        if "error" in r and "exit_code" not in r:
            if str(r["error"]).startswith("sandbox_violation:"):
                results.append(
                    CheckResult(
                        name=check["name"],
                        command=display,
                        passed=False,
                        exit_code=-4,
                        stdout=stdout,
                        stderr=str(r["error"]),
                        duration_ms=duration,
                        execution_policy=execution_policy,
                    )
                )
                break
            # FileNotFoundError surfaced through stream_run.
            results.append(
                CheckResult(
                    name=check["name"],
                    command=display,
                    passed=False,
                    exit_code=-3,
                    stdout=stdout,
                    stderr=f"executable not found: {r['error']}",
                    duration_ms=duration,
                    execution_policy=execution_policy,
                )
            )
            if fatal_on_failure:
                break
            continue
        if r.get("timed_out"):
            results.append(
                CheckResult(
                    name=check["name"],
                    command=display,
                    passed=False,
                    exit_code=-1,
                    stdout=stdout,
                    stderr=stderr or "timeout",
                    duration_ms=duration,
                    execution_policy=execution_policy,
                )
            )
            if fatal_on_failure:
                break
            continue
        if r.get("cancelled"):
            results.append(
                CheckResult(
                    name=check["name"],
                    command=display,
                    passed=False,
                    exit_code=-5,
                    stdout=stdout,
                    stderr=stderr or "cancelled",
                    duration_ms=duration,
                    execution_policy=execution_policy,
                )
            )
            break
        raw_exit_code = r.get("exit_code")
        if raw_exit_code is None:
            results.append(
                CheckResult(
                    name=check["name"],
                    command=display,
                    passed=False,
                    exit_code=-6,
                    stdout=stdout,
                    stderr=stderr or "verifier runner returned no exit_code",
                    duration_ms=duration,
                    execution_policy=execution_policy,
                )
            )
            if fatal_on_failure:
                break
            continue
        try:
            exit_code = int(raw_exit_code)
        except (TypeError, ValueError):
            results.append(
                CheckResult(
                    name=check["name"],
                    command=display,
                    passed=False,
                    exit_code=-6,
                    stdout=stdout,
                    stderr=stderr
                    or f"verifier runner returned invalid exit_code: {raw_exit_code!r}",
                    duration_ms=duration,
                    execution_policy=execution_policy,
                )
            )
            if fatal_on_failure:
                break
            continue
        results.append(
            CheckResult(
                name=check["name"],
                command=display,
                passed=exit_code == 0,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                duration_ms=duration,
                execution_policy=execution_policy,
            )
        )
        if fatal_on_failure and exit_code != 0:
            break
    return results


def _read_json(path: Path) -> Any:
    import json

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _any_exists(root: Path, names: list[str]) -> bool:
    return any((root / n).is_file() for n in names)
