"""Last-resort frame bounding for realtime WebSocket notifications."""

from __future__ import annotations

import copy
import logging
from typing import Any

from runtime.protocol import JsonRpcRequest, JsonRpcResponse, Notification, encode_message

_logger = logging.getLogger(__name__)
_FRAME_BYTE_LIMIT = 12 * 1024 * 1024
_FRAME_CHAR_FASTPASS = _FRAME_BYTE_LIMIT // 4
_FRAME_TRUNC_MARK = "…(字段过大已截断以保住连接)"
_FRAME_TRUNCATED_KEY = "_frameTruncated"


def _iter_string_leaves(obj: Any) -> list[tuple[Any, Any, int]]:
    out: list[tuple[Any, Any, int]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, str):
                    out.append((node, key, len(value)))
                else:
                    walk(value)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                if isinstance(value, str):
                    out.append((node, index, len(value)))
                else:
                    walk(value)

    walk(obj)
    return out


def _bound_oversized_frame(
    message: JsonRpcRequest | JsonRpcResponse | Notification,
) -> JsonRpcRequest | JsonRpcResponse | Notification:
    """Shrink the largest string leaves while preserving frame structure."""

    params = getattr(message, "params", None)
    if not isinstance(params, dict):
        return message
    params = copy.deepcopy(params)
    truncated = False
    for _ in range(80):
        leaves = _iter_string_leaves(params)
        if not leaves:
            break
        container, key, longest = max(leaves, key=lambda leaf: leaf[2])
        if longest <= len(_FRAME_TRUNC_MARK) + 1024:
            break
        value = container[key]
        container[key] = value[: max(1024, len(value) // 2)] + _FRAME_TRUNC_MARK
        truncated = True
        trimmed = message.model_copy(update={"params": params})
        if len(encode_message(trimmed).encode("utf-8")) <= _FRAME_BYTE_LIMIT:
            params[_FRAME_TRUNCATED_KEY] = True
            trimmed = message.model_copy(update={"params": params})
            _logger.warning(
                "realtime: frame for %s exceeded %d bytes — truncated its "
                "largest field to protect the connection",
                getattr(message, "method", "?"),
                _FRAME_BYTE_LIMIT,
            )
            return trimmed
    if truncated:
        params[_FRAME_TRUNCATED_KEY] = True
    return message.model_copy(update={"params": params})


__all__ = [
    "_FRAME_BYTE_LIMIT",
    "_FRAME_CHAR_FASTPASS",
    "_FRAME_TRUNCATED_KEY",
    "_FRAME_TRUNC_MARK",
    "_bound_oversized_frame",
]
