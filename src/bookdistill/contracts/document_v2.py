"""Source-aware document contracts for non-PDF ingestion.

The existing v1 PDF contracts remain unchanged during migration.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictV2Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PdfPageLocation(StrictV2Model):
    kind: Literal["pdf_page"] = "pdf_page"
    page_index: int = Field(ge=0)
    block_index: int = Field(ge=0)


class TextLineLocation(StrictV2Model):
    kind: Literal["text_line"] = "text_line"
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    heading_path: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_range(self) -> "TextLineLocation":
        if self.line_end < self.line_start:
            raise ValueError("line_end cannot precede line_start")
        return self


class EpubAnchorLocation(StrictV2Model):
    kind: Literal["epub_anchor"] = "epub_anchor"
    unit_index: int = Field(ge=0)
    href: str = Field(min_length=1)
    anchor: str | None = None
    block_index: int = Field(ge=0)


class DocxBlockLocation(StrictV2Model):
    kind: Literal["docx_block"] = "docx_block"
    block_index: int = Field(ge=0)
    heading_path: list[str] = Field(default_factory=list)


SourceLocation = Annotated[
    PdfPageLocation | TextLineLocation | EpubAnchorLocation | DocxBlockLocation,
    Field(discriminator="kind"),
]


class DocumentBlockV2(StrictV2Model):
    block_id: UUID
    order_index: int = Field(ge=0)
    kind: Literal["heading", "paragraph", "list", "code", "code_fence", "table"]
    raw_text: str = Field(min_length=1)
    text: str = Field(min_length=1)
    source_location: SourceLocation
    heading_level: int | None = Field(default=None, ge=1, le=6)


class DocumentUnitV2(StrictV2Model):
    unit_index: int = Field(ge=0)
    title: str | None = None
    blocks: list[DocumentBlockV2]


class ParsedDocumentV2(StrictV2Model):
    schema_version: Literal["2.0"] = "2.0"
    book_id: UUID
    format: Literal["pdf", "txt", "md", "epub", "docx"]
    parser_name: str = Field(min_length=1)
    parser_version: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    parse_config_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    units: list[DocumentUnitV2]

    @model_validator(mode="after")
    def check_units(self) -> "ParsedDocumentV2":
        if [unit.unit_index for unit in self.units] != list(range(len(self.units))):
            raise ValueError("units must be ordered from zero without gaps")
        return self


class ChapterV2(StrictV2Model):
    schema_version: Literal["2.0"] = "2.0"
    chapter_id: UUID
    book_id: UUID
    parent_id: UUID | None = None
    title: str = Field(min_length=1)
    level: int = Field(ge=1)
    order_index: int = Field(ge=0)
    source_block_id: UUID
    source_location: SourceLocation


class ChunkV2(StrictV2Model):
    schema_version: Literal["2.0"] = "2.0"
    chunk_id: UUID
    book_id: UUID
    chapter_id: UUID
    chapter_path: list[str] = Field(min_length=1)
    text: str = Field(min_length=1)
    raw_text: str = Field(min_length=1)
    source_block_ids: list[UUID] = Field(min_length=1)
    source_start: SourceLocation
    source_end: SourceLocation
    token_count: int = Field(gt=0)
    checksum: str = Field(pattern=r"^[a-f0-9]{64}$")


class BookV2(StrictV2Model):
    schema_version: Literal["2.0"] = "2.0"
    book_id: UUID
    title: str
    authors: list[str]
    format: Literal["txt", "md", "epub", "docx"]
    mime_type: str
    size_bytes: int = Field(ge=0)
    language: str
    source_unit_count: int | None = Field(default=None, ge=1)
    page_count: None = None
    status: Literal["queued", "processing", "processed", "failed", "cancelled"]
    retrieval_ready: bool = False


class TextBookAccepted(StrictV2Model):
    book: BookV2
    duplicate: bool
    task_id: UUID


class SourceSearchRequest(StrictV2Model):
    schema_version: Literal["2.0"] = "2.0"
    request_id: UUID
    query: str = Field(min_length=1, max_length=4000)
    book_ids: list[UUID] = Field(default_factory=list)
    chapter_ids: list[UUID] = Field(default_factory=list)
    limit: int = Field(default=10, ge=1, le=50)
    max_per_chapter: int = Field(default=3, ge=1, le=20)


class SourceEvidenceV2(StrictV2Model):
    schema_version: Literal["2.0"] = "2.0"
    evidence_id: UUID
    chunk_id: UUID
    book_id: UUID
    book_title: str
    format: Literal["txt", "md", "epub", "docx"]
    chapter_id: UUID
    chapter_path: list[str]
    source_start: SourceLocation
    source_end: SourceLocation
    document_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    text: str
    score: float = Field(gt=0)


class SourceSearchResponse(StrictV2Model):
    schema_version: Literal["2.0"] = "2.0"
    request_id: UUID
    evidence: list[SourceEvidenceV2]
    indexed_chunk_count: int = Field(ge=0)


class SourceReprocessRequest(StrictV2Model):
    from_stage: Literal["parse", "clean", "structure", "chunk", "persist", "index"] = "parse"
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    max_attempts: int = Field(default=3, ge=1, le=20)


class SourceSnapshotV2(StrictV2Model):
    """Read-only input contract shared by PDF and non-PDF consumers."""

    schema_version: Literal["2.0"] = "2.0"
    book_id: UUID
    format: Literal["pdf", "txt", "md", "epub", "docx"]
    document_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    document: ParsedDocumentV2
    chapters: list[ChapterV2]
    chunks: list[ChunkV2]
