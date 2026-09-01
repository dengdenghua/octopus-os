"""Appshot and native desktop-target routes for computer automation."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from runtime.execution.suckers import computer_macos, computer_uia_skills

from .computer_control_session import _record_control_evidence
from .computer_lease import _lease_from_body
from .computer_router_state import ComputerRouterState

_ScreenshotHandler = Callable[[dict[str, Any] | None], dict[str, Any]]
_PreviewActionHandler = Callable[[dict[str, Any], str | None], dict[str, Any]]
_AuthDependency = Callable[[Request], str | None]


def _element_index(item: dict[str, Any]) -> int | None:
    """Return a semantic element index without trusting native tree values."""

    try:
        return int(item.get("index", -1))
    except (TypeError, ValueError, OverflowError):
        return None


def _element_identity(item: dict[str, Any]) -> str:
    """Stable-enough identity for accessibility diffs and action re-grounding.

    Native accessibility indexes are traversal-order offsets and may move after
    any UI update.  Keep the index for the current snapshot, but derive an
    identity from semantic attributes and bounds so callers can detect stale
    state and the native executor can re-find the control before acting.
    """

    basis = {
        "role": str(item.get("role") or item.get("control_type") or ""),
        "title": str(item.get("title") or item.get("name") or ""),
        "description": str(item.get("description") or ""),
        "automation_id": str(item.get("automation_id") or ""),
        "position": item.get("position") or item.get("rect"),
        "size": item.get("size"),
    }
    raw = json.dumps(basis, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "ax-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _enrich_elements(elements: list[Any]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for fallback_index, raw in enumerate(elements):
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        if _element_index(item) is None or "index" not in item:
            item["index"] = fallback_index
        item["semantic_id"] = _element_identity(item)
        enriched.append(item)
    return enriched


def _element_delta(
    previous: list[dict[str, Any]],
    current: list[dict[str, Any]],
) -> dict[str, Any]:
    before = {str(item.get("semantic_id")): item for item in previous}
    after = {str(item.get("semantic_id")): item for item in current}
    added = [item for key, item in after.items() if key not in before]
    removed = [item for key, item in before.items() if key not in after]
    return {
        "mode": "delta",
        "added": added,
        "removed": removed,
        "unchanged_count": len(set(before) & set(after)),
    }


def _element_center(item: dict[str, Any]) -> tuple[int, int] | None:
    center = item.get("center")
    if isinstance(center, dict):
        try:
            return int(center["x"]), int(center["y"])
        except (KeyError, TypeError, ValueError):
            pass
    position = item.get("position")
    size = item.get("size")
    if (
        isinstance(position, (list, tuple))
        and isinstance(size, (list, tuple))
        and len(position) >= 2
        and len(size) >= 2
    ):
        try:
            return (
                int(float(position[0]) + float(size[0]) / 2),
                int(float(position[1]) + float(size[1]) / 2),
            )
        except (TypeError, ValueError):
            pass
    rect = item.get("rect")
    if isinstance(rect, dict):
        try:
            return (
                int(rect["left"]) + int(rect["width"]) // 2,
                int(rect["top"]) + int(rect["height"]) // 2,
            )
        except (KeyError, TypeError, ValueError):
            pass
    return None


def register_computer_appshot_routes(
    *,
    router: APIRouter,
    state: ComputerRouterState,
    screenshot: _ScreenshotHandler,
    preview_action: _PreviewActionHandler,
    auth_dependency: _AuthDependency,
) -> None:
    """Register screenshot-grounded semantic target routes on ``router``."""

    @router.post("/appshot")
    def appshot(body: dict[str, Any] | None = None) -> dict[str, Any]:
        body = body or {}
        created_at = time.time()
        control_action_id = str(
            body.get("control_action_id") or f"computer-appshot-{uuid.uuid4().hex[:16]}"
        )
        capture = screenshot(
            {
                **body,
                "control_action_id": f"{control_action_id}:screenshot",
            }
        )
        if computer_macos.MACOS_NATIVE_AVAILABLE:
            semantic = computer_macos.accessibility_snapshot(
                max_nodes=int(body.get("max_nodes") or 120)
            )
        else:
            semantic = computer_uia_skills._computer_uia_tree(
                root="foreground",
                max_depth=3,
                max_nodes=int(body.get("max_nodes") or 120),
                max_children=40,
                include_offscreen=False,
            )
        raw_elements = semantic.get("elements") or semantic.get("nodes") or []
        elements = _enrich_elements(raw_elements if isinstance(raw_elements, list) else [])
        # Expose one normalized list on both macOS AX and Windows UIA. Keep the
        # backend-native fields too so existing clients remain compatible.
        semantic = dict(semantic)
        semantic["elements"] = elements

        previous_snapshot_id = str(body.get("previous_snapshot_id") or "").strip()
        previous_elements: list[dict[str, Any]] = []
        if previous_snapshot_id:
            with state.appshot_lock:
                previous = state.appshots.get(previous_snapshot_id)
            if isinstance(previous, dict):
                stored = previous.get("elements")
                if isinstance(stored, list):
                    previous_elements = [item for item in stored if isinstance(item, dict)]
        target = {
            "kind": "desktop_window",
            "source": "computer",
            "id": str((semantic.get("window") or {}).get("id") or "foreground"),
            "title": str((semantic.get("window") or {}).get("title") or "Current window"),
            "app_id": str((semantic.get("app") or {}).get("id") or ""),
            "app_name": str((semantic.get("app") or {}).get("displayName") or ""),
        }
        snapshot_basis = json.dumps(
            {
                "created_at": created_at,
                "target": target,
                "path": capture.get("path"),
                "size_bytes": capture.get("size_bytes"),
            },
            sort_keys=True,
        )
        snapshot_id = "appshot-" + hashlib.sha256(snapshot_basis.encode()).hexdigest()[:20]
        ok = bool(capture.get("ok"))
        payload: dict[str, Any] = {
            "schema": "echo.appshot.v1",
            "ok": ok,
            "snapshot_id": snapshot_id,
            "created_at": created_at,
            "target": target,
            "screenshot": capture,
            "accessibility": semantic,
        }
        if previous_snapshot_id and previous_elements:
            payload["accessibility_delta"] = {
                "from_snapshot_id": previous_snapshot_id,
                **_element_delta(previous_elements, elements),
            }
        with state.appshot_lock:
            state.appshots[snapshot_id] = {
                "created_at": created_at,
                "target": target,
                "elements": elements,
            }
            # Appshots are short-lived grounding references, not a second
            # screenshot archive. Retain at most the newest 24 for five minutes.
            cutoff = created_at - 300
            retained = sorted(
                (
                    (key, value)
                    for key, value in state.appshots.items()
                    if float(value.get("created_at") or 0) >= cutoff
                ),
                key=lambda item: float(item[1].get("created_at") or 0),
                reverse=True,
            )[:24]
            state.appshots = dict(retained)
        _record_control_evidence(
            state,
            body,
            action_id=control_action_id,
            kind="screenshot",
            action="computer_appshot",
            ok=ok,
            summary=f"{target['app_name'] or target['title']} · {snapshot_id}",
            detail={
                "schema": payload["schema"],
                "snapshot_id": snapshot_id,
                "target": target,
                "path": capture.get("path"),
                "accessibility_backend": semantic.get("backend"),
                "accessibility_error": semantic.get("error"),
            },
            owner=_lease_from_body(body),
        )
        return payload

    @router.get("/targets")
    def targets() -> dict[str, Any]:
        if computer_macos.MACOS_NATIVE_AVAILABLE:
            native = computer_macos.list_apps()
            raw_apps = native.get("apps")
            apps = raw_apps if isinstance(raw_apps, list) else []
            items: list[dict[str, Any]] = []
            for app in apps:
                if not isinstance(app, dict):
                    continue
                raw_windows = app.get("windows")
                windows = raw_windows if isinstance(raw_windows, list) else []
                if not windows and bool(app.get("frontmost")):
                    # Accessibility permission may reveal the foreground app
                    # before it reveals its window tree. Keep an honest,
                    # actionable "current window" target instead of making the
                    # picker disappear while the user is granting permission.
                    windows = [
                        {
                            "id": "foreground",
                            "title": app.get("displayName") or "Current window",
                            "position": None,
                            "size": None,
                        }
                    ]
                for window in windows:
                    if not isinstance(window, dict):
                        continue
                    items.append(
                        {
                            "kind": "desktop_window",
                            "source": "computer",
                            "id": str(window.get("id") or "foreground"),
                            "title": str(window.get("title") or app.get("displayName") or "Window"),
                            "app_id": str(app.get("id") or ""),
                            "app_name": str(app.get("displayName") or ""),
                            "frontmost": bool(app.get("frontmost")),
                            "position": window.get("position"),
                            "size": window.get("size"),
                        }
                    )
            items.sort(key=lambda item: (not item["frontmost"], item["app_name"], item["title"]))
            return {
                "schema": "echo.automation_targets.v1",
                "targets": items,
                "count": len(items),
                "backend": native.get("backend", "macos-native"),
                **({"error": native["error"]} if native.get("error") else {}),
            }
        return {
            "schema": "echo.automation_targets.v1",
            "targets": [],
            "count": 0,
            "backend": "unavailable",
        }

    @router.post("/appshots/{snapshot_id}/elements/{element_index}/preview")
    def preview_appshot_element(
        snapshot_id: str,
        element_index: int,
        body: dict[str, Any] | None = None,
        actor: str | None = Depends(auth_dependency),
    ) -> dict[str, Any]:
        body = body or {}
        with state.appshot_lock:
            snapshot = state.appshots.get(snapshot_id)
        if snapshot is None:
            raise HTTPException(404, "appshot not found or expired")
        if time.time() - float(snapshot.get("created_at") or 0) > 300:
            with state.appshot_lock:
                state.appshots.pop(snapshot_id, None)
            raise HTTPException(410, "appshot expired; capture a fresh appshot")
        raw_elements = snapshot.get("elements")
        elements = raw_elements if isinstance(raw_elements, list) else []
        element = next(
            (
                item
                for item in elements
                if isinstance(item, dict) and _element_index(item) == element_index
            ),
            None,
        )
        if element is None:
            raise HTTPException(404, "appshot element not found")
        center = _element_center(element)
        if center is None:
            raise HTTPException(409, "appshot element has no actionable bounds")
        x, y = center
        requested_action = str(body.get("action") or "press").strip().lower()
        semantic_actions = {
            "press",
            "show_menu",
            "increment",
            "decrement",
            "expand",
            "collapse",
        }
        if requested_action not in semantic_actions | {"click", "move"}:
            raise HTTPException(400, "unsupported appshot element action")
        # Preserve the established click/move preview contract. Native-capable
        # hosts try semantic_action first; all others safely use these bounds.
        action = requested_action if requested_action in {"click", "move"} else "click"
        matched_control = {
            "id": f"{snapshot_id}:{element_index}",
            "semantic_id": element.get("semantic_id"),
            "name": element.get("title") or element.get("description") or element.get("value"),
            "control_type": element.get("role") or element.get("control_type"),
            "center": {"x": x, "y": y},
            "snapshot_id": snapshot_id,
            "element_index": element_index,
        }
        semantic_target = {
            key: element.get(key)
            for key in (
                "semantic_id",
                "role",
                "control_type",
                "title",
                "name",
                "description",
                "value",
                "automation_id",
                "position",
                "size",
                "rect",
            )
            if element.get(key) not in (None, "")
        }
        return preview_action(
            {
                **body,
                "automation_target": snapshot.get("target"),
                "action": action,
                "x": x,
                "y": y,
                "source": "appshot",
                "matched_control": matched_control,
                "semantic_action": (
                    requested_action if requested_action in semantic_actions else "press"
                ),
                "semantic_target": semantic_target,
            },
            actor,
        )


__all__ = ["register_computer_appshot_routes"]
