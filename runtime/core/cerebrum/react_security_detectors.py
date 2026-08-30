"""Security + quality detectors for ReAct trajectory steps.

Extracted from ``react_parsing.py`` (2026-06-06). These detectors are
self-contained: each takes a ``ReActStep`` and emits a list of issues
introduced by the step. Consumed by react_guards.py at Final Answer time.

╔══════════════════════════════════════════════════════════════════════╗
║ react_security_detectors.py · navigation map (~300 lines, 6 §).      ║
║                                                                      ║
║   §63 eval / exec / __import__ / compile      ~L40                  ║
║   §65 subprocess shell=True / os.system       ~L78                  ║
║   §66 unsafe deserialization (pickle/yaml)    ~L116                 ║
║   §67 network call inside loop                ~L162                 ║
║   §69 repeated string literal                 ~L209                 ║
║   §70 magic number (time/size unit)           ~L255                 ║
║                                                                      ║
║ Pattern: each section has ``_detect_*_in_payload(text)`` returning  ║
║ a list of issues, plus ``_step_introduces_*(step)`` that diffs      ║
║ new vs old payload to skip pre-existing usages.                     ║
╚══════════════════════════════════════════════════════════════════════╝

The 3 module-private helpers from react_parsing — ``_extract_step_path``,
``_extract_step_payloads``, ``_is_test_path`` — are imported below.
"""

from __future__ import annotations

import re

from runtime.core.cerebrum.react_types import ReActStep

from .react_parsing import (
    _extract_step_path,
    _extract_step_payloads,
    _is_test_path,
)

# ──────────────────────────────────────────────────────────────────
# §63 — eval / exec / __import__ call detection
# ──────────────────────────────────────────────────────────────────
# Adding ``eval(user_input)`` / ``exec(code_str)`` / ``__import__(name)``
# to runtime code is a security and maintainability red flag. There
# are legitimate uses (template engines, dynamic config), but they
# should be deliberate — and the agent should never reach for them
# as a quick fix. We flag any new use in non-test Python and let the
# agent justify it on Final Answer or refactor.

_DYNAMIC_EXEC_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("eval()", re.compile(r"(?:^|[^A-Za-z_.])eval\s*\(")),
    ("exec()", re.compile(r"(?:^|[^A-Za-z_.])exec\s*\(")),
    ("__import__()", re.compile(r"\b__import__\s*\(")),
    ("compile()", re.compile(r"(?:^|[^A-Za-z_.])compile\s*\(.*?,\s*['\"]<", re.DOTALL)),
)


def _detect_dynamic_exec_in_payload(text: str) -> list[str]:
    if not text:
        return []
    hits: list[str] = []
    for label, pattern in _DYNAMIC_EXEC_PATTERNS:
        if pattern.search(text):
            hits.append(label)
    return hits


def _step_introduces_dynamic_exec(step: ReActStep) -> list[str]:
    """List of dynamic-exec labels added by this write step.

    Skips test paths (test fixtures sometimes use eval intentionally
    to construct edge-case values) and non-Python files. Diffs new vs
    old payload to skip pre-existing usages.
    """
    path = _extract_step_path(step)
    if not path or not path.lower().endswith((".py", ".pyi")):
        return []
    if _is_test_path(path):
        return []
    new_text, old_text = _extract_step_payloads(step)
    new_hits = set(_detect_dynamic_exec_in_payload(new_text))
    old_hits = set(_detect_dynamic_exec_in_payload(old_text))
    return sorted(new_hits - old_hits)


# ──────────────────────────────────────────────────────────────────
# §65 — subprocess shell=True detection
# ──────────────────────────────────────────────────────────────────
# ``subprocess.run(..., shell=True)`` / ``Popen(..., shell=True)`` /
# ``os.system(...)`` are command-injection surfaces whenever any part
# of the command string comes from non-static input. We can't know at
# parse time whether the input is trusted, so we flag ALL new uses
# and let the agent justify in the Final Answer.

_SHELL_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "subprocess shell=True",
        re.compile(
            r"\b(?:subprocess\.|sp\.)?(?:run|call|check_call|check_output|Popen)"
            r"\s*\([^)]*\bshell\s*=\s*True",
            re.DOTALL,
        ),
    ),
    ("os.system()", re.compile(r"\bos\s*\.\s*system\s*\(")),
    ("os.popen()", re.compile(r"\bos\s*\.\s*popen\s*\(")),
)


def _detect_shell_injection_in_payload(text: str) -> list[str]:
    if not text:
        return []
    hits: list[str] = []
    for label, pattern in _SHELL_INJECTION_PATTERNS:
        if pattern.search(text):
            hits.append(label)
    return hits


def _step_introduces_shell_injection(step: ReActStep) -> list[str]:
    path = _extract_step_path(step)
    if not path or not path.lower().endswith((".py", ".pyi")):
        return []
    if _is_test_path(path):
        return []
    new_text, old_text = _extract_step_payloads(step)
    new_hits = set(_detect_shell_injection_in_payload(new_text))
    old_hits = set(_detect_shell_injection_in_payload(old_text))
    return sorted(new_hits - old_hits)


# ──────────────────────────────────────────────────────────────────
# §66 — unsafe deserialization detection
# ──────────────────────────────────────────────────────────────────
# ``pickle.loads(...)``, ``pickle.load(...)``, ``yaml.load(...)``
# (without ``Loader=SafeLoader``), ``marshal.loads(...)``, ``shelve.open(...)``
# can execute arbitrary code on attacker-controlled data. Safe variants
# exist (``yaml.safe_load``, ``json.loads``); we flag the unsafe ones.

_UNSAFE_DESER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("pickle.load(s)", re.compile(r"\b(?:cP|c)?[Pp]ickle\s*\.\s*loads?\s*\(")),
    ("marshal.loads", re.compile(r"\bmarshal\s*\.\s*loads?\s*\(")),
    (
        "yaml.load without SafeLoader",
        re.compile(
            r"\byaml\s*\.\s*load\s*\((?P<args>[^)]*)\)",
            re.DOTALL,
        ),
    ),
)


def _detect_unsafe_deser_in_payload(text: str) -> list[str]:
    if not text:
        return []
    hits: list[str] = []
    for label, pattern in _UNSAFE_DESER_PATTERNS:
        for match in pattern.finditer(text):
            if label.startswith("yaml.load"):
                args = match.group("args") or ""
                if "SafeLoader" in args or "safe_load" in args:
                    continue
            hits.append(label)
            break
    return hits


def _step_introduces_unsafe_deser(step: ReActStep) -> list[str]:
    path = _extract_step_path(step)
    if not path or not path.lower().endswith((".py", ".pyi")):
        return []
    if _is_test_path(path):
        return []
    new_text, old_text = _extract_step_payloads(step)
    new_hits = set(_detect_unsafe_deser_in_payload(new_text))
    old_hits = set(_detect_unsafe_deser_in_payload(old_text))
    return sorted(new_hits - old_hits)


# ──────────────────────────────────────────────────────────────────
# §67 — network call inside loop detection
# ──────────────────────────────────────────────────────────────────
# Catch the failure mode where the model writes a ``for x in items:``
# loop whose body issues a network call (``requests.get``, ``httpx.X``,
# ``urllib.request``) — turning a constant-time operation into O(n)
# round trips. Often hides under "just iterate the list and look each
# one up".
#
# Heuristic: a ``for`` / ``while`` block whose body contains one of the
# network-call markers AND that block was added in this trajectory
# (not pre-existing).

_NETWORK_CALL_RE = re.compile(
    r"\b(?:"
    r"requests\s*\.\s*(?:get|post|put|patch|delete|head|options)|"
    r"httpx\s*\.\s*(?:get|post|put|patch|delete|head|options|stream|AsyncClient|Client)|"
    r"urllib\.request\.(?:urlopen|Request)|"
    r"urlopen\s*\(|"
    r"client\s*\.\s*(?:get|post|put|patch|delete|fetch|request)\s*\("
    r")",
)
_LOOP_HEAD_RE = re.compile(
    r"(?:^|\n)(?P<indent>[ \t]*)(?:for\s+\w+\s+in\s+|while\s+)[^\n]*:\s*\n",
)


def _payload_has_network_in_loop(text: str) -> bool:
    if not text:
        return False
    for match in _LOOP_HEAD_RE.finditer(text):
        indent = match.group("indent")
        body_indent_marker = indent + " "
        rest = text[match.end() :]
        body_lines: list[str] = []
        for raw in rest.splitlines():
            if not raw.strip():
                body_lines.append(raw)
                continue
            if not raw.startswith(body_indent_marker):
                break
            body_lines.append(raw)
        body = "\n".join(body_lines)
        if _NETWORK_CALL_RE.search(body):
            return True
    return False


def _step_introduces_network_in_loop(step: ReActStep) -> bool:
    path = _extract_step_path(step)
    if not path or not path.lower().endswith((".py", ".pyi")):
        return False
    if _is_test_path(path):
        return False
    new_text, old_text = _extract_step_payloads(step)
    return _payload_has_network_in_loop(new_text) and not _payload_has_network_in_loop(old_text)


# ──────────────────────────────────────────────────────────────────
# §69 — duplicate string literal detection
# ──────────────────────────────────────────────────────────────────
# When the same string literal appears 3+ times in a single payload,
# it should usually be a named constant. Catches "magic strings" like
# ``"runtime/safety"`` repeated through a function. We only flag
# strings of length >= 8 (skips short tokens like ``""`` / ``"x"``)
# and ignore strings that look like regex patterns (start with ``r"``
# in source — but our payload doesn't preserve the prefix, so we just
# skip strings that contain regex meta-chars in dense form).

_STRING_LITERAL_RE = re.compile(
    r"""(?P<quote>['"])(?P<content>(?:(?!(?P=quote)).){8,})(?P=quote)""",
)
_REPEATED_LITERAL_THRESHOLD = 3


def _detect_repeated_literals_in_payload(text: str) -> list[tuple[str, int]]:
    if not text:
        return []
    counts: dict[str, int] = {}
    for match in _STRING_LITERAL_RE.finditer(text):
        content = match.group("content")
        if "\n" in content:
            continue
        # Skip strings that look like regex patterns or format strings.
        if any(meta in content for meta in (r"\d", r"\w", r"\s", r"\b", r"^\s*", r".*?")):
            continue
        # Skip docstring-shaped content.
        if content.endswith(".") and " " in content:
            continue
        counts[content] = counts.get(content, 0) + 1
    return sorted(
        ((s, n) for s, n in counts.items() if n >= _REPEATED_LITERAL_THRESHOLD),
        key=lambda x: -x[1],
    )


def _step_introduces_repeated_literal(step: ReActStep) -> list[tuple[str, int]]:
    path = _extract_step_path(step)
    if not path or not path.lower().endswith((".py", ".pyi", ".ts", ".tsx", ".js", ".jsx")):
        return []
    if _is_test_path(path):
        return []
    new_text, old_text = _extract_step_payloads(step)
    new_repeats = dict(_detect_repeated_literals_in_payload(new_text))
    old_repeats = dict(_detect_repeated_literals_in_payload(old_text))
    out: list[tuple[str, int]] = []
    for literal, count in new_repeats.items():
        if old_repeats.get(literal, 0) >= count:
            continue
        out.append((literal, count))
    return out


# ──────────────────────────────────────────────────────────────────
# §70 — magic number detection
# ──────────────────────────────────────────────────────────────────
# Catch bare numeric literals that should be named constants:
#   * time-unit values: 86400, 3600, 1440, 604800, 31536000
#   * size-unit values: 1024, 1048576, 1073741824
#   * port-number values: 80, 443, 8080, 5432, 6379, 27017 (when
#     used as comparisons, not in network bind context)
#   * arbitrary > 1000 numbers in non-test runtime code
#
# Conservative: only fire when the literal appears as a comparison
# RHS or function-call arg, not as a list length / array index / version.

_TIME_UNIT_LITERALS: frozenset[int] = frozenset({3600, 86400, 1440, 604800, 31536000, 2592000})
_SIZE_UNIT_LITERALS: frozenset[int] = frozenset({1024, 1048576, 1073741824, 4096, 8192, 65536})

_MAGIC_NUMBER_USAGE_RE = re.compile(
    r"(?:[<>=!]=?\s*|[*+\-/%]\s*|=\s*|,\s*|\(\s*)"
    r"(?P<num>\d{4,})"
    r"(?!\s*[\w.])",
)


def _detect_magic_numbers_in_payload(text: str) -> list[int]:
    if not text:
        return []
    found: set[int] = set()
    for match in _MAGIC_NUMBER_USAGE_RE.finditer(text):
        try:
            n = int(match.group("num"))
        except (ValueError, TypeError):
            continue
        if n in _TIME_UNIT_LITERALS or n in _SIZE_UNIT_LITERALS:
            found.add(n)
    return sorted(found)


def _step_introduces_magic_number(step: ReActStep) -> list[int]:
    path = _extract_step_path(step)
    if not path or not path.lower().endswith((".py", ".pyi")):
        return []
    if _is_test_path(path):
        return []
    new_text, old_text = _extract_step_payloads(step)
    new_hits = set(_detect_magic_numbers_in_payload(new_text))
    old_hits = set(_detect_magic_numbers_in_payload(old_text))
    return sorted(new_hits - old_hits)


__all__ = [
    "_detect_dynamic_exec_in_payload",
    "_step_introduces_dynamic_exec",
    "_detect_shell_injection_in_payload",
    "_step_introduces_shell_injection",
    "_detect_unsafe_deser_in_payload",
    "_step_introduces_unsafe_deser",
    "_payload_has_network_in_loop",
    "_step_introduces_network_in_loop",
    "_detect_repeated_literals_in_payload",
    "_step_introduces_repeated_literal",
    "_detect_magic_numbers_in_payload",
    "_step_introduces_magic_number",
]
