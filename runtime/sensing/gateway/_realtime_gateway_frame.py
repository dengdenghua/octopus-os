"""Last-ditch frame-size guard so no single WS frame exceeds the ceiling.

Split from ``realtime_gateway.py``. A single WS frame over the client's
~16 MiB message ceiling is dropped with code 1009, which kills the whole
connection (and has taken backends down mid-run). The per-field caps
upstream (e.g. command output) are the primary defense; this is the
last-ditch net for ANY field that grows unbounded — a huge diff, a huge
snapshot.
"""

from __future__ import annotations

import logging
from typing import Any

from runtime.protocol import JsonRpcRequest, JsonRpcResponse, Notification, encode_message

_logger = logging.getLogger(__name__)

# Bound to 12 MiB, leaving margin for protocol overhead. The trigger is an
# O(1) char-count so normal frames pay nothing; only the rare oversized
# frame does the precise byte work.
_FRAME_BYTE_LIMIT = 12 * 1024 * 1024
# A JSON char is at most 4 UTF-8 bytes, so under this many chars a frame is
# guaranteed under the byte limit and can skip the encode-and-measure path.
_FRAME_CHAR_FASTPASS = _FRAME_BYTE_LIMIT // 4
_FRAME_TRUNC_MARK = "…(字段过大已截断以保住连接)"
_FRAME_TRUNCATED_KEY = "_frameTruncated"

# Inbound anti-abuse ceiling, mirroring the team-rooms WS handler
# (``team_rooms_ws.py``). The outbound guard above bounds what WE emit;
# this bounds what a client can push at us. A single oversized frame is
# dropped before parsing (``decode_message`` never sees it), and a
# per-connection rate limiter sheds sustained floods. Lenient — a legit
# JSON-RPC frame is a few KB — so only a runaway or hostile client trips
# them. Set the gateway constructor args to 0 to disable.
_INBOUND_FRAME_BYTE_LIMIT = 64 * 1024
_INBOUND_MSG_PER_SEC = 30


def _iter_string_leaves(obj: Any) -> list[tuple[Any, Any, int]]:
    """Every (container, key, length) for string leaves, so the largest can
    be found and shortened in place."""
    out: list[tuple[Any, Any, int]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, str):
                    out.append((node, k, len(v)))
                else:
                    walk(v)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                if isinstance(v, str):
                    out.append((node, i, len(v)))
                else:
                    walk(v)

    walk(obj)
    return out


def _inject_trunc_metadata(params: dict[str, Any]) -> None:
    """Mark the frame as truncated at the params level so the truncation
    notice lives in metadata, not inside user-visible content strings."""
    params[_FRAME_TRUNCATED_KEY] = True


def _bound_oversized_frame(
    message: JsonRpcRequest | JsonRpcResponse | Notification,
) -> JsonRpcRequest | JsonRpcResponse | Notification:
    """Return a copy whose serialized size is under ``_FRAME_BYTE_LIMIT``,
    halving the single longest string leaf until it fits. Structure is
    preserved (only string leaves shrink), so the JSON stays valid."""
    params = getattr(message, "params", None)
    if not isinstance(params, dict):
        return message  # responses/errors carry no bulk field to shrink
    import copy

    params = copy.deepcopy(params)
    truncated = False
    for _ in range(80):  # bounded; each pass halves the biggest string
        leaves = _iter_string_leaves(params)
        if not leaves:
            break
        container, key, longest = max(leaves, key=lambda x: x[2])
        if longest <= len(_FRAME_TRUNC_MARK) + 1024:
            break  # nothing left big enough to help
        s = container[key]
        container[key] = s[: max(1024, len(s) // 2)] + _FRAME_TRUNC_MARK
        truncated = True
        trimmed = message.model_copy(update={"params": params})
        if len(encode_message(trimmed).encode("utf-8")) <= _FRAME_BYTE_LIMIT:
            if truncated:
                _inject_trunc_metadata(params)
                trimmed = message.model_copy(update={"params": params})
            _logger.warning(
                "realtime: frame for %s exceeded %d bytes — truncated its "
                "largest field to protect the connection",
                getattr(message, "method", "?"),
                _FRAME_BYTE_LIMIT,
            )
            return trimmed
    if truncated:
        _inject_trunc_metadata(params)
    return message.model_copy(update={"params": params})
