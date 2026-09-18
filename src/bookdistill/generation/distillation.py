"""Evidence-grounded knowledge-distillation application service."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TypeVar
from uuid import UUID

from pydantic import BaseModel

from bookdistill.contracts import (
    ArtifactTrace,
    Citation,
    KnowledgeDraft,
    KnowledgeRequest,
    KnowledgeResponse,
    LLMRequest,
    QuestionDraft,
    QuestionRequest,
    QuestionResponse,
    SummaryDraft,
    SummaryRequest,
    SummaryResponse,
    TaskType,
    ViewpointDraft,
    ViewpointRequest,
    ViewpointResponse,
)
from bookdistill.contracts.retrieval import Evidence

from .errors import InvalidCitationError, InvalidModelOutputError
from .gateway import LLMGateway


DraftT = TypeVar("DraftT", bound=BaseModel)


class DistillationService:
    prompt_versions = {
        TaskType.SUMMARY: "summary.v1",
        TaskType.KNOWLEDGE_EXTRACTION: "knowledge-extraction.v1",
        TaskType.QUESTION_GENERATION: "question-generation.v1",
        TaskType.VIEWPOINT_COMPARISON: "viewpoint-comparison.v1",
    }

    def __init__(self, gateway: LLMGateway) -> None:
        self._gateway = gateway

    def summarize(self, request: SummaryRequest) -> SummaryResponse:
        llm, draft = self._run(
            TaskType.SUMMARY, request.payload, request.trace_context,
            request.generation_config, SummaryDraft,
        )
        cited = [item for section in draft.sections for item in section.evidence_ids]
        cited += [item for takeaway in draft.key_takeaways for item in takeaway.evidence_ids]
        character_count = len(draft.overview) + sum(
            len(item.heading) + len(item.content) for item in draft.sections
        ) + sum(len(item.text) for item in draft.key_takeaways)
        if character_count > request.payload.max_characters:
            raise InvalidModelOutputError(
                "summary exceeds requested max_characters",
                repair_attempted=llm.repair_attempted,
            )
        citations = _citations(request.payload.evidence, cited)
        return SummaryResponse(
            **draft.model_dump(), citations=citations, trace=_trace(llm)
        )

    def extract_knowledge(self, request: KnowledgeRequest) -> KnowledgeResponse:
        llm, draft = self._run(
            TaskType.KNOWLEDGE_EXTRACTION, request.payload, request.trace_context,
            request.generation_config, KnowledgeDraft,
        )
        if len(draft.points) > request.payload.maximum_points:
            raise InvalidModelOutputError(
                "model returned more knowledge points than requested",
                repair_attempted=llm.repair_attempted,
            )
        concepts = [item.concept.strip().casefold() for item in draft.points]
        if len(concepts) != len(set(concepts)):
            raise InvalidModelOutputError(
                "model returned duplicate knowledge concepts",
                repair_attempted=llm.repair_attempted,
            )
        cited = [item for point in draft.points for item in point.evidence_ids]
        return KnowledgeResponse(
            **draft.model_dump(),
            citations=_citations(request.payload.evidence, cited),
            trace=_trace(llm),
        )

    def generate_questions(self, request: QuestionRequest) -> QuestionResponse:
        llm, draft = self._run(
            TaskType.QUESTION_GENERATION, request.payload, request.trace_context,
            request.generation_config, QuestionDraft,
        )
        if len(draft.questions) != request.payload.count:
            raise InvalidModelOutputError(
                "model returned a different question count than requested",
                repair_attempted=llm.repair_attempted,
            )
        allowed = set(request.payload.types)
        if any(item.question_type not in allowed for item in draft.questions):
            raise InvalidModelOutputError(
                "model returned a question type that was not requested",
                repair_attempted=llm.repair_attempted,
            )
        if request.payload.difficulty != "mixed" and any(
            item.difficulty != request.payload.difficulty for item in draft.questions
        ):
            raise InvalidModelOutputError(
                "model returned a difficulty that was not requested",
                repair_attempted=llm.repair_attempted,
            )
        cited = [item for question in draft.questions for item in question.evidence_ids]
        return QuestionResponse(
            **draft.model_dump(),
            citations=_citations(request.payload.evidence, cited),
            trace=_trace(llm),
        )

    def compare_viewpoints(self, request: ViewpointRequest) -> ViewpointResponse:
        llm, draft = self._run(
            TaskType.VIEWPOINT_COMPARISON, request.payload, request.trace_context,
            request.generation_config, ViewpointDraft,
        )
        groups = {item.book_id: item for item in request.payload.books}
        returned = {item.book_id for item in draft.per_book}
        if returned != set(groups) or len(returned) != len(draft.per_book):
            raise InvalidModelOutputError(
                "comparison must return exactly one viewpoint for every requested book",
                repair_attempted=llm.repair_attempted,
            )
        all_evidence = [item for group in request.payload.books for item in group.evidence]
        cited: list[UUID] = []
        for viewpoint in draft.per_book:
            group = groups[viewpoint.book_id]
            if viewpoint.book_title != group.book_title:
                raise InvalidModelOutputError(
                    "comparison changed a supplied book title",
                    repair_attempted=llm.repair_attempted,
                )
            allowed_ids = {item.evidence_id for item in group.evidence}
            invalid = [str(item) for item in viewpoint.evidence_ids if item not in allowed_ids]
            if invalid:
                raise InvalidCitationError(invalid)
            cited.extend(viewpoint.evidence_ids)
        for section in (draft.commonalities, draft.differences, draft.applicability):
            cited.extend(item for statement in section for item in statement.evidence_ids)
        return ViewpointResponse(
            **draft.model_dump(), citations=_citations(all_evidence, cited), trace=_trace(llm)
        )

    def _run(
        self,
        task_type: TaskType,
        payload: BaseModel,
        trace_context,
        generation_config,
        draft_model: type[DraftT],
    ):
        request = LLMRequest(
            task_type=task_type,
            prompt_version=self.prompt_versions[task_type],
            input=payload.model_dump(mode="json"),
            constraints=[
                "Use only supplied evidence.",
                "Every factual item requires supplied evidence IDs.",
            ],
            output_schema=draft_model.model_json_schema(),
            generation_config=generation_config,
            trace_context=trace_context,
        )
        return self._gateway.generate(request, draft_model)


def _citations(evidence: list[Evidence], cited_ids: list[UUID]) -> list[Citation]:
    by_id = {item.evidence_id: item for item in evidence}
    unique = set(cited_ids)
    invalid = sorted(str(item) for item in unique if item not in by_id)
    if invalid:
        raise InvalidCitationError(invalid)
    return [
        Citation(
            evidence_id=item.evidence_id,
            chunk_id=item.chunk_id,
            book_id=item.book_id,
            book_title=item.book_title,
            chapter_id=item.chapter_id,
            chapter_path=item.chapter_path,
            page_start=item.page_start,
            page_end=item.page_end,
            quote=item.text,
        )
        for item in sorted((by_id[item] for item in unique), key=lambda value: str(value.evidence_id))
    ]


def _trace(response) -> ArtifactTrace:
    return ArtifactTrace(
        model_provider=response.model_provider,
        model_name=response.model_name,
        prompt_version=response.prompt_version,
        usage=response.usage,
        latency_ms=response.latency_ms,
        provider_request_id=response.request_id,
        repair_attempted=response.repair_attempted,
        created_at=datetime.now(UTC),
    )
