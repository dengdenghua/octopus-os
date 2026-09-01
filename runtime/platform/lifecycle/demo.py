# ruff: noqa: E402 — module-level imports below are intentionally late

from __future__ import annotations

import time

# Public compatibility export. Keep the demo importable for downstream
# launchers, but do not treat it as a runtime dependency.
COMPATIBILITY_STATUS = "legacy-demo"


class InteractiveDemo:
    def __init__(self, slow: bool = True) -> None:
        self._slow = slow

    def run(self) -> None:
        self._intro()
        self._demo_static_planner()
        self._demo_skill_registry()
        self._demo_reflection()
        self._demo_safety()
        self._demo_credential_pool()
        self._demo_hot_cache()
        self._next_steps()

    def _print(self, text: str = "") -> None:
        print(text)
        if self._slow:
            time.sleep(0.3)

    def _print_step(self, title: str) -> None:
        print()
        print(f"{'─' * 50}")
        print(f"  {title}")
        print(f"{'─' * 50}")
        if self._slow:
            time.sleep(0.5)

    def _intro(self) -> None:
        print()
        print("  ╔══════════════════════════════════════════════╗")
        print("  ║        Echo Agent Interactive Demo        ║")
        print("  ║        仿生自进化智能体 · 5 分钟体验         ║")
        print("  ╚══════════════════════════════════════════════╝")
        print()
        self._print("  章鱼是地球上最聪明的无脊椎动物：")
        self._print("    · 9 个大脑（1 中央 + 8 触手）协同决策")
        self._print("    · 双路决策：反射（快）+ 深思（慢）")
        self._print("    · 墨汁防御 + 断腕自愈")
        self._print()
        self._print("  Echo Agent 把这些能力映射到软件架构：")
        self._print("    · Cerebrum  → 中央大脑（LLM Planner）")
        self._print("    · Ganglia   → 触手神经节（Graph Runtime）")
        self._print("    · Arms      → 8 条触手（Skill 执行）")
        self._print("    · Sensing   → 眼睛/吸盘（模型路由 + Key 池）")
        self._print("    · Safety    → 墨汁/免疫（安全防护）")
        self._print("    · Memory    → 基因记忆（反思 + 知识编译）")

    def _demo_static_planner(self) -> None:
        self._print_step("1. Static Planner · 规则驱动规划")
        self._print("  输入: 'list files in current directory'")
        self._print()

        try:
            from runtime.core.cerebrum.planner import Rule, StaticPlanner
            from runtime.platform.models import BudgetSpec, SkillId

            rules = [
                Rule(
                    name="demo_list",
                    intent_types=["task"],
                    keywords=["list", "demo"],
                    skill_sequence=[SkillId("list_cwd")],
                    priority=10,
                ),
            ]
            planner = StaticPlanner(
                rules=rules,
                default_budget=BudgetSpec(tokens=50_000, usd=0.50),
                fallback_skill=SkillId("list_cwd"),
            )
            from runtime.platform.models import ParsedIntent

            intent = ParsedIntent(
                raw="list files", intent_type="task", normalized_goal="list files"
            )
            plan = planner.plan(intent)
            self._print(f"  Plan strategy: {plan.strategy}")
            self._print(f"  Plan nodes: {len(plan.nodes)}")
            for n in plan.nodes:
                self._print(f"    · {n.node_id}: {n.skill_ref}")
            self._print()
            self._print("  ✓ Static Planner 无需 API Key，0 成本执行")
        except Exception as e:
            self._print(f"  (demo skipped: {e})")

    def _demo_skill_registry(self) -> None:
        self._print_step("2. Skill Registry · 技能注册中心")
        self._print("  Echo 内置多种 Skill，开箱即用：")
        self._print()

        try:
            from runtime.execution.suckers import SkillRegistry
            from runtime.execution.suckers.builtins import register_builtins

            registry = SkillRegistry()
            register_builtins(registry)
            names = registry.all_names()
            for n in names[:8]:
                skill = registry._by_name.get(n)
                desc = skill.description[:40] if skill else ""
                self._print(f"    · {n:<20s} {desc}")
            if len(names) > 8:
                self._print(f"    ... and {len(names) - 8} more")
            self._print()
            self._print(f"  ✓ {len(names)} built-in skills registered")
        except Exception as e:
            self._print(f"  (demo skipped: {e})")

    def _demo_reflection(self) -> None:
        self._print_step("3. Reflection · 6 条反思产出闭环")
        self._print("  每次任务执行后，Echo 自动反思：")
        self._print()
        reflections = [
            ("RuleExtractor", "失败模式 → 规则", "避免重复犯错"),
            ("MemoryConsolidator", "成功模式 → 记忆", "复用成功经验"),
            ("KGUpdater", "事件 → 知识图谱", "关联推理"),
            ("WorkflowRewriter", "提案 → 规则改写", "自动优化流程"),
            ("RecipeEvaluator", "配方 → 评分", "自我评估"),
            ("SkillForge", "经验 → 新 Skill", "能力沉淀"),
        ]
        for name, action, benefit in reflections:
            self._print(f"    · {name:<22s} {action:<20s} → {benefit}")
        self._print()
        self._print("  ✓ 6 条反思全闭环 · 越用越聪明")

    def _demo_safety(self) -> None:
        self._print_step("4. Safety · 内生安全体系")
        self._print("  三层安全防护，从架构层面保障：")
        self._print()
        layers = [
            ("Constitution Gate", "宪法门", "拒绝违反约束的指令"),
            ("Immunity System", "免疫系统", "信任源 + 白名单 + 隔离策略"),
            ("Circuit Breaker", "熔断器", "连续失败自动熔断 + 自愈"),
        ]
        for en, cn, desc in layers:
            self._print(f"    · {en:<20s} ({cn})  → {desc}")
        self._print()
        self._print("  ✓ 安全不是补丁，是架构的一部分")

    def _demo_credential_pool(self) -> None:
        self._print_step("5. Credential Pool · API Key 池")
        self._print("  多 Key 自动轮换 + 耗尽切换 + 冷却恢复：")
        self._print()

        try:
            from runtime.sensing.model_router.credential_pool import CredentialPool

            pool = CredentialPool(
                keys=["key-1", "key-2", "key-3"],
                strategy="round_robin",
                cooldown_seconds=60.0,
            )
            for i in range(4):
                key = pool.acquire()
                self._print(f"    第 {i + 1} 次获取: {key[:8]}...")
            pool.report_exhausted("key-1")
            key = pool.acquire()
            self._print(f"    key-1 耗尽后获取: {key[:8]}...")
            self._print()
            self._print("  ✓ Key 轮换 + 耗尽切换 + 自动恢复")
        except Exception as e:
            self._print(f"  (demo skipped: {e})")

    def _demo_hot_cache(self) -> None:
        self._print_step("6. Hot Cache · 会话热缓存")
        self._print("  跨会话上下文持久化，告别失忆：")
        self._print()

        try:
            from runtime.memory.runtime_state.hot_cache import SessionHotCache

            cache = SessionHotCache(
                path="~/.echo/demo_cache",
                max_words=500,
                ttl_hours=24.0,
            )
            entry = cache.save(
                agent_id="demo-agent",
                summary="用户偏好: 简洁回答, Python 代码, tabs 缩进",
                topics=["偏好", "代码风格"],
            )
            self._print(f"    保存: {entry.summary[:40]}...")
            self._print(f"    词数: {entry.word_count} / {cache._max_words}")
            self._print(f"    主题: {', '.join(entry.topics)}")

            loaded = cache.load("demo-agent")
            if loaded:
                self._print(f"    加载: {loaded.summary[:40]}...")
            self._print()
            self._print("  ✓ 跨会话记忆 · 下次对话不用从头来")

            import shutil
            from pathlib import Path

            demo_dir = Path(os.path.expanduser("~/.echo/demo_cache"))
            if demo_dir.exists():
                shutil.rmtree(demo_dir, ignore_errors=True)
        except Exception as e:
            self._print(f"  (demo skipped: {e})")

    def _next_steps(self) -> None:
        self._print_step("Next Steps · 下一步")
        self._print("  1. 生成配置:  python -m runtime setup")
        self._print("  2. 环境检查:  python -m runtime doctor")
        self._print("  3. 运行任务:  python -m runtime run 'your goal' --config config.yaml")
        self._print("  4. 浏览技能:  python -m runtime skills list")
        self._print("  5. 安装技能:  python -m runtime skills install <name>")
        self._print("  6. 备份数据:  python -m runtime backup")
        self._print("  7. 编译知识:  python -m runtime wiki compile")
        self._print()
        self._print("  文档: https://github.com/dengdenghua/echo-agent")
        print()


import os
