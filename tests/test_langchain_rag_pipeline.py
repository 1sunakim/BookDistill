"""Contract checks for the optional Runnable RAG path."""

from datetime import datetime, timezone
import sys
from types import ModuleType
from unittest.mock import patch
from uuid import uuid4

from bookdistill.contracts.generation import (
    ConversationTurn, ModelUsage, RagAnswerResponse, RetrieveAndAnswerRequest,
    TraceContext,
)
from bookdistill.contracts.retrieval import RetrievalResponse, RetrievalTrace
from bookdistill.generation.orchestration import RetrieveAndAnswerService


class FakeRunnable:
    def __init__(self, fn):
        self.fn = fn

    def invoke(self, value):
        return self.fn(value)

    def __or__(self, other):
        return FakeRunnable(lambda value: other.invoke(self.invoke(value)))


def test_runnable_reuses_one_history_snapshot_and_saves_once():
    core = ModuleType("langchain_core")
    runnables = ModuleType("langchain_core.runnables")
    runnables.RunnableLambda = FakeRunnable
    request = RetrieveAndAnswerRequest(
        trace_context=TraceContext(request_id=uuid4(), trace_id=uuid4()),
        question="它是什么？", conversation_id=uuid4(),
    )
    calls = {"history": 0, "save": 0, "search": 0, "answer": 0}

    class Repository:
        def recent_history(self, conversation_id, window):
            calls["history"] += 1
            return [ConversationTurn(role="user", content="先前问题")]

        def save_answer(self, answer_request, answer, conversation_id):
            calls["save"] += 1

    class Retrieval:
        def search(self, search_request):
            calls["search"] += 1
            assert search_request.query == "先前问题\nFollow-up / 追问: 它是什么？"
            assert search_request.request_id == request.trace_context.request_id
            return RetrievalResponse(
                request_id=search_request.request_id, evidence=[],
                trace=RetrievalTrace(
                    original_query=search_request.query, normalized_query=search_request.query,
                    retrieval_version="test", retrieval_variant="hybrid",
                    indexed_chunk_count=0, dense_latency_ms=0, sparse_latency_ms=0,
                    fusion_latency_ms=0, rerank_latency_ms=0, total_latency_ms=0,
                    candidates=[],
                ),
            )

    class Generation:
        def answer(self, answer_request):
            calls["answer"] += 1
            assert len(answer_request.payload.conversation_history) == 1
            assert answer_request.payload.evidence == []
            return RagAnswerResponse(
                answer="证据不足。", claims=[], citations=[], insufficient_evidence=True,
                model_provider="test", model_name="test", prompt_version="rag-answer.v1",
                usage=ModelUsage(), latency_ms=0, provider_request_id="test",
                created_at=datetime.now(timezone.utc),
            )

    with patch.dict(sys.modules, {"langchain_core": core, "langchain_core.runnables": runnables}):
        service = RetrieveAndAnswerService(
            Retrieval(), Generation(), Repository(), pipeline="runnable",
        )
        result = service.answer(request)
    assert result.answer.insufficient_evidence
    assert calls == {"history": 1, "save": 1, "search": 1, "answer": 1}
