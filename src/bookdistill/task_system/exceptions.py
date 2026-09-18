"""Errors with explicit retry semantics."""

from __future__ import annotations

from typing import Any


class JobExecutionError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable
        self.details = details or {}


class RetryableJobError(JobExecutionError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(code, message, retryable=True, details=details)


class PermanentJobError(JobExecutionError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(code, message, retryable=False, details=details)


class JobNotFoundError(LookupError):
    pass


class InvalidJobTransitionError(RuntimeError):
    pass
