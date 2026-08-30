from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_KEYS = {
    "fbclid",
    "gclid",
    "spm",
    "utm_campaign",
    "utm_content",
    "utm_medium",
    "utm_source",
    "utm_term",
}
_TRUSTED_HOSTS = {
    "github.com",
    "docs.github.com",
    "youtube.com",
    "www.youtube.com",
    "bilibili.com",
    "www.bilibili.com",
}


def canonical_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        query = urlencode(
            [(key, val) for key, val in parse_qsl(parts.query) if key.lower() not in _TRACKING_KEYS]
        )
        return urlunsplit(
            (parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/") or "/", query, "")
        )
    except ValueError:
        return value


def rank_and_dedupe(results: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    terms = {term.lower() for term in query.split() if len(term) > 1}
    unique: dict[str, dict[str, Any]] = {}
    for position, source in enumerate(results):
        item = dict(source)
        url = canonical_url(str(item.get("url") or ""))
        if not url:
            continue
        text = f"{item.get('title', '')} {item.get('snippet', '')}".lower()
        relevance = sum(1 for term in terms if term in text)
        host = urlsplit(url).hostname or ""
        trust = 2 if host in _TRUSTED_HOSTS else 1
        score = round(trust + relevance * 0.5 + max(0, 20 - position) * 0.01, 3)
        item.update(url=url, score=score, source_host=host)
        previous = unique.get(url)
        if previous is None or score > float(previous.get("score", 0)):
            unique[url] = item
    return sorted(unique.values(), key=lambda row: float(row.get("score", 0)), reverse=True)
