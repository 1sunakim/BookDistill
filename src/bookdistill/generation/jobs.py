"""Queue integration for evidence-grounded generation."""

from __future__ import annotations

from bookdistill.contracts import (
    KnowledgeRequest,
    QuestionRequest,
    RagAnswerRequest,
    SummaryRequest,
    TaskType,
    ViewpointRequest,
)
from bookdistill.task_system.exceptions import PermanentJobError, RetryableJobError
from bookdistill.task_system.handlers import JobContext

from .errors import GenerationError
from .service import RagAnswerService
from .distillation import DistillationService
from .repository import GenerationRepository


class RagAnswerJobHandler:
    job_type = "generation.rag_answer"
    stages = ("generate",)

    def __init__(self, service: RagAnswerService,
                 repository: GenerationRepository | None = None) -> None:
        self._service = service
        self._repository = repository

    def run_stage(
        self, stage_name: str, context: JobContext, previous_output: dict
    ) -> dict:
        del previous_output
        if stage_name != "generate":
            raise PermanentJobError(
                "UNKNOWN_GENERATION_STAGE", f"Unknown stage: {stage_name}"
            )
        try:
            request = RagAnswerRequest.model_validate(context.input_reference)
            response = self._service.answer(request)
            if self._repository:
                self._repository.save_answer(request, response)
            return response.model_dump(mode="json")
        except GenerationError as error:
            failure = RetryableJobError if error.retryable else PermanentJobError
            raise failure(error.code, error.message, error.details) from error
        except ValueError as error:
            raise PermanentJobError(
                "INVALID_GENERATION_JOB_INPUT", str(error)
            ) from error


class DistillationJobHandler:
    stages = ("generate",)
    _request_models = {
        TaskType.SUMMARY: SummaryRequest,
        TaskType.KNOWLEDGE_EXTRACTION: KnowledgeRequest,
        TaskType.QUESTION_GENERATION: QuestionRequest,
        TaskType.VIEWPOINT_COMPARISON: ViewpointRequest,
    }

    def __init__(self, task_type: TaskType, service: DistillationService,
                 repository: GenerationRepository | None = None) -> None:
        if task_type not in self._request_models:
            raise ValueError(f"unsupported distillation task: {task_type}")
        self.task_type = task_type
        self.job_type = f"generation.{task_type.value}"
        self._service = service
        self._repository = repository

    def run_stage(self, stage_name: str, context: JobContext, previous_output: dict) -> dict:
        del previous_output
        if stage_name != "generate":
            raise PermanentJobError("UNKNOWN_GENERATION_STAGE", f"Unknown stage: {stage_name}")
        try:
            request = self._request_models[self.task_type].model_validate(context.input_reference)
            methods = {
                TaskType.SUMMARY: self._service.summarize,
                TaskType.KNOWLEDGE_EXTRACTION: self._service.extract_knowledge,
                TaskType.QUESTION_GENERATION: self._service.generate_questions,
                TaskType.VIEWPOINT_COMPARISON: self._service.compare_viewpoints,
            }
            response = methods[self.task_type](request)
            output = response.model_dump(mode="json")
            if self._repository:
                artifact = self._repository.save_artifact(
                    self.task_type.value, request.model_dump(mode="json"), output
                )
                output["artifact_id"] = artifact["artifact_id"]
            return output
        except GenerationError as error:
            failure = RetryableJobError if error.retryable else PermanentJobError
            raise failure(error.code, error.message, error.details) from error
        except ValueError as error:
            raise PermanentJobError("INVALID_GENERATION_JOB_INPUT", str(error)) from error
