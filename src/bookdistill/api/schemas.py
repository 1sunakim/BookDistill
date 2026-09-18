from __future__ import annotations

from uuid import UUID

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class UploadAccepted(BaseModel):
    model_config = ConfigDict(extra="forbid")

    book_id: UUID
    job_id: UUID
    task_id: UUID | None = None
    duplicate: bool


class ReprocessBookRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    max_attempts: int = Field(default=3, ge=1, le=20)


class RebuildBookRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_stage: Literal["clean", "structure", "chunk"]
    chapter_ids: list[UUID] = Field(default_factory=list)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    max_attempts: int = Field(default=3, ge=1, le=20)

    @model_validator(mode="after")
    def validate_scope(self) -> "RebuildBookRequest":
        if len(self.chapter_ids) != len(set(self.chapter_ids)):
            raise ValueError("chapter_ids cannot contain duplicates")
        if self.chapter_ids and self.from_stage != "chunk":
            raise ValueError("chapter_ids are only allowed when from_stage is chunk")
        return self
