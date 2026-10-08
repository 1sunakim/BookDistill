"""Thin composition of the retrieval and generation public contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from time import perf_counter

from bookdistill.contracts import (
    RagAnswerPayload,
    RagAnswerRequest,
    RetrieveAndAnswerRequest,
    RetrieveAndAnswerResponse,
    RetrievalRequest,
    RetrievalResponse,
)
from bookdistill.retrieval import HybridRetrievalEngine

from .service import RagAnswerService
from .repository import GenerationRepository

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RagPipelineState:
    """BookDistill-owned state; LangChain objects never cross the API boundary."""
    request: RetrieveAndAnswerRequest
    raw_question: str = ""
    actual_query: str = ""
    history: list = field(default_factory=list)
    retrieval: RetrievalResponse | None = None
    selected_evidence: list = field(default_factory=list)
    answer: RetrieveAndAnswerResponse | None = None

    def __post_init__(self) -> None:
        self.raw_question = self.request.question


class RetrieveAndAnswerService:
    def __init__(
        self, retrieval: HybridRetrievalEngine, generation: RagAnswerService,
        repository: GenerationRepository | None = None,
        *, minimum_evidence_relevance: float = 0.02,
        max_context_tokens: int = 8192,
        pipeline: str = "legacy",
    ) -> None:
        self._retrieval = retrieval
        self._generation = generation
        self._repository = repository
        self._minimum_evidence_relevance = minimum_evidence_relevance
        self._max_context_tokens = max_context_tokens
        if pipeline not in {"legacy", "runnable"}:
            raise ValueError("rag pipeline must be 'legacy' or 'runnable'")
        self._pipeline = pipeline

    def retrieve(self, request: RetrieveAndAnswerRequest) -> RetrievalResponse:
        query, _ = self._query_and_history(request)
        return self._retrieve_with_query(request, query)

    def _retrieve_with_query(
        self, request: RetrieveAndAnswerRequest, query: str,
    ) -> RetrievalResponse:
        return self._retrieval.search(
            RetrievalRequest(
                request_id=request.trace_context.request_id,
                query=query,
                scope=request.scope,
                config=request.retrieval_config,
            )
        )

    def _query_and_history(self, request: RetrieveAndAnswerRequest):
        history = (
            self._repository.recent_history(request.conversation_id, request.history_window)
            if self._repository and request.conversation_id else []
        )
        query = request.question
        follow_up_markers = ("它", "其", "这个", "上述", "that", "it", "this", "those")
        if history and (len(query) < 30 or any(marker in query.casefold() for marker in follow_up_markers)):
            previous_users = [item.content for item in history if item.role == "user"]
            if previous_users:
                query = f"{previous_users[-1]}\nFollow-up / 追问: {query}"
        return query, history
    def answer_from_retrieval(
        self, request: RetrieveAndAnswerRequest, retrieval: RetrievalResponse,
    ) -> RetrieveAndAnswerResponse:
        history = (
            self._repository.recent_history(request.conversation_id, request.history_window)
            if self._repository and request.conversation_id else []
        )
        return self._answer_from_retrieval(request, retrieval, history)

    def _answer_from_retrieval(
        self, request: RetrieveAndAnswerRequest, retrieval: RetrievalResponse,
        history: list,
    ) -> RetrieveAndAnswerResponse:
        # Keep the newest turns that fit the configured model context. This is a
        # conservative character-based estimate that works for both Chinese and English.
        character_budget = max(0, self._max_context_tokens * 2)
        kept = []
        used = 0
        for turn in reversed(history):
            if used + len(turn.content) > character_budget:
                break
            kept.append(turn)
            used += len(turn.content)
        history = list(reversed(kept))
        evidence = [
            item for item in retrieval.evidence
            if (item.rerank_score if item.rerank_score is not None else item.fusion_score)
            >= self._minimum_evidence_relevance
        ]
        answer_request = RagAnswerRequest(
            trace_context=request.trace_context,
            payload=RagAnswerPayload(
                question=request.question, scope=request.scope,
                evidence=evidence, language=request.language,
                max_answer_characters=request.max_answer_characters,
                conversation_history=history,
            ), generation_config=request.generation_config,
        )
        answer = self._generation.answer(answer_request)
        if self._repository:
            self._repository.save_answer(answer_request, answer, request.conversation_id)
        return RetrieveAndAnswerResponse(retrieval=retrieval, answer=answer)

    def answer(self, request: RetrieveAndAnswerRequest) -> RetrieveAndAnswerResponse:
        if self._pipeline == "legacy":
            return self.answer_from_retrieval(request, self.retrieve(request))
        return self._runnable_pipeline().invoke(RagPipelineState(request=request)).answer

    def retrieve_runnable(self, request: RetrieveAndAnswerRequest) -> RetrievalResponse:
        return self._invoke_runnable_node("retrieval", request, lambda: self.retrieve(request))

    def answer_from_retrieval_runnable(
        self, request: RetrieveAndAnswerRequest, retrieval: RetrievalResponse,
    ) -> RetrieveAndAnswerResponse:
        return self._invoke_runnable_node(
            "answer", request, lambda: self.answer_from_retrieval(request, retrieval),
            evidence_count=len(retrieval.evidence),
        )

    @staticmethod
    def _invoke_runnable_node(stage: str, request: RetrieveAndAnswerRequest, fn, *, evidence_count: int = 0):
        try:
            from langchain_core.runnables import RunnableLambda
        except ImportError as error:
            raise RuntimeError(
                "BOOKDISTILL_RAG_PIPELINE=runnable requires the optional langchain dependency"
            ) from error
        started = perf_counter()
        try:
            result = RunnableLambda(lambda _input: fn()).invoke(None)
        except Exception as error:
            logger.info("rag_node", extra={
                "request_id": str(request.trace_context.request_id),
                "trace_id": str(request.trace_context.trace_id), "stage": stage,
                "duration_ms": round((perf_counter() - started) * 1000, 2),
                "evidence_count": evidence_count, "error_code": type(error).__name__,
            })
            raise
        logger.info("rag_node", extra={
            "request_id": str(request.trace_context.request_id),
            "trace_id": str(request.trace_context.trace_id), "stage": stage,
            "duration_ms": round((perf_counter() - started) * 1000, 2),
            "evidence_count": len(result.evidence) if stage == "retrieval" else evidence_count,
            "status": "ok",
        })
        return result

    def _runnable_pipeline(self):
        def retrieve(state: RagPipelineState) -> RagPipelineState:
            query, history = self._query_and_history(state.request)
            state.actual_query = query
            state.history = history
            state.retrieval = self._invoke_runnable_node(
                "retrieval", state.request,
                lambda: self._retrieve_with_query(state.request, state.actual_query),
            )
            state.selected_evidence = list(state.retrieval.evidence)
            return state

        def answer(state: RagPipelineState) -> RagPipelineState:
            assert state.retrieval is not None
            state.answer = self._invoke_runnable_node(
                "answer", state.request,
                lambda: self._answer_from_retrieval(
                    state.request, state.retrieval, state.history,
                ),
                evidence_count=len(state.retrieval.evidence),
            )
            return state

        from langchain_core.runnables import RunnableLambda
        return RunnableLambda(retrieve) | RunnableLambda(answer)
