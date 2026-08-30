"""统一能力包(Capability)体系:连接器 + Codex 插件归一。"""

from pathlib import Path

from runtime.platform.capabilities.capability_registry import (
    CapabilityRegistry,
    default_capability_registry,
)
from runtime.platform.capabilities.service import (
    CAPABILITY_SERVICE_SCHEMA,
    CapabilityLifecycleService,
    CapabilityPrincipal,
    CapabilityServiceError,
)
from runtime.platform.runtime_policy import capabilities as _runtime_policy

# ``runtime.platform.capabilities`` was historically the user-facing browser /
# desktop automation policy module.  It is now also the unified capability
# package, so keep the old load/save surface here instead of letting Python's
# package import precedence silently break settings and embedders.
Capabilities = _runtime_policy.Capabilities


def _store_path() -> Path:
    return _runtime_policy._store_path()


def load() -> Capabilities:
    return _runtime_policy._load_path(_store_path())


def save(caps: Capabilities) -> None:
    _runtime_policy._save_path(_store_path(), caps)


__all__ = [
    "CAPABILITY_SERVICE_SCHEMA",
    "CapabilityLifecycleService",
    "CapabilityPrincipal",
    "CapabilityRegistry",
    "CapabilityServiceError",
    "Capabilities",
    "default_capability_registry",
    "load",
    "save",
]
