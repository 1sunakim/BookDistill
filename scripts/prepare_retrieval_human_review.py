"""Prepare annotation and Top-10 inspection CSV files for human review."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        type=Path,
        default=PROJECT_ROOT / "examples" / "evaluation_dataset.rag-intro.manual-dev.v1.json",
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=PROJECT_ROOT / "examples" / "document_processing_snapshot.v1.json",
    )
    parser.add_argument(
        "--predictions",
        type=Path,
        default=PROJECT_ROOT
        / "artifacts"
        / "retrieval-ablation-real"
        / "hybrid_rerank"
        / "predictions.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "artifacts" / "retrieval-human-review",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    predictions = json.loads(args.predictions.read_text(encoding="utf-8"))
    chunks = {value["chunk_id"]: value for value in snapshot["chunks"]}
    cases = {value["case_id"]: value for value in dataset["cases"]}

    annotation_fields = [
        "case_id", "question", "question_type", "book_scope", "chapter_scope",
        "is_answerable", "reference_answer", "relevant_chunk_ids",
        "relevant_page_ranges", "difficulty", "annotator", "review_status",
        "human_notes",
    ]
    with (args.output / "annotation-queue.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=annotation_fields)
        writer.writeheader()
        for case in dataset["cases"]:
            writer.writerow(
                {
                    key: json.dumps(case.get(key), ensure_ascii=False)
                    if isinstance(case.get(key), (list, dict, bool))
                    else case.get(key, "")
                    for key in annotation_fields
                }
            )

    review_fields = [
        "case_id", "question", "rank", "chunk_id", "score", "chapter_path",
        "page_start", "page_end", "chunk_text", "expected_relevant",
        "human_semantic_relevant", "human_overconcentrated", "human_notes",
    ]
    with (args.output / "top10-review.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=review_fields)
        writer.writeheader()
        for prediction in predictions["predictions"]:
            case = cases[prediction["case_id"]]
            expected = set(case.get("relevant_chunk_ids", []))
            for rank, item in enumerate(prediction["retrieved"][:10], 1):
                chunk = chunks.get(item["chunk_id"], {})
                writer.writerow(
                    {
                        "case_id": prediction["case_id"],
                        "question": case["question"],
                        "rank": rank,
                        "chunk_id": item["chunk_id"],
                        "score": item.get("score", ""),
                        "chapter_path": " / ".join(chunk.get("chapter_path", [])),
                        "page_start": chunk.get("page_start", ""),
                        "page_end": chunk.get("page_end", ""),
                        "chunk_text": chunk.get("text", ""),
                        "expected_relevant": item["chunk_id"] in expected,
                        "human_semantic_relevant": "",
                        "human_overconcentrated": "",
                        "human_notes": "",
                    }
                )
    print(args.output)


if __name__ == "__main__":
    main()
