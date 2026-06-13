"""Octopus OS appliance 单用户认证引导。

复用 runtime 已测试的 local_auth(bcrypt 口令 + HS256 JWT),本模块只负责
appliance 形态特有的"首启设密码"环节,不自写任何 crypto:

- 首次启动:从环境变量 OCTOPUS_ADMIN_PASSWORD 取管理员密码;未提供则随机
  生成并在日志中醒目打印(类似 NAS 面板首启给出初始密码)。
- 把密码哈希与随机 jwt_secret 持久化到 data 目录(0600),据此构造
  adapters LocalAuthConfig(单 admin 用户 + 长会话),交给现成的
  create_local_auth_router 执行登录/签发 JWT。

单用户:NAS 是个人设备,固定用户名 admin。
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import secrets
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ADMIN_USERNAME = "admin"
SESSION_SECONDS = 30 * 24 * 3600  # 30 天长会话(NAS 常驻,少打扰)


def _store_path() -> Path:
    base = (
        os.environ.get("OCTOPUS_DATA_DIR")
        or os.environ.get("OCTOPUS_DATA")
        or "."
    )
    return Path(base) / "appliance-auth.json"


def load_or_bootstrap_auth() -> tuple[Any, str | None]:
    """返回 (LocalAuthConfig, generated_password)。

    generated_password 仅在本次随机生成新密码时非空(供调用方打印一次)。
    已存在凭据存储时直接读取,返回 (config, None)。
    """
    from runtime.adapters.integrations.local_auth.config import (
        LocalAuthConfig,
        hash_password,
    )

    path = _store_path()

    if path.exists():
        data = json.loads(path.read_text())
        config = LocalAuthConfig(
            enabled=True,
            allow_any_username=False,
            users={ADMIN_USERNAME: data["password_hash"]},
            jwt_secret=data["jwt_secret"],
            jwt_expire_seconds=SESSION_SECONDS,
        )
        return config, None

    env_password = os.environ.get("OCTOPUS_ADMIN_PASSWORD", "").strip()
    generated: str | None = None
    if env_password:
        password = env_password
    else:
        password = secrets.token_urlsafe(12)
        generated = password

    password_hash = hash_password(password)
    jwt_secret = secrets.token_urlsafe(48)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "username": ADMIN_USERNAME,
                "password_hash": password_hash,
                "jwt_secret": jwt_secret,
            }
        )
    )
    with contextlib.suppress(OSError):
        path.chmod(0o600)

    config = LocalAuthConfig(
        enabled=True,
        allow_any_username=False,
        users={ADMIN_USERNAME: password_hash},
        jwt_secret=jwt_secret,
        jwt_expire_seconds=SESSION_SECONDS,
    )
    return config, generated
