from __future__ import annotations

import contextvars
import os
import queue
import re
import threading
import time
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .registry import Skill, SkillRegistry
from .testing import SkillExpect, SkillTestCase

try:
    import httpx  # type: ignore[import-untyped]

    HTTPX_AVAILABLE = True
except ImportError:  # pragma: no cover
    HTTPX_AVAILABLE = False
    httpx = None  # type: ignore[assignment]

try:
    import trafilatura  # type: ignore[import-untyped]

    TRAFILATURA_AVAILABLE = True
except ImportError:  # pragma: no cover
    TRAFILATURA_AVAILABLE = False
    trafilatura = None  # type: ignore[assignment]


# ═══════════════════════════════════════════════════════════
# fetch_url
# ═══════════════════════════════════════════════════════════


def _extract_main_content(html: str, url: str) -> dict[str, Any] | None:
    if not TRAFILATURA_AVAILABLE or not html:
        return None
    try:
        meta = trafilatura.extract_metadata(html, default_url=url)
        text = trafilatura.extract(
            html,
            url=url,
            favor_precision=True,
            include_comments=False,
            include_tables=True,
            with_metadata=False,
        )
    except (OSError, ValueError):
        return None
    if not text:
        return None
    meta_dict: dict[str, Any] = {}
    if meta is not None:
        as_dict = meta.as_dict() if hasattr(meta, "as_dict") else {}
        for key in ("title", "author", "date", "sitename", "description", "language"):
            val = as_dict.get(key)
            if val:
                meta_dict[key] = val
    return {"text": text, "metadata": meta_dict}


def _fetch_url(
    url: str = "",
    *,
    timeout_ms: int = 5000,
    max_bytes: int = 100_000,
    client: Any = None,
    allow_private: bool = False,
    extract: bool = False,
    retries: int = 2,
    **_kw: Any,
) -> dict[str, Any]:
    if not url:
        return {"error": "missing url"}

    from runtime.safety.auth.url_guard import check_url

    verdict = check_url(url, allow_private=allow_private)
    if not verdict.allow:
        return {
            "error": f"ssrf_blocked: {verdict.reason}",
            "url": url,
            "blocked": True,
        }

    pinned_fetch = client is None
    if client is None and not HTTPX_AVAILABLE:
        return {"error": "httpx not installed"}

    max_attempts = max(1, min(int(retries) + 1, 4))
    attempt_count = 0
    last_retryable = False
    resp = None
    last_error: Exception | None = None
    for attempt in range(max_attempts):
        attempt_count += 1
        try:
            if pinned_fetch:
                from runtime.safety.auth.url_guard import safe_httpx_get

                resp = safe_httpx_get(
                    url,
                    timeout=timeout_ms / 1000,
                    allow_private=allow_private,
                    follow_redirects=False,
                )
            else:
                resp = client.get(url)
            break
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            label = f"{type(exc).__name__}: {exc}".lower()
            retryable = any(
                marker in label
                for marker in (
                    "ssl",
                    "tls",
                    "unexpected_eof",
                    "eof_while_reading",
                    "connection reset",
                    "remoteprotocolerror",
                    "readerror",
                    "connecterror",
                    "timeout",
                )
            )
            last_retryable = retryable
            if not retryable or attempt + 1 >= max_attempts:
                break
            time.sleep(0.15 * (attempt + 1))
    if resp is None:
        assert last_error is not None
        return {
            "error": f"http_error: {type(last_error).__name__}: {last_error}",
            "error_type": "network_error",
            "retryable": last_retryable,
            "attempts": attempt_count,
        }

    raw = resp.text
    result: dict[str, Any] = {
        "url": str(resp.url),
        "status_code": resp.status_code,
        "content_type": resp.headers.get("content-type", ""),
        "length": len(raw),
    }

    if extract:
        extracted = _extract_main_content(raw, str(resp.url))
        if extracted is not None:
            text = extracted["text"]
            truncated = len(text) > max_bytes
            if truncated:
                text = text[:max_bytes]
            result.update(
                {
                    "extracted": True,
                    "truncated": truncated,
                    "content": text,
                    "metadata": extracted["metadata"],
                }
            )
            return result
        result["extract_failed"] = (
            "trafilatura_unavailable" if not TRAFILATURA_AVAILABLE else "no_main_content"
        )

    body = raw
    truncated = len(body) > max_bytes
    if truncated:
        body = body[:max_bytes]
    result.update(
        {
            "extracted": False,
            "truncated": truncated,
            "content": body,
        }
    )
    return result


# ═══════════════════════════════════════════════════════════
# web_search
# ═══════════════════════════════════════════════════════════

# Snippets were hard-truncated to 400 chars, which silently dropped the
# exact numbers/figures the model needs (e.g. "17.6 亿美元"). Raise the cap
# and keep it in one place so every backend stays consistent.
_SNIPPET_CAP = 2000

_SEARCH_STOP_WORDS = {
    "and",
    "are",
    "for",
    "from",
    "how",
    "into",
    "not",
    "or",
    "the",
    "this",
    "what",
    "when",
    "where",
    "which",
    "with",
}


def _search_terms(query: str) -> set[str]:
    """Extract stable terms used only to detect catastrophically bad hits."""
    return {
        token
        for token in re.findall(r"[a-z0-9]+", (query or "").lower())
        if len(token) >= 3 and token not in _SEARCH_STOP_WORDS
    }


_PRIMARY_SOURCE_DOMAINS = (
    "courtlistener.com",
    "govinfo.gov",
    "justia.com",
    "law.justia.com",
    "pacermonitor.com",
    "patents.google.com",
    "sec.gov",
    "supremecourt.gov",
    "uscourts.gov",
    "uspto.gov",
)
_LOW_SIGNAL_DOMAINS = (
    "baijiahao.baidu.com",
    "facebook.com",
    "instagram.com",
    "pinterest.com",
    "reddit.com",
    "sohu.com",
    "tiktok.com",
    "wikipedia.org",
    "youtube.com",
    "zhihu.com",
)
_TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "ref",
    "source",
    "utm_campaign",
    "utm_content",
    "utm_medium",
    "utm_source",
    "utm_term",
}


def _canonical_search_url(url: str) -> str:
    """Canonical URL used for cross-engine result deduplication."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    query = urlencode(
        [
            (key, value)
            for key, value in parse_qsl(parts.query)
            if key.lower() not in _TRACKING_QUERY_KEYS
        ]
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def _source_quality(url: str) -> tuple[str, int]:
    """Return a transparent source tier and a small ranking bonus."""
    host = (urlsplit(url).hostname or "").lower()
    if (
        host.endswith(".gov")
        or host.endswith(".edu")
        or any(host == domain or host.endswith(f".{domain}") for domain in _PRIMARY_SOURCE_DOMAINS)
    ):
        return "primary", 6
    if any(host == domain or host.endswith(f".{domain}") for domain in _LOW_SIGNAL_DOMAINS):
        return "community", -5
    return "web", 0


def _matched_search_terms(query: str, item: dict[str, Any]) -> set[str]:
    terms = _search_terms(query)
    haystack = " ".join(str(item.get(field) or "") for field in ("title", "url", "snippet")).lower()
    return {term for term in terms if term in haystack}


def _results_look_irrelevant(query: str, results: list[dict[str, Any]]) -> bool:
    """True only when a multi-term query has no lexical anchor in any hit.

    This is a narrow circuit breaker for failures such as an ``Eight Sleep``
    lawsuit query returning only the Wikipedia page for the number 8.  It is
    not a general relevance ranker: one matching meaningful term is enough to
    keep a result set.
    """
    terms = _search_terms(query)
    if len(terms) < 2 or not results:
        return False
    required = 2 if len(terms) >= 3 else 1
    relevant_count = sum(
        1 for item in results if len(_matched_search_terms(query, item)) >= required
    )
    if relevant_count == 0:
        return True
    # One valid hit buried under a page of entity drift is still a failed
    # result set: the model should not have to discover the only useful source
    # among definitions, social posts and unrelated videos.
    return len(results) >= 5 and relevant_count / len(results) < 0.3


def _rank_search_results(
    query: str,
    results: list[dict[str, Any]],
    *,
    drop_irrelevant: bool = False,
) -> list[dict[str, Any]]:
    """Deduplicate, filter obvious drift, and rank by relevance + authority."""
    terms = _search_terms(query)
    entity_phrases = [
        " ".join(match.split()).lower()
        for match in re.findall(r"\b(?:[A-Z][\w-]+\s+){1,3}[A-Z][\w-]+\b", query or "")
    ]
    seen: set[str] = set()
    ranked: list[tuple[int, dict[str, Any]]] = []

    for original in results:
        item = dict(original)
        canonical_url = _canonical_search_url(str(item.get("url") or ""))
        dedupe_key = canonical_url or " ".join(str(item.get("title") or "").lower().split())
        if not dedupe_key or dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        title = str(item.get("title") or "").lower()
        haystack = " ".join(
            str(item.get(field) or "") for field in ("title", "url", "snippet")
        ).lower()
        matched = _matched_search_terms(query, item)
        title_matches = sum(1 for term in terms if term in title)
        source_tier, source_bonus = _source_quality(str(item.get("url") or ""))
        value = len(matched) * 3 + title_matches * 2 + source_bonus
        value += 5 * sum(1 for phrase in entity_phrases if phrase in haystack)
        item["source_quality"] = source_tier
        if terms:
            item["relevance_score"] = round(len(matched) / len(terms), 2)
        ranked.append((value, item))

    ranked.sort(key=lambda pair: pair[0], reverse=True)
    if drop_irrelevant and len(terms) >= 3:
        relevant = [pair for pair in ranked if len(_matched_search_terms(query, pair[1])) >= 2]
        # Returning no result is safer than feeding the model a confident page
        # of entity drift. The caller has already attempted the configured
        # backend fallback before this finalization point.
        ranked = relevant
    return [item for _, item in ranked]


def _finalize_search_result(result: dict[str, Any], query: str, max_results: int) -> dict[str, Any]:
    if result.get("error"):
        return result
    finalized = dict(result)
    finalized["results"] = _rank_search_results(
        query,
        list(result.get("results") or []),
        drop_irrelevant=result.get("quality_warning") == "low_relevance",
    )[:max_results]
    finalized["result_count"] = len(finalized["results"])
    return finalized


def _resolve_backend() -> str:
    explicit = (os.environ.get("WEB_SEARCH_BACKEND") or "").strip().lower()
    if explicit:
        return explicit
    if os.environ.get("DOUBAO_SEARCH_API_KEY"):
        return "doubao"
    if os.environ.get("TAVILY_API_KEY"):
        return "tavily"
    if os.environ.get("BRAVE_API_KEY"):
        return "brave"
    if os.environ.get("SERPER_API_KEY"):
        return "serper"
    if os.environ.get("SEARXNG_URL"):
        return "searxng"
    return "ddg"


def _available_backends() -> list[str]:
    """All backends that have credentials / are usable, in fallback priority."""
    backs: list[str] = []
    if os.environ.get("SEARXNG_URL"):
        backs.append("searxng")
    if os.environ.get("DOUBAO_SEARCH_API_KEY"):
        backs.append("doubao")
    if os.environ.get("TAVILY_API_KEY"):
        backs.append("tavily")
    if os.environ.get("BRAVE_API_KEY"):
        backs.append("brave")
    if os.environ.get("SERPER_API_KEY"):
        backs.append("serper")
    backs.append("ddg")  # keyless last resort
    return backs


def _web_search(
    query: str = "",
    *,
    max_results: int = 5,
    timeout_ms: int = 8000,
    client: Any = None,
    backend: str | None = None,
    **_kw: Any,
) -> dict[str, Any]:
    if not query:
        return {"error": "missing query", "results": []}

    chosen = (backend or _resolve_backend()).lower()
    close_after = False
    if client is None:
        if not HTTPX_AVAILABLE:
            return {"error": "httpx not installed", "results": []}
        client = httpx.Client(timeout=timeout_ms / 1000)
        close_after = True

    retrieval_limit = min(max(max_results * 3, 10), 30)
    try:
        result = _dispatch_search(client, chosen, query, retrieval_limit)
        error_code = str(result.get("error") or "")
        # ``backend`` is exposed to the model as a routing hint, so the model
        # can occasionally name a provider that is not configured locally.
        # Missing credentials and an open SearXNG circuit are not meaningful
        # hard selections: recover through another usable backend instead of
        # surfacing an avoidable tool failure to the conversation.
        recoverable_backend_error = (
            error_code.endswith("_missing_key")
            or error_code == "searxng_temporarily_unhealthy"
            or error_code == "searxng_missing_url"
        )
        # Fallback chain (Hermes parity): if the configured/default backend
        # errors, try every other available backend once before giving up.
        # Explicit, usable providers remain hard selections. An unavailable
        # provider is treated as a hint because model-generated tool arguments
        # must not turn absent local credentials into a fatal research error.
        if result.get("error") and (backend is None or recoverable_backend_error):
            for alt in _available_backends():
                if alt == chosen:
                    continue
                alt_res = _dispatch_search(client, alt, query, retrieval_limit)
                if (
                    not alt_res.get("error")
                    and alt_res.get("results")
                    and not _results_look_irrelevant(query, alt_res["results"])
                ):
                    alt_res["fallback_from"] = chosen
                    alt_res["fallback_reason"] = "backend_error"
                    return _finalize_search_result(alt_res, query, max_results)
        # Some engines return syntactically valid but catastrophically
        # irrelevant results (for example interpreting "Eight Sleep" as the
        # number 8). Treat that as a soft backend failure and try another
        # available provider once. Explicit backend selection remains hard.
        if (
            backend is None
            and not result.get("error")
            and _results_look_irrelevant(query, result.get("results") or [])
        ):
            for alt in _available_backends():
                if alt == chosen:
                    continue
                alt_res = _dispatch_search(client, alt, query, retrieval_limit)
                if alt_res.get("error") or not alt_res.get("results"):
                    continue
                if _results_look_irrelevant(query, alt_res["results"]):
                    continue
                alt_res["fallback_from"] = chosen
                alt_res["fallback_reason"] = "low_relevance"
                return _finalize_search_result(alt_res, query, max_results)
            result["quality_warning"] = "low_relevance"
        # A healthy backend can still return zero hits because of throttling
        # or an over-specific query. Try the remaining configured providers
        # before relaxing the query on the same backend.
        if (
            backend is None
            and chosen == "ddg"
            and not result.get("error")
            and not result.get("results")
        ):
            for alt in _available_backends():
                if alt == chosen:
                    continue
                alt_res = _dispatch_search(client, alt, query, retrieval_limit)
                if (
                    alt_res.get("error")
                    or not alt_res.get("results")
                    or _results_look_irrelevant(query, alt_res["results"])
                ):
                    continue
                alt_res["fallback_from"] = chosen
                alt_res["fallback_reason"] = "empty_results"
                return _finalize_search_result(alt_res, query, max_results)
        # Near-miss recovery (Hermes parity): a query that returns nothing is
        # retried once with a loosened form — quotes / parens / operators
        # stripped, and the first N words kept. Models often over-qualify a
        # query with exact phrases that no index matches; the relaxed form
        # probes whether the topic exists at all. The retry keeps the original
        # ``query`` on the result so the model can tell the hit is approximate.
        if not result.get("results") and not result.get("error"):
            relaxed = _relaxed_query(query)
            if relaxed and relaxed != query:
                retry = _dispatch_search(client, chosen, relaxed, retrieval_limit)
                if retry.get("results"):
                    retry["query"] = relaxed
                    retry["near_miss_retry"] = True
                    retry["original_query"] = query
                    return _finalize_search_result(retry, relaxed, max_results)
        return _finalize_search_result(result, query, max_results)
    finally:
        if close_after:
            client.close()


def _dispatch_search(
    client: Any,
    chosen: str,
    query: str,
    max_results: int,
) -> dict[str, Any]:
    """Route a web search to the configured backend."""
    if chosen == "doubao":
        key = os.environ.get("DOUBAO_SEARCH_API_KEY", "")
        if not key:
            return {"error": "doubao_missing_key", "results": []}
        return _doubao_search(client, key, query, max_results)
    if chosen == "tavily":
        key = os.environ.get("TAVILY_API_KEY", "")
        if not key:
            return {"error": "tavily_missing_key", "results": []}
        return _tavily_search(client, key, query, max_results)
    if chosen == "brave":
        key = os.environ.get("BRAVE_API_KEY", "")
        if not key:
            return {"error": "brave_missing_key", "results": []}
        return _brave_search(client, key, query, max_results)
    if chosen == "serper":
        key = os.environ.get("SERPER_API_KEY", "")
        if not key:
            return {"error": "serper_missing_key", "results": []}
        return _serper_search(client, key, query, max_results)
    if chosen == "searxng":
        base = os.environ.get("SEARXNG_URL", "")
        if not base:
            return {"error": "searxng_missing_url", "results": []}
        return _searxng_search(client, base, query, max_results)
    if chosen == "ddg":
        return _ddg_search(client, query, max_results)
    return {"error": f"unknown_backend: {chosen}", "results": []}


def _relaxed_query(query: str, max_words: int = 6) -> str:
    """Loosen an over-qualified search query for a near-miss retry.

    Strips quoted phrases, parentheses, boolean operators and site:/filetype:
    filters, collapses whitespace, then keeps the first ``max_words`` tokens.
    Returns an empty string when nothing searchable remains.
    """
    import re as _re

    # Quoted phrases: strip the quotes but keep the words — a "near miss"
    # retry loosens exact-phrase matching, it does not throw the topic away.
    stripped = _re.sub(r'"([^"]*)"', r" \1 ", query)
    stripped = _re.sub(r"[()\[\]{}]", " ", stripped)  # grouping
    # Boolean operators and field filters (site:example.com → drop both).
    stripped = _re.sub(
        r"\b(?:AND|OR|NOT)\b",
        " ",
        stripped,
        flags=_re.IGNORECASE,
    )
    stripped = _re.sub(
        r"\b(?:site|filetype|intitle|inurl):\S*",
        " ",
        stripped,
        flags=_re.IGNORECASE,
    )
    tokens = [t for t in stripped.split() if t]
    return " ".join(tokens[:max_words]).strip()


def _doubao_search(client: Any, api_key: str, query: str, max_results: int) -> dict[str, Any]:
    """豆包搜索 (Doubao Search) — 火山引擎为 AI Agent 构建的联网搜索服务。

    使用 Global 版端点 (``search_api/global_search``)，覆盖全球站点、每条结果带
    ``ContentTokenCount`` 等对 Agent 友好的字段。返回字段参考官方文档与开源实现
    huashu-doubao-search。
    """
    endpoint = "https://open.feedcoopapi.com/search_api/global_search"
    try:
        r = client.post(
            endpoint,
            json={
                "query": query,
                "doc_count": max_results,
                "max_snippet_length": _SNIPPET_CAP,
                "max_image_count_per_doc": 0,
            },
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:  # noqa: BLE001
        return {"error": f"doubao_error: {type(e).__name__}: {e}", "results": []}

    meta_err = (data.get("ResponseMetadata") or {}).get("Error")
    if meta_err:
        return {
            "error": f"doubao_api_error: {str(meta_err.get('Message') or meta_err)[:300]}",
            "results": [],
        }

    docs = (data.get("Result") or {}).get("Documents") or []
    results: list[dict[str, str]] = []
    for item in docs[:max_results]:
        snippets: list[str] = []
        for part in item.get("Snippet") or []:
            if part.get("Type") == "text" and part.get("Text"):
                snippets.append(str(part["Text"]).strip())
        doc_info = item.get("DocumentInfo") or {}
        results.append(
            {
                "title": item.get("Title") or "",
                "url": item.get("Url") or "",
                "snippet": "\n".join(snippets)[:_SNIPPET_CAP],
                "host": (item.get("HostInfo") or {}).get("Hostname") or "",
                "publish_time": doc_info.get("PublishTime") or "",
            }
        )
    return {"query": query, "backend": "doubao", "results": results}


def _tavily_search(client: Any, api_key: str, query: str, max_results: int) -> dict[str, Any]:
    try:
        r = client.post(
            "https://api.tavily.com/search",
            json={"api_key": api_key, "query": query, "max_results": max_results},
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:  # noqa: BLE001
        return {"error": f"tavily_error: {type(e).__name__}: {e}", "results": []}

    results = [
        {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": item.get("content", "")[:_SNIPPET_CAP],
        }
        for item in data.get("results", [])[:max_results]
    ]
    return {"query": query, "backend": "tavily", "results": results}


def _brave_search(client: Any, api_key: str, query: str, max_results: int) -> dict[str, Any]:
    try:
        r = client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": max_results},
            headers={
                "X-Subscription-Token": api_key,
                "Accept": "application/json",
            },
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:  # noqa: BLE001
        return {"error": f"brave_error: {type(e).__name__}: {e}", "results": []}

    web = (data.get("web") or {}).get("results") or []
    results = [
        {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": (item.get("description") or "")[:_SNIPPET_CAP],
        }
        for item in web[:max_results]
    ]
    return {"query": query, "backend": "brave", "results": results}


def _serper_search(client: Any, api_key: str, query: str, max_results: int) -> dict[str, Any]:
    try:
        r = client.post(
            "https://google.serper.dev/search",
            json={"q": query, "num": max_results},
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:  # noqa: BLE001
        return {"error": f"serper_error: {type(e).__name__}: {e}", "results": []}

    organic = data.get("organic") or []
    results = [
        {
            "title": item.get("title", ""),
            "url": item.get("link", ""),
            "snippet": (item.get("snippet") or "")[:_SNIPPET_CAP],
        }
        for item in organic[:max_results]
    ]
    return {"query": query, "backend": "serper", "results": results}


_SEARXNG_FAILURE_LOCK = threading.Lock()
_SEARXNG_UNHEALTHY_UNTIL = 0.0
_SEARXNG_COOLDOWN_S = 60.0


def _searxng_search(client: Any, base_url: str, query: str, max_results: int) -> dict[str, Any]:
    global _SEARXNG_UNHEALTHY_UNTIL
    now = time.monotonic()
    with _SEARXNG_FAILURE_LOCK:
        unhealthy_until = _SEARXNG_UNHEALTHY_UNTIL
    if unhealthy_until > now:
        return {
            "error": "searxng_temporarily_unhealthy",
            "results": [],
            "retry_after_s": max(1, int(unhealthy_until - now)),
        }
    endpoint = base_url.rstrip("/") + "/search"
    headers: dict[str, str] = {"Accept": "application/json"}
    api_key = os.environ.get("SEARXNG_API_KEY", "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        r = client.get(
            endpoint,
            params={
                "q": query,
                "format": "json",
                "engines": "google,bing,brave,duckduckgo",
                "categories": "general",
            },
            headers=headers,
            timeout=3.0,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:  # noqa: BLE001
        with _SEARXNG_FAILURE_LOCK:
            _SEARXNG_UNHEALTHY_UNTIL = time.monotonic() + _SEARXNG_COOLDOWN_S
        return {"error": f"searxng_error: {type(e).__name__}: {e}", "results": []}

    with _SEARXNG_FAILURE_LOCK:
        _SEARXNG_UNHEALTHY_UNTIL = 0.0

    items = data.get("results") or []
    results = [
        {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": (item.get("content") or "")[:_SNIPPET_CAP],
        }
        for item in items[:max_results]
    ]
    return {
        "query": query,
        "backend": "searxng",
        "results": _rank_search_results(query, results),
    }


def _ddgs_text_search(query: str, max_results: int) -> list[dict[str, str]] | None:
    """Search through the maintained DDGS adapter when it is available.

    Returning ``None`` asks the caller to use the keyless HTML fallback. DDGS
    aggregates multiple public search sources, but upstream throttling is
    expected and must never abort an agent turn.
    """
    try:
        from ddgs import DDGS
    except ImportError:
        return None

    try:
        raw_items = DDGS().text(query, max_results=max(max_results * 2, 10))
    except Exception:  # noqa: BLE001
        return None

    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw_items or []:
        url = str(item.get("href") or item.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        results.append(
            {
                "title": str(item.get("title") or "").strip(),
                "url": url,
                "snippet": str(item.get("body") or item.get("snippet") or "").strip()[
                    :_SNIPPET_CAP
                ],
            }
        )
        if len(results) >= max_results:
            break
    return results or None


def _ddg_search(client: Any, query: str, max_results: int) -> dict[str, Any]:
    # A caller-supplied client represents an explicit network boundary (proxy,
    # enterprise transport, fixture, or policy-enforcing adapter). DDGS owns
    # its own transport, so invoking it for arbitrary client-like objects would
    # silently bypass that boundary. Use the aggregator only for Echo's
    # ordinary in-process httpx client; otherwise keep all traffic on the
    # injected client's HTML path.
    use_library_adapter = bool(HTTPX_AVAILABLE and isinstance(client, httpx.Client))
    library_results = _ddgs_text_search(query, max_results) if use_library_adapter else None
    if library_results:
        return {
            "query": query,
            "backend": "ddg",
            "adapter": "ddgs",
            "results": library_results,
        }

    try:
        r = client.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers={"User-Agent": "echo-agent/0.1"},
        )
    except Exception as e:  # noqa: BLE001
        return {"error": f"ddg_error: {type(e).__name__}: {e}", "results": []}

    text = r.text
    # DDG HTML: <a class="result__a" href="URL">TITLE</a>
    import re

    items = re.findall(
        r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        text,
        flags=re.DOTALL,
    )
    snippets = re.findall(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', text, flags=re.DOTALL)

    def _clean(s: str) -> str:
        s = re.sub(r"<[^>]+>", "", s)
        return re.sub(r"\s+", " ", s).strip()

    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for i, (url, title) in enumerate(items[:max_results]):
        if not url or url in seen:
            continue
        seen.add(url)
        snippet = snippets[i] if i < len(snippets) else ""
        results.append(
            {
                "title": _clean(title),
                "url": url,
                "snippet": _clean(snippet)[:_SNIPPET_CAP],
            }
        )
    return {
        "query": query,
        "backend": "ddg",
        "adapter": "html-fallback",
        "results": results,
    }


# ═══════════════════════════════════════════════════════════
# web_fetch — extract just the answer to a prompt via cheap LLM
# ═══════════════════════════════════════════════════════════


_WEB_FETCH_SYSTEM = (
    "You extract specific information from web pages. Return ONLY the answer "
    "to the user's question, no preamble. If the page doesn't contain the "
    "answer, say 'not found in page'."
)


def set_web_fetch_router(router: Any, *, default_model: str | None = None) -> None:
    """Install the lightweight LLM route used by ``web_fetch``."""
    from runtime.platform.process.service_provider import get_provider

    provider = get_provider()
    provider.register_instance("web_fetch_cheap", router)
    provider.register_instance("web_fetch_default_model", default_model)


def _strip_html_fallback(html: str) -> str:
    """Regex-based fallback when trafilatura is unavailable.

    Strips <script>/<style> blocks then tags via html.parser, collapsing
    whitespace. Lossy but enough for ad-hoc Q&A.
    """
    import re
    from html.parser import HTMLParser

    cleaned = re.sub(
        r"<(script|style)\b[^>]*>.*?</\1>",
        " ",
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )

    class _Stripper(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self._chunks: list[str] = []

        def handle_data(self, data: str) -> None:
            self._chunks.append(data)

        def text(self) -> str:
            return "".join(self._chunks)

    parser = _Stripper()
    try:  # noqa: SIM105
        parser.feed(cleaned)
    except Exception:  # noqa: BLE001 — malformed HTML; return what we got
        pass
    return re.sub(r"\s+", " ", parser.text()).strip()


def _extract_text_for_prompt(html: str, url: str) -> str:
    """Try trafilatura.extract first, fall back to regex strip."""
    if TRAFILATURA_AVAILABLE and html:
        try:
            text = trafilatura.extract(
                html,
                url=url,
                include_links=False,
                include_comments=False,
                favor_precision=True,
            )
        except (OSError, ValueError):
            text = None
        if text:
            return text
    return _strip_html_fallback(html)


def _web_fetch(
    url: str = "",
    prompt: str = "",
    *,
    max_chars: int = 16000,
    cheap_model: str | None = None,
    client: Any = None,
    allow_private: bool = False,
    timeout_ms: int = 5000,
    llm_timeout_ms: int = 30_000,
    _llm_caller: Any = None,
    _trafilatura_override: Any = "__unset__",
    _rendered_fetcher: Any = None,
    **_kw: Any,
) -> dict[str, Any]:
    if not prompt:
        question = _kw.get("question")
        if isinstance(question, str) and question.strip():
            prompt = question
    if not url:
        return {"error": "missing url", "error_type": "invalid_argument"}
    if not prompt:
        return {"error": "missing prompt", "error_type": "invalid_argument"}

    # Step 1+2: reuse fetch_url to do GET + SSRF guard.
    fetched = _fetch_url(
        url=url,
        timeout_ms=timeout_ms,
        max_bytes=max(max_chars * 4, 200_000),
        client=client,
        allow_private=allow_private,
        extract=False,
    )
    if "error" in fetched:
        return {
            "error": fetched["error"],
            "error_type": "network_error",
            "url": url,
            "retryable": fetched.get("retryable", False),
            "attempts": fetched.get("attempts", 1),
            "recovery_hint": (
                "Do not end the task because one URL failed. Retry once, then use "
                "web_search to locate the same document on an official mirror, "
                "court docket, patent database, or cached source."
            ),
        }

    raw_html = fetched.get("content", "") or ""
    final_url = fetched.get("url", url)

    # Step 3+4: extract + truncate.
    if _trafilatura_override == "__unset__":
        extracted_text = _extract_text_for_prompt(raw_html, final_url)
    elif _trafilatura_override is None:
        # Test hook: simulate trafilatura import-missing.
        extracted_text = _strip_html_fallback(raw_html)
    else:
        extracted_text = _trafilatura_override

    if len(extracted_text) > max_chars:
        extracted_text = extracted_text[:max_chars]

    fetch_mode = "http"
    if not extracted_text:
        # JS-only shells frequently return a valid HTTP 200 with no readable
        # body. Escalate internally to a background renderer; do not expose or
        # open the user's interactive browser merely because extraction was
        # empty. Explicit Browser/Chrome turns use their own browser tools.
        rendered_fetcher = _rendered_fetcher
        if rendered_fetcher is None:
            try:
                from runtime.execution.suckers.browser_skills import _browser_get

                rendered_fetcher = _browser_get
            except ImportError:
                rendered_fetcher = None
        if callable(rendered_fetcher):
            try:
                rendered = rendered_fetcher(
                    url=final_url,
                    timeout_ms=max(timeout_ms, 10_000),
                    wait_ms=800,
                    max_bytes=max_chars,
                    allow_private=allow_private,
                    _background_only=True,
                )
            except Exception:  # noqa: BLE001 - optional recovery lane
                rendered = {}
            rendered_text = rendered.get("content") if isinstance(rendered, dict) else ""
            if isinstance(rendered_text, str) and rendered_text.strip():
                extracted_text = rendered_text.strip()
                final_url = str(rendered.get("url") or final_url)
                fetch_mode = "background_browser"

    if not extracted_text:
        return {
            "error": "empty_extract",
            "error_type": "network_error",
            "url": final_url,
            "recovery_hint": (
                "HTTP extraction and background rendering both returned no readable text. "
                "Use web_search for an official mirror or cached source; only request an "
                "interactive browser if login, clicking, or visual inspection is required."
            ),
        }

    # Step 5+6: cheap LLM call.
    caller = _llm_caller
    if caller is None:
        try:
            from runtime.platform.llm_infra.llm_caller import LLMCaller

            caller = LLMCaller("web_fetch_cheap", "web_fetch_default_model")
        except Exception as exc:  # noqa: BLE001
            return {
                "error": f"llm_caller_unavailable: {exc}",
                "error_type": "unsupported",
                "url": final_url,
                "fallback_extract": extracted_text,
            }

    user_msg = f"URL: {final_url}\nQuestion: {prompt}\n\nPage content:\n{extracted_text}"
    result_queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)
    caller_context = contextvars.copy_context()

    def _call_llm() -> None:
        try:
            result = caller.call(
                system=_WEB_FETCH_SYSTEM,
                user=user_msg,
                model=cheap_model,
                max_tokens=512,
                temperature=0.1,
            )
        except Exception as exc:  # noqa: BLE001 — returned to the caller thread
            result_queue.put(("error", exc))
        else:
            result_queue.put(("result", result))

    worker = threading.Thread(
        target=lambda: caller_context.run(_call_llm),
        name="web-fetch-llm",
        daemon=True,
    )
    worker.start()
    try:
        kind, payload = result_queue.get(timeout=max(0.001, llm_timeout_ms / 1000))
    except queue.Empty:
        return {
            "error": f"llm_timeout: no response within {llm_timeout_ms}ms",
            "error_type": "llm_timeout",
            "url": final_url,
            "fallback_extract": extracted_text,
        }
    if kind == "error":
        exc = payload
        return {
            "error": f"llm_failed: {type(exc).__name__}: {exc}",
            "error_type": "llm_failed",
            "url": final_url,
            "fallback_extract": extracted_text,
        }
    answer_text, meta = payload

    if not answer_text or (isinstance(meta, dict) and meta.get("error")):
        return {
            "error": (
                f"llm_failed: {meta.get('error', 'empty response')}"
                if isinstance(meta, dict)
                else "llm_failed: empty response"
            ),
            "error_type": "llm_failed",
            "url": final_url,
            "fallback_extract": extracted_text,
        }

    return {
        "ok": True,
        "url": final_url,
        "prompt": prompt,
        "answer": answer_text.strip(),
        "extracted_chars": len(extracted_text),
        "fetch_mode": fetch_mode,
        "model": (meta or {}).get("model") if isinstance(meta, dict) else None,
    }


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


WEB_SKILL_NAMES = ["fetch_url", "web_search", "web_fetch"]


def register_web_skills(registry: SkillRegistry) -> int:
    if not HTTPX_AVAILABLE:
        return 0

    registry.register(
        Skill(
            name="fetch_url",
            description=(
                "用途: 对一个已知 URL 发 HTTP GET；默认返回原始 body 文本 (有上限)。开 extract=true 走 trafilatura 抽取正文 + 元数据 (title/author/date/sitename/description/language)，把导航/广告/页脚剥掉。\n"
                "何时不用: 不知道目标网址、要先「搜一下」用 web_search；要执行本地命令用 exec_shell (curl/wget 不要绕道这里)；私网地址默认被 SSRF 拦截，需要时显式 allow_private=true。\n"
                "关键参数: url (必填); extract (默认 False, 阅读文章问答时建议 True); timeout_ms (默认 5000); max_bytes (默认 100000)。\n"
                '示例: fetch_url({"url": "https://example.com/post", "extract": true})'
            ),
            affinity=["web", "io"],
            cost_profile="low",
            trusted_source="skill://public/fetch_url",
            handler=_fetch_url,
            tests=[
                SkillTestCase(
                    name="missing_url_returns_error",
                    tier="golden",
                    args={"url": ""},
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        )
    )
    registry.register(
        Skill(
            name="web_search",
            description=(
                "用途: 网上检索 — 任何「需要谷歌一下」的查询 (新闻、价格、定义、X 的现状、近期事件、产品对比) 都走这里；返回结构化 [{title, url, snippet}]。\n"
                "何时不用: 已经知道具体 URL 直接读用 fetch_url；不要用 exec_shell 跑 curl/wget 拿 HTML (没法解析)；查本地代码/配置用 grep_text 或 glob_files。\n"
                "关键参数: query (必填); max_results (默认 5); backend (可选, 留空时按环境变量自动选 doubao/tavily/brave/serper/searxng/ddg)。\n"
                "路由提示: 通常不要填写 backend，让系统按当前可用凭据自动选择和故障降级；仅当用户明确要求某个搜索源时再指定。\n"
                "研究要求: 复杂调研至少使用两组不同关键词，优先读取 source_quality=primary 的原始来源；搜索摘要只用于发现候选 URL，关键结论必须再用 web_fetch 核验正文。\n"
                '示例: web_search({"query": "langgraph 0.2 release notes", "max_results": 5})'
            ),
            affinity=["web", "search"],
            cost_profile="low",
            trusted_source="skill://public/web_search",
            handler=_web_search,
            tests=[
                SkillTestCase(
                    name="empty_query_returns_error",
                    tier="golden",
                    args={"query": ""},
                    expect=SkillExpect(schema_keys=["error", "results"]),
                ),
            ],
        )
    )
    registry.register(
        Skill(
            name="web_fetch",
            description=(
                "用途: 给一个 URL + 一个问题，由廉价 LLM 在页面正文里抽出答案；只把 answer 字符串回给主模型，不再让主模型啃 50KB 原始 HTML。\n"
                "何时不用: 只想拿原文 / 自己解析用 fetch_url(extract=true)；不知道目标网址先用 web_search；要本地文件 Q&A 用 read_file 自己问。\n"
                "关键参数: url (必填); prompt (必填, 你想从页面里得到的答案); max_chars (送进 LLM 的正文上限, 默认 16000); cheap_model (可选, 留空走 web_fetch_default_model)。\n"
                '示例: web_fetch({"url": "https://docs.example.com/limits", "prompt": "What is the rate limit?"})'
            ),
            affinity=["web", "io", "llm"],
            cost_profile="mid",
            trusted_source="skill://public/web_fetch",
            handler=_web_fetch,
            tests=[
                SkillTestCase(
                    name="missing_url_returns_error",
                    tier="golden",
                    args={"url": "", "prompt": "what?"},
                    expect=SkillExpect(schema_keys=["error", "error_type"]),
                ),
                SkillTestCase(
                    name="missing_prompt_returns_error",
                    tier="golden",
                    args={"url": "https://example.com/", "prompt": ""},
                    expect=SkillExpect(schema_keys=["error", "error_type"]),
                ),
            ],
        )
    )
    # Native multi-platform reach shares the web capability gate. Importing
    # here avoids a module cycle because reach routes reuse _web_search and
    # _fetch_url at execution time.
    from .reach_skills import register_reach_skills

    return 3 + register_reach_skills(registry)
