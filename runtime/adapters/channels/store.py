from __future__ import annotations

import json
import threading
from pathlib import Path
from uuid import uuid4


class ThreadConversationStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self._memory: dict[tuple[str, str], str] = {}
        self._lock = threading.Lock()
        self._path: Path | None = Path(path) if path is not None else None
        if self._path is not None and self._path.exists():
            self._load()

    def get(self, channel_id: str, thread_id: str) -> str | None:
        with self._lock:
            return self._memory.get((channel_id, thread_id))

    def get_or_create(self, channel_id: str, thread_id: str) -> str:
        if not channel_id or not thread_id:
            raise ValueError("channel_id and thread_id must be non-empty")
        key = (channel_id, thread_id)
        with self._lock:
            if key in self._memory:
                return self._memory[key]
            conv_id = uuid4().hex
            self._memory[key] = conv_id
            if self._path is not None:
                self._append(channel_id, thread_id, conv_id)
            return conv_id

    def delete(self, channel_id: str, thread_id: str) -> bool:
        key = (channel_id, thread_id)
        with self._lock:
            if key not in self._memory:
                return False
            del self._memory[key]
            if self._path is not None:
                rec = {
                    "channel_id": channel_id,
                    "thread_id": thread_id,
                    "conversation_id": None,  # tombstone
                }
                with self._path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec) + "\n")
            return True

    def __len__(self) -> int:
        with self._lock:
            return len(self._memory)

    def _append(
        self,
        channel_id: str,
        thread_id: str,
        conv_id: str,
    ) -> None:
        rec = {
            "channel_id": channel_id,
            "thread_id": thread_id,
            "conversation_id": conv_id,
        }
        assert self._path is not None
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

    def _load(self) -> None:
        assert self._path is not None
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError:
            return
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            cid = obj.get("channel_id")
            tid = obj.get("thread_id")
            conv = obj.get("conversation_id")
            if not isinstance(cid, str) or not isinstance(tid, str):
                continue
            key = (cid, tid)
            if conv is None:
                self._memory.pop(key, None)
            elif isinstance(conv, str):
                self._memory[key] = conv
