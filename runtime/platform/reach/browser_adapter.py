from __future__ import annotations

from typing import Any


def read_with_browser(url: str, *, timeout_ms: int, max_bytes: int) -> dict[str, Any]:
    """Read through Echo' active Chrome/Electron/Playwright browser track."""
    from runtime.execution.suckers.browser_skills import _browser_get

    result = _browser_get(url=url, timeout_ms=timeout_ms, max_bytes=max_bytes, wait_ms=1200)
    if result.get("error"):
        return {
            **result,
            "backend": "browser",
            "url": url,
            "requires_login": True,
            "repair_hint": "Sign in in Chrome or the Echo browser, then retry with use_browser=true.",
        }
    return {
        **result,
        "ok": True,
        "backend": f"browser:{result.get('track', 'playwright')}",
        "url": result.get("url") or url,
    }
