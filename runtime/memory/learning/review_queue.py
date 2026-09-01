"""Durable human-review queue for TaskRun self-improvement candidates."""

from __future__ import annotations

import hashlib
import json
import threading
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from runtime.platform.io import atomic_write_json, read_json_with_backup
from runtime.platform.io.atomic import _cross_process_lock
from runtime.safety.auth.scope import TenantScope, row_visible

_SCHEMA = "echo.review_queue.v1"
_VALID_STATUSES = {"pending", "promoted", "rejected", "archived"}
_DECISIONS = {"promoted", "rejected", "archived"}
_PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2}
_EXECUTION_POLICY_KEYS = (
    "schema",
    "sandbox_requested",
    "workspace",
    "cwd",
    "backend",
    "hard",
    "allow_network",
    "env_mode",
    "process_group",
    "process_tree_kill",
    "timeout_s",
)


class ReviewQueue:
    """Deduplicating review queue before lessons become durable behavior."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def add_from_task_run_review(
        self,
        review: dict[str, Any],
        *,
        now: datetime | None = None,
        scope: TenantScope | None = None,
    ) -> dict[str, Any]:
        with self._write_lock():
            payload = self._read()
            items = list(payload.get("items") or [])
            now_text = _iso(now)
            source = _source_from_review(review)
            created = 0
            updated = 0
            touched: list[dict[str, Any]] = []

            for candidate in _items_from_review(review, now_text, scope=scope):
                existing = _find_item(items, candidate["id"], scope=scope)
                if existing is None:
                    items.append(candidate)
                    touched.append(candidate)
                    created += 1
                    continue
                _merge_existing_item(existing, candidate, now_text)
                touched.append(existing)
                updated += 1

            payload["items"] = sorted(items, key=_item_sort_key)
            payload["lastUpdated"] = now_text
            self._write(payload)
        return {
            "schema": _SCHEMA,
            "source": source,
            "created": created,
            "updated": updated,
            "items": touched,
            "total": len(payload["items"]),
        }

    def upsert_item(
        self,
        *,
        source: str,
        source_kind: str,
        candidate_kind: str,
        priority: str,
        target_bucket: str,
        title: str,
        text: str,
        metadata: dict[str, Any] | None = None,
        source_task_ids: list[str] | None = None,
        thread_ids: list[str] | None = None,
        turn_ids: list[str] | None = None,
        agent_ids: list[str] | None = None,
        tags: list[str] | None = None,
        now: datetime | None = None,
        scope: TenantScope | None = None,
    ) -> dict[str, Any]:
        with self._write_lock():
            payload = self._read()
            items = list(payload.get("items") or [])
            now_text = _iso(now)
            clean_title = _clean_text(title, limit=180)
            clean_text = _clean_text(text, limit=1200)
            if not clean_title or not clean_text:
                raise ValueError("title and text are required")
            candidate = _new_item(
                now_text=now_text,
                source_task_id="",
                thread_id="",
                turn_id="",
                agent_id="",
                source_kind=_clean_text(source_kind, limit=80) or "policy_review",
                candidate_kind=_clean_text(candidate_kind, limit=80) or "policy_review",
                priority=_priority(priority),
                target_bucket=_clean_text(target_bucket, limit=80) or "policy_review",
                title=clean_title,
                text=clean_text,
                metadata=metadata if isinstance(metadata, dict) else {},
                scope=scope,
            )
            candidate["source"] = _clean_text(source, limit=80) or "manual"
            candidate["source_task_ids"] = _clean_unique_list(source_task_ids, limit=80)
            candidate["thread_ids"] = _clean_unique_list(thread_ids, limit=80)
            candidate["turn_ids"] = _clean_unique_list(turn_ids, limit=80)
            candidate["agent_ids"] = _clean_unique_list(agent_ids, limit=80)
            candidate["tags"] = _merge_unique(candidate["tags"], tags or [])

            existing = _find_item(items, candidate["id"], scope=scope)
            created = 0
            updated = 0
            if existing is None:
                items.append(candidate)
                item = candidate
                created = 1
            else:
                _merge_existing_item(existing, candidate, now_text)
                item = existing
                updated = 1

            payload["items"] = sorted(items, key=_item_sort_key)
            payload["lastUpdated"] = now_text
            self._write(payload)
        return {
            "schema": _SCHEMA,
            "source": {"source": candidate["source"]},
            "created": created,
            "updated": updated,
            "items": [item],
            "total": len(payload["items"]),
        }

    def items(
        self,
        *,
        status: str | None = None,
        target_bucket: str | None = None,
        priority: str | None = None,
        source_task_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
        scope: TenantScope | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            rows = [row for row in self._read().get("items") or [] if row_visible(row, scope)]
        if status:
            rows = [row for row in rows if str(row.get("status") or "") == status]
        if target_bucket:
            rows = [row for row in rows if str(row.get("target_bucket") or "") == target_bucket]
        if priority:
            rows = [row for row in rows if str(row.get("priority") or "") == priority]
        if source_task_id:
            wanted = _clean_text(source_task_id, limit=120)
            rows = [row for row in rows if wanted in (row.get("source_task_ids") or [])]
        rows = sorted(rows, key=_item_sort_key)
        total = len(rows)
        return {
            "schema": _SCHEMA,
            "items": rows[offset : offset + limit],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def decide(
        self,
        item_id: str,
        *,
        action: str,
        reason: str = "",
        promoted_to: str | None = None,
        now: datetime | None = None,
        scope: TenantScope | None = None,
    ) -> dict[str, Any]:
        normalized_id = _clean_text(item_id, limit=80)
        decision = _clean_text(action, limit=40).lower()
        if decision not in _DECISIONS:
            raise ValueError("action must be one of: promoted, rejected, archived")

        with self._write_lock():
            payload = self._read()
            rows = list(payload.get("items") or [])
            item = _find_item(rows, normalized_id, scope=scope)
            if item is None:
                raise KeyError(normalized_id)

            now_text = _iso(now)
            item["status"] = decision
            item["updated_at"] = now_text
            item["decided_at"] = now_text
            item["decision_reason"] = _clean_text(reason, limit=1200)
            item["promoted_to"] = (
                _clean_text(promoted_to, limit=80)
                if decision == "promoted" and promoted_to
                else item.get("target_bucket", "none")
                if decision == "promoted"
                else "none"
            )

            payload["items"] = sorted(rows, key=_item_sort_key)
            payload["lastUpdated"] = now_text
            self._write(payload)
        return {"schema": _SCHEMA, "item": item}

    def update_metadata(
        self,
        item_id: str,
        *,
        metadata_patch: dict[str, Any],
        tags: list[str] | None = None,
        now: datetime | None = None,
        scope: TenantScope | None = None,
    ) -> dict[str, Any]:
        normalized_id = _clean_text(item_id, limit=80)
        with self._write_lock():
            payload = self._read()
            rows = list(payload.get("items") or [])
            item = _find_item(rows, normalized_id, scope=scope)
            if item is None:
                raise KeyError(normalized_id)
            if not isinstance(metadata_patch, dict):
                raise ValueError("metadata_patch must be an object")

            now_text = _iso(now)
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            metadata.update(metadata_patch)
            item["metadata"] = metadata
            item["tags"] = _merge_unique(item.get("tags"), tags or [])
            item["updated_at"] = now_text
            item["last_seen_at"] = now_text
            payload["items"] = sorted(rows, key=_item_sort_key)
            payload["lastUpdated"] = now_text
            self._write(payload)
        return {"schema": _SCHEMA, "item": item}

    def summary(self, *, scope: TenantScope | None = None) -> dict[str, Any]:
        with self._lock:
            rows = [row for row in self._read().get("items") or [] if row_visible(row, scope)]
        by_status = Counter(str(row.get("status") or "pending") for row in rows)
        by_priority = Counter(str(row.get("priority") or "P2") for row in rows)
        by_bucket = Counter(str(row.get("target_bucket") or "experience") for row in rows)
        return {
            "schema": _SCHEMA,
            "total": len(rows),
            "pending_count": by_status.get("pending", 0),
            "by_status": dict(sorted(by_status.items())),
            "by_priority": dict(sorted(by_priority.items())),
            "by_target_bucket": dict(sorted(by_bucket.items())),
            "next_actions": _next_actions(rows),
        }

    def _read(self) -> dict[str, Any]:
        raw = read_json_with_backup(self.path, default=None)
        if not isinstance(raw, dict):
            return _empty_payload()
        return _normalize_payload(raw)

    def _write(self, payload: dict[str, Any]) -> None:
        atomic_write_json(self.path, _normalize_payload(payload))

    def _write_lock(self) -> Any:
        return _StoreWriteLock(self._lock, self.path.parent / f"{self.path.name}.rw")


class _StoreWriteLock:
    def __init__(self, thread_lock: threading.RLock, target: Path) -> None:
        self._thread_lock = thread_lock
        self._target = target
        self._process_lock: Any = None

    def __enter__(self) -> None:
        self._thread_lock.__enter__()
        self._process_lock = _cross_process_lock(self._target)
        self._process_lock.__enter__()

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        try:
            if self._process_lock is not None:
                self._process_lock.__exit__(exc_type, exc, tb)
        finally:
            self._thread_lock.__exit__(exc_type, exc, tb)


def _empty_payload() -> dict[str, Any]:
    return {
        "schema": _SCHEMA,
        "version": 1,
        "lastUpdated": "",
        "items": [],
    }


def _normalize_payload(raw: dict[str, Any]) -> dict[str, Any]:
    payload = _empty_payload()
    rows: list[dict[str, Any]] = []
    for item in raw.get("items") or []:
        if not isinstance(item, dict):
            continue
        title = _clean_text(item.get("title"), limit=180)
        text = _clean_text(item.get("text"), limit=1200)
        if not title or not text:
            continue
        status = _clean_text(item.get("status"), limit=40) or "pending"
        if status not in _VALID_STATUSES:
            status = "pending"
        source_kind = _clean_text(item.get("source_kind"), limit=80) or "learning_candidate"
        candidate_kind = _clean_text(item.get("candidate_kind"), limit=80) or source_kind
        target_bucket = _clean_text(item.get("target_bucket"), limit=80) or "experience"
        normalized = {
            "id": _clean_text(item.get("id"), limit=80)
            or _item_id(
                source_kind=source_kind,
                candidate_kind=candidate_kind,
                target_bucket=target_bucket,
                title=title,
                text=text,
            ),
            "created_at": _clean_text(item.get("created_at"), limit=80),
            "updated_at": _clean_text(item.get("updated_at"), limit=80),
            "decided_at": _clean_text(item.get("decided_at"), limit=80),
            "source": _clean_text(item.get("source"), limit=80) or "task_run_review",
            "source_kind": source_kind,
            "candidate_kind": candidate_kind,
            "priority": _priority(item.get("priority")),
            "target_bucket": target_bucket,
            "title": title,
            "text": text,
            "status": status,
            "decision_reason": _clean_text(item.get("decision_reason"), limit=1200),
            "promoted_to": _clean_text(item.get("promoted_to"), limit=80) or "none",
            "occurrences": max(1, int(item.get("occurrences") or 1)),
            "last_seen_at": _clean_text(item.get("last_seen_at"), limit=80),
            "source_task_ids": _clean_unique_list(item.get("source_task_ids"), limit=80),
            "thread_ids": _clean_unique_list(item.get("thread_ids"), limit=80),
            "turn_ids": _clean_unique_list(item.get("turn_ids"), limit=80),
            "agent_ids": _clean_unique_list(item.get("agent_ids"), limit=80),
            "tags": _clean_unique_list(item.get("tags"), limit=40),
            "metadata": item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
            "source_hash": _clean_text(item.get("source_hash"), limit=80),
            "tenant_id": _clean_text(item.get("tenant_id"), limit=160),
            "owner_actor_id": _clean_text(item.get("owner_actor_id"), limit=160),
        }
        rows.append(normalized)
    payload.update(
        {
            "schema": _SCHEMA,
            "version": 1,
            "lastUpdated": _clean_text(raw.get("lastUpdated"), limit=80),
            "items": sorted(rows, key=_item_sort_key),
        }
    )
    return payload


def _items_from_review(
    review: dict[str, Any],
    now_text: str,
    *,
    scope: TenantScope | None = None,
) -> list[dict[str, Any]]:
    source_task_id = _clean_text(review.get("task_id"), limit=120)
    thread_id = _clean_text(review.get("thread_id"), limit=120)
    turn_id = _clean_text(review.get("turn_id"), limit=120)
    agent_id = _clean_text(review.get("agent_id"), limit=120)
    evidence = _review_evidence_metadata(review)
    rows: list[dict[str, Any]] = []
    for item in review.get("learning_candidates") or []:
        if not isinstance(item, dict):
            continue
        title = _clean_text(item.get("title"), limit=180)
        text = _clean_text(item.get("text"), limit=1200)
        if not title or not text:
            continue
        candidate_kind = _clean_text(item.get("kind"), limit=80) or "learning_candidate"
        target_bucket = _clean_text(item.get("memory_bucket"), limit=80) or "experience"
        rows.append(
            _new_item(
                now_text=now_text,
                source_task_id=source_task_id,
                thread_id=thread_id,
                turn_id=turn_id,
                agent_id=agent_id,
                source_kind="learning_candidate",
                candidate_kind=candidate_kind,
                priority=_priority(item.get("priority")),
                target_bucket=target_bucket,
                title=title,
                text=text,
                metadata={**evidence, "candidate": item},
                scope=scope,
            )
        )
    for item in review.get("backlog_candidates") or []:
        if not isinstance(item, dict):
            continue
        title = _clean_text(item.get("experiment"), limit=180)
        text = _clean_text(item.get("hypothesis"), limit=1200)
        if not title or not text:
            continue
        rows.append(
            _new_item(
                now_text=now_text,
                source_task_id=source_task_id,
                thread_id=thread_id,
                turn_id=turn_id,
                agent_id=agent_id,
                source_kind="backlog_candidate",
                candidate_kind="backlog_candidate",
                priority=_priority(item.get("priority")),
                target_bucket="experiment_backlog",
                title=title,
                text=text,
                metadata={
                    **evidence,
                    "candidate": item,
                    "minimal_implementation": _clean_text(
                        item.get("minimal_implementation"),
                        limit=1200,
                    ),
                    "validation_metric": _clean_text(item.get("validation_metric"), limit=600),
                },
                scope=scope,
            )
        )
    return rows


def _review_evidence_metadata(review: dict[str, Any]) -> dict[str, Any]:
    replay = review.get("replay") if isinstance(review.get("replay"), dict) else {}
    resume = review.get("resume") if isinstance(review.get("resume"), dict) else {}
    latest = (
        resume.get("latest_checkpoint") if isinstance(resume.get("latest_checkpoint"), dict) else {}
    )
    integrity = latest.get("integrity") if isinstance(latest.get("integrity"), dict) else {}
    return {
        "review_status": review.get("status"),
        "replay": {
            "schema": replay.get("schema"),
            "case_id": replay.get("case_id"),
            "fingerprint": replay.get("fingerprint"),
            "replayable": bool(replay.get("replayable")),
            "step_count": int(replay.get("step_count") or 0),
        },
        "resume": {
            "available": bool(resume.get("available")),
            "source": resume.get("source"),
            "latest_checkpoint_id": latest.get("id"),
            "checkpoint_type": latest.get("type"),
            "resume_safe": bool(integrity.get("resume_safe")),
            "continue_from_iteration": int(integrity.get("continue_from_iteration") or 0),
        },
        "execution_policies": _execution_policies_from_review(review),
    }


def _execution_policy_summary(policy: Any) -> dict[str, Any]:
    if not isinstance(policy, dict) or not policy:
        return {}
    return {key: policy[key] for key in _EXECUTION_POLICY_KEYS if key in policy}


def _append_execution_policies(
    out: list[dict[str, Any]],
    seen: set[str],
    value: Any,
) -> None:
    if not isinstance(value, list):
        return
    for policy in value:
        summary = _execution_policy_summary(policy)
        if not summary:
            continue
        marker = json.dumps(summary, ensure_ascii=False, sort_keys=True)
        if marker in seen:
            continue
        seen.add(marker)
        out.append(summary)


def _merge_execution_policies(*values: Any) -> list[dict[str, Any]]:
    policies: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values:
        _append_execution_policies(policies, seen, value)
    return policies


def _execution_policies_from_review(review: dict[str, Any]) -> list[dict[str, Any]]:
    policies: list[dict[str, Any]] = []
    seen: set[str] = set()
    replay = review.get("replay") if isinstance(review.get("replay"), dict) else {}
    for step in replay.get("steps") or []:
        if isinstance(step, dict):
            _append_execution_policies(policies, seen, step.get("execution_policies"))

    for finding in review.get("findings") or []:
        if not isinstance(finding, dict):
            continue
        evidence = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
        _append_execution_policies(policies, seen, evidence.get("execution_policies"))

    resume = review.get("resume") if isinstance(review.get("resume"), dict) else {}
    latest = (
        resume.get("latest_checkpoint") if isinstance(resume.get("latest_checkpoint"), dict) else {}
    )
    state = latest.get("state") if isinstance(latest.get("state"), dict) else {}
    last_verifier = (
        state.get("last_verifier") if isinstance(state.get("last_verifier"), dict) else {}
    )
    _append_execution_policies(policies, seen, last_verifier.get("execution_policies"))
    for item in state.get("recent_tool_calls") or []:
        if isinstance(item, dict):
            _append_execution_policies(policies, seen, item.get("execution_policies"))
    return policies


def _new_item(
    *,
    now_text: str,
    source_task_id: str,
    thread_id: str,
    turn_id: str,
    agent_id: str,
    source_kind: str,
    candidate_kind: str,
    priority: str,
    target_bucket: str,
    title: str,
    text: str,
    metadata: dict[str, Any],
    scope: TenantScope | None = None,
) -> dict[str, Any]:
    item_id = _item_id(
        source_kind=source_kind,
        candidate_kind=candidate_kind,
        target_bucket=target_bucket,
        title=title,
        text=text,
    )
    return {
        "id": item_id,
        "created_at": now_text,
        "updated_at": now_text,
        "tenant_id": scope.tenant_id if scope is not None else "",
        "owner_actor_id": scope.actor_id if scope is not None else "",
        "decided_at": "",
        "source": "task_run_review",
        "source_kind": source_kind,
        "candidate_kind": candidate_kind,
        "priority": priority,
        "target_bucket": target_bucket,
        "title": title,
        "text": text,
        "status": "pending",
        "decision_reason": "",
        "promoted_to": "none",
        "occurrences": 1,
        "last_seen_at": now_text,
        "source_task_ids": [source_task_id] if source_task_id else [],
        "thread_ids": [thread_id] if thread_id else [],
        "turn_ids": [turn_id] if turn_id else [],
        "agent_ids": [agent_id] if agent_id else [],
        "tags": _tags_for(source_kind, candidate_kind, priority, target_bucket),
        "metadata": metadata,
        "source_hash": _source_hash(
            source_kind=source_kind,
            candidate_kind=candidate_kind,
            target_bucket=target_bucket,
            title=title,
            text=text,
        ),
    }


def _merge_existing_item(
    existing: dict[str, Any],
    candidate: dict[str, Any],
    now_text: str,
) -> None:
    existing["updated_at"] = now_text
    existing["last_seen_at"] = now_text
    existing["occurrences"] = int(existing.get("occurrences") or 1) + 1
    existing["priority"] = _higher_priority(existing.get("priority"), candidate.get("priority"))
    for key in ("source_task_ids", "thread_ids", "turn_ids", "agent_ids", "tags"):
        existing[key] = _merge_unique(existing.get(key), candidate.get(key))
    metadata = existing.get("metadata") if isinstance(existing.get("metadata"), dict) else {}
    candidate_metadata = (
        candidate.get("metadata") if isinstance(candidate.get("metadata"), dict) else {}
    )
    metadata["execution_policies"] = _merge_execution_policies(
        metadata.get("execution_policies"),
        candidate_metadata.get("execution_policies"),
    )
    metadata["last_candidate"] = candidate_metadata.get("candidate")
    existing["metadata"] = metadata


def _find_item(
    items: list[dict[str, Any]],
    item_id: str,
    *,
    scope: TenantScope | None = None,
) -> dict[str, Any] | None:
    for item in items:
        if str(item.get("id") or "") == item_id and row_visible(item, scope):
            return item
    return None


def _item_id(
    *,
    source_kind: Any,
    candidate_kind: Any,
    target_bucket: Any,
    title: str,
    text: str,
) -> str:
    digest = _source_hash(
        source_kind=source_kind,
        candidate_kind=candidate_kind,
        target_bucket=target_bucket,
        title=title,
        text=text,
    )
    return f"rq_{digest}"


def _source_hash(
    *,
    source_kind: Any,
    candidate_kind: Any,
    target_bucket: Any,
    title: str,
    text: str,
) -> str:
    key = "|".join(
        [
            _clean_text(source_kind, limit=80).casefold(),
            _clean_text(candidate_kind, limit=80).casefold(),
            _clean_text(target_bucket, limit=80).casefold(),
            title.casefold(),
            text.casefold(),
        ]
    )
    return hashlib.blake2b(key.encode("utf-8"), digest_size=10).hexdigest()


def _source_from_review(review: dict[str, Any]) -> dict[str, str]:
    return {
        "task_id": _clean_text(review.get("task_id"), limit=120),
        "thread_id": _clean_text(review.get("thread_id"), limit=120),
        "turn_id": _clean_text(review.get("turn_id"), limit=120),
        "agent_id": _clean_text(review.get("agent_id"), limit=120),
    }


def _item_sort_key(row: dict[str, Any]) -> tuple[int, str, str, str]:
    return (
        _PRIORITY_RANK.get(str(row.get("priority") or "P2"), 2),
        str(row.get("status") or "pending"),
        str(row.get("last_seen_at") or row.get("updated_at") or ""),
        str(row.get("id") or ""),
    )


def _next_actions(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    pending = [row for row in rows if str(row.get("status") or "") == "pending"]
    for row in sorted(pending, key=_item_sort_key)[:8]:
        target = str(row.get("target_bucket") or "experience")
        title = str(row.get("title") or "")
        if target == "experiment_backlog":
            action = f"Promote or reject experiment: {title}"
        else:
            action = f"Promote, reject, or archive learning: {title}"
        actions.append(
            {
                "priority": _priority(row.get("priority")),
                "item_id": str(row.get("id") or ""),
                "target_bucket": target,
                "action": action,
            }
        )
    return actions


def _iso(value: datetime | None = None) -> str:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(UTC).isoformat()


def _clean_text(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text[:limit].rstrip()


def _clean_unique_list(value: Any, *, limit: int) -> list[str]:
    return _merge_unique([], value)[:limit]


def _merge_unique(left: Any, right: Any) -> list[str]:
    out: list[str] = []
    for collection in (left, right):
        if not isinstance(collection, list):
            continue
        for item in collection:
            text = _clean_text(item, limit=160)
            if text and text not in out:
                out.append(text)
    return out


def _priority(value: Any) -> str:
    raw = str(value or "P2").upper()
    return raw if raw in _PRIORITY_RANK else "P2"


def _higher_priority(left: Any, right: Any) -> str:
    left_p = _priority(left)
    right_p = _priority(right)
    return left_p if _PRIORITY_RANK[left_p] <= _PRIORITY_RANK[right_p] else right_p


def _tags_for(
    source_kind: str,
    candidate_kind: str,
    priority: str,
    target_bucket: str,
) -> list[str]:
    tags = [source_kind, candidate_kind, priority, target_bucket]
    return [tag for tag in tags if tag]


__all__ = ["ReviewQueue"]
