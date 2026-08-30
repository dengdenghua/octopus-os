from __future__ import annotations

import contextlib
import logging
import os
from typing import Any

from runtime.core.cerebrum.planner import Rule
from runtime.core.nerves.reflex import (
    CacheMatcher,
    DeterministicMatcher,
    ReflexMatch,
    ReflexRouter,
    RegexMatcher,
)
from runtime.execution.arms import Arm, ArmPool
from runtime.execution.suckers import SkillRegistry
from runtime.execution.tool_engine import ToolExecutor
from runtime.memory.hemolymph import ContextComposer
from runtime.memory.journal import InMemoryJournal, Journal
from runtime.platform.models import (
    ArmId,
    BudgetSpec,
    ParsedIntent,
    SkillId,
)
from runtime.platform.process.utils import safe_repr as _safe_repr
from runtime.safety.auth import TrustEngine
from runtime.sensing.model_router import MockModelRouter, ModelRouter

_logger = logging.getLogger(__name__)


class _Colors:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled

    def _wrap(self, code: str, s: str) -> str:
        return f"\x1b[{code}m{s}\x1b[0m" if self.enabled else s

    def dim(self, s: str) -> str:
        return self._wrap("2", s)

    def green(self, s: str) -> str:
        return self._wrap("32", s)

    def yellow(self, s: str) -> str:
        return self._wrap("33", s)

    def red(self, s: str) -> str:
        return self._wrap("31", s)

    def cyan(self, s: str) -> str:
        return self._wrap("36", s)

    def bold(self, s: str) -> str:
        return self._wrap("1", s)


def _try_reflex(intent: ParsedIntent, journal: Journal | None) -> ReflexMatch | None:
    router = _build_reflex_router()
    result = router.try_match(intent)
    if not isinstance(result, ReflexMatch):
        return None
    if journal is not None:
        with contextlib.suppress(Exception):
            journal.write_reflex_hit(
                rule_id=result.rule_id,
                kind=result.kind,
                latency_ms=result.latency_ms,
                intent_goal=intent.normalized_goal,
                response=_safe_repr(result.response),
            )
    return result


def _build_reflex_router() -> ReflexRouter:
    defaults = [
        RegexMatcher(
            rule_id="greeting_zh",
            pattern=r"^(你好|您好|嗨|哈喽)[!。?\.\?\!,~\u3002\uff01\uff1f]*$",
            response={"reply": "你好 👋 我是 Echo,有什么可以帮你的?"},
            priority=20,
        ),
        RegexMatcher(
            rule_id="greeting_en",
            pattern=r"^(hi|hello|hey|yo)[!\.\?,]*$",
            response={"reply": "Hi 👋 I'm Echo. What can I help you with?"},
            priority=20,
        ),
        RegexMatcher(
            rule_id="thanks_zh",
            pattern=r"^(谢谢|多谢|感谢)[!。?\.\?\!,~\u3002\uff01\uff1f]*$",
            response={"reply": "不客气,有需要随时叫我 🙂"},
            priority=20,
        ),
        RegexMatcher(
            rule_id="bye",
            pattern=r"^(再见|拜拜|bye|byebye|goodbye)[!\.\?,]*$",
            response={"reply": "再见 👋 想我了再来"},
            priority=20,
        ),
        RegexMatcher(
            rule_id="ping_diagnostic",
            pattern=r"^ping$",
            response={"reply": "pong"},
            priority=20,
        ),
        DeterministicMatcher(
            rule_id="chitchat_default",
            intent_type="chitchat",
            response={"reply": "嗯,我在。说说看你想做点啥?"},
            priority=10,
        ),
        CacheMatcher(rule_id="semantic_cache", ttl_seconds=60 * 60, priority=5),
    ]
    try:
        from runtime.core.nerves.reflex.rules_loader import (
            find_default_rules_file,
            load_rules_from_file,
            merge_with_defaults,
        )

        path = find_default_rules_file()
        file_rules = load_rules_from_file(path) if path else []
        merged = merge_with_defaults(defaults, file_rules)
    except Exception as exc:
        _logger.debug("reflex rules load failed: %s", exc)
        merged = defaults
    return ReflexRouter(merged)


DEFAULT_RULES = [
    Rule(
        name="swarm_probe",
        intent_types=["query", "task"],
        keywords=["swarm", "并发", "parallel", "多腕"],
        skill_sequence=[
            SkillId("list_cwd"),
            SkillId("count_words"),
            SkillId("hash_text"),
        ],
        node_args_templates=[
            {"path": "."},
            {"text": "hello world demo"},
            {"text": "parallel processing"},
        ],
        priority=20,
    ),
    Rule(
        name="file_probe",
        intent_types=["query", "task"],
        keywords=["file", "read", "contents", "words", "list", "目录", "文件"],
        skill_sequence=[
            SkillId("list_cwd"),
            SkillId("count_words"),
            SkillId("hash_text"),
        ],
        node_args_templates=[
            None,
            {"text": "{n0.path}"},
            {"text": "{n0.path}"},
        ],
        priority=10,
    ),
    Rule(
        name="hash_probe",
        keywords=["hash", "digest", "fingerprint"],
        skill_sequence=[SkillId("hash_text")],
        priority=5,
    ),
    Rule(
        name="default_list",
        intent_types=["query", "task"],
        skill_sequence=[SkillId("list_cwd")],
        priority=0,
    ),
]


def _build_stack(
    *,
    planner_type: str = "static",
    planner_model: str = "mock/planner",
    mock_response: str | None = None,
    anthropic_api_key: str | None = None,
    allow_untrusted: bool = False,
    trusted_sources: list[str] | None = None,
    journal: Journal | None = None,
):
    from runtime.core.cerebrum import LLMPlanner, StaticPlanner
    from runtime.execution.suckers.builtins import register_all

    registry = SkillRegistry()
    # Use register_all so write/edit/git/web skills are available to the
    # headless CLI. Plain register_builtins only gives list_cwd/read_file/
    # count_words/hash_text — the agent can read but never write, which
    # silently breaks SWE-bench and any autonomous code task.
    register_all(registry)

    immunity = TrustEngine(
        trusted_sources=trusted_sources or ["skill://public/*"],
        unknown_policy="allow" if allow_untrusted else "quarantine",
    )
    if journal is None:
        journal = InMemoryJournal()
    executor = ToolExecutor(registry=registry, immunity=immunity, journal=journal)

    if planner_type == "llm":
        router = _make_router(planner_model, mock_response, anthropic_api_key)
        from runtime.core.hearts.gill_pump import GillCache, retrieval_gill_enabled

        composer = ContextComposer(
            registry=registry,
            journal=journal,
            gill_cache=GillCache() if retrieval_gill_enabled() else None,
        )
        planner = LLMPlanner(
            router=router,
            registry=registry,
            composer=composer,
            planner_model=planner_model,
            default_budget=BudgetSpec(tokens=50_000, usd=0.50),
        )
    else:
        planner = StaticPlanner(
            rules=DEFAULT_RULES,
            default_budget=BudgetSpec(tokens=50_000, usd=0.50),
            fallback_skill=SkillId("list_cwd"),
        )

    return planner, executor, journal


def _build_arm_pool(runtime, signal_bus=None) -> ArmPool:
    return ArmPool(
        [
            Arm(
                arm_id=ArmId("code_arm"),
                affinity=["code", "file"],
                allowed_skills=[
                    SkillId("list_cwd"),
                    SkillId("read_file"),
                    SkillId("file_stats"),
                ],
                runtime=runtime,
                signal_bus=signal_bus,
            ),
            Arm(
                arm_id=ArmId("text_arm"),
                affinity=["text", "crypto"],
                allowed_skills=[
                    SkillId("count_words"),
                    SkillId("hash_text"),
                ],
                runtime=runtime,
                signal_bus=signal_bus,
            ),
            Arm(
                arm_id=ArmId("generic_arm"),
                affinity=["general"],
                allowed_skills=[
                    SkillId("list_cwd"),
                    SkillId("read_file"),
                    SkillId("count_words"),
                    SkillId("hash_text"),
                    SkillId("file_stats"),
                ],
                runtime=runtime,
                signal_bus=signal_bus,
            ),
        ]
    )


def _make_router(model: str, mock_response: str | None, api_key: str | None) -> ModelRouter:
    if model.startswith("mock/") or mock_response is not None:
        return MockModelRouter(response=mock_response or _default_mock_plan())

    # If OPENAI_BASE_URL is set, use the OpenAI-compatible router (Kimi, DeepSeek, etc.)
    openai_base_url = os.environ.get("OPENAI_BASE_URL")
    if openai_base_url:
        from runtime.sensing.model_router.openai_router import OpenAIModelRouter

        return OpenAIModelRouter(
            base_url=openai_base_url,
            api_key=os.environ.get("OPENAI_API_KEY"),
            default_model=model,
        )

    from runtime.sensing.model_router.anthropic_router import AnthropicModelRouter

    return AnthropicModelRouter(api_key=api_key, default_model=model)


def _default_mock_plan() -> str:
    return (
        '{"reasoning": "mock default", '
        '"nodes": ['
        '{"skill": "list_cwd", "args": {"path": "."}},'
        '{"skill": "count_words", "args": {"text": "{n0.path}"}}'
        "]}"
    )


def _slug_query(q: str, max_len: int = 20) -> str:
    keep = "".join(c if c.isalnum() else "_" for c in q)
    return keep[:max_len].strip("_") or "q"


def _graph_has_template_deps(graph) -> bool:
    import re

    pattern = re.compile(r"\{n\d+(\.\w+)*\}")
    for node in graph.nodes:
        for value in (node.args_template or {}).values():
            if isinstance(value, str) and pattern.search(value):
                return True
    return False


def _speedup_estimate(result) -> float:
    from runtime.execution.swarm import SwarmResult

    if not isinstance(result, SwarmResult):
        return 1.0
    if result.total_wall_ms <= 0:
        return 1.0
    serial_ms = sum(ar.cost.latency_ms for ar in result.arm_results)
    if serial_ms <= 0:
        return 1.0
    return serial_ms / result.total_wall_ms


def _short_output(output: Any) -> str:
    if output is None:
        return "\u2205"
    text = repr(output)
    if len(text) > 60:
        return text[:57] + "..."
    return text


def _export_winning_variants(journal_path: str, out_dir: str) -> int:
    import json
    import os

    results = []
    try:
        with open(journal_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                e = json.loads(line)
                if e.get("event") == "variant_win":
                    results.append(e)
    except Exception:  # noqa: BLE001 — journal scan best-effort; empty results triggers fallback
        pass
    if not results:
        return 0
    os.makedirs(out_dir, exist_ok=True)
    count = 0
    for r in results:
        pid = r.get("prompt_id", f"unknown_{count}")
        path = os.path.join(out_dir, f"{pid}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=2)
        count += 1
    return count


def print_cost_breakdown(steps: list, budget, c: _Colors | None = None) -> None:
    if c is None:
        c = _Colors()
    if not steps:
        return
    total_tokens = sum(s.result.cost.tokens for s in steps)
    total_usd = sum(s.result.cost.usd for s in steps)
    total_ms = sum(s.result.cost.latency_ms for s in steps)
    print()
    print(c.bold("─" * 40 + " COST BREAKDOWN " + "─" * 40))
    for i, s in enumerate(steps):
        skill = str(s.action.sucker_id)
        args = s.args_template or s.action.args
        ok = s.success
        status = c.green("\u2713") if ok else c.red("\u2717")
        cost_info = f"{s.result.cost.tokens} tok \u00b7 ${s.result.cost.usd:.4f} \u00b7 {s.result.cost.latency_ms:.1f} ms"
        print(f"  [{i}] {status} {skill}({_short_output(args)}) {c.dim(cost_info)}")
    print(
        c.bold(
            "\u2500" * 40
            + "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
            + "\u2500" * 40
        )
    )
    budget_max_tokens = getattr(budget, "tokens", None)
    if budget_max_tokens is None and hasattr(budget, "limits"):
        budget_max_tokens = getattr(budget.limits, "tokens", 0)
    if budget_max_tokens is None:
        budget_max_tokens = 0
    over_tokens = total_tokens - budget_max_tokens
    safe_max = budget_max_tokens if budget_max_tokens > 0 else total_tokens + 1
    limit_pct = (
        " {}".format(c.green("OK"))
        if total_tokens <= safe_max
        else " {}".format(c.red(f"OVER by {over_tokens} tokens"))
    )
    print(
        "  {} {} {} tok \u00b7 ${:.4f} \u00b7 {:.1f} ms{}".format(
            c.bold("Budget used:"), c.bold("TOTAL"), total_tokens, total_usd, total_ms, limit_pct
        )
    )
