"""Search projection helpers for the thread-state HTTP router."""

from __future__ import annotations

from typing import Any

from runtime.memory.threads.store import _project_fields


def search_select_fields(payload: dict[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return the public and authorization-safe internal field selections."""
    raw_select = payload.get("select")
    select = (
        tuple(field.strip() for field in raw_select if isinstance(field, str) and field.strip())
        if isinstance(raw_select, list)
        else ()
    )
    if not select:
        return (), ()

    # Access and archive checks still need these fields. They are stripped
    # again before returning the public projection.
    internal_select = tuple(dict.fromkeys((*select, "thread_id", "metadata")))
    return select, internal_select


def project_visible_search_page(
    visible: list[dict[str, Any]],
    *,
    select: tuple[str, ...],
    offset: int,
    limit: int,
    require_auth: bool,
) -> list[dict[str, Any]]:
    """Paginate visible threads and apply the exact caller projection."""
    page = visible[offset : offset + limit] if require_auth else visible
    if not select:
        return page
    return [_project_fields(thread, select) for thread in page]


__all__ = ["project_visible_search_page", "search_select_fields"]
