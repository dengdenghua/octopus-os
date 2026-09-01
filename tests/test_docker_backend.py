"""Implementation note."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from runtime.sensing.server import (
    DockerBackend,
    DockerUnavailableError,
)
from runtime.sensing.server.docker import _docker_available

_DOCKER_AVAILABLE = _docker_available()


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestConstructor:
    def test_defaults(self):
        m = DockerBackend()
        assert m.image == "python:3.11-slim"
        assert m.network_mode == "none"
        assert m.memory_mb is None
        assert m.cpus is None
        assert m.volumes == []

    def test_rejects_bad_timeout(self):
        with pytest.raises(ValueError):
            DockerBackend(timeout_seconds=0)

    def test_rejects_bad_memory(self):
        with pytest.raises(ValueError):
            DockerBackend(memory_mb=0)

    def test_rejects_bad_cpus(self):
        with pytest.raises(ValueError):
            DockerBackend(cpus=-1)

    def test_rejects_bad_volume_shape(self):
        with pytest.raises(ValueError):
            DockerBackend(volumes=[("/host", "/container")])  # Implementation note.
        with pytest.raises(ValueError):
            DockerBackend(volumes=[("/host", "/container", "rwx")])  # Implementation note.


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class _FakeProc:
    def __init__(self, stdout="ok\n", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _fake_result(stdout: str = "ok\n", stderr: str = "", returncode: int = 0) -> dict:
    return {
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": returncode,
        "timed_out": False,
        "stdout_truncated": False,
        "stderr_truncated": False,
    }


@pytest.fixture
def captured_argv():
    """Implementation note."""
    calls: list[list[str]] = []

    def _fake_stream(argv, **kwargs):
        calls.append(list(argv))
        return _fake_result()

    with (
        patch(
            "runtime.sensing.server.docker.shutil.which",
            return_value="/usr/bin/docker",
        ),
        patch(
            "runtime.sensing.server._streaming.stream_run",
            side_effect=_fake_stream,
        ),
    ):
        yield calls


class TestArgvComposition:
    def test_basic_argv(self, captured_argv):
        m = DockerBackend(image="alpine:3.19")
        with m.sandbox(arm_id="t") as box:
            box.run_command(["echo", "hi"])
        argv = captured_argv[0]
        assert argv[0] == "/usr/bin/docker"
        assert argv[1] == "run"
        assert "--rm" in argv
        assert "-i" in argv
        assert "--network" in argv
        assert argv[argv.index("--network") + 1] == "none"
        assert "alpine:3.19" in argv
        # Implementation note.
        i = argv.index("alpine:3.19")
        assert argv[i + 1 :] == ["echo", "hi"]

    def test_resource_limits(self, captured_argv):
        m = DockerBackend(memory_mb=256, cpus=0.5)
        with m.sandbox(arm_id="t") as box:
            box.run_command(["true"])
        argv = captured_argv[0]
        assert "-m" in argv
        assert argv[argv.index("-m") + 1] == "256m"
        assert "--cpus" in argv
        assert argv[argv.index("--cpus") + 1] == "0.5"

    def test_read_only_flag(self, captured_argv):
        m = DockerBackend(read_only=True)
        with m.sandbox(arm_id="t") as box:
            box.run_command(["true"])
        assert "--read-only" in captured_argv[0]

    def test_network_bridge(self, captured_argv):
        m = DockerBackend(network_mode="bridge")
        with m.sandbox(arm_id="t") as box:
            box.run_command(["true"])
        argv = captured_argv[0]
        assert argv[argv.index("--network") + 1] == "bridge"

    def test_volumes_and_env(self, captured_argv, tmp_path):
        m = DockerBackend(
            volumes=[(str(tmp_path), "/workspace", "ro")],
            env={"FOO": "bar"},
        )
        with m.sandbox(arm_id="t") as box:
            box.run_command(["true"])
        argv = captured_argv[0]
        # -v present
        vs = [argv[i + 1] for i, a in enumerate(argv) if a == "-v"]
        assert vs
        assert vs[0].endswith(":/workspace:ro")
        # -e present
        es = [argv[i + 1] for i, a in enumerate(argv) if a == "-e"]
        assert "FOO=bar" in es

    def test_cwd_forwarded(self, captured_argv):
        m = DockerBackend()
        with m.sandbox(arm_id="t") as box:
            box.run_command(["pwd"], cwd="/workspace")
        argv = captured_argv[0]
        assert "-w" in argv
        assert argv[argv.index("-w") + 1] == "/workspace"

    def test_extra_args_passthrough(self, captured_argv):
        m = DockerBackend(extra_args=["--user", "1000:1000"])
        with m.sandbox(arm_id="t") as box:
            box.run_command(["id"])
        argv = captured_argv[0]
        assert "--user" in argv
        assert argv[argv.index("--user") + 1] == "1000:1000"


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestRunCommandInput:
    def test_empty_argv_error(self, captured_argv):
        m = DockerBackend()
        with m.sandbox(arm_id="t") as box:
            r = box.run_command([])
        assert "error" in r

    def test_non_list_argv_error(self, captured_argv):
        m = DockerBackend()
        with m.sandbox(arm_id="t") as box:
            r = box.run_command("echo hi")  # type: ignore[arg-type]
        assert "error" in r

    def test_non_str_element_error(self, captured_argv):
        m = DockerBackend()
        with m.sandbox(arm_id="t") as box:
            r = box.run_command(["echo", 42])  # type: ignore[list-item]
        assert "error" in r

    def test_success_result_shape(self, captured_argv):
        m = DockerBackend()
        with m.sandbox(arm_id="t") as box:
            r = box.run_command(["true"])
        assert r["stdout"] == "ok\n"
        assert r["exit_code"] == 0
        assert r["timed_out"] is False
        assert r["container"].startswith("echo-")

    def test_non_zero_exit_code_not_error(self, captured_argv):
        """Implementation note."""
        with (
            patch(
                "runtime.sensing.server.docker.shutil.which",
                return_value="/usr/bin/docker",
            ),
            patch(
                "runtime.sensing.server._streaming.stream_run",
                return_value=_fake_result(stdout="", stderr="boom", returncode=42),
            ),
        ):
            m = DockerBackend()
            with m.sandbox(arm_id="t") as box:
                r = box.run_command(["false"])
            assert "error" not in r
            assert r["exit_code"] == 42
            assert r["stderr"] == "boom"


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestTimeout:
    def test_timeout_kills_container_and_returns_flag(self):
        kill_calls: list[list[str]] = []

        def _fake_stream(argv, *, on_timeout=None, **kwargs):
            # Simulate timeout: the helper invokes ``on_timeout`` before
            # returning the timed-out result.
            if on_timeout is not None:
                on_timeout(None)
            return {
                "stdout": "",
                "stderr": "",
                "exit_code": None,
                "timed_out": True,
                "stdout_truncated": False,
                "stderr_truncated": False,
            }

        def _fake_subprocess_run(argv, **kwargs):
            # Only the cleanup ``docker kill`` still flows through
            # ``subprocess.run``. Capture it.
            kill_calls.append(list(argv))

            class _R:
                returncode = 0
                stdout = ""
                stderr = ""

            return _R()

        with (
            patch(
                "runtime.sensing.server.docker.shutil.which",
                return_value="/usr/bin/docker",
            ),
            patch(
                "runtime.sensing.server._streaming.stream_run",
                side_effect=_fake_stream,
            ),
            patch(
                "runtime.sensing.server.docker.subprocess.run",
                side_effect=_fake_subprocess_run,
            ),
        ):
            m = DockerBackend(timeout_seconds=1.0)
            with m.sandbox(arm_id="t") as box:
                r = box.run_command(["sleep", "60"])
            assert r["timed_out"] is True
            assert r["exit_code"] is None
            assert kill_calls
            assert kill_calls[0][1] == "kill"
            assert kill_calls[0][2] == r["container"]
            assert box.timeouts == 1


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestDockerUnavailable:
    def test_run_command_raises_when_docker_missing(self):
        m = DockerBackend()
        with (
            patch(
                "runtime.sensing.server.docker.shutil.which",
                return_value=None,
            ),
            m.sandbox(arm_id="t") as box,
            pytest.raises(DockerUnavailableError),
        ):
            box.run_command(["echo", "hi"])

    def test_require_available_probes(self):
        m = DockerBackend()
        with (
            patch(
                "runtime.sensing.server.docker.shutil.which",
                return_value=None,
            ),
            pytest.raises(DockerUnavailableError),
        ):
            m.require_available()


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


@pytest.mark.skipif(
    not _DOCKER_AVAILABLE,
    reason="docker not available",
)
class TestLive:
    def test_hello_world_stdout(self):
        # Implementation note.
        m = DockerBackend(
            image="alpine:3.19",
            timeout_seconds=30.0,
            network_mode="none",
        )
        with m.sandbox(arm_id="live") as box:
            r = box.run_command(["echo", "hello"])
        assert r["exit_code"] == 0
        assert "hello" in r["stdout"]
        assert r["timed_out"] is False


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestSessionStats:
    def test_run_count_tracked(self, captured_argv):
        m = DockerBackend()
        with m.sandbox(arm_id="t") as box:
            box.run_command(["a"])
            box.run_command(["b"])
            box.run_command(["c"])
            assert box.run_command_count == 3
