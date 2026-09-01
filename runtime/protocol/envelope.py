"""JSON-RPC 2.0 envelope.

Three message kinds:
  * Request  — has ``id``, ``method``, ``params``. Sender awaits a Response.
  * Response — has ``id``, exactly one of ``result`` / ``error``.
  * Notification — has ``method`` and ``params`` but no ``id``. Fire-and-forget.

Both directions of the connection use the same envelope. Server-initiated
Requests are how approvals and elicitations work over a single channel.
"""

from __future__ import annotations

import json
from enum import IntEnum
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

JsonRpcId: TypeAlias = Annotated[int | str, Field(description="JSON-RPC 2.0 request id")]


class JsonRpcErrorCode(IntEnum):
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603
    # Application range. -32000 .. -32099 is reserved by JSON-RPC 2.0
    # for implementation-defined server errors; that's where our own
    # codes live so we never collide with the reserved ones below.
    APPROVAL_DENIED = -32000
    APPROVAL_TIMEOUT = -32001
    THREAD_NOT_FOUND = -32010
    TURN_NOT_ACTIVE = -32011
    UNAUTHORIZED = -32020
    SERVER_BUSY = -32030


class JsonRpcError(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: int
    message: str
    data: Any | None = None


class JsonRpcRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    jsonrpc: Literal["2.0"] = "2.0"
    id: JsonRpcId
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


class JsonRpcResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    jsonrpc: Literal["2.0"] = "2.0"
    id: JsonRpcId
    result: Any | None = None
    error: JsonRpcError | None = None

    @model_validator(mode="after")
    def _exactly_one_outcome(self) -> JsonRpcResponse:
        if (self.result is None) == (self.error is None):
            raise ValueError("response must carry exactly one of result/error")
        return self


class Notification(BaseModel):
    """A request without ``id``. No reply expected."""

    model_config = ConfigDict(populate_by_name=True)

    jsonrpc: Literal["2.0"] = "2.0"
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


Message: TypeAlias = JsonRpcRequest | JsonRpcResponse | Notification


def encode_message(message: Message) -> str:
    """Serialize a message to a single-line JSON string.

    The trailing newline is the responsibility of the framer (stdio uses
    ndjson; WebSocket sends one frame per call).
    """
    return message.model_dump_json(exclude_none=True)


def decode_message(payload: str | bytes) -> Message:
    """Parse a JSON-RPC envelope, dispatching on shape.

    Raises ``ValueError`` for malformed payloads — the caller is expected
    to translate that into a ``PARSE_ERROR`` response when appropriate.
    """
    try:
        raw: Any = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("envelope must be a JSON object")
    if "method" in raw and "id" in raw:
        return _try(JsonRpcRequest, raw)
    if "method" in raw:
        return _try(Notification, raw)
    if "id" in raw and ("result" in raw or "error" in raw):
        return _try(JsonRpcResponse, raw)
    raise ValueError("envelope is neither request, response, nor notification")


def _try(model: type[BaseModel], raw: dict[str, Any]) -> Any:
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc
