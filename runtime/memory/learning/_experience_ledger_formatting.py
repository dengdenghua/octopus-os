"""Sorting, formatting, and time helpers for the experience ledger."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

_PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2}


def source_from_review(review: dict[str, Any]) -> dict[str, str]:
    return {
        "task_id": clean_text(review.get("task_id"), limit=120),
        "thread_id": clean_text(review.get("thread_id"), limit=120),
        "turn_id": clean_text(review.get("turn_id"), limit=120),
        "agent_id": clean_text(review.get("agent_id"), limit=120),
    }


def record_sort_key(row: dict[str, Any]) -> tuple[int, str, str]:
    return (
        _PRIORITY_RANK.get(str(row.get("priority") or "P2"), 2),
        str(row.get("last_seen_at") or ""),
        str(row.get("id") or ""),
    )


def record_recall_sort_key(row: dict[str, Any]) -> tuple[int, float, str, str]:
    raw_quality = row.get("memory_quality")
    quality = raw_quality if isinstance(raw_quality, dict) else {}
    return (
        _PRIORITY_RANK.get(str(row.get("priority") or "P2"), 2),
        -float(quality.get("reliability") or 0.0),
        str(row.get("last_seen_at") or ""),
        str(row.get("id") or ""),
    )


def weekly_record_sort_key(row: dict[str, Any]) -> tuple[int, int, str]:
    return (
        _PRIORITY_RANK.get(str(row.get("priority") or "P2"), 2),
        -int(row.get("occurrences") or 1),
        str(row.get("last_seen_at") or ""),
    )


def next_actions(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    for row in rows[:8]:
        title = str(row.get("title") or "")
        item_priority = priority(row.get("priority"))
        bucket = str(row.get("memory_bucket") or "")
        if bucket == "experiment_backlog":
            action = f"Run or reject experiment: {title}"
        elif item_priority == "P0":
            action = f"Promote to failure-prevention rule: {title}"
        else:
            action = f"Review and classify learning: {title}"
        actions.append(
            {
                "priority": item_priority,
                "record_id": str(row.get("id") or ""),
                "action": action,
            }
        )
    return actions


def quality_next_actions(
    *, stale_count: int, contradicted_count: int, low_reliability_count: int
) -> list[str]:
    actions: list[str] = []
    if contradicted_count:
        actions.append("Archive or explain contradicted memory records before recall.")
    if stale_count:
        actions.append("Refresh stale memories with replay-backed evidence.")
    if low_reliability_count:
        actions.append(
            "Require stronger citations before low-reliability memories influence code mode."
        )
    return actions


def avg(values: Any) -> float:
    nums = [float(value) for value in values]
    return round(sum(nums) / len(nums), 3) if nums else 0.0


def within_week(value: Any, start: date, end: date) -> bool:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return False
    return start <= dt.date() < end


def week_start(value: str | date | None, *, now: datetime | None) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value.strip())
        except ValueError:  # noqa: BLE001 — malformed week input falls back to current week
            pass
    today = (now or datetime.now(UTC)).date()
    return today - timedelta(days=today.weekday())


def iso(value: datetime | None = None) -> str:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(UTC).isoformat()


def clean_text(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text[:limit].rstrip()


def clean_unique_list(value: Any, *, limit: int) -> list[str]:
    return merge_unique([], value)[:limit]


def merge_unique(left: Any, right: Any) -> list[str]:
    out: list[str] = []
    for collection in (left, right):
        if not isinstance(collection, list):
            continue
        for item in collection:
            text = clean_text(item, limit=160)
            if text and text not in out:
                out.append(text)
    return out


def priority(value: Any) -> str:
    raw = str(value or "P2").upper()
    return raw if raw in _PRIORITY_RANK else "P2"


def higher_priority(left: Any, right: Any) -> str:
    left_priority = priority(left)
    right_priority = priority(right)
    return (
        left_priority
        if _PRIORITY_RANK[left_priority] <= _PRIORITY_RANK[right_priority]
        else right_priority
    )


def tags_for(kind: str, item_priority: str, bucket: str) -> list[str]:
    return [tag for tag in (kind, item_priority, bucket) if tag]


__all__ = [
    "avg",
    "clean_text",
    "clean_unique_list",
    "higher_priority",
    "iso",
    "merge_unique",
    "next_actions",
    "priority",
    "quality_next_actions",
    "record_recall_sort_key",
    "record_sort_key",
    "source_from_review",
    "tags_for",
    "week_start",
    "weekly_record_sort_key",
    "within_week",
]
