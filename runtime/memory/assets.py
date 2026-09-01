"""Unified, governable memory assets.

The runtime historically persisted facts, journals, skills and graph data in
their own stores.  This module provides a small shared contract above those
stores so callers can reason about ownership, visibility and provenance
without migrating the existing data first.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Any

ASSET_TYPES = {
    "conversation",
    "atom",
    "scenario",
    "persona",
    "skill",
    "wiki",
    "code_graph",
    "media",
}
MEMORY_LAYERS = {"L0", "L1", "L2", "L3"}
VISIBILITIES = {"private", "team", "restricted", "agent"}
ASSET_STATUSES = {"draft", "active", "archived", "rejected"}


def _text(value: Any, fallback: str = "") -> str:
    return " ".join(str(value or fallback).split()).strip()


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [part for part in value.split(",")]
    if not isinstance(value, Iterable) or isinstance(value, (bytes, dict)):
        return []
    return list(dict.fromkeys(item for raw in value if (item := _text(raw))))


@dataclass(frozen=True, slots=True)
class MemoryProvenance:
    source_type: str = "manual"
    source_id: str = ""
    source_uri: str = ""
    captured_at: str = ""
    parent_ids: list[str] = field(default_factory=list)
    evidence: str = ""

    @classmethod
    def from_raw(cls, raw: Any, *, fallback_source: str = "manual") -> MemoryProvenance:
        raw = raw if isinstance(raw, dict) else {}
        return cls(
            source_type=_text(raw.get("source_type"), fallback_source),
            source_id=_text(raw.get("source_id")),
            source_uri=_text(raw.get("source_uri")),
            captured_at=_text(raw.get("captured_at")),
            parent_ids=_string_list(raw.get("parent_ids")),
            evidence=_text(raw.get("evidence")),
        )


@dataclass(frozen=True, slots=True)
class MemoryAsset:
    id: str
    asset_type: str
    layer: str
    title: str
    content: str
    owner: str
    visibility: str
    status: str
    version: int
    scope: str
    confidence: float
    created_at: str
    updated_at: str
    team_id: str = ""
    agent_id: str = ""
    project: str = ""
    allowed_users: list[str] = field(default_factory=list)
    allowed_roles: list[str] = field(default_factory=list)
    allowed_agents: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    provenance: MemoryProvenance = field(default_factory=MemoryProvenance)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def fact_to_asset(fact: dict[str, Any]) -> MemoryAsset:
    """Expose a legacy user fact through the unified asset contract."""
    asset_type = _text(fact.get("asset_type"), "atom")
    layer = _text(fact.get("layer"), "L1").upper()
    visibility = _text(fact.get("visibility"), "private").lower()
    status = _text(fact.get("status"), "active").lower()
    content = _text(fact.get("content"))
    created_at = _text(fact.get("createdAt") or fact.get("created_at"))
    try:
        confidence = max(0.0, min(1.0, float(fact.get("confidence", 0.8))))
    except (TypeError, ValueError):
        confidence = 0.8
    try:
        version = max(1, int(fact.get("asset_version", 1)))
    except (TypeError, ValueError):
        version = 1
    return MemoryAsset(
        id=_text(fact.get("id")),
        asset_type=asset_type if asset_type in ASSET_TYPES else "atom",
        layer=layer if layer in MEMORY_LAYERS else "L1",
        title=_text(fact.get("title"), content[:80]),
        content=content,
        owner=_text(fact.get("owner"), "local-user"),
        visibility=visibility if visibility in VISIBILITIES else "private",
        status=status if status in ASSET_STATUSES else "active",
        version=version,
        scope=_text(fact.get("scope"), "global"),
        confidence=confidence,
        created_at=created_at,
        updated_at=_text(fact.get("updatedAt"), created_at),
        team_id=_text(fact.get("team_id")),
        agent_id=_text(fact.get("agent_id")),
        project=_text(fact.get("project")),
        allowed_users=_string_list(fact.get("allowed_users")),
        allowed_roles=_string_list(fact.get("allowed_roles")),
        allowed_agents=_string_list(fact.get("allowed_agents")),
        tags=_string_list(fact.get("tags") or [fact.get("category")]),
        provenance=MemoryProvenance.from_raw(
            fact.get("provenance"), fallback_source=_text(fact.get("source"), "manual")
        ),
    )


def can_read_asset(
    asset: MemoryAsset,
    *,
    actor: str | None = None,
    roles: Iterable[str] = (),
    agent_id: str | None = None,
    team_id: str | None = None,
) -> bool:
    """Apply asset-level visibility after the caller's normal authentication."""
    actor = _text(actor, "local-user")
    role_set = set(_string_list(roles))
    clean_agent = _text(agent_id)
    clean_team = _text(team_id)
    if actor == asset.owner:
        return True
    if asset.visibility == "private":
        return False
    if asset.visibility == "team":
        return bool(clean_team and asset.team_id == clean_team)
    if asset.visibility == "agent":
        return bool(clean_agent and clean_agent in ({asset.agent_id} | set(asset.allowed_agents)))
    return bool(
        actor in asset.allowed_users
        or clean_agent in asset.allowed_agents
        or role_set.intersection(asset.allowed_roles)
    )


def asset_trace(asset: MemoryAsset) -> dict[str, Any]:
    """Return the stable trace envelope used by UI and audit tooling."""
    return {
        "asset_id": asset.id,
        "layer": asset.layer,
        "source": asdict(asset.provenance),
        "parent_ids": list(asset.provenance.parent_ids),
        "trace_complete": bool(
            asset.provenance.source_id or asset.provenance.source_uri or asset.provenance.evidence
        ),
    }


__all__ = [
    "ASSET_STATUSES",
    "ASSET_TYPES",
    "MEMORY_LAYERS",
    "VISIBILITIES",
    "MemoryAsset",
    "MemoryProvenance",
    "asset_trace",
    "can_read_asset",
    "fact_to_asset",
]
