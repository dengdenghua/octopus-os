"""Thread state HTTP router used by the realtime UI."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    from fastapi import APIRouter, HTTPException, Query, Request
    from fastapi.responses import Response

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment, misc]
    HTTPException = None  # type: ignore[assignment, misc]
    Query = None  # type: ignore[assignment, misc]
    Request = None  # type: ignore[assignment, misc]
    Response = None  # type: ignore[assignment, misc]

from runtime.sensing._fastapi_guard import require_fastapi

from ._thread_state_auto_title import build_auto_title_service
from ._thread_state_search_projection import project_visible_search_page, search_select_fields
from .thread_workspace import (
    _create_workspace_directory,
    _remove_workspace_directory_if_unchanged,
    managed_workspace_metadata,
    strip_client_workspace_metadata,
    verified_managed_workspace,
)

_logger = logging.getLogger(__name__)
_PUBLIC_NO_STORE_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Expires": "0",
}


def _positive_env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def _canonical_public_share_url(token: str) -> str | None:
    base = str(os.environ.get("ECHO_PUBLIC_SHARE_BASE_URL") or "").strip().rstrip("/")
    if not base:
        return None
    parsed = urlparse(base)
    is_loopback_http = parsed.scheme == "http" and parsed.hostname in {
        "127.0.0.1",
        "localhost",
        "::1",
    }
    if (parsed.scheme != "https" and not is_loopback_http) or not parsed.netloc:
        _logger.warning("ignored invalid ECHO_PUBLIC_SHARE_BASE_URL")
        return None
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        _logger.warning("ignored unsafe ECHO_PUBLIC_SHARE_BASE_URL")
        return None
    return f"{base}/#/share/{token}"


def _seed_child_realtime_log(
    logs_root: Path | str | None,
    parent_thread_id: str,
    child_thread_id: str,
    at_message_index: int | None,
) -> None:
    """Seed the child's realtime event log from the parent so the chat UI can
    reconstruct the forked conversation.

    Fork previously wrote only the legacy ``ThreadStateStore`` entry; the
    realtime UI reconstructs visible messages from the per-thread JSONL event
    log (``data/threads/<id>.jsonl``), which a fresh fork left empty — so a
    forked thread rendered "no messages". Copy the parent's turns (rewriting
    the thread id) up to the same cut the store snapshot used.
    """
    if not logs_root:
        return
    try:
        from runtime.memory.threads._event_log_helpers import thread_log_path

        parent_path = thread_log_path(logs_root, parent_thread_id)
        child_path = thread_log_path(logs_root, child_thread_id)
        if not parent_path.exists() or parent_path.stat().st_size <= 0:
            return
        from runtime.memory.threads.event_log import EventLog
        from runtime.sensing.gateway.realtime_thread_history import (
            _flatten_turns_to_messages,
        )

        keep_turn_ids: set[str] | None = None
        if at_message_index is not None:
            keep_turn_ids = set()
            used = 0
            for turn in EventLog(parent_path).replay():
                msgs, _, _ = _flatten_turns_to_messages([turn])
                keep_turn_ids.add(turn.id)
                used += len(msgs)
                if used > at_message_index:
                    break
        child_path.parent.mkdir(parents=True, exist_ok=True)
        with child_path.open("a", encoding="utf-8") as out:
            for line in parent_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if not isinstance(event, dict):
                    continue
                turn_id = event.get("turnId") or event.get("turn_id")
                if (
                    keep_turn_ids is not None
                    and isinstance(turn_id, str)
                    and turn_id not in keep_turn_ids
                ):
                    continue
                event["threadId"] = child_thread_id
                event.pop("thread_id", None)
                event.pop("eventId", None)
                out.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — seeding is best-effort
        _logger.warning(
            "seed child realtime log for fork failed (%s → %s)",
            parent_thread_id,
            child_thread_id,
            exc_info=True,
        )


def create_thread_state_router(
    *,
    store: Any,
    logs_root: Path | str | None = None,
    session_titles: Any = None,
    identity_store: Any = None,
    require_auth: bool = False,
    allow_local_workspace_access: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
    workspace_root: Path | str | None = None,
    group_store: Any = None,
    collaboration_store: Any = None,
    team_rooms_router: Any = None,
    project_store: Any = None,
) -> Any:
    require_fastapi(__name__)
    managed_workspace_required = require_auth and not allow_local_workspace_access

    router = APIRouter(tags=["threads"])

    from .thread_access import ThreadAccessResolver

    access_resolver = ThreadAccessResolver(
        thread_store=store,
        group_store=group_store,
        collaboration_store=collaboration_store,
        team_rooms_router=team_rooms_router,
        identity_store=identity_store,
    )
    share_store = None
    if logs_root is not None:
        from .thread_share_store import ThreadShareStore

        share_store = ThreadShareStore(
            Path(logs_root).parent / "thread-shares",
            ttl_seconds=_positive_env_int("ECHO_THREAD_SHARE_TTL_SECONDS", 30 * 86400),
            max_active_per_owner=_positive_env_int("ECHO_THREAD_SHARE_MAX_ACTIVE_PER_OWNER", 100),
            max_snapshot_bytes=_positive_env_int("ECHO_THREAD_SHARE_MAX_SNAPSHOT_BYTES", 1_200_000),
        )
    from .thread_share_relay import ThreadShareRelayClient

    # Optional managed-cloud relay. Explicit misconfiguration fails startup so
    # the UI never mints a link that looks public while the snapshot stayed local.
    share_relay = ThreadShareRelayClient.from_env()

    def _auth(request: Any) -> str | None:
        from runtime.safety.auth.principal import resolve_principal

        principal = resolve_principal(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )
        if principal is not None:
            request.state.thread_principal = principal
        return principal.actor_id if principal is not None else None

    def _tenant(request: Any) -> str | None:
        principal = getattr(getattr(request, "state", None), "thread_principal", None)
        return getattr(principal, "tenant_id", None)

    def _require_store() -> None:
        if store is None:
            raise HTTPException(503, "thread state unavailable")

    def _project_store_for_delete() -> Any:
        if project_store is None:
            return None
        resolved = project_store() if callable(project_store) else project_store
        if resolved is None:
            raise HTTPException(503, "thread project deletion fence unavailable")
        return resolved

    def _assign_managed_workspace(
        thread: dict[str, Any],
        *,
        actor_id: str,
        tenant_id: str,
    ) -> dict[str, Any]:
        """Allocate and persist the authenticated thread's server-owned root."""
        if workspace_root is None:
            raise HTTPException(503, "managed thread workspace unavailable")
        thread_id = thread.get("thread_id")
        if not isinstance(thread_id, str) or not thread_id:
            raise HTTPException(503, "thread store returned an invalid thread id")
        try:
            allocation = managed_workspace_metadata(
                workspace_root,
                tenant_id=tenant_id,
                actor_id=actor_id,
                thread_id=thread_id,
            )
            workspace = Path(allocation["workspace_path"])
            directory_identity = _create_workspace_directory(workspace)
        except FileExistsError:
            # A prior/concurrent request is successful only when the store has
            # the exact server-derived allocation for this principal.
            current = store.get(thread_id) if hasattr(store, "get") else None
            raw_metadata = current.get("metadata") if isinstance(current, dict) else None
            current_metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
            verified = verified_managed_workspace(
                workspace_root,
                thread_id=thread_id,
                metadata=current_metadata,
            )
            if (
                isinstance(current, dict)
                and verified is not None
                and current_metadata.get("owner_actor_id") == actor_id
                and current_metadata.get("tenant_id") == tenant_id
            ):
                return current
            raise HTTPException(409, "managed thread workspace already exists") from None
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            _logger.error("managed workspace allocation failed for %s: %s", thread_id, exc)
            raise HTTPException(503, "managed thread workspace unavailable") from exc

        def _recover_committed() -> dict[str, Any] | None:
            try:
                current = store.get(thread_id) if hasattr(store, "get") else None
                raw_metadata = current.get("metadata") if isinstance(current, dict) else None
                current_metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
                verified = verified_managed_workspace(
                    workspace_root,
                    thread_id=thread_id,
                    metadata=current_metadata,
                )
            except (OSError, RuntimeError, TypeError, ValueError):
                return None
            if (
                isinstance(current, dict)
                and verified is not None
                and current_metadata.get("owner_actor_id") == actor_id
                and current_metadata.get("tenant_id") == tenant_id
            ):
                return current
            return None

        def _rollback_uncommitted() -> None:
            # ``thread`` was created/forked by this request. Compare-and-delete
            # under the store lock; if anything changed, assume another request
            # took it over and leave both state and directory untouched.
            delete_if_unchanged = getattr(store, "delete_if_unchanged", None)
            if not callable(delete_if_unchanged):
                return
            try:
                deleted = bool(delete_if_unchanged(thread_id, thread))
            except (OSError, RuntimeError, TypeError, ValueError):
                return
            if deleted:
                _remove_workspace_directory_if_unchanged(workspace, directory_identity)

        try:
            if not hasattr(store, "update_state"):
                raise RuntimeError("thread store cannot persist managed workspace metadata")
            store.update_state(thread_id, metadata=allocation)
            updated = store.get(thread_id) if hasattr(store, "get") else None
        except Exception as exc:  # noqa: BLE001 - compensate every ordinary adapter failure
            recovered = _recover_committed()
            if recovered is not None:
                return recovered
            _rollback_uncommitted()
            _logger.error("managed workspace allocation failed for %s: %s", thread_id, exc)
            raise HTTPException(503, "managed thread workspace unavailable") from exc
        raw_updated_metadata = updated.get("metadata") if isinstance(updated, dict) else None
        updated_metadata = raw_updated_metadata if isinstance(raw_updated_metadata, dict) else {}
        verified = verified_managed_workspace(
            workspace_root,
            thread_id=thread_id,
            metadata=updated_metadata,
        )
        if (
            not isinstance(updated, dict)
            or verified is None
            or updated_metadata.get("owner_actor_id") != actor_id
            or updated_metadata.get("tenant_id") != tenant_id
        ):
            recovered = _recover_committed()
            if recovered is not None:
                return recovered
            _rollback_uncommitted()
            raise HTTPException(503, "managed thread workspace persistence failed")
        return updated

    def _title_service() -> Any:
        if store is None:
            raise HTTPException(503, "thread state unavailable")
        if session_titles is not None:
            return session_titles
        from runtime.memory.threads.session_title import SessionTitleService

        return SessionTitleService(store)

    def _require_thread_id(thread_id: str) -> str:
        from runtime.memory.threads.event_log import validate_thread_id

        try:
            return validate_thread_id(thread_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    def _can_manage(
        thread: dict[str, Any] | None,
        actor_id: str | None,
        tenant_id: str | None = None,
    ) -> bool:
        if thread is None:
            return False
        raw_metadata = thread.get("metadata")
        metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
        stored_tenant = str(metadata.get("tenant_id") or "").strip()
        if tenant_id and not tenant_id.startswith("legacy:") and stored_tenant != tenant_id:
            return False
        if tenant_id and stored_tenant and stored_tenant != tenant_id:
            return False
        owner = metadata.get("owner_actor_id") or metadata.get("actor_id")
        return not isinstance(owner, str) or not owner.strip() or owner.strip() == actor_id

    def _can_read(
        thread: dict[str, Any] | None,
        actor_id: str | None,
        tenant_id: str | None = None,
    ) -> bool:
        if _can_manage(thread, actor_id, tenant_id):
            return True
        if not require_auth or not isinstance(thread, dict):
            return False
        thread_id = thread.get("thread_id")
        if not isinstance(thread_id, str) or not thread_id:
            return False
        return access_resolver.resolve(thread_id, actor_id, tenant_id).can_read

    def _visible_thread(thread_id: str) -> dict[str, Any] | None:
        from runtime.memory.threads import ThreadPermanentlyDeletedError

        try:
            return store.get(thread_id)
        except ThreadPermanentlyDeletedError:
            return None

    def _get_owned_thread(
        thread_id: str,
        actor_id: str | None,
        tenant_id: str | None = None,
    ) -> dict[str, Any] | None:
        thread = _visible_thread(thread_id)
        if not _can_manage(thread, actor_id, tenant_id):
            return None
        return thread

    def _get_accessible_thread(
        thread_id: str,
        actor_id: str | None,
        tenant_id: str | None = None,
    ) -> dict[str, Any] | None:
        thread = _visible_thread(thread_id)
        if not _can_read(thread, actor_id, tenant_id):
            return None
        return thread

    def _is_archived(thread_id: str) -> bool:
        if logs_root is None:
            return False
        from runtime.memory.threads.event_log import EventLog, thread_log_path

        summary = EventLog(thread_log_path(logs_root, thread_id)).summary()
        return bool(summary and summary.archived)

    @router.post("/api/threads")
    def create_thread(request: Request, body: dict[str, Any] | None = None) -> dict[str, Any]:
        actor_id = _auth(request)
        tenant_id = _tenant(request)
        _require_store()
        payload = body or {}
        raw_metadata = payload.get("metadata")
        metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
        metadata = dict(metadata)
        if actor_id is not None:
            # The body may carry presentation metadata, but ownership is
            # server-derived and cannot be assigned to another actor.
            metadata["owner_actor_id"] = actor_id
            metadata["tenant_id"] = tenant_id or ""
        if managed_workspace_required:
            # A shared-mode client may describe presentation state, but it can
            # never choose a host filesystem root or forge the server marker.
            metadata = strip_client_workspace_metadata(metadata)
        raw_values = payload.get("values")
        values = raw_values if isinstance(raw_values, dict) else {}
        created = store.create(metadata=metadata, values=values)
        if managed_workspace_required:
            # ``_auth`` is fail-closed above, so these values are guaranteed in
            # authenticated mode. Keep the guard explicit for type safety and
            # for custom identity-store adapters.
            if not actor_id:
                raise HTTPException(401, "authentication required")
            return _assign_managed_workspace(
                created,
                actor_id=actor_id,
                tenant_id=tenant_id or f"legacy:{actor_id}",
            )
        return created

    @router.get("/api/threads/search")
    def search_threads_get(
        request: Request,
        q: str = "",
        limit: int = Query(20, ge=1, le=200),  # type: ignore[misc]
    ) -> dict[str, Any]:
        actor_id = _auth(request)
        tenant_id = _tenant(request)
        _require_store()
        needle = (q or "").strip().lower()
        results: list[dict[str, Any]] = []
        for thread in store.search(limit=500, offset=0):
            if not _can_read(thread, actor_id, tenant_id):
                continue
            thread_id = thread.get("thread_id")
            if isinstance(thread_id, str) and _is_archived(thread_id):
                continue
            values = thread.get("values") or {}
            title = str(values.get("title") or "")
            raw_messages = values.get("messages")
            messages = raw_messages if isinstance(raw_messages, list) else []
            haystack_parts = [title]
            for message in messages:
                if isinstance(message, dict):
                    haystack_parts.append(str(message.get("content") or ""))
            haystack = "\n".join(haystack_parts).lower()
            if needle and needle not in haystack:
                continue
            snippet = ""
            for part in haystack_parts:
                if needle and needle in part.lower():
                    snippet = part
                    break
            results.append(
                {
                    "thread_id": thread.get("thread_id"),
                    "title": title or "New chat",
                    "snippet": snippet[:240],
                    "created_at": thread.get("created_at"),
                    "updated_at": thread.get("updated_at"),
                    "message_count": len(messages),
                    "values": values,
                    "metadata": thread.get("metadata") or {},
                }
            )
            if len(results) >= limit:
                break
        return {"threads": results}

    # Echo Native Session API v2: full-text search endpoint
    @router.get("/api/threads/fts")
    def full_text_search(
        request: Request,
        q: str = "",
        agent_id: str | None = None,
        team_id: str | None = None,
        after: str | None = None,
        before: str | None = None,
        limit: int = Query(20, ge=1, le=100),  # type: ignore[misc]
    ) -> dict[str, Any]:
        actor_id = _auth(request)
        tenant_id = _tenant(request)
        _require_store()
        if not hasattr(store, "search_threads"):
            raise HTTPException(501, "full-text search not enabled")
        if not getattr(store, "search_enabled", True):
            raise HTTPException(501, "full-text search not enabled")
        query = (q or "").strip()
        if not query:
            raise HTTPException(400, "query parameter 'q' is required")
        try:
            results = store.search_threads(
                query,
                agent_id=agent_id,
                team_id=team_id,
                after=after,
                before=before,
                limit=limit,
            )
        except Exception as exc:
            _logger.exception("search failed")
            raise HTTPException(500, f"search failed: {exc}") from exc
        # Filter results by ownership
        filtered = [
            {
                "thread_id": r.thread_id,
                "title": r.title,
                "snippet": r.snippet,
                "rank": r.rank,
                "created_at": r.created_at,
                "updated_at": r.updated_at,
            }
            for r in results
            if _can_read(_visible_thread(r.thread_id), actor_id, tenant_id)
        ]
        return {"results": filtered, "count": len(filtered)}

    # Echo Native Session API v2: Markdown export (before {thread_id})
    @router.get("/api/threads/{thread_id}/export")
    def export_thread(
        request: Request,
        thread_id: str,
    ) -> Response:
        actor_id = _auth(request)
        tenant_id = _tenant(request)
        _require_store()
        thread_id = _require_thread_id(thread_id)
        if _is_archived(thread_id):
            raise HTTPException(404, f"thread not found: {thread_id}")
        if _get_accessible_thread(thread_id, actor_id, tenant_id) is None:
            raise HTTPException(404, f"thread not found: {thread_id}")
        if not hasattr(store, "export_thread_markdown"):
            raise HTTPException(501, "markdown export not enabled")
        try:
            markdown = store.export_thread_markdown(thread_id)
        except Exception as exc:
            _logger.exception("export failed")
            raise HTTPException(500, f"export failed: {exc}") from exc
        if markdown is None:
            raise HTTPException(404, f"thread not found: {thread_id}")
        return Response(
            content=markdown,
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{thread_id}.md"'},
        )

    # Echo Native Session API v2: feedback endpoints (before {thread_id})
    @router.post("/api/threads/{thread_id}/feedback")
    def add_feedback(
        request: Request,
        thread_id: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        actor_id = _auth(request)
        tenant_id = _tenant(request)
        _require_store()
        thread_id = _require_thread_id(thread_id)
        if _is_archived(thread_id):
            raise HTTPException(404, f"thread not found: {thread_id}")
        if _get_owned_thread(thread_id, actor_id, tenant_id) is None:
            raise HTTPException(404, f"thread not found: {thread_id}")
        if not hasattr(store, "add_message_feedback"):
            raise HTTPException(501, "feedback system not enabled")
        if not getattr(store, "feedback_enabled", True):
            raise HTTPException(501, "feedback system not enabled")
        message_index = body.get("message_index")
        feedback_type = body.get("feedback_type")
        tags = body.get("tags", [])
        comment = body.get("comment", "")
        if not isinstance(message_index, int) or message_index < 0:
            raise HTTPException(400, "message_index must be non-negative integer")
        if feedback_type not in ("thumbs_up", "thumbs_down"):
            raise HTTPException(400, "feedback_type must be 'thumbs_up' or 'thumbs_down'")
        if not isinstance(tags, list):
            raise HTTPException(400, "tags must be a list")
        try:
            feedback = store.add_message_feedback(
                thread_id,
                message_index,
                feedback_type,
                tags=tags,
                comment=comment,
                user_id=actor_id,
            )
        except Exception as exc:
            _logger.exception("add feedback failed")
            raise HTTPException(500, f"add feedback failed: {exc}") from exc
        if feedback is None:
            raise HTTPException(500, "failed to add feedback")
        return {
            "thread_id": feedback.thread_id,
            "message_index": feedback.message_index,
            "feedback_type": feedback.feedback_type,
            "tags": list(feedback.tags),
            "comment": feedback.comment,
            "timestamp": feedback.timestamp,
            "user_id": feedback.user_id,
        }

    @router.get("/api/threads/{thread_id}/feedback/stats")
    def get_feedback_stats(
        request: Request,
        thread_id: str,
    ) -> dict[str, Any]:
        actor_id = _auth(request)
        tenant_id = _tenant(request)
        _require_store()
        thread_id = _require_thread_id(thread_id)
        if _is_archived(thread_id):
            raise HTTPException(404, f"thread not found: {thread_id}")
        if _get_accessible_thread(thread_id, actor_id, tenant_id) is None:
            raise HTTPException(404, f"thread not found: {thread_id}")
        if not hasattr(store, "get_feedback_stats"):
            raise HTTPException(501, "feedback system not enabled")
        if not getattr(store, "feedback_enabled", True):
            raise HTTPException(501, "feedback system not enabled")
        try:
            stats = store.get_feedback_stats(thread_id)
        except Exception as exc:
            _logger.exception("get feedback stats failed")
            raise HTTPException(500, f"get feedback stats failed: {exc}") from exc
        return stats

    @router.get("/api/threads/{thread_id}/feedback")
    def get_feedback(
        request: Request,
        thread_id: str,
        message_index: int | None = None,
    ) -> dict[str, Any]:
        actor_id = _auth(request)
        tenant_id = _tenant(request)
        _require_store()
        thread_id = _require_thread_id(thread_id)
        if _is_archived(thread_id):
            raise HTTPException(404, f"thread not found: {thread_id}")
        if _get_accessible_thread(thread_id, actor_id, tenant_id) is None:
            raise HTTPException(404, f"thread not found: {thread_id}")
        if not hasattr(store, "get_message_feedback"):
            raise HTTPException(501, "feedback system not enabled")
        if not getattr(store, "feedback_enabled", True):
            raise HTTPException(501, "feedback system not enabled")
        try:
            if message_index is not None:
                feedbacks = store.get_message_feedback(thread_id, message_index)
            else:
                feedbacks = store.get_message_feedback(thread_id, None)
        except Exception as exc:
            _logger.exception("get feedback failed")
            raise HTTPException(500, f"get feedback failed: {exc}") from exc
        return {
            "feedbacks": [
                {
                    "thread_id": f.thread_id,
                    "message_index": f.message_index,
                    "feedback_type": f.feedback_type,
                    "tags": list(f.tags),
                    "comment": f.comment,
                    "timestamp": f.timestamp,
                    "user_id": f.user_id,
                }
                for f in feedbacks
            ]
        }

    @router.get("/api/threads/{thread_id}")
    def get_thread(request: Request, thread_id: str) -> dict[str, Any]:
        actor_id = _auth(request)
        tenant_id = _tenant(request)
        _require_store()
        thread_id = _require_thread_id(thread_id)
        if _is_archived(thread_id):
            raise HTTPException(404, f"thread not found: {thread_id}")
        thread = _get_accessible_thread(thread_id, actor_id, tenant_id)
        if thread is None:
            raise HTTPException(404, f"thread not found: {thread_id}")
        return thread

    @router.delete(
        "/api/threads/{thread_id}", status_code=204, response_class=Response, response_model=None
    )
    def delete_thread(request: Request, thread_id: str):
        actor_id = _auth(request)
        tenant_id = _tenant(request)
        _require_store()
        thread_id = _require_thread_id(thread_id)
        existing = _visible_thread(thread_id)
        if existing is not None and not _can_manage(existing, actor_id, tenant_id):
            raise HTTPException(404, f"thread not found: {thread_id}")
        from ._thread_state_delete import delete_thread_state

        delete_thread_state(
            store=store,
            thread_id=thread_id,
            existing=existing,
            actor_id=actor_id,
            tenant_id=tenant_id,
            require_auth=managed_workspace_required,
            workspace_root=workspace_root,
            logs_root=logs_root,
            is_archived=_is_archived,
            project_store=_project_store_for_delete(),
            group_store=group_store,
            logger=_logger,
        )

    @router.post("/api/threads/search")
    def search_threads_post(
        request: Request,
        body: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        actor_id = _auth(request)
        tenant_id = _tenant(request)
        _require_store()
        payload = body or {}
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None
        metadata = dict(metadata) if metadata is not None else None
        if actor_id is not None:
            metadata = metadata or {}
            metadata.pop("owner_actor_id", None)
            metadata.pop("actor_id", None)
            metadata["tenant_id"] = tenant_id or ""
        limit = int(payload.get("limit", 50) or 50)
        offset = int(payload.get("offset", 0) or 0)
        sort_by = str(payload.get("sortBy") or "updated_at")
        sort_order = str(payload.get("sortOrder") or "desc")
        select, internal_select = search_select_fields(payload)
        # A tenant can contain both owned and joined-room threads. Fetch a
        # bounded tenant slice first, apply the dynamic room ACL, then paginate
        # the visible result so same-tenant private threads cannot starve joined
        # conversations from the sidebar.
        fetch_limit = max(500, min(2000, offset + limit * 5)) if require_auth else limit
        results = store.search(
            limit=fetch_limit,
            offset=0 if require_auth else offset,
            metadata=metadata,
            sort_by=sort_by,
            sort_order=sort_order,
            select=internal_select or None,
        )
        visible = [
            thread
            for thread in results
            if _can_read(thread, actor_id, tenant_id)
            if not (isinstance(thread.get("thread_id"), str) and _is_archived(thread["thread_id"]))
        ]
        return project_visible_search_page(
            visible, select=select, offset=offset, limit=limit, require_auth=require_auth
        )

    @router.get("/api/threads/{thread_id}/state")
    def get_thread_state(request: Request, thread_id: str) -> dict[str, Any]:
        actor_id = _auth(request)
        tenant_id = _tenant(request)
        _require_store()
        thread_id = _require_thread_id(thread_id)
        if _is_archived(thread_id):
            raise HTTPException(404, f"thread not found: {thread_id}")
        if _get_accessible_thread(thread_id, actor_id, tenant_id) is None:
            raise HTTPException(404, f"thread not found: {thread_id}")
        state = store.get_state(thread_id)
        if state is None:
            raise HTTPException(404, f"thread not found: {thread_id}")
        return state

    @router.post("/api/threads/{thread_id}/shares", status_code=201)
    def create_thread_share(
        request: Request,
        thread_id: str,
    ) -> dict[str, Any]:
        """Capture a sanitised, immutable public snapshot of one thread."""
        actor_id = _auth(request)
        tenant_id = _tenant(request)
        _require_store()
        thread_id = _require_thread_id(thread_id)
        if share_store is None:
            raise HTTPException(503, "thread sharing unavailable")
        thread = _get_owned_thread(thread_id, actor_id, tenant_id)
        if thread is None or _is_archived(thread_id):
            raise HTTPException(404, f"thread not found: {thread_id}")
        state = store.get_state(thread_id)
        if not isinstance(state, dict):
            raise HTTPException(404, f"thread not found: {thread_id}")
        from .thread_share_store import build_public_thread_snapshot

        try:
            snapshot = build_public_thread_snapshot(thread, state)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if share_relay is not None:
            from .thread_share_relay import ThreadShareRelayError

            try:
                return share_relay.create(
                    source_thread_id=thread_id,
                    snapshot=snapshot,
                    actor_id=actor_id or "",
                    tenant_id=tenant_id or "",
                )
            except ThreadShareRelayError as exc:
                raise HTTPException(502, str(exc)) from exc
        try:
            record = share_store.create(
                thread_id=thread_id,
                actor_id=actor_id or "",
                tenant_id=tenant_id or "",
                snapshot=snapshot,
            )
        except ValueError as exc:
            raise HTTPException(413, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(429, str(exc)) from exc
        token = str(record["token"])
        response = {
            "token": token,
            "share_id": record["share_id"],
            # Hash-only paths preserve either the Vite root shell or /ui/.
            "share_path": f"#/share/{token}",
            "created_at": record["created_at"],
            "expires_at": record["expires_at"],
        }
        share_url = _canonical_public_share_url(token)
        if share_url:
            response["share_url"] = share_url
        return response

    @router.get("/api/threads/{thread_id}/shares")
    def list_thread_shares(request: Request, thread_id: str) -> dict[str, Any]:
        """List owner-manageable share metadata without returning capabilities."""
        actor_id = _auth(request)
        tenant_id = _tenant(request)
        _require_store()
        thread_id = _require_thread_id(thread_id)
        if share_store is None:
            raise HTTPException(503, "thread sharing unavailable")
        if _get_owned_thread(thread_id, actor_id, tenant_id) is None or _is_archived(thread_id):
            raise HTTPException(404, f"thread not found: {thread_id}")
        if share_relay is not None:
            from .thread_share_relay import ThreadShareRelayError

            try:
                return {
                    "shares": share_relay.list_for_thread(
                        source_thread_id=thread_id,
                        actor_id=actor_id or "",
                        tenant_id=tenant_id or "",
                    )
                }
            except ThreadShareRelayError as exc:
                raise HTTPException(502, str(exc)) from exc
        return {
            "shares": share_store.list_for_thread(
                thread_id=thread_id,
                actor_id=actor_id or "",
                tenant_id=tenant_id or "",
            )
        }

    @router.post("/api/public/thread-shares/resolve")
    def resolve_public_thread_share(body: dict[str, Any], response: Response) -> dict[str, Any]:
        """Resolve a capability from the request body so it never enters access logs."""
        if share_store is None:
            raise HTTPException(
                503,
                "thread sharing unavailable",
                headers=_PUBLIC_NO_STORE_HEADERS,
            )
        token = str(body.get("token") or "").strip()
        record = share_store.get(token)
        if record is None:
            raise HTTPException(
                404,
                "shared task not found, expired, or revoked",
                headers=_PUBLIC_NO_STORE_HEADERS,
            )
        response.headers.update(_PUBLIC_NO_STORE_HEADERS)
        return share_store.public_record(record)

    @router.get("/api/public/thread-shares/{token}")
    def get_public_thread_share(token: str, response: Response) -> dict[str, Any]:
        """Legacy anonymous read; new clients use the body-based resolve route."""
        if share_store is None:
            raise HTTPException(
                503,
                "thread sharing unavailable",
                headers=_PUBLIC_NO_STORE_HEADERS,
            )
        record = share_store.get(token)
        if record is None:
            raise HTTPException(
                404,
                "shared task not found or revoked",
                headers=_PUBLIC_NO_STORE_HEADERS,
            )
        response.headers.update(_PUBLIC_NO_STORE_HEADERS)
        response.headers["Deprecation"] = "true"
        return share_store.public_record(record)

    @router.delete(
        "/api/thread-shares/by-id/{share_id}",
        status_code=204,
        response_class=Response,
        response_model=None,
    )
    def revoke_thread_share_by_id(request: Request, share_id: str) -> Response:
        actor_id = _auth(request)
        tenant_id = _tenant(request)
        if share_store is None:
            raise HTTPException(503, "thread sharing unavailable")
        if share_relay is not None:
            from .thread_share_relay import ThreadShareRelayError

            try:
                share_relay.revoke(
                    share_id,
                    actor_id=actor_id or "",
                    tenant_id=tenant_id or "",
                )
            except ThreadShareRelayError as exc:
                raise HTTPException(502, str(exc)) from exc
            return Response(status_code=204)
        if not share_store.revoke_by_id(
            share_id,
            actor_id=actor_id or "",
            tenant_id=tenant_id or "",
        ):
            raise HTTPException(404, "shared task not found")
        return Response(status_code=204)

    @router.delete(
        "/api/thread-shares/{token}",
        status_code=204,
        response_class=Response,
        response_model=None,
    )
    def revoke_thread_share(request: Request, token: str) -> Response:
        actor_id = _auth(request)
        tenant_id = _tenant(request)
        if share_store is None:
            raise HTTPException(503, "thread sharing unavailable")
        if not share_store.revoke(
            token,
            actor_id=actor_id or "",
            tenant_id=tenant_id or "",
        ):
            raise HTTPException(404, "shared task not found")
        return Response(status_code=204)

    @router.post("/api/threads/{thread_id}/state")
    def update_thread_state(
        request: Request,
        thread_id: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        actor_id = _auth(request)
        tenant_id = _tenant(request)
        _require_store()
        thread_id = _require_thread_id(thread_id)
        if _is_archived(thread_id):
            raise HTTPException(404, f"thread not found: {thread_id}")
        if _get_owned_thread(thread_id, actor_id, tenant_id) is None:
            raise HTTPException(404, f"thread not found: {thread_id}")
        try:
            metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else None
            metadata = dict(metadata) if metadata is not None else None
            if managed_workspace_required and metadata is not None:
                metadata = strip_client_workspace_metadata(metadata)
            if actor_id is not None:
                metadata = metadata or {}
                metadata["owner_actor_id"] = actor_id
                metadata["tenant_id"] = tenant_id or ""
            return store.update_state(
                thread_id,
                values=body.get("values") if isinstance(body.get("values"), dict) else None,
                metadata=metadata,
                status=body.get("status") if isinstance(body.get("status"), str) else None,
            )
        except KeyError as exc:
            raise HTTPException(404, f"thread not found: {thread_id}") from exc

    @router.post("/api/threads/{thread_id}/history")
    def get_thread_history(
        request: Request,
        thread_id: str,
        body: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        actor_id = _auth(request)
        tenant_id = _tenant(request)
        _require_store()
        thread_id = _require_thread_id(thread_id)
        if _is_archived(thread_id):
            return []
        if _get_accessible_thread(thread_id, actor_id, tenant_id) is None:
            return []
        payload = body or {}
        limit = int(payload.get("limit", 50) or 50)
        return store.get_history(thread_id, limit=limit)

    @router.post("/api/threads/{thread_id}/fork")
    def fork_thread(
        request: Request,
        thread_id: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Fork a new thread from a completed-turn prefix (dsh sessions.fork).

        ``at_message_index`` anchors the cut at the first completed turn at
        or after it; omitted/out-of-range falls back to the last completed
        turn. Anchoring on an in-flight turn fails with 409
        ``fork-unavailable`` instead of clipping.
        """
        actor_id = _auth(request)
        tenant_id = _tenant(request)
        _require_store()
        thread_id = _require_thread_id(thread_id)
        if _is_archived(thread_id):
            raise HTTPException(404, f"thread not found: {thread_id}")
        if _get_owned_thread(thread_id, actor_id, tenant_id) is None:
            raise HTTPException(404, f"thread not found: {thread_id}")
        payload = body or {}
        at_index = payload.get("at_message_index") if isinstance(payload, dict) else None
        if at_index is not None and not isinstance(at_index, int):
            raise HTTPException(400, "at_message_index must be an integer")
        from runtime.memory.threads.store import ForkUnavailableError

        try:
            child = store.fork_thread(thread_id, at_message_index=at_index)
        except KeyError as exc:
            raise HTTPException(404, f"thread not found: {thread_id}") from exc
        except ForkUnavailableError as exc:
            raise HTTPException(409, "fork-unavailable") from exc
        if managed_workspace_required:
            if not actor_id:
                raise HTTPException(401, "authentication required")
            child = _assign_managed_workspace(
                child,
                actor_id=actor_id,
                tenant_id=tenant_id or f"legacy:{actor_id}",
            )
        _seed_child_realtime_log(logs_root, thread_id, child["thread_id"], at_index)
        values = child.get("values") if isinstance(child.get("values"), dict) else {}
        seeded = values.get("messages") or []
        return {
            "thread_id": child["thread_id"],
            "seeded_messages": len(seeded) if isinstance(seeded, list) else 0,
        }

    @router.post("/api/threads/{thread_id}/title/rename")
    def rename_thread_title(
        request: Request,
        thread_id: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        actor_id = _auth(request)
        tenant_id = _tenant(request)
        _require_store()
        thread_id = _require_thread_id(thread_id)
        if _is_archived(thread_id):
            raise HTTPException(404, f"thread not found: {thread_id}")
        if _get_owned_thread(thread_id, actor_id, tenant_id) is None:
            raise HTTPException(404, f"thread not found: {thread_id}")
        title = body.get("title") if isinstance(body, dict) else None
        if not isinstance(title, str):
            raise HTTPException(400, "title is required")
        try:
            snapshot = _title_service().rename(thread_id, title)
        except KeyError as exc:
            raise HTTPException(404, f"thread not found: {thread_id}") from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return snapshot.to_wire()

    @router.post("/api/threads/{thread_id}/title/refresh")
    def refresh_thread_title(
        request: Request,
        thread_id: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        actor_id = _auth(request)
        tenant_id = _tenant(request)
        _require_store()
        thread_id = _require_thread_id(thread_id)
        if _is_archived(thread_id):
            raise HTTPException(404, f"thread not found: {thread_id}")
        if _get_owned_thread(thread_id, actor_id, tenant_id) is None:
            raise HTTPException(404, f"thread not found: {thread_id}")
        payload = body or {}
        provider = payload.get("provider") if isinstance(payload, dict) else None
        force = bool(payload.get("force", False)) if isinstance(payload, dict) else False
        try:
            snapshot = _title_service().refresh(
                thread_id,
                provider=provider if isinstance(provider, str) else None,
                force=force,
            )
        except KeyError as exc:
            raise HTTPException(404, f"thread not found: {thread_id}") from exc
        return snapshot.to_wire()

    return router


__all__ = ["build_auto_title_service", "create_thread_state_router"]
