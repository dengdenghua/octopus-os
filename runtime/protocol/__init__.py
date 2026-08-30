"""Realtime agent protocol — JSON-RPC 2.0 envelope + item-oriented state model.

This package owns the wire contract between any client (web UI, Electron,
CLI, remote daemon) and the runtime. It is transport-agnostic: the same
envelopes ride over WebSocket, stdio, or named pipes.

Design rules:
  * Notifications carry token-level deltas; requests demand a reply.
  * Server-initiated requests (e.g. command approval) ride the same channel.
  * State is a flat ``items[]`` collection per turn, addressable by
    ``itemId``. Lifecycle is ``item/started``, ``item/<subtype>/delta``,
    ``item/completed``.
"""

from __future__ import annotations

from .envelope import (
    JsonRpcError,
    JsonRpcErrorCode,
    JsonRpcRequest,
    JsonRpcResponse,
    Notification,
    decode_message,
    encode_message,
)
from .events import ClientMethod, ServerMethod
from .items import (
    AgentMessageItem,
    AgentPhaseSnapshot,
    ApprovalItem,
    ArtifactItem,
    CommandExecutionItem,
    ErrorItem,
    EvidenceReference,
    FileChange,
    FileChangeItem,
    FileHunk,
    GroundingSource,
    Item,
    ItemMarker,
    ItemStatus,
    ItemType,
    McpToolCallItem,
    McpToolProgress,
    PlanItem,
    ReasoningItem,
    SteeringUserMessageItem,
    SubagentItem,
    TodoEntry,
    TodoListItem,
    ToolEffectSignal,
    Turn,
    TurnParams,
    TurnStatus,
    UserMessageItem,
    VerificationItem,
    VisibilityItem,
    WorkbenchSnapshotV2,
    WorkspaceFocus,
)

__all__ = [
    "AgentPhaseSnapshot",
    "AgentMessageItem",
    "ApprovalItem",
    "ArtifactItem",
    "ClientMethod",
    "CommandExecutionItem",
    "ErrorItem",
    "EvidenceReference",
    "FileChange",
    "FileChangeItem",
    "FileHunk",
    "GroundingSource",
    "Item",
    "ItemMarker",
    "ItemStatus",
    "ItemType",
    "JsonRpcError",
    "JsonRpcErrorCode",
    "JsonRpcRequest",
    "JsonRpcResponse",
    "McpToolCallItem",
    "McpToolProgress",
    "Notification",
    "PlanItem",
    "ReasoningItem",
    "ServerMethod",
    "SteeringUserMessageItem",
    "SubagentItem",
    "ToolEffectSignal",
    "TodoEntry",
    "TodoListItem",
    "Turn",
    "TurnParams",
    "TurnStatus",
    "UserMessageItem",
    "VerificationItem",
    "VisibilityItem",
    "WorkbenchSnapshotV2",
    "WorkspaceFocus",
    "decode_message",
    "encode_message",
]
