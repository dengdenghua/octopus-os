"""WebDriverAgent (WDA) HTTP client.

Thin async wrapper over the WDA HTTP API that runs on the iOS device.
WDA is the de-facto standard for iOS automation (used by Facebook's
WebDriverAgent, Appium's XCUITest driver, and tidevice/tinspin).

Default endpoint: ``http://localhost:8100`` (USB-forwarded to the device
via ``iproxy 8100 8100`` or ``tidevice wdaproxy``). Networked devices can
be reached directly at ``http://<device-ip>:8100``.

This module only depends on the standard library ``urllib`` to avoid a
hard dependency on ``httpx`` / ``aiohttp`` — keeping the tentacle runtime
installable in minimal environments. An ``asyncio`` executor runs the
blocking HTTP calls off the event loop.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urljoin, urlsplit

logger = logging.getLogger(__name__)

# Default WDA HTTP port (iproxy/tidevice forward this to the device)
DEFAULT_WDA_PORT = 8100
# Sensible default request timeout for WDA calls. WDA itself caps operations
# at ~60s; we stay below that so the Tentacle layer can surface timeouts.
DEFAULT_WDA_TIMEOUT = 30.0


class WdaError(RuntimeError):
    """Raised when a WDA HTTP call fails or returns a non-OK payload."""

    def __init__(self, message: str, *, status: int | None = None, payload: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.payload = payload


class WdaClient:
    """Async WebDriverAgent HTTP client.

    Lifecycle::

        client = WdaClient(base_url="http://localhost:8100")
        await client.connect()          # create session
        await client.tap(100, 200)      # coordinate tap
        screenshot = await client.screenshot()  # base64 PNG
        await client.disconnect()       # close session

    All methods are coroutines; blocking I/O is offloaded via
    ``asyncio.to_thread`` so the event loop is never stalled.
    """

    def __init__(
        self,
        base_url: str = f"http://localhost:{DEFAULT_WDA_PORT}",
        *,
        bundle_id: str | None = None,
        timeout: float = DEFAULT_WDA_TIMEOUT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.bundle_id = bundle_id
        self.timeout = timeout
        self._session_id: str | None = None

    # ── lifecycle ──────────────────────────────────────────

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def is_connected(self) -> bool:
        return self._session_id is not None

    async def connect(self) -> str:
        """Create a WDA session and (optionally) launch the bundle id.

        Returns the session id. If a session already exists it is reused.
        """
        if self._session_id is not None:
            return self._session_id

        payload: dict[str, Any] = {
            "capabilities": {
                "alwaysMatch": {
                    "platformName": "iOS",
                    "automationName": "XCUITest",
                },
                "firstMatch": [{}],
            }
        }
        if self.bundle_id:
            payload["capabilities"]["alwaysMatch"]["bundleId"] = self.bundle_id

        data = await self._request("POST", "/session", payload=payload)
        # WDA returns {"value": {"sessionId": "...", ...}, "sessionId": "..."}
        sid = data.get("sessionId") or (data.get("value") or {}).get("sessionId")
        if not sid:
            raise WdaError("WDA session response missing sessionId", payload=data)
        self._session_id = str(sid)
        logger.info("WDA session created: %s", self._session_id)
        return self._session_id

    async def disconnect(self) -> None:
        """Close the WDA session."""
        if self._session_id is None:
            return
        sid = self._session_id
        self._session_id = None
        try:
            await self._request("DELETE", f"/session/{sid}")
            logger.info("WDA session closed: %s", sid)
        except WdaError as exc:
            logger.warning("WDA session close failed: %s", exc)

    # ── status / health ────────────────────────────────────

    async def status(self) -> dict[str, Any]:
        """Return WDA ``/status`` payload (health check)."""
        data = await self._request("GET", "/status")
        return data.get("value", data)

    # ── interaction primitives ─────────────────────────────

    async def tap(self, x: int, y: int) -> dict[str, Any]:
        """Tap at absolute screen coordinates."""
        return await self._session_post("/wda/tap/0", {"x": x, "y": y})

    async def double_tap(self, x: int, y: int) -> dict[str, Any]:
        """Double-tap at absolute screen coordinates."""
        return await self._session_post("/wda/doubleTap", {"x": x, "y": y})

    async def long_press(self, x: int, y: int, duration: float = 2.0) -> dict[str, Any]:
        """Touch and hold at coordinates for ``duration`` seconds."""
        return await self._session_post(
            "/wda/touchAndHold",
            {"x": x, "y": y, "duration": duration},
        )

    async def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration: float = 0.5,
    ) -> dict[str, Any]:
        """Swipe from (x1, y1) to (x2, y2) over ``duration`` seconds."""
        return await self._session_post(
            "/wda/dragfromtoforduration",
            {
                "fromX": x1,
                "fromY": y1,
                "toX": x2,
                "toY": y2,
                "duration": duration,
            },
        )

    async def input_text(self, text: str) -> dict[str, Any]:
        """Type text into the currently focused element."""
        return await self._session_post("/wda/keys", {"value": list(text)})

    async def home(self) -> dict[str, Any]:
        """Press the device Home button (returns to springboard)."""
        return await self._session_post("/wda/homescreen", {})

    # ── element lookup ─────────────────────────────────────

    async def find_element(
        self,
        *,
        accessibility_id: str | None = None,
        class_name: str | None = None,
        xpath: str | None = None,
        partial: bool = False,
    ) -> dict[str, Any]:
        """Find a UI element by one of the supported WDA locators.

        Returns the WDA element descriptor (contains ``ELEMENT`` id).
        Raises :class:`WdaError` if no element matches.
        """
        using_value = _resolve_locator(
            accessibility_id=accessibility_id,
            class_name=class_name,
            xpath=xpath,
            partial=partial,
        )
        payload = {"using": using_value.using, "value": using_value.value}
        return await self._session_post("/element", payload)

    async def source(self) -> dict[str, Any]:
        """Return the accessibility tree (XML or JSON depending on WDA build)."""
        return await self._session_get("/source")

    # ── screenshot ─────────────────────────────────────────

    async def screenshot(self) -> str:
        """Capture the screen and return a base64-encoded PNG string."""
        data = await self._session_get("/screenshot")
        value = data.get("value")
        if isinstance(value, str):
            return value
        # some WDA builds wrap base64 inside {"value": <b64>}
        if isinstance(value, dict):
            b64 = value.get("screenshot") or value.get("base64")
            if isinstance(b64, str):
                return b64
        raise WdaError("WDA screenshot returned no base64 payload", payload=data)

    # ── app management ─────────────────────────────────────

    async def launch_app(self, bundle_id: str) -> dict[str, Any]:
        """Launch an app by bundle id (e.g. ``com.apple.mobilesafari``)."""
        return await self._session_post(
            "/wda/apps/launch",
            {"bundleId": bundle_id},
        )

    async def terminate_app(self, bundle_id: str) -> dict[str, Any]:
        """Terminate an app by bundle id."""
        return await self._session_post(
            "/wda/apps/terminate",
            {"bundleId": bundle_id},
        )

    async def active_app_info(self) -> dict[str, Any]:
        """Return information about the currently active app."""
        return await self._session_get("/wda/activeAppInfo")

    # ── window / screen info ───────────────────────────────

    async def window_size(self) -> tuple[int, int]:
        """Return ``(width, height)`` of the screen in points."""
        data = await self._session_get("/window/size")
        value = data.get("value", data)
        if isinstance(value, dict):
            return int(value.get("width", 0)), int(value.get("height", 0))
        raise WdaError("WDA window/size returned no dimensions", payload=data)

    # ── low-level HTTP helpers ─────────────────────────────

    async def _session_post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self._session_id is None:
            await self.connect()
        sid = self._session_id or ""
        return await self._request("POST", f"/session/{sid}{path}", payload=payload)

    async def _session_get(self, path: str) -> dict[str, Any]:
        if self._session_id is None:
            await self.connect()
        sid = self._session_id or ""
        return await self._request("GET", f"/session/{sid}{path}")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        import asyncio

        url = urljoin(self.base_url + "/", path.lstrip("/"))
        body: bytes | None = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
        # Blocking urllib call offloaded to a worker thread.
        return await asyncio.to_thread(self._sync_request, method, url, body)

    def _sync_request(self, method: str, url: str, body: bytes | None) -> dict[str, Any]:
        if urlsplit(url).scheme not in {"http", "https"}:
            raise WdaError("WDA endpoint must use http or https")
        req = urllib.request.Request(url=url, data=body, method=method)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        try:
            # The scheme is explicitly restricted above; WDA may be local HTTP or device HTTPS.
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # nosec B310
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
            try:
                err_payload = json.loads(err_body) if err_body else None
            except json.JSONDecodeError:
                err_payload = err_body
            raise WdaError(
                f"WDA {method} {url} failed: HTTP {exc.code}",
                status=exc.code,
                payload=err_payload,
            ) from exc
        except urllib.error.URLError as exc:
            raise WdaError(f"WDA {method} {url} unreachable: {exc.reason}") from exc

        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise WdaError(f"WDA returned non-JSON response: {exc}") from exc


# ── locator resolution helper ─────────────────────────────────


class _Locator:
    __slots__ = ("using", "value")

    def __init__(self, using: str, value: str) -> None:
        self.using = using
        self.value = value


def _resolve_locator(
    *,
    accessibility_id: str | None,
    class_name: str | None,
    xpath: str | None,
    partial: bool,
) -> _Locator:
    """Pick the first non-None locator and map it to WDA ``using``/``value``."""
    if accessibility_id:
        # WDA supports both 'accessibility id' and 'name' (legacy) — use the
        # canonical 'accessibility id' which is the Appium-standard name.
        return _Locator("accessibility id", accessibility_id)
    if class_name:
        return _Locator("class name", class_name)
    if xpath:
        return _Locator("xpath", xpath)
    raise WdaError("find_element requires one of accessibility_id/class_name/xpath")


__all__ = [
    "DEFAULT_WDA_PORT",
    "DEFAULT_WDA_TIMEOUT",
    "WdaClient",
    "WdaError",
]
