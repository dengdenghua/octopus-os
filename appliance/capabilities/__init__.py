"""Echo OS system capability contract and authenticated discovery API."""

from appliance.capabilities.builtins import build_builtin_registry
from appliance.capabilities.model import CapabilityDefinition
from appliance.capabilities.registry import CapabilityRegistry
from appliance.capabilities.router import create_capabilities_router

__all__ = [
    "CapabilityDefinition",
    "CapabilityRegistry",
    "build_builtin_registry",
    "create_capabilities_router",
]
