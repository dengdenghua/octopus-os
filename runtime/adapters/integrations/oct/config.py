"""oct 账号网关集成配置。

oct = EchoAI 自己的账号网关(echo-mobile server,线上 https://api.echo-age.com):
邮箱验证码登录→JWT、/account/* 积分会员、/billing/* 商品订单、/v1/chat/completions LLM 中转。
本集成把账号、会员、计费和模型调用统一到 oct 网关。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class OctConfig(BaseModel):
    enabled: bool = Field(
        default=False,
        description="总开关 · false 时所有 /api/*/oct 返 503",
    )
    base_url: str = Field(
        default="https://api.echo-age.com",
        description="oct 账号网关根地址(echo-mobile server)· 无尾斜杠",
    )
    default_model: str = Field(
        default="qwen3.5-flash",
        description="默认模型 id · 网关侧 FORCE_MODEL 可能再覆盖",
    )
    request_timeout_seconds: float = Field(
        default=15.0,
        ge=1.0,
        le=600.0,
        description="账号/积分类请求超时 · 保持短以便快失败",
    )
    llm_timeout_seconds: float = Field(
        default=120.0,
        ge=1.0,
        le=600.0,
        description="LLM 中转(/v1/chat/completions)超时 · 比账号查询长",
    )
    mock_mode: bool = Field(
        default=False,
        description=(
            "开发/演示模式 · 走网关 ALLOW_MOCK_AUTH 的 devCode(网关返回的验证码)· 生产必须 false"
        ),
    )

    # agent 登录成功后:把网关 JWT 存进 OctLink,并(若设密钥)额外签发 agent 自有 JWT 作为
    # 前端会话凭证。留空则 /email/login 直接把网关 JWT 当 access_token 返回。
    jwt_secret: str | None = Field(
        default=None,
        description="agent 自有会话 JWT 的 HS256 密钥 · 留空则复用网关 JWT 作为 access_token",
    )
    jwt_expire_seconds: int = Field(
        default=30 * 24 * 3600,
        description="agent 自有 JWT exp 距 iat 秒数 · 默认 30 天",
    )
    jwt_issuer: str | None = Field(
        default="echo-agent",
        description="agent 自有 JWT 的 iss claim",
    )


def load_oct_config_from_dict(data: dict | None) -> OctConfig:
    return OctConfig(**(data or {}))
