"""Browser session and relay compatibility router for the UI app."""

from __future__ import annotations

import asyncio
import contextlib
import html
import json
import math
import re
import shutil
import time
import urllib.error
import uuid
from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, Response
from starlette.requests import HTTPConnection

from runtime.platform.process.paths import app_paths
from runtime.platform.runtime_policy.browser_sessions import BrowserSessionCenter
from runtime.platform.ui._browser_artifact_path import resolve_browser_artifact_path
from runtime.platform.ui._browser_desktop_helpers import browser_system_info, open_extension_folder
from runtime.platform.ui._browser_router_helpers import (
    _SESSION_SENTINEL_NAME,
    _BrowserBackend,
    mark_session_active,
    mark_session_closed,
    secure_profile_dir,
)
from runtime.safety.auth.principal import require_operator, resolve_principal
from runtime.safety.auth.websocket import accepted_auth_subprotocol
from runtime.safety.replay.browser_desktop_replay import browser_session_replay_identity

__all__ = [
    "create_browser_router",
    "_SESSION_SENTINEL_NAME",
    "mark_session_active",
    "mark_session_closed",
    "secure_profile_dir",
]

# Leave two seconds for the HTTP polling fallback after the server declares a
# relay offline, so the visible status still meets the ten-second SLA even
# when the read-only status WebSocket is unavailable.
_RELAY_HEARTBEAT_FRESH_SECONDS = 6
_RELAY_OFFLINE_SECONDS = 8
_RELAY_PUSH_PING_SECONDS = 3


def create_browser_router(
    *,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> APIRouter:
    """Create the ``/api/browser/*`` session and relay router.

    These endpoints drive a real browser (navigation, form fill, credential
    entry). They previously had NO auth at all — inconsistent with the other
    routers that honour ``require_auth``. The router-level dependency below
    closes that gap: when ``require_auth`` is off (default / single-user dev)
    the dependency is a no-op so local preview is unchanged; when auth is
    enabled it enforces 401 across every browser endpoint and the handlers
    bind sessions/relay state to the verified Principal.
    """

    def _auth_dep(request: HTTPConnection) -> None:
        resolve_principal(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    router = APIRouter(tags=["browser"], dependencies=[Depends(_auth_dep)])
    browser_config_state: dict[str, Any] = {
        "max_open_tabs": 20,
        "max_saved_tabs": 10,
        "connection_mode": "playwright",
        "cdp_port": 9222,
        "headless": True,
        "viewport_width": 1440,
        "viewport_height": 900,
        "relay_allowed_hosts": [],
        "relay_blocked_hosts": [],
        "relay_require_allowlist": False,
    }
    browser_policy_path = app_paths().browser_policy_path
    browser_session_center = BrowserSessionCenter(browser_config_state)
    backend = _BrowserBackend(
        browser_config_state=browser_config_state,
        browser_policy_path=browser_policy_path,
        browser_session_center=browser_session_center,
    )

    def _principal(request: HTTPConnection) -> Any:
        return getattr(getattr(request, "state", None), "principal", None)

    def _owned_session(
        request: HTTPConnection,
        session_id: str,
        *,
        missing_ok: bool = False,
    ) -> dict[str, Any] | None:
        session = backend.browser_sessions.get(session_id)
        if session is None:
            if missing_ok:
                return None
            raise HTTPException(404, f"browser session not found: {session_id}")
        if require_auth:
            principal = _principal(request)
            owner = str(session.get("owner_actor_id") or "")
            if principal is None or not owner or owner != principal.actor_id:
                # Browser profiles contain cookies and may represent a real
                # logged-in user. Hide existence as well as contents.
                raise HTTPException(404, f"browser session not found: {session_id}")
        return session

    def _ensure_owned_session(
        request: HTTPConnection,
        session_id: str,
        *,
        headless: bool | None = None,
        project_id: str | None = None,
        profile_id: str | None = None,
    ) -> dict[str, Any]:
        principal = _principal(request)
        requested_profile = backend.browser_session_center._normalize_profile_id(
            profile_id or project_id or session_id
        )
        if require_auth and principal is not None:
            for other in backend.browser_sessions.values():
                if other.get("profile_id") != requested_profile:
                    continue
                if other.get("owner_actor_id") != principal.actor_id:
                    raise HTTPException(409, "browser profile is owned by another actor")
        existing = _owned_session(request, session_id, missing_ok=True)
        if existing is not None:
            return backend._ensure_browser_session(
                session_id,
                headless=headless,
                project_id=project_id,
                profile_id=profile_id,
            )
        session = backend._ensure_browser_session(
            session_id,
            headless=headless,
            project_id=project_id,
            profile_id=profile_id,
        )
        if require_auth:
            if principal is None:
                raise HTTPException(401, "authenticated browser principal required")
            session["owner_actor_id"] = principal.actor_id
            session["tenant_id"] = principal.tenant_id
        return session

    def _require_relay_owner(request: HTTPConnection) -> Any:
        if not require_auth:
            return None
        principal = _principal(request)
        if principal is None:
            raise HTTPException(401, "authenticated browser principal required")
        owner = str(backend.browser_relay_state.get("owner_actor_id") or "")
        if owner and owner != principal.actor_id:
            raise HTTPException(404, "browser relay not found")
        backend.browser_relay_state.setdefault("owner_actor_id", principal.actor_id)
        backend.browser_relay_state.setdefault("tenant_id", principal.tenant_id)
        return principal

    # ─── Filesystem helpers for desktop workspace pages ───────────────
    @router.get("/api/browser/system-info")
    def api_browser_system_info() -> dict[str, Any]:
        return browser_system_info(backend._detect_browsers)

    @router.post("/api/browser/launch")
    def api_browser_launch(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        session_id = str(body.get("session_id") or "").strip()
        if not session_id:
            raise HTTPException(400, "session_id is required")
        session = _ensure_owned_session(
            request,
            session_id,
            headless=bool(body.get("headless", backend.browser_config_state["headless"])),
            project_id=backend._session_project_id(session_id, body),
            profile_id=backend._session_profile_id(session_id, body),
        )
        backend.browser_session_center.update_settings(session, body)
        return {
            "status": "launched",
            "session": backend.browser_session_center.snapshot(session),
        }

    @router.get("/api/browser/session/status")
    def api_browser_session_status(
        request: Request,
        session_id: str = Query(default="default"),
    ) -> dict[str, Any]:
        try:
            session = _owned_session(request, session_id, missing_ok=True)
            snapshot = (
                backend.browser_session_center.snapshot(session)
                if session is not None
                else backend.browser_session_center.missing_snapshot(session_id)
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"exists": session is not None, "session": snapshot}

    @router.get("/api/browser/session/health")
    def api_browser_session_health(
        request: Request,
        session_id: str = Query(default="default"),
        limit: int = Query(default=10, ge=1, le=100),
    ) -> dict[str, Any]:
        try:
            # Health is also the recovery discovery endpoint: a genuinely
            # missing session must return a structured ``session_missing``
            # report instead of a generic 404. Ownership mismatches in auth
            # mode still raise 404 inside ``_owned_session``.
            _owned_session(request, session_id, missing_ok=True)
            return backend.browser_session_center.health_report(session_id, limit=limit)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post("/api/browser/session/ensure")
    def api_browser_session_ensure(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        session_id = str(body.get("session_id") or "default").strip()
        session = _ensure_owned_session(
            request,
            session_id,
            headless=body.get("headless") if "headless" in body else None,
            project_id=backend._session_project_id(session_id, body),
            profile_id=backend._session_profile_id(session_id, body),
        )
        backend.browser_session_center.update_settings(session, body)
        return {"status": "ready", "session": backend.browser_session_center.snapshot(session)}

    @router.post("/api/browser/session/viewport")
    def api_browser_session_viewport(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        session_id = str(body.get("session_id") or "default").strip()
        if not session_id:
            raise HTTPException(400, "session_id is required")
        session = _ensure_owned_session(
            request,
            session_id,
            headless=body.get("headless") if "headless" in body else None,
            project_id=backend._session_project_id(session_id, body),
            profile_id=backend._session_profile_id(session_id, body),
        )
        backend.browser_session_center.update_settings(
            session,
            {
                "viewport_width": body.get("width", body.get("viewport_width")),
                "viewport_height": body.get("height", body.get("viewport_height")),
            },
        )
        width, height = backend._session_viewport(session)
        page = session.get("page")
        if page is not None:
            try:
                page.set_viewport_size({"width": width, "height": height})
            except backend._browser_runtime_errors() as exc:
                backend._record_browser_action(
                    session,
                    "viewport",
                    f"{width}x{height}",
                    status="failed",
                    error=str(exc),
                    metadata={"width": width, "height": height},
                )
                raise HTTPException(500, f"browser viewport update failed: {exc}") from exc
        backend._record_browser_action(
            session,
            "viewport",
            f"{width}x{height}",
            metadata={"width": width, "height": height},
        )
        return {
            "ok": True,
            "session": backend.browser_session_center.snapshot(session),
        }

    @router.post("/api/browser/session/reset")
    def api_browser_session_reset(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        session_id = str(body.get("session_id") or "default").strip()
        if not session_id:
            raise HTTPException(400, "session_id is required")
        _owned_session(request, session_id, missing_ok=True)
        session = backend.browser_session_center.pop(session_id)
        if session is not None:
            backend._close_real_browser_session(session)
        relaunch = bool(body.get("relaunch", False))
        if relaunch:
            session = _ensure_owned_session(
                request,
                session_id,
                headless=body.get("headless") if "headless" in body else None,
                project_id=backend._session_project_id(session_id, body),
                profile_id=backend._session_profile_id(session_id, body),
            )
            backend.browser_session_center.update_settings(session, body)
            return {
                "ok": True,
                "status": "ready",
                "session": backend.browser_session_center.snapshot(session),
            }
        return {
            "ok": True,
            "status": "closed",
            "session": backend.browser_session_center.missing_snapshot(session_id),
        }

    @router.post("/api/browser/data/clear")
    def api_browser_data_clear(request: Request) -> dict[str, Any]:
        """Clear persistent Echo browser profiles owned by this principal.

        In single-user local mode all Echo-managed profiles are in scope.
        Authenticated deployments only remove profiles attached to the caller's
        live sessions; profiles owned by another actor are never traversed.
        """

        principal = _principal(request)
        sessions = list(backend.browser_sessions.items())
        if require_auth:
            sessions = [
                (session_id, session)
                for session_id, session in sessions
                if principal is not None and session.get("owner_actor_id") == principal.actor_id
            ]

        profile_root = Path("data/browser_sessions/profiles").resolve()
        profile_dirs: set[Path] = set()
        for session_id, _session in sessions:
            removed = backend.browser_session_center.pop(session_id)
            if removed is None:
                continue
            backend._close_real_browser_session(removed)
            raw_profile_dir = str(removed.get("profile_dir") or "").strip()
            if raw_profile_dir:
                profile_dirs.add(Path(raw_profile_dir).resolve())

        if not require_auth and profile_root.exists():
            profile_dirs.update(path.resolve() for path in profile_root.iterdir())

        removed_profiles = 0
        for profile_dir in profile_dirs:
            if profile_dir == profile_root or profile_root not in profile_dir.parents:
                continue
            if not profile_dir.exists() or not profile_dir.is_dir():
                continue
            shutil.rmtree(profile_dir)
            removed_profiles += 1

        return {
            "ok": True,
            "closed_sessions": len(sessions),
            "removed_profiles": removed_profiles,
        }

    @router.post("/api/browser/navigate")
    def api_browser_navigate(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        session_id = str(body.get("session_id") or "").strip()
        url = str(body.get("url") or "").strip()
        if not session_id:
            raise HTTPException(400, "session_id is required")
        if not url:
            raise HTTPException(400, "url is required")
        session = _ensure_owned_session(
            request,
            session_id,
            project_id=backend._session_project_id(session_id, body),
            profile_id=backend._session_profile_id(session_id, body),
        )
        return backend._navigate_browser_session(session, url)

    @router.post("/api/browser/action")
    def api_browser_action(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        session_id = str(body.get("session_id") or "").strip()
        action = str(body.get("action") or "").strip()
        if not session_id:
            raise HTTPException(400, "session_id is required")
        if not action:
            raise HTTPException(400, "action is required")
        session = _owned_session(request, session_id)
        if action == "back":
            return backend._move_browser_history(session, -1)
        if action == "forward":
            return backend._move_browser_history(session, 1)
        if action == "reload":
            return backend._reload_browser_session(session)
        if backend._ensure_real_browser_session(session):
            page = session.get("page")
            if page is not None:
                try:
                    if action == "click":
                        selector = str(body.get("selector") or "").strip()
                        if not selector:
                            raise HTTPException(400, "selector is required for click")
                        page.click(selector, timeout=10_000)
                    elif action == "type":
                        selector = str(body.get("selector") or "").strip()
                        text = str(body.get("text") or "")
                        if not selector:
                            raise HTTPException(400, "selector is required for type")
                        page.fill(selector, text, timeout=10_000)
                    elif action == "hover":
                        selector = str(body.get("selector") or "").strip()
                        if not selector:
                            raise HTTPException(400, "selector is required for hover")
                        page.hover(selector, timeout=10_000)
                    elif action == "click_at":
                        try:
                            x = int(body.get("x"))
                            y = int(body.get("y"))
                        except (TypeError, ValueError) as exc:
                            raise HTTPException(400, "x and y are required for click_at") from exc
                        page.mouse.click(x, y)
                    elif action == "double_click_at":
                        try:
                            x = int(body.get("x"))
                            y = int(body.get("y"))
                        except (TypeError, ValueError) as exc:
                            raise HTTPException(
                                400, "x and y are required for double_click_at"
                            ) from exc
                        page.mouse.dblclick(x, y)
                    elif action == "wait":
                        selector = str(body.get("selector") or "").strip()
                        timeout = int(body.get("timeout") or 10_000)
                        if not selector:
                            raise HTTPException(400, "selector is required for wait")
                        page.wait_for_selector(selector, timeout=timeout)
                    elif action == "press":
                        key = str(body.get("key") or "Enter")
                        page.keyboard.press(key)
                    elif action == "scroll":
                        selector = str(body.get("selector") or "").strip()
                        y_value = body.get("y", body.get("deltaY"))
                        if selector:
                            page.locator(selector).scroll_into_view_if_needed(timeout=10_000)
                        elif y_value is not None:
                            page.evaluate(f"window.scrollBy(0, {int(y_value)})")
                        else:
                            raise HTTPException(400, "selector or y is required for scroll")
                    elif action == "aria":
                        text = ""
                        try:
                            text = page.locator("body").inner_text(timeout=5000)
                        except backend._browser_runtime_errors():
                            text = ""
                        dom_nodes: list[dict[str, Any]] = []
                        try:
                            dom_nodes = page.evaluate(
                                """
                                () => {
                                  const selectorFor = (el) => {
                                    if (el.id) return `#${CSS.escape(el.id)}`;
                                    const parts = [];
                                    let node = el;
                                    while (node && node.nodeType === 1 && parts.length < 4) {
                                      let part = node.tagName.toLowerCase();
                                      if (node.getAttribute('data-testid')) {
                                        part += `[data-testid="${CSS.escape(node.getAttribute('data-testid'))}"]`;
                                      } else if (node.classList.length) {
                                        part += '.' + Array.from(node.classList).slice(0, 2).map(CSS.escape).join('.');
                                      }
                                      const parent = node.parentElement;
                                      if (parent) {
                                        const siblings = Array.from(parent.children).filter((child) => child.tagName === node.tagName);
                                        if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
                                      }
                                      parts.unshift(part);
                                      node = parent;
                                    }
                                    return parts.join(' > ');
                                  };
                                  return Array.from(document.querySelectorAll(
                                    'button, a, input, textarea, select, [role], [contenteditable="true"]'
                                  )).slice(0, 120).map((el) => ({
                                    tag: el.tagName.toLowerCase(),
                                    role: el.getAttribute('role') || '',
                                    name: el.getAttribute('aria-label') || el.getAttribute('name') || '',
                                    text: (el.innerText || el.value || '').replace(/\\s+/g, ' ').trim().slice(0, 180),
                                    selector: selectorFor(el),
                                  }));
                                }
                                """
                            )
                        except backend._browser_runtime_errors():
                            dom_nodes = []
                        snapshot = {
                            "role": "document",
                            "name": page.title(),
                            "url": page.url,
                            "text": text[:5000],
                            "truncated": len(text) > 5000,
                            "nodes": dom_nodes,
                        }
                        session["current_url"] = page.url
                        session["current_title"] = page.title()
                        backend._record_browser_action(session, action, "page semantic snapshot")
                        return {
                            "ok": True,
                            "url": str(session.get("current_url") or ""),
                            "title": str(session.get("current_title") or ""),
                            "nodes": snapshot,
                        }
                    else:
                        raise HTTPException(400, f"unsupported browser action: {action}")
                    session["current_url"] = page.url
                    session["current_title"] = page.title()
                    detail = str(
                        body.get("selector")
                        or body.get("text")
                        or body.get("y")
                        or (
                            f"{body.get('x')},{body.get('y')}"
                            if body.get("x") is not None and body.get("y") is not None
                            else action
                        )
                    )
                    backend._record_browser_action(session, action, detail)
                    return {
                        "ok": True,
                        "url": str(session.get("current_url") or ""),
                        "title": str(session.get("current_title") or ""),
                    }
                except HTTPException:
                    raise
                except backend._browser_runtime_errors() as exc:
                    backend._record_browser_action(
                        session,
                        action,
                        str(body.get("selector") or body.get("text") or body.get("y") or action),
                        status="failed",
                        error=str(exc),
                    )
                    raise HTTPException(
                        status_code=500,
                        detail={
                            "error": f"browser action failed: {exc}",
                            "replay_evidence": backend._browser_replay_evidence(
                                session_id, session
                            ),
                        },
                    ) from exc
        detail = str(
            body.get("selector")
            or body.get("text")
            or (
                f"{body.get('x')},{body.get('y')}"
                if body.get("x") is not None and body.get("y") is not None
                else action
            )
        )
        backend._record_browser_action(session, action, detail)
        return {
            "ok": True,
            "url": str(session.get("current_url") or ""),
            "title": str(session.get("current_title") or ""),
        }

    @router.get("/api/browser/screenshot/base64")
    def api_browser_screenshot_base64(request: Request, session_id: str) -> dict[str, Any]:
        session = _owned_session(request, session_id)
        backend._record_browser_action(session, "screenshot", str(session.get("current_url") or ""))
        return backend._browser_screenshot_payload(session)

    @router.get("/api/browser/page-info")
    def api_browser_page_info(request: Request, session_id: str) -> dict[str, Any]:
        session = _owned_session(request, session_id)
        return {
            "url": str(session.get("current_url") or ""),
            "title": str(session.get("current_title") or ""),
        }

    @router.get("/api/browser/extract-text")
    def api_browser_extract_text(request: Request, session_id: str) -> dict[str, Any]:
        session = _owned_session(request, session_id)
        url = str(session.get("current_url") or "")
        title = str(session.get("current_title") or "")
        text = ""
        if backend._ensure_real_browser_session(session):
            page = session.get("page")
            if page is not None:
                try:
                    url = page.url
                    title = page.title()
                    text = page.locator("body").inner_text(timeout=5_000)
                    session["current_url"] = url
                    session["current_title"] = title
                except backend._browser_runtime_errors():
                    text = ""
        if not text and url:
            # SSRF + rebinding-proof fetch.
            try:
                from runtime.safety.auth.url_guard import safe_urlopen

                raw, _headers = safe_urlopen(
                    url,
                    timeout=8.0,
                    read_cap_bytes=256_000,
                    allow_private=False,
                )
                html_text = raw.decode("utf-8", errors="replace")
                text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html_text)
                text = re.sub(r"(?s)<[^>]+>", " ", text)
                text = html.unescape(re.sub(r"\s+", " ", text)).strip()
            except (ValueError, urllib.error.URLError, TimeoutError, OSError):
                text = ""
        backend._record_browser_action(session, "extract", url)
        max_chars = 20_000
        return {
            "url": url,
            "title": title,
            "text": text[:max_chars],
            "truncated": len(text) > max_chars,
            "textLength": len(text),
        }

    @router.get("/api/browser/sessions")
    def api_browser_sessions(request: Request) -> dict[str, Any]:
        principal = _principal(request)
        visible_sessions = backend.browser_sessions.values()
        if require_auth:
            visible_sessions = [
                session
                for session in visible_sessions
                if principal is not None and session.get("owner_actor_id") == principal.actor_id
            ]
        sessions = [
            backend.browser_session_center.snapshot(session) for session in visible_sessions
        ]
        sessions.sort(key=lambda item: item["last_activity"], reverse=True)
        return {"sessions": sessions, "count": len(sessions)}

    @router.get("/api/browser/action-log")
    def api_browser_action_log(
        request: Request,
        session_id: str,
        limit: int = Query(default=50, ge=1, le=500),
    ) -> dict[str, Any]:
        session = _owned_session(request, session_id)
        actions = list(session.get("actions", []))[-limit:]
        return {"actions": actions}

    @router.get("/api/browser/session/replay-case")
    def api_browser_session_replay_case(
        request: Request,
        session_id: str,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        session = _owned_session(request, session_id)
        actions = list(session.get("actions", []))[-limit:]
        health = backend.browser_session_center.health_report(session_id, limit=min(limit, 100))
        identity = browser_session_replay_identity(
            session_id=session_id,
            actions=[action for action in actions if isinstance(action, dict)],
            health=health,
        )
        return {
            "schema": "echo.browser_session_replay_case.v1",
            "case_id": identity["case_id"],
            "fingerprint": identity["fingerprint"],
            "session_id": session_id,
            "replay_ready": bool(actions),
            "health": health,
            "session": backend.browser_session_center.snapshot(session),
            "actions": actions,
            "action_count": len(actions),
            "last_action": actions[-1] if actions else None,
        }

    @router.post("/api/browser/session/replay-case/queue")
    def api_browser_session_replay_case_queue(
        request: Request,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        session_id = str(body.get("session_id") or "default").strip()
        if not session_id:
            raise HTTPException(400, "session_id is required")
        limit = int(body.get("limit") or 100)
        limit = max(1, min(500, limit))
        replay_case = api_browser_session_replay_case(
            request=request,
            session_id=session_id,
            limit=limit,
        )
        if not replay_case.get("replay_ready"):
            raise HTTPException(409, "browser replay case has no actions to review")
        queued = backend._queue_browser_replay_case(
            replay_case,
            reason=str(body.get("reason") or ""),
            priority=str(body.get("priority") or ""),
        )
        return {
            "ok": True,
            "schema": "echo.browser_session_replay_case_queue.v1",
            "replay_case": replay_case,
            "queue": queued,
        }

    @router.post("/api/browser/close")
    def api_browser_close(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        session_id = str(body.get("session_id") or "").strip()
        if not session_id:
            raise HTTPException(400, "session_id is required")
        _owned_session(request, session_id, missing_ok=True)
        session = backend.browser_session_center.pop(session_id)
        if session is not None:
            backend._close_real_browser_session(session)
        return {"ok": True}

    @router.get("/api/browser/config")
    def api_browser_config() -> dict[str, Any]:
        return backend.browser_config_state

    @router.put("/api/browser/config")
    def api_browser_config_update(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        require_operator(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )
        allowed_modes = {"playwright", "extension", "cdp"}
        persist_policy = False
        for key in (
            "max_open_tabs",
            "max_saved_tabs",
            "cdp_port",
            "viewport_width",
            "viewport_height",
        ):
            if key in body:
                try:
                    backend.browser_config_state[key] = int(body[key])
                except (TypeError, ValueError) as exc:
                    raise HTTPException(400, f"{key} must be an integer") from exc
        if "connection_mode" in body:
            mode = str(body["connection_mode"])
            if mode not in allowed_modes:
                raise HTTPException(
                    400, "connection_mode must be one of playwright, extension, cdp"
                )
            backend.browser_config_state["connection_mode"] = mode
        if "headless" in body:
            backend.browser_config_state["headless"] = bool(body["headless"])
        if "relay_allowed_hosts" in body:
            backend.browser_config_state["relay_allowed_hosts"] = (
                backend._normalize_relay_host_patterns(body.get("relay_allowed_hosts"))
            )
            persist_policy = True
        if "relay_blocked_hosts" in body:
            backend.browser_config_state["relay_blocked_hosts"] = (
                backend._normalize_relay_host_patterns(body.get("relay_blocked_hosts"))
            )
            persist_policy = True
        if "relay_require_allowlist" in body:
            backend.browser_config_state["relay_require_allowlist"] = bool(
                body.get("relay_require_allowlist"),
            )
            persist_policy = True
        if persist_policy:
            backend._persist_browser_policy()
        return backend.browser_config_state

    def _relay_status_payload() -> dict[str, Any]:
        extension_path = backend._resolve_browser_extension_path()
        manifest = extension_path / "manifest.json"
        last_seen = int(backend.browser_relay_state.get("last_seen") or 0)
        push_connected = int(backend.browser_relay_state.get("push_connections") or 0) > 0
        last_seen_age = max(0, backend._now_ts() - last_seen) if last_seen else None
        # The settings UI polls every two seconds. A socket count alone is not
        # proof of liveness: a laptop sleep, cable pull, or dead extension can
        # leave a half-open TCP connection behind. Only a recent application
        # heartbeat is authoritative; the server pings frequently enough for
        # a healthy push client to refresh it inside the ten-second budget.
        # A heartbeat is authoritative even when the extension was installed
        # from Chrome's own profile rather than this checkout's suggested
        # unpacked path. ``manifest_exists`` is installation guidance only.
        connected = bool(
            last_seen_age is not None and last_seen_age <= _RELAY_HEARTBEAT_FRESH_SECONDS
        )
        connection_state = (
            "online"
            if connected
            else (
                "reconnecting"
                if last_seen_age is not None and last_seen_age < _RELAY_OFFLINE_SECONDS
                else "offline"
            )
        )
        backend.browser_relay_state["connected"] = connected
        return {
            "connected": connected,
            "connection_state": connection_state,
            "extension_version": str(
                backend.browser_relay_state.get("extension_version")
                or ("local-dev" if manifest.exists() else "")
            ),
            "pending_commands": len(backend.browser_relay_state.get("pending_commands") or []),
            "push_connected": push_connected,
            "last_seen": last_seen,
            "active_tab": backend.browser_relay_state.get("active_tab"),
            "recent_human_activity": list(
                backend.browser_relay_state.get("recent_human_activity") or []
            ),
            "extension_path": str(extension_path),
            "manifest_exists": manifest.exists(),
            "site_policy": backend._relay_policy_snapshot(),
            "control": backend._relay_control_snapshot(),
        }

    @router.get("/api/browser/relay/status")
    def api_browser_relay_status(request: Request) -> dict[str, Any]:
        _require_relay_owner(request)
        return _relay_status_payload()

    @router.websocket("/api/browser/relay/status/ws")
    async def api_browser_relay_status_ws(websocket: WebSocket) -> None:
        """Read-only status stream for settings and operator surfaces."""

        try:
            _require_relay_owner(websocket)
        except HTTPException as exc:
            await websocket.close(code=4403 if exc.status_code == 404 else 4401)
            return
        await websocket.accept(subprotocol=accepted_auth_subprotocol(websocket))
        try:
            while True:
                await websocket.send_json(
                    {
                        "type": "browser_relay_status",
                        "status": _relay_status_payload(),
                    }
                )
                await asyncio.sleep(1)
        except WebSocketDisconnect:
            return
        except RuntimeError:
            # Starlette raises RuntimeError when the peer vanishes between
            # ticks without completing a close handshake.
            return

    @router.post("/api/browser/relay/heartbeat")
    def api_browser_relay_heartbeat(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        _require_relay_owner(request)
        pending = backend._apply_relay_heartbeat(body)
        return {
            "ok": True,
            "pending_commands": len(pending),
            "commands": pending,
            "control": backend._relay_control_snapshot(),
        }

    @router.websocket("/api/browser/relay/ws")
    async def api_browser_relay_ws(websocket: WebSocket) -> None:
        try:
            _require_relay_owner(websocket)
        except HTTPException as exc:
            await websocket.close(code=4403 if exc.status_code == 404 else 4401)
            return
        await websocket.accept(subprotocol=accepted_auth_subprotocol(websocket))
        with backend.browser_relay_queue_lock:
            backend.browser_relay_state["push_connections"] = (
                int(backend.browser_relay_state.get("push_connections") or 0) + 1
            )
            backend.browser_relay_state["connected"] = True
            backend.browser_relay_state["last_seen"] = backend._now_ts()
        last_keepalive = time.monotonic()
        try:
            while True:
                message: Any = None
                with contextlib.suppress(TimeoutError):
                    message = await asyncio.wait_for(
                        websocket.receive_json(),
                        timeout=0.1,
                    )

                if isinstance(message, dict):
                    message_type = str(message.get("type") or "")
                    if message_type == "heartbeat":
                        pending = backend._apply_relay_heartbeat(message)
                        if pending:
                            await websocket.send_json({"type": "commands", "commands": pending})
                    elif message_type == "result":
                        try:
                            backend._apply_relay_result(message)
                        except ValueError as exc:
                            await websocket.send_json({"type": "error", "error": str(exc)})

                # Commands can be enqueued by a concurrent HTTP request while
                # no extension message is arriving. Drain them immediately so
                # delivery no longer depends on an MV3 JavaScript timer.
                pending = backend._drain_relay_commands()
                if pending:
                    await websocket.send_json({"type": "commands", "commands": pending})

                # Chrome 116+ keeps an extension service worker alive when a
                # WebSocket exchanges traffic inside the 30-second window.
                now = time.monotonic()
                if now - last_keepalive >= _RELAY_PUSH_PING_SECONDS:
                    await websocket.send_json({"type": "ping", "at": backend._now_ts()})
                    last_keepalive = now
        except WebSocketDisconnect:
            return
        finally:
            with backend.browser_relay_queue_lock:
                backend.browser_relay_state["push_connections"] = max(
                    0,
                    int(backend.browser_relay_state.get("push_connections") or 0) - 1,
                )

    @router.post("/api/browser/relay/control")
    def api_browser_relay_control(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        _require_relay_owner(request)
        action = str(body.get("action") or "").strip()
        if action in {"stop", "interrupt"}:
            interrupt = backend._record_relay_interrupt(
                reason=str(body.get("reason") or "operator_stop"),
                source=str(body.get("source") or "side_panel"),
                detail={"active_tab": backend._relay_active_tab_snapshot()},
            )
            return {
                "ok": True,
                "interrupt": interrupt,
                "control": backend._relay_control_snapshot(),
            }
        if action in {"resume", "clear_interrupt"}:
            backend._clear_relay_interrupt()
            return {"ok": True, "control": backend._relay_control_snapshot()}
        if action == "status":
            return {"ok": True, "control": backend._relay_control_snapshot()}
        raise HTTPException(400, "action must be one of stop, interrupt, resume, clear_interrupt")

    @router.post("/api/browser/relay/command")
    def api_browser_relay_command(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        _require_relay_owner(request)
        last_seen = int(backend.browser_relay_state.get("last_seen") or 0)
        if not last_seen or (backend._now_ts() - last_seen) > 15:
            raise HTTPException(409, "browser relay extension is not connected")
        action = str(body.get("action") or "").strip()
        if not action:
            raise HTTPException(400, "action is required")
        control = backend._relay_control_snapshot()
        if control["blocked"]:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "browser relay paused by human interrupt",
                    "control": control,
                },
            )
        active_lease = backend._relay_control_lease()
        if active_lease:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "browser relay tab lease is already active",
                    "control": backend._relay_control_snapshot(),
                },
            )
        site_policy = backend._relay_site_policy_decision(action, body)
        if site_policy["decision"] == "block":
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "browser relay site policy blocked command",
                    "site_policy": site_policy,
                },
            )
        try:
            timeout_seconds = float(body.get("timeout_seconds") or 8)
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, "timeout_seconds must be a number") from exc
        if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 300:
            raise HTTPException(400, "timeout_seconds must be between 0 and 300")
        deadline = time.time() + timeout_seconds
        command_id = str(uuid.uuid4())
        lease = backend._make_relay_lease(
            command_id=command_id,
            action=action,
            body=body,
            site_policy=site_policy,
        )
        command = {
            "id": command_id,
            "action": action,
            "params": {k: v for k, v in body.items() if k not in {"id", "action"}},
            "site_policy": site_policy,
            "lease": lease,
            "created_at": backend._now_ts(),
            "deadline_at": deadline,
        }
        backend.browser_relay_state["control_lease"] = lease
        with backend.browser_relay_queue_lock:
            pending = list(backend.browser_relay_state.get("pending_commands") or [])
            pending.append(command)
            backend.browser_relay_state["pending_commands"] = pending

        results = backend.browser_relay_state.setdefault("command_results", {})
        while time.time() < deadline:
            result = results.pop(command_id, None)
            if result is not None:
                if (backend.browser_relay_state.get("control_lease") or {}).get(
                    "command_id"
                ) == command_id:
                    backend.browser_relay_state["control_lease"] = None
                if result.get("ok") is False:
                    raise HTTPException(500, str(result.get("error") or "relay command failed"))
                result.setdefault("site_policy", site_policy)
                result["control"] = backend._relay_control_snapshot()
                return result
            time.sleep(0.1)

        with backend.browser_relay_queue_lock:
            backend.browser_relay_state["pending_commands"] = [
                item
                for item in (backend.browser_relay_state.get("pending_commands") or [])
                if item.get("id") != command_id
            ]
        if (backend.browser_relay_state.get("control_lease") or {}).get("command_id") == command_id:
            backend.browser_relay_state["control_lease"] = None
        raise HTTPException(504, "browser relay command timed out")

    @router.post("/api/browser/relay/result")
    def api_browser_relay_result(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        _require_relay_owner(request)
        try:
            backend._apply_relay_result(body)
        except ValueError:
            raise HTTPException(400, "id is required") from None
        return {"ok": True}

    @router.get("/api/browser/relay/bookmarklet.js")
    def api_browser_relay_bookmarklet_js() -> Any:
        script_path = backend._resolve_browser_extension_path() / "bookmarklet.js"
        if not script_path.exists():
            raise HTTPException(404, "bookmarklet relay script not found")
        return FileResponse(script_path, media_type="application/javascript")

    @router.get("/api/browser/relay/bookmarklet-poll")
    def api_browser_relay_bookmarklet_poll(
        request: Request,
        callback: str = Query(""),
        version: str = Query("bookmarklet"),
        url: str = Query(""),
        title: str = Query(""),
    ) -> Response:
        _require_relay_owner(request)
        if not re.fullmatch(r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*", callback):
            raise HTTPException(400, "invalid callback")
        backend.browser_relay_state["connected"] = True
        backend.browser_relay_state["last_seen"] = backend._now_ts()
        backend.browser_relay_state["extension_version"] = version or "bookmarklet"
        backend.browser_relay_state["active_tab"] = {
            "id": "bookmarklet",
            "url": url,
            "title": title,
        }
        pending = list(backend.browser_relay_state.get("pending_commands") or [])
        backend.browser_relay_state["pending_commands"] = []
        payload = {
            "ok": True,
            "pending_commands": len(pending),
            "commands": pending,
        }
        return Response(
            f"{callback}({json.dumps(payload, ensure_ascii=False)});",
            media_type="application/javascript",
        )

    @router.post("/api/browser/relay/bookmarklet-result")
    async def api_browser_relay_bookmarklet_result(request: Request) -> dict[str, Any]:
        raw = await request.body()
        try:
            body = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
            raise HTTPException(400, "invalid relay result") from exc
        if not isinstance(body, dict):
            raise HTTPException(400, "invalid relay result")
        return api_browser_relay_result(request, body)

    @router.post("/api/browser/open-extension-folder")
    def api_browser_open_extension_folder(request: Request) -> dict[str, Any]:
        require_operator(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )
        extension_path = backend._resolve_browser_extension_path()
        return open_extension_folder(extension_path)

    @router.get("/api/browser/extension-path")
    def api_browser_extension_path(request: Request) -> dict[str, Any]:
        require_operator(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )
        extension_path = backend._resolve_browser_extension_path()
        return {"path": str(extension_path), "exists": extension_path.exists()}

    @router.get("/api/browser-artifacts/{filename}")
    def serve_browser_artifact(request: Request, filename: str) -> Any:
        principal = _principal(request)
        fpath = resolve_browser_artifact_path(
            filename,
            principal=principal,
            require_auth=require_auth,
            authorize_legacy=lambda: require_operator(
                request,
                identity_store,
                require_auth,
                jwt_secret=jwt_secret,
                jwt_issuer=jwt_issuer,
                jwt_audience=jwt_audience,
            ),
        )
        return FileResponse(str(fpath), media_type="image/png")

    return router
