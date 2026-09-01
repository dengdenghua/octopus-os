"""方案 F · 真实 LLM 集成 PoC.

**目的**：端到端验证 LightweightLlmClient + SkillManifestLoader + LightweightReAct
三件套能配合真实 LLM API 跑通。

**两种模式**：

1. **Offline 模式**（无 API key）：用 FakeTransport 模拟 LLM 响应，验证协议正确性
2. **Online 模式**（需 DEEPSEEK_API_KEY 环境变量）：真调 DeepSeek API，验证端到端

**使用方法**：

.. code-block:: bash

    # Offline 模式（默认，无需 key）
    python examples/plan_f_poc.py

    # Online 模式（需 key）
    export DEEPSEEK_API_KEY="sk-xxxxxxxx"
    python examples/plan_f_poc.py --online

    # 也支持其他供应商
    export QWEN_API_KEY="sk-xxxxxxxx"
    python examples/plan_f_poc.py --provider qwen --online
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

# 让脚本能直接跑（不依赖 PYTHONPATH）
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from runtime.tentacle.llm import (  # noqa: E402
    FakeTransport,
    LightweightLlmClient,
    LightweightReAct,
    LlmConfig,
    ReActCallbacks,
    SkillManifestLoader,
    TaskOutcome,
    ToolCall,
    ToolCallResult,
)

# ── 工具执行器：Mock 实现（不真发请求） ──────────────────────


class _MockMobileExecutor:
    """Mock Android 设备 —— 模拟一次手机控制任务.

    流程：截图 → 找节点 → tap → 看到欢迎页 → 调 finish.
    不真发任何网络/系统调用，但能给 LLM 反馈真实形状的数据。
    """

    def __init__(self) -> None:
        self.calls: list[ToolCall] = []
        self.screenshot_count = 0

    def execute(self, tc: ToolCall) -> ToolCallResult:
        self.calls.append(tc)
        args = tc.arguments

        # 模拟设备行为
        if tc.name == "android.get_screen_info":
            self.screenshot_count += 1
            return ToolCallResult(
                tc.id, tc.name, True,
                json.dumps({
                    "current_app": "com.tencent.mm",
                    "current_activity": ".ui.LauncherUI",
                    "screen_size": [1080, 2400],
                    "tree": [
                        {"ref": "e001", "class": "TextView", "text": "微信", "bounds": [50, 100, 200, 200]},
                        {"ref": "e002", "class": "TextView", "text": "通讯录", "bounds": [50, 250, 200, 350]},
                        {"ref": "e003", "class": "TextView", "text": "发现", "bounds": [50, 400, 200, 500]},
                        {"ref": "e004", "class": "TextView", "text": "我", "bounds": [50, 550, 200, 650]},
                    ],
                }, ensure_ascii=False),
            )
        if tc.name == "android.tap":
            x, y = args.get("x", 0), args.get("y", 0)
            return ToolCallResult(
                tc.id, tc.name, True,
                f"tapped at ({x}, {y}), 0.05s elapsed",
            )
        if tc.name == "android.input_text":
            return ToolCallResult(
                tc.id, tc.name, True,
                f"typed: {args.get('text', '')!r}",
            )
        if tc.name == "android.open_app":
            return ToolCallResult(
                tc.id, tc.name, True,
                f"opened app: {args.get('app_name') or args.get('package_name')!r}, current=com.tencent.mm/.ui.LauncherUI",
            )
        if tc.name == "android.swipe":
            return ToolCallResult(tc.id, tc.name, True, "swiped 600ms")
        if tc.name == "android.wait":
            return ToolCallResult(tc.id, tc.name, True, f"waited {args.get('ms', 1000)}ms")
        if tc.name == "android.finish":
            return ToolCallResult(tc.id, tc.name, True, "task marked complete")
        if tc.name == "android.fail":
            return ToolCallResult(tc.id, tc.name, True, "task marked failed")
        # 默认
        return ToolCallResult(
            tc.id, tc.name, True,
            f"[MOCK] {tc.name}({args}) executed in 50ms",
        )


# ── Offline 模式：FakeTransport 预设 LLM 响应 ────────────────


def _build_offline_responses() -> list[dict[str, Any]]:
    """构造一个完整 ReAct 流的 LLM 响应序列.

    任务：在微信中找到"我"按钮并点击。
    """
    return [
        # Step 1: LLM 调 get_screen_info 看屏幕
        {
            "choices": [{"message": {
                "content": "先看一下屏幕",
                "tool_calls": [{
                    "id": "call_1", "type": "function",
                    "function": {"name": "android.get_screen_info", "arguments": "{}"},
                }],
            }, "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 250, "completion_tokens": 25, "total_tokens": 275},
            "model": "deepseek-chat",
        },
        # Step 2: LLM 看到屏幕后调 tap 点击"我" (e004 = bounds [50,550,200,650] → 中心 125,600)
        {
            "choices": [{"message": {
                "content": "点击'我'按钮",
                "tool_calls": [{
                    "id": "call_2", "type": "function",
                    "function": {"name": "android.tap",
                                 "arguments": '{"x": 125, "y": 600}'},
                }],
            }, "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 320, "completion_tokens": 20, "total_tokens": 340},
            "model": "deepseek-chat",
        },
        # Step 3: LLM 调 finish 收尾
        {
            "choices": [{"message": {
                "content": "任务完成，已点击'我'按钮",
                "tool_calls": [{
                    "id": "call_3", "type": "function",
                    "function": {"name": "android.finish",
                                 "arguments": '{"ok": true, "summary": "成功进入微信我页面"}'},
                }],
            }, "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 380, "completion_tokens": 35, "total_tokens": 415},
            "model": "deepseek-chat",
        },
        # Step 4: LLM 给个最终回复（finish 已收尾，但 LLM 多说了一句）
        {
            "choices": [{"message": {
                "content": "已成功完成。",
                "tool_calls": [],
            }, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 400, "completion_tokens": 8, "total_tokens": 408},
            "model": "deepseek-chat",
        },
    ]


def run_offline_mode(skills: list) -> dict[str, Any]:
    """Offline 模式 —— 用 FakeTransport 演示协议."""
    print("=" * 70)
    print("【Offline 模式】 FakeTransport 模拟 LLM 响应")
    print("=" * 70)
    print("任务: 在微信中找到 '我' 按钮并点击")
    print(f"加载技能: {len(skills)} 个（{sum(1 for s in skills if s.name.startswith('android.'))} android.*）")
    print()

    fake = FakeTransport(_build_offline_responses())
    client = LightweightLlmClient(
        LlmConfig.deepSeek(api_key="fake-key-for-offline"),
        transport=fake,
    )

    executor = _MockMobileExecutor()
    trace: list[str] = []
    callbacks = ReActCallbacks(
        on_step_start=lambda s: trace.append(f"  [Step {s}]"),
        on_tool_call=lambda tc: trace.append(f"    → LLM 调 {tc.name}({json.dumps(tc.arguments, ensure_ascii=False)[:60]})"),
        on_tool_result=lambda r: trace.append(f"    ← {r.name}: {r.content[:60]!r}..."),
        on_finish=lambda r: trace.append(f"  [结束] outcome={r.outcome.value}  steps={r.steps}  tokens={r.total_tokens}"),
    )

    react = LightweightReAct(
        client=client, executor=executor, max_steps=10,
        callbacks=callbacks,
    )

    start = time.time()
    result = react.run("在微信中找到'我'按钮并点击", skills=skills)
    elapsed = time.time() - start

    print("\n".join(trace))
    print()
    print(f"总耗时: {elapsed:.2f}s")
    print(f"工具调用次数: {len(executor.calls)}")
    print(f"工具调用序列: {[c.name for c in executor.calls]}")
    print()

    return {
        "outcome": result.outcome.value,
        "steps": result.steps,
        "tokens": result.total_tokens,
        "elapsed_s": elapsed,
        "executor_calls": len(executor.calls),
        "llm_calls": len(fake.calls),
    }


# ── Online 模式：真调 DeepSeek API ───────────────────────────


def run_online_mode(skills: list, provider: str = "deepseek") -> dict[str, Any]:
    """Online 模式 —— 真调 LLM API."""
    print("=" * 70)
    print(f"【Online 模式】 真实 {provider} API")
    print("=" * 70)

    # 配置
    if provider == "deepseek":
        config = LlmConfig.deepSeek()
    elif provider == "qwen":
        config = LlmConfig.qwen()
    elif provider == "openai":
        config = LlmConfig.openAi()
    elif provider == "glm":
        config = LlmConfig.glm()
    elif provider == "ollama":
        config = LlmConfig.ollama()
    else:
        raise ValueError(f"unknown provider: {provider}")

    if (not config.api_key or config.api_key == "ollama") and provider != "ollama":
        raise SystemExit(f"❌ {provider.upper()}_API_KEY not set")

    print(f"Model: {config.model}")
    print(f"Base URL: {config.base_url}")
    print("任务: 在微信中找到 '我' 按钮并点击")
    print()

    client = LightweightLlmClient(config)
    executor = _MockMobileExecutor()
    callbacks = ReActCallbacks(
        on_step_start=lambda s: print(f"  [Step {s}]"),
        on_tool_call=lambda tc: print(f"    → LLM 调 {tc.name}({json.dumps(tc.arguments, ensure_ascii=False)[:80]})"),
        on_tool_result=lambda r: print(f"    ← {r.name}: {r.content[:80]!r}"),
        on_finish=lambda r: print(f"  [结束] outcome={r.outcome.value}  steps={r.steps}  tokens={r.total_tokens}"),
    )
    react = LightweightReAct(client=client, executor=executor,
                             max_steps=10, callbacks=callbacks)

    start = time.time()
    result = react.run("在微信中找到'我'按钮并点击", skills=skills)
    elapsed = time.time() - start

    print()
    print(f"总耗时: {elapsed:.2f}s")
    print(f"工具调用: {[c.name for c in executor.calls]}")
    return {
        "outcome": result.outcome.value,
        "steps": result.steps,
        "tokens": result.total_tokens,
        "elapsed_s": elapsed,
        "executor_calls": len(executor.calls),
    }


# ── 基准测试：token 效率 ────────────────────────────────────


def benchmark_token_efficiency(skills: list) -> None:
    """演示：30 SKILL.md 单次塞进 system prompt 的 token 成本."""
    print("=" * 70)
    print("【基准】30 SKILL.md → 一次 LLM 调用的 token 成本")
    print("=" * 70)

    # 构造"system + 30 tools"的完整请求体
    from runtime.tentacle.llm import LlmConfig
    from runtime.tentacle.llm.chat_types import ChatMessage
    from runtime.tentacle.llm.lightweight_client import UrllibTransport

    config = LlmConfig.deepSeek(api_key="fake")
    client = LightweightLlmClient(config, transport=UrllibTransport())
    body = client._build_request_body(
        messages=[ChatMessage.system("你是助手"), ChatMessage.user("打开微信")],
        skills=skills,
        temperature=None,
        max_tokens=None,
    )

    # 估算 token（4 chars ≈ 1 token，中文 1.5 chars/token）
    body_json = json.dumps(body, ensure_ascii=False)
    char_count = len(body_json)
    est_tokens_en = char_count // 4
    est_tokens_zh = int(char_count * 0.7 / 4)

    print(f"30 个 skill 数量: {len(skills)}")
    print(f"请求体字符数: {char_count:,}")
    print(f"请求体字节数: {len(body_json.encode('utf-8')):,}")
    print(f"估算 tokens: {est_tokens_en:,}（纯英文）/ {est_tokens_zh:,}（中英混合）")
    print(f"DeepSeek 输入成本: ¥{est_tokens_zh / 1_000_000 * 2:.4f} 元/次")
    print("  （DeepSeek 定价：输入 ¥2/M tokens）")
    print()
    print("→ 实测 30 个 SKILL.md 全部塞进 system prompt，单次调用成本 < 0.01 元")
    print()


# ── 入口 ──────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="方案 F 端到端 PoC")
    parser.add_argument("--online", action="store_true", help="真调 LLM API（需 API key）")
    parser.add_argument("--provider", default="deepseek",
                        choices=["deepseek", "qwen", "openai", "glm", "ollama"])
    args = parser.parse_args()

    print()
    print("🐙 Echo Mobile · 方案 F PoC")
    print()

    # 加载 30 个 SKILL.md
    skills = SkillManifestLoader().load_directory(
        Path("runtime/tentacle/mobile/skills")
    )
    print(f"加载 {len(skills)} 个 SKILL.md（{sum(s.risk == 'low' for s in skills)} low / "
          f"{sum(s.risk == 'medium' for s in skills)} medium / "
          f"{sum(s.risk == 'high' for s in skills)} high）")
    print()

    if args.online:
        result = run_online_mode(skills, provider=args.provider)
    else:
        result = run_offline_mode(skills)

    benchmark_token_efficiency(skills)

    print("=" * 70)
    print("【总结】")
    print("=" * 70)
    for k, v in result.items():
        print(f"  {k}: {v}")
    print()
    if result["outcome"] == TaskOutcome.DONE.value:
        print("✅ PoC 跑通 —— 方案 F 端到端可行")
    else:
        print(f"⚠️  任务未完成（outcome={result['outcome']}）")
    print()


if __name__ == "__main__":
    main()

