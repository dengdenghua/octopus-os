from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

_LOG = logging.getLogger("echo.platform.state")
_SAFE_FILE_NAMESPACE_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,239}$")


def _require_file_namespace(namespace: str) -> str:
    value = str(namespace or "").strip()
    if not _SAFE_FILE_NAMESPACE_RE.fullmatch(value):
        raise ValueError(
            "invalid file state namespace: use letters, numbers, dot, underscore, or hyphen"
        )
    return value


@dataclass
class StateEntry:
    key: str
    value: Any
    namespace: str = "default"
    version: int = 1
    ts: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class StateBackend(ABC):
    @abstractmethod
    def get(self, key: str, namespace: str = "default") -> StateEntry | None: ...

    @abstractmethod
    def set(self, entry: StateEntry) -> StateEntry: ...

    @abstractmethod
    def delete(self, key: str, namespace: str = "default") -> bool: ...

    @abstractmethod
    def list_keys(self, namespace: str = "default", prefix: str = "") -> list[str]: ...

    @abstractmethod
    def list_namespaces(self) -> list[str]: ...


class MemoryBackend(StateBackend):
    def __init__(self) -> None:
        self._store: dict[str, dict[str, StateEntry]] = {}
        self._lock = threading.RLock()

    def _ns(self, namespace: str) -> dict[str, StateEntry]:
        if namespace not in self._store:
            self._store[namespace] = {}
        return self._store[namespace]

    def get(self, key: str, namespace: str = "default") -> StateEntry | None:
        with self._lock:
            return self._ns(namespace).get(key)

    def set(self, entry: StateEntry) -> StateEntry:
        with self._lock:
            ns = self._ns(entry.namespace)
            existing = ns.get(entry.key)
            if existing:
                entry.version = existing.version + 1
            entry.ts = datetime.now().isoformat(timespec="seconds")
            ns[entry.key] = entry
            return entry

    def delete(self, key: str, namespace: str = "default") -> bool:
        with self._lock:
            ns = self._ns(namespace)
            return ns.pop(key, None) is not None

    def list_keys(self, namespace: str = "default", prefix: str = "") -> list[str]:
        with self._lock:
            ns = self._ns(namespace)
            if prefix:
                return [k for k in ns if k.startswith(prefix)]
            return list(ns.keys())

    def list_namespaces(self) -> list[str]:
        with self._lock:
            return list(self._store.keys())


class FileBackend(StateBackend):
    def __init__(self, base_dir: str = "data/state") -> None:
        self._base = Path(base_dir)
        self._base.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _ns_dir(self, namespace: str) -> Path:
        d = self._base / _require_file_namespace(namespace)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _key_path(self, key: str, namespace: str) -> Path:
        safe_key = key.replace("/", "_").replace("\\", "_")
        if not safe_key or safe_key in {".", ".."}:
            raise ValueError("invalid file state key")
        path = self._ns_dir(namespace) / f"{safe_key}.json"
        base = self._base.resolve()
        resolved = path.resolve()
        if base not in resolved.parents:
            raise ValueError("file state path escapes base directory")
        return path

    def get(self, key: str, namespace: str = "default") -> StateEntry | None:
        path = self._key_path(key, namespace)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return StateEntry(**data)
        except Exception as _exc:
            _LOG.debug("state file read failed: %s", _exc)
            return None

    def set(self, entry: StateEntry) -> StateEntry:
        with self._lock:
            existing = self.get(entry.key, entry.namespace)
            if existing:
                entry.version = existing.version + 1
            entry.ts = datetime.now().isoformat(timespec="seconds")
            path = self._key_path(entry.key, entry.namespace)
            path.write_text(
                json.dumps(
                    {
                        "key": entry.key,
                        "value": entry.value,
                        "namespace": entry.namespace,
                        "version": entry.version,
                        "ts": entry.ts,
                        "metadata": entry.metadata,
                    },
                    ensure_ascii=False,
                    default=str,
                ),
                encoding="utf-8",
            )
            return entry

    def delete(self, key: str, namespace: str = "default") -> bool:
        path = self._key_path(key, namespace)
        if path.exists():
            path.unlink()
            return True
        return False

    def list_keys(self, namespace: str = "default", prefix: str = "") -> list[str]:
        ns_dir = self._ns_dir(namespace)
        keys = [f.stem for f in ns_dir.glob("*.json")]
        if prefix:
            keys = [k for k in keys if k.startswith(prefix)]
        return sorted(keys)

    def list_namespaces(self) -> list[str]:
        if not self._base.exists():
            return []
        return sorted(
            d.name for d in self._base.iterdir() if d.is_dir() and not d.name.startswith(".")
        )


class SQLiteBackend(StateBackend):
    def __init__(self, db_path: str = "data/state/state.db") -> None:
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA cache_size=-64000")
            self._conn.execute("PRAGMA busy_timeout=5000")
        return self._conn

    def _init_db(self) -> None:
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS state (
                namespace TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                ts TEXT NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY (namespace, key)
            )
        """)
        conn.commit()

    def get(self, key: str, namespace: str = "default") -> StateEntry | None:
        with self._lock:
            row = (
                self._get_conn()
                .execute(
                    "SELECT key, value, namespace, version, ts, metadata "
                    "FROM state WHERE namespace=? AND key=?",
                    (namespace, key),
                )
                .fetchone()
            )
        if row is None:
            return None
        try:
            value = json.loads(row[1])
            metadata = json.loads(row[5])
        except json.JSONDecodeError:
            value = row[1]
            metadata = {}
        return StateEntry(
            key=row[0],
            value=value,
            namespace=row[2],
            version=row[3],
            ts=row[4],
            metadata=metadata,
        )

    def set(self, entry: StateEntry) -> StateEntry:
        with self._lock:
            existing = self.get(entry.key, entry.namespace)
            if existing:
                entry.version = existing.version + 1
            entry.ts = datetime.now().isoformat(timespec="seconds")
            self._get_conn().execute(
                "INSERT OR REPLACE INTO state (namespace, key, value, version, ts, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    entry.namespace,
                    entry.key,
                    json.dumps(entry.value, ensure_ascii=False, default=str),
                    entry.version,
                    entry.ts,
                    json.dumps(entry.metadata, ensure_ascii=False),
                ),
            )
            self._get_conn().commit()
        return entry

    def delete(self, key: str, namespace: str = "default") -> bool:
        with self._lock:
            cursor = self._get_conn().execute(
                "DELETE FROM state WHERE namespace=? AND key=?",
                (namespace, key),
            )
            self._get_conn().commit()
            return cursor.rowcount > 0

    def list_keys(self, namespace: str = "default", prefix: str = "") -> list[str]:
        with self._lock:
            if prefix:
                rows = (
                    self._get_conn()
                    .execute(
                        "SELECT key FROM state WHERE namespace=? AND key LIKE ?",
                        (namespace, f"{prefix}%"),
                    )
                    .fetchall()
                )
            else:
                rows = (
                    self._get_conn()
                    .execute(
                        "SELECT key FROM state WHERE namespace=?",
                        (namespace,),
                    )
                    .fetchall()
                )
        return sorted(r[0] for r in rows)

    def list_namespaces(self) -> list[str]:
        with self._lock:
            rows = self._get_conn().execute("SELECT DISTINCT namespace FROM state").fetchall()
        return sorted(r[0] for r in rows)

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None


@dataclass
class WatchSubscription:
    watch_id: int
    key_pattern: str
    namespace: str
    handler: Callable[[StateEntry | None, StateEntry | None], None]


class StateStore:
    _instance: StateStore | None = None
    _lock: threading.Lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> StateStore:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(backend=MemoryBackend())
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        with cls._lock:
            cls._instance = None

    def __init__(self, backend: StateBackend | None = None) -> None:
        self._backend = backend or MemoryBackend()
        self._watches: dict[int, WatchSubscription] = {}
        self._next_watch_id: int = 1
        self._watch_lock = threading.RLock()

    @property
    def backend(self) -> StateBackend:
        return self._backend

    def get(self, key: str, namespace: str = "default") -> Any:
        entry = self._backend.get(key, namespace)
        return entry.value if entry else None

    def get_entry(self, key: str, namespace: str = "default") -> StateEntry | None:
        return self._backend.get(key, namespace)

    def set(
        self,
        key: str,
        value: Any,
        namespace: str = "default",
        metadata: dict[str, Any] | None = None,
    ) -> StateEntry:
        old_entry = self._backend.get(key, namespace)
        old_value = old_entry.value if old_entry else None

        entry = StateEntry(
            key=key,
            value=value,
            namespace=namespace,
            version=1,
            metadata=metadata or {},
        )
        new_entry = self._backend.set(entry)

        self._notify_watches(key, namespace, new_entry, old_entry)

        try:
            from runtime.platform.process.eventbus import StateChanged, get_eventbus

            bus = get_eventbus()
            bus.publish(
                StateChanged(
                    event_type="state.changed",
                    key=key,
                    old_value=old_value,
                    new_value=value,
                    agent_id=namespace,
                )
            )
        except Exception as _exc:
            _LOG.debug("state changed event publish failed: %s", _exc)

        return new_entry

    def delete(self, key: str, namespace: str = "default") -> bool:
        old_entry = self._backend.get(key, namespace)
        result = self._backend.delete(key, namespace)
        if result and old_entry:
            self._notify_watches(key, namespace, None, old_entry)
        return result

    def list_keys(self, namespace: str = "default", prefix: str = "") -> list[str]:
        return self._backend.list_keys(namespace, prefix)

    def list_namespaces(self) -> list[str]:
        return self._backend.list_namespaces()

    def inc(self, key: str, namespace: str = "default", delta: int = 1) -> int:
        current = self.get(key, namespace) or 0
        new_val = int(current) + delta
        self.set(key, new_val, namespace)
        return new_val

    def get_or_set(
        self,
        key: str,
        default: Any,
        namespace: str = "default",
    ) -> Any:
        val = self.get(key, namespace)
        if val is None:
            self.set(key, default, namespace)
            return default
        return val

    def watch(
        self,
        key_pattern: str,
        handler: Callable[[StateEntry | None, StateEntry | None], None],
        namespace: str = "default",
    ) -> int:
        with self._watch_lock:
            wid = self._next_watch_id
            self._next_watch_id += 1
            self._watches[wid] = WatchSubscription(
                watch_id=wid,
                key_pattern=key_pattern,
                namespace=namespace,
                handler=handler,
            )
        return wid

    def unwatch(self, watch_id: int) -> bool:
        with self._watch_lock:
            return self._watches.pop(watch_id, None) is not None

    def _notify_watches(
        self,
        key: str,
        namespace: str,
        new_entry: StateEntry | None,
        old_entry: StateEntry | None,
    ) -> None:
        import fnmatch

        with self._watch_lock:
            watches = list(self._watches.values())

        for w in watches:
            if w.namespace != namespace and w.namespace != "*":
                continue
            if not fnmatch.fnmatchcase(key, w.key_pattern):
                continue
            try:
                w.handler(new_entry, old_entry)
            except Exception as exc:
                _LOG.warning("watch handler error for %s: %s", key, exc)


def get_statestore() -> StateStore:
    return StateStore.get_instance()


__all__ = [
    "StateBackend",
    "MemoryBackend",
    "FileBackend",
    "SQLiteBackend",
    "StateEntry",
    "StateStore",
    "WatchSubscription",
    "get_statestore",
]
