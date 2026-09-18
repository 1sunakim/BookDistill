"""Pydantic contracts for the PDF-to-Chunk pipeline.

Page indexes are zero-based PDF physical page indexes. Page ranges are inclusive.
All models reject unknown fields so an incompatible producer fails early instead
of silently losing data.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


SCHEMA_VERSION = "1.0"
Sha256 = str


class StrictModel(BaseModel):
    """Common compatibility and serialization policy."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        use_enum_values=True,
    )


class BookStatus(str, Enum):
    UPLOADED = "uploaded"
    VALIDATING = "validating"
    PARSING = "parsing"
    STRUCTURING = "structuring"
    CHUNKING = "chunking"
    INDEXING = "indexing"
    READY = "ready"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ProcessingStage(str, Enum):
    VALIDATE = "validate"
    CLASSIFY = "classify"
    PARSE = "parse"
    CLEAN = "clean"
    STRUCTURE = "structure"
    CHUNK = "chunk"
    QUALITY_CHECK = "quality_check"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRYING = "retrying"
    CANCELLED = "cancelled"


class ErrorCategory(str, Enum):
    VALIDATION = "validation"
    UNSUPPORTED = "unsupported"
    PARSING = "parsing"
    OCR = "ocr"
    STRUCTURING = "structuring"
    CHUNKING = "chunking"
    STORAGE = "storage"
    INTERNAL = "internal"


class ContentType(str, Enum):
    TITLE = "title"
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    TABLE = "table"
    FIGURE_CAPTION = "figure_caption"
    FORMULA = "formula"
    HEADER = "header"
    FOOTER = "footer"
    PAGE_NUMBER = "page_number"
    OTHER = "other"


class BoundingBox(StrictModel):
    """Rectangle in PDF points, measured from the page's top-left corner."""

    x0: float = Field(ge=0)
    y0: float = Field(ge=0)
    x1: float = Field(gt=0)
    y1: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_rectangle(self) -> "BoundingBox":
        if self.x1 <= self.x0 or self.y1 <= self.y0:
            raise ValueError("bbox must have positive width and height")
        return self


class TextStyle(StrictModel):
    font_name: str | None = None
    font_size: float | None = Field(default=None, gt=0)
    bold: bool | None = None
    italic: bool | None = None


class TextBlock(StrictModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    block_id: UUID
    page_index: int = Field(ge=0)
    block_index: int = Field(ge=0)
    reading_order: int = Field(ge=0)
    content_type: ContentType
    raw_text: str = Field(min_length=1)
    text: str = Field(min_length=1)
    bbox: BoundingBox
    style: TextStyle | None = None
    source: Literal["text_layer", "ocr"]
    confidence: float | None = Field(default=None, ge=0, le=1)


class Page(StrictModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    page_index: int = Field(ge=0)
    printed_page_number: str | None = None
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    rotation: Literal[0, 90, 180, 270] = 0
    page_type: Literal["text", "scanned", "mixed"]
    ocr_used: bool = False
    quality_flags: list[str] = Field(default_factory=list)
    blocks: list[TextBlock]

    @model_validator(mode="after")
    def validate_blocks(self) -> "Page":
        block_ids = [block.block_id for block in self.blocks]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("block_id must be unique within a page")
        for block in self.blocks:
            if block.page_index != self.page_index:
                raise ValueError("block.page_index must match its containing page")
            if block.bbox.x1 > self.width or block.bbox.y1 > self.height:
                raise ValueError("block bbox must stay inside the page")
        return self


class Book(StrictModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    book_id: UUID
    title: str = Field(min_length=1)
    authors: list[str] = Field(default_factory=list)
    language: str = Field(min_length=2)
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    original_file_name: str = Field(min_length=1)
    mime_type: Literal["application/pdf"]
    size_bytes: int = Field(gt=0)
    sha256: Sha256 = Field(pattern=r"^[a-f0-9]{64}$")
    storage_uri: str = Field(min_length=1)
    page_count: int = Field(gt=0)
    status: BookStatus
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def validate_timestamps(self) -> "Book":
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        return self


class ParsedDocument(StrictModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    document_id: UUID
    book_id: UUID
    parser_name: str = Field(min_length=1)
    parser_version: str = Field(min_length=1)
    parse_config_sha256: Sha256 = Field(pattern=r"^[a-f0-9]{64}$")
    pages: list[Page]
    resources: list[dict[str, Any]] = Field(default_factory=list)
    created_at: AwareDatetime

    @model_validator(mode="after")
    def validate_pages(self) -> "ParsedDocument":
        page_indexes = [page.page_index for page in self.pages]
        if len(page_indexes) != len(set(page_indexes)):
            raise ValueError("page_index must be unique within a document")
        if page_indexes != sorted(page_indexes):
            raise ValueError("pages must be sorted by page_index")
        return self


class Chapter(StrictModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    chapter_id: UUID
    book_id: UUID
    parent_id: UUID | None = None
    title: str = Field(min_length=1)
    normalized_title: str = Field(min_length=1)
    level: int = Field(ge=1)
    order_index: int = Field(ge=0)
    page_start: int = Field(ge=0)
    page_end: int = Field(ge=0)
    source_block_id: UUID
    confidence: float = Field(ge=0, le=1)
    manually_verified: bool = False

    @model_validator(mode="after")
    def validate_page_range(self) -> "Chapter":
        if self.page_end < self.page_start:
            raise ValueError("chapter page_end cannot precede page_start")
        if self.parent_id == self.chapter_id:
            raise ValueError("chapter cannot be its own parent")
        return self


class Chunk(StrictModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    chunk_id: UUID
    book_id: UUID
    chapter_id: UUID
    chapter_path: list[str] = Field(min_length=1)
    text: str = Field(min_length=1)
    raw_text: str = Field(min_length=1)
    page_start: int = Field(ge=0)
    page_end: int = Field(ge=0)
    source_block_ids: list[UUID] = Field(min_length=1)
    previous_chunk_id: UUID | None = None
    next_chunk_id: UUID | None = None
    token_count: int = Field(gt=0)
    content_type: ContentType
    chunk_version: str = Field(min_length=1)
    checksum: Sha256 = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_links_and_range(self) -> "Chunk":
        if self.page_end < self.page_start:
            raise ValueError("chunk page_end cannot precede page_start")
        if self.chunk_id in {self.previous_chunk_id, self.next_chunk_id}:
            raise ValueError("chunk cannot link to itself")
        if len(self.source_block_ids) != len(set(self.source_block_ids)):
            raise ValueError("source_block_ids cannot contain duplicates")
        return self


class ErrorDetail(StrictModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    error_id: UUID
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]+$")
    category: ErrorCategory
    message: str = Field(min_length=1)
    stage: ProcessingStage
    retryable: bool
    page_index: int | None = Field(default=None, ge=0)
    details: dict[str, Any] = Field(default_factory=dict)
    trace_id: str = Field(min_length=1)
    occurred_at: AwareDatetime


class ParsingJob(StrictModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    job_id: UUID
    book_id: UUID
    status: JobStatus
    current_stage: ProcessingStage
    progress_percent: int = Field(ge=0, le=100)
    attempt: int = Field(ge=1)
    max_attempts: int = Field(ge=1)
    completed_stages: list[ProcessingStage] = Field(default_factory=list)
    checkpoint: dict[str, Any] = Field(default_factory=dict)
    error: ErrorDetail | None = None
    created_at: AwareDatetime
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def validate_state(self) -> "ParsingJob":
        if self.attempt > self.max_attempts:
            raise ValueError("attempt cannot exceed max_attempts")
        if self.status == JobStatus.SUCCEEDED and self.progress_percent != 100:
            raise ValueError("a succeeded job must have 100 percent progress")
        if self.status == JobStatus.FAILED and self.error is None:
            raise ValueError("a failed job must include an error")
        if self.finished_at and self.started_at and self.finished_at < self.started_at:
            raise ValueError("finished_at cannot precede started_at")
        return self


class DocumentProcessingSnapshot(StrictModel):
    """A transport fixture and cross-object integrity boundary.

    Online APIs may return the component models separately. This aggregate is
    useful for fixtures, exports, integration tests, and reproducible evaluation.
    """

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    book: Book
    document: ParsedDocument
    chapters: list[Chapter]
    chunks: list[Chunk]
    job: ParsingJob
    errors: list[ErrorDetail] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_references(self) -> "DocumentProcessingSnapshot":
        book_id = self.book.book_id
        if self.document.book_id != book_id or self.job.book_id != book_id:
            raise ValueError("document and job must reference snapshot book_id")
        if len(self.document.pages) != self.book.page_count:
            raise ValueError("book.page_count must match document pages")

        page_indexes = {page.page_index for page in self.document.pages}
        blocks = {
            block.block_id: block
            for page in self.document.pages
            for block in page.blocks
        }
        chapter_by_id = {chapter.chapter_id: chapter for chapter in self.chapters}
        chunk_by_id = {chunk.chunk_id: chunk for chunk in self.chunks}

        if len(chapter_by_id) != len(self.chapters):
            raise ValueError("chapter_id must be unique")
        if len(chunk_by_id) != len(self.chunks):
            raise ValueError("chunk_id must be unique")

        for chapter in self.chapters:
            if chapter.book_id != book_id:
                raise ValueError("chapter must reference snapshot book_id")
            if chapter.parent_id and chapter.parent_id not in chapter_by_id:
                raise ValueError("chapter parent_id does not exist")
            if chapter.source_block_id not in blocks:
                raise ValueError("chapter source_block_id does not exist")
            if chapter.page_start not in page_indexes or chapter.page_end not in page_indexes:
                raise ValueError("chapter page range references a missing page")

        for chunk in self.chunks:
            if chunk.book_id != book_id:
                raise ValueError("chunk must reference snapshot book_id")
            if chunk.chapter_id not in chapter_by_id:
                raise ValueError("chunk chapter_id does not exist")
            if chunk.page_start not in page_indexes or chunk.page_end not in page_indexes:
                raise ValueError("chunk page range references a missing page")
            for block_id in chunk.source_block_ids:
                block = blocks.get(block_id)
                if block is None:
                    raise ValueError("chunk source_block_id does not exist")
                if not chunk.page_start <= block.page_index <= chunk.page_end:
                    raise ValueError("chunk source block falls outside its page range")
            if chunk.previous_chunk_id and chunk.previous_chunk_id not in chunk_by_id:
                raise ValueError("previous_chunk_id does not exist")
            if chunk.next_chunk_id and chunk.next_chunk_id not in chunk_by_id:
                raise ValueError("next_chunk_id does not exist")

        return self
