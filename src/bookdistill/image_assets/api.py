"""Authenticated image upload, metadata and binary responses."""

import hashlib
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile

from bookdistill.api.content_auth import require_content_key
from bookdistill.contracts.image_assets import ImageAssetList, ImageAssetView
from .errors import ImageAssetError
from .service import ImageAssetService
from .validation import EXTENSIONS


def create_image_router(service: ImageAssetService) -> APIRouter:
    router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_content_key)], tags=["images"])

    @router.post("/images", status_code=201, response_model=ImageAssetView)
    def upload_image(file: Annotated[UploadFile, File()]) -> ImageAssetView:
        try:
            return service.upload(file.file)
        finally:
            file.file.close()

    @router.get("/images", response_model=ImageAssetList)
    def list_uploads(limit: Annotated[int, Query(ge=1, le=200)] = 50,
                     offset: Annotated[int, Query(ge=0)] = 0) -> ImageAssetList:
        return service.list_uploads(limit, offset)

    @router.get("/books/{book_id}/images", response_model=list[ImageAssetView])
    def list_book_images(book_id: UUID) -> list[ImageAssetView]:
        return service.list_images(book_id)

    @router.get("/images/{asset_id}", response_model=ImageAssetView)
    def get_image(asset_id: UUID) -> ImageAssetView:
        return service.get_image(asset_id)

    @router.get("/images/{asset_id}/content")
    def get_content(asset_id: UUID, request: Request) -> Response:
        image = service.get_image(asset_id)
        etag = f'"{image.sha256}"'
        headers = {
            "ETag": etag, "Cache-Control": "private, no-cache",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'inline; filename="{asset_id}.{EXTENSIONS[image.mime_type]}"',
        }
        with service.open_image(asset_id) as stream:
            content = stream.read()
        if len(content) != image.size_bytes or hashlib.sha256(content).hexdigest() != image.sha256:
            raise ImageAssetError("IMAGE_STORAGE_ERROR", "Image content failed integrity validation.")
        supplied = request.headers.get("If-None-Match", "")
        tags = [tag.strip().removeprefix("W/") for tag in supplied.split(",")]
        if "*" in tags or etag in tags:
            return Response(status_code=304, headers=headers)
        return Response(content, media_type=image.mime_type, headers=headers)

    return router
