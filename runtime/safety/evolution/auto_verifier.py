from __future__ import annotations

import shlex
import sys
from pathlib import Path
from typing import Any

from runtime.protocol import ItemStatus, VerificationItem
from runtime.safety.evolution.auto_verifier_metrics import (
    explain_verification_ranking,
    rank_verification_commands,
    record_auto_verifier_batch,
    record_auto_verifier_decision,
    record_auto_verifier_metric,
)
from runtime.safety.sandboxing.sandbox import (
    SandboxPolicy,
    SandboxRunner,
    SandboxViolation,
)

_OUTPUT_CAP = 4000
_TIMEOUT_S = 45.0
_MAX_COMMANDS = 3
_MAX_REPAIR_ATTEMPTS = 2


def _inference_domains() -> tuple[str, ...]:
    """Model inference endpoints that stay reachable in a network-denied
    sandbox (Claude Desktop parity). Best-effort; empty means deny-all."""
    try:
        from runtime.safety.sandboxing.sandbox import inference_domains

        return inference_domains()
    except Exception:  # noqa: BLE001 - best-effort; empty means deny-all
        return ()


def run_verification_plan(
    plan: dict[str, Any],
    *,
    sandbox_policy: dict[str, Any] | None,
    max_commands: int = _MAX_COMMANDS,
) -> list[VerificationItem]:
    """Run a bounded verifier batch, stopping at the first failure.

    A code change often needs more than one kind of evidence (for example,
    lint plus a targeted test).  The former single-command fallback could
    declare success after lint alone.  This batch keeps the ranking policy,
    runs at most ``max_commands`` distinct safe commands, and never hides a
    failing check behind later successes.
    """

    if not _sandbox_allows_auto_verification(sandbox_policy):
        return []
    workspace = _workspace(plan)
    if workspace is None:
        return []
    commands = plan.get("commands")
    if not isinstance(commands, list):
        return []
    candidates = [item for item in commands if isinstance(item, dict)]
    ranking = explain_verification_ranking(candidates)
    limit = max(1, min(int(max_commands), _MAX_COMMANDS))
    items: list[VerificationItem] = []
    seen: set[str] = set()
    for command in rank_verification_commands(candidates):
        raw = str(command.get("command") or "").strip()
        if not raw or raw in seen:
            continue
        seen.add(raw)
        item = _run_command(command, workspace, sandbox_policy or {})
        if item is None:
            continue
        _record_decision(ranking, item.command)
        items.append(item)
        if item.status != ItemStatus.COMPLETED or len(items) >= limit:
            break
    if items:
        passed_count = sum(item.status == ItemStatus.COMPLETED for item in items)
        stop_reason = (
            "failed"
            if passed_count != len(items)
            else "limit_reached"
            if len(items) >= limit
            else "exhausted"
        )
        record_auto_verifier_batch(
            candidate_count=len(candidates),
            commands=[item.command for item in items],
            passed_count=passed_count,
            stop_reason=stop_reason,
        )
    return items


def run_highest_priority_verification(
    plan: dict[str, Any],
    *,
    sandbox_policy: dict[str, Any] | None,
) -> VerificationItem | None:
    items = run_verification_plan(
        plan,
        sandbox_policy=sandbox_policy,
        max_commands=1,
    )
    return items[0] if items else None


def build_verification_repair_request(
    plan: dict[str, Any],
    items: list[VerificationItem],
    *,
    attempt: int,
    max_attempts: int = _MAX_REPAIR_ATTEMPTS,
) -> dict[str, Any]:
    """Build bounded, evidence-carrying input for a model repair round."""

    bounded_max = max(1, min(int(max_attempts), _MAX_REPAIR_ATTEMPTS))
    bounded_attempt = max(1, min(int(attempt), bounded_max))
    failures = [
        {
            "command": item.command,
            "kind": item.kind,
            "exit_code": item.exit_code,
            "summary": item.summary,
            "stdout_tail": (item.stdout_tail or "")[-_OUTPUT_CAP:],
            "stderr_tail": (item.stderr_tail or "")[-_OUTPUT_CAP:],
            "related_files": list(item.related_files),
        }
        for item in items
        if item.status != ItemStatus.COMPLETED
    ]
    evidence_lines: list[str] = []
    for failure in failures:
        evidence_lines.append(
            f"- {failure['command']} (exit={failure['exit_code']}): {failure['summary']}"
        )
        tail = str(failure.get("stderr_tail") or failure.get("stdout_tail") or "").strip()
        if tail:
            evidence_lines.append(f"  evidence: {tail[-1200:]}")
    prompt = "\n".join(
        [
            f"Verification repair attempt {bounded_attempt}/{bounded_max}.",
            "The previous verifier batch failed. Diagnose the evidence, make the smallest safe code repair, and do not claim success.",
            "After this repair round the runtime will rerun the verifier commands and require fresh passing evidence.",
            *evidence_lines,
        ]
    )
    return {
        "schema": "echo.verification_repair_request.v1",
        "attempt": bounded_attempt,
        "max_attempts": bounded_max,
        "workspace": str(plan.get("workspace") or ""),
        "targets": [str(target) for target in plan.get("targets") or []],
        "failures": failures,
        "fresh_evidence_required": True,
        "prompt": prompt,
    }


def build_agent_verification_request(
    plan: dict[str, Any],
    *,
    attempt: int,
    max_attempts: int = _MAX_REPAIR_ATTEMPTS,
) -> dict[str, Any]:
    """Build a bounded, run-the-plan prompt when code changed with no
    recorded verification evidence.

    This is the auto loop-back for the case where the auto-verifier could not
    produce any evidence (sandbox didn't allow it, or no allowlisted command
    fit) and the agent ended without running a verification step. Instead of
    hard-ending with a manual ``verification required`` error, the agent is
    asked to run the recommended commands itself with its own tools, fix
    anything that fails, and only then conclude.
    """

    bounded_max = max(1, min(int(max_attempts), _MAX_REPAIR_ATTEMPTS))
    bounded_attempt = max(1, min(int(attempt), bounded_max))
    commands = [
        {
            "command": str(command.get("command") or "").strip(),
            "kind": str(command.get("kind") or "manual"),
            "target": str(command.get("target") or "").strip(),
            "reason": str(command.get("reason") or "").strip(),
        }
        for command in (plan.get("commands") or [])
        if isinstance(command, dict) and str(command.get("command") or "").strip()
    ]
    command_lines = (
        "\n".join(
            f"- [{command['kind']}] {command['command']}"
            + (f" :: {command['reason']}" if command["reason"] else "")
            for command in commands
        )
        or "- (no recommended command matched; pick the repository's test / lint / build)"
    )
    prompt = "\n".join(
        [
            f"Verification attempt {bounded_attempt}/{bounded_max}.",
            "You changed code but no verification step was recorded before the final answer.",
            "Run the recommended verification commands yourself, fix anything that fails, "
            "and only then give the final answer. Do not claim success without fresh passing evidence.",
            command_lines,
        ]
    )
    return {
        "schema": "echo.verification_request.v1",
        "attempt": bounded_attempt,
        "max_attempts": bounded_max,
        "workspace": str(plan.get("workspace") or ""),
        "targets": [str(target) for target in plan.get("targets") or []],
        "commands": commands,
        "fresh_evidence_required": True,
        "prompt": prompt,
    }


def _sandbox_allows_auto_verification(policy: dict[str, Any] | None) -> bool:
    if not isinstance(policy, dict):
        return False
    return str(policy.get("type") or "") == "workspaceWrite"


def _workspace(plan: dict[str, Any]) -> Path | None:
    raw = plan.get("workspace")
    if not isinstance(raw, str) or not raw.strip():
        return None
    path = Path(raw).expanduser().resolve(strict=False)
    return path if path.is_dir() else None


def _run_command(
    command: dict[str, Any],
    workspace: Path,
    sandbox_policy: dict[str, Any],
) -> VerificationItem | None:
    raw = str(command.get("command") or "").strip()
    target = str(command.get("target") or "").strip()
    if not raw or not _target_exists(workspace, target):
        return None
    parsed = _parse_allowed_command(raw, workspace)
    if parsed is None:
        return None
    argv, cwd = parsed
    allow_network = bool(sandbox_policy.get("networkAccess"))
    policy = SandboxPolicy(
        workspace=workspace,
        allow_network=allow_network,
        timeout_s=_TIMEOUT_S,
        max_output_bytes=_OUTPUT_CAP,
        # Model inference endpoints stay reachable even when the sandbox
        # is network-denied (Claude Desktop parity).
        inference_domains=(() if allow_network else _inference_domains()),
    )
    try:
        result = SandboxRunner(policy).run(argv, cwd=cwd)
    except SandboxViolation as exc:
        _record_metric(
            command,
            ok=False,
            exit_code=None,
            duration_ms=0,
            reason=str(exc),
        )
        return VerificationItem(
            command=raw,
            kind=_verification_kind(command),
            status=ItemStatus.FAILED,
            exit_code=None,
            summary="Auto verification was blocked by sandbox policy.",
            stdout_tail=None,
            stderr_tail=str(exc)[:_OUTPUT_CAP],
            related_files=[target] if target else [],
        )

    ok = result.exit_code == 0 and not result.timed_out
    _record_metric(
        command,
        ok=ok,
        exit_code=result.exit_code,
        duration_ms=result.duration_ms,
        reason="timed_out" if result.timed_out else "",
    )
    output = (result.stdout or "").strip()
    error = (result.stderr or "").strip()
    summary = (
        "Auto verification passed."
        if ok
        else "Auto verification failed."
        if not result.timed_out
        else "Auto verification timed out."
    )
    return VerificationItem(
        command=raw,
        kind=_verification_kind(command),
        status=ItemStatus.COMPLETED if ok else ItemStatus.FAILED,
        exit_code=result.exit_code,
        summary=summary,
        stdout_tail=output[:_OUTPUT_CAP] if output else None,
        stderr_tail=error[:_OUTPUT_CAP] if error else None,
        related_files=[target] if target else [],
    )


def _target_exists(workspace: Path, target: str) -> bool:
    if not target:
        return False
    path = (workspace / target).resolve(strict=False)
    try:
        path.relative_to(workspace)
    except ValueError:
        return False
    return path.exists()


def _parse_allowed_command(raw: str, workspace: Path) -> tuple[list[str], Path] | None:
    try:
        parts = shlex.split(raw)
    except ValueError:
        return None
    if not parts:
        return None
    cwd = workspace
    if len(parts) >= 5 and parts[0] == "cd" and parts[2] == "&&":
        cwd = (workspace / parts[1]).resolve(strict=False)
        try:
            cwd.relative_to(workspace)
        except ValueError:
            return None
        if not cwd.is_dir():
            return None
        parts = parts[3:]
    if _is_allowed_python_command(parts):
        if parts[0] == "python":
            parts = [sys.executable, *parts[1:]]
        return parts, cwd
    if _is_allowed_pnpm_command(parts):
        return parts, cwd
    return None


def _is_allowed_python_command(parts: list[str]) -> bool:
    if len(parts) < 4 or parts[0] not in {"python", sys.executable} or parts[1] != "-m":
        return False
    module = parts[2]
    if module == "ruff":
        return len(parts) >= 5 and parts[3] == "check"
    return module == "pytest"


def _is_allowed_pnpm_command(parts: list[str]) -> bool:
    if len(parts) < 2 or parts[0] != "pnpm":
        return False
    if parts[1] == "check":
        return True
    return len(parts) >= 4 and parts[1:3] == ["vitest", "run"]


def _verification_kind(command: dict[str, Any]) -> str:
    kind = str(command.get("kind") or "manual")
    return (
        kind if kind in {"test", "lint", "typecheck", "build", "diagnostic", "manual"} else "manual"
    )


def _record_metric(
    command: dict[str, Any],
    *,
    ok: bool,
    exit_code: int | None,
    duration_ms: int,
    reason: str = "",
) -> None:
    try:
        record_auto_verifier_metric(
            command=str(command.get("command") or ""),
            kind=_verification_kind(command),
            ok=ok,
            exit_code=exit_code,
            duration_ms=duration_ms,
            target=str(command.get("target") or ""),
            reason=reason or str(command.get("reason") or ""),
        )
    except Exception:  # noqa: BLE001 - metrics must not affect verification
        return


def _record_decision(ranking: list[dict[str, Any]], selected_command: str) -> None:
    try:
        record_auto_verifier_decision(
            candidates=ranking,
            selected_command=selected_command,
        )
    except Exception:  # noqa: BLE001 - decision telemetry must not affect verification
        return


__all__ = [
    "build_agent_verification_request",
    "build_verification_repair_request",
    "run_highest_priority_verification",
    "run_verification_plan",
]
