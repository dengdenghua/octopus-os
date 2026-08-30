from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from runtime.core.cerebrum.run_state import RunStateSummary, converge_run_state


@dataclass(frozen=True)
class CompletionReceipt:
    """Machine-readable proof that a run reached a defensible terminal state."""

    ready: bool
    state: RunStateSummary
    issues: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    artifact_count: int = 0
    output_present: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "state": self.state.to_dict(),
            "issues": list(self.issues),
            "warnings": list(self.warnings),
            "artifact_count": self.artifact_count,
            "output_present": self.output_present,
        }


def build_completion_receipt(
    statuses: list[str] | tuple[str, ...],
    *,
    contract_issues: list[str] | tuple[str, ...] = (),
    contract_warnings: list[str] | tuple[str, ...] = (),
    artifact_count: int = 0,
    output_present: bool = False,
) -> CompletionReceipt:
    state = converge_run_state(list(statuses))
    issues: list[str] = list(contract_issues)
    warnings: list[str] = list(contract_warnings)

    if state.failed:
        issues.append("failed_work_items")
    if state.cancelled:
        issues.append("cancelled_work_items")
    if state.unknown:
        issues.append("unknown_work_item_state")
    if not state.terminal:
        warnings.append("run_not_terminal")
    if state.state == "completed" and not output_present and artifact_count == 0:
        warnings.append("no_output_or_artifact")

    ready = state.terminal and state.state == "completed" and not issues

    return CompletionReceipt(
        ready=ready,
        state=state,
        issues=tuple(dict.fromkeys(issues)),
        warnings=tuple(dict.fromkeys(warnings)),
        artifact_count=max(0, int(artifact_count or 0)),
        output_present=bool(output_present),
    )
