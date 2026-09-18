"""Stable domain failures mapped to ErrorDetail and API responses."""

from __future__ import annotations

from typing import Any

from bookdistill.contracts import ErrorCategory, ProcessingStage


class DocumentProcessingError(Exception):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        category: ErrorCategory,
        stage: ProcessingStage,
        retryable: bool = False,
        page_index: int | None = None,
        details: dict[str, Any] | None = None,
        http_status: int = 422,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.category = category
        self.stage = stage
        self.retryable = retryable
        self.page_index = page_index
        self.details = details or {}
        self.http_status = http_status


class UploadTooLargeError(DocumentProcessingError):
    def __init__(self, maximum_bytes: int) -> None:
        super().__init__(
            code="UPLOAD_TOO_LARGE",
            message=f"PDF exceeds the {maximum_bytes}-byte upload limit.",
            category=ErrorCategory.VALIDATION,
            stage=ProcessingStage.VALIDATE,
            details={"maximum_bytes": maximum_bytes},
            http_status=413,
        )

