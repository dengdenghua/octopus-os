from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ContractValidationReport:
    valid: bool
    errors: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


def validate_work_plan(plan: Any) -> ContractValidationReport:
    """Validate a parallel/swarm plan without depending on concrete models."""

    contracts = list(getattr(plan, "contracts", []) or [])
    phases = list(getattr(plan, "phases", []) or [])
    errors: list[str] = []
    warnings: list[str] = []

    contract_ids: list[str] = []
    owner_by_scope: dict[str, str] = {}
    contract_by_id: dict[str, Any] = {}
    covered_units: set[str] = set()

    for idx, contract in enumerate(contracts):
        cid = str(getattr(contract, "contract_id", "") or "")
        if not cid:
            errors.append(f"contract_{idx}_missing_id")
            continue
        if cid in contract_by_id:
            errors.append(f"duplicate_contract_id:{cid}")
        contract_by_id[cid] = contract
        contract_ids.append(cid)

        role = str(getattr(contract, "role", "") or "")
        if not role:
            warnings.append(f"contract_{cid}_missing_role")
        criteria = list(getattr(contract, "success_criteria", []) or [])
        if not criteria:
            warnings.append(f"contract_{cid}_missing_success_criteria")

        for unit in _contract_units(contract):
            covered_units.add(unit)
        for scope in list(getattr(contract, "owned_scope", []) or []):
            scope = str(scope)
            prior = owner_by_scope.get(scope)
            if prior is not None and prior != cid:
                errors.append(f"owned_scope_conflict:{scope}:{prior}:{cid}")
            owner_by_scope[scope] = cid

    phase_units = _phase_units(phases)
    for unit in phase_units:
        if unit not in covered_units and unit not in contract_by_id:
            errors.append(f"phase_unit_without_contract:{unit}")

    known_refs = set(contract_by_id) | covered_units | phase_units
    dep_graph: dict[str, set[str]] = {cid: set() for cid in contract_by_id}
    for cid, contract in contract_by_id.items():
        for dep in list(getattr(contract, "depends_on", []) or []):
            dep_id = str(dep)
            if dep_id not in known_refs:
                errors.append(f"unknown_dependency:{cid}:{dep_id}")
            if dep_id in dep_graph:
                dep_graph[cid].add(dep_id)

    errors.extend(_cycle_errors(dep_graph))

    for cid, contract in contract_by_id.items():
        forbidden = {str(item) for item in list(getattr(contract, "forbidden_scope", []) or [])}
        owned = {str(item) for item in list(getattr(contract, "owned_scope", []) or [])}
        overlap = sorted(forbidden & owned)
        for scope in overlap:
            errors.append(f"contract_forbids_owned_scope:{cid}:{scope}")

    errors.extend(_parallel_file_write_conflicts(phases, contract_by_id))

    return ContractValidationReport(
        valid=not errors,
        errors=tuple(dict.fromkeys(errors)),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def _contract_units(contract: Any) -> set[str]:
    units: set[str] = set()
    for attr in ("task_ids", "node_ids"):
        for value in list(getattr(contract, attr, []) or []):
            units.add(str(value))
    return units


def _phase_units(phases: list[Any]) -> set[str]:
    units: set[str] = set()
    for phase in phases:
        for attr in ("task_ids", "node_ids"):
            for value in list(getattr(phase, attr, []) or []):
                units.add(str(value))
    return units


def _parallel_file_write_conflicts(
    phases: list[Any],
    contract_by_id: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    for phase in phases:
        if getattr(phase, "parallel", False) is not True:
            continue
        owners_by_path: dict[str, str] = {}
        phase_contracts = _contracts_for_phase(phase, contract_by_id)
        for contract in phase_contracts:
            cid = str(getattr(contract, "contract_id", "") or "")
            for raw_path in list(getattr(contract, "write_paths", []) or []):
                path = _normalize_write_path(str(raw_path))
                if not path:
                    continue
                prior = owners_by_path.get(path)
                if prior is not None and prior != cid:
                    errors.append(f"parallel_file_write_conflict:{path}:{prior}:{cid}")
                    continue
                owners_by_path[path] = cid
    return errors


def _contracts_for_phase(phase: Any, contract_by_id: dict[str, Any]) -> list[Any]:
    task_ids = {str(task_id) for task_id in list(getattr(phase, "task_ids", []) or [])}
    node_ids = {str(node_id) for node_id in list(getattr(phase, "node_ids", []) or [])}
    units = task_ids | node_ids
    contracts: list[Any] = []
    for cid, contract in contract_by_id.items():
        contract_units = _contract_units(contract) | {cid}
        if units & contract_units:
            contracts.append(contract)
    return contracts


def _normalize_write_path(path: str) -> str:
    normalized = path.strip().replace("\\", "/").lstrip("./")
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    return normalized


def _cycle_errors(graph: dict[str, set[str]]) -> list[str]:
    visiting: set[str] = set()
    visited: set[str] = set()
    errors: list[str] = []

    def visit(node: str, path: list[str]) -> None:
        if node in visited:
            return
        if node in visiting:
            cycle = "->".join([*path, node])
            errors.append(f"dependency_cycle:{cycle}")
            return
        visiting.add(node)
        for dep in graph.get(node, set()):
            visit(dep, [*path, node])
        visiting.discard(node)
        visited.add(node)

    for node in graph:
        visit(node, [])
    return errors
