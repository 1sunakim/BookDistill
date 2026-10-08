"""HTTP boundary for grounded generation and retrieval composition."""

from __future__ import annotations

import json
from uuid import UUID
from fastapi import APIRouter, Body, Header, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse

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
from bookdistill.generation.repository import GenerationRepository
from bookdistill.retrieval import HybridRetrievalEngine
from bookdistill.task_system.dispatch import Dispatcher
from bookdistill.task_system.service import JobService


def create_generation_router(
    retrieval: HybridRetrievalEngine,
    generation: RagAnswerService | None,
    jobs: JobService,
    dispatcher: Dispatcher,
    distillation: DistillationService | None = None,
    repository: GenerationRepository | None = None,
    *, minimum_evidence_relevance: float = 0.02,
    max_context_tokens: int = 8192,
    rag_pipeline: str = "legacy",
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
        result = configured().answer(payload)
        if repository:
            repository.save_answer(payload, result)
        return result

    @router.post("/generation/summary", response_model=SummaryResponse)
    def summarize(payload: SummaryRequest, response: Response) -> SummaryResponse:
        result = configured_distillation().summarize(payload)
        if repository:
            artifact = repository.save_artifact("summary", payload.model_dump(mode="json"), result.model_dump(mode="json"))
            response.headers["X-Artifact-ID"] = artifact["artifact_id"]
        return result

    @router.post("/generation/knowledge-points", response_model=KnowledgeResponse)
    def knowledge_points(payload: KnowledgeRequest, response: Response) -> KnowledgeResponse:
        result = configured_distillation().extract_knowledge(payload)
        if repository:
            artifact = repository.save_artifact("knowledge_extraction", payload.model_dump(mode="json"), result.model_dump(mode="json"))
            response.headers["X-Artifact-ID"] = artifact["artifact_id"]
        return result

    @router.post("/generation/questions", response_model=QuestionResponse)
    def questions(payload: QuestionRequest, response: Response) -> QuestionResponse:
        result = configured_distillation().generate_questions(payload)
        if repository:
            artifact = repository.save_artifact("question_generation", payload.model_dump(mode="json"), result.model_dump(mode="json"))
            response.headers["X-Artifact-ID"] = artifact["artifact_id"]
        return result

    @router.post("/generation/viewpoint-comparison", response_model=ViewpointResponse)
    def viewpoints(payload: ViewpointRequest, response: Response) -> ViewpointResponse:
        result = configured_distillation().compare_viewpoints(payload)
        if repository:
            artifact = repository.save_artifact("viewpoint_comparison", payload.model_dump(mode="json"), result.model_dump(mode="json"))
            response.headers["X-Artifact-ID"] = artifact["artifact_id"]
        return result

    @router.post("/rag/answer", response_model=RetrieveAndAnswerResponse)
    def retrieve_and_answer(
        payload: RetrieveAndAnswerRequest,
    ) -> RetrieveAndAnswerResponse:
        if retrieval.dense_index.count() == 0:
            raise HTTPException(status_code=409, detail="no chunks have been indexed")
        return RetrieveAndAnswerService(
            retrieval, configured(), repository,
            minimum_evidence_relevance=minimum_evidence_relevance,
            max_context_tokens=max_context_tokens,
            pipeline=rag_pipeline,
        ).answer(payload)

    @router.post("/rag/answer/stream")
    def stream_answer(payload: RetrieveAndAnswerRequest) -> StreamingResponse:
        def events():
            try:
                service = RetrieveAndAnswerService(
                    retrieval, configured(), repository,
                    minimum_evidence_relevance=minimum_evidence_relevance,
                    max_context_tokens=max_context_tokens,
                    pipeline=rag_pipeline,
                )
                yield _sse("status", {"stage": "retrieving"})
                retrieval_result = (
                    service.retrieve_runnable(payload)
                    if rag_pipeline == "runnable" else service.retrieve(payload)
                )
                yield _sse("retrieval", {"evidence": [item.model_dump(mode="json") for item in retrieval_result.evidence]})
                yield _sse("status", {"stage": "generating"})
                result = (
                    service.answer_from_retrieval_runnable(payload, retrieval_result)
                    if rag_pipeline == "runnable"
                    else service.answer_from_retrieval(payload, retrieval_result)
                )
                answer = result.answer.answer
                for start in range(0, len(answer), 48):
                    yield _sse("delta", {"text": answer[start:start + 48]})
                yield _sse("citations", {"citations": [item.model_dump(mode="json") for item in result.answer.citations]})
                yield _sse("done", result.answer.model_dump(mode="json"))
            except Exception as error:
                yield _sse("error", {"code": type(error).__name__, "message": str(error)})
        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @router.post("/conversations", status_code=201)
    def create_conversation(payload: dict = Body(...)) -> dict:
        if not repository:
            raise HTTPException(503, "generation persistence is unavailable")
        return repository.create_conversation(
            title=str(payload.get("title") or "New conversation"),
            language=str(payload.get("language") or "bilingual"),
            scope=dict(payload.get("scope") or {}),
        )

    @router.get("/conversations/{conversation_id}")
    def get_conversation(conversation_id: UUID) -> dict:
        if not repository:
            raise HTTPException(503, "generation persistence is unavailable")
        try:
            return repository.get_conversation(conversation_id)
        except ValueError as error:
            raise HTTPException(404, str(error)) from error

    @router.get("/generation/artifacts/{artifact_id}")
    def get_artifact(artifact_id: UUID) -> dict:
        if not repository:
            raise HTTPException(503, "generation persistence is unavailable")
        try:
            return repository.get_artifact(artifact_id)
        except ValueError as error:
            raise HTTPException(404, str(error)) from error

    @router.get("/generation/runs/by-request/{request_id}")
    def get_generation_run(request_id: UUID) -> dict:
        if not repository:
            raise HTTPException(503, "generation persistence is unavailable")
        try:
            return repository.get_run(request_id)
        except ValueError as error:
            raise HTTPException(404, str(error)) from error

    @router.patch("/generation/artifacts/{artifact_id}", status_code=201)
    def edit_artifact(artifact_id: UUID, content: dict = Body(...)) -> dict:
        if not repository:
            raise HTTPException(503, "generation persistence is unavailable")
        try:
            original = repository.get_artifact(artifact_id)
            return repository.save_artifact(
                original["task_type"], original["source_scope"], content,
                parent_artifact_id=artifact_id, edit_state="edited",
            )
        except ValueError as error:
            raise HTTPException(404, str(error)) from error

    @router.post("/generation/artifacts/{artifact_id}/regenerate", status_code=201)
    def regenerate_artifact(artifact_id: UUID) -> dict:
        if not repository:
            raise HTTPException(503, "generation persistence is unavailable")
        try:
            original = repository.get_artifact(artifact_id)
        except ValueError as error:
            raise HTTPException(404, str(error)) from error
        service = configured_distillation()
        task = original["task_type"]
        models = {
            "summary": (SummaryRequest, service.summarize),
            "knowledge_extraction": (KnowledgeRequest, service.extract_knowledge),
            "question_generation": (QuestionRequest, service.generate_questions),
            "viewpoint_comparison": (ViewpointRequest, service.compare_viewpoints),
        }
        if task not in models:
            raise HTTPException(422, "artifact task cannot be regenerated")
        model, method = models[task]
        result = method(model.model_validate(original["source_scope"]))
        return repository.save_artifact(
            task, original["source_scope"], result.model_dump(mode="json"),
            parent_artifact_id=artifact_id, edit_state="regenerated",
        )

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


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
