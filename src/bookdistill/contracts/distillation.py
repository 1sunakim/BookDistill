"""Contracts for evidence-grounded summaries, knowledge, questions and comparisons."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from .generation import (
    Citation,
    GENERATION_SCHEMA_VERSION,
    GenerationConfig,
    GenerationModel,
    ModelUsage,
    TraceContext,
)
from .retrieval import Evidence


class EvidencePayload(GenerationModel):
    evidence: list[Evidence] = Field(min_length=1, max_length=100)
    language: str = Field(default="zh-CN", min_length=2, max_length=35)

    @model_validator(mode="after")
    def unique_evidence(self) -> "EvidencePayload":
        ids = [item.evidence_id for item in self.evidence]
        if len(ids) != len(set(ids)):
            raise ValueError("evidence IDs must be unique")
        return self


class CitedStatement(GenerationModel):
    text: str = Field(min_length=1)
    evidence_ids: list[UUID] = Field(min_length=1)


class ArtifactTrace(GenerationModel):
    model_provider: str
    model_name: str
    prompt_version: str
    usage: ModelUsage
    latency_ms: float = Field(ge=0)
    provider_request_id: str
    repair_attempted: bool = False
    created_at: datetime


class SummaryPayload(EvidencePayload):
    title: str = Field(min_length=1, max_length=1000)
    scope_type: Literal["chapter", "book"] = "chapter"
    depth: Literal["brief", "detailed", "review"] = "detailed"
    max_characters: int = Field(default=5000, ge=100, le=30_000)


class SummarySection(GenerationModel):
    heading: str = Field(min_length=1)
    content: str = Field(min_length=1)
    evidence_ids: list[UUID] = Field(min_length=1)


class SummaryDraft(GenerationModel):
    title: str = Field(min_length=1)
    overview: str = Field(min_length=1)
    sections: list[SummarySection] = Field(min_length=1)
    key_takeaways: list[CitedStatement] = Field(min_length=1)


class SummaryRequest(GenerationModel):
    schema_version: Literal["1.0"] = GENERATION_SCHEMA_VERSION
    trace_context: TraceContext
    payload: SummaryPayload
    generation_config: GenerationConfig = Field(default_factory=GenerationConfig)


class SummaryResponse(SummaryDraft):
    schema_version: Literal["1.0"] = GENERATION_SCHEMA_VERSION
    citations: list[Citation]
    trace: ArtifactTrace


class KnowledgePayload(EvidencePayload):
    maximum_points: int = Field(default=12, ge=1, le=50)


class KnowledgePoint(GenerationModel):
    concept: str = Field(min_length=1)
    definition: str = Field(min_length=1)
    attributes: list[str] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)
    related_concepts: list[str] = Field(default_factory=list)
    misconceptions: list[str] = Field(default_factory=list)
    evidence_ids: list[UUID] = Field(min_length=1)


class KnowledgeDraft(GenerationModel):
    points: list[KnowledgePoint] = Field(min_length=1)


class KnowledgeRequest(GenerationModel):
    schema_version: Literal["1.0"] = GENERATION_SCHEMA_VERSION
    trace_context: TraceContext
    payload: KnowledgePayload
    generation_config: GenerationConfig = Field(default_factory=GenerationConfig)


class KnowledgeResponse(KnowledgeDraft):
    schema_version: Literal["1.0"] = GENERATION_SCHEMA_VERSION
    citations: list[Citation]
    trace: ArtifactTrace


class QuestionPayload(EvidencePayload):
    count: int = Field(default=5, ge=1, le=30)
    types: list[Literal["fact", "concept", "comparison", "application", "true_false", "multiple_choice"]] = Field(
        default_factory=lambda: ["concept"]
    )
    difficulty: Literal["easy", "medium", "hard", "mixed"] = "medium"

    @model_validator(mode="after")
    def validate_types(self) -> "QuestionPayload":
        if not self.types:
            raise ValueError("at least one question type is required")
        if len(self.types) != len(set(self.types)):
            raise ValueError("question types must be unique")
        return self


class GeneratedQuestion(GenerationModel):
    stem: str = Field(min_length=1)
    question_type: Literal["fact", "concept", "comparison", "application", "true_false", "multiple_choice"]
    difficulty: Literal["easy", "medium", "hard"]
    options: list[str] = Field(default_factory=list)
    answer: str = Field(min_length=1)
    explanation: str = Field(min_length=1)
    evidence_ids: list[UUID] = Field(min_length=1)


class QuestionDraft(GenerationModel):
    questions: list[GeneratedQuestion] = Field(min_length=1)


class QuestionRequest(GenerationModel):
    schema_version: Literal["1.0"] = GENERATION_SCHEMA_VERSION
    trace_context: TraceContext
    payload: QuestionPayload
    generation_config: GenerationConfig = Field(default_factory=GenerationConfig)


class QuestionResponse(QuestionDraft):
    schema_version: Literal["1.0"] = GENERATION_SCHEMA_VERSION
    citations: list[Citation]
    trace: ArtifactTrace


class BookEvidenceGroup(EvidencePayload):
    book_id: UUID
    book_title: str = Field(min_length=1)

    @model_validator(mode="after")
    def evidence_belongs_to_book(self) -> "BookEvidenceGroup":
        if any(item.book_id != self.book_id for item in self.evidence):
            raise ValueError("all grouped evidence must belong to book_id")
        return self


class ViewpointPayload(GenerationModel):
    question: str = Field(min_length=1, max_length=4000)
    books: list[BookEvidenceGroup] = Field(min_length=2, max_length=10)
    language: str = Field(default="zh-CN", min_length=2, max_length=35)

    @model_validator(mode="after")
    def unique_books_and_evidence(self) -> "ViewpointPayload":
        book_ids = [item.book_id for item in self.books]
        if len(book_ids) != len(set(book_ids)):
            raise ValueError("book groups must be unique")
        evidence_ids = [e.evidence_id for book in self.books for e in book.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence cannot appear in multiple book groups")
        return self


class BookViewpoint(GenerationModel):
    book_id: UUID
    book_title: str = Field(min_length=1)
    viewpoint: str = Field(min_length=1)
    evidence_ids: list[UUID] = Field(min_length=1)


class ViewpointDraft(GenerationModel):
    per_book: list[BookViewpoint] = Field(min_length=2)
    commonalities: list[CitedStatement] = Field(default_factory=list)
    differences: list[CitedStatement] = Field(default_factory=list)
    applicability: list[CitedStatement] = Field(default_factory=list)


class ViewpointRequest(GenerationModel):
    schema_version: Literal["1.0"] = GENERATION_SCHEMA_VERSION
    trace_context: TraceContext
    payload: ViewpointPayload
    generation_config: GenerationConfig = Field(default_factory=GenerationConfig)


class ViewpointResponse(ViewpointDraft):
    schema_version: Literal["1.0"] = GENERATION_SCHEMA_VERSION
    citations: list[Citation]
    trace: ArtifactTrace
