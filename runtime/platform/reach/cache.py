from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from collections import OrderedDict
from collections.abc import Hashable
from contextlib import suppress
from copy import deepcopy
from pathlib import Path
from threading import Lock
from typing import Any


class ReachCache:
    """Bounded memory cache with an optional process-safe SQLite backing store."""

    def __init__(self, max_entries: int = 256, path: str | Path | None = None) -> None:
        self.max_entries = max(1, max_entries)
        self.path = Path(path).expanduser() if path else None
        self._entries: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
        self._lock = Lock()
        if self.path is not None:
            self._initialize_disk()

    @staticmethod
    def _key(value: Hashable) -> str:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _initialize_disk(self) -> None:
        assert self.path is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with suppress(OSError):
            self.path.parent.chmod(0o700)
        with sqlite3.connect(self.path, timeout=3) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS reach_cache "
                "(cache_key TEXT PRIMARY KEY, expires_at REAL NOT NULL, payload TEXT NOT NULL)"
            )
            db.execute("CREATE INDEX IF NOT EXISTS reach_cache_expiry ON reach_cache(expires_at)")
        with suppress(OSError):
            self.path.chmod(0o600)

    def get(self, key: Hashable) -> dict[str, Any] | None:
        cache_key = self._key(key)
        now = time.time()
        with self._lock:
            entry = self._entries.get(cache_key)
            if entry is not None:
                expires_at, value = entry
                if expires_at > now:
                    self._entries.move_to_end(cache_key)
                    result = deepcopy(value)
                    result.update(cached=True, cache_backend="memory")
                    return result
                self._entries.pop(cache_key, None)
        if self.path is None:
            return None
        try:
            with sqlite3.connect(self.path, timeout=3) as db:
                row = db.execute(
                    "SELECT expires_at, payload FROM reach_cache WHERE cache_key = ?",
                    (cache_key,),
                ).fetchone()
                if row is None:
                    return None
                if float(row[0]) <= now:
                    db.execute("DELETE FROM reach_cache WHERE cache_key = ?", (cache_key,))
                    return None
                value = json.loads(row[1])
        except (OSError, sqlite3.Error, ValueError, TypeError):
            return None
        self._remember(cache_key, float(row[0]), value)
        value.update(cached=True, cache_backend="sqlite")
        return value

    def put(self, key: Hashable, value: dict[str, Any], ttl_seconds: int) -> None:
        if value.get("error") or ttl_seconds <= 0:
            return
        cache_key = self._key(key)
        expires_at = time.time() + ttl_seconds
        clean = deepcopy(value)
        clean.pop("cached", None)
        clean.pop("cache_backend", None)
        self._remember(cache_key, expires_at, clean)
        if self.path is None:
            return
        try:
            with sqlite3.connect(self.path, timeout=3) as db:
                db.execute(
                    "INSERT OR REPLACE INTO reach_cache(cache_key, expires_at, payload) VALUES(?,?,?)",
                    (cache_key, expires_at, json.dumps(clean, ensure_ascii=False, default=str)),
                )
                db.execute("DELETE FROM reach_cache WHERE expires_at <= ?", (time.time(),))
                count = int(db.execute("SELECT COUNT(*) FROM reach_cache").fetchone()[0])
                if count > self.max_entries:
                    db.execute(
                        "DELETE FROM reach_cache WHERE cache_key IN "
                        "(SELECT cache_key FROM reach_cache ORDER BY expires_at ASC LIMIT ?)",
                        (count - self.max_entries,),
                    )
        except (OSError, sqlite3.Error, TypeError, ValueError):
            return

    def _remember(self, key: str, expires_at: float, value: dict[str, Any]) -> None:
        with self._lock:
            self._entries[key] = (expires_at, deepcopy(value))
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
        if self.path is not None:
            try:
                with sqlite3.connect(self.path, timeout=3) as db:
                    db.execute("DELETE FROM reach_cache")
            except (OSError, sqlite3.Error):
                pass


def _default_cache_path() -> Path:
    configured = os.environ.get("ECHO_REACH_CACHE_PATH")
    if configured:
        return Path(configured).expanduser()
    root = Path(os.environ.get("ECHO_HOME") or (Path.home() / ".echo"))
    return root / "cache" / "reach.sqlite3"


reach_cache = ReachCache(
    max_entries=int(os.environ.get("ECHO_REACH_CACHE_MAX_ENTRIES", "512")),
    path=_default_cache_path(),
)
