"""Tests for settings.yaml-driven guard kill-switch.

The persistent layer companion to ECHO_DISABLED_GUARDS env var:

* YAML-only: ``safety.disabled_guards`` in config.local.yaml /
  config.yaml / config.example.yaml turns off named guards across
  restarts.
* Env-only: still works as before.
* Both: the two sets are unioned.
* Malformed / missing YAML: silent fallback to empty set — settings
  must never break the loop.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from runtime.core.cerebrum.react_loop import (
    _disabled_guard_labels,
    _disabled_guards_from_yaml,
    _reset_disabled_set_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_kill_switch():
    """Reset the cached last-seen set + ensure clean env."""
    _reset_disabled_set_for_tests()
    yield
    _reset_disabled_set_for_tests()


@pytest.fixture
def isolated_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run inside a tmp dir so YAML lookup hits only what we put there."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ECHO_DISABLED_GUARDS", raising=False)
    return tmp_path


def _write_yaml(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


# ══════════════════════════════════════════════════════════════════
# _disabled_guards_from_yaml — direct tests
# ══════════════════════════════════════════════════════════════════


class TestYamlReader:
    def test_no_files_returns_empty(self, isolated_cwd: Path) -> None:
        assert _disabled_guards_from_yaml() == frozenset()

    def test_yaml_with_safety_block(self, isolated_cwd: Path) -> None:
        _write_yaml(
            isolated_cwd / "config.local.yaml",
            """
safety:
  disabled_guards:
    - magic-number guard
    - long-function guard
""",
        )
        assert _disabled_guards_from_yaml() == frozenset(
            {
                "magic-number guard",
                "long-function guard",
            }
        )

    def test_yaml_without_safety_block(self, isolated_cwd: Path) -> None:
        _write_yaml(isolated_cwd / "config.local.yaml", "model: claude-sonnet\n")
        assert _disabled_guards_from_yaml() == frozenset()

    def test_yaml_with_empty_disabled_guards(self, isolated_cwd: Path) -> None:
        _write_yaml(
            isolated_cwd / "config.local.yaml",
            """
safety:
  disabled_guards: []
""",
        )
        assert _disabled_guards_from_yaml() == frozenset()

    def test_local_takes_priority_over_example(
        self,
        isolated_cwd: Path,
    ) -> None:
        _write_yaml(
            isolated_cwd / "config.local.yaml",
            """
safety:
  disabled_guards: [from-local]
""",
        )
        _write_yaml(
            isolated_cwd / "config.example.yaml",
            """
safety:
  disabled_guards: [from-example]
""",
        )
        # Lookup order is local → main → example. Local wins.
        assert _disabled_guards_from_yaml() == frozenset({"from-local"})

    def test_falls_through_to_example_when_local_missing(
        self,
        isolated_cwd: Path,
    ) -> None:
        _write_yaml(
            isolated_cwd / "config.example.yaml",
            """
safety:
  disabled_guards: [from-example]
""",
        )
        assert _disabled_guards_from_yaml() == frozenset({"from-example"})

    def test_malformed_yaml_silent(self, isolated_cwd: Path) -> None:
        _write_yaml(
            isolated_cwd / "config.local.yaml",
            "{this is not: valid: yaml: at all",
        )
        # Returns empty, doesn't raise.
        assert _disabled_guards_from_yaml() == frozenset()

    def test_yaml_top_level_not_dict_silent(
        self,
        isolated_cwd: Path,
    ) -> None:
        _write_yaml(isolated_cwd / "config.local.yaml", "- just\n- a list\n")
        assert _disabled_guards_from_yaml() == frozenset()

    def test_safety_not_dict_silent(self, isolated_cwd: Path) -> None:
        _write_yaml(isolated_cwd / "config.local.yaml", "safety: not_a_dict\n")
        assert _disabled_guards_from_yaml() == frozenset()

    def test_disabled_guards_not_list_silent(
        self,
        isolated_cwd: Path,
    ) -> None:
        _write_yaml(
            isolated_cwd / "config.local.yaml",
            """
safety:
  disabled_guards: just-a-string
""",
        )
        assert _disabled_guards_from_yaml() == frozenset()

    def test_skips_non_string_entries(self, isolated_cwd: Path) -> None:
        _write_yaml(
            isolated_cwd / "config.local.yaml",
            """
safety:
  disabled_guards:
    - real-guard
    - 42
    - null
    - "  whitespace-stripped  "
""",
        )
        # 42 and null are skipped; whitespace stripped on real strings.
        assert _disabled_guards_from_yaml() == frozenset(
            {
                "real-guard",
                "whitespace-stripped",
            }
        )


# ══════════════════════════════════════════════════════════════════
# _disabled_guard_labels — env + yaml union
# ══════════════════════════════════════════════════════════════════


class TestEnvYamlUnion:
    def test_yaml_only(self, isolated_cwd: Path) -> None:
        _write_yaml(
            isolated_cwd / "config.local.yaml",
            """
safety:
  disabled_guards: [from-yaml]
""",
        )
        assert _disabled_guard_labels() == frozenset({"from-yaml"})

    def test_env_only(
        self,
        isolated_cwd: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ECHO_DISABLED_GUARDS", "from-env")
        assert _disabled_guard_labels() == frozenset({"from-env"})

    def test_both_unioned(
        self,
        isolated_cwd: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_yaml(
            isolated_cwd / "config.local.yaml",
            """
safety:
  disabled_guards: [from-yaml-1, from-yaml-2]
""",
        )
        monkeypatch.setenv(
            "ECHO_DISABLED_GUARDS",
            "from-env-1,from-env-2",
        )
        assert _disabled_guard_labels() == frozenset(
            {
                "from-yaml-1",
                "from-yaml-2",
                "from-env-1",
                "from-env-2",
            }
        )

    def test_overlap_dedups(
        self,
        isolated_cwd: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Same guard listed in both — union dedupes.
        _write_yaml(
            isolated_cwd / "config.local.yaml",
            """
safety:
  disabled_guards: [shared-guard]
""",
        )
        monkeypatch.setenv("ECHO_DISABLED_GUARDS", "shared-guard")
        assert _disabled_guard_labels() == frozenset({"shared-guard"})

    def test_empty_when_neither_set(self, isolated_cwd: Path) -> None:
        assert _disabled_guard_labels() == frozenset()

    def test_yaml_broken_env_works(
        self,
        isolated_cwd: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Even with broken yaml, env still drives the kill-switch.
        _write_yaml(
            isolated_cwd / "config.local.yaml",
            "{garbage: not: valid",
        )
        monkeypatch.setenv("ECHO_DISABLED_GUARDS", "from-env")
        assert _disabled_guard_labels() == frozenset({"from-env"})
