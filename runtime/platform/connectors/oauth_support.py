"""MCP 插件「网页 OAuth 授权」支持探测(带磁盘缓存 + 后台并发预热)。

判断一个 MCP 型插件能不能跳网页登录授权,标准是:它的 server 是否实现了
RFC 8414/9728 的 ``.well-known`` OAuth 元数据(oauth-discovery 能找到
authorize/token 端点)。探测要访问外部服务,所以:
  - 结果按 server url 缓存到内存 + ``~/.echo/capabilities/oauth_supported.json``,
    TTL 24h(磁盘缓存跨进程/重启保留);
  - 未命中的 url 由后台线程并发探测,不阻塞列表返回;
  - 列表先返回缓存命中结果,未命中返回 ``None``(前端不显示标识,探测完成
    后刷新即出现)。
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

CACHE_FILE = Path.home() / ".echo" / "capabilities" / "oauth_supported.json"
CACHE_TTL_SECONDS = 24 * 3600
_MAX_WORKERS = 8

_lock = threading.Lock()
_memory: dict[str, tuple[float, bool]] = {}
_prewarming: set[str] = set()
_loaded = False


def _load() -> None:
    global _loaded, _memory
    if _loaded:
        return
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        _memory = {
            str(k): (float(v.get("ts", 0)), bool(v.get("ok", False))) for k, v in data.items()
        }
    except Exception:  # noqa: BLE001 - 缓存损坏按空处理
        _memory = {}
    _loaded = True


def _save() -> None:
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {k: {"ts": round(t, 2), "ok": ok} for k, (t, ok) in _memory.items()}
        CACHE_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001 - 写盘失败不阻断
        pass


def supported(url: str | None) -> bool | None:
    """返回缓存内的 OAuth 支持判定;未探测过返回 None(未知)。"""
    if not url:
        return None
    _load()
    with _lock:
        hit = _memory.get(url)
    if hit is not None and time.time() - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]
    return None


def _probe(url: str) -> bool:
    try:
        from runtime.adapters.mcp_client import oauth_discovery

        return oauth_discovery.discover(url, timeout=6) is not None
    except Exception:  # noqa: BLE001 - 探测失败视为不支持
        return False


def prewarm(urls: list[str]) -> None:
    """后台并发探测尚未缓存的 url,结果写内存 + 磁盘。不阻塞调用方。"""
    _load()
    with _lock:
        now = time.time()
        todo = [
            u
            for u in urls
            if u
            and (u not in _memory or now - _memory[u][0] >= CACHE_TTL_SECONDS)
            and u not in _prewarming
        ]
        _prewarming.update(todo)
    if not todo:
        return

    def _run() -> None:
        try:
            results: list[bool] = []
            with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as ex:
                results = list(ex.map(_probe, todo))
            with _lock:
                stamp = time.time()
                for u, ok in zip(todo, results, strict=True):
                    _memory[u] = (stamp, ok)
            _save()
        finally:
            with _lock:
                for u in todo:
                    _prewarming.discard(u)

    threading.Thread(target=_run, daemon=True).start()


def annotate(item: dict[str, Any]) -> dict[str, Any]:
    """给 capability item 加 ``oauth_supported`` / ``oauth_provider`` 字段。

    网页登录支持分两条路径:
      1. 服务商直连 OAuth App(GitHub / GitLab 等 WorkBuddy ``server-side``
         连接器)—— 映射命中即视为支持(用户配置自己的 OAuth App 后即可跳转);
      2. MCP server 的 ``.well-known`` OAuth 元数据(Linear 等)—— 按探测缓存。
    """
    item = dict(item)
    from runtime.adapters.mcp_client.oauth_providers import get_provider_for_capability

    prov = get_provider_for_capability(item)
    if prov is not None:
        item["oauth_supported"] = True
        item["oauth_provider"] = prov.id
        item["oauth_provider_name"] = prov.name
        return item
    servers = item.get("mcp_servers") or []
    url = next(
        (str(s.get("url", "")) for s in servers if isinstance(s, dict) and s.get("url")),
        None,
    )
    item["oauth_supported"] = supported(url)
    item["oauth_provider"] = None
    return item
