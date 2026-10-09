from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import UUID

from PIL import Image
import pymupdf
from pydantic import ValidationError
from sqlalchemy import event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from fastapi.testclient import TestClient
from bookdistill.api.app import create_app
from bookdistill.bootstrap import build_runtime
from bookdistill.contracts import DocumentProcessingSnapshot, ImageResource, ParsedDocument, PdfTocResource
from bookdistill.db import ImageAssetRecord
from bookdistill.image_assets import ImageAssetService
from bookdistill.image_assets.errors import ImageAssetError
from bookdistill.image_assets.pdf_extractor import PdfImageExtractionError
from bookdistill.settings import Settings
from tests.support import migrate_database


def png(color="red", size=(8, 6), transparent=False) -> bytes:
    output = io.BytesIO()
    image = Image.new("RGBA" if transparent else "RGB", size, (255, 0, 0, 128) if transparent else color)
    image.save(output, format="PNG")
    image.close()
    return output.getvalue()


def pdf_with_images(*, text=True, repeated=True, color="red", transparent=False, large_second=False) -> bytes:
    with pymupdf.open() as document:
        for page_index in range(2 if repeated or large_second else 1):
            page = document.new_page()
            if text:
                page.insert_text((72, 72), f"{page_index + 1} Picture chapter", fontsize=20)
                page.insert_text((72, 112), f"This page contains searchable text and evidence number {page_index}.")
            payload = png(color, (50, 50) if large_second and page_index else (8, 6), transparent)
            page.insert_image(pymupdf.Rect(40, 150, 140, 250), stream=payload)
            if repeated and page_index == 0:
                page.insert_image(pymupdf.Rect(200, 150, 300, 250), stream=payload)
        if text:
            document.set_toc([[1, "1 Picture chapter", 1]])
        return document.tobytes()


def pdf_with_inline_image() -> bytes:
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), "Inline image with searchable text.")
        existing = b"\n".join(document.xref_stream(xref) for xref in page.get_contents())
        inline = b"\nq 100 0 0 100 30 300 cm\nBI /W 2 /H 2 /CS /RGB /BPC 8 /F /AHx ID\nFF000000FF000000FFFFFFFF>\nEI\nQ\n"
        xref = document.get_new_xref()
        document.update_object(xref, "<<>>")
        document.update_stream(xref, existing + inline)
        page.set_contents(xref)
        return document.tobytes()


class PdfImageAssetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.settings = Settings(database_url=f"sqlite:///{(self.root / 'pdf.db').as_posix()}",
                                 storage_root=self.root / "data", api_key="pdf-image-test-key")
        migrate_database(self.settings.database_url)
        self.app = create_app(self.settings)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.headers = {"X-BookDistill-API-Key": self.settings.api_key}
        self.images = self.app.state.image_assets

    def tearDown(self):
        self.client.close()
        self.app.state.engine.dispose()
        self.temporary.cleanup()

    def upload_book(self, payload=None) -> dict:
        response = self.client.post("/api/v1/books", headers=self.headers,
                                    files={"file": ("pictures.pdf", payload if payload is not None else pdf_with_images(), "application/pdf")})
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def list_images(self, book_id) -> list:
        response = self.client.get(f"/api/v1/books/{book_id}/images", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def content(self, asset_id):
        return self.client.get(f"/api/v1/images/{asset_id}/content", headers=self.headers)

    def source(self, book_id):
        return self.settings.storage_root / "books" / str(book_id) / "original.pdf"

    def test_multiple_occurrences_toc_and_stable_reprocess(self):
        accepted = self.upload_book()
        book_id = accepted["book_id"]
        first = self.list_images(book_id)
        self.assertEqual([image["page_index"] for image in first], [0, 0, 1])
        self.assertEqual(len({image["asset_id"] for image in first}), 3)
        self.assertEqual(len({image["sha256"] for image in first}), 1)
        document = self.app.state.ingestion_service.get_parsed_document(UUID(book_id))
        self.assertEqual(document.schema_version, "1.1")
        self.assertIsInstance(document.resources[0], PdfTocResource)
        self.assertEqual([str(resource.asset_id) for resource in document.resources if isinstance(resource, ImageResource)],
                         [image["asset_id"] for image in first])
        self.assertEqual(document.resources[0].entries[0].title, "1 Picture chapter")
        response = self.client.post(f"/api/v1/books/{book_id}/reprocess", headers=self.headers, json={})
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["status"], "succeeded", response.text)
        self.assertEqual(self.list_images(book_id), first)
        for image in first:
            self.assertEqual(self.content(image["asset_id"]).status_code, 200)
        self.assertEqual(list(self.images.storage.staging_root.glob("*.image")), [])

    def test_scanned_text_failure_keeps_images(self):
        accepted = self.upload_book(pdf_with_images(text=False, repeated=False))
        job = self.client.get(f"/api/v1/jobs/{accepted['task_id']}", headers=self.headers).json()
        self.assertEqual(job["status"], "failed", job)
        self.assertEqual(job["error"]["code"], "SCANNED_PAGE_UNSUPPORTED")
        self.assertEqual(self.app.state.ingestion_service.get_book(UUID(accepted["book_id"])).status, "failed")
        images = self.list_images(accepted["book_id"])
        self.assertEqual(len(images), 1)
        self.assertEqual(self.content(images[0]["asset_id"]).status_code, 200)
        self.assertIsNone(self.app.state.ingestion_service.get_parsed_document(UUID(accepted["book_id"])))

    def test_transparent_mask_is_saved_and_inline_images_are_not_omitted(self):
        accepted = self.upload_book(pdf_with_images(transparent=True, repeated=False))
        asset = self.list_images(accepted["book_id"])[0]
        with Image.open(io.BytesIO(self.content(asset["asset_id"]).content)) as image:
            self.assertEqual(image.mode, "RGBA")
            self.assertEqual(image.getpixel((0, 0))[3], 128)
        accepted = self.upload_book(pdf_with_inline_image())
        assets = self.list_images(accepted["book_id"])
        self.assertEqual(len(assets), 1)
        self.assertEqual((assets[0]["width"], assets[0]["height"]), (2, 2))
        with Image.open(io.BytesIO(self.content(assets[0]["asset_id"]).content)) as image:
            self.assertEqual(image.getpixel((0, 0))[:3], (255, 0, 0))

    def test_delete_book_preserves_independent_asset_with_identical_hash(self):
        book = self.upload_book()
        assets = self.list_images(book["book_id"])
        uploaded = self.client.post("/api/v1/images", headers=self.headers,
                                    files={"file": ("copy.png", self.content(assets[0]["asset_id"]).content)})
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        self.assertEqual(uploaded.json()["sha256"], assets[0]["sha256"])
        response = self.client.delete(f"/api/v1/books/{book['book_id']}", headers=self.headers)
        self.assertEqual(response.status_code, 204, response.text)
        for asset in assets:
            self.assertEqual(self.content(asset["asset_id"]).status_code, 404)
        self.assertEqual(self.content(uploaded.json()["asset_id"]).status_code, 200)
        self.assertEqual(list((self.images.storage.assets_root / "books" / book["book_id"]).glob("*.png")), [])
        with self.images.sessions() as session:
            self.assertEqual(len(session.scalars(select(ImageAssetRecord)).all()), 1)

    def test_failed_replacement_rolls_back_new_files_and_preserves_old_manifest(self):
        book_id = UUID(self.upload_book()["book_id"])
        original = self.images.list_images(book_id)
        replacement = self.root / "replacement.pdf"
        replacement.write_bytes(pdf_with_images(color="blue"))
        def fail_asset_commit(session):
            if any(isinstance(record, ImageAssetRecord) for record in session.new):
                raise SQLAlchemyError("injected image commit failure")
        event.listen(Session, "before_commit", fail_asset_commit)
        try:
            with self.assertRaises(ImageAssetError):
                self.images.replace_pdf_images(replacement, book_id)
        finally:
            event.remove(Session, "before_commit", fail_asset_commit)
        self.assertEqual(self.images.list_images(book_id), original)
        files = list((self.images.storage.assets_root / "books" / str(book_id)).glob("*.png"))
        self.assertEqual(len(files), len(original))
        for asset in original:
            self.assertEqual(self.content(asset.asset_id).status_code, 200)
        self.assertEqual(list(self.images.storage.staging_root.glob("*.image")), [])

    def test_extraction_limit_failure_cleans_staging_and_retains_existing_images(self):
        book_id = UUID(self.upload_book()["book_id"])
        original = self.images.list_images(book_id)
        replacement = self.root / "large.pdf"
        replacement.write_bytes(pdf_with_images(large_second=True))
        self.images.settings = replace(self.settings, image_max_pixels=100)
        with self.assertRaises(PdfImageExtractionError) as failure:
            self.images.replace_pdf_images(replacement, book_id)
        self.assertEqual(failure.exception.code, "PDF_IMAGE_LIMIT_EXCEEDED")
        self.assertEqual(failure.exception.page_index, 1)
        self.assertEqual(self.images.list_images(book_id), original)
        self.assertEqual(list(self.images.storage.staging_root.glob("*.image")), [])

    def test_pipeline_image_limit_is_not_reported_as_text_failure(self):
        self.images.settings = replace(self.settings, image_max_pixels=10)
        accepted = self.upload_book()
        job = self.client.get(f"/api/v1/jobs/{accepted['task_id']}", headers=self.headers).json()
        self.assertEqual(job["status"], "failed", job)
        self.assertEqual(job["error"]["code"], "PDF_IMAGE_LIMIT_EXCEEDED")
        self.assertEqual(job["error"]["details"]["component"], "image_extraction")
        self.assertEqual(self.list_images(accepted["book_id"]), [])

    def test_cleanup_failure_retries_on_next_extraction(self):
        book_id = UUID(self.upload_book()["book_id"])
        original = self.images.list_images(book_id)
        replacement = self.root / "replacement.pdf"
        replacement.write_bytes(pdf_with_images(color="blue", repeated=False))
        with patch.object(self.images, "cleanup_book_files", side_effect=OSError("cleanup unavailable")):
            new = self.images.replace_pdf_images(replacement, book_id)
        self.assertEqual(self.images.list_images(book_id), new)
        directory = self.images.storage.assets_root / "books" / str(book_id)
        self.assertEqual(len(list(directory.glob("*.png"))), len(original) + len(new))
        self.images.replace_pdf_images(replacement, book_id)
        self.assertEqual(len(list(directory.glob("*.png"))), len(new))

    def test_delete_retry_finishes_cleanup_after_metadata_commits(self):
        book_id = self.upload_book()["book_id"]
        directory = self.images.storage.assets_root / "books" / book_id
        with patch.object(self.images, "cleanup_book_files", side_effect=OSError("cleanup unavailable")):
            response = self.client.delete(f"/api/v1/books/{book_id}", headers=self.headers)
        self.assertEqual(response.status_code, 500)
        self.assertTrue(list(directory.glob("*.png")))
        self.assertIsNone(self.app.state.ingestion_service.get_book(UUID(book_id)))
        response = self.client.delete(f"/api/v1/books/{book_id}", headers=self.headers)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(list(directory.glob("*.png")), [])

    def test_book_lock_is_enforced_across_processes(self):
        book_id = UUID(self.upload_book()["book_id"])
        lock = self.images.book_lock(book_id)
        code = "from filelock import FileLock,Timeout\nimport sys\ntry:\n with FileLock(sys.argv[1],timeout=0): pass\nexcept Timeout: sys.exit(23)\n"
        arguments = [sys.executable, "-c", code, str(lock.lock_file)]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        with lock:
            result = subprocess.run(arguments, capture_output=True, timeout=10, creationflags=flags)
            self.assertEqual(result.returncode, 23, result.stderr.decode(errors="replace"))
        result = subprocess.run(arguments, capture_output=True, timeout=10, creationflags=flags)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))

    def test_worker_and_legacy_processing_share_image_persistence(self):
        runtime = build_runtime(replace(self.settings, queue_mode="none"))
        try:
            documents = runtime.documents
            self.assertIs(documents.images, runtime.images)
            accepted = documents.ingest(io.BytesIO(pdf_with_images(text=False, repeated=False)),
                                        original_file_name="scanned.pdf", declared_mime_type="application/pdf",
                                        title=None, authors=[], language="en")
            documents.process_job(accepted.job_id)
            self.assertEqual(documents.get_book(accepted.book_id).status, "failed")
            assets = runtime.images.list_images(accepted.book_id)
            self.assertEqual(len(assets), 1)
            with runtime.images.open_image(assets[0].asset_id) as stream:
                self.assertTrue(stream.read())
        finally:
            runtime.engine.dispose()


class DocumentResourceCompatibilityTests(unittest.TestCase):
    def setUp(self):
        fixture = Path(__file__).resolve().parents[1] / "examples/document_processing_snapshot.v1.json"
        self.payload = json.loads(fixture.read_text(encoding="utf-8"))

    def test_v1_snapshot_without_resources_and_with_toc_remains_valid(self):
        old = deepcopy(self.payload)
        old["document"].pop("resources", None)
        self.assertEqual(DocumentProcessingSnapshot.model_validate(old).document.schema_version, "1.0")
        old["document"]["resources"] = [{"type": "pdf_toc", "entries": [{"level": 1, "title": "Introduction", "page_index": 0}]}]
        self.assertIsInstance(DocumentProcessingSnapshot.model_validate(old).document.resources[0], PdfTocResource)

    def test_v11_image_resources_validate_page_identity_and_unknown_types(self):
        new = deepcopy(self.payload)
        new["document"]["schema_version"] = "1.1"
        resource = {"type": "image", "asset_id": "11111111-1111-5111-8111-111111111111",
                    "page_index": 0, "bbox": {"x0": -10, "y0": 0, "x1": 100, "y1": 100}}
        new["document"]["resources"] = [resource]
        snapshot = DocumentProcessingSnapshot.model_validate(new)
        self.assertEqual(snapshot.schema_version, "1.0")
        self.assertIsInstance(snapshot.document.resources[0], ImageResource)
        for resources in ([{**resource, "page_index": 999}], [resource, resource], [{"type": "unknown"}]):
            broken = deepcopy(new)
            broken["document"]["resources"] = resources
            with self.assertRaises(ValidationError):
                DocumentProcessingSnapshot.model_validate(broken)
        self.assertEqual(ParsedDocument.model_validate(snapshot.document.model_dump()).schema_version, "1.1")
