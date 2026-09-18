"""Application service for the minimum trustworthy-answer loop."""

from __future__ import annotations

from datetime import UTC, datetime

from bookdistill.contracts.generation import (
    Citation, LLMRequest, ModelUsage, RagAnswerDraft, RagAnswerRequest,
    RagAnswerResponse, TaskType,
)
from bookdistill.contracts.retrieval import Evidence
from .errors import InvalidCitationError
from .gateway import LLMGateway


class RagAnswerService:
    prompt_version = "rag-answer.v1"

    def __init__(self, gateway: LLMGateway) -> None:
        self._gateway = gateway

    def answer(self, request: RagAnswerRequest) -> RagAnswerResponse:
        if not request.payload.evidence:
            return self._refusal(request)

        llm_request = LLMRequest(
            task_type=TaskType.RAG_ANSWER,
            prompt_version=self.prompt_version,
            input=request.payload.model_dump(mode="json"),
            constraints=[
                "Use only supplied evidence.",
                "Cite only supplied evidence IDs.",
                "Every supported claim requires at least one citation.",
            ],
            output_schema=RagAnswerDraft.model_json_schema(),
            generation_config=request.generation_config,
            trace_context=request.trace_context,
        )
        llm_response, draft = self._gateway.generate(llm_request, RagAnswerDraft)
        evidence_by_id = {item.evidence_id: item for item in request.payload.evidence}
        cited_ids = {item for claim in draft.claims for item in claim.evidence_ids}
        invalid = sorted(str(item) for item in cited_ids if item not in evidence_by_id)
        if invalid:
            raise InvalidCitationError(invalid)
        citations = [self._citation(evidence_by_id[item]) for item in sorted(cited_ids, key=str)]
        return RagAnswerResponse(
            answer=draft.answer,
            claims=draft.claims,
            citations=citations,
            insufficient_evidence=draft.insufficient_evidence,
            follow_up=draft.follow_up,
            model_provider=llm_response.model_provider,
            model_name=llm_response.model_name,
            prompt_version=llm_response.prompt_version,
            usage=llm_response.usage,
            latency_ms=llm_response.latency_ms,
            provider_request_id=llm_response.request_id,
            repair_attempted=llm_response.repair_attempted,
            created_at=datetime.now(UTC),
        )

    @staticmethod
    def _citation(evidence: Evidence) -> Citation:
        return Citation(
            evidence_id=evidence.evidence_id, chunk_id=evidence.chunk_id,
            book_id=evidence.book_id, book_title=evidence.book_title,
            chapter_id=evidence.chapter_id, chapter_path=evidence.chapter_path,
            page_start=evidence.page_start, page_end=evidence.page_end,
            quote=evidence.text,
        )

    @staticmethod
    def _refusal(request: RagAnswerRequest) -> RagAnswerResponse:
        return RagAnswerResponse(
            answer="现有证据不足，无法依据所选书籍或章节回答该问题。",
            claims=[], citations=[], insufficient_evidence=True,
            follow_up="请扩大检索范围或补充相关资料。",
            model_provider="none", model_name="none",
            prompt_version=RagAnswerService.prompt_version,
            usage=ModelUsage(), latency_ms=0,
            provider_request_id=str(request.trace_context.request_id),
            created_at=datetime.now(UTC),
        )
