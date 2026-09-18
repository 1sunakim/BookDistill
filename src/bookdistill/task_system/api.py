"""HTTP adapter for the generic task system."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from bookdistill.contracts.task_system import CreateJobRequest, HealthResponse, JobList, JobView, TaskStatus

from .dispatch import Dispatcher
from .service import JobService


def create_task_router(
    jobs: JobService, dispatcher: Dispatcher, sessions: sessionmaker[Session],
    *, redis_url: str | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.get("/health/live", response_model=HealthResponse, tags=["system"])
    def live() -> HealthResponse:
        return HealthResponse(status="ok", timestamp=datetime.now(UTC), checks={"process": "ok"})

    @router.get("/health/ready", response_model=HealthResponse, tags=["system"])
    def ready() -> HealthResponse | JSONResponse:
        checks: dict[str, str] = {}
        try:
            with sessions() as session:
                session.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as error:
            checks["database"] = f"error:{type(error).__name__}"
        if redis_url:
            try:
                from redis import Redis
                client = Redis.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1)
                checks["redis"] = "ok" if client.ping() else "error:no-pong"
                client.close()
            except Exception as error:
                checks["redis"] = f"error:{type(error).__name__}"
        result = HealthResponse(
            status="ok" if all(value == "ok" for value in checks.values()) else "degraded",
            timestamp=datetime.now(UTC), checks=checks,
        )
        if result.status == "degraded":
            return JSONResponse(status_code=503, content=result.model_dump(mode="json"))
        return result

    @router.get("/metrics", response_class=PlainTextResponse, tags=["system"])
    def metrics() -> str:
        lines = ["# HELP bookdistill_jobs_total Jobs by current status.",
                 "# TYPE bookdistill_jobs_total gauge"]
        for job_status, count in sorted(jobs.repository.status_counts().items()):
            lines.append(f'bookdistill_jobs_total{{status="{job_status}"}} {count}')
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
