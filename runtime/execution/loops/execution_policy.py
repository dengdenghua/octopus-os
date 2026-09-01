from __future__ import annotations

import json
from typing import Any

from runtime.execution.loops.models import VerifierResult

_EXECUTION_POLICY_KEYS = (
    "schema",
    "sandbox_requested",
    "workspace",
    "cwd",
    "backend",
    "hard",
    "allow_network",
    "env_mode",
    "process_group",
    "process_tree_kill",
    "timeout_s",
    "result",
)

_EXECUTION_POLICY_RESULT_KEYS = (
    "status",
    "exit_code",
    "timed_out",
    "cancelled",
    "killed",
    "stdout_truncated",
    "stderr_truncated",
    "output_truncated",
    "error_type",
)


def execution_policy_summary(policy: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(policy, dict) or not policy:
        return {}
    summary = {key: policy[key] for key in _EXECUTION_POLICY_KEYS if key in policy}
    result = summary.get("result")
    if isinstance(result, dict):
        summary["result"] = {
            key: result[key] for key in _EXECUTION_POLICY_RESULT_KEYS if key in result
        }
    elif "result" in summary:
        summary.pop("result", None)
    return summary


def verifier_execution_policies(result: VerifierResult | None) -> list[dict[str, Any]]:
    if result is None:
        return []
    policies: list[dict[str, Any]] = []
    seen: set[str] = set()
    for finding in result.findings:
        policy = execution_policy_summary(finding.execution_policy)
        if not policy:
            continue
        marker = json.dumps(policy, ensure_ascii=False, sort_keys=True)
        if marker in seen:
            continue
        seen.add(marker)
        policies.append(policy)
    return policies


__all__ = [
    "execution_policy_summary",
    "verifier_execution_policies",
]
