"""Adapter from the live retrieval engine to offline evaluation contracts."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID, uuid5

from bookdistill.contracts import (
    EvaluationCase,
    RetrievalConfig,
    RetrievalPrediction,
    RetrievalRequest,
    RetrievalScope,
    RetrievedItem,
)

from .engine import HybridRetrievalEngine


EVALUATION_REQUEST_NAMESPACE = UUID("1e594b7c-c91d-49fd-91d7-89507be82a04")


class HybridRetrievalEvaluationSubject:
    """Run reviewed evaluation cases through the same production orchestration."""

    def __init__(
        self,
        engine: HybridRetrievalEngine,
        config: RetrievalConfig | None = None,
    ) -> None:
        self.engine = engine
        self.config = config or RetrievalConfig()

    def retrieve(self, cases: Sequence[EvaluationCase]) -> list[RetrievalPrediction]:
        predictions: list[RetrievalPrediction] = []
        for case in cases:
            try:
                response = self.engine.search(
                    RetrievalRequest(
                        request_id=uuid5(EVALUATION_REQUEST_NAMESPACE, case.case_id),
                        query=case.question,
                        scope=RetrievalScope(
                            book_ids=case.book_scope,
                            chapter_ids=case.chapter_scope,
                        ),
                        config=self.config,
                    )
                )
                predictions.append(
                    RetrievalPrediction(
                        case_id=case.case_id,
                        retrieved=[
                            RetrievedItem(
                                chunk_id=evidence.chunk_id,
                                score=(
                                    evidence.rerank_score
                                    if evidence.rerank_score is not None
                                    else evidence.fusion_score
                                ),
                            )
                            for evidence in response.evidence
                        ],
                        latency_ms=response.trace.total_latency_ms,
                    )
                )
            except Exception as error:
                predictions.append(
                    RetrievalPrediction(
                        case_id=case.case_id,
                        latency_ms=0.0,
                        error=f"{type(error).__name__}: {error}",
                    )
                )
        return predictions
