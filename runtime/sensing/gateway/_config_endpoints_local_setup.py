"""Import a freshly verified local model without replacing another connection."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

import httpx
from fastapi import Depends, HTTPException

from runtime.platform.models.custom_model_selection import custom_model_selection_id

if TYPE_CHECKING:
    from ._config_endpoints import _ConfigCtx


def register_verified_local_model(router: Any, ctx: _ConfigCtx) -> None:
    @router.post("/api/config/local-models/activate", dependencies=[Depends(ctx.require_admin)])
    @ctx.serialize_custom_models
    def activate(body: dict[str, Any]) -> dict[str, Any]:
        from runtime.sensing.model_router.local_model_setup import verified_model

        tag = body.get("tag")
        if not isinstance(tag, str):
            raise HTTPException(400, "model tag required")
        try:
            verified = verified_model(tag)
        except (ValueError, OSError) as exc:
            raise HTTPException(409, str(exc)) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(409, "本机模型服务不可用，请重新验证后再启用") from exc
        endpoint = verified["base_url"] + "/v1"
        seed = "verified-local-" + hashlib.sha256(f"{endpoint}\n{tag}".encode()).hexdigest()[:20]
        model_id = seed
        suffix = 1
        while model_id in ctx.custom_models and (
            ctx.custom_models[model_id].get("base_url") != endpoint
            or ctx.custom_models[model_id].get("models") != [tag]
        ):
            suffix += 1
            model_id = f"{seed}-{suffix}"
        previous = ctx.custom_models.get(model_id)
        entry = {
            "id": model_id,
            "name": model_id,
            "display_name": tag,
            "provider": "openai",
            "base_url": endpoint,
            "api_key": "",
            "models": [tag],
            "selection_only": True,
            "supports_thinking": False,
            "supports_tool_use": verified["supports_tool_use"],
            "supports_vision": verified["supports_vision"],
            "context_window": 4096,
            "default_headers": {},
        }
        try:
            status = ctx.register(entry)
            if status.get("ok") is not True:
                raise ValueError("模型路由未就绪，请检查服务后重试")
            ctx.custom_models[model_id] = entry
            ctx.save(model_id, strict=True)
        except Exception as exc:
            ctx.unregister_entry(entry, fallback_id=model_id)
            if previous is None:
                ctx.custom_models.pop(model_id, None)
            else:
                ctx.custom_models[model_id] = previous
                ctx.register(previous)
            raise HTTPException(503, "本机模型接入失败，原默认模型保持不变") from exc
        # The browser commits the principal's shared model profile separately,
        # only after this registration and durable configuration write succeed.
        return {
            "ok": True,
            "model_id": model_id,
            "selection_id": custom_model_selection_id(model_id, tag),
            "supports_tool_use": verified["supports_tool_use"],
            "supports_vision": verified["supports_vision"],
        }
