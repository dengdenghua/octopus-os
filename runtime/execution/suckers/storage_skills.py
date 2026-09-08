"""File Agent document search via echo-storage or the embedded desktop provider.

``echo-storage`` is the Echo family's local secure data cerebellum — the
File Agent / 数字资产管家 backend. It indexes the user's *documents* (parsing +
OCR + local vectors) and serves source-grounded retrieval over a narrow local
HTTP API (default ``http://127.0.0.1:8767``). Per the family architecture,
echo-agent must NOT own that file index — it CALLS Storage.

The human-facing File Agent UI reaches Storage through echo-agent's
same-origin ``/api/storage`` gateway; this module is the *agent-facing* half
and calls the private service directly. Both paths share the same Storage
index without echo-agent ever building a durable document index of its own.
The native desktop additionally has an explicitly bounded local fallback when
the optional sibling is not installed.

Best-effort + self-gating: Storage not running / not yet configured → the
native desktop can use the bounded local fallback, while other hosts receive a
clear actionable message. Zero new dependency: a tiny ``urllib`` client, no
httpx/requests. The base URL is the same default the frontend uses,
overridable via ``ECHO_STORAGE_URL``.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from echo_runtime.resource_identity import storage_file_resource_id
from runtime.safety.privacy import PrivacyViolation, private_urlopen
from runtime.safety.storage_privacy import storage_policy, verify_private_storage
from runtime.storage.desktop_provider import desktop_file_manager, desktop_search

from .registry import Skill, SkillRegistry
from .testing import SkillExpect, SkillTestCase

_DEFAULT_URL = "http://127.0.0.1:8767"
_TIMEOUT_S = 8.0
_MAX_TOP_K = 20
_SNIPPET_CAP = 600


def _base_url() -> str:
    raw = (os.environ.get("ECHO_STORAGE_URL") or "").strip()
    return (raw or _DEFAULT_URL).rstrip("/")


def _storage_token() -> str | None:
    """Bearer token for Storage's local API. Env override first, else the token
    file Storage writes (``~/.echo/storage/api_token``). Storage now requires
    it — without the header every call (and the liveness probe) 401s and Storage
    looks 'down' even when it is healthy and serving."""
    raw = (os.environ.get("ECHO_STORAGE_TOKEN") or "").strip()
    if raw:
        return raw
    try:
        token = (
            (Path.home() / ".echo" / "storage" / "api_token").read_text(encoding="utf-8").strip()
        )
        return token or None
    except OSError:
        return None


def _request(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = _TIMEOUT_S,
) -> Any:
    """Return Storage JSON or None on service failure; privacy denial raises."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}
    token = _storage_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        _base_url() + path,
        data=data,
        method=method,
        headers=headers,
    )
    try:
        with private_urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
        return json.loads(body) if body.strip() else {}
    except PrivacyViolation:
        raise
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None


def storage_manifest(*, timeout: float = _TIMEOUT_S) -> dict[str, Any] | None:
    """Probe Storage's ``/v1/manifest`` — ``None`` when the service is down."""
    return _request("GET", "/v1/manifest", timeout=timeout)


def storage_alive(*, timeout: float = 1.5) -> bool:
    """Liveness probe: True when Storage RESPONDS at all — including an auth
    error. A 401/403 means the server is up and answering (restarting it won't
    fix auth), so a supervisor must treat that as 'up' rather than thrash-restart
    a healthy Storage. Only a connection failure / timeout counts as down."""
    req = urllib.request.Request(_base_url() + "/v1/manifest", method="GET")
    token = _storage_token()
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with private_urlopen(req, timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True  # got an HTTP response → the server is up (even if 4xx)
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _unavailable() -> dict[str, Any]:
    return {
        "ok": False,
        "available": False,
        "hits": [],
        "count": 0,
        "message": (
            f"本地文档库(echo-storage)未运行或不可达({_base_url()})。"
            "请先启动 Storage 服务,并在「文件管家 / 数字资产管家」里配置 embedding "
            "模型和授权目录后再试。"
        ),
    }


def _desktop_search_fallback(
    query: str, *, top_k: int, source_ids: list[str]
) -> dict[str, Any] | None:
    """Use the embedded desktop provider when no Storage sibling is installed."""

    provider = desktop_file_manager()
    if provider is None:
        return None
    if source_ids and provider.source_id not in source_ids:
        hits: list[dict[str, Any]] = []
    else:
        hits = desktop_search(provider, query, top_k=top_k)
    return {
        "ok": True,
        "available": True,
        "query": query,
        "mode": storage_policy()["mode"],
        "hits": hits,
        "count": len(hits),
        "message": "使用桌面内嵌文件搜索（未连接外部 Storage 索引）。",
    }


def _search_documents(
    query: str = "",
    *,
    top_k: int | str = 8,
    source_ids: Any = None,
    **kw: Any,
) -> dict[str, Any]:
    """Search the user's local documents through echo-storage and return
    cited hits. The index + model stack live in Storage, not here."""
    query = str(query or kw.get("q") or kw.get("question") or kw.get("prompt") or "").strip()
    if not query:
        return {
            "ok": False,
            "available": True,
            "error": "query is required",
            "hits": [],
            "count": 0,
        }
    try:
        k = max(1, min(_MAX_TOP_K, int(top_k)))
    except (TypeError, ValueError):
        k = 8
    sids = [str(s) for s in source_ids] if isinstance(source_ids, (list, tuple)) else []

    try:
        policy = storage_policy()
        acknowledged = _request("PUT", "/v1/policy", policy)
        if acknowledged is None:
            fallback = _desktop_search_fallback(query, top_k=k, source_ids=sids)
            if fallback is not None:
                return fallback
            return _unavailable()
        if not isinstance(acknowledged, dict) or any(
            acknowledged.get(key) != value for key, value in policy.items()
        ):
            raise PrivacyViolation("本地数据库未确认系统策略，已停止检索。")
        if policy["mode"] == "privacy":
            verify_private_storage(
                acknowledged,
                _request("GET", "/v1/models"),
            )
        if policy != storage_policy():
            raise PrivacyViolation("隐私策略在准备检索时已变化，请重试。")
        resp = _request("POST", "/v1/search", {"query": query, "top_k": k, "source_ids": sids})
    except PrivacyViolation as exc:
        return {
            "ok": False,
            "available": True,
            "hits": [],
            "count": 0,
            "error": str(exc),
            "code": exc.code,
        }
    if resp is None:
        fallback = _desktop_search_fallback(query, top_k=k, source_ids=sids)
        if fallback is not None:
            return fallback
        return _unavailable()

    hits: list[dict[str, Any]] = []
    for h in resp.get("hits") or []:
        if not isinstance(h, dict):
            continue
        path = str(h.get("path") or "")
        resource_id = h.get("resource_id") or h.get("resourceId")
        if not isinstance(resource_id, str) or not resource_id.strip():
            source_id = h.get("source_id") or h.get("sourceId")
            if isinstance(source_id, str):
                try:
                    resource_id = storage_file_resource_id(source_id, path)
                except ValueError:
                    resource_id = None
        hit = {
            "path": path,
            "title": str(h.get("title") or ""),
            "snippet": str(h.get("snippet") or "")[:_SNIPPET_CAP],
            "score": h.get("score"),
            "citation": h.get("citation") if isinstance(h.get("citation"), dict) else {},
        }
        if isinstance(resource_id, str) and resource_id.strip():
            hit["resource_id"] = resource_id.strip()[:512]
        source_id = h.get("source_id") or h.get("sourceId")
        if isinstance(source_id, str) and source_id.strip():
            hit["source_id"] = source_id.strip()[:256]
        hits.append(hit)
    return {
        "ok": True,
        "available": True,
        "query": query,
        "mode": resp.get("mode"),
        "hits": hits,
        "count": len(hits),
        "message": resp.get("message"),
    }


_SEARCH_DOCUMENTS_DESCRIPTION = (
    "Search the USER'S OWN LOCAL DOCUMENTS (their files / notes / PDFs / scans) "
    "and return source-cited snippets. Backed by the echo-storage service "
    "(the File Agent / 数字资产管家 data cerebellum), which owns the document "
    "index, OCR and local embedding model — this skill just queries it. On the "
    "native desktop, a bounded filename/content fallback is used only when "
    "that sibling is unavailable; echo-agent never builds a durable document "
    "index itself.\n"
    "\n"
    "Use it when the user asks about THEIR files / documents / past notes "
    "('我之前写的那份…', 'what did my contract say about…', 'find my notes on X'). "
    "Do NOT use it for codebase/source questions (the repo grounding already "
    "covers code) or for general web facts (use web_search).\n"
    "\n"
    "Args: {query: string, top_k?: int 1-20 (default 8), source_ids?: [str] to "
    "restrict to specific indexed folders}.\n"
    "\n"
    "Returns: {ok, available (false → Storage not running/configured, with a "
    "message telling the user how to enable it), hits:[{path, title, snippet, "
    "score, citation, resource_id?}], count, mode}. Cite the returned path(s) "
    "in your answer and preserve resource_id when it is present."
)


def register_storage_skills(registry: SkillRegistry) -> int:
    """Register the File Agent document-search skill. Always registered; it
    self-reports at call time when Storage isn't available."""
    registry.register(
        Skill(
            name="search_documents",
            description=_SEARCH_DOCUMENTS_DESCRIPTION,
            affinity=["knowledge", "documents", "files", "file_agent", "rag", "retrieval"],
            cost_profile="low",  # one local HTTP call to a sibling service
            trusted_source="skill://public/search_documents",
            handler=_search_documents,
            privacy_local=True,
            tests=[
                SkillTestCase(
                    name="missing_query_returns_error",
                    tier="golden",
                    args={"query": ""},
                    expect=SkillExpect(schema_keys=["ok", "available", "hits", "count"]),
                    custom_predicate=lambda r: (
                        isinstance(r, dict)
                        and r.get("ok") is False
                        and "required" in (r.get("error") or "")
                    ),
                ),
            ],
        ),
        replace=True,
    )
    return 1
