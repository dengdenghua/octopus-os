"""Regression tests for the §19 language-mismatched verification guard.

Catches the failure mode where the model edits one language (TS/Rust/Go)
but only runs a verifier for another (e.g. ``pytest`` after a ``.tsx``
edit) and then claims completion. The legacy ``_has_code_verification``
returns True for *any* verifier — this guard cross-checks the
verifier's language against the languages actually written.
"""

from __future__ import annotations

from runtime.core.cerebrum.react_guards import (
    _language_mismatched_verification_guard,
)
from runtime.core.cerebrum.react_parsing import (
    _has_language_specific_verification,
    _path_language,
)
from runtime.core.cerebrum.react_types import ReActStep


def _step(
    iteration: int,
    *,
    thought: str = "",
    action: str = "",
    observation: str = "",
) -> ReActStep:
    return ReActStep(
        iteration=iteration,
        thought=thought,
        action=action,
        observation=observation,
    )


# ──────────────────────────────────────────────────────────────────
# Path → language helper
# ──────────────────────────────────────────────────────────────────


class TestPathLanguage:
    def test_python_extensions(self) -> None:
        assert _path_language("foo.py") == "python"
        assert _path_language("a/b/c.pyi") == "python"

    def test_typescript_family(self) -> None:
        assert _path_language("Bar.tsx") == "typescript"
        assert _path_language("util.ts") == "typescript"
        assert _path_language("legacy.js") == "typescript"
        assert _path_language("config.cjs") == "typescript"

    def test_rust_and_go(self) -> None:
        assert _path_language("src/lib.rs") == "rust"
        assert _path_language("cmd/main.go") == "go"

    def test_unknown_returns_none(self) -> None:
        # Markdown / YAML / config — unknown intentionally so the
        # guard stays quiet on doc-only edits.
        assert _path_language("README.md") is None
        assert _path_language("config.yaml") is None
        assert _path_language("") is None
        assert _path_language(None) is None


# ──────────────────────────────────────────────────────────────────
# Language-specific verification scan
# ──────────────────────────────────────────────────────────────────


class TestLanguageSpecificVerification:
    def test_pytest_counts_as_python_only(self) -> None:
        steps = [_step(1, action='exec_shell({"command": "pytest tests/"})')]
        assert _has_language_specific_verification(steps, language="python")
        assert not _has_language_specific_verification(steps, language="typescript")
        assert not _has_language_specific_verification(steps, language="rust")

    def test_tsc_counts_as_typescript_only(self) -> None:
        steps = [_step(1, action='exec_shell({"command": "npx tsc --noEmit"})')]
        assert _has_language_specific_verification(steps, language="typescript")
        assert not _has_language_specific_verification(steps, language="python")

    def test_cargo_test_counts_as_rust(self) -> None:
        steps = [_step(1, action='exec_shell({"command": "cargo test"})')]
        assert _has_language_specific_verification(steps, language="rust")
        assert not _has_language_specific_verification(steps, language="python")

    def test_go_test_counts_as_go(self) -> None:
        steps = [_step(1, action='exec_shell({"command": "go test ./..."})')]
        assert _has_language_specific_verification(steps, language="go")
        assert not _has_language_specific_verification(steps, language="typescript")

    def test_unknown_language_returns_false(self) -> None:
        steps = [_step(1, action='exec_shell({"command": "pytest"})')]
        assert not _has_language_specific_verification(steps, language="haskell")


# ──────────────────────────────────────────────────────────────────
# Final-answer guard
# ──────────────────────────────────────────────────────────────────


class TestLanguageMismatchedVerificationGuard:
    def test_non_code_mode_silent(self) -> None:
        steps = [
            _step(1, action='write_text_file({"path": "Bar.tsx", "content": "x"})'),
            _step(2, action='exec_shell({"command": "pytest"})'),
        ]
        assert (
            _language_mismatched_verification_guard(
                steps,
                "done",
                is_code_mode=False,
            )
            is None
        )

    def test_no_writes_silent(self) -> None:
        steps = [_step(1, action='read_file({"path": "Bar.tsx"})')]
        assert (
            _language_mismatched_verification_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is None
        )

    def test_unknown_language_silent(self) -> None:
        # Markdown edit — guard has nothing to enforce.
        steps = [
            _step(1, action='write_text_file({"path": "README.md", "content": "x"})'),
        ]
        assert (
            _language_mismatched_verification_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is None
        )

    def test_python_edit_python_verify_silent(self) -> None:
        steps = [
            _step(
                1,
                action='edit_file({"path": "runtime/foo.py", "old_string": "x", "new_string": "y"})',
            ),
            _step(2, action='exec_shell({"command": "ruff check runtime/foo.py"})'),
        ]
        assert (
            _language_mismatched_verification_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is None
        )

    def test_typescript_edit_no_typescript_verify_fires(self) -> None:
        # The exact bug: edited Bar.tsx but only ran pytest.
        steps = [
            _step(
                1,
                action='edit_file({"path": "frontend/Bar.tsx", "old_string": "x", "new_string": "y"})',
            ),
            _step(2, action='exec_shell({"command": "pytest tests/"})'),
        ]
        msg = _language_mismatched_verification_guard(
            steps,
            "done",
            is_code_mode=True,
        )
        assert msg is not None
        assert "typescript" in msg.lower()
        # Hint should mention a TS-appropriate verifier.
        assert "tsc" in msg.lower() or "vitest" in msg.lower() or "eslint" in msg.lower()

    def test_typescript_edit_with_tsc_silent(self) -> None:
        steps = [
            _step(
                1,
                action='edit_file({"path": "frontend/Bar.tsx", "old_string": "x", "new_string": "y"})',
            ),
            _step(2, action='exec_shell({"command": "npx tsc --noEmit"})'),
        ]
        assert (
            _language_mismatched_verification_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is None
        )

    def test_multi_language_partial_verify_fires(self) -> None:
        # Edited both Python and TS but only ran pytest — TS still unverified.
        steps = [
            _step(
                1,
                action='edit_file({"path": "runtime/foo.py", "old_string": "a", "new_string": "b"})',
            ),
            _step(
                2,
                action='edit_file({"path": "frontend/Bar.tsx", "old_string": "x", "new_string": "y"})',
            ),
            _step(3, action='exec_shell({"command": "pytest"})'),
        ]
        msg = _language_mismatched_verification_guard(
            steps,
            "done",
            is_code_mode=True,
        )
        assert msg is not None
        assert "typescript" in msg.lower()
        # Must NOT complain about python — that one was verified.
        assert "python" not in msg.lower()

    def test_multi_language_both_verified_silent(self) -> None:
        steps = [
            _step(
                1,
                action='edit_file({"path": "runtime/foo.py", "old_string": "a", "new_string": "b"})',
            ),
            _step(
                2,
                action='edit_file({"path": "frontend/Bar.tsx", "old_string": "x", "new_string": "y"})',
            ),
            _step(3, action='exec_shell({"command": "pytest"})'),
            _step(4, action='exec_shell({"command": "pnpm typecheck"})'),
        ]
        assert (
            _language_mismatched_verification_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is None
        )

    def test_help_request_short_circuits(self) -> None:
        # If the agent is punting (missing API key, etc.), don't pile on.
        steps = [
            _step(
                1,
                action='edit_file({"path": "frontend/Bar.tsx", "old_string": "x", "new_string": "y"})',
            ),
        ]
        final = "I cannot continue — please provide the API key."
        assert (
            _language_mismatched_verification_guard(
                steps,
                final,
                is_code_mode=True,
            )
            is None
        )

    def test_old_unverified_write_outside_window_silent(self) -> None:
        # Write happened > _LANG_MISMATCH_LOOKBACK steps ago — assume
        # the agent already verified or moved on.
        steps = [
            _step(
                1,
                action='edit_file({"path": "frontend/Bar.tsx", "old_string": "x", "new_string": "y"})',
            ),
        ] + [_step(i, action='read_file({"path": "x.py"})') for i in range(2, 20)]
        # Window default is 12 — last 12 steps don't include the .tsx write.
        assert (
            _language_mismatched_verification_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is None
        )
