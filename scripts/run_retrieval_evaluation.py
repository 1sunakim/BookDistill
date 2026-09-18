"""Run the quality-evaluation MVP against exported retrieval rankings."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from bookdistill.contracts import (  # noqa: E402
    EvaluationDataset,
    ExperimentConfig,
    RetrievalPredictionSet,
)
from bookdistill.evaluation import evaluate_retrieval, write_experiment_artifacts  # noqa: E402


def _load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate exported retrieval predictions")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    dataset = EvaluationDataset.model_validate(_load(args.dataset))
    predictions = RetrievalPredictionSet.model_validate(_load(args.predictions))
    experiment = ExperimentConfig.model_validate(_load(args.config))
    result = evaluate_retrieval(dataset, predictions, experiment)
    write_experiment_artifacts(result, args.output)
    print(args.output / "result.json")


if __name__ == "__main__":
    main()
