"""Helpers for browser_skills · extracted from browser_skills.py.

Contains page-reading utilities, higher-track dispatch helpers, and
text-matching helpers used by the browser skill handlers.  The handler
functions, ``_with_page``, ``_dispatch_higher_track``,
``_higher_track_backends`` and ``_annotate_browser_track_result`` remain
in ``browser_skills`` (tests monkeypatch them via the
``browser_skills`` module namespace, so they must not move).

Import order: ``browser_skills`` defines its constants first, THEN
imports these helpers.  When this module is loaded, ``browser_skills``
is already in ``sys.modules`` with the constants bound, so the import
of ``MAX_SCREENSHOT_BYTES`` below succeeds.
"""

from __future__ import annotations

import base64
import contextlib
from typing import Any

from .browser_skills import MAX_SCREENSHOT_BYTES

# ═══════════════════════════════════════════════════════════
# Page-reading helpers (used by browser_get / browser_extract)
# ═══════════════════════════════════════════════════════════


def _navigate_and_read(
    page: Any, url: str, timeout_ms: int, wait_ms: int, max_bytes: int
) -> dict[str, Any]:
    resp = None
    if url:
        try:
            resp = page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
        except Exception as e:  # noqa: BLE001
            return {"error": f"nav_error: {type(e).__name__}: {e}"}

    if wait_ms > 0:
        page.wait_for_timeout(wait_ms)

    try:
        title = page.title()
        text = page.inner_text("body")
    except Exception as e:  # noqa: BLE001
        return {"error": f"read_error: {type(e).__name__}: {e}"}

    truncated = len(text) > max_bytes
    if truncated:
        text = text[:max_bytes]

    return {
        "url": page.url,
        "status_code": resp.status if resp else None,
        "title": title,
        "length": len(text),
        "truncated": truncated,
        "content": text,
        "frames": _child_frame_snapshots(page, max_bytes=max_bytes),
    }


def _child_frame_snapshots(page: Any, *, max_bytes: int) -> list[dict[str, Any]]:
    """Return readable evidence from child frames without failing the page read.

    ``body.innerText`` on the top page intentionally excludes iframe
    documents.  Browser tasks that must wait for an iframe confirmation would
    therefore have no observable success signal even though the UI completed.
    Keep this best-effort and bounded: cross-origin or detached frames may
    reject DOM access and should not make the whole browser observation fail.
    """

    frames_value = getattr(page, "frames", [])
    frames = frames_value() if callable(frames_value) else frames_value
    if not isinstance(frames, (list, tuple)):
        return []
    main_frame = getattr(page, "main_frame", None)
    snapshots: list[dict[str, Any]] = []
    remaining = max(0, int(max_bytes))
    for frame in frames:
        if frame is main_frame:
            continue
        try:
            frame_text = str(frame.inner_text("body") or "")
            frame_url = str(getattr(frame, "url", "") or "")
            frame_name = str(getattr(frame, "name", "") or "")
        except Exception:  # noqa: BLE001 - detached/cross-origin frame
            continue
        clipped = frame_text[:remaining]
        snapshots.append(
            {
                "url": frame_url,
                "name": frame_name,
                "content": clipped,
                "truncated": len(frame_text) > len(clipped),
            }
        )
        remaining = max(0, remaining - len(clipped))
        if remaining == 0:
            break
    return snapshots


def _extract_from_page(
    page: Any,
    url: str,
    selector: str,
    attr: str | None,
    limit: int,
    timeout_ms: int,
    wait_ms: int,
) -> dict[str, Any]:
    if url:
        try:
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
        except Exception as e:  # noqa: BLE001
            return {"error": f"nav_error: {type(e).__name__}: {e}", "items": []}

    if wait_ms > 0:
        page.wait_for_timeout(wait_ms)

    try:
        handles = page.query_selector_all(selector)
    except Exception as e:  # noqa: BLE001
        return {"error": f"selector_error: {type(e).__name__}: {e}", "items": []}

    items: list[str] = []
    for h in handles[:limit]:
        try:
            val = h.inner_text() if attr is None else h.get_attribute(attr) or ""
        except (AttributeError, RuntimeError):  # noqa: BLE001
            val = ""
        items.append(val)

    return {
        "url": page.url,
        "selector": selector,
        "attr": attr,
        "count": len(items),
        "items": items,
    }


# ═══════════════════════════════════════════════════════════
# Higher-track dispatch helpers
# ═══════════════════════════════════════════════════════════


def _requested_browser_track() -> Any:
    """Resolve the trusted per-turn browser track preference, if present."""

    try:
        from runtime.execution.suckers.browser_backend import Track
        from runtime.platform.process.session import current_session

        session = current_session()
        metadata = getattr(session, "metadata", None) if session is not None else None
        raw = str((metadata or {}).get("browser_track_preference") or "").strip().lower()
        return Track(raw) if raw else None
    except (AttributeError, TypeError, ValueError, ImportError):
        return None


def _call_browser_backend(
    backend: Any,
    verb: str,
    payload: dict[str, Any],
    *,
    fallback_url: str = "",
):
    if verb == "navigate":
        return backend.navigate(str(payload.get("url") or fallback_url))
    if verb == "click":
        return backend.click(str(payload.get("selector") or ""))
    if verb == "type":
        return backend.type(
            str(payload.get("selector") or ""),
            str(payload.get("text") or ""),
            clear=bool(payload.get("clear") or payload.get("clear_first")),
        )
    if verb == "scroll":
        delta_raw = payload.get("delta_y", payload.get("deltaY", 0))
        return backend.scroll(
            selector=payload.get("selector"),
            delta_y=int(delta_raw or 0),
        )
    if verb == "wait":
        timeout_raw = payload.get("timeout_ms", payload.get("timeout", 10_000))
        return backend.wait(
            str(payload.get("selector") or ""),
            timeout_ms=int(timeout_raw or 10_000),
        )
    if verb == "state":
        return backend.state(max_items=int(payload.get("max_items") or 30))
    if verb == "extract":
        return backend.extract()
    if verb == "screenshot":
        return backend.screenshot(
            str(payload.get("path") or ""),
            full_page=bool(payload.get("full_page")),
        )
    raise ValueError(f"unsupported browser backend verb: {verb}")


def _materialize_higher_track_screenshot(
    result: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    path = str(payload.get("path") or "").strip()
    if not path:
        return result
    raw_data = result.get("dataUrl") or result.get("data")
    if not isinstance(raw_data, str) or not raw_data.strip():
        return result
    data = raw_data.strip()
    if "," in data:
        data = data.split(",", 1)[1]
    try:
        image_bytes = base64.b64decode(data)
    except (ValueError, TypeError):
        return result
    size = len(image_bytes)
    if size > MAX_SCREENSHOT_BYTES:
        return {
            "error": f"screenshot too large: {size} > {MAX_SCREENSHOT_BYTES}",
            "path": path,
        }
    from pathlib import Path as _P  # noqa: N814

    target = _P(path)
    with contextlib.suppress(OSError):
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(image_bytes)
    return {
        **result,
        "path": path,
        "size_bytes": size,
        "full_page": bool(payload.get("full_page")),
        "track": result.get("track", "extension"),
    }


# ═══════════════════════════════════════════════════════════
# Text-matching helper
# ═══════════════════════════════════════════════════════════


def _find_matches_in_text(
    *,
    text: str,
    needle: str,
    url: str = "",
    title: str = "",
    case_sensitive: bool = False,
    max_results: int = 20,
    context_chars: int = 80,
) -> dict[str, Any]:
    haystack = text if case_sensitive else text.lower()
    target = needle if case_sensitive else needle.lower()
    matches: list[dict[str, Any]] = []
    start = 0
    while len(matches) < max_results:
        idx = haystack.find(target, start)
        if idx < 0:
            break
        left = max(0, idx - context_chars)
        right = min(len(text), idx + len(needle) + context_chars)
        matches.append(
            {
                "index": idx,
                "snippet": text[left:right].replace("\n", " ").strip(),
            }
        )
        start = idx + max(1, len(target))
    return {
        "url": url,
        "title": title,
        "text": needle,
        "count": len(matches),
        "truncated": len(matches) >= max_results,
        "matches": matches,
    }


# ═══════════════════════════════════════════════════════════
# Result-payload helper
# ═══════════════════════════════════════════════════════════


def _browser_result_payload(result: Any) -> dict[str, Any]:
    raw = getattr(result, "raw", None)
    if isinstance(raw, dict):
        return raw
    ok = bool(getattr(result, "ok", False))
    data = getattr(result, "data", None)
    if isinstance(data, dict):
        return dict(data)
    if ok:
        return {}
    return {"error": str(getattr(result, "error", None) or "browser_error")}
