from __future__ import annotations

import json
import re
from typing import Any

from runtime.platform.process.service_provider import get_provider
from runtime.sensing.model_router import Message, ModelRequest

_FENCED_JSON = re.compile(
    r"```(?:json)?\s*\n(\{.*?\})\s*\n```",
    re.DOTALL | re.IGNORECASE,
)
_BARE_JSON = re.compile(r"(\{.*\})", re.DOTALL)


def _parse_json_envelope(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    m = _FENCED_JSON.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except (json.JSONDecodeError, ValueError):  # noqa: BLE001 — JSON pattern did not match; try next extraction pattern
            pass
    m = _BARE_JSON.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except (json.JSONDecodeError, ValueError):  # noqa: BLE001 — JSON pattern did not match; try next extraction pattern
            pass
    return None


def _resolve_model(
    model: str | None,
    *,
    default_key: str,
) -> str | None:
    if model and model not in ("echo-agent", ""):
        return model
    default = get_provider().get(default_key)
    if isinstance(default, str) and default:
        return default
    return None


class LLMCaller:
    def __init__(
        self,
        router_key: str,
        model_key: str,
        *,
        default_model: str | None = None,
    ) -> None:
        self._router_key = router_key
        self._model_key = model_key
        self._default_model = default_model

    def call(
        self,
        *,
        system: str,
        user: str,
        model: str | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.3,
    ) -> tuple[str, dict[str, Any]]:
        router = get_provider().get(self._router_key)
        if router is None:
            return "", {"error": f"router not wired ({self._router_key})"}
        use_model = _resolve_model(
            model,
            default_key=self._model_key,
        )
        if not use_model:
            return "", {"error": "no model resolved"}
        req = ModelRequest(
            model=use_model,
            messages=[
                Message(role="system", content=system),
                Message(role="user", content=user),
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        try:
            resp = router.call(req)
        except Exception as exc:  # noqa: BLE001
            return "", {"error": f"{type(exc).__name__}: {exc}"}
        return (
            str(getattr(resp, "text", None) or ""),
            {
                "model": use_model,
                "input_tokens": int(
                    getattr(resp, "input_tokens", 0) or 0,
                ),
                "output_tokens": int(
                    getattr(resp, "output_tokens", 0) or 0,
                ),
            },
        )

    def call_json(
        self,
        *,
        system: str,
        user: str,
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        text, meta = self.call(
            system=system,
            user=user,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        meta["raw_text"] = text
        parsed = _parse_json_envelope(text)
        if parsed is None:
            meta["parse_error"] = "could not parse JSON envelope"
        return parsed, meta
