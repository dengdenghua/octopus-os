"""Implementation note."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from runtime.sensing.server.k8s import (
    K8sBackend,
    K8sSandbox,
    K8sUnavailableError,
)

# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestK8sMantleValidation:
    def test_defaults(self):
        m = K8sBackend()
        assert m.image == "python:3.11-slim"
        assert m.namespace == "default"
        assert m.timeout_seconds == 60.0

    def test_requires_image(self):
        with pytest.raises(ValueError, match="image"):
            K8sBackend(image="")

    def test_requires_namespace(self):
        with pytest.raises(ValueError, match="namespace"):
            K8sBackend(namespace="")

    def test_rejects_zero_timeout(self):
        with pytest.raises(ValueError, match="timeout"):
            K8sBackend(timeout_seconds=0)


# ═══════════════════════════════════════════════════════════
# require_available
# ═══════════════════════════════════════════════════════════


class TestRequireAvailable:
    def test_missing_kubectl(self):
        m = K8sBackend()
        with (
            patch(
                "runtime.sensing.server.k8s.shutil.which",
                return_value=None,
            ),
            pytest.raises(K8sUnavailableError),
        ):
            m.require_available()

    def test_kubectl_version_fails(self):
        m = K8sBackend()
        fake = MagicMock(returncode=1, stdout="", stderr="err")
        with (
            patch("runtime.sensing.server.k8s.shutil.which", return_value="/usr/bin/kubectl"),
            patch(
                "runtime.sensing.server.k8s.subprocess.run",
                return_value=fake,
            ),
            pytest.raises(K8sUnavailableError),
        ):
            m.require_available()

    def test_kubectl_ok(self):
        m = K8sBackend()
        fake = MagicMock(returncode=0, stdout="clientVersion:", stderr="")
        with (
            patch("runtime.sensing.server.k8s.shutil.which", return_value="/usr/bin/kubectl"),
            patch(
                "runtime.sensing.server.k8s.subprocess.run",
                return_value=fake,
            ),
        ):
            m.require_available()


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestBuildKubectlArgv:
    def _box(self, **kw) -> K8sSandbox:
        m = K8sBackend(**kw)
        return K8sSandbox(backend=m, audit=MagicMock(), span=MagicMock())

    def test_basic(self):
        box = self._box(image="python:3.11-slim", namespace="test-ns")
        argv = box._build_kubectl_argv("pod-abc", None, ["echo", "hi"])
        assert "run" in argv
        assert "pod-abc" in argv
        assert "--rm" in argv
        assert "--restart=Never" in argv
        assert "--image" in argv
        assert "python:3.11-slim" in argv
        assert "--namespace" in argv
        assert "test-ns" in argv

    def test_inner_argv_after_double_dash(self):
        box = self._box()
        argv = box._build_kubectl_argv("p", None, ["python3", "-c", "print(1)"])
        idx = argv.index("--")
        assert argv[idx + 1 :] == ["python3", "-c", "print(1)"]

    def test_kubeconfig_and_context(self):
        box = self._box(kubeconfig="/tmp/kc", context="prod")
        argv = box._build_kubectl_argv("p", None, ["echo"])
        assert "--kubeconfig" in argv
        assert "--context" in argv
        assert "prod" in argv

    def test_env_added(self):
        box = self._box(env={"FOO": "1"})
        argv = box._build_kubectl_argv("p", None, ["echo"])
        # Implementation note.
        pairs = [argv[i + 1] for i, a in enumerate(argv) if a == "--env"]
        assert "FOO=1" in pairs

    def test_labels_added(self):
        box = self._box(labels={"app": "echo"})
        argv = box._build_kubectl_argv("p", None, ["echo"])
        pairs = [argv[i + 1] for i, a in enumerate(argv) if a == "--labels"]
        assert "app=echo" in pairs

    def test_cwd_wraps_with_sh_c(self):
        box = self._box()
        argv = box._build_kubectl_argv("p", "/tmp", ["echo", "hi"])
        idx = argv.index("--")
        tail = argv[idx + 1 :]
        assert tail[0] == "sh"
        assert tail[1] == "-c"
        assert "cd /tmp" in tail[2]
        assert "exec echo hi" in tail[2]


# ═══════════════════════════════════════════════════════════
# pod overrides JSON
# ═══════════════════════════════════════════════════════════


class TestPodOverrides:
    def _box(self, **kw) -> K8sSandbox:
        m = K8sBackend(**kw)
        return K8sSandbox(backend=m, audit=MagicMock(), span=MagicMock())

    def test_no_overrides_empty(self):
        box = self._box()
        assert box._build_pod_overrides() == ""

    def test_resource_requests(self):
        box = self._box(cpu_request="100m", memory_request="256Mi")
        ovr = box._build_pod_overrides()
        data = json.loads(ovr)
        assert data["apiVersion"] == "v1"
        ctr = data["spec"]["containers"][0]
        assert ctr["resources"]["requests"]["cpu"] == "100m"
        assert ctr["resources"]["requests"]["memory"] == "256Mi"

    def test_resource_limits(self):
        box = self._box(cpu_limit="500m", memory_limit="1Gi")
        ovr = box._build_pod_overrides()
        data = json.loads(ovr)
        ctr = data["spec"]["containers"][0]
        assert ctr["resources"]["limits"]["cpu"] == "500m"
        assert ctr["resources"]["limits"]["memory"] == "1Gi"

    def test_service_account(self):
        box = self._box(service_account="echo-sa")
        ovr = box._build_pod_overrides()
        assert json.loads(ovr)["spec"]["serviceAccountName"] == "echo-sa"

    def test_node_selector(self):
        box = self._box(node_selector={"gpu": "true"})
        ovr = box._build_pod_overrides()
        assert json.loads(ovr)["spec"]["nodeSelector"] == {"gpu": "true"}

    def test_overrides_injected_into_argv(self):
        box = self._box(cpu_request="100m")
        argv = box._build_kubectl_argv("p", None, ["echo"])
        assert "--overrides" in argv


# ═══════════════════════════════════════════════════════════
# run_command · subprocess mock
# ═══════════════════════════════════════════════════════════


class TestRunCommand:
    def test_rejects_empty_argv(self):
        m = K8sBackend()
        with m.sandbox("test") as box:
            r = box.run_command([])
            assert "error" in r

    def test_successful_run(self):
        m = K8sBackend()
        stream_result = {
            "stdout": "hello\n",
            "stderr": "",
            "exit_code": 0,
            "timed_out": False,
            "stdout_truncated": False,
            "stderr_truncated": False,
        }
        with (
            patch("runtime.sensing.server.k8s.shutil.which", return_value="/usr/bin/kubectl"),
            patch(
                "runtime.sensing.server._streaming.stream_run",
                return_value=stream_result,
            ),
            m.sandbox("test") as box,
        ):
            r = box.run_command(["echo", "hello"])
        assert r["exit_code"] == 0
        assert r["stdout"] == "hello\n"
        assert r["timed_out"] is False
        assert r["pod"].startswith("echo-")

    def test_timeout_triggers_cleanup(self):
        m = K8sBackend(timeout_seconds=0.5)
        delete_calls: list = []

        def fake_stream(argv, *, on_timeout=None, **kwargs):
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

        def fake_subprocess_run(cmd, **kwargs):
            if "delete" in cmd:
                delete_calls.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")

        with (
            patch("runtime.sensing.server.k8s.shutil.which", return_value="/usr/bin/kubectl"),
            patch(
                "runtime.sensing.server._streaming.stream_run",
                side_effect=fake_stream,
            ),
            patch(
                "runtime.sensing.server.k8s.subprocess.run",
                side_effect=fake_subprocess_run,
            ),
            m.sandbox("test") as box,
        ):
            r = box.run_command(["sleep", "10"])
        assert r["timed_out"] is True
        assert box.timeouts == 1
        assert any("delete" in c for c in delete_calls)

    def test_missing_kubectl_raises(self):
        m = K8sBackend()
        with (
            patch(
                "runtime.sensing.server.k8s.shutil.which",
                return_value=None,
            ),
            m.sandbox("test") as box,
            pytest.raises(K8sUnavailableError),
        ):
            box.run_command(["echo"])

    def test_output_truncation(self):
        m = K8sBackend()
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
            patch("runtime.sensing.server.k8s.shutil.which", return_value="/usr/bin/kubectl"),
            patch(
                "runtime.sensing.server._streaming.stream_run",
                return_value=stream_result,
            ),
            m.sandbox("test") as box,
        ):
            r = box.run_command(["cat"])
        assert len(r["stdout"]) == 200_000
        assert r["stdout_truncated"] is True
