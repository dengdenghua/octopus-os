"""Durable permanent-delete fence shared by realtime turn boundaries."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any


def _thread_store_for(runtime: Any, thread_access_resolver: Any = None) -> Any:
    store = getattr(runtime, "_thread_store", None)
    if store is not None:
        return store
    resolver = thread_access_resolver
    if resolver is None:
        resolver = getattr(runtime, "_thread_access_resolver", None)
    return getattr(resolver, "_thread_store", None)


def assert_thread_accepts_runtime_writes(
    runtime: Any,
    thread_id: str,
    *,
    thread_access_resolver: Any = None,
) -> None:
    """Reject a deleting/deleted thread before a runtime can append events.

    Callers must already hold the canonical ``ThreadTurnClaim`` and retain it
    through the write or runtime call. The delete route takes the same claim,
    making this durable probe a stable snapshot rather than a check/use race.
    A configured store with a missing or broken probe fails closed. A genuinely
    absent thread has no tombstone and remains a valid new-thread id.
    """

    from runtime.memory.threads._permanent_deletion import (
        ThreadPermanentlyDeletedError,
    )

    thread_store = _thread_store_for(runtime, thread_access_resolver)
    if thread_store is not None:
        assertion = getattr(thread_store, "assert_not_permanently_deleted", None)
        if callable(assertion):
            assertion(thread_id)
        else:
            probe = getattr(thread_store, "is_permanently_deleted", None)
            if callable(probe):
                deleted = probe(thread_id)
                if not isinstance(deleted, bool):
                    raise RuntimeError("thread permanent-delete probe returned invalid state")
                if deleted:
                    raise ThreadPermanentlyDeletedError(thread_id)
            else:
                lease_probe = getattr(thread_store, "thread_delete_lease", None)
                if not callable(lease_probe):
                    raise RuntimeError("thread permanent-delete probe unavailable")
                if lease_probe(thread_id) is not None:
                    raise ThreadPermanentlyDeletedError(thread_id)

    project_store = getattr(runtime, "_project_store", None)
    if project_store is not None:
        project_probe = getattr(project_store, "thread_delete_lease", None)
        if not callable(project_probe):
            raise RuntimeError("project thread-delete probe unavailable")
        if project_probe(thread_id) is not None:
            raise ThreadPermanentlyDeletedError(thread_id)


@asynccontextmanager
async def claimed_runtime_thread_write(
    runtime: Any,
    thread_id: str,
) -> AsyncIterator[None]:
    """Claim, re-probe, and serialize one maintenance EventLog mutation."""

    from runtime.platform.process.thread_turn_claim import (
        ThreadTurnClaimUnavailable,
        acquire_thread_turn_claim,
    )

    logs_root = getattr(runtime, "_logs_root", None)
    if logs_root is None:
        raise ThreadTurnClaimUnavailable("runtime has no authoritative thread-log root")
    claim = acquire_thread_turn_claim(logs_root, thread_id)
    try:
        assert_thread_accepts_runtime_writes(runtime, thread_id)
        yield
    finally:
        claim.release()


__all__ = [
    "assert_thread_accepts_runtime_writes",
    "claimed_runtime_thread_write",
]
