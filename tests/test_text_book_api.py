from __future__ import annotations

import io
from pathlib import Path
import sys
import tempfile
import unittest
from zipfile import ZipFile
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bookdistill.api.app import create_app
from bookdistill.db import SourceChunkIndexRecord
from bookdistill.settings import Settings
from bookdistill.task_system.dispatch import NullDispatcher
from bookdistill.task_system.repository import utc_now
from bookdistill.worker import _sync_cancelled_document
from tests.support import migrate_database
from tests.test_container_formats import epub_bytes, docx_bytes
from datetime import timedelta


class TextBookApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        settings = Settings(
            database_url=f"sqlite:///{(root / 'books.db').as_posix()}",
            storage_root=root / "storage", max_upload_bytes=1024 * 1024,
            api_key="text-test-key",
        )
        migrate_database(settings.database_url)
        self.app = create_app(settings)
        self.client = TestClient(self.app)
        self.client.headers.update({"X-BookDistill-API-Key": "text-test-key"})

    def tearDown(self) -> None:
        self.client.close()
        self.app.state.engine.dispose()
        self.temporary.cleanup()

    def test_markdown_upload_read_and_deduplicate(self) -> None:
        payload = "# 第一章\n正文一。\n## 小节\n正文二。\n".encode("utf-8")
        response = self.client.post(
            "/api/v2/books", files={"file": ("sample.md", io.BytesIO(payload), "text/markdown")},
        )
        self.assertEqual(response.status_code, 202, response.text)
        accepted = response.json()
        self.assertFalse(accepted["duplicate"])
        task = self.client.get(f"/api/v1/jobs/{accepted['task_id']}").json()
        self.assertEqual(task["status"], "succeeded")
        self.assertEqual(task["progress_current"], 6)
        self.assertEqual([stage["stage_name"] for stage in task["stages"]],
                         ["parse", "clean", "structure", "chunk", "persist", "index"])
        self.assertEqual(task["output_reference"]["index_status"], "ready")
        book = accepted["book"]
        self.assertTrue(book["retrieval_ready"])
        self.assertEqual(book["format"], "md")
        self.assertEqual(book["authors"], [])
        self.assertEqual(book["size_bytes"], len(payload))
        self.assertEqual(book["language"], "zh-CN")
        self.assertIsNone(book["page_count"])
        book_id = book["book_id"]
        chapters = self.client.get(f"/api/v2/books/{book_id}/chapters").json()
        self.assertEqual([chapter["title"] for chapter in chapters], ["第一章", "小节"])
        chunks = self.client.get(f"/api/v2/books/{book_id}/chunks").json()
        self.assertEqual([chunk["text"] for chunk in chunks], ["正文一。", "正文二。"])
        self.assertEqual(chunks[0]["source_start"]["line_start"], 2)
        self.assertNotIn("page_start", chunks[0])
        search = self.client.post("/api/v2/retrieval/search", json={
            "request_id": str(uuid4()), "query": "正文二", "book_ids": [book_id],
            "chapter_ids": [chapters[1]["chapter_id"]],
        })
        self.assertEqual(search.status_code, 200, search.text)
        evidence = search.json()["evidence"]
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["source_start"]["kind"], "text_line")
        self.assertEqual(evidence[0]["source_start"]["line_start"], 4)
        self.assertNotIn("page_start", evidence[0])
        self.assertEqual(self.client.post(
            "/api/v2/retrieval/search", json={"request_id": str(uuid4()), "query": "正文二"},
            headers={"X-BookDistill-API-Key": "wrong"},
        ).status_code, 401)
        restarted = create_app(self.app.state.settings, NullDispatcher())
        with TestClient(restarted) as client:
            client.headers.update({"X-BookDistill-API-Key": "text-test-key"})
            persisted = client.post("/api/v2/retrieval/search", json={
                "request_id": str(uuid4()), "query": "正文二", "book_ids": [book_id],
                "chapter_ids": [chapters[1]["chapter_id"]],
            })
            self.assertEqual(persisted.status_code, 200, persisted.text)
            self.assertEqual(persisted.json()["evidence"][0]["chunk_id"], evidence[0]["chunk_id"])
        restarted.state.engine.dispose()
        document = self.client.get(f"/api/v2/books/{book_id}/parsed-document").json()
        self.assertEqual(document["schema_version"], "2.0")
        snapshot = self.client.get(f"/api/v2/books/{book_id}/source-snapshot")
        self.assertEqual(snapshot.status_code, 200, snapshot.text)
        self.assertEqual(snapshot.headers["cache-control"], "private, no-store")
        self.assertEqual(snapshot.json()["document"], document)
        self.assertEqual(snapshot.json()["chunks"], chunks)
        self.assertEqual(snapshot.json()["document_revision"], evidence[0]["document_revision"])
        self.assertEqual(self.client.get(
            f"/api/v2/books/{book_id}/source-snapshot",
            headers={"X-BookDistill-API-Key": "wrong"},
        ).status_code, 401)
        source = self.client.get(f"/api/v2/books/{book_id}/source")
        self.assertEqual(source.text, payload.decode("utf-8"))
        self.assertEqual(
            self.client.get(f"/api/v2/books/{book_id}/source", headers={"X-BookDistill-API-Key": "wrong"}).status_code,
            401,
        )
        self.assertEqual(self.client.get("/api/v1/books").json(), [])
        duplicate = self.client.post(
            "/api/v2/books", files={"file": ("again.md", io.BytesIO(payload), "text/markdown")},
        )
        self.assertEqual(duplicate.status_code, 202, duplicate.text)
        self.assertTrue(duplicate.json()["duplicate"])
        self.assertEqual(duplicate.json()["book"]["book_id"], book_id)
        self.assertEqual(duplicate.json()["task_id"], accepted["task_id"])
        wrong_mime = self.client.post(
            "/api/v2/books", files={"file": ("again.md", io.BytesIO(payload), "application/pdf")},
        )
        self.assertEqual(wrong_mime.status_code, 415)
        self.assertEqual(self.client.get("/api/v2/books").json()[0]["book_id"], book_id)

    def test_txt_upload_and_invalid_file(self) -> None:
        payload = "1 Introduction\nUseful body text.\n".encode()
        response = self.client.post(
            "/api/v2/books", files={"file": ("sample.txt", io.BytesIO(payload), "text/plain")},
        )
        self.assertEqual(response.status_code, 202, response.text)
        book_id = response.json()["book"]["book_id"]
        chunks = self.client.get(f"/api/v2/books/{book_id}/chunks").json()
        self.assertEqual(chunks[0]["source_start"]["line_start"], 2)
        invalid = self.client.post(
            "/api/v2/books", files={"file": ("bad.txt", io.BytesIO(b"bad\x00data"), "text/plain")},
        )
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(invalid.json()["code"], "BINARY_TEXT_FILE")

    def test_plain_text_without_heading_uses_whole_book_chapter(self) -> None:
        response = self.client.post(
            "/api/v2/books", files={"file": ("notes.txt", io.BytesIO(b"First line\nSecond line"), "text/plain")},
        )
        self.assertEqual(response.status_code, 202, response.text)
        book_id = response.json()["book"]["book_id"]
        chapters = self.client.get(f"/api/v2/books/{book_id}/chapters").json()
        self.assertEqual([chapter["title"] for chapter in chapters], ["全文"])

    def test_long_source_line_splits_into_distinct_chunks(self) -> None:
        payload = ("word " * 1100).encode()
        response = self.client.post(
            "/api/v2/books", files={"file": ("long.txt", io.BytesIO(payload), "text/plain")},
        )
        self.assertEqual(response.status_code, 202, response.text)
        book_id = response.json()["book"]["book_id"]
        chunks = self.client.get(f"/api/v2/books/{book_id}/chunks").json()
        self.assertGreater(len(chunks), 1)
        self.assertEqual(len({chunk["chunk_id"] for chunk in chunks}), len(chunks))
        self.assertTrue(all(chunk["source_start"]["line_start"] == 1 for chunk in chunks))

    def test_epub_and_docx_upload_share_document_task_without_page_numbers(self) -> None:
        for filename, payload, mime, expected_kind in [
            ("sample.epub", epub_bytes(), "application/epub+zip", "epub_anchor"),
            ("sample.docx", docx_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx_block"),
        ]:
            with self.subTest(filename=filename):
                response = self.client.post(
                    "/api/v2/books", files={"file": (filename, io.BytesIO(payload), mime)},
                )
                self.assertEqual(response.status_code, 202, response.text)
                accepted = response.json()
                book_id = accepted["book"]["book_id"]
                self.assertEqual(accepted["book"]["status"], "processed")
                self.assertIsNone(accepted["book"]["page_count"])
                self.assertEqual(self.client.get(f"/api/v1/jobs/{accepted['task_id']}").json()["status"], "succeeded")
                chapters = self.client.get(f"/api/v2/books/{book_id}/chapters").json()
                chunks = self.client.get(f"/api/v2/books/{book_id}/chunks").json()
                self.assertTrue(chapters)
                self.assertTrue(chunks)
                if filename.endswith(".epub"):
                    self.assertEqual([chapter["title"] for chapter in chapters], ["起始章", "末章", "Later chapter"])
                    self.assertIsNone(chapters[1]["parent_id"])
                    self.assertEqual(chapters[2]["parent_id"], chapters[1]["chapter_id"])
                self.assertEqual(chunks[0]["source_start"]["kind"], expected_kind)
                self.assertNotIn("page_start", chunks[0])
                snapshot = self.client.get(f"/api/v2/books/{book_id}/source-snapshot")
                self.assertEqual(snapshot.status_code, 200, snapshot.text)
                self.assertEqual(snapshot.json()["format"], filename.rsplit(".", 1)[1])
                self.assertEqual(snapshot.json()["chunks"][0]["source_start"]["kind"], expected_kind)
                search = self.client.post("/api/v2/retrieval/search", json={
                    "request_id": str(uuid4()), "query": "body" if filename.endswith(".epub") else "Cell A",
                    "book_ids": [book_id],
                })
                self.assertEqual(search.status_code, 200, search.text)
                self.assertTrue(search.json()["evidence"])
                self.assertEqual(search.json()["evidence"][0]["source_start"]["kind"], expected_kind)
                original = self.client.get(f"/api/v2/books/{book_id}/source")
                self.assertEqual(original.content, payload)
                self.assertEqual(original.headers["content-type"], mime)
                exported = self.client.get(f"/api/v2/exports/obsidian?book_id={book_id}&include_original=true")
                self.assertEqual(exported.status_code, 200, exported.text[:200])
                with ZipFile(io.BytesIO(exported.content)) as archive:
                    self.assertEqual(archive.read(f"BookDistill/{book_id}/original.{filename.rsplit('.', 1)[1]}"), payload)
                    chapter_notes = [name for name in archive.namelist() if "/chapters/" in name]
                    self.assertTrue(chapter_notes)
                    self.assertIn(expected_kind.split("_")[0].upper() if expected_kind == "epub_anchor" else "段落",
                                  "\n".join(archive.read(name).decode("utf-8") for name in chapter_notes))
                self.assertEqual(self.client.get(
                    f"/api/v2/books/{book_id}/source",
                    headers={"X-BookDistill-API-Key": "wrong"},
                ).status_code, 401)

    def test_corrupt_epub_creates_no_book_or_task(self) -> None:
        response = self.client.post(
            "/api/v2/books", files={"file": ("broken.epub", io.BytesIO(b"broken"), "application/epub+zip")},
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["code"], "CORRUPT_BOOK_ARCHIVE")
        self.assertEqual(self.client.get("/api/v2/books").json(), [])
        self.assertEqual(self.client.get("/api/v1/jobs").json()["total"], 0)

    def test_reindex_and_delete_preserve_v1_v2_boundary(self) -> None:
        uploaded = self.client.post(
            "/api/v2/books",
            files={"file": ("notes.md", io.BytesIO(b"# Notes\nUnique phrase here.\n"), "text/markdown")},
        )
        self.assertEqual(uploaded.status_code, 202, uploaded.text)
        book_id = uploaded.json()["book"]["book_id"]
        self.assertEqual(self.client.delete(f"/api/v1/books/{book_id}").status_code, 404)
        self.assertEqual(self.client.get(f"/api/v2/books/{book_id}").status_code, 200)
        with self.app.state.text_book_service.sessions.begin() as session:
            session.execute(delete(SourceChunkIndexRecord).where(SourceChunkIndexRecord.book_id == book_id))
        self.assertFalse(self.client.get(f"/api/v2/books/{book_id}").json()["retrieval_ready"])
        reindex = self.client.post(f"/api/v2/books/{book_id}/reprocess", json={
            "from_stage": "index", "idempotency_key": f"reindex-{book_id}",
        })
        self.assertEqual(reindex.status_code, 202, reindex.text)
        self.assertEqual(reindex.json()["status"], "succeeded")
        self.assertEqual(reindex.json()["output_reference"]["index_status"], "ready")
        self.assertTrue(self.client.get(f"/api/v2/books/{book_id}").json()["retrieval_ready"])
        again = self.client.post(f"/api/v2/books/{book_id}/reprocess", json={
            "from_stage": "index", "idempotency_key": f"reindex-{book_id}",
        })
        self.assertEqual(again.json()["job_id"], reindex.json()["job_id"])
        search_payload = {"request_id": str(uuid4()), "query": "Unique phrase", "book_ids": [book_id]}
        self.assertEqual(len(self.client.post("/api/v2/retrieval/search", json=search_payload).json()["evidence"]), 1)
        deleted = self.client.delete(f"/api/v2/books/{book_id}")
        self.assertEqual(deleted.status_code, 204, deleted.text)
        self.assertEqual(self.client.get(f"/api/v2/books/{book_id}").status_code, 404)
        self.assertEqual(self.client.post("/api/v2/retrieval/search", json=search_payload).json()["evidence"], [])
        self.assertEqual(self.client.delete(f"/api/v2/books/{book_id}").status_code, 404)
        self.assertFalse((self.app.state.settings.storage_root / "books" / book_id).exists())

    def test_source_aware_obsidian_export(self) -> None:
        payload = b"# Notes\nA unique sentence.\n"
        uploaded = self.client.post(
            "/api/v2/books", files={"file": ("notes.md", io.BytesIO(payload), "text/markdown")},
        )
        book_id = uploaded.json()["book"]["book_id"]
        unauthenticated = self.client.get(
            f"/api/v2/exports/obsidian?book_id={book_id}",
            headers={"X-BookDistill-API-Key": "wrong"},
        )
        self.assertEqual(unauthenticated.status_code, 401)
        exported = self.client.get(f"/api/v2/exports/obsidian?book_id={book_id}&include_original=true")
        self.assertEqual(exported.status_code, 200, exported.text[:200])
        with ZipFile(io.BytesIO(exported.content)) as archive:
            names = archive.namelist()
            self.assertIn(f"BookDistill/{book_id}/original.md", names)
            self.assertEqual(archive.read(f"BookDistill/{book_id}/original.md"), payload)
            chapter_name = next(name for name in names if "/chapters/" in name)
            chapter = archive.read(chapter_name).decode("utf-8")
            self.assertIn("原文第 2", chapter)
            self.assertIn("A unique sentence.", chapter)
            self.assertNotIn("page_start", chapter)

    def test_reparse_rejects_changed_stored_original(self) -> None:
        uploaded = self.client.post(
            "/api/v2/books", files={"file": ("notes.txt", io.BytesIO(b"# Notes\nTrusted body.\n"), "text/plain")},
        )
        self.assertEqual(uploaded.status_code, 202, uploaded.text)
        book_id = uploaded.json()["book"]["book_id"]
        original = self.app.state.text_book_service.storage.original_path(UUID(book_id))
        original.write_bytes(b"# Notes\nChanged body.\n")
        rebuild = self.client.post(f"/api/v2/books/{book_id}/reprocess", json={"from_stage": "parse"})
        self.assertEqual(rebuild.status_code, 202, rebuild.text)
        self.assertEqual(rebuild.json()["status"], "failed")
        self.assertEqual(rebuild.json()["error"]["code"], "BOOK_SOURCE_CHANGED")
        self.assertFalse(self.client.get(f"/api/v2/books/{book_id}").json()["retrieval_ready"])


class QueuedTextBookApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        settings = Settings(
            database_url=f"sqlite:///{(root / 'books.db').as_posix()}",
            storage_root=root / "storage", queue_mode="disabled",
            max_upload_bytes=1024 * 1024, api_key="text-test-key",
        )
        migrate_database(settings.database_url)
        self.app = create_app(settings, NullDispatcher())
        self.client = TestClient(self.app)
        self.client.headers.update({"X-BookDistill-API-Key": "text-test-key"})

    def tearDown(self) -> None:
        self.client.close()
        self.app.state.engine.dispose()
        self.temporary.cleanup()

    def upload_queued(self) -> dict:
        response = self.client.post(
            "/api/v2/books",
            files={"file": ("queued.md", io.BytesIO(b"# Chapter\nBody text.\n"), "text/markdown")},
        )
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def test_queued_book_can_be_cancelled_without_parsing(self) -> None:
        accepted = self.upload_queued()
        book_id = accepted["book"]["book_id"]
        self.assertEqual(accepted["book"]["status"], "queued")
        self.assertIsNone(accepted["book"]["source_unit_count"])
        self.assertEqual(self.client.get(f"/api/v2/books/{book_id}/source-snapshot").status_code, 409)
        self.assertEqual(self.client.get(f"/api/v2/books/{book_id}/source").text, "# Chapter\nBody text.\n")
        self.assertEqual(self.client.get(f"/api/v2/books/{book_id}/chunks").status_code, 404)
        cancelled = self.client.post(f"/api/v1/jobs/{accepted['task_id']}/cancel")
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertEqual(cancelled.json()["status"], "cancelled")
        self.assertEqual(self.client.get(f"/api/v2/books/{book_id}").json()["status"], "cancelled")
        self.assertFalse((self.app.state.settings.storage_root / "books" / book_id / "parsed_document.raw.v2.json").exists())

    def test_active_book_cannot_be_reprocessed_or_deleted(self) -> None:
        accepted = self.upload_queued()
        book_id = accepted["book"]["book_id"]
        self.assertEqual(self.client.post(
            f"/api/v2/books/{book_id}/reprocess", json={"from_stage": "index"},
        ).status_code, 409)
        self.assertEqual(self.client.delete(f"/api/v2/books/{book_id}").status_code, 409)

    def test_clean_failure_resumes_at_checkpoint(self) -> None:
        accepted = self.upload_queued()
        book_id = accepted["book"]["book_id"]
        jobs = self.app.state.job_service
        books = self.app.state.text_book_service
        original = books.run_stage

        def fail_clean(target, stage):
            if stage == "clean":
                raise RuntimeError("temporary clean failure")
            return original(target, stage)

        books.run_stage = fail_clean
        first = jobs.execute(accepted["task_id"], "text-worker-one")
        self.assertEqual(first.status, "retrying")
        self.assertEqual(first.progress_current, 1)
        raw_path = self.app.state.settings.storage_root / "books" / book_id / "parsed_document.raw.v2.json"
        self.assertTrue(raw_path.exists())
        raw_modified_at = raw_path.stat().st_mtime_ns
        books.run_stage = original
        completed = jobs.execute(accepted["task_id"], "text-worker-two")
        self.assertEqual(completed.status, "succeeded")
        self.assertEqual(raw_path.stat().st_mtime_ns, raw_modified_at)
        self.assertEqual(next(stage.status for stage in completed.stages if stage.attempt == 2 and stage.stage_name == "parse"), "skipped")
        self.assertEqual(self.client.get(f"/api/v2/books/{book_id}").json()["status"], "processed")

    def test_lost_worker_cancellation_updates_text_book(self) -> None:
        accepted = self.upload_queued()
        jobs = self.app.state.job_service
        repository = jobs.repository
        repository.claim(accepted["task_id"], "lost-text-worker")
        repository.request_cancel(accepted["task_id"])
        recovered = repository.recover_stale(utc_now() + timedelta(seconds=1))
        self.assertEqual(recovered, [accepted["task_id"]])
        _sync_cancelled_document(repository, repository.get(accepted["task_id"]))
        self.assertEqual(self.client.get(f"/api/v2/books/{accepted['book']['book_id']}").json()["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
