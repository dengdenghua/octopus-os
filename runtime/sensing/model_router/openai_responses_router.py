"""OpenAI Responses-compatible transport for API-key model providers."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

from runtime.adapters.instrumentation import record_gen_ai_cost, trace_stage
from runtime.platform.models.llm import ModelRequest, ModelResponse, ModelRouter, ModelStreamEvent

from .chatgpt_subscription_router import (
    _build_responses_payload,
    _iter_responses_sse,
    _upstream_model,
)
from .models import DEFAULT_USER_AGENT, LLMResponseFormatError
from .provider import Provider, ProviderCapabilities

try:
    import httpx  # type: ignore[import-untyped]

    HTTPX_AVAILABLE = True
except ImportError:  # pragma: no cover
    HTTPX_AVAILABLE = False
    httpx = None  # type: ignore[assignment]


class OpenAIResponsesRouterError(LLMResponseFormatError):
    """An API-key Responses provider could not complete a request."""


class OpenAIResponsesModelRouter(Provider, ModelRouter):
    """Run a Responses-only upstream while retaining the Echo native loop."""

    provider_name = "openai_responses"
    capabilities = ProviderCapabilities(
        supports_vision=True,
        supports_tool_use=True,
        supports_streaming=True,
        supports_prompt_cache=True,
        supports_structured_output=True,
        default_model="",
        pricing_hint="provider",
    )

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        default_model: str,
        extra_headers: Mapping[str, str] | None = None,
        provider_name: str = "openai_responses",
        client: Any = None,
        timeout_seconds: float = 180.0,
    ) -> None:
        if not HTTPX_AVAILABLE:
            raise OpenAIResponsesRouterError("httpx not installed")
        normalized_base = str(base_url or "").rstrip("/")
        if not normalized_base:
            raise OpenAIResponsesRouterError("Responses provider base_url is required")
        self.default_model = str(default_model or "").strip()
        self.provider_name = str(provider_name or "openai_responses").strip()
        self._responses_url = f"{normalized_base}/responses"
        self._api_key = str(api_key or "")
        self._extra_headers = dict(extra_headers or {})
        self._client = client
        self._timeout_seconds = float(timeout_seconds)

    def call(self, request: ModelRequest) -> ModelResponse:
        final: ModelResponse | None = None
        for event in self.call_stream(request):
            if event.type == "done":
                final = event.final
        if final is None:
            raise OpenAIResponsesRouterError(
                f"{self.provider_name} stream ended before response.completed"
            )
        return final

    def call_stream(self, request: ModelRequest) -> Iterator[ModelStreamEvent]:
        model = _upstream_model(request.model or self.default_model)
        payload = _build_responses_payload(request, model=model)
        with trace_stage(
            "eyes.openai_responses_router.stream",
            **{
                "echo.model": model,
                "echo.provider": self.provider_name,
            },
        ) as span:
            response, owned_client = self._open_stream(payload)
            try:
                if response.status_code >= 400:
                    response.read()
                    raise OpenAIResponsesRouterError(
                        _safe_http_error(
                            response.status_code,
                            response.text,
                            provider_name=self.provider_name,
                        )
                    )
                final: ModelResponse | None = None
                try:
                    events = _iter_responses_sse(
                        response,
                        model=model,
                        provider=self.provider_name,
                        service_name=self.provider_name,
                    )
                    for event in events:
                        if event.type == "done":
                            final = event.final
                        yield event
                except LLMResponseFormatError as exc:
                    raise OpenAIResponsesRouterError(str(exc)) from exc
                if final is None:
                    raise OpenAIResponsesRouterError(
                        f"{self.provider_name} stream ended before response.completed"
                    )
                record_gen_ai_cost(
                    span,
                    system=self.provider_name,
                    model=final.model or model,
                    input_tokens=final.input_tokens,
                    output_tokens=final.output_tokens,
                    usd=0.0,
                )
            finally:
                response.close()
                if owned_client is not None:
                    owned_client.close()

    def _open_stream(self, payload: Mapping[str, Any]) -> tuple[Any, Any | None]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
            "User-Agent": DEFAULT_USER_AGENT,
            **self._extra_headers,
        }
        client = self._client
        owned_client = None
        if client is None:
            owned_client = httpx.Client(
                timeout=httpx.Timeout(
                    connect=30.0,
                    read=self._timeout_seconds,
                    write=30.0,
                    pool=10.0,
                )
            )
            client = owned_client
        request = client.build_request(
            "POST",
            self._responses_url,
            json=dict(payload),
            headers=headers,
        )
        return client.send(request, stream=True), owned_client


def _safe_http_error(status: int, body: str, *, provider_name: str) -> str:
    if status in {401, 403}:
        return f"{provider_name} API Key 无效或没有模型权限（HTTP {status}）。"
    if status == 429:
        return f"{provider_name} 请求额度暂时受限，请稍后重试。"
    lowered = str(body or "").casefold()
    if status == 400 and "context" in lowered and "token" in lowered:
        return f"{provider_name} 请求上下文超过模型限制（HTTP 400）。"
    return f"{provider_name} 模型服务请求失败（HTTP {status}）。"


__all__ = ["OpenAIResponsesModelRouter", "OpenAIResponsesRouterError"]
