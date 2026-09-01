from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..config.schema import (
    AgentConfig,
    BudgetConfig,
    CredentialPoolConfig,
    HotCacheConfig,
    ImmunityConfig,
    LearnConfig,
    PlannerConfig,
)

try:
    import yaml

    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False
    yaml = None


class SetupWizard:
    def __init__(
        self,
        output_path: str | Path | None = None,
        non_interactive: bool = False,
    ) -> None:
        if output_path is None:
            output_path = Path(".") / "config.yaml"
        self._output = Path(output_path)
        self._non_interactive = non_interactive

    def run(self) -> Path:
        if self._non_interactive:
            return self._run_non_interactive()
        return self._run_interactive()

    def _run_interactive(self) -> Path:
        self._print_banner()

        planner_type = self._ask_planner_type()
        model = self._ask_model(planner_type)
        api_key = self._ask_api_key(model) if planner_type == "llm" else None
        pool_keys = self._ask_credential_pool(api_key)
        journal = self._ask_journal()
        hot_cache = self._ask_hot_cache()

        config = self._build_config(
            planner_type=planner_type,
            model=model,
            api_key=api_key,
            pool_keys=pool_keys,
            journal=journal,
            hot_cache=hot_cache,
        )

        return self._write_config(config)

    def _run_non_interactive(self) -> Path:
        config = AgentConfig(
            planner=PlannerConfig(type="static"),
        )
        return self._write_config(config)

    def _print_banner(self) -> None:
        print()
        print("  ╔══════════════════════════════════════════╗")
        print("  ║   Echo Agent Setup Wizard             ║")
        print("  ║   仿生自进化智能体 · 3 分钟上手          ║")
        print("  ╚══════════════════════════════════════════╝")
        print()

    def _ask_planner_type(self) -> str:
        print("Step 1/5: 选择规划器")
        print("  [1] static  · 规则驱动 · 无需 API Key · 适合体验")
        print("  [2] llm     · LLM 驱动 · 需要 API Key · 完整能力")
        print()
        choice = self._input("请选择 [1/2]", default="1")
        return "static" if choice.strip() == "1" else "llm"

    def _ask_model(self, planner_type: str) -> str:
        if planner_type == "static":
            return "mock/planner"
        print()
        print("Step 2/5: 选择模型")
        print("  [1] Claude Haiku  · 快速便宜 · 推荐")
        print("  [2] Claude Sonnet · 更强更贵")
        print("  [3] GPT-4o-mini   · OpenAI 入门")
        print("  [4] GPT-4o        · OpenAI 旗舰")
        print("  [5] Ollama 本地   · 免费离线")
        print("  [6] 自定义模型 ID")
        print()
        choice = self._input("请选择 [1-6]", default="1")
        models = {
            "1": "claude-haiku-4-5-20251001",
            "2": "claude-sonnet-4-5-20250514",
            "3": "gpt-4o-mini",
            "4": "gpt-4o",
            "5": "ollama/llama3.2",
        }
        if choice in models:
            return models[choice]
        return self._input("输入模型 ID", default="claude-haiku-4-5-20251001")

    def _ask_api_key(self, model: str) -> str | None:
        if model.startswith("ollama/"):
            return None
        if model.startswith("claude"):
            env_var = "ANTHROPIC_API_KEY"
        elif model.startswith("gpt"):
            env_var = "OPENAI_API_KEY"
        else:
            env_var = None

        if env_var and os.environ.get(env_var):
            print()
            print(f"  ✓ 检测到 {env_var} 已设置")
            return None

        print()
        print(f"Step 3/5: API Key（模型: {model}）")
        key = self._input(f"输入 API Key（留空则使用 {env_var or '环境变量'}）", default="")
        return key if key.strip() else None

    def _ask_credential_pool(self, api_key: str | None) -> list[str]:
        print()
        print("Step 4/5: Credential Pool（多 Key 轮换）")
        print("  多个 API Key 可自动轮换 + 耗尽切换")
        keys_input = self._input("输入额外 Key（逗号分隔，留空跳过）", default="")
        if not keys_input.strip():
            return []
        return [k.strip() for k in keys_input.split(",") if k.strip()]

    def _ask_journal(self) -> str | None:
        print()
        print("Step 5/5: Journal 持久化")
        print("  Journal 记录所有事件 · 用于反思学习")
        choice = self._input("启用 Journal 持久化? [y/N]", default="n")
        if choice.strip().lower() in ("y", "yes"):
            return "events.jsonl"
        return None

    def _ask_hot_cache(self) -> bool:
        return True

    def _build_config(
        self,
        planner_type: str,
        model: str,
        api_key: str | None,
        pool_keys: list[str],
        journal: str | None,
        hot_cache: bool,
    ) -> AgentConfig:
        planner_kwargs: dict[str, Any] = {
            "type": planner_type,
            "model": model,
        }
        if api_key and model.startswith("claude"):
            planner_kwargs["anthropic_api_key"] = api_key
        if planner_type == "static":
            planner_kwargs["mock_response"] = (
                '{"reasoning":"demo","nodes":[{"skill":"list_cwd","args":{"path":"."}}]}'
            )

        pool_kwargs: dict[str, Any] = {}
        if pool_keys:
            pool_kwargs["keys"] = pool_keys

        return AgentConfig(
            planner=PlannerConfig(**planner_kwargs),
            budget=BudgetConfig(),
            immunity=ImmunityConfig(),
            learn=LearnConfig(),
            credential_pool=CredentialPoolConfig(**pool_kwargs),
            hot_cache=HotCacheConfig(enabled=hot_cache),
            journal_file=journal,
        )

    def _write_config(self, config: AgentConfig) -> Path:
        if not YAML_AVAILABLE:
            self._write_json(config)
            return self._output

        data = _config_to_yaml_dict(config)
        self._output.parent.mkdir(parents=True, exist_ok=True)
        self._output.write_text(
            yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

        print()
        print(f"  ✓ 配置已写入: {self._output}")
        print()
        if config.planner.type == "static":
            print(f"  运行: python -m runtime run 'list files' --config {self._output}")
        else:
            print(f"  运行: python -m runtime run 'hello' --config {self._output}")
        print("  演示: python -m runtime demo")
        print("  体检: python -m runtime doctor")
        print()
        return self._output

    def _write_json(self, config: AgentConfig) -> None:
        import json

        self._output.parent.mkdir(parents=True, exist_ok=True)
        self._output.write_text(
            json.dumps(config.model_dump(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"  ✓ 配置已写入: {self._output} (JSON format, PyYAML not installed)")

    def _input(self, prompt: str, default: str = "") -> str:
        try:
            suffix = f" [{default}]" if default else ""
            value = input(f"  {prompt}{suffix}: ").strip()
            return value if value else default
        except (EOFError, KeyboardInterrupt):
            print()
            return default


def _config_to_yaml_dict(config: AgentConfig) -> dict[str, Any]:
    data = config.model_dump()

    def _clean(d: Any) -> Any:
        if isinstance(d, dict):
            return {k: _clean(v) for k, v in d.items() if v is not None and v != [] and v}
        if isinstance(d, list) and len(d) == 0:
            return None
        return d

    return _clean(data) or {}
