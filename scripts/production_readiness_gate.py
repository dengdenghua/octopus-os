#!/usr/bin/env python3
"""Fail fast when release-critical quality signals regress.

This gate intentionally reuses the runtime scorecards instead of duplicating
their evidence rules. It is meant for local and CI verification, so failures
print the exact degraded signal and its next action.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from runtime.safety.evolution.agent_competitor_scorecard import (
    SCORECARD_CALIBRATION_AS_OF,
    SCORECARD_CALIBRATION_MAX_AGE_DAYS,
    SCORECARD_CALIBRATION_SOURCE_REVISION,
    compute_agent_competitor_scorecard,
)
from runtime.safety.evolution.agent_loop_quality import compute_agent_loop_quality
from runtime.safety.evolution.automation_radar import compute_automation_radar
from runtime.safety.evolution.browser_desktop_quality import (
    compute_browser_desktop_quality,
)
from runtime.safety.evolution.digital_employee_quality import (
    compute_digital_employee_quality,
)
from runtime.safety.evolution.e2e_surpass_certification import (
    compute_e2e_surpass_certification,
)
from runtime.safety.evolution.ecosystem_readiness import compute_ecosystem_readiness
from runtime.safety.evolution.permission_sandbox_quality import (
    compute_permission_sandbox_quality,
)
from runtime.safety.evolution.product_experience_quality import (
    compute_product_experience_quality,
)
from runtime.safety.evolution.repo_context_quality import (
    compute_repo_context_quality,
)

MIN_SCORE = 95


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check Echo production readiness quality scorecards.",
    )
    parser.add_argument(
        "--min-score",
        type=int,
        default=MIN_SCORE,
        help=f"Minimum Echo score for release-critical radars. Default: {MIN_SCORE}.",
    )
    parser.add_argument(
        "--review-queue-path",
        type=Path,
        default=None,
        help=(
            "Review queue to use for browser/desktop replay debt checks. "
            "Defaults to the active ECHO_DATA_DIR/ECHO_HOME runtime queue."
        ),
    )
    parser.add_argument(
        "--behavioral-bundle-path",
        type=Path,
        default=None,
        help=(
            "Digest-verified Echo/Codex same-task evidence bundle. "
            "Defaults to ECHO_BEHAVIORAL_EVAL_BUNDLE or the repository result path."
        ),
    )
    parser.add_argument(
        "--static-only",
        action="store_true",
        help=(
            "Run only deterministic scorecard, coverage, and quality checks. "
            "This mode is explicitly not release proof."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a machine-readable readiness report instead of text.",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        default=None,
        help="Write the machine-readable readiness report to this path.",
    )
    args = parser.parse_args(argv)

    result = run_gate(
        min_score=args.min_score,
        review_queue_path=args.review_queue_path,
        behavioral_bundle_path=args.behavioral_bundle_path,
        static_only=args.static_only,
    )
    report = result.to_dict()
    if args.json_output is not None:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(report, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
    if args.json:
        print(json.dumps(report, sort_keys=True))
        return 1 if result.failures else 0

    if result.failures:
        print("production readiness gate failed:", file=sys.stderr)
        for failure in result.failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    if result.static_only:
        prefix = "static-only readiness checks passed (NON-RELEASE PROOF)"
    elif result.release_proof:
        prefix = "commit-bound production readiness gate passed"
    else:
        prefix = (
            "production readiness gate passed (NOT COMMIT-BOUND RELEASE PROOF; "
            "set ECHO_BEHAVIORAL_EXPECTED_REVISION)"
        )
    print(
        f"{prefix}: scorecard={result.scorecard_score}, "
        f"automation={result.automation_score}, e2e={result.e2e_verdict}, "
        f"{result.e2e_summary_text}, quality={result.quality_summary}",
    )
    return 0


class GateResult:
    def __init__(
        self,
        *,
        failures: list[str],
        scorecard_score: int,
        scorecard_evidence_adjusted_score: int,
        scorecard_calibration: dict[str, Any],
        automation_score: int,
        e2e_ready: bool,
        e2e_verdict: str,
        e2e_summary: dict[str, Any],
        e2e_coverage: dict[str, Any],
        e2e_behavioral: dict[str, Any],
        e2e_failed_checks: list[str],
        quality_summary: str,
        static_only: bool,
        expected_revision: str,
    ) -> None:
        self.failures = failures
        self.scorecard_score = scorecard_score
        self.scorecard_evidence_adjusted_score = scorecard_evidence_adjusted_score
        self.scorecard_calibration = scorecard_calibration
        self.automation_score = automation_score
        self.e2e_ready = e2e_ready
        self.e2e_verdict = e2e_verdict
        self.e2e_summary = e2e_summary
        self.e2e_coverage = e2e_coverage
        self.e2e_behavioral = e2e_behavioral
        self.e2e_failed_checks = e2e_failed_checks
        self.quality_summary = quality_summary
        self.static_only = static_only
        self.expected_revision = expected_revision

    @property
    def gate_passed(self) -> bool:
        return not self.failures

    @property
    def release_proof(self) -> bool:
        return bool(
            self.gate_passed
            and not self.static_only
            and self.expected_revision
            and self.e2e_ready
            and self.e2e_behavioral.get("ready") is True
        )

    @property
    def e2e_summary_text(self) -> str:
        return (
            f"e2e_scorecard={_nested_int(self.e2e_summary, 'scorecard_echo')}, "
            f"e2e_best_external={_nested_int(self.e2e_summary, 'scorecard_best_external')}, "
            f"e2e_automation={_nested_int(self.e2e_summary, 'automation_echo')}, "
            f"e2e_coverage={_nested_int(self.e2e_summary, 'coverage_ready')}/"
            f"{_nested_int(self.e2e_summary, 'coverage_total')}, "
            f"e2e_quality={_nested_int(self.e2e_summary, 'quality_ready')}/"
            f"{_nested_int(self.e2e_summary, 'quality_total')}, "
            f"e2e_behavioral={'ready' if self.e2e_behavioral.get('ready') else 'missing'}"
        )

    def to_dict(self) -> dict[str, Any]:
        mode = "static_only" if self.static_only else "full"
        if self.static_only:
            proof_scope = "static_only_non_release"
            notice = (
                "NON-RELEASE PROOF: behavioral head-to-head evidence is reported "
                "but does not block this static-only gate."
            )
        elif self.release_proof:
            proof_scope = "commit_bound_release"
            notice = "Commit-bound release proof for the expected source revision."
        else:
            proof_scope = "full_gate_not_commit_bound_or_failed"
            notice = (
                "Not commit-bound release proof unless the full gate passes with "
                "ECHO_BEHAVIORAL_EXPECTED_REVISION set."
            )
        return {
            "schema": "echo.production_readiness_gate.v1",
            "mode": mode,
            "gate_passed": self.gate_passed,
            "ready": self.gate_passed and not self.static_only,
            "release_proof": self.release_proof,
            "proof_scope": proof_scope,
            "notice": notice,
            "expected_revision": self.expected_revision,
            "failures": list(self.failures),
            "scorecard_score": self.scorecard_score,
            "scorecard_evidence_adjusted_score": (self.scorecard_evidence_adjusted_score),
            "scorecard_calibration": dict(self.scorecard_calibration),
            "automation_score": self.automation_score,
            "e2e": {
                "ready": self.e2e_ready,
                "verdict": self.e2e_verdict,
                "summary": dict(self.e2e_summary),
                "coverage": dict(self.e2e_coverage),
                "behavioral": dict(self.e2e_behavioral),
                "failed_checks": list(self.e2e_failed_checks),
            },
            "quality_summary": self.quality_summary,
        }


def run_gate(
    *,
    min_score: int = MIN_SCORE,
    review_queue_path: str | Path | None = None,
    behavioral_bundle_path: str | Path | None = None,
    static_only: bool = False,
) -> GateResult:
    failures: list[str] = []
    expected_revision = os.environ.get("ECHO_BEHAVIORAL_EXPECTED_REVISION", "").strip()

    scorecard = compute_agent_competitor_scorecard(target_score=min_score)
    automation = compute_automation_radar(
        target_score=min_score,
        review_queue_path=review_queue_path,
    )
    e2e_certification = compute_e2e_surpass_certification(
        target_score=min_score,
        review_queue_path=review_queue_path,
        behavioral_bundle_path=behavioral_bundle_path,
    )
    quality_reports = [
        compute_repo_context_quality(),
        compute_permission_sandbox_quality(),
        compute_product_experience_quality(),
        compute_agent_loop_quality(),
        compute_digital_employee_quality(),
        compute_browser_desktop_quality(review_queue_path=review_queue_path),
        compute_ecosystem_readiness(),
    ]

    scorecard_score = _nested_int(scorecard, "overall", "echo")
    scorecard_evidence_adjusted_score = _nested_int(
        scorecard,
        "evidence_adjusted_overall",
        "echo",
    )
    automation_score = _nested_int(automation, "overall", "echo")
    e2e_summary = dict(e2e_certification.get("summary") or {})
    e2e_coverage = dict(e2e_certification.get("coverage") or {})
    e2e_behavioral = dict(e2e_certification.get("behavioral") or {})
    e2e_failed_checks = _failed_check_ids(e2e_certification.get("checks"))
    quality_ready = sum(1 for report in quality_reports if bool(report.get("ready")))
    quality_total = len(quality_reports)
    scorecard_surpass_summary = scorecard.get("surpass_summary")
    if not isinstance(scorecard_surpass_summary, Mapping):
        scorecard_surpass_summary = {}

    scorecard_calibration = _require_current_scorecard_calibration(
        failures,
        scorecard.get("baseline_context"),
    )
    _require_ready(
        failures,
        "agent competitor scorecard parity certification",
        scorecard.get("parity_certification"),
    )
    _require_ready(
        failures,
        "ecosystem readiness",
        scorecard.get("ecosystem_readiness"),
    )
    _require_min_score(
        failures,
        "agent scorecard echo evidence-adjusted overall",
        _nested_int(scorecard, "evidence_adjusted_overall", "echo"),
        min_score,
    )
    _require_min_score(
        failures,
        "automation radar echo overall",
        _nested_int(automation, "overall", "echo"),
        min_score,
    )
    _require_ready(
        failures,
        "automation radar browser/desktop quality",
        automation.get("browser_desktop_quality"),
    )
    browser_desktop_quality = _quality_report(
        quality_reports,
        "echo.browser_desktop_quality.v1",
    )
    _require_browser_desktop_replay_trends(
        failures,
        browser_desktop_quality,
    )
    _require_ready(
        failures,
        "automation radar parity certification",
        automation.get("parity_certification"),
    )
    _require_no_evidence_gaps(
        failures,
        "automation radar evidence gaps",
        automation.get("echo_gaps"),
    )
    if not static_only:
        _require_ready(
            failures,
            "e2e surpass certification",
            e2e_certification,
        )
        _require_ready(
            failures,
            "behavioral surpass evidence",
            e2e_behavioral,
        )
    _require_no_failed_checks(
        failures,
        "e2e surpass certification checks",
        _gate_checks(
            e2e_certification.get("checks"),
            static_only=static_only,
        ),
    )
    expected_summary: dict[str, Any] = {
        "scorecard_echo": scorecard_score,
        "scorecard_best_external": _best_external_score(scorecard),
        "scorecard_evidence_adjusted_echo": scorecard_evidence_adjusted_score,
        "automation_echo": automation_score,
        "automation_codex": _nested_int(automation, "overall", "codex"),
        "coverage_ready": _nested_int(
            e2e_coverage,
            "summary",
            "ready_domains",
        ),
        "coverage_total": _nested_int(
            e2e_coverage,
            "summary",
            "total_domains",
        ),
        "coverage_gap_domains": _nested_int(
            e2e_coverage,
            "summary",
            "gap_domains",
        ),
        "quality_ready": quality_ready,
        "quality_total": quality_total,
        "all_dimensions_surpassed": bool(
            scorecard_surpass_summary.get("all_dimensions_surpassed"),
        ),
        "scorecard_gap_dimensions": int(
            scorecard_surpass_summary.get("gap_dimensions") or 0,
        ),
        "automation_gap_dimensions": len(automation.get("echo_gaps") or []),
    }
    if not static_only:
        expected_summary.update(
            {
                "behavioral_ready": bool(e2e_behavioral.get("ready")),
                "behavioral_echo_pass_pow_k": _nested_float(
                    e2e_behavioral,
                    "systems",
                    "echo",
                    "aggregate_pass_pow_k",
                ),
                "behavioral_codex_pass_pow_k": _nested_float(
                    e2e_behavioral,
                    "systems",
                    "codex",
                    "aggregate_pass_pow_k",
                ),
            }
        )
    _require_e2e_summary_consistency(
        failures,
        e2e_summary,
        expected_summary,
    )

    for report in quality_reports:
        schema = str(report.get("schema") or "quality report")
        _require_ready(failures, schema, report)
        _require_score(
            failures,
            schema,
            report.get("score"),
            expected=1.0,
        )

    quality_summary = ", ".join(
        f"{report.get('schema')}={report.get('passed')}/{report.get('total')}"
        for report in quality_reports
    )
    return GateResult(
        failures=failures,
        scorecard_score=scorecard_score,
        scorecard_evidence_adjusted_score=scorecard_evidence_adjusted_score,
        scorecard_calibration=scorecard_calibration,
        automation_score=automation_score,
        e2e_ready=bool(e2e_certification.get("ready")),
        e2e_verdict=str(e2e_certification.get("verdict") or "unknown"),
        e2e_summary=e2e_summary,
        e2e_coverage=e2e_coverage,
        e2e_behavioral=e2e_behavioral,
        e2e_failed_checks=e2e_failed_checks,
        quality_summary=quality_summary,
        static_only=static_only,
        expected_revision=expected_revision,
    )


def _require_ready(
    failures: list[str],
    label: str,
    report: Any,
) -> None:
    if isinstance(report, Mapping) and report.get("ready") is True:
        return
    next_actions = []
    if isinstance(report, Mapping):
        next_actions = [str(item) for item in report.get("next_actions") or []]
    suffix = f" next_actions={next_actions}" if next_actions else ""
    failures.append(f"{label} is not ready{suffix}")


def _utc_today() -> date:
    return datetime.now(UTC).date()


def _require_current_scorecard_calibration(
    failures: list[str],
    context: Any,
) -> dict[str, Any]:
    evaluated_on = _utc_today()
    status: dict[str, Any] = {
        "schema": "echo.scorecard_calibration_status.v1",
        "ready": False,
        "evaluated_on": evaluated_on.isoformat(),
        "age_days": None,
        "context": dict(context) if isinstance(context, Mapping) else {},
    }
    if not isinstance(context, Mapping):
        failures.append("agent scorecard calibration metadata is unavailable")
        return status

    expected = {
        "as_of": SCORECARD_CALIBRATION_AS_OF,
        "source": "git_commit",
        "source_revision": SCORECARD_CALIBRATION_SOURCE_REVISION,
        "max_age_days": SCORECARD_CALIBRATION_MAX_AGE_DAYS,
    }
    mismatches = [
        f"{field}={context.get(field)!r} expected {expected_value!r}"
        for field, expected_value in expected.items()
        if context.get(field) != expected_value
    ]
    if mismatches:
        failures.append(
            "agent scorecard calibration metadata does not match the version-controlled "
            f"policy: {', '.join(mismatches)}",
        )
        return status

    try:
        calibrated_on = date.fromisoformat(SCORECARD_CALIBRATION_AS_OF)
    except ValueError:
        failures.append("agent scorecard calibration date is invalid")
        return status
    age_days = (evaluated_on - calibrated_on).days
    status["age_days"] = age_days
    if age_days < 0:
        failures.append(
            "agent scorecard calibration date is in the future: "
            f"as_of={SCORECARD_CALIBRATION_AS_OF}",
        )
        return status
    if age_days > SCORECARD_CALIBRATION_MAX_AGE_DAYS:
        failures.append(
            "agent scorecard calibration is stale: "
            f"as_of={SCORECARD_CALIBRATION_AS_OF}, age_days={age_days}, "
            f"max_age_days={SCORECARD_CALIBRATION_MAX_AGE_DAYS}; "
            "recalibrate the external architecture baseline before release",
        )
        return status
    status["ready"] = True
    return status


def _require_min_score(
    failures: list[str],
    label: str,
    score: int,
    minimum: int,
) -> None:
    if score >= minimum:
        return
    failures.append(f"{label} is {score}, below {minimum}")


def _require_score(
    failures: list[str],
    label: str,
    score: Any,
    *,
    expected: float,
) -> None:
    if isinstance(score, int | float) and float(score) >= expected:
        return
    failures.append(f"{label} score is {score!r}, expected >= {expected}")


def _require_no_rows(
    failures: list[str],
    label: str,
    rows: Any,
) -> None:
    if not rows:
        return
    failures.append(f"{label}: {_row_ids(rows)}")


def _require_no_evidence_gaps(
    failures: list[str],
    label: str,
    rows: Any,
) -> None:
    if not isinstance(rows, Sequence) or isinstance(rows, str):
        return
    blocking = [
        row for row in rows if isinstance(row, Mapping) and (row.get("evidence_ready") is not True)
    ]
    if blocking:
        failures.append(f"{label}: {_row_ids(blocking)}")


def _require_no_failed_checks(
    failures: list[str],
    label: str,
    rows: Any,
) -> None:
    if not isinstance(rows, Sequence) or isinstance(rows, str):
        failures.append(f"{label} are unavailable")
        return
    failed = [row for row in rows if isinstance(row, Mapping) and row.get("passed") is not True]
    if failed:
        failures.append(f"{label}: {_row_ids(failed)}")


def _gate_checks(rows: Any, *, static_only: bool) -> Any:
    if not static_only or not isinstance(rows, Sequence) or isinstance(rows, str):
        return rows
    return [
        row
        for row in rows
        if not (isinstance(row, Mapping) and str(row.get("id") or "").startswith("behavioral:"))
    ]


def _require_e2e_summary_consistency(
    failures: list[str],
    summary: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> None:
    for key, expected_value in expected.items():
        actual_value = summary.get(key)
        if actual_value == expected_value:
            continue
        failures.append(
            f"e2e summary mismatch: {key}={actual_value!r}, expected {expected_value!r}",
        )


def _require_browser_desktop_replay_trends(
    failures: list[str],
    report: Mapping[str, Any] | None,
) -> None:
    if not isinstance(report, Mapping):
        failures.append("browser/desktop replay trends are unavailable")
        return
    trends = report.get("replay_trends")
    if not isinstance(trends, Mapping):
        failures.append("browser/desktop replay trends are unavailable")
        return
    stale_count = int(trends.get("stale_source_artifact_count") or 0)
    if stale_count:
        failures.append(
            "browser/desktop replay stale source artifacts: "
            f"{stale_count}; reject or regenerate before release",
        )
    recipe_summary = trends.get("repair_recipe_summary")
    if isinstance(recipe_summary, Mapping):
        pending_cases = int(recipe_summary.get("total_pending_cases") or 0)
        recipe_count = int(recipe_summary.get("recipe_count") or 0)
        if pending_cases or recipe_count:
            failures.append(
                "browser/desktop replay repair recipes pending: "
                f"cases={pending_cases}, recipes={recipe_count}",
            )


def _quality_report(
    reports: Sequence[Mapping[str, Any]],
    schema: str,
) -> Mapping[str, Any] | None:
    for report in reports:
        if isinstance(report, Mapping) and report.get("schema") == schema:
            return report
    return None


def _nested_int(report: Mapping[str, Any], *keys: str) -> int:
    value: Any = report
    for key in keys:
        if not isinstance(value, Mapping):
            return 0
        value = value.get(key)
    return int(value or 0)


def _nested_float(report: Mapping[str, Any], *keys: str) -> float:
    value: Any = report
    for key in keys:
        if not isinstance(value, Mapping):
            return 0.0
        value = value.get(key)
    return float(value or 0.0)


def _best_external_score(scorecard: Mapping[str, Any]) -> int:
    overall = scorecard.get("overall")
    external = scorecard.get("external_competitors")
    if not isinstance(overall, Mapping) or not isinstance(external, Sequence):
        return 0
    return max(
        (
            int(overall.get(str(competitor)) or 0)
            for competitor in external
            if isinstance(competitor, str)
        ),
        default=0,
    )


def _failed_check_ids(rows: Any) -> list[str]:
    if not isinstance(rows, Sequence) or isinstance(rows, str):
        return ["unavailable"]
    failed: list[str] = []
    for row in rows:
        if isinstance(row, Mapping) and row.get("passed") is not True:
            failed.append(str(row.get("id") or row.get("title") or row))
    return failed


def _row_ids(rows: Any) -> str:
    if not isinstance(rows, Sequence) or isinstance(rows, str):
        return repr(rows)
    labels = []
    for row in rows:
        if isinstance(row, Mapping):
            labels.append(str(row.get("id") or row.get("title") or row))
        else:
            labels.append(str(row))
    return ", ".join(labels)


if __name__ == "__main__":
    raise SystemExit(main())


