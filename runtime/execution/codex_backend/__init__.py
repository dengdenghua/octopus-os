"""Codex App Server execution backend primitives.

The package is an anti-corruption boundary around OpenAI's versioned App
Server protocol.  Echo callers use the high-level execution session and
native event projection; transport details never become public gateway state."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .account import CodexAccountCapacityError as CodexAccountCapacityError
    from .account import CodexAccountConflict as CodexAccountConflict
    from .account import CodexAccountLeaseError as CodexAccountLeaseError
    from .account import CodexAccountService as CodexAccountService
    from .account import CodexAccountStatus as CodexAccountStatus
    from .account import codex_account_home as codex_account_home
    from .account import refresh_codex_execution_auth_home as refresh_codex_execution_auth_home
    from .account import resolve_codex_execution_auth_home as resolve_codex_execution_auth_home
    from .backend import CodexBackendStateError as CodexBackendStateError
    from .backend import CodexBackendUnavailable as CodexBackendUnavailable
    from .backend import CodexExecutionRequest as CodexExecutionRequest
    from .backend import CodexExecutionSession as CodexExecutionSession
    from .client import CodexAppServerClient as CodexAppServerClient
    from .command import resolve_codex_app_server_command as resolve_codex_app_server_command
    from .model_profile import CodexModelCompatibilityError as CodexModelCompatibilityError
    from .model_profile import CodexModelPreference as CodexModelPreference
    from .model_profile import CodexModelPreferenceStore as CodexModelPreferenceStore
    from .model_profile import ResolvedCodexExecutionProfile as ResolvedCodexExecutionProfile
    from .model_profile import codex_proxy_route_available as codex_proxy_route_available
    from .model_profile import resolve_codex_execution_profile as resolve_codex_execution_profile
    from .paths import resolve_codex_state_root as resolve_codex_state_root
    from .readiness import CodexReadiness as CodexReadiness
    from .readiness import inspect_codex_readiness as inspect_codex_readiness
    from .responses_proxy import CodexResponsesScope as CodexResponsesScope
    from .responses_proxy import ResponsesProxyError as ResponsesProxyError
    from .responses_proxy import ScopedResponsesProxy as ScopedResponsesProxy
    from .security import CodexSecurityError as CodexSecurityError
    from .security import CodexSecurityPolicy as CodexSecurityPolicy
    from .security import CodexSidecarContext as CodexSidecarContext
    from .security import CodexSidecarSecurity as CodexSidecarSecurity
    from .security import CodexThreadBinding as CodexThreadBinding
    from .types import DEFAULT_ENV_ALLOWLIST as DEFAULT_ENV_ALLOWLIST
    from .types import ApprovalHandler as ApprovalHandler
    from .types import ApprovalRequest as ApprovalRequest
    from .types import AppServerProcess as AppServerProcess
    from .types import BackpressureError as BackpressureError
    from .types import CodexAppServerConfig as CodexAppServerConfig
    from .types import CodexAppServerError as CodexAppServerError
    from .types import CodexProviderProfile as CodexProviderProfile
    from .types import ConfigurationError as ConfigurationError
    from .types import JsonObject as JsonObject
    from .types import JsonValue as JsonValue
    from .types import MessageTooLargeError as MessageTooLargeError
    from .types import Notification as Notification
    from .types import ProcessFactory as ProcessFactory
    from .types import ProcessLaunch as ProcessLaunch
    from .types import ProtocolError as ProtocolError
    from .types import RemoteError as RemoteError
    from .types import RequestTimeoutError as RequestTimeoutError
    from .types import TransportClosedError as TransportClosedError

_EXPORT_MODULES = {
    "ApprovalHandler": ".types",
    "ApprovalRequest": ".types",
    "AppServerProcess": ".types",
    "BackpressureError": ".types",
    "CodexAccountCapacityError": ".account",
    "CodexAccountConflict": ".account",
    "CodexAccountLeaseError": ".account",
    "CodexAccountService": ".account",
    "CodexAccountStatus": ".account",
    "CodexBackendStateError": ".backend",
    "CodexBackendUnavailable": ".backend",
    "CodexAppServerClient": ".client",
    "CodexAppServerConfig": ".types",
    "CodexAppServerError": ".types",
    "CodexExecutionRequest": ".backend",
    "CodexExecutionSession": ".backend",
    "CodexReadiness": ".readiness",
    "CodexModelCompatibilityError": ".model_profile",
    "CodexModelPreference": ".model_profile",
    "CodexModelPreferenceStore": ".model_profile",
    "CodexProviderProfile": ".types",
    "CodexResponsesScope": ".responses_proxy",
    "CodexSecurityError": ".security",
    "CodexSecurityPolicy": ".security",
    "CodexSidecarContext": ".security",
    "CodexSidecarSecurity": ".security",
    "CodexThreadBinding": ".security",
    "ConfigurationError": ".types",
    "DEFAULT_ENV_ALLOWLIST": ".types",
    "JsonObject": ".types",
    "JsonValue": ".types",
    "MessageTooLargeError": ".types",
    "Notification": ".types",
    "ProcessFactory": ".types",
    "ProcessLaunch": ".types",
    "ProtocolError": ".types",
    "RemoteError": ".types",
    "RequestTimeoutError": ".types",
    "ResolvedCodexExecutionProfile": ".model_profile",
    "ResponsesProxyError": ".responses_proxy",
    "ScopedResponsesProxy": ".responses_proxy",
    "TransportClosedError": ".types",
    "codex_account_home": ".account",
    "codex_proxy_route_available": ".model_profile",
    "resolve_codex_execution_auth_home": ".account",
    "refresh_codex_execution_auth_home": ".account",
    "resolve_codex_app_server_command": ".command",
    "resolve_codex_execution_profile": ".model_profile",
    "resolve_codex_state_root": ".paths",
    "inspect_codex_readiness": ".readiness",
}

__all__ = list(_EXPORT_MODULES)


def __getattr__(name: str) -> Any:
    module = _EXPORT_MODULES.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
