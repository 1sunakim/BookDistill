"""Persist assets independently, with cleanup on failed metadata commits."""

from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
import logging
from pathlib import Path
from typing import BinaryIO, Iterator
from uuid import UUID, uuid4
from pydantic import ValidationError
from filelock import FileLock

from sqlalchemy import delete, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from bookdistill.contracts.image_assets import ImageAsset, ImageAssetList, ImageAssetView
from bookdistill.db import BookRecord
from bookdistill.db.image_models import ImageAssetRecord
from bookdistill.settings import Settings
from .errors import ImageAssetError
from .storage import ImageStorage
from .validation import EXTENSIONS, validate_image

logger = logging.getLogger(__name__)


def _asset_from_record(record: ImageAssetRecord) -> ImageAsset:
    created_at = record.created_at
    if created_at.tzinfo is None:  # SQLite drops timezone information.
        created_at = created_at.replace(tzinfo=UTC)
    return ImageAsset(
        schema_version=record.schema_version, asset_id=record.asset_id,
        source_type=record.source_type, book_id=record.book_id,
        page_index=record.page_index, bbox=record.bbox, mime_type=record.mime_type,
        width=record.width, height=record.height, size_bytes=record.size_bytes,
        sha256=record.sha256, storage_key=record.storage_key, status=record.status,
        extractor_version=record.extractor_version, created_at=created_at,
    )


def asset_from_record(record: ImageAssetRecord) -> ImageAsset:
    try:
        return _asset_from_record(record)
    except (ValidationError, ValueError) as error:
        raise ImageAssetError("IMAGE_STORAGE_ERROR", "Stored image metadata is invalid.") from error


class ImageAssetService:
    def __init__(self, settings: Settings, sessions: sessionmaker[Session]) -> None:
        self.settings = settings
        self.sessions = sessions
        self.storage = ImageStorage(settings.storage_root, settings.image_max_upload_bytes)
        self._book_locks: dict[UUID, FileLock] = {}

    def book_lock(self, book_id: UUID) -> FileLock:
        lock_path = self.storage.checked_path(f"assets/.locks/{book_id}.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        return self._book_locks.setdefault(book_id, FileLock(lock_path))

    def cleanup_book_files(self, book_id: UUID, keep_keys: set[str]) -> None:
        """Also sweeps files orphaned by an earlier crash or failed cleanup."""
        key = f"assets/books/{book_id}"
        directory = self.storage.checked_path(key)
        if directory != self.storage.assets_root / "books" / str(book_id):
            raise ImageAssetError("IMAGE_STORAGE_ERROR", "Book asset directory failed boundary validation.")
        if not directory.exists():
            return
        for path in directory.iterdir():
            storage_key = f"{key}/{path.name}"
            if storage_key in keep_keys:
                continue
            if path.is_symlink() or path.is_dir():
                raise ImageAssetError("IMAGE_STORAGE_ERROR", "Book asset cleanup encountered an unsafe entry.")
            try:
                UUID(path.stem)
            except ValueError:
                continue
            if path.suffix in {".png", ".jpg", ".webp"}:
                self.storage.remove(storage_key)

    def replace_pdf_images(self, source_path: Path, book_id: UUID) -> list[ImageAssetView]:
        from .pdf_extractor import PdfImageExtractor

        with self.book_lock(book_id):
            prepared = []
            created_keys: list[str] = []
            committed = False
            try:
                with self.sessions() as session:
                    if session.get(BookRecord, str(book_id)) is None:
                        raise ImageAssetError("BOOK_NOT_FOUND", "Book was not found.", 404)
                    previous = {record.asset_id: asset_from_record(record) for record in session.scalars(
                        select(ImageAssetRecord).where(ImageAssetRecord.book_id == str(book_id))
                    )}
                prepared = PdfImageExtractor(self.settings, self.storage).extract(source_path, book_id)
                records = []
                views = []
                for item in prepared:
                    asset = item.asset
                    if str(asset.asset_id) in previous:
                        asset.created_at = previous[str(asset.asset_id)].created_at
                    target = self.storage.checked_path(asset.storage_key)
                    if target.exists():
                        with self.storage.open(asset.storage_key) as stream:
                            digest = hashlib.file_digest(stream, "sha256").hexdigest()
                        if digest != asset.sha256:
                            raise ImageAssetError("IMAGE_STORAGE_ERROR", "Existing image content is inconsistent.")
                    else:
                        self.storage.finalize(item.staged, asset.storage_key)
                        created_keys.append(asset.storage_key)
                    values = asset.model_dump(mode="json")
                    values["created_at"] = asset.created_at
                    values["occurrence_index"] = item.occurrence_index
                    records.append(ImageAssetRecord(**values))
                    views.append(asset.public_view())
                with self.sessions.begin() as session:
                    if session.scalar(select(BookRecord).where(BookRecord.id == str(book_id)).with_for_update()) is None:
                        raise ImageAssetError("BOOK_NOT_FOUND", "Book was not found.", 404)
                    session.execute(delete(ImageAssetRecord).where(ImageAssetRecord.book_id == str(book_id)))
                    session.add_all(records)
                committed = True
                try:
                    self.cleanup_book_files(book_id, {record.storage_key for record in records})
                except (ImageAssetError, OSError):
                    # Metadata has committed; a subsequent extraction retries the sweep.
                    logger.warning("PDF asset cleanup deferred for book %s", book_id)
                return views
            except (OSError, SQLAlchemyError) as error:
                raise ImageAssetError("IMAGE_STORAGE_ERROR", "PDF image assets could not be persisted.") from error
            finally:
                for item in prepared:
                    item.staged.path.unlink(missing_ok=True)
                if not committed:
                    for key in created_keys:
                        self.storage.remove(key)

    def upload(self, source: BinaryIO) -> ImageAssetView:
        staged = None
        finalized_key = None
        try:
            staged = self.storage.stage(source)
            image = validate_image(staged.path, self.settings.image_max_pixels, self.settings.image_max_dimension)
            asset_id = uuid4()
            asset = ImageAsset(
                asset_id=asset_id, source_type="upload", mime_type=image.mime_type,
                width=image.width, height=image.height, size_bytes=staged.size_bytes,
                sha256=staged.sha256,
                storage_key=f"assets/uploads/{asset_id}.{EXTENSIONS[image.mime_type]}",
                extractor_version="upload-v1", created_at=datetime.now(UTC),
            )
            self.storage.finalize(staged, asset.storage_key)
            finalized_key = asset.storage_key
            with self.sessions.begin() as session:
                values = asset.model_dump(mode="json")
                values["asset_id"] = str(asset.asset_id)
                values["created_at"] = asset.created_at
                session.add(ImageAssetRecord(**values))
            # Only clear rollback cleanup after the transaction has committed.
            finalized_key = None
            return asset.public_view()
        except (OSError, SQLAlchemyError) as error:
            raise ImageAssetError("IMAGE_STORAGE_ERROR", "Image could not be persisted.") from error
        finally:
            if staged is not None:
                staged.path.unlink(missing_ok=True)
            if finalized_key is not None:
                self.storage.remove(finalized_key)

    def get_asset(self, asset_id: UUID) -> ImageAsset:
        with self.sessions() as session:
            record = session.get(ImageAssetRecord, str(asset_id))
            if record is None:
                raise ImageAssetError("IMAGE_NOT_FOUND", "Image asset was not found.", 404)
            return asset_from_record(record)

    def get_image(self, asset_id: UUID) -> ImageAssetView:
        return self.get_asset(asset_id).public_view()

    def list_uploads(self, limit: int = 50, offset: int = 0) -> ImageAssetList:
        with self.sessions() as session:
            query = select(ImageAssetRecord).where(ImageAssetRecord.source_type == "upload")
            total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
            records = session.scalars(query.order_by(ImageAssetRecord.created_at, ImageAssetRecord.asset_id)
                                      .limit(limit).offset(offset)).all()
            return ImageAssetList(items=[asset_from_record(record).public_view() for record in records],
                                  total=total, limit=limit, offset=offset)

    def list_images(self, book_id: UUID) -> list[ImageAssetView]:
        with self.sessions() as session:
            if session.get(BookRecord, str(book_id)) is None:
                raise ImageAssetError("BOOK_NOT_FOUND", "Book was not found.", 404)
            records = session.scalars(select(ImageAssetRecord).where(ImageAssetRecord.book_id == str(book_id))
                                      .order_by(ImageAssetRecord.page_index, ImageAssetRecord.occurrence_index,
                                                ImageAssetRecord.asset_id)).all()
            return [asset_from_record(record).public_view() for record in records]

    @contextmanager
    def open_image(self, asset_id: UUID) -> Iterator[BinaryIO]:
        asset = self.get_asset(asset_id)
        with self.storage.open(asset.storage_key) as stream:
            yield stream
