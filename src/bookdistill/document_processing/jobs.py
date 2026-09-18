"""Generic task-system adapter for document processing and rebuilds."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from bookdistill.contracts import ProcessingStage
from bookdistill.task_system.exceptions import PermanentJobError, RetryableJobError
from bookdistill.task_system.exceptions import JobExecutionError
from bookdistill.task_system.handlers import JobContext

from .service import IngestionService
from .exceptions import DocumentProcessingError


IndexBook = Callable[[UUID], dict[str, Any]]


class DocumentProcessingJobHandler:
    job_type = "document.process"
    stages = ("parse", "clean", "structure", "chunk", "persist", "index")

    def __init__(self, service: IngestionService, index_book: IndexBook) -> None:
        self.service = service
        self.index_book = index_book

    def on_cancel(self, input_reference: dict[str, Any]) -> None:
        legacy_job_id = input_reference.get("legacy_job_id")
        if legacy_job_id:
            self.service.cancel_durable_job(UUID(str(legacy_job_id)))

    def run_stage(
        self,
        stage_name: str,
        context: JobContext,
        previous_output: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            book_id = UUID(str(context.input_reference["book_id"]))
            legacy_job_id = UUID(str(context.input_reference["legacy_job_id"]))
            from_stage = ProcessingStage(
                str(context.input_reference.get("from_stage", "parse"))
            )
            chapter_ids = {
                UUID(str(value))
                for value in context.input_reference.get("chapter_ids", [])
            }
        except (KeyError, TypeError, ValueError) as error:
            raise PermanentJobError(
                "DOCUMENT_JOB_INPUT_INVALID",
                "Document job input is missing or invalid.",
                {"reason": type(error).__name__},
            ) from error

        stage_order = {"parse": 0, "clean": 1, "structure": 2, "chunk": 3}
        from_order = {
            ProcessingStage.PARSE: 0,
            ProcessingStage.CLEAN: 1,
            ProcessingStage.STRUCTURE: 2,
            ProcessingStage.CHUNK: 3,
        }[from_stage]
        if stage_name in stage_order and stage_order[stage_name] < from_order:
            return {
                **previous_output, "book_id": str(book_id),
                "legacy_job_id": str(legacy_job_id), "from_stage": from_stage.value,
            }

        processing_stage = {
            "parse": ProcessingStage.PARSE,
            "clean": ProcessingStage.CLEAN,
            "structure": ProcessingStage.STRUCTURE,
            "chunk": ProcessingStage.CHUNK,
            "persist": ProcessingStage.CHUNK,
            "index": ProcessingStage.CHUNK,
        }.get(stage_name)
        if processing_stage is None:
            raise PermanentJobError(
                "DOCUMENT_STAGE_UNKNOWN", f"Unknown document stage: {stage_name}."
            )

        try:
            if stage_name != "index":
                self.service.begin_durable_stage(
                    legacy_job_id, processing_stage, context.attempt
                )
            if stage_name == "parse":
                output = self.service.run_parse_stage(book_id, legacy_job_id)
            elif stage_name == "clean":
                output = self.service.run_clean_stage(book_id, legacy_job_id)
            elif stage_name == "structure":
                output = self.service.run_structure_stage(book_id, legacy_job_id)
            elif stage_name == "chunk":
                output = self.service.run_chunk_stage(
                    book_id, legacy_job_id, chapter_ids or None
                )
            elif stage_name == "persist":
                output = self.service.run_persist_stage(
                    book_id, legacy_job_id,
                    replace_chapters=from_stage != ProcessingStage.CHUNK,
                )
            else:
                output = self.index_book(book_id)
            return {
                **previous_output, **output, "book_id": str(book_id),
                "legacy_job_id": str(legacy_job_id), "from_stage": from_stage.value,
            }
        except JobExecutionError as error:
            self.service.record_durable_failure(legacy_job_id, error, processing_stage)
            raise
        except DocumentProcessingError as error:
            self.service.record_durable_failure(legacy_job_id, error, processing_stage)
            error_type = RetryableJobError if error.retryable else PermanentJobError
            raise error_type(error.code, error.message, error.details) from error
        except Exception as error:
            self.service.record_durable_failure(legacy_job_id, error, processing_stage)
            raise RetryableJobError(
                "DOCUMENT_STAGE_FAILED", f"Document stage {stage_name} failed.",
                {"stage": stage_name, "exception_type": type(error).__name__},
            ) from error
