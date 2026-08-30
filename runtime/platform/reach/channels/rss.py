from __future__ import annotations

from typing import Any
from xml.etree import ElementTree

try:
    import feedparser  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    feedparser = None


def read_rss(client: Any, url: str, limit: int = 20) -> dict[str, Any]:
    response = client.get(url)
    response.raise_for_status()
    if feedparser is None:
        return _read_rss_xml(response.content, str(response.url), limit)
    feed = feedparser.loads(response.content)
    entries = [
        {
            "title": entry.get("title", ""),
            "url": entry.get("link", ""),
            "snippet": entry.get("summary", ""),
            "published": entry.get("published") or entry.get("updated"),
        }
        for entry in feed.entries[:limit]
    ]
    return {
        "ok": True,
        "platform": "rss",
        "backend": "feedparser",
        "url": str(response.url),
        "title": feed.feed.get("title", ""),
        "results": entries,
    }


def _read_rss_xml(content: bytes, url: str, limit: int) -> dict[str, Any]:
    root = ElementTree.fromstring(content)
    channel = root.find("channel") if root.tag.lower().endswith("rss") else root
    title = _text(channel, "title") if channel is not None else ""
    nodes = root.findall(".//item")
    if not nodes:
        nodes = root.findall(".//{*}entry")
    entries = []
    for node in nodes[:limit]:
        link_node = node.find("link")
        if link_node is None:
            link_node = node.find("{*}link")
        link = ""
        if link_node is not None:
            link = link_node.get("href") or (link_node.text or "").strip()
        entries.append(
            {
                "title": _text(node, "title"),
                "url": link,
                "snippet": _text(node, "description") or _text(node, "summary"),
                "published": _text(node, "pubDate") or _text(node, "updated"),
            }
        )
    return {
        "ok": True,
        "platform": "rss",
        "backend": "stdlib_xml",
        "url": url,
        "title": title,
        "results": entries,
    }


def _text(node: Any, local_name: str) -> str:
    if node is None:
        return ""
    found = node.find(local_name)
    if found is None:
        found = node.find(f"{{*}}{local_name}")
    return "" if found is None else "".join(found.itertext()).strip()
