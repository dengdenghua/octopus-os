"""Real BrowserBackend adapters over the three automation tracks.

Each adapter is a thin, uniform shell over an existing track:

* :class:`ElectronBackend` → ``browser_act_skills._bridge_call`` (the
  desktop webview bridge; already returns ``{"ok": ...}``).
* :class:`PlaywrightBackend` → the stateless ``browser_skills._browser_*``
  handlers, threaded with a remembered ``current_url`` so click/type/…
  act on the page the agent last navigated to.
* :class:`ExtensionBackend` → the relay command queue in
  ``browser_router`` (acts on the user's own live tab).

Every adapter takes an injectable transport so its mapping logic is
unit-testable with a stub — no Electron app, Playwright install, or
connected extension required. The defaults bind to the real track
functions, so wiring is a one-liner at the call site:
``ElectronBackend()`` / ``PlaywrightBackend()`` / ``ExtensionBackend()``.

End-to-end verification of the live tracks needs their runtimes and is
out of scope here; what's tested is that each adapter maps the seven
``BrowserBackend`` verbs to the correct track action and normalises the
result.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from runtime.execution.suckers.browser_backend import BrowserResult, Track

# A transport takes (action, payload) and returns the track's native
# response dict. This is exactly the shape of Electron's _bridge_call;
# the other two adapters wrap their tracks into the same shape.
Transport = Callable[[str, dict[str, Any]], dict[str, Any]]

_DEFAULT_GATEWAY_BASE_URL = "http://127.0.0.1:8000"
_BROWSER_RELAY_API_PATH = "/api/browser/relay"
_BROWSER_RELAY_BASE_URL_ENV_KEYS = (
    "ECHO_BROWSER_RELAY_BASE_URL",
    "ECHO_INTERNAL_GATEWAY_BASE_URL",
    "ECHO_PUBLIC_BASE_URL",
)
_BROWSER_RELAY_TOKEN_ENV_KEYS = (
    "ECHO_BROWSER_RELAY_TOKEN",
    "ECHO_GATEWAY_TOKEN",
)
_BROWSER_RELAY_TIMEOUT_SECONDS = 10


def _selected_relay_target() -> dict[str, str] | None:
    """Return the trusted per-turn Chrome target selected by the operator."""

    try:
        from runtime.platform.process.session import current_session

        session = current_session()
        metadata = getattr(session, "metadata", None) if session is not None else None
        raw = (metadata or {}).get("automation_target")
        if not isinstance(raw, dict):
            return None
        if raw.get("kind") != "browser_tab" or raw.get("source") != "browser_relay":
            return None
        target_id = str(raw.get("id") or "").strip()
        if not target_id:
            return None
        return {
            "target_tab_id": target_id,
            "target_tab_url": str(raw.get("url") or "").strip(),
            "target_tab_title": str(raw.get("title") or "").strip(),
        }
    except (AttributeError, TypeError, ImportError):
        return None


def browser_relay_diagnostics() -> dict[str, Any]:
    raw, source = _configured_browser_relay_base_url()
    token, token_source = _configured_browser_relay_token()
    try:
        base_url = _normalize_browser_relay_base_url(raw)
        error = ""
    except ValueError as exc:
        base_url = _normalize_browser_relay_base_url(_DEFAULT_GATEWAY_BASE_URL)
        error = str(exc)
    return {
        "schema": "echo.browser_relay_bridge.v1",
        "base_url": base_url,
        "configured_by": source,
        "env_keys": list(_BROWSER_RELAY_BASE_URL_ENV_KEYS),
        "default_gateway_base_url": _DEFAULT_GATEWAY_BASE_URL,
        "auth_configured": bool(token),
        "auth_configured_by": token_source,
        "auth_env_keys": list(_BROWSER_RELAY_TOKEN_ENV_KEYS),
        "error": error,
    }


def _configured_browser_relay_base_url() -> tuple[str, str]:
    for key in _BROWSER_RELAY_BASE_URL_ENV_KEYS:
        value = os.environ.get(key, "").strip()
        if value:
            return value, key
    return _DEFAULT_GATEWAY_BASE_URL, "default"


def _configured_browser_relay_token() -> tuple[str, str]:
    for key in _BROWSER_RELAY_TOKEN_ENV_KEYS:
        value = os.environ.get(key, "").strip()
        if value:
            return value, key
    return "", ""


def _normalize_browser_relay_base_url(raw: str) -> str:
    value = raw.strip().rstrip("/")
    if not value:
        value = _DEFAULT_GATEWAY_BASE_URL
    parsed = urllib_parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"invalid browser relay base url: {raw!r}")
    path = parsed.path.rstrip("/")
    if path.endswith(_BROWSER_RELAY_API_PATH):
        normalized_path = path
    elif path.endswith("/api/browser"):
        normalized_path = f"{path}/relay"
    elif path.endswith("/api"):
        normalized_path = f"{path}/browser/relay"
    else:
        normalized_path = f"{path}{_BROWSER_RELAY_API_PATH}"
    return urllib_parse.urlunparse(
        parsed._replace(path=normalized_path, params="", query="", fragment=""),
    )


def _browser_relay_request(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    timeout_seconds: float = _BROWSER_RELAY_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    diagnostics = browser_relay_diagnostics()
    base_url = str(diagnostics["base_url"])
    route = path if path.startswith("/") else f"/{path}"
    url = f"{base_url}{route}"
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    token, _token_source = _configured_browser_relay_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib_request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib_request.urlopen(req, timeout=timeout_seconds) as resp:  # nosec B310 — audited HTTP relay endpoint
            raw = resp.read()
            payload = json.loads(raw.decode("utf-8"))
            if isinstance(payload, dict):
                payload.setdefault("browser_relay", diagnostics)
                return payload
            return {"ok": True, "data": payload, "browser_relay": diagnostics}
    except urllib_error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8")
        except Exception:  # noqa: BLE001
            detail = exc.reason
        return {
            "ok": False,
            "error": f"browser relay http {exc.code}: {detail}",
            "browser_relay": diagnostics,
        }
    except urllib_error.URLError as exc:
        return {
            "ok": False,
            "error": f"browser relay unreachable at {base_url}: {exc.reason}",
            "browser_relay": diagnostics,
        }
    except (TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "error": f"browser relay failed: {type(exc).__name__}: {exc}",
            "browser_relay": diagnostics,
        }


# ── Electron webview ─────────────────────────────────────────────


class ElectronBackend:
    track = Track.ELECTRON

    def __init__(
        self,
        transport: Transport | None = None,
        *,
        available_probe: Callable[[], bool] | None = None,
    ) -> None:
        self._transport = transport or self._default_transport
        self._available_probe = available_probe or self._default_available

    @staticmethod
    def _default_transport(action: str, payload: dict[str, Any]) -> dict[str, Any]:
        from runtime.execution.suckers.browser_act_skills import _bridge_call

        return _bridge_call(action, payload)

    @staticmethod
    def _default_available() -> bool:
        from runtime.execution.suckers.browser_act_skills import _bridge_status

        # A stale bridge.json must not capture the request and return a hard
        # error. Only advertise Electron when the authenticated desktop bridge
        # is alive and has a targetable browser surface.
        return _bridge_status() is not None

    def available(self) -> bool:
        return bool(self._available_probe())

    def _call(self, action: str, payload: dict[str, Any]) -> BrowserResult:
        return BrowserResult.from_track(self.track, self._transport(action, payload))

    def navigate(self, url: str) -> BrowserResult:
        return self._call("navigate", {"url": url})

    def click(self, selector: str) -> BrowserResult:
        return self._call("click", {"selector": selector})

    def type(self, selector: str, text: str, *, clear: bool = False) -> BrowserResult:
        return self._call("type", {"selector": selector, "text": text, "clear": clear})

    def scroll(self, *, selector: str | None = None, delta_y: int = 0) -> BrowserResult:
        payload: dict[str, Any] = {}
        if selector:
            payload["selector"] = selector
        if delta_y:
            payload["deltaY"] = int(delta_y)
        return self._call("scroll", payload)

    def wait(self, selector: str, *, timeout_ms: int = 10_000) -> BrowserResult:
        return self._call("wait", {"selector": selector, "timeout": int(timeout_ms)})

    def state(self, *, max_items: int = 30) -> BrowserResult:
        # The live bridge derives state via injected JS; the bridge
        # exposes it under the same "state" action used elsewhere.
        return self._call("state", {"max_items": int(max_items)})

    def extract(self) -> BrowserResult:
        return self._call("extract", {})

    def screenshot(self, path: str = "", *, full_page: bool = False) -> BrowserResult:
        return self._call("screenshot", {})


# ── Playwright (headless, stateless per call) ────────────────────


class PlaywrightBackend:
    track = Track.PLAYWRIGHT

    def __init__(
        self,
        dispatch: Transport | None = None,
        *,
        available_probe: Callable[[], bool] | None = None,
        start_url: str = "about:blank",
    ) -> None:
        self._dispatch = dispatch or self._default_dispatch
        self._available_probe = available_probe or self._default_available
        self._current_url = start_url

    @staticmethod
    def _default_available() -> bool:
        try:
            from runtime.execution.suckers.browser_skills import PLAYWRIGHT_AVAILABLE

            return bool(PLAYWRIGHT_AVAILABLE)
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _default_dispatch(action: str, payload: dict[str, Any]) -> dict[str, Any]:
        # Route each verb to its stateless browser_skills handler. The
        # handlers take a ``url`` (the page to operate on) plus their
        # own kwargs and return a payload dict (``error`` key on fail).
        from runtime.execution.suckers import browser_skills as bs

        handlers: dict[str, Callable[..., dict[str, Any]]] = {
            "navigate": bs._browser_navigate,
            "click": bs._browser_click,
            "type": bs._browser_type,
            "scroll": bs._browser_scroll,
            "wait": bs._browser_wait,
            "state": bs._browser_state,
            "screenshot": bs._browser_screenshot,
            # extract() means "read the page's text". _browser_extract is
            # a CSS-selector scraper that REQUIRES a selector and errors
            # without one; _browser_get returns title + inner text, which
            # is what the BrowserBackend.extract contract wants.
            "extract": bs._browser_get,
        }
        handler = handlers.get(action)
        if handler is None:
            return {"error": f"unsupported action: {action}"}
        return handler(**payload)

    def available(self) -> bool:
        return bool(self._available_probe())

    def _call(self, action: str, payload: dict[str, Any]) -> BrowserResult:
        result = BrowserResult.from_track(self.track, self._dispatch(action, payload))
        # Keep current_url fresh from whatever the handler reports.
        if result.data:
            new_url = result.data.get("final_url") or result.data.get("url")
            if isinstance(new_url, str) and new_url:
                self._current_url = new_url
        return result

    def navigate(self, url: str) -> BrowserResult:
        self._current_url = url
        return self._call("navigate", {"url": url})

    def click(self, selector: str) -> BrowserResult:
        return self._call("click", {"url": self._current_url, "selector": selector})

    def type(self, selector: str, text: str, *, clear: bool = False) -> BrowserResult:
        return self._call(
            "type",
            {"url": self._current_url, "selector": selector, "text": text, "clear_first": clear},
        )

    def scroll(self, *, selector: str | None = None, delta_y: int = 0) -> BrowserResult:
        payload: dict[str, Any] = {"url": self._current_url}
        if selector:
            payload["to_selector"] = selector
        if delta_y:
            payload["to_y"] = int(delta_y)
        return self._call("scroll", payload)

    def wait(self, selector: str, *, timeout_ms: int = 10_000) -> BrowserResult:
        return self._call(
            "wait",
            {"url": self._current_url, "selector": selector, "timeout_ms": int(timeout_ms)},
        )

    def state(self, *, max_items: int = 30) -> BrowserResult:
        return self._call("state", {"url": self._current_url, "max_items": int(max_items)})

    def extract(self) -> BrowserResult:
        return self._call("extract", {"url": self._current_url})

    def screenshot(self, path: str = "", *, full_page: bool = False) -> BrowserResult:
        return self._call(
            "screenshot",
            {
                "url": self._current_url,
                "path": path,
                "full_page": bool(full_page),
            },
        )


# ── Extension relay (the user's own live browser) ────────────────


class ExtensionBackend:
    track = Track.EXTENSION

    def __init__(
        self,
        transport: Transport | None = None,
        *,
        available_probe: Callable[[], bool] | None = None,
    ) -> None:
        self._transport = transport or self._default_transport
        self._available_probe = available_probe or self._default_available

    def available(self) -> bool:
        return bool(self._available_probe())

    @staticmethod
    def _default_available() -> bool:
        status = _browser_relay_request(
            "GET",
            "/status",
            timeout_seconds=2,
        )
        return bool(status.get("connected"))

    @staticmethod
    def _default_transport(action: str, payload: dict[str, Any]) -> dict[str, Any]:
        request_payload = {
            "action": action,
            **payload,
        }
        selected_target = _selected_relay_target()
        if selected_target is not None:
            request_payload.update(selected_target)
        action_timeout_ms = int(payload.get("timeout") or 0)
        command_timeout_seconds = 0.0
        request_timeout_seconds = _BROWSER_RELAY_TIMEOUT_SECONDS
        if action_timeout_ms > 0:
            command_timeout_seconds = max(8.0, action_timeout_ms / 1000 + 1)
            request_payload["timeout_seconds"] = command_timeout_seconds
            request_timeout_seconds = max(
                _BROWSER_RELAY_TIMEOUT_SECONDS,
                command_timeout_seconds + 2,
            )
        result = _browser_relay_request(
            "POST",
            "/command",
            request_payload,
            timeout_seconds=request_timeout_seconds,
        )
        if isinstance(result, dict):
            result.setdefault("track", "extension")
        return result

    def _call(self, action: str, payload: dict[str, Any]) -> BrowserResult:
        return BrowserResult.from_track(self.track, self._transport(action, payload))

    def navigate(self, url: str) -> BrowserResult:
        return self._call("navigate", {"url": url})

    def click(self, selector: str) -> BrowserResult:
        return self._call("click", {"selector": selector})

    def type(self, selector: str, text: str, *, clear: bool = False) -> BrowserResult:
        return self._call("type", {"selector": selector, "text": text, "clear": clear})

    def scroll(self, *, selector: str | None = None, delta_y: int = 0) -> BrowserResult:
        payload: dict[str, Any] = {}
        if selector:
            payload["selector"] = selector
        if delta_y:
            payload["deltaY"] = int(delta_y)
        return self._call("scroll", payload)

    def wait(self, selector: str, *, timeout_ms: int = 10_000) -> BrowserResult:
        return self._call("wait", {"selector": selector, "timeout": int(timeout_ms)})

    def state(self, *, max_items: int = 30) -> BrowserResult:
        return self._call("state", {"max_items": int(max_items)})

    def extract(self) -> BrowserResult:
        return self._call("extract", {})

    def screenshot(self, path: str = "", *, full_page: bool = False) -> BrowserResult:
        return self._call("screenshot", {})


__all__ = ["ElectronBackend", "PlaywrightBackend", "ExtensionBackend"]
