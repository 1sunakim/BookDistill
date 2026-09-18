"""Four-variant retrieval ablation reporting.

The module consumes completed experiment results and never invokes retrieval.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from bookdistill.contracts import (
    AblationMetricSnapshot,
    AblationVariantResult,
    RetrievalAblationReport,
    RetrievalEvaluationResult,
)


REQUIRED_VARIANTS = ("sparse", "dense", "hybrid", "hybrid_rerank")


def _recall_at_10(result: RetrievalEvaluationResult) -> float:
    return next(metric.recall for metric in result.aggregate.cutoffs if metric.k == 10)


def build_ablation_report(
    results: Sequence[RetrievalEvaluationResult],
) -> RetrievalAblationReport:
    """Validate and summarize one result for each required retrieval variant."""

    if len(results) != len(REQUIRED_VARIANTS):
        raise ValueError("ablation requires exactly four experiment results")
    dataset_keys = {(result.dataset_name, result.dataset_version) for result in results}
    if len(dataset_keys) != 1:
        raise ValueError("all ablation experiments must use the same dataset and version")
    by_variant = {result.experiment.retrieval_variant: result for result in results}
    if set(by_variant) != set(REQUIRED_VARIANTS):
        raise ValueError(f"ablation requires variants: {', '.join(REQUIRED_VARIANTS)}")

    sparse = by_variant["sparse"]
    sparse_recall = _recall_at_10(sparse)
    rows: list[AblationVariantResult] = []
    for variant in REQUIRED_VARIANTS:
        result = by_variant[variant]
        snapshot = AblationMetricSnapshot(
            recall_at_10=_recall_at_10(result),
            mrr=result.aggregate.mrr,
            ndcg_at_10=result.aggregate.ndcg_at_10,
            latency_p50_ms=result.aggregate.latency_p50_ms,
            latency_p95_ms=result.aggregate.latency_p95_ms,
            missing_or_failed_predictions=result.aggregate.missing_or_failed_predictions,
        )
        rows.append(AblationVariantResult(
            retrieval_variant=variant,
            experiment_id=result.experiment.experiment_id,
            metrics=snapshot,
            recall_at_10_delta_vs_sparse=snapshot.recall_at_10 - sparse_recall,
            mrr_delta_vs_sparse=snapshot.mrr - sparse.aggregate.mrr,
            ndcg_at_10_delta_vs_sparse=snapshot.ndcg_at_10 - sparse.aggregate.ndcg_at_10,
            latency_p95_delta_vs_sparse_ms=(
                snapshot.latency_p95_ms - sparse.aggregate.latency_p95_ms
            ),
        ))

    dataset_name, dataset_version = next(iter(dataset_keys))
    return RetrievalAblationReport(
        dataset_name=dataset_name,
        dataset_version=dataset_version,
        created_at=datetime.now(timezone.utc),
        variants=rows,
        best_recall_at_10_variant=max(
            rows, key=lambda row: (row.metrics.recall_at_10, row.metrics.ndcg_at_10)
        ).retrieval_variant,
        best_ndcg_at_10_variant=max(
            rows, key=lambda row: (row.metrics.ndcg_at_10, row.metrics.recall_at_10)
        ).retrieval_variant,
        fastest_p95_variant=min(
            rows, key=lambda row: row.metrics.latency_p95_ms
        ).retrieval_variant,
    )


def write_ablation_artifacts(report: RetrievalAblationReport, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "ablation.json").write_text(
        report.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    with (output_dir / "ablation.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        fields = [
            "variant", "experiment_id", "recall_at_10", "mrr", "ndcg_at_10",
            "latency_p50_ms", "latency_p95_ms", "failed_predictions",
            "recall_delta_vs_sparse", "mrr_delta_vs_sparse",
            "ndcg_delta_vs_sparse", "p95_delta_vs_sparse_ms",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in report.variants:
            writer.writerow({
                "variant": row.retrieval_variant,
                "experiment_id": row.experiment_id,
                "recall_at_10": row.metrics.recall_at_10,
                "mrr": row.metrics.mrr,
                "ndcg_at_10": row.metrics.ndcg_at_10,
                "latency_p50_ms": row.metrics.latency_p50_ms,
                "latency_p95_ms": row.metrics.latency_p95_ms,
                "failed_predictions": row.metrics.missing_or_failed_predictions,
                "recall_delta_vs_sparse": row.recall_at_10_delta_vs_sparse,
                "mrr_delta_vs_sparse": row.mrr_delta_vs_sparse,
                "ndcg_delta_vs_sparse": row.ndcg_at_10_delta_vs_sparse,
                "p95_delta_vs_sparse_ms": row.latency_p95_delta_vs_sparse_ms,
            })

    lines = [
        "# Retrieval ablation report",
        "",
        f"Dataset: `{report.dataset_name}` / `{report.dataset_version}`",
        "",
        "| Variant | Recall@10 | MRR | nDCG@10 | P95 ms | ΔRecall vs Sparse | ΔP95 ms |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report.variants:
        lines.append(
            f"| {row.retrieval_variant} | {row.metrics.recall_at_10:.4f} | "
            f"{row.metrics.mrr:.4f} | {row.metrics.ndcg_at_10:.4f} | "
            f"{row.metrics.latency_p95_ms:.2f} | "
            f"{row.recall_at_10_delta_vs_sparse:+.4f} | "
            f"{row.latency_p95_delta_vs_sparse_ms:+.2f} |"
        )
    lines.extend([
        "",
        f"- Best Recall@10: `{report.best_recall_at_10_variant}`",
        f"- Best nDCG@10: `{report.best_ndcg_at_10_variant}`",
        f"- Fastest P95: `{report.fastest_p95_variant}`",
    ])
    (output_dir / "ablation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
