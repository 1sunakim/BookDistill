"""FastAPI composition root and document-processing endpoints."""

from __future__ import annotations

from contextlib import asynccontextmanager
from collections import defaultdict, deque
from time import perf_counter
import secrets
import time
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from bookdistill.contracts import (
    Book,
    Chapter,
    Chunk,
    CreateJobRequest,
    ErrorCategory,
    JobView,
    ParsedDocument,
    ParsingJob,
    ProcessingStage,
    TaskType,
)
from bookdistill.database import create_database_engine, create_session_factory
from bookdistill.document_processing.factory import build_document_service
from bookdistill.document_processing.jobs import DocumentProcessingJobHandler
from bookdistill.document_processing.exceptions import DocumentProcessingError
from bookdistill.document_processing.text_service import TextBookService
from bookdistill.document_processing.source_export import build_source_archive
from bookdistill.document_processing.pdf_source_adapter import adapt_pdf_snapshot
from bookdistill.document_processing.source_search import document_revision
from bookdistill.contracts.document_v2 import (
    BookV2, ChapterV2, ChunkV2, ParsedDocumentV2, SourceSearchRequest,
    SourceSearchResponse, SourceReprocessRequest, SourceSnapshotV2, TextBookAccepted,
)
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
from bookdistill.agent_proxy import create_agent_proxy_router
from bookdistill.generation.repository import GenerationRepository
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
from bookdistill.observability import metrics
from bookdistill.model_config import ModelConfigStore
from bookdistill.model_config_api import create_model_config_router
from bookdistill.image_assets import ImageAssetService
from bookdistill.image_assets.api import create_image_router
from bookdistill.image_assets.errors import ImageAssetError
from bookdistill.obsidian_export import ObsidianExportService
from bookdistill.obsidian_export.api import create_export_router
from bookdistill.obsidian_export.errors import ExportError
from bookdistill.obsidian_export.repository import ExportRepository

from .schemas import RebuildBookRequest, ReprocessBookRequest, UploadAccepted
from .content_auth import is_private_content_path, require_content_key


def create_app(
    settings: Settings | None = None,
    dispatcher: Dispatcher | None = None,
    generation_service: RagAnswerService | None = None,
    distillation_service: DistillationService | None = None,
) -> FastAPI:
    resolved = settings or Settings.from_environment()
    resolved.storage_root.mkdir(parents=True, exist_ok=True)
    engine = create_database_engine(resolved.database_url)
    sessions = create_session_factory(engine)
    model_config = ModelConfigStore(resolved.storage_root)
    images = ImageAssetService(resolved, sessions)
    service = build_document_service(resolved, sessions, images)
    text_books = TextBookService(
        sessions, service.storage, maximum_tokens=resolved.chunk_max_tokens
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(title="BookDistill API", version="0.1.0", lifespan=lifespan)
    app.state.settings = resolved
    app.state.engine = engine
    app.state.ingestion_service = service
    app.state.text_book_service = text_books
    app.state.image_assets = images
    retrieval = build_retrieval_engine(resolved)
    app.state.retrieval = retrieval
    generation = generation_service or build_generation_service(resolved, model_config)
    distillation = distillation_service or build_distillation_service(resolved, model_config)
    generation_repository = GenerationRepository(sessions)
    if generation is not None and generation_service is None:
        generation.source_validator = generation_repository.validate_evidence_sources
        generation.minimum_claim_support_overlap = resolved.minimum_claim_support_overlap
    if distillation is not None and distillation_service is None:
        distillation.source_validator = generation_repository.validate_evidence_sources
        distillation.minimum_claim_support_overlap = resolved.minimum_claim_support_overlap
    app.state.generation = generation
    app.state.distillation = distillation
    rate_windows: dict[str, deque[float]] = defaultdict(deque)
    app.state.generation_repository = generation_repository
    exporter = ObsidianExportService(
        ExportRepository(sessions, generation_repository), images, service.storage, images.book_lock,
    )
    app.state.obsidian_export = exporter

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
    registry.register(DocumentProcessingJobHandler(service, index_book, text_books))
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
        started = perf_counter()
        request_id = request.headers.get("X-Request-ID", uuid4().hex)
        request.state.request_id = request_id
        request.state.trace_id = request.headers.get("X-Trace-ID", request_id)
        client_host = request.client.host if request.client else "unknown"
        now = time.monotonic()
        window = rate_windows[client_host]
        while window and window[0] <= now - 60:
            window.popleft()
        content_auth_error = None
        if request.method != "OPTIONS" and is_private_content_path(request.url.path):
            try:
                require_content_key(request)
            except HTTPException as error:
                content_auth_error = error
        if len(window) >= resolved.rate_limit_per_minute:
            response = JSONResponse(status_code=429, content={
                "code": "RATE_LIMITED", "message": "Request rate limit exceeded.",
                "trace_id": request.state.trace_id, "details": {},
            })
        elif content_auth_error is not None:
            response = JSONResponse(status_code=content_auth_error.status_code, content={
                **content_auth_error.detail, "trace_id": request.state.trace_id, "details": {},
            })
        elif request.url.path.startswith("/api/v1/model-config") and not resolved.api_key:
            response = JSONResponse(status_code=503, content={
                "code": "ADMIN_KEY_REQUIRED",
                "message": "Set BOOKDISTILL_API_KEY on the server before managing model profiles.",
                "trace_id": request.state.trace_id, "details": {},
            })
        elif (
            resolved.api_key
            and (
                request.url.path.startswith("/api/v1/model-config")
                or request.url.path.startswith("/api/v1/agent")
                or
                request.method not in {"GET", "HEAD", "OPTIONS"}
                or request.url.path.startswith("/api/v1/jobs")
                or request.url.path == "/api/v1/metrics"
            )
            and not secrets.compare_digest(
                request.headers.get("X-BookDistill-API-Key", "").encode("utf-8"), resolved.api_key.encode("utf-8")
            )
        ):
            response = JSONResponse(status_code=401, content={
                "code": "AUTHENTICATION_REQUIRED", "message": "A valid API key is required.",
                "trace_id": request.state.trace_id, "details": {},
            })
        else:
            window.append(now)
            response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Trace-ID"] = request.state.trace_id
        route = request.scope.get("route")
        metrics.observe(
            request.method, getattr(route, "path", request.url.path),
            response.status_code, perf_counter() - started,
        )
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Idempotency-Key", "X-Request-ID", "X-Trace-ID", "X-BookDistill-API-Key", "If-None-Match"],
        expose_headers=["X-Request-ID", "X-Trace-ID", "X-Artifact-ID", "ETag", "Content-Disposition"],
    )

    @app.exception_handler(ImageAssetError)
    async def handle_image_error(request: Request, error: ImageAssetError) -> JSONResponse:
        return JSONResponse(status_code=error.status_code, content={
            "code": error.code, "message": error.message,
            "trace_id": request.state.trace_id, "details": {},
        })

    @app.exception_handler(ExportError)
    async def handle_export_error(request: Request, error: ExportError) -> JSONResponse:
        return JSONResponse(status_code=error.status_code, content={
            "code": error.code, "message": error.message,
            "trace_id": request.state.trace_id, "details": {},
        })

    @app.exception_handler(HTTPException)
    async def handle_http_error(request: Request, error: HTTPException) -> JSONResponse:
        detail = error.detail
        if isinstance(detail, dict):
            code = str(detail.get("code", "HTTP_ERROR"))
            message = str(detail.get("message", code))
            details = {key: value for key, value in detail.items() if key not in {"code", "message"}}
        else:
            code = "HTTP_ERROR"
            message = str(detail)
            details = {}
        return JSONResponse(status_code=error.status_code, content={
            "code": code, "message": message,
            "trace_id": request.state.trace_id, "details": details,
        })

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={
            "code": "VALIDATION_ERROR", "message": "Request validation failed.",
            "trace_id": request.state.trace_id,
            "details": {"errors": jsonable_encoder(error.errors())},
        })

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
    async def handle_document_error(request: Request, error: DocumentProcessingError) -> JSONResponse:
        details = dict(error.details)
        details.update({
            "category": error.category.value, "stage": error.stage.value,
            "retryable": error.retryable, "page_index": error.page_index,
        })
        return JSONResponse(status_code=error.http_status, content={
            "code": error.code, "message": error.message,
            "trace_id": request.state.trace_id, "details": details,
        })

    @app.exception_handler(GenerationError)
    async def handle_generation_error(
        request: Request, error: GenerationError
    ) -> JSONResponse:
        http_status = 503 if error.retryable or error.code == "PROVIDER_NOT_CONFIGURED" else 422
        return JSONResponse(status_code=http_status, content={
            "code": error.code, "message": error.message,
            "trace_id": request.state.trace_id, "details": error.details,
        })

    def _text_book_view(record) -> BookV2:
        return BookV2(
            book_id=UUID(record.id), title=record.title, authors=record.authors,
            format=record.format, mime_type=record.mime_type,
            size_bytes=record.size_bytes, language=record.language,
            source_unit_count=record.source_unit_count,
            status=record.status,
            retrieval_ready=text_books.is_retrieval_ready(UUID(record.id)),
        )

    def _text_book_content(book_id: UUID):
        result = text_books.get(book_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Text book not found")
        return result

    @app.post("/api/v2/books", response_model=TextBookAccepted, status_code=202)
    def upload_text_book(
        file: Annotated[UploadFile, File(description="UTF-8 TXT, Markdown, EPUB or DOCX book")],
        title: Annotated[str | None, Form()] = None,
        authors: Annotated[str, Form()] = "",
        language: Annotated[str, Form()] = "zh-CN",
    ) -> TextBookAccepted:
        result = text_books.ingest(
            file.file, filename=file.filename or "book.txt", mime_type=file.content_type,
            title=title, authors=[value.strip() for value in authors.split(",") if value.strip()],
            language=language,
        )
        task, created = jobs.create(CreateJobRequest(
            job_type="document.process", subject_id=result.book_id,
            input_reference={"book_id": str(result.book_id), "format": result.format},
            idempotency_key=f"document-upload:{result.book_id}",
        ))
        if created:
            dispatcher.dispatch(str(task.job_id))
        record = text_books.get_book(result.book_id)
        if record is None:
            raise RuntimeError("text book disappeared after upload")
        return TextBookAccepted(
            book=_text_book_view(record), duplicate=result.duplicate,
            task_id=task.job_id,
        )

    @app.get("/api/v2/books/{book_id}", response_model=BookV2)
    def get_text_book(book_id: UUID) -> BookV2:
        record = text_books.get_book(book_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Text book not found")
        return _text_book_view(record)

    @app.get("/api/v2/books", response_model=list[BookV2])
    def list_text_books() -> list[BookV2]:
        return [_text_book_view(record) for record in text_books.list_books()]

    @app.get("/api/v2/books/{book_id}/parsed-document", response_model=ParsedDocumentV2)
    def get_text_document(book_id: UUID) -> ParsedDocumentV2:
        return _text_book_content(book_id)[1]

    @app.get("/api/v2/books/{book_id}/chapters", response_model=list[ChapterV2])
    def get_text_chapters(book_id: UUID) -> list[ChapterV2]:
        return _text_book_content(book_id)[2]

    @app.get("/api/v2/books/{book_id}/chunks", response_model=list[ChunkV2])
    def get_text_chunks(book_id: UUID) -> list[ChunkV2]:
        return _text_book_content(book_id)[3]

    @app.get("/api/v2/books/{book_id}/source", response_model=None)
    def get_text_source(book_id: UUID) -> PlainTextResponse | FileResponse:
        book = text_books.get_book(book_id)
        if book is None:
            raise HTTPException(status_code=404, detail="Book not found")
        if book.format in {"epub", "docx"}:
            source_path = text_books.original_binary_path(book_id)
            if source_path is None:
                raise HTTPException(status_code=404, detail="Book source not found")
            return FileResponse(source_path, media_type=book.mime_type, filename=book.original_file_name)
        content = text_books.read_original(book_id)
        if content is None:
            raise HTTPException(status_code=404, detail="Text book not found")
        return PlainTextResponse(content, media_type="text/plain; charset=utf-8")

    @app.post("/api/v2/retrieval/search", response_model=SourceSearchResponse)
    def search_source_books(payload: SourceSearchRequest) -> SourceSearchResponse:
        return text_books.search.search(payload)

    @app.get("/api/v2/books/{book_id}/source-snapshot", response_model=SourceSnapshotV2)
    def get_source_snapshot(book_id: UUID, response: Response) -> SourceSnapshotV2:
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        if jobs.repository.find_active(book_id, "document.process") is not None:
            raise HTTPException(status_code=409, detail="Document processing is active")
        text_book = text_books.get_book(book_id)
        if text_book is not None:
            if not text_books.is_retrieval_ready(book_id):
                raise HTTPException(status_code=409, detail="Text book processing is incomplete")
            snapshot = text_books.get(book_id)
            if snapshot is None:
                raise HTTPException(status_code=409, detail="Text book source snapshot is incomplete")
            _, document, chapters, chunks = snapshot
        else:
            book = service.get_book(book_id)
            if book is None:
                raise HTTPException(status_code=404, detail="Book not found")
            if book.status != "ready":
                raise HTTPException(status_code=409, detail="PDF processing is incomplete")
            parsed = service.get_parsed_document(book_id)
            pdf_chapters = service.get_chapters(book_id)
            pdf_chunks = service.get_chunks(book_id)
            if parsed is None or pdf_chapters is None or pdf_chunks is None:
                raise HTTPException(status_code=409, detail="PDF source snapshot is incomplete")
            try:
                document, chapters, chunks = adapt_pdf_snapshot(
                    parsed, pdf_chapters, pdf_chunks, source_sha256=book.sha256,
                )
            except ValueError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
        snapshot_view = SourceSnapshotV2(
            book_id=book_id, format=document.format,
            document_revision=document_revision(document, chunks),
            document=document, chapters=chapters, chunks=chunks,
        )
        if jobs.repository.find_active(book_id, "document.process") is not None:
            raise HTTPException(status_code=409, detail="Document processing is active")
        return snapshot_view

    @app.post("/api/v2/books/{book_id}/reprocess", response_model=JobView, status_code=202)
    def reprocess_text_book(book_id: UUID, payload: SourceReprocessRequest) -> JobView:
        if text_books.get_book(book_id) is None:
            raise HTTPException(status_code=404, detail="Text book not found")
        if payload.idempotency_key:
            existing = jobs.repository.find_by_idempotency("document.process", payload.idempotency_key)
            if existing is not None:
                if existing.subject_id != book_id:
                    raise HTTPException(status_code=409, detail="Idempotency key belongs to another book")
                return existing
        active = jobs.repository.find_active(book_id, "document.process")
        if active is not None:
            raise HTTPException(status_code=409, detail={
                "code": "BOOK_JOB_ACTIVE", "job_id": str(active.job_id),
            })
        job, created = jobs.create(CreateJobRequest(
            job_type="document.process", subject_id=book_id,
            input_reference={
                "book_id": str(book_id), "format": text_books.get_book(book_id).format,
                "from_stage": payload.from_stage,
            },
            idempotency_key=payload.idempotency_key or f"source-reprocess:{book_id}:{uuid4()}",
            max_attempts=payload.max_attempts,
        ))
        if created:
            text_books.mark_queued(book_id)
            dispatcher.dispatch(str(job.job_id))
        return jobs.get(str(job.job_id))

    @app.delete("/api/v2/books/{book_id}", status_code=204)
    def delete_text_book(book_id: UUID) -> Response:
        if text_books.get_book(book_id) is None:
            raise HTTPException(status_code=404, detail="Text book not found")
        active = jobs.repository.find_active(book_id, "document.process")
        if active is not None:
            raise HTTPException(status_code=409, detail={
                "code": "BOOK_JOB_ACTIVE", "job_id": str(active.job_id),
            })
        text_books.delete_book(book_id)
        return Response(status_code=204)

    @app.get("/api/v2/exports/obsidian")
    def export_source_book(book_id: UUID, include_original: bool = False) -> StreamingResponse:
        if text_books.get_book(book_id) is None:
            raise HTTPException(status_code=404, detail="Text book not found")
        if not text_books.is_retrieval_ready(book_id):
            raise HTTPException(status_code=409, detail="Text book is not ready for export")
        try:
            archive = build_source_archive(text_books, book_id, include_original=include_original)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        archive.seek(0, 2)
        size = archive.tell()
        archive.seek(0)

        def chunks():
            try:
                while part := archive.read(1024 * 1024):
                    yield part
            finally:
                archive.close()

        return StreamingResponse(chunks(), media_type="application/zip", headers={
            "Content-Disposition": f'attachment; filename="bookdistill-{book_id}.zip"',
            "Content-Length": str(size), "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        }, background=BackgroundTask(archive.close))

    @app.post("/api/v1/books", response_model=UploadAccepted, status_code=202)
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

    @app.get("/api/v1/books", response_model=list[Book])
    def list_books(
        limit: int = Query(default=100, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> list[Book]:
        return service.list_books(limit=limit, offset=offset)

    @app.post(
        "/api/v1/books/{book_id}/reprocess",
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
        "/api/v1/books/{book_id}/rebuild",
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

    @app.delete("/api/v1/books/{book_id}", status_code=204)
    def delete_book(book_id: UUID) -> Response:
        active = jobs.repository.find_active(book_id, "document.process")
        if active is not None:
            raise HTTPException(
                status_code=409,
                detail={"code": "BOOK_JOB_ACTIVE", "job_id": str(active.job_id)},
            )
        if service.get_book(book_id) is None:
            service.delete_book(book_id)  # Finish any files left by a committed deletion.
            raise HTTPException(status_code=404, detail="Book not found")
        retrieval.delete_book(book_id)
        service.delete_book(book_id)
        return Response(status_code=204)

    @app.get("/api/v1/books/{book_id}", response_model=Book)
    def get_book(book_id: UUID) -> Book:
        book = service.get_book(book_id)
        if book is None:
            raise HTTPException(status_code=404, detail="Book not found")
        return book

    @app.get("/api/v1/document-jobs/{job_id}", response_model=ParsingJob)
    def get_job(job_id: UUID) -> ParsingJob:
        job = service.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return job

    @app.get("/api/v1/books/{book_id}/parsed-document", response_model=ParsedDocument)
    def get_parsed_document(book_id: UUID) -> ParsedDocument:
        document = service.get_parsed_document(book_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Parsed document not found")
        return document

    @app.get("/api/v1/books/{book_id}/chapters", response_model=list[Chapter])
    def get_chapters(book_id: UUID) -> list[Chapter]:
        chapters = service.get_chapters(book_id)
        if chapters is None:
            raise HTTPException(status_code=404, detail="Book not found")
        return chapters

    @app.get("/api/v1/books/{book_id}/chunks", response_model=list[Chunk])
    def get_chunks(book_id: UUID) -> list[Chunk]:
        chunks = service.get_chunks(book_id)
        if chunks is None:
            raise HTTPException(status_code=404, detail="Book not found")
        return chunks

    app.include_router(create_image_router(images))
    app.include_router(create_export_router(exporter))
    app.include_router(create_model_config_router(model_config, resolved))
    app.include_router(create_agent_proxy_router(resolved, service.get_book))
    app.include_router(create_retrieval_router(retrieval))
    app.include_router(create_evaluation_router(retrieval, resolved.storage_root))
    app.include_router(create_task_router(
        jobs, dispatcher, sessions,
        settings=resolved, model_config=model_config,
    ))
    app.include_router(
        create_generation_router(
            retrieval, generation, jobs, dispatcher, distillation, generation_repository,
            minimum_evidence_relevance=resolved.minimum_evidence_relevance,
            max_context_tokens=resolved.llm_max_context_tokens,
            rag_pipeline=resolved.rag_pipeline,
        )
    )

    return app
