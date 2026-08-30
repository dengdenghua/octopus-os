from __future__ import annotations

import contextlib
import hashlib
import ipaddress
import shlex
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from runtime.execution.misc.file_write_leases import record_file_read_snapshot
from runtime.execution.suckers import Skill
from runtime.execution.tool_engine._executor_fileops import _extract_path
from runtime.execution.tool_engine.skill_gate import canonical_tool_path
from runtime.platform.models import (
    ArmId,
    Budget,
    CostEntry,
    ExecutionResult,
    ExecutionStatus,
    SkillId,
    Step,
    TaskId,
    ToolCall,
)
from runtime.safety.governance import ExecutionPolicyContext

_READ_BEFORE_WRITE_TOOLS = frozenset(
    {
        "write_text_file",
        "edit_text_file",
        "edit_file",
        "multi_edit_file",
        "documents.replace_text",
        "spreadsheets.update_cells",
        "presentations.replace_text",
    }
)
_FILE_READ_TRACKING_TOOLS = frozenset(
    {
        "read_file",
        "read_file_range",
        "documents.extract_text",
        "documents.docx_info",
        "spreadsheets.read_sheet",
        "spreadsheets.workbook_info",
        "presentations.extract_text",
        "presentations.presentation_info",
    }
)
_READ_TRACKING_KEY = "_read_file_paths_this_turn"
_TRANSIENT_ERROR_NAME_HINTS = (
    "Timeout",
    "Connection",
    "Network",
    "RateLimit",
    "Retry",
    "Temporary",
    "Transient",
)


def _restore_trusted_browser_loopback_access(
    sucker_id: SkillId,
    args: dict[str, Any],
    *,
    trusted_runtime_grant: bool = False,
) -> dict[str, Any]:
    """Authorize loopback navigation for trusted Browser/UI-regression sessions.

    Model-supplied ``allow_private`` remains stripped unconditionally.  This
    trusted runtime grant is reconstructed from session metadata and is
    limited to browser tools plus localhost/loopback destinations.  Code-mode
    browser regression is included without turning the whole task into a
    browser-only workflow; LAN and cloud-metadata addresses remain blocked.
    """
    if not str(sucker_id).startswith("browser_"):
        return args
    raw_url = args.get("url")
    if not isinstance(raw_url, str) or not raw_url.strip():
        return args
    try:
        from runtime.platform.process.session import current_session

        session = current_session()
        metadata = getattr(session, "metadata", None) if session is not None else None
    except (AttributeError, ImportError):
        metadata = None
    if not isinstance(metadata, dict):
        metadata = {}
    surfaces = metadata.get("runtime_surfaces")
    surface_names = (
        {str(item).strip().lower() for item in surfaces} if isinstance(surfaces, list) else set()
    )
    explicit_browser = bool(
        metadata.get("browser_operation_mode") is True
        or metadata.get("browser_regression_enabled") is True
        or str(metadata.get("browser_surface") or "").strip().lower() in {"browser", "chrome"}
        or {"browser", "chrome"} & surface_names
    )
    if not explicit_browser and not trusted_runtime_grant:
        return args
    host = urlparse(raw_url).hostname
    if not host:
        return args
    loopback = host.lower() == "localhost"
    if not loopback:
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False
    if not loopback:
        return args
    trusted_args = dict(args)
    trusted_args["allow_private"] = True
    return trusted_args


def _validate_output(output: dict, schema: dict) -> None:
    required = schema.get("required", [])
    properties = schema.get("properties", {})
    for key in required:
        if key not in output:
            raise ValueError(f"missing required field: {key}")
    for key, prop in properties.items():
        if key not in output:
            continue
        expected_type = prop.get("type")
        val = output[key]
        if expected_type == "string" and not isinstance(val, str):
            raise ValueError(f"field {key}: expected string, got {type(val).__name__}")
        if expected_type == "number" and not isinstance(val, (int, float)):
            raise ValueError(f"field {key}: expected number, got {type(val).__name__}")
        if expected_type == "boolean" and not isinstance(val, bool):
            raise ValueError(f"field {key}: expected boolean, got {type(val).__name__}")
        if expected_type == "array" and not isinstance(val, list):
            raise ValueError(f"field {key}: expected array, got {type(val).__name__}")
        if expected_type == "object" and not isinstance(val, dict):
            raise ValueError(f"field {key}: expected object, got {type(val).__name__}")


def _is_transient_tool_exception(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    name = type(exc).__name__
    return any(hint in name for hint in _TRANSIENT_ERROR_NAME_HINTS)


def _call_handler_with_transient_retry(
    handler: Any,
    args: dict[str, Any],
    *,
    allow_retry: bool = True,
    timeout_s: float | None = None,
) -> tuple[Any, list[str]]:
    """Call a tool handler with transient retry, optionally under a ceiling.

    Audit T-06: when ``timeout_s > 0`` the call runs on a worker thread and
    must finish within the ceiling — on expiry ``TimeoutError`` is raised and
    the executor maps it to ``status="timeout"``. A hung Python thread cannot
    be force-killed, so the worker is released (``shutdown(wait=False)``)
    and the caller proceeds instead of pinning its thread forever.
    """

    def _run() -> tuple[Any, list[str]]:
        try:
            return handler(**args), []
        except Exception as exc:  # noqa: BLE001 - retry classifier needs arbitrary skill exceptions
            if not allow_retry or not _is_transient_tool_exception(exc):
                raise
            retry_tag = f"transient_retry:{type(exc).__name__}"
            return handler(**args), [retry_tag]

    if not timeout_s or timeout_s <= 0:
        return _run()

    import concurrent.futures as _cf

    # A timed handler still belongs to the current turn/tool call. Copy the
    # ambient Session, cancellation, parent-tool id and safety ContextVars
    # into the timeout worker instead of silently turning it into an orphan.
    import contextvars as _contextvars

    pool = _cf.ThreadPoolExecutor(max_workers=1, thread_name_prefix="tool-timeout")
    fut = pool.submit(_contextvars.copy_context().run, _run)
    try:
        return fut.result(timeout=float(timeout_s))
    except _cf.TimeoutError:
        pool.shutdown(wait=False, cancel_futures=True)
        raise TimeoutError(f"tool handler exceeded its timeout ({timeout_s:g}s)") from None
    except BaseException:
        pool.shutdown(wait=False, cancel_futures=True)
        raise


def _prepare_scoped_args(
    skill: Skill,
    sucker_id: SkillId,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Opt-in Session injection + mode-gated workspace scope handling.

    * A handler that declares ``session`` gets the current Session
      injected (old handlers without the param keep working).
    * Directory-base params (``root``/``cwd``/…) and path params
      (``path``/``file_path``/…) default to — or are resolved against —
      the scope's primary root, so "analyze this folder" scans the
      workspace the user picked instead of the process CWD.
    * ``sandbox_dir`` participates in the permission domain: omitted →
      filled with the scope primary; supplied → PermissionError when it
      escapes the scope's allowed roots; plan mode (no write scope) →
      every write call rejected until ``exit_plan_mode``.

    Scope is only enforced when a Session is bound — direct callers
    (tests, programmatic use) keep the old "LLM chooses sandbox_dir"
    contract. Raises PermissionError; callers keep it inside the
    dispatch ``try`` so denials map to the standard except branch.
    """
    import inspect as _inspect

    handler_params: dict[str, Any] = {}
    try:
        sig = _inspect.signature(skill.handler)
        handler_params = dict(sig.parameters)
    except (TypeError, ValueError):
        handler_params = {}

    if "session" in handler_params and "session" not in args:
        from runtime.platform.process.session import current_session

        args = {**args, "session": current_session()}

    _root_params = (
        "root",
        "cwd",
        "working_dir",
        "directory",
        "base_dir",
        "repo_dir",
    )
    _path_params = ("path", "filepath", "file_path", "filename")
    has_sandbox = "sandbox_dir" in handler_params
    has_root_param = any(p in handler_params for p in _root_params)
    has_path_param = any(p in handler_params for p in _path_params)
    if not (has_sandbox or has_root_param or has_path_param):
        return args

    from runtime.platform.process.session import current_session

    _sess = current_session()
    if _sess is None:
        return args

    from runtime.platform.process.scope import resolve_execution_scope

    scope = resolve_execution_scope(_sess)
    read_primary = scope.primary_read
    _mutates_files = bool(set(skill.affinity or []) & {"write", "edit", "exec", "dangerous"})
    arg_primary = scope.primary_write if _mutates_files else read_primary

    # Read-side root injection — if the LLM didn't supply the root (or
    # supplied the meaningless "."), inject the scope primary so the
    # read scans the user's selected folder.
    if has_root_param and arg_primary is not None:
        for _rp in _root_params:
            if _rp not in handler_params:
                continue
            _supplied = args.get(_rp)
            if _supplied in (None, "", ".", "./"):
                args = {**args, _rp: str(arg_primary)}
                break

    # Path-param injection — relative paths resolve against the scope
    # primary instead of the process CWD.
    if has_path_param and arg_primary is not None:
        from runtime.safety.auth.path_guard import normalize_scoped_relative_path

        for _pp in _path_params:
            if _pp not in handler_params:
                continue
            _supplied = args.get(_pp)
            if _supplied is None or _supplied == "":
                default = handler_params[_pp].default
                if default in (".", "./"):
                    args = {**args, _pp: str(arg_primary)}
                continue
            if _supplied == "." or _supplied == "./":
                args = {**args, _pp: str(arg_primary)}
                break
            _supplied_str = str(_supplied)
            if not Path(_supplied_str).is_absolute():
                normalized = normalize_scoped_relative_path(_supplied_str, arg_primary)
                args = {**args, _pp: str(arg_primary / normalized)}
                break

    if has_sandbox:
        if scope.primary_write is None:
            raise PermissionError(
                f"write skill {sucker_id!r} blocked: "
                f"thread is in '{scope.mode}' mode "
                "(no write scope). Call "
                "'exit_plan_mode' first with a confirmed "
                "plan summary to transition to chat / "
                "team / code mode."
            )
        supplied = args.get("sandbox_dir")
        default_sandbox = scope.primary_write if _mutates_files else read_primary
        # A tool may target any granted root, not only the primary project
        # directory. Pick the most specific containing root for an absolute
        # path. In local complete-access mode this selects the filesystem root
        # for paths outside the normal workspace.
        allowed_roots = scope.writable_roots if _mutates_files else scope.readable_roots
        # Directory parameters such as ``cwd`` are scope-bearing too. Exec
        # skills commonly receive an absolute cwd but no file path; ignoring
        # it left ``sandbox_dir`` pinned to output/final even in local
        # complete-access mode.
        for path_param in (*_path_params, *_root_params):
            raw_path = args.get(path_param)
            if not isinstance(raw_path, (str, Path)) or not Path(raw_path).is_absolute():
                continue
            try:
                resolved_path = Path(raw_path).expanduser().resolve(strict=False)
            except OSError:
                continue
            matching_roots: list[Path] = []
            for readable_root in allowed_roots:
                try:
                    resolved_path.relative_to(readable_root.resolve(strict=False))
                except (OSError, ValueError):
                    continue
                matching_roots.append(readable_root)
            if matching_roots:
                default_sandbox = max(
                    matching_roots,
                    key=lambda candidate: len(candidate.parts),
                )
            break
        if not supplied:
            # Lazily create the primary root so the skill can open
            # files there without having to mkdir itself.
            if _mutates_files:
                with contextlib.suppress(OSError):
                    scope.primary_write.mkdir(parents=True, exist_ok=True)
            args = {**args, "sandbox_dir": str(default_sandbox)}
        elif not (scope.allows_write(supplied) if _mutates_files else scope.allows_read(supplied)):
            raise PermissionError(
                f"sandbox_dir {supplied!r} escapes "
                f"write scope (mode={scope.mode}, "
                f"requested_mode={scope.requested_mode}, "
                f"writable_roots="
                f"{[str(r) for r in scope.writable_roots]}, "
                f"readable_roots="
                f"{[str(r) for r in scope.readable_roots]}, "
                f"workspace_path="
                f"{(_sess.metadata or {}).get('workspace_path', 'NOT SET')}"
                "). If workspace_path is NOT SET, "
                "the session lost its code-mode context. "
                "Try opening a new code thread "
                "with the correct workspace selected."
            )
    return args


def _declared_write_scope_violation(
    skill: Skill,
    sucker_id: SkillId,
    args: dict[str, Any],
) -> str | None:
    """Enforce an optional task-level allowlist for concrete file writes.

    The normal execution scope confines writes to a workspace.  Some tasks
    need a narrower contract (for example, an evaluation that permits edits
    to two named files only).  ``allowed_write_paths`` is trusted session
    metadata, never a model argument, and contains workspace-relative paths.
    Existing sessions without the field retain the normal workspace policy.
    """
    try:
        from runtime.platform.process.session import current_session

        session = current_session()
        metadata = getattr(session, "metadata", None) if session is not None else None
    except (AttributeError, ImportError):
        metadata = None
    if not isinstance(metadata, dict) or "allowed_write_paths" not in metadata:
        return None

    declared = metadata.get("allowed_write_paths")
    if not isinstance(declared, list) or not declared:
        return (
            f"[write-scope-denied] {sucker_id}: task write allowlist is empty or invalid; "
            "no file writes are permitted"
        )
    command_violation = _declared_scope_command_violation(str(sucker_id), args)
    if command_violation is not None:
        return (
            f"[write-scope-denied] {sucker_id}: {command_violation}; "
            f"permitted paths: {declared!r}. Use the dedicated read/test/lint tools "
            "without creating environments, lockfiles, caches, or package metadata."
        )

    affinity = set(skill.affinity or [])
    if "file" not in affinity or not affinity & {"write", "edit", "delete", "dangerous"}:
        return None
    workspace_value = metadata.get("workspace_path")
    if not isinstance(workspace_value, str) or not workspace_value.strip():
        return (
            f"[write-scope-denied] {sucker_id}: task write allowlist requires an absolute "
            "workspace_path"
        )
    workspace = Path(workspace_value).expanduser()
    if not workspace.is_absolute():
        return (
            f"[write-scope-denied] {sucker_id}: task write allowlist requires an absolute "
            "workspace_path"
        )
    workspace = workspace.resolve(strict=False)

    raw_target = _extract_path(args, None)
    if not raw_target:
        return (
            f"[write-scope-denied] {sucker_id}: could not determine the target file; "
            f"permitted paths: {declared!r}"
        )
    target = Path(raw_target).expanduser()
    if not target.is_absolute():
        target = workspace / target
    target = target.resolve(strict=False)
    try:
        target.relative_to(workspace)
    except ValueError:
        return (
            f"[write-scope-denied] {sucker_id}: {raw_target!r} escapes the task workspace; "
            f"permitted paths: {declared!r}"
        )

    allowed_targets: set[Path] = set()
    for item in declared:
        if not isinstance(item, str) or not item.strip():
            continue
        relative = Path(item.strip())
        if relative.is_absolute() or relative == Path(".") or ".." in relative.parts:
            continue
        candidate = (workspace / relative).resolve(strict=False)
        try:
            candidate.relative_to(workspace)
        except ValueError:
            continue
        allowed_targets.add(candidate)
    if target in allowed_targets:
        return None
    return (
        f"[write-scope-denied] {sucker_id}: {raw_target!r} is outside the task's declared "
        f"file set; permitted paths: {declared!r}. Do not create helper/package files."
    )


def _declared_scope_command_violation(
    skill_name: str,
    args: dict[str, Any],
) -> str | None:
    """Reject environment/package creation under a task file allowlist.

    Workspace confinement alone cannot stop a shell command from creating
    ``.venv``, lockfiles, or package-manager caches beside the two files a
    task explicitly permits.  Under a trusted task-level allowlist, package
    and environment management is outside scope; normal focused pytest/lint
    commands remain available.
    """
    if skill_name not in {
        "exec_shell",
        "background_exec",
        "ipython",
        "run_tests",
        "lint_check",
        "format_code",
    }:
        return None
    if skill_name == "ipython":
        return "ipython can perform arbitrary filesystem writes outside the declared file set"
    raw = args.get("command")
    if not raw:
        return None
    if isinstance(raw, list):
        tokens = [str(value) for value in raw]
    elif isinstance(raw, str):
        try:
            tokens = shlex.split(raw)
        except ValueError:
            return "command could not be parsed safely"
    else:
        return "command must be a string or argv list"
    if not tokens:
        return None
    program = Path(tokens[0]).name.lower()
    lowered = [token.lower() for token in tokens[1:]]
    if program in {"uv", "uvx", "pip", "pip3", "virtualenv", "conda", "mamba", "poetry", "pdm"}:
        return f"{program!r} may create an environment, cache, or lockfile outside the declared file set"
    if (
        program.startswith("python")
        and len(lowered) >= 2
        and lowered[0] == "-m"
        and lowered[1] in {"pip", "venv", "virtualenv", "ensurepip"}
    ):
        return f"python -m {lowered[1]} may create files outside the declared file set"
    if program.startswith("python") and any(token in {"-c", "-"} for token in lowered[:2]):
        return "inline Python can perform arbitrary filesystem writes outside the declared file set"
    if program in {"npm", "pnpm", "yarn", "bun"} and any(
        token in {"add", "install", "update", "upgrade", "link", "init"} for token in lowered[:2]
    ):
        return f"{program!r} package mutation is outside the declared file set"
    if program == "cargo" and any(token in {"add", "install", "update"} for token in lowered[:2]):
        return "cargo package mutation is outside the declared file set"
    if program == "go" and lowered and lowered[0] in {"get", "mod", "work"}:
        return "Go module/workspace mutation is outside the declared file set"
    if program in {"mkdir", "touch", "mktemp", "cp", "mv", "rm", "rmdir"}:
        return f"{program!r} cannot prove that every filesystem mutation stays in the declared file set"
    return None


class StepExecutionError(RuntimeError):
    pass


class ReadBeforeWriteRequired(RuntimeError):
    pass


def _extract_token_usage(output: Any) -> tuple[int, int]:
    """Pull real LLM token usage out of a skill's return value.

    Recognized conventions (first match wins):
      * ``output["cost"]["input_tokens"]`` / ``output["cost"]["output_tokens"]``
        (used by ``deep_evolve`` aggregating multiple inner LLM calls)
      * ``output["meta"]["input_tokens"]`` / ``output["meta"]["output_tokens"]``
        (used by ``deep_reflect``, ``learn_skill_from_text``,
        ``apply_skill`` — single-LLM-call skills)
      * top-level ``output["input_tokens"]`` / ``output["output_tokens"]``

    Returns ``(0, 0)`` if no usage info is present · falls back to the
    executor's estimate (close to zero for atomic skills).

    Defensive · never raises. An LLM-coupled skill that forgets to
    report tokens just under-charges its own budget slot; a malformed
    dict doesn't break the commit path.
    """
    if not isinstance(output, dict):
        return 0, 0
    for section in ("cost", "meta"):
        blk = output.get(section)
        if isinstance(blk, dict):
            _in = blk.get("input_tokens")
            _out = blk.get("output_tokens")
            if _in or _out:
                try:
                    return int(_in or 0), int(_out or 0)
                except (TypeError, ValueError):
                    continue
    _in = output.get("input_tokens")
    _out = output.get("output_tokens")
    if _in or _out:
        try:
            return int(_in or 0), int(_out or 0)
        except (TypeError, ValueError):  # noqa: BLE001 — token coercion fallthrough
            pass
    return 0, 0


def _read_before_write_violation(
    skill_name: str,
    args: dict[str, Any],
) -> str | None:
    if skill_name not in _READ_BEFORE_WRITE_TOOLS:
        return None
    target = canonical_tool_path(args)
    if target is None or not target.exists():
        return None

    try:
        from runtime.platform.process.session import current_session

        session = current_session()
    except (ImportError, AttributeError, RuntimeError):
        session = None
    if session is None:
        return None

    read_paths = session.metadata.get(_READ_TRACKING_KEY)
    if not isinstance(read_paths, list):
        read_paths = []
    target_key = _path_key(target)
    if target_key in set(str(p) for p in read_paths):
        return None
    return f"refuse: must read_file('{target}') in this turn before writing to an existing file"


def _file_write_lease_target(skill: Skill, args: dict[str, Any]) -> Path | None:
    affinity = set(skill.affinity or [])
    if "file" not in affinity:
        return None
    if not (affinity & {"write", "edit", "delete", "dangerous"}):
        return None
    return canonical_tool_path(args)


def _file_write_lease_owner(
    *,
    actor: str | None,
    arm_id: ArmId,
    caller: str,
) -> str:
    return str(actor or arm_id or caller or "unknown")


def _record_successful_read(
    skill_name: str,
    args: dict[str, Any],
    output: Any,
) -> None:
    if isinstance(output, dict) and output.get("error"):
        return
    paths: list[Path] = []
    if skill_name in _FILE_READ_TRACKING_TOOLS:
        path = canonical_tool_path(args)
        if path is not None:
            paths.append(path)
    elif skill_name == "exec_shell" and isinstance(output, dict):
        # The guard is about ensuring the model has inspected the current
        # contents, not about forcing one particular UI tool.  Native models
        # often use an argv-safe ``cat file`` for that inspection.  Recognise
        # only this narrow, successful, read-only form; arbitrary shell
        # commands must never grant a write capability.
        argv = output.get("argv")
        if output.get("exit_code") == 0 and isinstance(argv, list) and argv:
            command = str(argv[0])
            if command == "cat":
                cwd = args.get("cwd") or args.get("sandbox_dir")
                base = Path(str(cwd)).expanduser() if cwd else Path.cwd()
                for raw in argv[1:]:
                    value = str(raw)
                    if value.startswith("-"):
                        continue
                    candidate = Path(value).expanduser()
                    paths.append(candidate if candidate.is_absolute() else base / candidate)
    if not paths:
        return
    try:
        from runtime.platform.process.session import current_session

        session = current_session()
    except (ImportError, AttributeError, RuntimeError):
        session = None
    if session is None:
        return
    read_paths = session.metadata.get(_READ_TRACKING_KEY)
    if not isinstance(read_paths, list):
        read_paths = []
        session.metadata[_READ_TRACKING_KEY] = read_paths
    known = set(str(p) for p in read_paths)
    for path in paths:
        key = _path_key(path)
        if key not in known:
            read_paths.append(key)
            known.add(key)
        record_file_read_snapshot(session, path)


def _path_key(path: Path) -> str:
    return str(path.resolve(strict=False)).casefold()


def _resolve_workspace_for_diagnostics(args: dict[str, Any]) -> str | None:
    """Pick a workspace root for ``post_write_diagnostics``.

    Prefers the active session's ``workspace_path`` (carried in
    ``Session.metadata`` for code-mode threads). Falls back to the
    write call's ``sandbox_dir`` / ``cwd``. Returns ``None`` when no
    plausible root is available — diagnostics simply skip in that
    case rather than scanning the process CWD.
    """
    try:
        from runtime.platform.process.session import current_session

        session = current_session()
    except (ImportError, AttributeError, RuntimeError):
        session = None
    if session is not None:
        try:
            wp = (session.metadata or {}).get("workspace_path")
        except AttributeError:
            wp = None
        if isinstance(wp, str) and wp.strip() and Path(wp).is_dir():
            return wp
    for key in ("sandbox_dir", "cwd"):
        cand = args.get(key)
        if isinstance(cand, str) and cand.strip() and Path(cand).is_dir():
            return cand
    return None


def _make_reject_step(
    step_id: int,
    node_id: str,
    call: ToolCall,
    status: ExecutionStatus,
    reason: str = "",
    protocol_tags: list[str] | None = None,
) -> Step:
    from runtime.execution.tool_engine.effect_receipts import not_executed_effect_receipt

    return Step(
        step_id=step_id,
        node_id=node_id,
        action=call,
        result=ExecutionResult(
            call_id=call.call_id,
            status=status,
            output=None,
            error_type=status,
            stderr_tags=[status] + list(protocol_tags or []) + ([reason] if reason else []),
            effect_receipt=not_executed_effect_receipt(
                call_id=call.call_id,
                tool_name=call.sucker_id,
                reason=reason or status,
            ),
        ),
        immune_verdict=status if status == "immune_reject" else None,
    )


def _check_capability_permission(skill: Any) -> tuple[bool, str | None]:
    try:
        from runtime.execution.misc.capability_permissions import is_skill_allowed
        from runtime.platform.capabilities.permission_grants import (
            is_marketplace_skill_allowed,
        )

        allowed, reason = is_skill_allowed(str(getattr(skill, "name", "") or ""))
        if not allowed:
            return allowed, reason
        return is_marketplace_skill_allowed(skill)
    except (ImportError, AttributeError, TypeError, RuntimeError):  # noqa: BLE001 - permission layer must fail closed
        return False, "capability permission check failed"


def _check_task_capability_permission(skill_id: SkillId) -> tuple[bool, str | None]:
    try:
        from runtime.execution.misc.capability_permissions import permission_group_for_skill
        from runtime.platform.process.session import current_session
        from runtime.platform.process.task_supervisor import manifest_from_session_metadata

        session = current_session()
        manifest = manifest_from_session_metadata(session.metadata if session is not None else None)
        if manifest is None:
            return True, None
        normalized_skill_id = str(skill_id)
        if not manifest.allows_skill(normalized_skill_id):
            task_id = str(session.metadata.get("task_id") or "") if session is not None else ""
            suffix = f" for task {task_id}" if task_id else ""
            return False, f"task capability skill disabled: {normalized_skill_id}{suffix}"
        group = permission_group_for_skill(str(skill_id))
        if manifest.allows_group(group):
            return True, None
        task_id = str(session.metadata.get("task_id") or "") if session is not None else ""
        suffix = f" for task {task_id}" if task_id else ""
        return False, f"task capability group disabled: {group}{suffix}"
    except (ImportError, AttributeError, TypeError, RuntimeError):  # noqa: BLE001 - legacy paths without session metadata should continue
        return True, None


def _current_execution_policy_context() -> ExecutionPolicyContext:
    """Resolve session policy once for the canonical governance evaluator.

    Legacy execution paths without a process session keep the historical
    permissive default.  If an explicitly enforced policy cannot be parsed,
    the context constructor falls back to its safe risk matrix defaults.
    """
    try:
        from runtime.platform.process.session import current_session

        session = current_session()
        metadata = session.metadata if session is not None else {}
        return ExecutionPolicyContext.from_metadata(metadata)
    except (ImportError, AttributeError, TypeError, RuntimeError, ValueError):
        return ExecutionPolicyContext()


def _mark_task_waiting_approval(
    tool_name: str,
    reason: str,
    *,
    metadata_patch: dict[str, Any] | None = None,
) -> None:
    try:
        from runtime.platform.process.session import current_session
        from runtime.platform.process.task_supervisor import (
            TaskRunStatus,
            TaskSupervisor,
            TaskSupervisorStore,
        )

        session = current_session()
        metadata = session.metadata if session is not None else {}
        task_id = str(metadata.get("task_id") or "").strip()
        store_path = str(metadata.get("task_supervisor_store_path") or "").strip()
        if not task_id or not store_path:
            return
        supervisor = TaskSupervisor(
            TaskSupervisorStore(store_path),
            holder_id=str(metadata.get("task_supervisor_holder_id") or "") or None,
            lease_ttl_seconds=float(metadata.get("task_supervisor_lease_ttl_seconds") or 300.0),
        )
        patch = {
            "approval_required": True,
            "approval_tool_name": tool_name,
            "approval_reason": reason,
        }
        if isinstance(metadata_patch, dict):
            patch.update(metadata_patch)
        patch["approval_tool_name"] = tool_name
        patch["approval_reason"] = reason
        supervisor.transition(
            task_id,
            TaskRunStatus.WAITING_APPROVAL,
            reason=reason,
            metadata_patch=patch,
        )
    except Exception:  # noqa: BLE001 - approval status marking is best-effort
        return


def _hash_output(obj: Any) -> str | None:
    if obj is None:
        return None
    try:
        return hashlib.blake2b(repr(obj).encode("utf-8"), digest_size=8).hexdigest()
    except (TypeError, ValueError, RecursionError, UnicodeEncodeError):
        # Same tolerance as _safe_repr · an object we can't repr or
        # encode still shouldn't crash the step. None means "no
        # content hash for this step" · downstream dedup just skips.
        return None


def _notify_budget_warnings(
    budget: Budget,
    task_id: TaskId,
    arm_id: ArmId,
) -> None:
    """Notify community hooks when the budget crosses 80% / 95% utilization.

    At most once per level per Budget. Best-effort · dispatch exceptions
    can't rail the commit path.
    """
    try:
        crossings = budget.check_warn_crossing()
        if crossings:
            from runtime.platform.process.session import current_session as _cs_warn
            from runtime.safety.hooks.runner import dispatch_notification

            for level in crossings:
                dispatch_notification(
                    kind="budget_warn",
                    details={
                        "level_pct": level,
                        "task_id": str(task_id),
                        "arm_id": str(arm_id),
                        "utilization": budget.utilization,
                        "usd_spent": budget.usd_spent,
                        "tokens_spent": budget.tokens_spent,
                        "usd_limit": budget.limits.usd,
                        "tokens_limit": budget.limits.tokens,
                    },
                    session=_cs_warn(),
                )
    except (TypeError, ValueError, RuntimeError):  # noqa: BLE001
        pass


def _emit_skill_metrics(
    sucker_id: SkillId,
    status: ExecutionStatus,
    latency_ms: float,
    error_type: str | None,
) -> None:
    """Beak-level metrics. Increment counters + record latency.

    Best-effort: a metrics-registry import failure must NOT break execution.
    """
    try:
        from runtime.platform.observability.metrics import get_registry as _mr

        _reg = _mr()
        _calls = _reg.counter(
            "echo_skill_calls_total",
            "Total skill invocations",
            labels=["sucker_id", "status"],
        )
        _calls.inc(labels={"sucker_id": str(sucker_id), "status": str(status)})
        _lat = _reg.histogram(
            "echo_skill_latency_seconds",
            "Skill invocation latency (seconds)",
            labels=["sucker_id"],
        )
        _lat.observe(latency_ms / 1000.0, labels={"sucker_id": str(sucker_id)})
        if status != "success":
            _errs = _reg.counter(
                "echo_skill_errors_total",
                "Skill invocations that did not return success",
                labels=["sucker_id", "error_type"],
            )
            _errs.inc(
                labels={
                    "sucker_id": str(sucker_id),
                    "error_type": str(error_type or "unknown"),
                }
            )
    except (TypeError, ValueError, RuntimeError):  # noqa: BLE001
        pass


def _record_session_budget(budget_tracker: Any, actual_cost: CostEntry) -> None:
    """Session-level cumulative budget tracking.

    Records the actual cost of this step against the active session's
    ledger so cross-task / cross-arm aggregates are visible at
    /api/budget/sessions and warning callbacks fire at 80% / 95% of the
    configured ceiling.

    ``Session.thread_id`` is the canonical per-chat key. Fall back to
    ``turn_id`` so anonymous / legacy flows still bucket coherently.

    Best-effort: any failure (no active session, no tracker configured,
    BudgetExceeded raised by ceiling enforcement) propagates only when
    ``BudgetExceeded`` was specifically raised; otherwise we swallow so a
    misconfigured ledger can't break execution.
    """
    if budget_tracker is None:
        return
    try:
        from runtime.platform.process.session import current_session as _cs_bt

        _sess = _cs_bt()
        _sid: str | None = None
        if _sess is not None:
            _sid = (
                getattr(_sess, "thread_id", None)
                or getattr(_sess, "conversation_id", None)
                or getattr(_sess, "turn_id", None)
            )
        if _sid:
            budget_tracker.record(_sid, actual_cost)
    except Exception as _bt_exc:  # noqa: BLE001
        if type(_bt_exc).__name__ == "BudgetExceeded":
            raise
