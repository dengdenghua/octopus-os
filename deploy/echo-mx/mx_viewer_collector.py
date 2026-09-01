"""Capture MX messages from one long-lived, push-driven Viewer session.

The collector never receives the real MX token. It opens the loopback-only
Viewer once after the guardian reports a healthy session, then lets the
official SPA keep its normal Socket.IO connection alive. A DOM observer turns
socket-driven room-summary changes into a bounded queue; only changed rooms are
opened and captured. There is no periodic page reload, room enumeration, or
history backfill in this service.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import deque

import httpx

LOGGER = logging.getLogger("echo.mx_collector")
VIEWER_URL = os.environ.get("MX_COLLECTOR_VIEWER_URL", "http://127.0.0.1:8092/viewer")
HEALTH_URL = os.environ.get("MX_COLLECTOR_HEALTH_URL", "http://127.0.0.1:8092/healthz")
SESSION_RECHECK_SECONDS = max(
    30, min(int(os.environ.get("MX_COLLECTOR_SESSION_RECHECK_SECONDS", "300")), 900)
)
HEARTBEAT_SECONDS = max(
    60, min(int(os.environ.get("MX_COLLECTOR_HEARTBEAT_SECONDS", "900")), 1800)
)
EVENT_SETTLE_SECONDS = max(
    1.0, min(float(os.environ.get("MX_COLLECTOR_EVENT_SETTLE_SECONDS", "2.0")), 10.0)
)
MIN_ROOM_REFRESH_SECONDS = max(
    2.0, min(float(os.environ.get("MX_COLLECTOR_MIN_ROOM_REFRESH_SECONDS", "5.0")), 60.0)
)
ROOM_COOLDOWN_SECONDS = max(
    5.0, min(float(os.environ.get("MX_COLLECTOR_ROOM_COOLDOWN_SECONDS", "30.0")), 300.0)
)
RATE_WINDOW_SECONDS = max(
    60, min(int(os.environ.get("MX_COLLECTOR_RATE_WINDOW_SECONDS", "600")), 3600)
)
MAX_ROOM_REFRESHES_PER_WINDOW = max(
    1, min(int(os.environ.get("MX_COLLECTOR_MAX_REFRESHES_PER_WINDOW", "30")), 120)
)
MAX_PENDING_ROOMS = 256
ROOM_CHANGE_BINDING = "__echoMxRoomChanged"

ROOM_WATCHER_JS = r"""
() => {
  const key = '__echoMxPushWatcher';
  if (window[key] && typeof window[key].stop === 'function') window[key].stop();
  const selector = '.cu-list.menu-avatar .cu-item[id^="room"]';
  const signatures = new Map();
  const pending = new Set();
  let scheduled = false;
  let stopped = false;

  function roomId(node) {
    const element = node && (node.nodeType === 1 ? node : node.parentElement);
    const room = element && element.closest && element.closest(selector);
    return room && room.id ? room.id.slice(4) : '';
  }
  function signature(room) {
    const title = room.querySelector('.text-black.text-bold');
    const activeAt = room.querySelector('.text-grey.text-xs');
    let preview = '';
    room.querySelectorAll('.text-gray,.text-grey').forEach((node) => {
      const value = String(node.textContent || '').trim();
      if (value && value !== String(activeAt && activeAt.textContent || '').trim() &&
          value !== String(title && title.textContent || '').trim() && value.length > preview.length) {
        preview = value;
      }
    });
    return [
      String(title && title.textContent || '').trim(),
      String(activeAt && activeAt.textContent || '').trim(),
      preview
    ].join('\n');
  }
  function seed() {
    document.querySelectorAll(selector).forEach((room) => {
      if (room.id) signatures.set(room.id.slice(4), signature(room));
    });
  }
  function flush() {
    scheduled = false;
    if (stopped) return;
    pending.forEach((id) => {
      pending.delete(id);
      const room = document.getElementById('room' + id);
      if (!room) return;
      const next = signature(room);
      const previous = signatures.get(id);
      signatures.set(id, next);
      // A room first appearing in the lazy DOM is baseline state, not a push.
      if (previous === undefined || previous === next) return;
      Promise.resolve(window.__echoMxRoomChanged(id)).catch(() => {});
    });
  }
  function schedule(id) {
    if (!id || stopped) return;
    pending.add(id);
    if (scheduled) return;
    scheduled = true;
    window.requestAnimationFrame(flush);
  }
  seed();
  const observer = new MutationObserver((mutations) => {
    mutations.forEach((mutation) => {
      const direct = roomId(mutation.target);
      if (direct) schedule(direct);
      mutation.addedNodes.forEach((node) => {
        const added = roomId(node);
        if (added) schedule(added);
        const element = node && node.nodeType === 1 ? node : null;
        if (element && element.querySelectorAll) {
          element.querySelectorAll(selector).forEach((room) => schedule(room.id.slice(4)));
        }
      });
    });
  });
  observer.observe(document.documentElement, {subtree: true, childList: true, characterData: true});
  window[key] = {
    stop() { stopped = true; observer.disconnect(); pending.clear(); },
    roomCount: signatures.size
  };
  return signatures.size;
}
"""


class SessionNotReady(RuntimeError):
    def __init__(self, state: str) -> None:
        super().__init__(state)
        self.state = state


class RefreshBudget:
    """Bound changed-room fetches even if the upstream UI emits a mutation storm."""

    def __init__(self) -> None:
        self._events: deque[float] = deque()
        self._last_refresh_at = 0.0

    async def acquire(self) -> None:
        while True:
            now = time.monotonic()
            cutoff = now - RATE_WINDOW_SECONDS
            while self._events and self._events[0] <= cutoff:
                self._events.popleft()
            delays = [max(0.0, self._last_refresh_at + MIN_ROOM_REFRESH_SECONDS - now)]
            if len(self._events) >= MAX_ROOM_REFRESHES_PER_WINDOW:
                delays.append(max(0.0, self._events[0] + RATE_WINDOW_SECONDS - now))
            delay = max(delays)
            if delay <= 0:
                stamp = time.monotonic()
                self._last_refresh_at = stamp
                self._events.append(stamp)
                return
            await asyncio.sleep(delay)


async def _require_session(client: httpx.AsyncClient) -> dict[str, object]:
    """Read bridge-local guardian state; the endpoint never probes MX upstream."""

    try:
        response = await client.get(HEALTH_URL)
        payload = response.json()
    except (httpx.HTTPError, OSError, ValueError) as exc:
        raise SessionNotReady("bridge_unavailable") from exc
    if response.status_code != 200 or not isinstance(payload, dict):
        raise SessionNotReady("bridge_unavailable")
    if not payload.get("authenticated"):
        raise SessionNotReady(str(payload.get("state") or "login_required")[:64])
    return payload


async def _capture_visible(page: object) -> None:
    await page.evaluate("window.__echoMxCapture && window.__echoMxCapture()")


async def _viewer_frame(page: object) -> object:
    iframe = page.locator("iframe#frame")
    await iframe.wait_for(state="attached", timeout=30_000)
    frame = iframe.content_frame
    await frame.locator('.cu-list.menu-avatar .cu-item[id^="room"]').first.wait_for(
        state="attached", timeout=45_000
    )
    return frame


async def _activate_changed_room(frame: object, page: object, room_id: str) -> bool:
    action = await frame.evaluate(
        """roomId => {
          const room = document.getElementById('room' + roomId);
          if (!room || room.getAttribute('data-mx-empty-hidden') === '1') return 'missing';
          const active = room.classList.contains('mx-current-room') ||
            String(room.getAttribute('style') || '').includes('#e2e2e2');
          if (active) return 'current';
          room.click();
          return 'clicked';
        }""",
        room_id,
    )
    if action == "missing":
        return False
    await asyncio.sleep(EVENT_SETTLE_SECONDS)
    await _capture_visible(page)
    return True


async def _run_push_session(page: object, health_client: httpx.AsyncClient) -> None:
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=MAX_PENDING_ROOMS)
    pending: set[str] = set()
    cooldowns: dict[str, float] = {}
    budget = RefreshBudget()

    def room_changed(_source: object, raw_room_id: object) -> None:
        room_id = str(raw_room_id or "").strip()
        if not room_id or len(room_id) > 128 or room_id in pending:
            return
        if queue.full():
            LOGGER.warning("MX push queue full; coalescing new room events")
            return
        pending.add(room_id)
        queue.put_nowait(room_id)

    await page.expose_binding(ROOM_CHANGE_BINDING, room_changed)
    # This is the only navigation in a connected session. Reconnects use
    # exponential backoff and never enumerate historical rooms.
    await page.goto(VIEWER_URL, wait_until="domcontentloaded", timeout=45_000)
    frame = await _viewer_frame(page)
    room_count = await frame.evaluate(ROOM_WATCHER_JS)
    await _capture_visible(page)
    LOGGER.info("MX push collector connected baseline_rooms=%d", int(room_count or 0))

    while True:
        try:
            room_id = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
        except TimeoutError:
            await _require_session(health_client)
            if page.is_closed():
                raise RuntimeError("viewer page closed") from None
            watcher_ready = await frame.evaluate(
                "Boolean(window.__echoMxPushWatcher && window.__echoMxPushWatcher.roomCount >= 0)"
            )
            if not watcher_ready:
                raise RuntimeError("push watcher detached") from None
            LOGGER.info("MX push collector heartbeat healthy")
            continue

        pending.discard(room_id)
        queue.task_done()
        now = time.monotonic()
        if cooldowns.get(room_id, 0.0) > now:
            continue
        await budget.acquire()
        try:
            captured = await _activate_changed_room(frame, page, room_id)
        except Exception:  # noqa: BLE001 - reconnect handles detached SPA frames
            LOGGER.warning("MX changed-room capture failed room=%s", room_id[:24])
            raise
        if captured:
            cooldowns[room_id] = time.monotonic() + ROOM_COOLDOWN_SECONDS
            LOGGER.info("MX pushed room captured room=%s", room_id[:24])


async def run_forever() -> None:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - deployment dependency
        raise RuntimeError("playwright is required for the MX collector") from exc

    reconnect_backoff = 30
    last_session_state = ""
    async with async_playwright() as playwright, httpx.AsyncClient(
        timeout=httpx.Timeout(connect=5, read=15, write=10, pool=5),
        follow_redirects=False,
        trust_env=False,
    ) as health_client:
        while True:
            browser = None
            context = None
            page = None
            try:
                await _require_session(health_client)
                if last_session_state and last_session_state != "healthy":
                    LOGGER.info("MX session recovered; opening one push session")
                last_session_state = "healthy"
                browser = await playwright.chromium.launch(
                    headless=True,
                    args=["--disable-dev-shm-usage", "--no-first-run", "--disable-gpu"],
                )
                context = await browser.new_context(
                    viewport={"width": 1440, "height": 1000},
                    locale="zh-CN",
                    timezone_id="Asia/Shanghai",
                )
                page = await context.new_page()
                await _run_push_session(page, health_client)
            except SessionNotReady as exc:
                if exc.state != last_session_state:
                    LOGGER.warning("MX push collector paused session_state=%s", exc.state)
                    last_session_state = exc.state
                reconnect_backoff = 30
                await asyncio.sleep(SESSION_RECHECK_SECONDS)
            except Exception as exc:  # noqa: BLE001 - bounded reconnect after page/socket failure
                LOGGER.warning(
                    "MX push session disconnected (%s); retry_in=%ds",
                    type(exc).__name__,
                    reconnect_backoff,
                )
                await asyncio.sleep(reconnect_backoff)
                reconnect_backoff = min(reconnect_backoff * 2, HEARTBEAT_SECONDS)
            finally:
                if page is not None and not page.is_closed():
                    await page.close()
                if context is not None:
                    await context.close()
                if browser is not None:
                    await browser.close()


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()

