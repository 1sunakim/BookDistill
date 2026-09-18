"""Validate manual annotations against one or more processing snapshots."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from bookdistill.contracts import DocumentProcessingSnapshot, EvaluationDataset  # noqa: E402
from bookdistill.evaluation import assert_dataset_references  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate manual evaluation annotations")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--snapshots", type=Path, nargs="+", required=True)
    args = parser.parse_args()

    dataset = EvaluationDataset.model_validate_json(args.dataset.read_text(encoding="utf-8"))
    snapshots = [
        DocumentProcessingSnapshot.model_validate_json(path.read_text(encoding="utf-8"))
        for path in args.snapshots
    ]
    assert_dataset_references(dataset, snapshots)
    answerable = sum(case.is_answerable for case in dataset.cases)
    print(
        f"valid: {len(dataset.cases)} cases "
        f"({answerable} answerable, {len(dataset.cases) - answerable} unanswerable)"
    )


if __name__ == "__main__":
    main()
