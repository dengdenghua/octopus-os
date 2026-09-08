"""Codex transport adapter over the shared Echo tool authority."""

from typing import Any

from runtime.execution.tool_engine.host_tool_broker import (
    DYNAMIC_TOOL_CALL_METHOD,
    HostToolBroker,
    dynamic_tool_failure,
    validate_dynamic_tool_response,
)
from runtime.execution.tool_engine.host_tool_broker import (
    HostToolCatalog as CodexDynamicToolCatalog,
)

MAX_DYNAMIC_TOOLS = 128


class CodexDynamicToolBroker(HostToolBroker):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, execution_engine="codex", max_tools=MAX_DYNAMIC_TOOLS, **kwargs)


__all__ = [
    "CodexDynamicToolBroker",
    "CodexDynamicToolCatalog",
    "DYNAMIC_TOOL_CALL_METHOD",
    "dynamic_tool_failure",
    "validate_dynamic_tool_response",
]
