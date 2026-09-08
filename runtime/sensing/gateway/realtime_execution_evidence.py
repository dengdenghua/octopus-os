"""Persist the actual adapter/model before invoking it, without new storage."""

from __future__ import annotations

from typing import Any, Literal

from runtime.protocol.items import ExecutionSnapshot


def record_execution(
    log: Any,
    turn: Any,
    *,
    engine: Literal["native", "octopus", "codex", "opencode"],
    driver: str,
    model: str | None = None,
) -> None:
    previous = getattr(turn, "execution", None)
    # The unified supervisor records the durable native engine as ``octopus``
    # before entering the legacy realtime drivers, which still report their
    # historical ``native`` name.  Treat these two spellings as one engine
    # and retain the supervisor's canonical snapshot for the active turn.
    canonical = {"native": "octopus", "octopus": "octopus"}
    if previous is not None and canonical.get(previous.engine, previous.engine) != canonical.get(engine, engine):
        raise ValueError("an active turn cannot change its execution engine")
    effective_engine = previous.engine if previous is not None and canonical.get(previous.engine, previous.engine) == canonical.get(engine, engine) else engine
    snapshot = ExecutionSnapshot(
        engine=effective_engine,
        driver=driver,
        reason="legacy_driver",
        phase="primary",
        invocation=previous.invocation + 1 if previous else 1,
    )
    selection = None
    if isinstance(model, str) and model.strip() and model not in {"auto", "default"}:
        if len(model) > 256:
            raise ValueError("execution model identifier is too long")
        selection = {"engine": effective_engine, "invocation": snapshot.invocation, "model": model}
    update = getattr(log, "turn_updated", None)
    if callable(update):
        update(
            turn.thread_id,
            turn.id,
            execution=snapshot.model_dump(),
            execution_model=selection,
            durable=True,
        )
    turn.execution = snapshot
    if selection is not None and getattr(turn, "params", None) is not None:
        copy = getattr(turn.params, "model_copy", None)
        if callable(copy):
            turn.params = copy(update={"model": model})
