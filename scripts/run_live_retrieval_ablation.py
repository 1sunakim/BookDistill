"""Run four real retrieval variants and write a reproducible ablation report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qdrant_client import QdrantClient  # noqa: E402

from bookdistill.contracts import (  # noqa: E402
    DocumentProcessingSnapshot,
    EvaluationDataset,
    ExperimentConfig,
    RetrievalConfig,
    RetrievalPredictionSet,
)
from bookdistill.evaluation import (  # noqa: E402
    build_ablation_report,
    evaluate_retrieval,
    write_ablation_artifacts,
    write_experiment_artifacts,
)
from bookdistill.retrieval import (  # noqa: E402
    EmbeddingSimilarityReranker,
    FastEmbedEmbeddingProvider,
    FastEmbedReranker,
    HybridRetrievalEngine,
    HybridRetrievalEvaluationSubject,
    InMemoryBM25Index,
)
from bookdistill.retrieval.qdrant_index import QdrantDenseIndex  # noqa: E402


VARIANTS = ("sparse", "dense", "hybrid", "hybrid_rerank")
DENSE_MODEL = "BAAI/bge-small-zh-v1.5"
RERANKER_MODEL = "BAAI/bge-reranker-base"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "artifacts" / "retrieval-ablation-real",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "model-cache",
    )
    parser.add_argument(
        "--reranker-model",
        default=RERANKER_MODEL,
        help=(
            "FastEmbed CrossEncoder model, or 'embedding-similarity' to reuse "
            "the dense neural model as an offline semantic reranker."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    args.cache_dir.mkdir(parents=True, exist_ok=True)

    snapshot = DocumentProcessingSnapshot.model_validate_json(
        (PROJECT_ROOT / "examples" / "document_processing_snapshot.v1.json").read_text(
            encoding="utf-8"
        )
    )
    dataset = EvaluationDataset.model_validate_json(
        (
            PROJECT_ROOT
            / "examples"
            / "evaluation_dataset.rag-intro.manual-dev.v1.json"
        ).read_text(encoding="utf-8")
    )

    embedding = FastEmbedEmbeddingProvider(DENSE_MODEL, cache_dir=args.cache_dir)
    if args.reranker_model == "embedding-similarity":
        reranker = EmbeddingSimilarityReranker(embedding)
    else:
        reranker = FastEmbedReranker(args.reranker_model, cache_dir=args.cache_dir)
    resolved_reranker_model = reranker.model_name
    dense_index = QdrantDenseIndex(
        url="http://unused",
        collection_name="bookdistill_ablation_chunks",
        dimension=embedding.dimension,
        client=QdrantClient(location=":memory:"),
    )
    engine = HybridRetrievalEngine(
        embedding=embedding,
        dense_index=dense_index,
        sparse_index=InMemoryBM25Index(),
        reranker=reranker,
        retrieval_version="bge-zh-bm25-rrf-rerank-v1",
    )
    engine.index_chunks(snapshot.chunks, {snapshot.book.book_id: snapshot.book.title})

    results = []
    for variant in VARIANTS:
        config = RetrievalConfig(
            variant=variant,
            dense_top_k=10,
            sparse_top_k=10,
            fusion_candidates=10,
            rerank_candidates=10,
            final_evidence=2,
            rrf_k=60,
            max_per_chapter=2,
        )
        predictions = RetrievalPredictionSet(
            dataset_name=dataset.dataset_name,
            dataset_version=dataset.dataset_version,
            predictions=HybridRetrievalEvaluationSubject(engine, config).retrieve(
                dataset.cases
            ),
        )
        experiment = ExperimentConfig(
            experiment_id=f"rag-intro-{variant}-real-001",
            dataset_version=dataset.dataset_version,
            retrieval_variant=variant,
            parser_version=snapshot.document.parser_version,
            chunk_version=snapshot.chunks[0].chunk_version,
            embedding_model=DENSE_MODEL if variant != "sparse" else None,
            sparse_config={"name": "in-memory-bm25"} if variant != "dense" else {},
            fusion_config={"method": "rrf", "rrf_k": 60}
            if variant in {"hybrid", "hybrid_rerank"}
            else {},
            reranker_model=resolved_reranker_model
            if variant == "hybrid_rerank"
            else None,
            notes=(
                "Real engine run on the reviewed two-page pilot dataset; "
                "Qdrant local mode exercises the production DenseIndex adapter."
            ),
        )
        result = evaluate_retrieval(dataset, predictions, experiment)
        variant_dir = args.output / variant
        write_experiment_artifacts(result, variant_dir)
        (variant_dir / "predictions.json").write_text(
            predictions.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        results.append(result)

    report = build_ablation_report(results)
    write_ablation_artifacts(report, args.output)
    (args.output / "run-metadata.json").write_text(
        json.dumps(
            {
                "dense_model": DENSE_MODEL,
                "dense_dimension": embedding.dimension,
                "sparse": "BM25",
                "fusion": "RRF(k=60)",
                "reranker_model": resolved_reranker_model,
                "dense_index": "Qdrant local mode",
                "dataset": dataset.dataset_name,
                "dataset_version": dataset.dataset_version,
                "answerable_cases": sum(case.is_answerable for case in dataset.cases),
                "unanswerable_cases": sum(not case.is_answerable for case in dataset.cases),
                "limitation": "Two-page pilot dataset; metrics validate the pipeline, not production quality.",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print((args.output / "ablation.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
