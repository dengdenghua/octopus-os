"""Fresh evidence producers for browser/desktop repair recipe reruns.

Extracted from ``browser_desktop_repair_recipes.py``. These functions drive
the local browser API and the production computer-use loop to capture fresh
screenshots, pixel comparisons and screenshot-path-contract evidence that a
recipe rerun attaches before promotion.

Dependency order: ``_recipes_common`` → ``_recipes_api`` → this module.
The main ``browser_desktop_repair_recipes`` module imports the single public
entry point ``_produce_fresh_recipe_evidence`` and a handful of pixel helpers
needed by the rerun flow.
"""

from __future__ import annotations

import base64
import hashlib
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from runtime.safety.replay.browser_pixel_assertions import (
    assert_screenshot_pixels,
    compare_screenshot_pixels,
)

from ._recipes_api import _run_api_check, _run_api_mutation
from ._recipes_common import _dict


def _produce_fresh_recipe_evidence(
    recipe: dict[str, Any],
    *,
    api_base_url: str,
    api_get: Callable[[str], dict[str, Any]] | None,
    api_request: Callable[[str, str, dict[str, Any] | None], dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    kind = str(recipe.get("candidate_kind") or "")
    if kind == "browser_session_replay_case":
        return _produce_browser_session_evidence(
            recipe,
            api_base_url=api_base_url,
            api_request=api_request,
        )
    if kind == "browser_pixel_replay_gate_case":
        return _produce_browser_pixel_evidence(
            recipe,
            api_base_url=api_base_url,
            api_get=api_get,
            api_request=api_request,
        )
    if kind == "computer_activity_replay_case":
        return _produce_computer_activity_evidence(recipe)
    return []


def _produce_computer_activity_evidence(
    recipe: dict[str, Any],
) -> list[dict[str, Any]]:
    """Reproduce the resolved-screenshot contract for its exact failure cluster.

    The historical P0 cluster failed before the planner could act because the
    capture layer returned a sandbox-resolved path while the loop passed the
    original relative path onward. This probe runs the production loop with a
    capture adapter that deliberately returns a different readable path, then
    requires the planner to read that authoritative artifact.
    """

    summary = _dict(recipe.get("evidence_summary"))
    reason = str(summary.get("reason") or "")
    if "screenshot read failed" not in reason.lower():
        return []

    from runtime.execution.suckers.computer_use_loop import _run_computer_use_loop

    planner_paths: list[str] = []

    class _PathContractPlanner:
        def next_action(
            self,
            *,
            goal: str,
            screenshot_path: str,
            history: list[dict[str, Any]],
        ) -> dict[str, Any]:
            del goal, history
            planner_paths.append(screenshot_path)
            image = Path(screenshot_path).read_bytes()
            return {
                "action": "done",
                "summary": f"read {len(image)} screenshot bytes",
            }

    try:
        with tempfile.TemporaryDirectory(prefix="echo-computer-replay-") as tmp:
            root = Path(tmp)
            captured = root / "sandbox" / "resolved" / "iter_000.png"
            captured.parent.mkdir(parents=True)

            def _capture(**_kwargs: Any) -> dict[str, Any]:
                # Minimal valid PNG signature plus deterministic payload; the
                # contract under test is durable path handoff, not pixel CV.
                captured.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 256)
                return {
                    "path": str(captured),
                    "size_bytes": captured.stat().st_size,
                    "region": None,
                }

            result = _run_computer_use_loop(
                goal="verify sandbox-resolved screenshot handoff",
                planner=_PathContractPlanner(),
                screenshot_dir=".",
                sandbox_dir=str(root / "sandbox"),
                max_iterations=1,
                wait_between_ms=0,
                stop_on_error=True,
                capture_screen=_capture,
            )
            captured_bytes = captured.read_bytes()
            used_path = planner_paths[0] if planner_paths else ""
            ok = (
                result.get("status") == "success"
                and used_path == str(captured)
                and result.get("screenshots") == [str(captured)]
            )
            return [
                {
                    "type": "computer_screenshot_capture_evidence",
                    "schema": "echo.computer_screenshot_path_contract.v1",
                    "ok": ok,
                    "historical_reason": reason,
                    "requested_path": "iter_000.png",
                    "captured_path": str(captured),
                    "planner_path": used_path,
                    "captured_path_is_authoritative": used_path == str(captured),
                    "screenshot_bytes": len(captured_bytes),
                    "screenshot_sha256": hashlib.sha256(captured_bytes).hexdigest(),
                    "status": result.get("status"),
                    "summary": result.get("summary"),
                }
            ]
    except Exception as exc:  # noqa: BLE001 - failed probe is evidence
        return [
            {
                "type": "computer_screenshot_capture_evidence",
                "schema": "echo.computer_screenshot_path_contract.v1",
                "ok": False,
                "historical_reason": reason,
                "error": f"{type(exc).__name__}: {exc}",
            }
        ]


def _produce_browser_session_evidence(
    recipe: dict[str, Any],
    *,
    api_base_url: str,
    api_request: Callable[[str, str, dict[str, Any] | None], dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    plan = _dict(recipe.get("verification_plan"))
    session_id = _session_id_from_plan(plan) or "workspace"
    summary = _dict(recipe.get("evidence_summary"))
    last_action = _dict(summary.get("last_action"))
    navigate_url = str(last_action.get("detail") or "").strip()
    out = [
        _run_api_mutation(
            "POST",
            "/api/browser/session/ensure",
            {
                "session_id": session_id,
                "headless": True,
            },
            api_base_url=api_base_url,
            api_request=api_request,
        )
    ]
    if navigate_url.startswith(("http://", "https://")):
        out.append(
            _run_api_mutation(
                "POST",
                "/api/browser/navigate",
                {
                    "session_id": session_id,
                    "url": navigate_url,
                },
                api_base_url=api_base_url,
                api_request=api_request,
            )
        )
    return out


def _produce_browser_pixel_evidence(
    recipe: dict[str, Any],
    *,
    api_base_url: str,
    api_get: Callable[[str], dict[str, Any]] | None,
    api_request: Callable[[str, str, dict[str, Any] | None], dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    session_id = _pixel_session_id(recipe)
    out: list[dict[str, Any]] = []
    source_artifact = _source_artifact_check(recipe)
    if source_artifact:
        out.append(source_artifact)
    out.append(
        _run_api_mutation(
            "POST",
            "/api/browser/session/reset",
            {
                "session_id": session_id,
                "relaunch": False,
            },
            api_base_url=api_base_url,
            api_request=api_request,
        )
    )
    out = [
        *out,
        _run_api_mutation(
            "POST",
            "/api/browser/session/ensure",
            {
                "session_id": session_id,
                "headless": True,
            },
            api_base_url=api_base_url,
            api_request=api_request,
        ),
    ]
    navigate_url = _pixel_recipe_url(recipe)
    if navigate_url:
        out.append(
            _run_api_mutation(
                "POST",
                "/api/browser/navigate",
                {
                    "session_id": session_id,
                    "url": navigate_url,
                },
                api_base_url=api_base_url,
                api_request=api_request,
            )
        )
    before = _run_screenshot_check(
        f"/api/browser/screenshot/base64?session_id={session_id}",
        label="before",
        api_base_url=api_base_url,
        api_get=api_get,
    )
    out.append(before)
    out.append(
        _run_api_mutation(
            "POST",
            "/api/browser/action",
            {
                "session_id": session_id,
                "action": "reload",
            },
            api_base_url=api_base_url,
            api_request=api_request,
        )
    )
    after = _run_screenshot_check(
        f"/api/browser/screenshot/base64?session_id={session_id}",
        label="after",
        api_base_url=api_base_url,
        api_get=api_get,
    )
    out.append(after)
    after_bytes = after.get("_screenshot_bytes")
    if isinstance(after_bytes, bytes):
        out.append(_run_pixel_assertion(after_bytes))
    before_bytes = before.get("_screenshot_bytes")
    if isinstance(before_bytes, bytes) and isinstance(after_bytes, bytes):
        out.append(_run_pixel_comparison(before_bytes, after_bytes, recipe))
    out.append(
        _run_api_mutation(
            "POST",
            "/api/browser/session/reset",
            {
                "session_id": session_id,
                "relaunch": False,
            },
            api_base_url=api_base_url,
            api_request=api_request,
        )
    )
    return [_public_artifact(row) for row in out]


def _run_screenshot_check(
    path: str,
    *,
    label: str,
    api_base_url: str,
    api_get: Callable[[str], dict[str, Any]] | None,
) -> dict[str, Any]:
    result = _run_api_check(path, api_base_url=api_base_url, api_get=api_get)
    result["type"] = "screenshot_check"
    result["label"] = label
    if result.get("ok") is not True:
        return result
    try:
        encoded = str(_dict(result.get("response")).get("base64") or "")
        if encoded.startswith("data:"):
            encoded = encoded.split(",", 1)[1]
        screenshot = base64.b64decode(encoded, validate=True)
        result["_screenshot_bytes"] = screenshot
        result["bytes"] = len(screenshot)
    except Exception as exc:  # noqa: BLE001 - malformed screenshots are evidence
        result["ok"] = False
        result["error"] = f"invalid screenshot payload: {exc}"
    return result


def _run_pixel_assertion(screenshot: bytes) -> dict[str, Any]:
    try:
        assertion = assert_screenshot_pixels(screenshot)
        return {
            "type": "pixel_assertion",
            "ok": assertion.get("ok") is True,
            "assertion": assertion,
        }
    except Exception as exc:  # noqa: BLE001 - failed pixel parsing is evidence
        return {
            "type": "pixel_assertion",
            "ok": False,
            "error": str(exc),
        }


def _run_pixel_comparison(
    before: bytes,
    after: bytes,
    recipe: dict[str, Any],
) -> dict[str, Any]:
    try:
        comparison = compare_screenshot_pixels(
            before,
            after,
            min_changed_ratio=_pixel_min_changed_ratio(recipe),
        )
        return {
            "type": "pixel_comparison",
            "ok": comparison.get("ok") is True,
            "comparison": comparison,
        }
    except Exception as exc:  # noqa: BLE001 - failed pixel parsing is evidence
        return {
            "type": "pixel_comparison",
            "ok": False,
            "error": str(exc),
        }


def _public_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in artifact.items() if not str(key).startswith("_")}


def _pixel_session_id(recipe: dict[str, Any]) -> str:
    seed = str(recipe.get("recipe_id") or recipe.get("cluster_key") or "pixel")
    digest = hashlib.sha256(seed.encode()).hexdigest()[:10]
    return f"browser-pixel-{digest}"


def _pixel_recipe_url(recipe: dict[str, Any]) -> str:
    summary = _dict(recipe.get("evidence_summary"))
    artifact = _dict(summary.get("artifact"))
    url = str(artifact.get("url") or "").strip()
    return url if url.startswith(("http://", "https://")) else ""


def _source_artifact_check(recipe: dict[str, Any]) -> dict[str, Any] | None:
    summary = _dict(recipe.get("evidence_summary"))
    artifact = _dict(summary.get("artifact"))
    local_path = str(artifact.get("local_path") or "").strip()
    if not local_path:
        return None
    path = Path(local_path)
    return {
        "type": "source_artifact",
        "ok": path.is_file(),
        "path": local_path,
        "reason": "available" if path.is_file() else "source artifact is no longer readable",
    }


def _pixel_min_changed_ratio(recipe: dict[str, Any]) -> float:
    summary = _dict(recipe.get("evidence_summary"))
    thresholds = _dict(_dict(summary.get("failure")).get("thresholds"))
    value = thresholds.get("min_changed_ratio")
    try:
        ratio = float(value)
    except (TypeError, ValueError):
        return 0.01
    return max(0.0, min(ratio, 1.0))


def _session_id_from_plan(plan: dict[str, Any]) -> str:
    for check in plan.get("api_checks") or []:
        text = str(check or "")
        if "session_id=" not in text:
            continue
        query = parse_qs(urlparse(text).query)
        session_id = str((query.get("session_id") or [""])[0]).strip()
        if session_id:
            return session_id
    return ""
