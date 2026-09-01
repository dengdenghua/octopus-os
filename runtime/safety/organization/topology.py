"""Data models for team topologies.

A ``TeamTopology`` is a frozen recipe describing:
  * which roles are present (planner / generator / evaluator / ...)
  * which agent runs each role
  * how the roles coordinate (sequential vs. evaluator_optimizer)
  * any routing rules between roles

The recipe is deterministic: given the same task and the same
topology, the runner takes the same set of role activations. The
*content* of each activation depends on the underlying LLM, which is
why we still need pass@k evaluation to compare topologies fairly.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class Role(StrEnum):
    """The canonical role catalogue.

    Keep this list short and well-known so reflection / evolution
    code can pattern-match on it without an exhaustive switch.
    Free-form labels live in ``RoutingRule.label`` instead.
    """

    PLANNER = "planner"
    GENERATOR = "generator"
    EVALUATOR = "evaluator"
    CRITIC = "critic"
    RESEARCHER = "researcher"
    SYNTHESIZER = "synthesizer"


class CoordinationProtocol(StrEnum):
    """How a turn flows through the roles.

    MVP supports two protocols. Adding ``parallel`` / ``debate`` is a
    matter of new branches in ``TeamRunner`` and new validators here.
    """

    SEQUENTIAL = "sequential"
    EVALUATOR_OPTIMIZER = "evaluator_optimizer"
    PARALLEL = "parallel"


@dataclass(frozen=True)
class AgentSpec:
    """The minimum a topology needs to point at an agent.

    We don't embed the full ``Agent`` here because agents are mutable
    (their soul can be edited) and we want topologies to keep being
    valid across edits. The ``agent_id`` resolves through whatever
    ``AgentRegistry`` is wired into the stack at run-time.
    """

    agent_id: str
    # Optional per-role overrides — these take precedence over the
    # agent's defaults but only inside this topology slot.
    model: str | None = None
    temperature: float | None = None
    system_addendum: str | None = None
    # Number of concurrent copies of this role to run under the
    # ``parallel`` protocol. ``1`` means "run once, the default".
    # A value > 1 fans the role out into that many parallel sub-agent
    # invocations, each receiving the same prior context plus its own
    # replica index (see ``team_runner._run_parallel``).
    parallel_replicas: int = 1

    def __post_init__(self) -> None:
        if not self.agent_id:
            raise ValueError("AgentSpec.agent_id is required")
        if self.parallel_replicas < 1:
            raise ValueError("AgentSpec.parallel_replicas must be ≥ 1")


@dataclass(frozen=True)
class RoutingRule:
    """Conditional handoff between roles.

    ``label`` is a free-form classifier (e.g. ``"low_score"``,
    ``"needs_research"``) that the team_runner emits when the source
    role completes. When the label matches, the conversation is
    redirected to ``next_role`` instead of the default protocol order.

    Routing rules are *advisory* — the protocol still owns the
    main flow. They're how a topology says e.g. "if the evaluator
    flags a fact issue, loop back through the researcher".
    """

    from_role: Role
    label: str
    next_role: Role
    max_loops: int = 3  # safety net against infinite re-routes


@dataclass(frozen=True)
class TeamTopology:
    """A complete, hashable team recipe."""

    name: str
    protocol: CoordinationProtocol
    agents: dict[Role, AgentSpec]
    routing: tuple[RoutingRule, ...] = field(default_factory=tuple)
    # Free-form tag matching the task buckets the evolver groups by.
    # E.g. ``"code-refactor"`` or ``"research-report"``. The evolver
    # only compares performance *within* a bucket — apples to apples.
    task_bucket: str = "default"
    # Quality threshold used by ``evaluator_optimizer``. When the
    # evaluator scores below this, the generator retries.
    quality_threshold: float = 0.6
    # Per-turn iteration cap inside the runner. Independent of
    # ``react_loop.max_iterations`` which sits below this layer.
    max_iterations: int = 3
    # Free-form metadata for telemetry / UI rendering.
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("TeamTopology.name is required")
        if self.protocol == CoordinationProtocol.SEQUENTIAL:
            if Role.PLANNER not in self.agents and Role.GENERATOR not in self.agents:
                raise ValueError(
                    "sequential protocol requires at least one of planner or generator",
                )
        elif self.protocol == CoordinationProtocol.EVALUATOR_OPTIMIZER:
            if Role.GENERATOR not in self.agents:
                raise ValueError(
                    "evaluator_optimizer protocol requires a generator",
                )
            if Role.EVALUATOR not in self.agents:
                raise ValueError(
                    "evaluator_optimizer protocol requires an evaluator",
                )
        elif self.protocol == CoordinationProtocol.PARALLEL:
            if Role.PLANNER not in self.agents:
                raise ValueError(
                    "parallel protocol requires a planner",
                )
            if Role.SYNTHESIZER not in self.agents:
                raise ValueError(
                    "parallel protocol requires a synthesizer to converge",
                )
        if not 0.0 <= self.quality_threshold <= 1.0:
            raise ValueError("quality_threshold must be in [0, 1]")
        if self.max_iterations < 1:
            raise ValueError("max_iterations must be ≥ 1")

    @property
    def fingerprint(self) -> str:
        """Stable hash of the structural content.

        Two topologies with the same fingerprint are interchangeable
        as far as the evolver is concerned. Used to dedup proposals
        and to key performance buckets.
        """
        payload = {
            "protocol": str(self.protocol),
            "agents": {
                str(role): asdict(spec)
                for role, spec in sorted(
                    self.agents.items(),
                    key=lambda kv: str(kv[0]),
                )
            },
            "routing": [
                {
                    "from": str(r.from_role),
                    "label": r.label,
                    "next": str(r.next_role),
                }
                for r in sorted(
                    self.routing,
                    key=lambda r: (str(r.from_role), r.label),
                )
            ],
            "quality_threshold": self.quality_threshold,
            "max_iterations": self.max_iterations,
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "protocol": str(self.protocol),
            "task_bucket": self.task_bucket,
            "quality_threshold": self.quality_threshold,
            "max_iterations": self.max_iterations,
            "agents": {str(role): asdict(spec) for role, spec in self.agents.items()},
            "routing": [
                {
                    "from_role": str(r.from_role),
                    "label": r.label,
                    "next_role": str(r.next_role),
                    "max_loops": r.max_loops,
                }
                for r in self.routing
            ],
            "metadata": dict(self.metadata),
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TeamTopology:
        agents = {
            Role(role_key): AgentSpec(**spec)
            for role_key, spec in (data.get("agents") or {}).items()
        }
        routing = tuple(
            RoutingRule(
                from_role=Role(r["from_role"]),
                label=r["label"],
                next_role=Role(r["next_role"]),
                max_loops=int(r.get("max_loops", 3)),
            )
            for r in (data.get("routing") or [])
        )
        return cls(
            name=str(data["name"]),
            protocol=CoordinationProtocol(data["protocol"]),
            agents=agents,
            routing=routing,
            task_bucket=str(data.get("task_bucket", "default")),
            quality_threshold=float(data.get("quality_threshold", 0.6)),
            max_iterations=int(data.get("max_iterations", 3)),
            metadata=dict(data.get("metadata", {})),
        )


__all__ = [
    "AgentSpec",
    "CoordinationProtocol",
    "Role",
    "RoutingRule",
    "TeamTopology",
]
