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
        required_domains=(
            ("tasks", "storage") if os.environ.get("ECHO_DESKTOP") == "1" else ("tasks",)
        ),
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

    # The native desktop shell may run without the optional Storage sibling.
    # Give its local-database fallback the same authenticated file identity
    # endpoints as the appliance, while keeping the NAS appliance control
    # plane (state lock, Docker, photos, and destructive operations) out of
    # this lightweight extension. Mutating file actions remain unavailable
    # without the appliance's explicit approval/audit services.
    if os.environ.get("ECHO_DESKTOP") == "1":
        from appliance.agent_api.storage import (
            create_storage_proxy_router,
            desktop_file_manager,
        )
        from appliance.files import create_files_router
        from appliance.security import ApplianceAuthenticator

        context = _context
        provider = desktop_file_manager()
        if provider is None:
            raise RuntimeError("desktop file provider is unavailable")
        file_manager = provider.manager
        authenticator = ApplianceAuthenticator(getattr(context, "jwt_secret", None))
        app.state.echo_native_file_manager = file_manager
        app.include_router(
            create_files_router(
                file_manager,
                authenticator=authenticator,
            )
        )

        # A full Agent process already mounts this same-origin proxy from the
        # collaboration router.  The native desktop entrypoint can be a
        # smaller process, so provide the identical Storage contract there
        # only when it has not already been installed.
        if not any(
            getattr(route, "path", "") == "/api/storage/{storage_path:path}"
            for route in getattr(app, "routes", ())
        ):
            app.include_router(
                create_storage_proxy_router(
                    identity_store=getattr(context, "identity_store", None),
                    require_auth=getattr(context, "require_auth", False),
                    jwt_secret=getattr(context, "jwt_secret", None),
                    jwt_issuer=getattr(context, "jwt_issuer", None),
                    jwt_audience=getattr(context, "jwt_audience", None),
                )
            )


__all__ = ["register_app"]
