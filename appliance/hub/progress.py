"""Strict, secret-free progress contract for Hub lifecycle operations."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

HUB_PROGRESS_SCHEMA = "echo.hub.progress.v1"
HUB_STREAM_SCHEMA = "echo.hub.operation-stream.v1"

HUB_PROGRESS_STAGES = frozenset(
    {
        "queued",
        "validating",
        "pulling",
        "preparing",
        "snapshotting",
        "stopping",
        "starting",
        "verifying",
        "switching",
        "removing",
        "rolling-back",
        "completed",
        "failed",
        "interrupted",
    }
)
HUB_PROGRESS_STEPS = frozenset(
    {
        "waiting",
        "checking-plan",
        "pulling-image",
        "creating-resources",
        "snapshotting-data",
        "stopping-services",
        "starting-services",
        "checking-health",
        "switching-services",
        "removing-services",
        "restoring-state",
        "finished",
        "operation-failed",
        "runtime-restarted",
    }
)
HUB_PROGRESS_UNITS = frozenset({"layers", "images", "services", "volumes"})
_PROGRESS_FIELDS = {
    "schema",
    "stage",
    "step",
    "completed",
    "total",
    "unit",
    "item",
    "items",
}

HubProgressCallback = Callable[[dict[str, Any]], None]


def hub_progress(
    stage: str,
    step: str,
    *,
    completed: int | None = None,
    total: int | None = None,
    unit: str | None = None,
    item: int | None = None,
    items: int | None = None,
) -> dict[str, Any]:
    return validate_hub_progress(
        {
            "schema": HUB_PROGRESS_SCHEMA,
            "stage": stage,
            "step": step,
            "completed": completed,
            "total": total,
            "unit": unit,
            "item": item,
            "items": items,
        }
    )


def validate_hub_progress(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _PROGRESS_FIELDS:
        raise ValueError("Hub progress fields are invalid")
    stage = value.get("stage")
    step = value.get("step")
    completed = value.get("completed")
    total = value.get("total")
    unit = value.get("unit")
    item = value.get("item")
    items = value.get("items")
    if stage not in HUB_PROGRESS_STAGES or step not in HUB_PROGRESS_STEPS:
        raise ValueError("Hub progress state is invalid")
    if unit is not None and unit not in HUB_PROGRESS_UNITS:
        raise ValueError("Hub progress unit is invalid")
    for number in (completed, total, item, items):
        if number is not None and (
            not isinstance(number, int) or isinstance(number, bool) or not 0 <= number <= 4096
        ):
            raise ValueError("Hub progress counter is invalid")
    if (completed is None) != (total is None) or (item is None) != (items is None):
        raise ValueError("Hub progress counters are incomplete")
    if completed is not None and (unit is None or total <= 0 or completed > total):
        raise ValueError("Hub progress completion is invalid")
    if completed is None and unit is not None:
        raise ValueError("Hub progress unit has no counters")
    if item is not None and (items <= 0 or item <= 0 or item > items):
        raise ValueError("Hub progress item is invalid")
    return {
        "schema": HUB_PROGRESS_SCHEMA,
        "stage": stage,
        "step": step,
        "completed": completed,
        "total": total,
        "unit": unit,
        "item": item,
        "items": items,
    }


def emit_hub_progress(
    callback: HubProgressCallback | None,
    stage: str,
    step: str,
    **counters: Any,
) -> None:
    if callback is not None:
        callback(hub_progress(stage, step, **counters))


def pull_image_with_progress(
    docker: Any,
    image: str,
    *,
    callback: HubProgressCallback | None,
    item: int,
    items: int,
) -> None:
    emit_hub_progress(
        callback,
        "pulling",
        "pulling-image",
        completed=0,
        total=1,
        unit="images",
        item=item,
        items=items,
    )
    streaming_pull = getattr(docker, "pull_image_with_progress", None)
    if callable(streaming_pull) and callback is not None:

        def forward(progress: dict[str, Any]) -> None:
            normalized = validate_hub_progress(progress)
            callback({**normalized, "item": item, "items": items})

        streaming_pull(image, forward)
        return
    docker.pull_image(image)
    emit_hub_progress(
        callback,
        "pulling",
        "pulling-image",
        completed=1,
        total=1,
        unit="images",
        item=item,
        items=items,
    )


__all__ = [
    "HUB_PROGRESS_SCHEMA",
    "HUB_PROGRESS_STAGES",
    "HUB_PROGRESS_STEPS",
    "HUB_PROGRESS_UNITS",
    "HUB_STREAM_SCHEMA",
    "HubProgressCallback",
    "emit_hub_progress",
    "hub_progress",
    "pull_image_with_progress",
    "validate_hub_progress",
]
