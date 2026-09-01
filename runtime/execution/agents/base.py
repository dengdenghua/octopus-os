from __future__ import annotations

import threading
from collections.abc import Iterable
from typing import Any

from runtime.execution.arms.base import ArmPool, Worker


class Agent:
    def __init__(
        self,
        *,
        agent_id: str,
        display_name: str,
        description: str,
        soul: str,
        arms: ArmPool,
        icon: str = "",
        model: str | None = None,
        extra_affinity: list[str] | None = None,
        groups: list[str] | None = None,
        extra_skills: list[str] | None = None,
        capabilities: dict[str, Any] | None = None,
        budget: dict[str, Any] | None = None,
    ) -> None:
        if not agent_id:
            raise ValueError("agent_id must be non-empty")
        if len(arms) < 1:
            raise ValueError(
                f"agent {agent_id!r} must have at least 1 arm",
            )
        self.agent_id: str = agent_id
        self.display_name: str = display_name or agent_id
        self.description: str = description
        self.soul: str = soul
        self.arms: ArmPool = arms
        self.icon: str = icon
        self.model: str | None = model
        self.extra_affinity: list[str] = list(extra_affinity or [])
        self.groups: list[str] = list(groups or [])
        self.extra_skills: list[str] = list(extra_skills or [])
        # Capability flags · read by feature gates. Free-form so adding
        # one server-side needs no typed migration. (The former
        # ``code_mode_unlock`` flag was removed — code mode is available
        # to every agent by default; tool/permission scoping lives in the
        # skills & permissions system.)
        self.capabilities: dict[str, Any] = dict(capabilities or {})
        self.budget: dict[str, Any] = dict(budget or {})

    def affinity(self) -> list[str]:
        agg: set[str] = set(self.extra_affinity)
        for arm in self.arms:
            agg.update(arm.affinity)
        return sorted(agg)

    def allowed_skill_union(self) -> list[str]:
        from runtime.execution.misc.skill_policy import resolve_agent_skill_policy

        return resolve_agent_skill_policy(self).as_list()

    def skill_policy(self):
        from runtime.execution.misc.skill_policy import resolve_agent_skill_policy

        return resolve_agent_skill_policy(self)

    def can_use(self, skill_ref: Any) -> bool:
        return any(arm.can_use(skill_ref) for arm in self.arms)

    def pick_arm_for(self, assignment: Any) -> Worker | None:
        return self.arms.pick_for(assignment)

    def pick_arm_for_intent(self, intent: Any) -> Worker | None:
        return self.arms.pick_for_intent(intent)

    def __iter__(self):
        return iter(self.arms)

    def __len__(self) -> int:
        return len(self.arms)

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            f"Agent(agent_id={self.agent_id!r}, "
            f"arms={len(self.arms)}, affinity={self.affinity()!r})"
        )


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


class AgentNotFound(KeyError):
    pass


class AgentRegistry:
    def __init__(self, *, event_bus: Any = None) -> None:
        self._by_id: dict[str, Agent] = {}
        self._lock = threading.RLock()
        # Read-mostly HTTP/UI consumers must not wait behind a long hot-reload
        # transaction. Mutations publish a new immutable tuple atomically;
        # readers can keep showing the last complete roster while a watcher is
        # rebuilding agents in the background.
        self._snapshot: tuple[Agent, ...] = ()
        self._event_bus = event_bus

    def register(self, agent: Agent) -> None:
        with self._lock:
            if agent.agent_id in self._by_id:
                raise ValueError(f"duplicate agent_id: {agent.agent_id!r}")
            self._by_id[agent.agent_id] = agent
            self._snapshot = tuple(self._by_id.values())

        if self._event_bus is not None:
            try:
                from runtime.core.nerves import AgentAdded

                self._event_bus.publish(
                    AgentAdded(
                        agent_id=agent.agent_id,
                        display_name=agent.display_name,
                    )
                )
            except Exception:  # noqa: BLE001 — bus is best-effort; never break register
                pass

    def register_all(self, agents: Iterable[Agent]) -> int:
        count = 0
        for a in agents:
            self.register(a)
            count += 1
        return count

    def remove(self, agent_id: str) -> bool:
        with self._lock:
            existed = self._by_id.pop(agent_id, None) is not None
            if existed:
                self._snapshot = tuple(self._by_id.values())
        if existed and self._event_bus is not None:
            try:
                from runtime.core.nerves import AgentRemoved

                self._event_bus.publish(AgentRemoved(agent_id=agent_id))
            except (ImportError, TypeError, AttributeError, OSError):  # noqa: BLE001
                pass
        return existed

    def replace(self, agent: Agent) -> Agent | None:
        """Atomically swap an agent in the registry.

        Returns the previous agent (or None if this is actually a first
        register). Used by hot-reload so a `POST /api/agents/<id>/reload`
        replaces the in-memory Agent with a freshly-built one from disk
        without a restart. In-flight turns holding the old Agent
        reference keep using the old soul until they finish — by Python
        object identity, so there's no torn state.
        """
        prev: Agent | None
        with self._lock:
            prev = self._by_id.get(agent.agent_id)
            self._by_id[agent.agent_id] = agent
            self._snapshot = tuple(self._by_id.values())
        if self._event_bus is not None:
            try:
                from runtime.core.nerves import AgentAdded

                self._event_bus.publish(
                    AgentAdded(
                        agent_id=agent.agent_id,
                        display_name=agent.display_name,
                    )
                )
            except (TypeError, ValueError, AttributeError):  # noqa: BLE001
                pass
        return prev

    def get(self, agent_id: str) -> Agent:
        try:
            return self._by_id[agent_id]
        except KeyError as e:
            raise AgentNotFound(f"no agent named {agent_id!r}") from e

    def has(self, agent_id: str) -> bool:
        return agent_id in self._by_id

    def all_ids(self) -> list[str]:
        with self._lock:
            return sorted(self._by_id.keys())

    def all_agents(self) -> list[Agent]:
        return list(self._snapshot)

    def __len__(self) -> int:
        with self._lock:
            return len(self._by_id)

    def __iter__(self):
        with self._lock:
            return iter(list(self._by_id.values()))

    def pick_for_intent(self, intent: Any) -> Agent | None:
        goal = ""
        itype = ""
        for attr in ("normalized_goal", "raw"):
            v = getattr(intent, attr, None)
            if isinstance(v, str) and v:
                goal = v.lower()
                break
        t = getattr(intent, "intent_type", None)
        if isinstance(t, str):
            itype = t.lower()
        haystack = f"{goal} {itype}".lower()

        if not haystack.strip():
            return None

        with self._lock:
            agents_in_order = list(self._by_id.values())

        scored: list[tuple[int, int, int, Agent]] = []
        for idx, agent in enumerate(agents_in_order):
            tags = agent.affinity()
            score = sum(1 for tag in tags if tag and tag.lower() in haystack)
            if score > 0:
                scored.append((score, len(tags), -idx, agent))

        if not scored:
            return None
        scored.sort(key=lambda t: (t[0], t[1], t[2]), reverse=True)
        return scored[0][3]
