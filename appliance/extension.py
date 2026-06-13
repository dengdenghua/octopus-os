"""Octopus OS appliance —— 以 agent 官方扩展 API 挂载,不再 fork app.py。

经 ``OCTOPUS_APP_EXTENSIONS=appliance.extension`` 在 agent 组装末尾被调用,
把 NAS 桌面启动器(单用户认证 + 应用注册器 + 文件管理器)挂到 FastAPI app 上。
这是「消费而非 fork」的落地:appliance 逻辑住在 os 自己的包里,与母体 app.py
零冲突(见 docs/OS_DIFFERENTIATION.md P0)。

语义与原 fork 块一致:仅 ``OCTOPUS_APPLIANCE=1`` 时挂载,否则 no-op。
"""

from __future__ import annotations

import logging
import os
from typing import Any

_alog = logging.getLogger("octopus.appliance")


def register_app(app: Any, context: Any) -> None:
    """agent 扩展入口:挂载 appliance 路由。context.identity_store 来自 create_app。"""
    if os.environ.get("OCTOPUS_APPLIANCE") != "1":
        return

    from appliance.app_registry.router import create_appliance_router
    from appliance.auth import ADMIN_USERNAME, load_or_bootstrap_auth
    from runtime.adapters.integrations.local_auth import create_local_auth_router

    auth_cfg, generated_pw = load_or_bootstrap_auth()
    if generated_pw:
        _alog.warning(
            "appliance admin password generated (set OCTOPUS_ADMIN_PASSWORD to "
            "choose your own): username=%s password=%s",
            ADMIN_USERNAME,
            generated_pw,
        )
    # 登录端点(/api/auth/local/login,签发长会话 JWT)+ 受保护的启动器接口。
    app.include_router(
        create_local_auth_router(
            config=auth_cfg, identity_store=context.identity_store
        )
    )
    app.include_router(create_appliance_router(jwt_secret=auth_cfg.jwt_secret))

    # NAS 文件管理器(回收站语义)。root 默认对齐 compose 的存储挂载点;
    # 路径不可用时跳过挂载,不影响其余功能。
    nas_root = os.environ.get("OCTOPUS_NAS_ROOT") or os.path.join(
        os.environ.get("OCTOPUS_DATA_DIR", "/data"), "nas"
    )
    try:
        from appliance.files import FileManager, create_files_router

        app.include_router(
            create_files_router(FileManager(nas_root), jwt_secret=auth_cfg.jwt_secret)
        )
    except OSError as fs_exc:
        _alog.warning("NAS file manager not mounted (%s): %s", nas_root, fs_exc)
