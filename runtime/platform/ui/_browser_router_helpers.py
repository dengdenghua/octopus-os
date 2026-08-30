"""Private helpers and state backend for the browser router.

Pure structural split of ``_browser_router``: the module-level profile
helpers and the ``_BrowserBackend`` class (which holds the mutable
browser-session / relay state and its helper methods). No logic changes.

Logical responsibilities were split into ``_browser_helper_*.py``
sub-modules and re-exported here; ``_BrowserBackend`` inherits the
relay / discovery / session mixins so the public API is unchanged.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from runtime.platform.runtime_policy.browser_sessions import BrowserSessionCenter
from runtime.platform.ui._browser_helper_discovery import _DiscoveryBackendMixin
from runtime.platform.ui._browser_helper_nav import _NavigationBackendMixin
from runtime.platform.ui._browser_helper_profile import (
    _SESSION_SENTINEL_NAME,
    mark_session_active,
    mark_session_closed,
    secure_profile_dir,
)
from runtime.platform.ui._browser_helper_relay import _RelayBackendMixin
from runtime.platform.ui._browser_helper_session import _SessionBackendMixin

__all__ = [
    "_SESSION_SENTINEL_NAME",
    "mark_session_active",
    "mark_session_closed",
    "secure_profile_dir",
]


class _BrowserBackend(
    _RelayBackendMixin,
    _DiscoveryBackendMixin,
    _SessionBackendMixin,
    _NavigationBackendMixin,
):
    """Encapsulates the mutable browser-session / relay state and helpers."""

    def __init__(
        self,
        *,
        browser_config_state: dict[str, Any],
        browser_policy_path: Path,
        browser_session_center: BrowserSessionCenter,
    ) -> None:
        self.browser_config_state = browser_config_state
        self.browser_policy_path = browser_policy_path
        self.browser_session_center = browser_session_center
        self.browser_sessions = browser_session_center.sessions
        self.browser_relay_state: dict[str, Any] = {
            "connected": False,
            "extension_version": "",
            "last_seen": 0,
            "active_tab": None,
            "recent_human_activity": [],
            "pending_commands": [],
            "command_results": {},
            "control_lease": None,
            "human_interrupt": None,
            "push_connections": 0,
        }
        self.browser_relay_queue_lock = threading.Lock()
        self.relay_read_only_actions = {"extract", "aria", "state", "screenshot", "wait"}
        self._load_persisted_browser_policy()
