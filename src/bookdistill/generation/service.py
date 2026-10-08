"""Application service for the minimum trustworthy-answer loop."""

from __future__ import annotations

from datetime import UTC, datetime
from collections.abc import Callable
import re

from bookdistill.contracts.generation import (
    Citation, LLMRequest, ModelUsage, RagAnswerDraft, RagAnswerRequest,
    RagAnswerResponse, TaskType,
)
from bookdistill.contracts.retrieval import Evidence
from .errors import InvalidCitationError, InvalidModelOutputError
from .gateway import LLMGateway


class RagAnswerService:
    prompt_version = "rag-answer.v1"

    def __init__(self, gateway: LLMGateway, *,
                 source_validator: Callable[[list[Evidence]], None] | None = None,
                 minimum_claim_support_overlap: float = 0.05) -> None:
        self._gateway = gateway
        self.source_validator = source_validator
        self.minimum_claim_support_overlap = minimum_claim_support_overlap

    def answer(self, request: RagAnswerRequest) -> RagAnswerResponse:
        if not request.payload.evidence:
            return self._refusal(request)
        if self.source_validator:
            self.source_validator(request.payload.evidence)

        allowed_evidence_ids = [str(item.evidence_id) for item in request.payload.evidence]
        output_schema = RagAnswerDraft.model_json_schema()
        output_schema["$defs"]["AnswerClaim"]["properties"]["evidence_ids"]["items"][
            "enum"
        ] = allowed_evidence_ids
        llm_request = LLMRequest(
            task_type=TaskType.RAG_ANSWER,
            prompt_version=self.prompt_version,
            input=request.payload.model_dump(mode="json"),
            constraints=[
                "Use only supplied evidence.",
                "Cite only supplied evidence IDs.",
                f"Only cite these exact evidence IDs: {', '.join(allowed_evidence_ids)}.",
                "Every supported claim requires at least one citation.",
            ],
            output_schema=output_schema,
            generation_config=request.generation_config,
            trace_context=request.trace_context,
        )
        llm_response, draft = self._gateway.generate(llm_request, RagAnswerDraft)
        if len(draft.answer) > request.payload.max_answer_characters:
            raise InvalidModelOutputError(
                "model answer exceeds max_answer_characters",
                repair_attempted=llm_response.repair_attempted,
                details={
                    "answer_characters": len(draft.answer),
                    "max_answer_characters": request.payload.max_answer_characters,
                },
            )
        evidence_by_id = {item.evidence_id: item for item in request.payload.evidence}
        cited_ids = {item for claim in draft.claims for item in claim.evidence_ids}
        invalid = sorted(str(item) for item in cited_ids if item not in evidence_by_id)
        if invalid:
            raise InvalidCitationError(invalid, details={
                "repair_attempted": llm_response.repair_attempted,
                "usage": llm_response.usage.model_dump(),
                "response_reference": llm_response.raw_response_reference,
            })
        unsupported = [claim for claim in draft.claims if not self._claim_supported(
            claim.text, [evidence_by_id[item].text for item in claim.evidence_ids]
        )]
        if unsupported:
            return self._unsupported_refusal(request, llm_response)
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

    def _claim_supported(self, claim: str, evidence: list[str]) -> bool:
        def tokens(value: str) -> set[str]:
            return set(re.findall(r"[a-zA-Z0-9_]+|[\u3400-\u9fff]", value.casefold()))
        claim_tokens = tokens(claim)
        if not claim_tokens:
            return False
        evidence_tokens = tokens(" ".join(evidence))
        return len(claim_tokens & evidence_tokens) / len(claim_tokens) >= self.minimum_claim_support_overlap

    @staticmethod
    def _unsupported_refusal(request: RagAnswerRequest, llm_response) -> RagAnswerResponse:
        return RagAnswerResponse(
            answer="生成内容未通过证据支持校验，系统已降级为拒答。 / The answer failed evidence validation.",
            claims=[], citations=[], insufficient_evidence=True,
            follow_up="请调整问题或扩大检索范围。 / Refine the question or widen the scope.",
            model_provider=llm_response.model_provider, model_name=llm_response.model_name,
            prompt_version=llm_response.prompt_version, usage=llm_response.usage,
            latency_ms=llm_response.latency_ms, provider_request_id=llm_response.request_id,
            repair_attempted=llm_response.repair_attempted, created_at=datetime.now(UTC),
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
            answer="现有证据不足，无法依据所选书籍或章节回答该问题。 / Insufficient evidence to answer.",
            claims=[], citations=[], insufficient_evidence=True,
            follow_up="请扩大检索范围或补充相关资料。 / Widen the scope or add sources.",
            model_provider="none", model_name="none",
            prompt_version=RagAnswerService.prompt_version,
            usage=ModelUsage(), latency_ms=0,
            provider_request_id=str(request.trace_context.request_id),
            created_at=datetime.now(UTC),
        )
