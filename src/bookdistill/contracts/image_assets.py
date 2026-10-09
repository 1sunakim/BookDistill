"""Versioned image metadata. Public views never expose storage paths."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, field_validator, model_validator

from .document_processing import StrictModel


class ImageBoundingBox(StrictModel):
    x0: float = Field(allow_inf_nan=False)
    y0: float = Field(allow_inf_nan=False)
    x1: float = Field(allow_inf_nan=False)
    y1: float = Field(allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_area(self) -> "ImageBoundingBox":
        if self.x1 <= self.x0 or self.y1 <= self.y0:
            raise ValueError("image bounding box must have positive area")
        return self


class ImageAssetView(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    asset_id: UUID
    source_type: Literal["pdf", "upload"]
    book_id: UUID | None = None
    page_index: int | None = Field(default=None, ge=0)
    bbox: ImageBoundingBox | None = None
    mime_type: Literal["image/png", "image/jpeg", "image/webp"]
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: Literal["ready"] = "ready"
    extractor_version: str = Field(min_length=1)
    created_at: AwareDatetime

    @model_validator(mode="after")
    def validate_source(self) -> "ImageAssetView":
        coordinates = (self.book_id, self.page_index, self.bbox)
        if self.source_type == "pdf" and any(value is None for value in coordinates):
            raise ValueError("PDF images require a book, page and bounding box")
        if self.source_type == "upload" and any(value is not None for value in coordinates):
            raise ValueError("uploaded images cannot belong to a book or page")
        return self


class ImageAsset(ImageAssetView):
    storage_key: str = Field(min_length=1)

    @field_validator("storage_key")
    @classmethod
    def validate_storage_key(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            path.is_absolute() or "\\" in value or ":" in value or "\x00" in value
            or any(part in {"", ".", ".."} for part in value.split("/"))
            or not value.startswith("assets/")
        ):
            raise ValueError("storage_key must be a controlled relative asset path")
        return value

    def public_view(self) -> ImageAssetView:
        return ImageAssetView.model_validate(self.model_dump(exclude={"storage_key"}))


class ImageAssetList(StrictModel):
    items: list[ImageAssetView]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=200)
    offset: int = Field(ge=0)
