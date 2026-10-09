"""Extract every PDF bitmap occurrence before text parsing can fail."""

from dataclasses import dataclass
from datetime import UTC, datetime
import io
import json
from pathlib import Path
from uuid import UUID, uuid5

from PIL import Image
import pymupdf

from bookdistill.contracts.image_assets import ImageAsset, ImageBoundingBox
from bookdistill.settings import Settings
from .errors import ImageAssetError
from .storage import ImageStorage, StagedImage
from .validation import EXTENSIONS, MIME_TYPES, validate_image

PDF_IMAGE_NAMESPACE = UUID("7d7e0609-974f-4ae2-a6de-33630557606d")


@dataclass(frozen=True, slots=True)
class PreparedPdfImage:
    asset: ImageAsset
    occurrence_index: int
    staged: StagedImage


class PdfImageExtractionError(ImageAssetError):
    def __init__(self, page_index: int | None, *, limit: bool = False) -> None:
        super().__init__(
            "PDF_IMAGE_LIMIT_EXCEEDED" if limit else "PDF_IMAGE_EXTRACTION_FAILED",
            "A PDF image exceeds decoded limits." if limit else "PDF images could not be extracted.",
            413 if limit else 422,
        )
        self.page_index = page_index


def stable_image_id(book_id: UUID, page_index: int, occurrence_index: int,
                    bbox: ImageBoundingBox, sha256: str) -> UUID:
    coordinates = []
    for number in (bbox.x0, bbox.y0, bbox.x1, bbox.y1):
        formatted = f"{number:.3f}"
        coordinates.append("0.000" if formatted == "-0.000" else formatted)
    name = json.dumps([str(book_id), page_index, occurrence_index, coordinates, sha256],
                      ensure_ascii=False, separators=(",", ":"))
    return uuid5(PDF_IMAGE_NAMESPACE, name)


class PdfImageExtractor:
    def __init__(self, settings: Settings, storage: ImageStorage) -> None:
        self.settings = settings
        self.storage = storage

    def check_dimensions(self, width: int, height: int, page_index: int) -> None:
        if width <= 0 or height <= 0:
            raise PdfImageExtractionError(page_index)
        if width * height > self.settings.image_max_pixels or max(width, height) > self.settings.image_max_dimension:
            raise PdfImageExtractionError(page_index, limit=True)

    def normalize(self, block: dict, page_index: int) -> bytes:
        try:
            return self._normalize(block, page_index)
        except PdfImageExtractionError:
            raise
        except Image.DecompressionBombError as error:
            raise PdfImageExtractionError(page_index, limit=True) from error
        except (OSError, ValueError, SyntaxError, EOFError) as error:
            raise PdfImageExtractionError(page_index) from error

    def _normalize(self, block: dict, page_index: int) -> bytes:
        content = block["image"]
        mask_bytes = block.get("mask")
        with Image.open(io.BytesIO(content)) as image:
            self.check_dimensions(*image.size, page_index)
            if image.size != (block["width"], block["height"]):
                raise PdfImageExtractionError(page_index)
            if image.format in MIME_TYPES and not mask_bytes:
                return content
            # Unsupported embedded formats and soft masks become a single PNG.
            if mask_bytes:
                with Image.open(io.BytesIO(mask_bytes)) as mask:
                    self.check_dimensions(*mask.size, page_index)
                    if mask.size != image.size:
                        raise PdfImageExtractionError(page_index)
                    converted = image.convert("RGBA")
                    converted.putalpha(mask.convert("L"))
            else:
                converted = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            with converted:
                output = io.BytesIO()
                converted.save(output, format="PNG")
                return output.getvalue()

    def extract(self, source_path: Path, book_id: UUID) -> list[PreparedPdfImage]:
        prepared: list[PreparedPdfImage] = []
        staged_paths: list[Path] = []
        page_index = None
        try:
            with pymupdf.open(source_path) as source:
                for page_index, page in enumerate(source):
                    # Inspect metadata before asking MuPDF for decoded image bytes.
                    infos = page.get_image_info()
                    for info in infos:
                        self.check_dimensions(info["width"], info["height"], page_index)
                    blocks = [block for block in page.get_text(
                        "dict", flags=pymupdf.TEXT_PRESERVE_IMAGES, clip=pymupdf.INFINITE_RECT(), sort=False,
                    )["blocks"] if block["type"] == 1]
                    if len(blocks) != len(infos):
                        raise PdfImageExtractionError(page_index)
                    for occurrence_index, block in enumerate(blocks):
                        self.check_dimensions(block["width"], block["height"], page_index)
                        bbox = ImageBoundingBox(**dict(zip(("x0", "y0", "x1", "y1"), block["bbox"])))
                        content = self.normalize(block, page_index)
                        # PDF assets use decoded bounds, not the standalone upload byte cap.
                        staged = self.storage.stage(io.BytesIO(content), maximum_bytes=len(content))
                        staged_paths.append(staged.path)
                        image = validate_image(staged.path, self.settings.image_max_pixels, self.settings.image_max_dimension)
                        asset_id = stable_image_id(book_id, page_index, occurrence_index, bbox, staged.sha256)
                        asset = ImageAsset(
                            asset_id=asset_id, source_type="pdf", book_id=book_id, page_index=page_index, bbox=bbox,
                            mime_type=image.mime_type, width=image.width, height=image.height, size_bytes=staged.size_bytes,
                            sha256=staged.sha256, storage_key=f"assets/books/{book_id}/{asset_id}.{EXTENSIONS[image.mime_type]}",
                            extractor_version="pdf-images-v1", created_at=datetime.now(UTC),
                        )
                        prepared.append(PreparedPdfImage(asset, occurrence_index, staged))
            return prepared
        except BaseException as error:
            for path in staged_paths:
                path.unlink(missing_ok=True)
            if isinstance(error, (PdfImageExtractionError, OSError, KeyboardInterrupt, SystemExit)):
                raise
            if isinstance(error, ImageAssetError):
                raise PdfImageExtractionError(page_index, limit=error.code == "IMAGE_TOO_LARGE") from error
            raise PdfImageExtractionError(page_index) from error
