"""End-to-end smoke for the native tool-use path against a real model.

The unit suite (tests/test_react_native_tooluse.py) proves the wiring with a
fake router. This script proves the *real* round-trip: a tool-use-capable
model is given native ``tools=``, returns structured ``tool_calls``, those get
dispatched through the existing executor, and the loop reaches a final answer
— all with ``ECHO_NATIVE_TOOLUSE=1``.

It is deliberately NOT a ``test_*`` file, so pytest does not collect it into
the default suite (it costs real tokens). Run it manually against either:

    # an OpenAI-compatible provider from data/custom_models.json
    python tests/integration_native_tooluse.py mimo2.5

    # or Anthropic via env
    ANTHROPIC_API_KEY=sk-ant-... python tests/integration_native_tooluse.py

With neither it prints SKIP and exits 0, so it is safe to invoke anywhere.

Validated 2026-06 end-to-end against mimo-v2.5-pro, agnes-2.0-flash, and
gpt-5.5 (all returned structured tool_calls that dispatched + answered).

What it asserts:
  1. native activation — at least one model request carried a non-empty
     ``tools=`` list (proof the gate fired and specs were passed);
  2. real dispatch    — the skill ran (a ``tool_start`` event was emitted);
  3. end-to-end       — the loop returned a non-empty final answer.
"""

from __future__ import annotations

import json
import os
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

_DEFAULT_ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
_REPO_ROOT = Path(__file__).resolve().parents[1]


def _read_file(path: str = "") -> dict:
    """Toy skill the model is expected to call natively."""
    return {
        "path": path,
        "content": f"# {path}\nstatus = ready\nversion = 0.2.0\n",
    }


def _build_executor() -> ToolExecutor:
    reg = SkillRegistry()
    reg.register(
        Skill(
            name="read_config",
            description=(
                "Read a single config file and return its content. "
                "Args: path (string). Returns dict with path, content."
            ),
            trusted_source="builtin://read_config",
            handler=_read_file,
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


class _ToolsCapturingRouter:
    """Forwards to the real router but flags any request that carried a
    non-empty tools list — proof the native gate fired and specs were
    advertised to the model."""

    def __init__(self, inner: Any, model: str) -> None:
        self._inner = inner
        self.capabilities = inner.capabilities
        self.default_model = model
        self.requests_with_tools = 0

    def _note(self, req: Any) -> None:
        if getattr(req, "tools", None):
            self.requests_with_tools += 1

    def call(self, req: Any) -> Any:
        self._note(req)
        return self._inner.call(req)

    def call_stream(self, req: Any) -> Any:
        self._note(req)
        return self._inner.call_stream(req)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _Stack:
    def __init__(self, executor: ToolExecutor, router: Any, model: str) -> None:
        self.executor = executor

        class _Planner:
            def __init__(self, r: Any) -> None:
                self.router = r
                self.planner_model = model

        self.planner = _Planner(router)


def _resolve_router_and_model(argv: list[str]) -> tuple[Any, str] | None:
    """Pick a router+model from a custom_models provider arg or Anthropic env."""
    if len(argv) > 1 and argv[1].strip():
        key = argv[1].strip()
        path = _REPO_ROOT / "data" / "custom_models.json"
        cfgs = json.loads(path.read_text(encoding="utf-8"))
        if key not in cfgs:
            print(f"SKIP: '{key}' not in custom_models.json (have: {list(cfgs)})")
            return None
        cfg = cfgs[key]
        models = cfg.get("models") or ([cfg["id"]] if cfg.get("id") else [])
        model = models[0] if models else key
        from runtime.sensing.model_router.openai_router import OpenAIModelRouter

        router = OpenAIModelRouter(
            base_url=cfg["base_url"],
            api_key=cfg["api_key"],
            default_model=model,
            timeout_seconds=60.0,
        )
        return router, model

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if api_key:
        from runtime.sensing.model_router.anthropic_router import (
            AnthropicModelRouter,
        )

        model = os.environ.get("ECHO_NATIVE_SMOKE_MODEL", _DEFAULT_ANTHROPIC_MODEL)
        return AnthropicModelRouter(api_key=api_key, default_model=model), model
    return None


def main(argv: list[str]) -> int:
    resolved = _resolve_router_and_model(argv)
    if resolved is None:
        print(
            "SKIP: pass a custom_models.json provider key (e.g. `mimo2.5`) "
            "or set ANTHROPIC_API_KEY — native tool-use smoke skipped.",
        )
        return 0
    inner, model = resolved

    os.environ["ECHO_NATIVE_TOOLUSE"] = "1"
    print(f"using model: {model} (native tool-use ON)")
    if not getattr(inner.capabilities, "supports_tool_use", False):
        print("FAIL: router does not advertise supports_tool_use")
        return 1
    router = _ToolsCapturingRouter(inner, model)

    stack = _Stack(_build_executor(), router, model)
    intent = ParsedIntent(
        raw=(
            "Read the file app.config with the read_config tool, then tell me "
            "its version in one short sentence."
        ),
        intent_type="task",
        normalized_goal="读取 app.config 并报告版本",
        user_context={"auto_approve": True},
    )

    started = time.monotonic()
    events: list[dict] = []
    result = None
    try:
        gen = stream_react_loop(
            stack,
            intent,
            agent=None,
            model=model,
            max_iterations=5,
        )
        while True:
            events.append(next(gen))
    except StopIteration as stop:
        result = stop.value
    elapsed = time.monotonic() - started

    print(f"\nwall-clock: {elapsed * 1000:.0f} ms")
    tool_starts = [e for e in events if e.get("type") == "tool_start"]
    print(
        f"requests-with-tools: {router.requests_with_tools} | tool_start events: {len(tool_starts)}"
    )
    for s in tool_starts:
        print(f"  → tool_start iter={s.get('iteration')} name={s.get('tool_name')}")

    ok = True
    if router.requests_with_tools == 0:
        print("FAIL: no request carried native tools= — native gate did not fire")
        ok = False
    if not tool_starts:
        print("FAIL: the read_config skill was never dispatched")
        ok = False
    if result is None or not (result.final_answer or "").strip():
        print("FAIL: loop produced no final answer")
        ok = False

    if result is not None:
        print(f"\nfinal_answer ({len(result.final_answer or '')} chars):")
        print((result.final_answer or "")[:400])

    if ok:
        print("\n✓ PASS: native tool-use round-tripped end-to-end")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

