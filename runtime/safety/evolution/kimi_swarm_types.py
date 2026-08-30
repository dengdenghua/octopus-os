"""Shared config/constant/stage-resolution types for the Kimi Swarm load-test
family (kimi_swarm_load_test.py + its siblings).

Split out of the former ~1960-line kimi_swarm_load_test.py so every other
module in the family (failure taxonomy, proof lookup, resume planner, load
run, and the orchestrator that stays in kimi_swarm_load_test.py) can import
these without a circular dependency — this is the base of the family's
import chain, nothing here imports another kimi_swarm_* sibling.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from runtime.platform.models.llm import ModelRequest

_SCHEMA = "echo.kimi_swarm_load_test.v1"
_STEP_SCHEMA = "echo.kimi_swarm_load_step.v1"
_SUMMARY_EVIDENCE_SCHEMA = "echo.kimi_swarm_load_test_summary.v1"
_PREFLIGHT_SCHEMA = "echo.kimi_swarm_load_test_preflight.v1"
_STAGE_PLAN_SCHEMA = "echo.kimi_swarm_load_stage_plan.v1"
_PROOF_BUNDLE_SCHEMA = "echo.kimi_swarm_proof_bundle.v1"
_NEXT_STAGE_SCHEMA = "echo.kimi_swarm_next_stage.v1"
_RESUME_PLAN_SCHEMA = "echo.kimi_swarm_resume_plan.v1"
_COMPOSITE_PROOF_SCHEMA = "echo.kimi_swarm_composite_proof.v1"
_QUOTA_PROBE_SCHEMA = "echo.kimi_swarm_quota_probe.v1"
_DEFAULT_AGENT_COUNT = 300
_DEFAULT_STEP_COUNT = 4000
_DEFAULT_MAX_CONCURRENCY = 32
_DEFAULT_REFERENCE_PROVIDER_ID = "volcengine_ark"
_DEFAULT_REFERENCE_MODEL = "kimi-k3"
_DEFAULT_PROVIDER_OUTPUT_TOKENS_PER_STEP = 512
_DEFAULT_RESUME_CHUNK_STEP_COUNT = 64
_DEFAULT_RESUME_CHUNK_CONCURRENCY = 2
_PROVIDER_STEP_ATTEMPTS = 3

ProviderCaller = Callable[[ModelRequest], Any]


@dataclass(frozen=True)
class KimiSwarmLoadTestConfig:
    session_id: str = "kimi-swarm-load-test"
    provider_id: str = "dry_run"
    model: str = "dry-run-swarm"
    agent_count: int = _DEFAULT_AGENT_COUNT
    step_count: int = _DEFAULT_STEP_COUNT
    max_concurrency: int = _DEFAULT_MAX_CONCURRENCY
    real_provider: bool = False
    confirm_real_provider: bool = False
    record_every_step: bool = True
    max_provider_calls: int = 0
    estimated_max_tokens: int = 0
    stage_id: str = "auto"
    resume_from_session_id: str = ""
    resume_step_ranges: tuple[dict[str, int], ...] = ()


@dataclass(frozen=True)
class KimiSwarmQuotaProbeConfig:
    session_id: str = "kimi-swarm-quota-probe"
    provider_id: str = _DEFAULT_REFERENCE_PROVIDER_ID
    model: str = _DEFAULT_REFERENCE_MODEL
    confirm_real_provider: bool = False
    max_tokens: int = 16


def _normalize_counts(config: KimiSwarmLoadTestConfig) -> tuple[int, int, int]:
    agent_count = max(1, int(config.agent_count))
    step_count = max(1, int(config.step_count))
    max_concurrency = max(1, min(int(config.max_concurrency), agent_count, step_count))
    return agent_count, step_count, max_concurrency


def _resolve_stage(
    *,
    config: KimiSwarmLoadTestConfig,
    requested_agent_count: int,
    requested_step_count: int,
    requested_max_concurrency: int,
) -> dict[str, Any]:
    plan = _stage_plan(
        agent_count=requested_agent_count,
        step_count=requested_step_count,
        max_concurrency=requested_max_concurrency,
        real_provider=bool(config.real_provider),
    )
    stages = [stage for stage in plan["stages"] if isinstance(stage, dict)]
    requested_stage = str(config.stage_id or "auto").strip() or "auto"
    if requested_stage == "auto":
        requested_stage = "provider_full_reference" if config.real_provider else "dry_replay"
    if requested_stage == "provider_full_reference_resume":
        for stage in stages:
            if stage.get("id") == "provider_full_reference":
                return {
                    **dict(stage),
                    "id": "provider_full_reference_resume",
                    "source_stage_id": "provider_full_reference",
                    "title": "Provider full Kimi-reference resume",
                }
    for stage in stages:
        if stage.get("id") == requested_stage:
            return dict(stage)
    raise ValueError(f"unknown kimi swarm load-test stage_id: {requested_stage}")


def _previous_stage_id(stage_id: str) -> str:
    if stage_id == "provider_ramp":
        return "provider_canary"
    if stage_id == "provider_full_reference":
        return "provider_ramp"
    return ""


def _stage_plan(
    *,
    agent_count: int,
    step_count: int,
    max_concurrency: int,
    real_provider: bool,
) -> dict[str, Any]:
    if not real_provider:
        stages = [
            {
                "id": "dry_replay",
                "title": "Dry replay proof",
                "agent_count": agent_count,
                "step_count": step_count,
                "max_concurrency": max_concurrency,
                "requires_confirmation": False,
                "default": True,
            }
        ]
    else:
        canary_steps = min(step_count, 10)
        ramp_steps = min(step_count, max(canary_steps, 300))
        stages = [
            {
                "id": "provider_canary",
                "title": "Provider canary",
                "agent_count": min(agent_count, 10),
                "step_count": canary_steps,
                "max_concurrency": min(max_concurrency, 4),
                "requires_confirmation": True,
                "default": False,
            },
            {
                "id": "provider_ramp",
                "title": "Provider ramp",
                "agent_count": min(agent_count, 64),
                "step_count": ramp_steps,
                "max_concurrency": min(max_concurrency, 16),
                "requires_confirmation": True,
                "default": False,
            },
            {
                "id": "provider_full_reference",
                "title": "Provider full Kimi-reference proof",
                "agent_count": agent_count,
                "step_count": step_count,
                "max_concurrency": max_concurrency,
                "requires_confirmation": True,
                "default": True,
            },
        ]
    return {
        "schema": _STAGE_PLAN_SCHEMA,
        "stages": stages,
        "total_stages": len(stages),
    }


__all__ = [
    "KimiSwarmLoadTestConfig",
    "KimiSwarmQuotaProbeConfig",
    "ProviderCaller",
]
