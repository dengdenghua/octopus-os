"""Code-smell guards (post-step / pre-Final-Answer gates).

Extracted from ``react_guards.py`` (Wave 3, cluster 3) so the orchestration
module can stay under the size budget. Each guard returns either ``None``
(let the Final Answer through) or a message explaining why the model must
keep working.

Leaf-ish module: depends only on react_goal_analysis / react_parsing /
react_types — must never import react_guards.
"""

from __future__ import annotations

from runtime.core.cerebrum.react_goal_analysis import _final_answer_requests_user_help
from runtime.core.cerebrum.react_parsing import (
    _has_code_verification,
    _parse_action,
    _step_edits_frontend_outside_tsconfig,
    _step_introduces_async_without_await,
    _step_introduces_broad_except_suppression,
    _step_introduces_hardcoded_path,
    _step_introduces_log_swallow,
    _step_introduces_long_function,
    _step_introduces_print,
    _step_introduces_sleep,
    _step_is_full_file_rewrite_attempt,
    _step_is_oversized_edit,
    _step_is_surgical_edit_on,
    _step_replaced_code_with_comment,
)
from runtime.core.cerebrum.react_types import ReActStep

# ──────────────────────────────────────────────────────────────────
# §28 — commented-out-as-fix guard
# ──────────────────────────────────────────────────────────────────
# Catch the failure mode where the model "fixes" a problem by deleting
# or commenting out the offending code rather than diagnosing it.
# Heuristic at the parsing layer: an edit pair where old_string had
# executable Python and new_string has none.

_COMMENT_OUT_LOOKBACK = 12


def _trajectory_replaced_code_with_comment(steps: list[ReActStep]) -> bool:
    window = steps[-_COMMENT_OUT_LOOKBACK:] if steps else []
    return any(_step_replaced_code_with_comment(step) for step in window)


def _commented_out_as_fix_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    is_code_mode: bool,
) -> str | None:
    """Reject finals where a code chunk was replaced with comment/blank
    only — a classic sign of "I made the error go away by deleting the
    code that triggered it"."""
    if not is_code_mode or not steps:
        return None
    if _final_answer_requests_user_help(final_answer):
        return None
    if not _trajectory_replaced_code_with_comment(steps):
        return None
    return (
        "Cannot finish yet: an edit replaced executable Python with "
        "comments / blank lines / pure docstring. If the code was genuine "
        "dead code, restate that explicitly in the Final Answer and "
        "explain why nothing called it. Otherwise revert the deletion "
        "and diagnose the underlying problem — commenting out a failing "
        "call doesn't fix the bug, it hides it."
    )


# ──────────────────────────────────────────────────────────────────
# §30 — broad-except suppression guard
# ──────────────────────────────────────────────────────────────────
# Reject finals that introduce a NEW ``except Exception: pass`` /
# ``except: ...`` / ``except BaseException: # ignore`` pattern.
# Existing suppressions being moved around are NOT flagged because
# the parsing helper compares new_string to old_string.

_BROAD_EXCEPT_LOOKBACK = 12


def _trajectory_introduces_broad_except(steps: list[ReActStep]) -> bool:
    window = steps[-_BROAD_EXCEPT_LOOKBACK:] if steps else []
    return any(_step_introduces_broad_except_suppression(step) for step in window)


def _broad_except_suppression_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    is_code_mode: bool,
) -> str | None:
    """Reject finals that introduced a new bare-except / Exception
    suppression in non-test runtime code."""
    if not is_code_mode or not steps:
        return None
    if _final_answer_requests_user_help(final_answer):
        return None
    if not _trajectory_introduces_broad_except(steps):
        return None
    return (
        "Cannot finish yet: a new broad-except suppression was added "
        "(``except Exception: pass`` / ``except: ...`` / silent body). "
        "Catching all exceptions and discarding them hides bugs and "
        "makes future debugging much harder. Either narrow the except "
        "to the specific exception type you can recover from, log the "
        "error explicitly, or remove the try/except wrapper entirely "
        "if the operation should propagate failures."
    )


# ──────────────────────────────────────────────────────────────────
# §32 — frontend outside tsconfig.json `include` guard
# ──────────────────────────────────────────────────────────────────
# tsconfig.json's `include` is a hand-maintained list. Editing a
# .ts/.tsx that isn't in that list means tsc never sees the change.
# This guard fires at Final Answer time when a recent edit lands
# outside the include set AND (heuristic) no successful TypeScript
# verifier ran since.

_TSCONFIG_LOOKBACK = 12


def _trajectory_edits_outside_tsconfig(steps: list[ReActStep]) -> list[str]:
    """Return paths that were edited but live outside tsconfig.include.

    Each path appears at most once; we use the LAST edit's path so the
    error message points at the most recent surface area.
    """
    window = steps[-_TSCONFIG_LOOKBACK:] if steps else []
    seen: list[str] = []
    for step in window:
        if not _step_edits_frontend_outside_tsconfig(step):
            continue
        parsed = _parse_action(step.action)
        if parsed is None:
            continue
        tool_name, args = parsed
        path = args.get("path") or args.get("file") or args.get("file_path")
        if isinstance(path, str) and path not in seen:
            seen.append(path)
    return seen


def _frontend_outside_tsconfig_include_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    is_code_mode: bool,
) -> str | None:
    if not is_code_mode or not steps:
        return None
    if _final_answer_requests_user_help(final_answer):
        return None
    paths = _trajectory_edits_outside_tsconfig(steps)
    if not paths:
        return None
    preview = "; ".join(paths[:3])
    if len(paths) > 3:
        preview += f"; +{len(paths) - 3} more"
    return (
        "Cannot finish yet: edit(s) landed on TypeScript file(s) NOT listed "
        f"in frontend/tsconfig.json's `include`: {preview}. tsc will silently "
        "skip these — the change won't be type-checked. Either add the file(s) "
        "to `include`, or move the change into a file already covered. If the "
        "edit is intentionally outside the type-check surface (e.g. a script), "
        "say so explicitly in the Final Answer."
    )


# ──────────────────────────────────────────────────────────────────
# §33 — oversized single-edit guard
# ──────────────────────────────────────────────────────────────────
# A single edit step that writes more than _OVERSIZED_EDIT_LINE_THRESHOLD
# lines of NEW content is high-blast-radius. We don't reject these
# outright — the agent may legitimately need to rewrite a file — but
# we require a verification step to follow within the same trajectory.

_OVERSIZED_EDIT_LOOKBACK = 12


def _trajectory_has_oversized_edit(steps: list[ReActStep]) -> tuple[int, str | None]:
    """Return (line_count, path) for the latest oversized edit, or
    ``(0, None)`` if none in the lookback window."""
    window = steps[-_OVERSIZED_EDIT_LOOKBACK:] if steps else []
    for step in reversed(window):
        if not _step_is_oversized_edit(step):
            continue
        from runtime.core.cerebrum.react_parsing import _step_payload_line_count

        parsed = _parse_action(step.action)
        if parsed is None:
            continue
        tool_name, args = parsed
        path = args.get("path") or args.get("file") or args.get("file_path")
        return (_step_payload_line_count(step), path if isinstance(path, str) else None)
    return (0, None)


def _oversized_single_edit_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    is_code_mode: bool,
) -> str | None:
    if not is_code_mode or not steps:
        return None
    if _final_answer_requests_user_help(final_answer):
        return None
    line_count, path = _trajectory_has_oversized_edit(steps)
    if line_count <= 0:
        return None
    if _has_code_verification(steps):
        return None
    target = path or "an unknown file"
    return (
        f"Cannot finish yet: a single edit wrote {line_count} new lines to "
        f"{target} — well above the 200-line threshold for safe single-shot "
        "changes — without a verification step in this trajectory. Run the "
        "appropriate verifier (pytest / ruff / tsc / lint) on the affected "
        "file before reporting completion. Large rewrites are exactly where "
        "errors hide, and 'looks right to me' is not enough at this size."
    )


# ──────────────────────────────────────────────────────────────────
# §38 — time.sleep in production-path guard
# ──────────────────────────────────────────────────────────────────
# Adding ``time.sleep(...)`` to non-test runtime code is almost always
# a "wait for race condition" anti-pattern. Reject unless the same
# trajectory contains explicit acknowledgement (a clear comment in the
# new content explaining WHY a sleep is the right primitive — e.g.
# rate-limit cooperation, polling-with-backoff, retry).

_SLEEP_LOOKBACK = 12


def _trajectory_sleep_hits(steps: list[ReActStep]) -> list[str]:
    """Paths where new time.sleep / asyncio.sleep was added."""
    out: list[str] = []
    window = steps[-_SLEEP_LOOKBACK:] if steps else []
    for step in window:
        if not _step_introduces_sleep(step):
            continue
        parsed = _parse_action(step.action)
        if parsed is None:
            continue
        _name, args = parsed
        path = args.get("path") or args.get("file") or args.get("file_path")
        if isinstance(path, str) and path not in out:
            out.append(path)
    return out


def _sleep_in_production_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    is_code_mode: bool,
) -> str | None:
    if not is_code_mode or not steps:
        return None
    if _final_answer_requests_user_help(final_answer):
        return None
    paths = _trajectory_sleep_hits(steps)
    if not paths:
        return None
    preview = "; ".join(paths[:3])
    if len(paths) > 3:
        preview += f"; +{len(paths) - 3} more"
    return (
        f"Cannot finish yet: time.sleep / asyncio.sleep was added to non-test "
        f"runtime code: {preview}. Bare sleeps in production are almost "
        "always 'wait for the race condition to resolve itself' — they "
        "mask bugs and make tests flaky. Use the appropriate primitive "
        "(asyncio.Event, threading.Event, retry helper, explicit poll "
        "with cancel) or — if the sleep is deliberately part of a rate "
        "limiter / backoff / cooperative yield — add a comment explaining "
        "WHY and remove this nag by re-running."
    )


# ──────────────────────────────────────────────────────────────────
# §40 — full-file rewrite guard
# ──────────────────────────────────────────────────────────────────
# ``write_text_file`` overwriting an existing >100-line file silently
# drops anything the model "forgot" — common with imports, helpers,
# and docstrings. We allow the rewrite ONLY when the same trajectory
# previously edited the same file with edit_file/multi_edit_file
# (proving the model has surveyed the existing content) OR the file
# is brand new (doesn't exist on disk).

_FULL_REWRITE_LOOKBACK = 12


def _trajectory_full_rewrite_hits(
    steps: list[ReActStep],
    *,
    repo_root: str | None = None,
) -> list[tuple[str, int]]:
    """Return ``[(path, existing_line_count)]`` for full-rewrite attempts
    that lack a prior surgical edit on the same path within the lookback."""
    window = steps[-_FULL_REWRITE_LOOKBACK:] if steps else []
    bad: list[tuple[str, int]] = []
    for idx, step in enumerate(window):
        is_rewrite, path, line_count = _step_is_full_file_rewrite_attempt(
            step,
            repo_root=repo_root,
        )
        if not is_rewrite or not path:
            continue
        # Has any earlier step in the trajectory edited this file
        # surgically?
        prior = window[:idx]
        if any(_step_is_surgical_edit_on(s, target_path=path) for s in prior):
            continue
        bad.append((path, line_count))
    return bad


def _full_file_rewrite_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    is_code_mode: bool,
) -> str | None:
    if not is_code_mode or not steps:
        return None
    if _final_answer_requests_user_help(final_answer):
        return None
    bad = _trajectory_full_rewrite_hits(steps)
    if not bad:
        return None
    preview = "; ".join(f"{path} ({lines} existing lines)" for path, lines in bad[:3])
    if len(bad) > 3:
        preview += f"; +{len(bad) - 3} more"
    return (
        "Cannot finish yet: write_text_file overwrote existing file(s) without "
        f"first surveying them via edit_file or read_file: {preview}. "
        "Full-file rewrites silently drop imports, helpers, comments, and "
        "edge-case branches the model forgot. Use edit_file for surgical "
        "changes, OR read_file the existing content first and then "
        "write_text_file with full coverage. If the rewrite truly is "
        "deliberate (e.g. you scrubbed the file from a known-good "
        "template), add an explicit edit_file step earlier in the "
        "trajectory or note the intent in the Final Answer."
    )


# ──────────────────────────────────────────────────────────────────
# §44 — print() in production guard
# ──────────────────────────────────────────────────────────────────
# echo runs on ``logging`` everywhere. Adding a bare ``print(...)``
# to non-CLI runtime code is a debug leftover. CLI/script paths
# (runtime/cli.py, scripts/, tools/) are exempt at the parsing layer.

_PRINT_LOOKBACK = 12


def _trajectory_print_hits(steps: list[ReActStep]) -> list[str]:
    out: list[str] = []
    window = steps[-_PRINT_LOOKBACK:] if steps else []
    for step in window:
        if not _step_introduces_print(step):
            continue
        parsed = _parse_action(step.action)
        if parsed is None:
            continue
        _name, args = parsed
        path = args.get("path") or args.get("file") or args.get("file_path")
        if isinstance(path, str) and path not in out:
            out.append(path)
    return out


def _print_in_production_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    is_code_mode: bool,
) -> str | None:
    if not is_code_mode or not steps:
        return None
    if _final_answer_requests_user_help(final_answer):
        return None
    paths = _trajectory_print_hits(steps)
    if not paths:
        return None
    preview = "; ".join(paths[:3])
    if len(paths) > 3:
        preview += f"; +{len(paths) - 3} more"
    return (
        "Cannot finish yet: print(...) was added to non-test, non-CLI "
        f"runtime code: {preview}. echo uses ``logging`` everywhere "
        "(``_logger = logging.getLogger(__name__)`` + ``_logger.info(...)`` "
        "etc.). Bare prints leak debug output to stdout, can't be filtered "
        "by level, and break log scrapers. Replace with the appropriate "
        "log call — or, if the print was a debugging leftover, remove it "
        "entirely."
    )


# ──────────────────────────────────────────────────────────────────
# §45 — hardcoded personal path guard
# ──────────────────────────────────────────────────────────────────
# Catch hardcoded ``C:\Users\<name>``, ``/Users/<name>``,
# ``/home/<name>`` paths in committed code. These are user-specific
# and break on every other developer's machine.

_HARDCODED_PATH_LOOKBACK = 12


def _trajectory_hardcoded_path_hits(steps: list[ReActStep]) -> dict[str, str]:
    out: dict[str, str] = {}
    window = steps[-_HARDCODED_PATH_LOOKBACK:] if steps else []
    for step in window:
        labels = _step_introduces_hardcoded_path(step)
        if not labels:
            continue
        parsed = _parse_action(step.action)
        if parsed is None:
            continue
        _name, args = parsed
        path = args.get("path") or args.get("file") or args.get("file_path")
        if isinstance(path, str):
            out[path] = ", ".join(labels)
    return out


def _hardcoded_personal_path_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    is_code_mode: bool,
) -> str | None:
    if not is_code_mode or not steps:
        return None
    if _final_answer_requests_user_help(final_answer):
        return None
    hits = _trajectory_hardcoded_path_hits(steps)
    if not hits:
        return None
    items = list(hits.items())
    preview = "; ".join(f"{path} ({label})" for path, label in items[:3])
    if len(items) > 3:
        preview += f"; +{len(items) - 3} more"
    return (
        "Cannot finish yet: a user-specific path was hardcoded into "
        f"committed code: {preview}. ``C:\\Users\\<name>`` / "
        "``/Users/<name>`` / ``/home/<name>`` are machine-local and will "
        "break on every other developer's environment. Use ``Path.home()``, "
        "``os.path.expanduser('~')``, an environment variable, or read "
        "the location from config.yaml. If this really is a path that "
        "must point to a specific user dir at runtime, accept it via "
        "config / CLI flag rather than baking it into source."
    )


# ──────────────────────────────────────────────────────────────────
# §57 — async-without-await guard
# ──────────────────────────────────────────────────────────────────
# Catches ``async def foo():`` whose non-trivial body never awaits,
# yields, or uses async-with / async-for. The function returns a
# coroutine the caller likely never awaits — a silent bug.

_ASYNC_NO_AWAIT_LOOKBACK = 12


def _trajectory_async_no_await_hits(steps: list[ReActStep]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    window = steps[-_ASYNC_NO_AWAIT_LOOKBACK:] if steps else []
    for step in window:
        names = _step_introduces_async_without_await(step)
        if not names:
            continue
        parsed = _parse_action(step.action)
        if parsed is None:
            continue
        _name, args = parsed
        path = args.get("path") or args.get("file") or args.get("file_path")
        if isinstance(path, str):
            out.setdefault(path, []).extend(names)
    return out


def _async_without_await_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    is_code_mode: bool,
) -> str | None:
    if not is_code_mode or not steps:
        return None
    if _final_answer_requests_user_help(final_answer):
        return None
    hits = _trajectory_async_no_await_hits(steps)
    if not hits:
        return None
    items = [f"{path} :: {name}" for path, names in hits.items() for name in names]
    preview = "; ".join(items[:3])
    if len(items) > 3:
        preview += f"; +{len(items) - 3} more"
    return (
        f"Cannot finish yet: new ``async def`` function(s) never await, "
        f"yield, or use async-with/async-for in their body: {preview}. "
        "An async function with a synchronous body returns a coroutine "
        "the caller likely never awaits — meaning the body never runs. "
        "Either drop the ``async`` keyword (make it a normal def), or add "
        "the ``await`` you intended. If the function is genuinely an "
        "abstract / protocol stub, mark it ``@abstractmethod`` or use a "
        "``...`` body."
    )


# ──────────────────────────────────────────────────────────────────
# §59 — exception-swallow-via-log guard
# ──────────────────────────────────────────────────────────────────
# ``except SomeError: log.error(...)`` without re-raising silently
# discards the failure. Looks like proper handling; isn't. This is
# the more deceptive sibling of §30 broad-except-pass.

_LOG_SWALLOW_LOOKBACK = 12


def _trajectory_log_swallow_paths(steps: list[ReActStep]) -> list[str]:
    out: list[str] = []
    window = steps[-_LOG_SWALLOW_LOOKBACK:] if steps else []
    for step in window:
        if not _step_introduces_log_swallow(step):
            continue
        parsed = _parse_action(step.action)
        if parsed is None:
            continue
        _name, args = parsed
        path = args.get("path") or args.get("file") or args.get("file_path")
        if isinstance(path, str) and path not in out:
            out.append(path)
    return out


def _exception_swallow_via_log_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    is_code_mode: bool,
) -> str | None:
    if not is_code_mode or not steps:
        return None
    if _final_answer_requests_user_help(final_answer):
        return None
    paths = _trajectory_log_swallow_paths(steps)
    if not paths:
        return None
    preview = "; ".join(paths[:3])
    if len(paths) > 3:
        preview += f"; +{len(paths) - 3} more"
    return (
        "Cannot finish yet: ``except: log.error(...)`` without a re-raise "
        f"was added to runtime code: {preview}. Logging an error and then "
        "continuing silently swallows the failure — the next reader sees "
        "the log call and assumes it's handled, but the program just "
        "marches on with bad state. Either re-raise after logging "
        "(``raise``), narrow the except to a specific type you can "
        "actually recover from, or remove the try/except wrapper "
        "entirely if propagation is the right behavior."
    )


# ──────────────────────────────────────────────────────────────────
# §61 — long-function guard
# ──────────────────────────────────────────────────────────────────
# A new function whose substantive body exceeds 150 lines is too
# long to test, read, or reason about cohesively. We don't flag
# refactors that move existing long functions — only fresh additions.

_LONG_FUNCTION_LOOKBACK = 12


def _trajectory_long_function_hits(steps: list[ReActStep]) -> dict[str, list[tuple[str, int]]]:
    out: dict[str, list[tuple[str, int]]] = {}
    window = steps[-_LONG_FUNCTION_LOOKBACK:] if steps else []
    for step in window:
        hits = _step_introduces_long_function(step)
        if not hits:
            continue
        parsed = _parse_action(step.action)
        if parsed is None:
            continue
        _name, args = parsed
        path = args.get("path") or args.get("file") or args.get("file_path")
        if isinstance(path, str):
            out.setdefault(path, []).extend(hits)
    return out


def _long_function_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    is_code_mode: bool,
) -> str | None:
    if not is_code_mode or not steps:
        return None
    if _final_answer_requests_user_help(final_answer):
        return None
    hits = _trajectory_long_function_hits(steps)
    if not hits:
        return None
    items = [
        f"{path} :: {name} ({lines} lines)"
        for path, fn_hits in hits.items()
        for name, lines in fn_hits
    ]
    preview = "; ".join(items[:3])
    if len(items) > 3:
        preview += f"; +{len(items) - 3} more"
    return (
        f"Cannot finish yet: new function(s) exceed the 150-line "
        f"complexity threshold: {preview}. Long functions are hard to "
        "test, hard to read, and tend to bundle multiple responsibilities. "
        "Split into smaller helpers organised around a single concept "
        "each. If the length is fundamentally necessary (state machine, "
        "long switch dispatch), state that explicitly in the Final "
        "Answer so the next reviewer knows it was a deliberate choice."
    )


__all__ = [
    "_async_without_await_guard",
    "_broad_except_suppression_guard",
    "_commented_out_as_fix_guard",
    "_exception_swallow_via_log_guard",
    "_frontend_outside_tsconfig_include_guard",
    "_full_file_rewrite_guard",
    "_hardcoded_personal_path_guard",
    "_long_function_guard",
    "_oversized_single_edit_guard",
    "_print_in_production_guard",
    "_sleep_in_production_guard",
]
