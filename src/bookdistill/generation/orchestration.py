"""Thin composition of the retrieval and generation public contracts."""

from __future__ import annotations

from bookdistill.contracts import (
    RagAnswerPayload,
    RagAnswerRequest,
    RetrieveAndAnswerRequest,
    RetrieveAndAnswerResponse,
    RetrievalRequest,
)
from bookdistill.retrieval import HybridRetrievalEngine

from .service import RagAnswerService


class RetrieveAndAnswerService:
    def __init__(
        self, retrieval: HybridRetrievalEngine, generation: RagAnswerService
    ) -> None:
        self._retrieval = retrieval
        self._generation = generation

    def answer(self, request: RetrieveAndAnswerRequest) -> RetrieveAndAnswerResponse:
        retrieval = self._retrieval.search(
            RetrievalRequest(
                request_id=request.trace_context.request_id,
                query=request.question,
                scope=request.scope,
                config=request.retrieval_config,
            )
        )
        answer = self._generation.answer(
            RagAnswerRequest(
                trace_context=request.trace_context,
                payload=RagAnswerPayload(
                    question=request.question,
                    scope=request.scope,
                    evidence=retrieval.evidence,
                    language=request.language,
                    max_answer_characters=request.max_answer_characters,
                ),
                generation_config=request.generation_config,
            )
        )
        return RetrieveAndAnswerResponse(retrieval=retrieval, answer=answer)
