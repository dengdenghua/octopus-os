"""Shared types for the ``_agents_endpoints`` submodules.

Pure structural split of ``_agents_endpoints.py`` — no logic changes.
This module holds the lightweight bundle used to pass the auth / identity
closures (built once in ``_agents_endpoints._build_endpoints``) into each
handler-register submodule, so the submodules stay free of module-level
globals and avoid circular imports with ``_agents_endpoints``.
"""

from __future__ import annotations

from typing import Any, NamedTuple


class _AuthActions(NamedTuple):
    """Auth / identity helpers built once in ``_build_endpoints``.

    Each field is a closure over the router's injected context
    (``identity_store``, ``require_auth``, JWT params, ``thread_store``).
    Registered endpoints read them off this bundle the same way they used
    to read them off the enclosing factory scope.
    """

    auth: Any
    resolve_identity: Any
    require_admin: Any
    require_task_owner: Any
    require_thread_owner: Any
