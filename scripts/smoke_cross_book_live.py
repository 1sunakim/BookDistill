"""Exercise the deployed cross-book API with two synthetic PDF books.

Requires BOOKDISTILL_API_KEY in the environment. Uses only generated content.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pymupdf


BOOKS = [
    ("Planning Handbook A", "Planning begins by defining a goal. An agent breaks the goal into ordered steps, uses a search tool for evidence, checks the tool output, and revises the plan before answering. A reliable agent cites the source of each claim. Planning is useful when a question needs several dependent actions."),
    ("Planning Handbook B", "Planning begins by identifying constraints. An agent chooses one tool at a time, observes the result, and adjusts the next step. Tool output is evidence rather than an instruction. A reliable agent verifies source citations before reporting a conclusion. Planning is useful when several books must be compared."),
]
QUESTION = "How do the two books explain agent planning and tool use?"


def make_pdf(path: Path, title: str, body: str) -> None:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 82), title, fontsize=18)
    for index in range(4):
        page.insert_textbox((72, 120 + index * 145, 520, 250 + index * 145),
                            f"Chapter {index + 1}: Agent planning. {body}", fontsize=11)
    document.set_toc([[1, "Agent planning", 1]])
    document.save(path)
    document.close()


def wait_for_job(client: httpx.Client, job_id: str, deadline: float) -> dict:
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/jobs/{job_id}")
        response.raise_for_status()
        job = response.json()
        if job["status"] in {"succeeded", "failed", "cancelled"}:
            return job
        time.sleep(2)
    raise TimeoutError(f"job {job_id} exceeded test deadline")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18027")
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/cross-book-live-smoke"))
    parser.add_argument("--timeout-seconds", type=int, default=420)
    args = parser.parse_args()
    key = os.getenv("BOOKDISTILL_API_KEY")
    if not key:
        parser.error("BOOKDISTILL_API_KEY must be set in the environment")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + args.timeout_seconds
    book_ids: list[str] = []
    pdf_text_by_book: dict[str, str] = {}
    with httpx.Client(base_url=args.base_url, headers={"X-BookDistill-API-Key": key}, timeout=30) as client:
        response = client.get("/api/v1/health/ready")
        response.raise_for_status()
        for index, (title, body) in enumerate(BOOKS, start=1):
            path = args.output_dir / f"synthetic-planning-{index}.pdf"
            make_pdf(path, title, body)
            with path.open("rb") as stream:
                response = client.post("/api/v1/books", files={"file": (path.name, stream, "application/pdf")},
                                       data={"title": title, "authors": "Synthetic Test", "language": "en"})
            response.raise_for_status()
            accepted = response.json()
            job = wait_for_job(client, accepted["task_id"], deadline)
            if job["status"] != "succeeded":
                raise RuntimeError(f"book {index} processing ended as {job['status']}: {job.get('error')}")
            book_id = accepted["book_id"]
            book = client.get(f"/api/v1/books/{book_id}")
            book.raise_for_status()
            if book.json()["status"] != "ready":
                raise RuntimeError(f"book {index} is not ready")
            book_ids.append(book_id)
            with pymupdf.open(path) as document:
                pdf_text_by_book[book_id] = " ".join(document[0].get_text().split())

        payload = {"question": QUESTION, "book_ids": book_ids, "question_count": 2}
        headers = {"Idempotency-Key": f"synthetic-smoke:{uuid4()}"}
        response = client.post("/api/v1/agent/runs", json=payload, headers=headers)
        response.raise_for_status()
        run = response.json()
        duplicate = client.post("/api/v1/agent/runs", json=payload, headers=headers)
        duplicate.raise_for_status()
        if duplicate.json()["run_id"] != run["run_id"]:
            raise AssertionError("idempotent submission created another run")
        while run["status"] not in {"succeeded", "refused", "failed", "cancelled"} and time.monotonic() < deadline:
            time.sleep(2)
            response = client.get(f"/api/v1/agent/runs/{run['run_id']}")
            response.raise_for_status()
            run = response.json()
        if run["status"] != "succeeded":
            raise RuntimeError(f"cross-book run ended as {run['status']}: {run.get('error_code')}")
        result = run["result"]
        comparison = result["comparison"]
        questions = result["questions"]["questions"]
        citations = comparison["citations"]
        if len(comparison["per_book"]) != 2 or len(questions) != 2 or not citations:
            raise AssertionError("comparison, questions or citations are incomplete")
        if {citation["book_id"] for citation in citations} != set(book_ids):
            raise AssertionError("citations do not cover exactly the selected books")
        for citation in citations:
            quote = " ".join(citation.get("quote", "").split())
            if (not quote or quote not in pdf_text_by_book[citation["book_id"]]
                    or citation["page_start"] != 0 or citation["page_end"] != 0):
                raise AssertionError("citation does not match its source PDF and page")
        if not run.get("trace_id") or not run.get("events"):
            raise AssertionError("run trace or events are missing")
        summary = {"classification": "synthetic_engineering_smoke", "status": run["status"],
                   "run_id": run["run_id"], "trace_id": run["trace_id"], "book_ids": book_ids,
                   "per_book_count": len(comparison["per_book"]), "citation_count": len(citations),
                   "question_count": len(questions), "idempotency_reused_run": True,
                   "event_count": len(run.get("events", []))}
        (args.output_dir / "result.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
