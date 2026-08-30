"""Pydantic contracts for deep-research planning and execution."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

ResearchDepth = Literal["quick", "standard", "deep"]
ResearchSourceProvider = Literal[
    "web_search",
    "fetch_url",
    "uploaded_file",
    "local_file",
    "manual_material",
]
ResearchSourceKind = Literal[
    "web",
    "news",
    "academic",
    "company_site",
    "ecommerce",
    "social",
    "forum",
    "uploaded_file",
    "provided_url",
    "local_file",
]
ResearchStepStatus = Literal["pending", "running", "completed", "failed"]
ResearchPrefetchStatus = Literal["completed", "failed", "skipped"]
ResearchPrefetchAction = Literal["search", "fetch", "material", "skip"]


class ResearchMaterial(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: f"mat_{uuid4().hex[:10]}")
    kind: Literal["file", "url", "text", "site"] = "text"
    title: str = ""
    path: str | None = None
    url: str | None = None
    text: str | None = None
    notes: str | None = None

    @field_validator("title")
    @classmethod
    def _clean_title(cls, value: str) -> str:
        return value.strip()


class ResearchSource(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: f"src_{uuid4().hex[:10]}")
    kind: ResearchSourceKind
    label: str
    query_hint: str = ""
    provider: ResearchSourceProvider = "web_search"
    query_templates: list[str] = Field(default_factory=list)
    site_filters: list[str] = Field(default_factory=list)
    freshness_days: int | None = None
    url: str | None = None
    enabled: bool = True


class ResearchEvidence(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: f"ev_{uuid4().hex[:10]}")
    job_id: str | None = None
    step_id: str | None = None
    role_id: str | None = None
    title: str = ""
    url: str | None = None
    source_kind: ResearchSourceKind | None = None
    published_at: str | None = None
    quote_or_summary: str = ""
    claim: str = ""
    stance: Literal["support", "contradict", "context"] = "context"
    confidence: float = Field(default=0.5, ge=0, le=1)


class ResearchRole(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    subagent_name: str = "virtual-researcher"
    focus: str
    deliverable: str
    search_angles: list[str] = Field(default_factory=list)


class ResearchRouteDecision(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True, serialize_by_alias=True)

    schema_: str = Field(default="echo.subagent_route_decision.v1", alias="schema")
    step_id: str | None = None
    task_id: str | None = None
    role: str = ""
    action: str = "allow"
    reason: str = ""
    risk_level: str = "low"
    verdict: str = "unknown"
    score: float | None = None
    confidence: float = 0.0
    evidence_item_ids: list[str] = Field(default_factory=list)
    phase: str | None = None
    created_at: str | None = None


class ResearchStep(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    title: str
    role_id: str
    status: ResearchStepStatus = "pending"
    source_ids: list[str] = Field(default_factory=list)
    expected_searches: int = 0
    prompt: str = ""
    route_decision: ResearchRouteDecision | None = None


class ResearchPrefetchLog(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: f"pf_{uuid4().hex[:10]}")
    source_id: str | None = None
    source_kind: ResearchSourceKind | None = None
    source_label: str = ""
    provider: ResearchSourceProvider = "web_search"
    action: ResearchPrefetchAction = "search"
    query: str | None = None
    url: str | None = None
    status: ResearchPrefetchStatus = "completed"
    result_count: int = 0
    evidence_count: int = 0
    error: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class DeepResearchRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    topic: str
    thread_id: str | None = None
    lead_agent_name: str | None = None
    depth: ResearchDepth = "standard"
    locale: str = "zh-CN"
    materials: list[ResearchMaterial] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)
    source_kinds: list[ResearchSourceKind] = Field(
        default_factory=lambda: [
            "web",
            "news",
            "academic",
            "company_site",
            "ecommerce",
            "social",
            "forum",
            "uploaded_file",
            "provided_url",
        ]
    )
    roles: list[ResearchRole] = Field(default_factory=list)
    max_subagents: int | None = Field(default=None, ge=1, le=12)
    max_searches: int = Field(default=120, ge=1, le=1000)
    include_thread_uploads: bool = True
    prefetch_sources: bool = False
    task_risk_level: Literal["low", "medium", "high", "critical"] | None = None
    final_report_format: Literal["markdown", "brief", "slides_outline"] = "markdown"

    @field_validator("topic")
    @classmethod
    def _topic_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("topic is required")
        return value


class ResearchJob(BaseModel):
    model_config = ConfigDict(extra="ignore")

    job_id: str
    thread_id: str | None = None
    lead_agent_name: str | None = None
    topic: str
    status: Literal["planned", "running", "completed", "failed", "cancelled"] = "planned"
    depth: ResearchDepth
    locale: str
    created_at: str
    materials: list[ResearchMaterial]
    sources: list[ResearchSource]
    evidence: list[ResearchEvidence] = Field(default_factory=list)
    prefetch_logs: list[ResearchPrefetchLog] = Field(default_factory=list)
    route_decisions: list[ResearchRouteDecision] = Field(default_factory=list)
    roles: list[ResearchRole]
    steps: list[ResearchStep]
    max_searches: int
    dispatch_batch_id: str | None = None
    final_report_format: str = "markdown"
    final_report: str | None = None
    completed_at: str | None = None
    memory_entry: str | None = None
    memory_written_at: str | None = None
    memory_path: str | None = None


__all__ = [
    "DeepResearchRequest",
    "ResearchDepth",
    "ResearchEvidence",
    "ResearchJob",
    "ResearchMaterial",
    "ResearchPrefetchAction",
    "ResearchPrefetchLog",
    "ResearchPrefetchStatus",
    "ResearchRole",
    "ResearchRouteDecision",
    "ResearchSource",
    "ResearchSourceKind",
    "ResearchSourceProvider",
    "ResearchStep",
    "ResearchStepStatus",
]
