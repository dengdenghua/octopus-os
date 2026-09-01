"""Compatibility re-export for the lightweight process output sink."""

from runtime.platform.process.tool_output_sink import (
    ToolOutputSink,
    current_sink,
    emit,
    push_sink,
)

__all__ = ["ToolOutputSink", "current_sink", "emit", "push_sink"]
