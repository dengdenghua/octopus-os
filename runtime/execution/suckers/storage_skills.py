"""File Agent document search via the echo-storage sibling service.

``echo-storage`` is the Echo family's local secure data cerebellum — the
File Agent / 数字资产管家 backend. It indexes the user's *documents* (parsing +
OCR + local vectors) and serves source-grounded retrieval over a narrow local
HTTP API (default ``http://127.0.0.1:8767``). Per the family architecture,
echo-agent must NOT own that file index — it CALLS Storage.

The human-facing File Agent UI reaches Storage through echo-agent's
same-origin ``/api/storage`` gateway; this module is the *agent-facing* half
and calls the private service directly. Both paths share the same Storage
index without echo-agent ever building a document index of its own.

Best-effort + self-gating: Storage not running / not yet configured → the skill
returns a clear, actionable message and never crashes the turn. Zero new
dependency: a tiny ``urllib`` client, no httpx/requests. The base URL is the
same default the frontend uses, overridable via ``ECHO_STORAGE_URL``.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

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
) -> dict[str, Any] | None:
    """One best-effort call to Storage. Returns the decoded JSON object, or
    ``None`` when Storage is unreachable / errored — never raises."""
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
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — local http to a user-configured service  # nosec B310 — audited HTTP endpoint
            body = resp.read().decode("utf-8", "replace")
        return json.loads(body) if body.strip() else {}
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
        with urllib.request.urlopen(req, timeout=timeout):  # noqa: S310 — local http  # nosec B310 — audited HTTP endpoint
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

    resp = _request("POST", "/v1/search", {"query": query, "top_k": k, "source_ids": sids})
    if resp is None:
        return _unavailable()

    hits: list[dict[str, Any]] = []
    for h in resp.get("hits") or []:
        if not isinstance(h, dict):
            continue
        hits.append(
            {
                "path": str(h.get("path") or ""),
                "title": str(h.get("title") or ""),
                "snippet": str(h.get("snippet") or "")[:_SNIPPET_CAP],
                "score": h.get("score"),
                "citation": h.get("citation") if isinstance(h.get("citation"), dict) else {},
            }
        )
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
    "index, OCR and local embedding model — this skill just queries it; "
    "echo-agent never builds a document index itself.\n"
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
    "score, citation}], count, mode}. Cite the returned path(s) in your answer."
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
