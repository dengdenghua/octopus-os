from __future__ import annotations

import shutil
import subprocess
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

from runtime.adapters.instrumentation import trace_stage

from .local import BackendAudit, LocalBackend, Sandbox

_OUTPUT_CAP_BYTES = 200_000
_DEFAULT_IMAGE = "python:3.11-slim"


class K8sUnavailableError(RuntimeError):
    pass


def _kubectl_available(kubectl_bin: str = "kubectl") -> bool:
    exe = shutil.which(kubectl_bin)
    if exe is None:
        return False
    try:
        r = subprocess.run(
            [exe, "version", "--client=true", "-o", "yaml"],
            capture_output=True,
            timeout=5.0,
            text=True,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return r.returncode == 0


class K8sBackend(LocalBackend):
    def __init__(
        self,
        *,
        image: str = _DEFAULT_IMAGE,
        namespace: str = "default",
        timeout_seconds: float = 60.0,
        kubectl_bin: str = "kubectl",
        kubeconfig: str | Path | None = None,
        context: str | None = None,
        service_account: str | None = None,
        cpu_request: str | None = None,  # e.g. "100m"
        memory_request: str | None = None,  # e.g. "256Mi"
        cpu_limit: str | None = None,
        memory_limit: str | None = None,
        node_selector: dict[str, str] | None = None,
        env: dict[str, str] | None = None,
        labels: dict[str, str] | None = None,
        pod_name_prefix: str = "echo-",
        extra_args: list[str] | None = None,
        allowed_read_roots: list[Path] | None = None,
        allowed_write_roots: list[Path] | None = None,
    ) -> None:
        super().__init__(allowed_read_roots, allowed_write_roots)
        if not image:
            raise ValueError("image required")
        if not namespace:
            raise ValueError("namespace required")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        self.image = image
        self.namespace = namespace
        self.timeout_seconds = timeout_seconds
        self.kubectl_bin = kubectl_bin
        self.kubeconfig = Path(kubeconfig) if kubeconfig else None
        self.context = context
        self.service_account = service_account
        self.cpu_request = cpu_request
        self.memory_request = memory_request
        self.cpu_limit = cpu_limit
        self.memory_limit = memory_limit
        self.node_selector = dict(node_selector or {})
        self.env = dict(env or {})
        self.labels = dict(labels or {})
        self.pod_name_prefix = pod_name_prefix
        self.extra_args = list(extra_args or [])

    def require_available(self) -> None:
        if not _kubectl_available(self.kubectl_bin):
            raise K8sUnavailableError(
                f"{self.kubectl_bin} not on PATH or cluster unreachable",
            )

    @contextmanager
    def sandbox(self, arm_id: str) -> Iterator[K8sSandbox]:
        with trace_stage("backend.k8s.sandbox", arm_id=arm_id) as span:
            span.set_attribute("echo.backend.k8s.image", self.image)
            span.set_attribute("echo.backend.k8s.namespace", self.namespace)
            audit = BackendAudit(
                arm_id=arm_id,
                allowed_paths=[str(p) for p in self.allowed_read_roots],
            )
            box = K8sSandbox(backend=self, audit=audit, span=span)
            try:
                yield box
            finally:
                span.set_attribute("echo.backend.k8s.runs", box.run_command_count)
                span.set_attribute("echo.backend.timed_out_count", box.timeouts)


class K8sSandbox(Sandbox):
    def __init__(self, backend: K8sBackend, audit: BackendAudit, span: Any) -> None:
        super().__init__(backend=backend, audit=audit, span=span)
        self.backend: K8sBackend = backend  # type: ignore[assignment]
        self.run_command_count = 0
        self.timeouts = 0

    def run_command(
        self,
        argv: list[str],
        *,
        input_data: str | None = None,
        cwd: str | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        if not isinstance(argv, list) or not argv:
            return {"error": "argv must be a non-empty list"}
        for a in argv:
            if not isinstance(a, str):
                return {"error": f"argv element not str: {a!r}"}

        exe = shutil.which(self.backend.kubectl_bin)
        if exe is None:
            raise K8sUnavailableError(f"{self.backend.kubectl_bin} not on PATH")

        self.run_command_count += 1
        timeout = timeout_seconds or self.backend.timeout_seconds
        pod_name = f"{self.backend.pod_name_prefix}{uuid.uuid4().hex[:12]}"

        kubectl_argv = self._build_kubectl_argv(pod_name, cwd, argv)

        with trace_stage("backend.k8s.run_command", pod=pod_name) as span:
            span.set_attribute("echo.backend.argv_len", len(argv))
            from ._streaming import stream_run

            result = stream_run(
                [exe, *kubectl_argv],
                input_data=input_data,
                timeout=timeout,
                output_cap_bytes=_OUTPUT_CAP_BYTES,
                on_timeout=lambda _p: self._best_effort_delete(pod_name),
            )
            if "error" in result and "exit_code" not in result:
                return {"error": f"kubectl_exec_failed: {result['error']}"}
            if result.get("timed_out"):
                self.timeouts += 1
                span.set_attribute("echo.backend.timed_out", True)
                return {
                    "stdout": result["stdout"],
                    "stderr": result["stderr"],
                    "exit_code": None,
                    "timed_out": True,
                    "pod": pod_name,
                    "timeout_seconds": timeout,
                }
            span.set_attribute("echo.backend.exit_code", result["exit_code"])
            return {**result, "pod": pod_name}

    def _build_kubectl_argv(
        self,
        pod_name: str,
        cwd: str | None,
        inner_argv: list[str],
    ) -> list[str]:
        m = self.backend
        args: list[str] = []

        if m.kubeconfig is not None:
            args += ["--kubeconfig", str(m.kubeconfig)]
        if m.context is not None:
            args += ["--context", m.context]
        args += ["--namespace", m.namespace]

        args += ["run", pod_name]
        args += ["--rm", "-i", "--restart=Never"]
        args += ["--image", m.image]
        args += ["--attach=true"]
        args += ["--quiet=true"]

        overrides = self._build_pod_overrides()
        if overrides:
            args += ["--overrides", overrides]

        for k, v in m.env.items():
            args += ["--env", f"{k}={v}"]

        # labels
        for k, v in m.labels.items():
            args += ["--labels", f"{k}={v}"]

        args += list(m.extra_args)

        args.append("--")
        if cwd is not None:
            cwd_q = _sh_quote(cwd)
            cmd_q = " ".join(_sh_quote(a) for a in inner_argv)
            args += ["sh", "-c", f"cd {cwd_q} && exec {cmd_q}"]
        else:
            args += inner_argv
        return args

    def _build_pod_overrides(self) -> str:
        m = self.backend
        container: dict[str, Any] = {"name": "main"}
        resources: dict[str, dict[str, str]] = {}
        if m.cpu_request or m.memory_request:
            resources["requests"] = {}
            if m.cpu_request:
                resources["requests"]["cpu"] = m.cpu_request
            if m.memory_request:
                resources["requests"]["memory"] = m.memory_request
        if m.cpu_limit or m.memory_limit:
            resources["limits"] = {}
            if m.cpu_limit:
                resources["limits"]["cpu"] = m.cpu_limit
            if m.memory_limit:
                resources["limits"]["memory"] = m.memory_limit
        if resources:
            container["resources"] = resources

        spec: dict[str, Any] = {}
        if resources:
            spec["containers"] = [container]
        if m.service_account:
            spec["serviceAccountName"] = m.service_account
        if m.node_selector:
            spec["nodeSelector"] = m.node_selector

        if not spec:
            return ""

        import json

        overrides = {"apiVersion": "v1", "spec": spec}
        return json.dumps(overrides)

    def _best_effort_delete(self, pod_name: str) -> None:
        exe = shutil.which(self.backend.kubectl_bin)
        if exe is None:
            return
        args = [exe, "--namespace", self.backend.namespace]
        if self.backend.kubeconfig is not None:
            args += ["--kubeconfig", str(self.backend.kubeconfig)]
        if self.backend.context is not None:
            args += ["--context", self.backend.context]
        args += ["delete", "pod", pod_name, "--force", "--grace-period=0"]
        with suppress(subprocess.TimeoutExpired, OSError):
            subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=10.0,
                check=False,
            )


def _sh_quote(s: str) -> str:
    if not s:
        return "''"
    if all(c.isalnum() or c in "@%+=:,./-_" for c in s):
        return s
    return "'" + s.replace("'", "'\\''") + "'"
