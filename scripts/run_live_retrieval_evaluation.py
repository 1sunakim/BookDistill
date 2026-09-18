"""Run the retrieval engine against the checked-in end-to-end smoke dataset."""

from __future__ import annotations

import json
from pathlib import Path
import sys


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


snapshot = DocumentProcessingSnapshot.model_validate_json(
    (PROJECT_ROOT / "examples" / "document_processing_snapshot.v1.json").read_text(encoding="utf-8")
)
dataset = EvaluationDataset.model_validate_json(
    (PROJECT_ROOT / "examples" / "retrieval_dataset.bookdistill-sample.v1.json").read_text(
        encoding="utf-8"
    )
)
engine = HybridRetrievalEngine()
engine.index_chunks(snapshot.chunks, {snapshot.book.book_id: snapshot.book.title})
predictions = RetrievalPredictionSet(
    dataset_name=dataset.dataset_name,
    dataset_version=dataset.dataset_version,
    predictions=HybridRetrievalEvaluationSubject(engine).retrieve(dataset.cases),
)
experiment = ExperimentConfig(
    experiment_id="bookdistill-sample-hybrid-rerank-001",
    dataset_version=dataset.dataset_version,
    retrieval_variant="hybrid_rerank",
    parser_version=snapshot.document.parser_version,
    chunk_version=snapshot.chunks[0].chunk_version,
    embedding_model=engine.embedding.model_name,
    sparse_config={"name": "in-memory-bm25"},
    fusion_config={"method": "rrf", "rrf_k": 60},
    reranker_model=engine.reranker.model_name,
)
result = evaluate_retrieval(dataset, predictions, experiment)
print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
