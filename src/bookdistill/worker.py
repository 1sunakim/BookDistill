"""Celery worker entrypoint and periodic stale-job recovery."""

from __future__ import annotations

import socket
from functools import cache

from celery import Celery, signals

from bookdistill.bootstrap import build_runtime
from bookdistill.database import create_database_engine, create_session_factory
from bookdistill.task_system.repository import JobRepository, utc_now
from datetime import UTC, datetime, timedelta
from bookdistill.contracts.task_system import TaskStatus
from bookdistill.db.models import BookRecord, ParsingJobRecord
from bookdistill.logging_config import configure_logging
from bookdistill.settings import Settings


configure_logging()
settings = Settings.from_environment()
celery_app = Celery("bookdistill", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_serializer="json", result_serializer="json", accept_content=["json"],
    task_acks_late=True, task_reject_on_worker_lost=True,
    broker_transport_options={"visibility_timeout": 3600},
    beat_schedule={
        "recover-stale-jobs": {
            "task": "bookdistill.recover_stale_jobs",
            "schedule": 60.0,
        },
        "publish-worker-heartbeat": {
            "task": "bookdistill.publish_worker_heartbeat", "schedule": 15.0,
        },
    },
)


@cache
def get_runtime():
    """Build the heavy application runtime only when a worker executes a task."""
    return build_runtime(settings=settings)


@cache
def get_recovery_repository() -> JobRepository:
    """Create only the DB adapter; recovery must not load embedding or LLM models."""
    engine = create_database_engine(settings.database_url)
    return JobRepository(create_session_factory(engine))


def _write_worker_heartbeat() -> None:
    from redis import Redis
    client = Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1)
    try:
        client.set("bookdistill:worker:heartbeat", socket.gethostname(), ex=settings.worker_heartbeat_ttl_seconds)
    finally:
        client.close()


def _sync_cancelled_document(repository: JobRepository, job) -> None:
    """Keep the legacy document projection consistent without loading model adapters."""
    if job.job_type != "document.process":
        return
    legacy_job_id = job.input_reference.get("legacy_job_id")
    if not legacy_job_id:
        return
    now = datetime.now(UTC)
    with repository.session_factory.begin() as session:
        legacy = session.get(ParsingJobRecord, str(legacy_job_id))
        if legacy is None:
            return
        legacy.status = "cancelled"
        legacy.finished_at = legacy.updated_at = now
        book = session.get(BookRecord, legacy.book_id)
        if book is not None:
            book.status = "cancelled"
            book.updated_at = now


@signals.worker_ready.connect
def worker_ready(**_) -> None:
    _write_worker_heartbeat()


@celery_app.task(name="bookdistill.publish_worker_heartbeat")
def publish_worker_heartbeat() -> dict:
    _write_worker_heartbeat()
    return {"worker": socket.gethostname()}


@celery_app.task(name="bookdistill.execute_job")
def execute_job(job_id: str) -> dict:
    runtime = get_runtime()
    result = runtime.jobs.execute(job_id, worker_id=socket.gethostname())
    if result.status == TaskStatus.RETRYING.value:
        delay = runtime.settings.retry_base_seconds * (2 ** max(0, result.attempt - 1))
        execute_job.apply_async(args=[job_id], countdown=min(delay, 300))
    return {"job_id": job_id, "status": result.status, "attempt": result.attempt}


@celery_app.task(name="bookdistill.recover_stale_jobs")
def recover_stale_jobs() -> dict:
    repository = get_recovery_repository()
    recovered = repository.recover_stale(
        utc_now() - timedelta(seconds=settings.heartbeat_timeout_seconds)
    )
    for job_id in recovered:
        job = repository.get(job_id)
        if job.status == TaskStatus.RETRYING.value:
            execute_job.delay(job_id)
        elif job.status == TaskStatus.CANCELLED.value:
            _sync_cancelled_document(repository, job)
    return {"recovered": recovered}
