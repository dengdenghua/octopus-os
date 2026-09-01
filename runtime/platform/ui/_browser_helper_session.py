"""Browser-session handling helpers for the browser router backend.

Pure structural split of ``_browser_router_helpers``: session identity /
profile resolution, real-browser launch / teardown, action recording and
replay evidence. Exposed as ``_SessionBackendMixin`` — ``_BrowserBackend``
inherits it. No logic changes.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from runtime.execution.suckers.browser_launch import (
    launch_persistent_chromium,
)
from runtime.memory.learning.review_queue import ReviewQueue
from runtime.platform.process.paths import app_paths
from runtime.platform.ui._browser_helper_profile import (
    mark_session_active,
    mark_session_closed,
    secure_profile_dir,
)
from runtime.safety.replay.browser_desktop_replay import browser_session_replay_identity


class _SessionBackendMixin:
    """Browser-session handling helpers shared by the browser backend."""

    def _now_ts(self) -> int:
        return self.browser_session_center.now()

    def _session_project_id(self, session_id: str, body: dict[str, Any] | None = None) -> str:
        body = body or {}
        return str(
            body.get("project_id") or body.get("workspace_id") or body.get("owner_id") or session_id
        ).strip()

    def _session_profile_id(self, session_id: str, body: dict[str, Any] | None = None) -> str:
        body = body or {}
        return str(
            body.get("profile_id")
            or body.get("browser_profile_id")
            or self._session_project_id(session_id, body)
            or session_id
        ).strip()

    def _browser_profile_dir(self, session: dict[str, Any]) -> Path:
        profile_id = str(session.get("profile_id") or session.get("session_id") or "default")
        root = Path("data/browser_sessions/profiles").resolve()
        profile_dir = (root / profile_id).resolve()
        if not str(profile_dir).startswith(str(root)):
            raise HTTPException(400, "invalid browser profile id")
        profile_dir.mkdir(parents=True, exist_ok=True)
        secure_profile_dir(profile_dir)
        session["profile_dir"] = str(profile_dir)
        return profile_dir

    def _ensure_browser_session(
        self,
        session_id: str,
        *,
        headless: bool | None = None,
        project_id: str | None = None,
        profile_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            return self.browser_session_center.ensure(
                session_id,
                headless=headless,
                project_id=project_id,
                profile_id=profile_id,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    def _session_viewport(self, session: dict[str, Any]) -> tuple[int, int]:
        width = int(session.get("viewport_width") or self.browser_config_state["viewport_width"])
        height = int(session.get("viewport_height") or self.browser_config_state["viewport_height"])
        width = max(240, min(4096, width))
        height = max(160, min(4096, height))
        session["viewport_width"] = width
        session["viewport_height"] = height
        return width, height

    def _ensure_real_browser_session(self, session: dict[str, Any]) -> bool:
        if session.get("page") is not None:
            return True
        sync_playwright = self._playwright_runtime()
        if sync_playwright is None:
            return False
        executable_path = self._preferred_browser_executable()
        if not executable_path:
            return False
        playwright = None
        browser = None
        context = None
        page = None
        try:
            playwright = sync_playwright().start()
            profile_dir = self._browser_profile_dir(session)
            session["recovered_from_crash"] = mark_session_active(profile_dir)
            viewport_width, viewport_height = self._session_viewport(session)
            context = launch_persistent_chromium(
                playwright.chromium,
                user_data_dir=str(profile_dir),
                executable_path=executable_path,
                headless=bool(session.get("headless", True)),
                viewport={
                    "width": viewport_width,
                    "height": viewport_height,
                },
            )
            page = context.pages[0] if context.pages else context.new_page()
        except self._browser_runtime_errors():
            with contextlib.suppress(Exception):
                if context is not None:
                    context.close()
            with contextlib.suppress(Exception):
                if browser is not None:
                    browser.close()
            with contextlib.suppress(Exception):
                if playwright is not None:
                    playwright.stop()
            # The sentinel was written before launch; a launch failure
            # here is NOT a crash to recover from. Clear it so the next
            # attempt doesn't falsely report recovered_from_crash (common
            # when the chromium profile is locked).
            mark_session_closed(session.get("profile_dir"))
            return False
        session["playwright"] = playwright
        session["browser"] = browser
        session["context"] = context
        session["page"] = page
        session["mode"] = "playwright"
        return True

    def _close_real_browser_session(self, session: dict[str, Any]) -> None:
        for key in ("page", "context", "browser"):
            resource = session.get(key)
            if resource is None:
                continue
            with contextlib.suppress(Exception):
                resource.close()
            session[key] = None
        playwright = session.get("playwright")
        if playwright is not None:
            with contextlib.suppress(Exception):
                playwright.stop()
            session["playwright"] = None
        mark_session_closed(session.get("profile_dir"))
        session["mode"] = "mock"

    def _record_browser_action(
        self,
        session: dict[str, Any],
        action: str,
        detail: str,
        *,
        status: str = "ok",
        error: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.browser_session_center.record_action(
            session,
            action,
            detail,
            status=status,
            error=error,
            metadata=metadata,
        )

    def _queue_browser_replay_case(
        self,
        replay_case: dict[str, Any],
        *,
        reason: str = "",
        priority: str = "",
    ) -> dict[str, Any]:
        session_id = str(replay_case.get("session_id") or "default")
        health = replay_case.get("health")
        health = health if isinstance(health, dict) else {}
        last_action = replay_case.get("last_action")
        last_action = last_action if isinstance(last_action, dict) else {}
        issues = health.get("issues") if isinstance(health.get("issues"), list) else []
        failed = str(last_action.get("status") or "") == "failed"
        chosen_priority = priority or ("P0" if failed or not health.get("healthy") else "P1")
        action_name = str(last_action.get("action") or "no action")
        action_detail = str(last_action.get("detail") or "")
        issue_text = ", ".join(str(issue) for issue in issues) or "none"
        case_id = str(replay_case.get("case_id") or "")
        fingerprint = str(replay_case.get("fingerprint") or "")
        text = (
            f"Browser session `{session_id}` replay case `{case_id}` captured for operator review.\n"
            f"Last action: {action_name} {action_detail}".strip()
            + f"\nHealth score: {health.get('score', 0)}; issues: {issue_text}."
        )
        if reason:
            text += f"\nReason: {reason[:500]}"
        queue = ReviewQueue(app_paths().review_queue_path)
        return queue.upsert_item(
            source="browser_session_replay",
            source_kind="browser_desktop_replay",
            candidate_kind="browser_session_replay_case",
            priority=chosen_priority,
            target_bucket="browser_desktop_replay",
            title=f"Review browser replay case: {session_id}",
            text=text,
            metadata={
                "schema": replay_case.get("schema"),
                "case_id": case_id,
                "fingerprint": fingerprint,
                "session_id": session_id,
                "replay_ready": bool(replay_case.get("replay_ready")),
                "health": health,
                "last_action": last_action,
                "action_count": replay_case.get("action_count"),
            },
            tags=[
                "browser",
                "desktop",
                "replay_case",
                "review_queue",
                "operator_review",
            ],
        )

    def _browser_replay_evidence(
        self,
        session_id: str,
        session: dict[str, Any],
        *,
        limit: int = 100,
    ) -> dict[str, Any]:
        actions = [
            action
            for action in list(session.get("actions", []))[-limit:]
            if isinstance(action, dict)
        ]
        health = self.browser_session_center.health_report(session_id, limit=min(limit, 100))
        identity = browser_session_replay_identity(
            session_id=session_id,
            actions=actions,
            health=health,
        )
        return {
            "schema": "echo.browser_replay_evidence_hint.v1",
            "case_id": identity["case_id"],
            "fingerprint": identity["fingerprint"],
            "replay_ready": bool(actions),
            "replay_case_url": f"/api/browser/session/replay-case?session_id={session_id}",
            "queue_url": "/api/browser/session/replay-case/queue",
            "queue_body": {"session_id": session_id},
        }
