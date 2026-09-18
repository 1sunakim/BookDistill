"""Persistence models owned exclusively by the engineering task system."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bookdistill.database import Base
from bookdistill.db.models import utc_now


class JobRecord(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, default="1.0")
    parent_job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    job_type: Mapped[str] = mapped_column(String(100), nullable=False)
    subject_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    progress_current: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    input_reference: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    output_reference: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    checkpoint: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    error_retryable: Mapped[bool | None] = mapped_column()
    error_details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    worker_id: Mapped[str | None] = mapped_column(String(200))
    trace_id: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    stages: Mapped[list["StageRunRecord"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="StageRunRecord.created_at"
    )

    __table_args__ = (
        UniqueConstraint("job_type", "idempotency_key", name="uq_jobs_type_idempotency"),
        Index("ix_jobs_status_queued", "status", "queued_at"),
        Index("ix_jobs_subject", "subject_id"),
        Index("ix_jobs_parent", "parent_job_id"),
    )


class StageRunRecord(Base):
    __tablename__ = "job_stage_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    stage_name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output_reference: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    error_retryable: Mapped[bool | None] = mapped_column()
    error_details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    job: Mapped[JobRecord] = relationship(back_populates="stages")

    __table_args__ = (
        UniqueConstraint("job_id", "stage_name", "attempt", name="uq_stage_attempt"),
        Index("ix_stage_runs_job_status", "job_id", "status"),
    )
