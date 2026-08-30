"""Typed evolution candidates and append-only lineage persistence."""

from __future__ import annotations

import builtins
import hashlib
import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from runtime.platform.io import TransactionalFileError, path_transaction
from runtime.safety.auth.scope import TenantScope

CANDIDATE_SCHEMA = "echo.evolution.candidate.v1"
_LOCK = threading.RLock()


class CandidateRegistryError(RuntimeError):
    """Candidate lineage storage is unavailable, corrupt, or not durable."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_fd = os.open(path, flags)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


class GeneType(StrEnum):
    PROMPT = "prompt"
    SKILL = "skill"
    ROUTING = "routing"
    WORKFLOW = "workflow"
    ROLE = "role"
    POLICY = "policy"


class CandidateStatus(StrEnum):
    PROPOSED = "proposed"
    VALIDATED = "validated"
    SHADOW = "shadow"
    CANARY = "canary"
    PROMOTED = "promoted"
    REJECTED = "rejected"
    ROLLED_BACK = "rolled_back"


_TRANSITIONS: dict[CandidateStatus, frozenset[CandidateStatus]] = {
    CandidateStatus.PROPOSED: frozenset({CandidateStatus.VALIDATED, CandidateStatus.REJECTED}),
    CandidateStatus.VALIDATED: frozenset({CandidateStatus.SHADOW, CandidateStatus.REJECTED}),
    CandidateStatus.SHADOW: frozenset({CandidateStatus.CANARY, CandidateStatus.REJECTED}),
    CandidateStatus.CANARY: frozenset(
        {CandidateStatus.PROMOTED, CandidateStatus.REJECTED, CandidateStatus.ROLLED_BACK}
    ),
    CandidateStatus.PROMOTED: frozenset({CandidateStatus.ROLLED_BACK}),
    CandidateStatus.REJECTED: frozenset(),
    CandidateStatus.ROLLED_BACK: frozenset(),
}


@dataclass
class EvolutionCandidate:
    candidate_id: str
    gene_type: GeneType
    scope: str
    patch: dict[str, Any]
    proposer: str
    tenant_id: str | None = None
    owner_actor_id: str | None = None
    status: CandidateStatus = CandidateStatus.PROPOSED
    parent_id: str | None = None
    lineage_id: str = ""
    role_id: str = "general"
    task_domain: str = "general"
    environment_digest: str = ""
    risk_level: str = "medium"
    source_failures: list[str] = field(default_factory=list)
    experiment_ids: list[str] = field(default_factory=list)
    hard_gate_results: dict[str, bool] = field(default_factory=dict)
    metric_vector: dict[str, float] = field(default_factory=dict)
    rollback_target: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    schema: str = CANDIDATE_SCHEMA

    @property
    def hard_gate_passed(self) -> bool:
        return bool(self.hard_gate_results) and all(self.hard_gate_results.values())

    @property
    def deployment_key(self) -> str:
        return ":".join(
            (
                self.candidate_id,
                self.role_id or "general",
                self.task_domain or "general",
                self.environment_digest or "default",
            )
        )

    def to_wire(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "gene_type": self.gene_type.value,
            "status": self.status.value,
            "hard_gate_passed": self.hard_gate_passed,
            "deployment_key": self.deployment_key,
        }

    @classmethod
    def from_wire(cls, value: dict[str, Any]) -> EvolutionCandidate:
        return cls(
            candidate_id=str(value.get("candidate_id") or "").strip(),
            gene_type=GeneType(str(value.get("gene_type") or GeneType.PROMPT.value)),
            scope=str(value.get("scope") or "").strip(),
            patch=dict(value.get("patch") or {}),
            proposer=str(value.get("proposer") or "system"),
            tenant_id=str(value.get("tenant_id") or "").strip() or None,
            owner_actor_id=str(value.get("owner_actor_id") or "").strip() or None,
            status=CandidateStatus(str(value.get("status") or CandidateStatus.PROPOSED.value)),
            parent_id=str(value.get("parent_id") or "").strip() or None,
            lineage_id=str(value.get("lineage_id") or "").strip(),
            role_id=str(value.get("role_id") or "general"),
            task_domain=str(value.get("task_domain") or "general"),
            environment_digest=str(value.get("environment_digest") or ""),
            risk_level=str(value.get("risk_level") or "medium"),
            source_failures=[str(item) for item in value.get("source_failures") or []],
            experiment_ids=[str(item) for item in value.get("experiment_ids") or []],
            hard_gate_results={
                str(key): bool(item) for key, item in (value.get("hard_gate_results") or {}).items()
            },
            metric_vector={
                str(key): float(item)
                for key, item in (value.get("metric_vector") or {}).items()
                if isinstance(item, (int, float))
            },
            rollback_target=str(value.get("rollback_target") or "").strip() or None,
            metadata=dict(value.get("metadata") or {}),
            created_at=str(value.get("created_at") or _now()),
            updated_at=str(value.get("updated_at") or _now()),
            schema=str(value.get("schema") or CANDIDATE_SCHEMA),
        )


def candidate_id_for(
    *,
    gene_type: GeneType | str,
    scope: str,
    patch: dict[str, Any],
    parent_id: str | None = None,
    tenant_id: str | None = None,
    owner_actor_id: str | None = None,
) -> str:
    payload = json.dumps(
        {
            "gene_type": str(gene_type),
            "scope": scope,
            "patch": patch,
            "parent_id": parent_id,
            # Ownership is part of the identity domain.  The same learned
            # patch proposed by two tenants must never collapse to one
            # control-plane object, even when an explicitly global operator
            # later enumerates every tenant partition.
            "tenant_id": str(tenant_id or "").strip() or None,
            "owner_actor_id": str(owner_actor_id or "").strip() or None,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"cand_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:20]}"


class CandidateRegistry:
    """Append-only candidate events; latest event is the current state."""

    def __init__(
        self,
        path: str | Path = "data/evolution_candidates.jsonl",
        *,
        tenant_scope: TenantScope | None = None,
    ) -> None:
        self.path = Path(path)
        self.tenant_scope = tenant_scope
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def propose(
        self,
        *,
        gene_type: GeneType | str,
        scope: str,
        patch: dict[str, Any],
        proposer: str,
        parent_id: str | None = None,
        lineage_id: str = "",
        role_id: str = "general",
        task_domain: str = "general",
        environment_digest: str = "",
        risk_level: str = "medium",
        source_failures: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        tenant_id: str | None = None,
        owner_actor_id: str | None = None,
    ) -> EvolutionCandidate:
        resolved_type = GeneType(str(gene_type))
        if not scope.strip() or not patch:
            raise ValueError("candidate scope and non-empty patch are required")
        if self.tenant_scope is not None and not self.tenant_scope.allow_cross_tenant:
            expected_tenant = self.tenant_scope.tenant_id
            expected_owner = self.tenant_scope.actor_id
            if tenant_id is not None and str(tenant_id).strip() != expected_tenant:
                raise ValueError("candidate tenant provenance conflicts with registry scope")
            if owner_actor_id is not None and str(owner_actor_id).strip() != expected_owner:
                raise ValueError("candidate owner provenance conflicts with registry scope")
            tenant_id = expected_tenant
            owner_actor_id = expected_owner
        resolved_tenant = str(tenant_id or "").strip() or None
        resolved_owner = str(owner_actor_id or "").strip() or None
        if bool(resolved_tenant) != bool(resolved_owner):
            raise ValueError("candidate tenant and owner provenance must be supplied together")
        candidate_id = candidate_id_for(
            gene_type=resolved_type,
            scope=scope,
            patch=patch,
            parent_id=parent_id,
            tenant_id=resolved_tenant,
            owner_actor_id=resolved_owner,
        )
        # Hold the stable file transaction across the absence check and
        # append. Process-local locking alone lets two Uvicorn workers both
        # observe absence and interleave JSONL writes.
        with _LOCK, path_transaction(self.path):
            existing = self.get(candidate_id)
            if existing is not None:
                if (
                    existing.gene_type != resolved_type
                    or existing.scope != scope.strip()
                    or existing.patch != patch
                    or existing.parent_id != parent_id
                    or existing.tenant_id != resolved_tenant
                    or existing.owner_actor_id != resolved_owner
                ):
                    raise ValueError(f"candidate id conflict: {candidate_id}")
                return existing
            candidate = EvolutionCandidate(
                candidate_id=candidate_id,
                gene_type=resolved_type,
                scope=scope.strip(),
                patch=dict(patch),
                proposer=proposer.strip() or "system",
                tenant_id=resolved_tenant,
                owner_actor_id=resolved_owner,
                parent_id=parent_id,
                lineage_id=lineage_id.strip() or candidate_id,
                role_id=role_id.strip() or "general",
                task_domain=task_domain.strip() or "general",
                environment_digest=environment_digest.strip(),
                risk_level=risk_level.strip() or "medium",
                source_failures=list(source_failures or []),
                metadata=dict(metadata or {}),
            )
            self._append(candidate)
            return candidate

    def transition(
        self,
        candidate_id: str,
        status: CandidateStatus | str,
        *,
        hard_gate_results: dict[str, bool] | None = None,
        metric_vector: dict[str, float] | None = None,
        experiment_ids: list[str] | None = None,
        rollback_target: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> EvolutionCandidate:
        with _LOCK, path_transaction(self.path):
            current = self.get(candidate_id)
            if current is None:
                raise KeyError(f"unknown evolution candidate: {candidate_id}")
            next_status = CandidateStatus(str(status))
            if next_status not in _TRANSITIONS[current.status]:
                raise ValueError(
                    f"invalid candidate transition: {current.status.value} -> {next_status.value}"
                )
            if next_status in {
                CandidateStatus.VALIDATED,
                CandidateStatus.SHADOW,
                CandidateStatus.CANARY,
                CandidateStatus.PROMOTED,
            }:
                gates = (
                    hard_gate_results
                    if hard_gate_results is not None
                    else current.hard_gate_results
                )
                if not gates or not all(bool(item) for item in gates.values()):
                    raise ValueError(f"{next_status.value} requires passing hard-gate evidence")
            current.status = next_status
            current.updated_at = _now()
            if hard_gate_results is not None:
                current.hard_gate_results = dict(hard_gate_results)
            if metric_vector is not None:
                current.metric_vector = {
                    str(key): float(item) for key, item in metric_vector.items()
                }
            if experiment_ids is not None:
                current.experiment_ids = list(dict.fromkeys(experiment_ids))
            if rollback_target is not None:
                current.rollback_target = rollback_target
            if metadata:
                current.metadata.update(metadata)
            self._append(current)
            return current

    def record_evidence(
        self,
        candidate_id: str,
        *,
        hard_gate_results: dict[str, bool] | None = None,
        metric_vector: dict[str, float] | None = None,
        experiment_ids: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> EvolutionCandidate:
        """Append evidence without pretending that a lifecycle transition occurred."""

        with _LOCK, path_transaction(self.path):
            current = self.get(candidate_id)
            if current is None:
                raise KeyError(f"unknown evolution candidate: {candidate_id}")
            current.updated_at = _now()
            if hard_gate_results is not None:
                current.hard_gate_results = dict(hard_gate_results)
            if metric_vector is not None:
                current.metric_vector = {
                    str(key): float(item) for key, item in metric_vector.items()
                }
            if experiment_ids is not None:
                current.experiment_ids = list(dict.fromkeys(experiment_ids))
            if metadata:
                current.metadata.update(metadata)
            self._append(current)
            return current

    def get(self, candidate_id: str) -> EvolutionCandidate | None:
        return self._latest().get(candidate_id)

    def list(
        self,
        *,
        status: CandidateStatus | None = None,
        gene_type: GeneType | None = None,
        limit: int = 100,
    ) -> list[EvolutionCandidate]:
        rows = list(self._latest().values())
        if status is not None:
            rows = [row for row in rows if row.status == status]
        if gene_type is not None:
            rows = [row for row in rows if row.gene_type == gene_type]
        rows.sort(key=lambda row: row.updated_at)
        return rows[-max(1, int(limit)) :]

    def lineage(self, lineage_id: str) -> builtins.list[EvolutionCandidate]:
        return [row for row in self.list(limit=10_000) if row.lineage_id == lineage_id]

    def _append(self, candidate: EvolutionCandidate) -> None:
        line = json.dumps(candidate.to_wire(), ensure_ascii=False, sort_keys=True) + "\n"
        try:
            with _LOCK, path_transaction(self.path) as target:
                with target.open("a", encoding="utf-8") as handle:
                    handle.write(line)
                    handle.flush()
                    os.fsync(handle.fileno())
                _fsync_directory(target.parent)
        except (OSError, TransactionalFileError) as exc:
            raise CandidateRegistryError("candidate lineage append is not durable") from exc

    def _latest(self) -> dict[str, EvolutionCandidate]:
        try:
            with _LOCK, path_transaction(self.path) as target:
                if not target.exists():
                    return {}
                payload = target.read_bytes()
                if payload and not payload.endswith(b"\n"):
                    raise CandidateRegistryError("candidate lineage has a truncated row")
                try:
                    text = payload.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise CandidateRegistryError("candidate lineage is not UTF-8") from exc
                out: dict[str, EvolutionCandidate] = {}
                for line_number, line in enumerate(text.splitlines(), start=1):
                    if not line.strip():
                        continue
                    try:
                        raw = json.loads(line)
                        if not isinstance(raw, dict):
                            raise ValueError("row is not an object")
                        candidate = EvolutionCandidate.from_wire(raw)
                    except (json.JSONDecodeError, TypeError, ValueError) as exc:
                        raise CandidateRegistryError(
                            f"candidate lineage row {line_number} is invalid"
                        ) from exc
                    if not candidate.candidate_id:
                        raise CandidateRegistryError(
                            f"candidate lineage row {line_number} has no candidate id"
                        )
                    if (
                        self.tenant_scope is not None
                        and not self.tenant_scope.allow_cross_tenant
                        and (
                            candidate.tenant_id != self.tenant_scope.tenant_id
                            or candidate.owner_actor_id != self.tenant_scope.actor_id
                        )
                    ):
                        raise CandidateRegistryError(
                            "candidate lineage provenance conflicts with registry scope"
                        )
                    out[candidate.candidate_id] = candidate
                # Repair a prior writer that appended successfully but
                # reported an fsync error before acknowledging the mutation.
                # Candidate files are low-volume control-plane state, so a
                # read-side durability fence is preferable to returning a
                # possibly page-cache-only lifecycle state.
                with target.open("rb") as handle:
                    os.fsync(handle.fileno())
                _fsync_directory(target.parent)
                return out
        except (OSError, TransactionalFileError) as exc:
            raise CandidateRegistryError("candidate lineage cannot be locked or read") from exc


__all__ = [
    "CANDIDATE_SCHEMA",
    "CandidateRegistryError",
    "CandidateRegistry",
    "CandidateStatus",
    "EvolutionCandidate",
    "GeneType",
    "candidate_id_for",
]
