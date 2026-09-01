from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from runtime.safety.auth.url_guard import check_url

from .cache import reach_cache
from .channels import read_bilibili, read_github, read_rss, read_youtube, search_github
from .quality import rank_and_dedupe
from .resilience import host_rate_limiter

try:
    import httpx  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    httpx = None

_ALIASES = {
    "b站": "bilibili",
    "哔哩哔哩": "bilibili",
    "youtube": "youtube",
    "yt": "youtube",
    "github": "github",
    "gh": "github",
    "reddit": "reddit",
    "rss": "rss",
    "x": "x",
    "twitter": "x",
    "小红书": "xiaohongshu",
    "xhs": "xiaohongshu",
    "抖音": "douyin",
    "douyin": "douyin",
    "tiktok-cn": "douyin",
    "头条": "toutiao",
    "今日头条": "toutiao",
    "toutiao": "toutiao",
    "豆包": "doubao",
    "doubao": "doubao",
    "web": "web",
}

_SITE_FILTERS = {
    "youtube": "site:youtube.com",
    "bilibili": "site:bilibili.com/video",
    "reddit": "site:reddit.com",
    "x": "site:x.com",
    "xiaohongshu": "site:xiaohongshu.com",
    "douyin": "site:douyin.com",
    "toutiao": "site:toutiao.com",
    "doubao": "site:doubao.com",
}


def normalize_platform(value: str) -> str:
    raw = (value or "web").strip().lower()
    return _ALIASES.get(raw, raw)


def _client(timeout_ms: int) -> Any:
    if httpx is None:
        raise RuntimeError("httpx_not_installed")
    return httpx.Client(
        timeout=max(1, timeout_ms) / 1000,
        # Redirects are not followed: the entry-point ``check_url`` only vets the
        # URL we were handed, so an allowed host could 302 to a private address
        # and defeat the guard. Channels that legitimately need a hop should
        # re-enter through a checked call.
        follow_redirects=False,
        headers={"User-Agent": "echo-agent-reach/0.1"},
        transport=httpx.HTTPTransport(retries=2),
    )


def platform_search(
    platform: str = "web",
    query: str = "",
    *,
    max_results: int = 10,
    timeout_ms: int = 12_000,
    client: Any = None,
    web_search: Any = None,
    **_: Any,
) -> dict[str, Any]:
    if not query.strip():
        return {"error": "missing query", "results": []}
    target = normalize_platform(platform)
    limit = max(1, min(int(max_results), 50))
    use_cache = client is None and web_search is None
    cache_key = ("search", target, query.strip(), limit)
    cached = reach_cache.get(cache_key) if use_cache else None
    if cached is not None:
        return cached
    close_after = client is None
    try:
        client = client or _client(timeout_ms)
        if target == "github":
            if not host_rate_limiter.acquire("https://api.github.com"):
                return {
                    "error": "rate_limited",
                    "platform": target,
                    "results": [],
                    "retry_after_seconds": 60,
                }
            result = search_github(client, query.strip(), limit)
            result["results"] = rank_and_dedupe(result.get("results") or [], query.strip())
            if use_cache:
                reach_cache.put(cache_key, result, 120)
            return result
        if target == "rss":
            return {"error": "rss_search_not_supported", "results": [], "platform": target}
        if web_search is None:
            from runtime.execution.suckers.web_skills import _web_search

            web_search = _web_search
        routed_query = query.strip()
        if target == "reddit":
            routed_query = f"!rd {routed_query}"
        elif target in _SITE_FILTERS:
            routed_query = f"{_SITE_FILTERS[target]} {routed_query}"
        result = web_search(query=routed_query, max_results=limit, timeout_ms=timeout_ms)
        if target == "reddit" and not result.get("error") and not result.get("results"):
            first_query = routed_query
            routed_query = f"site:reddit.com {query.strip()}"
            result = web_search(query=routed_query, max_results=limit, timeout_ms=timeout_ms)
            result["fallback_from"] = first_query
        result["platform"] = target
        result["routed_query"] = routed_query
        result["results"] = rank_and_dedupe(result.get("results") or [], query.strip())[:limit]
        if use_cache:
            reach_cache.put(cache_key, result, 120)
        return result
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}", "platform": target, "results": []}
    finally:
        if close_after and client is not None:
            client.close()


def platform_read(
    url: str = "",
    *,
    platform: str = "",
    timeout_ms: int = 15_000,
    max_bytes: int = 200_000,
    include_transcript: bool = True,
    language: str = "",
    use_browser: bool = False,
    client: Any = None,
    **_: Any,
) -> dict[str, Any]:
    if not url.strip():
        return {"error": "missing url"}
    target = normalize_platform(platform) if platform else _platform_from_url(url)
    # SSRF guard. Every branch below hands ``url`` to an HTTP client (or to the
    # browser adapter), so the check belongs here at the single entry point
    # rather than in each channel. Mirrors ``web_skills._fetch_url``'s contract
    # so callers see the same ``ssrf_blocked`` shape from either surface.
    verdict = check_url(url)
    if not verdict.allow:
        return {
            "error": f"ssrf_blocked: {verdict.reason}",
            "platform": target,
            "url": url,
            "blocked": True,
        }
    use_cache = client is None
    cache_key = (
        "read",
        target,
        url.strip(),
        max_bytes,
        include_transcript,
        language,
        use_browser,
    )
    cached = reach_cache.get(cache_key) if use_cache else None
    if cached is not None:
        return cached
    close_after = client is None
    try:
        client = client or _client(timeout_ms)
        if not host_rate_limiter.acquire(url):
            return {
                "error": "rate_limited",
                "platform": target,
                "url": url,
                "retry_after_seconds": 60,
            }
        if target == "github":
            result = read_github(client, url)
        elif target == "bilibili":
            result = read_bilibili(client, url)
        elif target == "youtube":
            result = read_youtube(
                client,
                url,
                include_transcript=include_transcript,
                language=language,
            )
        elif target == "rss":
            result = read_rss(client, url)
        elif target in {"reddit", "x", "xiaohongshu", "douyin", "doubao"}:
            if use_browser:
                from .browser_adapter import read_with_browser

                result = read_with_browser(url, timeout_ms=timeout_ms, max_bytes=max_bytes)
                result["platform"] = target
            else:
                result = {
                    "error": "browser_session_required",
                    "platform": target,
                    "url": url,
                    "requires_browser": True,
                    "repair_hint": (
                        "Open the URL with browser_navigate, then use browser_extract. "
                        "Keep the existing signed-in browser profile when login is required."
                    ),
                }
        else:
            result = None
        if result is not None:
            if use_cache:
                reach_cache.put(cache_key, result, 300)
            return result
        from runtime.execution.suckers.web_skills import _fetch_url

        generic = _fetch_url(
            url=url,
            extract=True,
            timeout_ms=timeout_ms,
            max_bytes=max_bytes,
            client=client,
        )
        generic["platform"] = target
        generic["backend"] = "fetch_url"
        if use_cache:
            reach_cache.put(cache_key, generic, 300)
        return generic
    except Exception as exc:  # noqa: BLE001
        return {
            "error": f"{type(exc).__name__}: {exc}",
            "platform": target,
            "url": url,
            "repair_hint": "Check network access and the platform route with reach_doctor.",
        }
    finally:
        if close_after and client is not None:
            client.close()


def _platform_from_url(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host.endswith("github.com"):
        return "github"
    if "youtu.be" in host or "youtube.com" in host:
        return "youtube"
    if "bilibili.com" in host or host == "b23.tv":
        return "bilibili"
    if "reddit.com" in host:
        return "reddit"
    if host == "x.com" or host.endswith("twitter.com"):
        return "x"
    if "xiaohongshu.com" in host or host == "xhslink.com":
        return "xiaohongshu"
    if "douyin.com" in host:
        return "douyin"
    if "toutiao.com" in host:
        return "toutiao"
    if "doubao.com" in host:
        return "doubao"
    return "web"
