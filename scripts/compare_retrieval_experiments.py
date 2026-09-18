"""Compare two results produced by run_retrieval_evaluation.py."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from bookdistill.contracts import RetrievalEvaluationResult  # noqa: E402
from bookdistill.evaluation import compare_experiments  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare two retrieval experiments")
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    baseline = RetrievalEvaluationResult.model_validate_json(
        args.baseline.read_text(encoding="utf-8")
    )
    candidate = RetrievalEvaluationResult.model_validate_json(
        args.candidate.read_text(encoding="utf-8")
    )
    comparison = compare_experiments(baseline, candidate)
    rendered = json.dumps(comparison, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(args.output)
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
