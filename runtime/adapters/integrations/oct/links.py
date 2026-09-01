"""oct 账号绑定存储:agent actor → oct 网关 JWT + 积分快照。

``oct_token`` 存储 oct 网关签发的 JWT（用于后续
带 ``Authorization: Bearer`` 调网关 /account、/billing、/v1/chat/completions)。
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from runtime.platform.io import atomic_write_json

logger = logging.getLogger(__name__)

_STORE_FILENAME = "oct_links.json"


def _default_store_path() -> Path:
    project_root = Path(__file__).resolve().parents[4]
    data_dir = os.environ.get("ECHO_DATA_DIR")
    if data_dir:
        base = Path(data_dir)
        base.mkdir(parents=True, exist_ok=True)
        return base / _STORE_FILENAME
    home_dir = os.environ.get("ECHO_HOME")
    if home_dir:
        base = Path(home_dir)
        base.mkdir(parents=True, exist_ok=True)
        return base / _STORE_FILENAME
    base = project_root / ".echo"
    base.mkdir(parents=True, exist_ok=True)
    return base / _STORE_FILENAME


@dataclass
class OctLink:
    echo_user_id: str  # agent 本地 actor_id(如 "oct:user@example.com")
    oct_user_id: str  # oct 网关侧 userId(如 "u_xxx")
    oct_token: str  # oct 网关签发的 JWT(调网关用)
    email: str | None = None  # 登录邮箱(展示用)
    linked_at: float = 0.0
    last_synced_at: float | None = None
    credits_snapshot: dict[str, Any] = field(default_factory=dict)
    token_invalid: bool = False
    token_invalid_reason: str | None = None


class OctLinkStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path else _default_store_path()
        self._lock = threading.Lock()
        self._cache: dict[str, OctLink] | None = None

    def _load(self) -> dict[str, OctLink]:
        if self._cache is not None:
            return self._cache
        if not self._path.exists():
            self._cache = {}
            return self._cache
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("oct link store unreadable (%s) · starting empty", exc)
            self._cache = {}
            return self._cache
        cache: dict[str, OctLink] = {}
        for key, blob in (raw or {}).items():
            try:
                cache[key] = OctLink(**blob)
            except Exception:  # noqa: BLE001
                logger.warning("oct link for %s malformed · dropping", key)
        self._cache = cache
        return cache

    def _flush(self) -> None:
        assert self._cache is not None
        payload = {k: asdict(v) for k, v in self._cache.items()}
        atomic_write_json(self._path, payload)
        # 含网关 bearer JWT(默认 30 天有效),限本用户可读
        with contextlib.suppress(OSError):
            os.chmod(self._path, 0o600)

    def get(self, echo_user_id: str) -> OctLink | None:
        with self._lock:
            return self._load().get(echo_user_id)

    def put(self, link: OctLink) -> None:
        with self._lock:
            cache = self._load()
            cache[link.echo_user_id] = link
            self._flush()

    def delete(self, echo_user_id: str) -> bool:
        with self._lock:
            cache = self._load()
            if echo_user_id not in cache:
                return False
            cache.pop(echo_user_id)
            self._flush()
            return True

    def update_credits(
        self,
        echo_user_id: str,
        credits: dict[str, Any],
        *,
        now: float,
    ) -> OctLink | None:
        with self._lock:
            cache = self._load()
            link = cache.get(echo_user_id)
            if link is None:
                return None
            link.credits_snapshot = credits
            link.last_synced_at = now
            link.token_invalid = False
            link.token_invalid_reason = None
            self._flush()
            return link

    def mark_token_invalid(
        self,
        echo_user_id: str,
        reason: str | None,
    ) -> OctLink | None:
        with self._lock:
            cache = self._load()
            link = cache.get(echo_user_id)
            if link is None:
                return None
            link.token_invalid = True
            link.token_invalid_reason = reason
            self._flush()
            return link

    def all_actor_ids(self) -> list[str]:
        with self._lock:
            return list(self._load().keys())
