from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from uuid import uuid4

import pymupdf
from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from bookdistill.api.app import create_app  # noqa: E402
from bookdistill.contracts import ParsedDocument  # noqa: E402
from bookdistill.settings import Settings  # noqa: E402


def make_text_pdf() -> bytes:
    document = pymupdf.open()
    first_page = document.new_page()
    first_page.insert_text((220, 20), "BookDistill Handbook", fontsize=9)
    first_page.insert_text((72, 82), "1 Introduction", fontsize=20)
    first_page.insert_text(
        (72, 130),
        "BookDistill retrieval augmented generation document with searchable text.",
    )
    first_page.insert_text((295, 820), "1", fontsize=9)
    second_page = document.new_page()
    second_page.insert_text((220, 20), "BookDistill Handbook", fontsize=9)
    second_page.insert_text((72, 82), "1.1 Evidence", fontsize=16)
    second_page.insert_text(
        (72, 130),
        "Every parsed block keeps coordinates and a stable source identifier.",
    )
    second_page.insert_text((295, 820), "2", fontsize=9)
    document.set_toc([[1, "1 Introduction", 1], [2, "1.1 Evidence", 2]])
    payload = document.tobytes()
    document.close()
    return payload


def make_scanned_like_pdf() -> bytes:
    document = pymupdf.open()
    document.new_page()
    payload = document.tobytes()
    document.close()
    return payload


class IngestionApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        settings = Settings(
            database_url=f"sqlite:///{(root / 'bookdistill.db').as_posix()}",
            storage_root=root / "storage",
            max_upload_bytes=2 * 1024 * 1024,
            minimum_text_characters_per_page=10,
        )
        self.app = create_app(settings)
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()
        self.app.state.engine.dispose()
        self.temporary.cleanup()

    def test_upload_parse_query_and_deduplicate(self) -> None:
        pdf = make_text_pdf()
        response = self.client.post(
            "/v1/books",
            files={"file": ("rag.pdf", io.BytesIO(pdf), "application/pdf")},
            data={"title": "RAG Test Book", "authors": "Alice,Bob", "language": "en"},
        )
        self.assertEqual(response.status_code, 202, response.text)
        accepted = response.json()
        self.assertFalse(accepted["duplicate"])
        self.assertIsNotNone(accepted["task_id"])

        task_response = self.client.get(f"/api/v1/jobs/{accepted['task_id']}")
        self.assertEqual(task_response.status_code, 200)
        self.assertEqual(task_response.json()["job_type"], "document.process")
        self.assertEqual(task_response.json()["status"], "succeeded")
        self.assertEqual(
            [stage["stage_name"] for stage in task_response.json()["stages"]],
            ["parse", "clean", "structure", "chunk", "persist", "index"],
        )

        job_response = self.client.get(f"/v1/jobs/{accepted['job_id']}")
        self.assertEqual(job_response.status_code, 200)
        self.assertEqual(job_response.json()["status"], "succeeded")
        self.assertEqual(
            job_response.json()["completed_stages"],
            ["validate", "classify", "parse", "clean", "structure", "chunk"],
        )

        book_response = self.client.get(f"/v1/books/{accepted['book_id']}")
        self.assertEqual(book_response.status_code, 200)
        self.assertEqual(book_response.json()["sha256"], __import__("hashlib").sha256(pdf).hexdigest())
        self.assertEqual(book_response.json()["status"], "ready")

        books_response = self.client.get("/v1/books")
        self.assertEqual(books_response.status_code, 200, books_response.text)
        self.assertEqual([item["book_id"] for item in books_response.json()], [accepted["book_id"]])

        document_response = self.client.get(
            f"/v1/books/{accepted['book_id']}/parsed-document"
        )
        self.assertEqual(document_response.status_code, 200, document_response.text)
        document = ParsedDocument.model_validate(document_response.json())
        self.assertEqual(len(document.pages), 2)
        self.assertTrue(all(page.blocks for page in document.pages))
        self.assertTrue(
            all("repeated_header_detected" in page.quality_flags for page in document.pages)
        )

        chapters_response = self.client.get(
            f"/v1/books/{accepted['book_id']}/chapters"
        )
        self.assertEqual(chapters_response.status_code, 200, chapters_response.text)
        chapters = chapters_response.json()
        self.assertEqual([chapter["title"] for chapter in chapters], ["1 Introduction", "1.1 Evidence"])
        self.assertEqual(chapters[0]["level"], 1)
        self.assertEqual(chapters[1]["level"], 2)
        self.assertEqual(chapters[1]["parent_id"], chapters[0]["chapter_id"])

        chunks_response = self.client.get(f"/v1/books/{accepted['book_id']}/chunks")
        self.assertEqual(chunks_response.status_code, 200, chunks_response.text)
        chunks = chunks_response.json()
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0]["chapter_id"], chapters[0]["chapter_id"])
        self.assertEqual(chunks[1]["chapter_id"], chapters[1]["chapter_id"])
        self.assertEqual(chunks[0]["next_chunk_id"], chunks[1]["chunk_id"])
        self.assertEqual(chunks[1]["previous_chunk_id"], chunks[0]["chunk_id"])
        self.assertLessEqual(chunks[0]["token_count"], 512)

        retrieval_response = self.client.post(
            "/api/v1/retrieval/search",
            json={"request_id": str(uuid4()), "query": "Evidence"},
        )
        self.assertEqual(retrieval_response.status_code, 200, retrieval_response.text)
        self.assertTrue(retrieval_response.json()["evidence"])

        dataset_response = self.client.post(
            "/api/v1/evaluation/datasets",
            json={
                "schema_version": "1.0",
                "dataset_name": "uploaded-book",
                "dataset_version": "v1",
                "split": "smoke",
                "description": "API integration smoke dataset",
                "cases": [{
                    "schema_version": "1.0",
                    "case_id": "evidence-location",
                    "question": "What keeps coordinates and a stable identifier?",
                    "question_type": "fact",
                    "book_scope": [accepted["book_id"]],
                    "chapter_scope": [],
                    "reference_answer": "Every parsed block.",
                    "relevant_chunk_ids": [chunks[1]["chunk_id"]],
                    "relevant_page_ranges": [],
                    "difficulty": "easy",
                    "annotator": "integration-test",
                    "review_status": "approved",
                    "is_answerable": True,
                    "tags": ["smoke"],
                }],
            },
        )
        self.assertEqual(dataset_response.status_code, 201, dataset_response.text)
        self.assertEqual(dataset_response.json()["dataset_key"], "uploaded-book.v1")
        evaluation_response = self.client.post(
            "/api/v1/evaluation/runs",
            json={
                "dataset_key": "uploaded-book.v1",
                "experiment": {
                    "schema_version": "1.0",
                    "experiment_id": "api-smoke",
                    "dataset_version": "v1",
                    "retrieval_variant": "hybrid_rerank",
                    "parser_version": "pymupdf-v1",
                    "chunk_version": chunks[0]["chunk_version"],
                    "embedding_model": "memory-hash-v1",
                    "sparse_config": {},
                    "fusion_config": {"strategy": "rrf"},
                    "reranker_model": "lexical-v1",
                    "generation_config": {},
                },
            },
        )
        self.assertEqual(evaluation_response.status_code, 201, evaluation_response.text)
        self.assertEqual(evaluation_response.json()["aggregate"]["evaluated_cases"], 1)
        self.assertTrue((
            self.app.state.settings.storage_root
            / "evaluation-runs"
            / "api-smoke"
            / "result.json"
        ).exists())

        artifact_path = (
            self.app.state.settings.storage_root
            / "books"
            / accepted["book_id"]
            / "parsed_document.v1.json"
        )
        artifact = ParsedDocument.model_validate(
            json.loads(artifact_path.read_text(encoding="utf-8"))
        )
        self.assertEqual(artifact.document_id, document.document_id)
        chunks_artifact = artifact_path.with_name("chunks.v1.json")
        self.assertTrue(chunks_artifact.exists())

        duplicate = self.client.post(
            "/v1/books",
            files={"file": ("renamed.pdf", io.BytesIO(pdf), "application/pdf")},
            data={"language": "en"},
        )
        self.assertEqual(duplicate.status_code, 202)
        self.assertTrue(duplicate.json()["duplicate"])
        self.assertEqual(duplicate.json()["book_id"], accepted["book_id"])
        self.assertEqual(duplicate.json()["job_id"], accepted["job_id"])
        self.assertEqual(duplicate.json()["task_id"], accepted["task_id"])

    def test_reprocess_rebuild_and_delete_interfaces(self) -> None:
        uploaded = self.client.post(
            "/v1/books",
            files={"file": ("rag.pdf", io.BytesIO(make_text_pdf()), "application/pdf")},
            data={"language": "en"},
        ).json()
        book_id = uploaded["book_id"]

        reprocess = self.client.post(
            f"/v1/books/{book_id}/reprocess",
            json={"idempotency_key": f"reprocess-{book_id}", "max_attempts": 2},
        )
        self.assertEqual(reprocess.status_code, 202, reprocess.text)
        self.assertEqual(reprocess.json()["status"], "succeeded")
        repeated = self.client.post(
            f"/v1/books/{book_id}/reprocess",
            json={"idempotency_key": f"reprocess-{book_id}", "max_attempts": 2},
        )
        self.assertEqual(repeated.json()["job_id"], reprocess.json()["job_id"])

        chapters = self.client.get(f"/v1/books/{book_id}/chapters").json()
        chunks_before = self.client.get(f"/v1/books/{book_id}/chunks").json()
        rebuild = self.client.post(
            f"/v1/books/{book_id}/rebuild",
            json={
                "from_stage": "chunk",
                "chapter_ids": [chapters[1]["chapter_id"]],
                "idempotency_key": f"chunk-rebuild-{book_id}",
            },
        )
        self.assertEqual(rebuild.status_code, 202, rebuild.text)
        self.assertEqual(rebuild.json()["status"], "succeeded")
        chunks_after = self.client.get(f"/v1/books/{book_id}/chunks").json()
        self.assertEqual(
            [chunk["chunk_id"] for chunk in chunks_after],
            [chunk["chunk_id"] for chunk in chunks_before],
        )

        invalid_scope = self.client.post(
            f"/v1/books/{book_id}/rebuild",
            json={"from_stage": "clean", "chapter_ids": [chapters[0]["chapter_id"]]},
        )
        self.assertEqual(invalid_scope.status_code, 422)

        storage_path = self.app.state.settings.storage_root / "books" / book_id
        deleted = self.client.delete(f"/v1/books/{book_id}")
        self.assertEqual(deleted.status_code, 204, deleted.text)
        self.assertFalse(storage_path.exists())
        self.assertEqual(self.client.get(f"/v1/books/{book_id}").status_code, 404)
        self.assertEqual(
            self.client.get(f"/api/v1/jobs/{rebuild.json()['job_id']}").status_code,
            200,
        )

    def test_delete_is_blocked_while_document_job_is_queued(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = create_app(
                Settings(
                    database_url=f"sqlite:///{(root / 'blocked.db').as_posix()}",
                    storage_root=root / "storage",
                    queue_mode="disabled",
                    max_upload_bytes=2 * 1024 * 1024,
                )
            )
            with TestClient(app) as client:
                uploaded = client.post(
                    "/v1/books",
                    files={
                        "file": (
                            "queued.pdf",
                            io.BytesIO(make_text_pdf()),
                            "application/pdf",
                        )
                    },
                    data={"language": "en"},
                ).json()
                blocked = client.delete(f"/v1/books/{uploaded['book_id']}")
                self.assertEqual(blocked.status_code, 409)
                self.assertEqual(blocked.json()["detail"]["code"], "BOOK_JOB_ACTIVE")

    def test_invalid_pdf_header_returns_structured_error(self) -> None:
        response = self.client.post(
            "/v1/books",
            files={"file": ("fake.pdf", io.BytesIO(b"not a pdf"), "application/pdf")},
            data={"language": "en"},
        )
        self.assertEqual(response.status_code, 422)
        payload = response.json()
        self.assertEqual(payload["code"], "INVALID_PDF_HEADER")
        self.assertEqual(payload["stage"], "validate")

    def test_declared_non_pdf_mime_is_rejected(self) -> None:
        response = self.client.post(
            "/v1/books",
            files={"file": ("rag.pdf", io.BytesIO(make_text_pdf()), "text/plain")},
            data={"language": "en"},
        )
        self.assertEqual(response.status_code, 415)
        self.assertEqual(response.json()["code"], "INVALID_MIME_TYPE")

    def test_upload_size_limit_is_enforced_while_streaming(self) -> None:
        oversized = b"%PDF-" + b"x" * (2 * 1024 * 1024)
        response = self.client.post(
            "/v1/books",
            files={"file": ("large.pdf", io.BytesIO(oversized), "application/pdf")},
            data={"language": "en"},
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["code"], "UPLOAD_TOO_LARGE")

    def test_textless_page_fails_as_unsupported_without_ocr(self) -> None:
        response = self.client.post(
            "/v1/books",
            files={
                "file": (
                    "scanned.pdf",
                    io.BytesIO(make_scanned_like_pdf()),
                    "application/pdf",
                )
            },
            data={"language": "en"},
        )
        self.assertEqual(response.status_code, 202)
        job = self.client.get(f"/v1/jobs/{response.json()['job_id']}").json()
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error"]["code"], "SCANNED_PAGE_UNSUPPORTED")
        self.assertEqual(job["error"]["page_index"], 0)


if __name__ == "__main__":
    unittest.main()
