from pathlib import Path

from runtime.execution.suckers.verify_skills import detect_project, run_checks


def test_unknown_project_file_count_is_cross_platform(tmp_path: Path) -> None:
    (tmp_path / "website").mkdir()
    (tmp_path / "website" / "index.html").write_text("<!doctype html>", encoding="utf-8")

    profile = detect_project(str(tmp_path))

    assert profile.kind == "unknown"
    assert profile.checks[0]["name"] == "file-count"
    # No Unix-only shell pipes in the argv — runs on Windows too.
    argv = profile.checks[0]["argv"]
    assert isinstance(argv, list) and argv
    joined = " ".join(argv)
    assert "find . -maxdepth" not in joined
    assert "wc -l" not in joined

    [result] = run_checks(profile, timeout_per_check=10)
    assert result.passed is True
    assert result.stdout.strip() == "1"
    assert result.command == 'python -c "count files up to depth 3"'


def test_python_syntax_check_avoids_unix_pipes(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")

    profile = detect_project(str(tmp_path))
    syntax = next(check for check in profile.checks if check["name"] == "syntax")

    argv = syntax["argv"]
    assert isinstance(argv, list) and argv
    joined = " ".join(argv)
    assert "find ." not in joined
    assert "head -" not in joined
    # Inline code still uses py_compile — no shell piping required.
    assert "py_compile" in joined


def test_python_typecheck_offered_only_when_mypy_installed(tmp_path: Path) -> None:
    # A missing checker must not become a check: "No module named mypy"
    # would be reported as a typecheck FAILURE and injected into the
    # agent's auto-diagnostics after every file write.
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")

    profile = detect_project(str(tmp_path))
    names = [check["name"] for check in profile.checks]

    import importlib.util

    if importlib.util.find_spec("mypy") is None:
        assert "typecheck" not in names
    else:
        assert "typecheck" in names
    # The dependency-free syntax check is always available as the
    # fast-path candidate for auto-diagnostics.
    assert "syntax" in names


def test_node_typecheck_offered_only_when_tsc_installed(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name": "demo", "scripts": {}}', encoding="utf-8")
    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")

    # No node_modules/.bin/tsc → no typecheck entry (a bare ``npx tsc``
    # could hit the network to fetch the package).
    profile = detect_project(str(tmp_path))
    assert "typecheck" not in [check["name"] for check in profile.checks]

    bin_dir = tmp_path / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "tsc").write_text("#!/bin/sh\n", encoding="utf-8")

    profile = detect_project(str(tmp_path))
    typecheck = next(check for check in profile.checks if check["name"] == "typecheck")
    assert "--no-install" in typecheck["argv"]


def test_auto_diagnostics_skips_missing_tool_output() -> None:
    from runtime.core.cerebrum.react_execution import _output_indicates_missing_tool

    assert _output_indicates_missing_tool("/usr/bin/python3: No module named mypy")
    assert _output_indicates_missing_tool("sh: tsc: command not found")
    assert _output_indicates_missing_tool("npm error could not determine executable to run")
    assert not _output_indicates_missing_tool(
        "error TS2345: Argument of type 'string' is not assignable"
    )
    assert not _output_indicates_missing_tool("app.py:3: SyntaxError")
