"""Code-quality detectors for ReAct write steps.

Extracted from ``react_parsing.py``. Owns the comment-out-as-fix,
broad-except-suppression, tsconfig-path, oversized-edit, secret-leak,
destructive-call, sleep-in-loop, and full-file-rewrite detectors —
each exposing a ``_payload_*`` predicate plus a ``_step_introduces_*`` /
``_step_*`` adapter.

Depends on ``react_types``, the ``_react_parsing_tools`` leaf, and the
``_react_parsing_steps`` / ``_react_parsing_verification`` helpers.

Note: merged with the former ``_react_parsing_payload`` module (pure
``_payload_has_*`` concurrency / single-flight / path-boundary text
heuristics) on 2026-08-04.
"""

from __future__ import annotations

import json
import os as _os
import re
from typing import Any

from runtime.core.cerebrum._react_parsing_core import (
    _extract_step_path,
    _extract_step_payloads,
)
from runtime.core.cerebrum._react_parsing_tools import _parse_action
from runtime.core.cerebrum._react_parsing_verification import _is_test_path
from runtime.core.cerebrum.react_types import ReActStep

# ──────────────────────────────────────────────────────────────────
# §28 — comment-out-as-fix detection
# ──────────────────────────────────────────────────────────────────
# Catch the failure mode where the model "fixes" a problem by deleting
# or commenting out the problematic call/test/assertion rather than
# diagnosing it. Heuristic: in an edit_file pair, the new_string is
# made up purely of blank lines and comments, OR the new_string is a
# strict subset that drops a previously-present executable construct
# (assert, raise, function call) and replaces it with a comment.
#
# Conservative: only flags when old_string had executable Python code
# AND new_string has none. Refactors that genuinely delete dead code
# and replace it with a leading docstring/comment will trip this — we
# accept that small false-positive in exchange for catching the real
# anti-pattern.

_PYTHON_EXECUTABLE_LINE_RE = re.compile(
    r"^\s*(?!#)(?!\"\"\")(?!''')[A-Za-z_\(\[\{\@]",
    re.MULTILINE,
)
_PYTHON_KEY_EXECUTABLE_RE = re.compile(
    r"\b(?:assert|raise|return\s|yield|"
    r"def\s|class\s|if\s|for\s|while\s|try:|except|with\s|"
    r"await\s|async\s)",
)
_PYTHON_LINE_COMMENT_RE = re.compile(r"#[^\n]*")


def _strip_comments_for_executable_check(text: str) -> str:
    """Remove # line comments so the executable-keyword scan doesn't
    false-positive on commented-out code like ``# was: raise X``.

    Only strips line comments, not docstrings — docstrings are caught
    by the line-level regex's lookbehind for ``\"\"\"`` / ``'''``.
    """
    return _PYTHON_LINE_COMMENT_RE.sub("", text or "")


def _payload_has_executable_python(text: str) -> bool:
    """Whether the text payload contains at least one executable Python
    line (not comment-only, not pure docstring/blank)."""
    if not text:
        return False
    stripped = _strip_comments_for_executable_check(text)
    if _PYTHON_KEY_EXECUTABLE_RE.search(stripped):
        return True
    return bool(_PYTHON_EXECUTABLE_LINE_RE.search(stripped))


def _step_replaced_code_with_comment(step: ReActStep) -> bool:
    """Edit step that replaces executable Python with comment/blank only.

    Returns True when:
      * action is edit_file / multi_edit_file / str_replace etc.
      * path is .py and not a test path
      * old_string contains executable Python
      * new_string contains NO executable Python
    """
    parsed = _parse_action(step.action)
    if parsed is None:
        return False
    name, args = parsed
    if name not in {"edit_file", "multi_edit_file", "edit_code", "str_replace"}:
        return False
    path = _extract_step_path(step)
    if not path or not path.lower().endswith((".py", ".pyi")):
        return False
    if _is_test_path(path):
        return False
    pairs: list[tuple[str, str]] = []
    if isinstance(args.get("old_string"), str) and isinstance(args.get("new_string"), str):
        pairs.append((args["old_string"], args["new_string"]))
    edits = args.get("edits")
    if isinstance(edits, list):
        for edit in edits:
            if not isinstance(edit, dict):
                continue
            old = edit.get("old_string") or edit.get("old_str")
            new = edit.get("new_string") or edit.get("new_str")
            if isinstance(old, str) and isinstance(new, str):
                pairs.append((old, new))
    for old, new in pairs:
        if _payload_has_executable_python(old) and not _payload_has_executable_python(new):
            return True
    return False


# ──────────────────────────────────────────────────────────────────
# §30 — broad-except suppression detection
# ──────────────────────────────────────────────────────────────────
# Catch the failure mode where the model "fixes" an exception by
# wrapping it in ``try: ... except Exception: pass`` (or ``except:
# pass``) without doing anything with the error. This is one of the
# most common forms of papering over a bug.
#
# Heuristic: new_string introduces a bare ``except:`` or
# ``except Exception:`` (or ``except BaseException:``) block whose body
# is one of: ``pass``, ``...``, a comment-only line, or a single
# ``return None``. We require old_string to NOT contain that same
# pattern, so adding new suppression is flagged but moving an
# already-existing one isn't.

_BROAD_EXCEPT_HEAD_RE = re.compile(
    r"(?:^|\n)(?P<indent>[ \t]*)except\s*"
    r"(?:\(\s*(?:Exception|BaseException)\s*\)|"
    r"Exception|BaseException|)"
    r"\s*(?:as\s+\w+\s*)?:[ \t]*\n",
)

_SUPPRESSION_BODY_RE = re.compile(
    r"^[ \t]+(?:pass|\.\.\.|return\s+None|return)\s*(?:#.*)?$",
)


def _payload_has_broad_except_suppression(text: str) -> bool:
    """Detect ``except [Exception|BaseException|]: <suppression-body>``."""
    if not text:
        return False
    for match in _BROAD_EXCEPT_HEAD_RE.finditer(text):
        # Find the FIRST non-empty line after the except header line.
        rest = text[match.end() :]
        first_line = ""
        for line in rest.splitlines():
            if line.strip():
                first_line = line
                break
        if not first_line:
            continue
        if _SUPPRESSION_BODY_RE.match(first_line):
            return True
        if first_line.lstrip().startswith("#"):
            return True
    return False


def _step_introduces_broad_except_suppression(step: ReActStep) -> bool:
    """Whether this write step adds a NEW broad-except suppression.

    Skips test paths and non-Python paths. Compares new_string and
    write payloads against old_string so existing suppression isn't
    flagged repeatedly when code is moved around.
    """
    path = _extract_step_path(step)
    if not path or not path.lower().endswith((".py", ".pyi")):
        return False
    if _is_test_path(path):
        return False
    new_text, old_text = _extract_step_payloads(step)
    return _payload_has_broad_except_suppression(
        new_text
    ) and not _payload_has_broad_except_suppression(old_text)


# ──────────────────────────────────────────────────────────────────
# §32 — frontend file outside tsconfig.json `include` detection
# ──────────────────────────────────────────────────────────────────
# tsconfig.json's `include` is a hand-maintained list of 22 files in
# this repo. Editing a .ts/.tsx file that ISN'T in that list means the
# typechecker silently won't see the change — a real failure mode the
# memory references at reference_verify_commands.md. Same applies if
# the file matches `exclude`.
#
# Strategy: parse tsconfig.json once per call, normalise paths, and
# match the edited path against include/exclude. We DON'T cache the
# parsed result — the file might change mid-trajectory, and the cost
# of re-reading is trivial.

_TSCONFIG_PATH_CANDIDATES: tuple[str, ...] = (
    "frontend/tsconfig.json",
    "tsconfig.json",
)


def _strip_jsonc_comments(text: str) -> str:
    """Cheap JSONC → JSON converter. Drops // line comments and
    /* block */ comments. Naive: doesn't understand strings, so a
    URL inside a string with ``//`` will be mangled. tsconfig.json
    rarely embeds URLs, and the consequence of a mangled parse is
    "guard returns None" — non-fatal.
    """
    if not text:
        return text
    # Block comments first.
    text = re.sub(r"/\*[\s\S]*?\*/", "", text)
    # Line comments — match leading whitespace + // up to newline.
    return re.sub(r"(^|\s)//[^\n]*", r"\1", text)


def _load_tsconfig(repo_root: str | None = None) -> dict[str, Any] | None:
    root = repo_root or _os.getcwd()
    for candidate in _TSCONFIG_PATH_CANDIDATES:
        path = _os.path.join(root, candidate)
        try:
            with open(path, encoding="utf-8") as fh:
                raw = fh.read()
        except OSError:
            continue
        try:
            return json.loads(_strip_jsonc_comments(raw))
        except json.JSONDecodeError:
            return None
    return None


def _normalize_frontend_path(path: str) -> str:
    """Return path relative to ``frontend/`` if it lives there,
    otherwise the path unchanged. Always uses forward slashes.
    """
    norm = path.replace("\\", "/").lstrip("./")
    if norm.startswith("frontend/"):
        norm = norm[len("frontend/") :]
    return norm


def _matches_tsconfig_pattern(rel_path: str, pattern: str) -> bool:
    """Approximate tsc's pattern semantics. Supports:
      * exact match
      * ``dir/`` prefix-match (treats trailing path-segment as dir)
      * ``*`` and ``**`` glob — anchored to the start of ``rel_path``
    Ignores extension-rewriting nuances (tsc is more lenient); we
    bias toward false-negative (saying "matched" when in doubt).
    """
    rel_path = rel_path.replace("\\", "/")
    pattern = pattern.replace("\\", "/")
    if rel_path == pattern:
        return True
    # Bare directory → covers everything beneath it.
    if (
        not pattern.endswith("/")
        and "." not in pattern.rsplit("/", 1)[-1]
        and rel_path.startswith(pattern + "/")
    ):
        return True
    if pattern.endswith("/") and rel_path.startswith(pattern):
        return True
    # Glob: convert tsc-style globs to a regex.
    if "*" in pattern:
        regex = re.escape(pattern)
        regex = regex.replace(r"\*\*/", r"(?:.*/)?")
        regex = regex.replace(r"\*\*", r".*")
        regex = regex.replace(r"\*", r"[^/]*")
        return bool(re.fullmatch(regex, rel_path))
    return False


def _is_frontend_path_outside_tsconfig(
    path: str,
    *,
    repo_root: str | None = None,
) -> bool:
    """Whether a TypeScript edit lands outside the tsc include set.

    Returns False (silent) for non-frontend paths, non-TS files, paths
    that match include, paths inside exclude, or when tsconfig.json
    can't be located/parsed (don't nag if oracle missing).
    """
    if not path:
        return False
    norm = path.replace("\\", "/").lower()
    if not norm.endswith((".ts", ".tsx", ".js", ".jsx", ".cjs", ".mjs")):
        return False
    if "/frontend/" not in "/" + norm.lstrip("/") and not norm.startswith("frontend/"):
        return False
    config = _load_tsconfig(repo_root)
    if not config:
        return False
    rel = _normalize_frontend_path(path)
    excludes = config.get("exclude") or []
    if any(_matches_tsconfig_pattern(rel, str(pattern)) for pattern in excludes):
        return False  # Excluded on purpose — not the guard's business.
    includes = config.get("include") or []
    if not includes:
        return False
    return not any(_matches_tsconfig_pattern(rel, str(pattern)) for pattern in includes)


def _step_edits_frontend_outside_tsconfig(
    step: ReActStep,
    *,
    repo_root: str | None = None,
) -> bool:
    path = _extract_step_path(step)
    if not path:
        return False
    return _is_frontend_path_outside_tsconfig(path, repo_root=repo_root)


# ──────────────────────────────────────────────────────────────────
# §33 — oversized single-edit detection
# ──────────────────────────────────────────────────────────────────
# A single ``write_text_file`` / ``edit_file`` payload that rewrites
# more than N lines in one shot is a high-blast-radius change. The
# model often accumulates errors at this scale because the LLM has
# to keep too much context coherent. Threshold tuned empirically for
# this repo (median real edit is < 30 lines).

_OVERSIZED_EDIT_LINE_THRESHOLD = 200


def _count_payload_lines(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def _step_payload_line_count(step: ReActStep) -> int:
    """Total NEW-content lines across ``content``/``new_string``/``edits``.

    For a write_text_file we use the full content. For edit/multi-edit
    we sum new_string sizes — old_string is irrelevant because we care
    about what's being inserted.
    """
    new_text, _old_text = _extract_step_payloads(step)
    return _count_payload_lines(new_text)


def _step_is_oversized_edit(
    step: ReActStep,
    *,
    threshold: int = _OVERSIZED_EDIT_LINE_THRESHOLD,
) -> bool:
    """Whether this single edit step writes more than ``threshold`` lines.

    Skips test paths (test fixture files can be legitimately huge) and
    non-Python/TS-style code paths (config files, fixtures, JSON).
    """
    path = _extract_step_path(step)
    if not path:
        return False
    if _is_test_path(path):
        return False
    norm = path.lower()
    if not norm.endswith((".py", ".pyi", ".ts", ".tsx", ".js", ".jsx")):
        return False
    return _step_payload_line_count(step) > threshold


# ──────────────────────────────────────────────────────────────────
# §34 — secret-in-payload detection
# ──────────────────────────────────────────────────────────────────
# Detect well-known secret prefixes embedded in write payloads. We
# flag at the parsing layer; the guard layer surfaces it. This is a
# conservative regex set — false positives are tolerable because
# leaking a real key is much worse than nagging on a false hit.
#
# Patterns:
#   * ``sk-`` followed by 20+ chars (OpenAI / Anthropic-style)
#   * ``ghp_`` / ``ghs_`` / ``gho_`` / ``ghu_`` — GitHub PAT prefixes
#   * ``AKIA`` followed by 16 alnum chars — AWS access key
#   * ``xox[abps]-`` — Slack tokens
#   * ``-----BEGIN (RSA |EC |OPENSSH |DSA |PRIVATE)?(PRIVATE )?KEY-----``

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("OpenAI/Anthropic-style key", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b")),
    ("GitHub PAT", re.compile(r"\bgh[psou]_[A-Za-z0-9]{20,}\b")),
    ("AWS access key", re.compile(r"\bAKIA[A-Z0-9]{16}\b")),
    ("Slack token", re.compile(r"\bxox[abps]-[A-Za-z0-9-]{10,}\b")),
    ("Private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    # Hex API tokens of length 40+ assigned to obvious key-like names.
    (
        "Inline assigned credential",
        re.compile(
            r"(?:api[_-]?key|secret[_-]?key|access[_-]?token|password)"
            r"['\"]?\s*[:=]\s*['\"][A-Za-z0-9_\-]{20,}['\"]",
            re.IGNORECASE,
        ),
    ),
)


def _detect_secrets_in_payload(text: str) -> list[str]:
    """Return labels of any secret patterns matched in ``text``."""
    if not text:
        return []
    hits: list[str] = []
    for label, pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            hits.append(label)
    return hits


# A credential-shaped string is treated as a clearly-marked non-secret
# fixture — the exact escape the secret-leak guard message advertises
# ("if the string is genuinely a non-secret fixture — make that explicit")
# — when its assignment name or the value itself carries a fixture marker
# word. Test fixtures, docs examples, and example configs legitimately
# embed mock keys; those must not hard-fire a fail-closed guard and 3-strike
# the task. A single unmarked secret in the payload keeps the whole write
# flagged: real keys are random and won't carry these marker words.
_SECRET_FIXTURE_NAME_RE = re.compile(
    r"(?:^|[^A-Za-z0-9_])"
    r"(?:MOCK|FAKE|DUMMY|TEST|EXAMPLE|SAMPLE|PLACEHOLDER|FIXTURE)"
    r"[A-Za-z0-9_]*\s*=",
)
_SECRET_FIXTURE_VALUE_RE = re.compile(
    r"\b(?:mock|fake|dummy|test|example|sample|placeholder|fixture|"
    r"nonsecret|non-secret)\b",
    re.IGNORECASE,
)


def _secret_payload_is_clearly_fixture(text: str) -> bool:
    """Whether every secret-shaped match in ``text`` is a marked fixture.

    Returns False when ``text`` has no secret-shaped matches at all
    (nothing to judge) or any match is unmarked (a real credential).
    """
    any_hit = False
    for _label, pattern in _SECRET_PATTERNS:
        for match in pattern.finditer(text):
            any_hit = True
            line_start = text.rfind("\n", 0, match.start()) + 1
            line_end = text.find("\n", match.end())
            if line_end == -1:
                line_end = len(text)
            line = text[line_start:line_end]
            if _SECRET_FIXTURE_NAME_RE.search(line):
                continue
            if _SECRET_FIXTURE_VALUE_RE.search(match.group(0)):
                continue
            return False
    return any_hit


def _step_introduces_secret(step: ReActStep) -> list[str]:
    """List of secret-pattern labels matched in this write step's NEW
    content. Old content is excluded so existing committed-and-rotated
    leaks don't keep tripping the guard. Empty list = nothing matched.

    Test paths are exempt outright (mirroring every sibling security
    detector — ``_step_introduces_destructive_call`` etc.): tests
    legitimately embed mock credentials. Non-test writes are exempt only
    when every credential-shaped string is a clearly-marked fixture.
    """
    path = _extract_step_path(step)
    if path and _is_test_path(path):
        return []
    new_text, old_text = _extract_step_payloads(step)
    if not new_text and not old_text:
        return []
    new_hits = set(_detect_secrets_in_payload(new_text))
    old_hits = set(_detect_secrets_in_payload(old_text))
    introduced = new_hits - old_hits
    if not introduced:
        return []
    if _secret_payload_is_clearly_fixture(new_text):
        return []
    return sorted(introduced)


# ──────────────────────────────────────────────────────────────────
# §37 — destructive-call detection
# ──────────────────────────────────────────────────────────────────
# Catch the failure mode where the model adds a destructive filesystem
# or process call in production code without any safeguard:
#   * shutil.rmtree
#   * os.remove / os.unlink / Path.unlink
#   * os.removedirs
#   * subprocess.run / call / Popen with ``rm -rf`` / ``del /F`` etc.
#
# We only flag NEW additions (new_string vs old_string diff) on
# non-test Python paths. Test files are exempt because tests legitimately
# create and tear down fixtures.

_DESTRUCTIVE_CALL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("shutil.rmtree", re.compile(r"\bshutil\s*\.\s*rmtree\s*\(")),
    ("os.remove / os.unlink", re.compile(r"\bos\s*\.\s*(?:remove|unlink|removedirs)\s*\(")),
    ("Path.unlink / Path.rmdir", re.compile(r"\.(?:unlink|rmdir)\s*\(")),
    (
        "shell rm -rf",
        re.compile(r"(?:^|[\s\"'])rm\s+-[a-zA-Z]*r[a-zA-Z]*f", re.IGNORECASE),
    ),
    (
        "shell del /F",
        re.compile(r"(?:^|[\s\"'])del\s+/[fFsSqQ]", re.IGNORECASE),
    ),
)


def _detect_destructive_calls_in_payload(text: str) -> list[str]:
    if not text:
        return []
    hits: list[str] = []
    for label, pattern in _DESTRUCTIVE_CALL_PATTERNS:
        if pattern.search(text):
            hits.append(label)
    return hits


def _step_introduces_destructive_call(step: ReActStep) -> list[str]:
    """List of destructive-call labels added by this write step.

    Diffs new payload vs old payload so existing destructive calls
    being moved aren't repeatedly flagged. Skips test paths and
    non-Python files (shell scripts, .sh, etc. are out of scope —
    too many legitimate uses).
    """
    path = _extract_step_path(step)
    if not path or not path.lower().endswith((".py", ".pyi")):
        return []
    if _is_test_path(path):
        return []
    new_text, old_text = _extract_step_payloads(step)
    new_hits = set(_detect_destructive_calls_in_payload(new_text))
    old_hits = set(_detect_destructive_calls_in_payload(old_text))
    return sorted(new_hits - old_hits)


# ──────────────────────────────────────────────────────────────────
# §38 — time.sleep in production-path detection
# ──────────────────────────────────────────────────────────────────
# Adding ``time.sleep(N)`` to non-test runtime code is almost always
# a "wait for race condition to resolve" anti-pattern. Legitimate use
# cases (rate-limiter, retry-with-backoff) typically use a more
# specific construct (asyncio.sleep, tenacity, explicit retry helper).
# We flag the bare ``time.sleep`` and ``asyncio.sleep`` additions and
# let the model justify on a case-by-case basis.

_SLEEP_CALL_RE = re.compile(
    r"(?:^|[^A-Za-z_.])(?:time\s*\.\s*sleep|asyncio\s*\.\s*sleep)\s*\(",
)


def _payload_has_sleep_call(text: str) -> bool:
    if not text:
        return False
    return bool(_SLEEP_CALL_RE.search(text))


def _step_introduces_sleep(step: ReActStep) -> bool:
    """Whether this write step adds a NEW time.sleep / asyncio.sleep
    in non-test Python production code.

    Conservative: skip retry/backoff helpers (tenacity, etc.) implicitly
    by only catching the literal ``time.sleep`` / ``asyncio.sleep``
    forms.
    """
    path = _extract_step_path(step)
    if not path or not path.lower().endswith((".py", ".pyi")):
        return False
    if _is_test_path(path):
        return False
    new_text, old_text = _extract_step_payloads(step)
    return _payload_has_sleep_call(new_text) and not _payload_has_sleep_call(old_text)


# ──────────────────────────────────────────────────────────────────
# §40 — full-file rewrite detection
# ──────────────────────────────────────────────────────────────────
# ``write_text_file`` to a path that already exists and has substantial
# content is a high-risk move: the model could subtly drop imports /
# helpers / docstrings while "rewriting". We require either:
#   * the path is new (file doesn't exist on disk yet), or
#   * the same trajectory used edit_file / multi_edit_file on the SAME
#     file (proving the model knows the existing content).
# Otherwise, prefer edit_file with surgical changes.

_FULL_REWRITE_THRESHOLD = 100  # lines


def _step_is_full_file_rewrite_attempt(
    step: ReActStep,
    *,
    repo_root: str | None = None,
) -> tuple[bool, str | None, int]:
    """Return ``(is_rewrite, path, existing_line_count)``.

    ``is_rewrite`` is True when:
      * action is write_text_file (or alias) — full payload write
      * path resolves to an existing file > _FULL_REWRITE_THRESHOLD lines
      * path is non-test Python/TS code

    Caller is expected to additionally check whether the same trajectory
    contains a surgical edit on the same path before firing the guard.
    """
    parsed = _parse_action(step.action)
    if parsed is None:
        return (False, None, 0)
    name, _args = parsed
    if name not in {"write_text_file", "write_file", "create_file"}:
        return (False, None, 0)
    path = _extract_step_path(step)
    if not path:
        return (False, None, 0)
    if _is_test_path(path):
        return (False, path, 0)
    norm = path.lower()
    if not norm.endswith((".py", ".pyi", ".ts", ".tsx", ".js", ".jsx")):
        return (False, path, 0)
    abs_path = path
    if repo_root is not None:
        abs_path = _os.path.join(repo_root, path)
    try:
        with open(abs_path, encoding="utf-8") as fh:
            existing = fh.read()
    except OSError:
        # File doesn't exist (or can't read) → new file, no rewrite risk.
        return (False, path, 0)
    line_count = existing.count("\n") + (0 if existing.endswith("\n") else 1)
    return (line_count > _FULL_REWRITE_THRESHOLD, path, line_count)


def _step_is_surgical_edit_on(step: ReActStep, *, target_path: str) -> bool:
    """Whether a step is a surgical edit_file/multi_edit_file on the
    given target path. Used to whitelist a full-rewrite when the model
    has demonstrably read/edited the file surgically first."""
    parsed = _parse_action(step.action)
    if parsed is None:
        return False
    name, _args = parsed
    if name not in {"edit_file", "multi_edit_file", "edit_code", "str_replace"}:
        return False
    path = _extract_step_path(step)
    return path == target_path


# ──────────────────────────────────────────────────────────────────
# Payload anti-pattern detectors (merged from _react_parsing_payload)
# ──────────────────────────────────────────────────────────────────
# Pure, deterministic ``_payload_has_*`` text heuristics (no
# ``ReActStep`` dependency) that flag concurrency / single-flight /
# path-boundary anti-patterns embedded in write payloads. The guard
# layer wraps them with step-shape handlers.

_AMBIGUOUS_INFLIGHT_IDENTITY_RE = re.compile(
    r"if\s+(?:self\.)?(?P<map>[A-Za-z_][A-Za-z0-9_]*)\.get\(\s*"
    r"(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*\)\s+is\s+(?:not\s+)?"
    r"(?P<pending>[A-Za-z_][A-Za-z0-9_]*)\s*:"
)


def _payload_has_inflight_identity_comparison(text: str) -> bool:
    return bool(text and _AMBIGUOUS_INFLIGHT_IDENTITY_RE.search(text))


def _payload_has_ambiguous_inflight_leader_election(text: str) -> bool:
    """Detect re-reading an in-flight map to infer who created its entry.

    Once a shared pending object has been inserted, both its creator and every
    follower read the same object back.  An identity comparison performed
    after leaving the lock therefore cannot elect a leader; all callers can
    take the loader path.  A creator flag captured inside the locked
    ``pending is None`` branch is the auditable form.
    """
    if not text or "pending" not in text.lower() or ".get(" not in text:
        return False
    for match in _AMBIGUOUS_INFLIGHT_IDENTITY_RE.finditer(text):
        map_name = re.escape(match.group("map"))
        key_name = re.escape(match.group("key"))
        pending_name = re.escape(match.group("pending"))
        creates_entry = re.search(
            rf"if\s+{pending_name}\s+is\s+None\s*:"
            rf"[\s\S]{{0,700}}(?:self\.)?{map_name}\[\s*{key_name}\s*\]\s*=\s*"
            rf"{pending_name}\b",
            text,
        )
        explicit_election = re.search(r"\b(?:is_)?leader\s*=", text)
        if creates_entry and not explicit_election:
            return True
    return False


_WAITER_CALL_RE = re.compile(r"\.wait(?:_for)?\s*\([^\n]*\)|\.wait\s*\(\s*\)")
_MAPPING_POP_RE = re.compile(r"\.(?:pop)\(\s*(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*(?:,|\))")


def _payload_has_destructive_waiter_result_pop(text: str) -> bool:
    """Whether followers destructively consume one shared load result."""
    if not text or ".pop(" not in text or ".wait" not in text:
        return False
    for wait_match in _WAITER_CALL_RE.finditer(text):
        # Inspect only the follower's post-wait return path.  A later leader
        # branch may legitimately remove the in-flight map entry *after* it
        # publishes result/exception on a mutable object; the old unbounded
        # regex crossed that return boundary and misclassified safe cleanup.
        segment = text[wait_match.end() : wait_match.end() + 1200]
        terminal = re.search(r"\b(?:return|raise)\b", segment)
        if terminal is not None:
            segment = segment[: terminal.end()]
        if _MAPPING_POP_RE.search(segment):
            return True
    return False


_STALE_IMMUTABLE_WAITER_FALLBACK_RE = re.compile(
    r"\.wait(?:_for)?\s*\([^\n]*\)"
    r"[\s\S]{0,1600}?"
    r"(?P<map>(?:self\.)?[A-Za-z_][A-Za-z0-9_]*)\.get\(\s*"
    r"(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*,\s*"
    r"(?P<snapshot>[A-Za-z_][A-Za-z0-9_]*)\s*\)"
)
_DELETED_PENDING_WAITER_READ_RE = re.compile(
    r"\.wait(?:_for)?\s*\([^\n]*\)"
    r"[\s\S]{0,1600}?"
    r"(?P<map>(?:self\.)?[A-Za-z_][A-Za-z0-9_]*)\[\s*"
    r"(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*\]"
)


def _payload_has_stale_immutable_waiter_snapshot(text: str) -> bool:
    """Detect a waiter falling back to an immutable pre-wait tuple snapshot.

    Replacing ``pending[key]`` with a new tuple and then deleting the entry is
    not visible through the tuple a follower captured before ``wait()``.  A
    post-wait ``map.get(key, pending)`` therefore falls back to stale
    ``(event, None, None)`` and can return ``None`` or hide an exception.
    Mutable pending objects are safe because the captured object itself is
    updated before the event is signalled.
    """
    if not text or ".wait" not in text or "del " not in text:
        return False
    for match in _STALE_IMMUTABLE_WAITER_FALLBACK_RE.finditer(text):
        map_name = re.escape(match.group("map"))
        key_name = re.escape(match.group("key"))
        snapshot_name = re.escape(match.group("snapshot"))
        tuple_snapshot = re.search(
            rf"(?:[A-Za-z_][A-Za-z0-9_]*\s*,\s*){{2,}}[A-Za-z_][A-Za-z0-9_]*"
            rf"\s*=\s*{snapshot_name}\b",
            text,
        )
        tuple_replacement = re.search(
            rf"{map_name}\[\s*{key_name}\s*\]\s*=\s*\(",
            text,
        )
        deletes_entry = re.search(
            rf"del\s+{map_name}\[\s*{key_name}\s*\]",
            text,
        )
        if tuple_snapshot and tuple_replacement and deletes_entry:
            return True
    for match in _DELETED_PENDING_WAITER_READ_RE.finditer(text):
        map_name = re.escape(match.group("map"))
        key_name = re.escape(match.group("key"))
        tuple_replacement = re.search(
            rf"{map_name}\[\s*{key_name}\s*\]\s*=\s*\(",
            text,
        )
        deletes_entry = re.search(
            rf"del\s+{map_name}\[\s*{key_name}\s*\]",
            text,
        )
        if tuple_replacement and deletes_entry:
            return True
    return False


_TERMINAL_PENDING_TUPLE_RE = re.compile(
    r"(?P<map>(?:self\.)?[A-Za-z_][A-Za-z0-9_]*(?:pending|inflight)[A-Za-z0-9_]*)"
    r"\[\s*(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*\]\s*=\s*\("
    r"(?P<body>[^\n)]{1,500})\)",
    re.IGNORECASE,
)


def _payload_has_terminal_pending_entry_leak(text: str) -> bool:
    """Detect a completed in-flight tuple that is never removed.

    Keeping a terminal ``pending[key] = (event, value, error)`` entry makes
    every later caller look like a follower.  In a TTL cache that means an
    expired key can keep returning the old completed flight forever; a failed
    flight can likewise poison every retry.  Waiters may retain a mutable
    per-flight object, but the key must leave the *in-flight map* once the
    leader has published terminal state.
    """
    if not text or ".wait" not in text or ".set(" not in text or "loader(" not in text:
        return False
    assignments = list(_TERMINAL_PENDING_TUPLE_RE.finditer(text))
    if len(assignments) < 2:
        return False
    by_slot: dict[tuple[str, str], list[re.Match[str]]] = {}
    for match in assignments:
        slot = (match.group("map"), match.group("key"))
        by_slot.setdefault(slot, []).append(match)
    for (map_name, key_name), slot_assignments in by_slot.items():
        bodies = [match.group("body") for match in slot_assignments]
        has_initial_state = any(body.count("None") >= 2 for body in bodies)
        has_terminal_state = any(body.count("None") < 2 for body in bodies)
        if not (has_initial_state and has_terminal_state):
            continue
        escaped_map = re.escape(map_name)
        escaped_key = re.escape(key_name)
        removes_slot = re.search(
            rf"(?:del\s+{escaped_map}\[\s*{escaped_key}\s*\]"
            rf"|{escaped_map}\.pop\(\s*{escaped_key}\b)",
            text,
        )
        if not removes_slot:
            return True
    return False


def _payload_has_loader_barrier_deadlock(text: str) -> bool:
    """Detect a test loader waiting alone on a ``threading.Barrier``.

    In a single-flight test only the elected leader enters ``loader``;
    followers wait on the flight event.  A barrier placed inside that loader
    can therefore never collect the follower threads and deadlocks the test.
    Synchronising workers *before* ``get_or_load`` is the valid pattern.
    """
    if not text or "Barrier(" not in text or "get_or_load" not in text:
        return False
    lines = text.splitlines()
    for index, line in enumerate(lines):
        definition = re.match(
            r"^(?P<indent>[ \t]+)def\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\(",
            line,
            re.IGNORECASE,
        )
        if definition is None or "loader" not in definition.group("name").lower():
            continue
        indent_width = len(definition.group("indent").expandtabs(4))
        body_lines: list[str] = []
        for candidate in lines[index + 1 :]:
            if not candidate.strip():
                body_lines.append(candidate)
                continue
            candidate_indent = len(candidate) - len(candidate.lstrip(" \t"))
            if candidate_indent <= indent_width:
                break
            body_lines.append(candidate)
        body = "\n".join(body_lines)
        for wait in re.finditer(
            r"(?P<barrier>[A-Za-z_][A-Za-z0-9_]*)\.wait\s*\(",
            body,
        ):
            barrier = wait.group("barrier")
            assignment = re.search(
                rf"\b{re.escape(barrier)}\s*=\s*(?:threading\.)?Barrier\s*\("
                r"\s*(?P<parties>\d+)",
                text,
            )
            if assignment is None:
                continue
            total_waits = len(re.findall(rf"\b{re.escape(barrier)}\.wait\s*\(", text))
            loader_name = re.escape(definition.group("name"))
            passed_to_cache = re.search(
                rf"get_or_load\s*\([^\n]{{0,300}}\b{loader_name}\b",
                text,
            )
            parties = int(assignment.group("parties"))
            # The only bounded loader-barrier shape we tolerate is an
            # explicit two-party rendezvous between the elected loader and
            # one controller thread.  With N>2, extra static wait sites do
            # not prove N runtime participants (v31 had Barrier(5) but only
            # loader + main thread could ever reach it).  Worker threads in a
            # single-flight test wait on the flight event, not in loader.
            if passed_to_cache and not (parties == 2 and total_waits >= 2):
                return True
    return False


def _payload_has_wait_while_lock_held(text: str) -> bool:
    """Detect blocking on an event/future while retaining a map mutex."""
    if not text or ".wait(" not in text or "lock" not in text.lower():
        return False
    lines = text.splitlines()
    for index, line in enumerate(lines):
        context = re.match(
            r"^(?P<indent>[ \t]*)with\s+(?P<lock>(?:self\.)?[A-Za-z_][A-Za-z0-9_]*lock[A-Za-z0-9_]*)\s*:",
            line,
            re.IGNORECASE,
        )
        if context is None:
            continue
        indent_width = len(context.group("indent").expandtabs(4))
        body_lines: list[str] = []
        for candidate in lines[index + 1 :]:
            if not candidate.strip():
                body_lines.append(candidate)
                continue
            candidate_indent = len(candidate) - len(candidate.lstrip(" \t"))
            if candidate_indent <= indent_width:
                break
            body_lines.append(candidate)
        body = "\n".join(body_lines)
        if re.search(r"\b[A-Za-z_][A-Za-z0-9_.]*\.wait\s*\(", body):
            return True
    for acquire in re.finditer(
        r"(?P<lock>(?:self\.)?[A-Za-z_][A-Za-z0-9_]*lock[A-Za-z0-9_]*)"
        r"\.acquire\s*\(",
        text,
        re.IGNORECASE,
    ):
        lock_name = re.escape(acquire.group("lock"))
        release = re.search(rf"{lock_name}\.release\s*\(", text[acquire.end() :])
        segment_end = acquire.end() + (release.start() if release is not None else 1600)
        segment = text[acquire.end() : segment_end]
        if re.search(r"\b[A-Za-z_][A-Za-z0-9_.]*\.wait\s*\(", segment):
            return True
    return False


_SINGLE_PASS_URL_DECODE_RE = re.compile(r"\bunquote(?:_plus)?\s*\(")
_PATH_BOUNDARY_PAYLOAD_MARKERS = (
    "pathboundaryerror",
    "relative_to(",
    "commonpath(",
    "is_relative_to(",
    "symlink",
    "path traversal",
)


def _payload_has_single_pass_url_decode(text: str) -> bool:
    """Detect one-shot URL decoding in path-boundary validation.

    A single ``unquote`` turns a double-encoded traversal into a still-
    encoded path, so a subsequent canonical containment check sees an
    innocuous filename.  Repeated decoding in a loop (or two explicit nested
    decodes) is not flagged.  Callers must separately establish that the
    payload belongs to path-boundary code before treating this as a defect.
    """

    if not text:
        return False
    calls = list(_SINGLE_PASS_URL_DECODE_RE.finditer(text))
    if len(calls) != 1:
        return False
    call_line_start = text.rfind("\n", 0, calls[0].start()) + 1
    call_line = text[call_line_start : text.find("\n", calls[0].end())]
    call_indent = len(call_line) - len(call_line.lstrip(" \t"))
    prefix_lines = text[:call_line_start].splitlines()
    for line in reversed(prefix_lines):
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" \t"))
        if indent >= call_indent:
            continue
        stripped = line.strip()
        if re.match(r"(?:while\b|for\b).*:\s*(?:#.*)?$", stripped):
            return False
        # The nearest enclosing block is not a loop; outer blocks cannot
        # make the call repeat without the call being nested under them.
        break
    return True


def _payload_looks_like_path_boundary(text: str) -> bool:
    lowered = str(text or "").lower()
    return "unquote" in lowered and any(
        marker in lowered for marker in _PATH_BOUNDARY_PAYLOAD_MARKERS
    )
