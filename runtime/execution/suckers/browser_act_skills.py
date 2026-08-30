from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import time
from collections.abc import Callable
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request
from uuid import uuid4

from .registry import Skill, SkillRegistry

_BRIDGE_TIMEOUT = 30  # Implementation note.
_BRIDGE_STATUS_TIMEOUT = 0.75
_EXTENSION_FALLBACK_ACTIONS = frozenset(
    {"click", "type", "scroll", "wait", "navigate", "extract", "screenshot", "state"}
)

# ContextVar holding the active artifact emitter callable. The SSE pump
# in ``tool_bridge.stream_agentic_fallback`` sets this at session start
# (so artifact events flow into the SSE queue) and resets at session
# end. ``_emit_screenshot_artifact`` reads it after writing the file
# and, when present, calls it with the artifact event dict so the
# frontend receives a streamed ``artifact`` event inline. None means
# "no active stream" → file-only behavior, same as before.
_ACTIVE_ARTIFACT_EMITTER: ContextVar[Callable[[dict[str, Any]], None] | None] = ContextVar(
    "active_artifact_emitter", default=None
)
_LAST_SCREENSHOT_ARTIFACT: ContextVar[Path | None] = ContextVar(
    "last_screenshot_artifact", default=None
)


def set_artifact_emitter(
    emitter: Callable[[dict[str, Any]], None] | None,
) -> Token:
    """Install ``emitter`` as the active SSE artifact callback.

    Returns the ContextVar token; callers MUST pass it back to
    ``clear_artifact_emitter`` to restore the prior state when the
    stream ends. Mirrors how ``_current_session.set / .reset`` is used
    in ``tool_bridge`` so token lifetimes line up.
    """
    return _ACTIVE_ARTIFACT_EMITTER.set(emitter)


def clear_artifact_emitter(token: Token) -> None:
    """Reset the artifact emitter ContextVar to its prior value."""
    with contextlib.suppress(ValueError, LookupError):
        _ACTIVE_ARTIFACT_EMITTER.reset(token)


def _bridge_state_path() -> Path:
    root = os.environ.get("ECHO_DATA_DIR")
    if root:
        return Path(root) / "bridge.json"
    return Path(__file__).resolve().parents[3] / "data" / "bridge.json"


def _load_bridge() -> dict[str, Any] | None:
    path = _bridge_state_path()
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _bridge_call(action: str, params: dict[str, Any]) -> dict[str, Any]:
    state = _load_bridge()
    if not state:
        return {
            "ok": False,
            "error": (
                "Echo desktop (Electron) 未在运行 · 请在桌面端启动 Echo,本 skill 才能操作浏览器。"
            ),
        }
    port = state.get("port")
    token = state.get("token")
    if not port or not token:
        return {"ok": False, "error": "bridge.json 格式异常"}

    url = f"http://127.0.0.1:{port}/{action}"
    body = json.dumps(params).encode("utf-8")
    req = urllib_request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib_request.urlopen(req, timeout=_BRIDGE_TIMEOUT) as resp:  # nosec B310 — audited HTTP bridge endpoint
            raw = resp.read()
            return json.loads(raw.decode("utf-8"))
    except urllib_error.HTTPError as e:
        return {
            "ok": False,
            "error": f"bridge http {e.code}: {e.reason}",
        }
    except urllib_error.URLError as e:
        return {
            "ok": False,
            "error": f"bridge unreachable: {e.reason}",
        }
    except (TimeoutError, OSError) as e:
        return {"ok": False, "error": f"bridge timeout/error: {e}"}


def _bridge_status() -> dict[str, Any] | None:
    """Probe the Electron bridge without touching the current page.

    ``bridge.json`` can outlive a crashed desktop process, and a running
    desktop shell can legitimately have no browser webview selected. The
    authenticated ``/status`` call distinguishes both cases before a live
    action is dispatched, which makes an extension fallback safe: no
    mutating Electron request has run yet, so click/type cannot be duplicated.
    """

    state = _load_bridge()
    if not state:
        return None
    try:
        port = int(state.get("port") or 0)
    except (TypeError, ValueError):
        return None
    token = str(state.get("token") or "").strip()
    if not 0 < port <= 65535 or not token:
        return None
    req = urllib_request.Request(
        f"http://127.0.0.1:{port}/status",
        data=b"{}",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib_request.urlopen(req, timeout=_BRIDGE_STATUS_TIMEOUT) as resp:  # nosec B310 — fixed loopback bridge
            payload = json.loads(resp.read().decode("utf-8"))
            return payload if isinstance(payload, dict) else None
    except (
        urllib_error.HTTPError,
        urllib_error.URLError,
        TimeoutError,
        OSError,
        json.JSONDecodeError,
        UnicodeDecodeError,
    ):
        return None


def _electron_webview_available() -> bool:
    status = _bridge_status()
    return bool(
        status and status.get("ok") is True and status.get("activeWebContentsId") is not None
    )


def _extension_result_payload(result: Any) -> dict[str, Any]:
    payload = dict(result.raw or {}) if isinstance(result.raw, dict) else {}
    payload.setdefault("ok", bool(result.ok))
    payload.setdefault("track", str(getattr(result.track, "value", result.track)))
    if not result.ok:
        payload.setdefault("error", str(result.error or "extension browser action failed"))
    payload["live_browser_fallback"] = {
        "from": "electron",
        "to": "extension",
        "reason": "electron_webview_unavailable",
    }
    return payload


def _extension_fallback_result(
    action: str,
    params: dict[str, Any],
) -> dict[str, Any] | None:
    """Use the signed-in Chrome relay only when Electron cannot be targeted.

    The outer ``live_browser_*`` execution capability gate remains unchanged;
    the extension command then passes through the relay's site policy,
    selected-tab lease and human-takeover checks. Arbitrary JS is intentionally
    absent from the allow-list so this fallback cannot widen the authority of
    ``live_browser_execute_js``.
    """

    if action not in _EXTENSION_FALLBACK_ACTIONS or _electron_webview_available():
        return None
    from runtime.execution.suckers.browser_backends import ExtensionBackend

    backend = ExtensionBackend()
    try:
        if not backend.available():
            return None
        if action == "click":
            result = backend.click(str(params.get("selector") or ""))
        elif action == "type":
            result = backend.type(
                str(params.get("selector") or ""),
                str(params.get("text") or ""),
                clear=bool(params.get("clear")),
            )
        elif action == "scroll":
            result = backend.scroll(
                selector=str(params.get("selector") or "") or None,
                delta_y=int(params.get("deltaY") or 0),
            )
        elif action == "wait":
            result = backend.wait(
                str(params.get("selector") or ""),
                timeout_ms=int(params.get("timeout") or 10_000),
            )
        elif action == "navigate":
            result = backend.navigate(str(params.get("url") or ""))
        elif action == "extract":
            result = backend.extract()
        elif action == "screenshot":
            result = backend.screenshot()
        else:
            result = backend.state(max_items=int(params.get("max_items") or 30))
    except (OSError, TypeError, ValueError):
        # Availability can change between the read-only probe and dispatch.
        # Return to the legacy Electron error path rather than raising from a
        # live-browser skill or attempting a second mutating action.
        return None
    return _extension_result_payload(result)


def _live_browser_call(action: str, params: dict[str, Any]) -> dict[str, Any]:
    extension_result = _extension_fallback_result(action, params)
    if extension_result is not None:
        return extension_result
    return _bridge_call(action, params)


def _h_click(selector: str) -> dict[str, Any]:
    return _live_browser_call("click", {"selector": selector})


def _h_type(selector: str, text: str, clear: bool = False) -> dict[str, Any]:
    return _live_browser_call("type", {"selector": selector, "text": text, "clear": clear})


def _h_scroll(
    selector: str | None = None,
    delta_y: int = 0,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if selector:
        payload["selector"] = selector
    if delta_y:
        payload["deltaY"] = int(delta_y)
    return _live_browser_call("scroll", payload)


def _h_wait(selector: str, timeout: int = 10000) -> dict[str, Any]:
    return _live_browser_call("wait", {"selector": selector, "timeout": int(timeout)})


def _h_navigate(url: str) -> dict[str, Any]:
    return _live_browser_call("navigate", {"url": url})


def _h_extract() -> dict[str, Any]:
    return _live_browser_call("extract", {})


def _h_screenshot() -> dict[str, Any]:
    result = _live_browser_call("screenshot", {})
    if result.get("ok") and (result.get("data") or result.get("dataUrl")):
        _emit_screenshot_artifact(result)
    return result


def _artifacts_root(
    *,
    tenant_id: str | None = None,
    owner_actor_id: str | None = None,
) -> Path:
    from runtime.platform.process.paths import app_paths

    root = app_paths().data_dir / "browser_artifacts"
    if tenant_id and owner_actor_id:

        def _safe(value: str) -> str:
            return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]

        return root / "tenants" / _safe(tenant_id) / _safe(owner_actor_id)
    try:
        from runtime.memory.journal.journal_context import (
            current_owner_actor_id,
            current_tenant_id,
        )

        context_tenant = current_tenant_id()
        context_owner = current_owner_actor_id()
        if context_tenant and context_owner:
            return _artifacts_root(tenant_id=context_tenant, owner_actor_id=context_owner)
    except (ImportError, RuntimeError):  # noqa: BLE001 — tenant context is optional here
        pass
    return root


def _pixel_assertion_for_screenshot(path: Path) -> dict[str, Any]:
    try:
        from runtime.safety.replay.browser_pixel_assertions import (
            assert_screenshot_pixels,
        )

        return assert_screenshot_pixels(path)
    except Exception as exc:  # noqa: BLE001 - screenshot evidence is best-effort
        return {
            "schema": "echo.browser_pixel_assertion.v1",
            "ok": False,
            "reason": f"pixel assertion unavailable: {exc}",
        }


def _pixel_comparison_for_screenshots(before: Path | None, after: Path) -> dict[str, Any] | None:
    if before is None or not before.is_file():
        return None
    try:
        from runtime.safety.replay.browser_pixel_assertions import (
            compare_screenshot_pixels,
        )

        return compare_screenshot_pixels(before, after)
    except Exception as exc:  # noqa: BLE001 - screenshot evidence is best-effort
        return {
            "schema": "echo.browser_pixel_comparison.v1",
            "ok": False,
            "reason": f"pixel comparison unavailable: {exc}",
        }


def _pixel_replay_gate_case_for_artifact(
    event: dict[str, Any],
    pixel_assertion: dict[str, Any],
    pixel_comparison: dict[str, Any] | None,
) -> dict[str, Any] | None:
    try:
        from runtime.safety.replay.browser_pixel_assertions import (
            browser_pixel_replay_gate_case,
        )

        return browser_pixel_replay_gate_case(
            artifact=event,
            assertion=pixel_assertion,
            comparison=pixel_comparison,
        )
    except Exception:  # noqa: BLE001 - replay evidence is best-effort
        return None


def _queue_pixel_replay_gate_case(
    replay_gate_case: dict[str, Any],
) -> dict[str, Any] | None:
    try:
        from runtime.memory.learning.review_queue import ReviewQueue
        from runtime.platform.process.paths import app_paths

        artifact = (
            replay_gate_case.get("artifact")
            if isinstance(replay_gate_case.get("artifact"), dict)
            else {}
        )
        failures = (
            replay_gate_case.get("failures")
            if isinstance(replay_gate_case.get("failures"), list)
            else []
        )
        case_id = str(replay_gate_case.get("case_id") or "")
        filename = str(artifact.get("filename") or "browser screenshot")
        reasons = [
            str(item.get("reason") or "")
            for item in failures
            if isinstance(item, dict) and item.get("reason")
        ]
        reason_text = "; ".join(reasons) or "browser pixel evidence failed"
        replay_gate = (
            replay_gate_case.get("replay_gate")
            if isinstance(replay_gate_case.get("replay_gate"), dict)
            else {}
        )
        replay_summary = {
            "schema": replay_gate_case.get("schema"),
            "case_id": case_id,
            "fingerprint": _case_fingerprint(case_id),
            "replayable": False,
            "step_count": 1,
            "kind": replay_gate_case.get("kind"),
        }
        return ReviewQueue(app_paths().review_queue_path).upsert_item(
            source="browser_pixel_replay_gate",
            source_kind="browser_desktop_replay",
            candidate_kind="browser_pixel_replay_gate_case",
            priority="P0",
            target_bucket="browser_desktop_replay",
            title=f"Review browser pixel replay gate: {filename}",
            text=(
                f"Browser pixel replay gate case `{case_id}` failed for `{filename}`.\n"
                f"Reason: {reason_text}."
            ),
            metadata={
                "schema": replay_gate_case.get("schema"),
                "case_id": case_id,
                "replay": replay_summary,
                "replay_gate": replay_gate,
                "replay_gate_case": replay_gate_case,
                "artifact": artifact,
                "failure_count": len(failures),
            },
            source_task_ids=[str(replay_gate_case.get("task_id") or "")],
            agent_ids=[str(replay_gate_case.get("agent_id") or "")],
            tags=[
                "browser",
                "pixel",
                "replay_gate",
                "review_queue",
                "browser_pixel_evidence_failed",
            ],
        )
    except Exception:  # noqa: BLE001 - queueing must not break screenshots
        return None


def _case_fingerprint(case_id: str) -> str:
    return hashlib.blake2b(case_id.encode("utf-8"), digest_size=8).hexdigest()


def _emit_screenshot_artifact(bridge_response: dict[str, Any]) -> None:
    """Save the base64 screenshot to disk and push an ``artifact``
    event into the active SSE stream (when one is bound).

    File write is unconditional; the SSE push only fires when
    ``_ACTIVE_ARTIFACT_EMITTER`` is set by the pump in
    ``tool_bridge.stream_agentic_fallback``. Swallows all exceptions —
    a storage or stream failure must never surface as a skill error.
    """
    try:
        raw_b64 = bridge_response.get("data") or bridge_response.get("dataUrl") or ""
        if not raw_b64:
            return
        # Strip the optional data-URI prefix (data:image/png;base64,...)
        if "," in raw_b64:
            raw_b64 = raw_b64.split(",", 1)[1]
        img_bytes = base64.b64decode(raw_b64)

        # uuid suffix: on Windows/Py3.11 datetime.now() ticks at ~15.6ms,
        # so back-to-back screenshots got the SAME name — the second write
        # overwrote the first and the previous-pixel comparison compared
        # the file with itself.
        ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
        root = _artifacts_root()
        root.mkdir(parents=True, exist_ok=True)
        fname = f"screenshot-{ts}-{uuid4().hex[:6]}.png"
        fpath = root / fname
        previous_path = _LAST_SCREENSHOT_ARTIFACT.get()
        fpath.write_bytes(img_bytes)
        pixel_assertion = _pixel_assertion_for_screenshot(fpath)
        pixel_comparison = _pixel_comparison_for_screenshots(previous_path, fpath)
        _LAST_SCREENSHOT_ARTIFACT.set(fpath)

        width = bridge_response.get("width")
        height = bridge_response.get("height")
        caption = bridge_response.get("caption") or ""

        # Build the SSE-facing artifact event once so both delivery
        # paths (streaming emitter + legacy journal broadcast) push
        # the identical payload shape.
        try:
            from runtime.memory.journal.journal_context import (
                current_agent_id,
                current_conversation_id,
                current_owner_actor_id,
                current_tenant_id,
            )

            _thread_id = current_conversation_id() or ""
            _agent_id = current_agent_id() or ""
            _tenant_id = current_tenant_id() or ""
            _owner_actor_id = current_owner_actor_id() or ""
        except (ImportError, AttributeError):
            _thread_id = ""
            _agent_id = ""
            _tenant_id = ""
            _owner_actor_id = ""

        event: dict[str, Any] = {
            "type": "artifact",
            "kind": "screenshot",
            "url": f"/api/browser-artifacts/{fname}",
            "caption": caption,
            "ts": time.time(),
            # Additional fields — kept for backward compatibility with
            # the journal broadcast consumer; new consumers should only
            # rely on the four keys above.
            "thread_id": _thread_id,
            "agent_id": _agent_id,
            "tenant_id": _tenant_id,
            "owner_actor_id": _owner_actor_id,
            "skill": "live_browser_screenshot",
            "filename": fname,
            "mime_type": "image/png",
            "width": width,
            "height": height,
            "iso_ts": datetime.now(UTC).isoformat(),
            "local_path": str(fpath),
            "pixel_assertion": pixel_assertion,
        }
        if pixel_comparison is not None:
            event["pixel_comparison"] = pixel_comparison
        replay_gate_case = _pixel_replay_gate_case_for_artifact(
            event,
            pixel_assertion,
            pixel_comparison,
        )
        if replay_gate_case is not None:
            event["replay_gate_case"] = replay_gate_case
            queued = _queue_pixel_replay_gate_case(replay_gate_case)
            if queued is not None:
                event["replay_gate_queue"] = queued

        # Primary path · push to the live SSE queue when a stream is
        # attached. Zero wiring changes for intermediate callers because
        # the emitter is a ContextVar.
        try:
            emitter = _ACTIVE_ARTIFACT_EMITTER.get()
        except (LookupError, ValueError):
            emitter = None
        if emitter is not None:
            with contextlib.suppress(TypeError, ValueError):
                emitter(event)

        # Journal mirror · writes a ``BrowserArtifactEvent`` so any
        # journal subscriber (OpenAI gateway worker, observability
        # panel) sees the artifact the same way it sees step /
        # trajectory events. This is the path that delivers the
        # screenshot to the inline chat stream.
        try:
            from runtime.memory.journal import BrowserArtifactEvent

            # The browser artifact journal comes off the current Session
            # metadata (set by the worker loop). A former
            # ``sensing.gateway._active_streaming_journal`` singleton
            # accessor was removed long ago — its import always raised and
            # fell through to this path — so the upward gateway dependency
            # is dropped with the dead import.
            journal = None
            try:
                from runtime.platform.process.session import current_session

                sess = current_session()
                if sess is not None:
                    meta = getattr(sess, "metadata", None) or {}
                    if isinstance(meta, dict):
                        journal = meta.get("journal")
                        if journal is None:
                            stack = meta.get("stack")
                            if stack is not None:
                                journal = getattr(stack, "journal", None)
            except (ImportError, AttributeError):
                journal = None
            if journal is not None:
                journal.write(
                    BrowserArtifactEvent(
                        kind="screenshot",
                        url=event["url"],
                        filename=fname,
                        caption=caption,
                        mime_type="image/png",
                        width=int(width) if width is not None else None,
                        height=int(height) if height is not None else None,
                        thread_id=_thread_id,
                    )
                )
        except (AttributeError, OSError, TypeError, ValueError):  # noqa: BLE001 — browser bridge best-effort
            # Mirroring is best-effort; never break the skill call.
            pass

        # (A legacy broadcast fallback used to live here, but it only
        # fired via the removed ``_active_streaming_journal`` accessor —
        # always-None in practice — so it was inert dead code and is gone.)
    except Exception:  # noqa: BLE001 — browser bridge best-effort
        pass


def _h_execute_js(code: str) -> dict[str, Any]:
    return _bridge_call("execute-js", {"code": code})


def _h_current_url() -> dict[str, Any]:
    extension_result = _extension_fallback_result("state", {"max_items": 1})
    if extension_result is not None:
        return {
            "ok": bool(extension_result.get("ok")),
            "url": extension_result.get("url", ""),
            "title": extension_result.get("title", ""),
            "track": extension_result.get("track", "extension"),
            "live_browser_fallback": extension_result.get("live_browser_fallback"),
            **({"error": extension_result.get("error")} if extension_result.get("error") else {}),
        }
    return _bridge_call("current-url", {})


def _h_find(
    text: str = "",
    *,
    query: str | None = None,
    case_sensitive: bool = False,
    max_results: int = 20,
    context_chars: int = 80,
    **_: Any,
) -> dict[str, Any]:
    needle = str(query if query is not None else text).strip()
    if not needle:
        return {"ok": False, "error": "missing text", "matches": []}
    max_results = max(1, min(int(max_results), 100))
    context_chars = max(20, min(int(context_chars), 500))
    extension_result = _extension_fallback_result("extract", {})
    if extension_result is not None:
        if not extension_result.get("ok"):
            return {**extension_result, "matches": []}
        body_text = str(extension_result.get("text") or "")
        haystack = body_text if case_sensitive else body_text.lower()
        target = needle if case_sensitive else needle.lower()
        matches: list[dict[str, Any]] = []
        start = 0
        while len(matches) < max_results:
            index = haystack.find(target, start)
            if index < 0:
                break
            left = max(0, index - context_chars)
            right = min(len(body_text), index + len(needle) + context_chars)
            matches.append(
                {
                    "index": index,
                    "snippet": " ".join(body_text[left:right].split()),
                }
            )
            start = index + max(1, len(target))
        return {
            "ok": True,
            "url": extension_result.get("url", ""),
            "title": extension_result.get("title", ""),
            "text": needle,
            "count": len(matches),
            "truncated": len(matches) >= max_results,
            "matches": matches,
            "track": extension_result.get("track", "extension"),
            "live_browser_fallback": extension_result.get("live_browser_fallback"),
        }
    code = f"""(() => {{
        const needle = {json.dumps(needle)};
        const caseSensitive = {json.dumps(bool(case_sensitive))};
        const maxResults = {max_results};
        const contextChars = {context_chars};
        const bodyText = (document.body?.innerText || document.body?.textContent || '');
        const haystack = caseSensitive ? bodyText : bodyText.toLowerCase();
        const target = caseSensitive ? needle : needle.toLowerCase();
        const matches = [];
        let start = 0;
        while (matches.length < maxResults) {{
            const idx = haystack.indexOf(target, start);
            if (idx < 0) break;
            const left = Math.max(0, idx - contextChars);
            const right = Math.min(bodyText.length, idx + needle.length + contextChars);
            matches.push({{
                index: idx,
                snippet: bodyText.slice(left, right).replace(/\\s+/g, ' ').trim()
            }});
            start = idx + Math.max(1, target.length);
        }}
        return {{
            url: location.href,
            title: document.title,
            text: needle,
            count: matches.length,
            truncated: matches.length >= maxResults,
            matches
        }};
    }})()"""
    result = _h_execute_js(code)
    if result.get("ok") and isinstance(result.get("result"), dict):
        return {"ok": True, **result["result"]}
    return result


def _h_state(max_items: int = 30, **_: Any) -> dict[str, Any]:
    max_items = max(1, min(int(max_items), 100))
    extension_result = _extension_fallback_result("state", {"max_items": max_items})
    if extension_result is not None:
        return extension_result
    from runtime.execution.suckers.browser_dom_js import dom_state_iife_js

    result = _h_execute_js(dom_state_iife_js(max_items))
    if result.get("ok") and isinstance(result.get("result"), dict):
        return {"ok": True, **result["result"]}
    return result


def register_browser_act_skills(registry: SkillRegistry) -> int:
    items: list[Skill] = []

    items.append(
        Skill(
            name="live_browser_click",
            description=(
                "Click an element on the **current active browser tab** by "
                "CSS selector. Echo prefers its desktop webview and uses "
                "the signed-in Chrome relay when no desktop webview is active.\n"
                "Args: {selector: CSS selector, e.g. 'button[type=submit]' "
                "or '#login'}.\n"
                "Returns {ok, tag, text, error?}. Use `browser_extract` "
                "after click to read the resulting page."
            ),
            affinity=["browser", "automation", "web"],
            cost_profile="low",
            trusted_source="skill://browser_act/click",
            handler=_h_click,
        )
    )

    items.append(
        Skill(
            name="live_browser_type",
            description=(
                "Type text into an input/textarea on the current active "
                "browser tab.\nArgs: {selector: CSS, text: string to type, "
                "clear?: bool (default false; true clears existing first)}.\n"
                "Triggers `input` and `change` events so React/Vue forms see "
                "the change. Returns {ok, value, error?}."
            ),
            affinity=["browser", "automation", "form"],
            cost_profile="low",
            trusted_source="skill://browser_act/type",
            handler=_h_type,
        )
    )

    items.append(
        Skill(
            name="live_browser_wait",
            description=(
                "Poll for a CSS selector to appear (and be visible) on the "
                "current page. Use after navigation or click when the next "
                "step needs an element that loads async.\n"
                "Args: {selector: CSS, timeout?: ms (default 10000)}.\n"
                "Returns {ok, elapsed, error?}."
            ),
            affinity=["browser", "automation", "wait"],
            cost_profile="low",
            trusted_source="skill://browser_act/wait",
            handler=_h_wait,
        )
    )

    items.append(
        Skill(
            name="live_browser_scroll",
            description=(
                "Scroll the current page. Two modes:\n"
                "- {selector: CSS} · scroll the element into view (smooth)\n"
                "- {delta_y: pixels} · scroll the page by N pixels (positive=down)\n"
                "Returns {ok, y?, error?}."
            ),
            affinity=["browser", "automation"],
            cost_profile="low",
            trusted_source="skill://browser_act/scroll",
            handler=_h_scroll,
        )
    )

    items.append(
        Skill(
            name="live_browser_navigate",
            description=(
                "Load a new URL in the current active tab.\n"
                "Args: {url: full https://... URL}.\n"
                "Returns {ok, url, error?}. Follow with `browser_extract` "
                "or `browser_wait` to confirm load."
            ),
            affinity=["browser", "navigation"],
            cost_profile="low",
            trusted_source="skill://browser_act/navigate",
            handler=_h_navigate,
        )
    )

    items.append(
        Skill(
            name="live_browser_extract",
            description=(
                "Read the current page's text content (article body or main, "
                "fall back to body). Excludes scripts/styles/iframes. "
                "Truncated to 20K chars.\n"
                "Args: {} (none).\n"
                "Returns {ok, url, title, text, truncated, textLength, error?}.\n"
                "Use this as the agent's primary 'see the page' primitive — "
                "cheap, fast, no LLM-vision cost."
            ),
            affinity=["browser", "read", "extract"],
            cost_profile="low",
            trusted_source="skill://browser_act/extract",
            handler=_h_extract,
        )
    )

    items.append(
        Skill(
            name="live_browser_screenshot",
            description=(
                "Capture a screenshot of the current active tab as a base64 "
                "PNG data URL. EXPENSIVE in context (data URL ~hundreds of "
                "KB chars). Prefer `browser_extract` for text-based reasoning. "
                "Use screenshot only when visual layout / image content "
                "actually matters.\nArgs: {} (none).\n"
                "Returns {ok, dataUrl, width, height, error?}."
            ),
            affinity=["browser", "vision", "screenshot"],
            cost_profile="mid",
            trusted_source="skill://browser_act/screenshot",
            handler=_h_screenshot,
        )
    )

    items.append(
        Skill(
            name="live_browser_execute_js",
            description=(
                "Run arbitrary JavaScript in the current page's main world. "
                "Escape hatch for things not covered by other primitives. "
                "Returns {ok, result, error?} where `result` is the JS "
                "expression's return value (must be JSON-serializable).\n"
                "Args: {code: JavaScript expression or IIFE}.\n"
                "Examples:\n"
                "- code='document.querySelectorAll(\"a\").length' → count links\n"
                "- code='[...document.querySelectorAll(\"h1\")].map(el=>el.innerText)' → all H1 text\n"
                "USE WITH CAUTION · arbitrary JS can break the page."
            ),
            affinity=["browser", "advanced", "automation"],
            cost_profile="low",
            trusted_source="skill://browser_act/execute_js",
            handler=_h_execute_js,
        )
    )

    items.append(
        Skill(
            name="live_browser_current_url",
            description=(
                "Return the URL and title of the current active browser tab. "
                "Cheap probe — use to verify navigation succeeded or to "
                "remember where you are without re-reading content.\n"
                "Args: {} (none).\nReturns {ok, url, title, error?}."
            ),
            affinity=["browser", "probe"],
            cost_profile="low",
            trusted_source="skill://browser_act/current_url",
            handler=_h_current_url,
        )
    )

    find_description = (
        "Find text on the current active browser tab and return match "
        "snippets. Args: {text/query, case_sensitive?, max_results?, "
        "context_chars?}. Returns {ok, url, title, count, matches, error?}."
    )
    state_description = (
        "Return the current active browser tab state: URL, title, viewport, "
        "scroll, and visible links/buttons/inputs/headings. Args: {max_items?}."
    )
    items.append(
        Skill(
            name="live_browser_find",
            description=find_description,
            affinity=["browser", "read", "find"],
            cost_profile="low",
            trusted_source="skill://browser_act/find",
            handler=_h_find,
        )
    )
    items.append(
        Skill(
            name="live_browser_state",
            description=state_description,
            affinity=["browser", "read", "observe"],
            cost_profile="low",
            trusted_source="skill://browser_act/state",
            handler=_h_state,
        )
    )
    if not registry.has("browser_find"):
        items.append(
            Skill(
                name="browser_find",
                description=find_description,
                affinity=["browser", "read", "find"],
                cost_profile="low",
                trusted_source="skill://browser_act/browser_find_alias",
                handler=_h_find,
            )
        )
    if not registry.has("browser_state"):
        items.append(
            Skill(
                name="browser_state",
                description=state_description,
                affinity=["browser", "read", "observe"],
                cost_profile="low",
                trusted_source="skill://browser_act/browser_state_alias",
                handler=_h_state,
            )
        )

    for skill in items:
        registry.register(skill)
    return len(items)


__all__ = ["register_browser_act_skills"]
