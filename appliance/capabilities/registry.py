"""In-process registry for versioned Echo OS system capabilities."""

from __future__ import annotations

from collections.abc import Iterable

from appliance.capabilities.model import CapabilityDefinition


class CapabilityRegistry:
    def __init__(self, capabilities: Iterable[CapabilityDefinition] = ()) -> None:
        self._capabilities: dict[str, CapabilityDefinition] = {}
        for capability in capabilities:
            self.register(capability)

    def register(self, capability: CapabilityDefinition) -> None:
        if capability.id in self._capabilities:
            raise ValueError(f"duplicate capability id: {capability.id}")
        self._capabilities[capability.id] = capability

    def get(self, capability_id: str) -> CapabilityDefinition | None:
        return self._capabilities.get(capability_id)

    def list(self, *, provider_id: str | None = None) -> list[CapabilityDefinition]:
        capabilities = self._capabilities.values()
        if provider_id is not None:
            capabilities = (item for item in capabilities if item.provider.id == provider_id)
        return sorted(capabilities, key=lambda item: item.id)

    def __len__(self) -> int:
        return len(self._capabilities)


__all__ = ["CapabilityRegistry"]
