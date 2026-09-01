"""Typed records and strict write contracts for Narrative Studio v2.

Persisted records intentionally allow unknown fields so a newer writer does not
make an older runtime unable to read a project. Public request models stay
strict: callers cannot smuggle server-owned identity, revision, or canon fields
into a candidate record.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CandidateStatus = Literal["candidate"]
LanguageCode = Literal["zh", "en", "bilingual"]
ChapterStatus = Literal["planned", "draft", "candidate", "review"]
EntityKind = Literal[
    "character",
    "location",
    "faction",
    "technology",
    "item",
    "concept",
]
PipelineStageName = Literal[
    "outline",
    "draft",
    "continuity",
    "style",
    "revision",
    "editorial",
]
ReviewDecision = Literal["approve", "reject", "abstain"]
RevisionTargetType = Literal["chapter", "scene"]
RevisionOperation = Literal["create", "update", "restore", "migrated"]
RevisionActorSource = Literal[
    "authenticated_principal",
    "client_asserted",
    "agent_skill",
    "local",
]
ReviewTargetType = Literal[
    "world_pack",
    "branch",
    "story_arc",
    "chapter",
    "scene",
    "fact",
    "state_change",
    "entity",
    "relationship",
    "foreshadow",
    "context_pack",
    "pipeline_run",
]

PIPELINE_STAGE_ORDER: tuple[PipelineStageName, ...] = (
    "outline",
    "draft",
    "continuity",
    "style",
    "revision",
    "editorial",
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class PersistedModel(BaseModel):
    """Forward-readable base model that preserves fields from newer schemas."""

    model_config = ConfigDict(extra="allow")


class CandidateRecord(PersistedModel):
    """Fields shared by every persisted, non-canonical story artifact."""

    id: str
    project_id: str
    canon_status: CandidateStatus = "candidate"
    revision: int = Field(default=1, ge=1)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)

    @field_validator("canon_status", mode="before")
    @classmethod
    def reject_canon_promotion(cls, value: object) -> object:
        if value != "candidate":
            raise ValueError("Narrative Studio may only write candidate artifacts")
        return value


class GovernancePolicy(PersistedModel):
    review_quorum: int = Field(default=2, ge=1, le=100)
    approval_ratio: float = Field(default=0.67, gt=0.0, le=1.0)


class NarrativeProject(PersistedModel):
    schema_version: str = "echo.narrative-studio.project.v2"
    migrated_from: str | None = None
    id: str
    title: str
    premise: str = ""
    language: LanguageCode = "zh"
    canon_policy: Literal["candidate_only"] = "candidate_only"
    default_branch_id: str
    governance: GovernancePolicy = Field(default_factory=GovernancePolicy)
    source: str = "native"
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)


class WorldResource(PersistedModel):
    category: str
    relative_path: str
    sha256: str
    media_type: str = "text/markdown"
    excerpt: str = ""
    truncated: bool = False


class WorldPack(CandidateRecord):
    name: str
    summary: str = ""
    source_kind: str = "native"
    source_root: str | None = None
    resources: list[WorldResource] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class StoryBranch(CandidateRecord):
    name: str
    base_branch_id: str | None = None
    purpose: str = ""
    status: Literal["active", "archived"] = "active"


class StoryArc(CandidateRecord):
    branch_id: str
    name: str
    summary: str = ""
    start_chapter_ordinal: int | None = Field(default=None, ge=1)
    end_chapter_ordinal: int | None = Field(default=None, ge=1)
    beats: list[str] = Field(default_factory=list)
    status: Literal["planned", "active", "complete", "archived"] = "planned"


class Chapter(CandidateRecord):
    branch_id: str
    ordinal: int = Field(ge=1)
    title: str
    summary: str = ""
    body: str = ""
    status: ChapterStatus = "draft"


class Scene(CandidateRecord):
    branch_id: str
    chapter_id: str
    ordinal: int = Field(ge=1)
    title: str
    goal: str = ""
    conflict: str = ""
    outcome: str = ""
    pov_character_id: str | None = None
    body: str = ""
    status: ChapterStatus = "draft"


class CandidateRevision(PersistedModel):
    """Immutable audit snapshot for a versioned candidate artifact."""

    model_config = ConfigDict(extra="allow", frozen=True)

    schema_version: str = "echo.narrative-studio.candidate-revision.v1"
    project_id: str
    target_type: RevisionTargetType
    target_id: str
    revision: int = Field(ge=1)
    snapshot: dict[str, Any]
    snapshot_sha256: str = Field(min_length=64, max_length=64)
    snapshot_bytes: int = Field(ge=1)
    operation: RevisionOperation
    actor: str = Field(min_length=1, max_length=240)
    actor_source: RevisionActorSource
    message: str = Field(default="", max_length=20_000)
    restored_from_revision: int | None = Field(default=None, ge=1)
    history_origin: Literal["native", "legacy_baseline"] = "native"
    reconstructed: bool = False
    created_at: str = Field(default_factory=utc_now)

    @field_validator("snapshot")
    @classmethod
    def snapshot_must_remain_candidate(cls, value: dict[str, Any]) -> dict[str, Any]:
        if value.get("canon_status", "candidate") != "candidate":
            raise ValueError("revision snapshots may only contain candidate artifacts")
        return value


class NarrativeFact(CandidateRecord):
    branch_id: str | None = None
    subject: str
    predicate: str
    object: str
    scope: Literal["world", "branch", "chapter", "scene"] = "world"
    source_refs: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class StateChange(CandidateRecord):
    branch_id: str
    chapter_id: str | None = None
    scene_id: str | None = None
    entity_id: str
    field: str
    before: Any = None
    after: Any = None
    reason: str = ""
    source_refs: list[str] = Field(default_factory=list)


class Entity(CandidateRecord):
    branch_id: str | None = None
    kind: EntityKind
    name: str
    summary: str = ""
    aliases: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[str] = Field(default_factory=list)


class Relationship(CandidateRecord):
    branch_id: str | None = None
    from_entity_id: str
    to_entity_id: str
    kind: str
    summary: str = ""
    bidirectional: bool = False
    attributes: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[str] = Field(default_factory=list)


class Foreshadow(CandidateRecord):
    branch_id: str
    title: str
    setup: str
    intended_payoff: str = ""
    setup_chapter_id: str | None = None
    payoff_chapter_id: str | None = None
    status: Literal["planned", "planted", "echoed", "resolved", "abandoned"] = "planned"
    source_refs: list[str] = Field(default_factory=list)


class ContextSource(PersistedModel):
    ref: str
    kind: str
    title: str
    content: str
    char_count: int = Field(ge=0)
    truncated: bool = False


class ContextPack(CandidateRecord):
    branch_id: str
    target_chapter_id: str | None = None
    label: str = ""
    max_chars: int = Field(ge=1)
    max_items: int = Field(ge=1)
    total_chars: int = Field(ge=0)
    estimated_tokens: int = Field(ge=0)
    omitted_count: int = Field(ge=0)
    sources: list[ContextSource] = Field(default_factory=list)
    content: str = ""
    truncated: bool = False


class PipelineStage(PersistedModel):
    id: PipelineStageName
    name: PipelineStageName
    ordinal: int = Field(ge=1)
    canon_status: CandidateStatus = "candidate"
    status: Literal["pending", "submitted"] = "pending"
    output: str = ""
    source_refs: list[str] = Field(default_factory=list)
    submitted_by: str = ""
    submitted_at: str | None = None
    updated_at: str | None = None

    @field_validator("canon_status", mode="before")
    @classmethod
    def reject_canon_promotion(cls, value: object) -> object:
        if value != "candidate":
            raise ValueError("pipeline stages may only contain candidate output")
        return value


class PipelineRun(CandidateRecord):
    branch_id: str
    chapter_id: str | None = None
    context_pack_id: str | None = None
    goal: str = ""
    current_stage: PipelineStageName | None = "outline"
    status: Literal["active", "complete", "cancelled"] = "active"
    stages: list[PipelineStage] = Field(default_factory=list)


class ReviewRequest(CandidateRecord):
    target_type: ReviewTargetType
    target_id: str
    target_revision: int = Field(ge=1)
    title: str
    summary: str
    blocking: bool = False
    requested_by: str
    actor_source: Literal["authenticated_principal", "client_asserted", "agent_skill"]
    status: Literal["open", "resolved"] = "open"
    resolution: str = ""


class ReviewVote(PersistedModel):
    id: str
    project_id: str
    review_request_id: str
    voter_id: str
    decision: ReviewDecision
    rationale: str = ""
    actor_source: Literal["authenticated_principal", "client_asserted"]
    revision: int = Field(default=1, ge=1)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)


class CanonCommit(PersistedModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    schema_version: str = "echo.narrative-studio.canon-commit.v1"
    id: str
    project_id: str
    review_request_id: str
    target_type: ReviewTargetType
    target_id: str
    target_revision: int = Field(ge=1)
    snapshot: dict[str, Any]
    snapshot_sha256: str
    governance: dict[str, Any]
    committed_by: str
    actor_source: Literal["authenticated_principal", "client_asserted"]
    message: str = ""
    created_at: str = Field(default_factory=utc_now)


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectCreate(StrictRequest):
    id: str | None = None
    title: str = Field(min_length=1, max_length=160)
    premise: str = Field(default="", max_length=20_000)
    language: LanguageCode = "zh"
    review_quorum: int | None = Field(default=None, ge=1, le=100)
    approval_ratio: float | None = Field(default=None, gt=0.0, le=1.0)


class ProjectUpdate(StrictRequest):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    premise: str | None = Field(default=None, max_length=20_000)
    language: LanguageCode | None = None
    review_quorum: int | None = Field(default=None, ge=1, le=100)
    approval_ratio: float | None = Field(default=None, gt=0.0, le=1.0)


class WorldPackCreate(StrictRequest):
    id: str | None = None
    name: str = Field(min_length=1, max_length=160)
    summary: str = Field(default="", max_length=20_000)
    resources: list[WorldResource] = Field(default_factory=list, max_length=1000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class BranchCreate(StrictRequest):
    id: str | None = None
    name: str = Field(min_length=1, max_length=160)
    base_branch_id: str | None = None
    purpose: str = Field(default="", max_length=10_000)


class StoryArcCreate(StrictRequest):
    id: str | None = None
    branch_id: str
    name: str = Field(min_length=1, max_length=240)
    summary: str = Field(default="", max_length=30_000)
    start_chapter_ordinal: int | None = Field(default=None, ge=1)
    end_chapter_ordinal: int | None = Field(default=None, ge=1)
    beats: list[str] = Field(default_factory=list, max_length=500)
    status: Literal["planned", "active", "complete", "archived"] = "planned"


class StoryArcUpdate(StrictRequest):
    name: str | None = Field(default=None, min_length=1, max_length=240)
    summary: str | None = Field(default=None, max_length=30_000)
    start_chapter_ordinal: int | None = Field(default=None, ge=1)
    end_chapter_ordinal: int | None = Field(default=None, ge=1)
    beats: list[str] | None = Field(default=None, max_length=500)
    status: Literal["planned", "active", "complete", "archived"] | None = None


class ChapterCreate(StrictRequest):
    id: str | None = None
    branch_id: str
    ordinal: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=240)
    summary: str = Field(default="", max_length=30_000)
    body: str = Field(default="", max_length=2_000_000)
    status: ChapterStatus = "draft"


class ChapterUpdate(StrictRequest):
    ordinal: int | None = Field(default=None, ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=240)
    summary: str | None = Field(default=None, max_length=30_000)
    body: str | None = Field(default=None, max_length=2_000_000)
    status: ChapterStatus | None = None
    expected_revision: int | None = Field(default=None, ge=1)


class SceneCreate(StrictRequest):
    id: str | None = None
    branch_id: str
    ordinal: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=240)
    goal: str = Field(default="", max_length=20_000)
    conflict: str = Field(default="", max_length=20_000)
    outcome: str = Field(default="", max_length=20_000)
    pov_character_id: str | None = None
    body: str = Field(default="", max_length=1_000_000)
    status: ChapterStatus = "draft"


class SceneUpdate(StrictRequest):
    ordinal: int | None = Field(default=None, ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=240)
    goal: str | None = Field(default=None, max_length=20_000)
    conflict: str | None = Field(default=None, max_length=20_000)
    outcome: str | None = Field(default=None, max_length=20_000)
    pov_character_id: str | None = None
    body: str | None = Field(default=None, max_length=1_000_000)
    status: ChapterStatus | None = None
    expected_revision: int | None = Field(default=None, ge=1)


class RevisionRestoreRequest(StrictRequest):
    expected_revision: int | None = Field(default=None, ge=1)
    message: str = Field(default="", max_length=20_000)


class FactCreate(StrictRequest):
    id: str | None = None
    branch_id: str | None = None
    subject: str = Field(min_length=1, max_length=300)
    predicate: str = Field(min_length=1, max_length=300)
    object: str = Field(min_length=1, max_length=10_000)
    scope: Literal["world", "branch", "chapter", "scene"] = "world"
    source_refs: list[str] = Field(default_factory=list, max_length=100)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class StateChangeCreate(StrictRequest):
    id: str | None = None
    branch_id: str
    chapter_id: str | None = None
    scene_id: str | None = None
    entity_id: str = Field(min_length=1, max_length=300)
    field: str = Field(min_length=1, max_length=300)
    before: Any = None
    after: Any = None
    reason: str = Field(default="", max_length=20_000)
    source_refs: list[str] = Field(default_factory=list, max_length=100)


class EntityCreate(StrictRequest):
    id: str | None = None
    branch_id: str | None = None
    kind: EntityKind
    name: str = Field(min_length=1, max_length=240)
    summary: str = Field(default="", max_length=30_000)
    aliases: list[str] = Field(default_factory=list, max_length=100)
    attributes: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[str] = Field(default_factory=list, max_length=100)


class EntityUpdate(StrictRequest):
    name: str | None = Field(default=None, min_length=1, max_length=240)
    summary: str | None = Field(default=None, max_length=30_000)
    aliases: list[str] | None = Field(default=None, max_length=100)
    attributes: dict[str, Any] | None = None
    source_refs: list[str] | None = Field(default=None, max_length=100)


class RelationshipCreate(StrictRequest):
    id: str | None = None
    branch_id: str | None = None
    from_entity_id: str
    to_entity_id: str
    kind: str = Field(min_length=1, max_length=160)
    summary: str = Field(default="", max_length=30_000)
    bidirectional: bool = False
    attributes: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[str] = Field(default_factory=list, max_length=100)


class RelationshipUpdate(StrictRequest):
    kind: str | None = Field(default=None, min_length=1, max_length=160)
    summary: str | None = Field(default=None, max_length=30_000)
    bidirectional: bool | None = None
    attributes: dict[str, Any] | None = None
    source_refs: list[str] | None = Field(default=None, max_length=100)


class ForeshadowCreate(StrictRequest):
    id: str | None = None
    branch_id: str
    title: str = Field(min_length=1, max_length=240)
    setup: str = Field(min_length=1, max_length=30_000)
    intended_payoff: str = Field(default="", max_length=30_000)
    setup_chapter_id: str | None = None
    payoff_chapter_id: str | None = None
    status: Literal["planned", "planted", "echoed", "resolved", "abandoned"] = "planned"
    source_refs: list[str] = Field(default_factory=list, max_length=100)


class ForeshadowUpdate(StrictRequest):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    setup: str | None = Field(default=None, min_length=1, max_length=30_000)
    intended_payoff: str | None = Field(default=None, max_length=30_000)
    payoff_chapter_id: str | None = None
    status: Literal["planned", "planted", "echoed", "resolved", "abandoned"] | None = None
    source_refs: list[str] | None = Field(default=None, max_length=100)


class ContextPackBuildRequest(StrictRequest):
    id: str | None = None
    branch_id: str
    target_chapter_id: str | None = None
    label: str = Field(default="", max_length=240)
    max_chars: int | None = Field(default=None, ge=256, le=2_000_000)
    max_items: int | None = Field(default=None, ge=1, le=10_000)


class ContextPackUpdate(StrictRequest):
    label: str | None = Field(default=None, max_length=240)


class PipelineRunCreate(StrictRequest):
    id: str | None = None
    branch_id: str
    chapter_id: str | None = None
    context_pack_id: str | None = None
    goal: str = Field(default="", max_length=30_000)


class PipelineRunUpdate(StrictRequest):
    status: Literal["active", "cancelled"]


class PipelineStageSubmit(StrictRequest):
    output: str = Field(min_length=1, max_length=2_000_000)
    source_refs: list[str] = Field(default_factory=list, max_length=500)
    submitted_by: str = Field(min_length=1, max_length=240)


class ReviewRequestCreate(StrictRequest):
    id: str | None = None
    target_type: ReviewTargetType
    target_id: str
    title: str = Field(min_length=1, max_length=240)
    summary: str = Field(min_length=1, max_length=50_000)
    blocking: bool = False
    requested_by: str = Field(min_length=1, max_length=240)


class ReviewRequestUpdate(StrictRequest):
    status: Literal["open", "resolved"]
    resolution: str = Field(default="", max_length=50_000)


class ReviewVoteCreate(StrictRequest):
    voter_id: str = Field(min_length=1, max_length=240)
    decision: ReviewDecision
    rationale: str = Field(default="", max_length=20_000)


class ReviewVoteUpdate(StrictRequest):
    decision: ReviewDecision
    rationale: str = Field(default="", max_length=20_000)


class CanonCommitCreate(StrictRequest):
    review_request_id: str
    confirm: bool = False
    committed_by: str = Field(min_length=1, max_length=240)
    message: str = Field(default="", max_length=20_000)
    actor_type: Literal["human"] = "human"


class CanonReviewCommitRequest(StrictRequest):
    """Compatibility shape used by the native Narrative Studio workbench."""

    actor: str = Field(min_length=1, max_length=240)
    rationale: str = Field(default="", max_length=20_000)
    confirm: bool = False


class EchoImportRequest(StrictRequest):
    source_path: str | None = None
    pack_name: str = Field(default="ECHO Universe", min_length=1, max_length=160)
    include_content: bool = True


__all__ = [name for name in globals() if not name.startswith("_")]
