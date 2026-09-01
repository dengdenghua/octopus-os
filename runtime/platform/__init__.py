"""runtime.platform · 平台横切设施（Platform Infrastructure）

子包速查：
  budget         → 预算跟踪与限制
  config         → 配置加载与校验
  credentials    → 凭证管理
  i18n           → 国际化与本地化
  io             → 文件 I/O + atomic write
  models         → Pydantic 数据模型
  prompts        → prompt 资产管理
  ui             → Web UI 后端 routes

  process/       → 进程级状态与生命周期（session / state / scope / paths /
                   utils / eventbus / distributed_lock / streaming /
                   service_provider / event_bridge / turn_model /
                   session_executor）
  observability/ → 可观测性（metrics / health / logging_config /
                   structured_logging / redactor / doctor）
  plugins/       → 插件系统（plugin_base / plugin_compat / plugin_hub /
                   plugin_loader / plugins / skill_market）
  lifecycle/     → 部署与迁移（backup / data_migration / factory_reset /
                   setup_wizard / demo）
  llm_infra/     → LLM 基础设施（llm_cache / llm_caller / budget_tracker）
  runtime_policy/→ 运行时策略（browser_sessions / capabilities /
                   feature_flags / idempotency / identity_filter / retry /
                   workspaces）
"""

from __future__ import annotations

_LAZY_ATTRS = {
    "DomainEvent": ("runtime.platform.process.eventbus", "DomainEvent"),
    "EventBus": ("runtime.platform.process.eventbus", "EventBus"),
    "PluginLoader": ("runtime.platform.plugins.plugin_loader", "PluginLoader"),
    "StateStore": ("runtime.platform.process.state", "StateStore"),
    "get_eventbus": ("runtime.platform.process.eventbus", "get_eventbus"),
    "get_plugin_loader": ("runtime.platform.plugins.plugin_loader", "get_plugin_loader"),
    "get_statestore": ("runtime.platform.process.state", "get_statestore"),
}

_LAZY_MODULES = {
    # Backward-compat shims for code that does `from runtime.platform import X`
    # where X is a submodule name.
    "backup": "runtime.platform.lifecycle.backup",
    "data_migration": "runtime.platform.lifecycle.data_migration",
    "demo": "runtime.platform.lifecycle.demo",
    "factory_reset": "runtime.platform.lifecycle.factory_reset",
    "setup_wizard": "runtime.platform.lifecycle.setup_wizard",
    "budget_tracker": "runtime.platform.llm_infra.budget_tracker",
    "llm_cache": "runtime.platform.llm_infra.llm_cache",
    "llm_caller": "runtime.platform.llm_infra.llm_caller",
    "doctor": "runtime.platform.observability.doctor",
    "health": "runtime.platform.observability.health",
    "logging_config": "runtime.platform.observability.logging_config",
    "metrics": "runtime.platform.observability.metrics",
    "redactor": "runtime.platform.observability.redactor",
    "structured_logging": "runtime.platform.observability.structured_logging",
    "plugin_base": "runtime.platform.plugins.plugin_base",
    "plugin_compat": "runtime.platform.plugins.plugin_compat",
    "plugin_hub": "runtime.platform.plugins.plugin_hub",
    "plugin_loader": "runtime.platform.plugins.plugin_loader",
    "plugins": "runtime.platform.plugins.plugins",
    "skill_market": "runtime.platform.plugins.skill_market",
    "distributed_lock": "runtime.platform.process.distributed_lock",
    "event_bridge": "runtime.platform.process.event_bridge",
    "eventbus": "runtime.platform.process.eventbus",
    "paths": "runtime.platform.process.paths",
    "scope": "runtime.platform.process.scope",
    "service_provider": "runtime.platform.process.service_provider",
    "session": "runtime.platform.process.session",
    "session_executor": "runtime.platform.process.session_executor",
    "state": "runtime.platform.process.state",
    "streaming": "runtime.platform.process.streaming",
    "turn_model": "runtime.platform.process.turn_model",
    "utils": "runtime.platform.process.utils",
    "browser_sessions": "runtime.platform.runtime_policy.browser_sessions",
    "capabilities": "runtime.platform.runtime_policy.capabilities",
    "feature_flags": "runtime.platform.runtime_policy.feature_flags",
    "idempotency": "runtime.platform.runtime_policy.idempotency",
    "identity_filter": "runtime.platform.runtime_policy.identity_filter",
    "retry": "runtime.platform.runtime_policy.retry",
    "workspaces": "runtime.platform.runtime_policy.workspaces",
}


def __getattr__(name: str):
    from importlib import import_module

    if name in _LAZY_ATTRS:
        module_name, attr_name = _LAZY_ATTRS[name]
        value = getattr(import_module(module_name), attr_name)
        globals()[name] = value
        return value
    module_name = _LAZY_MODULES.get(name)
    if module_name is not None:
        value = import_module(module_name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "DomainEvent",
    "EventBus",
    "PluginLoader",
    "StateStore",
    "get_eventbus",
    "get_plugin_loader",
    "get_statestore",
    *_LAZY_MODULES,
]
