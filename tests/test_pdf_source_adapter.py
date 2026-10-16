from __future__ import annotations

import io
from pathlib import Path
import sys
import tempfile
import unittest
from uuid import UUID

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bookdistill.api.app import create_app
from bookdistill.document_processing.pdf_source_adapter import adapt_pdf_snapshot
from bookdistill.settings import Settings
from tests.support import migrate_database
from tests.test_ingestion_api import make_text_pdf


class PdfSourceAdapterTests(unittest.TestCase):
    def test_persisted_pdf_projects_to_real_page_locations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = Settings(
                database_url=f"sqlite:///{(root / 'books.db').as_posix()}",
                storage_root=root / "storage", minimum_text_characters_per_page=10,
                api_key="pdf-snapshot-test-key",
            )
            migrate_database(settings.database_url)
            app = create_app(settings)
            with TestClient(app) as client:
                client.headers.update({"X-BookDistill-API-Key": "pdf-snapshot-test-key"})
                accepted = client.post(
                    "/api/v1/books",
                    files={"file": ("sample.pdf", io.BytesIO(make_text_pdf()), "application/pdf")},
                )
                self.assertEqual(accepted.status_code, 202, accepted.text)
                book_id = UUID(accepted.json()["book_id"])
                service = app.state.ingestion_service
                book = service.get_book(book_id)
                document = service.get_parsed_document(book_id)
                chapters = service.get_chapters(book_id)
                chunks = service.get_chunks(book_id)
                self.assertIsNotNone(book)
                self.assertIsNotNone(document)
                self.assertTrue(chapters)
                self.assertTrue(chunks)
                projected, new_chapters, new_chunks = adapt_pdf_snapshot(
                    document, chapters, chunks, source_sha256=book.sha256,
                )
                self.assertEqual(projected.format, "pdf")
                self.assertEqual(len(projected.units), book.page_count)
                self.assertEqual(len(new_chapters), len(chapters))
                self.assertEqual(len(new_chunks), len(chunks))
                self.assertTrue(all(chunk.source_start.kind == "pdf_page" for chunk in new_chunks))
                self.assertEqual(new_chunks[0].source_start.page_index, chunks[0].page_start)
                snapshot = client.get(f"/api/v2/books/{book_id}/source-snapshot")
                self.assertEqual(snapshot.status_code, 200, snapshot.text)
                self.assertEqual(snapshot.json()["format"], "pdf")
                self.assertEqual(snapshot.json()["document"]["units"][0]["unit_index"], 0)
                self.assertEqual(snapshot.json()["chunks"][0]["source_start"]["kind"], "pdf_page")
                self.assertEqual(client.get(
                    f"/api/v2/books/{book_id}/source-snapshot",
                    headers={"X-BookDistill-API-Key": "wrong"},
                ).status_code, 401)
            app.state.engine.dispose()
