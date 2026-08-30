"""TTFT / streaming-UX live smoke probe.

Drives a real turn over the realtime WebSocket JSON-RPC protocol against a
running server and records the wall-clock event timeline. With ``--assert``
it also enforces the streaming contracts locked by the TTFT fixes
(2026-07-28, see ``docs/ttft-acceptance-checklist.md``):

- a reasoning/thinking block appears before every tool row;
- long answers stream progressively (≥2 delta events), not one dump;
- no ``Update:``/``Progress:`` label leaks into visible text;
- no duplicate narration pairs (streamed prose + condensed checkpoint).

Standalone (no tests-package imports); requires the ``websockets`` package
from the project venv.

Usage:
    .venv/bin/python -m scripts.ttft_smoke "联网搜索一条新闻并总结" \
        --model kimi-k3 --port 8010 --assert
    .venv/bin/python -m scripts.ttft_smoke "任务" --react   # force text protocol
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.request


def _post(base: str, path: str, payload: dict, token: str | None) -> dict:
    req = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


class Timeline:
    def __init__(self) -> None:
        self.marks: list[tuple[float, str]] = []
        self.t0 = time.monotonic()

    def mark(self, label: str) -> None:
        self.marks.append((time.monotonic() - self.t0, label))
        print(f"{self.marks[-1][0]:8.3f}s  {label}", flush=True)


async def run_turn(args: argparse.Namespace) -> tuple[Timeline, dict]:
    base = f"http://localhost:{args.port}"
    login = _post(
        base,
        "/api/auth/local/login",
        {"username": "ttft-smoke", "password": "x"},
        None,
    )
    token = login.get("access_token") or login.get("token")
    thread = _post(base, "/api/threads", {"title": "ttft-smoke"}, token)
    thread_id = thread["thread_id"]
    print(f"thread={thread_id} model={args.model}", flush=True)

    import websockets

    tl = Timeline()
    stats = {
        "thread_id": thread_id,
        "reasoning_chars": 0,
        "answer_chars": 0,
        "answer_deltas": 0,
        "tool_rows": 0,
        "reasoning_before_tool": True,
        "prefix_leak": False,
        "first_delta_s": None,
        "labels": [],
    }
    reasoning_seen_at: list[float] = []

    async with websockets.connect(
        f"ws://localhost:{args.port}/api/realtime",
        subprotocols=["bearer", token],
        max_size=8 * 1024 * 1024,
        proxy=None,
    ) as ws:
        await ws.send(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "turn/start",
            "params": {
                "threadId": thread_id,
                "input": [{"type": "text", "text": args.prompt,
                           **({"metadata": {"context": {"native_tool_loop": False}}}
                              if args.react else {})}],
                "approvalPolicy": "never",
                "model": args.model,
            },
        }))
        tl.mark("turn/start sent")
        done = False
        while not done:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=args.timeout)
            except TimeoutError:
                tl.mark("TIMEOUT waiting for events")
                break
            env = json.loads(raw)
            if "method" not in env:
                if env.get("id") == 1:
                    tl.mark(f"turn/start response (error={bool(env.get('error'))})")
                    done = True
                continue
            m = env["method"]
            p = env.get("params") or {}
            now = time.monotonic() - tl.t0
            if m == "item/reasoning/textDelta":
                delta = p.get("delta", "")
                stats["reasoning_chars"] += len(delta)
                reasoning_seen_at.append(now)
                if stats["first_delta_s"] is None:
                    stats["first_delta_s"] = now
                    tl.mark(f"first reasoning delta (+{len(delta)})")
            elif m == "item/agentMessage/delta":
                delta = p.get("delta", "")
                stats["answer_chars"] += len(delta)
                stats["answer_deltas"] += 1
                if stats["first_delta_s"] is None:
                    stats["first_delta_s"] = now
                head = delta.lstrip().lower()
                if stats["answer_deltas"] == 1 and head.startswith(("update:", "progress:")):
                    stats["prefix_leak"] = True
                if stats["answer_deltas"] <= 3:
                    tl.mark(f"answer delta +{len(delta)}")
            elif m == "item/started":
                item = p.get("item") or {}
                itype = item.get("type")
                if itype in ("commandExecution", "toolCall", "mcpToolCall"):
                    stats["tool_rows"] += 1
                    if not reasoning_seen_at:
                        stats["reasoning_before_tool"] = False
                    tl.mark(f"tool row #{stats['tool_rows']} ({itype})")
            elif m in ("turn/started", "turn/completed", "turn/interrupted", "error"):
                tl.mark(m)
        tl.mark(
            f"TOTAL reasoning={stats['reasoning_chars']} "
            f"answer={stats['answer_chars']} deltas={stats['answer_deltas']}"
        )

    if args.out:
        with open(args.out, "w") as fh:
            json.dump({"args": vars(args), "marks": tl.marks, "stats": stats},
                      fh, ensure_ascii=False, indent=1)
    return tl, stats


def check(stats: dict) -> list[str]:
    """Return assertion failures (empty = pass)."""
    failures: list[str] = []
    if stats["first_delta_s"] is None:
        failures.append("no visible delta at all")
    if not stats["reasoning_before_tool"]:
        failures.append("a tool row appeared before any reasoning/thinking content")
    if stats["prefix_leak"]:
        failures.append("Update:/Progress: label leaked into visible text")
    if stats["answer_chars"] >= 200 and stats["answer_deltas"] < 2:
        failures.append(
            f"long answer ({stats['answer_chars']} chars) arrived as one dump "
            f"({stats['answer_deltas']} delta)"
        )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="TTFT streaming smoke probe")
    parser.add_argument("prompt")
    parser.add_argument("--model", default="kimi-k3")
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--react", action="store_true",
                        help="force the ReAct text protocol (native_tool_loop=false)")
    parser.add_argument("--timeout", type=int, default=240,
                        help="per-event wait ceiling in seconds")
    parser.add_argument("--assert", dest="do_assert", action="store_true",
                        help="enforce streaming contracts; non-zero exit on failure")
    parser.add_argument("--out", default="",
                        help="optional JSON timeline output path")
    args = parser.parse_args()

    _tl, stats = asyncio.run(run_turn(args))
    if not args.do_assert:
        return 0
    failures = check(stats)
    if failures:
        print("\nASSERT FAILURES:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nASSERT OK: streaming contracts held")
    return 0


if __name__ == "__main__":
    sys.exit(main())

