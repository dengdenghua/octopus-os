"""Agent device transport compatibility surface consumed by Echo OS."""

from __future__ import annotations

import secrets
from typing import Any


def set_active_device_coordinator(coordinator: Any) -> None:
    from runtime.tentacle.team_bridge import set_active_coordinator

    set_active_coordinator(coordinator)


def create_device_coordinator(context: Any, *, port: int) -> Any:
    from runtime.core.cerebrum.planner import StaticPlanner
    from runtime.tentacle.coordinator import TentacleCoordinator
    from runtime.tentacle.mobile.cerebrum_adapter import CerebrumDecisionAdapter

    planner = getattr(getattr(context, "stack", None), "planner", None)
    if not callable(getattr(planner, "plan", None)):
        planner = StaticPlanner()
    return TentacleCoordinator(
        host="0.0.0.0",  # nosec B104 - explicit, approval-bound LAN feature
        port=port,
        dashboard_port=None,
        decision_engine=CerebrumDecisionAdapter(planner).decide,
        auth_token=secrets.token_urlsafe(32),
    )


__all__ = ["create_device_coordinator", "set_active_device_coordinator"]
