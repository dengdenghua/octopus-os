from __future__ import annotations

import json
import os
from typing import Any

from .models import (
    LLMResponseFormatError,
    ModelRequest,
    ModelResponse,
    ModelRouter,
    ToolCall,
)
from .provider import Provider, ProviderCapabilities

try:
    import httpx

    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False
    httpx = None  # type: ignore[assignment]


class OllamaRouterError(LLMResponseFormatError):
    pass


class OllamaModelInfo:
    def __init__(self, name: str, size: int = 0, family: str = "", quant: str = "") -> None:
        self.name = name
        self.size = size
        self.family = family
        self.quant = quant

    def __repr__(self) -> str:
        return f"OllamaModelInfo({self.name!r}, size={self.size}, family={self.family!r})"


class OllamaModelRouter(Provider, ModelRouter):
    provider_name = "ollama"
    capabilities = ProviderCapabilities(
        supports_vision=True,
        supports_tool_use=True,
        supports_streaming=True,
        supports_prompt_cache=False,
        supports_structured_output=False,
        default_model="llama3.2:3b",
        pricing_hint="free",
    )

    def __init__(
        self,
        *,
        base_url: str | None = None,
        default_model: str | None = None,
        timeout_seconds: float = 120.0,
        auto_detect: bool = True,
        client: Any = None,
    ) -> None:
        if not HTTPX_AVAILABLE:
            raise OllamaRouterError("httpx not installed · `pip install httpx`")
        self._base_url = (
            base_url or os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        ).rstrip("/")
        self._timeout = timeout_seconds
        self._client = client
        self._owns_client = client is None
        self._available: bool | None = None
        self._models: list[OllamaModelInfo] = []

        if default_model is not None:
            self.default_model = default_model
        elif auto_detect:
            self.default_model = self._auto_select_model()
        else:
            self.default_model = "llama3.2:3b"

    def is_available(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            client = self._get_client()
            resp = client.get(f"{self._base_url}/api/tags", timeout=5.0)
            self._available = resp.status_code == 200
            if self._available:
                self._parse_models(resp.json())
        except (ConnectionError, TimeoutError, OSError, TypeError, ValueError):
            self._available = False
        return self._available

    def list_models(self) -> list[OllamaModelInfo]:
        if self._models and self._available:
            return self._models
        try:
            client = self._get_client()
            resp = client.get(f"{self._base_url}/api/tags", timeout=5.0)
            if resp.status_code == 200:
                self._parse_models(resp.json())
                self._available = True
        except (ConnectionError, TimeoutError, OSError, TypeError, ValueError):  # noqa: BLE001 — ollama probe failed; return cached or empty model list
            pass
        return self._models

    def call(self, request: ModelRequest) -> ModelResponse:
        model = request.model or self.default_model

        with self._get_client() as client:
            try:
                payload = {
                    "model": model,
                    "messages": [{"role": m.role, "content": m.content} for m in request.messages],
                    "stream": False,
                }
                if request.max_tokens:
                    payload["max_tokens"] = request.max_tokens
                if request.temperature is not None:
                    payload["temperature"] = request.temperature
                # This router advertises ``supports_tool_use=True``, which
                # makes ``native_tool_use_active()`` return True and puts the
                # agentic loop into native mode — but the tool catalog was
                # never forwarded and the response path hardcoded
                # ``tool_calls=[]``. Native mode therefore could not produce a
                # single action on ollama: every round came back as prose and
                # the turn ended through the guard impasse. Ollama serves an
                # OpenAI-compatible endpoint, so the same wire shape applies.
                if request.tools:
                    payload["tools"] = [
                        {
                            "type": "function",
                            "function": {
                                "name": t.name,
                                "description": t.description,
                                "parameters": t.input_schema,
                            },
                        }
                        for t in request.tools
                    ]
                    payload["tool_choice"] = "required" if request.require_tool_use else "auto"

                resp = client.post(
                    f"{self._base_url}/v1/chat/completions",
                    json=payload,
                    timeout=self._timeout,
                )
            except Exception as e:
                raise OllamaRouterError(f"ollama http_error: {type(e).__name__}: {e}") from e

            if resp.status_code >= 400:
                raise OllamaRouterError(f"ollama http_{resp.status_code}: {resp.text[:500]}")

            try:
                data = resp.json()
            except json.JSONDecodeError as exc:
                raise OllamaRouterError(f"ollama invalid_json: {exc}") from exc

            from runtime.platform.models import CostEntry

            choices = data.get("choices") or []
            text = ""
            finish_reason = None
            raw_calls: list = []
            if choices:
                msg = choices[0].get("message", {})
                text = msg.get("content", "") or ""
                finish_reason = choices[0].get("finish_reason")
                raw_calls = msg.get("tool_calls") or []

            # Forwarding the catalog is only half the fix: without parsing the
            # calls back the loop still observes an actionless round.
            tool_calls: list[ToolCall] = []
            for tc in raw_calls:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") or {}
                args_raw = fn.get("arguments") or ""
                try:
                    args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append(
                    ToolCall(
                        id=str(tc.get("id") or ""),
                        name=str(fn.get("name") or ""),
                        input=args if isinstance(args, dict) else {},
                    )
                )

            usage = data.get("usage") or {}
            input_tokens = int(usage.get("prompt_tokens", 0) or 0)
            output_tokens = int(usage.get("completion_tokens", 0) or 0)

            return ModelResponse(
                text=text,
                tool_calls=tool_calls,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost=CostEntry(tokens_in=input_tokens, tokens_out=output_tokens, usd=0.0),
                finish_reason=finish_reason,
                model=data.get("model", model),
                provider="ollama",
            )

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        return httpx.Client(timeout=self._timeout)

    def _auto_select_model(self) -> str:
        if not self.is_available():
            return "llama3.2:3b"
        models = self.list_models()
        if not models:
            return "llama3.2:3b"
        preferred = ["llama3.2", "llama3.1", "llama3", "mistral", "qwen2"]
        for pref in preferred:
            for m in models:
                if m.name.startswith(pref):
                    return m.name
        return models[0].name

    def _parse_models(self, data: dict[str, Any]) -> None:
        self._models = []
        for m in data.get("models") or []:
            self._models.append(
                OllamaModelInfo(
                    name=m.get("name", ""),
                    size=m.get("size", 0),
                    family=m.get("details", {}).get("family", ""),
                    quant=m.get("details", {}).get("quantization_level", ""),
                )
            )
