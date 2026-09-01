"""Container sandbox — run tool commands in isolated Docker containers.

Provides a `ContainerSandbox` that wraps Docker exec for tool execution.
When enabled, `exec_shell` and write tools run inside a per-thread
container instead of the host process. If Docker is unavailable the
sandbox refuses to execute rather than falling back to the host.

MVP: single shared image, per-thread container, volume-mounted workspace.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

_logger = logging.getLogger(__name__)

DEFAULT_IMAGE = "python:3.12-slim"
CONTAINER_PREFIX = "echo-sandbox-"


@dataclass
class SandboxConfig:
    enabled: bool = False
    image: str = DEFAULT_IMAGE
    memory_limit: str = "512m"
    cpu_limit: float = 1.0
    network: str = "none"
    timeout_seconds: int = 60
    read_only_root: bool = True


@dataclass
class ContainerSandbox:
    thread_id: str
    workspace_path: str
    config: SandboxConfig = field(default_factory=SandboxConfig)
    _container_id: str | None = None

    @property
    def container_name(self) -> str:
        safe_id = hashlib.sha256(self.thread_id.encode("utf-8")).hexdigest()[:16]
        return f"{CONTAINER_PREFIX}{safe_id}"

    @property
    def is_docker_available(self) -> bool:
        return shutil.which("docker") is not None

    def ensure_running(self) -> bool:
        if not self.config.enabled or not self.is_docker_available:
            return False

        if self._container_id:
            try:
                result = subprocess.run(
                    ["docker", "inspect", "-f", "{{.State.Running}}", self.container_name],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0 and "true" in result.stdout.lower():
                    return True
            except (OSError, subprocess.SubprocessError):  # noqa: BLE001 — docker probe best-effort
                pass

        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                ["docker", "rm", "-f", self.container_name],
                capture_output=True,
                timeout=10,
            )

        workspace = Path(self.workspace_path).resolve()
        cmd = [
            "docker",
            "run",
            "-d",
            "--name",
            self.container_name,
            "--memory",
            self.config.memory_limit,
            f"--cpus={self.config.cpu_limit}",
            f"--network={self.config.network}",
            "-v",
            f"{workspace}:/workspace",
            "-w",
            "/workspace",
        ]
        if self.config.read_only_root:
            cmd.append("--read-only")
            cmd.extend(["--tmpfs", "/tmp:rw,noexec,nosuid,size=64m"])  # nosec B108 — docker tmpfs mount spec, not a temp file

        cmd.extend([self.config.image, "sleep", "infinity"])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                self._container_id = result.stdout.strip()[:12]
                _logger.info("sandbox container started: %s", self.container_name)
                return True
            _logger.warning("failed to start sandbox: %s", result.stderr)
        except Exception as exc:
            _logger.warning("docker run failed: %s", exc)

        return False

    def exec(self, command: str, timeout: int | None = None) -> dict[str, Any]:
        effective_timeout = timeout or self.config.timeout_seconds

        if not self._container_id and not self.ensure_running():
            _logger.error(
                "sandbox unavailable for thread %s — refusing to execute on host",
                self.thread_id,
            )
            return {
                "stdout": "",
                "stderr": (
                    "sandbox unavailable: command rejected (host execution disabled for safety)"
                ),
                "exit_code": -1,
                "sandboxed": False,
            }

        try:
            result = subprocess.run(
                ["docker", "exec", self.container_name, "sh", "-c", command],
                capture_output=True,
                text=True,
                timeout=effective_timeout,
            )
            return {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.returncode,
                "sandboxed": True,
            }
        except subprocess.TimeoutExpired:
            return {
                "stdout": "",
                "stderr": f"command timed out after {effective_timeout}s",
                "exit_code": -1,
                "sandboxed": True,
            }
        except (OSError, subprocess.SubprocessError) as exc:
            _logger.error("docker exec failed, refusing host fallback: %s", exc)
            return {
                "stdout": "",
                "stderr": (f"sandbox exec failed: {exc} (host execution disabled for safety)"),
                "exit_code": -1,
                "sandboxed": False,
            }

    def write_file(self, rel_path: str, content: str) -> dict[str, Any]:
        if not self._container_id and not self.ensure_running():
            return {"error": "sandbox not available", "sandboxed": False}

        if not rel_path.strip():
            return {
                "error": "invalid sandbox path: empty path",
                "sandboxed": True,
                "exit_code": -1,
            }

        rel = PurePosixPath(rel_path)
        if (
            rel.parts == ()
            or rel.is_absolute()
            or any(part in {"", ".", ".."} for part in rel.parts)
        ):
            return {
                "error": f"invalid sandbox path: {rel_path!r}",
                "sandboxed": True,
                "exit_code": -1,
            }

        script = (
            "from pathlib import Path\n"
            "import sys\n"
            "rel = sys.argv[1]\n"
            "target = (Path('/workspace') / rel).resolve()\n"
            "root = Path('/workspace').resolve()\n"
            "if root not in target.parents and target != root:\n"
            "    raise SystemExit('path escapes /workspace')\n"
            "target.parent.mkdir(parents=True, exist_ok=True)\n"
            "target.write_text(sys.stdin.read(), encoding='utf-8')\n"
        )

        try:
            result = subprocess.run(
                [
                    "docker",
                    "exec",
                    "-i",
                    self.container_name,
                    "python",
                    "-c",
                    script,
                    str(rel),
                ],
                input=content,
                capture_output=True,
                text=True,
                timeout=self.config.timeout_seconds,
            )
            return {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.returncode,
                "sandboxed": True,
            }
        except subprocess.TimeoutExpired:
            return {
                "stdout": "",
                "stderr": f"write timed out after {self.config.timeout_seconds}s",
                "exit_code": -1,
                "sandboxed": True,
            }
        except (OSError, subprocess.SubprocessError) as exc:
            _logger.error("docker write_file failed, refusing host fallback: %s", exc)
            return {
                "stdout": "",
                "stderr": (f"sandbox write failed: {exc} (host execution disabled for safety)"),
                "exit_code": -1,
                "sandboxed": False,
            }

    def cleanup(self) -> None:
        if not self.is_docker_available:
            return
        try:
            subprocess.run(
                ["docker", "rm", "-f", self.container_name],
                capture_output=True,
                timeout=10,
            )
            _logger.info("sandbox container removed: %s", self.container_name)
        except (OSError, subprocess.SubprocessError):  # noqa: BLE001 — container teardown best-effort
            pass
        self._container_id = None


_sandboxes: dict[str, ContainerSandbox] = {}


def get_sandbox(
    thread_id: str,
    workspace_path: str,
    config: SandboxConfig | None = None,
) -> ContainerSandbox:
    key = f"{thread_id}:{workspace_path}"
    if key not in _sandboxes:
        _sandboxes[key] = ContainerSandbox(
            thread_id=thread_id,
            workspace_path=workspace_path,
            config=config or SandboxConfig(),
        )
    return _sandboxes[key]


def cleanup_all() -> None:
    for sandbox in _sandboxes.values():
        sandbox.cleanup()
    _sandboxes.clear()
