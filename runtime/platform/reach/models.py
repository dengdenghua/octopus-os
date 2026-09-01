from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class ReachItem:
    title: str
    url: str
    snippet: str = ""
    platform: str = "web"
    kind: str = "result"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ChannelHealth:
    platform: str
    available: bool
    backend: str
    detail: str = ""
    requires_login: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
