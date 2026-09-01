from __future__ import annotations

import json
import re
from typing import Any


def platform_monitor(
    *,
    platform: str = "web",
    queries: list[str] | None = None,
    cron_expression: str = "0 * * * *",
    max_results: int = 10,
    output_format: str = "json",
    name: str = "",
    channel_id: str = "",
    thread_id: str = "",
    **_: Any,
) -> dict[str, Any]:
    """Create a recurring platform collection using Echo' durable cron store."""
    clean_queries = [str(query).strip() for query in (queries or []) if str(query).strip()][:50]
    if not clean_queries:
        return {"ok": False, "error": "queries are required", "error_type": "invalid_argument"}
    fmt = output_format.strip().lower()
    if fmt not in {"json", "markdown", "md"}:
        return {
            "ok": False,
            "error": "output_format must be json or markdown",
            "error_type": "invalid_argument",
        }
    spec = {
        "platform": platform,
        "queries": clean_queries,
        "max_results": max(1, min(int(max_results), 50)),
        "output_format": fmt,
    }
    prompt = (
        "Run the platform_collect tool with exactly this JSON input, preserve source URLs and "
        f"report the output_path plus failures: {json.dumps(spec, ensure_ascii=False)}"
    )
    task_name = name.strip() or f"reach_{_safe_name(platform)}"
    from runtime.execution.suckers.cron_skills import _schedule_task

    result = _schedule_task(
        prompt=prompt,
        cron_expression=cron_expression,
        recurring=True,
        name=task_name,
        channel_id=channel_id,
        thread_id=thread_id,
    )
    result["monitor"] = spec
    return result


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", value.strip()).strip("_")
    return cleaned[:40] or "web"
