"""Durable session-title state — dsh ``ctx.sessionTitle`` port.

dsh semantics ported:

- source ladder: ``fallback`` (first human message) → ``provider``
  (registered, e.g. an LLM provider) → ``user`` (explicit rename).
- latest-wins; a user rename **pins** the title: automatic regeneration
  stops scheduling until an explicit ``force`` refresh.
- provider vocabulary: named providers register on the service; a refresh
  runs the chosen provider and records provenance (provider id + model id)
  on the accepted title, mirroring dsh ``SessionTitleModelProvenance``.
- refresh failure keeps the last title — a provider that returns ``None``
  or empty never clobbers the current value.
- normalization: whitespace collapses to single spaces and the title must
  contain visible characters, otherwise ``TitleInvalidError`` (dsh
  ``title-invalid``).

State lives on the thread record so it survives restart and search/sort:
``values["title"]`` carries the display title and ``metadata.title_*`` keys
carry the durable envelope facts (source / pinned / provider / model /
updated_at). Threads created before the service still resolve a title
lazily through the fallback derivation.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

TITLE_MAX_LEN = 60

SOURCE_FALLBACK = "fallback"
SOURCE_PROVIDER = "provider"
SOURCE_USER = "user"


class TitleInvalidError(ValueError):
    """Raised when a title normalizes to empty (dsh ``title-invalid``)."""


def normalize_title(text: str) -> str:
    """Collapse inner whitespace and strip; empty/whitespace-only raises."""
    collapsed = re.sub(r"\s+", " ", text).strip()
    if not collapsed:
        raise TitleInvalidError("title must contain visible characters")
    return collapsed


def derive_fallback_title(messages: list[dict[str, Any]]) -> str | None:
    """First human message text as a <=60-char title (dsh first-prompt fallback)."""
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("type") != "human":
            continue
        raw = msg.get("content")
        if isinstance(raw, str) and raw.strip():
            text = raw.strip()
            return text if len(text) <= TITLE_MAX_LEN else text[: TITLE_MAX_LEN - 3] + "…"
        if isinstance(raw, list):
            for part in raw:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    text = part["text"].strip()
                    if text:
                        return (
                            text if len(text) <= TITLE_MAX_LEN else text[: TITLE_MAX_LEN - 3] + "…"
                        )
    return None


@dataclass(frozen=True)
class SessionTitleSnapshot:
    """Accepted title plus its durable envelope facts (dsh ``SessionTitleSnapshot``)."""

    title: str
    source: str
    pinned: bool = False
    provider: str | None = None
    model: str | None = None
    updated_at: str = ""

    def to_wire(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "source": self.source,
            "pinned": self.pinned,
            "provider": self.provider,
            "model": self.model,
            "updated_at": self.updated_at,
        }


TitleProvider = Callable[[dict[str, Any]], str | None]
"""``(thread record) -> normalized title``; ``None``/empty keeps the current title."""


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"


def _snapshot_from_thread(thread: dict[str, Any]) -> SessionTitleSnapshot | None:
    metadata = thread.get("metadata") if isinstance(thread.get("metadata"), dict) else {}
    source = metadata.get("title_source")
    if source not in (SOURCE_FALLBACK, SOURCE_PROVIDER, SOURCE_USER):
        return None
    values = thread.get("values") if isinstance(thread.get("values"), dict) else {}
    title = values.get("title")
    if not isinstance(title, str) or not title.strip():
        return None
    return SessionTitleSnapshot(
        title=title,
        source=source,
        pinned=bool(metadata.get("title_pinned", False)),
        provider=metadata.get("title_provider"),
        model=metadata.get("title_model"),
        updated_at=metadata.get("title_updated_at", ""),
    )


class SessionTitleService:
    """Owns the title state for one ``ThreadStateStore``.

    The service is deliberately store-shaped (``get`` / ``rename`` /
    ``refresh`` / ``register_provider``) so callers — HTTP router, prompt
    variables, future LLM tools — share one vocabulary.
    """

    def __init__(self, store: Any) -> None:
        if store is None:
            raise ValueError("SessionTitleService requires a thread store")
        self._store = store
        self._providers: dict[str, tuple[TitleProvider, str | None]] = {}

    # ─── provider vocabulary (dsh ctx.sessionTitle.register) ─────────────

    def register_provider(
        self,
        name: str,
        provider: TitleProvider,
        *,
        model: str | None = None,
    ) -> Callable[[], None]:
        """Register a named title provider; duplicate names fail loud."""
        key = name.strip()
        if not key:
            raise ValueError("provider name must not be empty")
        if key in self._providers:
            raise ValueError(f"title provider {key!r} is already registered")
        self._providers[key] = (provider, model)

        def unregister() -> None:
            self._providers.pop(key, None)

        return unregister

    def provider_names(self) -> list[str]:
        return list(self._providers)

    # ─── read side ────────────────────────────────────────────────────────

    def get(self, thread_id: str) -> SessionTitleSnapshot:
        """Current title; falls back to the first-message derivation."""
        thread = self._store.get(thread_id)
        if thread is None:
            raise KeyError(thread_id)
        recorded = _snapshot_from_thread(thread)
        if recorded is not None:
            return recorded
        values = thread.get("values") if isinstance(thread.get("values"), dict) else {}
        raw_title = values.get("title")
        if isinstance(raw_title, str) and raw_title.strip() and raw_title.strip() != "New chat":
            # Legacy thread with a title but no durable source facts: treat it
            # as the current fallback so a failed refresh never clobbers it.
            return SessionTitleSnapshot(
                title=raw_title.strip(),
                source=SOURCE_FALLBACK,
                pinned=False,
                updated_at="",
            )
        messages = values.get("messages") or []
        fallback = derive_fallback_title(messages if isinstance(messages, list) else [])
        return SessionTitleSnapshot(
            title=fallback or "New chat",
            source=SOURCE_FALLBACK,
            pinned=False,
            updated_at="",
        )

    # ─── mutations ────────────────────────────────────────────────────────

    def rename(self, thread_id: str, title: str) -> SessionTitleSnapshot:
        """Explicit user rename — pins the title against regeneration."""
        normalized = normalize_title(title)
        now = _utc_now_iso()
        self._store.update_state(
            thread_id,
            values={"title": normalized},
            metadata={
                "title_source": SOURCE_USER,
                "title_pinned": True,
                "title_provider": None,
                "title_model": None,
                "title_updated_at": now,
            },
        )
        return SessionTitleSnapshot(
            title=normalized,
            source=SOURCE_USER,
            pinned=True,
            updated_at=now,
        )

    def refresh(
        self,
        thread_id: str,
        *,
        provider: str | None = None,
        model: str | None = None,
        force: bool = False,
    ) -> SessionTitleSnapshot:
        """Regenerate the title through a registered provider.

        Pinned (user-renamed) titles are left untouched unless ``force``.
        With no provider registered, the first-message fallback is applied.
        A provider returning ``None``/empty keeps the previous title — a
        failed refresh never clobbers (dsh latest-wins + failure keep).
        """
        current = self.get(thread_id)
        if current.pinned and not force:
            return current

        thread = self._store.get(thread_id)
        if thread is None:
            raise KeyError(thread_id)

        chosen_name: str | None = provider
        chosen_fn: TitleProvider | None = None
        chosen_model: str | None = model
        if chosen_name is None:
            if self._providers:
                chosen_name, (chosen_fn, chosen_model) = next(iter(self._providers.items()))
        else:
            entry = self._providers.get(chosen_name)
            if entry is None:
                raise KeyError(f"unknown title provider: {chosen_name}")
            chosen_fn, entry_model = entry
            if chosen_model is None:
                chosen_model = entry_model

        now = _utc_now_iso()
        if chosen_fn is None:
            values = thread.get("values") if isinstance(thread.get("values"), dict) else {}
            messages = values.get("messages") or []
            fallback = derive_fallback_title(messages if isinstance(messages, list) else [])
            if not fallback:
                return current
            return self._persist(thread_id, fallback, SOURCE_FALLBACK, now)

        try:
            produced = chosen_fn(thread)
        except Exception:  # noqa: BLE001 — provider failures keep the current title
            logger.warning(
                "session-title provider %r failed; keeping current", chosen_name, exc_info=True
            )
            return current
        if not isinstance(produced, str) or not produced.strip():
            return current
        return self._persist(
            thread_id,
            normalize_title(produced),
            SOURCE_PROVIDER,
            now,
            provider=chosen_name,
            model=chosen_model,
        )

    def maybe_auto_refresh(
        self,
        thread_id: str,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> SessionTitleSnapshot:
        """First-completed-turn title regeneration (dsh auto-title).

        Called after a turn snapshot: consults a registered provider at most
        once per thread. A user-pinned title, an already provider-generated
        title, a thread with no registered provider, or a thread whose auto
        attempt already happened (success or failure) is left untouched, so a
        failing provider is not re-invoked on every following turn.
        """
        current = self.get(thread_id)
        if current.pinned or current.source == SOURCE_PROVIDER:
            return current
        if not self._providers:
            return current
        thread = self._store.get(thread_id)
        if thread is None:
            raise KeyError(thread_id)
        metadata = thread.get("metadata") if isinstance(thread.get("metadata"), dict) else {}
        if metadata.get("title_auto_attempted"):
            return current
        # Mark attempted before consulting the provider so a failed (or
        # crashed) regeneration is never retried turn after turn.
        self._store.update_state(thread_id, metadata={"title_auto_attempted": True})
        return self.refresh(thread_id, provider=provider, model=model)

    def _persist(
        self,
        thread_id: str,
        title: str,
        source: str,
        now: str,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> SessionTitleSnapshot:
        self._store.update_state(
            thread_id,
            values={"title": title},
            metadata={
                "title_source": source,
                "title_pinned": False,
                "title_provider": provider,
                "title_model": model,
                "title_updated_at": now,
            },
        )
        return SessionTitleSnapshot(
            title=title,
            source=source,
            pinned=False,
            provider=provider,
            model=model,
            updated_at=now,
        )


def register_session_title_variable(registry: Any, *, store_getter: Callable[[], Any]) -> None:
    """Expose the current session title as the ``{{ session_title }}`` prompt variable.

    dsh ``ctx.sessionTitle`` agent surface: the model can see (and, via the
    rename endpoint, control) its session title. Resolution is best-effort —
    when no ambient session/thread exists the variable renders the neutral
    default so assembly never fails on it.
    """

    def _provider(scope: str | None) -> str:
        try:
            from runtime.platform.process.session import current_session

            session = current_session()
            thread_id = getattr(session, "thread_id", None)
            store = store_getter()
            if thread_id and store is not None:
                service = SessionTitleService(store)
                return service.get(str(thread_id)).title
        except Exception:  # noqa: BLE001 — variable is best-effort
            logger.debug("session_title variable resolution failed", exc_info=True)
        return "New chat"

    registry.register_variable("session_title", _provider)


__all__ = [
    "SessionTitleService",
    "SessionTitleSnapshot",
    "TitleInvalidError",
    "TitleProvider",
    "derive_fallback_title",
    "normalize_title",
    "register_session_title_variable",
]
