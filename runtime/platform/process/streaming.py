"""Subprocess streaming helper.

Spawn a child process, stream stdout/stderr line-by-line as they arrive,
forward each line to the active ``tool_output_sink`` so the react-loop
can push ``commandExecution/outputDelta`` events to the client. The
return shape mirrors the old ``subprocess.run(capture_output=True)``
dict so callers can drop this in without restructuring.

Used by:
    * Mantles (docker / k8s / ssh CLI) — ``runtime.sensing.server.*``
    * Long-running skills (exec_shell, git, quality checks, verify) —
      ``runtime.execution.suckers.*``

Skills don't depend on the sensing/mantle layer, so this lives at the
platform tier where both can import it without crossing layers.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

_EXECUTION_POLICY_SCHEMA = "echo.execution_policy.v1"


def execution_policy_result_snapshot(
    *,
    status: str,
    exit_code: int | None = None,
    timed_out: bool = False,
    cancelled: bool = False,
    killed: bool = False,
    stdout_truncated: bool = False,
    stderr_truncated: bool = False,
    duration_ms: int | None = None,
    error_type: str | None = None,
) -> dict[str, Any]:
    """Stable, content-free subprocess outcome summary for audit/replay."""
    result = {
        "status": str(status or "unknown"),
        "exit_code": exit_code,
        "timed_out": bool(timed_out),
        "cancelled": bool(cancelled),
        "killed": bool(killed),
        "stdout_truncated": bool(stdout_truncated),
        "stderr_truncated": bool(stderr_truncated),
        "output_truncated": bool(stdout_truncated or stderr_truncated),
    }
    if duration_ms is not None:
        result["duration_ms"] = max(0, int(duration_ms))
    clean_error_type = str(error_type or "").strip()
    if clean_error_type:
        result["error_type"] = clean_error_type
    return result


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_nonnegative_int(value: Any, default: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _append_capped_utf8(
    parts: list[str],
    chunk: str,
    *,
    cap_bytes: int,
    state: dict[str, Any],
) -> None:
    if state["bytes"] >= cap_bytes:
        state["truncated"] = True
        return
    chunk_bytes = chunk.encode("utf-8", errors="replace")
    remaining = cap_bytes - int(state["bytes"])
    if len(chunk_bytes) <= remaining:
        parts.append(chunk)
        state["bytes"] += len(chunk_bytes)
        return
    state["truncated"] = True
    if remaining > 0:
        truncated = chunk_bytes[:remaining].decode("utf-8", errors="ignore")
        if truncated:
            parts.append(truncated)
    state["bytes"] = cap_bytes


def _sandbox_extra_env(env: Mapping[str, str] | None) -> dict[str, str]:
    """Return only caller-supplied env overrides for sandboxed subprocesses.

    Some legacy callers pass ``{**os.environ, **overrides}`` into
    ``stream_run``. Feeding that whole dict into ``SandboxPolicy.extra_env``
    would re-add secrets after the policy allow-list stripped them. Treat
    values identical to the current process env as inherited noise and keep
    only real overrides.
    """
    if not env:
        return {}
    extra: dict[str, str] = {}
    for key, value in env.items():
        key_s = str(key)
        value_s = str(value)
        if os.environ.get(key_s) != value_s:
            extra[key_s] = value_s
    return extra


def _inference_domains_for_policy(allow_network: bool) -> tuple[str, ...]:
    """Inference-domain whitelist for a SandboxPolicy, when network is denied.

    A network-*allowed* sandbox needs no whitelist (everything reachable), so
    skip the file read entirely. Only a network-denied sandbox enumerates the
    model endpoints it must still reach.
    """
    if allow_network:
        return ()
    try:
        from runtime.safety.sandboxing.sandbox import inference_domains

        return inference_domains()
    except Exception:  # noqa: BLE001 - best-effort; empty means deny-all
        return ()


def _egress_domains_for_policy(allow_network: bool, egress_allow_common: bool) -> tuple[str, ...]:
    """Host allowlist for a network-denied SandboxPolicy.

    ``allow_network=True`` needs nothing (everything reachable). Otherwise the
    inference endpoints always stay allowed; the "common domains" tier
    additionally pre-allows the bundled dev-tool registries/mirrors.
    """
    if allow_network:
        return ()
    inference = _inference_domains_for_policy(False)
    if not egress_allow_common:
        return inference
    try:
        from runtime.safety.sandboxing.sandbox import default_egress_domains

        merged = list(inference)
        for domain in default_egress_domains():
            if domain not in merged:
                merged.append(domain)
        return tuple(merged)
    except Exception:  # noqa: BLE001 - best-effort
        return inference


def execution_policy_snapshot(
    *,
    sandbox_requested: bool,
    workspace: str | None,
    cwd: str | None,
    backend: str,
    hard: bool,
    allow_network: bool,
    env_mode: str,
    process_group: bool,
    timeout_s: float | None,
    result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Stable audit shape for subprocess policy decisions."""
    snapshot = {
        "schema": _EXECUTION_POLICY_SCHEMA,
        "sandbox_requested": sandbox_requested,
        "workspace": workspace,
        "cwd": cwd,
        "backend": backend,
        "hard": hard,
        "allow_network": allow_network,
        "env_mode": env_mode,
        "process_group": process_group,
        "process_tree_kill": process_group,
        "timeout_s": timeout_s,
    }
    if isinstance(result, Mapping):
        snapshot["result"] = execution_policy_result_snapshot(
            status=str(result.get("status") or "unknown"),
            exit_code=_optional_int(result.get("exit_code")),
            timed_out=bool(result.get("timed_out")),
            cancelled=bool(result.get("cancelled")),
            killed=bool(result.get("killed")),
            stdout_truncated=bool(result.get("stdout_truncated")),
            stderr_truncated=bool(result.get("stderr_truncated")),
            duration_ms=_optional_int(result.get("duration_ms")),
            error_type=str(result.get("error_type") or ""),
        )
    return snapshot


def stream_run(
    argv: list[str],
    *,
    input_data: str | None = None,
    timeout: float,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    output_cap_bytes: int = 200_000,
    on_timeout: Callable[[subprocess.Popen[str]], None] | None = None,
    sandbox_dir: str | None = None,
    allow_network: bool = False,
    egress_allow_common: bool = False,
    sandbox_required: bool = False,
    approval_provider: Any | None = None,
    thread_id: str = "",
) -> dict[str, Any]:
    """Run ``argv`` as a subprocess, streaming output as it arrives.

    Returns a dict with ``stdout``, ``stderr``, ``exit_code``,
    ``timed_out``, ``stdout_truncated``, ``stderr_truncated``. Callers
    are expected to merge in backend-specific keys (``container``,
    ``pod``, ``host``, etc.) on the returned dict.

    ``on_timeout`` is called after the process is killed, letting the
    caller run any backend-specific cleanup (e.g. ``docker kill``).
    """
    output_cap_bytes = _coerce_nonnegative_int(output_cap_bytes, 200_000)
    run_cwd = cwd
    run_env = dict(env) if env is not None else None
    sandbox_backend = "direct"
    sandbox_hard = False
    sandbox_workspace: str | None = None
    env_mode = "custom" if env is not None else "inherit"

    def _policy_snapshot(
        *,
        process_group: bool = False,
        result: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return execution_policy_snapshot(
            sandbox_requested=sandbox_dir is not None,
            workspace=sandbox_workspace,
            cwd=run_cwd,
            backend=sandbox_backend,
            hard=sandbox_hard,
            allow_network=allow_network,
            env_mode=env_mode,
            process_group=process_group,
            timeout_s=timeout,
            result=result,
        )

    from runtime.safety.sandboxing.sandbox import (
        SandboxViolation,
        effective_process_sandbox_mode,
        process_sandbox_required,
    )

    # High-risk callers (shell, git, quality checks and verification) opt in
    # to this gate.  It closes the most dangerous legacy path: a commercial
    # request reaching stream_run without the executor having injected a
    # workspace sandbox.  Remote backend wrappers intentionally do not opt
    # in because their argv is already a docker/kubectl/ssh control command.
    if sandbox_required and sandbox_dir is None and process_sandbox_required():
        return {
            "error": (
                "sandbox_violation: shared/commercial execution requires a "
                "workspace sandbox and hard process backend"
            ),
            "execution_policy": _policy_snapshot(
                result={"status": "sandbox_violation", "error_type": "sandbox_violation"}
            ),
        }

    if sandbox_dir is not None:
        from runtime.safety.sandboxing.sandbox import (
            SandboxPolicy,
            resolved_process_backend,
        )

        sandbox_root = Path(sandbox_dir).expanduser().resolve()
        sandbox_workspace = str(sandbox_root)
        if not sandbox_root.is_dir():
            return {
                "error": f"sandbox_violation: workspace not a directory: {sandbox_root}",
                "execution_policy": _policy_snapshot(
                    result={"status": "sandbox_violation", "error_type": "sandbox_violation"}
                ),
            }
        if cwd is None:
            run_cwd = str(sandbox_root)
        else:
            candidate = Path(cwd).expanduser()
            if not candidate.is_absolute():
                candidate = sandbox_root / candidate
            candidate = candidate.resolve()
            try:
                candidate.relative_to(sandbox_root)
            except ValueError:
                return {
                    "error": (
                        f"sandbox_violation: cwd {candidate} escapes workspace {sandbox_root}"
                    ),
                    "execution_policy": _policy_snapshot(
                        result={"status": "sandbox_violation", "error_type": "sandbox_violation"}
                    ),
                }
            if not candidate.is_dir():
                return {
                    "error": f"sandbox_violation: cwd is not a directory: {candidate}",
                    "execution_policy": _policy_snapshot(
                        result={"status": "sandbox_violation", "error_type": "sandbox_violation"}
                    ),
                }
            run_cwd = str(candidate)
        policy = SandboxPolicy(
            workspace=sandbox_root,
            allow_network=allow_network,
            timeout_s=timeout,
            extra_env=_sandbox_extra_env(env),
            # Model inference endpoints stay reachable even when the sandbox
            # is network-denied (Claude Desktop parity). Resolved lazily from
            # custom_models.json / ANTHROPIC_BASE_URL; empty for network-allowed
            # sandboxes. The "common domains" tier pre-allows dev-tool hosts.
            inference_domains=_egress_domains_for_policy(allow_network, egress_allow_common),
            egress_allow_common=egress_allow_common,
        )
        run_env = policy.env_for()
        env_mode = "allowlist"
        try:
            # Preserve the zero-argument seam used by embedders/tests when no
            # deployment override is configured.  Commercial/explicit modes
            # go through the mode-aware selector so they cannot downgrade.
            if os.environ.get("ECHO_PROCESS_SANDBOX") or os.environ.get("ECHO_DEPLOYMENT_MODE"):
                choice = resolved_process_backend(effective_process_sandbox_mode())
            else:
                choice = resolved_process_backend()
            if getattr(choice, "needs_approval", False):
                # A hard sandbox was requested but is not available on this
                # host. Do not silently run unconfined: when an approval
                # provider is available, ask a human to authorize the direct
                # (non-sandboxed) fallback; otherwise fail closed.
                if approval_provider is not None:
                    from runtime.safety.approval.approval_gate import ApprovalRequest

                    decision = approval_provider.request(
                        ApprovalRequest(
                            thread_id=thread_id,
                            tool_name="exec_shell",
                            tool_call_id="",
                            args_preview=" ".join(str(a) for a in argv),
                            detail="no hard sandbox backend is available; "
                            "authorizing the unconfined execution fallback",
                        ),
                        timeout=120.0,
                    )
                    if not decision.approved:
                        return {
                            "error": "sandbox_fallback_denied: unconfined "
                            "execution was not authorized",
                            "sandbox_backend": "direct",
                            "sandbox_hard": False,
                            "execution_policy": _policy_snapshot(
                                result={
                                    "status": "denied",
                                    "error_type": "sandbox_fallback_denied",
                                }
                            ),
                        }
                    # Authorized: fall through to the shared transform below;
                    # the direct backend then runs with explicit human consent.
                else:
                    return {
                        "error": "sandbox_fallback_needs_approval: no hard sandbox "
                        "backend is available and no approval provider is "
                        "configured; refuse unconfined execution",
                        "sandbox_backend": "direct",
                        "sandbox_hard": False,
                        "execution_policy": _policy_snapshot(
                            result={
                                "status": "needs_approval",
                                "error_type": "sandbox_fallback_needs_approval",
                            }
                        ),
                    }
            run_argv, run_env, transformed_cwd = choice.backend.transform(
                list(argv),
                run_env,
                Path(run_cwd),
                policy,
            )
        except SandboxViolation as exc:
            return {
                "error": f"sandbox_violation: {exc}",
                "execution_policy": _policy_snapshot(
                    result={"status": "sandbox_violation", "error_type": "sandbox_violation"}
                ),
            }
        argv = run_argv
        run_cwd = str(transformed_cwd)
        sandbox_backend = choice.name
        sandbox_hard = choice.hard

    started_at = time.monotonic()
    try:
        from runtime.platform.process.tree import (
            process_group_kwargs,
            terminate_process_tree,
        )

        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=run_cwd,
            env=run_env,
            bufsize=1,
            shell=False,
            **process_group_kwargs(),
        )
    except FileNotFoundError as e:
        return {
            "error": f"exec_failed: {e}",
            "execution_policy": _policy_snapshot(
                result={"status": "exec_failed", "error_type": "file_not_found"}
            ),
        }
    except OSError as e:
        return {
            "error": f"exec_failed: {e}",
            "execution_policy": _policy_snapshot(
                result={"status": "exec_failed", "error_type": type(e).__name__}
            ),
        }

    from runtime.platform.process import tool_output_sink
    from runtime.safety.approval.cancellation import current_cancellation_token

    # Capture the sink on the calling thread — reader threads below do
    # not inherit ContextVars.
    sink = tool_output_sink.current_sink()
    cancel_token = current_cancellation_token()

    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    stream_state: dict[str, dict[str, Any]] = {
        "stdout": {"bytes": 0, "truncated": False},
        "stderr": {"bytes": 0, "truncated": False},
    }

    def _reader(stream: Any, parts: list[str], kind: str) -> None:
        try:
            for line in iter(stream.readline, ""):
                if not line:
                    break
                _append_capped_utf8(
                    parts,
                    line,
                    cap_bytes=output_cap_bytes,
                    state=stream_state[kind],
                )
                if sink is not None:
                    with contextlib.suppress(Exception):
                        sink(kind, line)  # type: ignore[arg-type]
        finally:
            with contextlib.suppress(Exception):
                stream.close()

    t_out = threading.Thread(
        target=_reader,
        args=(proc.stdout, stdout_parts, "stdout"),
        daemon=True,
    )
    t_err = threading.Thread(
        target=_reader,
        args=(proc.stderr, stderr_parts, "stderr"),
        daemon=True,
    )
    t_out.start()
    t_err.start()

    if input_data is not None and proc.stdin is not None:
        with contextlib.suppress(BrokenPipeError, OSError):
            proc.stdin.write(input_data)
        with contextlib.suppress(Exception):
            proc.stdin.close()

    timed_out = False
    cancelled = False
    killed = False
    deadline = time.monotonic() + timeout
    try:
        while True:
            if cancel_token.is_cancelled:
                cancelled = True
                killed = terminate_process_tree(proc)
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                killed = terminate_process_tree(proc)
                if on_timeout is not None:
                    with contextlib.suppress(Exception):
                        on_timeout(proc)
                break
            try:
                # Short wait slice so we can poll the cancel token
                # roughly every 100ms. Choosing too small wastes CPU;
                # too large delays responding to a stop click.
                proc.wait(timeout=min(remaining, 0.1))
                break
            except subprocess.TimeoutExpired:
                continue
    finally:
        t_out.join(timeout=2.0)
        t_err.join(timeout=2.0)

    raw_stdout = "".join(stdout_parts)
    raw_stderr = "".join(stderr_parts)
    duration_ms = int((time.monotonic() - started_at) * 1000)
    stdout_truncated = bool(stream_state["stdout"]["truncated"])
    stderr_truncated = bool(stream_state["stderr"]["truncated"])
    exit_code = None if (timed_out or cancelled) else proc.returncode
    status = "completed"
    if timed_out:
        status = "timed_out"
    elif cancelled:
        status = "cancelled"
    elif exit_code not in {0, None}:
        status = "failed"
    result_snapshot = {
        "status": status,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "timed_out": timed_out,
        "cancelled": cancelled,
        "killed": killed,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
    }
    return {
        "stdout": raw_stdout,
        "stderr": raw_stderr,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "timed_out": timed_out,
        "cancelled": cancelled,
        "killed": killed,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
        "sandbox_backend": sandbox_backend,
        "sandbox_hard": sandbox_hard,
        "execution_policy": _policy_snapshot(process_group=True, result=result_snapshot),
    }
