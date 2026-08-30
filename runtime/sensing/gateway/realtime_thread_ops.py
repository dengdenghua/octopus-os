"""Thread maintenance operations for the realtime runtime.

Split out of ``realtime_cerebrum.py``: post-turn / manual thread
compaction (durable ``turn_compacted`` events plus the per-thread lock
that serializes them) and per-hunk accept/reject decisions on
``FileChange`` items.

Every function takes the owning ``CerebrumRuntime`` as its first
argument; cross-method calls go through the runtime so subclass
overrides keep working.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from runtime.memory.threads.event_log import EventLog
from runtime.protocol import JsonRpcErrorCode, ServerMethod
from runtime.sensing.gateway.realtime_gateway import EventEmitter, _RpcError

if TYPE_CHECKING:
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime

_logger = logging.getLogger(__name__)


async def _maybe_compact(
    runtime: CerebrumRuntime,
    thread_id: str,
    log: EventLog,
    emitter: EventEmitter,
) -> None:
    async with runtime._compaction_locks.hold(thread_id):
        await runtime._maybe_compact_locked(thread_id, log, emitter)


async def _maybe_compact_locked(
    runtime: CerebrumRuntime,
    thread_id: str,
    log: EventLog,
    emitter: EventEmitter,
) -> None:
    """After a turn completes, check whether the thread warrants
    compaction. If yes, build a summary (possibly via LLM, off the
    event loop) and append a ``turn_compacted`` event.

    Failure is swallowed: a failed compaction must never break the
    turn that just succeeded. The runtime will try again next
    turn, and in the meantime the extra turn stays live context.
    """
    if runtime._compaction_policy is None:
        return
    from runtime.memory.threads.compaction import (
        CompactionPolicy,
        compact,
        should_compact,
    )

    policy: CompactionPolicy = runtime._compaction_policy
    # Replay walks the JSONL once; acceptable cost after a turn.
    try:
        turns = log.replay()
    except (OSError, ValueError, TypeError):
        _logger.exception("compaction: replay failed")
        return
    if not should_compact(turns, policy):
        return

    def do_compact() -> Any:
        # Bind LLM summariser at call time so a freshly-swapped
        # router is picked up without rebuilding the runtime.
        effective = policy
        if runtime._summary_router is not None and policy.custom_summariser is None:
            from runtime.memory.threads.compaction import _default_summariser
            from runtime.memory.threads.llm_summariser import (
                make_llm_summariser,
            )

            llm_summariser = make_llm_summariser(
                runtime._summary_router,
                fallback=_default_summariser,
            )
            import dataclasses

            effective = dataclasses.replace(policy, custom_summariser=llm_summariser)
        return compact(thread_id, turns, effective)

    try:
        result = await asyncio.to_thread(do_compact)
    except (OSError, ValueError, TypeError):
        _logger.exception("compaction: summariser failed")
        return
    if result is None:
        return
    try:
        logged = log.turn_compacted(thread_id, result.summary_turn, result.superseded_ids)
    except (OSError, ValueError, TypeError):
        _logger.exception("compaction: failed to persist turn_compacted")
        return
    try:
        await emitter.notify(
            ServerMethod.TURN_COMPACTED,
            {
                "threadId": thread_id,
                "supersededTurnIds": list(result.superseded_ids),
                "summaryTurn": result.summary_turn.model_dump(by_alias=True, mode="json"),
                "eventId": logged.event_id,
            },
        )
        await emitter.notify(
            ServerMethod.THREAD_STATUS_CHANGED,
            {
                "threadId": thread_id,
                "status": {
                    "type": "compacted",
                    "supersededTurnIds": list(result.superseded_ids),
                    "summaryTurnId": result.summary_turn.id,
                },
            },
        )
    except (ConnectionError, OSError, RuntimeError, TypeError):
        # The compaction is durable on disk already; a failed
        # notification is recoverable via ``thread/resume``.
        _logger.debug("compaction: notify failed", exc_info=True)


async def compact_thread(
    runtime: CerebrumRuntime,
    thread_id: str,
    emitter: EventEmitter | None = None,
) -> dict[str, Any]:
    """Manually compact a thread using the same durable event-log path
    as automatic compaction.

    This is intentionally conservative: it only compacts history older
    than the configured ``keep_recent`` window, so clicking the UI on a
    short conversation is a no-op instead of summarising away everything.
    """
    if runtime._compaction_policy is None:
        return {"threadId": thread_id, "compacted": False, "reason": "disabled"}

    from dataclasses import replace

    from runtime.memory.threads.compaction import (
        CompactionPolicy,
        compact,
    )

    log = runtime._log_for(thread_id)
    async with runtime._compaction_locks.hold(thread_id):
        turns = log.replay()
        policy: CompactionPolicy = runtime._compaction_policy
        if len(turns) <= policy.keep_recent:
            return {
                "threadId": thread_id,
                "compacted": False,
                "reason": "below_keep_recent",
                "turnCount": len(turns),
                "keepRecent": policy.keep_recent,
            }

        effective = replace(policy, trigger_at=policy.keep_recent + 1)
        if runtime._summary_router is not None and effective.custom_summariser is None:
            from runtime.memory.threads.compaction import _default_summariser
            from runtime.memory.threads.llm_summariser import make_llm_summariser

            effective = replace(
                effective,
                custom_summariser=make_llm_summariser(
                    runtime._summary_router,
                    fallback=_default_summariser,
                ),
            )

        result = await asyncio.to_thread(compact, thread_id, turns, effective)
        if result is None:
            return {"threadId": thread_id, "compacted": False, "reason": "no_stale_turns"}

        logged = log.turn_compacted(thread_id, result.summary_turn, result.superseded_ids)

    if emitter is not None:
        with contextlib.suppress(Exception):
            await emitter.notify(
                ServerMethod.TURN_COMPACTED,
                {
                    "threadId": thread_id,
                    "supersededTurnIds": list(result.superseded_ids),
                    "summaryTurn": result.summary_turn.model_dump(by_alias=True, mode="json"),
                    "eventId": logged.event_id,
                },
            )
        with contextlib.suppress(Exception):
            await emitter.notify(
                ServerMethod.THREAD_STATUS_CHANGED,
                {
                    "threadId": thread_id,
                    "status": {
                        "type": "compacted",
                        "supersededTurnIds": list(result.superseded_ids),
                        "summaryTurnId": result.summary_turn.id,
                    },
                },
            )
    return {
        "threadId": thread_id,
        "compacted": True,
        "supersededTurnIds": list(result.superseded_ids),
        "summaryTurnId": result.summary_turn.id,
    }


async def _handle_hunk_decide(
    runtime: CerebrumRuntime,
    params: dict[str, Any],
    emitter: EventEmitter,
) -> dict[str, Any]:
    """Reject (revert) or accept a single hunk after a FileChange item.

    ``rejected`` reverse-applies just that hunk's diff against the
    current file content; ``accepted`` is informational — the file
    already contains the patched version. The decision is broadcast
    as ``item/fileChange/hunkDecision`` so other connected clients
    update their UI state.
    """
    path_value = params.get("path")
    decision = params.get("decision")
    diff_text = params.get("diff")
    thread_id_value = params.get("threadId")
    thread_id = (
        runtime._require_thread_id(thread_id_value) if isinstance(thread_id_value, str) else None
    )
    turn_id = params.get("turnId")
    item_id = params.get("itemId")
    hunk_id = params.get("hunkId")
    if not isinstance(path_value, str) or not path_value.strip():
        raise _RpcError(JsonRpcErrorCode.INVALID_PARAMS, "path is required")
    if decision not in ("accepted", "rejected"):
        raise _RpcError(
            JsonRpcErrorCode.INVALID_PARAMS,
            "decision must be 'accepted' or 'rejected'",
        )

    if decision == "rejected" and thread_id is None:
        raise _RpcError(JsonRpcErrorCode.INVALID_PARAMS, "threadId required")
    if thread_id is not None:
        runtime._require_thread_owner(
            runtime._log_for(thread_id), getattr(emitter, "actor_id", None)
        )
        file_path = runtime._resolve_hunk_path(thread_id, path_value)
    else:
        from pathlib import Path

        file_path = Path(path_value)
    reverted_bytes = 0
    if decision == "rejected":
        if not isinstance(diff_text, str) or not diff_text.strip():
            raise _RpcError(
                JsonRpcErrorCode.INVALID_PARAMS,
                "diff is required to reject a hunk",
            )
        from runtime.sensing.gateway.fs_router import (
            _DiffApplyConflict,
            _DiffFormatError,
            _reverse_unified_diff,
        )

        try:
            current = (
                file_path.read_text(encoding="utf-8", errors="replace")
                if file_path.exists()
                else ""
            )
            reverted = _reverse_unified_diff(current, diff_text)
        except _DiffFormatError as exc:
            raise _RpcError(JsonRpcErrorCode.INVALID_PARAMS, str(exc)) from exc
        except _DiffApplyConflict as exc:
            raise _RpcError(
                JsonRpcErrorCode.APPROVAL_DENIED,
                f"hunk no longer applies cleanly: {exc}",
            ) from exc
        except OSError as exc:
            raise _RpcError(JsonRpcErrorCode.INTERNAL_ERROR, f"read failed: {exc}") from exc
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(reverted, encoding="utf-8")
            reverted_bytes = len(reverted.encode("utf-8"))
        except OSError as exc:
            raise _RpcError(JsonRpcErrorCode.INTERNAL_ERROR, f"write failed: {exc}") from exc

    await emitter.notify(
        ServerMethod.ITEM_FILE_CHANGE_HUNK_DECISION,
        {
            "threadId": thread_id,
            "turnId": turn_id,
            "itemId": item_id,
            "hunkId": hunk_id,
            "decision": decision,
            "path": str(file_path),
        },
    )
    return {
        "decision": decision,
        "path": str(file_path),
        "bytes": reverted_bytes,
    }


def _resolve_hunk_path(runtime: CerebrumRuntime, thread_id: str, path_value: str) -> Path:
    raw = Path(path_value).expanduser()
    if runtime._workspaces is None:
        return raw
    workspace_root = runtime._workspaces.layout(thread_id).root.resolve()
    candidate = raw if raw.is_absolute() else workspace_root / raw
    try:
        resolved = candidate.resolve(strict=False)
        # Reject symlinks that escape the workspace — a symlink
        # inside the tree can point outside, and relative_to alone
        # does not catch this because it operates on the
        # already-resolved path.
        if resolved.is_symlink():
            link_target = resolved.resolve()
            link_target.relative_to(workspace_root)
        resolved.relative_to(workspace_root)
    except (OSError, ValueError) as exc:
        raise _RpcError(
            JsonRpcErrorCode.INVALID_PARAMS,
            "path must stay within the thread workspace",
        ) from exc
    return resolved


# ── Drivers ───────────────────────────────────────────────
# Bodies live in ``realtime_react_stream`` / ``realtime_team_stream``;
# these thin methods keep the original call surface (and subclass
# override points) stable.
