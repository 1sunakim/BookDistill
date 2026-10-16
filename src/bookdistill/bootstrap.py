"""Composition root shared by API and workers."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from bookdistill.database import create_database_engine, create_session_factory
from bookdistill.db import JobRecord, StageRunRecord  # noqa: F401 - registers metadata
from bookdistill.document_processing import (
    DocumentProcessingJobHandler,
    build_document_service,
)
from bookdistill.document_processing.service import IngestionService
from bookdistill.document_processing.text_service import TextBookService
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
from bookdistill.generation.repository import GenerationRepository
from bookdistill.model_config import ModelConfigStore
from bookdistill.image_assets import ImageAssetService


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
    images: ImageAssetService


def build_runtime(settings: Settings | None = None) -> Runtime:
    settings = settings or Settings.from_environment()
    settings.storage_root.mkdir(parents=True, exist_ok=True)
    engine = create_database_engine(settings.database_url)
    sessions = create_session_factory(engine)
    registry = JobRegistry()
    registry.register(DemoJobHandler())
    images = ImageAssetService(settings, sessions)
    documents = build_document_service(settings, sessions, images)
    text_books = TextBookService(
        sessions, documents.storage, maximum_tokens=settings.chunk_max_tokens
    )
    retrieval = build_retrieval_engine(settings)

    def index_book(book_id):
        if settings.queue_mode == "celery" and settings.dense_backend.casefold() != "qdrant":
            raise PermanentJobError(
                "SHARED_DENSE_INDEX_REQUIRED",
                "Celery indexing requires a shared dense backend such as Qdrant.",
                {"dense_backend": settings.dense_backend},
            )
        if settings.queue_mode == "celery" and settings.sparse_backend.casefold() != "qdrant":
            raise PermanentJobError(
                "SHARED_SPARSE_INDEX_REQUIRED",
                "Celery indexing requires the shared Qdrant sparse backend.",
                {"sparse_backend": settings.sparse_backend},
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

    registry.register(DocumentProcessingJobHandler(documents, index_book, text_books))
    model_config = ModelConfigStore(settings.storage_root)
    generation = build_generation_service(settings, model_config)
    distillation = build_distillation_service(settings, model_config)
    generation_repository = GenerationRepository(sessions)
    if generation is not None:
        generation.source_validator = generation_repository.validate_evidence_sources
        generation.minimum_claim_support_overlap = settings.minimum_claim_support_overlap
    if distillation is not None:
        distillation.source_validator = generation_repository.validate_evidence_sources
        distillation.minimum_claim_support_overlap = settings.minimum_claim_support_overlap
    if generation is not None:
        registry.register(RagAnswerJobHandler(generation, generation_repository))
    if distillation is not None:
        for task_type in (
            TaskType.SUMMARY,
            TaskType.KNOWLEDGE_EXTRACTION,
            TaskType.QUESTION_GENERATION,
            TaskType.VIEWPOINT_COMPARISON,
        ):
            registry.register(DistillationJobHandler(task_type, distillation, generation_repository))
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
        images,
    )
