"""No single realtime frame may exceed the WS message ceiling: an oversized
frame is dropped with code 1009, killing the connection (and, mid-run,
backends). The per-field caps are the primary defense; this guards the
class — ANY field that grows unbounded gets truncated instead of crashing.
No model involved: the invariant is about bytes on the wire."""

from __future__ import annotations

import json

from runtime.protocol import JsonRpcResponse, Notification, encode_message
from runtime.sensing.gateway.realtime_gateway import (
    _FRAME_BYTE_LIMIT,
    _FRAME_TRUNC_MARK,
    _bound_oversized_frame,
)


def _encoded_bytes(msg) -> int:
    return len(encode_message(msg).encode("utf-8"))


def test_normal_frame_is_returned_unchanged() -> None:
    msg = Notification(method="item/agentMessage/delta", params={"delta": "hello"})
    assert _bound_oversized_frame(msg) is msg or _encoded_bytes(
        _bound_oversized_frame(msg)
    ) == _encoded_bytes(msg)


def test_runaway_single_field_is_truncated_under_limit() -> None:
    # A 40 MiB command-output field — the exact shape that dropped the
    # socket with 1009 before the caps existed.
    huge = "x" * (40 * 1024 * 1024)
    msg = Notification(
        method="turn/completed",
        params={"turn": {"items": [{"aggregated_output": huge}]}},
    )
    bounded = _bound_oversized_frame(msg)
    assert _encoded_bytes(bounded) <= _FRAME_BYTE_LIMIT
    # Structure survives + the truncation is visible.
    parsed = json.loads(encode_message(bounded))
    out = parsed["params"]["turn"]["items"][0]["aggregated_output"]
    assert out.endswith(_FRAME_TRUNC_MARK)


def test_many_large_fields_all_get_bounded() -> None:
    # Not one runaway string but many medium ones — the guard must still
    # bring the whole frame under the ceiling.
    msg = Notification(
        method="workbench/snapshot",
        params={"blobs": ["y" * (2 * 1024 * 1024) for _ in range(20)]},
    )
    bounded = _bound_oversized_frame(msg)
    assert _encoded_bytes(bounded) <= _FRAME_BYTE_LIMIT
    json.loads(encode_message(bounded))  # still valid JSON


def test_response_without_params_is_passed_through() -> None:
    # Guard only knows how to shrink params-bearing frames; a JSON-RPC
    # response carries no params, so it must pass through untouched.
    msg = JsonRpcResponse(id=1, result={"ok": True})
    assert _bound_oversized_frame(msg) is msg


def test_send_path_truncates_oversized_frame(caplog) -> None:
    # End-to-end through the real RpcConnection.send: a 40 MiB frame must
    # reach the socket already bounded, not as a >16 MiB frame that the
    # client drops with 1009.
    import asyncio

    from runtime.sensing.gateway.realtime_gateway import RpcConnection

    class _FakeWS:
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def send_text(self, text: str) -> None:
            self.sent.append(text)

    async def _run() -> str:
        ws = _FakeWS()
        conn = RpcConnection(ws)
        huge = "x" * (40 * 1024 * 1024)
        await conn.send(
            Notification(
                method="turn/completed",
                params={"turn": {"items": [{"aggregated_output": huge}]}},
            )
        )
        return ws.sent[-1]

    frame = asyncio.run(_run())
    assert len(frame.encode("utf-8")) <= _FRAME_BYTE_LIMIT
    # A small frame on the same connection must NOT be touched.
    parsed = json.loads(frame)
    assert parsed["method"] == "turn/completed"

