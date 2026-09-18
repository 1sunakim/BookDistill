"""Integration ports owned by the evaluation block."""

from __future__ import annotations

from typing import Protocol, Sequence

from bookdistill.contracts import EvaluationCase, RetrievalPrediction


class RetrievalEvaluationSubject(Protocol):
    """Optional live adapter; the MVP also accepts exported JSON predictions."""

    def retrieve(self, cases: Sequence[EvaluationCase]) -> list[RetrievalPrediction]: ...
