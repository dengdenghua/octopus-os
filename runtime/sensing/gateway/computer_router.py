"""Computer automation API.

The surface is intentionally split into observe -> preview -> execute.
Mouse and keyboard actions are high-risk, so the UI must first create a
short-lived preview token and then send that token back for execution.

Route registration and per-request orchestration live here; the actual
logic is split across focused siblings (each independently importable and
tested) that take the router's shared mutable state (pending previews,
the exclusive-operator lease, the bounded activity log, the screenshot
root, and the ControlSessionStore) as an explicit ``ComputerRouterState``
parameter instead of closure capture:

  computer_router_state.py        the shared ComputerRouterState + constants
  computer_diagnostics.py         diagnostic/capability payload builders (pure)
  computer_replay_evidence.py     replay-evidence summary (needs state)
  computer_runtime_readiness.py   /status capability aggregation (needs state)
  computer_lease.py               exclusive-operator lease claim/release
  computer_control_session.py     ControlSessionStore bookkeeping + activity log
  computer_actions.py             action normalize/execute/preview + UIA planning
  _computer_appshot_routes.py     screenshot-grounded target route registration
  computer_vision.py              vision-model config + OpenAI-compatible call
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from runtime.execution.suckers import computer_skills, computer_uia_skills
from runtime.safety.replay.browser_desktop_replay import computer_activity_replay_identity

from ._computer_appshot_routes import register_computer_appshot_routes
from .computer_actions import (
    _actions_from_payload,
    _execute,
    _execution_proof,
    _extract_json_payload,
    _normalize_action,
    _plan_actions,
    _preview_contract,
    _queue_preview,
)
from .computer_control_session import (
    _cleanup_pending,
    _ensure_control_session,
    _queue_activity_replay_case,
    _queue_uia_replay_assertion,
    _record_activity,
    _record_control_action,
    _record_control_evidence,
    _update_control_action,
)
from .computer_diagnostics import _computer_diagnostic, _execution_failure_diagnostic
from .computer_lease import (
    _claim_lease,
    _effective_owner,
    _lease_from_body,
    _public_lease,
    _release_lease,
)
from .computer_replay_evidence import _computer_replay_evidence
from .computer_router_state import ComputerRouterState
from .computer_runtime_readiness import _runtime_readiness
from .computer_vision import _call_openai_vision, _vision_model_config


def create_computer_router(
    *,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
    escalation: Any = None,
) -> APIRouter:
    def _auth_dep(request: Request) -> str | None:
        # Desktop automation can click/type on the host machine. Keep
        # the current friction-free local-dev behavior, but in auth-on
        # deploys reject anonymous access at the router boundary. Returns the
        # resolved actor so lease-mutating routes can bind the exclusive lease
        # to the authenticated principal instead of a spoofable body field.
        from runtime.safety.auth.principal import require_operator, resolve_principal

        principal = resolve_principal(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )
        if require_auth:
            require_operator(
                request,
                identity_store,
                require_auth,
                jwt_secret=jwt_secret,
                jwt_issuer=jwt_issuer,
                jwt_audience=jwt_audience,
            )
        return principal.actor_id if principal is not None else None

    router = APIRouter(
        prefix="/api/computer",
        tags=["computer"],
        dependencies=[Depends(_auth_dep)],
    )
    state = ComputerRouterState(escalation=escalation)

    @router.get("/status")
    def status() -> dict[str, Any]:
        _cleanup_pending(state)
        lease_state = _public_lease(state)
        info = computer_skills._screen_info()
        uia_status = computer_uia_skills._computer_uia_status()
        readiness = _runtime_readiness(
            state,
            screen_info=info,
            uia_status=uia_status,
            lease_state=lease_state,
        )
        return {
            "schema": "echo.computer_runtime_status.v1",
            "ok": "error" not in info,
            "ready": readiness["ready"],
            "health": readiness["health"],
            "pyautogui_available": bool(computer_skills.PYAUTOGUI_AVAILABLE),
            "uia_available": bool(uia_status.get("available")),
            "uia": uia_status,
            "lease": lease_state,
            "screen": info,
            "readiness": readiness,
            "capabilities": readiness["capabilities"],
            "degraded_capabilities": readiness["degraded_capabilities"],
            "critical_blockers": readiness["critical_blockers"],
            "recommended_actions": readiness["recommended_actions"],
            "replay_evidence": readiness["replay_evidence"],
            "activity_count": len(state.activity),
            "recent_activity": state.activity[-10:],
            "skills": [
                "screen_capture",
                "screen_info",
                "mouse_click",
                "mouse_move",
                "keyboard_type",
                "keyboard_press",
                "computer_observe",
                "computer_plan_next",
                "computer_preview_action",
                "computer_execute_token",
                "computer_use_loop",
                "computer_uia_status",
                "computer_uia_tree",
                "computer_uia_find",
            ],
            "mode": "preview-confirm-execute-with-lease",
        }

    @router.get("/activity")
    def computer_activity(
        limit: int = Query(default=50, ge=1, le=500),
    ) -> dict[str, Any]:
        _cleanup_pending(state)
        return {
            "schema": "echo.computer_activity.v1",
            "count": len(state.activity),
            "pending_count": len(state.pending),
            "lease": _public_lease(state),
            "items": state.activity[-limit:],
        }

    @router.get("/activity/replay-case")
    def computer_activity_replay_case(
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        _cleanup_pending(state)
        items = state.activity[-limit:]
        identity = computer_activity_replay_identity(
            items=[item for item in items if isinstance(item, dict)],
            pending_count=len(state.pending),
        )
        return {
            "schema": "echo.computer_activity_replay_case.v1",
            "case_id": identity["case_id"],
            "fingerprint": identity["fingerprint"],
            "replay_ready": bool(items),
            "activity_count": len(items),
            "pending_count": len(state.pending),
            "lease": _public_lease(state),
            "items": items,
            "last_activity": items[-1] if items else None,
        }

    @router.post("/activity/replay-case/queue")
    def computer_activity_replay_case_queue(body: dict[str, Any] | None = None) -> dict[str, Any]:
        body = body or {}
        limit = int(body.get("limit") or 100)
        limit = max(1, min(500, limit))
        replay_case = computer_activity_replay_case(limit=limit)
        if not replay_case.get("replay_ready"):
            raise HTTPException(409, "computer activity replay case has no actions to review")
        queued = _queue_activity_replay_case(
            replay_case,
            reason=str(body.get("reason") or ""),
            priority=str(body.get("priority") or ""),
        )
        return {
            "ok": True,
            "schema": "echo.computer_activity_replay_case_queue.v1",
            "replay_case": replay_case,
            "queue": queued,
        }

    @router.post("/screenshot")
    def screenshot(body: dict[str, Any] | None = None) -> dict[str, Any]:
        body = body or {}
        owner = _lease_from_body(body)
        control_action_id = _record_control_action(
            state,
            body,
            action_type="computer_observe",
            descriptor={"type": "screenshot", "region": body.get("region")},
            status="running",
            owner=owner,
        )
        state.screenshot_root.mkdir(parents=True, exist_ok=True)
        shot_path = state.screenshot_root / f"{int(time.time())}_{uuid.uuid4().hex[:8]}.png"
        region = body.get("region")
        result = computer_skills._screen_capture(
            path=str(shot_path),
            sandbox_dir=str(state.screenshot_root),
            region=region if isinstance(region, list) else None,
        )
        if "error" in result:
            _update_control_action(
                state,
                body,
                control_action_id,
                status="failed",
                result=result,
                error=str(result.get("error") or ""),
            )
            _record_control_evidence(
                state,
                body,
                action_id=control_action_id,
                kind="screenshot",
                action="computer_observe",
                ok=False,
                summary=str(result.get("error") or "screenshot failed"),
                detail=result,
                owner=owner,
            )
            return {"ok": False, "error": result["error"]}
        data = shot_path.read_bytes()
        payload = {
            "ok": True,
            "path": str(shot_path),
            "size_bytes": len(data),
            "data_url": "data:image/png;base64," + base64.standard_b64encode(data).decode("ascii"),
            "created_at": time.time(),
        }
        _update_control_action(
            state,
            body,
            control_action_id,
            status="done",
            result={key: value for key, value in payload.items() if key != "data_url"},
        )
        _record_control_evidence(
            state,
            body,
            action_id=control_action_id,
            kind="screenshot",
            action="computer_observe",
            ok=True,
            summary=f"{len(data)} bytes",
            detail={
                "path": str(shot_path),
                "size_bytes": len(data),
                "created_at": payload["created_at"],
            },
            owner=owner,
        )
        return payload

    @router.get("/uia/status")
    def uia_status() -> dict[str, Any]:
        return computer_uia_skills._computer_uia_status()

    @router.get("/uia/tree")
    def uia_tree(
        root: str = Query(default="foreground"),
        max_depth: int = Query(default=2, ge=0, le=8),
        max_nodes: int = Query(default=80, ge=1, le=1000),
        max_children: int = Query(default=30, ge=1, le=200),
        include_offscreen: bool = Query(default=False),
    ) -> dict[str, Any]:
        return computer_uia_skills._computer_uia_tree(
            root=root,
            max_depth=max_depth,
            max_nodes=max_nodes,
            max_children=max_children,
            include_offscreen=include_offscreen,
        )

    @router.get("/uia/find")
    def uia_find(
        query: str = Query(default=""),
        exact: bool = Query(default=False),
        root: str = Query(default="foreground"),
        max_results: int = Query(default=20, ge=1, le=100),
        max_depth: int = Query(default=5, ge=0, le=8),
        max_nodes: int = Query(default=300, ge=1, le=1000),
        include_offscreen: bool = Query(default=False),
    ) -> dict[str, Any]:
        return computer_uia_skills._computer_uia_find(
            query=query,
            exact=exact,
            root=root,
            max_results=max_results,
            max_depth=max_depth,
            max_nodes=max_nodes,
            include_offscreen=include_offscreen,
        )

    @router.post("/actions/preview")
    def preview_action(
        body: dict[str, Any], actor: str | None = Depends(_auth_dep)
    ) -> dict[str, Any]:
        _cleanup_pending(state)
        owner = _effective_owner(body, actor)
        action = _normalize_action(body)
        preview = _queue_preview(state, action, owner)
        control_action_id = _record_control_action(
            state,
            body,
            action_id=str(body.get("control_action_id") or f"computer-preview-{preview['token']}"),
            action_type=str(action.get("action") or "computer_action"),
            descriptor={
                "type": "computer_preview",
                "action": action,
                "risk": preview["risk"],
                "preview_token": preview["token"],
                "preview_contract": preview["preview_contract"],
            },
            status="waiting_user",
            owner=owner,
        )
        _record_control_evidence(
            state,
            body,
            action_id=control_action_id,
            kind="action",
            action=str(action.get("action") or "computer_preview"),
            ok=True,
            summary=f"preview queued · {preview['risk']['level']}",
            detail={
                "token": preview["token"],
                "risk": preview["risk"],
                "preview_contract": preview["preview_contract"],
            },
            owner=owner,
        )
        _record_activity(
            state,
            "preview_queued",
            action=action,
            token=str(preview["token"]),
            risk=preview["risk"],
            detail={
                "lease_owner": owner,
                "preview_contract_id": preview["preview_contract"]["contract_id"],
            },
            proof={"preview_contract": preview["preview_contract"]},
        )
        return {
            "ok": True,
            "lease": _public_lease(state),
            **preview,
        }

    register_computer_appshot_routes(
        router=router,
        state=state,
        screenshot=screenshot,
        preview_action=preview_action,
        auth_dependency=_auth_dep,
    )

    @router.post("/actions/plan")
    def plan_actions(body: dict[str, Any]) -> dict[str, Any]:
        _cleanup_pending(state)
        owner = _lease_from_body(body)
        goal = str(body.get("goal") or "")
        capture = bool(body.get("capture", True))
        control_action_id = _record_control_action(
            state,
            body,
            action_type="computer_plan",
            descriptor={"type": "computer_plan", "goal": goal, "capture": capture},
            status="running",
            owner=owner,
        )
        screenshot_data: dict[str, Any] | None = None
        if capture:
            screenshot_data = screenshot(
                {**body, "control_action_id": f"{control_action_id}:screenshot"}
            )

        suggestions = []
        for idx, action in enumerate(_plan_actions(goal), start=1):
            preview = _queue_preview(state, action, owner)
            suggestions.append(
                {
                    "id": f"step-{idx}",
                    "title": f"Step {idx}: {action['action']}",
                    "rationale": "Heuristic next action based on the task text and current screen observation.",
                    **preview,
                }
            )
            _record_control_action(
                state,
                body,
                action_id=f"computer-preview-{preview['token']}",
                action_type=str(action.get("action") or "computer_action"),
                descriptor={
                    "type": "computer_preview",
                    "goal": goal,
                    "action": action,
                    "risk": preview["risk"],
                    "preview_token": preview["token"],
                },
                status="waiting_user",
                owner=owner,
            )
        _record_activity(
            state,
            "plan_created",
            detail={
                "goal": goal,
                "suggestion_count": len(suggestions),
                "capture": capture,
            },
        )
        payload = {
            "ok": True,
            "goal": goal,
            "screenshot": screenshot_data,
            "suggestions": suggestions,
            "mode": "observe-plan-confirm",
            "lease": _public_lease(state),
            "limitations": [
                "This first pass uses local heuristics and UIA semantic grounding when available.",
                "Visual screenshot grounding is only used by /actions/vision, not this local planner.",
                "Every suggested action still requires explicit user confirmation before execution.",
            ],
        }
        _update_control_action(
            state,
            body,
            control_action_id,
            status="done",
            result={"suggestion_count": len(suggestions), "mode": payload["mode"]},
        )
        _record_control_evidence(
            state,
            body,
            action_id=control_action_id,
            kind="result",
            action="computer_plan",
            ok=True,
            summary=f"{len(suggestions)} suggestion(s)",
            detail={
                "goal": goal,
                "suggestions": [
                    {
                        "id": item.get("id"),
                        "title": item.get("title"),
                        "action": item.get("action"),
                        "risk": item.get("risk"),
                        "token": item.get("token"),
                    }
                    for item in suggestions
                ],
            },
            owner=owner,
        )
        return payload

    @router.post("/actions/ground")
    def ground_actions(body: dict[str, Any]) -> dict[str, Any]:
        _cleanup_pending(state)
        owner = _lease_from_body(body)
        goal = str(body.get("goal") or "")
        output = body.get("output")
        capture = bool(body.get("capture", True))
        control_action_id = _record_control_action(
            state,
            body,
            action_type="computer_ground",
            descriptor={"type": "computer_ground", "goal": goal, "capture": capture},
            status="running",
            owner=owner,
        )
        screenshot_data: dict[str, Any] | None = None
        if capture:
            screenshot_data = screenshot(
                {**body, "control_action_id": f"{control_action_id}:screenshot"}
            )

        if output is None:
            _update_control_action(
                state,
                body,
                control_action_id,
                status="done",
                result={"suggestion_count": 0, "mode": "vision-output-adapter"},
            )
            _record_control_evidence(
                state,
                body,
                action_id=control_action_id,
                kind="result",
                action="computer_ground",
                ok=True,
                summary="schema helper returned",
                detail={"goal": goal, "suggestion_count": 0},
                owner=owner,
            )
            return {
                "ok": True,
                "goal": goal,
                "screenshot": screenshot_data,
                "suggestions": [],
                "mode": "vision-output-adapter",
                "lease": _public_lease(state),
                "schema": {
                    "actions": [
                        {"action": "click", "x": 100, "y": 200, "button": "left"},
                        {"action": "type", "text": "hello"},
                        {"action": "key", "keys": ["enter"]},
                    ]
                },
                "limitations": [
                    "No vision model output was provided.",
                    "Paste a JSON action from any vision model to validate and queue it.",
                ],
            }

        payload = _extract_json_payload(output if isinstance(output, str) else json.dumps(output))
        actions = _actions_from_payload(payload)
        suggestions = []
        for idx, action in enumerate(actions, start=1):
            preview = _queue_preview(state, action, owner)
            suggestions.append(
                {
                    "id": f"vision-{idx}",
                    "title": f"Vision {idx}: {action['action']}",
                    "rationale": "Validated action parsed from vision model output.",
                    **preview,
                }
            )
            _record_control_action(
                state,
                body,
                action_id=f"computer-preview-{preview['token']}",
                action_type=str(action.get("action") or "computer_action"),
                descriptor={
                    "type": "computer_preview",
                    "goal": goal,
                    "action": action,
                    "risk": preview["risk"],
                    "preview_token": preview["token"],
                    "source": "ground",
                },
                status="waiting_user",
                owner=owner,
            )
        _record_activity(
            state,
            "grounded_actions_created",
            detail={"goal": goal, "suggestion_count": len(suggestions)},
        )
        payload = {
            "ok": True,
            "goal": goal,
            "screenshot": screenshot_data,
            "suggestions": suggestions,
            "mode": "vision-output-adapter",
            "lease": _public_lease(state),
            "limitations": [
                "This endpoint validates vision output but does not execute automatically.",
                "Every parsed action still requires explicit user confirmation.",
            ],
        }
        _update_control_action(
            state,
            body,
            control_action_id,
            status="done",
            result={"suggestion_count": len(suggestions), "mode": payload["mode"]},
        )
        _record_control_evidence(
            state,
            body,
            action_id=control_action_id,
            kind="result",
            action="computer_ground",
            ok=True,
            summary=f"{len(suggestions)} grounded action(s)",
            detail={"goal": goal, "suggestion_count": len(suggestions)},
            owner=owner,
        )
        return payload

    @router.post("/actions/vision")
    def vision_actions(body: dict[str, Any]) -> dict[str, Any]:
        _cleanup_pending(state)
        owner = _lease_from_body(body)
        goal = str(body.get("goal") or "")
        model_id = str(body.get("model_id") or "")
        control_action_id = _record_control_action(
            state,
            body,
            action_type="computer_vision",
            descriptor={"type": "computer_vision", "goal": goal, "model_id": model_id},
            status="running",
            owner=owner,
        )
        config = _vision_model_config(model_id)
        screenshot_data = screenshot(
            {**body, "control_action_id": f"{control_action_id}:screenshot"}
        )
        if not screenshot_data.get("ok"):
            _update_control_action(
                state,
                body,
                control_action_id,
                status="failed",
                result={"screenshot": screenshot_data},
                error=str(screenshot_data.get("error") or "screenshot failed"),
            )
            return {
                "ok": False,
                "goal": goal,
                "screenshot": screenshot_data,
                "suggestions": [],
                "mode": "vision-model",
                "lease": _public_lease(state),
                "error": screenshot_data.get("error") or "screenshot failed",
            }
        if not config:
            _update_control_action(
                state,
                body,
                control_action_id,
                status="failed",
                result={"screenshot": screenshot_data},
                error="vision model not configured",
            )
            return {
                "ok": False,
                "goal": goal,
                "screenshot": screenshot_data,
                "suggestions": [],
                "mode": "vision-model",
                "lease": _public_lease(state),
                "error": (
                    "vision model not configured · pass model_id for a custom openai-compatible "
                    "model or set ECHO_COMPUTER_VISION_* env vars"
                ),
            }
        data_url = str(screenshot_data.get("data_url") or "")
        output = _call_openai_vision(config=config, goal=goal, data_url=data_url)
        payload = _extract_json_payload(output)
        actions = _actions_from_payload(payload)
        suggestions = []
        for idx, action in enumerate(actions, start=1):
            preview = _queue_preview(state, action, owner)
            suggestions.append(
                {
                    "id": f"vision-model-{idx}",
                    "title": f"Vision model {idx}: {action['action']}",
                    "rationale": "Grounded action returned by the configured vision model.",
                    **preview,
                }
            )
            _record_control_action(
                state,
                body,
                action_id=f"computer-preview-{preview['token']}",
                action_type=str(action.get("action") or "computer_action"),
                descriptor={
                    "type": "computer_preview",
                    "goal": goal,
                    "action": action,
                    "risk": preview["risk"],
                    "preview_token": preview["token"],
                    "source": "vision",
                },
                status="waiting_user",
                owner=owner,
            )
        _record_activity(
            state,
            "vision_actions_created",
            detail={
                "goal": goal,
                "model_id": str(config.get("id") or model_id),
                "suggestion_count": len(suggestions),
            },
        )
        payload = {
            "ok": True,
            "goal": goal,
            "model_id": str(config.get("id") or model_id),
            "screenshot": screenshot_data,
            "suggestions": suggestions,
            "mode": "vision-model",
            "lease": _public_lease(state),
            "raw_output": output,
            "limitations": [
                "The screenshot is sent to the configured vision model provider.",
                "Returned actions are validated and require explicit user confirmation.",
            ],
        }
        _update_control_action(
            state,
            body,
            control_action_id,
            status="done",
            result={"suggestion_count": len(suggestions), "mode": payload["mode"]},
        )
        _record_control_evidence(
            state,
            body,
            action_id=control_action_id,
            kind="result",
            action="computer_vision",
            ok=True,
            summary=f"{len(suggestions)} vision action(s)",
            detail={"goal": goal, "model_id": str(config.get("id") or model_id)},
            owner=owner,
        )
        return payload

    @router.post("/actions/execute")
    def execute_action(
        body: dict[str, Any], actor: str | None = Depends(_auth_dep)
    ) -> dict[str, Any]:
        _cleanup_pending(state)
        token = str(body.get("token") or "")
        control_action_id = str(body.get("control_action_id") or f"computer-preview-{token}")
        _update_control_action(state, body, control_action_id, status="running")
        item = state.pending.pop(token, None)
        if not item:
            diagnostic = _computer_diagnostic(
                "preview_token_missing",
                severity="error",
                message="Preview token was not found or has expired.",
                recommended_action="create_new_preview",
                metadata={"token_present": bool(token)},
            )
            _record_activity(
                state,
                "execute_rejected",
                ok=False,
                token=token,
                error="preview token not found or expired",
                detail={"diagnostic": diagnostic},
            )
            _update_control_action(
                state,
                body,
                control_action_id,
                status="failed",
                result={"diagnostic": diagnostic},
                error="preview token not found or expired",
            )
            _record_control_evidence(
                state,
                body,
                action_id=control_action_id,
                kind="result",
                action="computer_execute",
                ok=False,
                summary="preview token not found or expired",
                detail={"diagnostic": diagnostic, "token_present": bool(token)},
            )
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "preview token not found or expired",
                    "diagnostic": diagnostic,
                    "recommended_actions": ["create_new_preview"],
                    "replay_evidence": _computer_replay_evidence(state),
                },
            )
        body_owner = _effective_owner(body, actor)
        item_owner = item.get("lease_owner")
        if isinstance(item_owner, dict):
            owner = {
                "owner_id": str(item_owner.get("owner_id") or body_owner["owner_id"]),
                "owner_label": str(item_owner.get("owner_label") or body_owner["owner_label"]),
            }
            if body.get("lease_owner_id") and body_owner["owner_id"] != owner["owner_id"]:
                diagnostic = _computer_diagnostic(
                    "preview_owner_mismatch",
                    severity="error",
                    message="Preview token belongs to another operator.",
                    recommended_action="create_new_preview",
                    metadata={
                        "preview_owner_id": owner.get("owner_id"),
                        "requested_owner_id": body_owner.get("owner_id"),
                    },
                )
                _record_activity(
                    state,
                    "execute_rejected",
                    ok=False,
                    action=item.get("action") if isinstance(item.get("action"), dict) else {},
                    token=token,
                    risk=item.get("risk") if isinstance(item.get("risk"), dict) else {},
                    error="preview token belongs to another operator",
                    detail={
                        "lease_owner": owner,
                        "requested_owner": body_owner,
                        "diagnostic": diagnostic,
                    },
                )
                _update_control_action(
                    state,
                    body,
                    control_action_id,
                    status="failed",
                    result={"diagnostic": diagnostic},
                    error="preview token belongs to another operator",
                )
                _record_control_evidence(
                    state,
                    body,
                    action_id=control_action_id,
                    kind="result",
                    action="computer_execute",
                    ok=False,
                    summary="preview owner mismatch",
                    detail={"diagnostic": diagnostic},
                    owner=body_owner,
                )
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "preview token belongs to another operator",
                        "lease_owner": owner,
                        "diagnostic": diagnostic,
                        "recommended_actions": ["create_new_preview"],
                        "replay_evidence": _computer_replay_evidence(state),
                    },
                )
        else:
            owner = body_owner
        lease_state = _claim_lease(state, owner)
        action = item["action"]
        result = _execute(action)
        ok = "error" not in result
        diagnostic = _execution_failure_diagnostic(action, result) if not ok else {}
        raw_preview_contract = item.get("preview_contract")
        preview_contract: dict[str, Any]
        if isinstance(raw_preview_contract, dict):
            preview_contract = raw_preview_contract
        else:
            raw_risk = item.get("risk")
            preview_contract = _preview_contract(
                action,
                owner,
                raw_risk if isinstance(raw_risk, dict) else {},
            )
        execution_proof = _execution_proof(
            contract=preview_contract,
            action=action,
            risk=item["risk"],
            lease_state=lease_state,
            result=result,
            ok=ok,
        )
        _record_activity(
            state,
            "action_executed",
            ok=ok,
            action=action,
            token=token,
            risk=item["risk"],
            lease_state=lease_state,
            error=str(result.get("error") or ""),
            detail={
                "result": result,
                "preview_contract_id": preview_contract.get("contract_id"),
                "execution_proof_id": execution_proof.get("proof_id"),
                **({"diagnostic": diagnostic} if diagnostic else {}),
            },
            proof={
                "preview_contract": preview_contract,
                "execution_proof": execution_proof,
            },
        )
        replay_assertion = (
            action.get("replay_assertion")
            if isinstance(action.get("replay_assertion"), dict)
            else {}
        )
        assertion_queue = None
        if replay_assertion.get("ok") is False:
            assertion_queue = _queue_uia_replay_assertion(action, replay_assertion)
        payload = {
            "ok": ok,
            "action": action,
            "risk": item["risk"],
            "result": result,
            "preview_contract": preview_contract,
            "execution_proof": execution_proof,
            "lease": lease_state,
            "executed_at": time.time(),
            **({"replay_assertion_queue": assertion_queue} if assertion_queue else {}),
            **({"diagnostic": diagnostic} if diagnostic else {}),
            **({"recommended_actions": [diagnostic["recommended_action"]]} if diagnostic else {}),
            **({"replay_evidence": _computer_replay_evidence(state)} if not ok else {}),
        }
        _update_control_action(
            state,
            body,
            control_action_id,
            status="done" if ok else "failed",
            result={
                "result": result,
                "execution_proof_id": execution_proof.get("proof_id"),
                **({"diagnostic": diagnostic} if diagnostic else {}),
            },
            error=str(result.get("error") or ""),
        )
        _record_control_evidence(
            state,
            body,
            action_id=control_action_id,
            kind="result",
            action="computer_execute",
            ok=ok,
            summary="executed" if ok else str(result.get("error") or "execute failed"),
            detail={
                "action": action,
                "risk": item["risk"],
                "result": result,
                "preview_contract": preview_contract,
                "execution_proof": execution_proof,
            },
            owner=owner,
        )
        return payload

    @router.post("/lease/release")
    def release_lease(
        body: dict[str, Any] | None = None, actor: str | None = Depends(_auth_dep)
    ) -> dict[str, Any]:
        owner = _effective_owner(body, actor)
        force = bool((body or {}).get("force", False))
        lease_state = _release_lease(state, owner, force=force)
        _ensure_control_session(state, body, owner)
        _record_control_evidence(
            state,
            body,
            kind="lease",
            action="computer_lease_release",
            ok=True,
            summary="lease released",
            detail={"lease": lease_state, "force": force},
            owner=owner,
        )
        _record_activity(
            state,
            "lease_released",
            lease_state=lease_state,
            detail={"owner": owner, "force": force},
        )
        return {
            "ok": True,
            "lease": lease_state,
        }

    return router
