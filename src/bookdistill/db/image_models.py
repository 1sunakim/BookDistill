"""Relational metadata for independently persisted image assets."""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from bookdistill.database import Base
from .models import utc_now


class ImageAssetRecord(Base):
    __tablename__ = "image_assets"

    asset_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, default="1.0")
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    book_id: Mapped[str | None] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"))
    page_index: Mapped[int | None] = mapped_column(Integer)
    occurrence_index: Mapped[int | None] = mapped_column(Integer)
    bbox: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    mime_type: Mapped[str] = mapped_column(String(32), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1000), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ready")
    extractor_version: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    __table_args__ = (
        CheckConstraint("source_type IN ('pdf', 'upload')", name="ck_image_source_type"),
        CheckConstraint("width > 0 AND height > 0 AND size_bytes > 0", name="ck_image_dimensions"),
        CheckConstraint("status = 'ready'", name="ck_image_status"),
        CheckConstraint("mime_type IN ('image/png', 'image/jpeg', 'image/webp')", name="ck_image_mime"),
        CheckConstraint(
            "(source_type = 'upload' AND book_id IS NULL AND page_index IS NULL "
            "AND occurrence_index IS NULL AND bbox IS NULL) OR "
            "(source_type = 'pdf' AND book_id IS NOT NULL AND page_index >= 0 "
            "AND page_index IS NOT NULL AND occurrence_index >= 0 "
            "AND occurrence_index IS NOT NULL AND bbox IS NOT NULL)",
            name="ck_image_source_coordinates",
        ),
        Index("ix_images_book_page", "book_id", "page_index", "occurrence_index"),
        Index("ix_images_upload_list", "source_type", "created_at", "asset_id"),
    )
