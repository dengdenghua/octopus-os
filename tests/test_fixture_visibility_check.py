"""Tests for the fixture-visibility guard.

The guard exists because a blanket ``.gitignore`` suffix rule twice hid a file
the tests read, staying green locally and failing on every fresh clone. These
tests build throwaway repositories so the assertions never depend on this
checkout's own ignore rules.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.lint import fixture_visibility_check as guard


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)


def _fixture_dir(root: Path) -> Path:
    d = root / "benchmarks" / "fixtures" / "case"
    d.mkdir(parents=True)
    return d


def test_passes_when_fixture_is_tracked(tmp_path: Path, capsys) -> None:
    _init_repo(tmp_path)
    (tmp_path / ".gitignore").write_text("*.db\n!benchmarks/fixtures/**/*.db\n", encoding="utf-8")
    (_fixture_dir(tmp_path) / "data.db").write_text("ROWS=1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)

    assert guard.main(["--repo-root", str(tmp_path)]) == 0
    assert "0 hidden" in capsys.readouterr().out


def test_fails_when_a_blanket_rule_hides_a_fixture(tmp_path: Path, capsys) -> None:
    _init_repo(tmp_path)
    # No negation: the blanket rule swallows the fixture, which is exactly the
    # regression that shipped twice.
    (tmp_path / ".gitignore").write_text("*.db\n", encoding="utf-8")
    (_fixture_dir(tmp_path) / "data.db").write_text("ROWS=1\n", encoding="utf-8")

    assert guard.main(["--repo-root", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "data.db" in err
    # The operator needs the offending rule, not just the file name.
    assert "*.db" in err


def test_ignores_editor_and_cache_junk(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / ".gitignore").write_text(".DS_Store\n__pycache__/\n", encoding="utf-8")
    d = _fixture_dir(tmp_path)
    (d / ".DS_Store").write_bytes(b"\x00")
    (d / "__pycache__").mkdir()
    (d / "__pycache__" / "x.pyc").write_bytes(b"\x00")

    assert guard.main(["--repo-root", str(tmp_path)]) == 0


def test_missing_fixture_root_is_not_an_error(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    assert guard.main(["--repo-root", str(tmp_path)]) == 0


@pytest.mark.parametrize("root", guard.FIXTURE_ROOTS)
def test_declared_roots_are_relative(root: str) -> None:
    """A leading slash or '..' would escape the repo when joined."""
    assert not Path(root).is_absolute()
    assert ".." not in Path(root).parts


def test_this_repo_has_no_hidden_fixtures() -> None:
    """The guard must hold for the real checkout, not just synthetic repos."""
    assert guard.main([]) == 0

