from __future__ import annotations

import json
import os
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from runtime.platform.io import atomic_write_json, atomic_write_text

from .router import platform_read, platform_search


def platform_collect(
    *,
    platform: str = "web",
    queries: list[str] | None = None,
    urls: list[str] | None = None,
    max_results: int = 10,
    output_path: str = "",
    output_format: str = "json",
    use_browser: bool = False,
    **_: Any,
) -> dict[str, Any]:
    """Run a bounded multi-query/multi-URL collection and persist its evidence."""
    clean_queries = [str(value).strip() for value in (queries or []) if str(value).strip()][:50]
    clean_urls = [str(value).strip() for value in (urls or []) if str(value).strip()][:100]
    if not clean_queries and not clean_urls:
        return {"error": "supply queries or urls", "items": []}
    fmt = output_format.strip().lower()
    if fmt not in {"json", "markdown", "md"}:
        return {"error": "output_format must be json or markdown", "items": []}
    searches = [
        platform_search(platform=platform, query=query, max_results=max_results)
        for query in clean_queries
    ]
    reads = [
        platform_read(url=url, platform=platform, use_browser=use_browser) for url in clean_urls
    ]
    created_at = datetime.now(UTC).isoformat()
    payload = {
        "ok": not any(row.get("error") for row in [*searches, *reads]),
        "platform": platform,
        "created_at": created_at,
        "queries": clean_queries,
        "urls": clean_urls,
        "searches": searches,
        "reads": reads,
    }
    destination = _output_path(output_path, fmt)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with suppress(OSError):
        destination.parent.chmod(0o700)
    if fmt == "json":
        atomic_write_json(destination, payload, mode=0o600)
    else:
        atomic_write_text(destination, _to_markdown(payload), mode=0o600)
    return {
        **payload,
        "output_path": str(destination),
        "search_count": len(searches),
        "read_count": len(reads),
    }


def _collections_root() -> Path:
    root = Path(os.environ.get("ECHO_HOME") or (Path.home() / ".echo"))
    return (root / "data" / "reach" / "collections").expanduser().resolve()


def _output_path(value: str, fmt: str) -> Path:
    """Resolve the collection output path, confined to the collections root.

    ``output_path`` is model-supplied (the skill description advertises it), so a
    bare ``resolve()`` would let ``..`` segments or an absolute path escape and
    write anywhere the service account can reach. Confine every caller-supplied
    path to the collections root and reject anything that lands outside it.
    """
    root = _collections_root()
    suffix = "json" if fmt == "json" else "md"
    if value.strip():
        candidate = (root / Path(value).expanduser()).resolve()
        if not candidate.is_relative_to(root):
            raise ValueError(f"output_path escapes the collections root: {value!r}")
        return candidate
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return root / f"collection-{stamp}.{suffix}"


def _to_markdown(payload: dict[str, Any]) -> str:
    lines = ["# Reach collection", "", f"Created: {payload['created_at']}", ""]
    for query, result in zip(payload["queries"], payload["searches"], strict=False):
        lines.extend([f"## Search: {query}", ""])
        for item in result.get("results") or []:
            lines.append(f"- [{item.get('title') or item.get('url')}]({item.get('url')})")
            if item.get("snippet"):
                lines.append(f"  {item['snippet']}")
        lines.append("")
    for url, result in zip(payload["urls"], payload["reads"], strict=False):
        lines.extend(
            [
                f"## Read: {url}",
                "",
                "```json",
                json.dumps(result, ensure_ascii=False, indent=2),
                "```",
                "",
            ]
        )
    return "\n".join(lines)
