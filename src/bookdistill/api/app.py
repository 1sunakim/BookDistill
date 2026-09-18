"""FastAPI composition root and document-processing endpoints."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from bookdistill.contracts import (
    Book,
    Chapter,
    Chunk,
    CreateJobRequest,
    ErrorCategory,
    ErrorDetail,
    JobView,
    ParsedDocument,
    ParsingJob,
    ProcessingStage,
    TaskType,
)
from bookdistill.database import Base, create_database_engine, create_session_factory
from bookdistill.document_processing.factory import build_document_service
from bookdistill.document_processing.jobs import DocumentProcessingJobHandler
from bookdistill.document_processing.exceptions import DocumentProcessingError
from bookdistill.settings import Settings
from bookdistill.generation import (
    DistillationJobHandler,
    DistillationService,
    GenerationError,
    RagAnswerJobHandler,
    RagAnswerService,
    build_generation_service,
    build_distillation_service,
)
from bookdistill.generation_api import create_generation_router
from bookdistill.evaluation_api import create_evaluation_router
from bookdistill.retrieval import build_retrieval_engine
from bookdistill.retrieval_api import create_retrieval_router
from bookdistill.task_system import DemoJobHandler, JobRegistry, JobService
from bookdistill.task_system.api import create_task_router
from bookdistill.task_system.dispatch import (
    CeleryDispatcher,
    Dispatcher,
    InlineDispatcher,
    NullDispatcher,
)
from bookdistill.task_system.exceptions import (
    InvalidJobTransitionError,
    JobExecutionError,
    JobNotFoundError,
)
from bookdistill.task_system.repository import JobRepository

from .schemas import RebuildBookRequest, ReprocessBookRequest, UploadAccepted


def create_app(
    settings: Settings | None = None,
    dispatcher: Dispatcher | None = None,
    generation_service: RagAnswerService | None = None,
    distillation_service: DistillationService | None = None,
) -> FastAPI:
    resolved = settings or Settings.from_environment()
    resolved.storage_root.mkdir(parents=True, exist_ok=True)
    engine = create_database_engine(resolved.database_url)
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    service = build_document_service(resolved, sessions)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(title="BookDistill API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Idempotency-Key", "X-Request-ID", "X-Trace-ID"],
        expose_headers=["X-Request-ID", "X-Trace-ID"],
    )
    app.state.settings = resolved
    app.state.engine = engine
    app.state.ingestion_service = service
    retrieval = build_retrieval_engine(resolved)
    app.state.retrieval = retrieval
    generation = generation_service or build_generation_service(resolved)
    distillation = distillation_service or build_distillation_service(resolved)
    app.state.generation = generation
    app.state.distillation = distillation

    def index_book(book_id: UUID) -> dict[str, object]:
        chunks = service.get_chunks(book_id)
        book = service.get_book(book_id)
        if not chunks or book is None:
            raise DocumentProcessingError(
                code="CHUNKS_NOT_READY",
                message="Chunks are not available for indexing.",
                category=ErrorCategory.STORAGE,
                stage=ProcessingStage.CHUNK,
                retryable=True,
            )
        indexed_count = retrieval.replace_book_chunks(book_id, chunks, book.title)
        service.mark_book_ready(book_id)
        return {"indexed_count": indexed_count, "book_status": "ready"}

    registry = JobRegistry()
    registry.register(DemoJobHandler())
    registry.register(DocumentProcessingJobHandler(service, index_book))
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
    jobs = JobService(
        JobRepository(sessions), registry,
        heartbeat_interval_seconds=max(1, resolved.heartbeat_timeout_seconds / 3),
    )
    app.state.job_service = jobs
    if dispatcher is None:
        if resolved.queue_mode == "celery":
            dispatcher = CeleryDispatcher()
        elif resolved.queue_mode == "inline":
            dispatcher = InlineDispatcher(jobs.execute)
        else:
            dispatcher = NullDispatcher()

    @app.middleware("http")
    async def trace_requests(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", uuid4().hex)
        request.state.request_id = request_id
        request.state.trace_id = request.headers.get("X-Trace-ID", request_id)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Trace-ID"] = request.state.trace_id
        return response

    @app.exception_handler(JobNotFoundError)
    async def handle_job_not_found(request: Request, error: JobNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={
            "code": "JOB_NOT_FOUND", "message": f"Job {error} was not found.",
            "trace_id": request.state.trace_id, "details": {},
        })

    @app.exception_handler(InvalidJobTransitionError)
    async def handle_transition_error(request: Request, error: InvalidJobTransitionError) -> JSONResponse:
        return JSONResponse(status_code=409, content={
            "code": "INVALID_JOB_TRANSITION", "message": str(error),
            "trace_id": request.state.trace_id, "details": {},
        })

    @app.exception_handler(JobExecutionError)
    async def handle_job_error(request: Request, error: JobExecutionError) -> JSONResponse:
        return JSONResponse(status_code=422, content={
            "code": error.code, "message": error.message,
            "trace_id": request.state.trace_id, "details": error.details,
        })

    @app.exception_handler(DocumentProcessingError)
    async def handle_document_error(_, error: DocumentProcessingError) -> JSONResponse:
        detail = ErrorDetail(
            error_id=uuid4(),
            code=error.code,
            category=error.category,
            message=error.message,
            stage=error.stage,
            retryable=error.retryable,
            page_index=error.page_index,
            details=error.details,
            trace_id=f"request-{uuid4()}",
            occurred_at=datetime.now(UTC),
        )
        return JSONResponse(status_code=error.http_status, content=detail.model_dump(mode="json"))

    @app.exception_handler(GenerationError)
    async def handle_generation_error(
        request: Request, error: GenerationError
    ) -> JSONResponse:
        http_status = 503 if error.retryable or error.code == "PROVIDER_NOT_CONFIGURED" else 422
        return JSONResponse(status_code=http_status, content={
            "code": error.code, "message": error.message,
            "trace_id": request.state.trace_id, "details": error.details,
        })

    @app.post("/v1/books", response_model=UploadAccepted, status_code=202)
    def upload_book(
        file: Annotated[UploadFile, File(description="PDF book")],
        title: Annotated[str | None, Form()] = None,
        authors: Annotated[str, Form(description="Comma-separated author names")] = "",
        language: Annotated[str, Form()] = "zh-CN",
    ) -> UploadAccepted:
        result = service.ingest(
            file.file,
            original_file_name=file.filename or "book.pdf",
            declared_mime_type=file.content_type,
            title=title,
            authors=[author.strip() for author in authors.split(",") if author.strip()],
            language=language,
        )
        if result.duplicate:
            previous_task = jobs.repository.find_by_idempotency(
                "document.process", f"document-upload:{result.book_id}"
            )
            task_id = previous_task.job_id if previous_task else None
        else:
            task, created = jobs.create(
                CreateJobRequest(
                    job_type="document.process",
                    subject_id=result.book_id,
                    input_reference={
                        "book_id": str(result.book_id),
                        "legacy_job_id": str(result.job_id),
                        "from_stage": "parse",
                        "chapter_ids": [],
                    },
                    idempotency_key=f"document-upload:{result.book_id}",
                )
            )
            if created:
                dispatcher.dispatch(str(task.job_id))
            task_id = task.job_id
        return UploadAccepted(
            book_id=result.book_id,
            job_id=result.job_id,
            task_id=task_id,
            duplicate=result.duplicate,
        )

    @app.get("/v1/books", response_model=list[Book])
    def list_books(
        limit: int = Query(default=100, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> list[Book]:
        return service.list_books(limit=limit, offset=offset)

    @app.post(
        "/v1/books/{book_id}/reprocess",
        response_model=JobView,
        status_code=202,
    )
    def reprocess_book(book_id: UUID, payload: ReprocessBookRequest) -> JobView:
        if payload.idempotency_key:
            existing = jobs.repository.find_by_idempotency(
                "document.process", payload.idempotency_key
            )
            if existing is not None:
                return existing
        active = jobs.repository.find_active(book_id, "document.process")
        if active is not None:
            raise HTTPException(
                status_code=409,
                detail={"code": "BOOK_JOB_ACTIVE", "job_id": str(active.job_id)},
            )
        if service.get_book(book_id) is None:
            raise HTTPException(status_code=404, detail="Book not found")
        legacy_job_id = service.create_processing_job(
            book_id, start_stage=ProcessingStage.PARSE
        )
        job, created = jobs.create(
            CreateJobRequest(
                job_type="document.process",
                subject_id=book_id,
                input_reference={
                    "book_id": str(book_id),
                    "legacy_job_id": str(legacy_job_id),
                    "from_stage": "parse",
                    "chapter_ids": [],
                },
                idempotency_key=(
                    payload.idempotency_key or f"document-reprocess:{book_id}:{uuid4()}"
                ),
                max_attempts=payload.max_attempts,
            )
        )
        if created:
            dispatcher.dispatch(str(job.job_id))
        return jobs.get(str(job.job_id))

    @app.post(
        "/v1/books/{book_id}/rebuild",
        response_model=JobView,
        status_code=202,
    )
    def rebuild_book(book_id: UUID, payload: RebuildBookRequest) -> JobView:
        if payload.idempotency_key:
            existing = jobs.repository.find_by_idempotency(
                "document.process", payload.idempotency_key
            )
            if existing is not None:
                return existing
        active = jobs.repository.find_active(book_id, "document.process")
        if active is not None:
            raise HTTPException(
                status_code=409,
                detail={"code": "BOOK_JOB_ACTIVE", "job_id": str(active.job_id)},
            )
        if service.get_book(book_id) is None:
            raise HTTPException(status_code=404, detail="Book not found")
        stage = ProcessingStage(payload.from_stage)
        legacy_job_id = service.create_processing_job(book_id, start_stage=stage)
        job, created = jobs.create(
            CreateJobRequest(
                job_type="document.process",
                subject_id=book_id,
                input_reference={
                    "book_id": str(book_id),
                    "legacy_job_id": str(legacy_job_id),
                    "from_stage": stage.value,
                    "chapter_ids": [str(item) for item in payload.chapter_ids],
                },
                idempotency_key=(
                    payload.idempotency_key or f"document-rebuild:{book_id}:{uuid4()}"
                ),
                max_attempts=payload.max_attempts,
            )
        )
        if created:
            dispatcher.dispatch(str(job.job_id))
        return jobs.get(str(job.job_id))

    @app.delete("/v1/books/{book_id}", status_code=204)
    def delete_book(book_id: UUID) -> Response:
        active = jobs.repository.find_active(book_id, "document.process")
        if active is not None:
            raise HTTPException(
                status_code=409,
                detail={"code": "BOOK_JOB_ACTIVE", "job_id": str(active.job_id)},
            )
        if service.get_book(book_id) is None:
            raise HTTPException(status_code=404, detail="Book not found")
        retrieval.delete_book(book_id)
        service.delete_book(book_id)
        return Response(status_code=204)

    @app.get("/v1/books/{book_id}", response_model=Book)
    def get_book(book_id: UUID) -> Book:
        book = service.get_book(book_id)
        if book is None:
            raise HTTPException(status_code=404, detail="Book not found")
        return book

    @app.get("/v1/jobs/{job_id}", response_model=ParsingJob)
    def get_job(job_id: UUID) -> ParsingJob:
        job = service.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return job

    @app.get("/v1/books/{book_id}/parsed-document", response_model=ParsedDocument)
    def get_parsed_document(book_id: UUID) -> ParsedDocument:
        document = service.get_parsed_document(book_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Parsed document not found")
        return document

    @app.get("/v1/books/{book_id}/chapters", response_model=list[Chapter])
    def get_chapters(book_id: UUID) -> list[Chapter]:
        chapters = service.get_chapters(book_id)
        if chapters is None:
            raise HTTPException(status_code=404, detail="Book not found")
        return chapters

    @app.get("/v1/books/{book_id}/chunks", response_model=list[Chunk])
    def get_chunks(book_id: UUID) -> list[Chunk]:
        chunks = service.get_chunks(book_id)
        if chunks is None:
            raise HTTPException(status_code=404, detail="Book not found")
        return chunks

    app.include_router(create_retrieval_router(retrieval))
    app.include_router(create_evaluation_router(retrieval, resolved.storage_root))
    app.include_router(create_task_router(
        jobs, dispatcher, sessions,
        redis_url=resolved.redis_url if resolved.queue_mode == "celery" else None,
    ))
    app.include_router(
        create_generation_router(retrieval, generation, jobs, dispatcher, distillation)
    )

    return app
