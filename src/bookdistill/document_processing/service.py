"""Application service coordinating ingestion, persistence, and parsing."""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import nullcontext
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from bookdistill.contracts import (
    Book,
    Chapter,
    Chunk,
    ErrorCategory,
    ErrorDetail,
    JobStatus,
    ParsedDocument,
    ImageResource,
    ParsingJob,
    ProcessingStage,
)
from bookdistill.db import (
    BookRecord,
    ChapterRecord,
    ChunkRecord,
    ParsedDocumentRecord,
    ParsingJobRecord,
    ProcessingErrorRecord,
    ImageAssetRecord,
)
from bookdistill.image_assets import ImageAssetService
from bookdistill.image_assets.errors import ImageAssetError

from .cleaning import DocumentCleaner
from .chunking import ChapterAwareChunker
from .exceptions import DocumentProcessingError
from .parser import PyMuPDFParser
from .storage import LocalBookStorage
from .structuring import ChapterStructurer
from .validation import PdfValidator


@dataclass(frozen=True, slots=True)
class IngestionResult:
    book_id: UUID
    job_id: UUID
    duplicate: bool


class IngestionService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        storage: LocalBookStorage,
        validator: PdfValidator,
        parser: PyMuPDFParser,
        cleaner: DocumentCleaner,
        structurer: ChapterStructurer,
        chunker: ChapterAwareChunker,
        images: ImageAssetService | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.storage = storage
        self.validator = validator
        self.parser = parser
        self.cleaner = cleaner
        self.structurer = structurer
        self.chunker = chunker
        self.images = images

    def _parse_with_images(self, source_path: Path, book_id: UUID) -> ParsedDocument:
        with self.images.book_lock(book_id) if self.images else nullcontext():
            image_views = []
            if self.images:
                try:
                    image_views = self.images.replace_pdf_images(source_path, book_id)
                except ImageAssetError as error:
                    raise DocumentProcessingError(
                        code=error.code if error.code.startswith("PDF_IMAGE_") else "PDF_IMAGE_EXTRACTION_FAILED",
                        message="PDF image extraction did not complete.",
                        category=ErrorCategory.STORAGE if error.status_code >= 500 else ErrorCategory.PARSING,
                        stage=ProcessingStage.PARSE, retryable=error.status_code >= 500,
                        page_index=getattr(error, "page_index", None),
                        details={"component": "image_extraction", "asset_error_code": error.code},
                        http_status=error.status_code,
                    ) from error
            # Asset transactions have committed before text parsing starts.
            document = self.parser.parse(source_path, book_id)
            document.resources = [*document.resources, *[
                ImageResource(asset_id=image.asset_id, page_index=image.page_index, bbox=image.bbox)
                for image in image_views
            ]]
            self.storage.write_json_artifact(book_id, "parsed_document.raw.v1.json", document.model_dump(mode="json"))
            return document

    def ingest(
        self,
        source: BinaryIO,
        *,
        original_file_name: str,
        declared_mime_type: str | None,
        title: str | None,
        authors: list[str],
        language: str,
    ) -> IngestionResult:
        normalized_title = (title or Path(original_file_name).stem).strip()
        if not normalized_title or len(language.strip()) < 2:
            from bookdistill.contracts import ErrorCategory

            raise DocumentProcessingError(
                code="INVALID_BOOK_METADATA",
                message="Book title and language are required.",
                category=ErrorCategory.VALIDATION,
                stage=ProcessingStage.VALIDATE,
                details={"title": normalized_title, "language": language},
                http_status=422,
            )
        staged = self.storage.stage(source)
        try:
            with self.session_factory() as session:
                existing = session.scalar(
                    select(BookRecord).where(BookRecord.sha256 == staged.sha256)
                )
                if existing is not None:
                    existing_job = session.scalar(
                        select(ParsingJobRecord)
                        .where(ParsingJobRecord.book_id == existing.id)
                        .order_by(ParsingJobRecord.created_at.desc())
                    )
                    if existing_job is None:
                        raise RuntimeError("existing book has no parsing job")
                    self.storage.discard(staged.path)
                    return IngestionResult(
                        book_id=UUID(existing.id),
                        job_id=UUID(existing_job.id),
                        duplicate=True,
                    )

            validated = self.validator.validate(staged.path, declared_mime_type)
            book_id = uuid4()
            job_id = uuid4()
            destination = self.storage.finalize(staged.path, book_id)
            now = datetime.now(UTC)
            book = BookRecord(
                id=str(book_id),
                title=normalized_title,
                authors=authors,
                language=language.strip(),
                description=None,
                tags=[],
                original_file_name=Path(original_file_name).name,
                mime_type=validated.mime_type,
                size_bytes=staged.size_bytes,
                sha256=staged.sha256,
                storage_uri=str(destination),
                page_count=validated.page_count,
                status="parsing",
                created_at=now,
                updated_at=now,
            )
            job = ParsingJobRecord(
                id=str(job_id),
                book_id=str(book_id),
                status="queued",
                current_stage="parse",
                progress_percent=25,
                attempt=1,
                max_attempts=3,
                completed_stages=["validate", "classify"],
                checkpoint={"sha256": staged.sha256},
                created_at=now,
                updated_at=now,
            )
            try:
                with self.session_factory.begin() as session:
                    session.add_all([book, job])
            except IntegrityError:
                destination.unlink(missing_ok=True)
                with self.session_factory() as session:
                    existing = session.scalar(
                        select(BookRecord).where(BookRecord.sha256 == staged.sha256)
                    )
                    if existing is None:
                        raise
                    existing_job = session.scalar(
                        select(ParsingJobRecord)
                        .where(ParsingJobRecord.book_id == existing.id)
                        .order_by(ParsingJobRecord.created_at.desc())
                    )
                    if existing_job is None:
                        raise
                    return IngestionResult(
                        book_id=UUID(existing.id),
                        job_id=UUID(existing_job.id),
                        duplicate=True,
                    )
            return IngestionResult(book_id=book_id, job_id=job_id, duplicate=False)
        except Exception:
            self.storage.discard(staged.path)
            raise

    def process_job(self, job_id: UUID) -> None:
        now = datetime.now(UTC)
        with self.session_factory.begin() as session:
            job = session.get(ParsingJobRecord, str(job_id))
            if job is None:
                return
            if job.status == "succeeded":
                return
            job.status = "running"
            job.current_stage = "parse"
            job.progress_percent = 40
            job.started_at = job.started_at or now
            job.updated_at = now
            book_id = UUID(job.book_id)
            book = session.get(BookRecord, job.book_id)
            if book is None:
                raise RuntimeError("parsing job references a missing book")
            source_path = Path(book.storage_uri)

        active_stage = ProcessingStage.PARSE
        try:
            document = self._parse_with_images(source_path, book_id)
            self._advance_job(
                job_id,
                stage=ProcessingStage.CLEAN,
                progress=60,
                completed=["validate", "classify", "parse"],
            )

            active_stage = ProcessingStage.CLEAN
            document = self.cleaner.clean(document)
            self._advance_job(
                job_id,
                stage=ProcessingStage.STRUCTURE,
                progress=78,
                completed=["validate", "classify", "parse", "clean"],
            )

            active_stage = ProcessingStage.STRUCTURE
            chapters = self.structurer.build(document)
            self._advance_job(
                job_id,
                stage=ProcessingStage.CHUNK,
                progress=90,
                completed=[
                    "validate",
                    "classify",
                    "parse",
                    "clean",
                    "structure",
                ],
            )

            active_stage = ProcessingStage.CHUNK
            chunks = self.chunker.chunk(document, chapters)
            artifact_path = self.storage.write_json_artifact(
                book_id,
                "parsed_document.v1.json",
                document.model_dump(mode="json"),
            )
            chapters_path = self.storage.write_json_artifact(
                book_id,
                "chapters.v1.json",
                {
                    "schema_version": "1.0",
                    "book_id": str(book_id),
                    "chapters": [chapter.model_dump(mode="json") for chapter in chapters],
                },
            )
            chunks_path = self.storage.write_json_artifact(
                book_id,
                "chunks.v1.json",
                {
                    "schema_version": "1.0",
                    "book_id": str(book_id),
                    "chunk_version": self.chunker.chunk_version,
                    "chunks": [chunk.model_dump(mode="json") for chunk in chunks],
                },
            )
            finished_at = datetime.now(UTC)
            with self.session_factory.begin() as session:
                job = session.get(ParsingJobRecord, str(job_id))
                book = session.get(BookRecord, str(book_id))
                if job is None or book is None:
                    raise RuntimeError("job or book disappeared while parsing")
                existing = session.scalar(
                    select(ParsedDocumentRecord).where(
                        ParsedDocumentRecord.book_id == str(book_id)
                    )
                )
                if existing is None:
                    existing = ParsedDocumentRecord(
                        id=str(document.document_id),
                        book_id=str(book_id),
                        schema_version=document.schema_version,
                        parser_name=document.parser_name,
                        parser_version=document.parser_version,
                        payload=document.model_dump(mode="json"),
                        artifact_uri=str(artifact_path),
                        created_at=document.created_at,
                    )
                    session.add(existing)
                else:
                    existing.id = str(document.document_id)
                    existing.schema_version = document.schema_version
                    existing.parser_name = document.parser_name
                    existing.parser_version = document.parser_version
                    existing.payload = document.model_dump(mode="json")
                    existing.artifact_uri = str(artifact_path)
                    existing.created_at = document.created_at

                session.execute(
                    delete(ChunkRecord).where(ChunkRecord.book_id == str(book_id))
                )
                session.execute(
                    delete(ChapterRecord).where(ChapterRecord.book_id == str(book_id))
                )
                for chapter in chapters:
                    session.add(
                        ChapterRecord(
                            id=str(chapter.chapter_id),
                            book_id=str(chapter.book_id),
                            parent_id=str(chapter.parent_id) if chapter.parent_id else None,
                            schema_version=chapter.schema_version,
                            title=chapter.title,
                            normalized_title=chapter.normalized_title,
                            level=chapter.level,
                            order_index=chapter.order_index,
                            page_start=chapter.page_start,
                            page_end=chapter.page_end,
                            source_block_id=str(chapter.source_block_id),
                            confidence=chapter.confidence,
                            manually_verified=chapter.manually_verified,
                        )
                    )
                    session.flush()

                for chunk in chunks:
                    session.add(
                        ChunkRecord(
                            id=str(chunk.chunk_id),
                            book_id=str(chunk.book_id),
                            chapter_id=str(chunk.chapter_id),
                            schema_version=chunk.schema_version,
                            chapter_path=chunk.chapter_path,
                            text=chunk.text,
                            raw_text=chunk.raw_text,
                            page_start=chunk.page_start,
                            page_end=chunk.page_end,
                            source_block_ids=[
                                str(block_id) for block_id in chunk.source_block_ids
                            ],
                            previous_chunk_id=(
                                str(chunk.previous_chunk_id)
                                if chunk.previous_chunk_id
                                else None
                            ),
                            next_chunk_id=(
                                str(chunk.next_chunk_id) if chunk.next_chunk_id else None
                            ),
                            token_count=chunk.token_count,
                            content_type=chunk.content_type,
                            chunk_version=chunk.chunk_version,
                            checksum=chunk.checksum,
                            created_at=finished_at,
                        )
                    )

                book.status = "indexing"
                book.updated_at = finished_at
                job.status = "succeeded"
                job.current_stage = "chunk"
                job.progress_percent = 100
                job.completed_stages = [
                    "validate",
                    "classify",
                    "parse",
                    "clean",
                    "structure",
                    "chunk",
                ]
                job.checkpoint = {
                    **job.checkpoint,
                    "document_id": str(document.document_id),
                    "last_completed_page_index": len(document.pages) - 1,
                    "chapter_count": len(chapters),
                    "chapters_artifact_uri": str(chapters_path),
                    "chunk_count": len(chunks),
                    "chunk_version": self.chunker.chunk_version,
                    "chunks_artifact_uri": str(chunks_path),
                }
                job.finished_at = finished_at
                job.updated_at = finished_at
        except Exception as error:
            self._fail_job(job_id, error, active_stage)

    def create_processing_job(
        self, book_id: UUID, *, start_stage: ProcessingStage = ProcessingStage.PARSE
    ) -> UUID:
        now = datetime.now(UTC)
        job_id = uuid4()
        with self.session_factory.begin() as session:
            book = session.get(BookRecord, str(book_id))
            if book is None:
                raise DocumentProcessingError(
                    code="BOOK_NOT_FOUND",
                    message="The requested book does not exist.",
                    category=ErrorCategory.VALIDATION,
                    stage=start_stage,
                    http_status=404,
                )
            book.status = {
                ProcessingStage.PARSE: "parsing",
                ProcessingStage.CLEAN: "parsing",
                ProcessingStage.STRUCTURE: "structuring",
                ProcessingStage.CHUNK: "chunking",
            }.get(start_stage, "parsing")
            book.updated_at = now
            session.add(
                ParsingJobRecord(
                    id=str(job_id),
                    book_id=str(book_id),
                    status="queued",
                    current_stage=start_stage.value,
                    progress_percent=0,
                    attempt=1,
                    max_attempts=3,
                    completed_stages=[],
                    checkpoint={"rebuild_from": start_stage.value},
                    created_at=now,
                    updated_at=now,
                )
            )
        return job_id

    def rebuild_job(
        self,
        job_id: UUID,
        *,
        from_stage: ProcessingStage,
        chapter_ids: set[UUID] | None = None,
    ) -> dict[str, object]:
        if from_stage == ProcessingStage.PARSE:
            self.process_job(job_id)
            state = self.get_job(job_id)
            return dict(state.checkpoint) if state else {}
        if from_stage not in {
            ProcessingStage.CLEAN,
            ProcessingStage.STRUCTURE,
            ProcessingStage.CHUNK,
        }:
            raise ValueError(f"unsupported rebuild stage: {from_stage.value}")
        if chapter_ids and from_stage != ProcessingStage.CHUNK:
            raise ValueError("chapter_ids are only supported for chunk rebuilds")

        now = datetime.now(UTC)
        with self.session_factory.begin() as session:
            job = session.get(ParsingJobRecord, str(job_id))
            if job is None:
                raise ValueError("legacy parsing job does not exist")
            job.status = "running"
            job.current_stage = from_stage.value
            job.progress_percent = 10
            job.started_at = job.started_at or now
            job.updated_at = now
            book_id = UUID(job.book_id)

        active_stage = from_stage
        try:
            if from_stage == ProcessingStage.CLEAN:
                raw_path = self.storage.book_root(book_id) / "parsed_document.raw.v1.json"
                if not raw_path.exists():
                    raise DocumentProcessingError(
                        code="RAW_PARSED_DOCUMENT_MISSING",
                        message="Raw parsed document is missing; rebuild from parse instead.",
                        category=ErrorCategory.STORAGE,
                        stage=ProcessingStage.CLEAN,
                        http_status=409,
                    )
                document = ParsedDocument.model_validate(
                    json.loads(raw_path.read_text(encoding="utf-8"))
                )
                document = self.cleaner.clean(document)
                active_stage = ProcessingStage.STRUCTURE
                chapters = self.structurer.build(document)
                active_stage = ProcessingStage.CHUNK
                chunks = self.chunker.chunk(document, chapters)
                replace_chapters = True
            elif from_stage == ProcessingStage.STRUCTURE:
                document = self._required_document(book_id, ProcessingStage.STRUCTURE)
                chapters = self.structurer.build(document)
                active_stage = ProcessingStage.CHUNK
                chunks = self.chunker.chunk(document, chapters)
                replace_chapters = True
            else:
                document = self._required_document(book_id, ProcessingStage.CHUNK)
                chapters = self.get_chapters(book_id) or []
                if not chapters:
                    raise DocumentProcessingError(
                        code="CHAPTERS_MISSING",
                        message="No chapters exist; rebuild from structure instead.",
                        category=ErrorCategory.STRUCTURING,
                        stage=ProcessingStage.CHUNK,
                        http_status=409,
                    )
                unknown = (chapter_ids or set()) - {
                    chapter.chapter_id for chapter in chapters
                }
                if unknown:
                    raise DocumentProcessingError(
                        code="CHAPTER_SCOPE_INVALID",
                        message="One or more requested chapters do not belong to the book.",
                        category=ErrorCategory.VALIDATION,
                        stage=ProcessingStage.CHUNK,
                        details={"unknown_chapter_ids": sorted(map(str, unknown))},
                        http_status=422,
                    )
                regenerated = self.chunker.chunk(document, chapters)
                existing_chunks = self.get_chunks(book_id) or []
                if chapter_ids and not existing_chunks:
                    raise DocumentProcessingError(
                        code="CHUNKS_MISSING",
                        message="No existing chunks are available for a chapter-local rebuild.",
                        category=ErrorCategory.STORAGE,
                        stage=ProcessingStage.CHUNK,
                        http_status=409,
                    )
                if chapter_ids and any(
                    chunk.chunk_version != self.chunker.chunk_version
                    for chunk in existing_chunks
                ):
                    raise DocumentProcessingError(
                        code="CHUNK_VERSION_CONFLICT",
                        message="Local rebuild cannot mix chunk versions; rebuild all chunks instead.",
                        category=ErrorCategory.CHUNKING,
                        stage=ProcessingStage.CHUNK,
                        details={"required_chunk_version": self.chunker.chunk_version},
                        http_status=409,
                    )
                chunks = self._merge_partial_chunks(
                    chapters,
                    regenerated,
                    existing_chunks,
                    chapter_ids,
                )
                replace_chapters = False

            checkpoint = self._persist_rebuild(
                book_id,
                document=document,
                chapters=chapters,
                chunks=chunks,
                replace_chapters=replace_chapters,
            )
            finished = datetime.now(UTC)
            with self.session_factory.begin() as session:
                job = session.get(ParsingJobRecord, str(job_id))
                if job is None:
                    raise RuntimeError("legacy parsing job disappeared")
                job.status = "succeeded"
                job.current_stage = "chunk"
                job.progress_percent = 100
                job.completed_stages = self._completed_stages(from_stage)
                job.checkpoint = {**job.checkpoint, **checkpoint}
                job.finished_at = finished
                job.updated_at = finished
            return checkpoint
        except Exception as error:
            self._fail_job(job_id, error, active_stage)
            return {}

    def delete_book(self, book_id: UUID) -> bool:
        with self.images.book_lock(book_id) if self.images else nullcontext():
            deleted = self._delete_book_records(book_id)
            # Retrying after a committed deletion can still finish orphan cleanup.
            self.storage.delete_book(book_id)
            if self.images:
                self.images.cleanup_book_files(book_id, set())
            return deleted

    def _delete_book_records(self, book_id: UUID) -> bool:
        with self.session_factory.begin() as session:
            book = session.get(BookRecord, str(book_id))
            if book is None:
                return False
            session.execute(delete(ImageAssetRecord).where(ImageAssetRecord.book_id == str(book_id)))
            job_ids = session.scalars(
                select(ParsingJobRecord.id).where(
                    ParsingJobRecord.book_id == str(book_id)
                )
            ).all()
            session.execute(delete(ChunkRecord).where(ChunkRecord.book_id == str(book_id)))
            session.execute(delete(ChapterRecord).where(ChapterRecord.book_id == str(book_id)))
            session.execute(
                delete(ParsedDocumentRecord).where(
                    ParsedDocumentRecord.book_id == str(book_id)
                )
            )
            if job_ids:
                session.execute(
                    delete(ProcessingErrorRecord).where(
                        ProcessingErrorRecord.job_id.in_(job_ids)
                    )
                )
            session.execute(
                delete(ParsingJobRecord).where(
                    ParsingJobRecord.book_id == str(book_id)
                )
            )
            session.delete(book)
        return True

    # Durable stage boundaries used by the generic task system.  Each method
    # writes its output before returning, so a worker may die between stages
    # without forcing already completed work to run again.
    def begin_durable_stage(
        self, job_id: UUID, stage: ProcessingStage, attempt: int
    ) -> None:
        now = datetime.now(UTC)
        with self.session_factory.begin() as session:
            job = session.get(ParsingJobRecord, str(job_id))
            if job is None:
                raise DocumentProcessingError(
                    code="LEGACY_JOB_MISSING",
                    message="The document compatibility job does not exist.",
                    category=ErrorCategory.STORAGE,
                    stage=stage,
                )
            book = session.get(BookRecord, job.book_id)
            if book is None:
                raise DocumentProcessingError(
                    code="BOOK_NOT_FOUND",
                    message="The document book does not exist.",
                    category=ErrorCategory.STORAGE,
                    stage=stage,
                )
            job.status = "running"
            job.current_stage = stage.value
            job.attempt = attempt
            job.started_at = job.started_at or now
            job.finished_at = None
            job.error_id = None
            job.updated_at = now
            book.status = {
                ProcessingStage.PARSE: "parsing",
                ProcessingStage.CLEAN: "parsing",
                ProcessingStage.STRUCTURE: "structuring",
                ProcessingStage.CHUNK: "chunking",
            }.get(stage, book.status)
            book.updated_at = now

    def run_parse_stage(self, book_id: UUID, job_id: UUID) -> dict[str, object]:
        with self.session_factory() as session:
            book = session.get(BookRecord, str(book_id))
            if book is None:
                raise DocumentProcessingError(
                    code="BOOK_NOT_FOUND", message="The book does not exist.",
                    category=ErrorCategory.STORAGE, stage=ProcessingStage.PARSE,
                )
            source_path = Path(book.storage_uri)
        document = self._parse_with_images(source_path, book_id)
        artifact = self.storage.book_root(book_id) / "parsed_document.raw.v1.json"
        self._advance_job(
            job_id, stage=ProcessingStage.CLEAN, progress=35,
            completed=["validate", "classify", "parse"],
        )
        return {
            "document_id": str(document.document_id),
            "raw_document_artifact_uri": str(artifact),
            "page_count": len(document.pages),
        }

    def run_clean_stage(self, book_id: UUID, job_id: UUID) -> dict[str, object]:
        raw_path = self.storage.book_root(book_id) / "parsed_document.raw.v1.json"
        if not raw_path.exists():
            raise DocumentProcessingError(
                code="RAW_PARSED_DOCUMENT_MISSING",
                message="Raw parsed document is missing; restart from parse.",
                category=ErrorCategory.STORAGE, stage=ProcessingStage.CLEAN,
            )
        document = ParsedDocument.model_validate(
            json.loads(raw_path.read_text(encoding="utf-8"))
        )
        cleaned = self.cleaner.clean(document)
        artifact = self.storage.write_json_artifact(
            book_id, "parsed_document.cleaned.v1.json", cleaned.model_dump(mode="json")
        )
        self._advance_job(
            job_id, stage=ProcessingStage.STRUCTURE, progress=52,
            completed=["validate", "classify", "parse", "clean"],
        )
        return {"cleaned_document_artifact_uri": str(artifact)}

    def run_structure_stage(self, book_id: UUID, job_id: UUID) -> dict[str, object]:
        document = self._stage_document(book_id, ProcessingStage.STRUCTURE)
        chapters = self.structurer.build(document)
        structured_document = self.storage.write_json_artifact(
            book_id, "parsed_document.structured.v1.json", document.model_dump(mode="json")
        )
        artifact = self.storage.write_json_artifact(
            book_id,
            "chapters.v1.json",
            {
                "schema_version": "1.0", "book_id": str(book_id),
                "chapters": [item.model_dump(mode="json") for item in chapters],
            },
        )
        self._advance_job(
            job_id, stage=ProcessingStage.CHUNK, progress=68,
            completed=["validate", "classify", "parse", "clean", "structure"],
        )
        return {
            "chapters_artifact_uri": str(artifact),
            "structured_document_artifact_uri": str(structured_document),
            "chapter_count": len(chapters),
        }

    def run_chunk_stage(
        self,
        book_id: UUID,
        job_id: UUID,
        chapter_ids: set[UUID] | None = None,
    ) -> dict[str, object]:
        document = self._stage_document(book_id, ProcessingStage.CHUNK)
        chapters = self._stage_chapters(book_id)
        if not chapters:
            raise DocumentProcessingError(
                code="CHAPTERS_MISSING", message="No chapter artifact is available.",
                category=ErrorCategory.STRUCTURING, stage=ProcessingStage.CHUNK,
            )
        unknown = (chapter_ids or set()) - {item.chapter_id for item in chapters}
        if unknown:
            raise DocumentProcessingError(
                code="CHAPTER_SCOPE_INVALID",
                message="One or more requested chapters do not belong to the book.",
                category=ErrorCategory.VALIDATION, stage=ProcessingStage.CHUNK,
            )
        regenerated = self.chunker.chunk(document, chapters)
        if chapter_ids:
            existing = self.get_chunks(book_id) or []
            if not existing:
                raise DocumentProcessingError(
                    code="CHUNKS_MISSING", message="Existing chunks are required for a local rebuild.",
                    category=ErrorCategory.STORAGE, stage=ProcessingStage.CHUNK,
                )
            chunks = self._merge_partial_chunks(chapters, regenerated, existing, chapter_ids)
        else:
            chunks = regenerated
        artifact = self.storage.write_json_artifact(
            book_id,
            "chunks.v1.json",
            {
                "schema_version": "1.0", "book_id": str(book_id),
                "chunk_version": self.chunker.chunk_version,
                "chunks": [item.model_dump(mode="json") for item in chunks],
            },
        )
        self._advance_job(
            job_id, stage=ProcessingStage.CHUNK, progress=84,
            completed=["validate", "classify", "parse", "clean", "structure", "chunk"],
        )
        return {
            "chunks_artifact_uri": str(artifact), "chunk_count": len(chunks),
            "chunk_version": self.chunker.chunk_version,
        }

    def run_persist_stage(
        self,
        book_id: UUID,
        job_id: UUID,
        *,
        replace_chapters: bool,
    ) -> dict[str, object]:
        document = self._stage_document(book_id, ProcessingStage.CHUNK)
        chapters = self._stage_chapters(book_id)
        chunks_path = self.storage.book_root(book_id) / "chunks.v1.json"
        if not chunks_path.exists():
            raise DocumentProcessingError(
                code="CHUNKS_ARTIFACT_MISSING", message="Chunk artifact is missing.",
                category=ErrorCategory.STORAGE, stage=ProcessingStage.CHUNK,
            )
        chunks_payload = json.loads(chunks_path.read_text(encoding="utf-8"))
        chunks = [Chunk.model_validate(item) for item in chunks_payload.get("chunks", [])]
        output = self._persist_rebuild(
            book_id, document=document, chapters=chapters, chunks=chunks,
            replace_chapters=replace_chapters,
        )
        now = datetime.now(UTC)
        with self.session_factory.begin() as session:
            job = session.get(ParsingJobRecord, str(job_id))
            if job is None:
                raise RuntimeError("parsing job disappeared during persistence")
            job.status = "succeeded"
            job.progress_percent = 100
            job.completed_stages = ["validate", "classify", "parse", "clean", "structure", "chunk"]
            job.checkpoint = {**(job.checkpoint or {}), **output}
            job.finished_at = now
            job.updated_at = now
        return output

    def record_durable_failure(
        self, job_id: UUID, error: Exception, stage: ProcessingStage
    ) -> None:
        self._fail_job(job_id, error, stage)

    def cancel_durable_job(self, job_id: UUID) -> None:
        now = datetime.now(UTC)
        with self.session_factory.begin() as session:
            job = session.get(ParsingJobRecord, str(job_id))
            if job is None:
                return
            job.status = "cancelled"
            job.finished_at = now
            job.updated_at = now
            book = session.get(BookRecord, job.book_id)
            if book is not None:
                book.status = "cancelled"
                book.updated_at = now

    def _stage_document(
        self, book_id: UUID, stage: ProcessingStage
    ) -> ParsedDocument:
        names = ["parsed_document.cleaned.v1.json", "parsed_document.v1.json"]
        if stage == ProcessingStage.CHUNK:
            names.insert(0, "parsed_document.structured.v1.json")
        for name in names:
            path = self.storage.book_root(book_id) / name
            if path.exists():
                return ParsedDocument.model_validate(
                    json.loads(path.read_text(encoding="utf-8"))
                )
        return self._required_document(book_id, stage)

    def _stage_chapters(self, book_id: UUID) -> list[Chapter]:
        path = self.storage.book_root(book_id) / "chapters.v1.json"
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            return [Chapter.model_validate(item) for item in payload.get("chapters", [])]
        return self.get_chapters(book_id) or []

    def _required_document(
        self, book_id: UUID, stage: ProcessingStage
    ) -> ParsedDocument:
        document = self.get_parsed_document(book_id)
        if document is None:
            raise DocumentProcessingError(
                code="PARSED_DOCUMENT_MISSING",
                message="Parsed document is missing; rebuild from parse instead.",
                category=ErrorCategory.STORAGE,
                stage=stage,
                http_status=409,
            )
        return document

    def _persist_rebuild(
        self,
        book_id: UUID,
        *,
        document: ParsedDocument,
        chapters: list[Chapter],
        chunks: list[Chunk],
        replace_chapters: bool,
    ) -> dict[str, object]:
        document_path = self.storage.write_json_artifact(
            book_id, "parsed_document.v1.json", document.model_dump(mode="json")
        )
        self.storage.write_json_artifact(
            book_id, "parsed_document.structured.v1.json", document.model_dump(mode="json")
        )
        chapters_path = self.storage.write_json_artifact(
            book_id,
            "chapters.v1.json",
            {
                "schema_version": "1.0",
                "book_id": str(book_id),
                "chapters": [chapter.model_dump(mode="json") for chapter in chapters],
            },
        )
        chunks_path = self.storage.write_json_artifact(
            book_id,
            "chunks.v1.json",
            {
                "schema_version": "1.0",
                "book_id": str(book_id),
                "chunk_version": self.chunker.chunk_version,
                "chunks": [chunk.model_dump(mode="json") for chunk in chunks],
            },
        )
        now = datetime.now(UTC)
        with self.session_factory.begin() as session:
            book = session.get(BookRecord, str(book_id))
            if book is None:
                raise RuntimeError("book disappeared during rebuild")
            parsed = session.scalar(
                select(ParsedDocumentRecord).where(
                    ParsedDocumentRecord.book_id == str(book_id)
                )
            )
            if parsed is None:
                parsed = ParsedDocumentRecord(
                    id=str(document.document_id),
                    book_id=str(book_id),
                    schema_version=document.schema_version,
                    parser_name=document.parser_name,
                    parser_version=document.parser_version,
                    payload=document.model_dump(mode="json"),
                    artifact_uri=str(document_path),
                    created_at=document.created_at,
                )
                session.add(parsed)
            else:
                parsed.payload = document.model_dump(mode="json")
                parsed.artifact_uri = str(document_path)

            session.execute(delete(ChunkRecord).where(ChunkRecord.book_id == str(book_id)))
            if replace_chapters:
                session.execute(
                    delete(ChapterRecord).where(ChapterRecord.book_id == str(book_id))
                )
                self._add_chapter_records(session, chapters)
            self._add_chunk_records(session, chunks, now)
            book.status = "indexing"
            book.updated_at = now
        return {
            "document_id": str(document.document_id),
            "chapter_count": len(chapters),
            "chunk_count": len(chunks),
            "chunk_version": self.chunker.chunk_version,
            "chapters_artifact_uri": str(chapters_path),
            "chunks_artifact_uri": str(chunks_path),
        }

    @staticmethod
    def _add_chapter_records(session: Session, chapters: list[Chapter]) -> None:
        for chapter in chapters:
            session.add(
                ChapterRecord(
                    id=str(chapter.chapter_id),
                    book_id=str(chapter.book_id),
                    parent_id=str(chapter.parent_id) if chapter.parent_id else None,
                    schema_version=chapter.schema_version,
                    title=chapter.title,
                    normalized_title=chapter.normalized_title,
                    level=chapter.level,
                    order_index=chapter.order_index,
                    page_start=chapter.page_start,
                    page_end=chapter.page_end,
                    source_block_id=str(chapter.source_block_id),
                    confidence=chapter.confidence,
                    manually_verified=chapter.manually_verified,
                )
            )
            session.flush()

    @staticmethod
    def _add_chunk_records(
        session: Session, chunks: list[Chunk], created_at: datetime
    ) -> None:
        for chunk in chunks:
            session.add(
                ChunkRecord(
                    id=str(chunk.chunk_id),
                    book_id=str(chunk.book_id),
                    chapter_id=str(chunk.chapter_id),
                    schema_version=chunk.schema_version,
                    chapter_path=chunk.chapter_path,
                    text=chunk.text,
                    raw_text=chunk.raw_text,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    source_block_ids=[str(item) for item in chunk.source_block_ids],
                    previous_chunk_id=(
                        str(chunk.previous_chunk_id) if chunk.previous_chunk_id else None
                    ),
                    next_chunk_id=str(chunk.next_chunk_id) if chunk.next_chunk_id else None,
                    token_count=chunk.token_count,
                    content_type=chunk.content_type,
                    chunk_version=chunk.chunk_version,
                    checksum=chunk.checksum,
                    created_at=created_at,
                )
            )

    @staticmethod
    def _merge_partial_chunks(
        chapters: list[Chapter],
        regenerated: list[Chunk],
        existing: list[Chunk],
        chapter_ids: set[UUID] | None,
    ) -> list[Chunk]:
        if not chapter_ids:
            return regenerated
        regenerated_by_chapter: dict[UUID, list[Chunk]] = {}
        existing_by_chapter: dict[UUID, list[Chunk]] = {}
        for chunk in regenerated:
            regenerated_by_chapter.setdefault(chunk.chapter_id, []).append(chunk)
        for chunk in existing:
            existing_by_chapter.setdefault(chunk.chapter_id, []).append(chunk)
        combined: list[Chunk] = []
        for chapter in sorted(chapters, key=lambda item: item.order_index):
            source = (
                regenerated_by_chapter if chapter.chapter_id in chapter_ids else existing_by_chapter
            )
            selected = source.get(chapter.chapter_id, [])
            if not selected:
                selected = regenerated_by_chapter.get(chapter.chapter_id, [])
            combined.extend(selected)
        ids = [chunk.chunk_id for chunk in combined]
        return [
            Chunk.model_validate(
                {
                    **chunk.model_dump(mode="json"),
                    "previous_chunk_id": str(ids[index - 1]) if index else None,
                    "next_chunk_id": (
                        str(ids[index + 1]) if index + 1 < len(ids) else None
                    ),
                }
            )
            for index, chunk in enumerate(combined)
        ]

    @staticmethod
    def _completed_stages(from_stage: ProcessingStage) -> list[str]:
        ordered = ["validate", "classify", "parse", "clean", "structure", "chunk"]
        if from_stage == ProcessingStage.CLEAN:
            return ordered
        if from_stage == ProcessingStage.STRUCTURE:
            return ordered
        if from_stage == ProcessingStage.CHUNK:
            return ordered
        return ordered

    def _advance_job(
        self,
        job_id: UUID,
        *,
        stage: ProcessingStage,
        progress: int,
        completed: list[str],
    ) -> None:
        with self.session_factory.begin() as session:
            job = session.get(ParsingJobRecord, str(job_id))
            if job is None:
                raise RuntimeError("parsing job disappeared")
            job.current_stage = stage.value
            job.progress_percent = progress
            job.completed_stages = completed
            job.updated_at = datetime.now(UTC)

    def _fail_job(
        self, job_id: UUID, error: Exception, active_stage: ProcessingStage
    ) -> None:
        if isinstance(error, DocumentProcessingError):
            domain_error = error
        else:
            from bookdistill.contracts import ErrorCategory

            domain_error = DocumentProcessingError(
                code="UNEXPECTED_DOCUMENT_PROCESSING_ERROR",
                message="An unexpected error occurred while processing the document.",
                category=ErrorCategory.INTERNAL,
                stage=active_stage,
                retryable=True,
                details={"reason": type(error).__name__},
            )
        now = datetime.now(UTC)
        error_id = uuid4()
        with self.session_factory.begin() as session:
            job = session.get(ParsingJobRecord, str(job_id))
            if job is None:
                return
            book = session.get(BookRecord, job.book_id)
            record = ProcessingErrorRecord(
                id=str(error_id),
                job_id=job.id,
                code=domain_error.code,
                category=domain_error.category.value,
                message=domain_error.message,
                stage=domain_error.stage.value,
                retryable=domain_error.retryable,
                page_index=domain_error.page_index,
                details=domain_error.details,
                trace_id=f"job-{job.id}",
                occurred_at=now,
            )
            session.add(record)
            session.flush()
            job.status = "failed"
            job.current_stage = domain_error.stage.value
            job.error_id = str(error_id)
            job.finished_at = now
            job.updated_at = now
            if book is not None:
                book.status = "failed"
                book.updated_at = now

    def get_book(self, book_id: UUID) -> Book | None:
        with self.session_factory() as session:
            record = session.get(BookRecord, str(book_id))
            return _book_contract(record) if record else None

    def list_books(self, *, limit: int = 100, offset: int = 0) -> list[Book]:
        with self.session_factory() as session:
            records = session.scalars(
                select(BookRecord)
                .order_by(BookRecord.updated_at.desc(), BookRecord.id)
                .limit(limit)
                .offset(offset)
            ).all()
            return [_book_contract(record) for record in records]

    def get_job(self, job_id: UUID) -> ParsingJob | None:
        with self.session_factory() as session:
            record = session.get(ParsingJobRecord, str(job_id))
            if record is None:
                return None
            error = (
                session.get(ProcessingErrorRecord, record.error_id)
                if record.error_id
                else None
            )
            return _job_contract(record, error)

    def get_parsed_document(self, book_id: UUID) -> ParsedDocument | None:
        with self.session_factory() as session:
            record = session.scalar(
                select(ParsedDocumentRecord).where(
                    ParsedDocumentRecord.book_id == str(book_id)
                )
            )
            return ParsedDocument.model_validate(record.payload) if record else None

    def get_chapters(self, book_id: UUID) -> list[Chapter] | None:
        with self.session_factory() as session:
            book = session.get(BookRecord, str(book_id))
            if book is None:
                return None
            records = session.scalars(
                select(ChapterRecord)
                .where(ChapterRecord.book_id == str(book_id))
                .order_by(ChapterRecord.order_index)
            ).all()
            return [_chapter_contract(record) for record in records]

    def get_chunks(self, book_id: UUID) -> list[Chunk] | None:
        with self.session_factory() as session:
            book = session.get(BookRecord, str(book_id))
            if book is None:
                return None
            records = session.scalars(
                select(ChunkRecord)
                .where(ChunkRecord.book_id == str(book_id))
                .order_by(ChunkRecord.page_start, ChunkRecord.id)
            ).all()
            chunks = [_chunk_contract(record) for record in records]
            return _order_linked_chunks(chunks)

    def mark_book_ready(self, book_id: UUID) -> None:
        """Advance book state only after the external retrieval index succeeds."""
        with self.session_factory.begin() as session:
            book = session.get(BookRecord, str(book_id))
            if book is None:
                raise ValueError(f"book not found: {book_id}")
            book.status = "ready"
            book.updated_at = datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _book_contract(record: BookRecord) -> Book:
    return Book(
        book_id=UUID(record.id),
        title=record.title,
        authors=record.authors,
        language=record.language,
        description=record.description,
        tags=record.tags,
        original_file_name=record.original_file_name,
        mime_type=record.mime_type,
        size_bytes=record.size_bytes,
        sha256=record.sha256,
        storage_uri=record.storage_uri,
        page_count=record.page_count,
        status=record.status,
        created_at=_aware(record.created_at),
        updated_at=_aware(record.updated_at),
    )


def _error_contract(record: ProcessingErrorRecord | None) -> ErrorDetail | None:
    if record is None:
        return None
    return ErrorDetail(
        error_id=UUID(record.id),
        code=record.code,
        category=record.category,
        message=record.message,
        stage=record.stage,
        retryable=record.retryable,
        page_index=record.page_index,
        details=record.details,
        trace_id=record.trace_id,
        occurred_at=_aware(record.occurred_at),
    )


def _chapter_contract(record: ChapterRecord) -> Chapter:
    return Chapter(
        chapter_id=UUID(record.id),
        book_id=UUID(record.book_id),
        parent_id=UUID(record.parent_id) if record.parent_id else None,
        title=record.title,
        normalized_title=record.normalized_title,
        level=record.level,
        order_index=record.order_index,
        page_start=record.page_start,
        page_end=record.page_end,
        source_block_id=UUID(record.source_block_id),
        confidence=record.confidence,
        manually_verified=record.manually_verified,
    )


def _chunk_contract(record: ChunkRecord) -> Chunk:
    return Chunk(
        chunk_id=UUID(record.id),
        book_id=UUID(record.book_id),
        chapter_id=UUID(record.chapter_id),
        chapter_path=record.chapter_path,
        text=record.text,
        raw_text=record.raw_text,
        page_start=record.page_start,
        page_end=record.page_end,
        source_block_ids=[UUID(block_id) for block_id in record.source_block_ids],
        previous_chunk_id=(UUID(record.previous_chunk_id) if record.previous_chunk_id else None),
        next_chunk_id=UUID(record.next_chunk_id) if record.next_chunk_id else None,
        token_count=record.token_count,
        content_type=record.content_type,
        chunk_version=record.chunk_version,
        checksum=record.checksum,
    )


def _order_linked_chunks(chunks: list[Chunk]) -> list[Chunk]:
    by_id = {chunk.chunk_id: chunk for chunk in chunks}
    starts = [chunk for chunk in chunks if chunk.previous_chunk_id is None]
    if len(starts) != 1:
        return sorted(chunks, key=lambda item: (item.page_start, str(item.chunk_id)))
    ordered: list[Chunk] = []
    visited: set[UUID] = set()
    current: Chunk | None = starts[0]
    while current is not None and current.chunk_id not in visited:
        ordered.append(current)
        visited.add(current.chunk_id)
        current = by_id.get(current.next_chunk_id) if current.next_chunk_id else None
    return ordered if len(ordered) == len(chunks) else sorted(
        chunks, key=lambda item: (item.page_start, str(item.chunk_id))
    )


def _job_contract(
    record: ParsingJobRecord, error: ProcessingErrorRecord | None = None
) -> ParsingJob:
    return ParsingJob(
        job_id=UUID(record.id),
        book_id=UUID(record.book_id),
        status=record.status,
        current_stage=record.current_stage,
        progress_percent=record.progress_percent,
        attempt=record.attempt,
        max_attempts=record.max_attempts,
        completed_stages=record.completed_stages,
        checkpoint=record.checkpoint,
        error=_error_contract(error),
        created_at=_aware(record.created_at),
        started_at=_aware(record.started_at),
        finished_at=_aware(record.finished_at),
        updated_at=_aware(record.updated_at),
    )
