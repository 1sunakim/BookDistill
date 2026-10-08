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
    def __init__(self, message: str, *, repair_attempted: bool,
                 details: dict[str, Any] | None = None) -> None:
        super().__init__(
            "INVALID_MODEL_OUTPUT",
            message,
            details={"repair_attempted": repair_attempted, **(details or {})},
        )


class ModelOutputTruncatedError(GenerationError):
    def __init__(self, *, max_output_tokens: int, input_tokens: int | None,
                 output_tokens: int | None,
                 repair_attempted: bool, response_reference: str | None) -> None:
        super().__init__(
            "MODEL_OUTPUT_TRUNCATED",
            "Model output was truncated before a complete structured response was available.",
            details={
                "finish_reason": "length",
                "max_output_tokens": max_output_tokens,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "repair_attempted": repair_attempted,
                "response_reference": response_reference,
            },
        )


class ModelStateConflictError(GenerationError):
    def __init__(self, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            "MODEL_STATE_CONFLICT",
            "claims and insufficient_evidence describe contradictory answer states.",
            details={"repair_attempted": False, **(details or {})},
        )


class InvalidCitationError(GenerationError):
    def __init__(self, invalid_evidence_ids: list[str], *,
                 details: dict[str, Any] | None = None) -> None:
        super().__init__(
            "INVALID_CITATION",
            "Evidence or citation validation failed.",
            details={"invalid_evidence_ids": invalid_evidence_ids, **(details or {})},
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
