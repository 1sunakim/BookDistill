"""Import two independent reviewer columns and bind them to the exact output."""
from __future__ import annotations
import argparse
import csv
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from bookdistill.evaluation.release import digest


def parsed(value, label):
    if not value.strip():
        raise ValueError(f"missing review field: {label}")
    return json.loads(value)


def boolean(value, label):
    if value.casefold() not in {"true", "false"}:
        raise ValueError(f"{label} must be true or false")
    return value.casefold() == "true"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--reviews", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.results.read_text(encoding="utf-8"))
    rows = {r["case_id"]: r for r in payload["results"]}
    with args.reviews.open(encoding="utf-8-sig", newline="") as stream:
        review_rows = list(csv.DictReader(stream))
    if len({r["case_id"] for r in review_rows}) != len(review_rows) or set(rows) != {r["case_id"] for r in review_rows}:
        raise ValueError("review CSV must contain every result exactly once")
    for review_row in review_rows:
        row = rows[review_row["case_id"]]
        citations = row.get("citations", [])
        reviewers = []
        for number in (1, 2):
            prefix = f"reviewer_{number}_"
            page_values = parsed(review_row[prefix+"page_correct_json"], prefix+"page_correct_json")
            claim_values = parsed(review_row[prefix+"claim_scores_json"], prefix+"claim_scores_json")
            point_values = parsed(review_row[prefix+"points_covered_json"], prefix+"points_covered_json")
            if len(page_values) != len(citations):
                raise ValueError(f"{review_row['case_id']}: reviewer {number} page review count mismatch")
            citation_reviews = [{"citation_sha256": digest(citation), "page_correct": value}
                                for citation, value in zip(citations, page_values)]
            reviewers.append({"kind":"human", "reviewer_id":review_row[prefix+"id"].strip(),
                "reviewed_at":review_row[prefix+"reviewed_at"].strip(), "citation_reviews":citation_reviews,
                "claim_faithfulness_scores":claim_values, "required_point_coverage":point_values,
                "refusal_correct":boolean(review_row[prefix+"refusal_correct"],prefix+"refusal_correct"),
                "answer_assertions_exhaustively_reviewed":boolean(review_row[prefix+"assertions_exhaustive"],prefix+"assertions_exhaustive"),
                "agrees_with_scores":boolean(review_row[prefix+"agrees_with_scores"],prefix+"agrees_with_scores")})
        row["reviewers"] = reviewers
        page_final = parsed(review_row["adjudicated_page_correct_json"], "adjudicated_page_correct_json")
        score_final = parsed(review_row["adjudicated_claim_scores_json"], "adjudicated_claim_scores_json")
        points_final = parsed(review_row["adjudicated_points_covered_json"], "adjudicated_points_covered_json")
        if len(page_final) != len(citations):
            raise ValueError(f"{review_row['case_id']}: adjudicated page review count mismatch")
        row["citation_reviews"] = [{"citation_sha256":digest(citation),"page_correct":value}
                                    for citation,value in zip(citations,page_final)]
        row["claim_faithfulness_scores"] = score_final
        row["required_point_coverage"] = points_final
        row["answer_assertions_exhaustively_reviewed"] = all(r["answer_assertions_exhaustively_reviewed"] for r in reviewers)
        row["adjudication_notes"] = review_row["adjudication_notes"].strip()
        response = {k: row.get(k) for k in ("answer","claims","citations","insufficient_evidence")}
        row["reviewed_response_sha256"] = digest(response)
    payload["results"] = list(rows.values())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
