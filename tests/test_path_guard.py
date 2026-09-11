"""Implementation note."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from runtime.safety.auth import check_path, is_safe_path

# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestSandboxContainment:
    def test_relative_inside_sandbox_allowed(self, tmp_path):
        v = check_path("inner.txt", sandbox_dir=tmp_path)
        assert v.allow
        assert Path(v.resolved).parent == tmp_path.resolve()

    def test_relative_escape_blocked(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        v = check_path("../evil.txt", sandbox_dir=sub)
        assert not v.allow
        assert "escapes_sandbox" in v.reason

    def test_absolute_outside_sandbox_blocked(self, tmp_path):
        outside = tmp_path.parent / "outside.txt"
        v = check_path(str(outside), sandbox_dir=tmp_path)
        assert not v.allow
        assert "escapes_sandbox" in v.reason

    def test_deep_relative_escape_blocked(self, tmp_path):
        """Implementation note."""
        sub = tmp_path / "a" / "b" / "c"
        sub.mkdir(parents=True)
        v = check_path("../../../evil.txt", sandbox_dir=sub)
        # Implementation note.
        assert not v.allow
        assert "escapes_sandbox" in v.reason

    def test_no_sandbox_no_containment_check(self, tmp_path):
        """Implementation note."""
        v = check_path(str(tmp_path / "some.txt"))
        assert v.allow

    def test_symlink_escape_blocked(self, tmp_path):
        """Implementation note."""
        sub = tmp_path / "sub"
        sub.mkdir()
        outside = tmp_path / "secret.txt"
        outside.write_text("classified", encoding="utf-8")
        link = sub / "escape_link"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            pytest.skip("symlink not supported in this env")

        v = check_path("escape_link", sandbox_dir=sub)
        assert not v.allow
        assert "escapes_sandbox" in v.reason


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestSensitivePaths:
    @pytest.mark.skipif(sys.platform == "win32", reason="unix paths")
    @pytest.mark.parametrize(
        "path",
        [
            "/etc/passwd",
            "/etc/shadow",
            "/etc/sudoers",
            "/root/.bashrc",
            "/root/anything",
        ],
    )
    def test_unix_absolute_sensitive_blocked(self, path):
        v = check_path(path)
        assert not v.allow
        assert "sensitive_abs_path" in v.reason

    def test_home_ssh_blocked(self, monkeypatch, tmp_path):
        """Implementation note."""
        fake_home = tmp_path / "home" / "alice"
        fake_home.mkdir(parents=True)
        ssh_dir = fake_home / ".ssh"
        ssh_dir.mkdir()
        target = ssh_dir / "id_rsa"
        target.write_text("FAKE KEY", encoding="utf-8")

        # Implementation note.
        monkeypatch.setattr(os.path, "expanduser", lambda p: str(fake_home))

        v = check_path(str(target))
        assert not v.allow
        assert "sensitive_home_path" in v.reason

    def test_home_aws_blocked(self, monkeypatch, tmp_path):
        fake_home = tmp_path / "h"
        (fake_home / ".aws").mkdir(parents=True)
        creds = fake_home / ".aws" / "credentials"
        creds.write_text("[default]", encoding="utf-8")
        monkeypatch.setattr(os.path, "expanduser", lambda p: str(fake_home))

        v = check_path(str(creds))
        assert not v.allow
        assert ".aws" in v.reason

    def test_allow_sensitive_flag(self, monkeypatch, tmp_path):
        fake_home = tmp_path / "h"
        (fake_home / ".ssh").mkdir(parents=True)
        key = fake_home / ".ssh" / "id_rsa"
        key.write_text("x", encoding="utf-8")
        monkeypatch.setattr(os.path, "expanduser", lambda p: str(fake_home))

        v = check_path(str(key), allow_sensitive=True)
        assert v.allow  # Implementation note.

    def test_non_sensitive_path_passes(self, tmp_path):
        regular = tmp_path / "data" / "file.txt"
        regular.parent.mkdir(parents=True)
        regular.write_text("x", encoding="utf-8")
        v = check_path(str(regular))
        assert v.allow


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestDOSDeviceNames:
    @pytest.mark.skipif(sys.platform != "win32", reason="windows-only rule")
    @pytest.mark.parametrize(
        "path",
        [
            "CON",
            "con",
            "aux.txt",
            "nul",
            "COM1.log",
            "LPT3",
            "C:/temp/aux",
        ],
    )
    def test_dos_device_blocked_on_windows(self, path):
        v = check_path(path)
        assert not v.allow
        assert "dos_device_name" in v.reason

    @pytest.mark.skipif(sys.platform == "win32", reason="unix path semantics")
    def test_dos_device_names_not_triggered_on_unix(self):
        """Implementation note."""
        v = check_path("/tmp/CON")
        # Implementation note.
        assert "dos_device_name" not in v.reason


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestBasicValidation:
    def test_empty_path_blocked(self):
        v = check_path("")
        assert not v.allow
        assert "empty_path" in v.reason

    def test_must_exist_when_missing(self, tmp_path):
        v = check_path(
            str(tmp_path / "nope.txt"),
            sandbox_dir=tmp_path,
            must_exist=True,
        )
        assert not v.allow
        assert "not_found" in v.reason

    def test_must_exist_when_exists(self, tmp_path):
        f = tmp_path / "yes.txt"
        f.write_text("x", encoding="utf-8")
        v = check_path(str(f), sandbox_dir=tmp_path, must_exist=True)
        assert v.allow


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestIsSafePath:
    def test_shortcut(self, tmp_path):
        assert is_safe_path(str(tmp_path / "a"), sandbox_dir=tmp_path)
        sub = tmp_path / "sub"
        sub.mkdir()
        assert not is_safe_path("../outside", sandbox_dir=sub)


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestReadFileIntegration:
    def test_read_file_blocks_escape(self, tmp_path):
        from runtime.execution.suckers.builtins import _read_file

        outside = tmp_path.parent / f"escape_{tmp_path.name}.txt"
        outside.write_text("secret", encoding="utf-8")

        result = _read_file(str(outside), sandbox_dir=str(tmp_path))
        assert "error" in result
        assert "path_blocked" in result["error"]

    def test_read_file_blocks_sensitive(self, monkeypatch, tmp_path):
        from runtime.execution.suckers.builtins import _read_file

        fake_home = tmp_path / "h"
        (fake_home / ".ssh").mkdir(parents=True)
        key = fake_home / ".ssh" / "id_rsa"
        key.write_text("FAKE", encoding="utf-8")
        monkeypatch.setattr(os.path, "expanduser", lambda p: str(fake_home))

        result = _read_file(str(key))
        assert "error" in result
        assert "path_blocked" in result["error"]
        assert "sensitive" in result["error"]

    def test_read_file_normal_still_works(self, tmp_path):
        from runtime.execution.suckers.builtins import _read_file

        f = tmp_path / "ok.txt"
        f.write_text("hello", encoding="utf-8")
        result = _read_file(str(f))
        assert "error" not in result
        assert result["content"] == "hello"

    def test_allow_sensitive_overrides(self, monkeypatch, tmp_path):
        from runtime.execution.suckers.builtins import _read_file

        fake_home = tmp_path / "h"
        (fake_home / ".ssh").mkdir(parents=True)
        key = fake_home / ".ssh" / "id_rsa"
        key.write_text("CONTENT", encoding="utf-8")
        monkeypatch.setattr(os.path, "expanduser", lambda p: str(fake_home))

        result = _read_file(str(key), allow_sensitive=True)
        assert "error" not in result
        assert result["content"] == "CONTENT"


class TestFileStatsIntegration:
    def test_file_stats_blocks_sensitive(self, monkeypatch, tmp_path):
        from runtime.execution.suckers.builtins import _file_stats

        fake_home = tmp_path / "h"
        (fake_home / ".ssh").mkdir(parents=True)
        key = fake_home / ".ssh" / "id_rsa"
        key.write_text("x", encoding="utf-8")
        monkeypatch.setattr(os.path, "expanduser", lambda p: str(fake_home))

        result = _file_stats(str(key))
        assert "error" in result
        assert "path_blocked" in result["error"]


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestWriteSkillsBackwardCompat:
    def test_escape_still_caught_via_path_guard(self, tmp_path):
        from runtime.execution.suckers.write_skills import _ensure_sandbox

        sub = tmp_path / "sub"
        sub.mkdir()
        _, err = _ensure_sandbox("../evil.txt", sub)
        assert err is not None
        assert "path_escapes_sandbox" in err

    def test_sensitive_caught_even_without_sandbox(self, monkeypatch, tmp_path):
        """Implementation note."""
        from runtime.execution.suckers.write_skills import _ensure_sandbox

        fake_home = tmp_path / "h"
        (fake_home / ".ssh").mkdir(parents=True)
        monkeypatch.setattr(os.path, "expanduser", lambda p: str(fake_home))

        _, err = _ensure_sandbox(str(fake_home / ".ssh" / "id_rsa"), None)
        assert err is not None
        assert "sensitive" in err
