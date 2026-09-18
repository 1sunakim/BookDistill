"""Celery worker entrypoint and periodic stale-job recovery."""

from __future__ import annotations

import socket

from celery import Celery

from bookdistill.bootstrap import build_runtime
from bookdistill.contracts.task_system import TaskStatus
from bookdistill.logging_config import configure_logging


configure_logging()
runtime = build_runtime(create_schema=False)
celery_app = Celery("bookdistill", broker=runtime.settings.redis_url, backend=runtime.settings.redis_url)
celery_app.conf.update(
    task_serializer="json", result_serializer="json", accept_content=["json"],
    task_acks_late=True, task_reject_on_worker_lost=True,
    broker_transport_options={"visibility_timeout": 3600},
    beat_schedule={
        "recover-stale-jobs": {
            "task": "bookdistill.recover_stale_jobs",
            "schedule": 60.0,
        }
    },
)


@celery_app.task(name="bookdistill.execute_job")
def execute_job(job_id: str) -> dict:
    result = runtime.jobs.execute(job_id, worker_id=socket.gethostname())
    if result.status == TaskStatus.RETRYING.value:
        delay = runtime.settings.retry_base_seconds * (2 ** max(0, result.attempt - 1))
        execute_job.apply_async(args=[job_id], countdown=min(delay, 300))
    return {"job_id": job_id, "status": result.status, "attempt": result.attempt}


@celery_app.task(name="bookdistill.recover_stale_jobs")
def recover_stale_jobs() -> dict:
    recovered = runtime.jobs.recover_stale(runtime.settings.heartbeat_timeout_seconds)
    for job_id in recovered:
        job = runtime.jobs.get(job_id)
        if job.status == TaskStatus.RETRYING.value:
            execute_job.delay(job_id)
    return {"recovered": recovered}
