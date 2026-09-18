from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

from pydantic import ValidationError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from bookdistill.contracts import (  # noqa: E402
    EvaluationDataset,
    ExperimentConfig,
    RetrievalEvaluationResult,
    RetrievalPredictionSet,
)
from bookdistill.evaluation import (  # noqa: E402
    assert_dataset_references,
    build_ablation_report,
    compare_experiments,
    evaluate_retrieval,
    write_experiment_artifacts,
    write_ablation_artifacts,
)


def load_fixture(name: str) -> object:
    return json.loads((PROJECT_ROOT / "examples" / name).read_text(encoding="utf-8"))


class QualityEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dataset = EvaluationDataset.model_validate(
            load_fixture("evaluation_dataset.smoke.v1.json")
        )
        self.predictions = RetrievalPredictionSet.model_validate(
            load_fixture("retrieval_predictions.smoke.v1.json")
        )
        self.config = ExperimentConfig.model_validate(
            load_fixture("experiment_config.smoke.v1.json")
        )

    def test_smoke_metrics_and_unanswerable_exclusion(self) -> None:
        result = evaluate_retrieval(self.dataset, self.predictions, self.config)
        self.assertEqual(result.aggregate.evaluated_cases, 1)
        self.assertEqual(result.aggregate.excluded_unanswerable_cases, 1)
        self.assertEqual(result.aggregate.mrr, 0.5)
        self.assertEqual(result.cases[0].first_relevant_rank, 2)
        recall_1 = next(metric.recall for metric in result.aggregate.cutoffs if metric.k == 1)
        recall_3 = next(metric.recall for metric in result.aggregate.cutoffs if metric.k == 3)
        self.assertEqual(recall_1, 0.0)
        self.assertEqual(recall_3, 1.0)

    def test_missing_prediction_is_a_zero_scored_failure(self) -> None:
        empty = RetrievalPredictionSet(
            dataset_name=self.dataset.dataset_name,
            dataset_version=self.dataset.dataset_version,
            predictions=[],
        )
        result = evaluate_retrieval(self.dataset, empty, self.config)
        self.assertEqual(result.aggregate.missing_or_failed_predictions, 1)
        self.assertEqual(result.aggregate.mrr, 0.0)
        self.assertEqual(result.cases[0].error, "missing prediction")

    def test_unknown_prediction_case_is_rejected(self) -> None:
        payload = self.predictions.model_dump(mode="json")
        payload["predictions"][0]["case_id"] = "unknown-case"
        predictions = RetrievalPredictionSet.model_validate(payload)
        with self.assertRaises(ValueError):
            evaluate_retrieval(self.dataset, predictions, self.config)

    def test_dataset_version_mismatch_is_rejected(self) -> None:
        payload = self.predictions.model_dump(mode="json")
        payload["dataset_version"] = "2.0.0"
        predictions = RetrievalPredictionSet.model_validate(payload)
        with self.assertRaises(ValueError):
            evaluate_retrieval(self.dataset, predictions, self.config)

    def test_answerable_case_requires_relevant_chunks(self) -> None:
        payload = self.dataset.model_dump(mode="json")
        payload["cases"][0]["relevant_chunk_ids"] = []
        with self.assertRaises(ValidationError):
            EvaluationDataset.model_validate(payload)

    def test_artifacts_are_machine_and_human_readable(self) -> None:
        result = evaluate_retrieval(self.dataset, self.predictions, self.config)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            write_experiment_artifacts(result, output)
            self.assertTrue((output / "result.json").exists())
            self.assertTrue((output / "cases.csv").exists())
            self.assertIn("Recall@10", (output / "report.md").read_text(encoding="utf-8"))
            loaded = RetrievalEvaluationResult.model_validate_json(
                (output / "result.json").read_text(encoding="utf-8")
            )
            self.assertEqual(loaded.experiment.experiment_id, self.config.experiment_id)

    def test_experiment_comparison_reports_delta(self) -> None:
        baseline = evaluate_retrieval(self.dataset, self.predictions, self.config)
        payload = self.predictions.model_dump(mode="json")
        payload["predictions"][0]["retrieved"].reverse()
        candidate_predictions = RetrievalPredictionSet.model_validate(payload)
        config_payload = self.config.model_dump(mode="json")
        config_payload["experiment_id"] = "smoke-hybrid-002"
        candidate = evaluate_retrieval(
            self.dataset,
            candidate_predictions,
            ExperimentConfig.model_validate(config_payload),
        )
        comparison = compare_experiments(baseline, candidate)
        self.assertEqual(comparison["metrics"]["mrr"]["delta"], 0.5)

    def _four_variant_results(self) -> list[RetrievalEvaluationResult]:
        results = []
        for index, variant in enumerate(("sparse", "dense", "hybrid", "hybrid_rerank")):
            config_payload = self.config.model_dump(mode="json")
            config_payload["experiment_id"] = f"smoke-{variant}"
            config_payload["retrieval_variant"] = variant
            result = evaluate_retrieval(
                self.dataset,
                self.predictions,
                ExperimentConfig.model_validate(config_payload),
            )
            result.aggregate.latency_p95_ms += index
            results.append(result)
        return results

    def test_four_variant_ablation_report(self) -> None:
        report = build_ablation_report(self._four_variant_results())
        self.assertEqual(len(report.variants), 4)
        self.assertEqual(report.fastest_p95_variant, "sparse")
        self.assertEqual(report.variants[0].recall_at_10_delta_vs_sparse, 0.0)

    def test_ablation_rejects_duplicate_or_missing_variants(self) -> None:
        results = self._four_variant_results()
        results[-1].experiment.retrieval_variant = "hybrid"
        with self.assertRaises(ValueError):
            build_ablation_report(results)

    def test_ablation_artifacts(self) -> None:
        report = build_ablation_report(self._four_variant_results())
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            write_ablation_artifacts(report, output)
            self.assertTrue((output / "ablation.json").exists())
            self.assertTrue((output / "ablation.csv").exists())
            markdown = (output / "ablation.md").read_text(encoding="utf-8")
            self.assertIn("| hybrid_rerank |", markdown)

    def test_manual_annotation_batch_matches_snapshot(self) -> None:
        from bookdistill.contracts import DocumentProcessingSnapshot

        dataset = EvaluationDataset.model_validate_json(
            (PROJECT_ROOT / "examples" / "evaluation_dataset.rag-intro.manual-dev.v1.json")
            .read_text(encoding="utf-8")
        )
        snapshot = DocumentProcessingSnapshot.model_validate_json(
            (PROJECT_ROOT / "examples" / "document_processing_snapshot.v1.json")
            .read_text(encoding="utf-8")
        )
        assert_dataset_references(dataset, [snapshot])
        self.assertEqual(len(dataset.cases), 18)
        self.assertEqual(sum(case.is_answerable for case in dataset.cases), 14)

    def test_manual_annotation_validator_rejects_unknown_chunk(self) -> None:
        from bookdistill.contracts import DocumentProcessingSnapshot
        from bookdistill.evaluation import validate_dataset_references

        payload = load_fixture("evaluation_dataset.rag-intro.manual-dev.v1.json")
        payload["cases"][0]["relevant_chunk_ids"][0] = (
            "99999999-9999-4999-8999-999999999999"
        )
        dataset = EvaluationDataset.model_validate(payload)
        snapshot = DocumentProcessingSnapshot.model_validate_json(
            (PROJECT_ROOT / "examples" / "document_processing_snapshot.v1.json")
            .read_text(encoding="utf-8")
        )
        issues = validate_dataset_references(dataset, [snapshot])
        self.assertTrue(any("unknown relevant_chunk_id" in issue for issue in issues))


if __name__ == "__main__":
    unittest.main()
