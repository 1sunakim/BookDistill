"""Persist assets independently, with cleanup on failed metadata commits."""

from contextlib import contextmanager
from datetime import UTC, datetime
from typing import BinaryIO, Iterator
from uuid import UUID, uuid4
from pydantic import ValidationError

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from bookdistill.contracts.image_assets import ImageAsset, ImageAssetList, ImageAssetView
from bookdistill.db import BookRecord
from bookdistill.db.image_models import ImageAssetRecord
from bookdistill.settings import Settings
from .errors import ImageAssetError
from .storage import ImageStorage
from .validation import EXTENSIONS, validate_image


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
