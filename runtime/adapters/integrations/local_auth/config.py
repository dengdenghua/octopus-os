from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


def hash_password(plaintext: str) -> str:
    """Hash a password using bcrypt with a random salt.

    Returns a string in the format ``bcrypt:<hash>`` for forward
    compatibility and to distinguish from legacy sha256 hashes.
    """
    import bcrypt

    pw_bytes = plaintext.encode("utf-8")
    hashed = bcrypt.hashpw(pw_bytes, bcrypt.gensalt(rounds=12))
    return f"bcrypt:{hashed.decode('utf-8')}"


def verify_password(plaintext: str, hashed: str) -> bool:
    """Verify a plaintext password against a bcrypt or legacy sha256 hash."""
    if hashed.startswith("bcrypt:"):
        import bcrypt

        stored = hashed[7:].encode("utf-8")
        return bcrypt.checkpw(plaintext.encode("utf-8"), stored)
    # Legacy sha256 fallback for migration
    import hashlib

    legacy = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    return hashed == legacy or hashed == f"sha256:{legacy}"


class LocalAuthConfig(BaseModel):
    enabled: bool = Field(
        default=False,
        description="总开关 · 生产环境慎开",
    )
    allow_any_username: bool = Field(
        default=False,
        description=(
            "True · 任何用户名都能登录 · dev 友好。False · 只认 allowed_usernames 或 users 里的键"
        ),
    )
    allowed_usernames: list[str] = Field(
        default_factory=list,
        description="白名单 · 仅 allow_any_username=False 且 users 空时生效",
    )
    users: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "用户名 → bcrypt 密码哈希(兼容旧 SHA-256)。非空时 · 强制密码登录 · "
            "allow_any_username 被忽略"
        ),
    )
    login_max_failures: int = Field(
        default=5,
        ge=1,
        le=100,
        description="同一直连 IP + 规范化用户名在窗口内触发锁定的失败次数",
    )
    login_ip_max_failures: int = Field(
        default=20,
        ge=1,
        le=1_000,
        description="同一直连 IP 在窗口内跨用户名触发锁定的总失败次数",
    )
    login_failure_window_seconds: float = Field(
        default=300.0,
        gt=0,
        le=86_400,
        description="登录失败计数滑动窗口(秒)",
    )
    login_lockout_seconds: float = Field(
        default=60.0,
        gt=0,
        le=86_400,
        description="达到失败阈值后的短期锁定时长(秒)",
    )
    login_rate_limit_max_entries: int = Field(
        default=10_000,
        ge=1,
        le=1_000_000,
        description="单进程登录限速状态的最大 IP/用户名组合数",
    )
    jwt_secret: str | None = Field(
        default=None,
        description=(
            "登录成功时签发 JWT 用的 HS256 密钥。不设则返 actor_id 但不发 JWT · 客户端走 X-Actor"
        ),
    )

    @staticmethod
    def _validate_jwt_secret(secret: str | None) -> None:
        if secret is None:
            return
        if len(secret) < 32:
            raise ValueError(f"jwt_secret must be at least 32 characters long, got {len(secret)}")
        # Check entropy: reject secrets that are too predictable
        # (e.g. all lowercase, all digits, or common patterns)
        has_lower = any(c.islower() for c in secret)
        has_upper = any(c.isupper() for c in secret)
        has_digit = any(c.isdigit() for c in secret)
        has_special = any(not c.isalnum() for c in secret)
        score = sum([has_lower, has_upper, has_digit, has_special])
        if score < 3:
            raise ValueError(
                "jwt_secret must contain at least 3 of: lowercase, uppercase, digits, special characters"
            )

    jwt_expire_seconds: int = Field(
        default=7 * 24 * 3600,
        description="JWT exp 距 iat 多少秒 · 默认 7 天",
    )
    jwt_issuer: str | None = Field(
        default="echo-agent",
        description="JWT iss claim",
    )
    jwt_audience: str | None = Field(
        default=None,
        description="JWT aud claim",
    )
    actor_prefix: str = Field(
        default="local:",
        description="actor_id 前缀 · 与其他账号提供方分命名空间",
    )
    default_roles: list[str] = Field(
        default_factory=lambda: ["user", "local"],
        description="新建 Identity 的默认 roles",
    )

    @property
    def password_required(self) -> bool:
        return bool(self.users)

    def model_post_init(self, __context: Any) -> None:
        self._validate_jwt_secret(self.jwt_secret)
