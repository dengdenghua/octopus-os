"""Quantify the chat fast-path vs ReAct tool-path TTFT and streaming
shape with the real mimo-v2.5-pro model.

Two prompts, same model, same stack:
  * "你好"                     → should hit reflection fast-path
                                  (no Thought/Action protocol overhead)
  * "用 slow_read 读 a.py b.py" → should hit ReAct loop with tools

For each: time-to-first-byte, total wall, chunk count, peek at
first 3 chunks. The point: prove that "你好" feels chat-like (TTFT
≈ network + first-token-decode) and not "tool-task-like" (which
must wait for an entire Thought/Action block before showing any
visible character).

Run from project root:
    python tests/integration_chat_vs_tool_ttft.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtime.core.cerebrum.react_loop import stream_react_loop  # noqa: E402
from runtime.execution.suckers import Skill, SkillRegistry  # noqa: E402
from runtime.execution.tool_engine import ToolExecutor  # noqa: E402
from runtime.platform.models import ParsedIntent  # noqa: E402
from runtime.safety.auth import TrustEngine  # noqa: E402
from runtime.sensing.gateway.openai_gateway.stream_handler import (  # noqa: E402
    _stream_direct_llm_fallback,
)
from runtime.sensing.gateway.realtime_turn_routing import (  # noqa: E402
    looks_like_tool_intent,
)
from runtime.sensing.model_router.openai_router import OpenAIModelRouter  # noqa: E402


def _build_router() -> OpenAIModelRouter:
    cfg = json.loads(
        (Path(__file__).resolve().parents[1] / "data" / "custom_models.json").read_text(
            encoding="utf-8"
        )
    )["mimo2.5"]
    # Schema may use either "model" (single) or "models" (list);
    # take the first listed model in either case.
    model_name = cfg.get("model") or (cfg.get("models") or ["mimo-v2.5-pro"])[0]
    return OpenAIModelRouter(
        base_url=cfg["base_url"],
        api_key=cfg["api_key"],
        default_model=model_name,
        timeout_seconds=120.0,
    )


class _Stack:
    def __init__(self, executor: ToolExecutor, router: OpenAIModelRouter) -> None:
        self.executor = executor

        class _Planner:
            planner_model = "mimo-v2.5-pro"

            def __init__(self, r: OpenAIModelRouter) -> None:
                self.router = r

        self.planner = _Planner(router)


def _slow_read(path: str = "") -> dict:
    time.sleep(0.2)
    return {"path": path, "content": f"[content of {path}]"}


def _build_executor() -> ToolExecutor:
    reg = SkillRegistry()
    reg.register(
        Skill(
            name="slow_read",
            description="Read a single file path (sleeps 0.2s).",
            trusted_source="builtin://slow_read",
            handler=_slow_read,
        ),
        verify_tests=False,
    )
    return ToolExecutor(
        registry=reg,
        immunity=TrustEngine(
            trusted_sources=["builtin://*"],
            unknown_policy="allow",
        ),
    )


def _bench_chat(stack: _Stack, prompt: str, *, user_model: str | None = None) -> dict:
    """Drive _stream_direct_llm_fallback directly (chat fast-path).

    ``user_model`` mirrors what realtime gateway forwards: ``None`` =
    default planner_model, ``"auto"`` = smart-routing sentinel from
    model_picker UI, anything else = user pinned a specific model.
    """
    intent = ParsedIntent(
        raw=prompt,
        intent_type="task",
        normalized_goal=prompt,
        user_context={"auto_approve": True},
    )
    started = time.monotonic()
    first_byte = None
    chunks: list[tuple[str, str]] = []
    routed_model = "?"
    for kind, payload, _final in _stream_direct_llm_fallback(
        stack,
        intent,
        agent=None,
        model=user_model,
    ):
        now = time.monotonic()
        if first_byte is None and kind == "text" and payload:
            first_byte = now - started
        chunks.append((kind, payload))
        if kind == "stats" and isinstance(payload, dict):
            routed_model = payload.get("model", routed_model)
    total = time.monotonic() - started
    text_chunks = [c for k, c in chunks if k == "text"]
    return {
        "ttfb_ms": int((first_byte or total) * 1000),
        "total_ms": int(total * 1000),
        "chunks": len(chunks),
        "text_chunks": len(text_chunks),
        "first_3_text": text_chunks[:3],
        "joined_chars": sum(len(c) for c in text_chunks),
        "routed_model": routed_model,
    }


def _bench_react(stack: _Stack, prompt: str) -> dict:
    """Drive stream_react_loop (full ReAct protocol)."""
    intent = ParsedIntent(
        raw=prompt,
        intent_type="task",
        normalized_goal=prompt,
        user_context={"auto_approve": True},
    )
    started = time.monotonic()
    first_byte = None
    text_chunks: list[str] = []
    tool_starts = 0
    tool_ends = 0
    gen = stream_react_loop(stack, intent, agent=None, max_iterations=4)
    try:
        while True:
            ev = next(gen)
            now = time.monotonic()
            if ev.get("type") == "text_delta":
                if first_byte is None:
                    first_byte = now - started
                text_chunks.append(ev.get("delta", ""))
            elif ev.get("type") == "tool_start":
                tool_starts += 1
            elif ev.get("type") == "tool_end":
                tool_ends += 1
    except StopIteration:
        pass
    total = time.monotonic() - started
    return {
        "ttfb_ms": int((first_byte or total) * 1000),
        "total_ms": int(total * 1000),
        "text_chunks": len(text_chunks),
        "first_3_text": text_chunks[:3],
        "joined_chars": sum(len(c) for c in text_chunks),
        "tool_starts": tool_starts,
        "tool_ends": tool_ends,
    }


def main() -> int:
    router = _build_router()
    executor = _build_executor()
    stack = _Stack(executor, router)

    # Warm: import overhead, first-call latency. Don't time it.
    print("warming up (one throw-away call)...")
    list(
        _stream_direct_llm_fallback(
            stack,
            ParsedIntent(raw="warm", intent_type="task", normalized_goal="warm", user_context={}),
            agent=None,
            model="mimo-v2.5-pro",
        )
    )

    chat_prompt = "你好"
    tool_prompt = "请用 slow_read 工具读取 a.py、b.py 两个文件的内容并对比。可以并发读取。"

    print("\n--- routing predictions ---")
    print(
        f"  '{chat_prompt}'  → tool_intent? {looks_like_tool_intent(chat_prompt)}  (False = chat fast-path)"
    )
    print(
        f"  '{tool_prompt[:30]}…'  → tool_intent? {looks_like_tool_intent(tool_prompt)}  (True = ReAct)"
    )

    print("\n--- chat fast-path: '你好' (model=auto) ---")
    chat_stats = _bench_chat(stack, chat_prompt, user_model="auto")
    print(f"  routed model: {chat_stats['routed_model']}")
    print(f"  TTFB:         {chat_stats['ttfb_ms']:>5} ms")
    print(f"  total wall:   {chat_stats['total_ms']:>5} ms")
    print(f"  text chunks:  {chat_stats['text_chunks']}")
    print(f"  total chars:  {chat_stats['joined_chars']}")
    print(f"  first 3 text chunks: {chat_stats['first_3_text']}")

    print("\n--- ReAct path: 'read 2 files' ---")
    tool_stats = _bench_react(stack, tool_prompt)
    print(f"  TTFB:        {tool_stats['ttfb_ms']:>5} ms")
    print(f"  total wall:  {tool_stats['total_ms']:>5} ms")
    print(f"  text chunks: {tool_stats['text_chunks']}")
    print(f"  tool calls:  {tool_stats['tool_starts']} start / {tool_stats['tool_ends']} end")
    print(f"  total chars: {tool_stats['joined_chars']}")
    print(f"  first 3 text chunks: {tool_stats['first_3_text']}")

    print("\n--- comparison ---")
    ratio = tool_stats["ttfb_ms"] / max(chat_stats["ttfb_ms"], 1)
    print(f"  TTFB ratio (tool/chat): {ratio:.1f}x")
    print(
        f"  chat first text len:    {len(chat_stats['first_3_text'][0]) if chat_stats['first_3_text'] else 0}"
    )
    print(
        f"  tool first text len:    {len(tool_stats['first_3_text'][0]) if tool_stats['first_3_text'] else 0}"
    )
    if chat_stats["text_chunks"] >= 3:
        print(f"  ✓ chat path streams in {chat_stats['text_chunks']} chunks (genuine streaming)")
    else:
        print(
            f"  ⚠ chat path delivered in {chat_stats['text_chunks']} chunks (router may be batching)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
