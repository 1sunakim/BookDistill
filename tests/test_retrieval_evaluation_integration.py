from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from bookdistill.contracts import (  # noqa: E402
    DocumentProcessingSnapshot,
    EvaluationDataset,
    ExperimentConfig,
    RetrievalPredictionSet,
)
from bookdistill.evaluation.runner import evaluate_retrieval  # noqa: E402
from bookdistill.retrieval import (  # noqa: E402
    HybridRetrievalEngine,
    HybridRetrievalEvaluationSubject,
)


class RetrievalEvaluationIntegrationTests(unittest.TestCase):
    def test_live_engine_produces_evaluable_predictions(self) -> None:
        snapshot = DocumentProcessingSnapshot.model_validate_json(
            (PROJECT_ROOT / "examples" / "document_processing_snapshot.v1.json").read_text(
                encoding="utf-8"
            )
        )
        dataset = EvaluationDataset.model_validate_json(
            (
                PROJECT_ROOT
                / "examples"
                / "retrieval_dataset.bookdistill-sample.v1.json"
            ).read_text(encoding="utf-8")
        )
        engine = HybridRetrievalEngine()
        engine.index_chunks(snapshot.chunks)
        prediction_set = RetrievalPredictionSet(
            dataset_name=dataset.dataset_name,
            dataset_version=dataset.dataset_version,
            predictions=HybridRetrievalEvaluationSubject(engine).retrieve(dataset.cases),
        )
        result = evaluate_retrieval(
            dataset,
            prediction_set,
            ExperimentConfig(
                experiment_id="integration-smoke",
                dataset_version=dataset.dataset_version,
                retrieval_variant="hybrid_rerank",
                parser_version=snapshot.document.parser_version,
                chunk_version=snapshot.chunks[0].chunk_version,
                embedding_model=engine.embedding.model_name,
                reranker_model=engine.reranker.model_name,
            ),
        )
        self.assertEqual(result.aggregate.evaluated_cases, 2)
        self.assertEqual(result.aggregate.excluded_unanswerable_cases, 1)
        self.assertEqual(result.aggregate.mrr, 1.0)
        self.assertEqual(result.aggregate.ndcg_at_10, 1.0)


if __name__ == "__main__":
    unittest.main()
