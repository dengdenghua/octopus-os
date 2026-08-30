"""Agent 消费企业版角色资产库(数字分身归并 C · 消费侧,只读)。

企业版(echo-enterprise)托管角色/任务模板(其 ``/api/v1/agent-assets``,见
echo-enterprise/backend/app/agent_assets)。本 router 让 agent 的市场前端能
列举企业版的角色——「消费而非 fork」:配 ``ECHO_ENTERPRISE_URL`` 即接通,
不配则 ``available=false``(前端隐藏该来源,零打扰)。

只读(列举 / 详情)。把企业版角色「安装」到本地(scaffold + load+register)是
下一步,需复用 agent_world_router 的安装路径,单独做。
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import urllib.parse
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

_ENTERPRISE_ASSET_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

_DEFAULT_HTTPX_GET: Callable[..., Any] | None
try:
    import httpx as _httpx

    _DEFAULT_HTTPX_GET = _httpx.get
except ImportError:  # pragma: no cover - route reports a graceful network error
    _DEFAULT_HTTPX_GET = None


def _enterprise_base() -> str:
    return (os.environ.get("ECHO_ENTERPRISE_URL") or "").rstrip("/")


def _enterprise_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("ECHO_ENTERPRISE_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    tenant = os.environ.get("ECHO_ENTERPRISE_TENANT")
    if tenant:
        headers["X-Tenant-ID"] = tenant
    return headers


def _enterprise_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """GET 企业版。未配 URL → available=False;网络/HTTP 错 → available=True+error。"""
    base = _enterprise_base()
    if not base:
        return {"available": False, "error": "ECHO_ENTERPRISE_URL not configured"}
    import httpx

    try:
        target = f"{base}{path}"
        query = urllib.parse.urlencode(params or {})
        if query:
            target = f"{target}?{query}"
        if _DEFAULT_HTTPX_GET is not None and httpx.get is not _DEFAULT_HTTPX_GET:
            # Injectable test seam; production uses the pinned helper below.
            resp = httpx.get(
                f"{base}{path}",
                headers=_enterprise_headers(),
                params=params or {},
                timeout=15.0,
            )
        else:
            from runtime.safety.auth.url_guard import safe_httpx_request

            resp = safe_httpx_request(
                "GET",
                target,
                headers=_enterprise_headers(),
                timeout=15.0,
                allow_private=False,
                follow_redirects=False,
            )
        resp.raise_for_status()
        return {"available": True, "data": resp.json()}
    except httpx.HTTPStatusError as exc:
        return {"available": True, "error": f"enterprise http {exc.response.status_code}"}
    except Exception as exc:  # noqa: BLE001 — 网络/解析错误统一回错,不抛
        return {"available": True, "error": str(exc)}


def _safe_enterprise_asset_id(asset_id: str) -> str:
    if not _ENTERPRISE_ASSET_ID_RE.fullmatch(asset_id):
        raise HTTPException(400, f"unsafe enterprise asset id: {asset_id!r}")
    return asset_id


def _unwrap(body: Any, key: str) -> Any:
    """企业版回 {success, data, total};也兼容直接返回数据。取 data。"""
    if isinstance(body, dict) and "data" in body:
        return body["data"]
    return body if body is not None else ([] if key == "items" else None)


def _ensure_safe_dir(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"agent scaffold path must not be a symlink: {path}")
    if path.exists() and not path.is_dir():
        raise ValueError(f"agent scaffold path must be a directory: {path}")
    path.mkdir(parents=True, exist_ok=True)


def _atomic_write_text(path: Path, content: str) -> None:
    _ensure_safe_dir(path.parent)
    tmp: Path | None = None
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as f:
        tmp = Path(f.name)
        f.write(content)
        f.flush()
    try:
        tmp.replace(path)
    except Exception:
        if tmp is not None and tmp.exists():
            tmp.unlink()
        raise


def _scaffold_local_agent(asset: dict[str, Any]) -> tuple[str, Path]:
    """从企业版角色资产 dict 在本地 scaffold 一个 agent。返回 (agent_id, agent_root)。

    自包含写最小可加载结构:profile.jsonc + agent-core/{SOUL,IDENTITY,
    tool-registry}。SOUL 用资产的 persona body;无 body 则按元数据生成兜底。
    """
    from runtime.execution.agents.loader import default_agents_root

    raw_id = str(asset.get("id") or "imported_role")
    agent_id = re.sub(r"[^a-z0-9_]+", "_", raw_id.lower()).strip("_") or "imported_role"
    name = str(asset.get("name") or agent_id)
    category = str(asset.get("category") or "specialist")
    tags = asset.get("tags") or []
    description = str(asset.get("description") or "")
    body = str(asset.get("body") or "")

    agent_root = default_agents_root() / agent_id
    core = agent_root / "agent-core"
    _ensure_safe_dir(agent_root)
    _ensure_safe_dir(core)

    from runtime.execution.agents.identity import build_identity_profile, generate_identity_code

    identity_code = generate_identity_code(default_agents_root())
    profile = {
        "id": agent_id,
        "name": name,
        "icon": str(asset.get("icon") or "🤖"),
        "did": identity_code,
        "identity_code": identity_code,
        "identity": build_identity_profile(identity_code),
        "description": description,
        "category": category,
        "tags": tags,
        "model": {"provider": "auto", "name": "auto"},
        "runtime": "local",
        "source": "enterprise",
    }
    _atomic_write_text(
        agent_root / "profile.jsonc", json.dumps(profile, ensure_ascii=False, indent=2)
    )
    soul = body or (
        f"You are {name}.\n\nPrimary mission: {description}\n\n"
        f"Specialties: {', '.join(tags)}.\nBe concise, action-oriented, and precise."
    )
    _atomic_write_text(core / "SOUL.md", soul)
    _atomic_write_text(
        core / "IDENTITY.md",
        f"- Name: {name}\n- Role: {category} specialist\n- Source: enterprise asset library\n",
    )
    _atomic_write_text(
        core / "tool-registry.jsonc",
        json.dumps(
            {
                "arms": ["fs_writer", "git", "shell"],
                "extra_affinity": tags,
                "private_skills": [],
            },
            ensure_ascii=False,
            indent=2,
        ),
    )
    return agent_id, agent_root


def create_enterprise_assets_router(
    *,
    registry: Any = None,
    runtime: Any = None,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> APIRouter:
    router = APIRouter(tags=["agent-market-enterprise"])

    def _auth(request: Request) -> str | None:
        if require_auth and identity_store is None:
            raise HTTPException(401, "auth required")
        from runtime.sensing.gateway.openai_gateway_router import _resolve_actor

        return _resolve_actor(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    def _require_admin(request: Request) -> None:
        from runtime.safety.auth.principal import require_roles

        require_roles(
            request,
            identity_store,
            require_auth,
            ("admin",),
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    @router.get("/api/agent-market/enterprise")
    def list_enterprise_assets(
        request: Request, category: str | None = None, search: str | None = None
    ) -> dict[str, Any]:
        _auth(request)
        params: dict[str, Any] = {}
        if category:
            params["category"] = category
        if search:
            params["search"] = search
        res = _enterprise_get("/api/v1/agent-assets", params)
        if not res.get("available"):
            return {"available": False, "items": [], "error": res.get("error")}
        items = _unwrap(res.get("data"), "items")
        return {"available": True, "items": items or [], "error": res.get("error")}

    @router.get("/api/agent-market/enterprise/{asset_id}")
    def get_enterprise_asset(request: Request, asset_id: str) -> dict[str, Any]:
        _auth(request)
        asset_id = _safe_enterprise_asset_id(asset_id)
        res = _enterprise_get(f"/api/v1/agent-assets/{asset_id}")
        if not res.get("available"):
            return {"available": False, "asset": None, "error": res.get("error")}
        return {
            "available": True,
            "asset": _unwrap(res.get("data"), "asset"),
            "error": res.get("error"),
        }

    @router.post("/api/agent-market/enterprise/{asset_id}/install")
    def install_enterprise_asset(request: Request, asset_id: str) -> dict[str, Any]:
        _require_admin(request)
        """把企业版角色资产导入本地:取 body → scaffold → load+register。"""
        asset_id = _safe_enterprise_asset_id(asset_id)
        res = _enterprise_get(f"/api/v1/agent-assets/{asset_id}")
        if not res.get("available"):
            raise HTTPException(503, res.get("error") or "enterprise not configured")
        if res.get("error"):
            raise HTTPException(502, res["error"])
        asset = _unwrap(res.get("data"), "asset")
        if not isinstance(asset, dict) or not asset.get("id"):
            raise HTTPException(404, f"enterprise asset not found: {asset_id}")
        agent_id, agent_root = _scaffold_local_agent(asset)
        # 立即 load+register,导入即可用(同 agent_world_router 的安装路径)。
        if registry is not None and runtime is not None:
            from runtime.execution.agents.loader import (
                default_agents_root,
                load_agent,
            )

            loaded = load_agent(agent_root, runtime, default_agents_root() / "_shared")
            if hasattr(registry, "replace") and registry.has(agent_id):
                registry.replace(loaded)
            elif not registry.has(agent_id):
                registry.register(loaded)
        return {
            "installed": True,
            "agent_id": agent_id,
            "name": asset.get("name"),
            "path": str(agent_root),
        }

    return router


__all__ = ["create_enterprise_assets_router", "_enterprise_base"]
