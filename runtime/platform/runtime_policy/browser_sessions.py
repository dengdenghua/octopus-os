"""Shared browser session state for UI preview and automation surfaces."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

_SETTING_KEYS = {
    "browser_regression_enabled",
    "browser_regression_mode",
    "browser_regression_preview_url",
    "browser_regression_requires_visible_cursor",
    "viewport_width",
    "viewport_height",
}

_DEFAULT_VIEWPORT_WIDTH = 1440
_DEFAULT_VIEWPORT_HEIGHT = 900
_MIN_VIEWPORT_WIDTH = 240
_MIN_VIEWPORT_HEIGHT = 160
_MAX_VIEWPORT_SIZE = 4096


class BrowserSessionCenter:
    """Small in-process registry for browser automation sessions.

    The router owns heavyweight runtime objects such as Playwright pages. This
    class owns the durable state shape and the API-facing snapshots, so browser
    preview, regression, and live automation do not each invent their own view
    of whether a session exists.
    """

    def __init__(
        self,
        config_state: dict[str, Any],
        *,
        now: Callable[[], int] | None = None,
    ) -> None:
        self._config_state = config_state
        self._now = now or (lambda: int(time.time()))
        self.sessions: dict[str, dict[str, Any]] = {}

    def now(self) -> int:
        return self._now()

    def ensure(
        self,
        session_id: str,
        *,
        headless: bool | None = None,
        project_id: str | None = None,
        profile_id: str | None = None,
    ) -> dict[str, Any]:
        normalized = self._normalize_session_id(session_id)
        normalized_project = self._normalize_project_id(project_id or normalized)
        normalized_profile = self._normalize_profile_id(profile_id or normalized_project)
        now = self.now()
        session = self.sessions.get(normalized)
        if session is None:
            session = {
                "session_id": normalized,
                "project_id": normalized_project,
                "profile_id": normalized_profile,
                "automation_mode": "browser_context",
                "uses_system_mouse": False,
                "desktop_lease_required": False,
                "is_launched": True,
                "created_at": now,
                "last_activity": now,
                "action_count": 0,
                "headless": self._config_state["headless"] if headless is None else bool(headless),
                "viewport_width": self._default_viewport_width(),
                "viewport_height": self._default_viewport_height(),
                "current_url": "",
                "current_title": "",
                "history": [],
                "history_index": -1,
                "actions": [],
                "mode": "mock",
                "browser_regression_enabled": False,
                "browser_regression_mode": "off",
                "browser_regression_preview_url": "",
                "browser_regression_requires_visible_cursor": False,
                "playwright": None,
                "browser": None,
                "context": None,
                "page": None,
                "profile_dir": "",
            }
            self.sessions[normalized] = session
        else:
            session["is_launched"] = True
            session["last_activity"] = now
            if headless is not None:
                session["headless"] = bool(headless)
            session.setdefault("project_id", normalized_project)
            session.setdefault("profile_id", normalized_profile)
            session.setdefault("automation_mode", "browser_context")
            session.setdefault("uses_system_mouse", False)
            session.setdefault("desktop_lease_required", False)
            session.setdefault("profile_dir", "")
            session.setdefault("viewport_width", self._default_viewport_width())
            session.setdefault("viewport_height", self._default_viewport_height())
        return session

    def get(self, session_id: str) -> dict[str, Any] | None:
        return self.sessions.get(self._normalize_session_id(session_id))

    def pop(self, session_id: str) -> dict[str, Any] | None:
        return self.sessions.pop(self._normalize_session_id(session_id), None)

    def update_settings(self, session: dict[str, Any], settings: dict[str, Any]) -> None:
        for key in _SETTING_KEYS:
            if key not in settings:
                continue
            value = settings[key]
            if key in {"browser_regression_enabled", "browser_regression_requires_visible_cursor"}:
                session[key] = bool(value)
            elif key == "browser_regression_mode":
                session[key] = str(value or "off")
            elif key == "browser_regression_preview_url":
                session[key] = str(value or "")
            elif key == "viewport_width":
                session[key] = self._coerce_viewport_int(
                    value,
                    minimum=_MIN_VIEWPORT_WIDTH,
                    fallback=self._default_viewport_width(),
                )
            elif key == "viewport_height":
                session[key] = self._coerce_viewport_int(
                    value,
                    minimum=_MIN_VIEWPORT_HEIGHT,
                    fallback=self._default_viewport_height(),
                )

    def record_action(
        self,
        session: dict[str, Any],
        action: str,
        detail: str,
        *,
        status: str = "ok",
        error: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        session["last_activity"] = self.now()
        session["action_count"] = int(session.get("action_count", 0)) + 1
        if bool(session.get("recovered_from_crash")) and status == "ok":
            session["recovery_revalidated_at"] = session["last_activity"]
        session.setdefault("actions", []).append(
            {
                "action": action,
                "detail": detail,
                "status": status,
                "error": error,
                "timestamp": session["last_activity"],
                "metadata": metadata or {},
            }
        )
        if len(session["actions"]) > 500:
            session["actions"] = session["actions"][-500:]

    def health_report(self, session_id: str, *, limit: int = 10) -> dict[str, Any]:
        normalized = self._normalize_session_id(session_id)
        session = self.get(normalized)
        if session is None:
            return {
                "schema": "echo.browser_session_health.v1",
                "exists": False,
                "healthy": False,
                "score": 0.0,
                "issues": ["session_missing"],
                "diagnostics": [
                    _diagnostic(
                        "session_missing",
                        severity="error",
                        message="Browser session does not exist.",
                        recommended_action="ensure_session",
                    ),
                ],
                "recommended_actions": ["ensure_session"],
                "session": self.missing_snapshot(normalized),
                "recent_actions": [],
                "replay_ready": False,
            }
        snapshot = self.snapshot(session)
        actions = list(session.get("actions") or [])
        recent = actions[-max(1, limit) :]
        now = self.now()
        stale_seconds = max(0, now - int(snapshot.get("last_activity") or now))
        issues: list[str] = []
        if not snapshot["healthy"]:
            issues.append("session_unhealthy")
        recovered_from_crash = bool(session.get("recovered_from_crash"))
        recovery_revalidated_at = int(session.get("recovery_revalidated_at") or 0)
        if recovered_from_crash and not recovery_revalidated_at:
            issues.append("recovered_from_crash")
        if not actions:
            issues.append("no_actions_recorded")
        elif str(actions[-1].get("status") or "ok") != "ok":
            issues.append("last_action_failed")
        if stale_seconds > 300:
            issues.append("stale_session")
        diagnostics = _browser_session_diagnostics(
            issues=issues,
            last_action=actions[-1] if actions else None,
            stale_seconds=stale_seconds,
            recovered_from_crash=recovered_from_crash,
            recovery_revalidated_at=recovery_revalidated_at,
        )
        score = max(0.0, round(1.0 - (0.2 * len(issues)), 3))
        return {
            "schema": "echo.browser_session_health.v1",
            "exists": True,
            "healthy": not issues,
            "score": score,
            "issues": issues,
            "diagnostics": diagnostics,
            "recommended_actions": _recommended_actions(diagnostics),
            "session": snapshot,
            "recent_actions": recent,
            "stale_seconds": stale_seconds,
            "replay_ready": bool(actions),
            "recovery_proof": {
                "schema": "echo.browser_session_recovery_proof.v1",
                "recovered_from_crash": recovered_from_crash,
                "revalidated": bool(recovery_revalidated_at),
                "revalidated_at": recovery_revalidated_at,
                "requires_operator_review": (recovered_from_crash and not recovery_revalidated_at),
                "replay_ready": bool(actions),
            },
        }

    def snapshot(self, session: dict[str, Any]) -> dict[str, Any]:
        page = session.get("page")
        mode = str(session.get("mode") or "mock")
        return {
            "session_id": str(session.get("session_id") or ""),
            "project_id": str(session.get("project_id") or session.get("session_id") or ""),
            "profile_id": str(session.get("profile_id") or session.get("session_id") or ""),
            "profile_dir": str(session.get("profile_dir") or ""),
            "automation_mode": str(session.get("automation_mode") or "browser_context"),
            "uses_system_mouse": bool(session.get("uses_system_mouse", False)),
            "desktop_lease_required": bool(session.get("desktop_lease_required", False)),
            "is_launched": bool(session.get("is_launched")),
            "created_at": int(session.get("created_at") or 0),
            "last_activity": int(session.get("last_activity") or 0),
            "action_count": int(session.get("action_count") or 0),
            "headless": bool(session.get("headless")),
            "viewport_width": int(session.get("viewport_width") or self._default_viewport_width()),
            "viewport_height": int(
                session.get("viewport_height") or self._default_viewport_height()
            ),
            "mode": mode,
            "runtime": mode,
            "has_page": page is not None,
            "healthy": bool(session.get("is_launched")) and (mode == "mock" or page is not None),
            "current_url": str(session.get("current_url") or ""),
            "current_title": str(session.get("current_title") or ""),
            "browser_regression_enabled": bool(session.get("browser_regression_enabled")),
            "browser_regression_mode": str(session.get("browser_regression_mode") or "off"),
            "browser_regression_preview_url": str(
                session.get("browser_regression_preview_url") or ""
            ),
            "browser_regression_requires_visible_cursor": bool(
                session.get("browser_regression_requires_visible_cursor")
            ),
            "recovered_from_crash": bool(session.get("recovered_from_crash")),
            "recovery_revalidated_at": int(session.get("recovery_revalidated_at") or 0),
        }

    def missing_snapshot(self, session_id: str) -> dict[str, Any]:
        return {
            "session_id": self._normalize_session_id(session_id),
            "project_id": self._normalize_project_id(session_id),
            "profile_id": self._normalize_profile_id(session_id),
            "profile_dir": "",
            "automation_mode": "browser_context",
            "uses_system_mouse": False,
            "desktop_lease_required": False,
            "is_launched": False,
            "created_at": 0,
            "last_activity": 0,
            "action_count": 0,
            "headless": bool(self._config_state["headless"]),
            "viewport_width": self._default_viewport_width(),
            "viewport_height": self._default_viewport_height(),
            "mode": "closed",
            "runtime": "closed",
            "has_page": False,
            "healthy": False,
            "current_url": "",
            "current_title": "",
            "browser_regression_enabled": False,
            "browser_regression_mode": "off",
            "browser_regression_preview_url": "",
            "browser_regression_requires_visible_cursor": False,
            "recovered_from_crash": False,
            "recovery_revalidated_at": 0,
        }

    def list_snapshots(self) -> list[dict[str, Any]]:
        sessions = [self.snapshot(session) for session in self.sessions.values()]
        sessions.sort(key=lambda item: item["last_activity"], reverse=True)
        return sessions

    @staticmethod
    def _normalize_session_id(session_id: str) -> str:
        normalized = str(session_id or "").strip()
        if not normalized:
            raise ValueError("session_id is required")
        return normalized

    @staticmethod
    def _normalize_project_id(project_id: str) -> str:
        normalized = str(project_id or "").strip()
        return normalized or "default"

    @staticmethod
    def _normalize_profile_id(profile_id: str) -> str:
        raw = str(profile_id or "").strip().lower()
        safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in raw)
        safe = "-".join(part for part in safe.split("-") if part)
        return safe[:80] or "default"

    def _default_viewport_width(self) -> int:
        return self._coerce_viewport_int(
            self._config_state.get("viewport_width"),
            minimum=_MIN_VIEWPORT_WIDTH,
            fallback=_DEFAULT_VIEWPORT_WIDTH,
        )

    def _default_viewport_height(self) -> int:
        return self._coerce_viewport_int(
            self._config_state.get("viewport_height"),
            minimum=_MIN_VIEWPORT_HEIGHT,
            fallback=_DEFAULT_VIEWPORT_HEIGHT,
        )

    @staticmethod
    def _coerce_viewport_int(value: Any, *, minimum: int, fallback: int) -> int:
        try:
            coerced = int(value)
        except (TypeError, ValueError):
            return fallback
        return max(minimum, min(_MAX_VIEWPORT_SIZE, coerced))


def _browser_session_diagnostics(
    *,
    issues: list[str],
    last_action: dict[str, Any] | None,
    stale_seconds: int,
    recovered_from_crash: bool,
    recovery_revalidated_at: int,
) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for issue in issues:
        if issue == "session_unhealthy":
            diagnostics.append(
                _diagnostic(
                    issue,
                    severity="error",
                    message="Browser runtime is not healthy.",
                    recommended_action="reset_session",
                )
            )
        elif issue == "recovered_from_crash":
            diagnostics.append(
                _diagnostic(
                    issue,
                    severity="warning",
                    message="Browser profile recovered from an unclean shutdown.",
                    recommended_action="revalidate_session",
                    metadata={
                        "recovered_from_crash": recovered_from_crash,
                        "recovery_revalidated_at": recovery_revalidated_at,
                    },
                )
            )
        elif issue == "no_actions_recorded":
            diagnostics.append(
                _diagnostic(
                    issue,
                    severity="info",
                    message="No browser actions have been recorded yet.",
                    recommended_action="run_navigation_probe",
                )
            )
        elif issue == "last_action_failed":
            diagnostics.append(_failed_action_diagnostic(last_action or {}))
        elif issue == "stale_session":
            diagnostics.append(
                _diagnostic(
                    issue,
                    severity="warning",
                    message="Browser session has been idle for too long.",
                    recommended_action="refresh_or_reset",
                    metadata={"stale_seconds": stale_seconds},
                )
            )
        else:
            diagnostics.append(
                _diagnostic(
                    issue,
                    severity="warning",
                    message=f"Browser session issue: {issue}",
                    recommended_action="inspect_health",
                )
            )
    return diagnostics


def _failed_action_diagnostic(action: dict[str, Any]) -> dict[str, Any]:
    error = str(action.get("error") or "")
    category = _classify_browser_action_error(error)
    action_name = str(action.get("action") or "action")
    detail = str(action.get("detail") or "")
    action_by_category = {
        "selector": "inspect_selector",
        "timeout": "retry_with_longer_timeout",
        "browser_closed": "reset_session",
        "navigation": "check_url_or_network",
        "permission": "review_browser_permissions",
    }
    return _diagnostic(
        "last_action_failed",
        severity="error",
        message=f"Last browser action failed: {action_name}.",
        recommended_action=action_by_category.get(category, "inspect_last_action"),
        metadata={
            "action": action_name,
            "detail": detail,
            "error_category": category,
            "error": error,
        },
    )


def _classify_browser_action_error(error: str) -> str:
    lower = error.lower()
    if any(token in lower for token in ("selector", "locator", "not found", "strict mode")):
        return "selector"
    if any(token in lower for token in ("timeout", "timed out", "deadline")):
        return "timeout"
    if any(
        token in lower
        for token in ("browser closed", "target closed", "context closed", "page closed")
    ):
        return "browser_closed"
    if any(token in lower for token in ("net::", "dns", "connection refused", "navigation failed")):
        return "navigation"
    if any(token in lower for token in ("permission", "denied", "not allowed")):
        return "permission"
    return "unknown"


def _diagnostic(
    code: str,
    *,
    severity: str,
    message: str,
    recommended_action: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "recommended_action": recommended_action,
        "metadata": metadata or {},
    }


def _recommended_actions(diagnostics: list[dict[str, Any]]) -> list[str]:
    actions: list[str] = []
    for item in diagnostics:
        action = str(item.get("recommended_action") or "").strip()
        if action and action not in actions:
            actions.append(action)
    return actions
