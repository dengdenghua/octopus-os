"""Agent-facing computer automation skills.

These skills are the conversational/agent surface for desktop automation.
They deliberately call the local ``/api/computer`` preview-confirm-execute
API instead of driving pyautogui directly, so an agent can observe and queue
candidate actions without turning the workspace page into a manual scripting
console.
"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from .registry import Skill, SkillRegistry

_DEFAULT_GATEWAY_BASE_URL = "http://127.0.0.1:8000"
_COMPUTER_API_PATH = "/api/computer"
_BASE_URL_ENV_KEYS = (
    "ECHO_COMPUTER_API_BASE_URL",
    "ECHO_INTERNAL_GATEWAY_BASE_URL",
    "ECHO_PUBLIC_BASE_URL",
)
_TIMEOUT_SECONDS = 90


def _computer_api_base_url() -> str:
    raw, _source = _configured_base_url()
    return _normalize_computer_api_base_url(raw)


def _computer_api_diagnostics() -> dict[str, Any]:
    raw, source = _configured_base_url()
    try:
        base_url = _normalize_computer_api_base_url(raw)
        error = ""
    except ValueError as exc:
        base_url = _normalize_computer_api_base_url(_DEFAULT_GATEWAY_BASE_URL)
        error = str(exc)
    return {
        "schema": "echo.computer_api_bridge.v1",
        "base_url": base_url,
        "configured_by": source,
        "env_keys": list(_BASE_URL_ENV_KEYS),
        "default_gateway_base_url": _DEFAULT_GATEWAY_BASE_URL,
        "error": error,
    }


def _configured_base_url() -> tuple[str, str]:
    for key in _BASE_URL_ENV_KEYS:
        value = os.environ.get(key, "").strip()
        if value:
            return value, key
    return _DEFAULT_GATEWAY_BASE_URL, "default"


def _normalize_computer_api_base_url(raw: str) -> str:
    value = raw.strip().rstrip("/")
    if not value:
        value = _DEFAULT_GATEWAY_BASE_URL
    parsed = urllib_parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"invalid computer api base url: {raw!r}")
    path = parsed.path.rstrip("/")
    if path.endswith(_COMPUTER_API_PATH):
        normalized_path = path
    elif path.endswith("/api"):
        normalized_path = f"{path}/computer"
    else:
        normalized_path = f"{path}{_COMPUTER_API_PATH}"
    return urllib_parse.urlunparse(
        parsed._replace(path=normalized_path, params="", query="", fragment=""),
    )


def _call(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    diagnostics = _computer_api_diagnostics()
    base_url = str(diagnostics["base_url"])
    route = path if path.startswith("/") else f"/{path}"
    url = f"{base_url}{route}"
    if body is not None:
        body = dict(body)
        target = _selected_desktop_target()
        if target is not None:
            body.setdefault("automation_target", target)
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    req = urllib_request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib_request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:  # nosec B310 — audited HTTP computer API endpoint
            raw = resp.read()
            data = json.loads(raw.decode("utf-8"))
            if isinstance(data, dict):
                data.setdefault("computer_api", diagnostics)
                return data
            return {"ok": True, "data": data, "computer_api": diagnostics}
    except urllib_error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8")
        except Exception:  # noqa: BLE001
            detail = exc.reason
        return {
            "ok": False,
            "error": f"computer api http {exc.code}: {detail}",
            "computer_api": diagnostics,
        }
    except urllib_error.URLError as exc:
        return {
            "ok": False,
            "error": f"computer api unreachable at {base_url}: {exc.reason}",
            "computer_api": diagnostics,
        }
    except (TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "error": f"computer api failed: {type(exc).__name__}: {exc}",
            "computer_api": diagnostics,
        }


def _selected_desktop_target() -> dict[str, Any] | None:
    try:
        from runtime.platform.process.session import current_session

        session = current_session()
        metadata = getattr(session, "metadata", None) if session is not None else None
        raw = (metadata or {}).get("automation_target")
        if not isinstance(raw, dict) or raw.get("kind") != "desktop_window":
            return None
        target_id = str(raw.get("id") or "").strip()
        if not target_id:
            return None
        return {
            "kind": "desktop_window",
            "source": str(raw.get("source") or "computer"),
            "id": target_id,
            "title": str(raw.get("title") or ""),
            "app_id": str(raw.get("app_id") or ""),
            "app_name": str(raw.get("app_name") or ""),
        }
    except (AttributeError, TypeError, ImportError):
        return None


def _compact_screenshot(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    out = dict(value)
    data_url = out.pop("data_url", None)
    if isinstance(data_url, str):
        out["data_url_omitted"] = True
        out["data_url_bytes"] = len(data_url)
    return out


def _compact_result(data: dict[str, Any]) -> dict[str, Any]:
    out = dict(data)
    if "screenshot" in out:
        out["screenshot"] = _compact_screenshot(out.get("screenshot"))
    raw = out.get("raw_output")
    if isinstance(raw, str) and len(raw) > 2000:
        out["raw_output"] = raw[:2000] + "...(truncated)"
    return out


def _computer_observe(
    capture: bool = True,
    *,
    uia: bool = False,
    **_kw: Any,
) -> dict[str, Any]:
    """Return desktop automation status and optionally screenshot/UIA context."""
    status = _call("GET", "/status")
    uia_tree = _call("GET", "/uia/tree?max_depth=2&max_nodes=80") if uia else None
    if not capture:
        out: dict[str, Any] = {
            "ok": bool(status.get("ok")),
            "status": status,
            "computer_api": _computer_api_diagnostics(),
        }
        if uia_tree is not None:
            out["uia"] = uia_tree
        return out
    shot = _call("POST", "/screenshot", {})
    out = {
        "ok": bool(status.get("ok")) and bool(shot.get("ok")),
        "status": status,
        "screenshot": _compact_screenshot(shot),
        "computer_api": _computer_api_diagnostics(),
    }
    if uia_tree is not None:
        out["uia"] = uia_tree
    return out


def _computer_plan_next(
    goal: str = "",
    *,
    use_vision: bool = False,
    model_id: str = "",
    capture: bool = True,
    **_kw: Any,
) -> dict[str, Any]:
    """Ask the local planner to produce queued candidate actions for a goal."""
    if not goal:
        return {"ok": False, "error": "missing goal"}
    if use_vision:
        data = _call("POST", "/actions/vision", {"goal": goal, "model_id": model_id})
    else:
        data = _call("POST", "/actions/plan", {"goal": goal, "capture": capture})
    return _compact_result(data)


def _computer_preview_action(action: dict[str, Any] | None = None, **_kw: Any) -> dict[str, Any]:
    """Validate and queue one action, returning a short-lived confirmation token."""
    if not isinstance(action, dict):
        return {"ok": False, "error": "action must be an object"}
    return _call("POST", "/actions/preview", action)


def _computer_execute_token(token: str = "", **_kw: Any) -> dict[str, Any]:
    """Execute a queued action by token.

    This should only be used after the user explicitly confirmed the specific
    queued action/token in the current conversation turn.
    """
    if not token:
        return {"ok": False, "error": "missing token"}
    return _call("POST", "/actions/execute", {"token": token})


def register_computer_api_skills(registry: SkillRegistry) -> int:
    items = [
        Skill(
            name="computer_observe",
            description=(
                "Observe the real desktop automation status and optionally capture "
                "a screenshot or UIA semantic tree. Args: {capture?: bool, "
                "uia?: bool}. Returns screen size, cursor, screenshot path/size, "
                "and optionally a bounded control tree; omits base64 image data "
                "to save context."
            ),
            affinity=["desktop", "automation", "observe", "computer"],
            cost_profile="low",
            trusted_source="skill://computer/observe",
            handler=_computer_observe,
        ),
        Skill(
            name="computer_plan_next",
            description=(
                "For an agent conversation: generate queued candidate desktop "
                "actions for a natural-language goal. Args: {goal: string, "
                "capture?: bool, use_vision?: bool, model_id?: string}. Returns "
                "suggestions with confirmation tokens; does not execute them."
            ),
            affinity=["desktop", "automation", "planner", "computer"],
            cost_profile="mid",
            trusted_source="skill://computer/plan_next",
            handler=_computer_plan_next,
        ),
        Skill(
            name="computer_preview_action",
            description=(
                "Validate and queue exactly one desktop action without executing. "
                "Args: {action:{action:'click'|'move'|'type'|'key'|'wait', ...}}. "
                "Returns risk and a short-lived token for later confirmation."
            ),
            affinity=["desktop", "automation", "preview", "computer"],
            cost_profile="low",
            trusted_source="skill://computer/preview_action",
            handler=_computer_preview_action,
        ),
        Skill(
            name="computer_execute_token",
            description=(
                "Execute a previously queued desktop action by token. Use only "
                "after the user explicitly confirms the exact token/action in the "
                "current conversation. Args: {token:string}."
            ),
            affinity=["desktop", "automation", "execute", "computer"],
            cost_profile="high",
            trusted_source="skill://computer/execute_token",
            handler=_computer_execute_token,
            # ADR-010 · executes a queued action on the one physical desktop
            # (via /api/computer/actions/execute) ⇒ serialise. observe/plan/
            # preview don't touch the screen, so they stay unmarked.
            exclusive_resource="device:desktop",
        ),
    ]
    for item in items:
        registry.register(item)
    return len(items)
