"""Tests for tools.lint.desktop_core_sync_check."""

from __future__ import annotations

import textwrap
from pathlib import Path

from tools.lint import desktop_core_sync_check as check


def _write(tmp_path: Path, pyproject: str, electron: str) -> tuple[Path, Path]:
    py = tmp_path / "pyproject.toml"
    cjs = tmp_path / "backend-runtime.cjs"
    py.write_text(textwrap.dedent(pyproject), encoding="utf-8")
    cjs.write_text(textwrap.dedent(electron), encoding="utf-8")
    return py, cjs


def _run(monkeypatch, py: Path, cjs: Path, *argv: str) -> int:
    monkeypatch.setattr(check, "PYPROJECT", py)
    monkeypatch.setattr(check, "ELECTRON", cjs)
    monkeypatch.setattr("sys.argv", ["desktop_core_sync_check.py", *argv])
    return check.main()


def test_real_repo_passes_strict(monkeypatch):
    if not check.PYPROJECT.exists() or not check.ELECTRON.exists():
        return  # workspace layout changed; unit tests below still cover logic
    monkeypatch.setattr("sys.argv", ["desktop_core_sync_check.py", "--strict"])
    assert check.main() == 0


def test_core_drift_is_error(monkeypatch, tmp_path):
    py, cjs = _write(
        tmp_path,
        """
        [project.optional-dependencies]
        desktop-core = ["fastapi>=0.115,<1.0", "httpx>=0.27"]
        vision = ["fastembed>=0.8.0"]
        """,
        """
        const CORE_DEPS = ["fastapi>=0.115,<1.0"];
        const OPTIONAL_GROUPS = {
          vision: ["fastembed>=0.8.0"],
        };
        """,
    )
    assert _run(monkeypatch, py, cjs) == 1


def test_missing_group_is_error(monkeypatch, tmp_path):
    py, cjs = _write(
        tmp_path,
        """
        [project.optional-dependencies]
        desktop-core = ["httpx>=0.27"]
        """,
        """
        const CORE_DEPS = ["httpx>=0.27"];
        const OPTIONAL_GROUPS = {
          ghost: ["sneaky>=1.0"],
        };
        """,
    )
    assert _run(monkeypatch, py, cjs) == 1


def test_optional_drift_ok_in_report_strict_fails(monkeypatch, tmp_path):
    py, cjs = _write(
        tmp_path,
        """
        [project.optional-dependencies]
        desktop-core = ["httpx>=0.27"]
        desktop = ["pyautogui>=0.9.54", "pillow>=10.0"]
        vision = ["fastembed>=0.8.0", "rapidocr-onnxruntime>=1.3.0"]
        mcp = ["mcp>=1.28.1,<2.0", "pyjwt[crypto]>=2.13.0"]
        """,
        """
        const CORE_DEPS = ["httpx>=0.27"];
        const OPTIONAL_GROUPS = {
          desktop: ["pyautogui>=0.9.54"],
          vision: ["fastembed>=0.8.0"],
          mcp: ["mcp>=0.9,<2.0"],
        };
        """,
    )
    # report mode: optional drift is a warning -> exit 0
    assert _run(monkeypatch, py, cjs) == 0
    # strict mode: any drift fails
    assert _run(monkeypatch, py, cjs, "--strict") == 1

