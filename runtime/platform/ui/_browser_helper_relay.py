"""Relay host / site-policy helpers for the browser router backend.

Pure structural split of ``_browser_router_helpers``: the relay
host-pattern normalization, heartbeat/result handling, site-policy
decisions, control snapshots, human-interrupt recording and lease
creation. Exposed as ``_RelayBackendMixin`` — ``_BrowserBackend``
inherits it. No logic changes.
"""

from __future__ import annotations

import urllib.parse
from typing import Any

from runtime.platform.io import atomic_write_json, read_json_with_backup


class _RelayBackendMixin:
    """Relay host / policy helpers shared by the browser backend."""

    def _normalize_relay_host_patterns(self, value: Any) -> list[str]:
        raw_items: list[Any]
        if isinstance(value, str):
            raw_items = [item.strip() for item in value.split(",")]
        elif isinstance(value, (list, tuple, set)):
            raw_items = list(value)
        else:
            raw_items = []
        out: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            text = str(item or "").strip().lower()
            if not text:
                continue
            if "://" in text:
                parsed = urllib.parse.urlparse(text)
                text = parsed.hostname or ""
            else:
                text = text.split("/", 1)[0].split("?", 1)[0]
                if ":" in text and not text.startswith("["):
                    text = text.split(":", 1)[0]
                text = text.strip("[]")
            if not text:
                continue
            text = "*." + text[2:].strip(".") if text.startswith("*.") else text.strip(".")
            if text and text not in seen:
                seen.add(text)
                out.append(text)
        return out[:200]

    def _apply_relay_heartbeat(self, body: dict[str, Any]) -> list[dict[str, Any]]:
        self.browser_relay_state["connected"] = True
        self.browser_relay_state["last_seen"] = self._now_ts()
        self.browser_relay_state["extension_version"] = str(
            body.get("extension_version") or "local-dev"
        )
        active_tab = body.get("active_tab")
        if isinstance(active_tab, dict):
            self.browser_relay_state["active_tab"] = {
                "id": active_tab.get("id"),
                "url": active_tab.get("url"),
                "title": active_tab.get("title"),
            }
        recent_human_activity = body.get("recent_human_activity")
        if isinstance(recent_human_activity, list):
            self.browser_relay_state["recent_human_activity"] = [
                item for item in recent_human_activity[-20:] if isinstance(item, dict)
            ]
        control_event = body.get("control_event")
        if isinstance(control_event, dict):
            event_type = str(control_event.get("type") or "").strip()
            if event_type == "human_interrupt":
                self._record_relay_interrupt(
                    reason=str(control_event.get("reason") or "human_activity"),
                    source=str(control_event.get("source") or "chrome_extension"),
                    detail={
                        "activity": control_event.get("activity"),
                        "lease": control_event.get("lease"),
                    },
                )
            elif event_type == "clear_interrupt":
                self._clear_relay_interrupt()
        return self._drain_relay_commands()

    def _drain_relay_commands(self) -> list[dict[str, Any]]:
        with self.browser_relay_queue_lock:
            pending = list(self.browser_relay_state.get("pending_commands") or [])
            self.browser_relay_state["pending_commands"] = []
            return pending

    def _apply_relay_result(self, body: dict[str, Any]) -> dict[str, Any]:
        command_id = str(body.get("id") or "").strip()
        if not command_id:
            raise ValueError("id is required")
        self.browser_relay_state["last_seen"] = self._now_ts()
        active_tab = body.get("active_tab")
        if isinstance(active_tab, dict):
            self.browser_relay_state["active_tab"] = {
                "id": active_tab.get("id"),
                "url": active_tab.get("url"),
                "title": active_tab.get("title"),
            }
        result = body.get("result")
        if not isinstance(result, dict):
            result = {"ok": False, "error": "missing relay result"}
        result.setdefault("ok", True)
        result.setdefault("id", command_id)
        if (self.browser_relay_state.get("control_lease") or {}).get("command_id") == command_id:
            self.browser_relay_state["control_lease"] = None
        result["control"] = self._relay_control_snapshot()
        self.browser_relay_state.setdefault("command_results", {})[command_id] = result
        return result

    def _browser_policy_payload(self) -> dict[str, Any]:
        return {
            "schema": "echo.browser_relay_site_policy.v1",
            "relay_allowed_hosts": list(self.browser_config_state.get("relay_allowed_hosts") or []),
            "relay_blocked_hosts": list(self.browser_config_state.get("relay_blocked_hosts") or []),
            "relay_require_allowlist": bool(
                self.browser_config_state.get("relay_require_allowlist"),
            ),
        }

    def _load_persisted_browser_policy(self) -> None:
        payload = read_json_with_backup(self.browser_policy_path, default={})
        if not isinstance(payload, dict):
            return
        if "relay_allowed_hosts" in payload:
            self.browser_config_state["relay_allowed_hosts"] = self._normalize_relay_host_patterns(
                payload.get("relay_allowed_hosts")
            )
        if "relay_blocked_hosts" in payload:
            self.browser_config_state["relay_blocked_hosts"] = self._normalize_relay_host_patterns(
                payload.get("relay_blocked_hosts")
            )
        if "relay_require_allowlist" in payload:
            self.browser_config_state["relay_require_allowlist"] = bool(
                payload.get("relay_require_allowlist"),
            )

    def _persist_browser_policy(self) -> None:
        atomic_write_json(self.browser_policy_path, self._browser_policy_payload())

    def _relay_host_from_url(self, url: str) -> str:
        try:
            return (urllib.parse.urlparse(url).hostname or "").strip(".").lower()
        except ValueError:
            return ""

    def _relay_host_matches(self, host: str, patterns: list[str]) -> bool:
        if not host:
            return False
        normalized = host.strip(".").lower()
        for pattern in patterns:
            item = pattern.strip().lower()
            if item == "*":
                return True
            if item.startswith("*."):
                suffix = item[2:]
                if normalized == suffix or normalized.endswith(f".{suffix}"):
                    return True
                continue
            if normalized == item:
                return True
        return False

    def _relay_policy_snapshot(
        self,
        *,
        decision: str = "",
        reason: str = "",
        target_host: str = "",
        target_url: str = "",
    ) -> dict[str, Any]:
        return {
            "schema": "echo.browser_relay_site_policy.v1",
            "decision": decision,
            "reason": reason,
            "target_host": target_host,
            "target_url": target_url,
            "persisted": self.browser_policy_path.exists(),
            "policy_path": str(self.browser_policy_path),
            "allowed_hosts": list(self.browser_config_state.get("relay_allowed_hosts") or []),
            "blocked_hosts": list(self.browser_config_state.get("relay_blocked_hosts") or []),
            "require_allowlist": bool(
                self.browser_config_state.get("relay_require_allowlist"),
            ),
        }

    def _relay_site_policy_decision(self, action: str, body: dict[str, Any]) -> dict[str, Any]:
        active_tab = self.browser_relay_state.get("active_tab")
        active_tab = active_tab if isinstance(active_tab, dict) else {}
        target_url = str(body.get("url") or active_tab.get("url") or "").strip()
        target_host = self._relay_host_from_url(target_url)
        if action == "navigate" and not target_host:
            return self._relay_policy_snapshot(
                decision="block",
                reason="missing_or_invalid_target_url",
                target_url=target_url,
            )
        blocked_hosts = list(self.browser_config_state.get("relay_blocked_hosts") or [])
        if self._relay_host_matches(target_host, blocked_hosts):
            return self._relay_policy_snapshot(
                decision="block",
                reason="host_blocked",
                target_host=target_host,
                target_url=target_url,
            )
        require_allowlist = bool(self.browser_config_state.get("relay_require_allowlist"))
        allowed_hosts = list(self.browser_config_state.get("relay_allowed_hosts") or [])
        if require_allowlist and not self._relay_host_matches(target_host, allowed_hosts):
            return self._relay_policy_snapshot(
                decision="block",
                reason="host_not_allowed",
                target_host=target_host,
                target_url=target_url,
            )
        return self._relay_policy_snapshot(
            decision="allow",
            reason="host_allowed" if target_host else "no_target_host",
            target_host=target_host,
            target_url=target_url,
        )

    def _relay_active_tab_snapshot(self) -> dict[str, Any] | None:
        active_tab = self.browser_relay_state.get("active_tab")
        if not isinstance(active_tab, dict):
            return None
        return {
            "id": active_tab.get("id"),
            "url": str(active_tab.get("url") or ""),
            "title": str(active_tab.get("title") or ""),
        }

    def _relay_control_lease(self) -> dict[str, Any] | None:
        lease = self.browser_relay_state.get("control_lease")
        if not isinstance(lease, dict):
            return None
        expires_at = int(lease.get("expires_at") or 0)
        if expires_at and expires_at <= self._now_ts():
            self.browser_relay_state["control_lease"] = None
            return None
        return lease

    def _relay_control_snapshot(self) -> dict[str, Any]:
        lease = self._relay_control_lease()
        interrupt = self.browser_relay_state.get("human_interrupt")
        interrupt = interrupt if isinstance(interrupt, dict) else None
        if interrupt:
            mode = "interrupted"
        elif lease:
            mode = "agent_active"
        else:
            mode = "idle"
        return {
            "schema": "echo.browser_relay_control.v1",
            "mode": mode,
            "lease": lease,
            "human_interrupt": interrupt,
            "blocked": bool(interrupt),
            "pending_commands": len(self.browser_relay_state.get("pending_commands") or []),
        }

    def _record_relay_interrupt(
        self,
        *,
        reason: str,
        source: str,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        interrupt = {
            "schema": "echo.browser_relay_human_interrupt.v1",
            "reason": reason,
            "source": source,
            "at": self._now_ts(),
            "active_tab": self._relay_active_tab_snapshot(),
            "detail": detail or {},
        }
        results = self.browser_relay_state.setdefault("command_results", {})
        for item in self.browser_relay_state.get("pending_commands") or []:
            command_id = str(item.get("id") or "").strip()
            if command_id:
                results[command_id] = {
                    "ok": False,
                    "id": command_id,
                    "error": f"browser relay interrupted: {reason}",
                    "control": {
                        "schema": "echo.browser_relay_control.v1",
                        "mode": "interrupted",
                        "human_interrupt": interrupt,
                        "blocked": True,
                    },
                }
        active_lease = self.browser_relay_state.get("control_lease")
        if isinstance(active_lease, dict):
            command_id = str(active_lease.get("command_id") or "").strip()
            if command_id:
                results[command_id] = {
                    "ok": False,
                    "id": command_id,
                    "error": f"browser relay interrupted: {reason}",
                    "control": {
                        "schema": "echo.browser_relay_control.v1",
                        "mode": "interrupted",
                        "human_interrupt": interrupt,
                        "blocked": True,
                    },
                }
        self.browser_relay_state["human_interrupt"] = interrupt
        self.browser_relay_state["control_lease"] = None
        self.browser_relay_state["pending_commands"] = []
        return interrupt

    def _clear_relay_interrupt(self) -> None:
        self.browser_relay_state["human_interrupt"] = None

    def _make_relay_lease(
        self,
        *,
        command_id: str,
        action: str,
        body: dict[str, Any],
        site_policy: dict[str, Any],
    ) -> dict[str, Any]:
        timeout_seconds = float(body.get("timeout_seconds") or 8)
        lease_seconds = float(body.get("lease_seconds") or max(5.0, timeout_seconds + 2.0))
        lease_seconds = max(3.0, min(60.0, lease_seconds))
        active_tab = self._relay_active_tab_snapshot()
        selected_tab_id = str(body.get("target_tab_id") or "").strip()
        if selected_tab_id:
            # The target is an operator-selected capability, not a hint. Build
            # the lease around it so the extension fails closed instead of
            # silently acting on a different active tab.
            active_tab = {
                "id": selected_tab_id,
                "url": str(body.get("target_tab_url") or ""),
                "title": str(body.get("target_tab_title") or ""),
            }
        return {
            "schema": "echo.browser_relay_tab_lease.v1",
            "id": f"lease-{command_id}",
            "command_id": command_id,
            "owner": "agent",
            "action": action,
            "read_only": action in self.relay_read_only_actions,
            "issued_at": self._now_ts(),
            "expires_at": self._now_ts() + int(lease_seconds),
            "tab": active_tab,
            "target_source": "operator_selection" if selected_tab_id else "active_tab",
            "require_same_tab": active_tab is not None,
            "require_same_url": bool(active_tab and active_tab.get("url")),
            "site_policy": site_policy,
        }
