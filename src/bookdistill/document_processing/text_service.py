"""V2 source-aware ingestion for text and structured container books."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from bookdistill.contracts import ErrorCategory, ProcessingStage
from bookdistill.contracts.document_v2 import ChapterV2, ChunkV2, ParsedDocumentV2
from bookdistill.db import BookRecord, SourceChunkIndexRecord, SourceDocumentRecord

from .exceptions import DocumentProcessingError
from .storage import LocalBookStorage
from .text_formats import LineOrientedBookReader
from .container_formats import ContainerBookReader
from .text_pipeline import TextDocumentProcessor
from .source_search import SourceSearchService, document_revision
from .cleaning import DocumentCleaner


@dataclass(frozen=True, slots=True)
class TextBookResult:
    book_id: UUID
    duplicate: bool
    format: str


class TextBookService:
    def __init__(
        self, sessions: sessionmaker[Session], storage: LocalBookStorage,
        *, maximum_tokens: int = 512,
    ) -> None:
        self.sessions = sessions
        self.storage = storage
        self.reader = LineOrientedBookReader()
        self.container_reader = ContainerBookReader()
        self.processor = TextDocumentProcessor(maximum_tokens)
        self.search = SourceSearchService(sessions)

    def ingest(
        self, source: BinaryIO, *, filename: str, mime_type: str | None,
        title: str | None, authors: list[str], language: str,
    ) -> TextBookResult:
        normalized_title = (title or Path(filename).stem).strip()
        if not normalized_title or len(language.strip()) < 2:
            raise DocumentProcessingError(
                code="INVALID_BOOK_METADATA", message="Book title and language are required.",
                category=ErrorCategory.VALIDATION, stage=ProcessingStage.VALIDATE, http_status=422,
            )
        staged = self.storage.stage(source)
        destination: Path | None = None
        try:
            reader = self.container_reader if Path(filename).suffix.lower() in {".epub", ".docx"} else self.reader
            validated = reader.validate(staged.path, filename=filename, declared_mime_type=mime_type)
            with self.sessions() as session:
                existing = session.scalar(select(BookRecord).where(BookRecord.sha256 == staged.sha256))
                if existing is not None:
                    if existing.format == validated.format:
                        return TextBookResult(UUID(existing.id), True, existing.format)
                    raise DocumentProcessingError(
                        code="DUPLICATE_BOOK_FORMAT", message="This file already exists with another format.",
                        category=ErrorCategory.VALIDATION, stage=ProcessingStage.VALIDATE, http_status=409,
                    )
            book_id = uuid4()
            destination = self.storage.finalize(
                staged.path, book_id, extension=f".{validated.format}"
            )
            now = datetime.now(UTC)
            try:
                with self.sessions.begin() as session:
                    session.add(BookRecord(
                        id=str(book_id), schema_version="2.0", title=normalized_title,
                        authors=authors, language=language.strip(), description=None, tags=[],
                        original_file_name=Path(filename).name, mime_type=validated.mime_type,
                        size_bytes=staged.size_bytes, sha256=staged.sha256,
                        storage_uri=str(destination), format=validated.format,
                        source_unit_count=None, page_count=None,
                        status="queued", created_at=now, updated_at=now,
                    ))
            except IntegrityError:
                with self.sessions() as session:
                    existing = session.scalar(select(BookRecord).where(BookRecord.sha256 == staged.sha256))
                    if existing is None or existing.format != validated.format:
                        raise
                    destination.unlink(missing_ok=True)
                    destination = None
                    return TextBookResult(UUID(existing.id), True, existing.format)
            return TextBookResult(book_id, False, validated.format)
        except Exception:
            if destination is not None:
                destination.unlink(missing_ok=True)
            raise
        finally:
            self.storage.discard(staged.path)

    def run_stage(self, book_id: UUID, stage_name: str) -> dict[str, object]:
        book = self.get_book(book_id)
        if book is None:
            raise DocumentProcessingError(
                code="BOOK_NOT_FOUND", message="Text book does not exist.",
                category=ErrorCategory.STORAGE, stage=ProcessingStage.PARSE,
            )
        self._set_status(book_id, "processing")
        if stage_name == "parse":
            original = self.storage.original_path(book_id)
            digest = hashlib.sha256()
            with self.storage.open_original(book_id) as source:
                while part := source.read(1024 * 1024):
                    digest.update(part)
            if digest.hexdigest() != book.sha256:
                raise DocumentProcessingError(
                    code="BOOK_SOURCE_CHANGED", message="Stored book source no longer matches its upload checksum.",
                    category=ErrorCategory.STORAGE, stage=ProcessingStage.PARSE, http_status=409,
                )
            if book.format in {"epub", "docx"}:
                document = self.container_reader.read(
                    original, book_id=book_id, source_sha256=book.sha256,
                    format_name=book.format,
                )
            else:
                parsed = self.reader.read(
                    original, filename=book.original_file_name,
                    declared_mime_type=book.mime_type,
                )
                document = parsed.to_document_v2(book_id, book.sha256)
            self.storage.write_json_artifact(
                book_id, "parsed_document.raw.v2.json", document.model_dump(mode="json")
            )
            self._set_status(book_id, "processing", source_unit_count=len(document.units))
            return {"source_unit_count": len(document.units), "format": book.format}
        if stage_name == "clean":
            document = self._load_document(book_id, "parsed_document.raw.v2.json")
            cleaned = document.model_copy(deep=True)
            for unit in cleaned.units:
                for block in unit.blocks:
                    if block.kind not in {"code", "code_fence"}:
                        block.text = DocumentCleaner.clean_block_text(block.text)
            self.storage.write_json_artifact(
                book_id, "parsed_document.cleaned.v2.json", cleaned.model_dump(mode="json")
            )
            return {"cleaned_block_count": sum(len(unit.blocks) for unit in cleaned.units)}
        if stage_name == "structure":
            document = self._load_document(book_id, "parsed_document.cleaned.v2.json")
            chapters = self.processor.build_chapters(document)
            self.storage.write_json_artifact(
                book_id, "chapters.v2.json",
                {"chapters": [chapter.model_dump(mode="json") for chapter in chapters]},
            )
            return {"chapter_count": len(chapters)}
        if stage_name == "chunk":
            document = self._load_document(book_id, "parsed_document.cleaned.v2.json")
            chapters = self._load_chapters(book_id)
            chunks = self.processor.build_chunks(document, chapters)
            if not chunks:
                raise DocumentProcessingError(
                    code="NO_BODY_TEXT", message="The book contains no body text to index.",
                    category=ErrorCategory.CHUNKING, stage=ProcessingStage.CHUNK,
                )
            self.storage.write_json_artifact(
                book_id, "chunks.v2.json",
                {"chunks": [chunk.model_dump(mode="json") for chunk in chunks]},
            )
            return {"chunk_count": len(chunks)}
        if stage_name == "persist":
            document = self._load_document(book_id, "parsed_document.cleaned.v2.json")
            chapters = self._load_chapters(book_id)
            chunks = self._load_chunks(book_id)
            with self.sessions.begin() as session:
                record = session.get(SourceDocumentRecord, str(book_id))
                if record is None:
                    record = SourceDocumentRecord(book_id=str(book_id), created_at=datetime.now(UTC))
                    session.add(record)
                record.schema_version = "2.0"
                record.document = document.model_dump(mode="json")
                record.chapters = [chapter.model_dump(mode="json") for chapter in chapters]
                record.chunks = [chunk.model_dump(mode="json") for chunk in chunks]
                record.document_revision = document_revision(document, chunks)
                if record.indexed_revision != record.document_revision:
                    record.indexed_revision = None
            return {"persisted_chunk_count": len(chunks)}
        if stage_name == "index":
            indexed = self.search.index_book(book_id)
            self._set_status(book_id, "processed")
            return indexed
        raise ValueError(f"unknown text document stage: {stage_name}")

    def mark_failed(self, book_id: UUID) -> None:
        self._set_status(book_id, "failed")

    def mark_cancelled(self, book_id: UUID) -> None:
        self._set_status(book_id, "cancelled")

    def is_retrieval_ready(self, book_id: UUID) -> bool:
        return self.search.is_ready(book_id)

    def mark_queued(self, book_id: UUID) -> None:
        self._set_status(book_id, "queued")

    def delete_book(self, book_id: UUID) -> bool:
        with self.sessions.begin() as session:
            book = session.get(BookRecord, str(book_id))
            if book is None or book.format not in {"txt", "md", "epub", "docx"}:
                return False
            session.execute(delete(SourceChunkIndexRecord).where(SourceChunkIndexRecord.book_id == str(book_id)))
            session.execute(delete(SourceDocumentRecord).where(SourceDocumentRecord.book_id == str(book_id)))
            session.delete(book)
        self.storage.delete_book(book_id)
        return True

    def _set_status(self, book_id: UUID, status: str, *, source_unit_count: int | None = None) -> None:
        with self.sessions.begin() as session:
            book = session.get(BookRecord, str(book_id))
            if book is None:
                return
            book.status = status
            book.updated_at = datetime.now(UTC)
            if source_unit_count is not None:
                book.source_unit_count = source_unit_count

    def _load_document(self, book_id: UUID, name: str) -> ParsedDocumentV2:
        path = self.storage.book_root(book_id) / name
        if not path.is_file():
            self.run_stage(book_id, "parse" if name == "parsed_document.raw.v2.json" else "clean")
        return ParsedDocumentV2.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def _load_chapters(self, book_id: UUID) -> list[ChapterV2]:
        path = self.storage.book_root(book_id) / "chapters.v2.json"
        if not path.is_file():
            self.run_stage(book_id, "structure")
        return [ChapterV2.model_validate(item) for item in json.loads(path.read_text(encoding="utf-8"))["chapters"]]

    def _load_chunks(self, book_id: UUID) -> list[ChunkV2]:
        path = self.storage.book_root(book_id) / "chunks.v2.json"
        if not path.is_file():
            self.run_stage(book_id, "chunk")
        return [ChunkV2.model_validate(item) for item in json.loads(path.read_text(encoding="utf-8"))["chunks"]]

    def get_book(self, book_id: UUID) -> BookRecord | None:
        with self.sessions() as session:
            book = session.get(BookRecord, str(book_id))
            if book is None or book.format not in {"txt", "md", "epub", "docx"}:
                return None
            session.expunge(book)
            return book

    def get(self, book_id: UUID) -> tuple[BookRecord, ParsedDocumentV2, list[ChapterV2], list[ChunkV2]] | None:
        with self.sessions() as session:
            book = session.get(BookRecord, str(book_id))
            source = session.get(SourceDocumentRecord, str(book_id))
            if book is None or source is None:
                return None
            session.expunge(book)
            return (
                book, ParsedDocumentV2.model_validate(source.document),
                [ChapterV2.model_validate(item) for item in source.chapters],
                [ChunkV2.model_validate(item) for item in source.chunks],
            )

    def list_books(self) -> list[BookRecord]:
        with self.sessions() as session:
            books = session.scalars(
                select(BookRecord).where(BookRecord.format.in_(("txt", "md", "epub", "docx")))
                .order_by(BookRecord.updated_at.desc(), BookRecord.id)
            ).all()
            for book in books:
                session.expunge(book)
            return list(books)

    def read_original(self, book_id: UUID) -> str | None:
        book = self.get_book(book_id)
        if book is None or book.format not in {"txt", "md"}:
            return None
        with self.storage.open_original(book_id) as source:
            return source.read().decode("utf-8-sig")

    def original_binary_path(self, book_id: UUID) -> Path | None:
        book = self.get_book(book_id)
        if book is None or book.format not in {"epub", "docx"}:
            return None
        return self.storage.original_path(book_id)
