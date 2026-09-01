"""Security + quality guards (post-step / pre-Final-Answer gates).

Extracted from ``react_guards.py`` (2026-06-06) — paired with the
detectors in ``react_security_detectors.py``. Each guard returns
either ``None`` (let Final Answer through) or a message explaining
why the model must keep working.

╔══════════════════════════════════════════════════════════════════════╗
║ react_security_guards.py · navigation map (~350 lines).              ║
║                                                                      ║
║   §63 dynamic exec (eval / exec / __import__)        ~L36           ║
║   §65 shell injection (subprocess shell=True)         ~L92           ║
║   §66 unsafe deserialization (pickle / yaml.load)     ~L146          ║
║   §67 network call inside loop                        ~L203          ║
║   §69 repeated string literal                         ~L251          ║
║   §70 magic number (time/size unit)                   ~L308          ║
║                                                                      ║
║ Each guard has a paired ``_trajectory_*_hits(steps)`` predicate     ║
║ that walks the recent-step window and aggregates detector output.   ║
╚══════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

from runtime.core.cerebrum.react_parsing import (
    _has_test_write,
    _parse_action,
    _step_introduces_destructive_call,
    _step_introduces_dynamic_exec,
    _step_introduces_magic_number,
    _step_introduces_network_in_loop,
    _step_introduces_repeated_literal,
    _step_introduces_secret,
    _step_introduces_shell_injection,
    _step_introduces_unsafe_deser,
)
from runtime.core.cerebrum.react_types import ReActStep

# ``_final_answer_requests_user_help`` lives in react_guards.py and has
# nuanced tail-inspection logic + extensive marker tables. Import it
# lazily at call time to avoid a circular import (react_guards imports
# this module to wire the guards into its registry).


def _user_help_requested(final_answer: str) -> bool:
    from runtime.core.cerebrum.react_guards import _final_answer_requests_user_help

    # These guards are fail-closed security gates: only a *genuine*
    # hand-off (a tight marker like 请确认 / please confirm / 无法继续)
    # may escape. The loose short-answer path — any mention of
    # token/权限/permission in a <150 char final — is too wide for them:
    # a brief report that happens to mention those words would silently
    # clear the guard while the risky call stays in the trajectory. The
    # secret-in-payload guard skips this escape entirely (a leak while
    # asking for help is still a leak).
    return _final_answer_requests_user_help(final_answer, allow_short_loose=False)


# ──────────────────────────────────────────────────────────────────
# §63 — eval / exec / __import__ guard
# ──────────────────────────────────────────────────────────────────

_DYNAMIC_EXEC_LOOKBACK = 12


def _trajectory_dynamic_exec_hits(steps: list[ReActStep]) -> dict[str, str]:
    out: dict[str, str] = {}
    window = steps[-_DYNAMIC_EXEC_LOOKBACK:] if steps else []
    for step in window:
        labels = _step_introduces_dynamic_exec(step)
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


def _dynamic_exec_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    is_code_mode: bool,
) -> str | None:
    if not steps:
        return None
    if _user_help_requested(final_answer):
        return None
    hits = _trajectory_dynamic_exec_hits(steps)
    if not hits:
        return None
    items = list(hits.items())
    preview = "; ".join(f"{path} ({label})" for path, label in items[:3])
    if len(items) > 3:
        preview += f"; +{len(items) - 3} more"
    return (
        f"Cannot finish yet: dynamic-execution call(s) added to runtime "
        f"code: {preview}. ``eval`` / ``exec`` / ``__import__`` open "
        "the door to code-injection bugs and make static analysis useless. "
        "Almost any legitimate need has a safer alternative: "
        "``ast.literal_eval`` for parsing literals, an explicit dispatch "
        "dict for plugin loading, ``importlib.import_module`` for "
        "well-known module names. Rewrite the code to one of those — a "
        "justification in the Final Answer does not clear this guard. If "
        "you genuinely believe dynamic exec is required and untrusted, "
        "ask the user to confirm before continuing."
    )


# ──────────────────────────────────────────────────────────────────
# §65 — shell-injection guard
# ──────────────────────────────────────────────────────────────────

_SHELL_INJECTION_LOOKBACK = 12


def _trajectory_shell_injection_hits(steps: list[ReActStep]) -> dict[str, str]:
    out: dict[str, str] = {}
    window = steps[-_SHELL_INJECTION_LOOKBACK:] if steps else []
    for step in window:
        labels = _step_introduces_shell_injection(step)
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


def _shell_injection_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    is_code_mode: bool,
) -> str | None:
    if not steps:
        return None
    if _user_help_requested(final_answer):
        return None
    hits = _trajectory_shell_injection_hits(steps)
    if not hits:
        return None
    items = list(hits.items())
    preview = "; ".join(f"{p} ({label})" for p, label in items[:3])
    if len(items) > 3:
        preview += f"; +{len(items) - 3} more"
    return (
        f"Cannot finish yet: shell-injection surface(s) added: {preview}. "
        "``subprocess(..., shell=True)`` / ``os.system`` / ``os.popen`` "
        "evaluate the entire command via the shell, so any non-static "
        "argument is a code-injection risk. Pass an argv LIST instead "
        "(``subprocess.run(['cmd', arg1, arg2])``) — that bypasses the "
        "shell entirely. Rewrite the code to an argv list — a "
        "justification in the Final Answer does not clear this guard. If "
        "you genuinely need shell features (pipes, globs) and the "
        "alternative is unacceptable, ask the user to confirm before "
        "continuing."
    )


# ──────────────────────────────────────────────────────────────────
# §66 — unsafe deserialization guard
# ──────────────────────────────────────────────────────────────────

_UNSAFE_DESER_LOOKBACK = 12


def _trajectory_unsafe_deser_hits(steps: list[ReActStep]) -> dict[str, str]:
    out: dict[str, str] = {}
    window = steps[-_UNSAFE_DESER_LOOKBACK:] if steps else []
    for step in window:
        labels = _step_introduces_unsafe_deser(step)
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


def _unsafe_deser_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    is_code_mode: bool,
) -> str | None:
    if not steps:
        return None
    if _user_help_requested(final_answer):
        return None
    hits = _trajectory_unsafe_deser_hits(steps)
    if not hits:
        return None
    items = list(hits.items())
    preview = "; ".join(f"{p} ({label})" for p, label in items[:3])
    if len(items) > 3:
        preview += f"; +{len(items) - 3} more"
    return (
        f"Cannot finish yet: unsafe deserialization call(s) added: {preview}. "
        "``pickle.loads`` / ``marshal.loads`` / ``yaml.load`` (without "
        "``Loader=SafeLoader``) execute arbitrary code on attacker-"
        "controlled input. Use ``json.loads`` for data exchange, "
        "``yaml.safe_load`` for YAML, or a typed schema validator "
        "(pydantic, msgpack with strict mode) instead. Rewrite the code "
        "to a safe loader — a justification in the Final Answer does not "
        "clear this guard. If you genuinely must use pickle (e.g. for "
        "trusted ML model weights) and no alternative is acceptable, ask "
        "the user to confirm the trust boundary before continuing."
    )


# ──────────────────────────────────────────────────────────────────
# §67 — network-in-loop guard
# ──────────────────────────────────────────────────────────────────

_NETWORK_IN_LOOP_LOOKBACK = 12


def _trajectory_network_in_loop_paths(steps: list[ReActStep]) -> list[str]:
    seen: list[str] = []
    seen_set: set[str] = set()
    window = steps[-_NETWORK_IN_LOOP_LOOKBACK:] if steps else []
    for step in window:
        if not _step_introduces_network_in_loop(step):
            continue
        parsed = _parse_action(step.action)
        if parsed is None:
            continue
        _name, args = parsed
        path = args.get("path") or args.get("file") or args.get("file_path")
        if isinstance(path, str) and path not in seen_set:
            seen.append(path)
            seen_set.add(path)
    return seen


def _network_in_loop_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    is_code_mode: bool,
) -> str | None:
    if not is_code_mode or not steps:
        return None
    if _user_help_requested(final_answer):
        return None
    paths = _trajectory_network_in_loop_paths(steps)
    if not paths:
        return None
    preview = "; ".join(paths[:3])
    if len(paths) > 3:
        preview += f"; +{len(paths) - 3} more"
    return (
        f"Cannot finish yet: network call inside a loop added in {preview}. "
        "An O(n) loop that issues a network call per iteration scales "
        "poorly and is rarely the right approach. Consider: (a) batching "
        "the calls (e.g. one bulk endpoint instead of N individual GETs), "
        "(b) async + ``asyncio.gather`` for fan-out, or (c) caching "
        "the result outside the loop if the data is invariant. If a "
        "per-iteration network call is genuinely required (e.g. paginated "
        "API), justify it in the Final Answer."
    )


# ──────────────────────────────────────────────────────────────────
# §69 — duplicate-string-literal guard
# ──────────────────────────────────────────────────────────────────

_REPEATED_LITERAL_LOOKBACK = 12


def _trajectory_repeated_literal_hits(
    steps: list[ReActStep],
) -> dict[str, list[tuple[str, int]]]:
    out: dict[str, list[tuple[str, int]]] = {}
    window = steps[-_REPEATED_LITERAL_LOOKBACK:] if steps else []
    for step in window:
        repeats = _step_introduces_repeated_literal(step)
        if not repeats:
            continue
        parsed = _parse_action(step.action)
        if parsed is None:
            continue
        _name, args = parsed
        path = args.get("path") or args.get("file") or args.get("file_path")
        if isinstance(path, str):
            out[path] = repeats
    return out


def _repeated_literal_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    is_code_mode: bool,
) -> str | None:
    if not is_code_mode or not steps:
        return None
    if _user_help_requested(final_answer):
        return None
    hits = _trajectory_repeated_literal_hits(steps)
    if not hits:
        return None
    items = list(hits.items())
    parts: list[str] = []
    for path, repeats in items[:3]:
        repeat_strs = ", ".join(f"{lit!r}({n}x)" for lit, n in repeats[:2])
        parts.append(f"{path}: {repeat_strs}")
    preview = "; ".join(parts)
    if len(items) > 3:
        preview += f"; +{len(items) - 3} more files"
    return (
        f"Cannot finish yet: repeated string literal(s) introduced: "
        f"{preview}. Strings appearing 3+ times in a payload should "
        "usually be a named constant — that way a typo / spelling "
        "change happens in one place and the constant's name documents "
        "intent. If the repetition is intentional (e.g. test fixtures, "
        "user-facing copy that should stay literal), justify it in the "
        "Final Answer."
    )


# ──────────────────────────────────────────────────────────────────
# §70 — magic number guard
# ──────────────────────────────────────────────────────────────────

_MAGIC_NUMBER_LOOKBACK = 12


def _trajectory_magic_number_hits(steps: list[ReActStep]) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    window = steps[-_MAGIC_NUMBER_LOOKBACK:] if steps else []
    for step in window:
        nums = _step_introduces_magic_number(step)
        if not nums:
            continue
        parsed = _parse_action(step.action)
        if parsed is None:
            continue
        _name, args = parsed
        path = args.get("path") or args.get("file") or args.get("file_path")
        if isinstance(path, str):
            out[path] = nums
    return out


def _magic_number_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    is_code_mode: bool,
) -> str | None:
    if not is_code_mode or not steps:
        return None
    if _user_help_requested(final_answer):
        return None
    hits = _trajectory_magic_number_hits(steps)
    if not hits:
        return None
    items = list(hits.items())
    parts: list[str] = []
    for path, nums in items[:3]:
        parts.append(f"{path}: {', '.join(str(n) for n in nums[:3])}")
    preview = "; ".join(parts)
    if len(items) > 3:
        preview += f"; +{len(items) - 3} more files"
    return (
        f"Cannot finish yet: magic number(s) introduced: {preview}. "
        "Bare numeric literals like ``86400`` (seconds in a day) or "
        "``1048576`` (1 MiB in bytes) should be named constants — "
        "the name documents intent, prevents typos, and makes the "
        "value reusable. Use ``SECONDS_PER_DAY = 86400`` or "
        "``datetime.timedelta(days=1).total_seconds()`` instead. "
        "If the number is genuinely arbitrary (e.g. a hash table size "
        "you tuned empirically), document the choice in a comment."
    )


# ──────────────────────────────────────────────────────────────────
# §34 — secret-in-payload guard
# ──────────────────────────────────────────────────────────────────
# Editing a runtime file with an embedded secret (sk-..., ghp_...,
# AKIA..., private key block, ``api_key="..."``) is a serious leak.
# We fire on ANY new secret-shaped string in any code-write trajectory
# step — secrets in non-code files (env templates) are caught by the
# generic pattern set, which is correct.

_SECRET_LOOKBACK = 12


def _trajectory_secret_hits(steps: list[ReActStep]) -> dict[str, str]:
    """Map ``path -> secret-label`` for any step that introduced a
    new secret pattern. Last write wins for a given path."""
    out: dict[str, str] = {}
    window = steps[-_SECRET_LOOKBACK:] if steps else []
    for step in window:
        labels = _step_introduces_secret(step)
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


def _secret_in_payload_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    is_code_mode: bool,
) -> str | None:
    """Reject finals where a write introduced a credential-shaped string.

    No help-request short circuit — leaking a secret while asking for
    help is still a leak. The guard always fires when a hit lands.
    """
    if not steps:
        return None
    hits = _trajectory_secret_hits(steps)
    if not hits:
        return None
    items = list(hits.items())
    preview = "; ".join(f"{path} ({label})" for path, label in items[:3])
    if len(items) > 3:
        preview += f"; +{len(items) - 3} more"
    return (
        "Cannot finish yet: a write step introduced a credential-shaped "
        f"value in: {preview}. Hard-coding API keys, GitHub PATs, AWS "
        'access keys, private-key blocks, or `api_key="..."` literals '
        "into source is a security incident. Move the value to an "
        "environment variable or local config (gitignored), or — if the "
        "string is genuinely a non-secret fixture — make that explicit "
        "(e.g. wrap with a clearly-marked test helper) and try again."
    )


# ──────────────────────────────────────────────────────────────────
# §37 — destructive-call guard
# ──────────────────────────────────────────────────────────────────
# Adding ``shutil.rmtree`` / ``os.remove`` / ``Path.unlink`` / shell
# ``rm -rf`` to non-test runtime code is a high-blast-radius change.
# We don't reject outright — sometimes the agent legitimately needs to
# clean up — but we require explicit acknowledgement: either the code
# is wrapped in safe_rm helpers (the existing echo tooling at
# runtime/execution/arms/safe_rm.py handles this), OR the trajectory
# touched a test that exercises the destructive path.

_DESTRUCTIVE_LOOKBACK = 12


def _trajectory_destructive_hits(steps: list[ReActStep]) -> dict[str, str]:
    """Map ``path -> labels`` for any step that introduced a new
    destructive call. Last write wins per path."""
    out: dict[str, str] = {}
    window = steps[-_DESTRUCTIVE_LOOKBACK:] if steps else []
    for step in window:
        labels = _step_introduces_destructive_call(step)
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


def _new_destructive_call_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    is_code_mode: bool,
) -> str | None:
    """Reject finals where a write step introduced a new destructive
    filesystem/shell call without a paired test edit."""
    if not steps:
        return None
    if _user_help_requested(final_answer):
        return None
    hits = _trajectory_destructive_hits(steps)
    if not hits:
        return None
    if _has_test_write(steps):
        return None  # Tests touched in trajectory — assume coverage.
    items = list(hits.items())
    preview = "; ".join(f"{path} ({label})" for path, label in items[:3])
    if len(items) > 3:
        preview += f"; +{len(items) - 3} more"
    return (
        "Cannot finish yet: a destructive filesystem/process call was "
        f"added without a paired test edit: {preview}. "
        "rm -rf / shutil.rmtree / Path.unlink / os.remove are easy to "
        "get catastrophically wrong (wrong path, race conditions, "
        "permission loops). Either wrap the call in the project's "
        "safe_rm helper (runtime/execution/arms/safe_rm.py), or add a "
        "test that exercises the cleanup with proper fixtures. A "
        "justification in the Final Answer does not clear this guard — "
        "only a paired test edit or safe_rm rewrite does."
    )


__all__ = [
    "_dynamic_exec_guard",
    "_magic_number_guard",
    "_network_in_loop_guard",
    "_new_destructive_call_guard",
    "_repeated_literal_guard",
    "_secret_in_payload_guard",
    "_shell_injection_guard",
    "_trajectory_destructive_hits",
    "_trajectory_dynamic_exec_hits",
    "_trajectory_magic_number_hits",
    "_trajectory_network_in_loop_paths",
    "_trajectory_repeated_literal_hits",
    "_trajectory_secret_hits",
    "_trajectory_shell_injection_hits",
    "_trajectory_unsafe_deser_hits",
    "_unsafe_deser_guard",
]
