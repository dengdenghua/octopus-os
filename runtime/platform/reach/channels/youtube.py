from __future__ import annotations

import re
from typing import Any

_VIDEO_RE = re.compile(r"(?:youtu\.be/|youtube\.com/(?:watch\?v=|shorts/))([\w-]{6,})")

try:
    import yt_dlp  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    yt_dlp = None


def read_youtube(
    client: Any,
    url: str,
    *,
    include_transcript: bool = True,
    language: str = "",
) -> dict[str, Any] | None:
    if not _VIDEO_RE.search(url):
        return None
    if yt_dlp is None:
        response = client.get(
            "https://www.youtube.com/oembed",
            params={"url": url, "format": "json"},
        )
        response.raise_for_status()
        data = response.json()
        return {
            "ok": True,
            "platform": "youtube",
            "backend": "youtube_oembed",
            "url": url,
            "title": data.get("title") or "",
            "author": data.get("author_name") or "",
            "author_url": data.get("author_url") or "",
            "thumbnail_url": data.get("thumbnail_url") or "",
            "subtitle_languages": [],
            "subtitle_hint": "Install the optional yt-dlp dependency to inspect subtitles.",
        }
    options = {
        "quiet": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 12,
        "extract_flat": False,
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)
    subtitles = info.get("subtitles") or info.get("automatic_captions") or {}
    result = {
        "ok": True,
        "platform": "youtube",
        "backend": "yt_dlp",
        "url": info.get("webpage_url") or url,
        "title": info.get("title") or "",
        "description": (info.get("description") or "")[:20_000],
        "author": info.get("uploader") or info.get("channel") or "",
        "duration": info.get("duration"),
        "published_at": info.get("upload_date"),
        "subtitle_languages": sorted(subtitles),
    }
    if include_transcript and subtitles:
        preferred = [language] if language else []
        preferred.extend(["zh-Hans", "zh-CN", "zh", "en"])
        selected = next((code for code in preferred if code in subtitles), None)
        selected = selected or next(iter(subtitles), None)
        tracks = subtitles.get(selected) or []
        track = next((row for row in tracks if row.get("ext") in {"vtt", "srv3", "ttml"}), None)
        track = track or (tracks[0] if tracks else None)
        if track and track.get("url"):
            try:
                response = client.get(track["url"])
                response.raise_for_status()
                raw = response.content.decode("utf-8", "replace")
                transcript = _subtitle_to_text(raw)
                result["transcript_language"] = selected
                result["transcript"] = transcript[:40_000]
                result["transcript_truncated"] = len(transcript) > 40_000
            except Exception as exc:  # noqa: BLE001
                result["transcript_error"] = f"{type(exc).__name__}: {exc}"
    return result


def _subtitle_to_text(raw: str) -> str:
    lines: list[str] = []
    previous = ""
    for line in raw.splitlines():
        text = line.strip()
        if not text or text.startswith(("WEBVTT", "NOTE", "Kind:", "Language:")):
            continue
        if "-->" in text or text.isdigit() or text.startswith("<?xml"):
            continue
        text = re.sub(r"<[^>]+>", "", text)
        text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        if text and text != previous:
            lines.append(text)
            previous = text
    return "\n".join(lines)
