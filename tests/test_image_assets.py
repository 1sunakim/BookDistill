from __future__ import annotations

from dataclasses import replace
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from PIL import Image
import pymupdf
from sqlalchemy import event, inspect, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from bookdistill.api.app import create_app
from bookdistill.contracts.image_assets import ImageAssetView
from bookdistill.database import create_database_engine
from bookdistill.db import BookRecord, ImageAssetRecord
from bookdistill.image_assets.errors import ImageAssetError
from bookdistill.settings import Settings
from tests.support import migrate_database


def image_bytes(format: str = "PNG", size: tuple[int, int] = (8, 6)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, "red").save(output, format=format)
    return output.getvalue()


class ImageAssetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.settings = Settings(
            database_url=f"sqlite:///{(self.root / 'test.db').as_posix()}",
            storage_root=self.root / "data", api_key="image-test-key", queue_mode="none",
        )
        migrate_database(self.settings.database_url)
        self.app = create_app(self.settings)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.headers = {"X-BookDistill-API-Key": self.settings.api_key}

    def tearDown(self) -> None:
        self.client.close()
        self.app.state.engine.dispose()
        self.temporary.cleanup()

    def upload(self, payload: bytes | None = None):
        return self.client.post("/api/v1/images", headers=self.headers,
                                files={"file": ("../../misleading.jpg", payload if payload is not None else image_bytes(), "text/plain")})

    def assert_error(self, response, status: int, code: str) -> None:
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(response.json()["code"], code)
        self.assertEqual(set(response.json()), {"code", "message", "trace_id", "details"})

    def test_real_formats_content_and_restart(self) -> None:
        for format, mime in (("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")):
            payload = image_bytes(format)
            response = self.upload(payload)
            self.assertEqual(response.status_code, 201, response.text)
            view = ImageAssetView.model_validate(response.json())
            self.assertEqual(view.mime_type, mime)
            self.assertEqual(view.sha256, hashlib.sha256(payload).hexdigest())
            self.assertIsNone(view.book_id)
            self.assertNotIn("storage_key", response.json())
            path = f"/api/v1/images/{view.asset_id}"
            content = self.client.get(path + "/content", headers=self.headers)
            self.assertEqual(content.content, payload)
            self.assertEqual(content.headers["content-type"], mime)
            self.assertEqual(content.headers["content-length"], str(len(payload)))
            self.assertEqual(content.headers["etag"], f'"{view.sha256}"')
            self.assertEqual(content.headers["cache-control"], "private, no-cache")
            self.assertEqual(content.headers["x-content-type-options"], "nosniff")
            self.assertIn(str(view.asset_id), content.headers["content-disposition"])
        self.client.close()
        self.app.state.engine.dispose()
        self.app = create_app(self.settings)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.assertEqual(self.client.get(path + "/content", headers=self.headers).content, payload)
        listing = self.client.get("/api/v1/images?limit=2&offset=1", headers=self.headers).json()
        self.assertEqual((listing["total"], len(listing["items"])), (3, 2))
        with self.app.state.image_assets.sessions() as session:
            self.assertEqual(session.scalars(select(BookRecord)).all(), [])

    def test_all_new_endpoints_require_key_before_lookup(self) -> None:
        asset_id = self.upload().json()["asset_id"]
        paths = ["/api/v1/images", f"/api/v1/images/{asset_id}",
                 f"/api/v1/images/{asset_id}/content", f"/api/v1/books/{uuid4()}/images",
                 "/api/v1/images/not-a-uuid"]
        for path in paths:
            self.assert_error(self.client.get(path), 401, "AUTHENTICATION_REQUIRED")
            self.assert_error(self.client.get(path, headers={"X-BookDistill-API-Key": "wrong"}), 401, "AUTHENTICATION_REQUIRED")
        self.assert_error(self.client.post("/api/v1/images", files={"file": ("x.png", image_bytes())}), 401, "AUTHENTICATION_REQUIRED")
        response = self.client.get(f"/api/v1/images/{asset_id}/content", headers={"If-None-Match": "*"})
        self.assert_error(response, 401, "AUTHENTICATION_REQUIRED")

    def test_unconfigured_key_is_fail_closed(self) -> None:
        app = create_app(replace(self.settings, api_key=""))
        try:
            with TestClient(app) as client:
                for path in ("/api/v1/images", f"/api/v1/images/{uuid4()}/content", f"/api/v1/books/{uuid4()}/images"):
                    self.assert_error(client.get(path), 503, "CONTENT_KEY_REQUIRED")
                self.assert_error(client.post("/api/v1/images", files={"file": ("x.png", image_bytes())}), 503, "CONTENT_KEY_REQUIRED")
        finally:
            app.state.engine.dispose()

    def test_etag_and_cors_preflight(self) -> None:
        view = self.upload().json()
        path = f"/api/v1/images/{view['asset_id']}/content"
        for condition in (f'"{view["sha256"]}"', f'W/"{view["sha256"]}"', "*"):
            response = self.client.get(path, headers={**self.headers, "If-None-Match": condition})
            self.assertEqual(response.status_code, 304)
            self.assertEqual(response.content, b"")
            self.assertEqual(response.headers["cache-control"], "private, no-cache")
        preflight = self.client.options(path, headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET",
                                                     "Access-Control-Request-Headers": "X-BookDistill-API-Key,If-None-Match"})
        self.assertEqual(preflight.status_code, 200, preflight.text)

    def test_reject_corrupt_unsupported_and_animation_without_files(self) -> None:
        for payload, status, code in ((b"", 422, "INVALID_IMAGE"), (b"not an image", 422, "INVALID_IMAGE"),
                                     (image_bytes()[:35], 422, "INVALID_IMAGE"),
                                     (image_bytes("BMP"), 415, "UNSUPPORTED_IMAGE_FORMAT")):
            self.assert_error(self.upload(payload), status, code)
        output = io.BytesIO()
        frames = [Image.new("RGB", (4, 4), color) for color in ("red", "blue")]
        frames[0].save(output, format="PNG", save_all=True, append_images=frames[1:])
        self.assert_error(self.upload(output.getvalue()), 422, "INVALID_IMAGE")
        self.assertEqual(self.app.state.image_assets.list_uploads().total, 0)
        self.assertEqual(list((self.settings.storage_root / "assets").rglob("*.image")), [])
        self.assertEqual(list((self.settings.storage_root / "assets").rglob("*.png")), [])

    def test_byte_pixel_and_dimension_limits(self) -> None:
        for override in ({"image_max_upload_bytes": 5}, {"image_max_pixels": 10}, {"image_max_dimension": 4}):
            app = create_app(replace(self.settings, **override))
            try:
                with TestClient(app) as client:
                    response = client.post("/api/v1/images", headers=self.headers, files={"file": ("x.png", image_bytes())})
                    self.assert_error(response, 413, "IMAGE_TOO_LARGE")
            finally:
                app.state.engine.dispose()
        self.assertEqual(self.app.state.image_assets.list_uploads().total, 0)

    def test_failed_commit_cleans_final_and_staging_files(self) -> None:
        service = self.app.state.image_assets
        def fail_commit(session):
            raise SQLAlchemyError("injected commit failure")
        event.listen(Session, "before_commit", fail_commit)
        try:
            with self.assertRaises(ImageAssetError) as failure:
                service.upload(io.BytesIO(image_bytes()))
        finally:
            event.remove(Session, "before_commit", fail_commit)
        self.assertEqual(failure.exception.code, "IMAGE_STORAGE_ERROR")
        self.assertEqual(service.list_uploads().total, 0)
        self.assertEqual(list((self.settings.storage_root / "assets").rglob("*.png")), [])
        self.assertEqual(list((self.settings.storage_root / "assets").rglob("*.image")), [])

    def test_traversal_escape(self) -> None:
        storage = self.app.state.image_assets.storage
        for key in ("../secret", "assets/../../secret", "/secret", "assets\\secret", "assets/C:/secret", "assets//secret"):
            with self.assertRaises(ImageAssetError):
                storage.checked_path(key)

    def test_symlink_escape(self) -> None:
        storage = self.app.state.image_assets.storage
        outside = self.root / "outside"
        outside.mkdir()
        link = self.settings.storage_root / "assets" / "escape"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("This Windows account cannot create symbolic links")
        with self.assertRaises(ImageAssetError):
            storage.checked_path("assets/escape/secret.png")

    def test_missing_and_changed_content_and_corrupt_metadata(self) -> None:
        asset_id = UUID(self.upload().json()["asset_id"])
        service = self.app.state.image_assets
        asset = service.get_asset(asset_id)
        file_path = service.storage.checked_path(asset.storage_key)
        file_path.write_bytes(b"x" * asset.size_bytes)
        self.assert_error(self.client.get(f"/api/v1/images/{asset_id}/content", headers=self.headers), 500, "IMAGE_STORAGE_ERROR")
        file_path.unlink()
        self.assert_error(self.client.get(f"/api/v1/images/{asset_id}/content", headers=self.headers), 404, "IMAGE_CONTENT_NOT_FOUND")
        with service.sessions.begin() as session:
            session.get(ImageAssetRecord, str(asset_id)).storage_key = "assets/../../private-file"
        response = self.client.get(f"/api/v1/images/{asset_id}/content", headers=self.headers)
        self.assert_error(response, 500, "IMAGE_STORAGE_ERROR")
        self.assertNotIn("private-file", response.text)

    def test_missing_ids_and_invalid_queries(self) -> None:
        self.assert_error(self.client.get(f"/api/v1/images/{uuid4()}", headers=self.headers), 404, "IMAGE_NOT_FOUND")
        self.assert_error(self.client.get(f"/api/v1/books/{uuid4()}/images", headers=self.headers), 404, "BOOK_NOT_FOUND")
        for path in ("/api/v1/images?limit=201", "/api/v1/images?offset=-1", "/api/v1/images/not-a-uuid"):
            self.assert_error(self.client.get(path, headers=self.headers), 422, "VALIDATION_ERROR")

    def test_existing_book_without_extraction_has_empty_image_list(self) -> None:
        with pymupdf.open() as document:
            document.new_page()
            payload = document.tobytes()
        accepted = self.client.post("/api/v1/books", headers=self.headers,
                                    files={"file": ("book.pdf", payload, "application/pdf")})
        self.assertEqual(accepted.status_code, 202, accepted.text)
        response = self.client.get(f"/api/v1/books/{accepted.json()['book_id']}/images", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), [])

    def test_invalid_multipart_is_authenticated_before_parsing(self) -> None:
        response = self.client.post("/api/v1/images", content=b"not multipart", headers={"Content-Type": "multipart/form-data"})
        self.assert_error(response, 401, "AUTHENTICATION_REQUIRED")
        response = self.client.post("/api/v1/images", headers=self.headers)
        self.assert_error(response, 422, "VALIDATION_ERROR")

    def test_limits_are_validated_at_startup(self) -> None:
        for value in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                replace(self.settings, image_max_pixels=value)

    def test_migration_downgrade_and_upgrade(self) -> None:
        self.client.close()
        self.app.state.engine.dispose()
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", self.settings.database_url)
        command.downgrade(config, "20260921_0001")
        engine = create_database_engine(self.settings.database_url)
        try:
            self.assertNotIn("image_assets", inspect(engine).get_table_names())
            self.assertIn("books", inspect(engine).get_table_names())
        finally:
            engine.dispose()
        command.upgrade(config, "head")
        engine = create_database_engine(self.settings.database_url)
        try:
            self.assertIn("image_assets", inspect(engine).get_table_names())
        finally:
            engine.dispose()
