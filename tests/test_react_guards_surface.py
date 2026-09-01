"""Regression tests for §32 / §33 / §34 — the surface-area guards.

* §32: ``_frontend_outside_tsconfig_include_guard`` — TypeScript edit
  to a path NOT in tsconfig.json's `include`.
* §33: ``_oversized_single_edit_guard`` — single edit > 200 lines
  without a verifier in the trajectory.
* §34: ``_secret_in_payload_guard`` — credential-shaped string in a
  write payload (sk-..., ghp_..., AKIA..., private key block, etc.).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from runtime.core.cerebrum.react_guards import (
    _frontend_outside_tsconfig_include_guard,
    _oversized_single_edit_guard,
    _secret_in_payload_guard,
)
from runtime.core.cerebrum.react_parsing import (
    _count_payload_lines,
    _detect_secrets_in_payload,
    _is_frontend_path_outside_tsconfig,
    _matches_tsconfig_pattern,
    _step_introduces_secret,
    _step_is_oversized_edit,
    _strip_jsonc_comments,
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


# ══════════════════════════════════════════════════════════════════
# §32 — tsconfig.include guard
# ══════════════════════════════════════════════════════════════════


@pytest.fixture
def fake_repo_with_tsconfig(tmp_path: Path) -> Path:
    """Build a tmp tree resembling the real repo's frontend/tsconfig."""
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "tsconfig.json").write_text(
        """{
            "compilerOptions": { "noEmit": true },
            "include": [
                "src/main.tsx",
                "src/app/page.tsx",
                "src/types/electron.d.ts"
            ],
            "exclude": ["node_modules", "dist", "src/app/api"]
        }""",
        encoding="utf-8",
    )
    return tmp_path


class TestStripJsoncComments:
    def test_block_comment(self) -> None:
        assert _strip_jsonc_comments("/* hi */ {}") == " {}"

    def test_line_comment(self) -> None:
        # Stripper preserves the leading whitespace before //.
        result = _strip_jsonc_comments("{ // x\n}")
        assert "//" not in result and result.strip() in {"{\n}", "{ \n}"}

    def test_no_comments_passthrough(self) -> None:
        assert _strip_jsonc_comments('{"x": 1}') == '{"x": 1}'


class TestMatchesTsconfigPattern:
    def test_exact_match(self) -> None:
        assert _matches_tsconfig_pattern("src/main.tsx", "src/main.tsx")

    def test_glob_star(self) -> None:
        assert _matches_tsconfig_pattern("src/foo.tsx", "src/*.tsx")
        assert not _matches_tsconfig_pattern("src/sub/foo.tsx", "src/*.tsx")

    def test_globstar(self) -> None:
        assert _matches_tsconfig_pattern("src/sub/foo.tsx", "src/**/*.tsx")
        assert _matches_tsconfig_pattern("src/foo.tsx", "src/**/*.tsx")

    def test_directory_prefix(self) -> None:
        # Bare dir-name pattern means everything inside.
        assert _matches_tsconfig_pattern("node_modules/foo/index.js", "node_modules")

    def test_no_match(self) -> None:
        assert not _matches_tsconfig_pattern("src/utils/random.ts", "src/main.tsx")


class TestIsFrontendPathOutsideTsconfig:
    def test_in_include_silent(self, fake_repo_with_tsconfig: Path) -> None:
        assert not _is_frontend_path_outside_tsconfig(
            "frontend/src/main.tsx",
            repo_root=str(fake_repo_with_tsconfig),
        )

    def test_not_in_include_fires(self, fake_repo_with_tsconfig: Path) -> None:
        assert _is_frontend_path_outside_tsconfig(
            "frontend/src/utils/lonely.ts",
            repo_root=str(fake_repo_with_tsconfig),
        )

    def test_excluded_silent(self, fake_repo_with_tsconfig: Path) -> None:
        # `src/app/api` is in exclude — matching there means tsc *intentionally*
        # skips it, so no guard nag.
        assert not _is_frontend_path_outside_tsconfig(
            "frontend/src/app/api/foo.ts",
            repo_root=str(fake_repo_with_tsconfig),
        )

    def test_non_frontend_path_silent(self, fake_repo_with_tsconfig: Path) -> None:
        assert not _is_frontend_path_outside_tsconfig(
            "runtime/foo.py",
            repo_root=str(fake_repo_with_tsconfig),
        )

    def test_no_tsconfig_silent(self, tmp_path: Path) -> None:
        # No oracle → don't nag.
        assert not _is_frontend_path_outside_tsconfig(
            "frontend/src/foo.tsx",
            repo_root=str(tmp_path),
        )


class TestFrontendOutsideTsconfigGuard:
    def test_non_code_mode_silent(
        self, fake_repo_with_tsconfig: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(fake_repo_with_tsconfig)
        steps = [
            _step(
                1,
                action='edit_file({"path": "frontend/src/utils/lonely.ts", "old_string": "x", "new_string": "y"})',
            ),
        ]
        assert (
            _frontend_outside_tsconfig_include_guard(
                steps,
                "done",
                is_code_mode=False,
            )
            is None
        )

    def test_in_include_silent(
        self, fake_repo_with_tsconfig: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(fake_repo_with_tsconfig)
        steps = [
            _step(
                1,
                action='edit_file({"path": "frontend/src/main.tsx", "old_string": "x", "new_string": "y"})',
            ),
        ]
        assert (
            _frontend_outside_tsconfig_include_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is None
        )

    def test_outside_include_fires(
        self, fake_repo_with_tsconfig: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(fake_repo_with_tsconfig)
        steps = [
            _step(
                1,
                action='edit_file({"path": "frontend/src/utils/lonely.ts", "old_string": "x", "new_string": "y"})',
            ),
        ]
        msg = _frontend_outside_tsconfig_include_guard(
            steps,
            "done",
            is_code_mode=True,
        )
        assert msg is not None
        assert "lonely.ts" in msg
        assert "tsconfig" in msg.lower() or "include" in msg.lower()

    def test_help_request_short_circuits(
        self, fake_repo_with_tsconfig: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(fake_repo_with_tsconfig)
        steps = [
            _step(
                1,
                action='edit_file({"path": "frontend/src/utils/lonely.ts", "old_string": "x", "new_string": "y"})',
            ),
        ]
        assert (
            _frontend_outside_tsconfig_include_guard(
                steps,
                "I cannot continue — please provide the API key.",
                is_code_mode=True,
            )
            is None
        )


# ══════════════════════════════════════════════════════════════════
# §33 — oversized single-edit guard
# ══════════════════════════════════════════════════════════════════


class TestCountPayloadLines:
    def test_simple(self) -> None:
        assert _count_payload_lines("a\nb\nc\n") == 3

    def test_no_trailing_newline(self) -> None:
        assert _count_payload_lines("a\nb") == 2

    def test_empty(self) -> None:
        assert _count_payload_lines("") == 0
        assert _count_payload_lines(None) == 0


class TestStepIsOversizedEdit:
    def test_under_threshold_silent(self) -> None:
        small = "x = 1\n" * 50
        step = _step(
            1,
            action=f'write_text_file({{"path": "runtime/foo.py", "content": "{small}"}})',
        )
        assert not _step_is_oversized_edit(step)

    def test_over_threshold_fires(self) -> None:
        big = "\\n".join(["x = 1"] * 250)
        step = _step(
            1,
            action=f'write_text_file({{"path": "runtime/foo.py", "content": "{big}"}})',
        )
        assert _step_is_oversized_edit(step)

    def test_test_path_skipped(self) -> None:
        big = "\\n".join(["x = 1"] * 250)
        step = _step(
            1,
            action=f'write_text_file({{"path": "tests/test_huge.py", "content": "{big}"}})',
        )
        assert not _step_is_oversized_edit(step)

    def test_non_code_path_skipped(self) -> None:
        big = "\\n".join(["x: 1"] * 250)
        step = _step(
            1,
            action=f'write_text_file({{"path": "config/foo.yaml", "content": "{big}"}})',
        )
        assert not _step_is_oversized_edit(step)


class TestOversizedSingleEditGuard:
    def test_non_code_mode_silent(self) -> None:
        big = "\\n".join(["x = 1"] * 250)
        steps = [
            _step(1, action=f'write_text_file({{"path": "runtime/foo.py", "content": "{big}"}})'),
        ]
        assert (
            _oversized_single_edit_guard(
                steps,
                "done",
                is_code_mode=False,
            )
            is None
        )

    def test_no_oversized_silent(self) -> None:
        steps = [
            _step(
                1,
                action='edit_file({"path": "runtime/foo.py", "old_string": "x", "new_string": "y"})',
            )
        ]
        assert (
            _oversized_single_edit_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is None
        )

    def test_oversized_no_verify_fires(self) -> None:
        big = "\\n".join(["x = 1"] * 250)
        steps = [
            _step(1, action=f'write_text_file({{"path": "runtime/foo.py", "content": "{big}"}})'),
        ]
        msg = _oversized_single_edit_guard(
            steps,
            "done",
            is_code_mode=True,
        )
        assert msg is not None
        assert "200-line" in msg or "lines" in msg

    def test_oversized_with_verify_silent(self) -> None:
        big = "\\n".join(["x = 1"] * 250)
        steps = [
            _step(1, action=f'write_text_file({{"path": "runtime/foo.py", "content": "{big}"}})'),
            _step(2, action='exec_shell({"command": "pytest tests/"})'),
        ]
        assert (
            _oversized_single_edit_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is None
        )

    def test_help_request_short_circuits(self) -> None:
        big = "\\n".join(["x = 1"] * 250)
        steps = [
            _step(1, action=f'write_text_file({{"path": "runtime/foo.py", "content": "{big}"}})'),
        ]
        assert (
            _oversized_single_edit_guard(
                steps,
                "I cannot continue — please provide the API key.",
                is_code_mode=True,
            )
            is None
        )


# ══════════════════════════════════════════════════════════════════
# §34 — secret-leak guard
# ══════════════════════════════════════════════════════════════════


class TestDetectSecretsInPayload:
    def test_openai_style_key(self) -> None:
        hits = _detect_secrets_in_payload("API_KEY = 'sk-abcdefghijklmnopqrstuvwxyz1234567890'")
        assert any("OpenAI" in h or "Anthropic" in h for h in hits)

    def test_github_pat(self) -> None:
        hits = _detect_secrets_in_payload("token = ghp_abcdefghijklmnopqrstuvwxyz123456")
        assert any("GitHub" in h for h in hits)

    def test_aws_access_key(self) -> None:
        hits = _detect_secrets_in_payload("AKIAIOSFODNN7EXAMPLE")
        assert any("AWS" in h for h in hits)

    def test_slack_token(self) -> None:
        hits = _detect_secrets_in_payload("token = 'xoxb-1234-5678-abcdefghijk'")
        assert any("Slack" in h for h in hits)

    def test_private_key_block(self) -> None:
        hits = _detect_secrets_in_payload("-----BEGIN RSA PRIVATE KEY-----\nMIIxxx...\n")
        assert any("Private key" in h for h in hits)

    def test_inline_assigned_credential(self) -> None:
        hits = _detect_secrets_in_payload(
            'config = {"api_key": "abcdefghijklmnopqrstuvwxyz"}',
        )
        assert any("Inline" in h for h in hits)

    def test_clean_code_silent(self) -> None:
        assert _detect_secrets_in_payload("def hello():\n    return 1\n") == []


class TestStepIntroducesSecret:
    def test_new_secret_in_runtime_detected(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "runtime/foo.py", '
                '"old_string": "x = 1", '
                '"new_string": "x = 1\\nAPI_KEY = \\"sk-abcdefghijklmnopqrstuvwxyz1234567890\\""})'
            ),
        )
        labels = _step_introduces_secret(step)
        assert labels  # non-empty
        assert any("OpenAI" in label or "Anthropic" in label for label in labels)

    def test_pre_existing_secret_silent(self) -> None:
        sk = "sk-abcdefghijklmnopqrstuvwxyz1234567890"
        step = _step(
            1,
            action=(
                'edit_file({"path": "runtime/foo.py", '
                '"old_string": "API_KEY = \\"' + sk + '\\"", '
                '"new_string": "API_KEY = \\"' + sk + '\\"  # rotate me"})'
            ),
        )
        assert _step_introduces_secret(step) == []

    def test_non_write_silent(self) -> None:
        step = _step(1, action='read_file({"path": "runtime/foo.py"})')
        assert _step_introduces_secret(step) == []


class TestSecretInPayloadGuard:
    def test_non_code_mode_still_blocks_secret(self) -> None:
        sk = "sk-abcdefghijklmnopqrstuvwxyz1234567890"
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x = 1", '
                    '"new_string": "API_KEY = \\"' + sk + '\\""})'
                ),
            ),
        ]
        msg = _secret_in_payload_guard(steps, "done", is_code_mode=False)
        assert msg is not None
        assert "credential" in msg.lower()

    def test_no_secret_silent(self) -> None:
        steps = [
            _step(
                1,
                action='edit_file({"path": "runtime/foo.py", "old_string": "x", "new_string": "y"})',
            )
        ]
        assert (
            _secret_in_payload_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is None
        )

    def test_secret_fires(self) -> None:
        sk = "sk-abcdefghijklmnopqrstuvwxyz1234567890"
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x = 1", '
                    '"new_string": "API_KEY = \\"' + sk + '\\""})'
                ),
            ),
        ]
        msg = _secret_in_payload_guard(
            steps,
            "done",
            is_code_mode=True,
        )
        assert msg is not None
        assert "credential" in msg.lower() or "secret" in msg.lower()
        assert "runtime/foo.py" in msg

    def test_secret_fires_even_on_help_request(self) -> None:
        # SECURITY: leaking a secret while asking for help is still a
        # leak. The §34 guard intentionally has NO help-request short
        # circuit.
        sk = "sk-abcdefghijklmnopqrstuvwxyz1234567890"
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x = 1", '
                    '"new_string": "API_KEY = \\"' + sk + '\\""})'
                ),
            ),
        ]
        assert (
            _secret_in_payload_guard(
                steps,
                "I cannot continue — please provide a real API key.",
                is_code_mode=True,
            )
            is not None
        )
