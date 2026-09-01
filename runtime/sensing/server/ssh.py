from __future__ import annotations

import contextlib
import logging
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from runtime.adapters.instrumentation import trace_stage
from runtime.sensing.server._ssh_security import paramiko_disabled_algorithms

from .local import BackendAudit, LocalBackend, Sandbox

_logger = logging.getLogger(__name__)

_OUTPUT_CAP_BYTES = 200_000


class SshUnavailableError(RuntimeError):
    pass


class SshBackend(LocalBackend):
    def __init__(
        self,
        *,
        host: str,
        user: str | None = None,
        port: int = 22,
        identity_file: str | Path | None = None,
        password: str | None = None,  # Implementation note.
        timeout_seconds: float = 30.0,
        connect_timeout: int = 10,
        strict_host_key_checking: bool = True,
        known_hosts_file: str | Path | None = None,
        env: dict[str, str] | None = None,
        extra_ssh_args: list[str] | None = None,
        use_paramiko: bool = False,
        allowed_read_roots: list[Path] | None = None,
        allowed_write_roots: list[Path] | None = None,
    ) -> None:
        super().__init__(allowed_read_roots, allowed_write_roots)
        if not host:
            raise ValueError("host required")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        if port <= 0 or port > 65535:
            raise ValueError(f"port out of range: {port}")
        if password is not None and not use_paramiko:
            raise ValueError(
                "password auth requires use_paramiko=True "
                "(ssh CLI mode 不接受 password · 用 identity_file)",
            )
        self.host = host
        self.user = user
        self.port = port
        self.identity_file = Path(identity_file) if identity_file else None
        self.password = password
        self.timeout_seconds = timeout_seconds
        self.connect_timeout = connect_timeout
        self.strict_host_key_checking = strict_host_key_checking
        self.known_hosts_file = Path(known_hosts_file) if known_hosts_file else None
        self.env = dict(env or {})
        self.extra_ssh_args = list(extra_ssh_args or [])
        self.use_paramiko = use_paramiko

    def require_available(self) -> None:
        if self.use_paramiko:
            try:
                import paramiko  # noqa: F401
            except ImportError as e:
                raise SshUnavailableError(
                    "paramiko not installed · pip install paramiko 或 "
                    "改用 CLI 模式（use_paramiko=False）",
                ) from e
        else:
            if shutil.which("ssh") is None:
                raise SshUnavailableError(
                    "ssh CLI not on PATH · 装 OpenSSH 客户端或 use_paramiko=True",
                )

    @contextmanager
    def sandbox(self, arm_id: str) -> Iterator[SshSandbox]:
        with trace_stage("backend.ssh.sandbox", arm_id=arm_id) as span:
            span.set_attribute("echo.backend.ssh.host", self.host)
            span.set_attribute(
                "echo.backend.ssh.backend",
                "paramiko" if self.use_paramiko else "cli",
            )
            audit = BackendAudit(
                arm_id=arm_id,
                allowed_paths=[str(p) for p in self.allowed_read_roots],
            )
            box = SshSandbox(backend=self, audit=audit, span=span)
            try:
                yield box
            finally:
                span.set_attribute("echo.backend.ssh.runs", box.run_command_count)
                span.set_attribute("echo.backend.timed_out_count", box.timeouts)
                box.close()


class SshSandbox(Sandbox):
    def __init__(self, backend: SshBackend, audit: BackendAudit, span: Any) -> None:
        super().__init__(backend=backend, audit=audit, span=span)
        self.backend: SshBackend = backend  # type: ignore[assignment]
        self.run_command_count = 0
        self.timeouts = 0
        self._paramiko_client: Any = None  # Implementation note.

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

        self.run_command_count += 1
        timeout = timeout_seconds or self.backend.timeout_seconds

        if self.backend.use_paramiko:
            return self._run_paramiko(argv, input_data, cwd, timeout)
        return self._run_cli(argv, input_data, cwd, timeout)

    def _run_cli(
        self,
        argv: list[str],
        input_data: str | None,
        cwd: str | None,
        timeout: float,
    ) -> dict[str, Any]:
        exe = shutil.which("ssh")
        if exe is None:
            raise SshUnavailableError("ssh CLI not on PATH")

        ssh_argv = self._build_ssh_argv(argv, cwd)

        with trace_stage("backend.ssh.run_command", host=self.backend.host) as span:
            span.set_attribute("echo.backend.argv_len", len(argv))
            from ._streaming import stream_run

            result = stream_run(
                [exe, *ssh_argv],
                input_data=input_data,
                timeout=timeout,
                output_cap_bytes=_OUTPUT_CAP_BYTES,
            )
            if "error" in result and "exit_code" not in result:
                return {"error": f"ssh_exec_failed: {result['error']}"}
            if result.get("timed_out"):
                self.timeouts += 1
                span.set_attribute("echo.backend.timed_out", True)
                return {
                    "stdout": result["stdout"],
                    "stderr": result["stderr"],
                    "exit_code": None,
                    "timed_out": True,
                    "host": self.backend.host,
                    "timeout_seconds": timeout,
                }
            span.set_attribute("echo.backend.exit_code", result["exit_code"])
            return {**result, "host": self.backend.host}

    def _build_ssh_argv(self, inner_argv: list[str], cwd: str | None) -> list[str]:
        m = self.backend
        args: list[str] = []
        args += ["-p", str(m.port)]
        args += ["-o", f"ConnectTimeout={m.connect_timeout}"]
        args += ["-o", "ServerAliveInterval=15"]
        args += ["-o", "ServerAliveCountMax=3"]
        args += ["-o", "BatchMode=yes"]  # Implementation note.
        if not m.strict_host_key_checking:
            # Opt-in MITM window · operators that disable strict checking
            # should know they lose host-key pinning. The paired
            # ``UserKnownHostsFile=/dev/null`` ensures no lingering key
            # entry points at a spoofed host on next connect.
            _logger.warning(
                "ssh_backend %s:%d · strict_host_key_checking is DISABLED "
                "(host key is not pinned; susceptible to MITM on first "
                "connection). Set strict_host_key_checking=True in the "
                "backend config to require a pre-populated known_hosts.",
                m.host,
                m.port,
            )
            args += ["-o", "StrictHostKeyChecking=no"]
            args += ["-o", "UserKnownHostsFile=/dev/null"]
        elif m.known_hosts_file is not None:
            args += ["-o", f"UserKnownHostsFile={m.known_hosts_file}"]
        if m.identity_file is not None:
            args += ["-i", str(m.identity_file)]
            args += ["-o", "IdentitiesOnly=yes"]
        args += list(m.extra_ssh_args)

        target = f"{m.user}@{m.host}" if m.user else m.host
        args.append(target)

        remote_cmd = self._compose_remote_command(inner_argv, cwd)
        args += ["--", *remote_cmd]
        return args

    def _compose_remote_command(
        self,
        argv: list[str],
        cwd: str | None,
    ) -> list[str]:
        m = self.backend
        if cwd is None and not m.env:
            return argv
        if cwd is None:
            env_prefix = ["env"]
            for k, v in m.env.items():
                env_prefix.append(f"{k}={v}")
            return [*env_prefix, *argv]

        cwd_quoted = _sh_quote(cwd)
        env_frag = " ".join(f"{_sh_quote(k)}={_sh_quote(v)}" for k, v in m.env.items())
        argv_frag = " ".join(_sh_quote(a) for a in argv)
        script = f"cd {cwd_quoted} && {'env ' + env_frag + ' ' if env_frag else ''}exec {argv_frag}"
        return ["sh", "-c", script]

    def _run_paramiko(
        self,
        argv: list[str],
        input_data: str | None,
        cwd: str | None,
        timeout: float,
    ) -> dict[str, Any]:
        try:
            import paramiko  # type: ignore[import-untyped]
        except ImportError as e:  # pragma: no cover - guarded in require_available
            raise SshUnavailableError("paramiko not installed") from e

        m = self.backend
        if self._paramiko_client is None:
            client = paramiko.SSHClient()
            if m.strict_host_key_checking:
                if m.known_hosts_file is not None:
                    client.load_host_keys(str(m.known_hosts_file))
                else:
                    client.load_system_host_keys()
                client.set_missing_host_key_policy(paramiko.RejectPolicy())
            else:
                # Opt-in MITM window · same rationale as the CLI
                # backend above. Load known_hosts when provided so at
                # least a pre-pinned host still fails loudly on
                # mismatch · only truly unknown hosts auto-add.
                _logger.warning(
                    "ssh_backend %s:%d (paramiko) · strict_host_key_checking is "
                    "DISABLED (host key not pinned; MITM possible on first "
                    "connection)",
                    m.host,
                    m.port,
                )
                if m.known_hosts_file is not None:
                    client.load_host_keys(str(m.known_hosts_file))
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())  # nosec B507 — reached only when the operator explicitly disabled strict host key checking; warning logged above
            connect_kwargs: dict[str, Any] = {
                "hostname": m.host,
                "port": m.port,
                "timeout": m.connect_timeout,
                "allow_agent": False,
                "look_for_keys": False,
                "disabled_algorithms": paramiko_disabled_algorithms(),
            }
            if m.user:
                connect_kwargs["username"] = m.user
            if m.identity_file:
                connect_kwargs["key_filename"] = str(m.identity_file)
            if m.password is not None:
                connect_kwargs["password"] = m.password
                connect_kwargs["allow_agent"] = False
            client.connect(**connect_kwargs)
            self._paramiko_client = client

        client = self._paramiko_client
        cmd_parts = [_sh_quote(a) for a in argv]
        if cwd is not None:
            cmd = f"cd {_sh_quote(cwd)} && {' '.join(cmd_parts)}"
        else:
            cmd = " ".join(cmd_parts)
        env_dict: dict[str, str] | None = dict(m.env) if m.env else None

        try:
            stdin, stdout, stderr = client.exec_command(  # nosec B601 — args quoted via _sh_quote before joining
                cmd,
                timeout=timeout,
                environment=env_dict,
            )
            if input_data is not None:
                stdin.write(input_data)
                stdin.channel.shutdown_write()

            # Stream stdout/stderr as they arrive via paramiko's file-like
            # wrappers. ``readline`` on these blocks until data is
            # available or the channel is closed, matching the local
            # subprocess path in _streaming.py.
            from runtime.platform.process import tool_output_sink

            sink = tool_output_sink.current_sink()
            out_parts: list[str] = []
            err_parts: list[str] = []

            def _drain(src: Any, parts: list[str], kind: str) -> None:
                try:
                    # paramiko's ChannelFile yields ``bytes`` by default
                    # (unlike local subprocess.Popen text-mode pipes).
                    # Empty bytes ``b""`` signals EOF.
                    while True:
                        raw = src.readline()
                        if not raw:
                            break
                        line = (
                            raw.decode("utf-8", errors="replace")
                            if isinstance(raw, (bytes, bytearray))
                            else raw
                        )
                        parts.append(line)
                        if sink is not None:
                            with contextlib.suppress(Exception):
                                sink(kind, line)  # type: ignore[arg-type]
                finally:
                    with contextlib.suppress(Exception):
                        src.close()

            import threading as _threading

            t_out = _threading.Thread(
                target=_drain,
                args=(stdout, out_parts, "stdout"),
                daemon=True,
            )
            t_err = _threading.Thread(
                target=_drain,
                args=(stderr, err_parts, "stderr"),
                daemon=True,
            )
            t_out.start()
            t_err.start()
            exit_code = stdout.channel.recv_exit_status()
            t_out.join(timeout=2.0)
            t_err.join(timeout=2.0)
            out = "".join(out_parts)
            err = "".join(err_parts)
        except Exception as e:  # noqa: BLE001
            name = type(e).__name__
            if "timeout" in name.lower():
                self.timeouts += 1
                return {
                    "stdout": "",
                    "stderr": "",
                    "exit_code": None,
                    "timed_out": True,
                    "host": m.host,
                    "timeout_seconds": timeout,
                }
            return {"error": f"ssh_exec_failed: {name}: {e}"}

        return {
            "stdout": out[:_OUTPUT_CAP_BYTES],
            "stderr": err[:_OUTPUT_CAP_BYTES],
            "exit_code": exit_code,
            "timed_out": False,
            "stdout_truncated": len(out) > _OUTPUT_CAP_BYTES,
            "stderr_truncated": len(err) > _OUTPUT_CAP_BYTES,
            "host": m.host,
        }

    def close(self) -> None:
        if self._paramiko_client is not None:
            with contextlib.suppress(Exception):
                self._paramiko_client.close()
            self._paramiko_client = None


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


def _sh_quote(s: str) -> str:
    if not s:
        return "''"
    if all(c.isalnum() or c in "@%+=:,./-_" for c in s):
        return s
    return "'" + s.replace("'", "'\\''") + "'"
