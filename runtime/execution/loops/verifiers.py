from __future__ import annotations

from collections.abc import Callable
from typing import Any

from runtime.execution.loops.models import VerifierFinding, VerifierResult

_AUTO_PROFILE = "auto"
_LEGACY_PROFILE = "python_repo_patch"
_KIND_PROFILES = frozenset({"python", "node", "node-ts", "rust", "go", "unknown"})
_BLOCKING_FAILURE_PRIORITY = (
    "project_kind_mismatch",
    "verifier_profile_unknown",
    "verifier_internal_error",
    "verifier_misconfigured",
    "verifier_sandbox_violation",
    "environment_missing_tool",
    "environment_missing_dependency",
    "verification_cancelled",
)
_FAILURE_PRIORITY = (
    *_BLOCKING_FAILURE_PRIORITY,
    "project_manifest_error",
    "verification_timeout",
    "syntax_error",
    "type_error",
    "lint_failure",
    "build_failure",
    "test_failure",
    "verification_failure",
)


def _finding_output(finding: VerifierFinding) -> str:
    return "\n".join(part for part in (finding.stderr, finding.stdout) if str(part or "").strip())


def _summarize_failed_findings(findings: list[VerifierFinding]) -> str:
    failed = [finding for finding in findings if not finding.passed]
    if not failed:
        return "all checks passed"
    names = ", ".join(finding.name for finding in failed[:5])
    category = _dominant_failure_category(findings)
    if category in _BLOCKING_FAILURE_PRIORITY:
        return f"verification blocker ({category}): {names}"
    if category:
        return f"failed checks ({category}): {names}"
    return f"failed checks: {names}"


def _dominant_failure_category(findings: list[VerifierFinding]) -> str:
    categories = {
        str(finding.category or "").strip()
        for finding in findings
        if not finding.passed and str(finding.category or "").strip()
    }
    for category in _FAILURE_PRIORITY:
        if category in categories:
            return category
    return sorted(categories)[0] if categories else ""


def _classify_finding(
    *,
    name: str,
    command: str,
    exit_code: int | None,
    stdout: str,
    stderr: str,
    execution_policy: dict[str, Any] | None = None,
) -> str:
    output = "\n".join(part for part in (stderr, stdout) if str(part or "").strip())
    policy_result = (
        execution_policy.get("result")
        if isinstance(execution_policy, dict) and isinstance(execution_policy.get("result"), dict)
        else {}
    )
    policy_status = str(policy_result.get("status") or "").strip().lower()
    policy_error_type = str(policy_result.get("error_type") or "").strip().lower()
    if policy_status == "sandbox_violation" or policy_error_type == "sandbox_violation":
        return "verifier_sandbox_violation"
    if policy_status == "timed_out" or policy_result.get("timed_out") is True:
        return "verification_timeout"
    if policy_status == "cancelled" or policy_result.get("cancelled") is True:
        return "verification_cancelled"
    if policy_status == "exec_failed":
        return "environment_missing_tool"
    try:
        from runtime.execution.suckers.verify_skills import classify_environment_gap

        environment_gap = classify_environment_gap(output)
    except Exception:  # pragma: no cover - defensive shared helper fallback
        environment_gap = ""
    if environment_gap:
        return environment_gap
    lowered = output.lower()
    command_lower = command.lower()
    check_name = name.strip().lower()
    manifest_check_names = {"package-json", "pyproject", "cargo-manifest", "go-manifest"}
    if exit_code == -2:
        return "verifier_misconfigured"
    if exit_code == -6:
        return "verifier_internal_error"
    if exit_code == -5:
        return "verification_cancelled"
    if exit_code == -4 and "sandbox_violation" in lowered:
        return "verifier_sandbox_violation"
    if "timeout" in lowered or "timed out" in lowered or exit_code == -1:
        return "verification_timeout"
    if (check_name in manifest_check_names and exit_code == 2) or any(
        marker in lowered
        for marker in (
            "ejsonparse",
            "jsondecodeerror",
            "tomldecodeerror",
            "invalid json",
            "invalid toml",
            "missing module directive",
        )
    ):
        return "project_manifest_error"
    if check_name == "syntax" or "syntaxerror" in lowered or "invalid syntax" in lowered:
        return "syntax_error"
    if check_name == "typecheck" or any(
        marker in command_lower for marker in ("tsc", "mypy", "pyright")
    ):
        return "type_error"
    if check_name in {"lint", "clippy", "vet"} or any(
        marker in command_lower for marker in ("eslint", "ruff", "clippy", "go vet")
    ):
        return "lint_failure"
    if (
        check_name in {"build", "check"}
        or "build" in command_lower
        or "cargo check" in command_lower
    ):
        return "build_failure"
    if check_name == "test" or any(
        marker in command_lower
        for marker in (
            "pytest",
            "vitest",
            "jest",
            "playwright",
            "cargo test",
            "go test",
            "npm test",
        )
    ):
        return "test_failure"
    return "verification_failure"


class LoopVerifierRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, Callable[[str], VerifierResult]] = {}

    def register(
        self,
        profile: str,
        handler: Callable[[str], VerifierResult],
    ) -> None:
        self._handlers[str(profile).strip()] = handler

    def run(self, profile: str, workspace_path: str) -> VerifierResult:
        key = str(profile or "").strip()
        handler = self._handlers.get(key)
        if handler is None:
            raise KeyError(key or "<empty>")
        return handler(workspace_path)


def _findings_from_checks(workspace_path: str) -> tuple[str, list[VerifierFinding]]:
    from runtime.execution.suckers.verify_skills import detect_project, run_checks

    profile = detect_project(workspace_path)
    results = run_checks(profile, timeout_per_check=60)
    return profile.kind, [
        VerifierFinding(
            name=result.name,
            command=result.command,
            passed=result.passed,
            category=(
                ""
                if result.passed
                else _classify_finding(
                    name=result.name,
                    command=result.command,
                    exit_code=result.exit_code,
                    stdout=result.stdout,
                    stderr=result.stderr,
                    execution_policy=result.execution_policy,
                )
            ),
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_ms=result.duration_ms,
            execution_policy=result.execution_policy,
        )
        for result in results
    ]


def _verifier_result(
    *,
    requested_profile: str,
    detected_kind: str,
    findings: list[VerifierFinding],
) -> VerifierResult:
    passed = all(finding.passed for finding in findings)
    return VerifierResult(
        profile=requested_profile,
        kind=detected_kind,
        failure_category="" if passed else _dominant_failure_category(findings),
        passed=passed,
        findings=findings,
        summary=_summarize_failed_findings(findings),
    )


def _run_auto_verifier(workspace_path: str) -> VerifierResult:
    detected_kind, findings = _findings_from_checks(workspace_path)
    return _verifier_result(
        requested_profile=_AUTO_PROFILE,
        detected_kind=detected_kind,
        findings=findings,
    )


def _run_legacy_python_repo_patch_verifier(workspace_path: str) -> VerifierResult:
    detected_kind, findings = _findings_from_checks(workspace_path)
    return _verifier_result(
        requested_profile=_LEGACY_PROFILE,
        detected_kind=detected_kind,
        findings=findings,
    )


def _kind_guarded_verifier(expected_kind: str) -> Callable[[str], VerifierResult]:
    def _run(workspace_path: str) -> VerifierResult:
        detected_kind, findings = _findings_from_checks(workspace_path)
        if detected_kind != expected_kind:
            findings = [
                VerifierFinding(
                    name="project-kind",
                    command="detect_project",
                    category="project_kind_mismatch",
                    passed=False,
                    exit_code=-4,
                    stderr=(
                        f"verifier profile {expected_kind!r} does not match "
                        f"detected project kind {detected_kind!r}"
                    ),
                ),
                *findings,
            ]
        return _verifier_result(
            requested_profile=expected_kind,
            detected_kind=detected_kind,
            findings=findings,
        )

    return _run


def build_default_loop_verifier_registry() -> LoopVerifierRegistry:
    registry = LoopVerifierRegistry()
    registry.register(_AUTO_PROFILE, _run_auto_verifier)
    registry.register(_LEGACY_PROFILE, _run_legacy_python_repo_patch_verifier)
    for profile in _KIND_PROFILES:
        registry.register(profile, _kind_guarded_verifier(profile))
    return registry
