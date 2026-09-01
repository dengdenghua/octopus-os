"""Standalone demo of the realtime gateway.

Runs an in-process FastAPI app, connects a test WebSocket, drives one
turn end-to-end, and prints the notification stream. Useful as:

  * A sanity check after editing protocol / gateway / echo runtime.
  * An example for anyone wiring a new RealtimeRuntime into the
    gateway.

Invoke::

    python -m demos.realtime_echo

Prints a compact log of every server→client event, then the final turn
snapshot. No external services, no network.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.protocol import (
    JsonRpcRequest,
    JsonRpcResponse,
    Notification,
    decode_message,
    encode_message,
)
from runtime.sensing.gateway.realtime_echo import EchoRuntime
from runtime.sensing.gateway.realtime_gateway import RealtimeGateway


def run() -> None:
    logs_root = Path("data/threads_demo")
    runtime = EchoRuntime(logs_root=logs_root)
    gateway = RealtimeGateway(runtime=runtime)

    app = FastAPI()
    app.include_router(gateway.router)

    with TestClient(app) as client, client.websocket_connect("/api/realtime") as ws:
        ws.send_text(
            encode_message(
                JsonRpcRequest(
                    id=1,
                    method="turn/start",
                    params={
                        "threadId": "demo-thread",
                        "input": [{"type": "text", "text": "hello from demo"}],
                        "approvalPolicy": "on-request",
                    },
                )
            )
        )

        while True:
            msg = decode_message(ws.receive_text())
            if isinstance(msg, Notification):
                print(f"◀ notify  {msg.method:<38} {_compact(msg.params)}")
                continue
            if isinstance(msg, JsonRpcRequest):
                print(f"◀ request {msg.method:<38} id={msg.id}")
                # Auto-approve in the demo.
                ws.send_text(
                    encode_message(
                        JsonRpcResponse(id=msg.id, result={"action": "accept"})
                    )
                )
                continue
            if isinstance(msg, JsonRpcResponse) and msg.id == 1:
                print("◀ response turn/start id=1 — turn completed")
                print()
                print("Final snapshot:")
                print(json.dumps(msg.result, indent=2, ensure_ascii=False))
                break

    print()
    print(f"Persisted to {logs_root / 'demo-thread.jsonl'}")


def _compact(payload: dict[str, object]) -> str:
    summary = {k: v for k, v in payload.items() if k not in {"item", "turn", "thread"}}
    s = json.dumps(summary, ensure_ascii=False)
    return s if len(s) < 96 else s[:93] + "..."


if __name__ == "__main__":
    run()

