"""Same-origin gateway for the private echo-storage service.

The browser talks only to ``/api/storage/*`` on echo-agent.  The gateway
injects Storage's local bearer token server-side and streams the response, so
the Storage port and credential never need to be exposed to the frontend.  An
explicitly desktop-only, read-only provider keeps the same contract usable
when the optional sibling is not installed.
"""

from __future__ import annotations

import json
import mimetypes
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse

from appliance.files.manager import PathEscape
from echo_runtime.resource_identity import (
    parse_storage_file_resource_id,
    storage_file_resource_id,
)
from runtime.safety.privacy import PrivacyViolation, privacy_enabled, require_local_endpoint
from runtime.safety.storage_privacy import (
    storage_compute_request,
    storage_policy,
    verify_private_storage,
)
from runtime.storage.desktop_provider import (
    desktop_file_entry,
    desktop_file_manager,
    desktop_files,
    desktop_search,
    desktop_source,
)

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

_RESOURCE_SEGMENT = re.compile(r"^[^/]+$")


def _allowed_storage_route(method: str, safe_path: str) -> bool:
    """Keep the same-origin gateway limited to the reviewed Storage API."""

    parts = safe_path.split("/")
    if parts[:1] != ["v1"]:
        return False
    if safe_path == "v1/manifest":
        return method == "GET"
    if safe_path == "v1/policy":
        return method == "GET"
    if safe_path == "v1/models":
        return method == "GET"
    if len(parts) == 4 and parts[:2] == ["v1", "models"]:
        return (
            method == "POST"
            and _RESOURCE_SEGMENT.fullmatch(parts[2]) is not None
            and parts[3] in {"download", "enable", "disable"}
        )
    if safe_path == "v1/sources":
        return method in {"GET", "POST"}
    if len(parts) == 3 and parts[:2] == ["v1", "sources"]:
        return method == "DELETE" and _RESOURCE_SEGMENT.fullmatch(parts[2]) is not None
    if safe_path == "v1/browse":
        return method == "GET"
    if safe_path == "v1/search":
        return method in {"GET", "POST"}
    if safe_path == "v1/index/jobs":
        return method == "POST"
    if len(parts) == 4 and parts[:3] == ["v1", "index", "jobs"]:
        return method == "GET" and _RESOURCE_SEGMENT.fullmatch(parts[3]) is not None
    if safe_path in {"v1/albums", "v1/apps", "v1/files"}:
        return method == "GET"
    if len(parts) == 4 and parts[:2] == ["v1", "apps"]:
        return _RESOURCE_SEGMENT.fullmatch(parts[2]) is not None and (
            (method == "POST" and parts[3] in {"open", "reveal"})
            or (method in {"GET", "HEAD"} and parts[3] == "icon")
        )
    if len(parts) == 4 and parts[:2] == ["v1", "files"]:
        return _RESOURCE_SEGMENT.fullmatch(parts[2]) is not None and (
            (method in {"GET", "HEAD"} and parts[3] == "content")
            or (method in {"GET", "PUT"} and parts[3] == "text")
            or (method == "POST" and parts[3] == "diff")
        )
    if safe_path == "v1/answer":
        return method == "POST"
    return False


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


def _resource_id_for_item(item: Any, *, default_source_id: Any = None) -> Any:
    """Add the shared Storage identity without exposing or rewriting paths."""
    if not isinstance(item, dict):
        return item
    existing = item.get("resource_id") or item.get("resourceId")
    if isinstance(existing, str) and existing.strip():
        return item
    path = item.get("path")
    source_id = item.get("source_id") or item.get("sourceId") or default_source_id
    if not isinstance(path, str) or not isinstance(source_id, str):
        return item
    try:
        resource_id = storage_file_resource_id(source_id, path)
    except ValueError:
        return item
    return {**item, "resource_id": resource_id}


def _normalize_resource_payload(safe_path: str, payload: Any) -> Any:
    """Normalize known Storage browse/search envelopes while leaving others opaque."""
    if safe_path == "v1/browse":
        if isinstance(payload, list):
            return [_resource_id_for_item(item) for item in payload]
        if isinstance(payload, dict) and isinstance(payload.get("entries"), list):
            source_id = payload.get("source_id") or payload.get("sourceId")
            return {
                **payload,
                "entries": [
                    _resource_id_for_item(item, default_source_id=source_id)
                    for item in payload["entries"]
                ],
            }
    if safe_path == "v1/search" and isinstance(payload, dict):
        hits = payload.get("hits")
        if isinstance(hits, list):
            source_id = payload.get("source_id") or payload.get("sourceId")
            return {
                **payload,
                "hits": [_resource_id_for_item(item, default_source_id=source_id) for item in hits],
            }
    if safe_path == "v1/files":
        if isinstance(payload, list):
            return [_resource_id_for_item(item) for item in payload]
        if isinstance(payload, dict):
            for key in ("files", "assets", "items"):
                items = payload.get(key)
                if isinstance(items, list):
                    source_id = payload.get("source_id") or payload.get("sourceId")
                    return {
                        **payload,
                        key: [
                            _resource_id_for_item(item, default_source_id=source_id)
                            for item in items
                        ],
                    }
    return payload


async def _desktop_storage_fallback(request: Request, safe_path: str, body: bytes) -> Any:
    """Serve the read-only embedded Storage contract after sibling failure."""

    local = desktop_file_manager()
    if local is None:
        return None
    manager = local.manager
    source_id = local.source_id
    parts = safe_path.split("/")
    try:
        if safe_path == "v1/manifest" and request.method == "GET":
            return JSONResponse(
                {
                    "service": "echo-storage-local",
                    "version": "1",
                    "role": "embedded",
                    "capabilities": ["browse", "search", "files", "content"],
                }
            )
        if safe_path == "v1/sources" and request.method == "GET":
            return JSONResponse([desktop_source(local)])
        if safe_path == "v1/browse" and request.method == "GET":
            path = request.query_params.get("path", "")
            entries = await run_in_threadpool(manager.list_dir, path)
            return JSONResponse([desktop_file_entry(entry, source_id) for entry in entries])
        if safe_path == "v1/files" and request.method == "GET":
            kind = request.query_params.get("kind", "document")
            try:
                limit = max(1, min(500, int(request.query_params.get("limit", "500"))))
            except ValueError:
                limit = 500
            assets = await run_in_threadpool(desktop_files, local, limit=limit)
            if kind in {"document", "image", "video"}:
                assets = [asset for asset in assets if asset["kind"] == kind]
            return JSONResponse(assets)
        if safe_path == "v1/search" and request.method in {"GET", "POST"}:
            payload: dict[str, Any] = {}
            if request.method == "POST" and body:
                try:
                    payload = json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    payload = {}
            query = (
                str(payload.get("query") or request.query_params.get("query") or "")
                .strip()
                .casefold()
            )
            if not query:
                return JSONResponse(
                    {
                        "query": "",
                        "mode": storage_policy()["mode"],
                        "hits": [],
                        "message": "请输入搜索内容",
                    }
                )
            try:
                top_k = max(
                    1, min(50, int(payload.get("top_k") or request.query_params.get("top_k") or 8))
                )
            except (TypeError, ValueError):
                top_k = 8
            hits = await run_in_threadpool(desktop_search, local, query, top_k=top_k)
            return JSONResponse(
                {
                    "query": query,
                    "mode": storage_policy()["mode"],
                    "hits": hits,
                    "message": None,
                }
            )
        if (
            len(parts) == 4
            and parts[:2] == ["v1", "files"]
            and parts[3] == "content"
            and request.method in {"GET", "HEAD"}
        ):
            decoded = parse_storage_file_resource_id(parts[2])
            if decoded is None or decoded[0] != source_id:
                return JSONResponse({"detail": "resource not found"}, status_code=404)
            target = await run_in_threadpool(manager.file_for_download, decoded[1].lstrip("/"))
            return FileResponse(target, media_type=mimetypes.guess_type(target.name)[0])
    except (FileNotFoundError, NotADirectoryError):
        return JSONResponse({"detail": "resource not found"}, status_code=404)
    except (PathEscape, ValueError):
        return JSONResponse({"detail": "invalid resource path"}, status_code=400)
    return None


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
        if safe_path == "v1/policy":
            if request.method == "GET":
                return JSONResponse(storage_policy())
            raise HTTPException(409, "隐私模式已统一到系统设置，请使用 /api/ai-mode。")
        if not _allowed_storage_route(request.method, safe_path):
            raise HTTPException(404, "unknown storage route")
        if re.fullmatch(r"v1/files/[^/]+/(text|diff)", safe_path):
            # The standalone token grants a configured root, not a family
            # member's OMV share ACL. Only registered operators may use it.
            from runtime.safety.auth.principal import require_operator

            require_operator(
                request, identity_store, require_auth,
                jwt_secret=jwt_secret, jwt_issuer=jwt_issuer, jwt_audience=jwt_audience,
            )
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

        try:
            require_local_endpoint(_base_url(), operation="storage_gateway")
        except PrivacyViolation as exc:
            raise HTTPException(403, str(exc)) from exc

        owns_client = http_client is None
        client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=3.0, read=300.0, write=30.0, pool=3.0),
            follow_redirects=False,
            trust_env=False,
        )
        url = f"{_base_url().rstrip('/')}/{safe_path}"
        try:
            if privacy_enabled() and (
                client._trust_env is not False or client.follow_redirects is not False
            ):
                raise PrivacyViolation("本地数据库连接未满足隐私传输要求。")
            policy = storage_policy()
            if storage_compute_request(request.method, safe_path):
                if policy["mode"] == "privacy" and "/models/" in safe_path:
                    raise PrivacyViolation("隐私模式下请先使用已配置的本机模型。")
                # An independent Storage service must explicitly acknowledge
                # the restriction before it receives this request's content.
                policy_reply = await client.put(
                    f"{_base_url()}/v1/policy",
                    json=policy,
                    headers=_upstream_headers(request),
                    follow_redirects=False,
                )
                policy_reply.raise_for_status()
                acknowledged = policy_reply.json()
                if not isinstance(acknowledged, dict) or any(
                    acknowledged.get(key) != value for key, value in policy.items()
                ):
                    raise PrivacyViolation("本地数据库未确认系统策略，已停止执行。")
                if policy["mode"] == "privacy":
                    models_reply = await client.get(
                        f"{_base_url()}/v1/models",
                        headers=_upstream_headers(request),
                        follow_redirects=False,
                    )
                    models_reply.raise_for_status()
                    verify_private_storage(acknowledged, models_reply.json())
            if policy != storage_policy():
                raise PrivacyViolation("隐私策略在准备请求时已变化，请重试。")
            require_local_endpoint(_base_url(), operation="storage_gateway")
            upstream_request = client.build_request(
                request.method,
                url,
                params=list(request.query_params.multi_items()),
                headers=_upstream_headers(request),
                content=body,
            )
            upstream = await client.send(upstream_request, stream=True, follow_redirects=False)
        except (PrivacyViolation, ValueError) as exc:
            if owns_client:
                await client.aclose()
            raise HTTPException(403, "隐私校验未通过，本地数据库未执行此请求。") from exc
        except httpx.HTTPError:
            if owns_client:
                await client.aclose()
            if re.fullmatch(r"v1/files/[^/]+/(text|diff)", safe_path):
                # A lost response can follow a successful commit. Never retry
                # this write against an unrelated embedded root.
                return JSONResponse(
                    {"detail": "Storage 连接中断，请重新读取文件确认保存状态；草稿应保留"},
                    status_code=503,
                )
            fallback = await _desktop_storage_fallback(request, safe_path, body)
            if fallback is not None:
                return fallback
            return JSONResponse(
                {"detail": "echo-storage unavailable"},
                status_code=503,
                headers={"Retry-After": "2"},
            )

        # Browse, search and indexed file listings are small JSON envelopes.
        # Normalize their resource identity at the same-origin boundary; keep
        # binary/content routes streaming so previews and downloads retain
        # their existing behavior.
        if request.method in {"GET", "POST"} and safe_path in {
            "v1/browse",
            "v1/search",
            "v1/files",
        }:
            try:
                payload = json.loads((await upstream.aread()).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                await upstream.aclose()
                if owns_client:
                    await client.aclose()
                return JSONResponse(
                    {"detail": "storage returned invalid JSON"},
                    status_code=502,
                )
            headers = _response_headers(upstream)
            headers.pop("content-length", None)
            headers.pop("etag", None)
            await upstream.aclose()
            if owns_client:
                await client.aclose()
            return JSONResponse(
                _normalize_resource_payload(safe_path, payload),
                status_code=upstream.status_code,
                headers=headers,
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
