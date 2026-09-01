"""Fleet broadcast — fan the same task out to many devices at once.

Group control ("一人驱动 N 台") composed from the single-device runner
[`run_device_task`][runtime.tentacle.team_bridge.run_device_task]: dispatch the
same natural-language task to a set of tentacles concurrently and aggregate the
per-device outcomes. Each device's failure is already isolated by
``run_device_task`` (it never raises), so one offline phone can't sink the batch.

This is the minimal group-control primitive; richer DAG orchestration (per-device
dependencies, affinity routing, device locks, heterogeneous result merge) can
later reuse the ``ParallelAgentOrchestrator`` scheduler — but a flat fan-out
covers the common "run this on all my phones" case.
"""

from __future__ import annotations

import asyncio
from typing import Any

from runtime.tentacle.team_bridge import run_device_task


def _resolve_targets(coordinator: Any, tentacle_ids: list[str] | None) -> list[str]:
    """Target tentacle ids. ``None`` → every online device. Order preserved,
    duplicates dropped."""
    if tentacle_ids is None:
        return [t.tentacle_id for t in coordinator.pool.all_online()]
    seen: set[str] = set()
    out: list[str] = []
    for tid in tentacle_ids:
        if tid and tid not in seen:
            seen.add(tid)
            out.append(tid)
    return out


async def broadcast(
    coordinator: Any,
    task: str,
    tentacle_ids: list[str] | None = None,
    *,
    max_concurrency: int = 8,
) -> dict[str, Any]:
    """Run the same task on many devices concurrently.

    Args:
        coordinator: the active ``TentacleCoordinator`` (holds ``pool`` +
            ``_decision_engine``).
        task: natural-language goal, run independently on each device.
        tentacle_ids: devices to target; ``None`` = all currently online.
        max_concurrency: cap on devices executing at once (back-pressure).

    Returns ``{ok, task, total, succeeded, failed, results}`` where ``results``
    is the list of per-device records from :func:`run_device_task` (in target
    order). ``ok`` is ``True`` only when there is at least one target and none
    failed. Never raises — a target that errors becomes a failed record.
    """
    targets = _resolve_targets(coordinator, tentacle_ids)
    if not targets:
        return {"ok": False, "task": task, "total": 0, "succeeded": 0, "failed": 0, "results": []}

    sem = asyncio.Semaphore(max(1, max_concurrency))

    async def _one(tid: str) -> dict[str, Any]:
        async with sem:
            return await run_device_task(coordinator, tid, task)

    raw = await asyncio.gather(*(_one(t) for t in targets), return_exceptions=True)

    results: list[dict[str, Any]] = []
    for tid, rec in zip(targets, raw, strict=False):
        if isinstance(rec, BaseException):
            # run_device_task is supposed to capture its own failures; this is a
            # belt-and-suspenders guard so the batch result is always well-formed.
            results.append(
                {
                    "tentacle_id": tid,
                    "ok": False,
                    "output": "",
                    "error": f"{type(rec).__name__}: {rec}",
                }
            )
        else:
            results.append(rec)

    succeeded = sum(1 for r in results if r.get("ok"))
    failed = len(results) - succeeded
    return {
        "ok": failed == 0,
        "task": task,
        "total": len(results),
        "succeeded": succeeded,
        "failed": failed,
        "results": results,
    }
