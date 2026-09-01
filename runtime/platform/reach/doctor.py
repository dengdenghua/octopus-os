from __future__ import annotations

import contextlib
import importlib.util
import os
from typing import Any

from runtime.platform.credentials import get_secret

from .models import ChannelHealth

try:
    import httpx  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    httpx = None


def diagnose_reach(timeout_ms: int = 5_000, client: Any = None, **_: Any) -> dict[str, Any]:
    close_after = client is None
    if client is None and httpx is not None:
        client = httpx.Client(timeout=max(1, timeout_ms) / 1000, follow_redirects=True)
    channels: list[ChannelHealth] = []
    searx_url = (os.environ.get("SEARXNG_URL") or "").rstrip("/")
    searx_ok = False
    if client is not None and searx_url:
        try:
            response = client.get(f"{searx_url}/search", params={"q": "health", "format": "json"})
            searx_ok = response.status_code == 200
        except Exception:  # noqa: BLE001
            pass
    channels.append(ChannelHealth("web", searx_ok, "searxng", searx_url or "not configured"))

    github_ok = False
    if client is not None:
        with contextlib.suppress(Exception):
            github_ok = client.get("https://api.github.com/rate_limit").status_code == 200
    github_authenticated = bool(get_secret("reach.github_token", env_var="GITHUB_TOKEN"))
    channels.append(
        ChannelHealth(
            "github",
            github_ok,
            "github_api_authenticated" if github_authenticated else "github_api_anonymous",
            (
                "Token is loaded from the OS keychain or GITHUB_TOKEN."
                if github_authenticated
                else "Anonymous API rate limits apply."
            ),
        )
    )
    channels.append(
        ChannelHealth(
            "youtube",
            True,
            "yt_dlp" if importlib.util.find_spec("yt_dlp") is not None else "youtube_oembed",
            "yt-dlp provides subtitle transcripts; oEmbed is the metadata-only fallback.",
        )
    )
    channels.append(ChannelHealth("bilibili", True, "bilibili_public_api"))
    channels.append(
        ChannelHealth(
            "rss",
            True,
            "feedparser" if importlib.util.find_spec("feedparser") is not None else "stdlib_xml",
        )
    )
    for platform in ("reddit", "x", "xiaohongshu", "douyin", "doubao"):
        channels.append(
            ChannelHealth(
                platform,
                True,
                "searxng_search + browser_navigate/browser_extract",
                "Public search works; full reading may require an existing browser login.",
                requires_login=True,
            )
        )
    channels.append(
        ChannelHealth(
            "toutiao",
            searx_ok,
            "searxng_search + fetch_url",
            "Search uses SearXNG; public article pages use the generic reader.",
        )
    )
    if close_after and client is not None:
        client.close()
    rows = [channel.to_dict() for channel in channels]
    return {
        "ok": True,
        "healthy": sum(1 for row in rows if row["available"]),
        "total": len(rows),
        "channels": rows,
    }
