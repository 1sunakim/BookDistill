"""Materialize source-anchored annotation candidates; never certify human review."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from bookdistill.contracts import DocumentProcessingSnapshot, EvaluationDataset
from bookdistill.evaluation.annotation import assert_dataset_references


def normalized(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).casefold()


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seeds", type=Path, default=ROOT / "examples/real_pdf_annotation_seeds.v1.json")
    args = parser.parse_args()
    if (args.output / "dataset.json").exists():
        raise SystemExit("Refusing to overwrite a frozen annotation batch; choose a new directory/version.")
    snapshot = DocumentProcessingSnapshot.model_validate_json(args.snapshot.read_text(encoding="utf-8"))
    seeds = json.loads(args.seeds.read_text(encoding="utf-8"))
    book = str(snapshot.book.book_id)
    pages = {p.page_index: p for p in snapshot.document.pages}
    cases, review, groups, scope = [], [], {}, {}
    for group in seeds["groups"]:
        support = []
        for point in group["points"]:
            page_text = "\n".join(b.text for b in pages[point["page"]].blocks)
            if normalized(point["quote"]) not in normalized(page_text):
                raise ValueError(f"Source anchor absent: {group['id']} / {point['quote']}")
            matches = [c for c in snapshot.chunks if c.page_start <= point["page"] <= c.page_end
                       and (normalized(point["quote"]) in normalized(c.text)
                            or normalized(point["quote"]) in normalized(" / ".join(c.chapter_path)))]
            if not matches:
                raise ValueError(f"Anchor lost during chunking: {group['id']} / {point['quote']}")
            support.append({**point, "chunk_ids": [str(c.chunk_id) for c in matches],
                            "pdf_viewer_page": point["page"] + 1,
                            "printed_page": pages[point["page"]].printed_page_number})
        relevant = sorted({cid for point in support for cid in point["chunk_ids"]})
        chunks = [c for c in snapshot.chunks if str(c.chunk_id) in relevant]
        scope[group["id"]] = sorted({str(c.chapter_id) for c in chunks})
        for number, question in enumerate(group["questions"], 1):
            cid = f"{group['id']}-{number}"
            tags = ["real-pdf", group["id"], "en" if question.isascii() else "zh",
                    "page-location" if group["id"] == "location" else "content"]
            cases.append(dict(case_id=cid, question=question, question_type=group["type"] if number == 1 else "paraphrase",
                              book_scope=[book], chapter_scope=scope[group["id"]] if group["id"] == "location" else [],
                              reference_answer=group["answer"], required_points=[p["text"] for p in support],
                              relevant_chunk_ids=relevant,
                              relevant_page_ranges=[dict(book_id=book, page_start=c.page_start, page_end=c.page_end) for c in chunks],
                              difficulty="hard" if group["type"] in {"cross_chapter", "cross_paragraph"} else "medium",
                              annotator="AI-source-grounded-draft", review_status="draft", is_answerable=True, tags=tags))
            groups[cid] = dict(group_id=group["id"], split=group["split"], required_evidence_groups=support)
    for entry in seeds["unanswerable"]:
        cid = entry["id"]
        cases.append(dict(case_id=cid, question=entry["question"], question_type="unanswerable", book_scope=[book],
                          chapter_scope=scope.get(entry.get("scope_group"), []), reference_answer=entry["reason"],
                          required_points=["明确证据不足并拒答，不附引用"], relevant_chunk_ids=[], relevant_page_ranges=[],
                          difficulty="hard", annotator="AI-source-grounded-draft", review_status="draft",
                          is_answerable=False, tags=["real-pdf", "unanswerable", "scoped" if entry.get("scope_group") else "absent"]))
        groups[cid] = dict(group_id=cid, split=entry["split"], required_evidence_groups=[])
    dataset = EvaluationDataset(dataset_name="real-pdf-module5-candidates", dataset_version=seeds["version"],
                                split="dev", description="AI候选标注；32题单PDF；未完成双人复核；仅机制测试。", cases=cases)
    assert_dataset_references(dataset, [snapshot])
    write(args.output / "dataset.json", dataset.model_dump(mode="json"))
    for split in ["dev", "test"]:
        subset = dataset.model_copy(update={"split": split, "cases": [c for c in dataset.cases if groups[c.case_id]["split"] == split]})
        write(args.output / f"dataset.{split}.json", subset.model_dump(mode="json"))
    for case in dataset.cases:
        review.append({"case_id": case.case_id, **groups[case.case_id], "should_refuse": not case.is_answerable,
                       "reference_answer": case.reference_answer, "required_points": case.required_points,
                       "reviewers": [], "adjudication": None})
    write(args.output / "annotations.json", {"dataset_sha256": hashlib.sha256((args.output / 'dataset.json').read_bytes()).hexdigest(),
          "snapshot_sha256": hashlib.sha256(args.snapshot.read_bytes()).hexdigest(),
          "pdf_sha256": snapshot.book.sha256, "page_convention": seeds["page_convention"],
          "status": "awaiting_two_independent_humans", "cases": review})
    with (args.output / "annotation-review-template.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        fields = ["case_id", "question", "is_answerable", "reference_answer", "required_points_json",
                  "evidence_groups_json", "relevant_chunk_ids_json", "relevant_page_ranges_json",
                  "reviewer_1_id", "reviewer_1_reviewed_at", "reviewer_1_approved", "reviewer_1_notes",
                  "reviewer_2_id", "reviewer_2_reviewed_at", "reviewer_2_approved", "reviewer_2_notes",
                  "adjudication_notes"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for case, row in zip(dataset.cases, review):
            writer.writerow({"case_id": case.case_id, "question": case.question, "is_answerable": case.is_answerable,
                "reference_answer": case.reference_answer or "", "required_points_json": json.dumps(case.required_points, ensure_ascii=False),
                "evidence_groups_json": json.dumps(row["required_evidence_groups"], ensure_ascii=False),
                "relevant_chunk_ids_json": json.dumps([str(v) for v in case.relevant_chunk_ids]),
                "relevant_page_ranges_json": json.dumps([p.model_dump(mode="json") for p in case.relevant_page_ranges]),
                "reviewer_1_id": "", "reviewer_1_reviewed_at": "", "reviewer_1_approved": "", "reviewer_1_notes": "",
                "reviewer_2_id": "", "reviewer_2_reviewed_at": "", "reviewer_2_approved": "", "reviewer_2_notes": "",
                "adjudication_notes": ""})
    print(json.dumps({"cases": len(cases), "answerable": sum(c.is_answerable for c in dataset.cases),
                      "classification": "mechanism_test", "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
