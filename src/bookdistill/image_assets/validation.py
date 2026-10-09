"""Validate real formats and fully decode images within configured bounds."""

from dataclasses import dataclass
from pathlib import Path
import warnings

from PIL import Image, UnidentifiedImageError

from .errors import ImageAssetError

MIME_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


@dataclass(frozen=True, slots=True)
class ValidatedImage:
    mime_type: str
    width: int
    height: int


def validate_image(path: Path, max_pixels: int, max_dimension: int) -> ValidatedImage:
    def check_size(image: Image.Image) -> None:
        width, height = image.size
        if width * height > max_pixels or max(width, height) > max_dimension:
            raise ImageAssetError("IMAGE_TOO_LARGE", "Decoded image exceeds configured limits.", 413)
        if width <= 0 or height <= 0:
            raise ImageAssetError("INVALID_IMAGE", "Image dimensions must be positive.", 422)

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                if image.format not in MIME_TYPES:
                    raise ImageAssetError("UNSUPPORTED_IMAGE_FORMAT", "Only PNG, JPEG and WebP are supported.", 415)
                check_size(image)
                if getattr(image, "n_frames", 1) != 1:
                    raise ImageAssetError("INVALID_IMAGE", "Animated or multi-frame images are unsupported.", 422)
                metadata = ValidatedImage(MIME_TYPES[image.format], *image.size)
                image.verify()
            with Image.open(path) as image:
                check_size(image)
                image.load()
                if image.size != (metadata.width, metadata.height):
                    raise ImageAssetError("INVALID_IMAGE", "Image dimensions changed during decoding.", 422)
        return metadata
    except ImageAssetError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ImageAssetError("IMAGE_TOO_LARGE", "Decoded image exceeds safe limits.", 413) from error
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, EOFError) as error:
        raise ImageAssetError("INVALID_IMAGE", "Image is empty, damaged or truncated.", 422) from error
