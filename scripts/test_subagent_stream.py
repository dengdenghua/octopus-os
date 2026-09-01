#!/usr/bin/env python3
"""Subagent streaming test client.

Run this against a live echo backend to see SSE events flow in
real time. Useful for debugging the cowork pane and verifying
streaming infrastructure end-to-end.

Usage:
    python scripts/test_subagent_stream.py [role] [prompt]
    python scripts/test_subagent_stream.py researcher "Search for X"
    python scripts/test_subagent_stream.py reviewer "What is 2+2?"
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request


def main() -> int:
    role = sys.argv[1] if len(sys.argv) > 1 else "researcher"
    prompt = sys.argv[2] if len(sys.argv) > 2 else (
        "Find the latest Eight Sleep patent applications from 2025 and list publication numbers."
    )

    body = json.dumps({
        "subagent_type": role,
        "prompt": prompt,
        "timeout_s": 300,
    }).encode("utf-8")

    req = urllib.request.Request(
        "http://127.0.0.1:8000/api/subagents/dispatch/stream",
        data=body,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )

    print(f"→ POST /api/subagents/dispatch/stream  role={role}")
    print(f"  prompt: {prompt[:80]}...")
    print()

    started = time.time()
    text_chars = 0
    tool_count = 0

    try:
        with urllib.request.urlopen(req, timeout=400) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").rstrip()
                if not line:
                    continue
                if not line.startswith("data: "):
                    continue
                payload = line[len("data: "):]
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    print(f"  [parse-fail] {payload[:120]}")
                    continue

                etype = event.get("type", "?")
                elapsed = time.time() - started

                if etype == "subagent_spawned":
                    print(f"  [{elapsed:6.2f}s] 🐣 SPAWNED  "
                          f"role={event.get('role')} codename={event.get('codename')}")
                elif etype == "sub_text_delta":
                    chunk = event.get("delta", "")
                    text_chars += len(chunk)
                    # Print first 60 chars of each delta + tally
                    print(f"  [{elapsed:6.2f}s] 💬 TEXT     "
                          f"r{event.get('round',0)} +{len(chunk)}c "
                          f"(total {text_chars}c)")
                elif etype == "sub_tool_start":
                    tool_count += 1
                    skill = event.get("skill", "")
                    args = event.get("args_preview", "")[:60]
                    print(f"  [{elapsed:6.2f}s] 🔧 TOOL→    "
                          f"r{event.get('round',0)} {skill}({args})")
                elif etype == "sub_tool_end":
                    skill = event.get("skill", "")
                    status = event.get("status", "")
                    dur = event.get("duration_ms", 0)
                    print(f"  [{elapsed:6.2f}s] ✓ TOOL←    "
                          f"r{event.get('round',0)} {skill} {status} ({dur}ms)")
                elif etype == "subagent_finished":
                    ok = event.get("ok")
                    print(f"  [{elapsed:6.2f}s] 🏁 FINISHED "
                          f"ok={ok} rounds={event.get('iteration_count')} "
                          f"duration={event.get('duration_s'):.1f}s")
                elif etype == "result":
                    success = event.get("success")
                    output = event.get("output", "")
                    rounds = event.get("iteration_count", "?")
                    print()
                    print(f"  [{elapsed:6.2f}s] 📦 RESULT   success={success} rounds={rounds}")
                    if event.get("error"):
                        print(f"               error: {event['error']}")
                    print()
                    print("  ── output preview ──────────────────────────")
                    for line2 in output.splitlines()[:20]:
                        print(f"  | {line2}")
                    if len(output.splitlines()) > 20:
                        print(f"  | ... +{len(output.splitlines()) - 20} more lines")
                elif etype == "done":
                    print(f"  [{elapsed:6.2f}s] ✅ STREAM END")
                    return 0
                else:
                    print(f"  [{elapsed:6.2f}s] ?? {etype}: {json.dumps(event)[:100]}")

    except KeyboardInterrupt:
        print("\n  [interrupted]")
        return 130
    except Exception as exc:
        print(f"\n  [error] {type(exc).__name__}: {exc}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
