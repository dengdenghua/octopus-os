"""TopologyForge — apply proposals through the gene-lock gate.

The forge is the only place that mutates ``data/topology_registry.json``.
It accepts a ``Proposal`` from the evolver (or from a UI handler), builds
the candidate topology, runs a lightweight shadow validation, and — if
the gate clears — writes the new topology into the active registry.

Shadow validation in MVP is a *static* check (does the candidate
satisfy ``TeamTopology.__post_init__``? does it reference agents that
exist in the registry?). Live shadow-run validation lives downstream
in tests / staging environments.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from runtime.platform.io import atomic_write_json
from runtime.platform.process.paths import app_paths
from runtime.safety.evolution.subagent_policy import evaluate_agent_policy

from .builtin_topologies import seed_builtin_topologies, upgrade_present_builtin_topologies
from .evolver import Proposal
from .topology import AgentSpec, CoordinationProtocol, TeamTopology

_logger = logging.getLogger("echo.organization.forge")


@dataclass
class PromoteResult:
    proposal_kind: str
    base_fingerprint: str
    accepted: bool = False
    new_topology: TeamTopology | None = None
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


def _registry_path() -> Path:
    try:
        return app_paths().data_dir / "topologies.json"
    except (AttributeError, OSError, TypeError):
        return Path("data") / "topologies.json"


# Cache of the last-loaded registry keyed by resolved path, validated by the
# file's (mtime_ns, size). Reading the topology file on every deep-research
# dispatch (and every forge read) is wasted IO when the file hasn't changed;
# ``save_registry`` writes atomically (new inode / bumped mtime), so a changed
# mtime or size always forces a reload. Tests that mutate a temp file see the
# new content because the mtime/size guard trips.
_REGISTRY_CACHE: dict[Path, tuple[int, int, dict[str, TeamTopology]]] = {}


def _invalidate_registry_cache() -> None:
    _REGISTRY_CACHE.clear()


def load_registry(
    *,
    path: Path | str | None = None,
) -> dict[str, TeamTopology]:
    target = Path(path) if path else _registry_path()
    try:
        stat = target.stat()
        key = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        key = (0, 0)
    cached = _REGISTRY_CACHE.get(target)
    if cached is not None and cached[0] == key[0] and cached[1] == key[1]:
        return cached[2]
    out = _load_registry_uncached(target)
    _REGISTRY_CACHE[target] = (key[0], key[1], out)
    return out


def _load_registry_uncached(target: Path) -> dict[str, TeamTopology]:
    out: dict[str, TeamTopology] = {}
    if target.is_file():
        import json

        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        for fp, body in (raw.get("topologies") or {}).items():
            try:
                t = TeamTopology.from_dict(body)
                out[fp] = t
            except (ValueError, TypeError, KeyError) as exc:
                _logger.warning("skipping malformed topology %s: %s", fp, exc)
    # First-boot UX: an empty (or missing) registry gets the built-in
    # recipes seeded so multi-agent dispatch works out of the box. We
    # only seed on a *fully empty* registry — once the user has added
    # their own topologies, we never re-seed (their omission of a
    # built-in is treated as a deliberate choice).
    if not out:
        added = seed_builtin_topologies(out)
        if added:
            _logger.info(
                "seeded %d built-in topologies into empty registry %s",
                added,
                target,
            )
            try:
                save_registry(out, path=target)
            except OSError as exc:
                _logger.warning(
                    "could not persist seeded registry to %s: %s",
                    target,
                    exc,
                )
    else:
        upgraded = upgrade_present_builtin_topologies(out)
        if upgraded:
            _logger.info("upgraded %d present built-in topologies in %s", upgraded, target)
            try:
                save_registry(out, path=target)
            except OSError as exc:
                _logger.warning("could not persist upgraded registry to %s: %s", target, exc)
    return out


def save_registry(
    registry: dict[str, TeamTopology],
    *,
    path: Path | str | None = None,
) -> None:
    target = Path(path) if path else _registry_path()
    payload = {
        "ts": time.time(),
        "topologies": {fp: t.to_dict() for fp, t in registry.items()},
    }
    atomic_write_json(target, payload)
    # A successful write bumps the file's mtime, but guard against the
    # pathological same-size/same-mtime write by dropping the cached entry.
    _REGISTRY_CACHE.pop(target, None)


# ── Mutation appliers ────────────────────────────────────────


def _apply_swap_agent(
    base: TeamTopology,
    detail: dict[str, Any],
) -> TeamTopology:
    from .topology import Role

    role = Role(detail["role"])
    new_agent_id = str(detail["new_agent"])
    old_spec = base.agents.get(role)
    if old_spec is None:
        raise ValueError(f"base topology has no role {role}")
    new_agents = dict(base.agents)
    new_agents[role] = AgentSpec(
        agent_id=new_agent_id,
        model=old_spec.model,
        temperature=old_spec.temperature,
        system_addendum=old_spec.system_addendum,
    )
    return TeamTopology(
        name=f"{base.name}+swap({role}:{new_agent_id})",
        protocol=base.protocol,
        agents=new_agents,
        routing=base.routing,
        task_bucket=base.task_bucket,
        quality_threshold=base.quality_threshold,
        max_iterations=base.max_iterations,
        metadata={
            **base.metadata,
            "derived_from": base.fingerprint,
            "mutation": "swap_agent",
            "promotion_source": str(detail.get("source") or "topology_proposal"),
            "promotion_detail": dict(detail),
        },
    )


def _apply_switch_protocol(
    base: TeamTopology,
    detail: dict[str, Any],
) -> TeamTopology:
    target = CoordinationProtocol(detail["to"])
    return TeamTopology(
        name=f"{base.name}+protocol({target})",
        protocol=target,
        agents=base.agents,
        routing=base.routing,
        task_bucket=base.task_bucket,
        quality_threshold=base.quality_threshold,
        max_iterations=base.max_iterations,
        metadata={**base.metadata, "derived_from": base.fingerprint, "mutation": "switch_protocol"},
    )


def _apply_adjust_threshold(
    base: TeamTopology,
    detail: dict[str, Any],
) -> TeamTopology:
    new_threshold = float(detail["new_threshold"])
    return TeamTopology(
        name=f"{base.name}+threshold({new_threshold:.2f})",
        protocol=base.protocol,
        agents=base.agents,
        routing=base.routing,
        task_bucket=base.task_bucket,
        quality_threshold=new_threshold,
        max_iterations=base.max_iterations,
        metadata={
            **base.metadata,
            "derived_from": base.fingerprint,
            "mutation": "adjust_threshold",
        },
    )


_APPLIERS = {
    "swap_agent": _apply_swap_agent,
    "switch_protocol": _apply_switch_protocol,
    "adjust_quality_threshold": _apply_adjust_threshold,
}


# ── Validation ───────────────────────────────────────────────


def _shadow_validate(
    candidate: TeamTopology,
    agent_registry: Any | None,
    subagent_policy_path: Path | str | None = None,
) -> tuple[bool, str]:
    """Static checks that don't run any LLM.

    A failure here means the topology is structurally broken — the
    forge refuses to promote it and the proposal is dropped from
    the active set.
    """
    # __post_init__ already enforced protocol-vs-roles invariants
    # at construction. Re-check anyway for stale registries.
    try:
        # Touch the fingerprint to force any lazy validation paths.
        _ = candidate.fingerprint
    except Exception as exc:  # noqa: BLE001
        return False, f"fingerprint error: {exc}"

    if agent_registry is not None:
        missing: list[str] = []
        for role, spec in candidate.agents.items():
            try:
                exists = agent_registry.has(spec.agent_id)
            except Exception:  # noqa: BLE001
                exists = True  # registry can't be queried — let it pass
            if not exists:
                missing.append(f"{role}:{spec.agent_id}")
        if missing:
            return False, f"missing agents in registry: {', '.join(missing)}"

    policy_report = evaluate_agent_policy(
        {str(role): spec.agent_id for role, spec in candidate.agents.items()},
        path=subagent_policy_path,
    )
    retired = policy_report.get("retired") or []
    if retired:
        blocked = [
            f"{item.get('role')}:{item.get('agent_id')}"
            for item in retired
            if isinstance(item, dict)
        ]
        return False, ("retired agents in operator policy: " + ", ".join(blocked))

    return True, ""


# ── Forge ────────────────────────────────────────────────────


class TopologyForge:
    def __init__(
        self,
        *,
        registry_path: Path | str | None = None,
        agent_registry: Any | None = None,
        subagent_policy_path: Path | str | None = None,
    ) -> None:
        self._registry_path = Path(registry_path) if registry_path else None
        self._agent_registry = agent_registry
        self._subagent_policy_path = Path(subagent_policy_path) if subagent_policy_path else None

    def promote(
        self,
        proposal: Proposal,
        *,
        approver: str | None = None,
        bypass_cooldown: bool = False,
    ) -> PromoteResult:
        """Build the candidate, validate it, then run it through
        ``gene_locks.PROMOTE_TOPOLOGY``. On success, write to registry
        and return the new topology.
        """
        registry = load_registry(path=self._registry_path)
        base = registry.get(proposal.base_topology)
        if base is None:
            return PromoteResult(
                proposal_kind=proposal.kind,
                base_fingerprint=proposal.base_topology,
                accepted=False,
                reason="base topology not found in registry",
            )

        applier = _APPLIERS.get(proposal.kind)
        if applier is None:
            return PromoteResult(
                proposal_kind=proposal.kind,
                base_fingerprint=proposal.base_topology,
                accepted=False,
                reason=f"unknown proposal kind: {proposal.kind}",
            )
        try:
            candidate = applier(base, proposal.detail)
        except (ValueError, KeyError, TypeError) as exc:
            return PromoteResult(
                proposal_kind=proposal.kind,
                base_fingerprint=proposal.base_topology,
                accepted=False,
                reason=f"applier failed: {exc}",
            )

        ok, reason = _shadow_validate(
            candidate,
            self._agent_registry,
            self._subagent_policy_path,
        )
        if not ok:
            return PromoteResult(
                proposal_kind=proposal.kind,
                base_fingerprint=proposal.base_topology,
                accepted=False,
                reason=f"shadow validation: {reason}",
            )

        # Gene-lock gate.
        try:
            from runtime.safety.gene_locks import (
                LockViolation,
                MutationKind,
                gate_mutation,
            )

            gate_mutation(
                kind=MutationKind.PROMOTE_TOPOLOGY,
                target=candidate.fingerprint,
                autonomous=approver is None,
                approver=approver,
                bypass_cooldown=bypass_cooldown,
            )
        except LockViolation as lv:
            return PromoteResult(
                proposal_kind=proposal.kind,
                base_fingerprint=proposal.base_topology,
                accepted=False,
                reason=f"gene_locks blocked: {lv}",
            )
        except (ImportError, AttributeError, OSError):  # noqa: BLE001 — gene_locks unavailable; proceed
            pass

        # Commit.
        registry[candidate.fingerprint] = candidate
        try:
            save_registry(registry, path=self._registry_path)
        except OSError as exc:
            return PromoteResult(
                proposal_kind=proposal.kind,
                base_fingerprint=proposal.base_topology,
                accepted=False,
                reason=f"registry write failed: {exc}",
            )
        return PromoteResult(
            proposal_kind=proposal.kind,
            base_fingerprint=proposal.base_topology,
            accepted=True,
            new_topology=candidate,
            reason="promoted",
        )


__all__ = [
    "PromoteResult",
    "TopologyForge",
    "load_registry",
    "save_registry",
]
