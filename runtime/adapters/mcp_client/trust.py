from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

from runtime.platform.io import atomic_write_json

_log = logging.getLogger("runtime.adapters.mcp_client.trust")


# ═══════════════════════════════════════════════════════════
# Data
# ═══════════════════════════════════════════════════════════


@dataclass
class TrustEntry:
    server_name: str
    approved: bool
    added_ts: float
    tool_digest: str = ""
    note: str = ""


def _tool_digest(tool_names: Iterable[str]) -> str:
    """Stable hash of the tool-name set · order-insensitive so a
    server re-ordering its tool list doesn't force re-approval."""
    sorted_names = sorted(set(tool_names))
    h = hashlib.blake2b(digest_size=8)
    for n in sorted_names:
        h.update(n.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


# ═══════════════════════════════════════════════════════════
# Store
# ═══════════════════════════════════════════════════════════


def _trust_store_path(tenant_id: str | None = None) -> Path:
    """Return the approval file for the legacy or tenant-scoped store."""
    home = os.environ.get("ECHO_HOME")
    base = Path(home) if home else (Path.home() / ".echo")
    if tenant_id:
        digest = hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()[:32]
        base = base / "tenants" / digest
    base.mkdir(parents=True, exist_ok=True)
    return base / "mcp_trust.json"


class MCPTrustStore:
    """Thread-safe JSON-backed approval registry.

    API kept minimal so it stays embeddable in the MCP router
    and the bridge without forcing a service layer:

    * ``is_approved(server_name, tool_names=None)`` — check
    * ``approve(server_name, tool_names, note="")``  — grant
    * ``revoke(server_name)``                        — deny
    * ``list_all()``                                  — UI read
    """

    def __init__(
        self,
        path: Path | str | None = None,
        *,
        tenant_id: str | None = None,
    ) -> None:
        self.tenant_id = str(tenant_id).strip() if tenant_id else None
        self._path = Path(path) if path else _trust_store_path(self.tenant_id)
        self._lock = threading.RLock()
        self._entries: dict[str, TrustEntry] = {}
        self._load()

    # ─── IO ─────────────────────────────────────────────────

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _log.warning(
                "mcp_trust.json unreadable (%s: %s) · starting empty",
                type(exc).__name__,
                exc,
            )
            return
        entries = raw.get("entries") or []
        for item in entries:
            try:
                e = TrustEntry(
                    server_name=str(item["server_name"]),
                    approved=bool(item.get("approved", False)),
                    added_ts=float(item.get("added_ts", 0.0)),
                    tool_digest=str(item.get("tool_digest", "")),
                    note=str(item.get("note", "")),
                )
                self._entries[e.server_name] = e
            except (KeyError, TypeError, ValueError):
                continue

    def _save(self) -> None:
        payload = {
            "version": 1,
            "entries": [asdict(e) for e in self._entries.values()],
        }
        atomic_write_json(self._path, payload)

    # ─── Public API ─────────────────────────────────────────

    def is_approved(
        self,
        server_name: str,
        tool_names: Iterable[str] | None = None,
    ) -> bool:
        """True iff the server is approved AND (if tool_names given)
        the current tool set matches the digest at approval time."""
        with self._lock:
            entry = self._entries.get(server_name)
            if entry is None or not entry.approved:
                return False
            if tool_names is None:
                return True
            if not entry.tool_digest:
                # Legacy entry · no digest pinned · treat as approved.
                return True
            return _tool_digest(tool_names) == entry.tool_digest

    def approve(
        self,
        server_name: str,
        tool_names: Iterable[str] = (),
        note: str = "",
    ) -> TrustEntry:
        with self._lock:
            entry = TrustEntry(
                server_name=server_name,
                approved=True,
                added_ts=time.time(),
                tool_digest=_tool_digest(tool_names),
                note=note,
            )
            self._entries[server_name] = entry
            self._save()
            return entry

    def revoke(self, server_name: str) -> bool:
        with self._lock:
            entry = self._entries.get(server_name)
            if entry is None:
                return False
            entry.approved = False
            self._save()
            return True

    def forget(self, server_name: str) -> bool:
        """Delete the entry entirely · used when removing a server."""
        with self._lock:
            if server_name not in self._entries:
                return False
            del self._entries[server_name]
            self._save()
            return True

    def list_all(self) -> list[TrustEntry]:
        with self._lock:
            return [
                TrustEntry(**asdict(e))  # defensive copy
                for e in self._entries.values()
            ]


# ═══════════════════════════════════════════════════════════
# Module-global convenience · one store per process
# ═══════════════════════════════════════════════════════════

_GLOBAL_STORES: dict[str, MCPTrustStore] = {}
_GLOBAL_STORE_LOCK = threading.Lock()


def get_trust_store(tenant_id: str | None = None) -> MCPTrustStore:
    """Return the approval store isolated to ``tenant_id``.

    The no-argument form preserves the local CLI/legacy store.  Shared HTTP
    deployments must pass the server-resolved tenant; files are partitioned
    by a one-way tenant digest and are never selected from request input.
    """
    key = str(tenant_id).strip() if tenant_id else "__legacy__"
    if key not in _GLOBAL_STORES:
        with _GLOBAL_STORE_LOCK:
            if key not in _GLOBAL_STORES:
                _GLOBAL_STORES[key] = MCPTrustStore(
                    tenant_id=None if key == "__legacy__" else key,
                )
    return _GLOBAL_STORES[key]


def reset_trust_store_for_tests() -> None:
    """Drop the singleton · tests use this to isolate from user's
    real ~/.echo directory (pair with monkeypatching
    $ECHO_HOME)."""
    with _GLOBAL_STORE_LOCK:
        _GLOBAL_STORES.clear()


__all__ = [
    "TrustEntry",
    "MCPTrustStore",
    "get_trust_store",
    "reset_trust_store_for_tests",
]
