"""ThreadStateStore · lightweight thread/state persistence for frontend compat."""

from __future__ import annotations

import contextlib
import copy
import json
import logging
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ._permanent_deletion import (
    ThreadPermanentDeleteLease,
    ThreadPermanentlyDeletedError,
    deletion_record_path,
    deletion_scope_allowed,
    read_deletion_record,
    write_deletion_record,
)
from ._state_mutation_lock import (
    iter_jsonl_records_reverse,
    latest_persisted_thread,
    remove_stale_thread_copies,
    thread_mutation_lock,
)
from .feedback import FeedbackStore, FeedbackType, MessageFeedback
from .session_export import export_thread_to_markdown
from .session_index import SessionIndex, entry_from_thread
from .session_search import SearchResult, SessionSearchIndex

logger = logging.getLogger(__name__)

_PATH_SEGMENT_RE = re.compile(r'[<>:"/\\|?*\x00-\x1F]')


class ForkUnavailableError(RuntimeError):
    """The anchor's turn is still open; dsh ``fork-unavailable``.

    Raised instead of silently clipping to an earlier turn so the caller
    can surface a distinct error to the user.
    """


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"


def _deepcopy(value: Any) -> Any:
    return copy.deepcopy(value)


_STATE_FROM_THREAD_KEY = "state_from_thread"


def _encoded_state_record(state: dict[str, Any]) -> dict[str, Any]:
    """Drop values duplicated verbatim by the record's ``thread`` payload."""

    return {key: value for key, value in state.items() if key not in {"values", "metadata"}}


def _decoded_state_record(
    record: dict[str, Any],
    thread: dict[str, Any],
) -> dict[str, Any] | None:
    raw_state = record.get("state")
    if not isinstance(raw_state, dict):
        return None
    if record.get(_STATE_FROM_THREAD_KEY) is not True:
        return raw_state
    state = dict(raw_state)
    # Thread snapshots are replaced rather than mutated in-place. Sharing the
    # immutable value objects here avoids recreating a second full conversation
    # in memory; all public getters return defensive deep copies.
    state["values"] = thread.get("values", {})
    state["metadata"] = thread.get("metadata", {})
    return state


_PROJECT_BINDING_METADATA_KEYS = frozenset(
    {"project_id", "project_home", "project_binding_generation"}
)
_PROJECT_BINDING_VALUE_KEYS = frozenset({"project_id", "project_home"})


def _without_project_binding_fields(
    payload: dict[str, Any] | None,
    keys: frozenset[str],
) -> dict[str, Any]:
    return {key: _deepcopy(value) for key, value in (payload or {}).items() if key not in keys}


def _sidebar_title_source(value: dict[str, Any]) -> str | None:
    """Extract a compact first-user-message title source without its history."""

    values = value.get("values")
    messages = values.get("messages") if isinstance(values, dict) else None
    if not isinstance(messages, list):
        return None
    for message in messages:
        if not isinstance(message, dict) or message.get("type") != "human":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content[:4096]
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if isinstance(part, str):
                    parts.append(part)
                elif (
                    isinstance(part, dict)
                    and part.get("type") == "text"
                    and isinstance(part.get("text"), str)
                ):
                    parts.append(part["text"])
            title = " ".join(part for part in parts if part)
            return title[:4096] if title else None
        return None
    return None


def _project_fields(
    value: dict[str, Any],
    fields: tuple[str, ...] | list[str],
) -> dict[str, Any]:
    """Copy only requested top-level or dotted fields from a thread row."""

    projected: dict[str, Any] = {}
    for field in fields:
        if not isinstance(field, str):
            continue
        if field == "values.sidebar_title_source":
            raw_values = value.get("values")
            existing_title = (
                raw_values.get("sidebar_title_source") if isinstance(raw_values, dict) else None
            )
            title_source = (
                existing_title if isinstance(existing_title, str) else _sidebar_title_source(value)
            )
            if title_source is not None:
                projected.setdefault("values", {})["sidebar_title_source"] = title_source
            continue
        parts = tuple(part for part in field.split(".") if part)
        if not parts:
            continue
        source: Any = value
        target = projected
        for index, part in enumerate(parts):
            if not isinstance(source, dict) or part not in source:
                break
            if index == len(parts) - 1:
                target[part] = _deepcopy(source[part])
                break
            source = source[part]
            existing = target.get(part)
            if not isinstance(existing, dict):
                existing = {}
                target[part] = existing
            target = existing
    return projected


def _default_values(values: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = {
        "title": "New chat",
        "messages": [],
        "artifacts": [],
    }
    if values:
        merged.update(_deepcopy(values))
    return merged


def _safe_path_segment(value: str, fallback: str) -> str:
    clean = _PATH_SEGMENT_RE.sub("_", value.strip())
    clean = re.sub(r"\s+", "-", clean).strip(" .")
    return clean or fallback


class ThreadStateStore:
    """Thread state store with append-only JSONL persistence.

    Two persistence modes:

    1. **Legacy single-file** — ``ThreadStateStore(path=...)`` · every
       thread's records append to one big jsonl. Matches the original
       ``data/threads.jsonl`` behavior.

    2. **Scoped routing** — ``ThreadStateStore(per_agent_base=<repo>)``
       · team threads write to
       ``<repo>/teams/<team_id>/sessions/<thread_id>.jsonl`` first.
       Solo agent threads write under
       ``<repo>/agents/<agent_id>/sessions/<thread_id>.jsonl``. Threads
       with neither team nor agent metadata fall back to
       ``<repo>/data/sessions/misc/<thread_id>.jsonl``.

    Modes are mutually exclusive (pass only one of ``path`` /
    ``per_agent_base``). ``None`` for both is pure in-memory.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        per_agent_base: str | Path | None = None,
        dated_layout: bool = False,
        index_enabled: bool = True,
        search_enabled: bool = True,
        feedback_enabled: bool = True,
        session_origin: str = "echo",
    ) -> None:
        self._threads: dict[str, dict[str, Any]] = {}
        self._history: dict[str, list[dict[str, Any]]] = {}
        self._memory_thread_deletions: dict[str, ThreadPermanentDeleteLease] = {}
        self._lock = threading.RLock()

        # Mode selection
        if path is not None and per_agent_base is not None:
            raise ValueError(
                "ThreadStateStore: pass only one of path= / per_agent_base=",
            )
        self._path: Path | None = Path(path) if path is not None else None
        self._per_agent_base: Path | None = (
            Path(per_agent_base) if per_agent_base is not None else None
        )

        # ── Ergonomics ─────────────────────────────────────
        # ``dated_layout`` shards new threads under
        # ``sessions/<YYYY>/<MM>/<thread_id>.jsonl``. Reads still
        # cover the flat ``sessions/<thread_id>.jsonl`` so older
        # threads are not orphaned. Only honored in per-agent mode.
        self._dated_layout = dated_layout and self._per_agent_base is not None

        # ``session_origin`` is stamped into the per-thread
        # ``session_meta`` header (see ``_write_session_header``).
        # Default is ``echo`` so operators can tell at a glance
        # which runtime produced a given session file.
        self._session_origin = session_origin

        # Lightweight index for fast list/search. Lives next to the
        # session tree so it's portable. Disabled when neither path
        # nor per_agent_base is set (in-memory tests).
        self._index: SessionIndex | None = None
        if index_enabled:
            index_path = self._resolve_index_path()
            if index_path is not None:
                self._index = SessionIndex(index_path)

        # Echo Native full-text session search index
        self._search: SessionSearchIndex | None = None
        if search_enabled:
            search_path = self._resolve_search_path()
            if search_path is not None:
                self._search = SessionSearchIndex(search_path)

        # Echo Native message-feedback store
        self._feedback: FeedbackStore | None = None
        if feedback_enabled:
            feedback_base = self._resolve_feedback_base()
            if feedback_base is not None:
                self._feedback = FeedbackStore(feedback_base)

        # Load existing records
        if self._path is not None and self._path.exists():
            self._load_from(self._path)
        if self._per_agent_base is not None:
            self._load_from_per_agent_tree()
        self._prune_permanently_deleted_threads_locked()
        self._repair_conflicting_agent_copies_locked()

        # Backfill the index from in-memory state on first boot. The
        # index is authoritative AFTER first write; before then we
        # need to seed it from whatever the file walk discovered.
        if self._index is not None and len(self._index) == 0 and self._threads:
            self._reindex_all_locked()

    # ─── index helpers ───────────────────────────────────────

    def _resolve_index_path(self) -> Path | None:
        if self._per_agent_base is not None:
            return self._per_agent_base / "data" / "sessions" / "session_index.jsonl"
        if self._path is not None:
            return self._path.with_name("session_index.jsonl")
        return None

    def _resolve_search_path(self) -> Path | None:
        """Resolve the path for the Echo FTS5 search database."""
        if self._per_agent_base is not None:
            return self._per_agent_base / "data" / "sessions" / "search.db"
        if self._path is not None:
            return self._path.with_suffix(".search.db")
        return None

    def _resolve_feedback_base(self) -> Path | None:
        """Resolve the base path for Echo feedback storage."""
        if self._per_agent_base is not None:
            return self._per_agent_base / "data" / "sessions"
        if self._path is not None:
            return self._path.parent
        return None

    def _index_file_for(self, target: Path) -> str:
        """Return the per-thread file path **as recorded in the
        index**, repo-relative when in per-agent mode so the index
        stays portable across moves of the project root.
        """
        if self._per_agent_base is None:
            return str(target)
        try:
            return str(target.resolve().relative_to(self._per_agent_base.resolve())).replace(
                "\\", "/"
            )
        except ValueError:
            return str(target)

    def _reindex_all_locked(self) -> None:
        assert self._index is not None
        for _thread_id, thread in self._threads.items():
            target = (
                self._per_thread_path(thread) if self._per_agent_base is not None else self._path
            )
            if target is None:
                continue
            entry = entry_from_thread(
                thread,
                file_path=self._index_file_for(target),
            )
            if entry is not None:
                self._index.upsert(entry)

    def _thread_delete_record_locked(
        self,
        thread_id: str,
    ) -> ThreadPermanentDeleteLease | None:
        path = deletion_record_path(
            journal_path=self._path,
            per_agent_base=self._per_agent_base,
            thread_id=thread_id,
        )
        if path is None:
            return self._memory_thread_deletions.get(thread_id)
        record = read_deletion_record(path)
        if record is not None and record.thread_id != thread_id:
            raise RuntimeError("thread deletion record id mismatch")
        return record

    def _write_thread_delete_record_locked(self, lease: ThreadPermanentDeleteLease) -> None:
        path = deletion_record_path(
            journal_path=self._path,
            per_agent_base=self._per_agent_base,
            thread_id=lease.thread_id,
        )
        if path is None:
            self._memory_thread_deletions[lease.thread_id] = lease
            return
        write_deletion_record(path, lease)

    def _assert_thread_writable_locked(self, thread_id: str) -> None:
        if self._thread_delete_record_locked(thread_id) is not None:
            raise ThreadPermanentlyDeletedError(thread_id)

    def _latest_persisted_locked(self, thread_id: str):
        """Read durable state using the current canonical path when known."""

        source_hint: Path | None = self._path
        if source_hint is None and self._per_agent_base is not None:
            current = self._threads.get(thread_id)
            if current is not None:
                source_hint = self._per_thread_path(current)
        return latest_persisted_thread(
            journal_path=self._path,
            per_agent_base=self._per_agent_base,
            thread_id=thread_id,
            source_hint=source_hint,
        )

    def _prune_permanently_deleted_threads_locked(self) -> None:
        """Drop stale snapshots whose durable delete fence already exists."""

        for thread_id in tuple(self._threads):
            if self._thread_delete_record_locked(thread_id) is None:
                continue
            self._threads.pop(thread_id, None)
            self._history.pop(thread_id, None)

    def is_permanently_deleted(self, thread_id: str) -> bool:
        """Return whether normal readers and writers are permanently fenced."""

        with (
            self._lock,
            thread_mutation_lock(
                journal_path=self._path,
                per_agent_base=self._per_agent_base,
                thread_id=thread_id,
            ),
        ):
            return self._thread_delete_record_locked(thread_id) is not None

    def assert_not_permanently_deleted(self, thread_id: str) -> None:
        """Fail closed when a permanent delete is in progress or finalized."""

        with (
            self._lock,
            thread_mutation_lock(
                journal_path=self._path,
                per_agent_base=self._per_agent_base,
                thread_id=thread_id,
            ),
        ):
            self._assert_thread_writable_locked(thread_id)

    def thread_delete_lease(
        self,
        thread_id: str,
        *,
        tenant_id: str = "",
        owner_id: str = "",
    ) -> ThreadPermanentDeleteLease | None:
        """Read an authorized durable permanent-deletion claim or tombstone."""

        tenant_id = str(tenant_id or "").strip()
        owner_id = str(owner_id or "").strip()
        with (
            self._lock,
            thread_mutation_lock(
                journal_path=self._path,
                per_agent_base=self._per_agent_base,
                thread_id=thread_id,
            ),
        ):
            lease = self._thread_delete_record_locked(thread_id)
            if lease is not None and not deletion_scope_allowed(
                lease,
                tenant_id=tenant_id,
                owner_id=owner_id,
            ):
                raise PermissionError("thread deletion belongs to another principal")
            return lease

    def thread_for_permanent_delete(
        self,
        thread_id: str,
        token: str,
    ) -> dict[str, Any] | None:
        """Read the deletion snapshot through the exact durable lease token.

        This is the narrow retry path for filesystem cleanup after a process
        restart. Normal readers remain fenced as soon as deletion begins.
        """

        token = str(token or "").strip()
        if not token:
            raise ValueError("thread delete token is required")
        with (
            self._lock,
            thread_mutation_lock(
                journal_path=self._path,
                per_agent_base=self._per_agent_base,
                thread_id=thread_id,
            ),
        ):
            lease = self._thread_delete_record_locked(thread_id)
            if lease is None or lease.token != token:
                raise ThreadPermanentlyDeletedError(thread_id)
            if lease.finalized:
                return None
            persisted = self._latest_persisted_locked(thread_id)
            current = persisted.thread if persisted.found else self._threads.get(thread_id)
            return _deepcopy(current) if current is not None else None

    def begin_permanent_delete(
        self,
        thread_id: str,
        *,
        tenant_id: str = "",
        owner_id: str = "",
        expected: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        status: str = "deleting",
    ) -> ThreadPermanentDeleteLease:
        """Irreversibly fence every normal state writer for ``thread_id``."""

        tenant_id = str(tenant_id or "").strip()
        owner_id = str(owner_id or "").strip()
        with (
            self._lock,
            thread_mutation_lock(
                journal_path=self._path,
                per_agent_base=self._per_agent_base,
                thread_id=thread_id,
            ),
        ):
            existing = self._thread_delete_record_locked(thread_id)
            if existing is not None:
                if not deletion_scope_allowed(
                    existing,
                    tenant_id=tenant_id,
                    owner_id=owner_id,
                ):
                    raise PermissionError("thread deletion belongs to another principal")
                return existing
            persisted = self._latest_persisted_locked(thread_id)
            current = persisted.thread if persisted.found else self._threads.get(thread_id)
            if expected is not None and current != expected:
                raise RuntimeError("thread state changed before permanent deletion")
            if current is not None:
                thread = _deepcopy(current)
                merged_metadata = _deepcopy(thread.get("metadata") or {})
                if metadata:
                    merged_metadata.update(_deepcopy(metadata))
                thread["metadata"] = merged_metadata
                thread["status"] = status
                thread["updated_at"] = _utc_now_iso()
                state = self._make_state(thread)
                target = self._append_upsert(
                    thread,
                    state,
                    revision=persisted.revision + 1,
                )
                remove_stale_thread_copies(
                    journal_path=self._path,
                    per_agent_base=self._per_agent_base,
                    thread_id=thread_id,
                    keep_path=target,
                )
                self._threads[thread_id] = thread
                self._remember_state_locked(thread_id, state)
            lease = ThreadPermanentDeleteLease(
                thread_id=thread_id,
                token=f"TD-{uuid4().hex}",
                resumed=False,
                finalized=False,
                tenant_id=tenant_id,
                owner_id=owner_id,
            )
            self._write_thread_delete_record_locked(lease)
            return lease

    def finalize_permanent_delete(self, thread_id: str, token: str) -> bool:
        """Delete the snapshot and retain a permanent writer tombstone."""

        token = str(token or "").strip()
        if not token:
            raise ValueError("thread delete token is required")

        with (
            self._lock,
            thread_mutation_lock(
                journal_path=self._path,
                per_agent_base=self._per_agent_base,
                thread_id=thread_id,
            ),
        ):
            lease = self._thread_delete_record_locked(thread_id)
            if lease is None or lease.token != token:
                raise ThreadPermanentlyDeletedError(thread_id)
            return self._finalize_permanent_delete_locked(lease)

    def permanently_delete(self, thread_id: str, token: str) -> bool:
        """Permanently fence and delete a thread using an idempotency token.

        The fence is durable before any snapshot/index removal starts. A retry
        with the exact token completes or confirms the delete; a different
        token can never take over an existing deletion.
        """

        token = str(token or "").strip()
        if not token:
            raise ValueError("thread delete token is required")
        with (
            self._lock,
            thread_mutation_lock(
                journal_path=self._path,
                per_agent_base=self._per_agent_base,
                thread_id=thread_id,
            ),
        ):
            lease = self._thread_delete_record_locked(thread_id)
            if lease is None:
                lease = ThreadPermanentDeleteLease(
                    thread_id=thread_id,
                    token=token,
                    resumed=False,
                    finalized=False,
                )
                self._write_thread_delete_record_locked(lease)
            elif lease.token != token:
                raise ThreadPermanentlyDeletedError(thread_id)
            return self._finalize_permanent_delete_locked(lease)

    def _finalize_permanent_delete_locked(self, lease: ThreadPermanentDeleteLease) -> bool:
        if lease.finalized:
            return True
        thread_id = lease.thread_id
        persisted = self._latest_persisted_locked(thread_id)
        current = persisted.thread if persisted.found else self._threads.get(thread_id)
        target = self._append_delete(
            thread_id,
            _deepcopy(current) if current is not None else None,
            revision=persisted.revision + 1,
        )
        remove_stale_thread_copies(
            journal_path=self._path,
            per_agent_base=self._per_agent_base,
            thread_id=thread_id,
            keep_path=target,
        )
        self._threads.pop(thread_id, None)
        self._history.pop(thread_id, None)
        self._write_thread_delete_record_locked(
            ThreadPermanentDeleteLease(
                thread_id=thread_id,
                token=lease.token,
                resumed=True,
                finalized=True,
                tenant_id=lease.tenant_id,
                owner_id=lease.owner_id,
            )
        )
        return True

    def create(
        self,
        *,
        metadata: dict[str, Any] | None = None,
        values: dict[str, Any] | None = None,
        status: str = "idle",
    ) -> dict[str, Any]:
        now = _utc_now_iso()
        thread: dict[str, Any] = {
            "thread_id": uuid4().hex,
            "status": status,
            "created_at": now,
            "updated_at": now,
            "metadata": _without_project_binding_fields(
                metadata,
                _PROJECT_BINDING_METADATA_KEYS,
            ),
            "values": _default_values(
                _without_project_binding_fields(values, _PROJECT_BINDING_VALUE_KEYS)
            ),
        }
        state = self._make_state(thread)
        with self._lock:
            self._threads[thread["thread_id"]] = thread
            self._history[thread["thread_id"]] = [state]
            self._append_upsert(thread, state)
            return _deepcopy(thread)

    def ensure_thread(
        self,
        thread_id: str,
        *,
        metadata: dict[str, Any] | None = None,
        values: dict[str, Any] | None = None,
        status: str = "idle",
    ) -> dict[str, Any]:
        with (
            self._lock,
            thread_mutation_lock(
                journal_path=self._path,
                per_agent_base=self._per_agent_base,
                thread_id=thread_id,
            ),
        ):
            self._assert_thread_writable_locked(thread_id)
            persisted = self._latest_persisted_locked(thread_id)
            if persisted.found and persisted.thread is not None:
                existing = _deepcopy(persisted.thread)
                self._threads[thread_id] = existing
                remove_stale_thread_copies(
                    journal_path=self._path,
                    per_agent_base=self._per_agent_base,
                    thread_id=thread_id,
                    keep_path=persisted.source_path,
                )
                return _deepcopy(existing)
            existing = self._threads.get(thread_id) if not persisted.found else None
            if existing is not None:
                return _deepcopy(existing)

            now = _utc_now_iso()
            thread: dict[str, Any] = {
                "thread_id": thread_id,
                "status": status,
                "created_at": now,
                "updated_at": now,
                "metadata": _without_project_binding_fields(
                    metadata,
                    _PROJECT_BINDING_METADATA_KEYS,
                ),
                "values": _default_values(
                    _without_project_binding_fields(values, _PROJECT_BINDING_VALUE_KEYS)
                ),
            }
            state = self._make_state(thread)
            self._threads[thread_id] = thread
            self._history[thread_id] = [state]
            target = self._append_upsert(thread, state, revision=persisted.revision + 1)
            remove_stale_thread_copies(
                journal_path=self._path,
                per_agent_base=self._per_agent_base,
                thread_id=thread_id,
                keep_path=target,
            )
            return _deepcopy(thread)

    def fork_thread(
        self,
        thread_id: str,
        *,
        at_message_index: int | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        """Fork a new thread from a completed-turn prefix (dsh ``sessions.fork``).

        The cut boundary is the first completed turn at or after
        ``at_message_index`` (a message's fork button passes its index, so
        the fork includes that whole turn); an omitted or out-of-range
        anchor falls back to the source's last completed turn. A turn is
        ``open`` when it is the last turn and has no assistant message yet
        (no snapshot has been written for it) — anchoring on an open turn
        raises :class:`ForkUnavailableError` instead of clipping to an
        earlier turn. When no completed turn exists the seed is empty and
        the child behaves like a fresh thread.

        The child inherits ordinary source metadata (agent / team / owner /
        tenant / workspace), but not Project OS binding fields, and records its lineage
        as ``metadata.parent_thread_id`` + ``metadata.parent_message_index``
        (the last included source message index, ``-1`` for an empty seed).
        Only conversation history is transferred — artifacts and live state
        are not copied (dsh: "the seed transfers conversation history only").
        """
        with (
            self._lock,
            thread_mutation_lock(
                journal_path=self._path,
                per_agent_base=self._per_agent_base,
                thread_id=thread_id,
            ),
        ):
            self._assert_thread_writable_locked(thread_id)
            persisted = self._latest_persisted_locked(thread_id)
            source = persisted.thread if persisted.found else self._threads.get(thread_id)
            if source is None:
                raise KeyError(thread_id)
            self._threads[thread_id] = _deepcopy(source)
            raw_source_values = source.get("values")
            source_values: dict[str, Any] = (
                raw_source_values if isinstance(raw_source_values, dict) else {}
            )
            messages = source_values.get("messages") or []
            if not isinstance(messages, list):
                messages = []
            humans = [
                idx
                for idx, msg in enumerate(messages)
                if isinstance(msg, dict) and msg.get("type") == "human"
            ]

            cut = self._resolve_fork_cut(messages, humans, at_message_index)
            seed = messages[: cut + 1] if cut >= 0 else []

            now = _utc_now_iso()
            child: dict[str, Any] = {
                "thread_id": uuid4().hex,
                "status": "idle",
                "created_at": now,
                "updated_at": now,
                "metadata": _without_project_binding_fields(
                    source.get("metadata") if isinstance(source.get("metadata"), dict) else None,
                    _PROJECT_BINDING_METADATA_KEYS,
                ),
                "values": _default_values({}),
            }
            lineage = child["metadata"]
            lineage["parent_thread_id"] = source["thread_id"]
            lineage["parent_message_index"] = cut
            src_title = source_values.get("title")
            child["values"]["messages"] = _deepcopy(seed)
            child["values"]["title"] = (
                title.strip()
                if isinstance(title, str) and title.strip()
                else src_title or "New chat"
            )
            state = self._make_state(child)
            self._threads[child["thread_id"]] = child
            self._history[child["thread_id"]] = [state]
            self._append_upsert(child, state)
            return _deepcopy(child)

    @staticmethod
    def _resolve_fork_cut(
        messages: list[dict[str, Any]],
        humans: list[int],
        at_message_index: int | None,
    ) -> int:
        """Return the last source message index to seed (``-1`` = empty)."""
        if not humans:
            return -1

        anchor: int | None = None
        if at_message_index is not None and 0 <= at_message_index < len(messages):
            anchor = at_message_index

        if anchor is not None:
            # Turn containing the anchor = the human message at/before it.
            turn_human = 0
            for idx in humans:
                if idx <= anchor:
                    turn_human = idx
                else:
                    break
            next_human = next((idx for idx in humans if idx > turn_human), None)
            if next_human is None:
                # The anchor is inside the last turn.
                if not any(
                    isinstance(msg, dict) and msg.get("type") == "ai"
                    for msg in messages[turn_human + 1 :]
                ):
                    raise ForkUnavailableError(
                        "anchor turn is still open; no completed turn to fork"
                    )
                return len(messages) - 1
            return next_human - 1

        # No anchor: cut at the end of the last completed turn.
        last_human = humans[-1]
        if any(
            isinstance(msg, dict) and msg.get("type") == "ai" for msg in messages[last_human + 1 :]
        ):
            return len(messages) - 1
        # Last turn is open (queued prompt without a snapshot): fall back to
        # the previous completed turn; empty seed when none exists.
        return last_human - 1 if len(humans) >= 2 else -1

    def get(self, thread_id: str) -> dict[str, Any] | None:
        with (
            self._lock,
            thread_mutation_lock(
                journal_path=self._path,
                per_agent_base=self._per_agent_base,
                thread_id=thread_id,
            ),
        ):
            self._assert_thread_writable_locked(thread_id)
            thread = self._threads.get(thread_id)
            return _deepcopy(thread) if thread is not None else None

    def delete(self, thread_id: str) -> bool:
        with (
            self._lock,
            thread_mutation_lock(
                journal_path=self._path,
                per_agent_base=self._per_agent_base,
                thread_id=thread_id,
            ),
        ):
            self._assert_thread_writable_locked(thread_id)
            persisted = self._latest_persisted_locked(thread_id)
            current = persisted.thread if persisted.found else self._threads.get(thread_id)
            if current is None:
                return False
            thread = _deepcopy(current)
            # Persist the tombstone before making the deletion visible in
            # memory.  If durable append fails, callers can retry against the
            # unchanged thread instead of receiving success for a state that
            # would resurrect on restart.
            target = self._append_delete(
                thread_id,
                thread,
                revision=persisted.revision + 1,
            )
            remove_stale_thread_copies(
                journal_path=self._path,
                per_agent_base=self._per_agent_base,
                thread_id=thread_id,
                keep_path=target,
            )
            self._threads.pop(thread_id, None)
            self._history.pop(thread_id, None)
            return True

    def delete_if_unchanged(self, thread_id: str, expected: dict[str, Any]) -> bool:
        """Atomically delete a thread only while it matches *expected*.

        Allocation rollback uses this compare-and-delete primitive so a
        failure cannot erase a thread that another request updated or claimed
        between the compensating read and delete.
        """
        with (
            self._lock,
            thread_mutation_lock(
                journal_path=self._path,
                per_agent_base=self._per_agent_base,
                thread_id=thread_id,
            ),
        ):
            self._assert_thread_writable_locked(thread_id)
            persisted = self._latest_persisted_locked(thread_id)
            current = persisted.thread if persisted.found else self._threads.get(thread_id)
            if current is None or current != expected:
                return False
            thread = _deepcopy(current)
            target = self._append_delete(
                thread_id,
                thread,
                revision=persisted.revision + 1,
            )
            remove_stale_thread_copies(
                journal_path=self._path,
                per_agent_base=self._per_agent_base,
                thread_id=thread_id,
                keep_path=target,
            )
            self._threads.pop(thread_id, None)
            self._history.pop(thread_id, None)
            return True

    def clear(self) -> None:
        """Drop all in-memory threads and rebuild persistent indices on disk."""
        with self._lock:
            self._threads.clear()
            self._history.clear()

    def search(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        metadata: dict[str, Any] | None = None,
        sort_by: str = "updated_at",
        sort_order: str = "desc",
        select: tuple[str, ...] | list[str] | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            threads = [
                thread
                for thread_id, thread in self._threads.items()
                if self._thread_delete_record_locked(thread_id) is None
            ]
        if metadata:
            threads = [
                thread
                for thread in threads
                if all(thread["metadata"].get(key) == value for key, value in metadata.items())
            ]
        reverse = sort_order.lower() != "asc"
        threads.sort(key=lambda item: item.get(sort_by) or "", reverse=reverse)
        selected = threads[offset : offset + limit]
        if select:
            return [_project_fields(item, select) for item in selected]
        return [_deepcopy(item) for item in selected]

    def get_state(self, thread_id: str) -> dict[str, Any] | None:
        with (
            self._lock,
            thread_mutation_lock(
                journal_path=self._path,
                per_agent_base=self._per_agent_base,
                thread_id=thread_id,
            ),
        ):
            self._assert_thread_writable_locked(thread_id)
            history = self._history.get(thread_id)
            if not history:
                return None
            return _deepcopy(history[-1])

    def _remember_state_locked(self, thread_id: str, state: dict[str, Any]) -> None:
        """Retain only the latest state in RAM when a durable journal exists.

        Every checkpoint contains the complete message list. Keeping all of
        those copies made process memory grow with ``turns × conversation
        size`` even though history is already durable on disk. Pure in-memory
        stores still retain their full history for test and embedding use.
        """

        if self._path is not None or self._per_agent_base is not None:
            self._history[thread_id] = [state]
            return
        self._history.setdefault(thread_id, []).append(state)

    def _history_from_journal_locked(
        self,
        thread_id: str,
        *,
        limit: int,
    ) -> list[dict[str, Any]] | None:
        """Read newest history states lazily, stopping at the delete boundary."""

        if self._path is not None:
            target = self._path
        elif self._per_agent_base is not None:
            thread = self._threads.get(thread_id)
            target = self._per_thread_path(thread) if thread is not None else None
        else:
            return None
        if target is None or not target.exists():
            return None

        states: list[dict[str, Any]] = []
        for record in iter_jsonl_records_reverse(target):
            if record.get("thread_id") != thread_id:
                continue
            if record.get("op") == "delete":
                break
            thread = record.get("thread")
            if not isinstance(thread, dict):
                continue
            state = _decoded_state_record(record, thread)
            if state is None:
                continue
            states.append(state)
            if limit > 0 and len(states) >= limit:
                break
        return states

    def get_history(self, thread_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        with (
            self._lock,
            thread_mutation_lock(
                journal_path=self._path,
                per_agent_base=self._per_agent_base,
                thread_id=thread_id,
            ),
        ):
            self._assert_thread_writable_locked(thread_id)
            persisted = self._history_from_journal_locked(thread_id, limit=limit)
            if persisted is not None:
                return [_deepcopy(item) for item in persisted]
            history = self._history.get(thread_id, [])
            sliced = history[-limit:] if limit > 0 else history[:]
            return [_deepcopy(item) for item in reversed(sliced)]

    def update_state(
        self,
        thread_id: str,
        *,
        values: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        with (
            self._lock,
            thread_mutation_lock(
                journal_path=self._path,
                per_agent_base=self._per_agent_base,
                thread_id=thread_id,
            ),
        ):
            self._assert_thread_writable_locked(thread_id)
            persisted = self._latest_persisted_locked(thread_id)
            current = persisted.thread if persisted.found else self._threads.get(thread_id)
            if current is None:
                raise KeyError(thread_id)
            thread = _deepcopy(current)
            merged_values = _default_values(thread.get("values"))
            if values:
                for key, value in _without_project_binding_fields(
                    values,
                    _PROJECT_BINDING_VALUE_KEYS,
                ).items():
                    merged_values[key] = _deepcopy(value)
            merged_metadata = _deepcopy(thread.get("metadata", {}))
            if metadata:
                merged_metadata.update(
                    _without_project_binding_fields(
                        metadata,
                        _PROJECT_BINDING_METADATA_KEYS,
                    )
                )
            # Persona is a thread-creation property, not mutable turn state.
            # Keeping it stable also keeps the append-only session in one
            # ``agents/<owner>/sessions`` shard. Role switching must create a
            # new thread instead of moving an existing conversation.
            owner_agent_id = self._agent_id_for(thread)
            if owner_agent_id:
                merged_metadata["agent"] = owner_agent_id
                for key in ("agent_name", "agent_id", "assistant_id"):
                    if key in merged_metadata:
                        merged_metadata[key] = owner_agent_id
            thread["values"] = merged_values
            thread["metadata"] = merged_metadata
            thread["updated_at"] = _utc_now_iso()
            if status is not None:
                thread["status"] = status
            state = self._make_state(thread)
            self._threads[thread_id] = thread
            self._remember_state_locked(thread_id, state)
            target = self._append_upsert(thread, state, revision=persisted.revision + 1)
            remove_stale_thread_copies(
                journal_path=self._path,
                per_agent_base=self._per_agent_base,
                thread_id=thread_id,
                keep_path=target,
            )
            return _deepcopy(state)

    def update_state_if_unchanged(
        self,
        thread_id: str,
        expected: dict[str, Any],
        *,
        values: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        status: str | None = None,
    ) -> dict[str, Any] | None:
        """Atomically update a thread only while it matches ``expected``.

        Managed-workspace deletion uses this transition to persist its
        retryable tombstone before moving any directory.  A concurrent owner,
        scope or state change therefore aborts deletion instead of applying a
        stale filesystem decision.
        """
        with (
            self._lock,
            thread_mutation_lock(
                journal_path=self._path,
                per_agent_base=self._per_agent_base,
                thread_id=thread_id,
            ),
        ):
            self._assert_thread_writable_locked(thread_id)
            persisted = self._latest_persisted_locked(thread_id)
            current = persisted.thread if persisted.found else self._threads.get(thread_id)
            if current is None or current != expected:
                return None
            thread = _deepcopy(current)
            merged_values = _default_values(thread.get("values"))
            if values:
                for key, value in _without_project_binding_fields(
                    values,
                    _PROJECT_BINDING_VALUE_KEYS,
                ).items():
                    merged_values[key] = _deepcopy(value)
            merged_metadata = _deepcopy(thread.get("metadata", {}))
            if metadata:
                merged_metadata.update(
                    _without_project_binding_fields(
                        metadata,
                        _PROJECT_BINDING_METADATA_KEYS,
                    )
                )
            owner_agent_id = self._agent_id_for(thread)
            if owner_agent_id:
                merged_metadata["agent"] = owner_agent_id
                for key in ("agent_name", "agent_id", "assistant_id"):
                    if key in merged_metadata:
                        merged_metadata[key] = owner_agent_id
            thread["values"] = merged_values
            thread["metadata"] = merged_metadata
            thread["updated_at"] = _utc_now_iso()
            if status is not None:
                thread["status"] = status
            state = self._make_state(thread)
            self._threads[thread_id] = thread
            self._remember_state_locked(thread_id, state)
            target = self._append_upsert(thread, state, revision=persisted.revision + 1)
            remove_stale_thread_copies(
                journal_path=self._path,
                per_agent_base=self._per_agent_base,
                thread_id=thread_id,
                keep_path=target,
            )
            return _deepcopy(thread)

    def set_project_binding_metadata(
        self,
        thread_id: str,
        project_id: str | None,
        *,
        expected_project_id: str | None = None,
        generation: int | None = None,
    ) -> dict[str, Any]:
        """Project the optional Project OS binding into thread metadata.

        This dedicated transition can *remove* keys, unlike ``update_state``'s
        merge semantics.  Conversation values and history are otherwise left
        untouched.  The compare guard prevents a stale detach from clearing a
        newer project binding.
        """

        desired = str(project_id or "").strip()
        expected = str(expected_project_id or "").strip()
        with (
            self._lock,
            thread_mutation_lock(
                journal_path=self._path,
                per_agent_base=self._per_agent_base,
                thread_id=thread_id,
            ),
        ):
            self._assert_thread_writable_locked(thread_id)
            persisted = self._latest_persisted_locked(thread_id)
            current = persisted.thread if persisted.found else self._threads.get(thread_id)
            if current is None:
                raise KeyError(thread_id)
            thread = _deepcopy(current)
            metadata = _deepcopy(thread.get("metadata") or {})
            values = _default_values(thread.get("values"))
            metadata_id = str(metadata.get("project_id") or "").strip()
            values_id = str(values.get("project_id") or "").strip()
            if metadata_id and values_id and metadata_id != values_id:
                raise RuntimeError("thread project metadata is inconsistent")
            current_id = metadata_id or values_id
            raw_generation = metadata.get("project_binding_generation")
            has_generation = isinstance(raw_generation, int)
            current_generation = max(0, raw_generation) if isinstance(raw_generation, int) else 0
            if generation is not None and (not isinstance(generation, int) or generation < 0):
                raise ValueError("project binding generation must be non-negative")
            incoming_generation = current_generation if generation is None else generation
            if incoming_generation < current_generation:
                raise RuntimeError("stale thread project binding generation")
            if incoming_generation == current_generation and has_generation:
                if desired != current_id:
                    raise RuntimeError("thread project binding generation conflict")
                return _deepcopy(thread)
            if generation is None:
                if expected and current_id and current_id != expected:
                    raise RuntimeError("thread project metadata changed")
                if desired and current_id and current_id != desired:
                    raise RuntimeError("thread is already projected to another project")

            if desired:
                metadata["project_id"] = desired
                metadata["project_home"] = True
                values["project_id"] = desired
                values["project_home"] = True
            else:
                metadata.pop("project_id", None)
                metadata.pop("project_home", None)
                values.pop("project_id", None)
                values.pop("project_home", None)
            if generation is not None:
                metadata["project_binding_generation"] = incoming_generation

            thread["metadata"] = metadata
            thread["values"] = values
            thread["updated_at"] = _utc_now_iso()
            state = self._make_state(thread)
            self._threads[thread_id] = thread
            self._remember_state_locked(thread_id, state)
            target = self._append_upsert(thread, state, revision=persisted.revision + 1)
            remove_stale_thread_copies(
                journal_path=self._path,
                per_agent_base=self._per_agent_base,
                thread_id=thread_id,
                keep_path=target,
            )
            return _deepcopy(thread)

    def __len__(self) -> int:
        with self._lock:
            return sum(
                self._thread_delete_record_locked(thread_id) is None for thread_id in self._threads
            )

    def _make_state(self, thread: dict[str, Any]) -> dict[str, Any]:
        checkpoint_id = uuid4().hex
        return {
            "values": thread["values"],
            "next": [],
            "metadata": thread["metadata"],
            "checkpoint": {
                "id": checkpoint_id,
                "checkpoint_id": checkpoint_id,
                "ts": thread["updated_at"],
            },
            "checkpoint_id": checkpoint_id,
            "tasks": [],
        }

    # ─── path routing ────────────────────────────────────────

    def _agent_id_for(self, thread: dict[str, Any]) -> str | None:
        """Extract a stable agent_id from a thread's metadata (if any).

        Metadata keys we try, in order: ``agent`` · ``agent_name`` ·
        ``agent_id`` · ``assistant_id``. We filter out the ``lead_agent`` placeholder
        (used by the OpenAI-compat gateway when no persona is bound).
        """
        meta = thread.get("metadata") or {}
        for key in ("agent", "agent_name", "agent_id", "assistant_id"):
            value = meta.get(key)
            if isinstance(value, str) and value and value != "lead_agent":
                return value
        return None

    def _team_id_for(self, thread: dict[str, Any]) -> str | None:
        meta = thread.get("metadata") or {}
        value = meta.get("team_id")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    def _team_path_for(self, thread: dict[str, Any]) -> Path | None:
        if self._per_agent_base is None:
            return None
        thread_id = thread.get("thread_id")
        team_id = self._team_id_for(thread)
        if not isinstance(thread_id, str) or not thread_id or team_id is None:
            return None
        sess_root = (
            self._per_agent_base / "teams" / _safe_path_segment(team_id, "team") / "sessions"
        )
        return self._resolve_thread_file(sess_root, thread_id)

    def _agent_path_for(self, thread: dict[str, Any]) -> Path | None:
        if self._per_agent_base is None:
            return None
        thread_id = thread.get("thread_id")
        if not isinstance(thread_id, str) or not thread_id:
            return None
        agent_id = self._agent_id_for(thread)
        if agent_id is None:
            return None
        sess_root = (
            self._per_agent_base / "agents" / _safe_path_segment(agent_id, "agent") / "sessions"
        )
        return self._resolve_thread_file(sess_root, thread_id)

    def _misc_path_for(self, thread_id: str) -> Path | None:
        if self._per_agent_base is None:
            return None
        sess_root = self._per_agent_base / "data" / "sessions" / "misc"
        return self._resolve_thread_file(sess_root, thread_id)

    def _resolve_thread_file(self, sess_root: Path, thread_id: str) -> Path:
        """Pick the on-disk path for a thread's jsonl.

        Read order: any existing file (flat OR dated) wins so an
        upgrade from flat → dated layout doesn't orphan history.
        Write order: dated when ``dated_layout`` is on, flat
        otherwise.
        """
        flat = sess_root / f"{thread_id}.jsonl"
        if flat.exists():
            return flat
        if self._dated_layout:
            # Look for any existing dated file (created in a prior
            # month) before defaulting to "now".
            if sess_root.exists():
                hits = list(sess_root.rglob(f"{thread_id}.jsonl"))
                if hits:
                    # A thread touched across months can have several dated
                    # files (/YYYY/MM/<id>.jsonl). Pick the LATEST: the path is
                    # zero-padded so max() is chronological. rglob() order is
                    # filesystem-arbitrary, so the old hits[0] could return a
                    # stale month for both reads and appends.
                    return max(hits)
            now = datetime.now(UTC)
            return sess_root / f"{now.year:04d}" / f"{now.month:02d}" / f"{thread_id}.jsonl"
        return flat

    def _per_thread_path(self, thread: dict[str, Any]) -> Path | None:
        """Compute the on-disk jsonl for this thread in per-agent mode.

        Returns None when this store isn't running in per-agent mode
        (caller should fall back to self._path instead).
        """
        if self._per_agent_base is None:
            return None
        thread_id = thread.get("thread_id")
        if not isinstance(thread_id, str) or not thread_id:
            return None
        team_path = self._team_path_for(thread)
        if team_path is not None:
            return team_path
        agent_path = self._agent_path_for(thread)
        if agent_path is not None:
            return agent_path
        # No agent bound yet — stash in misc. Future writes keep landing
        # here unless we add rename-on-agent-bind (we don't, to keep
        # file-path stability).
        return self._misc_path_for(thread_id)

    def _append_upsert(
        self,
        thread: dict[str, Any],
        state: dict[str, Any],
        *,
        revision: int | None = None,
    ) -> Path | None:
        record = {
            "op": "upsert",
            "thread_id": thread["thread_id"],
            "thread": thread,
            "state": _encoded_state_record(state),
            _STATE_FROM_THREAD_KEY: True,
        }
        if revision is not None:
            record["revision"] = revision
            record["operation_at"] = _utc_now_iso()
        self._cleanup_stale_paths_if_promoted(thread)
        target = self._append_record(thread, record)
        if self._index is not None and target is not None:
            entry = entry_from_thread(
                thread,
                file_path=self._index_file_for(target),
            )
            if entry is not None:
                self._index.upsert(entry)

        # Update the Echo Native session-search index.
        if self._search is not None:
            self._update_search_index(thread)
        return target

    def _cleanup_stale_paths_if_promoted(self, thread: dict[str, Any]) -> None:
        """If a thread just gained an agent tag and is about to be
        written under ``agents/<agent_id>/sessions/...``, remove any
        earlier stub file that was written to ``data/sessions/misc/``.

        Rationale: the very first ``ensure_thread()`` runs before any
        turn knows which agent will handle the thread, so it routes
        to misc. After the first turn's ``update_state()`` assigns an
        ``agent`` in metadata, later writes land under the per-agent
        path — leaving a lingering misc file for the same thread id
        that never receives more writes. Delete it so each thread has
        exactly one on-disk home.
        """
        if self._per_agent_base is None:
            return
        thread_id = thread.get("thread_id")
        if not isinstance(thread_id, str) or not thread_id:
            return
        target = self._per_thread_path(thread)
        candidates = [
            self._misc_path_for(thread_id),
            self._agent_path_for(thread),
        ]
        for candidate in candidates:
            if candidate is None or candidate == target or not candidate.exists():
                continue
            with contextlib.suppress(OSError):
                candidate.unlink()

    def _append_delete(
        self,
        thread_id: str,
        thread: dict[str, Any] | None = None,
        *,
        revision: int | None = None,
    ) -> Path | None:
        stub = thread or {"thread_id": thread_id, "metadata": {}}
        record: dict[str, Any] = {"op": "delete", "thread_id": thread_id}
        if revision is not None:
            record["revision"] = revision
            record["operation_at"] = _utc_now_iso()
        target = self._append_record(stub, record)
        if self._index is not None:
            self._index.delete(thread_id)
        if self._search is not None:
            self._search.delete_thread(thread_id)
        return target

    def _append_record(self, thread: dict[str, Any], record: dict[str, Any]) -> Path | None:
        target: Path | None
        target = self._per_thread_path(thread) if self._per_agent_base is not None else self._path
        if target is None:
            return None
        target.parent.mkdir(parents=True, exist_ok=True)
        # The ``session_meta`` header goes on the first line of a
        # brand-new file. The "is it new?" decision is made *under the
        # lock* in ``_append_locked`` (by testing for an empty file), so
        # two workers racing to create the same per-thread JSONL can't
        # both emit a header.
        header_line = json.dumps(self._session_meta(thread), ensure_ascii=False) + "\n"
        record_line = json.dumps(record, ensure_ascii=False) + "\n"
        self._append_locked(target, record_line, header_line=header_line)
        return target

    def _append_locked(
        self,
        target: Path,
        line: str,
        *,
        header_line: str | None = None,
    ) -> None:
        """Append ``line`` to ``target`` durably under a cross-process
        file lock, writing ``header_line`` first iff the file is still
        empty when the lock is taken.

        ``self._lock`` only serialises writers inside *this* Python
        process. Under ``uvicorn --workers N`` two processes appending
        the same per-thread ``<thread_id>.jsonl`` can interleave their
        ``write``/``flush`` cycles and lose or corrupt records — POSIX
        ``O_APPEND`` is atomic only for writes ≤ PIPE_BUF (~4 KB), and
        thread records routinely exceed that. Wrap the write in an
        OS-level ``flock`` (``msvcrt`` on Windows) plus an ``fsync``,
        mirroring ``JSONLJournal.write``. Falls back to a plain append
        when ``fcntl``/``msvcrt`` aren't importable (e.g. WASM build).
        """
        import os as _os

        with self._lock, target.open("a", encoding="utf-8") as handle:
            fd = handle.fileno()
            locked = False
            try:
                try:
                    if _os.name == "nt":
                        import msvcrt as _msvcrt

                        # ``msvcrt.locking`` locks 1 byte at the CURRENT
                        # file position, and mode "a" positions the fd
                        # at EOF-at-open — a size that differs per
                        # writer. Two writers opening at different sizes
                        # would each lock a different byte and never
                        # actually contend. Seek to a fixed offset (0)
                        # first so every writer locks the *same* byte;
                        # the append-position seek below then restores
                        # the correct write cursor.
                        handle.seek(0, 0)
                        _msvcrt.locking(  # type: ignore[attr-defined]
                            fd,
                            _msvcrt.LK_LOCK,  # type: ignore[attr-defined]
                            1,
                        )
                        locked = True
                    else:
                        import fcntl as _fcntl

                        _fcntl.flock(fd, _fcntl.LOCK_EX)
                        locked = True
                except (OSError, ImportError) as lock_exc:
                    locked = False
                    # Degrading to an unlocked append re-opens the very
                    # interleaving window this helper exists to close —
                    # say so, or field corruption is undiagnosable.
                    logger.warning(
                        "thread-store: file lock unavailable for %s (%s); "
                        "appending without cross-process lock",
                        target.name,
                        lock_exc,
                    )
                # Seek to end: another process may have extended the file
                # since our ``open("a")`` computed the cursor. ``tell()``
                # then reports the current size — zero means we are the
                # writer that gets to lay down the ``session_meta`` header.
                try:  # noqa: SIM105
                    handle.seek(0, 2)
                except OSError:  # best-effort · handle already at append position on most platforms
                    pass
                if header_line is not None and handle.tell() == 0:
                    handle.write(header_line)
                handle.write(line)
                handle.flush()
                try:  # noqa: SIM105
                    _os.fsync(fd)
                except OSError:  # best-effort · data already flushed to the OS buffer above
                    pass
            finally:
                if locked:
                    try:
                        if _os.name == "nt":
                            import msvcrt as _msvcrt

                            try:  # noqa: SIM105
                                handle.seek(0, 0)
                            except (
                                OSError
                            ):  # best-effort · unlock below still targets the intended byte
                                pass
                            _msvcrt.locking(  # type: ignore[attr-defined]
                                fd,
                                _msvcrt.LK_UNLCK,  # type: ignore[attr-defined]
                                1,
                            )
                        else:
                            import fcntl as _fcntl

                            _fcntl.flock(fd, _fcntl.LOCK_UN)
                    except (
                        OSError
                    ):  # best-effort · closing the handle below releases the OS lock anyway
                        pass

    def _session_meta(self, thread: dict[str, Any]) -> dict[str, Any]:
        """Build a ``session_meta`` header. The first line of each
        session file carries the metadata the journal indexer
        needs (id, origin, timestamps) before any user content
        appears."""
        metadata = thread.get("metadata") or {}
        return {
            "type": "session_meta",
            "payload": {
                "id": thread.get("thread_id", ""),
                "timestamp": thread.get("created_at", _utc_now_iso()),
                "originator": self._session_origin,
                "agent": metadata.get("agent"),
                "team_id": metadata.get("team_id"),
            },
        }

    # ─── load ────────────────────────────────────────────────

    def _load_from(
        self,
        path: Path,
        implied_agent: str | None = None,
        implied_team_id: str | None = None,
    ) -> None:
        """Read a single jsonl (legacy single-file OR one per-thread
        file) and fold its records into memory.

        ``implied_agent`` · when set and a record's thread has no
        explicit ``metadata.agent`` tag, tag it to this value. The
        caller (per-agent tree walker) passes the folder-derived
        agent id so legacy threads that predate the metadata tagging
        get retroactively attributed. This is the "migration" path
        for old history that was invisible under the agent filter.
        """

        # Scoped storage uses exactly one journal per thread. Its newest valid
        # operation fully determines the current state, while older checkpoints
        # remain available through ``get_history()``. Reading from the tail
        # avoids parsing every full snapshot during service startup.
        if self._per_agent_base is not None:
            for record in iter_jsonl_records_reverse(path):
                if record.get("thread_id") != path.stem:
                    continue
                if self._apply_loaded_record(
                    record,
                    implied_agent=implied_agent,
                    implied_team_id=implied_team_id,
                ):
                    return
            return

        # The legacy journal can interleave many thread ids, so it still needs
        # a forward fold. Stream it line by line to keep peak memory bounded.
        try:
            handle = path.open("r", encoding="utf-8")
        except OSError:
            return
        with handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    self._apply_loaded_record(
                        record,
                        implied_agent=implied_agent,
                        implied_team_id=implied_team_id,
                    )

    def _apply_loaded_record(
        self,
        record: dict[str, Any],
        *,
        implied_agent: str | None,
        implied_team_id: str | None,
    ) -> bool:
        """Fold one valid state operation and report whether it was applied."""

        if record.get("type") == "session_meta":
            return False
        thread_id = record.get("thread_id")
        if not isinstance(thread_id, str) or not thread_id:
            return False
        if record.get("op") == "delete":
            self._threads.pop(thread_id, None)
            self._history.pop(thread_id, None)
            return True
        thread = record.get("thread")
        if not isinstance(thread, dict):
            return False
        state = _decoded_state_record(record, thread)
        if state is None:
            return False
        # Retro-tag legacy threads that lived under a per-agent folder without
        # explicit metadata.agent. The folder name is the authoritative owner.
        if implied_agent and isinstance(thread.get("metadata"), dict):
            metadata = thread["metadata"]
            if not metadata.get("agent"):
                metadata["agent"] = implied_agent
        if implied_team_id and isinstance(thread.get("metadata"), dict):
            metadata = thread["metadata"]
            if not metadata.get("team_id"):
                metadata["team_id"] = implied_team_id
            if not metadata.get("mode"):
                metadata["mode"] = "team"
        self._threads[thread_id] = thread
        self._remember_state_locked(thread_id, state)
        return True

    def _load_from_per_agent_tree(self) -> None:
        """Scan team, agent, and misc session trees and load every thread."""
        assert self._per_agent_base is not None
        misc_dir = self._per_agent_base / "data" / "sessions" / "misc"
        if misc_dir.exists():
            for jsonl in self._canonical_session_files(misc_dir):
                self._load_from(jsonl)
        agents_root = self._per_agent_base / "agents"
        if agents_root.exists():
            for agent_dir in agents_root.iterdir():
                if not agent_dir.is_dir():
                    continue
                sess = agent_dir / "sessions"
                if not sess.exists():
                    continue
                implied = agent_dir.name
                for jsonl in self._canonical_session_files(sess):
                    self._load_from(jsonl, implied_agent=implied)
        teams_root = self._per_agent_base / "teams"
        if teams_root.exists():
            for team_dir in teams_root.iterdir():
                if not team_dir.is_dir():
                    continue
                sess = team_dir / "sessions"
                if not sess.exists():
                    continue
                implied_team_id = team_dir.name
                for jsonl in self._canonical_session_files(sess):
                    self._load_from(jsonl, implied_team_id=implied_team_id)

    def _repair_conflicting_agent_copies_locked(self) -> None:
        """Prefer the thread's original role when stale copies disagree.

        Older clients could send a different ``agent_name`` on a later turn,
        producing two files with the same thread id in different role folders.
        File traversal order then decided which conversation appeared after a
        restart.  The earliest ``created_at`` identifies the original role;
        move the fullest/latest conversation back there and remove the stale
        duplicate so future reads and sidebar filters are deterministic.
        """
        if self._per_agent_base is None:
            return
        agents_root = self._per_agent_base / "agents"
        if not agents_root.exists():
            return

        candidates: dict[str, list[tuple[str, Path, dict[str, Any]]]] = {}
        for agent_dir in agents_root.iterdir():
            if not agent_dir.is_dir():
                continue
            sessions = agent_dir / "sessions"
            if not sessions.exists():
                continue
            for path in self._canonical_session_files(sessions):
                thread = self._latest_thread_from_file(path)
                if thread is None:
                    continue
                candidates.setdefault(path.stem, []).append((agent_dir.name, path, thread))

        for thread_id, discovered_copies in candidates.items():
            if len(discovered_copies) < 2:
                continue
            with thread_mutation_lock(
                journal_path=self._path,
                per_agent_base=self._per_agent_base,
                thread_id=thread_id,
            ):
                if self._thread_delete_record_locked(thread_id) is not None:
                    self._threads.pop(thread_id, None)
                    self._history.pop(thread_id, None)
                    continue
                # The constructor may have blocked behind a live writer after
                # its initial scan. Never repair from that stale snapshot:
                # re-enumerate every discovered role file while holding the
                # same logical lock used by normal mutations.
                copies: list[tuple[str, Path, dict[str, Any]]] = []
                for agent, path, _thread in discovered_copies:
                    current = self._latest_thread_from_file(path)
                    if current is not None:
                        copies.append((agent, path, current))

                persisted = latest_persisted_thread(
                    journal_path=self._path,
                    per_agent_base=self._per_agent_base,
                    thread_id=thread_id,
                )
                if not persisted.found or persisted.thread is None:
                    continue
                if len(copies) < 2:
                    # Another worker already completed the repair or a normal
                    # mutation removed the stale copy while this constructor
                    # waited. Refresh this instance from the durable winner.
                    refreshed = _deepcopy(persisted.thread)
                    refreshed_state = self._make_state(refreshed)
                    self._threads[thread_id] = refreshed
                    self._history[thread_id] = [refreshed_state]
                    continue

                owner_agent, owner_path, _owner_thread = min(
                    copies,
                    key=lambda item: (
                        str(item[2].get("created_at") or "9999"),
                        str(item[2].get("updated_at") or "9999"),
                    ),
                )
                repaired = _deepcopy(persisted.thread)
                repaired_meta = repaired.setdefault("metadata", {})
                repaired_meta["agent"] = owner_agent
                for key in ("agent_name", "agent_id", "assistant_id"):
                    if key in repaired_meta:
                        repaired_meta[key] = owner_agent
                state = self._make_state(repaired)
                state["metadata"] = _deepcopy(repaired_meta)

                # Rebuild one canonical file under the original owner. The
                # revision keeps the repair ordered after the durable snapshot
                # it folded, so a later constructor cannot prefer an old copy.
                owner_path.parent.mkdir(parents=True, exist_ok=True)
                tmp_path = owner_path.with_suffix(owner_path.suffix + ".repair")
                tmp_path.write_text(
                    json.dumps(self._session_meta(repaired), ensure_ascii=False)
                    + "\n"
                    + json.dumps(
                        {
                            "op": "upsert",
                            "thread_id": thread_id,
                            "thread": repaired,
                            "state": state,
                            "revision": persisted.revision + 1,
                            "operation_at": _utc_now_iso(),
                        },
                        ensure_ascii=False,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                tmp_path.replace(owner_path)
                for _agent, path, _thread in copies:
                    if path == owner_path:
                        continue
                    with contextlib.suppress(OSError):
                        path.unlink()
                logger.warning(
                    "thread-store: repaired conflicting role copies for %s; owner=%s, removed=%d",
                    thread_id,
                    owner_agent,
                    len(copies) - 1,
                )
                self._threads[thread_id] = repaired
                self._history[thread_id] = [state]
                if self._index is not None:
                    entry = entry_from_thread(
                        repaired,
                        file_path=self._index_file_for(owner_path),
                    )
                    if entry is not None:
                        self._index.upsert(entry)

    @staticmethod
    def _latest_thread_from_file(path: Path) -> dict[str, Any] | None:
        for record in iter_jsonl_records_reverse(path):
            thread = record.get("thread") if isinstance(record, dict) else None
            if isinstance(thread, dict):
                return thread
        return None

    def _canonical_session_files(self, sess_root: Path) -> list[Path]:
        """Return one canonical jsonl per thread under a session root.

        Mirrors ``_resolve_thread_file`` for boot-time recovery: an existing
        flat file wins for flat→dated upgrades; otherwise the latest dated path
        wins. This keeps stale month shards from overwriting newer state during
        startup replay.
        """
        chosen: dict[str, Path] = {}
        for jsonl in sorted(sess_root.rglob("*.jsonl")):
            thread_id = jsonl.stem
            current = chosen.get(thread_id)
            if current is None:
                chosen[thread_id] = jsonl
                continue
            current_is_flat = current.parent == sess_root
            candidate_is_flat = jsonl.parent == sess_root
            if current_is_flat:
                continue
            if candidate_is_flat or jsonl > current:
                chosen[thread_id] = jsonl
        return sorted(chosen.values())

    # ─── Echo Native session search and export ───────────

    def _update_search_index(self, thread: dict[str, Any]) -> None:
        """Update the FTS5 search index for a thread."""
        if self._search is None:
            return

        thread_id = thread.get("thread_id")
        if not isinstance(thread_id, str):
            return

        values = thread.get("values", {})
        title = values.get("title", "Untitled")
        messages = values.get("messages", [])

        agent_id = self._agent_id_for(thread)
        team_id = self._team_id_for(thread)
        created_at = thread.get("created_at")
        updated_at = thread.get("updated_at")

        self._search.index_thread(
            thread_id=thread_id,
            title=str(title),
            messages=messages,
            agent_id=agent_id,
            team_id=team_id,
            created_at=created_at,
            updated_at=updated_at,
        )

    @property
    def search_enabled(self) -> bool:
        """Whether the Echo full-text session-search index is active.

        The router uses this to return 501 instead of silently empty results
        when the operator disabled search.
        """
        return self._search is not None

    @property
    def feedback_enabled(self) -> bool:
        """Whether the Echo message-feedback store is active."""
        return self._feedback is not None

    def search_threads(
        self,
        query: str,
        *,
        agent_id: str | None = None,
        team_id: str | None = None,
        after: str | None = None,
        before: str | None = None,
        limit: int = 50,
    ) -> list[SearchResult]:
        """Search Echo threads by content.

        Args:
            query: Search query (supports FTS5 syntax)
            agent_id: Filter by agent
            team_id: Filter by team
            after: ISO date, threads updated after this
            before: ISO date, threads updated before this
            limit: Max results (default 50)

        Returns:
            List of SearchResult, ordered by relevance
        """
        if self._search is None:
            return []

        results = self._search.search(
            query,
            agent_id=agent_id,
            team_id=team_id,
            after=after,
            before=before,
            limit=limit,
        )
        with self._lock:
            return [
                result
                for result in results
                if self._thread_delete_record_locked(result.thread_id) is None
            ]

    def export_thread_markdown(self, thread_id: str) -> str | None:
        """Export an Echo thread to Markdown.

        Args:
            thread_id: Thread identifier

        Returns:
            Markdown string with YAML frontmatter, or None if thread not found
        """
        with (
            self._lock,
            thread_mutation_lock(
                journal_path=self._path,
                per_agent_base=self._per_agent_base,
                thread_id=thread_id,
            ),
        ):
            self._assert_thread_writable_locked(thread_id)
            thread = self._threads.get(thread_id)
            if thread is None:
                return None

            values = thread.get("values", {})
            title = values.get("title", "Untitled")
            messages = values.get("messages", [])

            agent_id = self._agent_id_for(thread)
            team_id = self._team_id_for(thread)
            created_at = thread.get("created_at")
            updated_at = thread.get("updated_at")

            return export_thread_to_markdown(
                thread_id=thread_id,
                title=str(title),
                messages=messages,
                agent_id=agent_id,
                team_id=team_id,
                created_at=created_at,
                updated_at=updated_at,
            )

    def delete_thread(self, thread_id: str) -> None:
        """Delete a thread and clean up its session-search index rows.

        Removes from in-memory cache, session index, and search index.
        The on-disk JSONL file is left intact for audit/recovery.
        """
        with (
            self._lock,
            thread_mutation_lock(
                journal_path=self._path,
                per_agent_base=self._per_agent_base,
                thread_id=thread_id,
            ),
        ):
            self._assert_thread_writable_locked(thread_id)
            if thread_id in self._threads:
                del self._threads[thread_id]
            if thread_id in self._history:
                del self._history[thread_id]

            if self._index is not None:
                self._index.delete(thread_id)

            if self._search is not None:
                self._search.delete_thread(thread_id)

    # ─── Echo Native message feedback ──────────────

    def add_message_feedback(
        self,
        thread_id: str,
        message_index: int,
        feedback_type: FeedbackType,
        *,
        tags: list[str] | None = None,
        comment: str = "",
        user_id: str | None = None,
    ) -> MessageFeedback | None:
        """Add Echo evaluation feedback for a message.

        Args:
            thread_id: Thread identifier
            message_index: Zero-based index of the message
            feedback_type: "thumbs_up" or "thumbs_down"
            tags: Optional list of tags (e.g., ["helpful"], ["inaccurate", "too_verbose"])
            comment: Optional free-form comment
            user_id: Optional user identifier

        Returns:
            The recorded MessageFeedback, or None if feedback is disabled
        """
        if self._feedback is None:
            return None

        return self._feedback.add_feedback(
            thread_id=thread_id,
            message_index=message_index,
            feedback_type=feedback_type,
            tags=tags,
            comment=comment,
            user_id=user_id,
        )

    def get_message_feedback(
        self, thread_id: str, message_index: int | None = None
    ) -> list[MessageFeedback]:
        """Get feedback for an Echo thread or specific message.

        Args:
            thread_id: Thread identifier
            message_index: Optional message index to filter by

        Returns:
            List of MessageFeedback, in chronological order
        """
        if self._feedback is None:
            return []

        if message_index is not None:
            return self._feedback.get_message_feedback(thread_id, message_index)

        return self._feedback.get_feedback(thread_id)

    def get_feedback_stats(self, thread_id: str) -> dict[str, Any]:
        """Get feedback statistics for an Echo thread.

        Returns:
            Dictionary with counts:
            - total: Total feedback count
            - thumbs_up: Positive feedback count
            - thumbs_down: Negative feedback count
            - tags: Dict of tag -> count
            - messages_with_feedback: List of message indices with any feedback
        """
        if self._feedback is None:
            return {
                "total": 0,
                "thumbs_up": 0,
                "thumbs_down": 0,
                "tags": {},
                "messages_with_feedback": [],
            }

        return self._feedback.get_stats(thread_id)

    def export_rlhf_dataset(
        self,
        output_path: str | Path,
        *,
        min_feedback_count: int = 1,
        feedback_type_filter: FeedbackType | None = None,
    ) -> int:
        """Export all feedback as an evaluation/training dataset.

        Args:
            output_path: Path to write JSONL dataset
            min_feedback_count: Only include threads with at least this many feedbacks
            feedback_type_filter: Only include specific feedback type (optional)

        Returns:
            Number of feedback entries exported
        """
        if self._feedback is None:
            return 0

        return self._feedback.export_rlhf_dataset(
            output_path,
            min_feedback_count=min_feedback_count,
            feedback_type_filter=feedback_type_filter,
        )
