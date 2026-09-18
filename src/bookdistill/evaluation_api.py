"""HTTP boundary for managed evaluation datasets and live retrieval runs."""

from __future__ import annotations

import json
from pathlib import Path
import re

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict

from bookdistill.contracts import (
    EvaluationDataset,
    ExperimentConfig,
    RetrievalEvaluationResult,
    RetrievalPredictionSet,
)
from bookdistill.evaluation.runner import evaluate_retrieval, write_experiment_artifacts
from bookdistill.retrieval import HybridRetrievalEngine
from bookdistill.retrieval.evaluation import HybridRetrievalEvaluationSubject


SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class EvaluationApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvaluationDatasetSummary(EvaluationApiModel):
    dataset_key: str
    dataset_name: str
    dataset_version: str
    split: str
    case_count: int
    description: str | None = None


class EvaluationRunRequest(EvaluationApiModel):
    dataset_key: str
    experiment: ExperimentConfig


def create_evaluation_router(
    retrieval: HybridRetrievalEngine, storage_root: Path
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/evaluation", tags=["evaluation"])
    datasets_root = storage_root / "evaluation-datasets"
    runs_root = storage_root / "evaluation-runs"

    def dataset_path(dataset_key: str) -> Path:
        if not SAFE_ID.fullmatch(dataset_key):
            raise HTTPException(status_code=422, detail="invalid dataset key")
        return datasets_root / f"{dataset_key}.json"

    def run_path(experiment_id: str) -> Path:
        if not SAFE_ID.fullmatch(experiment_id):
            raise HTTPException(status_code=422, detail="invalid experiment id")
        return runs_root / experiment_id / "result.json"

    def load_dataset(dataset_key: str) -> EvaluationDataset:
        path = dataset_path(dataset_key)
        if not path.exists():
            raise HTTPException(status_code=404, detail="evaluation dataset not found")
        return EvaluationDataset.model_validate_json(path.read_text(encoding="utf-8"))

    @router.get("/datasets", response_model=list[EvaluationDatasetSummary])
    def list_datasets() -> list[EvaluationDatasetSummary]:
        if not datasets_root.exists():
            return []
        summaries: list[EvaluationDatasetSummary] = []
        for path in sorted(datasets_root.glob("*.json")):
            try:
                dataset = EvaluationDataset.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            except Exception:
                continue
            summaries.append(
                EvaluationDatasetSummary(
                    dataset_key=path.stem,
                    dataset_name=dataset.dataset_name,
                    dataset_version=dataset.dataset_version,
                    split=str(dataset.split),
                    case_count=len(dataset.cases),
                    description=dataset.description,
                )
            )
        return summaries

    @router.get("/datasets/{dataset_key}", response_model=EvaluationDataset)
    def get_dataset(dataset_key: str) -> EvaluationDataset:
        return load_dataset(dataset_key)

    @router.post(
        "/datasets",
        response_model=EvaluationDatasetSummary,
        status_code=status.HTTP_201_CREATED,
    )
    def save_dataset(dataset: EvaluationDataset) -> EvaluationDatasetSummary:
        datasets_root.mkdir(parents=True, exist_ok=True)
        key = f"{dataset.dataset_name}.{dataset.dataset_version}"
        if not SAFE_ID.fullmatch(key):
            raise HTTPException(
                status_code=422,
                detail="dataset name and version must contain only letters, digits, dot, dash or underscore",
            )
        path = dataset_path(key)
        path.write_text(dataset.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return EvaluationDatasetSummary(
            dataset_key=key,
            dataset_name=dataset.dataset_name,
            dataset_version=dataset.dataset_version,
            split=str(dataset.split),
            case_count=len(dataset.cases),
            description=dataset.description,
        )

    @router.get("/runs", response_model=list[RetrievalEvaluationResult])
    def list_runs() -> list[RetrievalEvaluationResult]:
        if not runs_root.exists():
            return []
        results: list[RetrievalEvaluationResult] = []
        for path in runs_root.glob("*/result.json"):
            try:
                results.append(
                    RetrievalEvaluationResult.model_validate_json(
                        path.read_text(encoding="utf-8")
                    )
                )
            except Exception:
                continue
        return sorted(results, key=lambda item: item.created_at, reverse=True)

    @router.get("/runs/{experiment_id}", response_model=RetrievalEvaluationResult)
    def get_run(experiment_id: str) -> RetrievalEvaluationResult:
        path = run_path(experiment_id)
        if not path.exists():
            raise HTTPException(status_code=404, detail="evaluation run not found")
        return RetrievalEvaluationResult.model_validate_json(
            path.read_text(encoding="utf-8")
        )

    @router.post(
        "/runs",
        response_model=RetrievalEvaluationResult,
        status_code=status.HTTP_201_CREATED,
    )
    def run_evaluation(payload: EvaluationRunRequest) -> RetrievalEvaluationResult:
        if retrieval.dense_index.count() == 0:
            raise HTTPException(status_code=409, detail="no chunks have been indexed")
        dataset = load_dataset(payload.dataset_key)
        if payload.experiment.dataset_version != dataset.dataset_version:
            raise HTTPException(
                status_code=422,
                detail="experiment dataset_version does not match dataset",
            )
        predictions = HybridRetrievalEvaluationSubject(retrieval).retrieve(dataset.cases)
        prediction_set = RetrievalPredictionSet(
            dataset_name=dataset.dataset_name,
            dataset_version=dataset.dataset_version,
            predictions=predictions,
        )
        result = evaluate_retrieval(dataset, prediction_set, payload.experiment)
        write_experiment_artifacts(
            result, runs_root / payload.experiment.experiment_id
        )
        return result

    return router
