"""Bounded staging, atomic writes and validated asset paths."""

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import tempfile
from typing import BinaryIO

from bookdistill.contracts.image_assets import ImageAsset
from .errors import ImageAssetError


@dataclass(frozen=True, slots=True)
class StagedImage:
    path: Path
    sha256: str
    size_bytes: int


class ImageStorage:
    def __init__(self, root: Path, max_bytes: int) -> None:
        self.root = root.resolve()
        self.assets_root = (self.root / "assets").resolve()
        if not self.assets_root.is_relative_to(self.root) or self.assets_root == self.root:
            raise ValueError("asset directory escaped storage root")
        self.assets_root.mkdir(parents=True, exist_ok=True)
        self.staging_root = self.checked_path("assets/.staging")
        self.staging_root.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes

    def checked_path(self, storage_key: str) -> Path:
        # Reuse the same lexical rules without needing a full metadata instance.
        try:
            ImageAsset.validate_storage_key(storage_key)
        except ValueError as error:
            raise ImageAssetError("IMAGE_STORAGE_ERROR", "Asset path failed validation.") from error
        target = (self.root / storage_key).resolve()
        if (not target.is_relative_to(self.assets_root) or target == self.assets_root
                or target != self.root / storage_key):
            raise ImageAssetError("IMAGE_STORAGE_ERROR", "Asset path failed boundary validation.")
        return target

    def stage(self, source: BinaryIO, *, maximum_bytes: int | None = None) -> StagedImage:
        max_bytes = self.max_bytes if maximum_bytes is None else maximum_bytes
        staging_root = self.checked_path("assets/.staging")
        descriptor, name = tempfile.mkstemp(dir=staging_root, suffix=".image")
        path = Path(name)
        total = 0
        digest = hashlib.sha256()
        try:
            with os.fdopen(descriptor, "wb") as output:
                while chunk := source.read(min(1024 * 1024, max_bytes - total + 1)):
                    total += len(chunk)
                    if total > max_bytes:
                        raise ImageAssetError("IMAGE_TOO_LARGE", "Image upload exceeds the byte limit.", 413)
                    output.write(chunk)
                    digest.update(chunk)
                output.flush()
                os.fsync(output.fileno())
            return StagedImage(path, digest.hexdigest(), total)
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    def finalize(self, staged: StagedImage, storage_key: str) -> None:
        target = self.checked_path(storage_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target = self.checked_path(storage_key)
        if target.exists():
            raise ImageAssetError("IMAGE_STORAGE_ERROR", "Asset destination already exists.")
        os.replace(staged.path, target)

    def remove(self, storage_key: str) -> None:
        self.checked_path(storage_key).unlink(missing_ok=True)

    def open(self, storage_key: str) -> BinaryIO:
        try:
            return self.checked_path(storage_key).open("rb")
        except FileNotFoundError as error:
            raise ImageAssetError("IMAGE_CONTENT_NOT_FOUND", "Image content was not found.", 404) from error
        except OSError as error:
            raise ImageAssetError("IMAGE_STORAGE_ERROR", "Image content could not be read.") from error
