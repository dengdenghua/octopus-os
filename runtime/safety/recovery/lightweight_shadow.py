from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from runtime.safety.recovery.skill_forge import (
    ForgeConfig,
    ForgedSkillCandidate,
)

_LOG = logging.getLogger("echo.regeneration.lightweight_shadow")


@dataclass
class ShadowConfig:
    sample_size: int = 10
    pass_rate: float = 0.70
    max_retries: int = 1


@dataclass
class ShadowResult:
    candidate_id: str
    passed: bool
    total_runs: int
    success_count: int
    success_rate: float
    error_types: list[str]


def lightweight_shadow_validate(
    candidate: ForgedSkillCandidate,
    registry: Any,
    config: ShadowConfig | None = None,
) -> ShadowResult:
    config = config or ShadowConfig()
    success_count = 0
    error_types: list[str] = []

    for _i in range(config.sample_size):
        try:
            handler = _build_replay_handler(candidate, registry)
            result = handler(**_extract_seed_args(candidate))
            if _is_success(result):
                success_count += 1
            else:
                error_types.append(_extract_error_type(result))
        except Exception as e:
            error_types.append(type(e).__name__)

    rate = success_count / max(1, config.sample_size)
    passed = rate >= config.pass_rate

    return ShadowResult(
        candidate_id=candidate.candidate_id,
        passed=passed,
        total_runs=config.sample_size,
        success_count=success_count,
        success_rate=round(rate, 3),
        error_types=list(set(error_types)),
    )


def _build_replay_handler(candidate: ForgedSkillCandidate, registry: Any) -> Any:
    from runtime.safety.recovery.skill_forge import SkillForge

    forge = SkillForge.__new__(SkillForge)
    forge.registry = registry
    forge.config = ForgeConfig()
    return forge._build_meta_handler(candidate)


def _extract_seed_args(candidate: ForgedSkillCandidate) -> dict[str, Any]:
    if candidate.step_templates and candidate.step_templates[0]:
        return dict(candidate.step_templates[0])
    return {}


def _is_success(result: Any) -> bool:
    if isinstance(result, dict):
        return bool(result.get("success", False))
    return result is not None


def _extract_error_type(result: Any) -> str:
    if isinstance(result, dict):
        return str(result.get("error_type", "unknown"))
    return "unknown"
