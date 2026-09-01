"""
Ambient Suggestions · "what should the user do next?"

Scans recent conversation history, extracts unfinished threads of
intent, and surfaces them as a ranked list of proposed next actions.
The user clicks one to spawn a new thread pre-loaded with the
suggested prompt.

Why this exists
---------------
Reflection tells the agent what went wrong in the LAST turn
(``deep_evolution``). Turn-scoring tells the agent how it did
(``turn_scoring``). Neither answers "what's the best NEXT
conversation?" — the user has to invent that themselves. Ambient
suggestions closes that loop.

Storage layout
--------------

    data/ambient_suggestions/
        <project_hash>/
            suggestions.json        ← active + recent history

``<project_hash>`` is a SHA-1 of the project root path (so each
workspace has its own bucket).

Data model
----------

    Suggestion {
        id:            UUID,
        project_root:  str (absolute path),
        title:         str (≤ 80 chars, imperative action phrase),
        description:   str (one-line reasoning),
        prompt:        str (what to send when the user accepts),
        locale:        "en-US" | "zh-CN" | "ja-JP" | "ko-KR",
        source_turn_ids: list[str],     # audit trail
        status:        "pending" | "accepted" | "dismissed",
        created_at:    ISO-8601,
        updated_at:    ISO-8601,
        model:         str | None,      # which LLM produced it
        experimental:  bool,
    }

    Bucket {
        project_root:    str,
        generated_at:    ISO-8601,
        suggestions:     list[Suggestion],
    }

LLM generator
-------------
Reuses ``deep_evolution._llm_call_json`` so the router-wiring path
is identical to ``deep_reflect``/``deep_evolve``. The generator
consumes:
  - the last N scored turns (from ``turn_scoring``)
  - each turn's stored thread title (summarizes topic cheaply)
  - current SOUL / MEMORY (so suggestions respect the agent persona)

It does NOT read full transcripts — that's what ``deep_reflect``
already does for per-turn scoring. Ambient lives at the "sessions
view" level: broad, cheap, shallow.

Gating
------
Entire feature is behind the ``ui.ambient_suggestions`` feature
flag (registered as experimental). Any public caller that enters
this module should first check ``feature_flags.is_on(...)`` and
bail early if off. Storage calls are cheap enough to allow without
the flag, but LLM calls are the real cost.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from runtime.platform.io import atomic_write_json, read_json_with_backup
from runtime.safety.auth.scope import TenantScope

_LOG = logging.getLogger("echo.ambient_suggestions")

_STATUS_PENDING = "pending"
_STATUS_ACCEPTED = "accepted"
_STATUS_DISMISSED = "dismissed"
_VALID_STATUSES = frozenset({_STATUS_PENDING, _STATUS_ACCEPTED, _STATUS_DISMISSED})

# Cap per project. Beyond this we drop the oldest dismissed items
# first, then the oldest accepted, then the oldest pending. Keeps
# the json file small; UI rarely renders > 20.
_MAX_SUGGESTIONS_PER_PROJECT = 50

# How many recent scored turns to feed the LLM. More = better signal
# but higher prompt cost. We keep the window small (single digits to
# ~20) and tune per surface based on observed retrieval quality.
_DEFAULT_TURN_WINDOW = 15

_DEFAULT_LOCALE = "en-US"
_LOCALE_LABELS = {
    "en-US": "English (United States)",
    "zh-CN": "Simplified Chinese (简体中文)",
    "ja-JP": "Japanese (日本語)",
    "ko-KR": "Korean (한국어)",
}


def _normalize_locale(locale: str | None) -> str:
    """Normalize browser-style locale values to the supported UI locales."""
    value = str(locale or "").strip().replace("_", "-").lower()
    if value.startswith("zh"):
        return "zh-CN"
    if value.startswith("ja"):
        return "ja-JP"
    if value.startswith("ko"):
        return "ko-KR"
    return _DEFAULT_LOCALE


# ═══════════════════════════════════════════════════════════
# Data model
# ═══════════════════════════════════════════════════════════


@dataclass
class Suggestion:
    """A single ranked proposal. Mutable only through this module
    (``upsert`` / ``mark_status``) so ``updated_at`` stays honest.
    """

    id: str
    project_root: str
    title: str
    description: str
    prompt: str
    locale: str = _DEFAULT_LOCALE
    status: str = _STATUS_PENDING
    source_turn_ids: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    model: str | None = None
    experimental: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Suggestion | None:
        try:
            return cls(
                id=str(raw["id"]),
                project_root=str(raw["project_root"]),
                title=str(raw.get("title") or "")[:200],
                description=str(raw.get("description") or "")[:1000],
                prompt=str(raw.get("prompt") or "")[:4000],
                # Buckets written before locale support were generated by an
                # English-only prompt, so treating them as en-US prevents
                # stale English chips leaking into other UI languages.
                locale=_normalize_locale(raw.get("locale")),
                status=(
                    raw.get("status") if raw.get("status") in _VALID_STATUSES else _STATUS_PENDING
                ),
                source_turn_ids=list(raw.get("source_turn_ids") or []),
                created_at=str(raw.get("created_at") or ""),
                updated_at=str(raw.get("updated_at") or ""),
                model=raw.get("model"),
                experimental=bool(raw.get("experimental", True)),
            )
        except (KeyError, TypeError, ValueError):
            return None


# ═══════════════════════════════════════════════════════════
# Paths
# ═══════════════════════════════════════════════════════════


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _project_hash(project_root: str | Path) -> str:
    norm = str(Path(project_root).resolve())
    return hashlib.sha1(norm.encode("utf-8"), usedforsecurity=False).hexdigest()


def _bucket_path(base_dir: Path, project_root: str | Path) -> Path:
    return base_dir / _project_hash(project_root) / "suggestions.json"


def _default_base_dir() -> Path:
    """Resolve the on-disk root the same way other memory modules do.

    Uses ``ECHO_DATA_DIR`` when set (tests, custom installs),
    else ``<cwd>/data``. Matches ``runtime.platform.process.paths``.
    """
    from runtime.platform.process.paths import app_paths

    return app_paths().data_dir / "ambient_suggestions"


# ═══════════════════════════════════════════════════════════
# Storage
# ═══════════════════════════════════════════════════════════


class _BucketLock:
    """One lock per bucket path. Cheap dedup via dict + mutex."""

    _locks: dict[str, threading.Lock] = {}
    _guard = threading.Lock()

    @classmethod
    def for_path(cls, path: Path) -> threading.Lock:
        key = str(path.resolve())
        with cls._guard:
            lock = cls._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                cls._locks[key] = lock
            return lock


def read_bucket(
    project_root: str | Path,
    *,
    base_dir: Path | None = None,
    locale: str | None = None,
) -> dict[str, Any]:
    """Return the on-disk bucket (or an empty stub).

    Structure is stable — UIs can render directly off this:
        { project_root, generated_at, suggestions: [...] }
    """
    base = base_dir or _default_base_dir()
    path = _bucket_path(base, project_root)
    raw = read_json_with_backup(path, default=None)
    if not isinstance(raw, dict):
        return {
            "project_root": str(Path(project_root).resolve()),
            "generated_at": "",
            "suggestions": [],
        }
    suggestions: list[dict[str, Any]] = []
    for entry in raw.get("suggestions") or []:
        if isinstance(entry, dict):
            s = Suggestion.from_dict(entry)
            if s is not None and (locale is None or s.locale == _normalize_locale(locale)):
                suggestions.append(s.to_dict())
    return {
        "project_root": str(raw.get("project_root") or Path(project_root).resolve()),
        "generated_at": str(raw.get("generated_at") or ""),
        "suggestions": suggestions,
    }


def _prune(suggestions: list[Suggestion]) -> list[Suggestion]:
    """Trim to ``_MAX_SUGGESTIONS_PER_PROJECT`` without losing the
    active pending set. Drop order: dismissed → accepted → pending
    (oldest first within each bucket).
    """
    if len(suggestions) <= _MAX_SUGGESTIONS_PER_PROJECT:
        return suggestions
    priority = {
        _STATUS_DISMISSED: 0,
        _STATUS_ACCEPTED: 1,
        _STATUS_PENDING: 2,
    }
    # Sort so drop-candidates come first: low priority first, then
    # oldest ``updated_at`` first within each priority. We keep the
    # TAIL of the sort (highest priority, newest).
    suggestions_sorted = sorted(
        suggestions,
        key=lambda s: (priority.get(s.status, 0), s.updated_at),
    )
    return suggestions_sorted[-_MAX_SUGGESTIONS_PER_PROJECT:]


def _write_bucket(
    path: Path,
    project_root: str | Path,
    suggestions: list[Suggestion],
) -> None:
    payload = {
        "project_root": str(Path(project_root).resolve()),
        "generated_at": _now_iso(),
        "suggestions": [s.to_dict() for s in _prune(suggestions)],
    }
    atomic_write_json(path, payload)


def upsert_many(
    project_root: str | Path,
    candidates: Iterable[Suggestion],
    *,
    base_dir: Path | None = None,
) -> int:
    """Merge a batch of fresh suggestions into the bucket.

    Dedupe rule: locale + case-insensitive title match against any
    EXISTING suggestion (regardless of status). On a hit we refresh
    ``updated_at`` and ``source_turn_ids`` but keep the original
    ``id`` + ``status`` — so dismissing a suggestion once means it
    stays dismissed even if the LLM re-proposes it.

    Returns the number of NEW suggestions added.
    """
    base = base_dir or _default_base_dir()
    path = _bucket_path(base, project_root)
    lock = _BucketLock.for_path(path)
    with lock:
        raw = read_bucket(project_root, base_dir=base)
        existing: list[Suggestion] = []
        by_title: dict[tuple[str, str], Suggestion] = {}
        for entry in raw["suggestions"]:
            s = Suggestion.from_dict(entry)
            if s is None:
                continue
            existing.append(s)
            by_title[(s.locale, s.title.lower().strip())] = s

        added = 0
        now = _now_iso()
        for candidate in candidates:
            if not candidate.title or not candidate.prompt:
                continue
            candidate.locale = _normalize_locale(candidate.locale)
            key = (candidate.locale, candidate.title.lower().strip())
            if key in by_title:
                target = by_title[key]
                target.updated_at = now
                target.description = candidate.description or target.description
                target.prompt = candidate.prompt or target.prompt
                # Merge audit trail (keep order, dedupe).
                seen = set(target.source_turn_ids)
                for tid in candidate.source_turn_ids:
                    if tid not in seen:
                        target.source_turn_ids.append(tid)
                        seen.add(tid)
                continue
            candidate.id = candidate.id or uuid4().hex
            candidate.created_at = candidate.created_at or now
            candidate.updated_at = now
            candidate.project_root = str(Path(project_root).resolve())
            existing.append(candidate)
            by_title[key] = candidate
            added += 1

        _write_bucket(path, project_root, existing)
        return added


def mark_status(
    project_root: str | Path,
    suggestion_id: str,
    status: str,
    *,
    base_dir: Path | None = None,
) -> dict[str, Any] | None:
    """Update a suggestion's status. Returns the updated dict or
    ``None`` when not found."""
    if status not in _VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}")
    base = base_dir or _default_base_dir()
    path = _bucket_path(base, project_root)
    lock = _BucketLock.for_path(path)
    with lock:
        raw = read_bucket(project_root, base_dir=base)
        suggestions = [Suggestion.from_dict(entry) for entry in raw["suggestions"]]
        suggestions = [s for s in suggestions if s is not None]
        hit: Suggestion | None = None
        for s in suggestions:
            if s.id == suggestion_id:
                s.status = status
                s.updated_at = _now_iso()
                hit = s
                break
        if hit is None:
            return None
        _write_bucket(path, project_root, suggestions)
        return hit.to_dict()


def clear(
    project_root: str | Path,
    *,
    base_dir: Path | None = None,
    only_status: str | None = None,
) -> int:
    """Drop suggestions (all, or only those with ``only_status``).

    Returns the count removed.
    """
    base = base_dir or _default_base_dir()
    path = _bucket_path(base, project_root)
    lock = _BucketLock.for_path(path)
    with lock:
        raw = read_bucket(project_root, base_dir=base)
        suggestions: list[Suggestion] = []
        for entry in raw["suggestions"]:
            s = Suggestion.from_dict(entry)
            if s is not None:
                suggestions.append(s)
        before = len(suggestions)
        if only_status is None:
            suggestions = []
        else:
            if only_status not in _VALID_STATUSES:
                raise ValueError(f"invalid status {only_status!r}")
            suggestions = [s for s in suggestions if s.status != only_status]
        _write_bucket(path, project_root, suggestions)
        return before - len(suggestions)


# ═══════════════════════════════════════════════════════════
# Generator (LLM-backed)
# ═══════════════════════════════════════════════════════════


_SYSTEM_PROMPT = """You are a thoughtful senior engineer helping a \
user decide what to do next. You'll see a condensed history of their \
recent work: how each conversation ended, what broke, what they've \
been exploring.

Your job: produce 3-6 CONCRETE, ACTIONABLE follow-up tasks the user \
should consider for this project.

Rules:
  - Each suggestion must be specific to this project, not generic.
  - Bias toward unfinished work: failed/interrupted turns > successes.
  - Each title is an imperative verb phrase, ≤ 80 chars.
  - Each description states WHY this makes sense now, in 1-2 \
sentences.
  - The "prompt" field is what to send to the agent verbatim if the \
user accepts. Write it naturally, as if the user typed it.
  - Do NOT suggest tasks the user has clearly already dismissed.
  - Output JSON only, no prose. Schema:
      {"suggestions": [
        {"title": "...", "description": "...", "prompt": "...",
         "source_turn_ids": ["turn-uuid-1", ...]}
      ]}
"""


def _render_system_prompt(locale: str) -> str:
    normalized = _normalize_locale(locale)
    language = _LOCALE_LABELS[normalized]
    return f"""{_SYSTEM_PROMPT}

Language requirement:
  - Write every user-visible value in "title", "description", and "prompt" in {language}.
  - Match the requested locale {normalized}; do not default to English.
  - Keep code identifiers, commands, file paths, APIs, and product names unchanged.
"""


def _summarize_turns(
    scores: list[Any],
    *,
    title_lookup: dict[str, str] | None = None,
) -> str:
    """Render recent turns into a compact bullet list for the LLM.

    ``title_lookup`` maps ``thread_id → thread title`` so the LLM
    sees topic hints without loading full transcripts. Pass ``None``
    to skip — the LLM still has ``reason`` and ``score`` to work
    with.
    """
    lines: list[str] = []
    for s in scores:
        title = ""
        if title_lookup:
            title = title_lookup.get(s.thread_id or "", "")
        bits = [
            f"- turn_id={s.turn_id or '?'}",
            f"thread={s.thread_id[:8] if s.thread_id else '?'}",
            f"score={s.score:.2f}",
            f"reason={s.reason or 'n/a'}",
        ]
        if title:
            bits.append(f'topic="{title[:100]}"')
        lines.append(" ".join(bits))
    return "\n".join(lines) if lines else "(no recent turns)"


def _render_user_prompt(
    project_root: str,
    scores_summary: str,
    existing_dismissed: list[str],
    locale: str,
) -> str:
    dismissed_block = (
        "Previously dismissed suggestion titles (do not re-propose):\n"
        + "\n".join(f"  - {t}" for t in existing_dismissed)
        if existing_dismissed
        else "No prior dismissals."
    )
    return f"""Project root: {project_root}
Response locale: {_normalize_locale(locale)} ({_LOCALE_LABELS[_normalize_locale(locale)]})

Recent turn history (newest first):
{scores_summary}

{dismissed_block}

Produce 3-6 suggestions as JSON per the schema in your instructions.
"""


def generate_suggestions(
    project_root: str | Path,
    agent_id: str,
    *,
    turn_window: int = _DEFAULT_TURN_WINDOW,
    model: str | None = None,
    base_dir: Path | None = None,
    title_lookup: dict[str, str] | None = None,
    locale: str = _DEFAULT_LOCALE,
    scope: TenantScope | None = None,
) -> dict[str, Any]:
    """Produce new ambient suggestions via LLM, merge into bucket.

    Returns a dict:
        { "generated": int, "added": int, "model": str | None,
          "error": str | None, "suggestions": list[dict] }

    ``generated`` is the raw number of candidates the LLM produced;
    ``added`` is how many survived dedupe against the existing
    bucket.

    Caller is responsible for feature-flag gating. Raises nothing;
    all failure modes come back in the dict.
    """
    from runtime.memory.learning.deep_evolution import _llm_call_json
    from runtime.memory.learning.turn_scoring import read_recent_scores

    base = base_dir or _default_base_dir()
    proj = str(Path(project_root).resolve())
    normalized_locale = _normalize_locale(locale)

    scores = read_recent_scores(agent_id, limit=turn_window, scope=scope)
    if not scores:
        return {
            "generated": 0,
            "added": 0,
            "model": None,
            "error": "no scored turns yet — agent hasn't run enough",
            "suggestions": [],
        }

    existing = read_bucket(project_root, base_dir=base, locale=normalized_locale)
    dismissed = [
        str(s.get("title") or "")
        for s in existing.get("suggestions") or []
        if s.get("status") == _STATUS_DISMISSED
    ]

    user_prompt = _render_user_prompt(
        proj,
        _summarize_turns(scores, title_lookup=title_lookup),
        dismissed,
        normalized_locale,
    )

    parsed, meta = _llm_call_json(
        system=_render_system_prompt(normalized_locale),
        user=user_prompt,
        model=model,
        max_tokens=1800,
        temperature=0.3,
    )
    if parsed is None:
        return {
            "generated": 0,
            "added": 0,
            "model": meta.get("model"),
            "error": meta.get("error") or meta.get("parse_error") or "LLM returned no JSON",
            "suggestions": [],
        }

    raw_list = parsed.get("suggestions") if isinstance(parsed, dict) else None
    if not isinstance(raw_list, list):
        return {
            "generated": 0,
            "added": 0,
            "model": meta.get("model"),
            "error": "response missing 'suggestions' array",
            "suggestions": [],
        }

    candidates: list[Suggestion] = []
    for raw in raw_list:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        prompt = str(raw.get("prompt") or "").strip()
        description = str(raw.get("description") or "").strip()
        if not title or not prompt:
            continue
        candidates.append(
            Suggestion(
                id=uuid4().hex,
                project_root=proj,
                title=title[:200],
                description=description[:1000],
                prompt=prompt[:4000],
                locale=normalized_locale,
                source_turn_ids=[
                    str(t).strip() for t in (raw.get("source_turn_ids") or []) if str(t).strip()
                ][:10],
                model=meta.get("model"),
                experimental=True,
            )
        )

    added = upsert_many(project_root, candidates, base_dir=base)

    # Return the POST-merge view so caller can surface the final
    # list directly. This is the shape /api endpoints will shape.
    merged = read_bucket(project_root, base_dir=base, locale=normalized_locale)
    return {
        "generated": len(candidates),
        "added": added,
        "model": meta.get("model"),
        "error": None,
        "suggestions": merged["suggestions"],
    }


__all__ = [
    "Suggestion",
    "clear",
    "generate_suggestions",
    "mark_status",
    "read_bucket",
    "upsert_many",
]
