"""Export evaluation JSON Schemas for independent producers and consumers."""

from __future__ import annotations

import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from bookdistill.contracts import (  # noqa: E402
    EvaluationDataset,
    ExperimentConfig,
    RetrievalAblationReport,
    RetrievalEvaluationResult,
    RetrievalPredictionSet,
)


def main() -> None:
    schemas = {
        "evaluation-dataset.v1.schema.json": EvaluationDataset,
        "retrieval-predictions.v1.schema.json": RetrievalPredictionSet,
        "experiment-config.v1.schema.json": ExperimentConfig,
        "retrieval-evaluation-result.v1.schema.json": RetrievalEvaluationResult,
        "retrieval-ablation-report.v1.schema.json": RetrievalAblationReport,
    }
    output_dir = PROJECT_ROOT / "schemas"
    output_dir.mkdir(parents=True, exist_ok=True)
    for file_name, model in schemas.items():
        output = output_dir / file_name
        output.write_text(
            json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(output)


if __name__ == "__main__":
    main()
