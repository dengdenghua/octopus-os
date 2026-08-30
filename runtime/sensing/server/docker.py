from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any, Literal

from runtime.adapters.instrumentation import trace_stage

from .local import BackendAudit, LocalBackend, Sandbox

NetworkMode = Literal["none", "bridge", "host"]

_OUTPUT_CAP_BYTES = 200_000
_DEFAULT_IMAGE = "python:3.11-slim"


class DockerUnavailableError(RuntimeError):
    pass


def _docker_available() -> bool:
    exe = shutil.which("docker")
    if exe is None:
        return False
    try:
        r = subprocess.run(
            [exe, "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            timeout=5.0,
            text=True,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return r.returncode == 0 and bool(r.stdout.strip())


class DockerBackend(LocalBackend):
    def __init__(
        self,
        *,
        image: str = _DEFAULT_IMAGE,
        timeout_seconds: float = 30.0,
        network_mode: NetworkMode = "none",
        memory_mb: int | None = None,
        cpus: float | None = None,
        read_only: bool = False,
        volumes: list[tuple[str | Path, str, str]] | None = None,
        env: dict[str, str] | None = None,
        extra_args: list[str] | None = None,
        allowed_read_roots: list[Path] | None = None,
        allowed_write_roots: list[Path] | None = None,
    ) -> None:
        super().__init__(allowed_read_roots, allowed_write_roots)
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        if memory_mb is not None and memory_mb <= 0:
            raise ValueError("memory_mb must be > 0")
        if cpus is not None and cpus <= 0:
            raise ValueError("cpus must be > 0")
        for v in volumes or []:
            if len(v) != 3 or v[2] not in ("ro", "rw"):
                raise ValueError(f"volumes items must be (host, container, 'ro'|'rw'), got {v!r}")
        self.image = image
        self.timeout_seconds = timeout_seconds
        self.network_mode: NetworkMode = network_mode
        self.memory_mb = memory_mb
        self.cpus = cpus
        self.read_only = read_only
        self.volumes = list(volumes or [])
        self.env = dict(env or {})
        self.extra_args = list(extra_args or [])

    def require_available(self) -> None:
        if not _docker_available():
            raise DockerUnavailableError(
                "docker CLI unavailable (not on PATH or daemon unreachable)"
            )

    @contextmanager
    def sandbox(self, arm_id: str) -> Iterator[DockerSandbox]:
        with trace_stage("backend.docker.sandbox", arm_id=arm_id) as span:
            span.set_attribute("echo.backend.image", self.image)
            span.set_attribute("echo.backend.network_mode", self.network_mode)
            audit = BackendAudit(
                arm_id=arm_id,
                allowed_paths=[str(p) for p in self.allowed_read_roots],
            )
            box = DockerSandbox(backend=self, audit=audit, span=span)
            try:
                yield box
            finally:
                span.set_attribute(
                    "echo.backend.docker_runs",
                    box.run_command_count,
                )
                span.set_attribute(
                    "echo.backend.timed_out_count",
                    box.timeouts,
                )


class DockerSandbox(Sandbox):
    def __init__(
        self,
        backend: DockerBackend,
        audit: BackendAudit,
        span: Any,
    ) -> None:
        super().__init__(backend=backend, audit=audit, span=span)
        self.backend: DockerBackend = backend  # type: ignore[assignment]
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

        exe = shutil.which("docker")
        if exe is None:
            raise DockerUnavailableError("docker not on PATH")

        self.run_command_count += 1
        timeout = timeout_seconds or self.backend.timeout_seconds
        container_name = f"echo-{uuid.uuid4().hex[:12]}"

        docker_argv = self._build_docker_argv(container_name, cwd, argv)

        with trace_stage(
            "backend.docker.run_command",
            container=container_name,
        ) as span:
            span.set_attribute("echo.backend.argv_len", len(argv))
            from ._streaming import stream_run

            result = stream_run(
                [exe, *docker_argv],
                input_data=input_data,
                timeout=timeout,
                output_cap_bytes=_OUTPUT_CAP_BYTES,
                on_timeout=lambda _p: self._best_effort_kill(container_name),
            )
            if "error" in result and "exit_code" not in result:
                return {"error": f"docker_exec_failed: {result['error']}"}
            if result.get("timed_out"):
                self.timeouts += 1
                span.set_attribute("echo.backend.timed_out", True)
                return {
                    "stdout": result["stdout"],
                    "stderr": result["stderr"],
                    "exit_code": None,
                    "timed_out": True,
                    "container": container_name,
                    "timeout_seconds": timeout,
                }
            span.set_attribute("echo.backend.exit_code", result["exit_code"])
            return {
                **result,
                "container": container_name,
            }

    def _build_docker_argv(
        self,
        name: str,
        cwd: str | None,
        inner_argv: list[str],
    ) -> list[str]:
        m = self.backend
        args: list[str] = ["run", "--rm", "-i", "--name", name]

        args += ["--network", m.network_mode]
        if m.read_only:
            args.append("--read-only")
        if m.memory_mb is not None:
            args += ["-m", f"{m.memory_mb}m"]
        if m.cpus is not None:
            args += ["--cpus", str(m.cpus)]

        for host, container, mode in m.volumes:
            host_resolved = str(Path(host).resolve()) if os.path.exists(host) else str(host)
            args += ["-v", f"{host_resolved}:{container}:{mode}"]

        for k, v in m.env.items():
            args += ["-e", f"{k}={v}"]

        if cwd:
            args += ["-w", cwd]

        args += list(m.extra_args)

        args += [m.image, *inner_argv]
        return args

    @staticmethod
    def _best_effort_kill(name: str) -> None:
        exe = shutil.which("docker")
        if exe is None:
            return
        with suppress(subprocess.TimeoutExpired, OSError):
            subprocess.run(
                [exe, "kill", name],
                capture_output=True,
                text=True,
                timeout=5.0,
                check=False,
            )
