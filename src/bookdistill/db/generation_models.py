"""Persistence models for conversations, generation runs and distillation artifacts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from bookdistill.database import Base
from .models import utc_now


class ConversationRecord(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    language: Mapped[str] = mapped_column(String(32), nullable=False, default="bilingual")
    scope: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class ConversationMessageRecord(Base):
    __tablename__ = "conversation_messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    generation_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    __table_args__ = (Index("ix_conversation_messages_conversation", "conversation_id", "created_at"),)


class GenerationRunRecord(Base):
    __tablename__ = "generation_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    request_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    insufficient_evidence: Mapped[bool] = mapped_column(nullable=False)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    claims: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    model_provider: Mapped[str] = mapped_column(String(100), nullable=False)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    __table_args__ = (Index("ix_generation_runs_conversation", "conversation_id", "created_at"),)


class DistillationArtifactRecord(Base):
    __tablename__ = "distillation_artifacts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_scope: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    parent_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("distillation_artifacts.id", ondelete="SET NULL"), nullable=True
    )
    edit_state: Mapped[str] = mapped_column(String(32), nullable=False, default="generated")
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    model_provider: Mapped[str] = mapped_column(String(100), nullable=False)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
