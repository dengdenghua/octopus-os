"""Shared, fail-closed admission for private inference and tool execution.

Local means a loopback endpoint, not a provider/model label. This is an
application boundary; it does not attest that a user-installed loopback
service is itself offline. Such services remain part of the trusted host.
"""

from __future__ import annotations

import ipaddress
import logging
import urllib.request
from typing import Any
from urllib.parse import urlsplit

_LOG = logging.getLogger("echo.privacy")


class PrivacyViolation(PermissionError):
    code = "privacy_egress_blocked"


def privacy_enabled() -> bool:
    from runtime.core.cerebrum.ai_mode import current_ai_mode

    return current_ai_mode() == "privacy"


def is_loopback_endpoint(endpoint: Any) -> bool:
    if not isinstance(endpoint, str) or not endpoint or endpoint != endpoint.strip():
        return False
    try:
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or "\\" in endpoint
            or any(ord(char) < 32 for char in endpoint)
        ):
            return False
        # Accessing port also rejects malformed/out-of-range ports.
        _ = parsed.port
        host = parsed.hostname or ""
        if host == "localhost":
            return True
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def deny_private_operation(operation: str) -> None:
    if privacy_enabled():
        # No prompt, file path, query string, credentials or response content.
        _LOG.warning("privacy_egress_blocked operation=%s", operation)
        raise PrivacyViolation(
            "隐私模式已阻止此操作：仅允许本机模型和已验证的本地工具。"
            "请配置本机模型，或明确切换到效率模式后再试。"
        )


def require_local_endpoint(endpoint: str, *, operation: str) -> None:
    if not is_loopback_endpoint(endpoint):
        deny_private_operation(operation)


def require_local_router(router: Any, model: str | None = None) -> None:
    """Check the selected adapter before vision preprocessing or fallback."""
    if not privacy_enabled():
        return
    # Custom catalog rows use a server-owned rewriting adapter. Resolve the
    # exact row/protocol before checking its transport, including Responses.
    if (
        type(router).__module__ == "runtime.sensing.gateway.config_router"
        and type(router).__name__ == "_UpstreamModelRewrite"
    ):
        require_local_router(router.privacy_upstream(model), model)
        return
    # Only adapters whose HTTP transport is configured without ambient
    # proxies or redirects are admitted. Unknown adapters fail closed.
    from runtime.sensing.model_router.ollama_router import OllamaModelRouter
    from runtime.sensing.model_router.openai_responses_router import OpenAIResponsesModelRouter
    from runtime.sensing.model_router.openai_router import OpenAIModelRouter

    if type(router) is OllamaModelRouter:
        endpoint = router._base_url
    elif type(router) is OpenAIModelRouter:
        endpoint = router.base_url
    elif type(router) is OpenAIResponsesModelRouter:
        endpoint = router._responses_url
    else:
        deny_private_operation("unverified_model_adapter")
        return
    require_local_endpoint(endpoint, operation="model_inference")
    client = getattr(router, "_client", None)
    if client is not None:
        # Injected transports are server configuration, but must carry the
        # same proxy/redirect restrictions as clients created by the adapter.
        import httpx

        if not isinstance(client, httpx.Client) or (
            client._trust_env is not False or client.follow_redirects is not False
        ):
            deny_private_operation("unverified_model_transport")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise PrivacyViolation("隐私模式禁止通过重定向转发本地请求。")


def private_urlopen(request: urllib.request.Request, *, timeout: float):
    """Keep private HTTP calls off ambient proxies and redirect chains."""
    require_local_endpoint(request.full_url, operation="local_service_request")
    if not privacy_enabled():
        return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    return opener.open(request, timeout=timeout)  # noqa: S310


def tool_privacy_denial(skill: Any) -> str | None:
    if not privacy_enabled():
        return None
    # Opt-in is server-owned and tied to built-in Python handlers. A remote
    # plugin cannot become private-safe by borrowing a built-in tool name.
    handler = getattr(skill, "handler", None)
    module = getattr(handler, "__module__", "")
    safe = getattr(skill, "privacy_local", False) is True and module.startswith(
        ("runtime.execution.", "appliance.")
    )
    if safe:
        return None
    return (
        "privacy_egress_blocked: 隐私模式下，此工具的本地隔离尚未验证，已阻止执行。"
        "联网、命令、浏览器和第三方插件需要效率模式。"
    )
