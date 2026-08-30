from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from runtime.platform.process.paths import project_root as default_project_root

BUNDLE_SCHEMA = "echo.behavioral_surpass_bundle.v2"
SUITE_SCHEMA = "echo.behavioral_surpass_suite.v1"
REPORT_SCHEMA = "echo.behavioral_surpass_evidence.v1"
SYSTEM_PROVENANCE_SCHEMA = "echo.behavioral_system_provenance.v1"
TRAJECTORY_SCHEMA = "echo.behavioral_trajectory.v2"
CODEX_DESKTOP_EXECUTABLE = "/Applications/ChatGPT.app/Contents/Resources/codex"
REQUIRED_SYSTEMS = ("echo", "codex")
REQUIRED_DOMAINS = (
    "general_runtime_and_coding",
    "frontend_product_experience",
    "browser_desktop_automation",
    "multi_agent_digital_employee",
    "repo_memory_knowledge",
    "security_governance",
    "extensions_ecosystem",
)
ALLOWED_EXECUTION_MODES: dict[str, set[str]] = {
    "general_runtime_and_coding": {"real_provider"},
    "frontend_product_experience": {"live_runtime"},
    "browser_desktop_automation": {"live_runtime"},
    "multi_agent_digital_employee": {"real_provider"},
    "repo_memory_knowledge": {"real_provider", "live_runtime"},
    "security_governance": {"live_runtime", "deterministic_integration"},
    "extensions_ecosystem": {"live_runtime", "deterministic_integration"},
}
DEFAULT_BUNDLE_PATH = "benchmarks/results/behavioral-surpass-latest.json"
DEFAULT_SUITE_MANIFEST_PATH = "benchmarks/behavioral-surpass-suite.json"
DEFAULT_INFRASTRUCTURE_STATUS_PATH = "benchmarks/results/behavioral-infrastructure-latest.json"


def compute_behavioral_surpass_evidence(
    *,
    root: str | Path | None = None,
    bundle_path: str | Path | None = None,
    now: datetime | None = None,
    max_age_days: int = 30,
    min_k: int = 3,
    min_cases_per_domain: int = 2,
    min_pass_pow_k: float = 0.95,
    surpass_margin: float = 0.0,
) -> dict[str, Any]:
    base = Path(root) if root is not None else default_project_root(Path(__file__))
    path = _resolve_bundle_path(base, bundle_path)
    manifest_path = _resolve_manifest_path(base)
    infrastructure_path = _resolve_infrastructure_status_path(base)
    current_time = now or datetime.now(UTC)
    checks: list[dict[str, Any]] = []
    errors: list[str] = []
    bundle = _read_bundle(path, errors)
    manifest = _read_manifest(manifest_path, errors)
    manifest_digest = _file_digest(manifest_path)

    _add_check(
        checks,
        "bundle_present",
        "Behavioral evidence bundle exists",
        path.exists(),
        1 if path.exists() else 0,
        1,
        "Run the same behavioral suite against Echo and Codex.",
    )
    schema_ok = isinstance(bundle, dict) and bundle.get("schema") == BUNDLE_SCHEMA
    _add_check(
        checks,
        "bundle_schema",
        "Behavioral evidence uses the current schema",
        schema_ok,
        1 if schema_ok else 0,
        1,
        f"Write a {BUNDLE_SCHEMA} evidence bundle.",
    )
    manifest_signatures, manifest_valid = _manifest_signatures(manifest, errors)
    manifest_schema_ok = bool(manifest_valid and manifest.get("schema") == SUITE_SCHEMA)
    _add_check(
        checks,
        "suite_manifest",
        "The fixed behavioral suite manifest is valid",
        manifest_schema_ok,
        len(manifest_signatures),
        len(REQUIRED_DOMAINS) * 2,
        f"Restore the version-controlled {SUITE_SCHEMA} manifest.",
    )
    manifest_locked = bool(
        manifest_schema_ok
        and manifest_digest
        and manifest.get("suite_id") == bundle.get("suite_id")
        and bundle.get("suite_manifest_sha256") == manifest_digest
    )
    _add_check(
        checks,
        "suite_manifest_locked",
        "Evidence is bound to the version-controlled suite",
        manifest_locked,
        1 if manifest_locked else 0,
        1,
        "Run the fixed suite and record its exact SHA-256 in the evidence bundle.",
    )

    generated_at = _parse_datetime(bundle.get("generated_at") if isinstance(bundle, dict) else None)
    age_days = None
    fresh = False
    if generated_at is not None:
        age_days = (current_time - generated_at).total_seconds() / 86_400
        fresh = -(5 / 1440) <= age_days <= max_age_days
    _add_check(
        checks,
        "bundle_fresh",
        "Behavioral evidence is fresh",
        fresh,
        round(age_days, 3) if age_days is not None else -1,
        max_age_days,
        f"Regenerate behavioral evidence within {max_age_days} days.",
    )

    metadata_ok = bool(
        isinstance(bundle, dict)
        and str(bundle.get("suite_id") or "").strip()
        and str(bundle.get("runner_version") or "").strip()
        and str(bundle.get("source_revision") or "").strip()
    )
    _add_check(
        checks,
        "bundle_metadata",
        "Suite, runner, and source revision are recorded",
        metadata_ok,
        1 if metadata_ok else 0,
        1,
        "Record suite_id, runner_version, and source_revision.",
    )

    systems = bundle.get("systems") if isinstance(bundle, dict) else None
    system_rows: dict[str, dict[str, Any]] = {}
    for system_id in REQUIRED_SYSTEMS:
        raw_system = systems.get(system_id) if isinstance(systems, dict) else None
        system_rows[system_id] = _validate_system(
            base=base,
            system_id=system_id,
            raw_system=raw_system,
            min_k=min_k,
            min_cases_per_domain=min_cases_per_domain,
            errors=errors,
        )
    systems_present = all(row["present"] for row in system_rows.values())
    _add_check(
        checks,
        "systems_present",
        "Echo and Codex results are both present",
        systems_present,
        sum(1 for row in system_rows.values() if row["present"]),
        len(REQUIRED_SYSTEMS),
        "Run the identical suite on both systems.",
    )

    provenance_valid = all(row["provenance_valid"] for row in system_rows.values())
    _add_check(
        checks,
        "system_provenance",
        "Config, model, and signed Codex identities match the approved policy",
        provenance_valid,
        sum(1 for row in system_rows.values() if row["provenance_valid"]),
        len(REQUIRED_SYSTEMS),
        "Regenerate evidence with the protected behavioral identity policy.",
    )

    case_sets = [set(row["case_ids"]) for row in system_rows.values()]
    comparison_signatures = [row["comparison_signatures"] for row in system_rows.values()]
    same_cases = (
        systems_present
        and bool(case_sets[0])
        and all(signature == comparison_signatures[0] for signature in comparison_signatures[1:])
    )
    _add_check(
        checks,
        "same_cases",
        "Both systems ran the exact same cases",
        same_cases,
        len(case_sets[0] & case_sets[1]) if len(case_sets) == 2 else 0,
        len(case_sets[0] | case_sets[1]) if len(case_sets) == 2 else 0,
        "Use identical case IDs, prompts, and rubrics for Echo and Codex.",
    )
    fixed_suite_cases = bool(
        manifest_locked
        and all(row["comparison_signatures"] == manifest_signatures for row in system_rows.values())
    )
    _add_check(
        checks,
        "fixed_suite_cases",
        "Both systems ran every case in the fixed suite",
        fixed_suite_cases,
        sum(
            1
            for signature in system_rows["echo"]["comparison_signatures"]
            if manifest_signatures.get(signature)
            == system_rows["echo"]["comparison_signatures"].get(signature)
        ),
        len(manifest_signatures),
        "Run every manifest case without cherry-picking or post-run edits.",
    )

    domain_rows = _compare_domains(system_rows, min_cases_per_domain=min_cases_per_domain)
    domains_ready = all(row["ready"] for row in domain_rows)
    _add_check(
        checks,
        "domain_coverage",
        "Every required domain has repeated isolated trials",
        domains_ready,
        sum(1 for row in domain_rows if row["ready"]),
        len(REQUIRED_DOMAINS),
        f"Provide at least {min_cases_per_domain} cases per domain with k >= {min_k}.",
    )

    artifacts_verified = all(row["artifacts_verified"] for row in system_rows.values())
    _add_check(
        checks,
        "artifacts_verified",
        "Every case has digest-verified trajectory artifacts",
        artifacts_verified,
        sum(int(row["verified_artifacts"]) for row in system_rows.values()),
        sum(int(row["expected_artifacts"]) for row in system_rows.values()),
        "Store each trajectory artifact and its SHA-256 digest.",
    )

    methods_valid = all(row["methods_valid"] for row in system_rows.values())
    _add_check(
        checks,
        "methods_valid",
        "Trials use outcome grading, isolated state, and real execution modes",
        methods_valid,
        sum(int(row["valid_cases"]) for row in system_rows.values()),
        sum(int(row["total_cases"]) for row in system_rows.values()),
        "Use outcome graders, isolated trials, and the required live/provider execution mode.",
    )

    echo_score = float(system_rows["echo"]["aggregate_pass_pow_k"])
    codex_score = float(system_rows["codex"]["aggregate_pass_pow_k"])
    echo_clears_floor = echo_score >= min_pass_pow_k
    _add_check(
        checks,
        "echo_reliability_floor",
        "Echo repeated-run reliability clears the floor",
        echo_clears_floor,
        round(echo_score, 4),
        min_pass_pow_k,
        "Fix failing Echo cases and rerun the full suite.",
    )
    head_to_head = same_cases and echo_score >= codex_score + surpass_margin
    _add_check(
        checks,
        "head_to_head",
        "Echo meets or exceeds Codex on the same suite",
        head_to_head,
        round(echo_score - codex_score, 4),
        surpass_margin,
        "Run comparable trials and close the measured Codex gap.",
    )
    no_domain_regressions = bool(domain_rows) and all(
        row["ready"] and row["echo_pass_pow_k"] >= row["codex_pass_pow_k"] for row in domain_rows
    )
    _add_check(
        checks,
        "no_domain_regressions",
        "Echo is not behind Codex in any required domain",
        no_domain_regressions,
        sum(
            1
            for row in domain_rows
            if row["ready"] and row["echo_pass_pow_k"] >= row["codex_pass_pow_k"]
        ),
        len(REQUIRED_DOMAINS),
        "Repair every domain where Echo trails the Codex baseline.",
    )

    expected_revision = os.environ.get("ECHO_BEHAVIORAL_EXPECTED_REVISION", "").strip()
    revision_matches = not expected_revision or (
        isinstance(bundle, dict) and str(bundle.get("source_revision") or "") == expected_revision
    )
    _add_check(
        checks,
        "source_revision",
        "Evidence matches the requested source revision",
        revision_matches,
        1 if revision_matches else 0,
        1,
        "Regenerate evidence for the release revision.",
    )

    ready = bool(checks) and all(bool(check["passed"]) for check in checks)
    infrastructure = _infrastructure_status(
        infrastructure_path,
        current_time=current_time,
        max_age_days=max_age_days,
        active=not ready,
    )
    verdict = (
        "surpassed"
        if ready
        else "infrastructure_blocked"
        if infrastructure["active"]
        else _failure_verdict(checks)
    )
    next_actions = [
        str(check["next_action"])
        for check in checks
        if not check["passed"] and check.get("next_action")
    ]
    if infrastructure["active"]:
        next_actions.insert(
            0,
            "Restore model-provider availability, then resume the unscored behavioral run.",
        )
    return {
        "schema": REPORT_SCHEMA,
        "ready": ready,
        "verdict": verdict,
        "bundle_path": str(path),
        "bundle_exists": path.exists(),
        "suite_manifest_path": str(manifest_path),
        "suite_manifest_sha256": manifest_digest,
        "infrastructure": infrastructure,
        "generated_at": generated_at.isoformat() if generated_at else "",
        "age_days": round(age_days, 3) if age_days is not None else None,
        "max_age_days": max_age_days,
        "min_k": min_k,
        "min_cases_per_domain": min_cases_per_domain,
        "min_pass_pow_k": min_pass_pow_k,
        "surpass_margin": surpass_margin,
        "systems": system_rows,
        "domains": domain_rows,
        "checks": checks,
        "errors": sorted(set(errors)),
        "next_actions": list(dict.fromkeys(next_actions)),
    }


def _resolve_bundle_path(base: Path, bundle_path: str | Path | None) -> Path:
    raw = bundle_path or os.environ.get("ECHO_BEHAVIORAL_EVAL_BUNDLE") or DEFAULT_BUNDLE_PATH
    path = Path(raw)
    return path if path.is_absolute() else base / path


def _resolve_manifest_path(base: Path) -> Path:
    raw = os.environ.get("ECHO_BEHAVIORAL_SUITE_MANIFEST") or DEFAULT_SUITE_MANIFEST_PATH
    path = Path(raw)
    return path if path.is_absolute() else base / path


def _resolve_infrastructure_status_path(base: Path) -> Path:
    raw = (
        os.environ.get("ECHO_BEHAVIORAL_INFRASTRUCTURE_STATUS")
        or DEFAULT_INFRASTRUCTURE_STATUS_PATH
    )
    path = Path(raw)
    return path if path.is_absolute() else base / path


def _infrastructure_status(
    path: Path,
    *,
    current_time: datetime,
    max_age_days: int,
    active: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if path.is_file():
        try:
            candidate = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(candidate, dict):
                payload = candidate
        except (OSError, json.JSONDecodeError):
            payload = {}
    generated_at = _parse_datetime(payload.get("generated_at"))
    age_days = (
        (current_time - generated_at).total_seconds() / 86_400 if generated_at is not None else None
    )
    current = bool(
        payload.get("schema") == "echo.behavioral_infrastructure_failure.v1"
        and payload.get("scored") is False
        and age_days is not None
        and -(5 / 1440) <= age_days <= max_age_days
    )
    failures = payload.get("failures")
    safe_failures = [
        {
            "case_id": str(row.get("case_id") or ""),
            "categories": [str(value) for value in row.get("categories") or []],
        }
        for row in failures or []
        if isinstance(row, dict)
    ]
    return {
        "active": bool(active and current),
        "current": current,
        "path": str(path),
        "generated_at": generated_at.isoformat() if generated_at else "",
        "age_days": round(age_days, 3) if age_days is not None else None,
        "system_id": str(payload.get("system_id") or ""),
        "failures": safe_failures,
    }


def _read_bundle(path: Path, errors: list[str]) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"bundle unreadable: {type(exc).__name__}: {exc}")
        return {}
    if not isinstance(value, dict):
        errors.append("bundle root must be an object")
        return {}
    return value


def _read_manifest(path: Path, errors: list[str]) -> dict[str, Any]:
    if not path.exists():
        errors.append(f"suite manifest missing: {path}")
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"suite manifest unreadable: {type(exc).__name__}: {exc}")
        return {}
    if not isinstance(value, dict):
        errors.append("suite manifest root must be an object")
        return {}
    return value


def _file_digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _manifest_signatures(
    manifest: dict[str, Any],
    errors: list[str],
) -> tuple[dict[str, str], bool]:
    raw_cases = manifest.get("cases")
    cases = raw_cases if isinstance(raw_cases, list) else []
    signatures: dict[str, str] = {}
    domain_counts = {domain: 0 for domain in REQUIRED_DOMAINS}
    valid = bool(cases)
    for index, raw_case in enumerate(cases):
        if not isinstance(raw_case, dict):
            errors.append(f"suite manifest cases[{index}] must be an object")
            valid = False
            continue
        case_id = str(raw_case.get("id") or "").strip()
        domain = str(raw_case.get("domain") or "").strip()
        prompt = raw_case.get("prompt")
        rubric = raw_case.get("rubric")
        execution_mode = str(raw_case.get("execution_mode") or "")
        case_valid = bool(
            case_id
            and case_id not in signatures
            and domain in REQUIRED_DOMAINS
            and isinstance(prompt, str)
            and prompt.strip()
            and isinstance(rubric, dict)
            and rubric
            and execution_mode in ALLOWED_EXECUTION_MODES[domain]
        )
        if not case_valid:
            errors.append(f"suite manifest case is invalid: index={index}, id={case_id!r}")
            valid = False
            continue
        phases = raw_case.get("phases")
        phase_rows = phases if isinstance(phases, list) else []
        if any(not isinstance(phase, str) or not phase.strip() for phase in phase_rows):
            errors.append(f"suite manifest case has invalid phases: {case_id}")
            valid = False
            continue
        prompt_source = (
            json.dumps(
                {"prompt": prompt, "phases": phase_rows},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if phase_rows
            else prompt
        )
        prompt_digest = hashlib.sha256(prompt_source.encode("utf-8")).hexdigest()
        rubric_digest = hashlib.sha256(
            json.dumps(
                rubric,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        signatures[case_id] = f"{domain}:{rubric_digest}:{prompt_digest}"
        domain_counts[domain] += 1
    expected_cases = len(REQUIRED_DOMAINS) * 2
    valid = bool(
        valid
        and len(signatures) == expected_cases
        and all(count == 2 for count in domain_counts.values())
    )
    if not valid:
        errors.append("suite manifest must contain exactly two valid cases per required domain")
    return dict(sorted(signatures.items())), valid


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=UTC)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _validate_system(
    *,
    base: Path,
    system_id: str,
    raw_system: Any,
    min_k: int,
    min_cases_per_domain: int,
    errors: list[str],
) -> dict[str, Any]:
    present = isinstance(raw_system, dict) and bool(str(raw_system.get("version") or "").strip())
    raw_provenance = raw_system.get("provenance") if isinstance(raw_system, dict) else None
    provenance, provenance_digest, provenance_fields_valid = _validate_system_provenance(
        raw_provenance,
        system_id=system_id,
        errors=errors,
    )
    recorded_provenance_digest = (
        str(raw_system.get("provenance_sha256") or "").strip().lower()
        if isinstance(raw_system, dict)
        else ""
    )
    provenance_valid = bool(
        provenance_fields_valid
        and provenance_digest
        and recorded_provenance_digest == provenance_digest
    )
    if provenance_fields_valid and recorded_provenance_digest != provenance_digest:
        errors.append(f"{system_id} system provenance digest mismatch")
    cases = raw_system.get("cases") if isinstance(raw_system, dict) else None
    case_rows = cases if isinstance(cases, list) else []
    valid_cases = 0
    verified_artifacts = 0
    expected_artifacts = 0
    all_case_artifacts_valid = bool(case_rows)
    case_ids: list[str] = []
    comparison_signatures: dict[str, str] = {}
    domain_scores: dict[str, list[float]] = {domain: [] for domain in REQUIRED_DOMAINS}
    for index, raw_case in enumerate(case_rows):
        if not isinstance(raw_case, dict):
            errors.append(f"{system_id}.cases[{index}] must be an object")
            continue
        case_id = str(raw_case.get("id") or "").strip()
        domain = str(raw_case.get("domain") or "").strip()
        if case_id:
            case_ids.append(case_id)
        k = _safe_int(raw_case.get("k"))
        passes = _safe_int(raw_case.get("passes"))
        trajectory_count = _safe_int(raw_case.get("trajectory_count"))
        rubric_digest = str(raw_case.get("rubric_digest") or "").strip().lower()
        prompt_digest = str(raw_case.get("prompt_digest") or "").strip().lower()
        pass_pow_k = 1.0 if k >= min_k and passes == k else 0.0
        allowed_modes = ALLOWED_EXECUTION_MODES.get(domain, set())
        method_valid = bool(
            case_id
            and domain in REQUIRED_DOMAINS
            and k >= min_k
            and 0 <= passes <= k
            and trajectory_count >= k
            and raw_case.get("outcome_grader") is True
            and raw_case.get("isolated_state") is True
            and str(raw_case.get("execution_mode") or "") in allowed_modes
            and len(rubric_digest) == 64
            and all(character in "0123456789abcdef" for character in rubric_digest)
            and len(prompt_digest) == 64
            and all(character in "0123456789abcdef" for character in prompt_digest)
        )
        if method_valid:
            valid_cases += 1
            domain_scores[domain].append(pass_pow_k)
            comparison_signatures[case_id] = f"{domain}:{rubric_digest}:{prompt_digest}"
        artifacts = raw_case.get("artifacts")
        artifact_rows = artifacts if isinstance(artifacts, list) else []
        expected_artifacts += max(k, len(artifact_rows))
        artifact_paths = [
            str(artifact.get("path") or "")
            for artifact in artifact_rows
            if isinstance(artifact, dict)
        ]
        unique_artifacts = len(artifact_paths) == len(set(artifact_paths))
        artifact_outcomes: list[bool] = []
        case_verified = 0
        for trial_index, artifact in enumerate(artifact_rows):
            verified, artifact_passed = _verify_artifact(
                base,
                artifact,
                errors,
                system_id=system_id,
                system_version=(
                    str(raw_system.get("version") or "") if isinstance(raw_system, dict) else ""
                ),
                system_provenance_sha256=provenance_digest,
                case_id=case_id,
                prompt_digest=prompt_digest,
                trial_index=trial_index,
            )
            if verified:
                case_verified += 1
            if artifact_passed is not None:
                artifact_outcomes.append(artifact_passed)
        verified_artifacts += case_verified
        case_artifacts_valid = bool(
            k >= min_k
            and len(artifact_rows) == k
            and unique_artifacts
            and case_verified == k
            and len(artifact_outcomes) == k
            and sum(artifact_outcomes) == passes
        )
        if not unique_artifacts:
            errors.append(f"{system_id}.{case_id} reuses trajectory artifact paths")
        if len(artifact_outcomes) == k and sum(artifact_outcomes) != passes:
            errors.append(f"{system_id}.{case_id} pass count disagrees with trajectory verdicts")
        all_case_artifacts_valid = all_case_artifacts_valid and case_artifacts_valid
    duplicate_case_ids = len(case_ids) != len(set(case_ids))
    if duplicate_case_ids:
        errors.append(f"{system_id} contains duplicate case IDs")
    domain_counts = {domain: len(scores) for domain, scores in domain_scores.items()}
    aggregate_scores = [score for scores in domain_scores.values() for score in scores]
    return {
        "present": present,
        "version": str(raw_system.get("version") or "") if isinstance(raw_system, dict) else "",
        "provenance": provenance,
        "provenance_sha256": recorded_provenance_digest,
        "provenance_valid": provenance_valid,
        "total_cases": len(case_rows),
        "valid_cases": valid_cases,
        "methods_valid": bool(case_rows)
        and valid_cases == len(case_rows)
        and not duplicate_case_ids,
        "case_ids": sorted(set(case_ids)),
        "comparison_signatures": dict(sorted(comparison_signatures.items())),
        "domain_case_counts": domain_counts,
        "domains_ready": all(count >= min_cases_per_domain for count in domain_counts.values()),
        "domain_pass_pow_k": {
            domain: round(sum(scores) / len(scores), 4) if scores else 0.0
            for domain, scores in domain_scores.items()
        },
        "aggregate_pass_pow_k": (
            round(sum(aggregate_scores) / len(aggregate_scores), 4) if aggregate_scores else 0.0
        ),
        "verified_artifacts": verified_artifacts,
        "expected_artifacts": expected_artifacts,
        "artifacts_verified": all_case_artifacts_valid
        and expected_artifacts > 0
        and verified_artifacts == expected_artifacts,
    }


def _verify_artifact(
    base: Path,
    raw_artifact: Any,
    errors: list[str],
    *,
    system_id: str,
    system_version: str,
    system_provenance_sha256: str,
    case_id: str,
    prompt_digest: str,
    trial_index: int,
) -> tuple[bool, bool | None]:
    if not isinstance(raw_artifact, dict):
        return False, None
    relative = str(raw_artifact.get("path") or "").strip()
    expected = str(raw_artifact.get("sha256") or "").strip().lower()
    if not relative or len(expected) != 64:
        return False, None
    path = (base / relative).resolve()
    try:
        path.relative_to(base.resolve())
    except ValueError:
        errors.append(f"artifact escapes repository root: {relative}")
        return False, None
    try:
        content = path.read_bytes()
    except OSError:
        return False, None
    actual = hashlib.sha256(content).hexdigest()
    if actual != expected:
        errors.append(f"artifact digest mismatch: {relative}")
        return False, None
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError):
        errors.append(f"artifact is not trajectory JSON: {relative}")
        return False, None
    trajectory = payload.get("trajectory") if isinstance(payload, dict) else None
    verdict = payload.get("verdict") if isinstance(payload, dict) else None
    semantic_match = bool(
        isinstance(payload, dict)
        and payload.get("schema") == TRAJECTORY_SCHEMA
        and payload.get("system_id") == system_id
        and payload.get("system_version") == system_version
        and payload.get("system_provenance_sha256") == system_provenance_sha256
        and payload.get("case_id") == case_id
        and payload.get("trial_index") == trial_index
        and payload.get("prompt_sha256") == prompt_digest
        and isinstance(trajectory, dict)
        and trajectory.get("case_id") == case_id
        and isinstance(trajectory.get("steps"), list)
        and isinstance(verdict, dict)
        and isinstance(verdict.get("passed"), bool)
    )
    if not semantic_match:
        errors.append(f"artifact trajectory metadata mismatch: {relative}")
        return False, None
    return True, bool(verdict["passed"])


def validate_behavioral_system_provenance(
    raw: Any,
    *,
    system_id: str,
) -> dict[str, Any]:
    """Validate and normalize one system's release-evidence identity policy."""

    errors: list[str] = []
    normalized, _digest, valid = _validate_system_provenance(
        raw,
        system_id=system_id,
        errors=errors,
    )
    if not valid:
        detail = "; ".join(errors) or f"invalid {system_id} behavioral provenance"
        raise ValueError(detail)
    return normalized


def behavioral_system_provenance_digest(provenance: dict[str, Any]) -> str:
    """Return the canonical digest embedded in every trajectory artifact."""

    serialized = json.dumps(
        provenance,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _validate_system_provenance(
    raw: Any,
    *,
    system_id: str,
    errors: list[str],
) -> tuple[dict[str, Any], str, bool]:
    if not isinstance(raw, dict):
        errors.append(f"{system_id} system provenance is missing")
        return {}, "", False

    model = raw.get("model")
    model = model if isinstance(model, dict) else {}
    expected_model = str(model.get("expected") or "").strip()
    requested_model = str(model.get("requested") or "").strip()
    base_valid = bool(
        raw.get("schema") == SYSTEM_PROVENANCE_SCHEMA
        and raw.get("system_id") == system_id
        and expected_model
        and requested_model == expected_model
    )
    normalized: dict[str, Any] = {
        "schema": SYSTEM_PROVENANCE_SCHEMA,
        "system_id": system_id,
        "model": {
            "expected": expected_model,
            "requested": requested_model,
        },
    }

    if system_id == "echo":
        config = raw.get("config")
        config = config if isinstance(config, dict) else {}
        expected_sha256 = str(config.get("expected_sha256") or "").strip().lower()
        observed_sha256 = str(config.get("observed_sha256") or "").strip().lower()
        normalized["config"] = {
            "expected_sha256": expected_sha256,
            "observed_sha256": observed_sha256,
        }
        identity_valid = bool(_is_sha256(expected_sha256) and observed_sha256 == expected_sha256)
    elif system_id == "codex":
        executable = raw.get("executable")
        executable = executable if isinstance(executable, dict) else {}
        codesign = executable.get("codesign")
        codesign = codesign if isinstance(codesign, dict) else {}
        path = str(executable.get("path") or "").strip()
        expected_sha256 = str(executable.get("expected_sha256") or "").strip().lower()
        observed_sha256 = str(executable.get("observed_sha256") or "").strip().lower()
        expected_team = str(codesign.get("expected_team_identifier") or "").strip()
        observed_team = str(codesign.get("observed_team_identifier") or "").strip()
        expected_identifier = str(codesign.get("expected_identifier") or "").strip()
        observed_identifier = str(codesign.get("observed_identifier") or "").strip()
        normalized["executable"] = {
            "path": path,
            "expected_sha256": expected_sha256,
            "observed_sha256": observed_sha256,
            "codesign": {
                "expected_team_identifier": expected_team,
                "observed_team_identifier": observed_team,
                "expected_identifier": expected_identifier,
                "observed_identifier": observed_identifier,
            },
        }
        identity_valid = bool(
            path == CODEX_DESKTOP_EXECUTABLE
            and _is_sha256(expected_sha256)
            and observed_sha256 == expected_sha256
            and expected_team
            and observed_team == expected_team
            and expected_identifier
            and observed_identifier == expected_identifier
        )
    else:
        errors.append(f"unsupported behavioral provenance system: {system_id}")
        return normalized, "", False

    valid = bool(base_valid and identity_valid)
    if not valid:
        errors.append(f"{system_id} system provenance does not match the approved identity")
        return normalized, "", False
    return normalized, behavioral_system_provenance_digest(normalized), True


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _compare_domains(
    systems: dict[str, dict[str, Any]],
    *,
    min_cases_per_domain: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for domain in REQUIRED_DOMAINS:
        echo_count = int(systems["echo"]["domain_case_counts"].get(domain, 0))
        codex_count = int(systems["codex"]["domain_case_counts"].get(domain, 0))
        rows.append(
            {
                "id": domain,
                "ready": (
                    echo_count >= min_cases_per_domain and codex_count >= min_cases_per_domain
                ),
                "echo_cases": echo_count,
                "codex_cases": codex_count,
                "echo_pass_pow_k": float(systems["echo"]["domain_pass_pow_k"].get(domain, 0.0)),
                "codex_pass_pow_k": float(systems["codex"]["domain_pass_pow_k"].get(domain, 0.0)),
            }
        )
    return rows


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _add_check(
    checks: list[dict[str, Any]],
    check_id: str,
    title: str,
    passed: bool,
    score: Any,
    target: Any,
    next_action: str,
) -> None:
    checks.append(
        {
            "id": check_id,
            "title": title,
            "passed": bool(passed),
            "score": score,
            "target": target,
            "next_action": next_action,
        }
    )


def _failure_verdict(checks: list[dict[str, Any]]) -> str:
    failed = {str(check["id"]) for check in checks if not check["passed"]}
    if "bundle_present" in failed:
        return "missing_behavioral_evidence"
    if "bundle_fresh" in failed:
        return "stale_behavioral_evidence"
    if failed & {"head_to_head", "no_domain_regressions", "echo_reliability_floor"}:
        return "behavioral_gap"
    return "invalid_behavioral_evidence"


__all__ = [
    "ALLOWED_EXECUTION_MODES",
    "BUNDLE_SCHEMA",
    "CODEX_DESKTOP_EXECUTABLE",
    "DEFAULT_BUNDLE_PATH",
    "DEFAULT_SUITE_MANIFEST_PATH",
    "REPORT_SCHEMA",
    "REQUIRED_DOMAINS",
    "REQUIRED_SYSTEMS",
    "SUITE_SCHEMA",
    "SYSTEM_PROVENANCE_SCHEMA",
    "TRAJECTORY_SCHEMA",
    "behavioral_system_provenance_digest",
    "compute_behavioral_surpass_evidence",
    "validate_behavioral_system_provenance",
]
