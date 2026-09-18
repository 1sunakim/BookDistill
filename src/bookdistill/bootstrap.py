"""Composition root shared by API and workers."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from bookdistill.database import Base, create_database_engine, create_session_factory
from bookdistill.db import JobRecord, StageRunRecord  # noqa: F401 - registers metadata
from bookdistill.document_processing import (
    DocumentProcessingJobHandler,
    build_document_service,
)
from bookdistill.document_processing.service import IngestionService
from bookdistill.settings import Settings
from bookdistill.generation import (
    DistillationJobHandler,
    DistillationService,
    RagAnswerJobHandler,
    RagAnswerService,
    build_generation_service,
    build_distillation_service,
)
from bookdistill.contracts import TaskType
from bookdistill.task_system import DemoJobHandler, JobRegistry, JobService
from bookdistill.task_system.exceptions import PermanentJobError
from bookdistill.task_system.repository import JobRepository
from bookdistill.retrieval import HybridRetrievalEngine, build_retrieval_engine


@dataclass(slots=True)
class Runtime:
    settings: Settings
    engine: Engine
    sessions: sessionmaker[Session]
    registry: JobRegistry
    jobs: JobService
    generation: RagAnswerService | None
    distillation: DistillationService | None
    documents: IngestionService
    retrieval: HybridRetrievalEngine


def build_runtime(settings: Settings | None = None, *, create_schema: bool = False) -> Runtime:
    settings = settings or Settings.from_environment()
    settings.storage_root.mkdir(parents=True, exist_ok=True)
    engine = create_database_engine(settings.database_url)
    sessions = create_session_factory(engine)
    if create_schema:
        Base.metadata.create_all(engine)
    registry = JobRegistry()
    registry.register(DemoJobHandler())
    documents = build_document_service(settings, sessions)
    retrieval = build_retrieval_engine(settings)

    def index_book(book_id):
        if settings.queue_mode == "celery" and settings.dense_backend.casefold() != "qdrant":
            raise PermanentJobError(
                "SHARED_DENSE_INDEX_REQUIRED",
                "Celery indexing requires a shared dense backend such as Qdrant.",
                {"dense_backend": settings.dense_backend},
            )
        chunks = documents.get_chunks(book_id)
        book = documents.get_book(book_id)
        if not chunks or book is None:
            raise PermanentJobError(
                "CHUNKS_NOT_READY", "Chunks are not available for indexing."
            )
        indexed = retrieval.replace_book_chunks(book_id, chunks, book.title)
        documents.mark_book_ready(book_id)
        return {"indexed_count": indexed, "book_status": "ready"}

    registry.register(DocumentProcessingJobHandler(documents, index_book))
    generation = build_generation_service(settings)
    distillation = build_distillation_service(settings)
    if generation is not None:
        registry.register(RagAnswerJobHandler(generation))
    if distillation is not None:
        for task_type in (
            TaskType.SUMMARY,
            TaskType.KNOWLEDGE_EXTRACTION,
            TaskType.QUESTION_GENERATION,
            TaskType.VIEWPOINT_COMPARISON,
        ):
            registry.register(DistillationJobHandler(task_type, distillation))
    return Runtime(
        settings, engine, sessions, registry,
        JobService(
            JobRepository(sessions), registry,
            heartbeat_interval_seconds=max(1, settings.heartbeat_timeout_seconds / 3),
        ),
        generation,
        distillation,
        documents,
        retrieval,
    )
