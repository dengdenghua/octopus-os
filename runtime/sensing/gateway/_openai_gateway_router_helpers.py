from __future__ import annotations

from typing import Any


def _reasoning_effort_from_body(body: dict[str, Any]) -> str | None:
    from runtime.sensing.model_router.models import normalize_reasoning_effort

    candidates: list[Any] = [body.get("reasoning_effort"), body.get("effort")]
    reasoning = body.get("reasoning")
    if isinstance(reasoning, dict):
        candidates.append(reasoning.get("effort"))
    context = body.get("context")
    if isinstance(context, dict):
        candidates.append(context.get("reasoning_effort"))
    for candidate in candidates:
        if candidate is None:
            continue
        normalized = normalize_reasoning_effort(candidate)
        if normalized:
            return normalized
        # DeepSeek native vocabulary: ``off`` is a legitimate explicit
        # disable. The OpenAI-style normalizer refuses it, so keep it for
        # the deepseek profile's thinking normalization to turn into
        # ``thinking: {type: disabled}``.
        if str(candidate).strip().lower() in ("off", "disabled"):
            return "off"
    return None


def _deep_requested(body: dict[str, Any]) -> bool:
    """Whether the caller explicitly asked for the deep agentic path.

    Trivial inputs otherwise short-circuit through the reflex fast-path and
    never produce a task_id / trace. Setting ``deep`` (or ``execution=deep``,
    or ``context.deep``) forces planner+runtime so the run is verifiable +
    replayable — the contract enterprise relies on to surface a trust trace.
    """
    if bool(body.get("deep")):
        return True
    if str(body.get("execution") or "").lower() == "deep":
        return True
    ctx = body.get("context")
    return bool(isinstance(ctx, dict) and ctx.get("deep"))


def _evict_idle_rate_buckets(
    windows: dict[str, Any],
    semaphores: dict[str, Any],
    cutoff: float,
) -> int:
    """Drop per-actor rate-limit buckets whose sliding window has emptied
    out — no call newer than ``cutoff`` (a monotonic timestamp). Mutates
    both dicts in place and returns the number of buckets evicted.

    Module-level and pure so the eviction invariant (empty windows go,
    active ones stay, the paired semaphore is dropped alongside) is
    unit-testable without standing up the whole gateway router.
    """
    stale: list[str] = []
    for key, window in windows.items():
        while window and window[0] < cutoff:
            window.popleft()
        if not window:
            stale.append(key)
    for key in stale:
        windows.pop(key, None)
        semaphores.pop(key, None)
    return len(stale)
