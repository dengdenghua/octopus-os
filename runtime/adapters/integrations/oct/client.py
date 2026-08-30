"""oct 账号网关 HTTP 客户端 helpers。

网关鉴权 = ``Authorization: Bearer {JWT}``(网关登录端点签发的 JWT)。
错误约定:HTTP 401 = token 失效/未登录;402 = 积分不足;其余 4xx/5xx = 网关拒绝。
所有函数同步(httpx),可注入 http_client 便于测试。
"""

from __future__ import annotations

import logging
import time
from typing import Any

try:
    import httpx  # type: ignore[import-untyped]

    HTTPX_AVAILABLE = True
except ImportError:  # pragma: no cover
    HTTPX_AVAILABLE = False
    httpx = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


class OctClientError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        body: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def is_dead_token(status_code: int | None) -> bool:
    """网关 401 = JWT 失效/未登录 → 需要重新登录。"""
    return status_code == 401


def is_insufficient_credits(status_code: int | None) -> bool:
    """网关 402 = 积分不足。"""
    return status_code == 402


def mask_email(email: str) -> str:
    email = (email or "").strip()
    if "@" not in email:
        return "***"
    local, _, domain = email.partition("@")
    head = local[:1] if local else ""
    return f"{head}***@{domain}"


def _client(http_client: Any) -> Any:
    client = http_client or (httpx if HTTPX_AVAILABLE else None)
    if client is None:
        raise OctClientError("httpx 未安装且未注入 http_client")
    return client


def _parse(r: Any, url: str) -> dict[str, Any]:
    status = getattr(r, "status_code", 0)
    text = getattr(r, "text", "") or ""
    if status >= 400:
        logger.warning("oct gateway rejected: url=%s status=%s resp=%r", url, status, text[:300])
        raise OctClientError(
            f"gateway rejected: {text[:512]}",
            status_code=status,
            body=text,
        )
    try:
        return r.json() if (text or getattr(r, "content", b"")) else {}
    except ValueError as exc:
        raise OctClientError(
            "gateway returned non-JSON",
            status_code=status,
            body=text,
        ) from exc


def post_public(
    url: str,
    body: dict[str, Any],
    *,
    timeout: float,
    http_client: Any = None,
) -> dict[str, Any]:
    """无鉴权 POST(发码/登录)。日志脱敏 email。"""
    log_body = {
        k: (mask_email(v) if k == "email" and isinstance(v, str) else v) for k, v in body.items()
    }
    t0 = time.perf_counter()
    client = _client(http_client)
    try:
        r = client.post(url, json=body, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        logger.warning("oct gateway unreachable: url=%s body=%s err=%s", url, log_body, exc)
        raise OctClientError(f"gateway unreachable: {exc}") from exc
    _ = int((time.perf_counter() - t0) * 1000)
    return _parse(r, url)


def get_public(
    url: str,
    *,
    timeout: float,
    params: dict[str, Any] | None = None,
    http_client: Any = None,
) -> dict[str, Any]:
    """Public GET that deliberately sends no Authorization header."""

    client = _client(http_client)
    try:
        r = client.get(url, params=params or {}, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        logger.warning("oct public GET %s unreachable: %s", url, exc)
        raise OctClientError(f"gateway unreachable: {exc}") from exc
    return _parse(r, url)


def get_auth(
    url: str,
    *,
    token: str,
    timeout: float,
    params: dict[str, Any] | None = None,
    http_client: Any = None,
) -> dict[str, Any]:
    """带 Bearer 的 GET(account/billing 查询)。"""
    if not isinstance(token, str) or not token.strip():
        raise OctClientError("gateway bearer token is missing", status_code=401)
    client = _client(http_client)
    headers = {"Authorization": f"Bearer {token.strip()}"}
    try:
        r = client.get(url, headers=headers, params=params or {}, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        logger.warning("oct GET %s unreachable: %s", url, exc)
        raise OctClientError(f"gateway unreachable: {exc}") from exc
    return _parse(r, url)


def post_auth(
    url: str,
    body: dict[str, Any],
    *,
    token: str,
    timeout: float,
    http_client: Any = None,
) -> dict[str, Any]:
    """带 Bearer 的 POST(daily-claim/orders/estimate)。"""
    client = _client(http_client)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        r = client.post(url, json=body, headers=headers, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        logger.warning("oct POST %s unreachable: %s", url, exc)
        raise OctClientError(f"gateway unreachable: {exc}") from exc
    return _parse(r, url)
