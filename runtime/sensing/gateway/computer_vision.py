"""Vision-model config resolution + OpenAI-compatible vision call for the
computer-automation router.

Split out of the former ~1994-line computer_router.py. Fully self-contained
— no shared-state access, only reads ``data/custom_models.json`` (fixed
path) and environment variables.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import HTTPException

try:
    import httpx  # type: ignore[import-untyped]

    HTTPX_AVAILABLE = True
except ImportError:  # pragma: no cover
    HTTPX_AVAILABLE = False
    httpx = None  # type: ignore[assignment]


def _load_custom_model(model_id: str) -> dict[str, Any] | None:
    if not model_id:
        return None
    path = Path("data/custom_models.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    entry = data.get(model_id)
    return entry if isinstance(entry, dict) else None


def _vision_model_config(model_id: str) -> dict[str, Any] | None:
    selected = model_id or os.getenv("ECHO_COMPUTER_VISION_MODEL", "")
    entry = _load_custom_model(selected)
    if entry:
        # Pick the first upstream model id from the new
        # ``models`` list, falling back to the legacy ``model``
        # field so pre-refactor entries keep working.
        upstream = ""
        raw_models = entry.get("models")
        if isinstance(raw_models, list):
            for m in raw_models:
                if isinstance(m, str) and m.strip():
                    upstream = m.strip()
                    break
        if not upstream:
            legacy = entry.get("model")
            if isinstance(legacy, str) and legacy.strip():
                upstream = legacy.strip()
        if not upstream:
            upstream = selected
        return {
            "id": selected,
            "provider": str(entry.get("provider") or "openai").lower(),
            "base_url": str(entry.get("base_url") or ""),
            "api_key": str(entry.get("api_key") or ""),
            "model": upstream,
            "default_headers": entry.get("default_headers") or {},
        }

    env_base = os.getenv("ECHO_COMPUTER_VISION_BASE_URL", "")
    env_key = os.getenv("ECHO_COMPUTER_VISION_API_KEY", "")
    env_model = selected or os.getenv("ECHO_COMPUTER_VISION_UPSTREAM_MODEL", "")
    if env_base and env_model:
        return {
            "id": selected or env_model,
            "provider": "openai",
            "base_url": env_base,
            "api_key": env_key,
            "model": env_model,
            "default_headers": {},
        }
    return None


def _call_openai_vision(
    *,
    config: dict[str, Any],
    goal: str,
    data_url: str,
) -> str:
    if not HTTPX_AVAILABLE or httpx is None:
        raise HTTPException(503, "httpx not installed")
    provider = str(config.get("provider") or "openai").lower()
    if provider not in {"openai", "openai-compatible", "custom"}:
        raise HTTPException(
            400,
            f"vision call currently supports openai-compatible models only, got {provider}",
        )
    base_url = str(config.get("base_url") or "").rstrip("/")
    model = str(config.get("model") or "")
    if not base_url or not model:
        raise HTTPException(400, "vision model base_url and model are required")
    headers = {
        "Content-Type": "application/json",
    }
    api_key = str(config.get("api_key") or "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    default_headers = config.get("default_headers") or {}
    if isinstance(default_headers, dict):
        headers.update({str(k): str(v) for k, v in default_headers.items()})

    prompt = (
        "You are a desktop UI grounding model. "
        "Inspect the screenshot and return only JSON. "
        'Schema: {"actions":[{"action":"click","x":number,"y":number,'
        '"button":"left"}]} or type/key/wait actions. '
        "Coordinates must use the screenshot's native pixel coordinate system. "
        f"Task: {goal}"
    )
    payload = {
        "model": model,
        "temperature": 0,
        "stream": False,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
    }
    try:
        response = httpx.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=60,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"vision upstream request failed: {exc}") from exc
    if response.status_code >= 400:
        raise HTTPException(response.status_code, "vision upstream returned an error")
    try:
        data = response.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(502, "vision upstream returned non-JSON") from exc
    choices = data.get("choices") if isinstance(data, dict) else None
    if not isinstance(choices, list) or not choices:
        raise HTTPException(502, "vision upstream returned no choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        parts = [
            str(part.get("text"))
            for part in content
            if isinstance(part, dict) and part.get("type") in {None, "text"}
        ]
        content = "\n".join(parts)
    if not isinstance(content, str) or not content.strip():
        raise HTTPException(502, "vision upstream returned empty content")
    return content


__all__: list[str] = []
