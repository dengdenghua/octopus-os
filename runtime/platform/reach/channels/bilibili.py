from __future__ import annotations

import re
from typing import Any

_BVID_RE = re.compile(r"(?:bilibili\.com/video/|^)(BV[0-9A-Za-z]+)", re.I)


def read_bilibili(client: Any, url: str) -> dict[str, Any] | None:
    match = _BVID_RE.search(url)
    if not match:
        return None
    bvid = match.group(1)
    response = client.get(
        "https://api.bilibili.com/x/web-interface/view",
        params={"bvid": bvid},
        headers={"Referer": "https://www.bilibili.com/"},
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 0:
        return {"error": payload.get("message") or "bilibili_api_error", "platform": "bilibili"}
    data = payload.get("data") or {}
    owner = data.get("owner") or {}
    return {
        "ok": True,
        "platform": "bilibili",
        "backend": "bilibili_public_api",
        "url": f"https://www.bilibili.com/video/{bvid}",
        "title": data.get("title") or "",
        "description": data.get("desc") or "",
        "author": owner.get("name") or "",
        "published_at": data.get("pubdate"),
        "duration": data.get("duration"),
        "stats": data.get("stat") or {},
        "pages": data.get("pages") or [],
    }
