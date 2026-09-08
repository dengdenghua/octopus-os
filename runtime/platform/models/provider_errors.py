"""Secret-free, typed failures returned by external model providers.

The model layer talks to services that use different error bodies and status
codes.  Callers still need to distinguish a bad credential, an unavailable
model, throttling and a transport outage so they can show the right recovery
action.  This module keeps that distinction without allowing provider response
text (which may contain URLs, paths or tokens) to cross the public boundary.
"""

from __future__ import annotations

from typing import Any

from .llm import LLMResponseFormatError

MODEL_UNAVAILABLE_MESSAGE = "当前模型服务已将所选模型标记为不可用，请切换模型后重试。"
PROVIDER_HTTP_MESSAGES: dict[int, str] = {
    400: "模型服务拒绝了请求，请检查模型配置或切换模型后重试（HTTP 400）。",
    401: "模型服务凭据无效，请在插件设置中重新连接（HTTP 401）。",
    402: "模型服务账户余额不足，请充值或切换模型（HTTP 402）。",
    403: "当前账号无权使用此模型，请检查模型权限或切换模型（HTTP 403）。",
    404: "模型服务地址或模型不存在，请检查模型配置（HTTP 404）。",
    422: "模型服务无法处理请求参数，请检查模型配置（HTTP 422）。",
    429: "模型服务请求受限，请稍后重试（HTTP 429）。",
}


class ModelProviderHTTPError(LLMResponseFormatError):
    """An external HTTP outcome with a bounded, safe public projection."""

    def __init__(
        self,
        message: str = "model provider request failed",
        *,
        status_code: int | None = None,
        response_body: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code if isinstance(status_code, int) else None
        # Only inspect a bounded body for stable availability markers.  The
        # body itself is never retained or returned to a caller.
        lowered = str(response_body)[:16_384].casefold()
        self.model_unavailable = self.status_code in {400, 404} and any(
            marker in lowered
            for marker in ("model is unavailable", "model_unavailable", "model_not_found")
        )

    def public_failure(self) -> tuple[int, str]:
        """Return an HTTP-like classification and a secret-free message."""

        if self.model_unavailable:
            return 400, MODEL_UNAVAILABLE_MESSAGE
        if self.status_code in PROVIDER_HTTP_MESSAGES:
            return self.status_code, PROVIDER_HTTP_MESSAGES[self.status_code]
        return 502, "暂时无法连接模型服务，请检查网络和服务状态后重试。"

    @classmethod
    def from_exception(cls, error: BaseException) -> ModelProviderHTTPError:
        """Classify an arbitrary HTTP client exception without leaking it."""

        response: Any = getattr(error, "response", None)
        status = getattr(response, "status_code", None)
        body = ""
        if response is not None:
            try:
                body = str(getattr(response, "text", ""))
            except Exception:  # noqa: BLE001 — diagnostics are best effort
                body = ""
        return cls(status_code=status if isinstance(status, int) else None, response_body=body)


__all__ = [
    "MODEL_UNAVAILABLE_MESSAGE",
    "PROVIDER_HTTP_MESSAGES",
    "ModelProviderHTTPError",
]
