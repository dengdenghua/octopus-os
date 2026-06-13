"""Octopus OS · P2 同机 agent webui 投喂:后端 serve 独立的 agent 工作台 UI。

去 fork 方向:os 前端不再打包 agent 工作台,而由 os 后端在子路径 ``/agent-ui/``
serve 一份**独立构建**的 agent webui(os 镜像顺带构建 agent 前端,见
docs/P2_FRONTEND_DEFORK_PLAN.md);os 桌面用窗口 iframe 加载它。

- ``OCTOPUS_AGENT_WEBUI_DIST`` 指向 agent webui 构建产物目录(含 index.html)。
  未设/无效 → 不挂载,``/api/appliance/config`` 回 ``agent_workspace_url: null``,
  前端回退到同源工作台路由(过渡态:os 仍自带工作台前端)。
- agent webui 必须以 ``base=/agent-ui/`` 构建,使其 assets 落在 ``/agent-ui/assets/``,
  与 os 自身 webui 的 ``/assets/`` 不冲突。工作台是 hash 路由,故无需服务端 SPA 兜底。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.staticfiles import StaticFiles

_log = logging.getLogger("octopus.appliance")

_MOUNT_PREFIX = "/agent-ui"
# 工作台入口(hash 路由进 agent SPA);与前端 DEFAULT_WORKSPACE_PATH 对应。
_WORKSPACE_HASH = "#/workspace/realtime/new"


def _resolve_dist() -> Path | None:
    env_path = os.environ.get("OCTOPUS_AGENT_WEBUI_DIST")
    if not env_path:
        return None
    p = Path(env_path)
    if p.is_dir() and (p / "index.html").is_file():
        return p
    _log.warning("OCTOPUS_AGENT_WEBUI_DIST 无效(目录或 index.html 缺失):%s", env_path)
    return None


def agent_ui_base() -> str | None:
    """已投喂 agent webui 时返回其挂载前缀(/agent-ui/),否则 None。

    前端据此为**任意** agent 路由拼窗口 URL:base ? `${base}#${route}` : route。
    """
    if _resolve_dist() is None:
        return None
    return f"{_MOUNT_PREFIX}/"


def agent_workspace_url() -> str | None:
    """已投喂 agent webui 时返回工作台首入口 URL,否则 None(同源回退)。"""
    if _resolve_dist() is None:
        return None
    return f"{_MOUNT_PREFIX}/{_WORKSPACE_HASH}"


def mount_agent_ui(app: Any) -> bool:
    """把独立 agent webui 挂到 /agent-ui/(若已投喂)。返回是否挂载。

    同时注册公开端点 ``/api/appliance/config``,把 agent_workspace_url 暴露给前端
    (前端据此决定窗口加载同源工作台还是 /agent-ui/ 的外部 webui)。
    """
    dist = _resolve_dist()

    # config 端点始终注册(回 null 表示未投喂),前端无脑读即可。
    router = APIRouter()

    @router.get("/api/appliance/config", include_in_schema=False)
    def _appliance_config() -> dict[str, Any]:
        return {
            "agent_workspace_url": agent_workspace_url(),
            "agent_ui_base": agent_ui_base(),
        }

    app.include_router(router)

    if dist is None:
        return False

    app.mount(
        _MOUNT_PREFIX,
        StaticFiles(directory=str(dist), html=True),
        name="agent-ui",
    )
    _log.info("agent webui 已投喂并挂载于 %s/(dist=%s)", _MOUNT_PREFIX, dist)
    return True
