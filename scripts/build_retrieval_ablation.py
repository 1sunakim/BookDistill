"""Build a four-variant ablation report from completed experiment results."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from bookdistill.contracts import RetrievalEvaluationResult  # noqa: E402
from bookdistill.evaluation import build_ablation_report, write_ablation_artifacts  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize the four retrieval variants")
    parser.add_argument(
        "--results",
        type=Path,
        nargs=4,
        required=True,
        metavar="RESULT_JSON",
        help="Exactly four result.json files: sparse, dense, hybrid, hybrid_rerank",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    results = [
        RetrievalEvaluationResult.model_validate_json(path.read_text(encoding="utf-8"))
        for path in args.results
    ]
    report = build_ablation_report(results)
    write_ablation_artifacts(report, args.output)
    print(args.output / "ablation.json")


if __name__ == "__main__":
    main()
