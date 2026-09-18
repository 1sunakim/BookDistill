"""Application service implementing execution, resume, retry and cancellation."""

from __future__ import annotations

from datetime import timedelta
import logging
import socket
from threading import Event, Thread
from uuid import uuid4

from bookdistill.contracts.task_system import CreateJobRequest, JobList, JobView, StageStatus, TaskStatus

from .exceptions import JobExecutionError, PermanentJobError
from .handlers import JobContext, JobRegistry
from .repository import JobRepository, utc_now


logger = logging.getLogger(__name__)


class JobService:
    def __init__(
        self, repository: JobRepository, registry: JobRegistry, heartbeat_interval_seconds: float = 30
    ) -> None:
        self.repository = repository
        self.registry = registry
        self.heartbeat_interval_seconds = heartbeat_interval_seconds

    def create(self, request: CreateJobRequest, trace_id: str | None = None) -> tuple[JobView, bool]:
        handler = self.registry.get(request.job_type)
        if request.parent_job_id is not None:
            self.repository.get(request.parent_job_id)
        return self.repository.create(request, handler.stages, trace_id or uuid4().hex)

    def get(self, job_id: str) -> JobView:
        return self.repository.get(job_id)

    def list(self, status: str | None = None, limit: int = 50, offset: int = 0) -> JobList:
        return self.repository.list(status=status, limit=limit, offset=offset)

    def execute(self, job_id: str, worker_id: str | None = None) -> JobView:
        worker_id = worker_id or socket.gethostname()
        claimed = self.repository.claim(job_id, worker_id)
        if claimed is None:
            return self.repository.get(job_id)
        handler = self.registry.get(claimed.job_type)
        previous_output = claimed.output_reference
        for stage_name in handler.stages:
            current = self.repository.get(job_id)
            if current.status == TaskStatus.CANCEL_REQUESTED.value:
                return self._mark_cancelled(job_id)
            stage = next(
                item for item in current.stages
                if item.stage_name == stage_name and item.attempt == current.attempt
            )
            if stage.status in {StageStatus.SUCCEEDED.value, StageStatus.SKIPPED.value}:
                previous_output = stage.output_reference or previous_output
                continue
            self.repository.start_stage(job_id, stage_name, current.attempt)
            self.repository.heartbeat(job_id, worker_id)
            context = JobContext(
                job_id=job_id, trace_id=current.trace_id, attempt=current.attempt,
                input_reference=current.input_reference, checkpoint=current.checkpoint,
            )
            try:
                output = self._run_with_heartbeat(
                    job_id, worker_id, handler.run_stage, stage_name, context, previous_output
                )
            except JobExecutionError as error:
                logger.warning("job stage failed", extra={"job_id": job_id, "stage": stage_name, "code": error.code})
                return self.repository.fail(job_id, stage_name, current.attempt, error)
            except Exception as error:  # defensive boundary around feature adapters
                logger.exception("unhandled job stage error", extra={"job_id": job_id, "stage": stage_name})
                failure = PermanentJobError(
                    "UNHANDLED_STAGE_ERROR", "The stage raised an unhandled error.",
                    {"exception_type": type(error).__name__},
                )
                return self.repository.fail(job_id, stage_name, current.attempt, failure)
            self.repository.complete_stage(job_id, stage_name, current.attempt, output)
            previous_output = output
        if self.repository.get(job_id).status == TaskStatus.CANCEL_REQUESTED.value:
            return self._mark_cancelled(job_id)
        return self.repository.succeed(job_id)

    def cancel(self, job_id: str) -> JobView:
        result = self.repository.request_cancel(job_id)
        if result.status == TaskStatus.CANCELLED.value:
            self._notify_cancelled(result)
        return result

    def retry(self, job_id: str) -> JobView:
        return self.repository.retry(job_id)

    def recover_stale(self, timeout_seconds: int) -> list[str]:
        recovered = self.repository.recover_stale(
            utc_now() - timedelta(seconds=timeout_seconds)
        )
        for job_id in recovered:
            job = self.repository.get(job_id)
            if job.status == TaskStatus.CANCELLED.value:
                self._notify_cancelled(job)
        return recovered

    def _mark_cancelled(self, job_id: str) -> JobView:
        result = self.repository.mark_cancelled(job_id)
        self._notify_cancelled(result)
        return result

    def _notify_cancelled(self, job: JobView) -> None:
        handler = self.registry.get(job.job_type)
        callback = getattr(handler, "on_cancel", None)
        if callback is None:
            return
        try:
            callback(job.input_reference)
        except Exception:
            logger.exception("job cancellation hook failed", extra={"job_id": str(job.job_id)})

    def _run_with_heartbeat(self, job_id, worker_id, callback, *args):
        stopped = Event()

        def pulse() -> None:
            while not stopped.wait(self.heartbeat_interval_seconds):
                self.repository.heartbeat(job_id, worker_id)

        thread = Thread(target=pulse, name=f"heartbeat-{job_id}", daemon=True)
        thread.start()
        try:
            return callback(*args)
        finally:
            stopped.set()
            thread.join(timeout=min(self.heartbeat_interval_seconds, 1))
