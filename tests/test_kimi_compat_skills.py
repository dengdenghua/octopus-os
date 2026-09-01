"""Audit S-05: kimi-compat skills must not read/copy sensitive paths.

The deploy/version/image skills resolve caller-supplied paths through
_ensure_path; allow_sensitive=True used to let ~/.ssh and friends
through, so a prompt-injected request could publish secrets into the
servable deployments area.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.execution.suckers.kimi_compat_skills import (
    _deploy_website,
    _ensure_path,
)


def _fake_app_paths(tmp_path: Path):
    """Point app_paths().data_dir under tmp_path so deploys stay local."""
    import runtime.platform.process.paths as paths_mod

    class _Paths:
        data_dir = tmp_path / "data"

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(paths_mod, "app_paths", lambda: _Paths())
    return monkeypatch


def test_ensure_path_rejects_sensitive_home_dir(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".ssh").mkdir()
    p, err = _ensure_path(str(tmp_path / ".ssh"))
    assert p is not None
    assert err is not None and "path_blocked" in err and "sensitive" in err


def test_deploy_website_rejects_ssh_source(monkeypatch, tmp_path: Path):
    """S-05 acceptance: a deploy request sourcing ~/.ssh is rejected and
    nothing lands in the deployments area."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".ssh").mkdir()
    (tmp_path / ".ssh" / "id_rsa").write_text("secret", encoding="utf-8")
    mp = _fake_app_paths(tmp_path)
    try:
        result = _deploy_website(local_dir=str(tmp_path / ".ssh"))
    finally:
        mp.undo()
    assert result.get("ok") is not True
    assert "path_blocked" in (result.get("error") or "")


def test_deploy_website_still_works_for_legit_project(monkeypatch, tmp_path: Path):
    """Removing allow_sensitive must not break ordinary project deploys."""
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<h1>hi</h1>", encoding="utf-8")
    mp = _fake_app_paths(tmp_path)
    try:
        result = _deploy_website(local_dir=str(site))
    finally:
        mp.undo()
    assert result.get("ok") is True
    deployment = result["deployment"]
    dest = Path(deployment["path"])
    assert (dest / "index.html").read_text(encoding="utf-8") == "<h1>hi</h1>"

