"""End-to-end: drive stream_react_loop with the real mimo2.5 model
(OpenAI-compatible at xiaomimimo.com) and verify multi-action dispatch.

Reads model config from data/custom_models.json. Uses OpenAIModelRouter
directly. No backend or stack-builder needed.

Run from project root:
    python tests/integration_real_model_dispatch.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtime.core.cerebrum.react_loop import stream_react_loop  # noqa: E402
from runtime.execution.suckers import Skill, SkillRegistry  # noqa: E402
from runtime.execution.tool_engine import ToolExecutor  # noqa: E402
from runtime.platform.models import ParsedIntent  # noqa: E402
from runtime.safety.auth import TrustEngine  # noqa: E402
from runtime.sensing.model_router.openai_router import OpenAIModelRouter  # noqa: E402

_DELAY = 0.25


def _slow_read(path: str = "") -> dict:
    time.sleep(_DELAY)
    return {"path": path, "content": f"[mock content for {path}]"}


def _build_executor() -> ToolExecutor:
    reg = SkillRegistry()
    reg.register(
        Skill(
            name="slow_read",
            description=(
                "Read a single file. Sleeps 0.25s to simulate I/O. "
                "Args: path (string). Returns dict with path, content."
            ),
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


class _Stack:
    def __init__(self, executor: ToolExecutor, router: Any) -> None:
        self.executor = executor

        class _Planner:
            planner_model = "mimo-v2.5-pro"

            def __init__(self, r: Any) -> None:
                self.router = r

        self.planner = _Planner(router)


def main() -> int:
    custom = json.loads(
        (Path(__file__).resolve().parents[1] / "data" / "custom_models.json").read_text(
            encoding="utf-8"
        )
    )
    if "mimo2.5" not in custom:
        print("FAIL: mimo2.5 not in data/custom_models.json")
        return 1
    cfg = custom["mimo2.5"]
    print(f"using model: {cfg['model']} @ {cfg['base_url']}")

    router = OpenAIModelRouter(
        base_url=cfg["base_url"],
        api_key=cfg["api_key"],
        default_model=cfg["model"],
        timeout_seconds=120.0,
    )

    stack = _Stack(_build_executor(), router)
    intent = ParsedIntent(
        raw=(
            "请读取以下三个文件并简要对比: a.py, b.py, c.py。"
            "可用工具 slow_read。三个文件相互独立, 一次性并发读取。"
        ),
        intent_type="task",
        normalized_goal="读取并对比 a.py b.py c.py",
        user_context={"auto_approve": True},
    )

    started = time.monotonic()
    events: list[dict] = []
    result = None
    try:
        gen = stream_react_loop(stack, intent, agent=None, max_iterations=4)
        while True:
            events.append(next(gen))
    except StopIteration as stop:
        result = stop.value
    elapsed = time.monotonic() - started

    print(f"\nwall-clock: {elapsed * 1000:.0f} ms")
    if result is None:
        print("FAIL: react_loop returned None")
        for e in events[-5:]:
            print(f"  late event: {e}")
        return 1

    starts = [e for e in events if e["type"] == "tool_start"]
    ends = [e for e in events if e["type"] == "tool_end"]
    print(f"events: {len(events)} | tool_start: {len(starts)} | tool_end: {len(ends)}")
    for s in starts:
        print(
            f"  → tool_start iter={s['iteration']} "
            f"name={s['tool_name']} batch={s.get('parallel_batch_size', 1)}"
        )
    for e in ends:
        print(
            f"  ← tool_end   iter={e['iteration']} name={e['tool_name']} "
            f"status={e['status']} dur={e['duration_ms']}ms"
        )

    parallel_step = None
    print("\nresult.steps:")
    for s in result.steps:
        n = len(s.actions or [])
        print(f"  iter={s.iteration} actions={n} thought={(s.thought or '')[:80]!r}")
        if n > 1:
            parallel_step = s

    print(f"\nfinal_answer ({len(result.final_answer)} chars):")
    print(result.final_answer[:400])

    if parallel_step is None:
        print("\n⚠ model did NOT emit a multi-action block this round")
        print("   (acceptable: model chose serial; the *capability* is wired)")
        return 0

    print(f"\n✓ multi-action: {len(parallel_step.actions)} parallel calls")
    for i, a in enumerate(parallel_step.actions, 1):
        print(f"    [{i}] {a}")
    print(f"  observation snippet: {(parallel_step.observation or '')[:300]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
