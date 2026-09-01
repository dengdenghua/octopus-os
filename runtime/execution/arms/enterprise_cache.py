"""Enterprise Arm 本地决策层(Ganglion).

Ganglion = Arm 自带的本地决策层,不是独立节层。
EnterpriseDecisionCache 缓存企业数据(任务/审批/人员),
当 echo-enterprise 不可达时提供过期数据,实现断联自治。

与 mobile Ganglion(独立进程 + 本地 ReAct loop)不同,
enterprise Ganglion 是母体内的轻量缓存层:
- 后台 daemon 定期同步企业数据
- skill 调用优先读缓存(减少 HTTP 往返)
- 服务断联时返回 stale-but-available 数据
- 恢复后自动刷新
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any

_LOG = logging.getLogger(__name__)

_DEFAULT_URL = "http://127.0.0.1:3100"
_DEFAULT_TTL = 300  # 5 min
_DEFAULT_SYNC_INTERVAL = 60  # 1 min
_TIMEOUT_S = 5.0


# ─── helpers (与 enterprise_skills 同构,零新依赖) ──────────


def _base_url() -> str:
    raw = (os.environ.get("ECHO_ENTERPRISE_URL") or "").strip()
    return (raw or _DEFAULT_URL).rstrip("/")


def _api_token() -> str | None:
    return (os.environ.get("ECHO_ENTERPRISE_TOKEN") or "").strip() or None


def _request(
    path: str,
    *,
    timeout: float = _TIMEOUT_S,
) -> dict[str, Any] | None:
    """Best-effort GET; returns None on any failure."""
    headers: dict[str, str] = {}
    token = _api_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        _base_url() + path,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310  # nosec B310 — audited HTTP endpoint
            body = resp.read().decode("utf-8", "replace")
        return json.loads(body) if body.strip() else {}
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None


# ─── EnterpriseDecisionCache ───────────────────────────────


class EnterpriseDecisionCache:
    """Enterprise Arm 的本地决策缓存(Ganglion 层).

    后台 daemon 定期从 echo-enterprise 同步任务/审批/人员数据。
    skill 调用时优先读缓存,减少 HTTP 往返;服务断联时返回
    stale-but-available 数据,实现断联自治。
    """

    def __init__(
        self,
        *,
        ttl: int = _DEFAULT_TTL,
        sync_interval: int = _DEFAULT_SYNC_INTERVAL,
    ) -> None:
        self._ttl = ttl
        self._sync_interval = sync_interval
        self._lock = threading.Lock()
        self._cache: dict[str, Any] = {}
        self._last_sync: dict[str, float] = {}
        self._running = False
        self._thread: threading.Thread | None = None
        self._service_ok: bool = False

    # ─── lifecycle ──────────────────────────────────────

    def start(self) -> None:
        """启动后台同步 daemon."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._sync_loop,
            name="enterprise-ganglion-sync",
            daemon=True,
        )
        self._thread.start()
        _LOG.info("EnterpriseDecisionCache(Ganglion) sync daemon started")

    def stop(self) -> None:
        """停止后台同步 daemon."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        _LOG.info("EnterpriseDecisionCache(Ganglion) sync daemon stopped")

    # ─── public query ───────────────────────────────────

    def get_tasks(
        self,
        project_id: str,
        status: str = "",
        limit: int = 20,
    ) -> dict[str, Any] | None:
        """查询缓存中的任务列表;无缓存返回 None."""
        key = f"tasks:{project_id}:{status}:{limit}"
        return self._get(key)

    def get_approvals(
        self,
        status: str = "",
        limit: int = 20,
    ) -> dict[str, Any] | None:
        """查询缓存中的审批列表;无缓存返回 None."""
        key = f"approvals:{status}:{limit}"
        return self._get(key)

    def get_persons(self, limit: int = 50) -> dict[str, Any] | None:
        """查询缓存中的人员列表;无缓存返回 None."""
        key = f"persons:{limit}"
        return self._get(key)

    def is_service_ok(self) -> bool:
        """企业版服务是否可达."""
        return self._service_ok

    @property
    def cache_size(self) -> int:
        """当前缓存条目数(诊断用)."""
        with self._lock:
            return len(self._cache)

    # ─── internal ───────────────────────────────────────

    def _get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            return self._cache.get(key)

    def _put(self, key: str, data: dict[str, Any]) -> None:
        with self._lock:
            self._cache[key] = data
            self._last_sync[key] = time.monotonic()

    def _is_fresh(self, key: str) -> bool:
        with self._lock:
            ts = self._last_sync.get(key, 0.0)
            return (time.monotonic() - ts) < self._ttl

    def _sync_loop(self) -> None:
        """后台同步 daemon 主循环."""
        while self._running:
            try:
                self._sync_once()
            except Exception:  # noqa: BLE001
                _LOG.debug("EnterpriseDecisionCache sync error", exc_info=True)
            # sleep in small increments so stop() can interrupt
            deadline = time.monotonic() + self._sync_interval
            while self._running and time.monotonic() < deadline:
                time.sleep(1.0)

    def _sync_once(self) -> None:
        """执行一次全量同步."""
        # ── 1. health check ──
        health = _request("/health")
        self._service_ok = health is not None

        if not self._service_ok:
            _LOG.debug("enterprise service unreachable, skip sync")
            return

        # ── 2. sync default-size lists ──
        # tasks per project 需要已知 project_id,此处只同步通用列表
        resp = _request("/approvals?skip=0&limit=20")
        if resp is not None:
            data = resp.get("data", []) if isinstance(resp, dict) else []
            self._put(
                "approvals::20",
                {
                    "ok": True,
                    "available": True,
                    "count": resp.get("total", len(data)) if isinstance(resp, dict) else len(data),
                    "approvals": data,
                },
            )

        resp = _request("/persons?skip=0&limit=50")
        if resp is not None:
            data = resp.get("data", []) if isinstance(resp, dict) else []
            self._put(
                "persons:50",
                {
                    "ok": True,
                    "available": True,
                    "count": resp.get("total", len(data)) if isinstance(resp, dict) else len(data),
                    "persons": data,
                },
            )

        _LOG.debug("EnterpriseDecisionCache synced (%d entries)", self.cache_size)
