"""Verification-completeness guards for ReAct code-mode turns.

Extracted from ``react_guards.py`` (Wave 3, cluster 3): the
write-then-verify follow-up trio (consumed by react_in_flight_nudges /
react_loop) and the verification-completeness family (language match,
path policy, test coverage, signature/typecheck, wire schema,
dependency declaration, false/red verification claims). Leaf module:
depends only on react_goal_analysis / react_parsing / react_types /
verification_policy — must never import react_guards.
"""

from __future__ import annotations

from runtime.core.cerebrum.react_goal_analysis import _final_answer_requests_user_help
from runtime.core.cerebrum.react_parsing import (
    _final_answer_claims_verification,
    _has_code_verification,
    _has_code_write,
    _has_language_specific_verification,
    _has_successful_verification_observation,
    _has_test_write,
    _has_verification_requiring_code_write,
    _has_wire_contract_test_write,
    _is_code_write_step,
    _latest_verification_observation_is_red,
    _parse_action,
    _path_language,
    _step_changed_public_signature,
    _step_command_text,
    _step_edits_wire_schema,
    _step_introduces_python_public_symbol,
    _step_introduces_third_party_imports,
    _step_writes_dep_manifest,
)
from runtime.core.cerebrum.react_types import ReActStep
from runtime.core.cerebrum.verification_policy import (
    classify_path,
    command_satisfies_requirement,
    summarize_requirements,
    verification_requirements_for_paths,
)

# Verification "tools" — anything in this set counts as a real
# verification action for the post-write guard. This is intentionally
# narrower than ``_has_code_verification``, which scans whole trajectories.
_SHELL_VERIFICATION_TOOLS: frozenset[str] = frozenset(
    {
        "exec_shell",
        "shell_command",
        "bash",
        "run_tests",
        "run_checks",
        "verify",
    }
)

# How many steps after a code-write action we tolerate before
# expecting a verification step. Tuned empirically: long edit
# sequences (multi_edit_file × N then run tests once) need slack;
# anything beyond ~6 steps is "the model forgot to verify".
_POST_WRITE_VERIFY_WINDOW = 6


def _is_static_web_artifact_path(path: str | None) -> bool:
    return classify_path(path) == "static-web"


def _action_path(step: ReActStep) -> str | None:
    parsed = _parse_action(step.action)
    if parsed is None:
        return None
    _name, args = parsed
    value = args.get("path") or args.get("file") or args.get("file_path")
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().replace("\\", "/")


def _is_followup_verification_step(
    step: ReActStep,
    *,
    written_path: str | None,
) -> bool:
    parsed = _parse_action(step.action)
    if parsed is None:
        return False
    name, _args = parsed
    if _is_static_web_artifact_path(written_path):
        action_text = _step_command_text(step)
        if name == "read_file" and written_path:
            read_path = _action_path(step)
            return read_path == written_path
        if name.startswith("browser_"):
            return True
        if name in _SHELL_VERIFICATION_TOOLS:
            return any(
                marker in action_text
                for marker in (
                    "node -c",
                    "html validate",
                    "htmlhint",
                    "browser regression",
                    "playwright",
                    "npm run build",
                    "pnpm build",
                )
            )
    if name in _SHELL_VERIFICATION_TOOLS:
        return _has_code_verification([step])
    if name == "read_file" and written_path:
        read_path = _action_path(step)
        return read_path == written_path
    return False


def _unverified_write_followup_guard(
    steps: list[ReActStep],
    *,
    is_code_mode: bool,
) -> str | None:
    """Detect code writes that don't get a follow-up verification.

    Fires DURING the loop. Looks at the last write step within the
    sliding window: if more than ``_POST_WRITE_VERIFY_WINDOW`` steps
    have happened since AND no verification action ran in that
    window, nudge the model to verify before stacking more changes.

    Only active in code mode — chat / research turns have no
    "verify" semantics.

    Returns ``None`` when:
      * not code mode
      * no write actions in history
      * the last write is still within the tolerance window
      * a verification action ran after the last write
    """
    if not is_code_mode or not steps:
        return None

    # Find the last write index.
    last_write_idx = -1
    written_path: str | None = None
    for idx in range(len(steps) - 1, -1, -1):
        if _is_code_write_step(steps[idx]):
            last_write_idx = idx
            written_path = _action_path(steps[idx])
            break
    if last_write_idx < 0:
        return None

    # How many steps since the last write?
    distance = (len(steps) - 1) - last_write_idx
    if distance < _POST_WRITE_VERIFY_WINDOW:
        return None  # still within tolerance

    # Was there a verification step after the last write?
    for follow in steps[last_write_idx + 1 :]:
        if _is_followup_verification_step(follow, written_path=written_path):
            return None  # already verified

    if _is_static_web_artifact_path(written_path):
        return (
            "You wrote a static web artifact "
            f"{distance} step(s) ago without a smoke check. "
            "Before making more changes, verify the artifact with one "
            "of: read_file the written HTML/CSS path, run a small "
            "HTML/embedded-JS syntax check, or use a browser smoke "
            "check/screenshot if a preview URL is available. Do not "
            "default to TypeScript typecheck unless this is actually a "
            "TS/React project file."
        )

    return (
        "You have written or edited code "
        f"{distance} step(s) ago without running verification. "
        "Before making more changes, run an appropriate check: "
        "ruff/pytest/tsc/eslint/test on the affected files (use "
        "exec_shell), or read_file the result back to confirm the "
        "edit landed as intended. Stacking more edits without "
        "verification compounds errors."
    )


def _failed_verification_followup_guard(
    steps: list[ReActStep],
    *,
    is_code_mode: bool,
) -> str | None:
    """Keep a red verifier focused on repair instead of toolchain detours."""
    if not is_code_mode or not steps or not _latest_verification_observation_is_red(steps):
        return None

    latest_verify_idx = -1
    for idx in range(len(steps) - 1, -1, -1):
        if _has_code_verification([steps[idx]]):
            latest_verify_idx = idx
            break
    if latest_verify_idx < 0:
        return None
    if any(_is_code_write_step(step) for step in steps[latest_verify_idx + 1 :]):
        return None

    return (
        "The latest verification is red. Read the preserved tail diagnostic and fix the underlying "
        "source/test/config (or run the targeted formatter) before launching another verifier. "
        "Do not install dependencies, probe alternate Python environments, or create ad-hoc runner "
        "scripts to bypass a registered verifier. For a concurrency-test timeout, audit lock ownership "
        "and wait/notify paths as a likely deadlock before retrying."
    )


def _redundant_green_verification_guard(
    steps: list[ReActStep],
    *,
    is_code_mode: bool,
) -> str | None:
    """Stop code agents from repeatedly re-running an already-green suite."""
    if not is_code_mode or not steps:
        return None

    last_write_idx = -1
    for idx in range(len(steps) - 1, -1, -1):
        if _is_code_write_step(steps[idx]):
            last_write_idx = idx
            break
    if last_write_idx < 0:
        return None

    green_rounds = sum(
        1
        for step in steps[last_write_idx + 1 :]
        if _has_successful_verification_observation([step])
    )
    if green_rounds < 2:
        return None

    return (
        f"Verification is already green in {green_rounds} separate rounds with no intervening code "
        "write. Do not run tests, lint, shell probes, or another verifier again. If the checklist is "
        "stale, call todo_write once with accurate completed statuses; otherwise emit the concise "
        "Final Answer now with the recorded test/lint evidence."
    )


# ──────────────────────────────────────────────────────────────────
# §19 — language-mismatched verification guard
# ──────────────────────────────────────────────────────────────────
# Catch the failure mode where the model edits Foo.tsx but only runs
# pytest, then claims "verified". The legacy ``_has_code_verification``
# is language-agnostic so it returns True; this guard cross-checks
# against the language(s) actually written and surfaces the gap.
#
# Reaches Final Answer time only — at mid-flight a nudge here would
# fight the §18 post-write-verify guard (which doesn't care about
# language). At final-answer the cost of false-claim is highest, so
# we add the strict cross-check there.

# How recent a write must be for the language-mismatch check to fire
# at Final Answer time. Captures the trailing slice of the trajectory
# — long sessions may have already verified earlier-language edits;
# we focus on what the model touched last.
_LANG_MISMATCH_LOOKBACK = 12


def _languages_recently_written(steps: list[ReActStep]) -> set[str]:
    languages: set[str] = set()
    window = steps[-_LANG_MISMATCH_LOOKBACK:] if steps else []
    for step in window:
        if not _is_code_write_step(step):
            continue
        path = _action_path(step)
        lang = _path_language(path)
        if lang is not None:
            languages.add(lang)
    return languages


def _language_mismatched_verification_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    is_code_mode: bool,
) -> str | None:
    """Reject finals that ran verifiers in the wrong language.

    Triggers when:
      * code mode is active
      * a code-write step happened in the recent window
      * the written file's language has known verifiers
      * NO verifier in that language ran in the trajectory
      * the agent isn't punting the task to the user

    Stays silent when no recognised language was written (e.g. only
    Markdown or YAML edits) — those are handled by the broader §18
    post-write guard.
    """
    if not is_code_mode or not steps:
        return None
    if _final_answer_requests_user_help(final_answer):
        return None

    languages = _languages_recently_written(steps)
    if not languages:
        return None

    missing = [
        lang
        for lang in sorted(languages)
        if not _has_language_specific_verification(steps, language=lang)
    ]
    if not missing:
        return None

    suggestions = {
        "python": "pytest / ruff / py_compile",
        "typescript": "tsc / eslint / vitest / npm run typecheck",
        "rust": "cargo check / cargo test / cargo clippy",
        "go": "go build / go test / go vet",
    }
    hints = "; ".join(
        f"{lang}: {suggestions.get(lang, 'language-appropriate verifier')}" for lang in missing
    )
    langs_repr = ", ".join(missing)
    return (
        f"Cannot finish yet: edits in {langs_repr} are not verified by a "
        "matching verifier. Running a verifier in a different language "
        "does not count as verification for these files. Run one before "
        f"reporting completion — {hints}."
    )


# ──────────────────────────────────────────────────────────────────
# §20 — new-Python-code-without-test guard
# ──────────────────────────────────────────────────────────────────
# Catch the failure mode where the model adds a new public top-level
# function or class to non-test .py code AND the trajectory contains
# no edit to any test file. Conservative on purpose:
#
#   * Only fires for top-level `def NAME(` / `class NAME` introductions
#     (private `_NAME` and nested defs are skipped).
#   * Only fires for non-test paths.
#   * Stays silent when ANY test file was touched in the trajectory —
#     we don't try to verify the test actually covers the new symbol;
#     a manual edit of a test file is enough signal that the agent is
#     thinking about coverage.
#   * Stays silent when the agent is punting (help-request short-circuit).
#
# False-negative cases we accept:
#   * Renaming an existing public symbol (looks like a new symbol).
#     Tolerated because rename refactors usually touch tests anyway.
#   * Pure additions hidden inside private helpers. Out of scope.
#
# Same lookback window as §19 for consistency.

_VERIFICATION_POLICY_LOOKBACK = 16


def _recent_policy_written_paths(steps: list[ReActStep]) -> tuple[list[str], int]:
    paths: list[str] = []
    last_write_idx = -1
    start_idx = max(0, len(steps) - _VERIFICATION_POLICY_LOOKBACK)
    for idx, step in enumerate(steps[start_idx:], start=start_idx):
        if not _is_code_write_step(step):
            continue
        path = _action_path(step)
        if not path:
            continue
        last_write_idx = idx
        if path not in paths:
            paths.append(path)
    return paths, last_write_idx


def _path_verification_policy_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    is_code_mode: bool,
) -> str | None:
    """Reject finals that skip path-specific verification obligations."""

    if not is_code_mode or not steps:
        return None
    if _final_answer_requests_user_help(final_answer):
        return None

    written_paths, last_write_idx = _recent_policy_written_paths(steps)
    if last_write_idx < 0 or not written_paths:
        return None

    requirements = verification_requirements_for_paths(written_paths)
    if not requirements:
        return None

    followup_texts = [
        f"{step.action or ''}\n{step.observation or ''}" for step in steps[last_write_idx + 1 :]
    ]
    missing = [
        req
        for req in requirements
        if req.required
        and not any(command_satisfies_requirement(text, req) for text in followup_texts)
    ]
    if not missing:
        return None

    return (
        "Cannot finish yet: the touched files require specific verification "
        "after the latest edit. Missing: "
        f"{summarize_requirements(missing)}. Run one of the suggested checks "
        "or explicitly ask the user for help if verification is impossible."
    )


_NEW_SYMBOL_LOOKBACK = 12


def _trajectory_added_public_python_symbol(steps: list[ReActStep]) -> bool:
    window = steps[-_NEW_SYMBOL_LOOKBACK:] if steps else []
    return any(_step_introduces_python_public_symbol(step) for step in window)


def _new_python_code_without_test_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    is_code_mode: bool,
) -> str | None:
    """Reject finals that introduce new public Python symbols without
    touching any test file in the trajectory.

    Returns ``None`` when:
      * not code mode
      * no recent new-public-symbol introduction
      * any test file was edited in the trajectory
      * agent is asking for user help
    """
    if not is_code_mode or not steps:
        return None
    if _final_answer_requests_user_help(final_answer):
        return None
    if not _trajectory_added_public_python_symbol(steps):
        return None
    if _has_test_write(steps):
        return None
    return (
        "Cannot finish yet: a new public Python function or class was "
        "added to runtime code, but no test file was edited or created. "
        "Add a focused test under tests/ that exercises the new symbol "
        "(happy path + at least one edge case), or — if a test truly "
        "isn't applicable — say so explicitly in the Final Answer and "
        "explain why."
    )


# ──────────────────────────────────────────────────────────────────
# §21 — public-signature change without typecheck guard
# ──────────────────────────────────────────────────────────────────
# Editing ``def public_thing(a, b)`` → ``def public_thing(a, b, c)`` is a
# silent breaker for every caller. We require the trajectory to run a
# typechecker (mypy / pyright / ruff with PYI rules) when a public sig
# changed. ``ruff`` alone is borderline — it catches some cases but not
# parameter-count breaks. We treat mypy / pyright / pyrefly as the
# canonical typecheckers; basic ``ruff check`` does NOT count.

_PYTHON_TYPECHECK_MARKERS: tuple[str, ...] = (
    "mypy",
    "pyright",
    "pyrefly",
    "pyre",
)

_SIG_CHANGE_LOOKBACK = 12


def _trajectory_changed_public_signature(steps: list[ReActStep]) -> bool:
    window = steps[-_SIG_CHANGE_LOOKBACK:] if steps else []
    return any(_step_changed_public_signature(step) for step in window)


def _has_python_typecheck_run(steps: list[ReActStep]) -> bool:
    for step in steps:
        haystack = (step.action or "").lower()
        parsed = _parse_action(step.action)
        if parsed is not None:
            _name, args = parsed
            haystack += " " + str(args.get("command") or args.get("cmd") or "").lower()
        if any(marker in haystack for marker in _PYTHON_TYPECHECK_MARKERS):
            return True
    return False


def _signature_changed_without_typecheck_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    is_code_mode: bool,
) -> str | None:
    """Reject finals that change a public Python signature without
    running mypy/pyright/pyrefly in the trajectory."""
    if not is_code_mode or not steps:
        return None
    if _final_answer_requests_user_help(final_answer):
        return None
    if not _trajectory_changed_public_signature(steps):
        return None
    if _has_python_typecheck_run(steps):
        return None
    return (
        "Cannot finish yet: a public function/method signature changed in "
        "non-test Python code without running a typechecker. ruff alone "
        "doesn't catch parameter-count breaks. Run mypy / pyright / "
        "pyrefly on the affected module before reporting completion, or "
        "explicitly note that no typechecker is configured for the project."
    )


# ──────────────────────────────────────────────────────────────────
# §22 — wire-schema change without compat-test guard
# ──────────────────────────────────────────────────────────────────
# echo has no DB migrations, but it DOES expose wire-shape schemas
# that external SDKs talk to (anthropic_compat / openai_gateway /
# protocol/items.py). Mutating those without touching a wire-shape
# contract test is a silent break for downstream clients.

_WIRE_SCHEMA_LOOKBACK = 12


def _trajectory_edits_wire_schema(steps: list[ReActStep]) -> bool:
    window = steps[-_WIRE_SCHEMA_LOOKBACK:] if steps else []
    return any(_step_edits_wire_schema(step) for step in window)


def _wire_schema_change_without_compat_test_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    is_code_mode: bool,
) -> str | None:
    """Reject finals that mutate wire-shape protocol/SDK-compat code
    without editing or adding a wire-shape contract test."""
    if not is_code_mode or not steps:
        return None
    if _final_answer_requests_user_help(final_answer):
        return None
    if not _trajectory_edits_wire_schema(steps):
        return None
    if _has_wire_contract_test_write(steps):
        return None
    return (
        "Cannot finish yet: a wire-shape schema (protocol/items.py, "
        "anthropic_compat, or openai_gateway) was modified without "
        "editing a wire-shape contract test (e.g. tests/...openai_compat... "
        "or tests/...anthropic_compat...). External SDK clients will break "
        "silently if these contracts shift untested. Add or update a "
        "contract test, or note explicitly that the change is internal-only "
        "and explain why no client is affected."
    )


# ──────────────────────────────────────────────────────────────────
# §23 — third-party import without dependency declaration guard
# ──────────────────────────────────────────────────────────────────
# Adding ``import requests`` to runtime code without putting
# ``requests`` in pyproject.toml ships a code path that ImportErrors
# on a clean install. We detect new third-party imports across the
# trajectory; if none of the trajectory's writes touched a dependency
# manifest (pyproject.toml etc.), the guard fires.

_NEW_IMPORT_LOOKBACK = 12


def _trajectory_new_third_party_imports(steps: list[ReActStep]) -> set[str]:
    out: set[str] = set()
    window = steps[-_NEW_IMPORT_LOOKBACK:] if steps else []
    for step in window:
        out.update(_step_introduces_third_party_imports(step))
    return out


def _has_dep_manifest_write(steps: list[ReActStep]) -> bool:
    return any(_step_writes_dep_manifest(step) for step in steps)


def _new_third_party_import_without_dep_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    is_code_mode: bool,
) -> str | None:
    """Reject finals that add ``import X`` for a third-party package
    without writing to pyproject.toml / requirements / etc."""
    if not is_code_mode or not steps:
        return None
    if _final_answer_requests_user_help(final_answer):
        return None
    new_imports = _trajectory_new_third_party_imports(steps)
    if not new_imports:
        return None
    if _has_dep_manifest_write(steps):
        return None
    pkgs = ", ".join(sorted(new_imports))
    return (
        f"Cannot finish yet: new third-party import(s) added — {pkgs} — "
        "but no dependency manifest (pyproject.toml / requirements.txt) "
        "was updated in this trajectory. The code will ImportError on a "
        "clean install. Either declare the package(s) properly or remove "
        "the import."
    )


# ──────────────────────────────────────────────────────────────────
# §24 — false-verification-claim guard
# ──────────────────────────────────────────────────────────────────
# The Final Answer says "tests pass / 已通过测试 / build succeeded" but
# the trajectory has no successful verifier observation. This is a
# different failure shape than §18 (no verifier ran at all): here the
# model DID issue a verifier call, but it failed (ModuleNotFoundError,
# command not found, traceback) — and the model ignored the failure
# and claimed success anyway.


def _false_verification_claim_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    is_code_mode: bool,
) -> str | None:
    if not is_code_mode or not steps:
        return None
    if _final_answer_requests_user_help(final_answer):
        return None
    if not _final_answer_claims_verification(final_answer):
        return None
    # A read-only analysis turn may report "tests pass" as a documented
    # fact it read from the repo (README, CI config) rather than claiming
    # it ran the verifier itself. Only force verification when the agent
    # actually modified code — mirroring the red-verification guard, which
    # is gated on _has_verification_requiring_code_write for the same reason.
    # Without this gate a pure research/analysis answer is rejected 3x here,
    # the final answer stays buffered (the "streaming silences for hundreds
    # of seconds" symptom), and the turn dies on a guard impasse.
    if not _has_verification_requiring_code_write(steps):
        return None
    if _has_successful_verification_observation(steps):
        return None
    return (
        "Cannot finish yet: the Final Answer claims tests/typecheck/build "
        "passed, but no successful verifier observation is recorded in "
        "this trajectory. Either the verifier failed (ModuleNotFoundError, "
        "command-not-found, traceback) or it never ran. Re-run the verifier, "
        "fix any errors, and only claim success after a clean run — or "
        "remove the unsupported claim from the Final Answer."
    )


def _red_verification_observation_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    is_code_mode: bool,
) -> str | None:
    """Block a code-mode Final Answer when the model's own most recent
    verification run is red. The false-verification guard only fires when
    the answer *claims* success; this one fires on the recorded failure
    itself — you cannot declare done while your latest test/type/lint/build
    run is failing, whether or not you claimed otherwise. Gated on an
    actual code write so read-only turns that merely surface a red run
    aren't blocked."""
    if not is_code_mode or not steps:
        return None
    if _final_answer_requests_user_help(final_answer):
        return None
    if not _has_code_write(steps):
        return None
    if not _latest_verification_observation_is_red(steps):
        return None
    return (
        "Cannot finish yet: your most recent verification run is failing "
        "(failing tests / type errors / lint errors / build error in the "
        "last verifier observation). Diagnose and fix the underlying cause, "
        "then re-run the verifier until it is clean before finishing. Do not "
        "finish on a red verification, and do not silence it by deleting or "
        "skipping the failing check."
    )
