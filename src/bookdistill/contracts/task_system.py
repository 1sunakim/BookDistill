"""Public contracts for the engineering task system.

The task service deliberately treats work from other product blocks as opaque
references.  Feature modules integrate by registering a ``JobHandler``.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


TASK_SCHEMA_VERSION = "1.0"


class TaskModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, use_enum_values=True)


class TaskStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRYING = "retrying"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"


class StageStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class CreateJobRequest(TaskModel):
    schema_version: Literal["1.0"] = TASK_SCHEMA_VERSION
    job_type: str = Field(pattern=r"^[a-z][a-z0-9_.-]+$", max_length=100)
    parent_job_id: UUID | None = None
    subject_id: UUID | None = None
    input_reference: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    max_attempts: int = Field(default=3, ge=1, le=20)


class JobError(TaskModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]+$")
    message: str = Field(min_length=1)
    retryable: bool
    details: dict[str, Any] = Field(default_factory=dict)


class StageRunView(TaskModel):
    stage_run_id: UUID
    stage_name: str
    status: StageStatus
    attempt: int = Field(ge=1)
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    output_reference: dict[str, Any] = Field(default_factory=dict)
    error: JobError | None = None
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None


class JobView(TaskModel):
    schema_version: Literal["1.0"] = TASK_SCHEMA_VERSION
    job_id: UUID
    parent_job_id: UUID | None = None
    job_type: str
    subject_id: UUID | None = None
    status: TaskStatus
    progress_current: int = Field(ge=0)
    progress_total: int = Field(ge=0)
    attempt: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    input_reference: dict[str, Any]
    output_reference: dict[str, Any]
    checkpoint: dict[str, Any]
    idempotency_key: str
    error: JobError | None = None
    worker_id: str | None = None
    trace_id: str
    created_at: AwareDatetime
    queued_at: AwareDatetime | None = None
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    heartbeat_at: AwareDatetime | None = None
    stages: list[StageRunView] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_progress(self) -> "JobView":
        if self.progress_total and self.progress_current > self.progress_total:
            raise ValueError("progress_current cannot exceed progress_total")
        return self


class JobList(TaskModel):
    items: list[JobView]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


class ErrorResponse(TaskModel):
    code: str
    message: str
    trace_id: str
    details: dict[str, Any] = Field(default_factory=dict)


class HealthResponse(TaskModel):
    status: Literal["ok", "degraded"]
    service: str = "bookdistill-api"
    checks: dict[str, str] = Field(default_factory=dict)
    timestamp: datetime
