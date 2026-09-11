"""Lightweight password compatibility boundary for native system services.

Unlike :mod:`appliance.agent_api.auth`, this module intentionally avoids the
router, Pydantic configuration and JWT/session stack. Small systemd helpers can
verify an Echo credential without importing the full Agent web runtime.
"""

from runtime.adapters.integrations.local_auth.passwords import (
    hash_password,
    verify_password,
)

__all__ = ["hash_password", "verify_password"]
