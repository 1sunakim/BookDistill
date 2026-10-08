"""Fail CI when the selected retrieval variant misses release thresholds."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
from bookdistill.evaluation.release import retrieval_checks


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--report",
        type=Path,
        default=PROJECT_ROOT / "artifacts" / "retrieval-ablation-real" / "ablation.json",
    )
    parser.add_argument(
        "--thresholds",
        type=Path,
        default=PROJECT_ROOT / "examples" / "retrieval_release_thresholds.v1.json",
    )
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    thresholds = json.loads(args.thresholds.read_text(encoding="utf-8"))
    target = next(
        (
            value
            for value in report["variants"]
            if value["retrieval_variant"] == thresholds["target_variant"]
        ),
        None,
    )
    if target is None:
        raise SystemExit(f"missing target variant: {thresholds['target_variant']}")
    metrics = target["metrics"]
    checks = retrieval_checks(metrics, thresholds)
    eligible = report.get("acceptance", {}).get("eligible") is True
    print(json.dumps({"variant": target["retrieval_variant"], "checks": checks,
                      "metrics_passed": all(checks.values()), "passed": all(checks.values()) and eligible,
                      "classification": "quality_acceptance" if eligible else "mechanism_test"}, indent=2))
    if not all(checks.values()) or not eligible:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
