"""Side-effect-free readiness checks for the Codex execution engine.

The model-profile endpoint and the realtime router need to distinguish a
compatible model from an executable turn. This probe only reads feature flags,
resolves the pinned executable and asks the account boundary for an existing
auth home; it never starts App Server, refreshes credentials, or makes a model
request.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from runtime.platform.runtime_policy.feature_flags import resolution

from .command import resolve_codex_app_server_command
from .model_profile import ResolvedCodexExecutionProfile
from .types import ConfigurationError


@dataclass(frozen=True, slots=True)
class CodexReadiness:
    """A stable, secret-free explanation of whether a Codex turn may start."""

    available: bool
    reason: str | None = None


def inspect_codex_readiness(
    profile: ResolvedCodexExecutionProfile,
    *,
    auth_source: Callable[[], Path | None],
    tools_available: bool = True,
) -> CodexReadiness:
    """Check local prerequisites without provisioning or contacting a provider."""

    # Keep the feature gate and execution path on the same policy source.
    # Importing lazily avoids making the model-profile read depend on the
    # role-runner's heavier process setup.
    from .role_runner import require_codex_backend_enabled
    from .security import CodexSecurityError

    value, source = resolution("execution.codex_app_server")
    if source not in (None, "default") and value is not True:
        return CodexReadiness(False, "disabled")
    try:
        require_codex_backend_enabled()
    except CodexSecurityError:
        return CodexReadiness(False, "disabled")
    try:
        resolve_codex_app_server_command()
    except ConfigurationError:
        return CodexReadiness(False, "executable_unavailable")
    if not profile.compatible:
        return CodexReadiness(False, "model_incompatible")
    if not tools_available:
        return CodexReadiness(False, "tools_unavailable")
    if not profile.proxy_required:
        try:
            if auth_source() is None:
                return CodexReadiness(False, "account_required")
        except (CodexSecurityError, ConfigurationError, OSError, ValueError):
            return CodexReadiness(False, "account_unavailable")
    return CodexReadiness(True)


__all__ = ["CodexReadiness", "inspect_codex_readiness"]
