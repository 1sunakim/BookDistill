"""Stable failures for the generation boundary."""

from __future__ import annotations

from typing import Any


class GenerationError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}


class InvalidModelOutputError(GenerationError):
    def __init__(self, message: str, *, repair_attempted: bool) -> None:
        super().__init__(
            "INVALID_MODEL_OUTPUT",
            message,
            details={"repair_attempted": repair_attempted},
        )


class InvalidCitationError(GenerationError):
    def __init__(self, invalid_evidence_ids: list[str]) -> None:
        super().__init__(
            "INVALID_CITATION",
            "The model cited evidence that was not supplied in the request.",
            details={"invalid_evidence_ids": invalid_evidence_ids},
        )


class ProviderConfigurationError(GenerationError):
    def __init__(self, message: str) -> None:
        super().__init__("PROVIDER_NOT_CONFIGURED", message, details={})


class ProviderRequestError(GenerationError):
    def __init__(
        self, message: str, *, retryable: bool, status_code: int | None = None
    ) -> None:
        super().__init__(
            "PROVIDER_REQUEST_FAILED",
            message,
            retryable=retryable,
            details={"status_code": status_code},
        )
