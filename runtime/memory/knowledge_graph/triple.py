from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from runtime.platform.models import Source, new_id, now_utc

TripleStatus = Literal["active", "archived", "disputed", "superseded"]


class Triple(BaseModel):
    model_config = ConfigDict(frozen=True)

    triple_id: UUID = Field(default_factory=new_id)
    subject: str = Field(..., min_length=1)
    predicate: str = Field(..., min_length=1)
    object: str = Field(..., min_length=1)  # Implementation note.
    confidence: float = Field(default=0.75, ge=0.0, le=1.0)
    source: Source
    ts: datetime = Field(default_factory=now_utc)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    status: TripleStatus = "active"
    superseded_by: UUID | None = None

    @property
    def sp_key(self) -> tuple[str, str]:
        return (self.subject, self.predicate)
