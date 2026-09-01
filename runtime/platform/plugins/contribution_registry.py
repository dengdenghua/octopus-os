"""Process-local registry for first-class Echo plugin contributions.

Specialised runtime registries remain authoritative for executable tools,
prompts, hooks, and jobs. This registry covers descriptor-oriented seams whose
consumers are composed at a higher layer: agents, workflows, model providers,
UI surfaces, renderers, commands, settings schemas, and future capability
kinds. Every row has an owner and an identity-aware disposer so a plugin
generation can be rolled back or unloaded without ghost entries.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ContributionRecord:
    kind: str
    name: str
    owner: str
    value: Any
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_public(self) -> dict[str, Any]:
        """Return metadata safe for lifecycle and marketplace APIs."""

        return {
            "kind": self.kind,
            "name": self.name,
            "owner": self.owner,
            "description": self.description,
            "metadata": dict(self.metadata),
        }


class ContributionRegistry:
    """Thread-safe registry keyed by ``(kind, name)``."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], ContributionRecord] = {}
        self._lock = threading.RLock()

    def register(
        self,
        *,
        kind: str,
        name: str,
        owner: str,
        value: Any,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        kind = kind.strip()
        name = name.strip()
        owner = owner.strip()
        if not kind or not name or not owner:
            raise ValueError("contribution kind, name, and owner must be non-empty")
        record = ContributionRecord(
            kind=kind,
            name=name,
            owner=owner,
            value=value,
            description=description,
            metadata=dict(metadata or {}),
        )
        key = (kind, name)
        with self._lock:
            if key in self._records:
                existing = self._records[key]
                raise ValueError(
                    f"contribution {kind}:{name} is already registered by {existing.owner}"
                )
            self._records[key] = record

        def dispose() -> None:
            with self._lock:
                if self._records.get(key) is record:
                    self._records.pop(key, None)

        return dispose

    def get(self, kind: str, name: str) -> ContributionRecord | None:
        with self._lock:
            return self._records.get((kind, name))

    def list(
        self,
        *,
        kind: str | None = None,
        owner: str | None = None,
    ) -> list[ContributionRecord]:
        with self._lock:
            rows = list(self._records.values())
        if kind is not None:
            rows = [row for row in rows if row.kind == kind]
        if owner is not None:
            rows = [row for row in rows if row.owner == owner]
        return sorted(rows, key=lambda row: (row.kind, row.name))

    def unregister_owner(self, owner: str) -> int:
        with self._lock:
            keys = [key for key, row in self._records.items() if row.owner == owner]
            for key in keys:
                self._records.pop(key, None)
        return len(keys)


__all__ = ["ContributionRecord", "ContributionRegistry"]
