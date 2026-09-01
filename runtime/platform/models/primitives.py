from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, NewType
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

TaskId = NewType("TaskId", UUID)
ArmId = NewType("ArmId", str)
SkillId = NewType("SkillId", str)
TrajectoryId = NewType("TrajectoryId", UUID)
StepId = NewType("StepId", int)
GenomeId = NewType("GenomeId", UUID)
RecipeId = NewType("RecipeId", UUID)


def new_id() -> UUID:
    return uuid4()


def now_utc() -> datetime:
    return datetime.now(UTC)


SourceType = Literal["user", "tool", "doc", "trajectory", "inference", "system"]


class Source(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_id: str = Field(..., min_length=1)
    source_type: SourceType
    trust_score: float = Field(default=0.5, ge=0.0, le=1.0)


class CostEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    tokens_in: int = Field(default=0, ge=0)
    tokens_out: int = Field(default=0, ge=0)
    usd: float = Field(default=0.0, ge=0.0)
    latency_ms: float = Field(default=0.0, ge=0.0)

    @property
    def tokens(self) -> int:
        return self.tokens_in + self.tokens_out

    def __add__(self, other: CostEntry) -> CostEntry:
        return CostEntry(
            tokens_in=self.tokens_in + other.tokens_in,
            tokens_out=self.tokens_out + other.tokens_out,
            usd=self.usd + other.usd,
            latency_ms=max(self.latency_ms, other.latency_ms),  # Implementation note.
        )


TRUST_USER_DEFAULT = 0.80
TRUST_TOOL_DEFAULT = 0.75
TRUST_DOC_DEFAULT = 0.60
TRUST_TRAJECTORY_DEFAULT = 0.55
TRUST_INFERENCE_DEFAULT = 0.50  # Implementation note.
TRUST_INFERENCE_CAP = 0.50  # Implementation note.
TRUST_REM_CAP = 0.40  # Implementation note.


DEFAULT_TRUST_BY_TYPE: dict[SourceType, float] = {
    "user": TRUST_USER_DEFAULT,
    "tool": TRUST_TOOL_DEFAULT,
    "doc": TRUST_DOC_DEFAULT,
    "trajectory": TRUST_TRAJECTORY_DEFAULT,
    "inference": TRUST_INFERENCE_DEFAULT,
    "system": 1.0,
}


def default_source(source_id: str, source_type: SourceType) -> Source:
    return Source(
        source_id=source_id,
        source_type=source_type,
        trust_score=DEFAULT_TRUST_BY_TYPE.get(source_type, 0.5),
    )
