"""Image-search provider backends for the kimi-compat skill group.

Search order in ``search_image_by_text``: an explicit ``backend`` arg (or
``IMAGE_SEARCH_BACKEND`` env) wins; otherwise any provider whose API key is
configured (Brave, Serper, SearXNG) is tried before the keyless DuckDuckGo
fallback. Split out of ``kimi_compat_skills`` so the provider adapters can
grow without turning that module into a god file.
"""

from __future__ import annotations

import html
import os
import re
from typing import Any
from urllib.parse import unquote, urlparse

try:
    import httpx  # type: ignore[import-untyped]

    HTTPX_AVAILABLE = True
except ImportError:  # pragma: no cover
    HTTPX_AVAILABLE = False
    httpx = None  # type: ignore[assignment]


_HTTP_TIMEOUT_S = 12.0
_MAX_IMAGE_RESULTS = 30


def _client(timeout_s: float = _HTTP_TIMEOUT_S) -> Any:
    if not HTTPX_AVAILABLE:
        return None
    return httpx.Client(timeout=timeout_s, follow_redirects=True)


def _unwrap_ddg_url(url: str) -> str:
    parsed = urlparse(url)
    if "duckduckgo.com" not in parsed.netloc:
        return url
    match = re.search(r"[?&]uddg=([^&]+)", url)
    return unquote(match.group(1)) if match else url


def search_image_by_text(
    query: str = "",
    *,
    max_results: int = 10,
    backend: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    if not query.strip():
        return {"error": "missing query", "results": []}
    if not HTTPX_AVAILABLE:
        return {"error": "httpx not installed", "results": []}
    max_results = max(1, min(int(max_results), _MAX_IMAGE_RESULTS))
    chosen = (backend or os.environ.get("IMAGE_SEARCH_BACKEND") or "").lower()

    with _client() as client:
        if chosen == "brave" or (not chosen and os.environ.get("BRAVE_API_KEY")):
            result = _brave_image_search(client, query, max_results)
            if "error" not in result:
                return result
        if chosen == "serper" or (not chosen and os.environ.get("SERPER_API_KEY")):
            result = _serper_image_search(client, query, max_results)
            if "error" not in result:
                return result
        if chosen == "searxng" or (not chosen and os.environ.get("SEARXNG_URL")):
            result = _searxng_image_search(client, query, max_results)
            if "error" not in result:
                return result
        return _ddg_image_search(client, query, max_results)


def _brave_image_search(client: Any, query: str, max_results: int) -> dict[str, Any]:
    key = os.environ.get("BRAVE_API_KEY", "")
    if not key:
        return {"error": "brave_missing_key", "results": []}
    try:
        r = client.get(
            "https://api.search.brave.com/res/v1/images/search",
            params={"q": query, "count": max_results},
            headers={"X-Subscription-Token": key, "Accept": "application/json"},
        )
        r.raise_for_status()
        data = r.json()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"brave_error: {type(exc).__name__}: {exc}", "results": []}
    items = data.get("results") or []
    results = []
    for item in items[:max_results]:
        props = item.get("properties") or {}
        thumb = item.get("thumbnail") or {}
        results.append(
            {
                "title": item.get("title") or "",
                "image_url": props.get("url") or item.get("url") or "",
                "thumbnail_url": thumb.get("src") or "",
                "source_url": item.get("url") or "",
                "width": props.get("width"),
                "height": props.get("height"),
            }
        )
    return {"query": query, "backend": "brave", "results": results}


def _serper_image_search(client: Any, query: str, max_results: int) -> dict[str, Any]:
    key = os.environ.get("SERPER_API_KEY", "")
    if not key:
        return {"error": "serper_missing_key", "results": []}
    try:
        r = client.post(
            "https://google.serper.dev/images",
            json={"q": query, "num": max_results},
            headers={"X-API-KEY": key, "Content-Type": "application/json"},
        )
        r.raise_for_status()
        data = r.json()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"serper_error: {type(exc).__name__}: {exc}", "results": []}
    results = [
        {
            "title": item.get("title") or "",
            "image_url": item.get("imageUrl") or "",
            "thumbnail_url": item.get("thumbnailUrl") or "",
            "source_url": item.get("link") or "",
            "width": item.get("imageWidth"),
            "height": item.get("imageHeight"),
        }
        for item in (data.get("images") or [])[:max_results]
    ]
    return {"query": query, "backend": "serper", "results": results}


def _searxng_image_search(client: Any, query: str, max_results: int) -> dict[str, Any]:
    base = os.environ.get("SEARXNG_URL", "")
    if not base:
        return {"error": "searxng_missing_url", "results": []}
    try:
        r = client.get(
            base.rstrip("/") + "/search",
            params={"q": query, "format": "json", "categories": "images"},
        )
        r.raise_for_status()
        data = r.json()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"searxng_error: {type(exc).__name__}: {exc}", "results": []}
    results = []
    for item in (data.get("results") or [])[:max_results]:
        results.append(
            {
                "title": item.get("title") or "",
                "image_url": item.get("img_src") or item.get("url") or "",
                "thumbnail_url": item.get("thumbnail") or "",
                "source_url": item.get("url") or "",
            }
        )
    return {"query": query, "backend": "searxng", "results": results}


def _ddg_image_search(client: Any, query: str, max_results: int) -> dict[str, Any]:
    try:
        page = client.get(
            "https://duckduckgo.com/",
            params={"q": query},
            headers={"User-Agent": "echo-agent/0.2"},
        )
        vqd_match = re.search(r"vqd=['\"]?([^'\"&]+)", page.text)
        if not vqd_match:
            return _ddg_html_image_fallback(client, query, max_results)
        r = client.get(
            "https://duckduckgo.com/i.js",
            params={"q": query, "vqd": vqd_match.group(1), "o": "json"},
            headers={"User-Agent": "echo-agent/0.2", "Referer": str(page.url)},
        )
        r.raise_for_status()
        data = r.json()
    except Exception:
        return _ddg_html_image_fallback(client, query, max_results)
    results = []
    for item in (data.get("results") or [])[:max_results]:
        results.append(
            {
                "title": html.unescape(item.get("title") or ""),
                "image_url": item.get("image") or "",
                "thumbnail_url": item.get("thumbnail") or "",
                "source_url": item.get("url") or "",
                "width": item.get("width"),
                "height": item.get("height"),
            }
        )
    return {"query": query, "backend": "ddg", "results": results}


def _ddg_html_image_fallback(client: Any, query: str, max_results: int) -> dict[str, Any]:
    try:
        r = client.post(
            "https://html.duckduckgo.com/html/",
            data={"q": f"{query} image"},
            headers={"User-Agent": "echo-agent/0.2"},
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": f"ddg_error: {type(exc).__name__}: {exc}", "results": []}
    links = re.findall(
        r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        r.text,
        flags=re.DOTALL,
    )
    results = []
    for url, title in links[:max_results]:
        clean_title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", title)).strip()
        results.append(
            {
                "title": html.unescape(clean_title),
                "image_url": "",
                "thumbnail_url": "",
                "source_url": _unwrap_ddg_url(url),
            }
        )
    return {"query": query, "backend": "ddg-html", "results": results}


def search_image_by_image(image_url: str = "", image_path: str = "", **_: Any) -> dict[str, Any]:
    if not image_url and not image_path:
        return {"error": "missing image_url or image_path", "results": []}
    return {
        "error": (
            "reverse_image_search_not_configured: configure a provider adapter "
            "(for example SerpAPI/Bing Visual Search) before using this tool"
        ),
        "image_url": image_url,
        "image_path": image_path,
        "results": [],
    }
