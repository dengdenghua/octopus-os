"""Shell execution skills for write_skills · extracted from write_skills.py.

Contains ``exec_shell`` / ``background_exec`` / ``read_background_output`` /
``kill_background_exec`` (and their shell aliases) and ``ipython``.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

from runtime.safety.env_scrub import scrub_credential_env as _scrub_unconfined_env

from ._write_skills_background import (
    _BACKGROUND_PROCESSES,
    _background_execution_policy,
    _background_paths,
    _BackgroundProcess,
    _prune_finished_background_processes,
    _read_background_metadata,
    _snapshot_background_metadata,
    _sweep_background_dirs,
    _write_background_metadata,
    background_process_identity_matches,
)
from ._write_skills_common import (
    _DEFAULT_EXEC_TIMEOUT_S,
    _EXEC_OUTPUT_CAP,
    _ensure_sandbox,
    _error_with_execution_policy,
    _execution_policy_from_result,
    _parse_command,
)

# Read-only git subcommands. exec_shell is classified as a mutating tool
# (affinity ``exec``/``dangerous``), so in sandbox mode its explicit ``cwd``
# is confined to the sandbox workdir and a ``git status`` aimed at the
# workspace root fails with ``path_escapes_sandbox``. The git READ skills
# (git_status/git_diff/git_log) already run at the workspace root in sandbox
# mode — their affinity is ``read``, so the executor injects the workspace
# as their sandbox. A raw read-only ``git`` command via exec_shell should get
# the same treatment: rewrite it to ``git -C <root> …`` so the process itself
# stays sandbox-confined (writes outside the workdir are denied by the
# backend) while git inspects the repo the agent pointed at.
#
# Misclassification is fail-safe: a subcommand not in this set is left alone
# (stays confined to the workdir); a read-looking name whose git alias mutates
# still gets its writes denied by the sandbox backend, which is the real
# security boundary here.
_READ_ONLY_GIT_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "status",
        "diff",
        "log",
        "show",
        "blame",
        "shortlog",
        "whatchanged",
        "grep",
        "ls-files",
        "ls-tree",
        "rev-parse",
        "rev-list",
        "for-each-ref",
        "show-ref",
        "diff-index",
        "diff-tree",
        "name-rev",
        "merge-base",
        "describe",
        "count-objects",
        "check-ignore",
        "check-ref-format",
        "check-attr",
    }
)


def _is_read_only_git_argv(argv: list[str]) -> bool:
    """True when ``argv`` is ``git <read-only subcommand>``.

    Tolerates git's global options before the subcommand: ``-C <dir>`` /
    ``-c <k=v>`` (value-taking) and bare flags like ``--no-pager`` /
    ``--paginate``. Unknown subcommands return False, so they stay confined.
    """
    if not argv or argv[0] not in {"git", "git.exe"}:
        return False
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok in {"-C", "-c"} and i + 1 < len(argv):
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        return tok in _READ_ONLY_GIT_SUBCOMMANDS
    return False


def _read_only_git_rewrite(
    argv: list[str],
    cwd: str | None,
    sandbox_dir: str | None,
) -> tuple[list[str], str | None] | None:
    """Rewrites a read-only ``git`` command aimed outside the sandbox.

    When a read-only ``git`` command requests a ``cwd`` outside the sandbox
    workdir (the workspace root, in sandbox mode), returns
    ``(["git", "-C", <root>, …], None)`` — the process then runs at the
    sandbox root while git's working directory is the requested root, so
    sandboxed inspection of the real repo works without relaxing write
    confinement. Returns ``None`` when no rewrite applies (not read-only git,
    no sandbox, or the cwd is already inside the sandbox).
    """
    if not sandbox_dir or not cwd or not _is_read_only_git_argv(argv):
        return None
    try:
        root = Path(cwd).expanduser()
        if not root.is_absolute():
            root = Path(sandbox_dir).expanduser() / root
        root = root.resolve(strict=False)
        work = Path(sandbox_dir).expanduser().resolve(strict=False)
    except (OSError, ValueError):
        return None
    if root == work or root.is_relative_to(work):
        # Already inside the sandbox — no rewrite needed.
        return None
    return ["git", "-C", str(root), *argv[1:]], None


def _exec_shell(
    command: str | list[str] = "",
    *,
    cwd: str | None = None,
    timeout_s: float = _DEFAULT_EXEC_TIMEOUT_S,
    env: dict[str, str] | None = None,
    sandbox_dir: str | None = None,
    run_in_background: bool = False,
    background: bool = False,
    allow_network: bool | None = None,
    egress_allow_common: bool | None = None,
    **_kw: Any,
) -> dict[str, Any]:
    if run_in_background or background:
        return _background_exec(
            command=command,
            cwd=cwd,
            env=env,
            sandbox_dir=sandbox_dir,
            allow_network=allow_network,
            egress_allow_common=egress_allow_common,
        )

    argv, parse_error = _parse_command(command)
    if parse_error:
        return {"error": parse_error}
    assert argv is not None

    # Read-only git inspection may target the workspace root, which in
    # sandbox mode sits outside the sandbox workdir (path_escapes_sandbox
    # otherwise). Rewrite to ``git -C <root> …`` so the process stays
    # sandbox-confined while git inspects the requested root.
    if cwd is not None:
        rewritten = _read_only_git_rewrite(argv, cwd, sandbox_dir)
        if rewritten is not None:
            argv, cwd = rewritten

    if cwd is not None:
        resolved_cwd, err = _ensure_sandbox(cwd, sandbox_dir)
        if err:
            return {"error": err}
        if not resolved_cwd.is_dir():
            return {"error": f"cwd not a directory: {resolved_cwd}"}
        cwd_str = str(resolved_cwd)
    elif sandbox_dir is not None:
        sandbox_root = Path(sandbox_dir).expanduser().resolve()
        if not sandbox_root.is_dir():
            return {"error": f"sandbox_violation: workspace not a directory: {sandbox_root}"}
        cwd_str = str(sandbox_root)
    else:
        cwd_str = None

    run_env = None
    if sandbox_dir is not None:
        # Confined exec: the sandbox backend (in ``stream_run``) owns the
        # environment. When the caller supplies an explicit env we pass
        # only that; otherwise leave ``run_env`` None so the backend
        # builds its allowlisted env.
        if env is not None:
            run_env = {str(k): str(v) for k, v in env.items()}
    else:
        # UNCONFINED exec (no sandbox_dir): never hand the child our full
        # os.environ — a model-driven shell on the compat-gateway path
        # (no bound Session) could echo $ANTHROPIC_API_KEY & friends.
        # Start from a credential-scrubbed copy and lay any explicit
        # caller env on top.
        run_env = _scrub_unconfined_env(env)

    from runtime.platform.process.streaming import stream_run

    r = stream_run(
        argv,
        cwd=cwd_str,
        env=run_env,
        timeout=timeout_s,
        output_cap_bytes=_EXEC_OUTPUT_CAP,
        sandbox_dir=sandbox_dir,
        sandbox_required=True,
        allow_network=_resolved_allow_network(allow_network),
        egress_allow_common=_resolved_egress_allow_common(egress_allow_common),
    )
    if "error" in r and "exit_code" not in r:
        msg = r["error"]
        if "FileNotFoundError" in msg or "not found" in msg.lower():
            return _error_with_execution_policy(f"command not found: {msg}", r, argv=argv)
        return _error_with_execution_policy(f"exec_failed: {msg}", r, argv=argv)
    if r.get("timed_out"):
        return {
            "error": f"timeout after {timeout_s}s",
            "timed_out": True,
            "argv": argv,
            "stdout": r["stdout"],
            "stderr": r["stderr"],
            "execution_policy": _execution_policy_from_result(r),
        }
    return {
        "argv": argv,
        "exit_code": r["exit_code"],
        "stdout": r["stdout"],
        "stderr": r["stderr"],
        "stdout_truncated": r["stdout_truncated"],
        "stderr_truncated": r["stderr_truncated"],
        "sandbox_backend": r.get("sandbox_backend", "direct"),
        "sandbox_hard": bool(r.get("sandbox_hard")),
        "execution_policy": _execution_policy_from_result(r),
    }


def _resolved_allow_network(explicit: bool | None) -> bool:
    """Resolve the effective ``allow_network`` for a shell exec.

    Precedence:
      1. Explicit caller value (tool arg ``allow_network``) — wins.
      2. The bound Session's declared ``sandbox_policy.networkAccess`` —
         the turn explicitly opted into network access.
      3. Fallback ``False`` — sandbox default is network DENIED.

    ``scope.network_policy`` is deliberately NOT consulted: it defaults to
    "allow" outside plan mode (it governs browser/remote-exec surfaces, not
    the confined shell), so reading it here would flip the default from
    denied to allowed and effectively escape the sandbox's network policy.
    """
    if explicit is not None:
        return bool(explicit)
    try:
        from runtime.platform.process.session import current_session

        sess = current_session()
        if sess is None:
            return False
        policy = (sess.metadata or {}).get("sandbox_policy")
        if isinstance(policy, dict):
            return bool(policy.get("networkAccess") or policy.get("network_access"))
        return False
    except Exception:  # noqa: BLE001 - best-effort; sandbox default is deny
        return False


def _resolved_egress_allow_common(explicit: bool | None) -> bool:
    """Resolve the "common domains" network tier for a shell exec.

    When the sandbox is network-denied, this tier pre-allows the bundled
    dev-tool registries/mirrors (npm / pip / git / apt / rust / go). The
    user picks it from the sandbox settings page ("常用域名"), never by
    hand-maintaining hosts.

    Precedence:
      1. Explicit caller value (tool arg) — wins.
      2. The bound Session's declared ``sandbox_policy.egressAllowCommon``.
      3. Fallback ``False`` — a denied sandbox allows only inference.
    """
    if explicit is not None:
        return bool(explicit)
    try:
        from runtime.platform.process.session import current_session

        sess = current_session()
        if sess is None:
            return False
        policy = (sess.metadata or {}).get("sandbox_policy")
        if isinstance(policy, dict):
            return bool(policy.get("egressAllowCommon") or policy.get("egress_allow_common"))
        return False
    except Exception:  # noqa: BLE001 - best-effort; deny stays deny
        return False


def _inference_domains() -> tuple[str, ...]:
    """Model inference endpoints that stay reachable in a network-denied
    sandbox (Claude Desktop parity). Best-effort; empty means deny-all."""
    try:
        from runtime.safety.sandboxing.sandbox import inference_domains

        return inference_domains()
    except Exception:  # noqa: BLE001 - best-effort; empty means deny-all
        return ()


def _background_exec(
    command: str | list[str] = "",
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    sandbox_dir: str | None = None,
    allow_network: bool | None = None,
    egress_allow_common: bool | None = None,
    **_kw: Any,
) -> dict[str, Any]:
    argv, parse_error = _parse_command(command)
    if parse_error:
        return {"error": parse_error}
    assert argv is not None

    # Opportunistic housekeeping on every new background task: bound the
    # in-memory registry and sweep terminal task dirs past their TTL.
    with contextlib.suppress(Exception):
        _prune_finished_background_processes()
        _sweep_background_dirs()

    # Cap simultaneously running tasks so a runaway agent cannot fork-bomb
    # the host via repeated background_exec calls.
    from ._write_skills_background import _BACKGROUND_MAX_CONCURRENT

    running_ids = [tid for tid, bg in _BACKGROUND_PROCESSES.items() if bg.proc.poll() is None]
    if len(running_ids) >= _BACKGROUND_MAX_CONCURRENT:
        return {
            "error": (
                f"too_many_background_tasks: {len(running_ids)} running, "
                f"max {_BACKGROUND_MAX_CONCURRENT}; kill one before starting more"
            ),
            "running_task_ids": running_ids,
            "argv": argv,
        }

    from runtime.safety.sandboxing.sandbox import process_sandbox_required

    if sandbox_dir is None and process_sandbox_required():
        from runtime.platform.process.streaming import execution_policy_snapshot

        return {
            "error": (
                "sandbox_violation: shared/commercial background execution "
                "requires a workspace sandbox and hard process backend"
            ),
            "argv": argv,
            "execution_policy": execution_policy_snapshot(
                sandbox_requested=False,
                workspace=None,
                cwd=cwd,
                backend="direct",
                hard=False,
                allow_network=False,
                env_mode="scrubbed",
                process_group=True,
                timeout_s=None,
                result={
                    "status": "sandbox_violation",
                    "error_type": "sandbox_violation",
                },
            ),
        }

    if cwd is not None:
        resolved_cwd, err = _ensure_sandbox(cwd, sandbox_dir)
        if err:
            return {"error": err}
        if not resolved_cwd.is_dir():
            return {"error": f"cwd not a directory: {resolved_cwd}"}
        cwd_str = str(resolved_cwd)
    elif sandbox_dir is not None:
        sandbox_root = Path(sandbox_dir).expanduser().resolve()
        if not sandbox_root.is_dir():
            return {"error": f"sandbox_violation: workspace not a directory: {sandbox_root}"}
        cwd_str = str(sandbox_root)
    else:
        cwd_str = None

    run_env = None
    sandbox_backend = "direct"
    sandbox_hard = False
    sandbox_workspace: str | None = None
    env_mode = "custom" if env is not None else "inherit"
    if sandbox_dir is not None:
        from runtime.platform.process.streaming import _sandbox_extra_env
        from runtime.safety.sandboxing.sandbox import (
            SandboxPolicy,
            SandboxViolation,
            effective_process_sandbox_mode,
            resolved_process_backend,
        )

        sandbox_root = Path(sandbox_dir).expanduser().resolve()
        sandbox_workspace = str(sandbox_root)
        resolved_network = _resolved_allow_network(allow_network)
        resolved_common = _resolved_egress_allow_common(egress_allow_common)
        policy = SandboxPolicy(
            workspace=sandbox_root,
            allow_network=resolved_network,
            extra_env=_sandbox_extra_env(env),
            # Model inference endpoints stay reachable even when the sandbox
            # is network-denied (Claude Desktop parity); the "common domains"
            # tier additionally pre-allows dev-tool registries/mirrors.
            inference_domains=(() if resolved_network else _inference_domains()),
            egress_allow_common=resolved_common,
        )
        run_env = policy.env_for()
        env_mode = "allowlist"
        try:
            if os.environ.get("ECHO_PROCESS_SANDBOX") or os.environ.get("ECHO_DEPLOYMENT_MODE"):
                choice = resolved_process_backend(effective_process_sandbox_mode())
            else:
                choice = resolved_process_backend()
            argv, run_env, transformed_cwd = choice.backend.transform(
                list(argv),
                run_env,
                Path(cwd_str),
                policy,
            )
        except SandboxViolation as exc:
            return {
                "error": f"sandbox_violation: {exc}",
                "argv": argv,
                "execution_policy": _background_execution_policy(
                    sandbox_requested=True,
                    sandbox_workspace=sandbox_workspace,
                    cwd=cwd_str,
                    sandbox_backend=sandbox_backend,
                    sandbox_hard=sandbox_hard,
                    env_mode=env_mode,
                ),
            }
        cwd_str = str(transformed_cwd)
        sandbox_backend = choice.name
        sandbox_hard = choice.hard
    else:
        # UNCONFINED background exec: scrub credential vars from the
        # inherited environment (see ``_exec_shell``), applied whether or
        # not the caller passed an explicit env.
        run_env = _scrub_unconfined_env(env)
        env_mode = "scrubbed"

    execution_policy = _background_execution_policy(
        sandbox_requested=sandbox_dir is not None,
        sandbox_workspace=sandbox_workspace,
        cwd=cwd_str,
        sandbox_backend=sandbox_backend,
        sandbox_hard=sandbox_hard,
        env_mode=env_mode,
    )

    task_id = f"bg_{uuid4().hex[:16]}"
    paths = _background_paths(task_id)
    try:
        from runtime.platform.process.tree import process_group_kwargs

        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd_str,
            env=run_env,
            bufsize=1,
            shell=False,
            **process_group_kwargs(),
        )
    except FileNotFoundError as e:
        return {"error": f"command not found: {e}", "argv": argv}
    except OSError as e:
        return {"error": f"exec_failed: {e}", "argv": argv}

    task = _BackgroundProcess(
        task_id=task_id,
        argv=argv,
        proc=proc,
        cwd=cwd_str,
        sandbox_backend=sandbox_backend,
        sandbox_hard=sandbox_hard,
        execution_policy=execution_policy,
        stdout_path=paths["stdout"],
        stderr_path=paths["stderr"],
        metadata_path=paths["metadata"],
    )
    _BACKGROUND_PROCESSES[task_id] = task
    snap = task.snapshot()
    snap["message"] = (
        "background process started; call read_background_output with task_id to poll output/status"
    )
    return snap


def _read_background_output(
    task_id: str = "",
    **_kw: Any,
) -> dict[str, Any]:
    if not task_id:
        return {"error": "missing task_id"}
    task = _BACKGROUND_PROCESSES.get(task_id)
    if task is None:
        metadata = _read_background_metadata(task_id)
        if metadata is None:
            return {"error": f"unknown task_id: {task_id}", "task_id": task_id}
        return _snapshot_background_metadata(metadata)
    return task.snapshot()


def _read_shell_output(
    task_id: str = "",
    **kw: Any,
) -> dict[str, Any]:
    return _read_background_output(task_id=task_id, **kw)


def _kill_background_exec(
    task_id: str = "",
    **_kw: Any,
) -> dict[str, Any]:
    if not task_id:
        return {"error": "missing task_id"}
    task = _BACKGROUND_PROCESSES.get(task_id)
    if task is None:
        metadata = _read_background_metadata(task_id)
        if metadata is None:
            return {"error": f"unknown task_id: {task_id}", "task_id": task_id}
        try:
            pid = int(metadata.get("pid") or 0)
        except (TypeError, ValueError):
            pid = 0
        if pid > 0:
            if not background_process_identity_matches(metadata):
                return {
                    "error": "process_identity_mismatch: refusing to kill a reused or foreign pid",
                    "task_id": task_id,
                    "status": "unknown",
                    "pid": pid,
                }
            from runtime.platform.process.tree import terminate_pid_tree

            with contextlib.suppress(Exception):
                terminate_pid_tree(pid)
        metadata["cancelled"] = True
        with contextlib.suppress(Exception):
            _write_background_metadata(_background_paths(task_id)["metadata"], metadata)
        return _snapshot_background_metadata(metadata)
    return task.kill()


def _kill_shell(
    task_id: str = "",
    **kw: Any,
) -> dict[str, Any]:
    return _kill_background_exec(task_id=task_id, **kw)


def _ipython(
    code: str = "",
    *,
    cwd: str | None = None,
    timeout_s: float = _DEFAULT_EXEC_TIMEOUT_S,
    sandbox_dir: str | None = None,
    **_kw: Any,
) -> dict[str, Any]:
    """Execute a Python snippet with the current interpreter."""
    if not code.strip():
        return {"error": "missing code"}

    cwd_str = None
    if cwd is not None:
        resolved_cwd, err = _ensure_sandbox(cwd, sandbox_dir)
        if err:
            return {"error": err}
        if not resolved_cwd.is_dir():
            return {"error": f"cwd not a directory: {resolved_cwd}"}
        cwd_str = str(resolved_cwd)

    from runtime.platform.process.streaming import stream_run

    r = stream_run(
        [sys.executable, "-c", code],
        cwd=cwd_str,
        timeout=timeout_s,
        output_cap_bytes=_EXEC_OUTPUT_CAP,
        sandbox_dir=sandbox_dir,
        sandbox_required=True,
    )
    if "error" in r and "exit_code" not in r:
        return _error_with_execution_policy(f"exec_failed: {r['error']}", r)
    if r.get("timed_out"):
        return {
            "error": f"timeout after {timeout_s}s",
            "timed_out": True,
            "stdout": r["stdout"],
            "stderr": r["stderr"],
            "execution_policy": _execution_policy_from_result(r),
        }
    return {
        "exit_code": r["exit_code"],
        "stdout": r["stdout"],
        "stderr": r["stderr"],
        "success": r["exit_code"] == 0,
        "stdout_truncated": r["stdout_truncated"],
        "stderr_truncated": r["stderr_truncated"],
        "sandbox_backend": r.get("sandbox_backend", "direct"),
        "sandbox_hard": bool(r.get("sandbox_hard")),
        "execution_policy": _execution_policy_from_result(r),
    }
