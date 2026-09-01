"""Conversation (journal) endpoints for the agents router.

Pure structural split of ``_agents_endpoints.py`` — no logic changes.
``_register_conversations`` attaches the conversation listing + events
endpoints to the injected router, only when a journal is wired.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

try:
    from fastapi import HTTPException, Request

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    HTTPException = None  # type: ignore[assignment, misc]
    Request = None  # type: ignore[assignment, misc]

from ._agents_endpoints_shared import _AuthActions

if TYPE_CHECKING:
    from ._agents_endpoints import _AgentsCtx


def _register_conversations(router: Any, ctx: _AgentsCtx, auth: _AuthActions) -> None:
    journal = ctx.journal
    require_auth = ctx.require_auth
    thread_store = ctx.thread_store
    _auth = auth.auth
    _require_thread_owner = auth.require_thread_owner

    if journal is not None:

        @router.get("/api/conversations")
        def list_conversations(
            request: Request,
            agent: str | None = None,
            limit: int = 50,
        ) -> list[dict[str, Any]]:
            actor = _auth(request)
            cids = journal.list_conversations(agent_id=agent)

            # Filter to conversations owned by the caller
            owned_threads: set[str] | None = None
            if require_auth and actor and thread_store is not None:
                from runtime.core.cerebrum.pause_control import get_pause_controller

                ctrl = get_pause_controller()
                owned_threads = ctrl.list_thread_ids_for_owner(thread_store, actor)

            out: list[dict[str, Any]] = []
            for cid in cids[:limit]:
                # Skip if not owned (when filtering is active)
                if owned_threads is not None and cid not in owned_threads:
                    continue
                events = journal.read_by_conversation(cid)
                if not events:
                    continue
                agents = {e.agent_id for e in events if e.agent_id}
                out.append(
                    {
                        "conversation_id": cid,
                        "agent_id": next(iter(agents)) if len(agents) == 1 else None,
                        "first_ts": events[0].ts.isoformat(),
                        "last_ts": events[-1].ts.isoformat(),
                        "event_count": len(events),
                    }
                )
            return out

        @router.get("/api/conversations/{conversation_id}/events")
        def get_conversation_events(
            request: Request,
            conversation_id: str,
            limit: int = 200,
        ) -> dict[str, Any]:
            _require_thread_owner(request, conversation_id)
            events = journal.read_by_conversation(conversation_id)
            if not events:
                raise HTTPException(
                    404,
                    f"conversation not found: {conversation_id}",
                )
            return {
                "conversation_id": conversation_id,
                "total": len(events),
                "events": [
                    {
                        "event_id": str(e.event_id),
                        "event_type": e.event_type,
                        "ts": e.ts.isoformat(),
                        "task_id": str(e.task_id) if e.task_id else None,
                        "agent_id": e.agent_id,
                        "actor": e.actor,
                    }
                    for e in events[:limit]
                ],
            }
