"""Relational persistence for document-processing state and artifacts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bookdistill.database import Base


def utc_now() -> datetime:
    from datetime import UTC

    return datetime.now(UTC)


class BookRecord(Base):
    __tablename__ = "books"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, default="1.0")
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    authors: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    language: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    original_file_name: Mapped[str] = mapped_column(String(1000), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    storage_uri: Mapped[str] = mapped_column(String(2000), nullable=False)
    format: Mapped[str] = mapped_column(String(16), nullable=False, default="pdf")
    source_unit_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    jobs: Mapped[list["ParsingJobRecord"]] = relationship(back_populates="book")
    parsed_document: Mapped["ParsedDocumentRecord | None"] = relationship(
        back_populates="book", uselist=False
    )

    __table_args__ = (Index("ix_books_status", "status"),)


class SourceDocumentRecord(Base):
    """V2 source-aware document and its first-batch chapter/Chunk projections."""

    __tablename__ = "source_documents"

    book_id: Mapped[str] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), primary_key=True
    )
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    document: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    chapters: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    chunks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    document_revision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    indexed_revision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class SourceChunkIndexRecord(Base):
    """Search projection over V2 chunks; source_documents remains authoritative."""

    __tablename__ = "source_chunk_index"

    chunk_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    book_id: Mapped[str] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), nullable=False)
    chapter_id: Mapped[str] = mapped_column(String(36), nullable=False)
    document_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    format: Mapped[str] = mapped_column(String(16), nullable=False)
    token_frequencies: Mapped[dict[str, int]] = mapped_column(JSON, nullable=False)
    token_length: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    __table_args__ = (
        Index("ix_source_chunk_index_book", "book_id"),
        Index("ix_source_chunk_index_chapter", "chapter_id"),
    )


class ParsingJobRecord(Base):
    __tablename__ = "parsing_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, default="1.0")
    book_id: Mapped[str] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    current_stage: Mapped[str] = mapped_column(String(32), nullable=False)
    progress_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    completed_stages: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    checkpoint: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    book: Mapped[BookRecord] = relationship(back_populates="jobs")
    __table_args__ = (
        Index("ix_parsing_jobs_book_id", "book_id"),
        Index("ix_parsing_jobs_status", "status"),
    )


class ProcessingErrorRecord(Base):
    __tablename__ = "processing_errors"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, default="1.0")
    job_id: Mapped[str] = mapped_column(
        ForeignKey("parsing_jobs.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    retryable: Mapped[bool] = mapped_column(nullable=False)
    page_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    trace_id: Mapped[str] = mapped_column(String(100), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (Index("ix_processing_errors_job_id", "job_id"),)


class ParsedDocumentRecord(Base):
    __tablename__ = "parsed_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    book_id: Mapped[str] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    parser_name: Mapped[str] = mapped_column(String(100), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    artifact_uri: Mapped[str] = mapped_column(String(2000), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    book: Mapped[BookRecord] = relationship(back_populates="parsed_document")


class ChapterRecord(Base):
    __tablename__ = "chapters"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    book_id: Mapped[str] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("chapters.id", ondelete="CASCADE"), nullable=True
    )
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(1000), nullable=False)
    normalized_title: Mapped[str] = mapped_column(String(1000), nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page_start: Mapped[int] = mapped_column(Integer, nullable=False)
    page_end: Mapped[int] = mapped_column(Integer, nullable=False)
    source_block_id: Mapped[str] = mapped_column(String(36), nullable=False)
    confidence: Mapped[float] = mapped_column(nullable=False)
    manually_verified: Mapped[bool] = mapped_column(nullable=False, default=False)

    __table_args__ = (
        Index("ix_chapters_book_order", "book_id", "order_index", unique=True),
        Index("ix_chapters_parent_id", "parent_id"),
    )


class ChunkRecord(Base):
    __tablename__ = "chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    book_id: Mapped[str] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    chapter_id: Mapped[str] = mapped_column(
        ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False
    )
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    chapter_path: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    page_start: Mapped[int] = mapped_column(Integer, nullable=False)
    page_end: Mapped[int] = mapped_column(Integer, nullable=False)
    source_block_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    previous_chunk_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    next_chunk_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    content_type: Mapped[str] = mapped_column(String(32), nullable=False)
    chunk_version: Mapped[str] = mapped_column(String(200), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (
        Index("ix_chunks_book_id", "book_id"),
        Index("ix_chunks_chapter_id", "chapter_id"),
        Index("ix_chunks_book_version", "book_id", "chunk_version"),
    )
