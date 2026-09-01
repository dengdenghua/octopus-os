"""Same-origin gateway for the private echo-storage service.

The browser talks only to ``/api/storage/*`` on echo-agent.  The gateway
injects Storage's local bearer token server-side and streams the response, so
the Storage port and credential never need to be exposed to the frontend.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

_REQUEST_BODY_LIMIT = 16 * 1024 * 1024
_FORWARDED_REQUEST_HEADERS = {
    "accept",
    "content-type",
    "if-modified-since",
    "if-none-match",
    "range",
}
_FORWARDED_RESPONSE_HEADERS = {
    "accept-ranges",
    "cache-control",
    "content-disposition",
    "content-length",
    "content-range",
    "content-type",
    "etag",
    "last-modified",
}


def _safe_storage_path(path: str) -> str:
    clean = str(path or "").strip().lstrip("/")
    parts = clean.split("/")
    if not clean or parts[0] != "v1" or any(part in {"", ".", ".."} for part in parts):
        raise HTTPException(404, "unknown storage route")
    return clean


def _upstream_headers(request: Request) -> dict[str, str]:
    headers = {
        name: value
        for name, value in request.headers.items()
        if name.lower() in _FORWARDED_REQUEST_HEADERS
    }
    from runtime.execution.suckers.storage_skills import _storage_token

    token = _storage_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _response_headers(response: httpx.Response) -> dict[str, str]:
    return {
        name: value
        for name, value in response.headers.items()
        if name.lower() in _FORWARDED_RESPONSE_HEADERS
    }


def create_storage_proxy_router(
    *,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/storage", tags=["storage"])

    def _auth(request: Request) -> None:
        if require_auth and identity_store is None:
            raise HTTPException(401, "auth required")
        from runtime.adapters.web_auth import _resolve_actor

        _resolve_actor(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    async def proxy_storage(request: Request, storage_path: str) -> Any:
        _auth(request)
        safe_path = _safe_storage_path(storage_path)
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > _REQUEST_BODY_LIMIT:
                    raise HTTPException(413, "storage request body too large")
            except ValueError as exc:
                raise HTTPException(400, "invalid content-length") from exc
        body = await request.body()
        if len(body) > _REQUEST_BODY_LIMIT:
            raise HTTPException(413, "storage request body too large")

        from runtime.execution.suckers.storage_skills import _base_url

        owns_client = http_client is None
        client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=3.0, read=300.0, write=30.0, pool=3.0),
            follow_redirects=False,
        )
        url = f"{_base_url().rstrip('/')}/{safe_path}"
        try:
            upstream_request = client.build_request(
                request.method,
                url,
                params=list(request.query_params.multi_items()),
                headers=_upstream_headers(request),
                content=body,
            )
            upstream = await client.send(upstream_request, stream=True)
        except httpx.HTTPError:
            if owns_client:
                await client.aclose()
            return JSONResponse(
                {"detail": "echo-storage unavailable"},
                status_code=503,
                headers={"Retry-After": "2"},
            )

        async def _stream() -> AsyncIterator[bytes]:
            try:
                async for chunk in upstream.aiter_raw():
                    if chunk:
                        yield chunk
            finally:
                await upstream.aclose()
                if owns_client:
                    await client.aclose()

        return StreamingResponse(
            _stream(),
            status_code=upstream.status_code,
            headers=_response_headers(upstream),
        )

    # FastAPI uses one operation ID per ``APIRoute``.  Registering multiple
    # methods on a single route therefore emits duplicate OpenAPI operation
    # IDs, which breaks generated clients.  Keep one route per method so the
    # proxy remains fully described by the public contract.
    for method in ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"):
        router.add_api_route(
            "/{storage_path:path}",
            proxy_storage,
            methods=[method],
            operation_id=f"proxy_storage_{method.lower()}",
        )

    return router
