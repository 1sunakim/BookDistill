"""Offline retrieval evaluation and artifact export."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean
from typing import Iterable

from bookdistill.contracts import (
    AggregateRetrievalMetrics,
    CaseRetrievalMetrics,
    CutoffMetric,
    EvaluationCase,
    EvaluationDataset,
    ExperimentConfig,
    RetrievalEvaluationResult,
    RetrievalPrediction,
    RetrievalPredictionSet,
)

from .metrics import (
    DEFAULT_CUTOFFS,
    first_relevant_rank,
    ndcg_at_k,
    percentile,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)


def _mean(values: Iterable[float]) -> float:
    materialized = list(values)
    return fmean(materialized) if materialized else 0.0


def _case_metrics(case: EvaluationCase, prediction: RetrievalPrediction | None) -> CaseRetrievalMetrics:
    ranked = [item.chunk_id for item in prediction.retrieved] if prediction else []
    relevant = set(case.relevant_chunk_ids)
    error = prediction.error if prediction else "missing prediction"
    first_rank = first_relevant_rank(ranked, relevant)
    return CaseRetrievalMetrics(
        case_id=case.case_id,
        question_type=case.question_type,
        reciprocal_rank=reciprocal_rank(ranked, relevant),
        ndcg_at_10=ndcg_at_k(ranked, relevant, 10),
        cutoffs=[
            CutoffMetric(
                k=k,
                recall=recall_at_k(ranked, relevant, k),
                precision=precision_at_k(ranked, relevant, k),
            )
            for k in DEFAULT_CUTOFFS
        ],
        first_relevant_rank=first_rank,
        latency_ms=prediction.latency_ms if prediction else 0.0,
        passed_at_10=first_rank is not None and first_rank <= 10,
        error=error,
    )


def _aggregate(
    cases: list[CaseRetrievalMetrics], excluded: int, failed: int
) -> AggregateRetrievalMetrics:
    cutoff_values = {
        k: [next(value for value in case.cutoffs if value.k == k) for case in cases]
        for k in DEFAULT_CUTOFFS
    }
    latencies = [case.latency_ms for case in cases if case.error is None]
    return AggregateRetrievalMetrics(
        evaluated_cases=len(cases),
        excluded_unanswerable_cases=excluded,
        missing_or_failed_predictions=failed,
        mrr=_mean(case.reciprocal_rank for case in cases),
        ndcg_at_10=_mean(case.ndcg_at_10 for case in cases),
        cutoffs=[
            CutoffMetric(
                k=k,
                recall=_mean(value.recall for value in cutoff_values[k]),
                precision=_mean(value.precision for value in cutoff_values[k]),
            )
            for k in DEFAULT_CUTOFFS
        ],
        latency_p50_ms=percentile(latencies, 0.50),
        latency_p95_ms=percentile(latencies, 0.95),
    )


def evaluate_retrieval(
    dataset: EvaluationDataset,
    predictions: RetrievalPredictionSet,
    experiment: ExperimentConfig,
) -> RetrievalEvaluationResult:
    """Evaluate exported rankings without invoking a retrieval implementation."""

    if predictions.dataset_name != dataset.dataset_name:
        raise ValueError("prediction dataset_name does not match dataset")
    if predictions.dataset_version != dataset.dataset_version:
        raise ValueError("prediction dataset_version does not match dataset")
    if experiment.dataset_version != dataset.dataset_version:
        raise ValueError("experiment dataset_version does not match dataset")

    dataset_ids = {case.case_id for case in dataset.cases}
    prediction_by_id = {prediction.case_id: prediction for prediction in predictions.predictions}
    unknown = set(prediction_by_id) - dataset_ids
    if unknown:
        raise ValueError(f"predictions contain unknown case_ids: {sorted(unknown)}")

    answerable = [case for case in dataset.cases if case.is_answerable]
    case_results = [_case_metrics(case, prediction_by_id.get(case.case_id)) for case in answerable]
    failed = sum(case.error is not None for case in case_results)

    by_type: dict[str, AggregateRetrievalMetrics] = {}
    for question_type in sorted({str(case.question_type) for case in case_results}):
        grouped = [case for case in case_results if str(case.question_type) == question_type]
        by_type[question_type] = _aggregate(
            grouped, excluded=0, failed=sum(case.error is not None for case in grouped)
        )

    return RetrievalEvaluationResult(
        experiment=experiment,
        dataset_name=dataset.dataset_name,
        dataset_version=dataset.dataset_version,
        created_at=datetime.now(timezone.utc),
        aggregate=_aggregate(
            case_results,
            excluded=len(dataset.cases) - len(answerable),
            failed=failed,
        ),
        by_question_type=by_type,
        cases=case_results,
    )


def _metric_map(result: RetrievalEvaluationResult) -> dict[str, float]:
    metrics = {
        "mrr": result.aggregate.mrr,
        "ndcg_at_10": result.aggregate.ndcg_at_10,
        "latency_p50_ms": result.aggregate.latency_p50_ms,
        "latency_p95_ms": result.aggregate.latency_p95_ms,
    }
    for cutoff in result.aggregate.cutoffs:
        metrics[f"recall_at_{cutoff.k}"] = cutoff.recall
        metrics[f"precision_at_{cutoff.k}"] = cutoff.precision
    return metrics


def compare_experiments(
    baseline: RetrievalEvaluationResult, candidate: RetrievalEvaluationResult
) -> dict[str, object]:
    if baseline.dataset_name != candidate.dataset_name or baseline.dataset_version != candidate.dataset_version:
        raise ValueError("experiments must use the same dataset name and version")
    before = _metric_map(baseline)
    after = _metric_map(candidate)
    return {
        "dataset_name": baseline.dataset_name,
        "dataset_version": baseline.dataset_version,
        "baseline_experiment_id": baseline.experiment.experiment_id,
        "candidate_experiment_id": candidate.experiment.experiment_id,
        "metrics": {
            name: {
                "baseline": before[name],
                "candidate": after[name],
                "delta": after[name] - before[name],
            }
            for name in before
        },
    }


def write_experiment_artifacts(result: RetrievalEvaluationResult, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "result.json").write_text(
        result.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )

    with (output_dir / "cases.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "case_id", "question_type", "first_relevant_rank", "reciprocal_rank",
                "ndcg_at_10", "recall_at_10", "passed_at_10", "latency_ms", "error",
            ],
        )
        writer.writeheader()
        for case in result.cases:
            recall_10 = next(metric.recall for metric in case.cutoffs if metric.k == 10)
            writer.writerow({
                "case_id": case.case_id,
                "question_type": case.question_type,
                "first_relevant_rank": case.first_relevant_rank,
                "reciprocal_rank": case.reciprocal_rank,
                "ndcg_at_10": case.ndcg_at_10,
                "recall_at_10": recall_10,
                "passed_at_10": case.passed_at_10,
                "latency_ms": case.latency_ms,
                "error": case.error,
            })

    recall_10 = next(metric.recall for metric in result.aggregate.cutoffs if metric.k == 10)
    failures = [case for case in result.cases if not case.passed_at_10 or case.error]
    lines = [
        f"# Retrieval evaluation: {result.experiment.experiment_id}",
        "",
        f"- Dataset: `{result.dataset_name}` / `{result.dataset_version}`",
        f"- Variant: `{result.experiment.retrieval_variant}`",
        f"- Evaluated cases: {result.aggregate.evaluated_cases}",
        f"- Recall@10: {recall_10:.4f}",
        f"- MRR: {result.aggregate.mrr:.4f}",
        f"- nDCG@10: {result.aggregate.ndcg_at_10:.4f}",
        f"- Latency P50/P95: {result.aggregate.latency_p50_ms:.2f} / {result.aggregate.latency_p95_ms:.2f} ms",
        "",
        "## Failures",
        "",
    ]
    if failures:
        lines.extend(
            f"- `{case.case_id}`: {case.error or 'no relevant chunk in top 10'}"
            for case in failures
        )
    else:
        lines.append("No retrieval failures at cutoff 10.")
    (output_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
