"""Configuration and authentication helpers for the Tentacle dashboard."""

from __future__ import annotations

import os
from typing import Any

from fastapi import WebSocket

from runtime.tentacle.mobile.vlm import VlmConfig


def auto_detect_vlm_config() -> VlmConfig | None:
    """Detect the first configured vision model provider."""

    vlm_key = os.environ.get("VLM_API_KEY", "").strip()
    if vlm_key:
        return VlmConfig(
            base_url=os.environ.get("VLM_BASE_URL", "https://api.openai.com/v1"),
            api_key=vlm_key,
            model=os.environ.get("VLM_MODEL", "gpt-4o"),
        )

    provider_keys = (
        ("QWEN_API_KEY", VlmConfig.qwen_vl),
        ("OPENAI_API_KEY", VlmConfig.openai_vl),
        ("DEEPSEEK_API_KEY", VlmConfig.deepseek_vl),
        ("GLM_API_KEY", VlmConfig.glm_vl),
    )
    for environment_key, factory in provider_keys:
        api_key = os.environ.get(environment_key, "").strip()
        if api_key:
            return factory(api_key)
    return None


def resolve_websocket_actor(
    websocket: WebSocket,
    *,
    identity_store: Any,
    require_auth: bool,
    jwt_secret: str | None,
    jwt_issuer: str | None,
    jwt_audience: str | None,
) -> str | None:
    """Resolve the authenticated actor for a dashboard WebSocket."""

    if identity_store is None:
        if require_auth:
            raise PermissionError("identity store required for tentacle auth")
        return None

    token: str | None = None
    try:
        auth_header = websocket.headers.get("authorization") or ""
    except Exception:  # noqa: BLE001
        auth_header = ""
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()

    if token is None:
        try:
            subprotocol = websocket.headers.get("sec-websocket-protocol") or ""
        except Exception:  # noqa: BLE001
            subprotocol = ""
        parts = [part.strip() for part in subprotocol.split(",") if part.strip()]
        if len(parts) >= 2 and parts[0].lower() == "bearer":
            token = parts[1]

    if not token:
        if require_auth:
            raise PermissionError("missing tentacle auth token")
        return None

    if jwt_secret and token.count(".") == 2:
        identity = identity_store.verify_jwt(
            token,
            secret=jwt_secret,
            required_issuer=jwt_issuer,
            required_audience=jwt_audience,
        )
        if identity is not None:
            return identity.actor_id
        if require_auth:
            raise PermissionError("invalid jwt")

    identity = identity_store.verify_api_key(token)
    if identity is not None:
        return identity.actor_id
    if require_auth:
        raise PermissionError("invalid token")
    return None


__all__ = ["auto_detect_vlm_config", "resolve_websocket_actor"]
