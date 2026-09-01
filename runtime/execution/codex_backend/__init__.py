"""Codex App Server execution backend primitives.

The package is an anti-corruption boundary around OpenAI's versioned App
Server protocol.  Echo callers use the high-level execution session and
native event projection; transport details never become public gateway state.
"""

from __future__ import annotations

from .account import (
    CodexAccountCapacityError,
    CodexAccountConflict,
    CodexAccountLeaseError,
    CodexAccountService,
    CodexAccountStatus,
    codex_account_home,
    refresh_codex_execution_auth_home,
    resolve_codex_execution_auth_home,
)
from .backend import (
    CodexBackendStateError,
    CodexBackendUnavailable,
    CodexExecutionRequest,
    CodexExecutionSession,
)
from .client import CodexAppServerClient
from .command import resolve_codex_app_server_command
from .model_profile import (
    CodexModelCompatibilityError,
    CodexModelPreference,
    CodexModelPreferenceStore,
    ResolvedCodexExecutionProfile,
    codex_proxy_route_available,
    resolve_codex_execution_profile,
)
from .paths import resolve_codex_state_root
from .responses_proxy import (
    CodexResponsesScope,
    ResponsesProxyError,
    ScopedResponsesProxy,
)
from .security import (
    CodexSecurityError,
    CodexSecurityPolicy,
    CodexSidecarContext,
    CodexSidecarSecurity,
    CodexThreadBinding,
)
from .types import (
    DEFAULT_ENV_ALLOWLIST,
    ApprovalHandler,
    ApprovalRequest,
    AppServerProcess,
    BackpressureError,
    CodexAppServerConfig,
    CodexAppServerError,
    CodexProviderProfile,
    ConfigurationError,
    JsonObject,
    JsonValue,
    MessageTooLargeError,
    Notification,
    ProcessFactory,
    ProcessLaunch,
    ProtocolError,
    RemoteError,
    RequestTimeoutError,
    TransportClosedError,
)

__all__ = [
    "ApprovalHandler",
    "ApprovalRequest",
    "AppServerProcess",
    "BackpressureError",
    "CodexAccountCapacityError",
    "CodexAccountConflict",
    "CodexAccountLeaseError",
    "CodexAccountService",
    "CodexAccountStatus",
    "CodexBackendStateError",
    "CodexBackendUnavailable",
    "CodexAppServerClient",
    "CodexAppServerConfig",
    "CodexAppServerError",
    "CodexExecutionRequest",
    "CodexExecutionSession",
    "CodexModelCompatibilityError",
    "CodexModelPreference",
    "CodexModelPreferenceStore",
    "CodexProviderProfile",
    "CodexResponsesScope",
    "CodexSecurityError",
    "CodexSecurityPolicy",
    "CodexSidecarContext",
    "CodexSidecarSecurity",
    "CodexThreadBinding",
    "ConfigurationError",
    "DEFAULT_ENV_ALLOWLIST",
    "JsonObject",
    "JsonValue",
    "MessageTooLargeError",
    "Notification",
    "ProcessFactory",
    "ProcessLaunch",
    "ProtocolError",
    "RemoteError",
    "RequestTimeoutError",
    "ResolvedCodexExecutionProfile",
    "ResponsesProxyError",
    "ScopedResponsesProxy",
    "TransportClosedError",
    "codex_account_home",
    "codex_proxy_route_available",
    "resolve_codex_execution_auth_home",
    "refresh_codex_execution_auth_home",
    "resolve_codex_app_server_command",
    "resolve_codex_execution_profile",
    "resolve_codex_state_root",
]
