"""Runtime-readiness aggregation for the computer-automation router.

Split out of the former ~1994-line computer_router.py. Combines screen /
UIA / lease / replay-evidence checks into the single "is desktop
automation usable right now" payload the /status endpoint returns.
"""

from __future__ import annotations

from typing import Any

from .computer_diagnostics import _computer_capability
from .computer_replay_evidence import _computer_replay_evidence
from .computer_router_state import ComputerRouterState

_LEASE_TTL_SECONDS = 30


def _runtime_readiness(
    state: ComputerRouterState,
    *,
    screen_info: dict[str, Any],
    uia_status: dict[str, Any],
    lease_state: dict[str, Any],
) -> dict[str, Any]:
    screen_ok = "error" not in screen_info
    uia_ok = bool(uia_status.get("available"))
    lease_held = bool(lease_state.get("held"))
    replay_evidence = _computer_replay_evidence(state)
    capabilities = [
        _computer_capability(
            "screen_observation",
            title="Screen observation",
            available=screen_ok,
            critical=True,
            mode="pyautogui_screen_info",
            reason=str(screen_info.get("error") or ""),
            recommended_action=("check_display_or_desktop_permissions" if not screen_ok else ""),
            metadata={
                key: screen_info.get(key)
                for key in ("width", "height", "cursor_x", "cursor_y")
                if key in screen_info
            },
        ),
        _computer_capability(
            "preview_execute_contract",
            title="Preview-confirm-execute contract",
            available=True,
            critical=True,
            mode="token_preview_with_lease",
            metadata={
                "pending_count": len(state.pending),
                "lease_ttl_seconds": _LEASE_TTL_SECONDS,
            },
        ),
        _computer_capability(
            "lease_coordination",
            title="Desktop lease coordination",
            available=not lease_held,
            critical=False,
            mode="exclusive_operator_lease",
            reason=(
                f"lease held by {lease_state.get('owner_label') or lease_state.get('owner_id')}"
                if lease_held
                else ""
            ),
            recommended_action="wait_or_release_lease" if lease_held else "",
            metadata=lease_state,
        ),
        _computer_capability(
            "uia_semantic_grounding",
            title="UIA semantic grounding",
            available=uia_ok,
            critical=False,
            mode="accessibility_tree",
            reason=str(uia_status.get("error") or ""),
            recommended_action=("install_or_enable_uia_backend" if not uia_ok else ""),
            metadata={
                "platform": uia_status.get("platform"),
                "available": uia_status.get("available"),
            },
        ),
        _computer_capability(
            "replay_evidence",
            title="Replay evidence and review queue",
            available=True,
            critical=True,
            mode="activity_replay_case",
            metadata={
                "replay_ready": replay_evidence.get("replay_ready"),
                "case_id": replay_evidence.get("case_id"),
                "fingerprint": replay_evidence.get("fingerprint"),
                "activity_count": len(state.activity),
            },
        ),
    ]
    critical_blockers = [
        item for item in capabilities if item["critical"] and not item["available"]
    ]
    degraded = [item for item in capabilities if not item["available"] and not item["critical"]]
    if critical_blockers:
        health = "blocked"
    elif degraded:
        health = "degraded"
    else:
        health = "ready"
    recommended_actions = [
        str(item.get("recommended_action") or "")
        for item in [*critical_blockers, *degraded]
        if str(item.get("recommended_action") or "").strip()
    ]
    return {
        "schema": "echo.computer_runtime_readiness.v1",
        "ready": not critical_blockers,
        "health": health,
        "capabilities": capabilities,
        "degraded_capabilities": degraded,
        "critical_blockers": critical_blockers,
        "recommended_actions": recommended_actions,
        "replay_evidence": replay_evidence,
    }


__all__: list[str] = []
