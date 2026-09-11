"""Implementation note."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from runtime.sensing.server.ssh import (
    SshBackend,
    SshSandbox,
    SshUnavailableError,
    _sh_quote,
)

# ═══════════════════════════════════════════════════════════
# _sh_quote
# ═══════════════════════════════════════════════════════════


class TestShQuote:
    def test_empty_string(self):
        assert _sh_quote("") == "''"

    def test_simple_identifier_unquoted(self):
        assert _sh_quote("hello") == "hello"
        assert _sh_quote("path/to/file") == "path/to/file"
        assert _sh_quote("a.b-c_d") == "a.b-c_d"

    def test_spaces_force_quote(self):
        assert _sh_quote("hello world") == "'hello world'"

    def test_embedded_single_quote_escaped(self):
        # POSIX trick: 'foo'\''bar'
        assert _sh_quote("foo'bar") == "'foo'\\''bar'"

    def test_special_chars_quoted(self):
        assert _sh_quote("$VAR") == "'$VAR'"
        assert _sh_quote("`cmd`") == "'`cmd`'"
        assert _sh_quote("a;b") == "'a;b'"


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestSshMantleValidation:
    def test_requires_host(self):
        with pytest.raises(ValueError, match="host"):
            SshBackend(host="")

    def test_rejects_bad_port(self):
        with pytest.raises(ValueError, match="port"):
            SshBackend(host="x", port=0)
        with pytest.raises(ValueError, match="port"):
            SshBackend(host="x", port=70000)

    def test_rejects_zero_timeout(self):
        with pytest.raises(ValueError, match="timeout"):
            SshBackend(host="x", timeout_seconds=0)

    def test_rejects_password_in_cli_mode(self):
        """Implementation note."""
        with pytest.raises(ValueError, match="paramiko"):
            SshBackend(host="x", password="secret", use_paramiko=False)

    def test_password_with_paramiko_ok(self):
        m = SshBackend(host="x", password="secret", use_paramiko=True)
        assert m.password == "secret"


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestRequireAvailable:
    def test_cli_mode_missing_ssh_raises(self):
        m = SshBackend(host="x")
        with (
            patch(
                "runtime.sensing.server.ssh.shutil.which",
                return_value=None,
            ),
            pytest.raises(SshUnavailableError, match="ssh CLI"),
        ):
            m.require_available()

    def test_cli_mode_with_ssh_ok(self):
        m = SshBackend(host="x")
        with patch("runtime.sensing.server.ssh.shutil.which", return_value="/usr/bin/ssh"):
            m.require_available()  # Implementation note.

    def test_paramiko_mode_missing_raises(self):
        m = SshBackend(host="x", use_paramiko=True)
        with patch.dict("sys.modules", {"paramiko": None}):
            # Implementation note.
            import builtins

            orig = builtins.__import__

            def fake_import(name, *a, **kw):
                if name == "paramiko":
                    raise ImportError("no paramiko")
                return orig(name, *a, **kw)

            with (
                patch(
                    "builtins.__import__",
                    side_effect=fake_import,
                ),
                pytest.raises(SshUnavailableError, match="paramiko"),
            ):
                m.require_available()


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestBuildSshArgv:
    def _box(self, **kw) -> SshSandbox:
        m = SshBackend(**kw)
        return SshSandbox(backend=m, audit=MagicMock(), span=MagicMock())

    def test_basic_argv_includes_port_timeout_target(self):
        box = self._box(host="h.example", user="u", port=2222)
        argv = box._build_ssh_argv(["echo", "hi"], cwd=None)
        assert "-p" in argv
        assert "2222" in argv
        assert "u@h.example" in argv
        assert "ConnectTimeout=10" in " ".join(argv)
        assert "--" in argv

    def test_no_user_uses_bare_host(self):
        box = self._box(host="h.example")
        argv = box._build_ssh_argv(["echo"], cwd=None)
        assert "h.example" in argv
        assert "@" not in " ".join(argv).split("--")[0]

    def test_batch_mode_enabled(self):
        """Implementation note."""
        box = self._box(host="h")
        argv = box._build_ssh_argv(["echo"], cwd=None)
        assert "BatchMode=yes" in " ".join(argv)

    def test_identity_file_passed(self):
        box = self._box(host="h", identity_file="/secrets/id_rsa")
        argv = box._build_ssh_argv(["echo"], cwd=None)
        joined = " ".join(argv)
        assert "-i" in argv
        assert "id_rsa" in joined  # Implementation note.
        assert "IdentitiesOnly=yes" in joined

    def test_strict_host_key_off_uses_dev_null(self):
        box = self._box(host="h", strict_host_key_checking=False)
        joined = " ".join(box._build_ssh_argv(["echo"], cwd=None))
        assert "StrictHostKeyChecking=no" in joined
        assert "UserKnownHostsFile=/dev/null" in joined

    def test_known_hosts_file_passed(self):
        box = self._box(host="h", known_hosts_file="/tmp/kh")
        joined = " ".join(box._build_ssh_argv(["echo"], cwd=None))
        assert "UserKnownHostsFile=" in joined
        assert "kh" in joined  # Implementation note.

    def test_inner_argv_after_double_dash(self):
        box = self._box(host="h")
        argv = box._build_ssh_argv(["python3", "-c", "print(1)"], cwd=None)
        idx = argv.index("--")
        assert argv[idx + 1 :] == ["python3", "-c", "print(1)"]

    def test_env_wraps_argv_with_env_prefix(self):
        box = self._box(host="h", env={"FOO": "1", "BAR": "2"})
        argv = box._build_ssh_argv(["echo", "ok"], cwd=None)
        idx = argv.index("--")
        tail = argv[idx + 1 :]
        assert tail[0] == "env"
        assert "FOO=1" in tail
        assert "BAR=2" in tail
        assert tail[-2:] == ["echo", "ok"]

    def test_cwd_uses_sh_c(self):
        box = self._box(host="h")
        argv = box._build_ssh_argv(["echo", "hi"], cwd="/tmp")
        idx = argv.index("--")
        tail = argv[idx + 1 :]
        assert tail[0] == "sh"
        assert tail[1] == "-c"
        script = tail[2]
        assert "cd /tmp" in script
        assert "exec echo hi" in script

    def test_cwd_with_spaces_is_quoted(self):
        box = self._box(host="h")
        argv = box._build_ssh_argv(["echo"], cwd="/my dir")
        script = argv[-1]
        assert "cd '/my dir'" in script


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestRunCommand:
    def test_rejects_empty_argv(self):
        m = SshBackend(host="h")
        with m.sandbox("test") as box:
            r = box.run_command([])
            assert "error" in r

    def test_rejects_non_str_argv(self):
        m = SshBackend(host="h")
        with m.sandbox("test") as box:
            r = box.run_command(["echo", 1])  # type: ignore[list-item]
            assert "error" in r

    def test_successful_run_returns_stdout(self):
        m = SshBackend(host="h")
        stream_result = {
            "stdout": "ok\n",
            "stderr": "",
            "exit_code": 0,
            "timed_out": False,
            "stdout_truncated": False,
            "stderr_truncated": False,
        }
        with (
            patch("runtime.sensing.server.ssh.shutil.which", return_value="/usr/bin/ssh"),
            patch(
                "runtime.sensing.server._streaming.stream_run",
                return_value=stream_result,
            ),
            m.sandbox("test") as box,
        ):
            r = box.run_command(["echo", "ok"])
        assert r["exit_code"] == 0
        assert r["stdout"] == "ok\n"
        assert r["timed_out"] is False
        assert r["host"] == "h"

    def test_timeout_returns_timed_out(self):
        m = SshBackend(host="h", timeout_seconds=0.5)
        stream_result = {
            "stdout": "",
            "stderr": "",
            "exit_code": None,
            "timed_out": True,
            "stdout_truncated": False,
            "stderr_truncated": False,
        }
        with (
            patch("runtime.sensing.server.ssh.shutil.which", return_value="/usr/bin/ssh"),
            patch(
                "runtime.sensing.server._streaming.stream_run",
                return_value=stream_result,
            ),
            m.sandbox("test") as box,
        ):
            r = box.run_command(["sleep", "10"])
        assert r["timed_out"] is True
        assert r["exit_code"] is None
        assert box.timeouts == 1

    def test_missing_ssh_binary_raises(self):
        m = SshBackend(host="h")
        with (
            patch(
                "runtime.sensing.server.ssh.shutil.which",
                return_value=None,
            ),
            m.sandbox("test") as box,
            pytest.raises(SshUnavailableError),
        ):
            box.run_command(["echo", "x"])

    def test_stdout_truncation(self):
        m = SshBackend(host="h")
        big = "x" * 200_000
        stream_result = {
            "stdout": big,
            "stderr": "",
            "exit_code": 0,
            "timed_out": False,
            "stdout_truncated": True,
            "stderr_truncated": False,
        }
        with (
            patch("runtime.sensing.server.ssh.shutil.which", return_value="/usr/bin/ssh"),
            patch(
                "runtime.sensing.server._streaming.stream_run",
                return_value=stream_result,
            ),
            m.sandbox("test") as box,
        ):
            r = box.run_command(["cat", "big"])
        assert len(r["stdout"]) == 200_000
        assert r["stdout_truncated"] is True

    def test_run_count_increments(self):
        m = SshBackend(host="h")
        stream_result = {
            "stdout": "",
            "stderr": "",
            "exit_code": 0,
            "timed_out": False,
            "stdout_truncated": False,
            "stderr_truncated": False,
        }
        with (
            patch("runtime.sensing.server.ssh.shutil.which", return_value="/usr/bin/ssh"),
            patch(
                "runtime.sensing.server._streaming.stream_run",
                return_value=stream_result,
            ),
            m.sandbox("test") as box,
        ):
            box.run_command(["echo"])
            box.run_command(["echo"])
            assert box.run_command_count == 2
