from __future__ import annotations

import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from runtime.platform.models import now_utc


@dataclass
class AgentGroup:
    group_id: str
    display_name: str = ""
    description: str = ""
    members: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=now_utc)
    updated_at: datetime = field(default_factory=now_utc)


class AgentGroupNotFound(KeyError):
    pass


class AgentGroupRegistry:
    def __init__(self) -> None:
        self._groups: dict[str, AgentGroup] = {}
        self._lock = threading.RLock()

    # ─── CRUD ────────────────────────────────────────

    def create(self, group: AgentGroup) -> None:
        if not group.group_id:
            raise ValueError("group_id must be non-empty")
        with self._lock:
            if group.group_id in self._groups:
                raise ValueError(f"duplicate group_id: {group.group_id!r}")
            members = list(dict.fromkeys(group.members))
            self._groups[group.group_id] = AgentGroup(
                group_id=group.group_id,
                display_name=group.display_name,
                description=group.description,
                members=members,
                created_at=group.created_at,
                updated_at=group.updated_at,
            )

    def update(
        self,
        group_id: str,
        *,
        display_name: str | None = None,
        description: str | None = None,
    ) -> AgentGroup:
        with self._lock:
            if group_id not in self._groups:
                raise AgentGroupNotFound(group_id)
            g = self._groups[group_id]
            new = AgentGroup(
                group_id=g.group_id,
                display_name=display_name if display_name is not None else g.display_name,
                description=description if description is not None else g.description,
                members=list(g.members),
                created_at=g.created_at,
                updated_at=now_utc(),
            )
            self._groups[group_id] = new
            return new

    def remove(self, group_id: str) -> bool:
        with self._lock:
            return self._groups.pop(group_id, None) is not None

    def get(self, group_id: str) -> AgentGroup:
        with self._lock:
            if group_id not in self._groups:
                raise AgentGroupNotFound(group_id)
            return self._groups[group_id]

    def has(self, group_id: str) -> bool:
        with self._lock:
            return group_id in self._groups

    def list_all(self) -> list[AgentGroup]:
        with self._lock:
            return sorted(self._groups.values(), key=lambda g: g.group_id)

    def __len__(self) -> int:
        with self._lock:
            return len(self._groups)

    def __iter__(self):
        with self._lock:
            return iter(list(self._groups.values()))

    def add_member(self, group_id: str, agent_id: str) -> bool:
        if not agent_id:
            raise ValueError("agent_id must be non-empty")
        with self._lock:
            if group_id not in self._groups:
                raise AgentGroupNotFound(group_id)
            g = self._groups[group_id]
            if agent_id in g.members:
                return False
            new_members = [*g.members, agent_id]
            self._groups[group_id] = AgentGroup(
                group_id=g.group_id,
                display_name=g.display_name,
                description=g.description,
                members=new_members,
                created_at=g.created_at,
                updated_at=now_utc(),
            )
            return True

    def remove_member(self, group_id: str, agent_id: str) -> bool:
        with self._lock:
            if group_id not in self._groups:
                raise AgentGroupNotFound(group_id)
            g = self._groups[group_id]
            if agent_id not in g.members:
                return False
            new_members = [m for m in g.members if m != agent_id]
            self._groups[group_id] = AgentGroup(
                group_id=g.group_id,
                display_name=g.display_name,
                description=g.description,
                members=new_members,
                created_at=g.created_at,
                updated_at=now_utc(),
            )
            return True

    def groups_for_agent(self, agent_id: str) -> list[str]:
        with self._lock:
            return sorted([gid for gid, g in self._groups.items() if agent_id in g.members])


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


def effective_groups_for_agent(
    *,
    agent_id: str,
    static_groups: Iterable[str] = (),
    registry: AgentGroupRegistry | None = None,
) -> list[str]:
    merged: set[str] = set(static_groups)
    if registry is not None:
        merged.update(registry.groups_for_agent(agent_id))
    return sorted(merged)
