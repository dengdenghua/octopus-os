from __future__ import annotations

from typing import Any

from runtime.safety.evolution.agent_competitor_scorecard import (
    DEFAULT_TARGET_SCORE as DEFAULT_AGENT_SCORECARD_TARGET_SCORE,
)

try:
    from pydantic import BaseModel, Field

    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False


if FASTAPI_AVAILABLE:

    class SubagentPolicyDecisionBody(BaseModel):
        action: str = Field(..., pattern="^(watch|retire|clear)$")
        reason: str = ""
        evidence_item_ids: list[str] = Field(default_factory=list)
        actor: str = "operator_panel"

    class ScorecardGapQueueBody(BaseModel):
        target_score: int = Field(
            default=DEFAULT_AGENT_SCORECARD_TARGET_SCORE,
            ge=1,
            le=100,
        )
        limit: int = Field(default=10, ge=1, le=50)
        reason: str = "operator_scorecard_gap_review"
        dimension_id: str = ""

    class VerifierDriftQueueBody(BaseModel):
        limit: int = Field(default=1000, ge=1, le=5000)

    class RepairRoutePromotionQueueBody(BaseModel):
        limit: int = Field(default=1000, ge=1, le=5000)

    class BrowserDesktopRepairRecipeQueueBody(BaseModel):
        limit: int = Field(default=1000, ge=1, le=5000)
        min_occurrences: int = Field(default=1, ge=1, le=20)

    class BrowserDesktopStaleArtifactRejectionBody(BaseModel):
        limit: int = Field(default=1000, ge=1, le=5000)

    class BrowserDesktopRepairRecipeEvidenceBody(BaseModel):
        item_id: str
        passed: bool = False
        provided: list[str] = Field(default_factory=list)
        artifacts: list[dict[str, Any]] = Field(default_factory=list)
        notes: str = ""
        actor: str = "operator_panel"

    class BrowserDesktopRepairRecipeRerunBody(BaseModel):
        item_id: str
        api_base_url: str = ""
        promote_source_cases: bool = False
        actor: str = "operator_panel"

    class BrowserDesktopRepairRecipeRerunBatchBody(BaseModel):
        api_base_url: str = ""
        promote_source_cases: bool = False
        actor: str = "operator_panel"
        limit: int = Field(default=20, ge=1, le=100)

    class AutomationPolicyRuleInstallBody(BaseModel):
        draft_id: str
        confirm_install: bool = False
        limit: int = Field(default=100, ge=1, le=500)

    class KimiSwarmLoadTestBody(BaseModel):
        session_id: str = "kimi-swarm-load-test"
        provider_id: str = "dry_run"
        model: str = "dry-run-swarm"
        agent_count: int = Field(default=300, ge=1, le=512)
        step_count: int = Field(default=4000, ge=1, le=20000)
        max_concurrency: int = Field(default=32, ge=1, le=256)
        real_provider: bool = False
        confirm_real_provider: bool = False
        max_provider_calls: int = Field(default=0, ge=0, le=20000)
        estimated_max_tokens: int = Field(default=0, ge=0, le=20_000_000)
        record_every_step: bool = True
        stage_id: str = "auto"
        resume_from_session_id: str = ""
        resume_step_ranges: list[dict[str, int]] = Field(default_factory=list)

    class KimiSwarmQuotaProbeBody(BaseModel):
        session_id: str = "kimi-swarm-quota-probe"
        provider_id: str = "volcengine_ark"
        model: str = "kimi-k3"
        confirm_real_provider: bool = False
        max_tokens: int = Field(default=16, ge=1, le=512)

    class DualHelixShadowSettingsBody(BaseModel):
        enabled: bool = False

    class DualHelixShadowRunBody(BaseModel):
        goal: str = Field(..., min_length=1, max_length=20_000)
        primary_engine: str = Field(..., pattern="^(echo|codex)$")
        primary_output: str = Field(default="", max_length=50_000)
        workspace_path: str = Field(default="", max_length=4_096)
        source_thread_id: str = Field(default="", max_length=512)
        source_message_id: str = Field(default="", max_length=512)
        candidate_id: str = Field(default="", max_length=512)
        experiment_id: str = Field(default="", max_length=512)

    class CandidateCanaryOutcomeBody(BaseModel):
        success: bool

    class CandidateRollbackBody(BaseModel):
        reason: str = Field(default="operator rollback", min_length=1, max_length=500)


__all__ = [
    "AutomationPolicyRuleInstallBody",
    "BrowserDesktopRepairRecipeEvidenceBody",
    "BrowserDesktopRepairRecipeQueueBody",
    "BrowserDesktopRepairRecipeRerunBatchBody",
    "BrowserDesktopRepairRecipeRerunBody",
    "BrowserDesktopStaleArtifactRejectionBody",
    "CandidateCanaryOutcomeBody",
    "CandidateRollbackBody",
    "DualHelixShadowRunBody",
    "DualHelixShadowSettingsBody",
    "FASTAPI_AVAILABLE",
    "KimiSwarmLoadTestBody",
    "KimiSwarmQuotaProbeBody",
    "RepairRoutePromotionQueueBody",
    "ScorecardGapQueueBody",
    "SubagentPolicyDecisionBody",
    "VerifierDriftQueueBody",
]
