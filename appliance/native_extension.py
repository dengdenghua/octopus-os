"""Minimal Echo OS extension for the loopback-native Agent service.

The NAS appliance extension deliberately owns authentication, Docker/NAS
control surfaces, and the browser desktop root.  A real Echo OS installation
already has an operating-system login boundary and runs Agent on loopback, so
it needs a much smaller integration point: expose the independently built
Agent workbench, publish its verified source identity, and project the real
Agent task supervisor for the native desktop.
"""

from __future__ import annotations

import os
from typing import Any


def register_app(app: Any, _context: Any) -> None:
    """Expose the verified Agent runtime to a native Echo OS device."""

    if os.environ.get("ECHO_NATIVE_OS") != "1":
        return

    from appliance.agent_api.contract import require_agent_api_contract
    from appliance.agent_ui import mount_agent_ui
    from appliance.task_projection import create_task_projection_router

    mount_agent_ui(app)
    app.state.echo_agent_api_contract = require_agent_api_contract(
        required_domains=("tasks",),
        optional_domains=(),
    )

    # Native Echo OS already has a PAM/logind session boundary and the Agent is
    # loopback-only. Reuse Agent's live store and realtime gateway; the two
    # bounded recovery actions still re-enter Agent authority. Native system
    # capability/audit providers can join later without changing the schema.
    app.include_router(
        create_task_projection_router(
            supervisor=getattr(app.state, "task_supervisor", None),
            realtime_gateway=getattr(app.state, "realtime_gateway", None),
            audit=None,
            jwt_secret=None,
        )
    )


__all__ = ["register_app"]
