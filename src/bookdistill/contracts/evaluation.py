"""Versioned contracts for offline quality evaluation.

This module owns evaluation data only. Parsers, retrievers, and generators
integrate by exporting results to these contracts.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


EVALUATION_SCHEMA_VERSION = "1.0"


class EvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, use_enum_values=True)


class DatasetSplit(str, Enum):
    SMOKE = "smoke"
    DEV = "dev"
    TEST = "test"


class QuestionType(str, Enum):
    FACT = "fact"
    DEFINITION = "definition"
    PARAPHRASE = "paraphrase"
    ENTITY_OR_NUMBER = "entity_or_number"
    CROSS_PARAGRAPH = "cross_paragraph"
    CROSS_CHAPTER = "cross_chapter"
    MULTI_BOOK = "multi_book"
    UNANSWERABLE = "unanswerable"
    DISTRACTOR = "distractor"


class PageRange(EvaluationModel):
    book_id: UUID
    page_start: int = Field(ge=0)
    page_end: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_range(self) -> "PageRange":
        if self.page_end < self.page_start:
            raise ValueError("page_end cannot precede page_start")
        return self


class EvaluationCase(EvaluationModel):
    schema_version: Literal["1.0"] = EVALUATION_SCHEMA_VERSION
    case_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    question: str = Field(min_length=1, max_length=4000)
    question_type: QuestionType
    book_scope: list[UUID] = Field(min_length=1)
    chapter_scope: list[UUID] = Field(default_factory=list)
    reference_answer: str | None = None
    required_points: list[str] = Field(default_factory=list)
    relevant_chunk_ids: list[UUID] = Field(default_factory=list)
    relevant_page_ranges: list[PageRange] = Field(default_factory=list)
    difficulty: Literal["easy", "medium", "hard"]
    annotator: str = Field(min_length=1)
    review_status: Literal["draft", "reviewed", "approved"]
    is_answerable: bool = True
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_ground_truth(self) -> "EvaluationCase":
        if len(self.book_scope) != len(set(self.book_scope)):
            raise ValueError("book_scope cannot contain duplicates")
        if len(self.chapter_scope) != len(set(self.chapter_scope)):
            raise ValueError("chapter_scope cannot contain duplicates")
        if len(self.relevant_chunk_ids) != len(set(self.relevant_chunk_ids)):
            raise ValueError("relevant_chunk_ids cannot contain duplicates")
        if self.is_answerable and not self.relevant_chunk_ids:
            raise ValueError("answerable cases require relevant_chunk_ids")
        if not self.is_answerable and self.relevant_chunk_ids:
            raise ValueError("unanswerable cases cannot declare relevant_chunk_ids")
        return self


class EvaluationDataset(EvaluationModel):
    schema_version: Literal["1.0"] = EVALUATION_SCHEMA_VERSION
    dataset_name: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    split: DatasetSplit
    description: str | None = None
    cases: list[EvaluationCase] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_case_ids(self) -> "EvaluationDataset":
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("case_id must be unique within a dataset")
        return self


class RetrievedItem(EvaluationModel):
    chunk_id: UUID
    score: float | None = None


class RetrievalPrediction(EvaluationModel):
    schema_version: Literal["1.0"] = EVALUATION_SCHEMA_VERSION
    case_id: str
    retrieved: list[RetrievedItem] = Field(default_factory=list)
    latency_ms: float = Field(ge=0)
    error: str | None = None

    @model_validator(mode="after")
    def validate_ranking(self) -> "RetrievalPrediction":
        ids = [item.chunk_id for item in self.retrieved]
        if len(ids) != len(set(ids)):
            raise ValueError("retrieved chunk_ids must be unique and rank ordered")
        if self.error and self.retrieved:
            raise ValueError("failed predictions cannot contain retrieved items")
        return self


class RetrievalPredictionSet(EvaluationModel):
    schema_version: Literal["1.0"] = EVALUATION_SCHEMA_VERSION
    dataset_name: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    predictions: list[RetrievalPrediction]

    @model_validator(mode="after")
    def validate_prediction_ids(self) -> "RetrievalPredictionSet":
        ids = [prediction.case_id for prediction in self.predictions]
        if len(ids) != len(set(ids)):
            raise ValueError("prediction case_id must be unique")
        return self


class ExperimentConfig(EvaluationModel):
    schema_version: Literal["1.0"] = EVALUATION_SCHEMA_VERSION
    experiment_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    dataset_version: str = Field(min_length=1)
    retrieval_variant: Literal["sparse", "dense", "hybrid", "hybrid_rerank", "external"]
    parser_version: str = Field(min_length=1)
    chunk_version: str = Field(min_length=1)
    embedding_model: str | None = None
    sparse_config: dict[str, Any] = Field(default_factory=dict)
    fusion_config: dict[str, Any] = Field(default_factory=dict)
    reranker_model: str | None = None
    llm_model: str | None = None
    prompt_version: str | None = None
    generation_config: dict[str, Any] = Field(default_factory=dict)
    code_version: str | None = None
    notes: str | None = None


class CutoffMetric(EvaluationModel):
    k: int = Field(ge=1)
    recall: float = Field(ge=0, le=1)
    precision: float = Field(ge=0, le=1)


class CaseRetrievalMetrics(EvaluationModel):
    case_id: str
    question_type: QuestionType
    reciprocal_rank: float = Field(ge=0, le=1)
    ndcg_at_10: float = Field(ge=0, le=1)
    cutoffs: list[CutoffMetric]
    first_relevant_rank: int | None = Field(default=None, ge=1)
    latency_ms: float = Field(ge=0)
    passed_at_10: bool
    error: str | None = None


class AggregateRetrievalMetrics(EvaluationModel):
    evaluated_cases: int = Field(ge=0)
    excluded_unanswerable_cases: int = Field(ge=0)
    missing_or_failed_predictions: int = Field(ge=0)
    mrr: float = Field(ge=0, le=1)
    ndcg_at_10: float = Field(ge=0, le=1)
    cutoffs: list[CutoffMetric]
    latency_p50_ms: float = Field(ge=0)
    latency_p95_ms: float = Field(ge=0)


class RetrievalEvaluationResult(EvaluationModel):
    schema_version: Literal["1.0"] = EVALUATION_SCHEMA_VERSION
    experiment: ExperimentConfig
    dataset_name: str
    dataset_version: str
    created_at: AwareDatetime
    aggregate: AggregateRetrievalMetrics
    by_question_type: dict[str, AggregateRetrievalMetrics]
    cases: list[CaseRetrievalMetrics]


class AblationMetricSnapshot(EvaluationModel):
    recall_at_10: float = Field(ge=0, le=1)
    mrr: float = Field(ge=0, le=1)
    ndcg_at_10: float = Field(ge=0, le=1)
    latency_p50_ms: float = Field(ge=0)
    latency_p95_ms: float = Field(ge=0)
    missing_or_failed_predictions: int = Field(ge=0)


class AblationVariantResult(EvaluationModel):
    retrieval_variant: Literal["sparse", "dense", "hybrid", "hybrid_rerank"]
    experiment_id: str
    metrics: AblationMetricSnapshot
    recall_at_10_delta_vs_sparse: float
    mrr_delta_vs_sparse: float
    ndcg_at_10_delta_vs_sparse: float
    latency_p95_delta_vs_sparse_ms: float


class RetrievalAblationReport(EvaluationModel):
    schema_version: Literal["1.0"] = EVALUATION_SCHEMA_VERSION
    dataset_name: str
    dataset_version: str
    created_at: AwareDatetime
    variants: list[AblationVariantResult] = Field(min_length=4, max_length=4)
    best_recall_at_10_variant: str
    best_ndcg_at_10_variant: str
    fastest_p95_variant: str
