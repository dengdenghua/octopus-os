"""Browser navigation / screenshot helpers for the browser router backend.

Pure structural split of ``_browser_router_helpers``: page-title fetch,
history navigation (navigate / back / forward / reload) and the screenshot
placeholder. Exposed as ``_NavigationBackendMixin`` — ``_BrowserBackend``
inherits it. No logic changes.
"""

from __future__ import annotations

import base64
import html
import urllib.error
from typing import Any


class _NavigationBackendMixin:
    """Browser navigation / screenshot helpers shared by the browser backend."""

    def _page_title_for_url(self, url: str) -> str:
        # SSRF + DNS-rebinding guard. ``safe_urlopen`` resolves once
        # and pins the connect target to the approved IP so a hostile
        # DNS can't swap in an internal address between check and
        # fetch.
        try:
            from runtime.safety.auth.url_guard import safe_urlopen
        except Exception:  # noqa: BLE001 — best-effort; fail-open
            return url
        try:
            raw, headers = safe_urlopen(
                url,
                timeout=5.0,
                read_cap_bytes=32768,
                allow_private=False,
            )
        except (ValueError, urllib.error.URLError, TimeoutError, OSError):
            return url
        content_type = headers.get("Content-Type", "")
        # safe_urlopen stripped the charset hint from the original
        # HTTPResponse; fall back to UTF-8.
        text = raw.decode("utf-8", errors="replace")
        lower = text.lower()
        start = lower.find("<title>")
        end = lower.find("</title>")
        if start != -1 and end != -1 and end > start:
            return text[start + 7 : end].strip()
        if "text/plain" in content_type:
            return url
        return url

    def _navigate_browser_session(self, session: dict[str, Any], url: str) -> dict[str, str]:
        if self._ensure_real_browser_session(session):
            page = session.get("page")
            if page is not None:
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=10_000)
                    title = page.title()
                    final_url = page.url
                    history = list(session.get("history", []))
                    history_index = int(session.get("history_index", -1))
                    if history_index + 1 < len(history):
                        history = history[: history_index + 1]
                    history.append({"url": final_url, "title": title})
                    session["history"] = history
                    session["history_index"] = len(history) - 1
                    session["current_url"] = final_url
                    session["current_title"] = title
                    self._record_browser_action(session, "navigate", final_url)
                    return {"url": final_url, "title": title}
                except self._browser_runtime_errors():
                    self._close_real_browser_session(session)
        title = self._page_title_for_url(url)
        history = list(session.get("history", []))
        history_index = int(session.get("history_index", -1))
        if history_index + 1 < len(history):
            history = history[: history_index + 1]
        history.append({"url": url, "title": title})
        session["history"] = history
        session["history_index"] = len(history) - 1
        session["current_url"] = url
        session["current_title"] = title
        self._record_browser_action(session, "navigate", url)
        return {"url": url, "title": title}

    def _move_browser_history(self, session: dict[str, Any], delta: int) -> dict[str, str]:
        if self._ensure_real_browser_session(session):
            page = session.get("page")
            if page is not None:
                try:
                    if delta < 0:
                        page.go_back(wait_until="domcontentloaded", timeout=10_000)
                    else:
                        page.go_forward(wait_until="domcontentloaded", timeout=10_000)
                    session["current_url"] = page.url
                    session["current_title"] = page.title()
                    action_name = "back" if delta < 0 else "forward"
                    self._record_browser_action(session, action_name, session["current_url"])
                    return {"url": session["current_url"], "title": session["current_title"]}
                except self._browser_runtime_errors():
                    self._close_real_browser_session(session)
        history = list(session.get("history", []))
        if not history:
            return {"url": "", "title": ""}
        current_index = int(session.get("history_index", len(history) - 1))
        target_index = max(0, min(len(history) - 1, current_index + delta))
        entry = history[target_index]
        session["history_index"] = target_index
        session["current_url"] = str(entry.get("url") or "")
        session["current_title"] = str(entry.get("title") or "")
        action_name = "back" if delta < 0 else "forward"
        self._record_browser_action(session, action_name, session["current_url"])
        return {"url": session["current_url"], "title": session["current_title"]}

    def _reload_browser_session(self, session: dict[str, Any]) -> dict[str, str]:
        if self._ensure_real_browser_session(session):
            page = session.get("page")
            if page is not None:
                try:
                    page.reload(wait_until="domcontentloaded", timeout=10_000)
                    session["current_url"] = page.url
                    session["current_title"] = page.title()
                    self._record_browser_action(session, "reload", session["current_url"])
                    return {"url": session["current_url"], "title": session["current_title"]}
                except self._browser_runtime_errors():
                    self._close_real_browser_session(session)
        url = str(session.get("current_url") or "")
        if not url:
            return {"url": "", "title": ""}
        title = self._page_title_for_url(url)
        session["current_title"] = title
        history = list(session.get("history", []))
        index = int(session.get("history_index", -1))
        if 0 <= index < len(history):
            history[index] = {"url": url, "title": title}
            session["history"] = history
        self._record_browser_action(session, "reload", url)
        return {"url": url, "title": title}

    def _browser_screenshot_payload(self, session: dict[str, Any]) -> dict[str, Any]:
        width, height = self._session_viewport(session)
        if self._ensure_real_browser_session(session):
            page = session.get("page")
            if page is not None:
                try:
                    image = page.screenshot(full_page=True, type="png")
                    return {
                        "base64": base64.b64encode(image).decode("ascii"),
                        "width": width,
                        "height": height,
                    }
                except self._browser_runtime_errors():
                    self._close_real_browser_session(session)
        title = html.escape(str(session.get("current_title") or "Echo Browser Session"))
        url = html.escape(
            str(session.get("current_url") or "Navigate to a URL to start browser automation")
        )
        action_count = int(session.get("action_count", 0))
        svg = f"""
<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <defs>
    <linearGradient id="bg" x1="0" x2="1" y1="0" y2="1">
      <stop offset="0%" stop-color="#0f172a" />
      <stop offset="100%" stop-color="#1e293b" />
    </linearGradient>
  </defs>
  <rect width="{width}" height="{height}" fill="url(#bg)" />
  <rect x="40" y="36" width="{width - 80}" height="{height - 72}" rx="24" fill="#f8fafc" opacity="0.96" />
  <rect x="72" y="78" width="{width - 144}" height="52" rx="16" fill="#e2e8f0" />
  <circle cx="96" cy="104" r="8" fill="#ef4444" />
  <circle cx="124" cy="104" r="8" fill="#f59e0b" />
  <circle cx="152" cy="104" r="8" fill="#22c55e" />
  <text x="188" y="111" fill="#334155" font-size="20" font-family="Segoe UI, Arial, sans-serif">{url}</text>
  <text x="88" y="204" fill="#0f172a" font-size="34" font-weight="700" font-family="Segoe UI, Arial, sans-serif">{title}</text>
  <text x="88" y="254" fill="#475569" font-size="22" font-family="Segoe UI, Arial, sans-serif">Interactive preview placeholder rendered by the compatibility backend.</text>
  <text x="88" y="292" fill="#475569" font-size="22" font-family="Segoe UI, Arial, sans-serif">Actions recorded: {action_count}</text>
  <text x="88" y="330" fill="#475569" font-size="22" font-family="Segoe UI, Arial, sans-serif">Session: {html.escape(str(session.get("session_id") or ""))}</text>
</svg>
""".strip()
        return {
            "base64": base64.b64encode(svg.encode("utf-8")).decode("ascii"),
            "width": width,
            "height": height,
        }
