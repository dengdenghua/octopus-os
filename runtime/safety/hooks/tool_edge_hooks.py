"""Declarative tool-edge hooks (``preToolUse`` / ``postToolUse`` hooks
that live in config files, not source code).

Complements :mod:`runtime.safety.hooks`, which dispatches to Python
handlers registered via ``@register_hook``. This module is the external
flavour: hooks are declared in a JSON config, executed as subprocess
under :class:`runtime.safety.sandboxing.sandbox.SandboxRunner`, and veto by exit
code or JSON stdout. Users and admins can ship hooks without touching
the runtime source.

Both systems run simultaneously when wired in — the declarative set
runs first (cheap static-config layer), then the in-process Python
registry. This mirrors the permission model's two-layer shape (static
rules → dynamic callback).

Configuration shape::

    {
      "hooks": [
        {
          "event": "preToolUse",
          "match": {"tool": "exec_shell"},
          "command": ["python", "scripts/audit_shell.py"]
        },
        {
          "event": "postToolUse",
          "match": {"tool": "*"},
          "command": ["python", "scripts/log_tool.py"]
        }
      ]
    }

Convention for the hook script:

* stdin receives a JSON payload.
* exit 0 + no output (or non-decision JSON) = allow / informational.
* exit 0 + ``{"allow": false, "reason": "..."}`` on stdout = veto.
* non-zero exit = veto with stderr as the reason.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from runtime.safety.sandboxing.sandbox import (
    SandboxPolicy,
    SandboxResult,
    SandboxRunner,
    SandboxViolation,
)

_logger = logging.getLogger(__name__)

ToolEdgeEvent = Literal["preToolUse", "postToolUse"]

_POST_WRITE_TOOLS: frozenset[str] = frozenset(
    {
        "write_file",
        "create_file",
        "write_text_file",
        "append_text_file",
        "edit_text_file",
        "edit_file",
        "multi_edit_file",
        "edit_code",
        "str_replace",
    }
)

_DIAGNOSTIC_RECORD_SCHEMA = "echo.post_write_diagnostic_record.v1"

_POST_WRITE_TIMEOUT_S = 8.0
_POST_WRITE_OUTPUT_CAP = 2000
_REGRESSION_MATRIX_OUTPUT_CAP = 2200


@dataclass(frozen=True, slots=True)
class RegressionCheck:
    kind: str
    command: str
    reason: str
    priority: int = 2


@dataclass(frozen=True, slots=True)
class RegressionMatrix:
    target: str
    checks: tuple[RegressionCheck, ...]

    def render(self) -> str | None:
        if not self.checks:
            return None
        lines = [f"target: {self.target}", "recommended checks:"]
        for check in sorted(self.checks, key=lambda item: (item.priority, item.command)):
            lines.append(f"- [{check.kind}] {check.command} :: {check.reason}")
        return "\n".join(lines)[:_REGRESSION_MATRIX_OUTPUT_CAP]


def post_write_diagnostics(
    tool_name: str,
    args: dict[str, Any],
    result: Any,
    *,
    workspace_path: str | Path,
) -> str | None:
    """Run lightweight per-file diagnostics after a successful write.

    Fires only for the small set of write/edit tools listed in
    :data:`_POST_WRITE_TOOLS`. Picks the first relevant check from the
    project profile that matches the file's suffix:

    * ``.py`` — ``ruff check`` (single file, 8s timeout)
    * ``.ts/.tsx/.js/.jsx`` — single-file eslint when an eslint config
      lives in the project root, otherwise skipped (tsc is project-wide
      and too heavy for a per-write hook).

    Returns
    -------
    str | None
        ``None`` when the tool isn't a write skill, the result reports
        an error, the affected file can't be located, no relevant
        check is available for the language, the check passes, the
        check times out, or any subprocess error occurs. Returns the
        diagnostic stdout (capped at 2000 chars) when the check fails.

    Best-effort by contract — never raises into the executor.
    """
    try:
        if tool_name not in _POST_WRITE_TOOLS:
            return None
        if not isinstance(result, dict):
            return None
        # Failure heuristic: an "error" key without a "path" means the
        # write didn't land. We tolerate "error" + "path" because some
        # write skills surface partial-success warnings under "error".
        if "error" in result and "path" not in result:
            return None

        target = _resolve_write_target(args, workspace_path)
        if target is None or not target.is_file():
            return None

        suffix = target.suffix.lower()
        try:
            from runtime.execution.suckers.verify_skills import detect_project

            profile = detect_project(str(workspace_path))
        except Exception:  # noqa: BLE001 — diagnostics must never raise
            return None

        if profile.kind == "unknown":
            return None

        if suffix == ".py":
            return _run_ruff_single_file(target, workspace_path)
        if suffix in {".ts", ".tsx", ".js", ".jsx"}:
            return _run_eslint_single_file(target, workspace_path)
        return None
    except Exception:  # noqa: BLE001 — diagnostics must never raise
        return None


def post_write_diagnostic_record(
    tool_name: str,
    args: dict[str, Any],
    result: Any,
    *,
    workspace_path: str | Path,
) -> dict[str, Any]:
    """Return an auditable diagnostic decision for every post-write attempt."""
    try:
        if tool_name not in _POST_WRITE_TOOLS:
            return _diagnostic_record(tool_name, status="skipped", reason="not a write tool")
        if not isinstance(result, dict):
            return _diagnostic_record(tool_name, status="skipped", reason="non-dict result")
        if "error" in result and "path" not in result:
            return _diagnostic_record(tool_name, status="skipped", reason="write failed")
        target = _resolve_write_target(args, workspace_path)
        if target is None:
            return _diagnostic_record(tool_name, status="skipped", reason="no target path")
        target_text = str(target)
        if not target.is_file():
            return _diagnostic_record(
                tool_name,
                target=target_text,
                status="skipped",
                reason="target is not a file",
            )
        try:
            from runtime.execution.suckers.verify_skills import detect_project

            profile = detect_project(str(workspace_path))
        except Exception as exc:  # noqa: BLE001
            return _diagnostic_record(
                tool_name,
                target=target_text,
                status="skipped",
                reason=f"project detection failed: {exc}",
            )
        if profile.kind == "unknown":
            return _diagnostic_record(
                tool_name,
                target=target_text,
                status="skipped",
                reason="unknown project type",
            )
        suffix = target.suffix.lower()
        if suffix == ".py":
            diagnostic = _run_ruff_single_file(target, workspace_path)
            return _diagnostic_record(
                tool_name,
                target=target_text,
                status="failed" if diagnostic else "passed",
                reason="ruff failed" if diagnostic else "ruff passed",
                diagnostic=diagnostic,
            )
        if suffix in {".ts", ".tsx", ".js", ".jsx"}:
            diagnostic = _run_eslint_single_file(target, workspace_path)
            return _diagnostic_record(
                tool_name,
                target=target_text,
                status="failed" if diagnostic else "passed",
                reason="eslint failed" if diagnostic else "eslint passed or unavailable",
                diagnostic=diagnostic,
            )
        return _diagnostic_record(
            tool_name,
            target=target_text,
            status="skipped",
            reason=f"unsupported suffix: {suffix or '<none>'}",
        )
    except Exception as exc:  # noqa: BLE001
        return _diagnostic_record(
            tool_name,
            status="skipped",
            reason=f"diagnostics unavailable: {exc}",
        )


def _diagnostic_record(
    tool_name: str,
    *,
    status: str,
    reason: str,
    target: str = "",
    diagnostic: str | None = None,
) -> dict[str, Any]:
    return {
        "schema": _DIAGNOSTIC_RECORD_SCHEMA,
        "tool_name": tool_name,
        "target": target,
        "status": status,
        "reason": reason,
        "diagnostic": diagnostic or "",
        "required": True,
    }


def post_write_regression_matrix(
    tool_name: str,
    args: dict[str, Any],
    result: Any,
    *,
    workspace_path: str | Path,
) -> str | None:
    """Recommend targeted verification commands for a successful write.

    Unlike :func:`post_write_diagnostics`, this does not execute tests.
    It gives the model a low-latency matrix of the most relevant commands
    to run next, based on the edited path and nearby test files.
    """
    try:
        if tool_name not in _POST_WRITE_TOOLS:
            return None
        if not isinstance(result, dict):
            return None
        if "error" in result and "path" not in result:
            return None
        target = _resolve_write_target(args, workspace_path)
        if target is None:
            return None
        matrix = regression_matrix_for_path(target, workspace_path=workspace_path)
        return matrix.render()
    except Exception:  # noqa: BLE001 — diagnostics must never raise
        return None


def regression_matrix_for_path(
    target: str | Path,
    *,
    workspace_path: str | Path,
) -> RegressionMatrix:
    workspace = Path(workspace_path).resolve(strict=False)
    path = Path(target).expanduser()
    if not path.is_absolute():
        path = workspace / path
    path = path.resolve(strict=False)
    rel = _safe_relative(path, workspace)
    checks: list[RegressionCheck] = []

    suffix = path.suffix.lower()
    if suffix == ".py":
        checks.append(
            RegressionCheck(
                kind="lint",
                command=_python_module_cmd("ruff", f"check {rel}"),
                reason="edited Python file",
                priority=1,
            )
        )
        checks.extend(_python_regression_checks(rel, workspace))
    elif suffix in {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}:
        frontend_root = _frontend_root_for(path, workspace)
        checks.extend(_frontend_regression_checks(path, workspace, frontend_root))
    elif path.name in {"package.json", "pnpm-lock.yaml", "vite.config.ts", "tsconfig.json"}:
        frontend_root = _frontend_root_for(path, workspace)
        checks.append(
            RegressionCheck(
                kind="typecheck",
                command=_frontend_cmd("pnpm check", frontend_root, workspace),
                reason="frontend dependency or compiler configuration changed",
                priority=1,
            )
        )
    elif path.name in {"pyproject.toml", "requirements.txt", "uv.lock"}:
        checks.append(
            RegressionCheck(
                kind="test",
                command=_python_module_cmd(
                    "pytest", "tests/test_config.py tests/test_cli_smoke.py -q"
                ),
                reason="Python project configuration changed",
                priority=1,
            )
        )

    return RegressionMatrix(target=rel, checks=tuple(_dedupe_checks(checks)))


def _resolve_write_target(
    args: dict[str, Any],
    workspace_path: str | Path,
) -> Path | None:
    raw = args.get("path") or args.get("file_path") or args.get("filepath")
    if not isinstance(raw, str) or not raw.strip():
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        base = args.get("sandbox_dir") or args.get("cwd")
        if isinstance(base, str) and base.strip():
            path = Path(base).expanduser() / path
        else:
            path = Path(workspace_path) / path
    try:
        return path.resolve(strict=False)
    except OSError:
        return path


def _safe_relative(path: Path, workspace: Path) -> str:
    try:
        return path.relative_to(workspace).as_posix()
    except ValueError:
        return path.as_posix()


def _python_module_cmd(module: str, args: str = "") -> str:
    suffix = f" {args.strip()}" if args.strip() else ""
    return f"python -m {module}{suffix}"


def _frontend_root_for(path: Path, workspace: Path) -> Path:
    frontend = workspace / "frontend"
    try:
        path.relative_to(frontend)
    except ValueError:
        return workspace
    return frontend


def _frontend_cmd(command: str, frontend_root: Path, workspace: Path) -> str:
    rel_root = _safe_relative(frontend_root, workspace)
    return command if rel_root == "." else f"cd {rel_root} && {command}"


def _python_regression_checks(rel: str, workspace: Path) -> list[RegressionCheck]:
    checks: list[RegressionCheck] = []
    path = Path(rel)
    if rel.startswith("tests/") and path.name.startswith("test_"):
        checks.append(
            RegressionCheck(
                kind="test",
                command=_python_module_cmd("pytest", f"{rel} -q"),
                reason="edited test file",
                priority=1,
            )
        )
        return checks

    candidates = _python_test_candidates(rel)
    existing = [candidate for candidate in candidates if (workspace / candidate).is_file()]
    for test_path in existing[:4]:
        checks.append(
            RegressionCheck(
                kind="test",
                command=_python_module_cmd("pytest", f"{test_path} -q"),
                reason="mapped from edited Python module",
                priority=1,
            )
        )

    def add_existing(test_paths: list[str], *, reason: str) -> None:
        existing_paths = [test for test in test_paths if (workspace / test).is_file()]
        if not existing_paths:
            return
        checks.append(
            RegressionCheck(
                kind="test",
                command=_python_module_cmd("pytest", f"{' '.join(existing_paths)} -q"),
                reason=reason,
                priority=2,
            )
        )

    if rel.startswith("runtime/sensing/gateway/"):
        add_existing(
            ["tests/test_realtime_cerebrum.py", "tests/test_app_observability_endpoints.py"],
            reason="gateway changes can affect realtime/API contracts",
        )
    if rel.startswith("runtime/safety/evolution/"):
        add_existing(
            ["tests/test_evolution_modules.py", "tests/test_evolution_integration.py"],
            reason="evolution core changed",
        )
    if rel.startswith("runtime/memory/diagnostics/"):
        add_existing(
            ["tests/test_agent_trace_store.py", "tests/test_agent_trace_router.py"],
            reason="trace diagnostics changed",
        )
    if rel.startswith("runtime/execution/tool_engine/"):
        add_existing(
            ["tests/test_tool_protocol.py", "tests/test_tool_bridge_scope.py"],
            reason="tool execution engine changed",
        )
    if rel.startswith("runtime/safety/hooks/"):
        add_existing(
            ["tests/test_tool_edge_hooks.py", "tests/test_post_write_diagnostics.py"],
            reason="tool hook behavior changed",
        )
    return checks


def _python_test_candidates(rel: str) -> list[str]:
    path = Path(rel)
    stem = path.stem
    candidates = [f"tests/test_{stem}.py"]
    if stem == "trace_store":
        candidates.append("tests/test_agent_trace_store.py")
    if stem == "promotion_applier":
        candidates.append("tests/test_promotion_applier.py")
    if stem == "tool_edge_hooks":
        candidates.append("tests/test_tool_edge_hooks.py")
        candidates.append("tests/test_post_write_diagnostics.py")
    return list(dict.fromkeys(candidates))


def _frontend_regression_checks(
    path: Path,
    workspace: Path,
    frontend_root: Path,
) -> list[RegressionCheck]:
    rel = _safe_relative(path, workspace)
    checks: list[RegressionCheck] = []
    frontend_rel = _safe_relative(path, frontend_root)
    if ".test." in path.name or path.name.endswith(".test.ts") or path.name.endswith(".test.tsx"):
        checks.append(
            RegressionCheck(
                kind="test",
                command=_frontend_cmd(f"pnpm vitest run {frontend_rel}", frontend_root, workspace),
                reason="edited frontend test file",
                priority=1,
            )
        )
    else:
        for test_path in _frontend_test_candidates(path):
            if test_path.is_file():
                checks.append(
                    RegressionCheck(
                        kind="test",
                        command=_frontend_cmd(
                            f"pnpm vitest run {_safe_relative(test_path, frontend_root)}",
                            frontend_root,
                            workspace,
                        ),
                        reason="co-located frontend test",
                        priority=1,
                    )
                )
                break
    checks.append(
        RegressionCheck(
            kind="typecheck",
            command=_frontend_cmd("pnpm check", frontend_root, workspace),
            reason=f"TypeScript surface changed: {rel}",
            priority=2,
        )
    )
    return checks


def _frontend_test_candidates(path: Path) -> list[Path]:
    stem = path.stem
    if stem.endswith(".test"):
        return [path]
    return [
        path.with_name(f"{stem}.test{path.suffix}"),
        path.with_name(f"{stem}.test.ts"),
        path.with_name(f"{stem}.test.tsx"),
        path.with_name(f"{stem}.test.mjs"),
    ]


def _dedupe_checks(checks: list[RegressionCheck]) -> list[RegressionCheck]:
    seen: set[str] = set()
    out: list[RegressionCheck] = []
    for check in checks:
        if check.command in seen:
            continue
        seen.add(check.command)
        out.append(check)
    return out


def _run_ruff_single_file(
    target: Path,
    workspace_path: str | Path,
) -> str | None:
    argv = [sys.executable, "-m", "ruff", "check", "--no-fix", str(target)]
    try:
        proc = subprocess.run(  # noqa: S603 — argv list, shell=False
            argv,
            capture_output=True,
            text=True,
            cwd=str(workspace_path) if Path(workspace_path).is_dir() else None,
            timeout=_POST_WRITE_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    except Exception:  # noqa: BLE001
        return None
    if proc.returncode == 0:
        return None
    output = (proc.stdout or "") + (proc.stderr or "")
    output = output.strip()
    if not output:
        return None
    return f"ruff diagnostics ({target.name}):\n{output[:_POST_WRITE_OUTPUT_CAP]}"


def _run_eslint_single_file(
    target: Path,
    workspace_path: str | Path,
) -> str | None:
    workspace = Path(workspace_path)
    if not workspace.is_dir():
        return None
    eslint_markers = (
        ".eslintrc",
        ".eslintrc.js",
        ".eslintrc.cjs",
        ".eslintrc.json",
        ".eslintrc.yaml",
        ".eslintrc.yml",
        "eslint.config.js",
        "eslint.config.mjs",
        "eslint.config.cjs",
    )
    if not any((workspace / m).exists() for m in eslint_markers):
        return None
    npx = "npx.cmd" if os.name == "nt" else "npx"
    argv = [npx, "eslint", "--no-eslintrc=false", "--max-warnings=0", str(target)]
    try:
        proc = subprocess.run(  # noqa: S603 — argv list, shell=False
            argv,
            capture_output=True,
            text=True,
            cwd=str(workspace),
            timeout=_POST_WRITE_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    except Exception:  # noqa: BLE001
        return None
    if proc.returncode == 0:
        return None
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if not output:
        return None
    return f"eslint diagnostics ({target.name}):\n{output[:_POST_WRITE_OUTPUT_CAP]}"


@dataclass(frozen=True, slots=True)
class ToolEdgeHookSpec:
    event: ToolEdgeEvent
    command: tuple[str, ...]
    tool: str = "*"
    timeout_s: float = 10.0
    args_contains: str = ""

    def matches(self, tool_name: str, args_preview: str) -> bool:  # noqa: SIM103 — early-return guards read better
        if not fnmatch.fnmatchcase(tool_name, self.tool):
            return False
        return not (self.args_contains and self.args_contains not in args_preview)


@dataclass(frozen=True, slots=True)
class ToolEdgeHookConfig:
    hooks: tuple[ToolEdgeHookSpec, ...] = field(default_factory=tuple)

    def for_event(self, event: ToolEdgeEvent) -> tuple[ToolEdgeHookSpec, ...]:
        return tuple(h for h in self.hooks if h.event == event)


@dataclass(frozen=True, slots=True)
class ToolEdgeHookOutcome:
    allow: bool
    reason: str = ""
    output: str = ""
    exit_code: int = 0
    timed_out: bool = False
    spec: ToolEdgeHookSpec | None = None

    @property
    def vetoed(self) -> bool:
        return not self.allow


def load_tool_edge_hook_config(path: Path) -> ToolEdgeHookConfig:
    """Read the declarative config file. Missing → empty."""
    if not path.exists():
        return ToolEdgeHookConfig()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        _logger.warning("declarative hooks: failed to parse %s (%s); ignoring", path, exc)
        return ToolEdgeHookConfig()
    return _parse_tool_edge_payload(raw)


def _parse_tool_edge_payload(payload: Any) -> ToolEdgeHookConfig:
    if not isinstance(payload, dict):
        return ToolEdgeHookConfig()
    rules_raw = payload.get("hooks")
    if not isinstance(rules_raw, list):
        return ToolEdgeHookConfig()
    parsed: list[ToolEdgeHookSpec] = []
    for entry in rules_raw:
        if not isinstance(entry, dict):
            continue
        event = entry.get("event")
        if event not in ("preToolUse", "postToolUse"):
            continue
        command = entry.get("command")
        if not isinstance(command, list) or not command:
            continue
        cmd_tuple = tuple(str(x) for x in command)
        match = entry.get("match") or {}
        if not isinstance(match, dict):
            match = {}
        tool = match.get("tool") or "*"
        args_contains = match.get("args_contains") or ""
        timeout = float(entry.get("timeout_s") or 10.0)
        if not isinstance(tool, str):
            continue
        if not isinstance(args_contains, str):
            args_contains = ""
        parsed.append(
            ToolEdgeHookSpec(
                event=event,
                command=cmd_tuple,
                tool=tool,
                args_contains=args_contains,
                timeout_s=timeout,
            )
        )
    return ToolEdgeHookConfig(hooks=tuple(parsed))


class ToolEdgeHookRunner:
    """Executes declarative hooks at the tool boundary."""

    def __init__(
        self,
        config: ToolEdgeHookConfig,
        *,
        sandbox_factory: Any = None,
    ) -> None:
        self._config = config
        self._sandbox_factory = sandbox_factory or _default_sandbox_factory

    def run_pre(
        self,
        *,
        tool_name: str,
        args_preview: str,
        thread_id: str,
        workspace: Path,
        extra: dict[str, Any] | None = None,
    ) -> ToolEdgeHookOutcome:
        """Run all matching ``preToolUse`` hooks. First veto wins."""
        last = ToolEdgeHookOutcome(allow=True)
        for spec in self._config.for_event("preToolUse"):
            if not spec.matches(tool_name, args_preview):
                continue
            outcome = self._run_one(
                spec,
                payload={
                    "event": "preToolUse",
                    "tool": tool_name,
                    "argsPreview": args_preview,
                    "threadId": thread_id,
                    "extra": extra or {},
                },
                workspace=workspace,
            )
            if outcome.vetoed:
                return outcome
            last = outcome
        return last

    def run_post(
        self,
        *,
        tool_name: str,
        args_preview: str,
        result_preview: str,
        thread_id: str,
        workspace: Path,
        extra: dict[str, Any] | None = None,
    ) -> Iterable[ToolEdgeHookOutcome]:
        """Run all matching ``postToolUse`` hooks. Vetoes ignored."""
        outcomes: list[ToolEdgeHookOutcome] = []
        for spec in self._config.for_event("postToolUse"):
            if not spec.matches(tool_name, args_preview):
                continue
            outcomes.append(
                self._run_one(
                    spec,
                    payload={
                        "event": "postToolUse",
                        "tool": tool_name,
                        "argsPreview": args_preview,
                        "resultPreview": result_preview,
                        "threadId": thread_id,
                        "extra": extra or {},
                    },
                    workspace=workspace,
                )
            )
        return outcomes

    def _run_one(
        self,
        spec: ToolEdgeHookSpec,
        *,
        payload: dict[str, Any],
        workspace: Path,
    ) -> ToolEdgeHookOutcome:
        runner = self._sandbox_factory(workspace, spec.timeout_s)
        try:
            result = runner.run(
                list(spec.command),
                stdin_text=json.dumps(payload, ensure_ascii=False),
            )
        except SandboxViolation as exc:
            _logger.warning("hook %s: sandbox refused (%s)", spec.command, exc)
            return ToolEdgeHookOutcome(
                allow=False,
                reason=f"hook sandbox violation: {exc}",
                spec=spec,
            )
        return _parse_tool_edge_result(result, spec)


def _parse_tool_edge_result(result: SandboxResult, spec: ToolEdgeHookSpec) -> ToolEdgeHookOutcome:
    text = (result.stdout or "").strip()
    decision: dict[str, Any] = {}
    if text.startswith("{") and text.endswith("}"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                decision = parsed
        except json.JSONDecodeError:  # noqa: BLE001 — hook config JSON malformed; skip hook
            pass
    if result.timed_out:
        return ToolEdgeHookOutcome(
            allow=False,
            reason="hook timed out",
            output=result.stdout,
            exit_code=result.exit_code,
            timed_out=True,
            spec=spec,
        )
    if result.exit_code != 0:
        reason = (result.stderr or "").strip() or f"hook exit {result.exit_code}"
        return ToolEdgeHookOutcome(
            allow=False,
            reason=reason,
            output=result.stdout,
            exit_code=result.exit_code,
            spec=spec,
        )
    if "allow" in decision and decision["allow"] is False:
        return ToolEdgeHookOutcome(
            allow=False,
            reason=str(decision.get("reason") or "hook declined"),
            output=result.stdout,
            exit_code=0,
            spec=spec,
        )
    return ToolEdgeHookOutcome(allow=True, output=result.stdout, exit_code=0, spec=spec)


def _default_sandbox_factory(workspace: Path, timeout_s: float) -> SandboxRunner:
    return SandboxRunner(
        SandboxPolicy(
            workspace=workspace,
            timeout_s=timeout_s,
            allow_network=False,
            # Model inference endpoints stay reachable in a network-denied
            # sandbox (Claude Desktop parity).
            inference_domains=_inference_domains(),
        )
    )


def _inference_domains() -> tuple[str, ...]:
    """Best-effort inference-domain whitelist; empty means deny-all."""
    try:
        from runtime.safety.sandboxing.sandbox import inference_domains

        return inference_domains()
    except Exception:  # noqa: BLE001 - best-effort; empty means deny-all
        return ()


__all__ = [
    "ToolEdgeEvent",
    "ToolEdgeHookConfig",
    "ToolEdgeHookOutcome",
    "ToolEdgeHookRunner",
    "ToolEdgeHookSpec",
    "load_tool_edge_hook_config",
    "post_write_diagnostic_record",
    "post_write_diagnostics",
    "post_write_regression_matrix",
    "regression_matrix_for_path",
]
