"""Shared turn/session preparation for the OpenAI compatibility gateway.

Both the synchronous and SSE paths must derive filesystem authority from the
same server-owned identity.  Keeping this in one helper prevents the stream
worker from silently falling back to the legacy conversation-slug workspace.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from fastapi import HTTPException

from runtime.platform.process.paths import app_paths
from runtime.platform.process.session import Session
from runtime.platform.runtime_policy.workspaces import (
    WorkspaceLayout,
    WorkspaceManager,
    managed_workspace_path,
)
from runtime.safety.auth.scope import TenantScope
from runtime.safety.recovery.tenant_scope import (
    AUTHORITATIVE_SCOPE_CONTEXT_KEY,
    authoritative_scope_context,
)

_LOCAL_TENANT_NAMESPACE = "echo-openai-local"
_LOCAL_ACTOR_NAMESPACE = "anonymous"
_UNGRADABLE_DISPOSITIONS = frozenset(
    {
        "blocked_on_user",
        "cancelled",
        "interrupted",
        "paused",
    }
)


@dataclass(frozen=True, slots=True)
class PreparedChatTurn:
    """The one authoritative Session and workspace for an API turn."""

    session: Session
    workspace: WorkspaceLayout


def _opaque_thread_id(thread_id: str) -> str:
    return sha256(thread_id.encode("utf-8")).hexdigest()[:32]


def prepare_chat_turn(
    stack: Any,
    *,
    turn_id: str,
    actor: str | None,
    agent: Any,
    conversation_id: str | None,
    tenant_id: str | None,
    owner_actor_id: str | None = None,
) -> PreparedChatTurn:
    """Allocate an opaque, scope-partitioned workspace and bind its Session.

    Authenticated requests require the complete principal tuple.  Anonymous
    local requests use a fixed local namespace, while still hashing the
    conversation id so unsafe or colliding user strings never become path
    segments.  ``bind_managed`` supplies the symlink and rebind checks used by
    all authenticated workspace consumers.
    """

    resolved_turn_id = str(turn_id or "").strip()
    if not resolved_turn_id:
        raise HTTPException(500, "chat turn id is missing")
    turn_thread_id = str(conversation_id or resolved_turn_id)

    supplied_identity = any((tenant_id, actor, owner_actor_id))
    complete_identity = bool(tenant_id) and bool(actor)
    if supplied_identity and not complete_identity:
        raise HTTPException(500, "authenticated workspace identity is incomplete")
    if owner_actor_id is not None and owner_actor_id != actor:
        raise HTTPException(500, "authenticated workspace identity is inconsistent")

    workspace_root = app_paths().data_dir / "workspaces"
    workspace_manager = WorkspaceManager(workspace_root)
    opaque_thread_id = _opaque_thread_id(turn_thread_id)
    managed_workspace = managed_workspace_path(
        workspace_root,
        tenant_id=str(tenant_id or _LOCAL_TENANT_NAMESPACE),
        actor_id=str(actor or _LOCAL_ACTOR_NAMESPACE),
        thread_id=opaque_thread_id,
    )
    workspace_layout = workspace_manager.bind_managed(
        turn_thread_id,
        managed_workspace,
    )

    resolved_owner = actor if complete_identity else None
    session_metadata: dict[str, Any] = {
        "enforce_executor_approval": True,
        "tenant_id": tenant_id,
        "owner_actor_id": resolved_owner,
        "_artifact_output_root": str(workspace_layout.final),
        "_execution_stack": stack,
    }
    if complete_identity and resolved_owner is not None and tenant_id is not None:
        session_metadata[AUTHORITATIVE_SCOPE_CONTEXT_KEY] = authoritative_scope_context(
            TenantScope(tenant_id=tenant_id, actor_id=resolved_owner)
        )
    session = Session(
        actor=actor,
        agent=agent,
        thread_id=turn_thread_id,
        conversation_id=turn_thread_id,
        turn_id=resolved_turn_id,
        metadata=session_metadata,
    )
    return PreparedChatTurn(session=session, workspace=workspace_layout)


def candidate_outcome_for_trajectory(trajectory: Any) -> bool | None:
    """Map a runtime trajectory to governed-canary evidence semantics."""

    outcome = getattr(trajectory, "outcome", None)
    disposition = str(getattr(outcome, "disposition", "") or "").strip().lower()
    if disposition in _UNGRADABLE_DISPOSITIONS:
        return None
    return bool(getattr(outcome, "success", False) and not getattr(outcome, "degraded", False))


def settle_candidate_outcomes(turn_id: str, success: bool | None) -> None:
    """Best-effort canary settlement which never changes API availability."""

    try:
        from runtime.safety.evolution.runtime_outcomes import (
            settle_runtime_candidate_outcomes,
        )

        settle_runtime_candidate_outcomes(str(turn_id), success=success)
    except Exception as exc:  # noqa: BLE001 - telemetry must not break the API turn
        logging.getLogger(__name__).debug(
            "candidate outcome settlement failed: %s",
            exc,
        )


__all__ = [
    "PreparedChatTurn",
    "candidate_outcome_for_trajectory",
    "prepare_chat_turn",
    "settle_candidate_outcomes",
]
