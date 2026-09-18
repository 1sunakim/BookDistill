"""HTTP boundary for grounded generation and retrieval composition."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request, status

from bookdistill.contracts import (
    CreateJobRequest,
    JobView,
    KnowledgeRequest,
    KnowledgeResponse,
    QuestionRequest,
    QuestionResponse,
    RagAnswerRequest,
    RagAnswerResponse,
    RetrieveAndAnswerRequest,
    RetrieveAndAnswerResponse,
    SummaryRequest,
    SummaryResponse,
    TaskType,
    ViewpointRequest,
    ViewpointResponse,
)
from bookdistill.generation.distillation import DistillationService
from bookdistill.generation.orchestration import RetrieveAndAnswerService
from bookdistill.generation.service import RagAnswerService
from bookdistill.retrieval import HybridRetrievalEngine
from bookdistill.task_system.dispatch import Dispatcher
from bookdistill.task_system.service import JobService


def create_generation_router(
    retrieval: HybridRetrievalEngine,
    generation: RagAnswerService | None,
    jobs: JobService,
    dispatcher: Dispatcher,
    distillation: DistillationService | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["generation"])

    def configured() -> RagAnswerService:
        if generation is None:
            raise HTTPException(
                status_code=503,
                detail="LLM provider is not configured.",
            )
        return generation

    def configured_distillation() -> DistillationService:
        if distillation is None:
            raise HTTPException(status_code=503, detail="LLM provider is not configured.")
        return distillation

    @router.post("/generation/rag-answer", response_model=RagAnswerResponse)
    def answer_from_evidence(payload: RagAnswerRequest) -> RagAnswerResponse:
        return configured().answer(payload)

    @router.post("/generation/summary", response_model=SummaryResponse)
    def summarize(payload: SummaryRequest) -> SummaryResponse:
        return configured_distillation().summarize(payload)

    @router.post("/generation/knowledge-points", response_model=KnowledgeResponse)
    def knowledge_points(payload: KnowledgeRequest) -> KnowledgeResponse:
        return configured_distillation().extract_knowledge(payload)

    @router.post("/generation/questions", response_model=QuestionResponse)
    def questions(payload: QuestionRequest) -> QuestionResponse:
        return configured_distillation().generate_questions(payload)

    @router.post("/generation/viewpoint-comparison", response_model=ViewpointResponse)
    def viewpoints(payload: ViewpointRequest) -> ViewpointResponse:
        return configured_distillation().compare_viewpoints(payload)

    @router.post("/rag/answer", response_model=RetrieveAndAnswerResponse)
    def retrieve_and_answer(
        payload: RetrieveAndAnswerRequest,
    ) -> RetrieveAndAnswerResponse:
        if retrieval.dense_index.count() == 0:
            raise HTTPException(status_code=409, detail="no chunks have been indexed")
        return RetrieveAndAnswerService(retrieval, configured()).answer(payload)

    @router.post(
        "/generation/rag-answer/jobs",
        response_model=JobView,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def enqueue_answer(
        payload: RagAnswerRequest,
        request: Request,
        idempotency_key: str | None = Header(
            default=None, alias="Idempotency-Key", max_length=128
        ),
    ) -> JobView:
        configured()
        job, created = jobs.create(
            CreateJobRequest(
                job_type="generation.rag_answer",
                subject_id=(payload.payload.evidence[0].book_id if payload.payload.evidence else None),
                input_reference=payload.model_dump(mode="json"),
                idempotency_key=idempotency_key,
            ),
            trace_id=request.state.trace_id,
        )
        if created:
            dispatcher.dispatch(str(job.job_id))
        return job

    def enqueue_distillation(
        task_type: TaskType, payload, request: Request, idempotency_key: str | None
    ) -> JobView:
        configured_distillation()
        job, created = jobs.create(
            CreateJobRequest(
                job_type=f"generation.{task_type.value}",
                input_reference=payload.model_dump(mode="json"),
                idempotency_key=idempotency_key,
            ),
            trace_id=request.state.trace_id,
        )
        if created:
            dispatcher.dispatch(str(job.job_id))
        return job

    @router.post("/generation/summary/jobs", response_model=JobView, status_code=202)
    def enqueue_summary(payload: SummaryRequest, request: Request,
                        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> JobView:
        return enqueue_distillation(TaskType.SUMMARY, payload, request, idempotency_key)

    @router.post("/generation/knowledge-points/jobs", response_model=JobView, status_code=202)
    def enqueue_knowledge(payload: KnowledgeRequest, request: Request,
                          idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> JobView:
        return enqueue_distillation(TaskType.KNOWLEDGE_EXTRACTION, payload, request, idempotency_key)

    @router.post("/generation/questions/jobs", response_model=JobView, status_code=202)
    def enqueue_questions(payload: QuestionRequest, request: Request,
                          idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> JobView:
        return enqueue_distillation(TaskType.QUESTION_GENERATION, payload, request, idempotency_key)

    @router.post("/generation/viewpoint-comparison/jobs", response_model=JobView, status_code=202)
    def enqueue_viewpoints(payload: ViewpointRequest, request: Request,
                           idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> JobView:
        return enqueue_distillation(TaskType.VIEWPOINT_COMPARISON, payload, request, idempotency_key)

    return router
