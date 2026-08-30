"""API check/mutation helpers for browser/desktop repair recipe evidence.

Extracted from ``browser_desktop_repair_recipes.py``. These functions drive
the local API endpoints that produce fresh replay evidence during recipe
rerun verification. They depend only on the standard library and the
``_recipes_common`` primitives.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin
from urllib.request import Request, urlopen


def _run_api_check(
    path: str,
    *,
    api_base_url: str,
    api_get: Callable[[str], dict[str, Any]] | None,
) -> dict[str, Any]:
    try:
        data = api_get(path) if api_get is not None else _default_api_get(api_base_url, path)
        ok = _api_check_ok(path, data)
        return {
            "type": "api_check",
            "url": path,
            "ok": ok,
            "response": data,
        }
    except Exception as exc:  # noqa: BLE001 - failed checks are evidence too
        return {
            "type": "api_check",
            "url": path,
            "ok": False,
            "error": str(exc),
        }


def _run_api_mutation(
    method: str,
    path: str,
    body: dict[str, Any] | None,
    *,
    api_base_url: str,
    api_request: Callable[[str, str, dict[str, Any] | None], dict[str, Any]] | None,
) -> dict[str, Any]:
    try:
        data = (
            api_request(method, path, body)
            if api_request is not None
            else _default_api_request(api_base_url, method, path, body)
        )
        ok = data.get("ok") is not False and not str(data.get("error") or "")
        return {
            "type": "api_mutation",
            "method": method,
            "url": path,
            "ok": ok,
            "response": data,
        }
    except Exception as exc:  # noqa: BLE001 - failed producers are evidence too
        return {
            "type": "api_mutation",
            "method": method,
            "url": path,
            "ok": False,
            "error": str(exc),
        }


def _default_api_get(api_base_url: str, path: str) -> dict[str, Any]:
    if not path.startswith("/api/"):
        raise ValueError("api check path must start with /api/")
    url = urljoin(api_base_url.rstrip("/") + "/", path.lstrip("/"))
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=8) as response:  # noqa: S310 - localhost API check  # nosec B310 — audited localhost API endpoint
        raw = response.read(2_000_000)
    data = json.loads(raw.decode("utf-8"))
    return data if isinstance(data, dict) else {"value": data}


def _default_api_request(
    api_base_url: str,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> dict[str, Any]:
    if not path.startswith("/api/"):
        raise ValueError("api mutation path must start with /api/")
    url = urljoin(api_base_url.rstrip("/") + "/", path.lstrip("/"))
    data = json.dumps(body or {}).encode("utf-8")
    request = Request(
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    with urlopen(request, timeout=12) as response:  # noqa: S310 - localhost API producer  # nosec B310 — audited localhost API endpoint
        raw = response.read(2_000_000)
    parsed = json.loads(raw.decode("utf-8"))
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _api_check_ok(path: str, data: dict[str, Any]) -> bool:
    if data.get("ok") is False:
        return False
    if "/api/browser/session/replay-case" in path:
        return bool(data.get("replay_ready"))
    if "/api/browser/session/health" in path:
        return bool(data.get("healthy"))
    if "/api/computer/activity/replay-case" in path:
        return bool(data.get("replay_ready"))
    return True


def _provided_evidence_from_api_check(
    path: str,
    artifact: dict[str, Any],
) -> list[str]:
    if artifact.get("ok") is not True:
        return []
    if "/api/browser/session/replay-case" in path:
        return ["browser_session_replay_case"]
    if "/api/browser/session/health" in path:
        return ["session_health"]
    if "/api/computer/activity/replay-case" in path:
        return ["computer_activity_replay_case"]
    return []


def _provided_evidence_from_artifact(artifact: dict[str, Any]) -> list[str]:
    if artifact.get("ok") is not True:
        return []
    if artifact.get("type") == "pixel_assertion":
        return ["fresh_screenshot"]
    if artifact.get("type") == "pixel_comparison":
        return ["pixel_comparison"]
    if artifact.get("type") == "computer_screenshot_capture_evidence":
        return ["computer_screenshot_path_contract"]
    return []
