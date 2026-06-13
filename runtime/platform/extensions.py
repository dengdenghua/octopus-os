"""Octopus 扩展发现 —— 让外部代码在不 fork agent 的前提下扩展它。

消费者(企业版 / octopus-os / mobile 等)通过环境变量声明扩展模块,agent 在
组装时发现并调用,从而**挂自定义路由 / 注册自定义技能**而无需改 agent 源码:

- ``OCTOPUS_APP_EXTENSIONS="mod_a,mod_b:func"``
    逗号分隔;每项的函数(默认 ``register_app``)以 ``(app, context)`` 调用,
    可在 FastAPI app 上挂路由。context 暴露 agent 内部句柄(identity_store 等)。
- ``OCTOPUS_SKILL_EXTENSIONS="mod:func"``
    每项的函数(默认 ``register_skills``)以 ``(registry)`` 调用,返回注册技能数。

未配置 → 全部 no-op,agent 行为不变。单个扩展异常只记日志、不影响其余。
这是「消费而非 fork」生态架构的地基(见 octopus-os/docs/OS_DIFFERENTIATION.md)。
"""

from __future__ import annotations

import importlib
import logging
import os
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

_logger = logging.getLogger(__name__)


@dataclass
class AppExtensionContext:
    """扩展挂路由时可能需要的 agent 内部句柄(按需扩充,保持向后兼容)。"""

    identity_store: Any = None
    stack: Any = None
    agent_registry: Any = None


def _iter_specs(env_value: str, default_func: str) -> Iterator[tuple[str, str]]:
    for entry in (env_value or "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        module_name, _, func_name = entry.partition(":")
        yield module_name.strip(), (func_name.strip() or default_func)


def load_app_extensions(app: Any, context: AppExtensionContext) -> int:
    """发现并运行 OCTOPUS_APP_EXTENSIONS 声明的应用扩展。返回成功数。"""
    count = 0
    for module_name, func_name in _iter_specs(
        os.environ.get("OCTOPUS_APP_EXTENSIONS", ""), "register_app"
    ):
        try:
            module = importlib.import_module(module_name)
            getattr(module, func_name)(app, context)
            count += 1
            _logger.info("app extension loaded: %s:%s", module_name, func_name)
        except Exception as exc:  # noqa: BLE001 — 扩展失败不应拖垮启动
            _logger.warning(
                "app extension %s:%s failed: %s", module_name, func_name, exc
            )
    return count


def load_skill_extensions(registry: Any) -> int:
    """发现并运行 OCTOPUS_SKILL_EXTENSIONS 声明的技能扩展。返回注册技能总数。"""
    total = 0
    for module_name, func_name in _iter_specs(
        os.environ.get("OCTOPUS_SKILL_EXTENSIONS", ""), "register_skills"
    ):
        try:
            module = importlib.import_module(module_name)
            total += int(getattr(module, func_name)(registry) or 0)
            _logger.info("skill extension loaded: %s:%s", module_name, func_name)
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "skill extension %s:%s failed: %s", module_name, func_name, exc
            )
    return total
