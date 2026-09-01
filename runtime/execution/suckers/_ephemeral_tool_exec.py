"""Tool execution helpers for ephemeral sub-agent runs.

Split out from ``ephemeral_runner.py`` to keep that module under the
god-file line cap. Pure structural move — no behavior changes.

Contains:
    * ``_ephemeral_write_confine_block`` — scopes a sub-agent's filesystem
      tools to a locked worktree (replicates the executor's sandbox-arg
      injector for the ephemeral direct-dispatch path).
    * ``_execute_tool_in_subagent`` — runs one tool_use call inside an
      ephemeral sub-agent (mirrors ``tool_bridge._execute_tool_call`` but
      skips ``stack.executor`` so the sub-agent sees the SHARED session).
"""

from __future__ import annotations

from typing import Any

from runtime.execution.suckers.ephemeral_injection_gate import (
    ephemeral_injection_taint_block,
    scan_and_escalate_ephemeral_taint,
)
from runtime.execution.suckers.layers import EPHEMERAL_MEMORY_SKILLS

__all__ = [
    "_ephemeral_write_confine_block",
    "_execute_tool_in_subagent",
]


def _ephemeral_write_confine_block(call: Any, skill: Any) -> str | None:
    """Scope a sub-agent's filesystem tools to a locked worktree.

    Ephemeral runs bypass the executor's sandbox-arg injector, so a write skill
    would otherwise run with ``sandbox_dir=None`` (no confinement → it can write
    anywhere, verified live). When the Session pins ``_locked_write_root`` (set
    by ``call_subagent(workspace_path=...)``), we replicate the injector here:
    inject the locked root as ``sandbox_dir`` so the skill's own ``check_path``
    confines relative writes into the worktree and blocks escapes. A write skill
    that can't take a sandbox_dir is blocked (fail-closed) rather than allowed to
    escape. Shell/exec-class tools are blocked outright (audit F-02) — a cwd
    nudge is not a sandbox for a command interpreter. Returns a block message,
    or None to proceed.
    """
    from runtime.platform.process.session import current_session

    meta = getattr(current_session(), "metadata", None) or {}
    locked = meta.get("_locked_write_root")
    if not locked:
        return None
    name = (getattr(call, "name", "") or "").lower()
    affinity = [str(a).lower() for a in (getattr(skill, "affinity", None) or [])]
    call_input = getattr(call, "input", None)
    args = call_input if isinstance(call_input, dict) else {}
    # Audit F-02: shell/exec-class tools cannot be confined to the locked
    # worktree — injecting ``cwd`` is a nudge, not a sandbox (the command
    # can cd anywhere and write straight into the main tree). Fail closed
    # inside an isolated spawn instead of letting the sub-agent escape.
    shell_affinity = any(a in ("shell", "exec") for a in affinity)
    shell_name = any(tok in name for tok in ("exec_shell", "background_exec", "run_command"))
    if shell_affinity or shell_name:
        return (
            f"(blocked: '{getattr(call, 'name', '?')}' is a shell/exec tool and "
            f"cannot be confined to the locked worktree — isolated spawns run "
            f"without shell access. Do not retry shell tools here; use the "
            f"read-only retrieval tools instead: read_file, read_file_range, "
            f"grep_text, glob_files, list_cwd, tree, code_search. If a command "
            f"must really run, report it as a finding for the parent session.)"
        )
    path_payload = any(key in args for key in ("path", "file_path", "filepath", "root", "patch"))
    filesystem_affinity = any(
        a in ("file", "io", "filesystem", "file-read", "file-write", "write", "edit")
        for a in affinity
    )
    filesystem_name = name in {
        "list_cwd",
        "read_file",
        "file_stats",
        "glob_files",
        "grep_text",
        "tree",
        "read_file_range",
    } or (
        path_payload and any(tok in name for tok in ("write", "edit", "patch", "create", "append"))
    )
    if not (filesystem_affinity or filesystem_name):
        # Logical state writers such as bb_write / todo_write are not file
        # operations and must remain usable inside a locked worktree.
        return None
    try:
        import inspect

        params = inspect.signature(skill.handler).parameters
    except (TypeError, ValueError):
        params = {}
    if isinstance(call_input, dict):
        if "cwd" in params and not call_input.get("cwd"):
            call_input["cwd"] = str(locked)
        if "sandbox_dir" in params and not call_input.get("sandbox_dir"):
            call_input["sandbox_dir"] = str(locked)
        if "root" in params:
            from pathlib import Path

            root = str(call_input.get("root") or ".")
            if not Path(root).is_absolute():
                call_input["root"] = str(Path(str(locked)) / root)

    is_write = any(tok in name for tok in ("write", "edit", "patch", "create", "append")) or any(
        a in ("write", "edit", "file-write") for a in affinity
    )
    if not is_write:
        return None
    if "sandbox_dir" not in params:
        return (
            f"(blocked: '{getattr(call, 'name', '?')}' can't be confined to the "
            f"locked worktree — refusing to write unsandboxed)"
        )
    return None


def _execute_tool_in_subagent(
    registry: Any,
    call: Any,
) -> tuple[str, bool]:
    """Run one tool_use call inside an ephemeral sub-agent.

    Mirrors the simpler shape of ``tool_bridge._execute_tool_call``
    but doesn't go through ``stack.executor`` — sub-agents already
    inherit the parent's Session via ContextVar, and we want their
    skill calls to see the SHARED ``current_session()`` so blackboard
    + memory skills resolve to the parent's turn_id / agent. Going
    through executor would re-set Session and break this.

    Returns ``(output_text, is_error)``.
    """
    import json

    try:
        if not registry.has(call.name):
            return (f"(skill not found: {call.name})", True)
        skill = registry.get(call.name)
    except Exception as exc:  # noqa: BLE001
        return (f"(registry error: {exc})", True)

    # Injection-taint gate — ephemeral runs bypass the executor chokepoint, so
    # enforce here, fail-closed (block, since there's no approval channel).
    _taint_block = ephemeral_injection_taint_block(call, call.name)
    if _taint_block is not None:
        return (_taint_block, True)

    # Memory / SOUL skills require a bound Session; ephemeral sub-agents run
    # without one (current_session() is not propagated into the dispatch thread),
    # so calling them raises RuntimeError and surfaces as a failed tool call the
    # model keeps retrying. Block with a clean error instead — a sub-agent must
    # not mutate the parent agent's durable memory anyway. This is the ultimate
    # fallback: advertised-list stripping (ephemeral_agents) is the polite layer.
    if str(call.name) in EPHEMERAL_MEMORY_SKILLS:
        return (
            "(unavailable: long-term memory / SOUL skills (remember, recall, "
            "note_user, diary_write, and the self-evolution tools) are disabled "
            "inside sub-agents — a sub-agent runs without a bound Session and "
            "must not mutate the parent agent's durable memory. Report any "
            "memory-worthy fact as a finding for the parent session instead.)",
            True,
        )

    _confine_block = _ephemeral_write_confine_block(call, skill)
    if _confine_block is not None:
        return (_confine_block, True)

    # The mini-loop intentionally bypasses ``executor.execute_step`` so child
    # memory/blackboard calls stay on the shared Session.  It must still reuse
    # the executor's Session-derived path preparation: otherwise relative
    # ``read_file`` / ``list_cwd`` calls fall back to the server process cwd.
    # That made personal-workspace children search the entire repository even
    # though the parent had a precise artifact root.  Keep the direct dispatch,
    # but apply the same trusted cwd/sandbox injection before the safety gate.
    try:
        from runtime.platform.process.session import current_session

        _scope_meta = getattr(current_session(), "metadata", None) or {}
    except (ImportError, AttributeError, LookupError):
        _scope_meta = {}
    if isinstance(getattr(call, "input", None), dict) and not _scope_meta.get("_locked_write_root"):
        from runtime.execution.tool_engine._executor_helpers import (
            _prepare_scoped_args,
        )
        from runtime.platform.models import SkillId

        try:
            _scoped_input = _prepare_scoped_args(
                skill,
                SkillId(str(call.name)),
                dict(call.input),
            )
        except PermissionError as exc:
            return (f"(blocked: {exc})", True)
        call.input.clear()
        call.input.update(_scoped_input)

    # Direct-dispatch hardening — ephemeral runs bypass the executor
    # chokepoint, so apply the same pre-execution safety gates here (SEC-1/2).
    if isinstance(getattr(call, "input", None), dict):
        from runtime.execution.tool_engine.skill_gate import gate_inner_dispatch
        from runtime.safety.auth import MODEL_FORBIDDEN_ARGS

        # Drop model-controllable privilege flags (allow_sensitive /
        # allow_private) the model must never set — same as the executor.
        # ``call`` is a frozen model, so mutate the input dict in place rather
        # than rebinding the attribute (cf. the sandbox_dir injection above).
        for _forbidden in MODEL_FORBIDDEN_ARGS:
            call.input.pop(_forbidden, None)
        # Capability denylist + immunity (when a TrustEngine is ambiently
        # bound) + credential-file denylist (check_file_write). Without these
        # an unsandboxed sub-agent could write ./.env / ./id_rsa, or run an
        # operator-disabled tool — the executor blocks both. Reuse the shared
        # primitive the other direct-dispatch points already use so this path
        # stays in lock-step with the executor instead of re-implementing gates.
        _gate = gate_inner_dispatch(skill, call.input, caller="ephemeral_subagent")
        if _gate is not None:
            return (f"(blocked: {_gate.message})", True)

    try:
        output = skill.handler(**call.input)
    except TypeError as exc:
        return (f"(TypeError: {exc})", True)
    except Exception as exc:  # noqa: BLE001
        return (f"(skill error: {type(exc).__name__}: {exc})", True)

    output_is_error = isinstance(output, dict) and (
        output.get("ok") is False or output.get("success") is False
    )
    if isinstance(output, str):
        rendered = output
    else:
        try:
            rendered = json.dumps(output, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            rendered = repr(output)

    # If this tool ingested untrusted content carrying injection markers,
    # escalate the turn taint so a LATER risky tool in the same ephemeral run
    # is gated too — mirrors the executor chokepoint's post-success scan.
    scan_and_escalate_ephemeral_taint(
        call.name,
        getattr(skill, "affinity", None),
        rendered,
    )
    # Same 4kB cap as parent agentic loop · keeps sub-agent context
    # from blowing up on a single huge tool result (e.g. a full-page
    # web_search output).
    if len(rendered) > 4000:
        rendered = rendered[:4000] + f"\n\n...(truncated, {len(rendered) - 4000} more chars)"
    return (rendered, output_is_error)
