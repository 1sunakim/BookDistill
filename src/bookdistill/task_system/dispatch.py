"""Queue adapter kept separate from task state and business handlers."""

from __future__ import annotations

from typing import Protocol


class Dispatcher(Protocol):
    def dispatch(self, job_id: str, *, countdown: int = 0) -> None: ...


class CeleryDispatcher:
    def dispatch(self, job_id: str, *, countdown: int = 0) -> None:
        from bookdistill.worker import execute_job

        execute_job.apply_async(args=[job_id], countdown=countdown)


class NullDispatcher:
    """Useful for tests and API-only diagnostics; workers may claim jobs separately."""

    def dispatch(self, job_id: str, *, countdown: int = 0) -> None:
        return None


class InlineDispatcher:
    """Local deterministic dispatcher; production uses CeleryDispatcher."""

    def __init__(self, execute) -> None:
        self._execute = execute

    def dispatch(self, job_id: str, *, countdown: int = 0) -> None:
        del countdown
        self._execute(job_id)
