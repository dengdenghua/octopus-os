from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from runtime.platform.io import atomic_write_text


class HotCacheEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    agent_id: str
    summary: str
    word_count: int
    saved_at: str  # ISO 8601
    session_id: str = ""
    topics: list[str] = Field(default_factory=list)


class SessionHotCache:
    def __init__(
        self,
        path: str = "~/.echo/hot_cache",
        max_words: int = 500,
        ttl_hours: float = 24.0,
    ) -> None:
        self._path = Path(os.path.expanduser(path))
        self._max_words = max_words
        self._ttl_hours = ttl_hours

    def save(
        self,
        agent_id: str,
        summary: str,
        session_id: str = "",
        topics: list[str] | None = None,
    ) -> HotCacheEntry:
        truncated = self._truncate(summary)
        entry = HotCacheEntry(
            agent_id=agent_id,
            summary=truncated,
            word_count=len(truncated.split()),
            saved_at=datetime.now(UTC).isoformat(),
            session_id=session_id,
            topics=topics or [],
        )
        self._path.mkdir(parents=True, exist_ok=True)
        file_path = self._path / f"{_safe_filename(agent_id)}.json"
        atomic_write_text(file_path, entry.model_dump_json(indent=2))
        return entry

    def load(self, agent_id: str) -> HotCacheEntry | None:
        file_path = self._path / f"{_safe_filename(agent_id)}.json"
        if not file_path.exists():
            return None
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            entry = HotCacheEntry(**data)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return None
        if not self._is_fresh(entry.saved_at):
            return None
        return entry

    def clear(self, agent_id: str) -> bool:
        file_path = self._path / f"{_safe_filename(agent_id)}.json"
        if file_path.exists():
            file_path.unlink()
            return True
        return False

    def list_agents(self) -> list[str]:
        if not self._path.exists():
            return []
        return [f.stem for f in self._path.glob("*.json") if f.is_file()]

    def _truncate(self, text: str) -> str:
        words = text.split()
        if len(words) <= self._max_words:
            return text
        return " ".join(words[: self._max_words]) + "..."

    def _is_fresh(self, iso_timestamp: str) -> bool:
        try:
            saved = datetime.fromisoformat(iso_timestamp)
            if saved.tzinfo is None:
                saved = saved.replace(tzinfo=UTC)
            now = datetime.now(UTC)
            age_hours = (now - saved).total_seconds() / 3600.0
            return age_hours <= self._ttl_hours
        except Exception:
            return False


def _safe_filename(agent_id: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in agent_id)
