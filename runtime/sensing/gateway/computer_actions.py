"""Action normalization/execution/preview-contract + UIA goal-planning for
the computer-automation router.

Split out of the former ~1994-line computer_router.py. Everything here is
a pure function (build a dict from arguments, no shared-state access)
except ``_queue_preview``, which stages a preview token in
``state.pending`` for the confirm-then-execute flow.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from typing import Any

from fastapi import HTTPException

from runtime.execution.suckers import computer_macos, computer_skills, computer_uia_skills

from .computer_router_state import _PENDING_TTL_SECONDS, ComputerRouterState

_VALID_ACTIONS = {"click", "move", "type", "key", "wait"}


def _normalize_action(body: dict[str, Any]) -> dict[str, Any]:
    action = str(body.get("action") or "").strip().lower()
    if action not in _VALID_ACTIONS:
        raise HTTPException(400, f"unsupported action: {action or '<empty>'}")

    if action in {"click", "move"}:
        normalized = {
            "action": action,
            "x": int(body.get("x", -1)),
            "y": int(body.get("y", -1)),
            "button": str(body.get("button") or "left"),
            "clicks": int(body.get("clicks") or 1),
            "duration": float(body.get("duration") or 0.0),
        }
        for key in (
            "source",
            "matched_control",
            "replay_assertion",
            "semantic_target",
            "semantic_action",
        ):
            value = body.get(key)
            if isinstance(value, (str, dict)):
                normalized[key] = value
        return _with_automation_target(normalized, body)
    if action == "type":
        return _with_automation_target(
            {
                "action": "type",
                "text": str(body.get("text") or ""),
                "interval": float(body.get("interval") or 0.01),
            },
            body,
        )
    if action == "key":
        keys = body.get("keys")
        if isinstance(keys, str):
            keys = [k.strip() for k in keys.split("+") if k.strip()]
        if not isinstance(keys, list):
            raise HTTPException(400, "keys must be a list or + separated string")
        return _with_automation_target({"action": "key", "keys": keys}, body)
    return _with_automation_target({"action": "wait", "ms": int(body.get("ms") or 500)}, body)


def _with_automation_target(action: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    raw = body.get("automation_target")
    if not isinstance(raw, dict) or raw.get("kind") != "desktop_window":
        return action
    target_id = str(raw.get("id") or "").strip()
    if not target_id:
        return action
    action["automation_target"] = {
        "kind": "desktop_window",
        "source": str(raw.get("source") or "computer"),
        "id": target_id,
        "title": str(raw.get("title") or ""),
        "app_id": str(raw.get("app_id") or ""),
        "app_name": str(raw.get("app_name") or ""),
    }
    return action


def _risk_for(action: dict[str, Any]) -> dict[str, str]:
    kind = action["action"]
    if kind == "type":
        return {
            "level": "high",
            "reason": "will type text into the currently focused application",
        }
    if kind in {"click", "key"}:
        return {
            "level": "medium",
            "reason": "can trigger UI actions in the active application",
        }
    return {"level": "low", "reason": "does not submit data by itself"}


def _execute(action: dict[str, Any]) -> dict[str, Any]:
    kind = action["action"]
    target = action.get("automation_target")
    if kind != "wait" and isinstance(target, dict):
        from runtime.execution.suckers import computer_macos

        if computer_macos.MACOS_NATIVE_AVAILABLE:
            activated = computer_macos.activate_window_target(
                app_id=str(target.get("app_id") or ""),
                app_name=str(target.get("app_name") or ""),
                window_id=str(target.get("id") or ""),
                window_title=str(target.get("title") or ""),
            )
            if activated.get("error"):
                return {
                    "error": str(activated["error"]),
                    "automation_target": target,
                }
    if kind == "click":
        semantic_target = action.get("semantic_target")
        semantic_action = str(action.get("semantic_action") or "").strip()
        semantic_error = ""
        if isinstance(semantic_target, dict) and semantic_action:
            from runtime.execution.suckers import computer_macos

            if computer_macos.MACOS_NATIVE_AVAILABLE:
                semantic_result = computer_macos.perform_accessibility_action(
                    semantic_target,
                    action=semantic_action,
                )
                if not semantic_result.get("error"):
                    return {
                        **semantic_result,
                        "semantic": True,
                        "coordinate_fallback": False,
                    }
                semantic_error = str(semantic_result.get("error") or "")
        coordinate_result = computer_skills._mouse_click(
            x=int(action["x"]),
            y=int(action["y"]),
            button=str(action.get("button") or "left"),
            clicks=int(action.get("clicks") or 1),
            duration=float(action.get("duration") or 0.0),
        )
        if semantic_error and not coordinate_result.get("error"):
            return {
                **coordinate_result,
                "semantic": False,
                "coordinate_fallback": True,
                "semantic_error": semantic_error,
            }
        return coordinate_result
    if kind == "move":
        return computer_skills._mouse_move(
            x=int(action["x"]),
            y=int(action["y"]),
            duration=float(action.get("duration") or 0.2),
        )
    if kind == "type":
        return computer_skills._keyboard_type(
            text=str(action.get("text") or ""),
            interval=float(action.get("interval") or 0.01),
        )
    if kind == "key":
        return computer_skills._keyboard_press(keys=action.get("keys"))
    if kind == "wait":
        ms = int(action.get("ms") or 0)
        if ms < 0 or ms > 60_000:
            return {"error": f"wait ms out of range: {ms}"}
        time.sleep(ms / 1000)
        return {"waited_ms": ms}
    return {"error": f"unsupported action: {kind}"}


def _ensure_uia_replay_trace(action: dict[str, Any]) -> dict[str, Any]:
    assertion = (
        action.get("replay_assertion") if isinstance(action.get("replay_assertion"), dict) else {}
    )
    if not assertion or assertion.get("trace_id") and assertion.get("source_trace"):
        return action
    enriched = computer_uia_skills.uia_replay_assertion_for_action(action)
    merged = {
        **assertion,
        "trace_id": assertion.get("trace_id") or enriched.get("trace_id"),
        "source_trace": assertion.get("source_trace") or enriched.get("source_trace"),
    }
    action = dict(action)
    action["replay_assertion"] = merged
    return action


def _queue_preview(
    state: ComputerRouterState, action: dict[str, Any], owner: dict[str, str]
) -> dict[str, Any]:
    action = _ensure_uia_replay_trace(action)
    token = uuid.uuid4().hex
    risk = _risk_for(action)
    contract = _preview_contract(action, owner, risk)
    state.pending[token] = {
        "token": token,
        "action": action,
        "risk": risk,
        "preview_contract": contract,
        "created_at": time.time(),
        "lease_owner": owner,
    }
    return {
        "token": token,
        "action": action,
        "risk": risk,
        "preview_contract": contract,
        "expires_in_seconds": _PENDING_TTL_SECONDS,
        "lease_owner": owner,
    }


def _preview_contract(
    action: dict[str, Any],
    owner: dict[str, str],
    risk: dict[str, str],
) -> dict[str, Any]:
    payload = {
        "schema": "echo.computer_preview_contract.v1",
        "action": _stable_action_payload(action),
        "lease_owner": owner,
        "risk": risk,
        "ttl_seconds": _PENDING_TTL_SECONDS,
        "requires_execute_token": True,
    }
    payload["contract_id"] = _stable_digest(payload)
    return payload


def _execution_proof(
    *,
    contract: dict[str, Any],
    action: dict[str, Any],
    risk: dict[str, str],
    lease_state: dict[str, Any],
    result: dict[str, Any],
    ok: bool,
) -> dict[str, Any]:
    payload = {
        "schema": "echo.computer_execution_proof.v1",
        "preview_contract_id": contract.get("contract_id"),
        "action": _stable_action_payload(action),
        "risk": risk,
        "lease": {
            "held": bool(lease_state.get("held")),
            "owner_id": lease_state.get("owner_id"),
            "owner_label": lease_state.get("owner_label"),
        },
        "result": _stable_result_payload(result),
        "ok": ok,
    }
    payload["proof_id"] = _stable_digest(payload)
    return payload


def _stable_action_payload(action: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "action",
        "x",
        "y",
        "button",
        "clicks",
        "duration",
        "text",
        "interval",
        "keys",
        "ms",
        "source",
        "matched_control",
        "replay_assertion",
        "automation_target",
        "semantic_target",
        "semantic_action",
    }
    return {key: _stable_value(action.get(key)) for key in sorted(allowed) if key in action}


def _stable_result_payload(result: dict[str, Any]) -> dict[str, Any]:
    return {
        str(key): _stable_value(value)
        for key, value in sorted(result.items())
        if str(key) not in {"created_at", "timestamp", "token"}
    }


def _stable_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _stable_value(item)
            for key, item in sorted(value.items())
            if str(key) not in {"created_at", "timestamp", "token"}
        }
    if isinstance(value, list):
        return [_stable_value(item) for item in value]
    if isinstance(value, bool | int | float) or value is None:
        return value
    return str(value)


def _stable_digest(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _goal_uia_queries(goal: str) -> list[str]:
    text = goal.strip()
    if not text:
        return []
    candidates: list[str] = []
    for match in re.finditer(r"[\"'“”‘’]([^\"'“”‘’]{1,60})[\"'“”‘’]", text):
        candidates.append(match.group(1))
    patterns = (
        r"(?:click|press|select|open)\s+(?:the\s+)?(.{1,60})",
        r"(?:点击|单击|按下|选择|打开)\s*([^,，。；;:：\n\r]{1,40})",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            candidates.append(match.group(1))
    if not candidates and len(text) <= 40 and "http://" not in text and "https://" not in text:
        candidates.append(text)

    cleaned: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        item = candidate.strip(" \t\r\n'\"“”‘’()[]{}<>:：,，.。;；")
        item = re.sub(
            r"\b(button|control|field|link|menu)\b",
            "",
            item,
            flags=re.IGNORECASE,
        )
        item = item.replace("按钮", "").replace("控件", "").replace("菜单", "")
        item = " ".join(item.split()).strip()
        key = item.lower()
        if item and key not in seen:
            seen.add(key)
            cleaned.append(item)
    return cleaned[:5]


def _uia_actions_for_goal(goal: str) -> list[dict[str, Any]]:
    candidates: list[tuple[int, str, dict[str, Any]]] = []
    for query in _goal_uia_queries(goal):
        found = computer_uia_skills._computer_uia_find(
            query=query,
            max_results=5,
            max_depth=5,
            max_nodes=300,
        )
        if not found.get("ok"):
            continue
        for match in found.get("matches") or []:
            if not isinstance(match, dict):
                continue
            if match.get("offscreen") or match.get("enabled") is False:
                continue
            center = match.get("center")
            if not isinstance(center, dict):
                continue
            try:
                x = int(center["x"])
                y = int(center["y"])
            except (KeyError, TypeError, ValueError):
                continue
            score = _uia_match_score(match, query)
            action = {
                "action": "click",
                "x": x,
                "y": y,
                "button": "left",
                "clicks": 1,
                "duration": 0.0,
                "source": "uia",
                "matched_control": {
                    "id": match.get("id"),
                    "name": match.get("name"),
                    "control_type": match.get("control_type"),
                    "class_name": match.get("class_name"),
                    "automation_id": match.get("automation_id"),
                    "center": center,
                    "rect": match.get("rect"),
                    "query": query,
                    "score": score,
                },
            }
            action["replay_assertion"] = computer_uia_skills.uia_replay_assertion_for_action(
                action,
            )
            candidates.append((score, query, action))
    if not candidates:
        return []
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [candidates[0][2]]


def _macos_actions_for_goal(goal: str) -> list[dict[str, Any]]:
    if not computer_macos.MACOS_NATIVE_AVAILABLE:
        return []
    snapshot = computer_macos.accessibility_snapshot(max_nodes=300)
    if snapshot.get("error"):
        return []
    candidates: list[tuple[int, dict[str, Any]]] = []
    for query in _goal_uia_queries(goal):
        needle = query.strip().lower()
        for element in snapshot.get("elements") or []:
            if not isinstance(element, dict) or element.get("enabled") is False:
                continue
            position = element.get("position")
            size = element.get("size")
            if not (
                isinstance(position, (list, tuple))
                and len(position) >= 2
                and isinstance(size, (list, tuple))
                and len(size) >= 2
            ):
                continue
            name = str(
                element.get("title") or element.get("description") or element.get("value") or ""
            ).strip()
            lowered = name.lower()
            if not needle or needle not in lowered:
                continue
            role = str(element.get("role") or "")
            score = 100 if lowered == needle else 70 if lowered.startswith(needle) else 50
            if any(kind in role.lower() for kind in ("button", "menu", "link", "tab")):
                score += 20
            x = int(float(position[0]) + float(size[0]) / 2)
            y = int(float(position[1]) + float(size[1]) / 2)
            semantic_target = {
                key: element.get(key)
                for key in (
                    "role",
                    "title",
                    "description",
                    "value",
                    "position",
                    "size",
                )
                if element.get(key) not in (None, "")
            }
            candidates.append(
                (
                    score,
                    {
                        "action": "click",
                        "x": x,
                        "y": y,
                        "button": "left",
                        "clicks": 1,
                        "duration": 0.0,
                        "source": "macos-accessibility",
                        "semantic_action": "press",
                        "semantic_target": semantic_target,
                        "matched_control": {
                            "name": name,
                            "control_type": role,
                            "center": {"x": x, "y": y},
                            "query": query,
                            "score": score,
                        },
                    },
                )
            )
    if not candidates:
        return []
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [candidates[0][1]]


def _uia_match_score(match: dict[str, Any], query: str) -> int:
    needle = query.strip().lower()
    name = str(match.get("name") or "").strip().lower()
    automation_id = str(match.get("automation_id") or "").strip().lower()
    class_name = str(match.get("class_name") or "").strip().lower()
    control_type = str(match.get("control_type") or "").strip().lower()

    score = 0
    if bool(match.get("interactive")):
        score += 100
    if name == needle:
        score += 80
    elif name.startswith(needle):
        score += 60
    elif needle and needle in name:
        score += 40
    if automation_id == needle:
        score += 70
    elif needle and needle in automation_id:
        score += 35
    if needle and needle in class_name:
        score += 10
    if "button" in control_type or "menuitem" in control_type or "hyperlink" in control_type:
        score += 15
    if match.get("enabled") is not False:
        score += 5
    return score


def _plan_actions(goal: str) -> list[dict[str, Any]]:
    text = goal.strip().lower()
    if not text:
        return []

    actions: list[dict[str, Any]] = []
    if "http://" in text or "https://" in text:
        url = goal.strip()
        for marker in ("http://", "https://"):
            idx = url.lower().find(marker)
            if idx >= 0:
                url = url[idx:].split()[0]
                break
        actions.extend(
            [
                {"action": "key", "keys": ["ctrl", "l"]},
                {"action": "type", "text": url, "interval": 0.01},
                {"action": "key", "keys": ["enter"]},
            ]
        )
    elif semantic_actions := (_macos_actions_for_goal(goal) or _uia_actions_for_goal(goal)):
        actions.extend(semantic_actions)
    elif any(word in text for word in ("browser", "chrome", "edge", "浏览器", "网页")):
        actions.extend(
            [
                {"action": "key", "keys": ["win"]},
                {"action": "type", "text": "edge", "interval": 0.01},
                {"action": "key", "keys": ["enter"]},
            ]
        )
    elif any(word in text for word in ("刷新", "reload", "refresh")):
        actions.append({"action": "key", "keys": ["ctrl", "r"]})
    elif any(word in text for word in ("关闭", "close")):
        actions.append({"action": "key", "keys": ["alt", "f4"]})
    else:
        actions.append({"action": "wait", "ms": 500})
    return actions[:5]


def _extract_json_payload(text: str) -> Any:
    cleaned = text.strip()
    if not cleaned:
        raise HTTPException(400, "missing vision output")
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL | re.IGNORECASE)
    if fenced:
        cleaned = fenced.group(1).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start_candidates = [idx for idx in (cleaned.find("{"), cleaned.find("[")) if idx >= 0]
        if not start_candidates:
            raise HTTPException(400, "vision output does not contain JSON") from None
        start = min(start_candidates)
        end = max(cleaned.rfind("}"), cleaned.rfind("]"))
        if end <= start:
            raise HTTPException(400, "vision output JSON is incomplete") from None
        try:
            return json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise HTTPException(400, f"invalid vision output JSON: {exc}") from None


def _actions_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("actions"), list):
        raw_actions = payload["actions"]
    elif isinstance(payload, dict) and isinstance(payload.get("suggestions"), list):
        raw_actions = [
            item.get("action") if isinstance(item, dict) else item
            for item in payload["suggestions"]
        ]
    elif isinstance(payload, list):
        raw_actions = payload
    else:
        raw_actions = [payload]

    actions = []
    for raw in raw_actions[:5]:
        if not isinstance(raw, dict):
            continue
        normalized_input = dict(raw)
        if "action" not in normalized_input and "type" in normalized_input:
            normalized_input["action"] = normalized_input["type"]
        actions.append(_normalize_action(normalized_input))
    return actions


__all__: list[str] = []
