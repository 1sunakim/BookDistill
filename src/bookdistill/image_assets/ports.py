from typing import BinaryIO, ContextManager, Protocol
from uuid import UUID

from bookdistill.contracts.image_assets import ImageAssetView


class ImageReader(Protocol):
    def list_images(self, book_id: UUID) -> list[ImageAssetView]: ...
    def open_image(self, asset_id: UUID) -> ContextManager[BinaryIO]: ...
