"""Transactional repository for durable job state."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload, sessionmaker

from bookdistill.contracts.task_system import (
    CreateJobRequest, JobError, JobList, JobView, StageRunView, StageStatus, TaskStatus,
)
from bookdistill.db.task_models import JobRecord, StageRunRecord

from .exceptions import InvalidJobTransitionError, JobExecutionError, JobNotFoundError


TERMINAL = {TaskStatus.SUCCEEDED.value, TaskStatus.FAILED.value, TaskStatus.CANCELLED.value}


def utc_now() -> datetime:
    return datetime.now(UTC)


def canonical_hash(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


class JobRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def create(self, request: CreateJobRequest, stages: tuple[str, ...], trace_id: str) -> tuple[JobView, bool]:
        key = request.idempotency_key or canonical_hash(
            {"job_type": request.job_type, "subject_id": request.subject_id, "input": request.input_reference}
        )
        try:
            with self.session_factory() as session, session.begin():
                existing = session.scalar(
                    select(JobRecord).where(JobRecord.job_type == request.job_type, JobRecord.idempotency_key == key)
                )
                if existing is not None:
                    session.refresh(existing, attribute_names=["stages"])
                    return self._view(existing), False
                now = utc_now()
                record = JobRecord(
                    id=str(uuid4()), parent_job_id=str(request.parent_job_id) if request.parent_job_id else None,
                    job_type=request.job_type, subject_id=str(request.subject_id) if request.subject_id else None,
                    status=TaskStatus.QUEUED.value, progress_total=len(stages), max_attempts=request.max_attempts,
                    input_reference=request.input_reference, idempotency_key=key, trace_id=trace_id,
                    queued_at=now, created_at=now, updated_at=now,
                )
                session.add(record)
                session.flush()
                for stage in stages:
                    session.add(StageRunRecord(
                        id=str(uuid4()), job_id=record.id, stage_name=stage,
                        status=StageStatus.PENDING.value, attempt=1,
                        input_hash=canonical_hash({"input": request.input_reference, "stage": stage}),
                    ))
                session.flush()
                session.refresh(record, attribute_names=["stages"])
                return self._view(record), True
        except IntegrityError:
            # A concurrent request may win the unique idempotency-key insert.
            with self.session_factory() as session:
                existing = session.scalar(
                    select(JobRecord).options(selectinload(JobRecord.stages)).where(
                        JobRecord.job_type == request.job_type, JobRecord.idempotency_key == key
                    )
                )
                if existing is None:
                    raise
                return self._view(existing), False

    def get(self, job_id: str | UUID) -> JobView:
        with self.session_factory() as session:
            record = session.scalar(
                select(JobRecord).options(selectinload(JobRecord.stages)).where(JobRecord.id == str(job_id))
            )
            if record is None:
                raise JobNotFoundError(str(job_id))
            return self._view(record)

    def list(self, *, status: str | None, limit: int, offset: int) -> JobList:
        with self.session_factory() as session:
            filters = [JobRecord.status == status] if status else []
            total = session.scalar(select(func.count()).select_from(JobRecord).where(*filters)) or 0
            records = session.scalars(
                select(JobRecord).options(selectinload(JobRecord.stages)).where(*filters)
                .order_by(JobRecord.created_at.desc()).limit(limit).offset(offset)
            ).all()
            return JobList(items=[self._view(item) for item in records], total=total, limit=limit, offset=offset)

    def status_counts(self) -> dict[str, int]:
        with self.session_factory() as session:
            rows = session.execute(
                select(JobRecord.status, func.count()).group_by(JobRecord.status)
            ).all()
            return {status: count for status, count in rows}

    def find_active(self, subject_id: str | UUID, job_type: str) -> JobView | None:
        active = [
            TaskStatus.PENDING.value,
            TaskStatus.QUEUED.value,
            TaskStatus.RUNNING.value,
            TaskStatus.RETRYING.value,
            TaskStatus.CANCEL_REQUESTED.value,
        ]
        with self.session_factory() as session:
            record = session.scalar(
                select(JobRecord)
                .options(selectinload(JobRecord.stages))
                .where(
                    JobRecord.subject_id == str(subject_id),
                    JobRecord.job_type == job_type,
                    JobRecord.status.in_(active),
                )
                .order_by(JobRecord.created_at.desc())
            )
            return self._view(record) if record is not None else None

    def find_by_idempotency(
        self, job_type: str, idempotency_key: str
    ) -> JobView | None:
        with self.session_factory() as session:
            record = session.scalar(
                select(JobRecord)
                .options(selectinload(JobRecord.stages))
                .where(
                    JobRecord.job_type == job_type,
                    JobRecord.idempotency_key == idempotency_key,
                )
            )
            return self._view(record) if record is not None else None

    def claim(self, job_id: str, worker_id: str) -> JobView | None:
        now = utc_now()
        with self.session_factory() as session, session.begin():
            changed = session.execute(
                update(JobRecord).where(
                    JobRecord.id == job_id,
                    JobRecord.status.in_([TaskStatus.QUEUED.value, TaskStatus.RETRYING.value]),
                ).values(
                    status=TaskStatus.RUNNING.value, worker_id=worker_id, heartbeat_at=now,
                    started_at=func.coalesce(JobRecord.started_at, now), attempt=JobRecord.attempt + 1,
                    error_code=None, error_message=None, error_retryable=None, error_details={}, updated_at=now,
                )
            ).rowcount
            if not changed:
                return None
        return self.get(job_id)

    def heartbeat(self, job_id: str, worker_id: str) -> None:
        with self.session_factory() as session, session.begin():
            session.execute(update(JobRecord).where(
                JobRecord.id == job_id, JobRecord.status == TaskStatus.RUNNING.value,
                JobRecord.worker_id == worker_id,
            ).values(heartbeat_at=utc_now(), updated_at=utc_now()))

    def start_stage(self, job_id: str, stage_name: str, attempt: int) -> None:
        now = utc_now()
        with self.session_factory() as session, session.begin():
            stage = self._stage_for_attempt(session, job_id, stage_name, attempt)
            stage.status, stage.started_at = StageStatus.RUNNING.value, now

    def complete_stage(self, job_id: str, stage_name: str, attempt: int, output: dict) -> None:
        now = utc_now()
        with self.session_factory() as session, session.begin():
            stage = self._stage_for_attempt(session, job_id, stage_name, attempt)
            stage.status, stage.output_reference, stage.finished_at = StageStatus.SUCCEEDED.value, output, now
            job = session.get(JobRecord, job_id)
            assert job is not None
            job.progress_current += 1
            job.checkpoint = {"last_completed_stage": stage_name, "attempt": attempt}
            job.output_reference = output
            job.heartbeat_at = job.updated_at = now

    def fail(self, job_id: str, stage_name: str, attempt: int, error: JobExecutionError) -> JobView:
        now = utc_now()
        with self.session_factory() as session, session.begin():
            stage = self._stage_for_attempt(session, job_id, stage_name, attempt)
            stage.status, stage.finished_at = StageStatus.FAILED.value, now
            stage.error_code, stage.error_message = error.code, error.message
            stage.error_retryable, stage.error_details = error.retryable, error.details
            job = session.get(JobRecord, job_id)
            assert job is not None
            retry = error.retryable and job.attempt < job.max_attempts
            job.status = TaskStatus.RETRYING.value if retry else TaskStatus.FAILED.value
            job.error_code, job.error_message = error.code, error.message
            job.error_retryable, job.error_details = error.retryable, error.details
            job.worker_id = None
            job.finished_at = None if retry else now
            job.queued_at = now if retry else job.queued_at
            job.updated_at = now
            if retry:
                self._prepare_retry_stages(session, job, attempt + 1)
        return self.get(job_id)

    def succeed(self, job_id: str) -> JobView:
        now = utc_now()
        with self.session_factory() as session, session.begin():
            job = session.get(JobRecord, job_id)
            if job is None:
                raise JobNotFoundError(job_id)
            job.status, job.finished_at, job.heartbeat_at = TaskStatus.SUCCEEDED.value, now, now
            job.worker_id, job.updated_at = None, now
        return self.get(job_id)

    def request_cancel(self, job_id: str) -> JobView:
        now = utc_now()
        with self.session_factory() as session, session.begin():
            job = session.get(JobRecord, job_id)
            if job is None:
                raise JobNotFoundError(job_id)
            if job.status in TERMINAL:
                raise InvalidJobTransitionError(f"cannot cancel job in {job.status}")
            if job.status in {TaskStatus.PENDING.value, TaskStatus.QUEUED.value, TaskStatus.RETRYING.value}:
                job.status, job.finished_at = TaskStatus.CANCELLED.value, now
            else:
                job.status = TaskStatus.CANCEL_REQUESTED.value
            job.updated_at = now
        return self.get(job_id)

    def mark_cancelled(self, job_id: str) -> JobView:
        now = utc_now()
        with self.session_factory() as session, session.begin():
            job = session.get(JobRecord, job_id)
            if job is None:
                raise JobNotFoundError(job_id)
            job.status, job.finished_at, job.worker_id = TaskStatus.CANCELLED.value, now, None
            job.updated_at = now
        return self.get(job_id)

    def retry(self, job_id: str) -> JobView:
        now = utc_now()
        with self.session_factory() as session, session.begin():
            job = session.get(JobRecord, job_id)
            if job is None:
                raise JobNotFoundError(job_id)
            if job.status != TaskStatus.FAILED.value:
                raise InvalidJobTransitionError("only failed jobs can be retried")
            if job.attempt >= job.max_attempts:
                job.max_attempts = job.attempt + 1
            job.status, job.queued_at, job.finished_at = TaskStatus.QUEUED.value, now, None
            job.worker_id, job.updated_at = None, now
            self._prepare_retry_stages(session, job, job.attempt + 1)
        return self.get(job_id)

    def recover_stale(self, stale_before: datetime) -> list[str]:
        recovered: list[str] = []
        now = utc_now()
        with self.session_factory() as session, session.begin():
            jobs = session.scalars(select(JobRecord).where(
                JobRecord.status.in_([
                    TaskStatus.RUNNING.value, TaskStatus.CANCEL_REQUESTED.value,
                ]),
                JobRecord.heartbeat_at < stale_before,
            )).all()
            for job in jobs:
                was_cancel_requested = job.status == TaskStatus.CANCEL_REQUESTED.value
                if was_cancel_requested:
                    job.status = TaskStatus.CANCELLED.value
                    job.error_code = job.error_message = None
                    job.error_retryable = None
                    job.finished_at = now
                else:
                    job.status = TaskStatus.RETRYING.value if job.attempt < job.max_attempts else TaskStatus.FAILED.value
                    job.error_code, job.error_message = "WORKER_HEARTBEAT_LOST", "Worker heartbeat timed out."
                    job.error_retryable = True
                job.worker_id, job.updated_at = None, now
                if job.status == TaskStatus.RETRYING.value:
                    job.queued_at = now
                    self._prepare_retry_stages(session, job, job.attempt + 1)
                elif not was_cancel_requested:
                    job.finished_at = now
                recovered.append(job.id)
        return recovered

    @staticmethod
    def _stage_for_attempt(session: Session, job_id: str, stage_name: str, attempt: int) -> StageRunRecord:
        stage = session.scalar(select(StageRunRecord).where(
            StageRunRecord.job_id == job_id, StageRunRecord.stage_name == stage_name,
            StageRunRecord.attempt == attempt,
        ))
        if stage is None:
            raise JobNotFoundError(f"stage {stage_name} attempt {attempt}")
        return stage

    @staticmethod
    def _prepare_retry_stages(session: Session, job: JobRecord, next_attempt: int) -> None:
        previous = session.scalars(select(StageRunRecord).where(
            StageRunRecord.job_id == job.id, StageRunRecord.attempt == job.attempt,
        ).order_by(StageRunRecord.created_at)).all()
        for old in previous:
            session.add(StageRunRecord(
                id=str(uuid4()), job_id=job.id, stage_name=old.stage_name,
                status=StageStatus.SKIPPED.value if old.status == StageStatus.SUCCEEDED.value else StageStatus.PENDING.value,
                attempt=next_attempt, input_hash=old.input_hash,
                output_reference=old.output_reference if old.status == StageStatus.SUCCEEDED.value else {},
                finished_at=utc_now() if old.status == StageStatus.SUCCEEDED.value else None,
            ))

    @staticmethod
    def _view(record: JobRecord) -> JobView:
        error = None
        if record.error_code:
            error = JobError(code=record.error_code, message=record.error_message or record.error_code,
                             retryable=bool(record.error_retryable), details=record.error_details or {})
        stages = []
        for item in record.stages:
            stage_error = None
            if item.error_code:
                stage_error = JobError(code=item.error_code, message=item.error_message or item.error_code,
                                       retryable=bool(item.error_retryable), details=item.error_details or {})
            stages.append(StageRunView(
                stage_run_id=UUID(item.id), stage_name=item.stage_name, status=item.status,
                attempt=item.attempt, input_hash=item.input_hash,
                output_reference=item.output_reference or {}, error=stage_error,
                started_at=_aware(item.started_at), finished_at=_aware(item.finished_at),
            ))
        return JobView(
            job_id=UUID(record.id), parent_job_id=UUID(record.parent_job_id) if record.parent_job_id else None,
            job_type=record.job_type, subject_id=UUID(record.subject_id) if record.subject_id else None,
            status=record.status, progress_current=record.progress_current, progress_total=record.progress_total,
            attempt=record.attempt, max_attempts=record.max_attempts,
            input_reference=record.input_reference or {}, output_reference=record.output_reference or {},
            checkpoint=record.checkpoint or {}, idempotency_key=record.idempotency_key, error=error,
            worker_id=record.worker_id, trace_id=record.trace_id, created_at=_aware(record.created_at),
            queued_at=_aware(record.queued_at), started_at=_aware(record.started_at),
            finished_at=_aware(record.finished_at), heartbeat_at=_aware(record.heartbeat_at), stages=stages,
        )
