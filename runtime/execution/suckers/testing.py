from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SkillTestTier = Literal["golden", "regression", "synthesized"]

TIER_THRESHOLDS: dict[SkillTestTier, float] = {
    "golden": 1.00,
    "regression": 0.95,
    "synthesized": 0.80,
}


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


class SkillExpect(BaseModel):
    model_config = ConfigDict(frozen=True)

    output_equals: Any | None = None
    output_contains: list[str] | None = None  # Implementation note.
    schema_keys: list[str] | None = None  # Implementation note.
    raises: str | None = None  # Implementation note.
    no_exception: bool = False
    max_latency_ms: float | None = None


class SkillTestCase(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    name: str = Field(..., min_length=1)
    tier: SkillTestTier = "golden"
    args: dict[str, Any] = Field(default_factory=dict)
    expect: SkillExpect = Field(default_factory=SkillExpect)
    custom_predicate: Callable[[Any], bool] | None = None  # Implementation note.


class SkillTestResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_name: str
    tier: SkillTestTier
    passed: bool
    reason: str = ""
    latency_ms: float = 0.0


class SkillTestReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    skill_name: str
    results: list[SkillTestResult] = Field(default_factory=list)
    tier_pass_rates: dict[SkillTestTier, float] = Field(default_factory=dict)
    tier_counts: dict[SkillTestTier, int] = Field(default_factory=dict)
    overall_passed: bool = False

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed(self) -> list[SkillTestResult]:
        return [r for r in self.results if not r.passed]


class SkillTestsFailed(RuntimeError):
    def __init__(self, skill_name: str, report: SkillTestReport) -> None:
        self.report = report
        fails = "; ".join(f"{r.case_name}:{r.reason}" for r in report.failed)
        super().__init__(
            f"skill {skill_name!r} failed tests "
            f"({report.passed_count}/{report.total} passed): {fails}"
        )


# ═══════════════════════════════════════════════════════════
# Tester
# ═══════════════════════════════════════════════════════════


class SkillTester:
    def run(self, skill: Any) -> SkillTestReport:
        results: list[SkillTestResult] = []
        for case in skill.tests:
            results.append(self._run_case(skill.handler, case))

        per_tier: dict[SkillTestTier, list[bool]] = {}
        for r in results:
            per_tier.setdefault(r.tier, []).append(r.passed)

        tier_rates: dict[SkillTestTier, float] = {
            tier: sum(bs) / len(bs) for tier, bs in per_tier.items()
        }
        tier_counts: dict[SkillTestTier, int] = {tier: len(bs) for tier, bs in per_tier.items()}

        overall = True
        for tier, rate in tier_rates.items():
            if rate < TIER_THRESHOLDS[tier]:
                overall = False
                break

        return SkillTestReport(
            skill_name=skill.name,
            results=results,
            tier_pass_rates=tier_rates,
            tier_counts=tier_counts,
            overall_passed=overall,
        )

    def verify(self, skill: Any) -> SkillTestReport:
        report = self.run(skill)
        if not report.overall_passed:
            raise SkillTestsFailed(skill.name, report)
        return report

    def _run_case(
        self,
        handler: Callable[..., Any],
        case: SkillTestCase,
    ) -> SkillTestResult:
        t0 = time.monotonic()
        output: Any = None
        exc_type: str | None = None
        try:
            output = handler(**case.args)
        except Exception as e:  # Implementation note.
            exc_type = type(e).__name__

        latency_ms = (time.monotonic() - t0) * 1000

        ok, reason = self._check_expect(case.expect, output, exc_type, latency_ms)
        if ok and case.custom_predicate is not None:
            try:
                if not case.custom_predicate(output):
                    ok, reason = False, "custom_predicate=False"
            except Exception as e:  # noqa: BLE001
                ok, reason = False, f"custom_predicate raised {type(e).__name__}"

        return SkillTestResult(
            case_name=case.name,
            tier=case.tier,
            passed=ok,
            reason=reason,
            latency_ms=latency_ms,
        )

    @staticmethod
    def _check_expect(
        expect: SkillExpect,
        output: Any,
        exc_type: str | None,
        latency_ms: float,
    ) -> tuple[bool, str]:
        if expect.raises is not None:
            if exc_type is None:
                return False, f"expected {expect.raises} but no exception"
            if exc_type != expect.raises:
                return False, f"expected {expect.raises}, got {exc_type}"
            return True, ""

        if exc_type is not None:
            return False, f"unexpected exception: {exc_type}"

        if expect.no_exception and exc_type is not None:
            return False, f"got exception {exc_type}"

        if expect.output_equals is not None and output != expect.output_equals:
            return False, f"output != expected (got {output!r})"

        if expect.output_contains:
            text = str(output)
            for sub in expect.output_contains:
                if sub not in text:
                    return False, f"missing {sub!r} in output"

        if expect.schema_keys:
            if not isinstance(output, dict):
                return False, f"output not dict (got {type(output).__name__})"
            missing = [k for k in expect.schema_keys if k not in output]
            if missing:
                return False, f"missing keys: {missing}"

        if expect.max_latency_ms is not None and latency_ms > expect.max_latency_ms:
            return False, f"too slow: {latency_ms:.1f}ms > {expect.max_latency_ms}ms"

        return True, ""
