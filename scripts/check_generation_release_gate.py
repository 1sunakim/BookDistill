"""Evaluate human-reviewed generation results against the release thresholds."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from bookdistill.evaluation.release import generation_gate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path, help="JSON result file described in docs/generation-acceptance.md")
    parser.add_argument("--dataset", type=Path, default=Path("examples/generation_acceptance_dataset.v1.json"))
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    payload = json.loads(args.results.read_text(encoding="utf-8"))
    outcome = generation_gate(dataset, payload)
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    return 0 if outcome["passed"] else 1
if __name__ == "__main__":
    raise SystemExit(main())
