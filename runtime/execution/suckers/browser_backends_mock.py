"""Mock browser backend — scripted, deterministic, no runtime needed.

Serves two roles:

* **Test fixture** — drives the unified-interface call sites and the
  resolver without Playwright / Electron / a real browser.
* **Honest degradation** — when no real track is available the caller
  can still get a structured ``ok=False`` "no browser here" result
  instead of a None it has to special-case.

The real Playwright / Electron / extension adapters belong next to
this file once a machine with those runtimes can verify them
end-to-end; they implement the same :class:`BrowserBackend` Protocol.
"""

from __future__ import annotations

from typing import Any

from runtime.execution.suckers.browser_backend import BrowserResult, Track


class MockBrowserBackend:
    """Records every call and returns scripted results.

    Parameters
    ----------
    available :
        What :meth:`available` reports. Set False to test the resolver
        skipping an unavailable backend.
    url :
        Current page URL, updated by :meth:`navigate`.
    """

    track = Track.MOCK

    def __init__(self, *, available: bool = True, url: str = "about:blank") -> None:
        self._available = available
        self._url = url
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def available(self) -> bool:
        return self._available

    def _record(self, action: str, **kwargs: Any) -> BrowserResult:
        self.calls.append((action, kwargs))
        payload: dict[str, Any] = {"ok": True, "action": action, **kwargs}
        return BrowserResult.from_track(self.track, payload)

    def navigate(self, url: str) -> BrowserResult:
        self._url = url
        return self._record("navigate", url=url)

    def click(self, selector: str) -> BrowserResult:
        return self._record("click", selector=selector)

    def type(self, selector: str, text: str, *, clear: bool = False) -> BrowserResult:
        return self._record("type", selector=selector, text=text, clear=clear)

    def scroll(self, *, selector: str | None = None, delta_y: int = 0) -> BrowserResult:
        return self._record("scroll", selector=selector, delta_y=delta_y)

    def wait(self, selector: str, *, timeout_ms: int = 10_000) -> BrowserResult:
        return self._record("wait", selector=selector, timeout_ms=timeout_ms)

    def state(self, *, max_items: int = 30) -> BrowserResult:
        self.calls.append(("state", {"max_items": max_items}))
        return BrowserResult.from_track(
            self.track,
            {
                "ok": True,
                "url": self._url,
                "title": "mock page",
                "links": [],
                "buttons": [],
                "inputs": [],
                "headings": [],
            },
        )

    def extract(self) -> BrowserResult:
        self.calls.append(("extract", {}))
        return BrowserResult.from_track(
            self.track,
            {"ok": True, "url": self._url, "title": "mock page", "text": ""},
        )

    def screenshot(self, path: str = "", *, full_page: bool = False) -> BrowserResult:
        return self._record("screenshot", path=path, full_page=full_page)


__all__ = ["MockBrowserBackend"]
