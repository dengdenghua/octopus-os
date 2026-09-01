from __future__ import annotations

import contextlib
import threading
from typing import Any

from runtime.execution.suckers.browser_launch import (
    launch_chromium,
)

try:
    from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]

    PLAYWRIGHT_AVAILABLE = True
except ImportError:  # pragma: no cover
    PLAYWRIGHT_AVAILABLE = False
    sync_playwright = None  # type: ignore[assignment]


DEFAULT_TIMEOUT_MS = 10_000
MAX_TEXT_BYTES = 100_000
MAX_SCREENSHOT_BYTES = 10 * 1024 * 1024  # 10MB hard cap
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


# Import helpers from submodule (after constants are defined so the
# circular import resolves cleanly — see _browser_skills_helpers.py).
from ._browser_skills_helpers import (  # noqa: E402  (after constants)
    _browser_result_payload,
    _call_browser_backend,
    _child_frame_snapshots,
    _extract_from_page,
    _find_matches_in_text,
    _materialize_higher_track_screenshot,
    _navigate_and_read,
    _requested_browser_track,
)


def _has_agent_browser_session() -> bool:
    """Whether an agent turn can reuse the thread's persistent page."""
    if not PLAYWRIGHT_AVAILABLE:
        return False
    try:
        from runtime.platform.process.session import current_session

        return current_session() is not None
    except Exception:  # noqa: BLE001 - session support is optional
        return False


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


def _check_url_safe(url: str, allow_private: bool) -> str | None:
    if not url:
        return "missing url"
    from runtime.safety.auth.url_guard import check_url

    verdict = check_url(url, allow_private=allow_private)
    if not verdict.allow:
        return f"ssrf_blocked: {verdict.reason}"
    return None


# ═══════════════════════════════════════════════════════════
# browser_get
# ═══════════════════════════════════════════════════════════


def _browser_get(
    url: str = "",
    *,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    wait_ms: int = 0,
    max_bytes: int = MAX_TEXT_BYTES,
    allow_private: bool = False,
    page: Any = None,
    _background_only: bool = False,
    **_kw: Any,
) -> dict[str, Any]:
    if page is None:
        if url:
            err = _check_url_safe(url, allow_private)
            if err:
                return {"error": err, "blocked": err.startswith("ssrf_")}
        if not url:
            routed = _dispatch_higher_track("extract", {}, url="")
            if routed is not None:
                return routed
            if not _has_agent_browser_session():
                return {"error": "missing url", "blocked": False}

    result = _with_page(
        page,
        lambda current: _navigate_and_read(current, url, timeout_ms, wait_ms, max_bytes),
        verb="extract",
        payload={},
        url=url,
        allow_higher_track=not _background_only,
    )
    # Live Electron/extension tracks expose extracted body text as ``text``;
    # keep browser_get's established ``content`` contract regardless of which
    # browser served the request.
    live_text = result.get("content")
    if not isinstance(live_text, str):
        live_text = result.get("text")
    if isinstance(live_text, str):
        truncated = len(live_text) > max_bytes
        result["content"] = live_text[:max_bytes]
        result["length"] = len(result["content"])
        result["truncated"] = bool(result.get("truncated")) or truncated
    error = str(result.get("error") or "").lower()
    if (
        page is None
        and url
        and ("executable doesn't exist" in error or "playwright not installed" in error)
    ):
        from runtime.execution.suckers.web_skills import _fetch_url

        fetched = _fetch_url(
            url,
            timeout_ms=min(timeout_ms, 8_000),
            max_bytes=max_bytes,
            allow_private=allow_private,
            extract=True,
        )
        if not fetched.get("error"):
            fetched["browser_fallback"] = "http_extract"
            fetched["fallback_reason"] = "playwright_executable_missing"
            return fetched
    return result


# ═══════════════════════════════════════════════════════════
# browser_extract
# ═══════════════════════════════════════════════════════════


def _browser_extract(
    url: str = "",
    selector: str = "",
    *,
    attr: str | None = None,
    limit: int = 20,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    wait_ms: int = 0,
    allow_private: bool = False,
    page: Any = None,
    **_kw: Any,
) -> dict[str, Any]:
    if not selector:
        return {"error": "missing selector", "items": []}
    if page is None:
        if url:
            err = _check_url_safe(url, allow_private)
            if err:
                return {"error": err, "blocked": err.startswith("ssrf_"), "items": []}
        elif not _has_agent_browser_session():
            return {"error": "missing url", "items": []}

    return _with_page(
        page,
        lambda current: _extract_from_page(
            current,
            url,
            selector,
            attr,
            limit,
            timeout_ms,
            wait_ms,
        ),
    )


# ═══════════════════════════════════════════════════════════
# Higher-track dispatch (stays here for monkeypatch compatibility)
# ═══════════════════════════════════════════════════════════


def _higher_track_backends() -> list[Any]:
    """Non-Playwright browser tracks, highest priority first: the user's
    extension and the desktop Electron browser. Built fresh each call so
    availability is re-probed (the relay / bridge can come and go). A test
    seam — monkeypatch to inject fakes."""
    from runtime.execution.suckers.browser_backends import (
        ElectronBackend,
        ExtensionBackend,
    )

    return [ExtensionBackend(), ElectronBackend()]


def _annotate_browser_track_result(
    payload: Any,
    *,
    served_track: Any,
) -> dict[str, Any]:
    """Expose whether an explicit @Chrome/@Browser track preference held.

    Previously an extension disconnect silently moved an @Chrome action onto
    Electron or Playwright.  The operation could succeed in the wrong browser
    while the model reported that it acted on the signed-in Chrome tab.  This
    receipt makes the selected track and any fallback explicit to the model,
    UI, trajectory recorder, and final-answer guards.

    ``payload`` is ideally a dict (the common case for browser actions that
    return structured results). Non-dict payloads (raw strings, bools, None
    from void actions) are wrapped under a ``"result"`` key so the track
    metadata can still be attached without losing the original value.
    """

    served = str(getattr(served_track, "value", served_track) or "")
    requested_track = _requested_browser_track()
    requested = str(getattr(requested_track, "value", requested_track) or "")
    result = dict(payload) if isinstance(payload, dict) else {"result": payload}
    if served:
        result.setdefault("track", served)
    if not requested:
        return result
    result["browser_track_preference"] = requested
    result["browser_track_preference_satisfied"] = served == requested
    if served != requested:
        result["browser_track_fallback"] = {
            "requested": requested,
            "served": served,
            "reason": f"{requested}_unavailable",
        }
    return result


def _dispatch_higher_track(
    verb: str,
    payload: dict[str, Any],
    *,
    url: str = "",
) -> dict[str, Any] | None:
    """Run ``verb`` on the highest-priority AVAILABLE non-Playwright track
    (extension → Electron), preserving the PW skills' "navigate then act"
    semantics so a flow stays on ONE browser. Returns the track's result dict,
    or ``None`` when no higher track is available (caller falls back to
    headless Playwright)."""
    try:
        from runtime.execution.suckers.browser_backend import resolve_backend

        chosen = resolve_backend(
            _higher_track_backends(),
            prefer=_requested_browser_track(),
        )
    except Exception:  # noqa: BLE001 — backend layer optional
        return None
    if chosen is None:
        return None
    try:
        # Match the stateless PW skills: each call navigates first (except the
        # navigate verb itself), then acts — so we never split a flow between
        # the real browser and headless PW.
        if url and verb != "navigate":
            nav = chosen.navigate(url)
            if not nav.ok:
                return _browser_result_payload(nav)
        res = _call_browser_backend(chosen, verb, payload, fallback_url=url)
        result = _annotate_browser_track_result(
            _browser_result_payload(res),
            served_track=getattr(chosen, "track", ""),
        )
        if verb == "screenshot" and "error" not in result:
            return _materialize_higher_track_screenshot(result, payload)
        return result
    except Exception as e:  # noqa: BLE001
        return {"error": f"browser_error: {type(e).__name__}: {e}"}


def _with_page(
    page: Any,
    action: Any,
    *,
    launch_timeout_ms: int = DEFAULT_TIMEOUT_MS,
    verb: str | None = None,
    payload: dict[str, Any] | None = None,
    url: str = "",
    allow_higher_track: bool = True,
) -> dict[str, Any]:
    if page is not None:
        from runtime.execution.suckers.browser_backend import Track

        return _annotate_browser_track_result(action(page), served_track=Track.PLAYWRIGHT)

    # Prefer the user's real browser (extension > desktop Electron) when one is
    # live; fall back to headless Playwright. Unavailable tracks (the common
    # case — no desktop app running) resolve to None, so the PW path below is
    # unchanged. Only verbs the higher tracks implement pass ``verb``.
    if allow_higher_track and verb is not None:
        routed = _dispatch_higher_track(verb, payload or {}, url=url)
        if routed is not None:
            return routed

    if not PLAYWRIGHT_AVAILABLE:
        return {"error": "playwright not installed"}

    # Inside an agent session, reuse a persistent per-session page so multi-step
    # flows (navigate → click → type) share state. The page lives on a dedicated
    # worker thread (Playwright sync is thread-affine) and ``action`` runs there.
    # Outside a session (direct / unit-test calls) keep the original stateless
    # throwaway-browser behaviour — no surprise persistence, no regression.
    try:
        from runtime.platform.process.session import current_session

        sess = current_session()
    except Exception:  # noqa: BLE001 — session module optional
        sess = None
    if sess is not None:
        try:
            from runtime.execution.suckers.browser_session_worker import (
                get_browser_session_pool,
            )

            pool = get_browser_session_pool()
            key = f"thr:{getattr(sess, 'thread_id', None) or threading.get_ident()}"
            try:
                from runtime.execution.suckers.browser_backend import Track

                return _annotate_browser_track_result(
                    pool.get_or_create(key).submit(action),
                    served_track=Track.PLAYWRIGHT,
                )
            except RuntimeError:
                # The worker was closed (reaper eviction / timeout retirement)
                # between get_or_create and submit — one fresh retry resolves
                # the race (get_or_create makes a new worker for a closed key).
                from runtime.execution.suckers.browser_backend import Track

                return _annotate_browser_track_result(
                    pool.get_or_create(key).submit(action),
                    served_track=Track.PLAYWRIGHT,
                )
        except Exception as e:  # noqa: BLE001
            return {"error": f"browser_error: {type(e).__name__}: {e}"}

    try:
        with sync_playwright() as pw:
            browser = launch_chromium(pw.chromium, headless=True)
            try:
                ctx = browser.new_context()
                new_page = ctx.new_page()
                from runtime.execution.suckers.browser_backend import Track

                return _annotate_browser_track_result(
                    action(new_page),
                    served_track=Track.PLAYWRIGHT,
                )
            finally:
                browser.close()
    except Exception as e:  # noqa: BLE001
        return {"error": f"browser_error: {type(e).__name__}: {e}"}


# ═══════════════════════════════════════════════════════════
# browser_navigate
# ═══════════════════════════════════════════════════════════


def _browser_navigate(
    url: str = "",
    *,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    wait_ms: int = 0,
    allow_private: bool = False,
    page: Any = None,
    **_kw: Any,
) -> dict[str, Any]:
    if page is None:
        err = _check_url_safe(url, allow_private)
        if err:
            return {"error": err, "blocked": err.startswith("ssrf_")}
    elif not url:
        return {"error": "missing url"}

    def _act(p: Any) -> dict[str, Any]:
        try:
            resp = p.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
        except Exception as e:  # noqa: BLE001
            return {"error": f"nav_error: {type(e).__name__}: {e}"}
        if wait_ms > 0:
            p.wait_for_timeout(wait_ms)
        try:
            title = p.title()
        except (AttributeError, RuntimeError):  # noqa: BLE001
            title = ""
        return {
            "url": p.url,
            "status_code": resp.status if resp else None,
            "title": title,
        }

    return _with_page(page, _act, verb="navigate", payload={"url": url}, url=url)


# ═══════════════════════════════════════════════════════════
# browser_find
# ═══════════════════════════════════════════════════════════


def _browser_find(
    url: str = "",
    text: str = "",
    *,
    query: str | None = None,
    case_sensitive: bool = False,
    max_results: int = 20,
    context_chars: int = 80,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    wait_ms: int = 0,
    allow_private: bool = False,
    page: Any = None,
    **_kw: Any,
) -> dict[str, Any]:
    needle = str(query if query is not None else text).strip()
    if not needle:
        return {"error": "missing text", "matches": []}
    max_results = max(1, min(int(max_results), 100))
    context_chars = max(20, min(int(context_chars), 500))
    if page is None:
        if not url:
            routed = _dispatch_higher_track("extract", {}, url="")
            if routed is not None:
                if "error" in routed:
                    return {**routed, "matches": []}
                text_value = routed.get("text") or routed.get("content") or ""
                return _find_matches_in_text(
                    text=str(text_value or ""),
                    needle=needle,
                    url=str(routed.get("url") or ""),
                    title=str(routed.get("title") or ""),
                    case_sensitive=case_sensitive,
                    max_results=max_results,
                    context_chars=context_chars,
                )
            if not _has_agent_browser_session():
                return {"error": "missing url", "matches": []}
        else:
            err = _check_url_safe(url, allow_private)
            if err:
                return {"error": err, "blocked": err.startswith("ssrf_"), "matches": []}
            routed = _dispatch_higher_track("extract", {}, url=url)
            if routed is not None:
                if "error" in routed:
                    return {**routed, "matches": []}
                text_value = routed.get("text") or routed.get("content") or ""
                return _find_matches_in_text(
                    text=str(text_value or ""),
                    needle=needle,
                    url=str(routed.get("url") or url),
                    title=str(routed.get("title") or ""),
                    case_sensitive=case_sensitive,
                    max_results=max_results,
                    context_chars=context_chars,
                )

    def _act(p: Any) -> dict[str, Any]:
        if url:
            try:
                p.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            except Exception as e:  # noqa: BLE001
                return {"error": f"nav_error: {type(e).__name__}: {e}", "matches": []}
        if wait_ms > 0:
            p.wait_for_timeout(wait_ms)
        try:
            body_text = p.inner_text("body")
        except Exception as e:  # noqa: BLE001
            return {"error": f"read_error: {type(e).__name__}: {e}", "matches": []}

        return _find_matches_in_text(
            text=body_text,
            needle=needle,
            url=p.url,
            title=p.title(),
            case_sensitive=case_sensitive,
            max_results=max_results,
            context_chars=context_chars,
        )

    return _with_page(page, _act)


# ═══════════════════════════════════════════════════════════
# browser_state
# ═══════════════════════════════════════════════════════════


def _browser_state(
    url: str = "",
    *,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    wait_ms: int = 0,
    allow_private: bool = False,
    max_items: int = 30,
    page: Any = None,
    **_kw: Any,
) -> dict[str, Any]:
    max_items = max(1, min(int(max_items), 100))
    if page is None:
        if not url:
            routed = _dispatch_higher_track("state", {"max_items": max_items}, url="")
            if routed is not None:
                return routed
            if not _has_agent_browser_session():
                return {"error": "missing url", "blocked": False}
        else:
            err = _check_url_safe(url, allow_private)
            if err:
                return {"error": err, "blocked": err.startswith("ssrf_")}

    def _act(p: Any) -> dict[str, Any]:
        resp = None
        if url:
            try:
                resp = p.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            except Exception as e:  # noqa: BLE001
                return {"error": f"nav_error: {type(e).__name__}: {e}"}
        if wait_ms > 0:
            p.wait_for_timeout(wait_ms)
        try:
            text = p.inner_text("body")
        except Exception:  # noqa: BLE001 — best-effort; fail-open
            text = ""
        try:
            from runtime.execution.suckers.browser_dom_js import (
                dom_snapshot_function_js,
            )

            snapshot = p.evaluate(dom_snapshot_function_js(), max_items)
        except Exception as e:  # noqa: BLE001
            return {"error": f"state_error: {type(e).__name__}: {e}"}
        return {
            "url": p.url,
            "status_code": resp.status if resp else None,
            "title": p.title(),
            "text_length": len(text),
            "frames": _child_frame_snapshots(p, max_bytes=MAX_TEXT_BYTES),
            **snapshot,
        }

    return _with_page(
        page,
        _act,
        verb="state",
        payload={"max_items": max_items},
        url=url,
    )


# ═══════════════════════════════════════════════════════════
# browser_click
# ═══════════════════════════════════════════════════════════


def _browser_click(
    url: str = "",
    selector: str = "",
    *,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    wait_after_ms: int = 0,
    allow_private: bool = False,
    page: Any = None,
    **_kw: Any,
) -> dict[str, Any]:
    if not selector:
        return {"error": "missing selector"}
    if page is None:
        if not url:
            routed = _dispatch_higher_track(
                "click",
                {"selector": selector},
                url="",
            )
            if routed is not None:
                return routed
            if not _has_agent_browser_session():
                return {"error": "missing url", "blocked": False}
        else:
            err = _check_url_safe(url, allow_private)
            if err:
                return {"error": err, "blocked": err.startswith("ssrf_")}

    def _act(p: Any) -> dict[str, Any]:
        if url:
            try:
                p.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            except Exception as e:  # noqa: BLE001
                return {"error": f"nav_error: {type(e).__name__}: {e}"}
        try:
            p.click(selector, timeout=timeout_ms)
        except Exception as e:  # noqa: BLE001
            return {"error": f"click_error: {type(e).__name__}: {e}"}
        if wait_after_ms > 0:
            p.wait_for_timeout(wait_after_ms)
        try:
            title = p.title()
        except (AttributeError, RuntimeError):  # noqa: BLE001
            title = ""
        return {
            "clicked": selector,
            "final_url": p.url,
            "title": title,
        }

    return _with_page(
        page,
        _act,
        verb="click",
        payload={"selector": selector},
        url=url,
    )


# ═══════════════════════════════════════════════════════════
# browser_type
# ═══════════════════════════════════════════════════════════


def _browser_type(
    url: str = "",
    selector: str = "",
    text: str = "",
    *,
    value: str | None = None,
    option_label: str | None = None,
    press_enter: bool = False,
    clear_first: bool = True,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    allow_private: bool = False,
    page: Any = None,
    **_kw: Any,
) -> dict[str, Any]:
    if not selector:
        return {"error": "missing selector"}
    # Native tool-capable models commonly use DOM-oriented ``value`` for
    # inputs and ``option_label`` for selects.  Keep ``text`` canonical while
    # accepting both explicit schema aliases instead of silently filling an
    # empty string through ``**_kw``.
    text = text or option_label or value or ""
    if page is None:
        if not url:
            routed = _dispatch_higher_track(
                "type",
                {"selector": selector, "text": text, "clear": clear_first},
                url="",
            )
            if routed is not None:
                return routed
            if not _has_agent_browser_session():
                return {"error": "missing url", "blocked": False}
        else:
            err = _check_url_safe(url, allow_private)
            if err:
                return {"error": err, "blocked": err.startswith("ssrf_")}

    def _act(p: Any) -> dict[str, Any]:
        if url:
            try:
                p.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            except Exception as e:  # noqa: BLE001
                return {"error": f"nav_error: {type(e).__name__}: {e}"}
        try:
            locator = p.locator(selector)
            try:
                tag_name = locator.evaluate("element => element.tagName")
            except AttributeError:
                # Lightweight/test backends may expose the older page-level
                # fill/press API without a full Locator implementation.
                if clear_first:
                    p.fill(selector, "", timeout=timeout_ms)
                p.fill(selector, text, timeout=timeout_ms)
                if press_enter:
                    p.press(selector, "Enter", timeout=timeout_ms)
                input_kind = "text"
            else:
                if str(tag_name).upper() == "SELECT":
                    # Keep one generic form-entry primitive while still handling
                    # native selects correctly. Prefer the visible label, then
                    # fall back to the value for machine-oriented forms.
                    try:
                        locator.select_option(label=text, timeout=timeout_ms)
                    except Exception:  # noqa: BLE001
                        locator.select_option(value=text, timeout=timeout_ms)
                    input_kind = "select"
                else:
                    if clear_first:
                        locator.fill("", timeout=timeout_ms)
                    locator.fill(text, timeout=timeout_ms)
                    input_kind = "text"
                if press_enter:
                    locator.press("Enter", timeout=timeout_ms)
        except Exception as e:  # noqa: BLE001
            return {"error": f"type_error: {type(e).__name__}: {e}"}
        return {
            "filled": selector,
            "text_len": len(text),
            "input_kind": input_kind,
            "pressed_enter": press_enter,
            "final_url": p.url,
        }

    return _with_page(
        page,
        _act,
        verb="type",
        payload={"selector": selector, "text": text, "clear": clear_first},
        url=url,
    )


# ═══════════════════════════════════════════════════════════
# browser_scroll
# ═══════════════════════════════════════════════════════════


def _browser_scroll(
    url: str = "",
    *,
    to_selector: str | None = None,
    to_y: int | None = None,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    allow_private: bool = False,
    page: Any = None,
    **_kw: Any,
) -> dict[str, Any]:
    if (to_selector is None) == (to_y is None):
        return {"error": "provide exactly one of to_selector / to_y"}
    if page is None:
        if not url:
            payload: dict[str, Any] = {}
            if to_selector is not None:
                payload["selector"] = to_selector
            if to_y is not None:
                payload["delta_y"] = int(to_y)
            routed = _dispatch_higher_track("scroll", payload, url="")
            if routed is not None:
                return routed
            if not _has_agent_browser_session():
                return {"error": "missing url", "blocked": False}
        else:
            err = _check_url_safe(url, allow_private)
            if err:
                return {"error": err, "blocked": err.startswith("ssrf_")}

    def _act(p: Any) -> dict[str, Any]:
        if url:
            try:
                p.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            except Exception as e:  # noqa: BLE001
                return {"error": f"nav_error: {type(e).__name__}: {e}"}
        try:
            if to_selector is not None:
                p.locator(to_selector).scroll_into_view_if_needed(
                    timeout=timeout_ms,
                )
                scrolled_to = {"selector": to_selector}
            else:
                p.evaluate(f"window.scrollTo(0, {int(to_y)})")
                scrolled_to = {"y": int(to_y)}
        except Exception as e:  # noqa: BLE001
            return {"error": f"scroll_error: {type(e).__name__}: {e}"}
        return {"scrolled_to": scrolled_to, "final_url": p.url}

    return _with_page(
        page,
        _act,
        verb="scroll",
        payload={
            "selector": to_selector,
            "delta_y": int(to_y) if to_y is not None else 0,
        },
        url=url,
    )


# ═══════════════════════════════════════════════════════════
# browser_upload
# ═══════════════════════════════════════════════════════════


def _browser_upload(
    url: str = "",
    selector: str = "",
    path: str = "",
    *,
    sandbox_dir: str | None = None,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    allow_private: bool = False,
    page: Any = None,
    **_kw: Any,
) -> dict[str, Any]:
    """Set a file input from a path confined by the current workspace."""
    if not selector:
        return {"error": "missing selector"}
    if not path:
        return {"error": "missing path"}

    from pathlib import Path

    from runtime.safety.auth.path_guard import check_path

    verdict = check_path(path, sandbox_dir=sandbox_dir, must_exist=True)
    if not verdict.allow:
        return {"error": f"path_blocked: {verdict.reason}"}
    resolved = Path(verdict.resolved or path)
    try:
        if not resolved.is_file():
            return {"error": "upload path is not a file"}
        size = resolved.stat().st_size
    except OSError as exc:
        return {"error": f"upload_path_error: {type(exc).__name__}: {exc}"}
    if size > MAX_UPLOAD_BYTES:
        return {"error": f"upload too large: {size} > {MAX_UPLOAD_BYTES}"}

    if page is None:
        if not url:
            if not _has_agent_browser_session():
                return {"error": "missing url", "blocked": False}
        else:
            err = _check_url_safe(url, allow_private)
            if err:
                return {"error": err, "blocked": err.startswith("ssrf_")}

    def _act(p: Any) -> dict[str, Any]:
        if url:
            try:
                p.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            except Exception as exc:  # noqa: BLE001
                return {"error": f"nav_error: {type(exc).__name__}: {exc}"}
        try:
            p.locator(selector).set_input_files(str(resolved), timeout=timeout_ms)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"upload_error: {type(exc).__name__}: {exc}"}
        return {
            "uploaded": selector,
            "file_name": resolved.name,
            "size_bytes": size,
            "final_url": p.url,
        }

    return _with_page(
        page,
        _act,
    )


# ═══════════════════════════════════════════════════════════
# browser_wait
# ═══════════════════════════════════════════════════════════


def _browser_wait(
    url: str = "",
    selector: str = "",
    *,
    state: str = "visible",  # visible / hidden / attached / detached
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    allow_private: bool = False,
    page: Any = None,
    **_kw: Any,
) -> dict[str, Any]:
    if not selector:
        return {"error": "missing selector"}
    if state not in {"visible", "hidden", "attached", "detached"}:
        return {"error": f"invalid state: {state!r}"}
    if page is None:
        if not url:
            routed = _dispatch_higher_track(
                "wait",
                {"selector": selector, "timeout": timeout_ms},
                url="",
            )
            if routed is not None:
                return routed
            if not _has_agent_browser_session():
                return {"error": "missing url", "blocked": False}
        else:
            err = _check_url_safe(url, allow_private)
            if err:
                return {"error": err, "blocked": err.startswith("ssrf_")}

    def _act(p: Any) -> dict[str, Any]:
        if url:
            try:
                p.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            except Exception as e:  # noqa: BLE001
                return {"error": f"nav_error: {type(e).__name__}: {e}"}
        try:
            p.wait_for_selector(selector, state=state, timeout=timeout_ms)
        except Exception as e:  # noqa: BLE001
            return {
                "error": f"wait_timeout: {type(e).__name__}: {e}",
                "timed_out": True,
                "selector": selector,
                "state": state,
            }
        return {"waited_for": selector, "state": state, "final_url": p.url}

    return _with_page(
        page,
        _act,
        verb="wait",
        payload={"selector": selector, "timeout": timeout_ms},
        url=url,
    )


# ═══════════════════════════════════════════════════════════
# browser_screenshot
# ═══════════════════════════════════════════════════════════


def _browser_screenshot(
    url: str = "",
    path: str = "",
    *,
    full_page: bool = False,
    sandbox_dir: str | None = None,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    wait_ms: int = 0,
    allow_private: bool = False,
    page: Any = None,
    **_kw: Any,
) -> dict[str, Any]:
    if not path:
        return {"error": "missing path"}

    from runtime.safety.auth.path_guard import check_path

    verdict = check_path(path, sandbox_dir=sandbox_dir)
    if not verdict.allow:
        return {"error": f"path_blocked: {verdict.reason}"}
    resolved = verdict.resolved or path

    if page is None:
        if not url:
            routed = _dispatch_higher_track(
                "screenshot",
                {"path": str(resolved), "full_page": bool(full_page)},
                url="",
            )
            if routed is not None:
                return routed
            if not _has_agent_browser_session():
                return {"error": "missing url", "blocked": False}
        else:
            err = _check_url_safe(url, allow_private)
            if err:
                return {"error": err, "blocked": err.startswith("ssrf_")}

    def _act(p: Any) -> dict[str, Any]:
        if url:
            try:
                p.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            except Exception as e:  # noqa: BLE001
                return {"error": f"nav_error: {type(e).__name__}: {e}"}
        if wait_ms > 0:
            p.wait_for_timeout(wait_ms)
        try:
            p.screenshot(path=str(resolved), full_page=full_page)
        except Exception as e:  # noqa: BLE001
            return {"error": f"screenshot_error: {type(e).__name__}: {e}"}

        from pathlib import Path as _P  # noqa: N814

        size = 0
        with contextlib.suppress(OSError):
            size = _P(str(resolved)).stat().st_size
        if size > MAX_SCREENSHOT_BYTES:
            with contextlib.suppress(OSError):
                _P(str(resolved)).unlink()
            return {
                "error": f"screenshot too large: {size} > {MAX_SCREENSHOT_BYTES}",
                "path": str(resolved),
            }
        return {
            "path": str(resolved),
            "size_bytes": size,
            "full_page": full_page,
        }

    return _with_page(
        page,
        _act,
        verb="screenshot",
        payload={"path": str(resolved), "full_page": bool(full_page)},
        url=url,
    )


# ═══════════════════════════════════════════════════════════
# Registrar · moved to _browser_skills_handlers to keep this file
# under 1000 lines.  Re-exported below so public callers are
# unaffected.  The import MUST come after all handler definitions
# above so the submodule can resolve them at load time.
# ═══════════════════════════════════════════════════════════
from ._browser_skills_handlers import (  # noqa: E402  (after defs)
    BROWSER_SKILL_NAMES,
    register_browser_skills,
)

__all__ = ["BROWSER_SKILL_NAMES", "register_browser_skills"]
