from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextlib import suppress as _suppress
from pathlib import Path
from typing import Any

from runtime.adapters.instrumentation import trace_stage

from .local import BackendAudit, LocalBackend, Sandbox

_SUBPROCESS_ENTRY = """
import json, os, sys, traceback
from pathlib import Path

def _resolve_roots(raw):
    roots = []
    for item in raw or []:
        try:
            roots.append(Path(item).resolve())
        except Exception:  # noqa: BLE001 — sandbox path filter; an unresolvable path is dropped silently by design
            pass
    return roots

def _within(path, roots):
    try:
        resolved = Path(path).resolve()
    except Exception:  # noqa: BLE001 — best-effort; fail-open
        return False
    for root in roots:
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False

def _is_write_open(mode, flags):
    if isinstance(mode, str) and any(ch in mode for ch in ("w", "a", "x", "+")):
        return True
    if isinstance(flags, int):
        write_flags = (
            getattr(os, "O_WRONLY", 0)
            | getattr(os, "O_RDWR", 0)
            | getattr(os, "O_CREAT", 0)
            | getattr(os, "O_TRUNC", 0)
            | getattr(os, "O_APPEND", 0)
        )
        return bool(flags & write_flags)
    return False

def _install_path_audit(read_roots_raw, write_roots_raw):
    read_roots = _resolve_roots(read_roots_raw)
    write_roots = _resolve_roots(write_roots_raw)
    if not read_roots and not write_roots:
        return
    trusted_code_roots = []
    try:
        import runtime
        trusted_code_roots.append(Path(runtime.__file__).resolve().parent)
    except Exception:  # noqa: BLE001 — runtime might not be importable inside the sandbox; trusted-roots stays empty by design
        pass
    for entry in sys.path:
        if not entry:
            continue
        try:
            p = Path(entry).resolve()
        except Exception:  # noqa: BLE001 — best-effort; fail-open
            continue
        text = str(p)
        if text.startswith(sys.base_prefix) or "site-packages" in text:
            trusted_code_roots.append(p)
    readable = tuple(read_roots + write_roots + trusted_code_roots)
    writable = tuple(write_roots)

    def hook(event, args):
        if event != "open":
            return
        path = args[0] if args else None
        if not isinstance(path, (str, bytes, os.PathLike)):
            return
        mode = args[1] if len(args) > 1 else "r"
        flags = args[2] if len(args) > 2 else 0
        if _is_write_open(mode, flags):
            if not writable or not _within(path, writable):
                raise PermissionError(f"write outside allowed roots: {path}")
            return
        if readable and not _within(path, readable):
            raise PermissionError(f"read outside allowed roots: {path}")

    sys.addaudithook(hook)

try:
    from runtime.execution.suckers import SkillRegistry
    from runtime.execution.suckers.builtins import register_all
    payload = json.load(sys.stdin)
    reg = SkillRegistry()
    register_all(reg)
    skill = reg.get(payload["skill_name"])
    _install_path_audit(
        payload.get("allowed_read_roots"),
        payload.get("allowed_write_roots"),
    )
    result = skill.handler(**payload["args"])
    sys.stdout.write(json.dumps(result, default=str))
except Exception as e:  # noqa: BLE001 — sandbox entry; error reported to stderr
    sys.stderr.write(f"{type(e).__name__}: {e}\\n{traceback.format_exc()}")
    sys.exit(3)
"""


_OUTPUT_CAP_BYTES = 200_000


class SubprocessBackend(LocalBackend):
    def __init__(
        self,
        *,
        timeout_seconds: float = 30.0,
        max_memory_mb: int | None = None,
        max_cpu_seconds: int | None = None,
        allowed_read_roots: list[Path] | None = None,
        allowed_write_roots: list[Path] | None = None,
    ) -> None:
        super().__init__(allowed_read_roots, allowed_write_roots)
        self.timeout_seconds = timeout_seconds
        self.max_memory_mb = max_memory_mb
        self.max_cpu_seconds = max_cpu_seconds

    @contextmanager
    def sandbox(self, arm_id: str) -> Iterator[SubprocessSandbox]:
        with trace_stage("backend.subprocess.sandbox", arm_id=arm_id) as span:
            audit = BackendAudit(
                arm_id=arm_id,
                allowed_paths=[str(p) for p in self.allowed_read_roots],
            )
            box = SubprocessSandbox(backend=self, audit=audit, span=span)
            try:
                yield box
            finally:
                span.set_attribute("echo.backend.subprocess_calls", box.subprocess_call_count)
                span.set_attribute("echo.backend.timed_out_count", box.timeouts)


class SubprocessSandbox(Sandbox):
    def __init__(self, backend: SubprocessBackend, audit: BackendAudit, span: Any) -> None:
        super().__init__(backend=backend, audit=audit, span=span)
        self.backend: SubprocessBackend = backend  # type: ignore[assignment]
        self.subprocess_call_count = 0
        self.timeouts = 0

    def run_skill(self, skill_name: str, args: dict[str, Any]) -> dict[str, Any]:
        self.subprocess_call_count += 1
        with trace_stage(
            "backend.subprocess.run_skill",
            **{"echo.skill.name": skill_name},
        ) as span:
            payload = json.dumps(
                {
                    "skill_name": skill_name,
                    "args": args,
                    "allowed_read_roots": [str(p) for p in self.backend.allowed_read_roots],
                    "allowed_write_roots": [str(p) for p in self.backend.allowed_write_roots],
                },
            )
            preexec = self._build_preexec()

            try:
                proc = subprocess.run(
                    [sys.executable, "-c", _SUBPROCESS_ENTRY],
                    input=payload,
                    capture_output=True,
                    text=True,
                    timeout=self.backend.timeout_seconds,
                    preexec_fn=preexec,
                )
            except subprocess.TimeoutExpired:
                self.timeouts += 1
                span.set_attribute("echo.backend.timed_out", True)
                return {
                    "error": f"subprocess timeout after {self.backend.timeout_seconds}s",
                    "timed_out": True,
                    "skill_name": skill_name,
                }

            span.set_attribute("echo.backend.exit_code", proc.returncode)

            if proc.returncode != 0:
                stderr = proc.stderr
                return {
                    "error": f"subprocess exit {proc.returncode}",
                    "stderr": stderr[:_OUTPUT_CAP_BYTES],
                    "stderr_truncated": len(stderr) > _OUTPUT_CAP_BYTES,
                    "exit_code": proc.returncode,
                    "skill_name": skill_name,
                }

            try:
                return json.loads(proc.stdout)
            except json.JSONDecodeError:
                stdout = proc.stdout
                return {
                    "error": "invalid json output",
                    "stdout": stdout[:_OUTPUT_CAP_BYTES],
                    "stdout_truncated": len(stdout) > _OUTPUT_CAP_BYTES,
                    "skill_name": skill_name,
                }

    def _build_preexec(self) -> Callable[[], None] | None:
        if sys.platform == "win32":
            return None
        mem = self.backend.max_memory_mb
        cpu = self.backend.max_cpu_seconds
        if not mem and not cpu:
            return None

        def _apply() -> None:
            import resource  # Implementation note.

            if mem is not None:
                byte_cap = mem * 1024 * 1024
                with _suppress(ValueError, OSError):
                    # macOS does not support RLIMIT_AS; skip silently.
                    resource.setrlimit(resource.RLIMIT_AS, (byte_cap, byte_cap))
            if cpu is not None:
                resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))

        return _apply
