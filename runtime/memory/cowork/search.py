"""Group-level search over the cowork substrate (replayable).

Searches the three thread-scoped stores a cowork group is made of:

  - the shared **blackboard** — key + value + writer
  - async **coworker tasks** — assignee / prompt / result
  - the membership/mode **event log** — actor / action / target / mode

These are exactly the event-sourced log + the surfaces folded from it, so the
search is "replayable": ``until_seq`` bounds the membership-event scan to a past
point, matching ``GroupStore.state(thread_id, until_seq=...)``. Blackboard and
tasks are current-state (the board isn't seq-versioned), which the result marks.

Per-thread data is small, so this is a plain ranked in-memory scan — no FTS
engine, no extra storage. Matching is case-insensitive substring per
whitespace-split term, which handles CJK naturally (no tokenizer needed).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ALL_KINDS = ("blackboard", "task", "event", "room_message", "room_task")


@dataclass
class SearchHit:
    """One ranked match, uniform across the three searched surfaces."""

    kind: str  # blackboard | task | event | room_message | room_task
    title: str
    snippet: str
    score: float
    actor: str = ""  # writer (board) / assignee (task) / actor (event)
    ts: str | None = None
    ref: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "title": self.title,
            "snippet": self.snippet,
            "score": round(self.score, 4),
            "actor": self.actor,
            "ts": self.ts,
            "ref": self.ref,
        }


def _terms(query: str) -> list[str]:
    """Lowercased, de-duplicated search terms. Whitespace splits multi-word
    queries; a whitespace-free query stays whole (covers CJK like ``营养赛道``)."""
    q = (query or "").strip().lower()
    if not q:
        return []
    parts = [p for p in q.split() if p]
    if len(parts) <= 1:
        return [q]
    # Keep the individual words AND the full phrase so an exact phrase ranks
    # above scattered word hits.
    seen: dict[str, None] = {}
    for term in [*parts, q]:
        seen.setdefault(term, None)
    return list(seen)


def _field_score(text: str, terms: list[str], weight: float) -> float:
    """Sum of ``weight`` for each term that appears in ``text``."""
    if not text:
        return 0.0
    low = text.lower()
    return sum(weight for term in terms if term in low)


def _snippet(text: str, terms: list[str], width: int = 140) -> str:
    """A window of ``text`` centred on the first matching term."""
    if not text:
        return ""
    clean = " ".join(text.split())
    low = clean.lower()
    pos = -1
    for term in terms:
        idx = low.find(term)
        if idx != -1 and (pos == -1 or idx < pos):
            pos = idx
    if pos <= 0 or len(clean) <= width:
        return clean[:width] + ("…" if len(clean) > width else "")
    start = max(0, pos - width // 3)
    end = min(len(clean), start + width)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(clean) else ""
    return f"{prefix}{clean[start:end]}{suffix}"


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    import json

    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def search_group(
    store: Any,
    thread_id: str,
    query: str,
    *,
    limit: int = 20,
    kinds: tuple[str, ...] | list[str] | None = None,
    until_seq: int | None = None,
    async_store: Any = None,
    room_message_store: Any = None,
    room_task_provider: Any = None,
) -> list[SearchHit]:
    """Ranked matches for ``query`` across the session's surfaces.

    ``kinds`` restricts which surfaces are searched (default: all). ``until_seq``
    bounds the event scan to ``seq <= until_seq`` (replay). ``async_store`` is
    reused if given, else built from ``store.base_dir``. When the thread has a
    linked room, its transcript (``room_message_store``) and team tasks
    (``room_task_provider`` — room_id → list of task dicts) are searched too — so
    search is session-wide across all three sources, not just the cowork thread.
    """
    terms = _terms(query)
    if not terms or not thread_id:
        return []
    wanted = tuple(kinds) if kinds else ALL_KINDS

    hits: list[SearchHit] = []

    if "blackboard" in wanted:
        hits.extend(_search_blackboard(store, thread_id, terms))
    if "task" in wanted:
        hits.extend(_search_tasks(store, thread_id, terms, async_store))
    if "event" in wanted:
        hits.extend(_search_events(store, thread_id, terms, until_seq))
    if "room_message" in wanted and room_message_store is not None:
        hits.extend(_search_room_messages(store, thread_id, terms, room_message_store))
    if "room_task" in wanted and room_task_provider is not None:
        hits.extend(_search_room_tasks(store, thread_id, terms, room_task_provider))

    # Highest score first; break ties by recency (events/tasks carry ts;
    # blackboard hits have no ts and sort last within a tie, which is fine).
    hits.sort(key=lambda h: (h.score, h.ts or ""), reverse=True)
    return hits[: max(0, limit)]


def _search_blackboard(store: Any, thread_id: str, terms: list[str]) -> list[SearchHit]:
    snapshot = store.blackboard_snapshot(thread_id)
    if not snapshot:
        return []
    writers_by_key: dict[str, list[str]] = {}
    try:
        board = store.blackboard(thread_id)
        audit = getattr(board, "audit", None)
        if callable(audit):
            writers_by_key = audit().get("writers_by_key", {}) or {}
    except Exception:  # noqa: BLE001 — writer attribution is best-effort
        writers_by_key = {}

    out: list[SearchHit] = []
    for key, value in snapshot.items():
        value_text = _as_text(value)
        score = _field_score(key, terms, 3.0) + _field_score(value_text, terms, 1.0)
        if score <= 0:
            continue
        writer = ", ".join(writers_by_key.get(key, []) or [])
        score += _field_score(writer, terms, 1.0)
        out.append(
            SearchHit(
                kind="blackboard",
                title=key,
                snippet=_snippet(value_text or key, terms),
                score=score,
                actor=writer,
                ref={"key": key, "state": "current"},
            )
        )
    return out


def _search_room_messages(
    store: Any, thread_id: str, terms: list[str], room_message_store: Any
) -> list[SearchHit]:
    room_id = getattr(store.state(thread_id), "room_id", None)
    if not room_id:
        return []
    # Query each term independently then union by seq — so a message containing
    # "nutrition" and "plan" in any order is found even when "nutrition plan"
    # doesn't appear verbatim.  _field_score then ranks across all matched terms.
    seen: dict[object, dict] = {}
    for term in terms:
        try:
            for m in room_message_store.search(room_id, term, limit=50):
                s = m.get("seq")
                if s is not None and s not in seen:
                    seen[s] = m
        except Exception:  # noqa: BLE001 — linked-room search degrades to other surfaces
            return []
    out: list[SearchHit] = []
    for m in seen.values():
        text = _as_text(m.get("text"))
        metadata = m.get("metadata") if isinstance(m.get("metadata"), dict) else {}
        metadata_text = _as_text(metadata)
        score = _field_score(text, terms, 1.5) + _field_score(metadata_text, terms, 1.0)
        if score <= 0:
            continue
        author = m.get("display_name") or m.get("participant_id") or ""
        card = metadata.get("system_card") if isinstance(metadata.get("system_card"), dict) else {}
        title = str(card.get("title") or author)
        snippet_source = text
        if not any(term in text.lower() for term in terms):
            snippet_source = str(card.get("summary") or card.get("title") or metadata_text)
        out.append(
            SearchHit(
                kind="room_message",
                title=title,
                snippet=_snippet(snippet_source, terms),
                score=score,
                actor=author,
                ts=m.get("ts"),
                ref={
                    "room_id": room_id,
                    "seq": m.get("seq"),
                    **(
                        {"entity_refs": metadata.get("entity_refs")}
                        if metadata.get("entity_refs")
                        else {}
                    ),
                },
            )
        )
    return out


def _search_room_tasks(
    store: Any, thread_id: str, terms: list[str], room_task_provider: Any
) -> list[SearchHit]:
    """Search the linked room's team tasks (title / description / status /
    assignees / SOP) — the heavyweight room-scoped work units, distinct from the
    cowork async tasks. Assignee matching mirrors :func:`_search_tasks` so an
    agent/participant name surfaces the room tasks routed to them."""
    room_id = getattr(store.state(thread_id), "room_id", None)
    if not room_id:
        return []
    try:
        rows = room_task_provider(room_id) or []
    except Exception:  # noqa: BLE001 — linked-room tasks degrade to other surfaces
        return []
    out: list[SearchHit] = []
    for task in rows:
        data = task if isinstance(task, dict) else {}
        title = _as_text(data.get("title"))
        description = _as_text(data.get("description"))
        status = _as_text(data.get("status"))
        assignees = " ".join(
            _as_text(a.get("ref")) for a in (data.get("assignees") or []) if isinstance(a, dict)
        )
        sop_template = _as_text(data.get("sop_template"))
        score = (
            _field_score(title, terms, 2.0)
            + _field_score(description, terms, 1.0)
            + _field_score(assignees, terms, 1.0)
            + _field_score(sop_template, terms, 1.0)
            + _field_score(status, terms, 0.5)
        )
        if score <= 0:
            continue
        out.append(
            SearchHit(
                kind="room_task",
                title=title or "(untitled task)",
                snippet=_snippet(description or title, terms),
                score=score,
                actor=_as_text(data.get("created_by")),
                ts=data.get("updated_at") or data.get("created_at"),
                ref={"room_id": room_id, "task_id": data.get("id"), "status": status},
            )
        )
    return out


def _search_tasks(
    store: Any, thread_id: str, terms: list[str], async_store: Any
) -> list[SearchHit]:
    s = async_store
    if s is None:
        from runtime.memory.cowork.async_work import AsyncWorkStore

        s = AsyncWorkStore(base_dir=store.base_dir, group_store=store)
    try:
        tasks = s.list(thread_id)
    except Exception:  # noqa: BLE001 — search degrades to other surfaces
        return []

    out: list[SearchHit] = []
    for task in tasks:
        data = task.to_dict() if hasattr(task, "to_dict") else dict(task)
        prompt = _as_text(data.get("prompt"))
        result = _as_text(data.get("result"))
        assignee = _as_text(data.get("assignee"))
        score = (
            _field_score(prompt, terms, 2.0)
            + _field_score(result, terms, 2.0)
            + _field_score(assignee, terms, 1.0)
        )
        if score <= 0:
            continue
        status = _as_text(data.get("status"))
        out.append(
            SearchHit(
                kind="task",
                title=f"{assignee}: {prompt[:60]}".strip(": "),
                snippet=_snippet(result or prompt, terms),
                score=score,
                actor=assignee,
                ts=data.get("created_at"),
                ref={
                    "task_id": data.get("task_id"),
                    "status": status,
                },
            )
        )
    return out


def _search_events(
    store: Any, thread_id: str, terms: list[str], until_seq: int | None
) -> list[SearchHit]:
    try:
        events = store.events(thread_id)
    except Exception:  # noqa: BLE001 — search degrades to other surfaces
        return []

    out: list[SearchHit] = []
    for ev in events:
        if until_seq is not None and getattr(ev, "seq", 0) > until_seq:
            continue
        data = ev.to_dict() if hasattr(ev, "to_dict") else dict(ev)
        action = _as_text(data.get("action"))
        actor = _as_text(data.get("actor"))
        target = _as_text(data.get("target_id"))
        mode = _as_text(data.get("mode"))
        role = _as_text(data.get("role"))
        score = (
            _field_score(actor, terms, 2.0)
            + _field_score(target, terms, 2.0)
            + _field_score(action, terms, 1.0)
            + _field_score(mode, terms, 1.0)
            + _field_score(role, terms, 1.0)
        )
        if score <= 0:
            continue
        descriptor = " ".join(p for p in [action, target or mode, role] if p)
        out.append(
            SearchHit(
                kind="event",
                title=f"{actor} · {descriptor}".strip(" ·"),
                snippet=_snippet(descriptor or action, terms),
                score=score,
                actor=actor,
                ts=data.get("ts"),
                ref={"seq": getattr(ev, "seq", None)},
            )
        )
    return out
