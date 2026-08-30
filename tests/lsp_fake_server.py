"""A scripted stdio LSP server used to test the client without an install.

Run as ``python -m tests.lsp_fake_server <behaviour>``. Behaviours cover the
paths a real server makes expensive to reproduce: a wedged request, a
mid-session crash, an error response, and verbose stderr.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

# Framing is written out longhand rather than imported from the runtime: a
# fake server that shares the client's codec would agree with it about a
# malformed frame, and prove nothing.


def encode_message(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload).encode("utf-8")
    # Content-Length counts encoded bytes, not characters.
    return b"Content-Length: %d\r\n\r\n%s" % (len(body), body)


def read_message(stream: Any) -> dict[str, Any] | None:
    """Read one framed message from ``stream``, or None at clean EOF."""
    length = -1
    while True:
        line = stream.readline()
        if not line:
            return None
        stripped = line.strip()
        if not stripped:
            break
        name, _, value = stripped.decode("ascii", errors="replace").partition(":")
        if name.strip().lower() == "content-length":
            length = int(value.strip())
    if length < 0:
        return None
    chunks: list[bytes] = []
    remaining = length
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return json.loads(b"".join(chunks).decode("utf-8"))


def _reply(payload: dict[str, Any]) -> None:
    sys.stdout.buffer.write(encode_message(payload))
    sys.stdout.buffer.flush()


def main() -> int:
    behaviour = sys.argv[1] if len(sys.argv) > 1 else "normal"
    if behaviour == "noisy":
        for i in range(20):
            print(f"fake-lsp: warming up {i}", file=sys.stderr, flush=True)

    while True:
        try:
            message = read_message(sys.stdin.buffer)
        except Exception as exc:  # noqa: BLE001 - a test double; report and stop
            # Say why. A silent exit 1 here reads to the client as "the server
            # refused to start" and sends the reader hunting the wrong bug.
            print(f"fake-lsp: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            return 1
        if message is None:
            return 0
        method = message.get("method")
        msg_id = message.get("id")

        if method == "initialize":
            if behaviour == "no_initialize_reply":
                time.sleep(30)
                return 0
            _reply(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {"capabilities": {"definitionProvider": True}},
                }
            )
            continue
        if method == "initialized":
            continue
        if method == "shutdown":
            _reply({"jsonrpc": "2.0", "id": msg_id, "result": None})
            continue
        if method == "exit":
            return 0

        if method == "textDocument/didOpen":
            uri = message["params"]["textDocument"]["uri"]
            _reply(
                {
                    "jsonrpc": "2.0",
                    "method": "textDocument/publishDiagnostics",
                    "params": {
                        "uri": uri,
                        "diagnostics": [
                            {
                                "range": {
                                    "start": {"line": 2, "character": 4},
                                    "end": {"line": 2, "character": 9},
                                },
                                "severity": 1,
                                "message": "undefined name 'nope'",
                                "source": "fake",
                            }
                        ],
                    },
                }
            )
            continue
        if method in ("textDocument/didChange", "textDocument/didClose"):
            continue

        if method == "textDocument/definition":
            if behaviour == "hang":
                time.sleep(30)
                continue
            if behaviour == "crash":
                sys.exit(3)
            if behaviour == "error":
                _reply(
                    {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "error": {"code": -32603, "message": "internal indexer failure"},
                    }
                )
                continue
            uri = message["params"]["textDocument"]["uri"]
            _reply(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": [
                        {
                            "uri": uri,
                            "range": {
                                "start": {"line": 9, "character": 4},
                                "end": {"line": 9, "character": 11},
                            },
                        }
                    ],
                }
            )
            continue
        if method == "textDocument/hover":
            _reply(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {"contents": {"kind": "markdown", "value": "```python\nx: int\n```"}},
                }
            )
            continue
        if method == "textDocument/references":
            uri = message["params"]["textDocument"]["uri"]
            _reply(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": [
                        {
                            "uri": uri,
                            "range": {
                                "start": {"line": n, "character": 0},
                                "end": {"line": n, "character": 5},
                            },
                        }
                        for n in (3, 14)
                    ],
                }
            )
            continue
        if method == "workspace/symbol":
            query = (message.get("params") or {}).get("query", "")
            _reply(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": [
                        {
                            "name": f"{query}_handler",
                            "kind": 12,
                            "location": {
                                "uri": "file:///tmp/fake/mod.py",
                                "range": {
                                    "start": {"line": 41, "character": 0},
                                    "end": {"line": 41, "character": 20},
                                },
                            },
                        }
                    ],
                }
            )
            continue
        if method == "textDocument/documentSymbol":
            _reply(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": [
                        {
                            "name": "Widget",
                            "kind": 5,
                            "range": {
                                "start": {"line": 0, "character": 0},
                                "end": {"line": 20, "character": 0},
                            },
                            "selectionRange": {
                                "start": {"line": 0, "character": 6},
                                "end": {"line": 0, "character": 12},
                            },
                            "children": [
                                {
                                    "name": "render",
                                    "kind": 6,
                                    "range": {
                                        "start": {"line": 5, "character": 4},
                                        "end": {"line": 9, "character": 0},
                                    },
                                    "selectionRange": {
                                        "start": {"line": 5, "character": 8},
                                        "end": {"line": 5, "character": 14},
                                    },
                                }
                            ],
                        }
                    ],
                }
            )
            continue

        if msg_id is not None:
            _reply({"jsonrpc": "2.0", "id": msg_id, "result": None})


if __name__ == "__main__":
    raise SystemExit(main())

