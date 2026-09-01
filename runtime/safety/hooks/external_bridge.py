"""Industry ``hooks.json`` bridge — dsh hook-protocol + dialect bridges.

Ported from DeepSeek Harness' ``@deepseek-ai/dsh-hook-protocol`` plus the
``hooks-claude-code`` / ``hooks-codex`` bridges: loads UNMODIFIED Claude
Code / Codex ``hooks.json`` files and drives them through the local hook
registry, so existing external shell hooks keep working without a rewrite.

Shared protocol (both dialects):

* Matchers — absent / empty / ``*`` match all; Claude treats a pure
  ``[A-Za-z0-9_|]+`` pattern as literal pipe-separated alternatives and
  anything else as an unanchored regex; Codex treats every non-empty
  pattern as an unanchored regex. Invalid regexes never match (and are
  rejected at config parse time with a stable diagnostic).
* Codec — exit 2 blocks with stderr as the reason; any other non-zero
  exit is a non-blocking error; structured stdout is honored only on a
  clean exit (malformed JSON is leniently ignored). A top-level
  ``decision`` of ``approve``/``block`` is legacy; a matching
  ``permissionDecision`` (``allow``/``deny``/``ask``) overrides it.
* Execution — one shell command per hook entry, JSON payload on stdin
  (trailing newline for the Claude dialect, none for Codex), a per-hook
  timeout that degrades to a non-blocking error, and never a raise into
  the agent loop (infra faults pass through).
* Config shape (both dialects) — ``{"version": 1, "hooks": {"PreToolUse":
  [{"matcher": "...", "hooks": [{"type": "command", "command": "..."}]}]}}``.
  Only ``command`` hooks run; other hook types are skipped with a reason.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from itertools import count
from pathlib import Path
from typing import Any, Literal

from .events import (
    NotificationEvent,
    PermissionDeniedEvent,
    PermissionRequestEvent,
    PostToolUseEvent,
    PostToolUseFailureEvent,
    PreToolUseEvent,
    SessionStartEvent,
    StopEvent,
    SubagentStartEvent,
    SubagentStopEvent,
    UserPromptSubmitEvent,
)
from .registry import HookDecision, get_global_registry

_logger = logging.getLogger("runtime.safety.hooks.external")

HookDialect = Literal["claude-code", "codex"]

# dsh ``DEFAULT_HOOK_TIMEOUT_MS`` (600000 ms = 10 minutes).
DEFAULT_HOOK_TIMEOUT_S = 600.0
# dsh ``BLOCKING_EXIT_CODE`` — exit 2 blocks with stderr as the reason.
BLOCKING_EXIT_CODE = 2
# dsh ``DEFAULT_STDERR_SUMMARY_MAX_CHARS`` — cap for the durable
# ``hook/result`` stderr summary (both bridges' config default).
DEFAULT_STDERR_SUMMARY_MAX_CHARS = 500

_EVENT_TYPES = {
    "UserPromptSubmit": UserPromptSubmitEvent,
    "PreToolUse": PreToolUseEvent,
    "PostToolUse": PostToolUseEvent,
    "PostToolUseFailure": PostToolUseFailureEvent,
    "Notification": NotificationEvent,
    "Stop": StopEvent,
    "SessionStart": SessionStartEvent,
    "SubagentStart": SubagentStartEvent,
    "SubagentStop": SubagentStopEvent,
    "PermissionRequest": PermissionRequestEvent,
    "PermissionDenied": PermissionDeniedEvent,
}
# Events whose groups may carry a tool-name matcher (dsh discards matchers
# for events without a matcher subject).
_MATCHER_EVENTS = {"PreToolUse", "PostToolUse", "PostToolUseFailure"}

_CLAUDE_LITERAL_RE = re.compile(r"^[A-Za-z0-9_|]+$")


@dataclass(frozen=True, slots=True)
class ExternalHookSpec:
    """One normalized command hook from a dialect config file."""

    event: str
    command: str
    dialect: HookDialect = "codex"
    matcher: str = ""
    timeout_s: float = DEFAULT_HOOK_TIMEOUT_S


@dataclass(frozen=True, slots=True)
class ExternalHookOutput:
    """Neutral decoded outcome of one hook run (dsh ``HookOutput``)."""

    decision: str | None = None  # "allow" | "deny" | "ask" | "block" | "approve"
    exit_code: int | None = None
    reason: str = ""
    modified_prompt: str | None = None
    modified_input: dict[str, Any] | None = None
    additional_directives: str | None = None
    stdout: str = ""
    stderr_summary: str = ""


# One stable per-invocation id so a ``hook/invoked``/``hook/result`` pair
# correlates in the log (dsh ``nextHandlerId``). ``itertools.count`` next()
# is atomic, so concurrent hook runs never collide.
_handler_counter = count(1)


def _next_handler_id(point: str, dialect: HookDialect) -> str:
    return f"{dialect}:{point}:{next(_handler_counter)}"


def summarize_stderr(stderr: str, max_chars: int = DEFAULT_STDERR_SUMMARY_MAX_CHARS) -> str | None:
    """Trimmed stderr for ``hook/result``; ``None`` when blank, capped with
    an ellipsis when over (dsh ``summarizeStderr``)."""
    text = (stderr or "").strip()
    if not text:
        return None
    if len(text) > max_chars:
        return text[:max_chars] + "…"
    return text


# ── Matchers (dsh ``matcherDiagnostic`` / ``matchesMatcher``) ──────────────


def _is_match_all(matcher: str | None) -> bool:
    return matcher is None or matcher == "" or matcher == "*"


def matcher_diagnostic(matcher: str | None, dialect: HookDialect) -> str | None:
    """Stable diagnostic for an invalid matcher, ``None`` when valid."""
    if _is_match_all(matcher):
        return None
    pattern = matcher or ""
    if dialect == "claude-code" and _CLAUDE_LITERAL_RE.match(pattern):
        return None
    try:
        re.compile(pattern)
    except re.error:
        return f"invalid {dialect} regex matcher {pattern!r}"
    return None


def matches_matcher(matcher: str | None, query: str, dialect: HookDialect) -> bool:
    """Whether a matcher selects ``query``; invalid regexes never match."""
    if _is_match_all(matcher):
        return True
    pattern = matcher or ""
    if dialect == "claude-code" and _CLAUDE_LITERAL_RE.match(pattern):
        return query in pattern.split("|")
    try:
        return re.search(pattern, query) is not None
    except re.error:
        return False


# ── Codec (dsh ``parseHookOutput``) ────────────────────────────────────────


def parse_hook_output(
    exit_code: int | None,
    stdout: str,
    stderr: str,
) -> ExternalHookOutput:
    """Decode one hook process outcome into the neutral ``HookOutput``.

    Exit 2 blocks with stderr as the reason; every other failure is a
    non-blocking error; structured stdout is honored only on a clean exit.
    """
    trimmed_out = (stdout or "").strip()
    trimmed_err = (stderr or "").strip()
    if exit_code == BLOCKING_EXIT_CODE:
        return ExternalHookOutput(
            decision="block",
            exit_code=exit_code,
            reason=trimmed_err or "hook blocked (exit 2)",
            stdout=trimmed_out,
            stderr_summary=trimmed_err,
        )
    if exit_code != 0:
        # Non-blocking infra / script error (dsh keeps the turn alive).
        return ExternalHookOutput(
            exit_code=exit_code,
            stdout=trimmed_out,
            stderr_summary=trimmed_err,
        )
    if not trimmed_out:
        return ExternalHookOutput(exit_code=exit_code, stderr_summary=trimmed_err)
    try:
        parsed = json.loads(trimmed_out)
    except json.JSONDecodeError:
        # Malformed JSON on a clean exit = no structured output (lenient).
        return ExternalHookOutput(exit_code=exit_code, stdout=trimmed_out)
    if not isinstance(parsed, dict):
        return ExternalHookOutput(exit_code=exit_code, stdout=trimmed_out)

    # Legacy top-level decision is approve/block ONLY; permissionDecision
    # (allow/deny/ask) overrides it when present.
    top = parsed.get("decision")
    decision = top if top in ("approve", "block") else None
    permission = parsed.get("permissionDecision")
    if permission in ("allow", "deny", "ask"):
        decision = permission
    reason = parsed.get("reason")
    if not isinstance(reason, str):
        reason = ""
    modified_prompt = parsed.get("modifiedPrompt")
    if not isinstance(modified_prompt, str):
        modified_prompt = None
    modified_input = parsed.get("modifiedInput")
    if not isinstance(modified_input, dict):
        modified_input = None
    directives = parsed.get("additionalDirectives")
    if not isinstance(directives, str):
        directives = None
    return ExternalHookOutput(
        decision=decision,
        exit_code=exit_code,
        reason=reason,
        modified_prompt=modified_prompt,
        modified_input=modified_input,
        additional_directives=directives,
        stdout=trimmed_out,
        stderr_summary=trimmed_err,
    )


# ── Execution (dsh ``runHook``) ────────────────────────────────────────────


def run_external_hook(
    command: str,
    payload: dict[str, Any],
    *,
    dialect: HookDialect = "codex",
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    timeout_s: float = DEFAULT_HOOK_TIMEOUT_S,
    plugin_root: str = "",
    project_dir: str = "",
    allowed_commands: list[str] | None = None,
) -> ExternalHookOutput:
    """Run one command hook. Never raises — infra faults pass through.

    Audit S-04 hardening:
      * ``${CLAUDE_PLUGIN_ROOT}`` / ``${CLAUDE_PROJECT_DIR}`` are
        substituted with ``shlex.quote`` so a workspace/plugin path cannot
        smuggle shell metacharacters into the invocation.
      * When ``allowed_commands`` is a non-empty glob list, commands that
        do not match are refused without executing.

    Audit C-06 (bandit B602): the command runs with ``shell=False`` via
    ``shlex.split`` — no shell metacharacter expansion, no injection
    surface from paths or payloads. Hooks that genuinely need pipelines /
    redirection should wrap them in a script file and call that script.
    """
    cmd = command
    if plugin_root:
        cmd = cmd.replace("${CLAUDE_PLUGIN_ROOT}", shlex.quote(plugin_root))
    if project_dir:
        cmd = cmd.replace("${CLAUDE_PROJECT_DIR}", shlex.quote(project_dir))
    if allowed_commands and not any(fnmatch.fnmatch(cmd, pattern) for pattern in allowed_commands):
        _logger.warning(
            "external hook command not allowed by allowlist (refused): %s",
            cmd[:120],
        )
        return ExternalHookOutput(reason="hook command not allowed by allowlist")
    payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if dialect == "claude-code":
        payload_json += "\n"
    run_env = dict(os.environ)
    if project_dir:
        run_env["CLAUDE_PROJECT_DIR"] = project_dir
    if env:
        run_env.update(env)
    try:
        # shell=False: split argv ourselves so shell metacharacters in the
        # command are inert. A malformed quote raises ValueError and is
        # caught by the infra-fault handler below (non-blocking).
        argv = shlex.split(cmd)
        proc = subprocess.run(
            argv,
            shell=False,
            input=payload_json,
            text=True,
            capture_output=True,
            timeout=timeout_s,
            cwd=cwd,
            env=run_env,
        )
        return parse_hook_output(proc.returncode, proc.stdout, proc.stderr)
    except subprocess.TimeoutExpired:
        _logger.warning("external hook timed out after %ss: %s", timeout_s, cmd[:120])
        return ExternalHookOutput(reason="hook timed out")
    except (OSError, ValueError) as exc:  # noqa: BLE001 — infra fault = non-blocking
        _logger.warning("external hook could not run (%s): %s", exc, cmd[:120])
        return ExternalHookOutput(reason="hook could not run")


# ── Config parsing (dsh bridge ``parse*Config``) ───────────────────────────


def parse_external_hooks(
    raw: Any,
    dialect: HookDialect,
) -> tuple[list[ExternalHookSpec], list[str]]:
    """Normalize one dialect ``hooks.json`` payload.

    Returns ``(specs, skipped)`` — only ``command`` hooks with valid
    matchers survive; everything else is reported with a stable reason.
    """
    specs: list[ExternalHookSpec] = []
    skipped: list[str] = []
    if not isinstance(raw, dict):
        return specs, ["hooks config is not an object"]
    hooks = raw.get("hooks")
    if not isinstance(hooks, dict):
        return specs, ["hooks config has no hooks object"]
    for event, groups in hooks.items():
        if event not in _EVENT_TYPES:
            skipped.append(f"unsupported event {event}")
            continue
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            matcher = group.get("matcher")
            if matcher is not None and not isinstance(matcher, str):
                skipped.append(f"non-string matcher on {event}")
                continue
            diag = matcher_diagnostic(matcher, dialect)
            if diag is not None:
                skipped.append(f"{diag} on {event}")
                continue
            entries = group.get("hooks")
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                if entry.get("type") not in (None, "command"):
                    skipped.append(f"non-command hook on {event}")
                    continue
                command = entry.get("command")
                if not isinstance(command, str) or not command.strip():
                    skipped.append(f"empty command on {event}")
                    continue
                timeout = entry.get("timeoutSec")
                if timeout is None:
                    timeout = entry.get("timeout_s")
                try:
                    timeout_s = float(timeout) if timeout is not None else DEFAULT_HOOK_TIMEOUT_S
                except (TypeError, ValueError):
                    timeout_s = DEFAULT_HOOK_TIMEOUT_S
                specs.append(
                    ExternalHookSpec(
                        event=event,
                        command=command.strip(),
                        dialect=dialect,
                        matcher=matcher or "",
                        timeout_s=timeout_s,
                    )
                )
    return specs, skipped


def load_external_hooks(
    path: Path, dialect: HookDialect
) -> tuple[list[ExternalHookSpec], list[str]]:
    """Read one ``hooks.json``. Missing / unparsable → empty, never raises."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        _logger.warning("external hooks: failed to parse %s (%s); ignoring", path, exc)
        return [], []
    specs, skipped = parse_external_hooks(raw, dialect)
    for reason in skipped:
        _logger.warning("external hooks: skipping %s in %s", reason, path)
    return specs, skipped


# ── Payloads + decision mapping (dsh bridge event adapters) ────────────────


def _session_fields(session: Any) -> dict[str, str]:
    session_id = cwd = workspace = ""
    if session is not None:
        session_id = str(getattr(session, "session_id", "") or getattr(session, "id", "") or "")
        cwd = str(getattr(session, "cwd", "") or "")
        workspace = str(
            getattr(session, "workspace_path", "") or getattr(session, "workspace", "") or ""
        )
    return {
        "session_id": session_id,
        "cwd": cwd or os.getcwd(),
        "workspace": workspace,
    }


def build_payload(event: Any, event_name: str) -> dict[str, Any]:
    """One per-event stdin payload (dsh bridge payload builder)."""
    payload: dict[str, Any] = {"hook_event_name": event_name}
    payload.update(_session_fields(getattr(event, "session", None)))
    if event_name == "UserPromptSubmit":
        payload["prompt"] = getattr(event, "prompt_text", "")
        payload["thread_id"] = getattr(event, "thread_id", "")
    elif event_name == "PreToolUse":
        payload["tool_name"] = getattr(event, "sucker_id", "")
        payload["tool_input"] = getattr(event, "args", None) or {}
        payload["tool_use_id"] = getattr(event, "caller", "") or ""
        payload["caller"] = getattr(event, "caller", "") or ""
    elif event_name == "PostToolUse":
        payload["tool_name"] = getattr(event, "sucker_id", "")
        payload["tool_input"] = getattr(event, "args", None) or {}
        payload["tool_response"] = getattr(event, "output", None)
        payload["success"] = bool(getattr(event, "success", True))
    elif event_name == "Notification":
        payload["kind"] = getattr(event, "kind", "")
        payload["details"] = getattr(event, "details", None) or {}
    elif event_name == "Stop":
        payload["thread_id"] = getattr(event, "thread_id", "")
        payload["success"] = bool(getattr(event, "success", True))
        payload["step_count"] = getattr(event, "step_count", 0)
    elif event_name == "SessionStart":
        payload["thread_id"] = getattr(event, "thread_id", "")
    return payload


def _decision_from_output(
    output: ExternalHookOutput,
    event: Any,
    spec: ExternalHookSpec,
) -> HookDecision:
    """Map the neutral output onto a local ``HookDecision``.

    ``block``/``deny`` cancel; ``ask`` passes through to the runtime's own
    approval pipeline; ``allow``/``approve`` pass. ``modifiedPrompt`` and
    ``modifiedInput`` are honored (dsh defers input rewrite — our registry
    supports it natively); ``additionalDirectives`` append to the prompt
    on UserPromptSubmit only, matching Claude Code's continuation seam.
    """
    if output.decision in ("block", "deny"):
        return HookDecision.cancel(output.reason or "external hook blocked")
    if output.decision == "ask":
        _logger.debug("external hook asked (no ask lane) on %s; passing to approval", spec.event)
        return HookDecision.pass_through()
    if spec.event == "UserPromptSubmit":
        if output.modified_prompt is not None:
            return HookDecision.modify_prompt(output.modified_prompt)
        if output.additional_directives:
            original = getattr(event, "prompt_text", "") or ""
            return HookDecision.modify_prompt(f"{original}\n\n{output.additional_directives}")
    if spec.event == "PreToolUse" and output.modified_input is not None:
        return HookDecision.modify_args(output.modified_input)
    if output.additional_directives:
        _logger.debug(
            "external hook additionalDirectives on %s are not injected (deferred)",
            spec.event,
        )
    return HookDecision.pass_through()


def _journal_hook_pair(
    *,
    session: Any,
    point: str,
    dialect: HookDialect,
    handler_id: str,
    matcher: str,
    output: ExternalHookOutput,
    duration_ms: int,
) -> None:
    """Best-effort dsh ``hook/invoked`` + ``hook/result`` journal rows.

    The pair is log-only (never a surface event) and written only when the
    hook's runtime session carries a bound journal — dsh's
    ``session && opts.turn !== undefined`` guard. ``turn_id`` ties the pair
    to the runtime turn whose lifecycle fired the hook; stderr is trimmed
    and capped at ``DEFAULT_STDERR_SUMMARY_MAX_CHARS``; an absent process
    exit stays omitted. Never raises — telemetry loss must not break hooks.
    """
    journal = None
    meta: dict[str, Any] = {}
    session_id = ""
    turn_id = ""
    if session is not None:
        session_id = str(getattr(session, "session_id", "") or getattr(session, "id", "") or "")
        turn_id = str(getattr(session, "turn_id", "") or "")
        meta = getattr(session, "metadata", None) or {}
        if not isinstance(meta, dict):
            meta = {}
        journal = meta.get("journal")
        if journal is None:
            stack = meta.get("stack")
            if stack is not None:
                journal = getattr(stack, "journal", None)
    if journal is None:
        return
    try:
        from runtime.memory.journal._journal_models import (
            HookInvokedEvent,
            HookResultEvent,
        )

        journal.write(
            HookInvokedEvent(
                task_id=meta.get("task_id"),
                session_id=session_id,
                turn_id=turn_id,
                point=point,
                dialect=dialect,
                handler_id=handler_id,
                matcher=matcher or None,
            )
        )
        journal.write(
            HookResultEvent(
                task_id=meta.get("task_id"),
                session_id=session_id,
                turn_id=turn_id,
                point=point,
                handler_id=handler_id,
                decision=output.decision or "pass",
                exit_code=output.exit_code,
                stderr_summary=summarize_stderr(
                    output.stderr_summary,
                    DEFAULT_STDERR_SUMMARY_MAX_CHARS,
                ),
                duration_ms=duration_ms,
            )
        )
    except (OSError, TypeError, ValueError):  # noqa: BLE001 — best-effort
        pass


def _make_handler(spec: ExternalHookSpec):
    def handler(event: Any) -> HookDecision | None:
        if spec.event in _MATCHER_EVENTS and spec.matcher:
            tool_name = getattr(event, "sucker_id", "") or ""
            if not matches_matcher(spec.matcher, tool_name, spec.dialect):
                return None
        payload = build_payload(event, spec.event)
        session = getattr(event, "session", None)
        handler_id = _next_handler_id(spec.event, spec.dialect)
        started = time.monotonic()
        output = run_external_hook(
            spec.command,
            payload,
            dialect=spec.dialect,
            timeout_s=spec.timeout_s,
        )
        duration_ms = int(round((time.monotonic() - started) * 1000))
        _journal_hook_pair(
            session=session,
            point=spec.event,
            dialect=spec.dialect,
            handler_id=handler_id,
            matcher=spec.matcher,
            output=output,
            duration_ms=duration_ms,
        )
        return _decision_from_output(output, event, spec)

    return handler


# ── Discovery + registration ───────────────────────────────────────────────


def discover_external_hook_paths() -> list[tuple[Path, HookDialect]]:
    """Default discovery order: explicit env → home → process cwd.

    ``ECHO_HOOKS_JSON`` pins one file (dialect guessed from the path);
    otherwise both ``.claude/hooks.json`` and ``.codex/hooks.json`` under
    the home directory and the launch cwd are picked up, mirroring how
    Claude Code / Codex discover their own configs.
    """
    paths: list[tuple[Path, HookDialect]] = []
    explicit = os.environ.get("ECHO_HOOKS_JSON", "")
    if explicit:
        path = Path(explicit).expanduser()
        dialect: HookDialect = "codex" if ".codex" in str(path) else "claude-code"
        paths.append((path, dialect))
    home = Path.home()
    for sub, dialect in ((".claude/hooks.json", "claude-code"), (".codex/hooks.json", "codex")):
        for root in (home, Path.cwd()):
            path = root / sub
            if path.is_file():
                paths.append((path, dialect))
    return paths


def register_external_hooks(
    registry: Any = None,
    paths: list[tuple[Path, HookDialect]] | None = None,
    command_allowlist: list[str] | None = None,
) -> int:
    """Load every discovered (or given) ``hooks.json`` into the registry.

    Returns the number of command hooks registered. Best-effort: a bad
    config logs and is skipped; a failing hook degrades to pass-through
    at dispatch time (never into the agent loop).

    Audit S-04: when ``command_allowlist`` is set (explicitly or via the
    ``ECHO_HOOK_COMMAND_ALLOWLIST`` env, ``|``-separated globs),
    commands that do not match are skipped at registration — they can
    never execute. Empty/None keeps backward-compatible behaviour (with
    the shlex-quoting injection fix still applied at run time).
    """
    registry = registry or get_global_registry()
    if paths is None:
        paths = discover_external_hook_paths()
    if command_allowlist is None:
        raw = os.environ.get("ECHO_HOOK_COMMAND_ALLOWLIST", "").strip()
        if raw:
            command_allowlist = [p.strip() for p in raw.split("|") if p.strip()]
    registered = 0
    seen: set[tuple[str, str]] = set()
    for path, dialect in paths:
        key = (str(path.resolve()), dialect)
        if key in seen:
            continue
        seen.add(key)
        specs, _skipped = load_external_hooks(path, dialect)
        for spec in specs:
            if command_allowlist and not any(
                fnmatch.fnmatch(spec.command, pattern) for pattern in command_allowlist
            ):
                _logger.warning(
                    "external hook command not in allowlist (skipped): %s",
                    spec.command[:120],
                )
                continue
            registry.register(_EVENT_TYPES[spec.event], _make_handler(spec))
            registered += 1
    if registered:
        _logger.info("registered %d external command hook(s)", registered)
    return registered


__all__ = [
    "BLOCKING_EXIT_CODE",
    "DEFAULT_HOOK_TIMEOUT_S",
    "DEFAULT_STDERR_SUMMARY_MAX_CHARS",
    "ExternalHookOutput",
    "ExternalHookSpec",
    "build_payload",
    "discover_external_hook_paths",
    "load_external_hooks",
    "matcher_diagnostic",
    "matches_matcher",
    "parse_external_hooks",
    "parse_hook_output",
    "register_external_hooks",
    "run_external_hook",
    "summarize_stderr",
]
