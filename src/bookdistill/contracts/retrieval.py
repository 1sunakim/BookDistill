"""Versioned contracts for Chunk-to-Evidence retrieval.

Document processing ends at ``Chunk``. This module only consumes that upstream
contract and intentionally contains no PDF parsing or chunking logic.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .document_processing import Chunk


RETRIEVAL_SCHEMA_VERSION = "1.0"


class RetrievalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class RetrievalScope(RetrievalModel):
    book_ids: list[UUID] = Field(default_factory=list)
    chapter_ids: list[UUID] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_ids(self) -> "RetrievalScope":
        if len(self.book_ids) != len(set(self.book_ids)):
            raise ValueError("book_ids cannot contain duplicates")
        if len(self.chapter_ids) != len(set(self.chapter_ids)):
            raise ValueError("chapter_ids cannot contain duplicates")
        return self


class RetrievalConfig(RetrievalModel):
    variant: Literal["sparse", "dense", "hybrid", "hybrid_rerank"] = "hybrid_rerank"
    dense_top_k: int = Field(default=30, ge=1, le=200)
    sparse_top_k: int = Field(default=30, ge=1, le=200)
    fusion_candidates: int = Field(default=40, ge=1, le=200)
    rerank_candidates: int = Field(default=30, ge=1, le=200)
    final_evidence: int = Field(default=6, ge=1, le=20)
    rrf_k: int = Field(default=60, ge=1, le=1000)
    max_per_chapter: int = Field(default=3, ge=1, le=20)
    context_window: int = Field(default=1, ge=0, le=3)
    context_token_budget: int = Field(default=3000, ge=128, le=32000)
    max_context_tokens_per_evidence: int = Field(default=800, ge=64, le=8000)

    @model_validator(mode="after")
    def validate_candidate_sizes(self) -> "RetrievalConfig":
        if self.rerank_candidates > self.fusion_candidates:
            raise ValueError("rerank_candidates cannot exceed fusion_candidates")
        if self.final_evidence > self.rerank_candidates:
            raise ValueError("final_evidence cannot exceed rerank_candidates")
        return self


class RetrievalRequest(RetrievalModel):
    schema_version: Literal["1.0"] = RETRIEVAL_SCHEMA_VERSION
    request_id: UUID
    query: str = Field(min_length=1, max_length=4000)
    scope: RetrievalScope = Field(default_factory=RetrievalScope)
    config: RetrievalConfig = Field(default_factory=RetrievalConfig)


class CandidateTrace(RetrievalModel):
    chunk_id: UUID
    dense_score: float | None = None
    dense_rank: int | None = Field(default=None, ge=1)
    sparse_score: float | None = None
    sparse_rank: int | None = Field(default=None, ge=1)
    fusion_score: float = Field(ge=0)
    fusion_rank: int = Field(ge=1)
    rerank_score: float | None = None
    final_rank: int | None = Field(default=None, ge=1)


class Evidence(RetrievalModel):
    schema_version: Literal["1.0"] = RETRIEVAL_SCHEMA_VERSION
    evidence_id: UUID
    chunk_id: UUID
    book_id: UUID
    book_title: str | None = None
    chapter_id: UUID
    chapter_path: list[str] = Field(min_length=1)
    page_start: int = Field(ge=0)
    page_end: int = Field(ge=0)
    text: str = Field(min_length=1)
    dense_score: float | None = None
    dense_rank: int | None = Field(default=None, ge=1)
    sparse_score: float | None = None
    sparse_rank: int | None = Field(default=None, ge=1)
    fusion_score: float = Field(ge=0)
    fusion_rank: int = Field(ge=1)
    rerank_score: float | None = None
    final_rank: int = Field(ge=1)
    retrieval_version: str = Field(min_length=1)
    context_chunk_ids: list[UUID] = Field(default_factory=list)
    context_expanded: bool = False
    estimated_tokens: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_page_range(self) -> "Evidence":
        if self.page_end < self.page_start:
            raise ValueError("evidence page_end cannot precede page_start")
        return self


class RetrievalTrace(RetrievalModel):
    schema_version: Literal["1.0"] = RETRIEVAL_SCHEMA_VERSION
    original_query: str
    normalized_query: str
    retrieval_version: str
    retrieval_variant: Literal["sparse", "dense", "hybrid", "hybrid_rerank"]
    indexed_chunk_count: int = Field(ge=0)
    dense_latency_ms: float = Field(ge=0)
    sparse_latency_ms: float = Field(ge=0)
    fusion_latency_ms: float = Field(ge=0)
    rerank_latency_ms: float = Field(ge=0)
    total_latency_ms: float = Field(ge=0)
    rerank_degraded: bool = False
    rerank_applied: bool = False
    dense_status: Literal["skipped", "hit", "empty", "timeout", "error"] = "skipped"
    sparse_status: Literal["skipped", "hit", "empty", "timeout", "error"] = "skipped"
    rerank_status: Literal["skipped", "hit", "empty", "timeout", "error"] = "skipped"
    dense_hit_count: int = Field(default=0, ge=0)
    sparse_hit_count: int = Field(default=0, ge=0)
    rerank_input_count: int = Field(default=0, ge=0)
    dense_degraded: bool = False
    sparse_degraded: bool = False
    dense_timeout_ms: int = Field(default=0, ge=0)
    sparse_timeout_ms: int = Field(default=0, ge=0)
    rerank_timeout_ms: int = Field(default=0, ge=0)
    index_versions: list[str] = Field(default_factory=list)
    model_names: list[str] = Field(default_factory=list)
    chunk_versions: list[str] = Field(default_factory=list)
    index_generated_at: datetime | None = None
    context_token_count: int = Field(default=0, ge=0)
    candidates: list[CandidateTrace]


class RetrievalResponse(RetrievalModel):
    schema_version: Literal["1.0"] = RETRIEVAL_SCHEMA_VERSION
    request_id: UUID
    evidence: list[Evidence]
    trace: RetrievalTrace


class IndexChunksRequest(RetrievalModel):
    """Only integration boundary required from document processing."""

    chunks: list[Chunk] = Field(min_length=1)
    book_titles: dict[UUID, str] = Field(default_factory=dict)


class IndexChunksResponse(RetrievalModel):
    indexed_count: int = Field(ge=0)
