"""Versioned contracts for evidence-grounded generation.

This boundary starts at ``Evidence`` and deliberately knows nothing about how
evidence was retrieved. The model may cite stable evidence IDs only; source
metadata is enriched by application code.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .retrieval import Evidence, RetrievalConfig, RetrievalResponse, RetrievalScope


GENERATION_SCHEMA_VERSION = "1.0"


class GenerationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class TaskType(StrEnum):
    RAG_ANSWER = "rag_answer"
    SUMMARY = "summary"
    KNOWLEDGE_EXTRACTION = "knowledge_extraction"
    QUESTION_GENERATION = "question_generation"
    VIEWPOINT_COMPARISON = "viewpoint_comparison"


class GenerationConfig(GenerationModel):
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=1200, ge=1, le=16_000)


class TraceContext(GenerationModel):
    request_id: UUID
    trace_id: UUID


class ConversationTurn(GenerationModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=20_000)


class RagAnswerPayload(GenerationModel):
    question: str = Field(min_length=1, max_length=4000)
    scope: RetrievalScope = Field(default_factory=RetrievalScope)
    evidence: list[Evidence] = Field(default_factory=list, max_length=20)
    language: str = Field(default="bilingual", min_length=2, max_length=35)
    max_answer_characters: int = Field(default=3000, ge=1, le=20_000)
    conversation_history: list[ConversationTurn] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def validate_evidence(self) -> "RagAnswerPayload":
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence IDs must be unique")
        if self.scope.book_ids and any(
            item.book_id not in self.scope.book_ids for item in self.evidence
        ):
            raise ValueError("evidence contains a book outside the requested scope")
        if self.scope.chapter_ids and any(
            item.chapter_id not in self.scope.chapter_ids for item in self.evidence
        ):
            raise ValueError("evidence contains a chapter outside the requested scope")
        return self


class LLMRequest(GenerationModel):
    """Provider-neutral request envelope with a task-specific payload."""

    schema_version: Literal["1.0"] = GENERATION_SCHEMA_VERSION
    task_type: TaskType
    prompt_version: str = Field(min_length=1)
    input: dict[str, Any]
    constraints: list[str] = Field(default_factory=list)
    output_schema: dict[str, Any]
    generation_config: GenerationConfig = Field(default_factory=GenerationConfig)
    trace_context: TraceContext


class ModelUsage(GenerationModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class LLMResponse(GenerationModel):
    schema_version: Literal["1.0"] = GENERATION_SCHEMA_VERSION
    task_type: TaskType
    content: dict[str, Any]
    finish_reason: str
    model_provider: str
    model_name: str
    prompt_version: str
    usage: ModelUsage = Field(default_factory=ModelUsage)
    latency_ms: float = Field(ge=0)
    request_id: str = Field(min_length=1)
    raw_response_reference: str | None = None
    repair_attempted: bool = False


class AnswerClaim(GenerationModel):
    text: str = Field(min_length=1)
    evidence_ids: list[UUID] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_evidence_ids(self) -> "AnswerClaim":
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("claim evidence_ids must be unique")
        return self


class RagAnswerDraft(GenerationModel):
    """The only answer shape accepted from a model provider."""

    answer: str = Field(min_length=1)
    claims: list[AnswerClaim] = Field(default_factory=list)
    insufficient_evidence: bool
    follow_up: str | None = None

    @model_validator(mode="after")
    def validate_answer_state(self) -> "RagAnswerDraft":
        if self.insufficient_evidence and self.claims:
            raise ValueError("an insufficient-evidence answer cannot contain claims")
        if not self.insufficient_evidence and not self.claims:
            raise ValueError("a supported answer must contain at least one cited claim")
        return self


class Citation(GenerationModel):
    evidence_id: UUID
    chunk_id: UUID
    book_id: UUID
    book_title: str | None = None
    chapter_id: UUID
    chapter_path: list[str] = Field(min_length=1)
    page_start: int = Field(ge=0)
    page_end: int = Field(ge=0)
    quote: str = Field(min_length=1)


class RagAnswerRequest(GenerationModel):
    schema_version: Literal["1.0"] = GENERATION_SCHEMA_VERSION
    trace_context: TraceContext
    payload: RagAnswerPayload
    generation_config: GenerationConfig = Field(default_factory=GenerationConfig)


class RagAnswerResponse(GenerationModel):
    schema_version: Literal["1.0"] = GENERATION_SCHEMA_VERSION
    answer: str
    claims: list[AnswerClaim]
    citations: list[Citation]
    insufficient_evidence: bool
    follow_up: str | None = None
    model_provider: str
    model_name: str
    prompt_version: str
    usage: ModelUsage
    latency_ms: float = Field(ge=0)
    provider_request_id: str
    repair_attempted: bool = False
    created_at: datetime

    @model_validator(mode="after")
    def validate_answer_state_and_citations(self) -> "RagAnswerResponse":
        if self.insufficient_evidence:
            if self.claims or self.citations:
                raise ValueError(
                    "an insufficient-evidence response cannot contain claims or citations"
                )
            return self
        if not self.claims or not self.citations:
            raise ValueError(
                "a supported response requires claims and enriched citations"
            )
        claimed = {item for claim in self.claims for item in claim.evidence_ids}
        cited = {item.evidence_id for item in self.citations}
        if claimed != cited:
            raise ValueError("response citations must exactly match claimed evidence IDs")
        return self


class RetrieveAndAnswerRequest(GenerationModel):
    """Composition contract; retrieval and generation remain separate internally."""

    schema_version: Literal["1.0"] = GENERATION_SCHEMA_VERSION
    trace_context: TraceContext
    question: str = Field(min_length=1, max_length=4000)
    scope: RetrievalScope = Field(default_factory=RetrievalScope)
    retrieval_config: RetrievalConfig = Field(default_factory=RetrievalConfig)
    generation_config: GenerationConfig = Field(default_factory=GenerationConfig)
    language: str = Field(default="bilingual", min_length=2, max_length=35)
    max_answer_characters: int = Field(default=3000, ge=1, le=20_000)
    conversation_id: UUID | None = None
    history_window: int = Field(default=6, ge=0, le=12)


class RetrieveAndAnswerResponse(GenerationModel):
    schema_version: Literal["1.0"] = GENERATION_SCHEMA_VERSION
    retrieval: RetrievalResponse
    answer: RagAnswerResponse
