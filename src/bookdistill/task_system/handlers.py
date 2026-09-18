"""Feature-neutral extension point for work owned by other product blocks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .exceptions import PermanentJobError, RetryableJobError


@dataclass(frozen=True, slots=True)
class JobContext:
    job_id: str
    trace_id: str
    attempt: int
    input_reference: dict[str, Any]
    checkpoint: dict[str, Any]


class JobHandler(Protocol):
    job_type: str
    stages: tuple[str, ...]

    def run_stage(self, stage_name: str, context: JobContext, previous_output: dict[str, Any]) -> dict[str, Any]: ...


class JobRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, JobHandler] = {}

    def register(self, handler: JobHandler) -> None:
        if handler.job_type in self._handlers:
            raise ValueError(f"handler already registered: {handler.job_type}")
        if not handler.stages or len(handler.stages) != len(set(handler.stages)):
            raise ValueError("handler stages must be non-empty and unique")
        self._handlers[handler.job_type] = handler

    def get(self, job_type: str) -> JobHandler:
        try:
            return self._handlers[job_type]
        except KeyError as error:
            raise PermanentJobError("JOB_TYPE_UNREGISTERED", f"No handler is registered for {job_type}.") from error

    def contains(self, job_type: str) -> bool:
        return job_type in self._handlers


class DemoJobHandler:
    """Infrastructure acceptance fixture; it contains no BookDistill feature logic."""

    job_type = "system.demo"
    stages = ("prepare", "process", "finalize")

    def run_stage(self, stage_name: str, context: JobContext, previous_output: dict[str, Any]) -> dict[str, Any]:
        fail_until = int(context.input_reference.get("fail_until_attempt", 0))
        fail_stage = context.input_reference.get("fail_stage", "process")
        if stage_name == fail_stage and context.input_reference.get("permanent_failure", False):
            raise PermanentJobError("DEMO_PERMANENT_FAILURE", "Requested permanent demo failure.")
        if stage_name == fail_stage and context.attempt <= fail_until:
            raise RetryableJobError(
                "DEMO_TRANSIENT_FAILURE", "Requested transient demo failure.",
                {"attempt": context.attempt, "fail_until_attempt": fail_until},
            )
        return {**previous_output, stage_name: "completed", "last_attempt": context.attempt}
