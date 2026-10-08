"""HTTP adapter for the generic task system."""

from __future__ import annotations

from datetime import UTC, datetime
import shutil
import tempfile
from pathlib import Path
from urllib.request import urlopen

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from bookdistill.contracts.task_system import CreateJobRequest, HealthResponse, JobList, JobView, TaskStatus
from bookdistill.observability import metrics as process_metrics
from bookdistill.settings import Settings
from bookdistill.model_config import ModelConfigStore

from .dispatch import Dispatcher
from .service import JobService


def create_task_router(
    jobs: JobService, dispatcher: Dispatcher, sessions: sessionmaker[Session],
    *, settings: Settings, model_config: ModelConfigStore | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/v1/health/live", response_model=HealthResponse, tags=["system"])
    def live() -> HealthResponse:
        return HealthResponse(status="ok", timestamp=datetime.now(UTC), checks={"process": "ok"})

    @router.get("/api/v1/health/ready", response_model=HealthResponse, tags=["system"])
    def ready() -> HealthResponse | JSONResponse:
        checks: dict[str, str] = {}
        try:
            with sessions() as session:
                session.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as error:
            checks["database"] = f"error:{type(error).__name__}"
        if settings.queue_mode == "celery":
            try:
                from redis import Redis
                client = Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1)
                checks["redis"] = "ok" if client.ping() else "error:no-pong"
                heartbeat = client.get("bookdistill:worker:heartbeat")
                checks["worker"] = "ok" if heartbeat else "error:no-heartbeat"
                client.close()
            except Exception as error:
                checks["redis"] = f"error:{type(error).__name__}"
                checks["worker"] = "error:redis-unavailable"
        else:
            checks["redis"] = "disabled"
            checks["worker"] = "inline"
        if settings.dense_backend.casefold() == "qdrant" or settings.sparse_backend.casefold() == "qdrant":
            try:
                with urlopen(f"{settings.qdrant_url}/healthz", timeout=1) as response:
                    checks["qdrant"] = "ok" if response.status == 200 else f"error:http-{response.status}"
            except Exception as error:
                checks["qdrant"] = f"error:{type(error).__name__}"
        else:
            checks["qdrant"] = "disabled"
        try:
            settings.storage_root.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=settings.storage_root, prefix=".ready-", delete=True):
                pass
            free = shutil.disk_usage(settings.storage_root).free
            checks["storage"] = "ok" if free >= settings.minimum_storage_free_bytes else "error:low-disk"
        except Exception as error:
            checks["storage"] = f"error:{type(error).__name__}"
        model_settings = model_config.effective_settings(settings) if model_config else settings
        if model_settings.llm_provider.casefold() in {"disabled", "none", "off"}:
            checks["model"] = "disabled"
        elif model_settings.llm_provider.casefold() == "openai-compatible" and not model_settings.llm_api_key:
            checks["model"] = "error:missing-api-key"
        elif model_settings.llm_provider.casefold() == "transformers-local" and (
            model_settings.llm_local_model_path is None or not Path(model_settings.llm_local_model_path).exists()
        ):
            checks["model"] = "error:model-path"
        else:
            checks["model"] = "ok"
        healthy_values = {"ok", "disabled", "inline"}
        result = HealthResponse(
            status="ok" if all(value in healthy_values for value in checks.values()) else "degraded",
            timestamp=datetime.now(UTC), checks=checks,
        )
        if result.status == "degraded":
            return JSONResponse(status_code=503, content=result.model_dump(mode="json"))
        return result

    @router.get("/api/v1/metrics", response_class=PlainTextResponse, tags=["system"])
    def metrics() -> str:
        lines = ["# HELP bookdistill_jobs_total Jobs by current status.",
                 "# TYPE bookdistill_jobs_total gauge"]
        for job_status, count in sorted(jobs.repository.status_counts().items()):
            lines.append(f'bookdistill_jobs_total{{status="{job_status}"}} {count}')
        lines.extend([
            "# HELP bookdistill_job_duration_seconds_sum Cumulative completed task duration.",
            "# TYPE bookdistill_job_duration_seconds_sum counter",
            "# HELP bookdistill_job_duration_seconds_count Completed task duration observations.",
            "# TYPE bookdistill_job_duration_seconds_count counter",
            "# HELP bookdistill_job_failures_total Failed tasks.",
            "# TYPE bookdistill_job_failures_total gauge",
        ])
        durations = jobs.repository.duration_stats()
        for job_status, (count, total) in sorted(durations.items()):
            lines.append(f'bookdistill_job_duration_seconds_sum{{status="{job_status}"}} {total:.6f}')
            lines.append(f'bookdistill_job_duration_seconds_count{{status="{job_status}"}} {count}')
        lines.append(f'bookdistill_job_failures_total {jobs.repository.status_counts().get("failed", 0)}')
        lines.extend(process_metrics.render())
        return "\n".join(lines) + "\n"

    @router.post("/api/v1/jobs", response_model=JobView, status_code=status.HTTP_202_ACCEPTED, tags=["jobs"])
    def create_job(payload: CreateJobRequest, request: Request, response: Response) -> JobView:
        job, created = jobs.create(payload, trace_id=request.state.trace_id)
        if created:
            dispatcher.dispatch(str(job.job_id))
        else:
            response.status_code = status.HTTP_200_OK
        return job

    @router.get("/api/v1/jobs", response_model=JobList, tags=["jobs"])
    def list_jobs(
        job_status: TaskStatus | None = Query(default=None, alias="status"),
        limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0),
    ) -> JobList:
        return jobs.list(status=job_status.value if job_status else None, limit=limit, offset=offset)

    @router.get("/api/v1/jobs/{job_id}", response_model=JobView, tags=["jobs"])
    def get_job(job_id: str) -> JobView:
        return jobs.get(job_id)

    @router.post("/api/v1/jobs/{job_id}/cancel", response_model=JobView, tags=["jobs"])
    def cancel_job(job_id: str) -> JobView:
        return jobs.cancel(job_id)

    @router.post("/api/v1/jobs/{job_id}/retry", response_model=JobView, status_code=202, tags=["jobs"])
    def retry_job(job_id: str) -> JobView:
        job = jobs.retry(job_id)
        dispatcher.dispatch(job_id)
        return job

    return router
