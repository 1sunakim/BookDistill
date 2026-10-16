"""Streaming local storage with hashing and atomic finalization."""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import BinaryIO, Any
from uuid import UUID

from .exceptions import UploadTooLargeError


@dataclass(frozen=True, slots=True)
class StagedUpload:
    path: Path
    sha256: str
    size_bytes: int


class LocalBookStorage:
    def __init__(self, root: Path, maximum_bytes: int) -> None:
        self.root = root.resolve()
        self.maximum_bytes = maximum_bytes
        self.staging_root = self.root / ".staging"
        self.staging_root.mkdir(parents=True, exist_ok=True)

    def stage(self, source: BinaryIO, chunk_size: int = 1024 * 1024) -> StagedUpload:
        digest = hashlib.sha256()
        total = 0
        descriptor, temporary_name = tempfile.mkstemp(suffix=".upload", dir=self.staging_root)
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as destination:
                while data := source.read(chunk_size):
                    total += len(data)
                    if total > self.maximum_bytes:
                        raise UploadTooLargeError(self.maximum_bytes)
                    digest.update(data)
                    destination.write(data)
                destination.flush()
                os.fsync(destination.fileno())
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
        return StagedUpload(path=temporary_path, sha256=digest.hexdigest(), size_bytes=total)

    def finalize(self, staged_path: Path, book_id: UUID, *, extension: str = ".pdf") -> Path:
        if extension not in {".pdf", ".txt", ".md", ".epub", ".docx"}:
            raise ValueError("unsupported stored book extension")
        book_root = self.book_root(book_id)
        book_root.mkdir(parents=True, exist_ok=True)
        destination = book_root / f"original{extension}"
        os.replace(staged_path, destination)
        return destination

    def discard(self, staged_path: Path) -> None:
        staged_path.unlink(missing_ok=True)

    def book_root(self, book_id: UUID | str) -> Path:
        return self.root / "books" / str(book_id)

    @contextmanager
    def open_original(self, book_id: UUID):
        """Read an ID-owned original without trusting user filenames or persisted URIs."""
        expected = self.original_path(book_id)
        with expected.open("rb") as source:
            yield source

    def original_path(self, book_id: UUID) -> Path:
        """Resolve the single controlled original file for a book ID."""
        root = self.book_root(UUID(str(book_id)))
        matches = [root / f"original{extension}" for extension in (".pdf", ".txt", ".md", ".epub", ".docx") if (root / f"original{extension}").is_file()]
        if len(matches) != 1:
            raise FileNotFoundError("book has no unique stored original")
        expected = matches[0]
        if expected.resolve() != expected:
            raise ValueError("book storage redirects outside its owned path")
        return expected

    def write_json_artifact(self, book_id: UUID | str, name: str, payload: dict[str, Any]) -> Path:
        destination = self.book_root(book_id) / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, destination)
        return destination

    def delete_book(self, book_id: UUID | str) -> bool:
        target = self.book_root(book_id).resolve()
        books_root = (self.root / "books").resolve()
        if target.parent != books_root:
            raise ValueError("book storage path escaped the configured books directory")
        if not target.exists():
            return False
        shutil.rmtree(target)
        return True
