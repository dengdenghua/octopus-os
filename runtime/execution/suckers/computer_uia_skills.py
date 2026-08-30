from __future__ import annotations

import hashlib
import importlib.util
import json
import platform
from typing import Any

from .registry import Skill, SkillRegistry

uiautomation: Any | None = None
UIA_AVAILABLE = importlib.util.find_spec("uiautomation") is not None
_UIA_LOAD_ERROR: str | None = None

UIA_SKILL_NAMES = [
    "computer_uia_status",
    "computer_uia_tree",
    "computer_uia_find",
]

_INTERACTIVE_TYPES = {
    "button",
    "calendar",
    "checkbox",
    "combobox",
    "dataitem",
    "edit",
    "hyperlink",
    "listitem",
    "menuitem",
    "radio button",
    "radiobutton",
    "slider",
    "spinner",
    "splitbutton",
    "tabitem",
    "treeitem",
}


def _check_uia() -> str | None:
    global _UIA_LOAD_ERROR, uiautomation
    if platform.system() != "Windows":
        return "windows UI Automation is only available on Windows"
    if not UIA_AVAILABLE and uiautomation is None:
        return "uiautomation not installed - `pip install uiautomation`"
    if uiautomation is None and _UIA_LOAD_ERROR is None:
        try:
            import uiautomation as _uiautomation  # type: ignore[import-not-found]

            uiautomation = _uiautomation
        except Exception as exc:  # noqa: BLE001
            _UIA_LOAD_ERROR = f"{type(exc).__name__}: {exc}"
    if uiautomation is None:
        return f"uiautomation unavailable: {_UIA_LOAD_ERROR or 'load failed'}"
    return None


def _safe_attr(obj: Any, *names: str, default: Any = "") -> Any:
    for name in names:
        try:
            value = getattr(obj, name)
        except Exception:  # noqa: BLE001
            continue
        try:
            return value() if callable(value) and name.startswith("get_") else value
        except Exception:  # noqa: BLE001
            continue
    return default


def _clean_text(value: Any, limit: int = 160) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    text = " ".join(text.split())
    if len(text) > limit:
        return text[: limit - 1] + "..."
    return text


def _rect_to_dict(rect: Any) -> dict[str, int] | None:
    if rect is None:
        return None
    if isinstance(rect, (list, tuple)) and len(rect) >= 4:
        left, top, right, bottom = rect[:4]
    else:
        left = _safe_attr(rect, "left", "Left", default=None)
        top = _safe_attr(rect, "top", "Top", default=None)
        right = _safe_attr(rect, "right", "Right", default=None)
        bottom = _safe_attr(rect, "bottom", "Bottom", default=None)
    try:
        left_i = int(left)
        top_i = int(top)
        right_i = int(right)
        bottom_i = int(bottom)
    except (TypeError, ValueError):
        return None
    width = max(0, right_i - left_i)
    height = max(0, bottom_i - top_i)
    return {
        "left": left_i,
        "top": top_i,
        "right": right_i,
        "bottom": bottom_i,
        "x": left_i,
        "y": top_i,
        "width": width,
        "height": height,
    }


def _center(rect: dict[str, int] | None) -> dict[str, int] | None:
    if not rect or rect["width"] <= 0 or rect["height"] <= 0:
        return None
    return {
        "x": rect["left"] + rect["width"] // 2,
        "y": rect["top"] + rect["height"] // 2,
    }


def _get_children(control: Any, max_children: int) -> list[Any]:
    try:
        children = control.GetChildren()
    except Exception:  # noqa: BLE001
        return []
    if children is None:
        return []
    try:
        return list(children)[:max_children]
    except TypeError:
        return []


def _control_kind(control: Any) -> str:
    value = _safe_attr(control, "ControlTypeName", "control_type", "type")
    text = _clean_text(value, limit=80)
    if text.endswith("Control"):
        text = text[:-7]
    return text


def _control_node(control: Any, path: str) -> dict[str, Any]:
    rect = _rect_to_dict(_safe_attr(control, "BoundingRectangle", "bounding_rect", default=None))
    kind = _control_kind(control)
    kind_key = kind.replace("Control", "").replace("_", " ").lower()
    center = _center(rect)
    node: dict[str, Any] = {
        "id": path,
        "name": _clean_text(_safe_attr(control, "Name", "name")),
        "control_type": kind,
        "class_name": _clean_text(_safe_attr(control, "ClassName", "class_name"), limit=120),
        "automation_id": _clean_text(
            _safe_attr(control, "AutomationId", "automation_id"),
            limit=120,
        ),
        "enabled": bool(_safe_attr(control, "IsEnabled", "enabled", default=True)),
        "offscreen": bool(_safe_attr(control, "IsOffscreen", "offscreen", default=False)),
        "rect": rect,
        "interactive": kind_key in _INTERACTIVE_TYPES,
    }
    if center is not None:
        node["center"] = center
    return node


def _resolve_root(root: str) -> tuple[Any | None, str | None]:
    err = _check_uia()
    if err:
        return None, err
    assert uiautomation is not None
    root_key = str(root or "foreground").strip().lower()
    try:
        if root_key in {"desktop", "root"}:
            return uiautomation.GetRootControl(), None
        getter = getattr(uiautomation, "GetForegroundControl", None)
        if callable(getter):
            control = getter()
            if control is not None:
                return control, None
        return uiautomation.GetRootControl(), None
    except Exception as exc:  # noqa: BLE001
        return None, f"uia root failed: {type(exc).__name__}: {exc}"


def _collect_nodes(
    control: Any,
    *,
    max_depth: int,
    max_nodes: int,
    max_children: int,
    include_offscreen: bool,
) -> tuple[dict[str, Any], int, bool]:
    count = 0
    truncated = False

    def walk(current: Any, depth: int, path: str) -> dict[str, Any] | None:
        nonlocal count, truncated
        if count >= max_nodes:
            truncated = True
            return None
        node = _control_node(current, path)
        if node["offscreen"] and not include_offscreen and depth > 0:
            return None
        count += 1
        if depth >= max_depth:
            return node
        children: list[dict[str, Any]] = []
        for idx, child in enumerate(_get_children(current, max_children)):
            child_node = walk(child, depth + 1, f"{path}.{idx}")
            if child_node is not None:
                children.append(child_node)
            if count >= max_nodes:
                truncated = True
                break
        if children:
            node["children"] = children
        return node

    root = walk(control, 0, "0") or _control_node(control, "0")
    return root, count, truncated


def _flatten(node: dict[str, Any]) -> list[dict[str, Any]]:
    out = [dict(node, children=None)]
    out[0].pop("children", None)
    for child in node.get("children") or []:
        if isinstance(child, dict):
            out.extend(_flatten(child))
    return out


def _computer_uia_status(**_kw: Any) -> dict[str, Any]:
    err = _check_uia()
    return {
        "ok": err is None,
        "platform": platform.system(),
        "backend": "uiautomation",
        "available": err is None,
        "error": err,
    }


def _computer_uia_tree(
    root: str = "foreground",
    *,
    max_depth: int = 2,
    max_nodes: int = 80,
    max_children: int = 30,
    include_offscreen: bool = False,
    **_kw: Any,
) -> dict[str, Any]:
    max_depth = max(0, min(int(max_depth), 8))
    max_nodes = max(1, min(int(max_nodes), 1000))
    max_children = max(1, min(int(max_children), 200))
    control, err = _resolve_root(root)
    if err or control is None:
        return {
            "ok": False,
            "error": err or "uia root unavailable",
            "root": root,
            "nodes": [],
            "count": 0,
        }
    tree, count, truncated = _collect_nodes(
        control,
        max_depth=max_depth,
        max_nodes=max_nodes,
        max_children=max_children,
        include_offscreen=include_offscreen,
    )
    return {
        "ok": True,
        "root": root,
        "backend": "uiautomation",
        "count": count,
        "truncated": truncated,
        "tree": tree,
        "nodes": _flatten(tree),
    }


def _matches(node: dict[str, Any], query: str, exact: bool) -> bool:
    fields = (
        node.get("name"),
        node.get("control_type"),
        node.get("class_name"),
        node.get("automation_id"),
    )
    haystacks = [str(value or "") for value in fields]
    if exact:
        return any(value.lower() == query for value in haystacks)
    return any(query in value.lower() for value in haystacks)


def _computer_uia_find(
    query: str = "",
    *,
    exact: bool = False,
    root: str = "foreground",
    max_results: int = 20,
    max_depth: int = 5,
    max_nodes: int = 300,
    include_offscreen: bool = False,
    **_kw: Any,
) -> dict[str, Any]:
    needle = str(query or "").strip().lower()
    if not needle:
        return {"ok": False, "error": "missing query", "matches": []}
    tree = _computer_uia_tree(
        root=root,
        max_depth=max_depth,
        max_nodes=max_nodes,
        include_offscreen=include_offscreen,
    )
    if not tree.get("ok"):
        return {"ok": False, "error": tree.get("error"), "matches": []}
    limit = max(1, min(int(max_results), 100))
    matches = [
        node
        for node in tree.get("nodes", [])
        if isinstance(node, dict) and _matches(node, needle, exact)
    ][:limit]
    return {
        "ok": True,
        "query": query,
        "exact": exact,
        "root": root,
        "count": len(matches),
        "truncated": len(matches) >= limit,
        "matches": matches,
    }


def uia_replay_assertion_for_action(action: dict[str, Any]) -> dict[str, Any]:
    matched = (
        action.get("matched_control") if isinstance(action.get("matched_control"), dict) else {}
    )
    rect = matched.get("rect") if isinstance(matched.get("rect"), dict) else {}
    center = matched.get("center") if isinstance(matched.get("center"), dict) else {}
    failures: list[str] = []
    try:
        action_x = int(action.get("x"))
        action_y = int(action.get("y"))
        center_x = int(center.get("x"))
        center_y = int(center.get("y"))
    except (TypeError, ValueError):
        failures.append("action or control center coordinates are missing")
        action_x = action_y = center_x = center_y = 0
    else:
        if (action_x, action_y) != (center_x, center_y):
            failures.append("action coordinates do not match the resolved control center")

    try:
        left = int(rect.get("left"))
        top = int(rect.get("top"))
        right = int(rect.get("right"))
        bottom = int(rect.get("bottom"))
    except (TypeError, ValueError):
        failures.append("resolved control rectangle is missing")
        left = top = right = bottom = 0
    else:
        if not (left <= center_x <= right and top <= center_y <= bottom):
            failures.append("resolved control center is outside its rectangle")

    if not str(matched.get("automation_id") or matched.get("name") or "").strip():
        failures.append("resolved control has no stable name or automation id")

    ok = not failures
    source_trace = {
        "schema": "echo.computer_uia_grounding_trace.v1",
        "source": action.get("source"),
        "query": matched.get("query"),
        "matched_control": {
            "id": matched.get("id"),
            "name": matched.get("name"),
            "control_type": matched.get("control_type"),
            "class_name": matched.get("class_name"),
            "automation_id": matched.get("automation_id"),
            "center": center,
            "rect": rect,
            "score": matched.get("score"),
        },
    }
    trace_id = _stable_trace_id(source_trace)
    return {
        "schema": "echo.computer_uia_replay_assertion.v1",
        "trace_id": trace_id,
        "ok": ok,
        "reason": "passed" if ok else "; ".join(failures),
        "action": {
            "action": action.get("action"),
            "x": action.get("x"),
            "y": action.get("y"),
            "source": action.get("source"),
        },
        "source_trace": source_trace,
        "matched_control": {
            "id": matched.get("id"),
            "name": matched.get("name"),
            "control_type": matched.get("control_type"),
            "class_name": matched.get("class_name"),
            "automation_id": matched.get("automation_id"),
            "center": center,
            "rect": rect,
            "query": matched.get("query"),
            "score": matched.get("score"),
        },
    }


def _stable_trace_id(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def register_computer_uia_skills(registry: SkillRegistry) -> int:
    items = [
        Skill(
            name="computer_uia_status",
            description=(
                "Report whether Windows UI Automation semantic desktop "
                "inspection is available. Read-only."
            ),
            affinity=["desktop", "automation", "uia", "observe"],
            cost_profile="low",
            trusted_source="skill://computer/uia_status",
            handler=_computer_uia_status,
        ),
        Skill(
            name="computer_uia_tree",
            description=(
                "Return a bounded Windows UI Automation control tree for "
                "the foreground window or desktop root. Read-only. Args: "
                "{root?: 'foreground'|'desktop', max_depth?, max_nodes?, "
                "include_offscreen?}."
            ),
            affinity=["desktop", "automation", "uia", "observe"],
            cost_profile="low",
            trusted_source="skill://computer/uia_tree",
            handler=_computer_uia_tree,
        ),
        Skill(
            name="computer_uia_find",
            description=(
                "Find desktop controls by visible name, control type, class "
                "name, or automation id using Windows UI Automation. "
                "Read-only; returned centers can be used with previewed "
                "mouse actions."
            ),
            affinity=["desktop", "automation", "uia", "find"],
            cost_profile="low",
            trusted_source="skill://computer/uia_find",
            handler=_computer_uia_find,
        ),
    ]
    for item in items:
        registry.register(item)
    return len(items)


__all__ = [
    "UIA_AVAILABLE",
    "UIA_SKILL_NAMES",
    "_computer_uia_find",
    "_computer_uia_status",
    "_computer_uia_tree",
    "register_computer_uia_skills",
    "uia_replay_assertion_for_action",
]
