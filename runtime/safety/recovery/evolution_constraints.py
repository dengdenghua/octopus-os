from __future__ import annotations

"""Central constraints for evolved prompt artifacts.

Echo has several downstream safety layers (ledger, canary, rollback,
shadow validation). This validator is the early gate: reject obviously unsafe
or operationally expensive prompt mutations before they become proposals.
"""

import re  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402


@dataclass(frozen=True)
class EvolutionConstraintResult:
    passed: bool
    name: str
    message: str
    details: str = ""


@dataclass(slots=True)
class EvolutionConstraintConfig:
    max_prompt_chars: int = 2_000
    max_prompt_growth_ratio: float = 0.35
    disallowed_patterns: tuple[str, ...] = field(
        default_factory=lambda: (
            r"\bignore\s+(all\s+)?previous\s+instructions\b",
            r"\bbypass\s+(approval|permission|sandbox|policy|safety)\b",
            r"\bdisable\s+(approval|permission|sandbox|policy|safety)\b",
            r"\bnever\s+ask\s+for\s+(approval|permission)\b",
            r"\bexfiltrat(e|ion)\b",
            r"\b(api[_-]?key|secret|password|token)\b.*\b(print|dump|expose|send)\b",
        )
    )
    volatile_patterns: tuple[str, ...] = field(
        default_factory=lambda: (
            r"\b(current|today'?s)\s+(date|time|timestamp)\b",
            r"\bnow\(\)",
            r"\brandom\(",
            r"\buuid\b",
        )
    )


class EvolutionConstraintValidator:
    """Validate evolved planner/system-prompt candidates."""

    def __init__(self, config: EvolutionConstraintConfig | None = None) -> None:
        self.config = config or EvolutionConstraintConfig()

    def validate_prompt(
        self,
        prompt: str,
        *,
        baseline_prompt: str | None = None,
    ) -> list[EvolutionConstraintResult]:
        text = str(prompt or "")
        results = [
            self._check_non_empty(text),
            self._check_size(text),
            self._check_disallowed_patterns(text),
            self._check_prompt_cache_stability(text),
        ]
        if baseline_prompt:
            results.append(self._check_growth(text, baseline_prompt))
        return results

    def passed(
        self,
        prompt: str,
        *,
        baseline_prompt: str | None = None,
    ) -> bool:
        return all(
            r.passed
            for r in self.validate_prompt(
                prompt,
                baseline_prompt=baseline_prompt,
            )
        )

    def _check_non_empty(self, text: str) -> EvolutionConstraintResult:
        if text.strip():
            return EvolutionConstraintResult(True, "non_empty", "prompt is non-empty")
        return EvolutionConstraintResult(False, "non_empty", "prompt is empty")

    def _check_size(self, text: str) -> EvolutionConstraintResult:
        size = len(text)
        limit = self.config.max_prompt_chars
        if size <= limit:
            return EvolutionConstraintResult(
                True,
                "size_limit",
                f"prompt size OK: {size}/{limit} chars",
            )
        return EvolutionConstraintResult(
            False,
            "size_limit",
            f"prompt size exceeded: {size}/{limit} chars",
        )

    def _check_growth(self, text: str, baseline: str) -> EvolutionConstraintResult:
        base_len = max(1, len(baseline))
        growth = (len(text) - base_len) / base_len
        limit = self.config.max_prompt_growth_ratio
        if growth <= limit:
            return EvolutionConstraintResult(
                True,
                "growth_limit",
                f"prompt growth OK: {growth:+.1%}",
            )
        return EvolutionConstraintResult(
            False,
            "growth_limit",
            f"prompt growth exceeded: {growth:+.1%} > {limit:+.1%}",
        )

    def _check_disallowed_patterns(self, text: str) -> EvolutionConstraintResult:
        lowered = text.lower()
        for pattern in self.config.disallowed_patterns:
            if re.search(pattern, lowered, flags=re.IGNORECASE):
                return EvolutionConstraintResult(
                    False,
                    "permission_safety",
                    "prompt contains a permission/safety bypass pattern",
                    details=pattern,
                )
        return EvolutionConstraintResult(
            True,
            "permission_safety",
            "no permission/safety bypass pattern detected",
        )

    def _check_prompt_cache_stability(self, text: str) -> EvolutionConstraintResult:
        lowered = text.lower()
        for pattern in self.config.volatile_patterns:
            if re.search(pattern, lowered, flags=re.IGNORECASE):
                return EvolutionConstraintResult(
                    False,
                    "cache_stability",
                    "prompt contains volatile runtime content",
                    details=pattern,
                )
        return EvolutionConstraintResult(
            True,
            "cache_stability",
            "no volatile runtime content detected",
        )


def serialize_constraint_results(
    results: list[EvolutionConstraintResult],
) -> list[dict[str, str | bool]]:
    return [
        {
            "passed": r.passed,
            "name": r.name,
            "message": r.message,
            "details": r.details,
        }
        for r in results
    ]


__all__ = [
    "EvolutionConstraintConfig",
    "EvolutionConstraintResult",
    "EvolutionConstraintValidator",
    "serialize_constraint_results",
]
