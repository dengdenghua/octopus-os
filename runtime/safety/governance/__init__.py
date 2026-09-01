"""Unified execution-governance primitives.

The package turns a tool call into a stable instruction + decision pair so
the executor, audit trail, and UI do not each invent their own safety state.
"""

from .execution_policy import (
    ExecutionInstruction,
    ExecutionPolicyContext,
    GovernanceDecision,
    GovernanceOutcome,
    build_execution_instruction,
    evaluate_execution_policy,
)

__all__ = [
    "ExecutionInstruction",
    "ExecutionPolicyContext",
    "GovernanceDecision",
    "GovernanceOutcome",
    "build_execution_instruction",
    "evaluate_execution_policy",
]
