"""Echo OS appliance 本地家庭认证引导。

复用 runtime 已测试的 local_auth(bcrypt 口令 + HS256 JWT),本模块只负责
appliance 形态特有的"首启设密码"环节,不自写任何 crypto:

- 首次启动:从环境变量 ECHO_ADMIN_PASSWORD 取管理员密码;未提供则随机
  生成并在日志中醒目打印(类似 NAS 面板首启给出初始密码)。
- 把密码哈希与随机 jwt_secret 持久化到 data 目录(0600),据此构造
  adapters LocalAuthConfig(admin + 已开通的家庭成员 + 长会话),交给现成的
  create_local_auth_router 执行登录/签发 JWT。

``appliance-auth.json`` 仍保留旧版顶层 admin 字段，确保备份、恢复和密码
轮换向后兼容；``accounts`` 只保存 Echo 登录目录和 OMV 用户映射。Echo 不读取
Agent 或 OMV 的私有数据库，也不复用 OMV 的密码哈希。
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import secrets
import stat
from pathlib import Path
from typing import Any

from appliance.state_schema import AUTH_SCHEMA_VERSION_KEY, CURRENT_SCHEMA_VERSION

logger = logging.getLogger(__name__)

ADMIN_USERNAME = "admin"
SESSION_SECONDS = 30 * 24 * 3600  # 30 天长会话(NAS 常驻,少打扰)
LOGIN_MAX_FAILURES = 5
LOGIN_IP_MAX_FAILURES = 20
LOGIN_FAILURE_WINDOW_SECONDS = 5 * 60
LOGIN_LOCKOUT_SECONDS = 60
LOGIN_RATE_LIMIT_MAX_ENTRIES = 10_000
SESSION_NOT_BEFORE_KEY = "session_not_before"
ACCOUNT_SESSION_NOT_BEFORE_KEY = "account_session_not_before"
ACCOUNTS_KEY = "accounts"
MAX_LOCAL_ACCOUNTS = 32
_ACCOUNT_USERNAME = re.compile(r"[a-z][a-z0-9_-]{0,31}")
_ACCOUNT_ROLES = {"admin", "member"}


def _admin_account(password_hash: str) -> dict[str, Any]:
    return {
        "display_name": "管理员",
        "role": "admin",
        "password_hash": password_hash,
        "omv_username": None,
        "active": True,
    }


def normalized_accounts(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return a validated copy of the public-directory-backed login records.

    Legacy stores without ``accounts`` are interpreted as one admin account.
    Callers may then persist the returned mapping as an online, backward-
    compatible migration.
    """

    if payload.get("username") != ADMIN_USERNAME:
        raise ValueError("appliance auth store has the wrong administrator")
    admin_hash = payload.get("password_hash")
    if not isinstance(admin_hash, str) or not admin_hash or len(admin_hash) > 4096:
        raise ValueError("appliance auth store has an invalid administrator hash")

    raw_accounts = payload.get(ACCOUNTS_KEY)
    if raw_accounts is None:
        return {ADMIN_USERNAME: _admin_account(admin_hash)}
    if not isinstance(raw_accounts, dict) or not 1 <= len(raw_accounts) <= MAX_LOCAL_ACCOUNTS:
        raise ValueError("appliance auth store has an invalid account directory")

    accounts: dict[str, dict[str, Any]] = {}
    omv_owners: set[str] = set()
    expected_fields = {
        "display_name",
        "role",
        "password_hash",
        "omv_username",
        "active",
    }
    for raw_username, raw_record in raw_accounts.items():
        if (
            not isinstance(raw_username, str)
            or _ACCOUNT_USERNAME.fullmatch(raw_username) is None
            or not isinstance(raw_record, dict)
            or set(raw_record) != expected_fields
        ):
            raise ValueError("appliance auth store has an invalid account record")
        display_name = raw_record.get("display_name")
        role = raw_record.get("role")
        password_hash = raw_record.get("password_hash")
        omv_username = raw_record.get("omv_username")
        active = raw_record.get("active")
        if (
            not isinstance(display_name, str)
            or not display_name.strip()
            or len(display_name.strip()) > 64
            or role not in _ACCOUNT_ROLES
            or not isinstance(password_hash, str)
            or not password_hash
            or len(password_hash) > 4096
            or not isinstance(active, bool)
        ):
            raise ValueError("appliance auth store has an invalid account record")
        if omv_username is not None and (
            not isinstance(omv_username, str) or _ACCOUNT_USERNAME.fullmatch(omv_username) is None
        ):
            raise ValueError("appliance auth store has an invalid OMV account mapping")

        if raw_username == ADMIN_USERNAME:
            if (
                role != "admin"
                or omv_username is not None
                or active is not True
                or password_hash != admin_hash
            ):
                raise ValueError("appliance administrator account is inconsistent")
        else:
            if role != "member" or omv_username != raw_username:
                raise ValueError("appliance member account mapping is inconsistent")
            folded = omv_username.casefold()
            if folded in omv_owners:
                raise ValueError("appliance OMV account mapping is duplicated")
            omv_owners.add(folded)

        accounts[raw_username] = {
            "display_name": display_name.strip(),
            "role": role,
            "password_hash": password_hash,
            "omv_username": omv_username,
            "active": active,
        }

    if ADMIN_USERNAME not in accounts:
        raise ValueError("appliance account directory is missing the administrator")
    return accounts


def payload_with_normalized_accounts(payload: dict[str, Any]) -> dict[str, Any]:
    updated = dict(payload)
    updated[ACCOUNTS_KEY] = normalized_accounts(payload)
    return updated


def _validate_auth_store_path(target: Path, *, create_parent: bool = False) -> None:
    parent = target.parent
    if parent.is_symlink():
        raise ValueError("appliance auth directory must not be a symlink")
    if create_parent:
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not parent.is_dir():
        raise ValueError("appliance auth directory must be a directory")
    # The implicit local-development path is the checkout's current directory;
    # never change its permissions. Explicit data directories are private state.
    if parent != Path("."):
        parent.chmod(0o700)
        if stat.S_IMODE(parent.stat().st_mode) != 0o700:
            raise OSError("appliance auth directory permissions are not private")
    if target.is_symlink():
        raise ValueError("appliance auth store must not be a symlink")
    if target.exists():
        if not target.is_file():
            raise ValueError("appliance auth store must be a regular file")
        target.chmod(0o600)
        if stat.S_IMODE(target.stat().st_mode) != 0o600:
            raise OSError("appliance auth store permissions are not private")


def auth_store_path() -> Path:
    base = os.environ.get("ECHO_DATA_DIR") or os.environ.get("ECHO_DATA") or "."
    return Path(base) / "appliance-auth.json"


def read_auth_store(path: Path | None = None) -> dict[str, Any]:
    target = path or auth_store_path()
    _validate_auth_store_path(target)
    if not target.is_file():
        raise ValueError("appliance auth store must be a regular file")
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("appliance auth store must be a JSON object")
    return payload


def write_auth_store(payload: dict[str, Any], path: Path | None = None) -> None:
    """Atomically replace the private credential store and durably flush it."""

    target = path or auth_store_path()
    _validate_auth_store_path(target, create_parent=True)
    temporary = target.with_name(f".{target.name}.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        with contextlib.suppress(OSError):
            target.chmod(0o600)
        with contextlib.suppress(OSError):
            directory = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def _auth_config(config_type: Any, data: dict[str, Any]) -> Any:
    """Pin the appliance session and brute-force policy instead of inheriting drift."""

    return config_type(
        enabled=True,
        allow_any_username=False,
        users={
            username: account["password_hash"]
            for username, account in normalized_accounts(data).items()
            if account["active"]
        },
        jwt_secret=data["jwt_secret"],
        jwt_expire_seconds=SESSION_SECONDS,
        login_max_failures=LOGIN_MAX_FAILURES,
        login_ip_max_failures=LOGIN_IP_MAX_FAILURES,
        login_failure_window_seconds=LOGIN_FAILURE_WINDOW_SECONDS,
        login_lockout_seconds=LOGIN_LOCKOUT_SECONDS,
        login_rate_limit_max_entries=LOGIN_RATE_LIMIT_MAX_ENTRIES,
    )


def load_or_bootstrap_auth() -> tuple[Any, str | None]:
    """返回 (LocalAuthConfig, generated_password)。

    generated_password 仅在本次随机生成新密码时非空(供调用方打印一次)。
    已存在凭据存储时直接读取,返回 (config, None)。
    """
    # Keep read/write helpers usable by offline OS backup and schema tooling
    # without importing the private Agent distribution.  Only live auth
    # bootstrap crosses the compatibility boundary.
    from appliance.agent_api.auth import LocalAuthConfig, hash_password

    path = auth_store_path()

    if path.exists():
        data = read_auth_store(path)
        normalized = payload_with_normalized_accounts(data)
        if normalized != data:
            write_auth_store(normalized, path)
        data = normalized
        # 已存在存储时同样清理残留的环境变量，避免误用
        with contextlib.suppress(KeyError):
            os.environ.pop("ECHO_ADMIN_PASSWORD", None)
        config = _auth_config(LocalAuthConfig, data)
        return config, None

    env_password = os.environ.get("ECHO_ADMIN_PASSWORD", "").strip()
    generated: str | None = None
    if env_password:
        password = env_password
        # bcrypt 静默截断 72B，必须在入口处拒绝，避免“强口令实际弱化”
        if len(password.encode("utf-8")) > 72:
            raise ValueError("ECHO_ADMIN_PASSWORD must be at most 72 UTF-8 bytes (bcrypt limit)")
    else:
        password = secrets.token_urlsafe(12)
        generated = password

    # 阅后即焚：避免残留在 /proc/<pid>/environ 与子进程环境
    with contextlib.suppress(KeyError):
        os.environ.pop("ECHO_ADMIN_PASSWORD", None)
        # 清理常见变体，防止宿主 .env 透传
        if "ECHO_ADMIN_PASSWORD" in os.environ:
            del os.environ["ECHO_ADMIN_PASSWORD"]

    password_hash = hash_password(password)
    # The local all-in-one launcher gives Agent and Echo the same signing key.
    # Production can do the same through its injected runtime config; otherwise
    # a fresh appliance still receives an independent random secret.
    jwt_secret = os.environ.get("ECHO_LOCAL_JWT_SECRET", "").strip()
    if not jwt_secret:
        jwt_secret = secrets.token_urlsafe(48)

    payload = {
        "username": ADMIN_USERNAME,
        "password_hash": password_hash,
        "jwt_secret": jwt_secret,
        SESSION_NOT_BEFORE_KEY: 0,
        ACCOUNT_SESSION_NOT_BEFORE_KEY: {},
        AUTH_SCHEMA_VERSION_KEY: CURRENT_SCHEMA_VERSION,
    }
    payload[ACCOUNTS_KEY] = normalized_accounts(payload)
    write_auth_store(payload, path)

    config = _auth_config(LocalAuthConfig, payload)
    return config, generated


__all__ = [
    "ADMIN_USERNAME",
    "ACCOUNTS_KEY",
    "ACCOUNT_SESSION_NOT_BEFORE_KEY",
    "MAX_LOCAL_ACCOUNTS",
    "SESSION_NOT_BEFORE_KEY",
    "auth_store_path",
    "load_or_bootstrap_auth",
    "normalized_accounts",
    "payload_with_normalized_accounts",
    "read_auth_store",
    "write_auth_store",
]
