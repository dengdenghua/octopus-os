"""Shell execution telemetry events.

Structured metrics for shell command lifecycle, integrated with
echo-agent's OpenTelemetry tracing infrastructure.

Event types
~~~~~~~~~~~
- ShellExecInitSuccess / ShellExecInitFailed
- ShellExecSpawnSuccess / ShellExecSpawnFailed
- ShellExecStateSnapshotCapture
- ShellExecStateSnapshotRestore
- ShellExecSafeRmBlocked
- ShellExecOutputDropped
- ShellExecCommandExit
- ShellExecProcessTreeCleanup

Each event carries:
- metrics: numeric values (elapsed_ms, bytes, line_count, etc.)
- categories: string labels (shell_type, command_hash, etc.)
- extra: free-form context for debugging
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from runtime.adapters.instrumentation.tracing import (
    get_tracer,
)

_logger = logging.getLogger(__name__)


@dataclass
class ShellExecEvent:
    """A single structured telemetry event."""

    name: str
    metrics: dict[str, float | int] = field(default_factory=dict)
    categories: dict[str, str] = field(default_factory=dict)
    extra: dict[str, str] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_span_attributes(self) -> dict[str, Any]:
        """Convert event to OpenTelemetry span attributes."""
        attrs: dict[str, Any] = {
            "echo.shell.event_name": self.name,
            "echo.shell.timestamp": self.timestamp,
        }
        for k, v in self.metrics.items():
            attrs[f"echo.shell.metric.{k}"] = v
        for k, v in self.categories.items():
            attrs[f"echo.shell.category.{k}"] = v
        for k, v in self.extra.items():
            attrs[f"echo.shell.extra.{k}"] = v
        return attrs


class ShellExecTelemetry:
    """Manages shell execution telemetry events.

    Usage:
        telemetry = ShellExecTelemetry()
        with telemetry.measure_spawn("bash", "ls -la") as ctx:
            ...  # execute command
            ctx.set_success(elapsed_ms=123, output_bytes=456)
    """

    def __init__(self) -> None:
        self._tracer = get_tracer("shell_exec")
        self._event_count = 0

    def _hash_command(self, command: str) -> str:
        """Create a safe hash of the command for telemetry (no PII)."""
        return hashlib.sha256(command.encode()).hexdigest()[:12]

    def _emit(self, event: ShellExecEvent) -> None:
        """Emit a telemetry event via OpenTelemetry span."""
        self._event_count += 1
        try:
            with self._tracer.start_span(f"shell_exec.{event.name}") as span:
                for k, v in event.to_span_attributes().items():
                    span.set_attribute(k, v)
        except (TypeError, ValueError, AttributeError):
            _logger.debug("failed to emit shell exec event: %s", event.name)

    def record_init_success(
        self,
        shell_type: str,
        elapsed_ms: float,
        pid: int,
    ) -> None:
        self._emit(
            ShellExecEvent(
                name="ShellExecInitSuccess",
                metrics={"elapsed_ms": elapsed_ms},
                categories={
                    "shell_type": shell_type,
                    "pid": str(pid),
                },
            )
        )

    def record_init_failed(
        self,
        shell_type: str,
        elapsed_ms: float,
        error: str,
    ) -> None:
        self._emit(
            ShellExecEvent(
                name="ShellExecInitFailed",
                metrics={"elapsed_ms": elapsed_ms},
                categories={
                    "shell_type": shell_type,
                    "error_type": type(error).__name__,
                },
                extra={"error": str(error)[:500]},
            )
        )

    def record_spawn_success(
        self,
        shell_type: str,
        command: str,
        elapsed_ms: float,
        output_bytes: int,
        exit_code: int,
    ) -> None:
        self._emit(
            ShellExecEvent(
                name="ShellExecSpawnSuccess",
                metrics={
                    "elapsed_ms": elapsed_ms,
                    "output_bytes": output_bytes,
                    "exit_code": exit_code,
                },
                categories={
                    "shell_type": shell_type,
                    "command_hash": self._hash_command(command),
                },
            )
        )

    def record_spawn_failed(
        self,
        shell_type: str,
        command: str,
        elapsed_ms: float,
        error: str,
    ) -> None:
        self._emit(
            ShellExecEvent(
                name="ShellExecSpawnFailed",
                metrics={"elapsed_ms": elapsed_ms},
                categories={
                    "shell_type": shell_type,
                    "command_hash": self._hash_command(command),
                    "error_type": type(error).__name__,
                },
                extra={"error": str(error)[:500]},
            )
        )

    def record_state_snapshot_capture(
        self,
        shell_type: str,
        state_size_bytes: int,
        var_count: int,
    ) -> None:
        self._emit(
            ShellExecEvent(
                name="ShellExecStateSnapshotCapture",
                metrics={
                    "state_size_bytes": state_size_bytes,
                    "var_count": var_count,
                },
                categories={"shell_type": shell_type},
            )
        )

    def record_state_snapshot_restore(
        self,
        shell_type: str,
        elapsed_ms: float,
        restored: bool,
    ) -> None:
        self._emit(
            ShellExecEvent(
                name="ShellExecStateSnapshotRestore",
                metrics={"elapsed_ms": elapsed_ms},
                categories={
                    "shell_type": shell_type,
                    "restored": str(restored),
                },
            )
        )

    def record_safe_rm_blocked(
        self,
        shell_type: str,
        blocked_command: str,
    ) -> None:
        self._emit(
            ShellExecEvent(
                name="ShellExecSafeRmBlocked",
                categories={
                    "shell_type": shell_type,
                    "blocked_command": blocked_command[:100],
                },
            )
        )

    def record_output_dropped(
        self,
        shell_type: str,
        bytes_dropped: int,
        lines_dropped: int,
    ) -> None:
        self._emit(
            ShellExecEvent(
                name="ShellExecOutputDropped",
                metrics={
                    "bytes_dropped": bytes_dropped,
                    "lines_dropped": lines_dropped,
                },
                categories={"shell_type": shell_type},
            )
        )

    def record_process_tree_cleanup(
        self,
        shell_type: str,
        pid: int,
        elapsed_ms: float,
        success: bool,
    ) -> None:
        self._emit(
            ShellExecEvent(
                name="ShellExecProcessTreeCleanup",
                metrics={"elapsed_ms": elapsed_ms},
                categories={
                    "shell_type": shell_type,
                    "pid": str(pid),
                    "success": str(success),
                },
            )
        )

    @property
    def event_count(self) -> int:
        return self._event_count


_global_telemetry: ShellExecTelemetry | None = None


def get_shell_telemetry() -> ShellExecTelemetry:
    """Get or create the global shell telemetry instance."""
    global _global_telemetry
    if _global_telemetry is None:
        _global_telemetry = ShellExecTelemetry()
    return _global_telemetry
