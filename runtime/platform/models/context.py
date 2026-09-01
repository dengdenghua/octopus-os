from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .primitives import new_id


class QuotaAllocation(BaseModel):
    model_config = ConfigDict(frozen=True)

    system: float = Field(..., ge=0.0, le=1.0)
    suckers: float = Field(..., ge=0.0, le=1.0)
    memory: float = Field(..., ge=0.0, le=1.0)
    history: float = Field(..., ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _sum_to_one(self) -> QuotaAllocation:
        total = self.system + self.suckers + self.memory + self.history
        if abs(total - 1.0) > 1e-3:
            raise ValueError(f"quotas must sum to 1.0, got {total}")
        return self

    def as_tokens(self, total_budget: int) -> dict[str, int]:
        return {
            "system": int(self.system * total_budget),
            "suckers": int(self.suckers * total_budget),
            "memory": int(self.memory * total_budget),
            "history": int(self.history * total_budget),
        }


DEFAULT_QUOTAS = QuotaAllocation(system=0.15, suckers=0.10, memory=0.30, history=0.45)

CODE_QUOTAS = QuotaAllocation(system=0.12, suckers=0.08, memory=0.45, history=0.35)

DEEP_RESEARCH_QUOTAS = QuotaAllocation(system=0.10, suckers=0.08, memory=0.50, history=0.32)


def select_quotas(task_type: str | None = None) -> QuotaAllocation:
    if task_type == "code":
        return CODE_QUOTAS
    if task_type in ("deep_research", "research"):
        return DEEP_RESEARCH_QUOTAS
    return DEFAULT_QUOTAS


class ContextSegment(BaseModel):
    model_config = ConfigDict(frozen=True)

    bucket: str  # "system" | "suckers" | "memory" | "history"
    content: str
    tokens_estimated: int = Field(..., ge=0)
    source_refs: list[str] = Field(default_factory=list)
    is_cacheable: bool = True  # prompt cache hint


class ContextPacket(BaseModel):
    model_config = ConfigDict(frozen=True)

    packet_id: UUID = Field(default_factory=new_id)
    total_budget_tokens: int = Field(..., gt=0)
    quotas: QuotaAllocation = Field(default=DEFAULT_QUOTAS)
    segments: list[ContextSegment] = Field(default_factory=list)
    recipe_id: str | None = None
    task_type: str | None = None

    @property
    def tokens_used(self) -> int:
        return sum(s.tokens_estimated for s in self.segments)

    @property
    def tokens_by_bucket(self) -> dict[str, int]:
        by: dict[str, int] = {}
        for seg in self.segments:
            by[seg.bucket] = by.get(seg.bucket, 0) + seg.tokens_estimated
        return by

    def over_budget(self) -> bool:
        return self.tokens_used > self.total_budget_tokens

    def bucket_overflow(self) -> dict[str, int]:
        allocated = self.quotas.as_tokens(self.total_budget_tokens)
        used = self.tokens_by_bucket
        return {bucket: used.get(bucket, 0) - allocated.get(bucket, 0) for bucket in allocated}


class WorkingSetFile(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    last_read_at: float = 0.0
    last_modified_at: float = 0.0
    tokens_estimated: int = 0
    relevance: str = "related"


class WorkingSet(BaseModel):
    model_config = ConfigDict(frozen=False)

    files: list[WorkingSetFile] = Field(default_factory=list)
    task_summary: str = ""
    current_phase: str = "understand"

    def add_file(self, path: str, *, tokens: int = 0, relevance: str = "related") -> None:
        import time

        existing = [f for f in self.files if f.path == path]
        if existing:
            return
        now = time.time()
        self.files.append(
            WorkingSetFile(
                path=path,
                last_read_at=now,
                tokens_estimated=tokens,
                relevance=relevance,
            )
        )

    def mark_modified(self, path: str) -> None:
        import time

        for i, f in enumerate(self.files):
            if f.path == path:
                self.files[i] = WorkingSetFile(
                    path=f.path,
                    last_read_at=f.last_read_at,
                    last_modified_at=time.time(),
                    tokens_estimated=f.tokens_estimated,
                    relevance="editing",
                )
                break

    def prioritize(self) -> list[WorkingSetFile]:
        def _sort_key(f: WorkingSetFile) -> tuple:
            rank = {"editing": 0, "related": 1, "referenced": 2}.get(f.relevance, 3)
            return (rank, -max(f.last_read_at, f.last_modified_at))

        return sorted(self.files, key=_sort_key)

    def top_n(self, n: int = 8) -> list[WorkingSetFile]:
        return self.prioritize()[:n]
