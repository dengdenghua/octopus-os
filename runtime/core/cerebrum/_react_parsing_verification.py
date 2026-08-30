"""Verification-trail detection helpers for ReAct steps.

Extracted from ``react_parsing.py``. Owns the verification/violation
markers and predicates (``_has_code_verification`` /
``_step_is_verify`` / ``_path_language`` / ``_node_command_is_verification``),
the test-path / public-symbol / wire-schema / dependency checks, and the
false-verification-claim + red-observation detectors.

Depends on ``react_types``, the ``_react_parsing_tools`` leaf, and the
``_react_parsing_core`` helpers (``_extract_step_path`` /
``_extract_step_payloads`` / ``_is_code_write_step``).
"""

from __future__ import annotations

import re
import sys as _sys

from runtime.core.cerebrum._react_parsing_core import (
    _extract_step_path,
    _extract_step_payloads,
    _is_code_write_step,
)
from runtime.core.cerebrum._react_parsing_tools import _parse_action
from runtime.core.cerebrum.react_types import ReActStep

_DEDICATED_VERIFY_TOOLS: frozenset[str] = frozenset(
    {
        "run_tests",
        "run_checks",
        "verify",
        "lint_check",
        "format_code",
    }
)
_VERIFY_TOOLS: frozenset[str] = frozenset(
    {
        "exec_shell",
        "shell_command",
        "bash",
        *_DEDICATED_VERIFY_TOOLS,
    }
)

# Verification markers grouped by language. The "all" bucket is the
# legacy flat list — kept for the language-agnostic
# ``_has_code_verification`` helper. The per-language buckets power
# the §19 language-mismatch guard: writing TS but only running pytest
# does NOT count as verifying the TS edit.
_LANG_VERIFY_MARKERS: dict[str, tuple[str, ...]] = {
    "python": (
        "pytest",
        "unittest",
        "ruff",
        "mypy",
        "py_compile",
        "python -m compileall",
        "pyright",
        "flake8",
        "black --check",
    ),
    "typescript": (
        "tsc",
        "npm run typecheck",
        "pnpm typecheck",
        "yarn typecheck",
        "npm run lint",
        "pnpm lint",
        "yarn lint",
        "npm test",
        "pnpm test",
        "yarn test",
        "eslint",
        "vitest",
        "playwright",
        "jest",
    ),
    "rust": ("cargo check", "cargo build", "cargo test", "cargo clippy"),
    "go": ("go build", "go test", "go vet", "golangci-lint"),
}

_VERIFY_MARKERS_ALL: tuple[str, ...] = tuple(
    marker for markers in _LANG_VERIFY_MARKERS.values() for marker in markers
)


# File-extension → language bucket. Kept conservative: only languages
# we have a verifier story for. Unknown extensions return ``None`` so
# the guard treats them as "we don't know — don't nag".
_EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "typescript",
    ".jsx": "typescript",
    ".mjs": "typescript",
    ".cjs": "typescript",
    ".rs": "rust",
    ".go": "go",
}


def _path_language(path: str | None) -> str | None:
    """Map a file path to a verification-language bucket, or ``None``.

    Returning ``None`` for unknown extensions is intentional: callers
    must treat "unknown language" as "skip the language-specific
    check", not as "verification missing". We'd rather miss a real
    miss than spam a noisy nudge for every Markdown / YAML edit.
    """
    if not path:
        return None
    lowered = path.lower()
    for ext, lang in _EXT_TO_LANG.items():
        if lowered.endswith(ext):
            return lang
    return None


def _step_command_text(step: ReActStep) -> str:
    """Concatenate Action + arg `command` lowercased — for marker scans."""
    parsed = _parse_action(step.action)
    if parsed is None:
        return (step.action or "").lower()
    _name, args = parsed
    command = str(args.get("command") or args.get("cmd") or "")
    return f"{step.action or ''} {command}".lower()


_NODE_VERIFY_SCRIPT_RE = re.compile(
    r"^\s*node(?:\s+--?[a-z0-9_-]+)*\s+"
    r"(?!-e(?:\s|$))"
    r"[^\n]*?(?:test|spec|verify|verification|check)[^\n]*$",
    re.IGNORECASE,
)
_NODE_INLINE_FAILURE_BRANCH_RE = re.compile(
    r"^\s*node\s+-e(?:\s|$)[\s\S]*?"
    r"process\.exit\s*\(\s*(?:1\b|[^)]*\b(?:fail|failed|error|errors)\b[^)]*)\)",
    re.IGNORECASE,
)


def _node_command_is_verification(command: str) -> bool:
    """Recognize executable Node verification without trusting fake green text.

    Static fixtures often have no package manifest, so agents reasonably run
    ``node verify.js`` or an inline race harness.  A script name must carry a
    verification marker; inline code must contain a genuine non-zero failure
    branch.  Merely printing ``tests passed`` and exiting zero is deliberately
    excluded.
    """

    return bool(
        _NODE_VERIFY_SCRIPT_RE.search(command) or _NODE_INLINE_FAILURE_BRANCH_RE.search(command)
    )


def _step_is_verify(step: ReActStep, *, markers: tuple[str, ...]) -> bool:
    actions = step.actions or ([step.action] if step.action else [])
    for action in actions:
        parsed = _parse_action(action)
        if parsed is None:
            continue
        name, args = parsed
        if name in _DEDICATED_VERIFY_TOOLS:
            return True
        if name not in _VERIFY_TOOLS:
            continue
        command = str(args.get("command") or args.get("cmd") or "")
        if "tsc" in markers and _node_command_is_verification(command):
            return True
        haystack = f"{action} {command}".lower()
        if any(marker in haystack for marker in markers):
            return True
    return False


def _has_code_verification(steps: list[ReActStep]) -> bool:
    return any(_step_is_verify(step, markers=_VERIFY_MARKERS_ALL) for step in steps)


def _has_language_specific_verification(
    steps: list[ReActStep],
    *,
    language: str,
) -> bool:
    """True if any step ran a verifier whose markers belong to ``language``.

    Strict variant of ``_has_code_verification`` — used by the §19
    language-mismatch guard. ``language`` must be a key in
    ``_LANG_VERIFY_MARKERS``; unknown languages return False so callers
    can skip the check.
    """
    markers = _LANG_VERIFY_MARKERS.get(language)
    if not markers:
        return False
    return any(_step_is_verify(step, markers=markers) for step in steps)


# ──────────────────────────────────────────────────────────────────
# §20 — new-public-symbol detection (for the test-coverage guard)
# ──────────────────────────────────────────────────────────────────
# Conservative scan: only flag NEW top-level ``def name(`` and
# ``class Name`` introductions in non-test .py edits. We accept missing
# some real cases (private methods, nested defs) in exchange for near-
# zero false positives on refactors / docstring tweaks / import shuffles.

# Top-level public def/class — must be flush-left, name must not start
# with ``_``. ``async def`` covered. ``def __init__`` etc. start with
# underscore so they're already skipped.
_PUBLIC_SYMBOL_INTRO_RE = re.compile(
    r"(?:^|\n)(?:async\s+)?(?:def|class)\s+(?P<name>[A-Za-z][A-Za-z0-9_]*)",
)


def _is_test_path(path: str | None) -> bool:
    """Whether a path looks like a test file or test directory entry.

    ``tests/`` anywhere in the path counts (project-relative or absolute
    on either separator), as does a basename matching ``test_*.py`` or
    ``*_test.py``. Conftest is treated as a test file too.
    """
    if not path:
        return False
    norm = path.replace("\\", "/").lower()
    if "/tests/" in norm or norm.startswith("tests/") or norm == "tests":
        return True
    base = norm.rsplit("/", 1)[-1]
    if base == "conftest.py":
        return True
    if base.startswith("test_") and base.endswith(".py"):
        return True
    return base.endswith("_test.py")


def _extract_write_payload(action: str | None) -> str:
    """Return the textual payload that was written/inserted by an action.

    Concatenates ``content``, ``new_string``, and the ``new_string`` field
    of every entry in ``edits`` (multi_edit_file shape). Old/source text is
    explicitly excluded — we only care about what's NEW.
    """
    parsed = _parse_action(action or "")
    if parsed is None:
        return ""
    _name, args = parsed
    chunks: list[str] = []
    for key in ("content", "new_string", "new_str"):
        value = args.get(key)
        if isinstance(value, str):
            chunks.append(value)
    edits = args.get("edits")
    if isinstance(edits, list):
        for edit in edits:
            if not isinstance(edit, dict):
                continue
            for key in ("new_string", "new_str", "content"):
                value = edit.get(key)
                if isinstance(value, str):
                    chunks.append(value)
    return "\n".join(chunks)


def _step_introduces_python_public_symbol(step: ReActStep) -> bool:
    """Whether this write step adds a NEW top-level public def/class.

    Only fires for .py files that aren't test files. Detects by scanning
    the new-content payload for ``def NAME(`` / ``class NAME`` at column
    0 where NAME does not start with ``_``. Refactors that move existing
    code without adding new defs are NOT flagged because the matched
    line was already present (the guard layer dedups across the whole
    trajectory below).
    """
    path = _extract_step_path(step)
    if not path or not path.lower().endswith((".py", ".pyi")):
        return False
    if _is_test_path(path):
        return False
    payload = _extract_write_payload(step.action)
    if not payload:
        return False
    for match in _PUBLIC_SYMBOL_INTRO_RE.finditer(payload):
        if not match.group("name").startswith("_"):
            return True
    return False


def _has_test_write(steps: list[ReActStep]) -> bool:
    """Any write step targeting a test path (tests/ dir or test_*.py)."""
    for step in steps:
        if not _is_code_write_step(step):
            continue
        path = _extract_step_path(step)
        if path is not None and _is_test_path(path):
            return True
    return False


# ──────────────────────────────────────────────────────────────────
# §21 — public-signature change detection
# ──────────────────────────────────────────────────────────────────
# Catch the failure mode where the model edits a public ``def NAME(...)``
# parameter list (or return annotation) and ships without running a
# typechecker. We don't try to AST-diff the whole file — we just look
# at edit_file old/new pairs and check whether the old line "def F(...)"
# became a different "def F(...)" in the new payload.
#
# Conservative bias: only triggers on edit_file / multi_edit_file
# actions where BOTH old_string and new_string contain a top-level
# public def with the same name, and the parameter list differs. Whole-
# file rewrites via write_text_file are out of scope (we'd need the
# previous content to compare).

_PUBLIC_DEF_LINE_RE = re.compile(
    r"(?:^|\n)\s*(?:async\s+)?def\s+(?P<name>[A-Za-z][A-Za-z0-9_]*)\s*"
    r"\((?P<params>[^)]*)\)\s*(?:->\s*(?P<ret>[^:\n]+?))?\s*:",
)


def _extract_public_signatures(text: str) -> dict[str, tuple[str, str]]:
    """Map ``name -> (params, return_annotation)`` for top-level public defs.

    Names starting with ``_`` are excluded — those are private and a
    signature change there is internal refactoring, not an API break.
    """
    sigs: dict[str, tuple[str, str]] = {}
    if not text:
        return sigs
    for match in _PUBLIC_DEF_LINE_RE.finditer(text):
        name = match.group("name")
        if name.startswith("_"):
            continue
        params = (match.group("params") or "").strip()
        ret = (match.group("ret") or "").strip()
        # Last write wins — duplicate names within one payload chunk
        # shouldn't happen in valid Python, but be defensive.
        sigs[name] = (params, ret)
    return sigs


def _step_changed_public_signature(step: ReActStep) -> bool:
    """Whether this edit changes the parameter list / return annotation
    of a top-level public def (same name in old AND new, different sig).

    Returns False for write_text_file (no old payload to compare),
    non-Python paths, and test-path edits.
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
        old_sigs = _extract_public_signatures(old)
        new_sigs = _extract_public_signatures(new)
        common = set(old_sigs) & set(new_sigs)
        for symbol in common:
            if old_sigs[symbol] != new_sigs[symbol]:
                return True
    return False


# ──────────────────────────────────────────────────────────────────
# §22 — wire-schema change detection
# ──────────────────────────────────────────────────────────────────
# echo has no DB migrations, but it DOES have wire-shape schemas
# that external SDKs depend on (anthropic_compat, openai_gateway,
# protocol/items.py). A change there without a paired contract test
# can silently break SDK clients. We look at write actions whose path
# matches one of the wire-schema patterns; the guard then enforces
# that the trajectory ALSO touched a wire-shape contract test.

_WIRE_SCHEMA_PATH_PATTERNS: tuple[str, ...] = (
    "/runtime/protocol/items.py",
    "/runtime/sensing/siphon/anthropic_compat/",
    "/runtime/sensing/siphon/openai_gateway/",
    "/runtime/protocol/",
)

# Tests that count as "wire-shape contract test edits" for §22.
_WIRE_CONTRACT_TEST_MARKERS: tuple[str, ...] = (
    "anthropic_compat",
    "anthropic_gateway",
    "openai_gateway",
    "openai_sse",
    "openai_compat",
    "wire_shape",
    "wire_contract",
    "protocol_items",
)


def _is_wire_schema_path(path: str | None) -> bool:
    if not path:
        return False
    norm = "/" + path.replace("\\", "/").lstrip("/").lower()
    return any(pattern in norm for pattern in _WIRE_SCHEMA_PATH_PATTERNS)


def _is_wire_contract_test_path(path: str | None) -> bool:
    if not path or not _is_test_path(path):
        return False
    norm = path.replace("\\", "/").lower()
    return any(marker in norm for marker in _WIRE_CONTRACT_TEST_MARKERS)


def _step_edits_wire_schema(step: ReActStep) -> bool:
    path = _extract_step_path(step)
    return _is_wire_schema_path(path) if path else False


def _has_wire_contract_test_write(steps: list[ReActStep]) -> bool:
    for step in steps:
        if not _is_code_write_step(step):
            continue
        path = _extract_step_path(step)
        if path is not None and _is_wire_contract_test_path(path):
            return True
    return False


# ──────────────────────────────────────────────────────────────────
# §23 — third-party import without dependency declaration
# ──────────────────────────────────────────────────────────────────
# Look at write payloads for ``import X`` / ``from X import ...`` lines
# whose top-level package isn't stdlib AND isn't a first-party
# (``runtime`` / ``tests``) package AND wasn't already declared in
# pyproject.toml in the SAME trajectory.
#
# We use sys.stdlib_module_names (Python 3.10+) as the stdlib oracle.
# First-party packages are pinned: anything else must show up in a
# write to pyproject.toml within the same trajectory.

_FIRST_PARTY_TOP_LEVEL: frozenset[str] = frozenset(
    {
        "runtime",
        "tests",
        "frontend",
        "tools",
        "scripts",
    }
)

_IMPORT_LINE_RE = re.compile(
    r"(?:^|\n)\s*(?:from\s+(?P<from>[A-Za-z_][A-Za-z0-9_.]*)\s+import|"
    r"import\s+(?P<imp>[A-Za-z_][A-Za-z0-9_.]*))",
)


def _top_level_module(name: str) -> str:
    return name.split(".", 1)[0]


def _is_third_party_module(top: str) -> bool:
    if not top:
        return False
    if top in _FIRST_PARTY_TOP_LEVEL:
        return False
    if top in _sys.stdlib_module_names:
        return False
    # ``__future__`` lives in stdlib_module_names in 3.11+; defensive
    # double-check for older minors.
    return not top.startswith("__")


def _new_third_party_imports_in_payload(text: str) -> set[str]:
    out: set[str] = set()
    if not text:
        return out
    for match in _IMPORT_LINE_RE.finditer(text):
        raw = match.group("from") or match.group("imp") or ""
        top = _top_level_module(raw)
        if _is_third_party_module(top):
            out.add(top)
    return out


def _step_introduces_third_party_imports(step: ReActStep) -> set[str]:
    """Set of NEW third-party top-level packages this step appears to
    import. ``new_string`` minus ``old_string`` ensures we only flag
    additions, not pre-existing imports being moved around."""
    path = _extract_step_path(step)
    if not path or not path.lower().endswith((".py", ".pyi")):
        return set()
    if _is_test_path(path):
        return set()
    new_text, old_text = _extract_step_payloads(step)
    new_imports = _new_third_party_imports_in_payload(new_text)
    old_imports = _new_third_party_imports_in_payload(old_text)
    return new_imports - old_imports


def _step_writes_dep_manifest(step: ReActStep) -> bool:
    path = _extract_step_path(step)
    if not path:
        return False
    norm = path.replace("\\", "/").lower()
    base = norm.rsplit("/", 1)[-1]
    return base in {
        "pyproject.toml",
        "requirements.txt",
        "setup.py",
        "setup.cfg",
        "poetry.lock",
        "uv.lock",
    }


# ──────────────────────────────────────────────────────────────────
# §24 — false-verification claim detection
# ──────────────────────────────────────────────────────────────────
# Catch the failure mode where the model writes "all tests pass" /
# "已通过测试" in its Final Answer but the trajectory contains no
# successful verifier observation. This is the textual counterpart
# to the §18/§19 pattern — those guard the trajectory shape; this
# guards the claim itself.

_VERIFY_CLAIM_RE = re.compile(
    r"(?:"
    # English
    r"\ball\s+tests?\s+pass(?:ed|ing)?\b|"
    r"\btests?\s+pass(?:ed|ing)?\b|"
    r"\bverified\b|"
    r"\btypechecks?\s+pass(?:ed|ing)?\b|"
    r"\blint(?:ing)?\s+pass(?:ed|ing)?\b|"
    r"\bbuild\s+(?:passes|passed|succeed(?:ed|s)?)\b|"
    r"\b\d+\s+pass(?:ed|ing)?\b|"
    # Chinese
    r"全部测试通过|测试[已都全]?通过|已通过测试|"
    r"已[运通]?(?:跑|过)?(?:完|过)?(?:测试|test)|"
    r"(?:测试|test|lint|typecheck|类型检查|构建|build)[已全都]*(?:通过|成功|无误)|"
    r"无错误"
    r")",
    re.IGNORECASE,
)


def _final_answer_claims_verification(final_answer: str) -> bool:
    if not final_answer:
        return False
    return bool(_VERIFY_CLAIM_RE.search(final_answer))


# A verifier observation showing FAILING output must never be mistaken for
# a passing one. Conservative by construction: only strong, unambiguous
# failure signals a green run never emits — non-zero failure/error counts,
# uppercase runner tokens (pytest ``FAILED``, go ``FAIL``), compiler/lint
# error lines. "0 failed", "13 passed", "Found 0 errors", "All checks
# passed" deliberately do NOT match. Infra errors (ModuleNotFoundError,
# command-not-found) are handled separately by the callers below.
_RED_TOKEN_RE = re.compile(r"\bFAILED\b|\bFAIL\b")  # case-sensitive on purpose
_RED_PHRASE_RE = re.compile(
    r"\b[1-9]\d*\s+failed\b|"
    r"\b[1-9]\d*\s+error(?:s)?\b|"
    r"\bfound\s+[1-9]\d*\s+error|"
    r"\berror\s+ts\d+|"
    r"\bnpm\s+err!|"
    r"\bassertion\s*error\b|"
    r"\b(?:build|compilation|type-?check|typecheck|lint|tests?)\s+failed\b|"
    r"\btimeout after\b|"
    r'"(?:is_)?timed_out"\s*:\s*(?:true|1)\b|'
    r"(?<![\"'\w])timed[_ -]?out\b(?!\s*[\"']?\s*[:=]\s*(?:false|0)\b)|"
    r"\btool failed\b|"
    r'"success"\s*:\s*false|'
    r"\bexit\s+code\s+[1-9]|"
    r"\breturned\s+non-?zero|"
    r"测试[^。\n]{0,4}失败|构建失败|编译[^。\n]{0,4}失败|"
    r"类型检查[^。\n]{0,6}(?:失败|错误)|校验[^。\n]{0,4}失败",
    re.IGNORECASE,
)


def _verification_observation_is_red(observation: str) -> bool:
    """True when a verifier observation shows failing output (failing
    tests / type / lint / build), as opposed to an infra error like
    ModuleNotFoundError which callers handle separately. Strong-signal
    only — a passing run must never match."""
    if not observation:
        return False
    return bool(_RED_TOKEN_RE.search(observation) or _RED_PHRASE_RE.search(observation))


def _has_successful_verification_observation(steps: list[ReActStep]) -> bool:
    """Whether any verification step produced a non-empty, non-error,
    non-*failing* observation. Stricter than ``_has_code_verification`` —
    that just checks the action was issued; this checks the action *ran
    and did not report failures*.
    """
    for step in steps:
        if not _step_is_verify(step, markers=_VERIFY_MARKERS_ALL):
            continue
        observation = (step.observation or "").strip()
        if not observation or observation == "N/A":
            continue
        lowered = observation.lower()
        if (
            "未执行观察" in observation
            or "not executed" in lowered
            or "tool-availability guard" in lowered
            or "工具失败" in observation
            or "工具执行异常" in observation
            or "command not found" in lowered
            or "no such file" in lowered
            or "modulenotfounderror" in lowered
            or "traceback (most recent call last)" in lowered
            or _verification_observation_is_red(observation)
        ):
            continue
        return True
    return False


def _latest_verification_observation_is_red(steps: list[ReActStep]) -> bool:
    """Whether the MOST RECENT verifier observation in the trajectory is
    red (failing tests / type / lint / build). Only the latest matters: a
    run that went red then green (re-run after a fix) must not be flagged.
    Returns False when no verifier observation exists."""
    for step in reversed(steps):
        if not _step_is_verify(step, markers=_VERIFY_MARKERS_ALL):
            continue
        observation = (step.observation or "").strip()
        if not observation or observation == "N/A":
            continue
        return _verification_observation_is_red(observation)
    return False
