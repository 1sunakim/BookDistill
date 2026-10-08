"""Black-box Compose smoke test: upload -> worker -> retrieval -> QA -> delete."""

from __future__ import annotations

import io
import os
import time
from uuid import uuid4

import httpx
import pymupdf


def make_pdf() -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 80), "1 Worker recovery", fontsize=18)
    page.insert_text(
        (72, 130),
        "BookDistill workers parse documents and persist searchable evidence.",
    )
    document.set_toc([[1, "1 Worker recovery", 1]])
    payload = document.tobytes()
    document.close()
    return payload


def main() -> None:
    base_url = os.getenv("BOOKDISTILL_E2E_BASE_URL", "http://127.0.0.1:8000")
    headers = {}
    if api_key := os.getenv("BOOKDISTILL_API_KEY"):
        headers["X-BookDistill-API-Key"] = api_key
    with httpx.Client(base_url=base_url, headers=headers, timeout=30) as client:
        ready = client.get("/api/v1/health/ready")
        ready.raise_for_status()
        uploaded = client.post(
            "/api/v1/books",
            files={"file": ("worker-e2e.pdf", io.BytesIO(make_pdf()), "application/pdf")},
            data={"title": "Worker E2E", "language": "en"},
        )
        uploaded.raise_for_status()
        accepted = uploaded.json()
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            job = client.get(f"/api/v1/jobs/{accepted['task_id']}")
            job.raise_for_status()
            state = job.json()["status"]
            if state == "succeeded":
                break
            if state in {"failed", "cancelled"}:
                raise RuntimeError(f"document task ended as {state}: {job.text}")
            time.sleep(1)
        else:
            raise TimeoutError("document worker did not complete within 120 seconds")

        scope = {"book_ids": [accepted["book_id"]], "chapter_ids": []}
        retrieval = client.post("/api/v1/retrieval/search", json={
            "schema_version": "1.0", "request_id": str(uuid4()),
            "query": "How do workers persist searchable evidence?", "scope": scope,
        })
        retrieval.raise_for_status()
        assert retrieval.json()["evidence"], retrieval.text

        answer = client.post("/api/v1/rag/answer", json={
            "schema_version": "1.0",
            "trace_context": {"request_id": str(uuid4()), "trace_id": str(uuid4())},
            "question": "How do workers persist searchable evidence?",
            "scope": scope, "language": "en",
        })
        answer.raise_for_status()
        assert answer.json()["answer"]["citations"], answer.text

        deleted = client.delete(f"/api/v1/books/{accepted['book_id']}")
        deleted.raise_for_status()
        assert client.get(f"/api/v1/books/{accepted['book_id']}").status_code == 404
    print("Docker E2E passed: upload -> worker -> index -> retrieve -> answer -> delete")


if __name__ == "__main__":
    main()
