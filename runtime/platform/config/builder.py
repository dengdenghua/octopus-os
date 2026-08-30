"""
Build an agent runtime stack from ``AgentConfig``.

Returns a ``BuiltStack`` containing the planner, executor, registry,
journal, graph runtime, and related runtime services.
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from runtime.adapters.mcp_client import MCPClient
    from runtime.platform.config.schema import MCPServerConfigEntry

from runtime.core.cerebrum import LLMPlanner, StaticPlanner
from runtime.core.cerebrum.planner import Rule
from runtime.core.graph_runtime import GraphRuntime
from runtime.execution.suckers import SkillRegistry
from runtime.execution.tool_engine import ToolExecutor
from runtime.execution.tool_engine.effect_store import EffectStore, SQLiteEffectStore
from runtime.memory.hemolymph import ContextComposer
from runtime.memory.journal import InMemoryJournal, Journal, JSONLJournal
from runtime.platform.models import BudgetSpec, SkillId
from runtime.platform.observability.redactor import Redactor
from runtime.safety.auth import TrustEngine

from .schema import AgentConfig

Planner = StaticPlanner | LLMPlanner


@runtime_checkable
class StackProtocol(Protocol):
    """Minimal interface that all stack consumers depend on.

    Replaces ``stack: Any`` throughout the codebase so that IDE
    auto-complete and mypy can verify field access.
    """

    config: AgentConfig
    registry: SkillRegistry
    journal: Journal
    immunity: TrustEngine
    executor: ToolExecutor
    runtime: GraphRuntime
    planner: Planner
    mcp_clients: list[Any]

    @property
    def is_llm_planner(self) -> bool: ...

    def close_mcp_clients(self) -> None: ...


@dataclass
class BuiltStack:
    """Complete agent runtime stack for callers to use directly."""

    config: AgentConfig
    registry: SkillRegistry
    journal: Journal
    immunity: TrustEngine
    executor: ToolExecutor
    runtime: GraphRuntime
    planner: Planner
    mcp_clients: list[Any] = field(default_factory=list)

    @property
    def is_llm_planner(self) -> bool:
        return isinstance(self.planner, LLMPlanner)

    def close_mcp_clients(self) -> None:
        """graceful shutdown · 逐个 close 长连 MCP client。"""
        for c in self.mcp_clients:
            with contextlib.suppress(Exception):
                c.close()
        self.mcp_clients.clear()


def _in_memory_journal_cap(max_bytes: int | None) -> int:
    """Approximate event cap for the in-memory ring journal (audit R-04).

    The JSONL journal bounds by bytes; the in-memory journal bounds by event
    count. Assume ~1KiB per event (a conservative average for tool args /
    steps), so the default 50MB maps to ~50k events. ``None``/``<=0`` keeps
    the journal unbounded (explicit opt-out).
    """
    if max_bytes is None or max_bytes <= 0:
        return 0
    return max(1_000, int(max_bytes) // 1024)


def build_from_config(config: AgentConfig) -> BuiltStack:
    """Assemble the complete runtime stack from ``AgentConfig``."""
    # 1. SkillRegistry: always retain the complete local coding surface.
    #    ``enable_web_skills`` controls external-web capability only; it must
    #    not silently remove filesystem writes, shell, git or test tools.
    registry = SkillRegistry()
    from runtime.execution.all_skills import register_all, register_local

    if config.enable_web_skills:
        register_all(registry)
    else:
        register_local(registry)
    try:
        from runtime.safety.evolution.runtime_deployment import (
            load_governed_candidate_skills,
        )

        load_governed_candidate_skills(registry)
    except Exception:  # noqa: BLE001 - governed candidates fail closed
        pass

    # 2. MCP servers: connect configured servers one by one. Each server gets
    #    a persistent client to avoid spawning a new process per tool call.
    mcp_clients: list[Any] = []
    for srv in config.mcp_servers:
        client = _register_mcp_server(registry, srv)
        if client is not None:
            mcp_clients.append(client)

    # 3. Journal
    # Default-on secret redaction: the journal is the source-of-truth audit log
    # and records tool args/outputs, so run every payload through the redactor
    # before persistence to keep accidental secrets (.env values, keys) off disk.
    journal: Journal
    journal = (
        JSONLJournal(
            config.journal_file,
            max_size_bytes=config.journal_max_bytes,
            redactor=Redactor(),
        )
        if config.journal_file
        else InMemoryJournal(max_events=_in_memory_journal_cap(config.journal_max_bytes))
    )

    # 4. Immunity
    from runtime.safety.auth.attack_memory import AttackMemory

    adaptive = None
    if config.immunity.enable_adaptive:
        from runtime.safety.auth.adaptive_immunity import AdaptiveImmunity

        adaptive = AdaptiveImmunity(
            window_size=config.immunity.adaptive_window_size,
            quarantine_threshold=config.immunity.adaptive_quarantine_threshold,
        )

    immunity = TrustEngine(
        trusted_sources=list(config.immunity.trusted_sources),
        self_whitelist=list(config.immunity.self_whitelist),
        unknown_policy=config.immunity.unknown_policy,
        attack_memory=AttackMemory(
            config.immunity.attack_memory_path,
            threshold=config.immunity.attack_threshold,
            window_s=float(config.immunity.attack_window_seconds),
        ),
        adaptive=adaptive,
    )

    # 5. ToolExecutor
    effect_store = _build_effect_store(config)
    executor = ToolExecutor(
        registry=registry,
        immunity=immunity,
        journal=journal,
        effect_store=effect_store,
    )

    # 6. GraphRuntime
    runtime = GraphRuntime(executor=executor, journal=journal)

    # 7. Planner
    planner = _build_planner(config, registry, journal)

    # 8. Optional learning preload: rules and memory patterns.
    if config.learn.learn_from_journal and isinstance(planner, LLMPlanner):
        learn_path = Path(config.learn.learn_from_journal)
        if learn_path.exists():
            learn_journal = JSONLJournal(learn_path)
            planner.learn_from_journal(
                learn_journal,
                min_hits=config.learn.min_hits,
                max_rules=config.learn.max_rules,
            )
    if config.learn.learn_memories_from_journal and isinstance(planner, LLMPlanner):
        mem_path = Path(config.learn.learn_memories_from_journal)
        if mem_path.exists():
            mem_journal = JSONLJournal(mem_path)
            planner.learn_memories_from_journal(mem_journal)
    if config.learn.learn_kg_from_journal and isinstance(planner, LLMPlanner):
        kg_path = Path(config.learn.learn_kg_from_journal)
        if kg_path.exists():
            kg_journal = JSONLJournal(kg_path)
            planner.kg_max_triples = config.learn.kg_max_triples
            # Durable KG: accumulate learned facts into an on-disk store beside
            # the journal so they survive restarts and compound across sessions,
            # rather than being rebuilt in-memory and lost each process.
            planner.enable_persistent_kg(kg_path.parent / "planner_kg.db")
            planner.learn_kg_from_journal(kg_journal)

    # 9. WorkflowRewriter auto-rewrite for the static planner.
    if config.learn.rewrite_from_journal and isinstance(planner, StaticPlanner):
        rw_path = Path(config.learn.rewrite_from_journal)
        if rw_path.exists():
            rw_journal = JSONLJournal(rw_path)
            planner.rewrite_from_journal(
                rw_journal,
                min_confidence=config.learn.rewrite_min_confidence,
                min_severity=config.learn.rewrite_min_severity,
            )

    # 10. RecipeEvaluator è‡ªçœï¼ˆä»… LLM planner å¯ç”¨ï¼‰
    if config.learn.assess_recipe_from_journal and isinstance(planner, LLMPlanner):
        ar_path = Path(config.learn.assess_recipe_from_journal)
        if ar_path.exists():
            ar_journal = JSONLJournal(ar_path)
            planner.assess_recipe_from_journal(ar_journal)

    return BuiltStack(
        config=config,
        registry=registry,
        journal=journal,
        immunity=immunity,
        executor=executor,
        runtime=runtime,
        planner=planner,
        mcp_clients=mcp_clients,
    )


def _build_effect_store(config: AgentConfig) -> EffectStore | None:
    settings = config.tool_effects
    if settings.backend == "auto":
        # UI/server startup attaches the app-local SQLite path. Pure library
        # callers remain in-memory unless they opt into an explicit backend.
        return None
    if settings.backend == "sqlite":
        assert settings.sqlite_path is not None
        return SQLiteEffectStore(settings.sqlite_path)
    from runtime.execution.tool_engine.redis_effect_store import RedisEffectStore

    assert settings.redis_url is not None
    return RedisEffectStore.from_url(
        settings.redis_url,
        key_prefix=settings.key_prefix,
        connect_timeout_s=settings.connect_timeout_seconds,
    )


def _build_planner(
    config: AgentConfig,
    registry: SkillRegistry,
    journal: Journal,
) -> Planner:
    """æŒ‰ config.planner.type åˆ†æ”¯ã€‚"""
    p = config.planner
    default_budget = BudgetSpec(
        tokens=config.budget.max_tokens,
        usd=config.budget.max_usd,
        latency_ms=config.budget.max_latency_ms,
    )

    if p.type == "llm":
        from runtime.sensing.model_router import MockModelRouter, ModelRouter

        router: ModelRouter
        if p.model.startswith("mock/") or p.mock_response is not None:
            router = MockModelRouter(response=p.mock_response)
        elif (
            (getattr(config, "oct", None) and config.oct.enabled)
            and not p.anthropic_api_key
            and not os.environ.get("ANTHROPIC_API_KEY")
            and not os.environ.get("ANTHROPIC_AUTH_TOKEN")
        ):
            from runtime.sensing.model_router.dispatch_router import ModelDispatchRouter
            from runtime.sensing.model_router.models import UnconfiguredModelRouter
            from runtime.sensing.model_router.openai_router import (
                build_fallback_router_from_custom_models,
            )

            # Self-configured model (custom_models.json) is the fallback so
            # unresolved / guest requests run a real model. When none is
            # configured, a clear "no model configured" error beats the old
            # account fallback's confusing login gate.
            self_fallback = build_fallback_router_from_custom_models(p.model)
            router = ModelDispatchRouter(fallback=self_fallback or UnconfiguredModelRouter())
        else:
            from runtime.sensing.model_router.anthropic_router import AnthropicModelRouter
            from runtime.sensing.model_router.dispatch_router import ModelDispatchRouter
            from runtime.sensing.model_router.models import UnconfiguredModelRouter
            from runtime.sensing.model_router.openai_router import (
                build_fallback_router_from_custom_models,
            )

            # The packaged desktop app ships a keyless config on purpose. If no
            # anthropic key is configured, fall back to a self-configured model
            # (custom_models.json) or a clear "no model configured" router so the
            # backend still boots offline — the user adds a key from the UI later.
            _has_anthropic_key = bool(
                p.anthropic_api_key
                or os.environ.get("ANTHROPIC_API_KEY")
                or os.environ.get("ANTHROPIC_AUTH_TOKEN")
            )
            if not _has_anthropic_key:
                self_fallback = build_fallback_router_from_custom_models(p.model)
                router = ModelDispatchRouter(fallback=self_fallback or UnconfiguredModelRouter())
            else:
                # Wrap the anthropic router in a dispatcher so
                # ``config_router._register`` can attach user-defined model
                # aliases (``claude-mirror`` -> ``claude-sonnet-4-6``) on top.
                # Without this wrap the alias hits the upstream proxy
                # untranslated and gets a 403 "unsupported model type", which is
                # exactly the bug the smoke logs surfaced.
                _base_router = AnthropicModelRouter(
                    api_key=p.anthropic_api_key,
                    default_model=p.model,
                )
                router = ModelDispatchRouter(fallback=_base_router)
        # Keep execution engine and model source independent.  The dispatcher
        # prefix makes ``chatgpt/<model>`` use the principal's ChatGPT login
        # while the surrounding LLMPlanner/ReAct loop remains Echo-native.
        # Registration itself is keyless and side-effect free; credentials are
        # resolved only when that route is actually selected.
        from runtime.sensing.model_router.chatgpt_subscription_router import (
            ChatGPTSubscriptionModelRouter,
        )
        from runtime.sensing.model_router.dispatch_router import ModelDispatchRouter

        if isinstance(router, ModelDispatchRouter):
            router.register("chatgpt", ChatGPTSubscriptionModelRouter())
        from runtime.core.hearts.gill_pump import GillCache, retrieval_gill_enabled

        gill_cache = None
        if retrieval_gill_enabled():
            gill_cache = GillCache()
        composer = ContextComposer(
            registry=registry,
            journal=journal,
            gill_cache=gill_cache,
        )
        return LLMPlanner(
            router=router,
            registry=registry,
            composer=composer,
            planner_model=p.model,
            default_budget=default_budget,
            max_nodes=p.max_nodes,
            auto_persist_rules_path=config.learn.rules_persist_path,
            auto_persist_memories_path=config.learn.memories_persist_path,
        )

    # static
    return StaticPlanner(
        rules=_default_static_rules(enable_web_skills=config.enable_web_skills),
        default_budget=default_budget,
        fallback_skill=SkillId("list_cwd"),
        auto_persist_rules_path=config.learn.static_rules_persist_path,
    )


def _default_static_rules(*, enable_web_skills: bool) -> list[Rule]:
    """Small safe rule set for StaticPlanner fallback mode."""
    rules: list[Rule] = []
    if enable_web_skills:
        rules.append(
            Rule(
                name="research_web_search",
                intent_types=["query", "task"],
                keywords=[
                    "research",
                    "survey",
                    "search",
                    "lookup",
                    "latest",
                    "news",
                    "调研",
                    "研究",
                    "搜索",
                    "查找",
                    "查询",
                    "资料",
                    "信息",
                    "最新",
                    "新闻",
                ],
                skill_sequence=[SkillId("web_search")],
                node_args_templates=[
                    {"query": "{intent_goal}", "max_results": 5},
                ],
                priority=30,
            )
        )
    rules.append(
        Rule(
            name="default_list",
            intent_types=["query", "task"],
            skill_sequence=[SkillId("list_cwd")],
            priority=0,
        )
    )
    return rules


def _register_mcp_server(
    registry: SkillRegistry,
    entry: MCPServerConfigEntry,
) -> MCPClient | None:
    """Register one MCP server from config and expose its tools as skills.

    Picks the transport from ``entry.transport`` (or an ``entry.url``):

    * ``stdio`` → ``PersistentStdioMCPClient`` (local subprocess server;
      persistent so it isn't re-spawned per skill call, which can exhaust
      file descriptors and PIDs in long-running sessions);
    * ``http`` / ``sse`` → ``HttpMCPClient`` (remote/hosted server — most of
      the public MCP ecosystem: GitHub, Slack, Linear, Notion, ...).

    Returns the client bound to the registered skill handlers. Callers are
    responsible for closing it during process teardown. Failures or a missing
    SDK return ``None``.
    """
    from runtime.adapters.mcp_client import (
        HttpMCPClient,
        MCPClientError,
        MCPServerConfig,
        PersistentStdioMCPClient,
        register_mcp_tools_as_skills,
    )
    from runtime.adapters.mcp_client.client import HTTP_AVAILABLE, STDIO_AVAILABLE

    is_remote = entry.transport in ("http", "sse") or bool(entry.url)
    if (is_remote and not HTTP_AVAILABLE) or (not is_remote and not STDIO_AVAILABLE):
        # MCP SDK is not installed; skip instead of crashing.
        return None

    config = MCPServerConfig(
        name=entry.name,
        command=entry.command,
        args=list(entry.args),
        env=dict(entry.env),
        transport=entry.transport,
        url=entry.url,
        headers=dict(entry.headers),
        sandbox_dir=entry.sandbox_dir,
    )
    client: MCPClient
    try:
        client = HttpMCPClient(config) if is_remote else PersistentStdioMCPClient(config)
    except MCPClientError:
        return None
    except OSError:
        return None
    try:
        register_mcp_tools_as_skills(registry, client, name_prefix=entry.name_prefix)
    except (OSError, TypeError, ValueError):
        # Release the client (subprocess / connection) when registration fails.
        # ``IOError`` is an alias of ``OSError`` on supported Python
        # versions; passing a tuple as one suppress argument is both redundant
        # and incorrectly typed (contextlib expects exception classes as
        # separate positional arguments).
        with contextlib.suppress(OSError):
            client.close()
        return None
    return client
